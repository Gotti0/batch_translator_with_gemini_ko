# pronoun_service.py
import json
import random
import re
import time
import os
import threading
import csv
from pathlib import Path
from typing import Dict, Any, Optional, List, Union, Tuple, Callable 
from concurrent.futures import ThreadPoolExecutor, as_completed

# dtos 모듈에서 PronounExtractionProgressDTO 임포트
try:
    from .gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException
    from .file_handler import write_csv_file, PRONOUN_CSV_HEADER, ensure_dir_exists, delete_file, read_csv_file
    from .logger_config import setup_logger
    from .exceptions import BtgPronounException, BtgApiClientException
    from .dtos import PronounExtractionProgressDTO # DTO 임포트
except ImportError:
    # 단독 실행 또는 다른 경로에서의 import를 위한 fallback
    from gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException
    from file_handler import write_csv_file, PRONOUN_CSV_HEADER, ensure_dir_exists, delete_file, read_csv_file
    from logger_config import setup_logger
    from exceptions import BtgPronounException, BtgApiClientException
    from dtos import PronounExtractionProgressDTO # DTO 임포트

logger = setup_logger(__name__)

class PronounService:
    """
    텍스트에서 고유명사를 추출하고 관리하는 비즈니스 로직을 담당합니다.
    """
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]):
        """
        PronounService를 초기화합니다.

        Args:
            gemini_client (GeminiClient): Gemini API와 통신하기 위한 클라이언트.
            config (Dict[str, Any]): 애플리케이션 설정. 
                                     (예: model_name, temperature, top_p, 
                                      max_pronoun_entries, pronoun_sample_ratio, max_workers 등)
        """
        self.gemini_client = gemini_client
        self.config = config
        self.pronouns_dict: Dict[str, Dict[str, Union[str, int]]] = {} # {외국어: {"번역": 한국어, "등장횟수": int}}
        self._lock = threading.Lock() # 병렬 처리 시 self.pronouns_dict 접근 동기화용

    def _get_extraction_prompt(self, chunk: str) -> str:
        """고유명사 추출을 위한 프롬프트를 생성합니다."""
        return (
            "# 텍스트에서 고유명사 추출하기\n\n"
            "다음 텍스트에서 모든 외국어 고유명사(인명, 지명, 조직명 등)를 추출하고, "
            "한국어로 번역과 등장 횟수를 함께 제공해주세요.\n"
            "JSON 형식으로 결과를 반환해주세요:\n\n"
            "```json\n"
            "{\n"
            "  \"고유명사1\": {\"번역\": \"한국어 번역1\", \"등장횟수\": 5},\n"
            "  \"고유명사2\": {\"번역\": \"한국어 번역2\", \"등장횟수\": 3}\n"
            "}\n"
            "```\n\n"
            "텍스트:\n"
            "```\n"
            f"{chunk}\n"
            "```\n\n"
            "JSON 형식으로만 응답해주세요. 추가 설명이나 마크다운 없이 순수 JSON 데이터만 제공해주세요.\n"
        )

    def _extract_pronouns_from_chunk_via_api(self, chunk: str, retry_count: int = 0, max_retries: int = 3) -> Dict[str, Any]:
        """
        단일 텍스트 청크에서 Gemini API를 사용하여 고유명사를 추출합니다.

        Args:
            chunk (str): 분석할 텍스트 청크.
            retry_count (int): 현재 재시도 횟수.
            max_retries (int): 최대 재시도 횟수.

        Returns:
            Dict[str, Any]: 추출된 고유명사 정보 (JSON 파싱된 딕셔너리). 오류 시 빈 딕셔너리.
        """
        prompt = self._get_extraction_prompt(chunk)
        model_name = self.config.get("model_name", "gemini-1.5-flash-latest") # 모델 이름 설정에서 가져오도록 수정
        
        generation_config = {
            "temperature": self.config.get("pronoun_extraction_temperature", 0.2), 
            "top_p": self.config.get("top_p", 0.9), # config에서 top_p 가져오기
        }

        try:
            response_text = self.gemini_client.generate_text(
                prompt=prompt,
                model_name=model_name,
                generation_config_dict=generation_config
            )

            if not response_text:
                logger.warning(f"고유명사 추출 API로부터 응답을 받지 못했습니다. 청크: {chunk[:50]}...")
                return {}

            json_str = response_text.strip()
            json_str = re.sub(r'^```json\s*', '', json_str, flags=re.IGNORECASE)
            json_str = re.sub(r'\s*```$', '', json_str, flags=re.IGNORECASE)
            json_str = json_str.strip()
            
            try:
                pronouns = json.loads(json_str)
                if not isinstance(pronouns, dict):
                    logger.warning(f"JSON 파싱 결과가 딕셔너리가 아닙니다: {type(pronouns)}. 응답: {json_str[:100]}")
                    return {}
                return pronouns
            except json.JSONDecodeError as je:
                logger.warning(f"JSON 파싱 오류: {je}. 응답 형식 정제 시도. 응답: {json_str[:200]}")
                json_match = re.search(r'\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}', json_str, re.DOTALL)
                if json_match:
                    try:
                        pronouns = json.loads(json_match.group(0))
                        if not isinstance(pronouns, dict):
                            logger.warning(f"정제된 JSON 파싱 결과가 딕셔너리가 아닙니다: {type(pronouns)}")
                            return {}
                        return pronouns
                    except json.JSONDecodeError as je2:
                        logger.error(f"정제된 JSON 파싱 실패: {je2}. 응답: {json_match.group(0)[:200]}")
                        return {}
                
                if retry_count < max_retries:
                    logger.info(f"JSON 형식 오류, 재시도 중 ({retry_count + 1}/{max_retries})...")
                    time.sleep(1 + retry_count) 
                    return self._extract_pronouns_from_chunk_via_api(chunk, retry_count + 1, max_retries)
                else:
                    logger.error("최대 재시도 횟수 초과 (JSON 형식 오류). 빈 결과 반환.")
                    return {}
        
        except GeminiContentSafetyException as e_safety:
            logger.warning(f"고유명사 추출 중 콘텐츠 안전 문제: {e_safety}. 청크: {chunk[:50]}...")
            return {}
        except (GeminiRateLimitException, GeminiApiException) as e_api:
            logger.warning(f"고유명사 추출 API 호출 실패: {e_api}. 청크: {chunk[:50]}...")
            if retry_count < max_retries:
                logger.info(f"API 오류, 재시도 중 ({retry_count + 1}/{max_retries})...")
                time.sleep( (1 + retry_count) * 2 ) 
                return self._extract_pronouns_from_chunk_via_api(chunk, retry_count + 1, max_retries)
            else:
                logger.error(f"최대 재시도 횟수 초과 (API 오류).")
                raise BtgApiClientException(f"고유명사 추출 API 호출 최대 재시도 실패: {e_api}", original_exception=e_api) from e_api
        except Exception as e:
            logger.error(f"고유명사 추출 중 예상치 못한 오류: {e}. 청크: {chunk[:50]}...")
            raise BtgPronounException(f"고유명사 추출 중 오류: {e}", original_exception=e) from e


    def _select_sample_chunks(self, all_chunks: List[str]) -> List[str]:
        """전체 청크 리스트에서 표본 청크를 선택합니다."""
        sample_ratio = self.config.get("pronoun_sample_ratio", 25.0) / 100.0 
        if not (0 < sample_ratio <= 1.0):
            logger.warning(f"잘못된 pronoun_sample_ratio 값: {sample_ratio*100}%. 25%로 조정합니다.")
            sample_ratio = 0.25
        
        total_chunks = len(all_chunks)
        if total_chunks == 0:
            return []
        
        sample_size = max(1, int(total_chunks * sample_ratio))
        
        if sample_size >= total_chunks: 
            return all_chunks
            
        selected_indices = sorted(random.sample(range(total_chunks), sample_size))
        return [all_chunks[i] for i in selected_indices]

    def _merge_pronouns_info(self, new_pronouns: Dict[str, Any]):
        """
        새롭게 추출된 고유명사 정보를 기존 self.pronouns_dict에 병합합니다.
        """
        with self._lock:
            for foreign, data in new_pronouns.items():
                if not isinstance(data, dict) or "번역" not in data or "등장횟수" not in data:
                    logger.warning(f"잘못된 형식의 고유명사 데이터 건너뜀: {foreign} - {data}")
                    continue
                
                korean_translation = str(data["번역"])
                try:
                    count = int(data["등장횟수"])
                except ValueError:
                    logger.warning(f"고유명사 '{foreign}'의 등장횟수 '{data['등장횟수']}'가 숫자가 아닙니다. 1로 처리합니다.")
                    count = 1
                
                if foreign in self.pronouns_dict:
                    self.pronouns_dict[foreign]["등장횟수"] = int(self.pronouns_dict[foreign]["등장횟수"]) + count
                else:
                    self.pronouns_dict[foreign] = {"번역": korean_translation, "등장횟수": count}
    
    def _get_pronoun_file_paths(self, input_file_path: Union[str, Path]) -> Tuple[Path, Path]:
        """입력 파일 경로를 기반으로 _seed.csv와 _fallen.csv 파일 경로를 생성합니다."""
        p = Path(input_file_path)
        base_name = p.stem
        output_dir = p.parent
        
        seed_path = output_dir / f"{base_name}_seed.csv"
        fallen_path = output_dir / f"{base_name}_fallen.csv" 
        return seed_path, fallen_path

    def _update_and_save_pronoun_files(self, input_file_path: Union[str, Path], is_final_save: bool = False):
        """
        현재 self.pronouns_dict를 기반으로 _seed.csv와 _fallen.csv (임시) 파일을 업데이트합니다.
        """
        seed_path, fallen_path = self._get_pronoun_file_paths(input_file_path)
        
        sorted_pronouns_list = sorted(
            self.pronouns_dict.items(), 
            key=lambda item: int(item[1].get("등장횟수", 0)), 
            reverse=True
        )
        
        max_entries = self.config.get("max_pronoun_entries", 20)
        
        top_items_to_save = []
        for foreign, data in sorted_pronouns_list[:max_entries]:
            top_items_to_save.append([foreign, str(data.get("번역","")), str(data.get("등장횟수",0))])
        
        try:
            write_csv_file(seed_path, top_items_to_save, header=PRONOUN_CSV_HEADER)
            logger.debug(f"{seed_path}에 상위 {len(top_items_to_save)}개 고유명사 저장됨.")
        except Exception as e:
            logger.error(f"{seed_path} 저장 중 오류: {e}")
            raise BtgPronounException(f"{seed_path} 저장 실패", original_exception=e)

        if not is_final_save: 
            remaining_items_to_save = []
            if len(sorted_pronouns_list) > max_entries:
                for foreign, data in sorted_pronouns_list[max_entries:]:
                     remaining_items_to_save.append([foreign, str(data.get("번역","")), str(data.get("등장횟수",0))])
            try:
                write_csv_file(fallen_path, remaining_items_to_save, header=PRONOUN_CSV_HEADER)
                logger.debug(f"{fallen_path}에 나머지 {len(remaining_items_to_save)}개 고유명사 저장됨.")
            except Exception as e:
                logger.error(f"{fallen_path} 저장 중 오류: {e}")
        
        elif is_final_save and fallen_path.exists():
            try:
                delete_file(fallen_path)
                logger.info(f"최종 저장: 임시 파일 {fallen_path} 삭제됨.")
            except Exception as e:
                logger.warning(f"임시 파일 {fallen_path} 삭제 중 오류: {e}")


    def extract_and_save_pronouns_from_text_chunks(self, 
                                                 all_text_chunks: List[str], 
                                                 input_file_path_for_naming: Union[str, Path],
                                                 progress_callback: Optional[Callable[[PronounExtractionProgressDTO], None]] = None
                                                 ) -> Path: # tqdm_file_stream 제거, progress_callback 타입 변경
        """
        주어진 텍스트 청크 리스트에서 고유명사를 추출하고 _seed.csv 파일에 저장합니다.

        Args:
            all_text_chunks (List[str]): 분석할 전체 텍스트 청크 리스트.
            input_file_path_for_naming (Union[str, Path]): 
                출력 CSV 파일 이름 생성에 사용될 원본 입력 파일 경로.
            progress_callback (Optional[Callable[[PronounExtractionProgressDTO], None]], optional): 
                진행 상황을 알리기 위한 콜백 함수.

        Returns:
            Path: 생성된 _seed.csv 파일의 경로.

        Raises:
            BtgPronounException: 고유명사 추출 또는 저장 과정에서 심각한 오류 발생 시.
        """
        self.pronouns_dict = {} 
        sample_chunks = self._select_sample_chunks(all_text_chunks)
        num_sample_chunks = len(sample_chunks)

        if not sample_chunks:
            logger.info("고유명사 추출을 위한 표본 청크가 없습니다.")
            seed_path, _ = self._get_pronoun_file_paths(input_file_path_for_naming)
            write_csv_file(seed_path, [], header=PRONOUN_CSV_HEADER) # 빈 파일 생성
            if progress_callback: # 콜백 호출 (0/0)
                progress_callback(PronounExtractionProgressDTO(
                    total_sample_chunks=0, 
                    processed_sample_chunks=0, 
                    current_status_message="표본 청크 없음"
                ))
            return seed_path

        logger.info(f"총 {len(all_text_chunks)}개 청크 중 {num_sample_chunks}개의 표본 청크로 고유명사 추출 시작...")
        
        max_workers = self.config.get("max_workers", os.cpu_count() or 1) # config에서 max_workers 가져오기
        
        processed_count = 0
        if progress_callback: # 초기 콜백 호출
            progress_callback(PronounExtractionProgressDTO(
                total_sample_chunks=num_sample_chunks, 
                processed_sample_chunks=0, 
                current_status_message="추출 시작 중..."
            ))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._extract_pronouns_from_chunk_via_api, chunk): chunk 
                       for chunk in sample_chunks}
            
            for future in as_completed(futures):
                chunk_processed = futures[future] # 어떤 청크에 대한 결과인지 확인 (로깅용)
                try:
                    new_pronouns = future.result()
                    if new_pronouns:
                        self._merge_pronouns_info(new_pronouns)
                except Exception as exc:
                    logger.error(f"표본 청크 처리 중 예외 발생 (청크: {chunk_processed[:50]}...): {exc}")
                finally:
                    processed_count += 1
                    if progress_callback:
                        status_msg = f"표본 청크 {processed_count}/{num_sample_chunks} 처리 완료"
                        if processed_count == num_sample_chunks:
                            status_msg = "모든 표본 청크 처리 완료, 결과 병합 중..."
                        progress_callback(PronounExtractionProgressDTO(
                            total_sample_chunks=num_sample_chunks, 
                            processed_sample_chunks=processed_count, 
                            current_status_message=status_msg
                        ))
        
        self._update_and_save_pronoun_files(input_file_path_for_naming, is_final_save=True)
        
        seed_path, _ = self._get_pronoun_file_paths(input_file_path_for_naming)
        logger.info(f"고유명사 추출 완료. 결과가 {seed_path}에 저장되었습니다.")
        if progress_callback: # 최종 완료 콜백
            progress_callback(PronounExtractionProgressDTO(
                total_sample_chunks=num_sample_chunks, 
                processed_sample_chunks=processed_count, 
                current_status_message=f"추출 완료: {seed_path.name}"
            ))
        return seed_path


if __name__ == '__main__':
    # PronounService 테스트를 위한 간단한 예제 코드
    # 실행 전 GOOGLE_API_KEY 환경 변수 설정 필요
    
    if 'GeminiClient' not in globals(): 
        class MockGeminiClient:
            _call_count = 0
            def __init__(self, auth_credentials, project=None, location=None): # auth_credentials로 변경
                logger.info(f"MockGeminiClient initialized with auth_credentials: {'*' * len(str(auth_credentials)) if auth_credentials else 'None'}")
            
            def generate_text(self, prompt, model_name, generation_config_dict, safety_settings_list_of_dicts=None, stream=False, system_instruction=None): # system_instruction 추가
                MockGeminiClient._call_count +=1
                logger.info(f"MockGeminiClient.generate_text call #{MockGeminiClient._call_count} for model: {model_name}")
                
                if "청크1" in prompt:
                    return json.dumps({
                        "Alice": {"번역": "앨리스", "등장횟수": 3},
                        "Wonderland": {"번역": "이상한 나라", "등장횟수": 2}
                    })
                elif "청크2" in prompt:
                    if MockGeminiClient._call_count % 3 == 0: 
                         logger.warning("Mock 콘텐츠 안전 문제 발생 시뮬레이션")
                         raise GeminiContentSafetyException("Mock 콘텐츠 안전 문제")
                    return json.dumps({
                        "Bob": {"번역": "밥", "등장횟수": 5},
                        "Wonderland": {"번역": "이상한 나라", "등장횟수": 1} 
                    })
                elif "청크3" in prompt:
                    if MockGeminiClient._call_count % 2 == 0: 
                        logger.warning("Mock API 사용량 제한 시뮬레이션")
                        raise GeminiRateLimitException("Mock API 사용량 제한")
                    return json.dumps({
                        "Charlie": {"번역": "찰리", "등장횟수": 1}
                    })
                return "{}" 
        
        GeminiClient = MockGeminiClient 

    # --- 테스트 설정 ---
    test_auth_creds = os.environ.get("GOOGLE_API_KEY", "test_key_if_not_set") # API 키 또는 SA JSON 문자열
    
    sample_config_pronoun = {
        "api_key": test_auth_creds if isinstance(test_auth_creds, str) and not test_auth_creds.startswith('{') else "", # 단일 키인 경우
        "api_keys": [test_auth_creds] if isinstance(test_auth_creds, str) and not test_auth_creds.startswith('{') else [],
        "auth_credentials": test_auth_creds, # GeminiClient가 처리하도록 전달
        "model_name": "gemini-1.5-flash-latest", 
        "pronoun_extraction_temperature": 0.1,
        "pronoun_sample_ratio": 50.0, 
        "max_pronoun_entries": 3,    
        "max_workers": 2,
        "top_p": 0.9 # 추가
    }

    # --- 테스트 실행 ---
    logger.info("PronounService 테스트 시작...")
    
    try:
        gemini_client_instance = GeminiClient(auth_credentials=sample_config_pronoun["auth_credentials"])
        pronoun_service = PronounService(gemini_client_instance, sample_config_pronoun)

        # all_text_chunks_data 정의 추가
        all_text_chunks_data = [
            "첫 번째 청크1 내용입니다. Alice가 Wonderland에 갔습니다.",
            "두 번째 청크2는 Bob과 Wonderland에 대한 이야기입니다.",
            "세 번째 청크3에는 Charlie가 등장합니다.",
            "네 번째 청크에는 특별한 고유명사가 없습니다.",
            "다섯 번째 청크1는 Alice가 다시 등장하는 내용입니다."
        ]
        
        test_output_dir = Path("test_pronoun_service_output")
        ensure_dir_exists(test_output_dir)
        dummy_input_file = test_output_dir / "sample_novel.txt"

        def mock_progress_dto_cb(dto: PronounExtractionProgressDTO): # DTO를 받는 콜백
            logger.info(f"고유명사 진행 DTO: {dto.processed_sample_chunks}/{dto.total_sample_chunks} - {dto.current_status_message}")

        print("\n--- 고유명사 추출 및 저장 테스트 ---")
        try:
            seed_p, fallen_p = pronoun_service._get_pronoun_file_paths(dummy_input_file)
            delete_file(seed_p)
            delete_file(fallen_p)

            result_seed_path = pronoun_service.extract_and_save_pronouns_from_text_chunks(
                all_text_chunks_data, 
                dummy_input_file,
                progress_callback=mock_progress_dto_cb # 수정된 콜백 전달
            )
            print(f"결과 _seed.csv 파일 경로: {result_seed_path}")
            assert result_seed_path.exists()

            if result_seed_path.exists():
                saved_pronouns = []
                with open(result_seed_path, 'r', encoding='utf-8', newline='') as f:
                    reader = csv.reader(f)
                    header = next(reader)
                    assert header == PRONOUN_CSV_HEADER
                    for row in reader:
                        saved_pronouns.append(row)
                
                print(f"_seed.csv 내용: {saved_pronouns}")
                assert len(saved_pronouns) <= sample_config_pronoun["max_pronoun_entries"]
                if saved_pronouns:
                     assert len(saved_pronouns[0]) == 3 
                assert not fallen_p.exists() # 최종 저장 시 fallen 파일은 삭제되어야 함
        except Exception as e:
            print(f"고유명사 추출 테스트 오류: {type(e).__name__} - {e}")
            logger.error("고유명사 추출 테스트 중 오류 발생", exc_info=True)


    except Exception as e:
        logger.error(f"PronounService 테스트 중 전역 오류 발생: {e}", exc_info=True)
    finally:
        # import shutil
        # if test_output_dir.exists():
        #     shutil.rmtree(test_output_dir)
        #     logger.info(f"테스트 디렉토리 '{test_output_dir}' 삭제됨.")
        pass

    logger.info("PronounService 테스트 종료.")
