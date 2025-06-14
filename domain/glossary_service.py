# c:\Users\Hyunwoo_Room\Downloads\Neo_Batch_Translator\glossary_service.py
import json
import random
import re
import time
import os
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Union, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException
    from infrastructure.file_handler import write_json_file, ensure_dir_exists, delete_file, read_json_file
    from infrastructure.logger_config import setup_logger
    from utils.chunk_service import ChunkService
    from core.exceptions import BtgBusinessLogicException, BtgApiClientException, BtgFileHandlerException
    from core.dtos import GlossaryExtractionProgressDTO, GlossaryEntryDTO
except ImportError:
    # 단독 실행 또는 다른 경로에서의 import를 위한 fallback
    from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException # type: ignore
    from infrastructure.file_handler import write_json_file, ensure_dir_exists, delete_file, read_json_file # type: ignore
    from utils.chunk_service import ChunkService # type: ignore
    from infrastructure.logger_config import setup_logger # type: ignore
    from core.exceptions import BtgBusinessLogicException, BtgApiClientException, BtgFileHandlerException # type: ignore
    from core.dtos import GlossaryExtractionProgressDTO, GlossaryEntryDTO # type: ignore

logger = setup_logger(__name__)

class SimpleGlossaryService:
    """
    텍스트에서 간단한 용어집 항목(원본 용어, 번역된 용어, 출발/도착 언어, 등장 횟수)을
    추출하고 관리하는 비즈니스 로직을 담당합니다. (경량화 버전)
    """
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]):
        """
        SimpleGlossaryService를 초기화합니다.

        Args:
            gemini_client (GeminiClient): Gemini API와 통신하기 위한 클라이언트.
            config (Dict[str, Any]): 애플리케이션 설정 (주로 파일명 접미사 등).
        """
        self.gemini_client = gemini_client
        self.config = config
        self.chunk_service = ChunkService() # ChunkService 인스턴스화
        # self.all_extracted_entries: List[GlossaryEntryDTO] = [] # 추출된 모든 항목 (필요시 멤버 변수로, 아니면 로컬 변수로)
        self._lock = threading.Lock() # 병렬 처리 시 공유 자원 접근 동기화용
    
    def _get_glossary_extraction_prompt(self, segment_text: str, user_override_glossary_prompt: Optional[str] = None) -> str:
        """용어집 항목 추출을 위한 프롬프트를 생성합니다."""
        if user_override_glossary_prompt and user_override_glossary_prompt.strip():
            base_template = user_override_glossary_prompt
            logger.info("사용자 재정의 용어집 추출 프롬프트를 사용합니다.")
        else:
            base_template = self.config.get(
                "simple_glossary_extraction_prompt_template",
                ("Analyze the following text. Identify key terms, focusing specifically on "
                 "**people (characters), proper nouns (e.g., unique items, titles, artifacts), "
                 "place names (locations, cities, countries, specific buildings), and organization names (e.g., companies, groups, factions, schools)**. "
                 "For each identified term, provide its translation into {target_lang_name} (BCP-47: {target_lang_code}), "
                 "and estimate their occurrence count in this segment.\n"
                 "Each item in the 'terms' array should have 'keyword' (original term), "
                 "'translated_keyword' (the translation), "
                 "'target_language' (BCP-47 of translated_keyword, should be {target_lang_code}), "
                 "and 'occurrence_count' (estimated count in this segment, integer).\n"
                 "Text: ```\n{novelText}\n```\n"
                 "Respond with a single JSON object containing one key: 'terms', which is an array of the extracted term objects.\n"
                 "Example response:\n"
                 "{\n"
                 "  \"terms\": [\n"
                 "    {\"keyword\": \"猫\", \"translated_keyword\": \"cat\", \"target_language\": \"en\", \"occurrence_count\": 3},\n"
                 "    {\"keyword\": \"犬\", \"translated_keyword\": \"dog\", \"target_language\": \"en\", \"occurrence_count\": 1}\n"
                 "  ]\n"
                 "}\n"
                 "Ensure your entire response is a single valid JSON object.")
            )
        # 경량화된 서비스에서는 사용자가 번역 목표 언어를 명시적으로 제공한다고 가정
        # 또는 설정에서 가져올 수 있음. 여기서는 예시로 "ko" (한국어)를 사용.
        # 실제 구현에서는 이 부분을 동적으로 설정해야 함.
        target_lang_code = self.config.get("glossary_target_language_code", "ko")
        target_lang_name = self.config.get("glossary_target_language_name", "Korean")

        prompt = base_template.replace("{target_lang_code}", target_lang_code)
        prompt = prompt.replace("{target_lang_name}", target_lang_name)
        prompt = prompt.replace("{novelText}", segment_text) # 수정: base_template 대신 prompt 사용
        return prompt

    def _parse_raw_glossary_items_to_dto( # 함수명 변경
        self,
        raw_item_list: List[Dict[str, Any]],
        # source_language_code는 DTO에서 제거되므로 파라미터 불필요   
    ) -> List[GlossaryEntryDTO]: # 반환 타입 변경
        """
        API 응답 등으로 받은 원시 용어집 항목 딕셔너리 리스트를 GlossaryEntryDTO 리스트로 변환합니다.
        """
        glossary_entries: List[GlossaryEntryDTO] = [] # 변수명 변경
        if not isinstance(raw_item_list, list):
            logger.warning(f"용어집 항목 데이터가 리스트가 아닙니다: {type(raw_item_list)}. 원본: {str(raw_item_list)[:200]}")
            return glossary_entries

        for item_dict in raw_item_list:
            if isinstance(item_dict, dict) and \
               "keyword" in item_dict and \
               "translated_keyword" in item_dict and \
               "target_language" in item_dict:
                entry_data = {
                    "keyword": item_dict.get("keyword"),
                    "translated_keyword": item_dict.get("translated_keyword"),
                    "target_language": item_dict.get("target_language"),
                    "occurrence_count": int(item_dict.get("occurrence_count", 0))
                }
                if not all(entry_data.get(key) for key in ["keyword", "translated_keyword", "target_language"]): # source_language 제거                  
                    logger.warning(f"필수 필드 누락된 용어집 항목 건너뜀: {item_dict}")
                    continue
                glossary_entries.append(GlossaryEntryDTO(**entry_data)) # DTO 변경
            else:
                logger.warning(f"잘못된 용어집 항목 형식 건너뜀: {item_dict}")
        return glossary_entries

    # _get_conflict_resolution_prompt, _group_similar_keywords_via_api 메서드는 경량화로 인해 제거 또는 대폭 단순화.
    # 여기서는 제거하는 것으로 가정. 필요하다면 매우 단순한 형태로 재구현.

    def _extract_glossary_entries_from_segment_via_api( # 함수명 변경
        self,
        segment_text: str,
        user_override_glossary_prompt: Optional[str] = None
    ) -> List[GlossaryEntryDTO]: # 반환 타입 변경
        """
        단일 텍스트 세그먼트에서 Gemini API를 사용하여 용어집 항목들을 추출합니다.
        GeminiClient의 내장 재시도 로직을 활용합니다.       
        """
        prompt = self._get_glossary_extraction_prompt(segment_text, user_override_glossary_prompt)
        model_name = self.config.get("model_name", "gemini-2.0-flash")
        generation_config_params = { # 변수명 변경 (선택 사항이지만, 명확성을 위해)
            "temperature": self.config.get("glossary_extraction_temperature", 0.3), # 단순 추출이므로 약간 높여도 됨
            "response_mime_type": "application/json",
        }

        try:
            response_data = self.gemini_client.generate_text(
                prompt=prompt,
                model_name=model_name,
                generation_config_dict=generation_config_params # 호출 시 인자명 수정
            )

            if isinstance(response_data, dict):                
                logger.debug("GeminiClient가 파싱된 딕셔너리(JSON 객체)를 반환했습니다.")
                # 프롬프트는 'terms' 키를 가진 객체를 요청
                raw_terms = response_data.get("terms")

                if isinstance(raw_terms, list):
                    return self._parse_raw_glossary_items_to_dto(raw_terms) # 변경된 함수 호출
                else:
                    logger.warning(f"API 응답 JSON 객체에 'detected_language_code' 또는 'entities' 필드가 누락/잘못되었습니다: {response_data}")
                    return [] # 유효한 'terms'가 없으면 빈 리스트 반환
            elif response_data is None:
                logger.warning(f"용어집 추출 API로부터 응답을 받지 못했습니다 (GeminiClient가 None 반환). 세그먼트: {segment_text[:50]}...")
                return []
            elif isinstance(response_data, str): # GeminiClient가 JSON 파싱에 실패하여 문자열을 반환한 경우
                logger.warning(f"GeminiClient가 JSON 파싱에 실패하여 문자열을 반환했습니다. 응답: {response_data[:200]}... 세그먼트: {segment_text[:50]}...")
                # 이 경우, API가 유효하지 않은 JSON을 생성했거나 GeminiClient의 파서에 문제가 있을 수 있습니다.
                # 서비스 레벨에서 단순 API 재시도는 도움이 되지 않을 가능성이 높습니다.                          
                return []
            else:
                logger.warning(f"GeminiClient로부터 예상치 않은 타입의 응답 ({type(response_data)})을 받았습니다. 세그먼트: {segment_text[:50]}...")              
                return []
            
        except GeminiApiException as e_api: # GeminiClient의 자체 재시도 후에도 해결되지 않은 API 오류
            logger.error(f"용어집 추출 API 호출 최종 실패 (GeminiClient 재시도 후 발생): {e_api}. 세그먼트: {segment_text[:50]}...")
            raise BtgApiClientException(f"용어집 추출 API 호출 최종 실패: {e_api}", original_exception=e_api) from e_api       
        except Exception as e:
            logger.error(f"용어집 추출 중 예상치 못한 내부 오류: {e}. 세그먼트: {segment_text[:50]}...", exc_info=True)
            # DTO 변환 등 이 메서드 내 다른 로직에서 발생할 수 있는 예외
            raise BtgBusinessLogicException(f"용어집 추출 중 내부 오류: {e}", original_exception=e) from e


    def _select_sample_segments(self, all_segments: List[str]) -> List[str]:
        """전체 세그먼트 리스트에서 표본 세그먼트를 선택합니다."""
        # 샘플링 방식 설정 (uniform, random, importance-based 등)
        sampling_method = self.config.get("glossary_sampling_method", "uniform") # 설정 키 변경
        sample_ratio = self.config.get("glossary_sampling_ratio", 10.0) / 100.0 # 기본 샘플링 비율 낮춤 (경량화)
        
        if not (0 < sample_ratio <= 1.0):
            logger.warning(f"잘못된 lorebook_sampling_ratio 값: {sample_ratio*100}%. 25%로 조정합니다.")
            sample_ratio = 0.25
        
        total_segments = len(all_segments)
        if total_segments == 0:
            return []
        
        sample_size = max(1, int(total_segments * sample_ratio))
        
        if sample_size >= total_segments: 
            return all_segments
        
        if sampling_method == "random":
            selected_indices = sorted(random.sample(range(total_segments), sample_size))
        elif sampling_method == "uniform": # 균등 샘플링
            step = total_segments / sample_size
            selected_indices = sorted(list(set(int(i * step) for i in range(sample_size)))) # 중복 제거 및 정렬
            # sample_size보다 적게 선택될 수 있으므로, 부족분은 랜덤으로 채우거나 앞부분에서 채움
            if len(selected_indices) < sample_size:
                additional_needed = sample_size - len(selected_indices)
                remaining_indices = [i for i in range(total_segments) if i not in selected_indices]
                if len(remaining_indices) >= additional_needed:
                    selected_indices.extend(random.sample(remaining_indices, additional_needed))
                else: # 남은 인덱스가 부족하면 모두 추가
                    selected_indices.extend(remaining_indices)
                selected_indices = sorted(list(set(selected_indices)))

        # TODO: "importance-based" 샘플링 구현 (예: 특정 키워드 포함 세그먼트 우선)
        else: # 기본은 랜덤
            selected_indices = sorted(random.sample(range(total_segments), sample_size))
            
        return [all_segments[i] for i in selected_indices]

    def _get_lorebook_output_path(self, input_file_path: Union[str, Path]) -> Path:
        """입력 파일 경로를 기반으로 로어북 JSON 파일 경로를 생성합니다."""
        p_input = Path(input_file_path)
        base_name = p_input.stem
        output_dir = p_input.parent
        suffix = self.config.get("glossary_output_json_filename_suffix", "_glossary.json") # 설정 키 변경
        return output_dir / f"{base_name}{suffix}" # 파일명 변경

    def _save_glossary_to_json(self, glossary_entries: List[GlossaryEntryDTO], output_path: Path): # 함수명 및 DTO 변경
        """용어집 항목 리스트를 JSON 파일로 저장합니다."""
        # dataclass 객체를 dict 리스트로 변환
        data_to_save = [entry.__dict__ for entry in glossary_entries]
        try:
            write_json_file(output_path, data_to_save, indent=4) # file_handler 사용
            logger.info(f"용어집이 {output_path}에 저장되었습니다. 총 {len(glossary_entries)}개 항목.")
        except Exception as e:
            logger.error(f"용어집 JSON 파일 저장 중 오류 ({output_path}): {e}")
            raise BtgFileHandlerException(f"용어집 JSON 파일 저장 실패: {output_path}", original_exception=e) from e

    def _resolve_glossary_conflicts(self, all_extracted_entries: List[GlossaryEntryDTO]) -> List[GlossaryEntryDTO]: # 함수명 및 DTO 변경
        """추출된 용어집 항목들의 충돌을 해결합니다. (경량화 버전: 중복 제거 및 등장 횟수 합산)"""
        if not all_extracted_entries:
            return []

        logger.info(f"용어집 충돌 해결 시작. 총 {len(all_extracted_entries)}개 항목 검토 중...")
        
        # (keyword, target_language)를 키로 사용하여 그룹화 및 등장 횟수 합산       
        # translated_keyword는 첫 번째 등장한 것을 사용하거나, 가장 긴 것을 사용하는 등의 규칙 적용 가능
        # 여기서는 첫 번째 등장한 translated_keyword를 사용
        final_entries_map: Dict[Tuple[str, str], GlossaryEntryDTO] = {} # 키에서 source_language 제거

        for entry in all_extracted_entries:
            key_tuple = (entry.keyword.lower(), entry.target_language.lower()) # 키에서 source_language 제거
            if key_tuple not in final_entries_map:
                final_entries_map[key_tuple] = entry
            else:
                # 이미 존재하는 키이면 등장 횟수만 합산
                final_entries_map[key_tuple].occurrence_count += entry.occurrence_count
        
        final_glossary = list(final_entries_map.values())
        # 최종 용어집 정렬 (예: 키워드, 도착언어 순)
        final_glossary.sort(key=lambda x: (x.keyword.lower(), x.target_language.lower())) # 정렬 키에서 source_language 제거
              
        logger.info(f"용어집 충돌 해결 완료. 최종 {len(final_glossary)}개 항목.")
        return final_glossary

    def _select_best_entry_from_group(self, entry_group: List[GlossaryEntryDTO]) -> GlossaryEntryDTO: # DTO 변경
        """주어진 용어집 항목 그룹에서 가장 좋은 항목을 선택합니다 (예: 가장 긴 설명, 가장 높은 중요도)."""
        if not entry_group:
            raise ValueError("빈 용어집 항목 그룹에서 최선 항목을 선택할 수 없습니다.")
        # 경량화 버전에서는 복잡한 선택 로직 대신 첫 번째 항목 반환 또는 등장 횟수 많은 것 선택 등
        entry_group.sort(key=lambda e: (-e.occurrence_count, e.keyword.lower())) # 등장 횟수 많은 순, 같으면 키워드 순
        return entry_group[0]

    def extract_and_save_glossary(self, # 함수명 변경
                                  # all_text_segments: List[str], # 직접 세그먼트 리스트를 받는 대신 원본 텍스트를 받도록 변경
                                  novel_text_content: str, # 원본 텍스트 내용
                                  input_file_path_for_naming: Union[str, Path],
                                  # novel_language_code: Optional[str] = None, # LLM이 감지하므로 불필요
                                  progress_callback: Optional[Callable[[GlossaryExtractionProgressDTO], None]] = None, # DTO 변경
                                  seed_glossary_path: Optional[Union[str, Path]] = None, # 시드 용어집 경로 추가
                                  user_override_glossary_extraction_prompt: Optional[str] = None # 사용자 재정의 프롬프트
                                 ) -> Path:
        """
        주어진 텍스트 내용에서 로어북을 추출하고 JSON 파일에 저장합니다.

        Args:
            novel_text_content (str): 분석할 전체 텍스트 내용.
            input_file_path_for_naming (Union[str, Path]):
                출력 JSON 파일 이름 생성에 사용될 원본 입력 파일 경로.
            progress_callback (Optional[Callable[[GlossaryExtractionProgressDTO], None]], optional): # DTO 변경
                진행 상황을 알리기 위한 콜백 함수.
            seed_glossary_path (Optional[Union[str, Path]], optional):
                참고할 기존 용어집 JSON 파일 경로.
            user_override_glossary_extraction_prompt (Optional[str], optional):
                용어집 추출 시 사용할 사용자 정의 프롬프트. 제공되면 기본 프롬프트를 대체합니다.

        Returns:
            Path: 생성된 로어북 JSON 파일의 경로.

        Raises:
            BtgBusinessLogicException: 용어집 추출 또는 저장 과정에서 심각한 오류 발생 시.
        """
        all_extracted_entries_from_segments: List[GlossaryEntryDTO] = [] # DTO 변경
        seed_entries: List[GlossaryEntryDTO] = [] # DTO 변경

        if seed_glossary_path:
            seed_path_obj = Path(seed_glossary_path)
            if seed_path_obj.exists() and seed_path_obj.is_file():
                try:
                    logger.info(f"시드 용어집 파일 로드 중: {seed_path_obj}")
                    raw_seed_data = read_json_file(seed_path_obj)
                    if isinstance(raw_seed_data, list):
                        for item_dict in raw_seed_data:
                            if isinstance(item_dict, dict) and "keyword" in item_dict and \
                               "translated_keyword" in item_dict and \
                               "target_language" in item_dict:
                                try:
                                    entry = GlossaryEntryDTO( # DTO 변경
                                        keyword=item_dict.get("keyword", ""),
                                        translated_keyword=item_dict.get("translated_keyword", ""),
                                        target_language=item_dict.get("target_language", ""),
                                        occurrence_count=int(item_dict.get("occurrence_count", 0))
                                    )
                                    if entry.keyword and entry.translated_keyword:
                                        seed_entries.append(entry)
                                except (TypeError, ValueError) as e_dto:
                                    logger.warning(f"시드 용어집 항목 DTO 변환 중 오류: {item_dict}, 오류: {e_dto}")
                        logger.info(f"{len(seed_entries)}개의 시드 용어집 항목 로드 완료.")
                except Exception as e_seed:
                    logger.error(f"시드 용어집 파일 로드 중 오류 ({seed_path_obj}): {e_seed}", exc_info=True)
            else:
                logger.warning(f"제공된 시드 용어집 경로를 찾을 수 없거나 파일이 아닙니다: {seed_glossary_path}")
        
        # ChunkService를 사용하여 텍스트를 세그먼트로 분할
        # 경량화로 인해 청크 크기 설정을 단순화하거나 제거할 수 있음. 여기서는 유지.
        glossary_segment_size = self.config.get("glossary_chunk_size", self.config.get("chunk_size", 8000))
        all_text_segments = self.chunk_service.create_chunks_from_file_content(novel_text_content, glossary_segment_size)

        sample_segments = self._select_sample_segments(all_text_segments)
        num_sample_segments = len(sample_segments)

        # 진행률 표시를 위한 유효 총 세그먼트 수 계산
        # 표본 세그먼트가 없더라도 시드 처리 등의 작업이 있으면 1로 설정
        effective_total_segments_for_progress = num_sample_segments
        if num_sample_segments == 0 and seed_entries:
            effective_total_segments_for_progress = 1 # 시드 처리 작업을 1개의 단위로 간주
        elif num_sample_segments == 0 and not novel_text_content.strip() and not seed_entries:
            effective_total_segments_for_progress = 0 # 아무 작업도 없는 경우 (또는 1로 하여 즉시 완료 표시 가능)

        if not novel_text_content.strip() and not sample_segments and not seed_entries:
            logger.info("입력 텍스트가 비어있고, 표본 세그먼트 및 시드 용어집도 없습니다. 빈 용어집을 생성합니다.")
            lorebook_output_path = self._get_lorebook_output_path(input_file_path_for_naming)
            self._save_glossary_to_json([], lorebook_output_path) # 빈 용어집 파일 생성, 함수명 변경
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO( # DTO 변경
                    total_segments=effective_total_segments_for_progress, # 0 또는 1
                    processed_segments=0,
                    current_status_message="입력 텍스트 및 시드 없음",
                    extracted_entries_count=0
                ))
            return lorebook_output_path
        elif not novel_text_content.strip() and not sample_segments and seed_entries:
            logger.info("입력 텍스트가 비어있고 표본 세그먼트가 없습니다. 시드 용어집만으로 처리합니다.")
            all_extracted_entries_from_segments.extend(seed_entries)
            # 추출 과정 없이 바로 충돌 해결 및 저장으로 넘어감
            if progress_callback: # 초기 콜백
                progress_callback(GlossaryExtractionProgressDTO( # DTO 변경
                    total_segments=effective_total_segments_for_progress, # 1로 설정됨
                    processed_segments=0, # 아직 처리 시작 전
                    current_status_message="시드 용어집 처리 중...", extracted_entries_count=len(seed_entries)
                ))
        elif sample_segments: # Process sample_segments
            logger.info(f"총 {len(all_text_segments)}개 세그먼트 중 {num_sample_segments}개의 표본 세그먼트로 용어집 추출 시작...")
        
            max_workers = self.config.get("max_workers", os.cpu_count() or 1)
            processed_segments_count = 0
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO( # DTO 변경
                    total_segments=effective_total_segments_for_progress, # num_sample_segments와 동일
                    processed_segments=processed_segments_count,
                    current_status_message="추출 시작 중...",
                    extracted_entries_count=len(seed_entries) # 시드 항목 수 포함
                ))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_segment = {
                    executor.submit(
                        self._extract_glossary_entries_from_segment_via_api,
                        segment,
                        user_override_glossary_extraction_prompt): segment
                           for segment in sample_segments}
                
                for future in as_completed(future_to_segment):
                    segment_processed_text = future_to_segment[future]
                    try:
                        extracted_entries_for_segment = future.result()
                        if extracted_entries_for_segment:
                            with self._lock: # 여러 스레드가 동시에 리스트에 추가할 수 있으므로 동기화
                                all_extracted_entries_from_segments.extend(extracted_entries_for_segment)
                    except Exception as exc:
                        logger.error(f"표본 세그먼트 처리 중 예외 발생 (세그먼트: {segment_processed_text[:50]}...): {exc}")
                    finally:
                        processed_segments_count += 1
                        if progress_callback:
                            status_msg = f"표본 세그먼트 {processed_segments_count}/{num_sample_segments} 처리 완료"
                            if processed_segments_count == num_sample_segments:
                                status_msg = "모든 표본 세그먼트 처리 완료, 충돌 해결 및 저장 중..."
                            progress_callback(GlossaryExtractionProgressDTO( # DTO 변경
                                total_segments=effective_total_segments_for_progress,
                                processed_segments=processed_segments_count,
                                current_status_message=status_msg,
                                extracted_entries_count=len(all_extracted_entries_from_segments) + len(seed_entries)
                            ))
        # 시드 항목이 있고, 새로운 추출도 있었다면 병합
        if seed_entries and (novel_text_content.strip() and sample_segments): # Check if new extraction happened
            logger.info(f"{len(seed_entries)}개의 시드 항목을 새로 추출된 항목과 병합합니다.")
            all_extracted_entries_from_segments.extend(seed_entries)
        elif not (novel_text_content.strip() and sample_segments) and seed_entries: # No new extraction, only seed
            # all_extracted_entries_from_segments already contains seed_entries if this branch is hit
            pass

        # 모든 세그먼트 처리 후 또는 시드만 있는 경우 충돌 해결
        final_glossary = self._resolve_glossary_conflicts(all_extracted_entries_from_segments) # 함수명 및 변수명 변경
        
        # _update_occurrence_counts 호출 제거. LLM 추정치 또는 시드 파일의 등장 횟수만 사용.
        # 로어북 최대 항목 수 제한 (설정값 사용)
        max_total_glossary_entries = self.config.get("glossary_max_total_entries", 500) # 기본값 줄임
        if len(final_glossary) > max_total_glossary_entries:
            logger.info(f"추출된 용어집 항목({len(final_glossary)}개)이 최대 제한({max_total_glossary_entries}개)을 초과하여 상위 항목만 저장합니다.")
            # 중요도 등으로 정렬되어 있으므로 상위 항목 선택
            final_glossary = final_glossary[:max_total_glossary_entries]

        # 최종 저장 전, 등장 횟수(내림차순), 키워드(오름차순) 순으로 정렬
        final_glossary.sort(key=lambda x: (-x.occurrence_count, x.keyword.lower()))
        logger.info(f"최종 용어집을 등장 횟수 순으로 정렬했습니다. (상위 3개: {[e.keyword for e in final_glossary[:3]]})")

        # 최종 로어북 저장
        glossary_output_path = self._get_lorebook_output_path(input_file_path_for_naming) # 함수명 변경 (내부적으로 파일명 접미사 변경)
        self._save_glossary_to_json(final_glossary, glossary_output_path) # 함수명 변경
        
        logger.info(f"용어집 추출 및 저장 완료. 결과: {glossary_output_path}")

        # 최종 진행률 콜백
        if progress_callback:
            final_processed_segments = processed_segments_count
            if effective_total_segments_for_progress == 1 and num_sample_segments == 0 and seed_entries:
                # 시드만 처리한 경우, 처리 완료로 간주
                final_processed_segments = 1
            elif effective_total_segments_for_progress == 0: # 아무 작업도 없었던 경우
                final_processed_segments = 0

            progress_callback(GlossaryExtractionProgressDTO( # DTO 변경
                total_segments=effective_total_segments_for_progress,
                processed_segments=final_processed_segments,
                current_status_message=f"추출 완료: {glossary_output_path.name}",
                extracted_entries_count=len(final_glossary)
            ))
        return glossary_output_path
