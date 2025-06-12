# c:\Users\Hyunwoo_Room\Downloads\Neo_Batch_Translator\lorebook_service.py
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
    from .gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException
    from .file_handler import write_json_file, ensure_dir_exists, delete_file, read_json_file # read_json_file 추가
    from .logger_config import setup_logger
    from .chunk_service import ChunkService # ChunkService 임포트
    from .exceptions import BtgBusinessLogicException, BtgApiClientException, BtgFileHandlerException
    from .dtos import GlossaryExtractionProgressDTO, GlossaryEntryDTO # DTO 변경
except ImportError:
    # 단독 실행 또는 다른 경로에서의 import를 위한 fallback
    from gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException
    from file_handler import write_json_file, ensure_dir_exists, delete_file, read_json_file # read_json_file 추가
    from chunk_service import ChunkService # ChunkService 임포트
    from logger_config import setup_logger
    from exceptions import BtgBusinessLogicException, BtgApiClientException, BtgFileHandlerException
    from dtos import GlossaryExtractionProgressDTO, GlossaryEntryDTO

logger = setup_logger(__name__)

class LorebookService:
    """
    텍스트에서 로어북 항목(키워드, 설명, 카테고리 등)을 추출하고 관리하는 비즈니스 로직을 담당합니다.
    """
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]): # 클래스명 변경: LorebookService -> GlossaryService
        """
        GlossaryService를 초기화합니다.

        Args:
            gemini_client (GeminiClient): Gemini API와 통신하기 위한 클라이언트.
            config (Dict[str, Any]): 애플리케이션 설정.
                                     (용어집 추출 관련 설정 포함: glossary_sampling_ratio,
                                      glossary_max_entries_per_segment, glossary_extraction_prompt_template 등)
        """
        self.gemini_client = gemini_client
        self.config = config
        self.chunk_service = ChunkService() # ChunkService 인스턴스화
        # self.all_extracted_entries: List[GlossaryEntryDTO] = [] # 추출된 모든 항목 (필요시 멤버 변수로, 아니면 로컬 변수로)
        self._lock = threading.Lock() # 병렬 처리 시 공유 자원 접근 동기화용

    def _get_glossary_extraction_prompt(self, segment_text: str) -> str: # 함수명 변경
        """용어집 항목 추출을 위한 프롬프트를 생성합니다."""
        base_template = self.config.get(
            "glossary_extraction_prompt_template", # 설정 키 변경
            ("First, identify the BCP-47 language code of the following text.\n"
             "Then, using that identified language as the source language for the keywords, extract major terms (characters, places, items, specific concepts, etc.), their aliases, and their term types from the text.\n" # 프롬프트 내용 수정
             "Each item in the 'entities' array should have 'keyword' (the main term), 'description' (in Korean), 'aliases' (a list of alternative names or nicknames), 'term_type' (e.g., PERSON, LOCATION, ORGANIZATION, GENERAL_TERM), 'category', 'importance'(1-10), 'isSpoiler'(true/false) keys.\n" # 필드 추가 명시
             "Summarize descriptions to not exceed {max_chars_per_entry} characters, and extract a maximum of {max_entries_per_segment} items.\n" # aliases, term_type 추가
             "For keyword extraction and alias identification, set sensitivity to {keyword_sensitivity} and prioritize items based on: {priority_settings}.\n"
             "Text: ```\n{novelText}\n```\n"
             "Respond with a single JSON object containing two keys:\n"
             "1. 'detected_language_code': The BCP-47 language code you identified (string).\n"
             "2. 'entities': The JSON array of extracted lorebook entries.\n"
             "Example response:\n"
             "{\n"
             "  \"detected_language_code\": \"ja\",\n"
             "  \"entities\": [\n" # 예시 응답에 aliases, term_type 추가 필요
             "    {\"keyword\": \"主人公\", \"description\": \"物語の主要なキャラクター\", \"aliases\": [\"彼女\", \"그녀\"], \"term_type\": \"PERSON\", \"category\": \"인물\", \"importance\": 10, \"isSpoiler\": false}\n"
             "  ]\n"
             "}\n"
             "Ensure your entire response is a single valid JSON object.")
        )

        prompt = base_template.replace("{novelText}", segment_text)
        prompt = prompt.replace("{max_chars_per_entry}", str(self.config.get("lorebook_max_chars_per_entry", 200)))
        prompt = prompt.replace("{max_entries_per_segment}", str(self.config.get("glossary_max_entries_per_segment", 5))) # 설정 키 변경
        
        # 키워드 민감도 설정값 반영
        keyword_sensitivity = self.config.get("glossary_keyword_sensitivity", "medium") # 설정 키 변경
        prompt = prompt.replace("{keyword_sensitivity}", keyword_sensitivity)

        # 우선순위 설정값 반영 (딕셔셔너리를 문자열로 변환)
        priority_settings_dict = self.config.get("glossary_priority_settings", {"PERSON": 10, "LOCATION": 8, "GENERAL_TERM": 5}) # 예시 우선순위 변경
        priority_settings_str = ", ".join([f"{k}: {v}" for k, v in priority_settings_dict.items()])
        prompt = prompt.replace("{priority_settings}", priority_settings_str)
        
        # 샘플링 방식도 프롬프트에 힌트로 제공 가능 (선택 사항)
        # sampling_method = self.config.get("lorebook_sampling_method", "uniform")
        # prompt += f"\n참고: 이 텍스트는 '{sampling_method}' 방식으로 샘플링되었습니다."
        return prompt

    def _parse_raw_glossary_items_to_dto( # 함수명 변경
        self,
        raw_item_list: List[Dict[str, Any]],
        segment_text_preview: str,
        source_language_code: Optional[str] = None
    ) -> List[GlossaryEntryDTO]: # 반환 타입 변경
        """
        API 응답 등으로 받은 원시 용어집 항목 딕셔너리 리스트를 GlossaryEntryDTO 리스트로 변환합니다.
        """
        glossary_entries: List[GlossaryEntryDTO] = [] # 변수명 변경
        if not isinstance(raw_item_list, list):
            logger.warning(f"용어집 항목 데이터가 리스트가 아닙니다: {type(raw_item_list)}. 원본: {str(raw_item_list)[:200]}")
            return glossary_entries

        for item_dict in raw_item_list:
            if isinstance(item_dict, dict) and "keyword" in item_dict and "description" in item_dict:
                entry_data = {
                    "keyword": item_dict.get("keyword"),
                    "description_ko": item_dict.get("description"), # AI 응답의 'description'을 'description_ko'에 매핑
                    "aliases": item_dict.get("aliases", []), # 별칭 필드 추가
                    "term_type": item_dict.get("term_type"), # 용어 타입 필드 추가
                    "category": item_dict.get("category"),
                    "importance": item_dict.get("importance"),
                    "isSpoiler": item_dict.get("isSpoiler", False),
                    "sourceSegmentTextPreview": segment_text_preview,
                    "source_language": source_language_code
                }
                if not entry_data["keyword"] or not entry_data["description_ko"]:
                    logger.warning(f"필수 필드(keyword 또는 description) 누락된 용어집 항목 건너뜀: {item_dict}")
                    continue
                glossary_entries.append(GlossaryEntryDTO(**entry_data)) # DTO 변경
            else:
                logger.warning(f"잘못된 용어집 항목 형식 건너뜀: {item_dict}")
        return glossary_entries

    def _get_conflict_resolution_prompt(self, keyword: str, conflicting_entries: List[GlossaryEntryDTO]) -> str: # DTO 변경
        """용어집 충돌 해결을 위한 프롬프트를 생성합니다."""
        base_template = self.config.get(
            "glossary_conflict_resolution_prompt_template", # 설정 키 변경
            "다음은 동일 키워드 '{keyword}'에 대해 여러 출처에서 추출된 로어북 항목들입니다.\n"
            "이 정보들을 종합하여 가장 정확하고 포괄적인 단일 로어북 항목으로 병합해주세요.\n"
            "병합된 설명은 한국어로 작성하고, 카테고리, 중요도, 스포일러 여부도 결정해주세요.\n"
            "JSON 객체 (키: 'keyword', 'description', 'category', 'importance', 'isSpoiler') 형식으로 반환해주세요.\n\n"
            "충돌 항목들:\n{conflicting_items_text}\n\nJSON 형식으로만 응답해주세요."
        )
        items_text_list = []
        for i, entry in enumerate(conflicting_entries):
            items_text_list.append(
                f"  항목 {i+1}:\n"
                f"    - 설명: {entry.description_ko}\n"
                f"    - 별칭: {', '.join(entry.aliases) if entry.aliases else 'N/A'}\n" # 별칭 정보 추가
                f"    - 타입: {entry.term_type or 'N/A'}\n" # 타입 정보 추가
                f"    - 카테고리: {entry.category or 'N/A'}\n"
                f"    - 중요도: {entry.importance or 'N/A'}\n"
                f"    - 스포일러: {entry.isSpoiler}\n"
                f"    - 출처 미리보기: {entry.sourceSegmentTextPreview or 'N/A'}"
            )
        
        prompt = base_template.replace("{keyword}", keyword)
        prompt = prompt.replace("{conflicting_items_text}", "\n".join(items_text_list))
        return prompt

    def _group_similar_keywords_via_api(self, keywords: List[str]) -> Dict[str, str]:
        """
        LLM API를 사용하여 유사하거나 동일한 의미의 키워드들을 그룹핑하고,
        각 원본 키워드에 대한 대표 키워드를 매핑하는 딕셔너리를 반환합니다.
        """
        if not keywords:
            return {}

        unique_keywords = sorted(list(set(keywords)))
        if len(unique_keywords) <= 1: # 키워드가 없거나 하나만 있으면 그룹핑 불필요
            return {kw: kw for kw in unique_keywords}

        # TODO: 프롬프트는 LLM의 응답 형식과 성능에 맞춰 정교하게 조정 필요
        prompt = (
            "다음은 로어북에서 추출된 키워드 목록입니다. 의미상 동일하거나 매우 유사한 대상을 가리키는 키워드들을 그룹화하고, "
            "각 그룹의 가장 대표적인 키워드를 지정해주세요.\n"
            "응답은 JSON 객체 형식으로, 각 원본 키워드를 키로 하고 해당 키워드의 대표 키워드를 값으로 하는 매핑을 제공해주세요.\n"
            "예를 들어, ['주인공', '그녀', '레이카']가 있다면, '주인공'을 대표로 하여 "
            "{'주인공': '주인공', '그녀': '주인공', '레이카': '주인공'}과 같이 응답할 수 있습니다.\n"
            "만약 그룹화할 대상이 없다면 원본 키워드를 그대로 대표 키워드로 사용해주세요.\n\n"
            f"키워드 목록:\n{json.dumps(unique_keywords, ensure_ascii=False)}\n\n"
            "JSON 응답:"
        )
        model_name = self.config.get("model_name", "gemini-2.0-flash")
        generation_config = {
            "temperature": self.config.get("lorebook_semantic_grouping_temperature", 0.1), # 낮은 온도로 일관성 유도
            "response_mime_type": "application/json", # JSON 응답 요청
        }

        try:
            response_data = self.gemini_client.generate_text(
                prompt=prompt,
                model_name=model_name,
                generation_config_dict=generation_config
            )
            if isinstance(response_data, dict):
                # 모든 키워드가 응답에 포함되도록 보장 (LLM이 누락할 경우 대비)
                keyword_map = {kw: response_data.get(kw, kw) for kw in unique_keywords}
                logger.info(f"유사 키워드 그룹핑 API 호출 성공. {len(keyword_map)}개 매핑 생성.")
                return keyword_map
            logger.warning(f"유사 키워드 그룹핑 API로부터 예상치 못한 응답 형식: {type(response_data)}. 그룹핑 없이 진행.")
            return {kw: kw for kw in unique_keywords}
        except Exception as e:
            logger.error(f"유사 키워드 그룹핑 API 호출 중 오류: {e}. 그룹핑 없이 진행.")
            return {kw: kw for kw in unique_keywords} # 오류 시, 각 키워드는 스스로를 대표

    def _extract_glossary_entries_from_segment_via_api( # 함수명 변경
        self,
        segment_text: str,
        source_language_of_segment: Optional[str] = None, # 세그먼트의 언어 코드
        retry_count: int = 0, max_retries: int = 2 # 재시도 횟수 줄임
    ) -> List[GlossaryEntryDTO]: # 반환 타입 변경
        """
        단일 텍스트 세그먼트에서 Gemini API를 사용하여 로어북 항목들을 추출합니다.

        Args:
            segment_text (str): 분석할 텍스트 세그먼트.
            source_language_of_segment (Optional[str]): 이 세그먼트의 언어 코드.
            retry_count (int): 현재 재시도 횟수.
            max_retries (int): 최대 재시도 횟수.
        """
        prompt = self._get_glossary_extraction_prompt(segment_text) # 변경된 함수 호출
        model_name = self.config.get("model_name", "gemini-2.0-flash")
        
        generation_config = {
            "temperature": self.config.get("glossary_extraction_temperature", self.config.get("temperature", 0.2)), # 설정 키 변경
            "top_p": self.config.get("top_p", 0.9),
            "response_mime_type": "application/json", # Gemini API가 JSON 출력 직접 지원
        }

        try:
            # GeminiClient가 response_mime_type="application/json" 설정 시
            # 이미 파싱된 Python 객체(이 경우 List[Dict])를 반환한다고 가정합니다.
            response_data = self.gemini_client.generate_text(
                prompt=prompt,
                model_name=model_name,
                generation_config_dict=generation_config
            )

            if response_data is None: # None 또는 빈 응답 처리
                logger.warning(f"용어집 추출 API로부터 응답을 받지 못했습니다. 세그먼트: {segment_text[:50]}...")
                return []

            if isinstance(response_data, list):
                # 프롬프트는 JSON 객체를 요청했으므로, 리스트는 예상치 못한 응답
                logger.warning(f"용어집 추출 API로부터 예상치 못한 리스트 응답을 받았습니다: {response_data}")
            elif isinstance(response_data, dict): # GeminiClient가 JSON 객체를 파싱하여 반환한 경우
                logger.debug("GeminiClient가 파싱된 딕셔너리(JSON 객체)를 반환했습니다.")
                detected_lang = response_data.get("detected_language_code")
                raw_entities = response_data.get("entities")

                if detected_lang and isinstance(raw_entities, list):
                    logger.info(f"LLM이 감지한 언어: {detected_lang}. 추출된 항목 수: {len(raw_entities)}")
                    return self._parse_raw_glossary_items_to_dto(raw_entities, segment_text[:100], detected_lang) # 변경된 함수 호출
                else:
                    logger.warning(f"API 응답 JSON 객체에 'detected_language_code' 또는 'entities' 필드가 누락/잘못되었습니다: {response_data}")
            elif isinstance(response_data, str):
                logger.warning("GeminiClient가 JSON 문자열을 반환했습니다 (파싱 실패 또는 JSON 응답 아님). LorebookService에서 파싱 시도.")
                json_str = response_data.strip()
                json_str = re.sub(r'^```json\s*', '', json_str, flags=re.IGNORECASE) 
                json_str = re.sub(r'\s*```$', '', json_str, flags=re.IGNORECASE)
                json_str = json_str.strip()
                try:
                    parsed_dict = json.loads(json_str)
                    if isinstance(parsed_dict, dict):
                        detected_lang = parsed_dict.get("detected_language_code")
                        raw_entities = parsed_dict.get("entities")
                        if detected_lang and isinstance(raw_entities, list):
                            logger.info(f"LLM이 감지한 언어 (문자열 파싱): {detected_lang}. 추출된 항목 수: {len(raw_entities)}")
                            return self._parse_raw_glossary_items_to_dto(raw_entities, segment_text[:100], detected_lang) # 변경된 함수 호출
                        else:
                            logger.warning(f"파싱된 JSON 객체에 'detected_language_code' 또는 'entities' 필드가 누락/잘못되었습니다: {parsed_dict}")
                    else:
                        logger.warning(f"파싱된 JSON 데이터가 객체가 아닙니다: {type(parsed_dict)}. 응답: {json_str[:100]}")
                except json.JSONDecodeError as je:
                    logger.warning(f"JSON 파싱 오류 (문자열 응답): {je}. 응답: {json_str[:200]}")
            else: # None 또는 다른 예기치 않은 타입
                logger.warning(f"GeminiClient로부터 예상치 않은 타입의 응답을 받았습니다: {type(response_data)}. 세그먼트: {segment_text[:50]}...")
                return []

            # 여기까지 왔다면, response_data가 list나 str이 아니었거나, str 파싱에 실패한 경우.
            # 재시도 로직으로 넘어갑니다.
            if retry_count < max_retries: # 재시도 로직 강화
                logger.info(f"용어집 추출 응답 형식 오류 또는 파싱 실패, 재시도 중 ({retry_count + 1}/{max_retries})...")
                time.sleep(1 + retry_count)
                return self._extract_glossary_entries_from_segment_via_api(segment_text, source_language_of_segment, retry_count + 1, max_retries)
            else:
                logger.error("최대 재시도 횟수 초과 (응답 형식 오류 또는 파싱 실패). 빈 결과 반환.")
                return []

        except GeminiContentSafetyException as e_safety:
            logger.warning(f"용어집 추출 중 콘텐츠 안전 문제: {e_safety}. 세그먼트: {segment_text[:50]}...")
            return [] # 콘텐츠 안전 문제는 재시도하지 않고 빈 리스트 반환
        except (GeminiRateLimitException, GeminiApiException) as e_api:
            logger.warning(f"용어집 추출 API 호출 실패: {e_api}. 세그먼트: {segment_text[:50]}...")
            if retry_count < max_retries:
                logger.info(f"API 오류, 재시도 중 ({retry_count + 1}/{max_retries})...")
                time.sleep( (1 + retry_count) * 2 ) 
                return self._extract_glossary_entries_from_segment_via_api(segment_text, source_language_of_segment, retry_count + 1, max_retries)
            else:
                logger.error(f"최대 재시도 횟수 초과 (API 오류).")
                raise BtgApiClientException(f"용어집 추출 API 호출 최대 재시도 실패: {e_api}", original_exception=e_api) from e_api
        except Exception as e:
            logger.error(f"용어집 추출 중 예상치 못한 오류: {e}. 세그먼트: {segment_text[:50]}...")
            raise BtgBusinessLogicException(f"용어집 추출 중 오류: {e}", original_exception=e) from e

    def _select_sample_segments(self, all_segments: List[str]) -> List[str]:
        """전체 세그먼트 리스트에서 표본 세그먼트를 선택합니다."""
        # 샘플링 방식 설정 (uniform, random, importance-based 등)
        sampling_method = self.config.get("lorebook_sampling_method", "uniform")
        sample_ratio = self.config.get("lorebook_sampling_ratio", 25.0) / 100.0
        
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
        """추출된 용어집 항목들의 충돌을 해결합니다."""
        if not all_extracted_entries:
            return []

        logger.info(f"용어집 충돌 해결 시작. 총 {len(all_extracted_entries)}개 항목 검토 중...")
        
        all_keywords = [entry.keyword for entry in all_extracted_entries if entry.keyword] # 키워드가 있는 항목만 추출
        keyword_to_representative_map: Dict[str, str] = {} # 기본값: 빈 맵
        if all_keywords: # 추출된 키워드가 있을 경우에만 API 호출
            logger.info("의미 기반 유사 키워드 그룹핑 시도...")
            keyword_to_representative_map = self._group_similar_keywords_via_api(all_keywords)
            logger.debug(f"유사 키워드 매핑 결과: {keyword_to_representative_map}")

        grouped_by_keyword: Dict[str, List[GlossaryEntryDTO]] = {} # DTO 변경
        for entry in all_extracted_entries:
            # 의미 기반 그룹핑이 활성화되었고 매핑이 있다면 대표 키워드를 사용, 아니면 원본 키워드 사용
            representative_keyword = keyword_to_representative_map.get(entry.keyword, entry.keyword)
            # 그룹핑을 위한 키는 소문자로 통일
            grouping_key_lower = representative_keyword.lower()
            
            if grouping_key_lower not in grouped_by_keyword:
                grouped_by_keyword[grouping_key_lower] = []
            grouped_by_keyword[grouping_key_lower].append(entry)

        final_glossary: List[GlossaryEntryDTO] = [] # 변수명 및 DTO 변경
        # conflict_resolution_batch_size = self.config.get("lorebook_conflict_resolution_batch_size", 5) # 현재는 키워드별로 처리

        for keyword_lower, entries_for_keyword in grouped_by_keyword.items():
            if len(entries_for_keyword) == 1:
                final_lorebook.append(entries_for_keyword[0]) # 충돌 없음
            else:
                # 그룹의 대표 키워드 (또는 그룹 내 첫 번째 항목의 키워드)를 충돌 해결 프롬프트에 사용
                # keyword_lower는 정규화된 대표 키워드의 소문자 버전임.
                # 실제 프롬프트에는 그룹 내 항목 중 하나의 원본 키워드(대소문자 유지)를 사용하는 것이 좋을 수 있음.
                representative_keyword_for_prompt = entries_for_keyword[0].keyword 
                logger.debug(f"대표 키워드 '{representative_keyword_for_prompt}' (그룹 키: '{keyword_lower}')에 대해 {len(entries_for_keyword)}개의 잠재적 충돌 항목 발견.")
                conflict_prompt = self._get_conflict_resolution_prompt(representative_keyword_for_prompt, entries_for_keyword)
                model_name = self.config.get("model_name", "gemini-2.0-flash")
                generation_config = {
                    "temperature": self.config.get("glossary_conflict_resolution_temperature", self.config.get("temperature", 0.3)), # 설정 키 변경
                    "top_p": self.config.get("top_p", 0.9),
                    "response_mime_type": "application/json",
                }

                try:
                    merged_response_text = self.gemini_client.generate_text(
                        prompt=conflict_prompt,
                        model_name=model_name,
                        generation_config_dict=generation_config
                    )

                    if merged_response_text:
                        if isinstance(merged_response_text, (dict, list)): # 이미 파싱된 경우
                            merged_json_str = json.dumps(merged_response_text)
                        elif isinstance(merged_response_text, str):
                            merged_json_str = merged_response_text.strip()
                            merged_json_str = re.sub(r'^```json\s*', '', merged_json_str, flags=re.IGNORECASE)
                            merged_json_str = re.sub(r'\s*```$', '', merged_json_str, flags=re.IGNORECASE)
                            merged_json_str = merged_json_str.strip()
                        else:
                            raise ValueError(f"API 응답이 예상치 않은 타입: {type(merged_response_text)}")

                        merged_entry_dict = json.loads(merged_json_str)
                        if isinstance(merged_entry_dict, dict) and "keyword" in merged_entry_dict and "description" in merged_entry_dict:
                            # API 응답에는 sourceSegmentTextPreview가 없을 수 있으므로, 원본 항목들 중 하나의 것을 사용하거나 None
                            source_preview = entries_for_keyword[0].sourceSegmentTextPreview
                            merged_entry_data = {
                                "keyword": merged_entry_dict.get("keyword", representative_keyword_for_prompt), # API 결과 또는 그룹 대표 키워드 사용
                                "description_ko": merged_entry_dict.get("description"),
                                "aliases": merged_entry_dict.get("aliases", entries_for_keyword[0].aliases), # 별칭 병합
                                "term_type": merged_entry_dict.get("term_type", entries_for_keyword[0].term_type), # 타입 병합
                                "category": merged_entry_dict.get("category"),
                                "importance": merged_entry_dict.get("importance"),
                                "isSpoiler": merged_entry_dict.get("isSpoiler", False),
                                "sourceSegmentTextPreview": source_preview,
                                "source_language": entries_for_keyword[0].source_language # 원본 항목의 언어 코드 사용
                            }
                            if not merged_entry_data["keyword"] or not merged_entry_data["description_ko"]:
                                logger.warning(f"병합된 용어집 항목에 필수 필드 누락: {merged_entry_dict}. 원본 중 첫 번째 항목 사용.")
                                final_glossary.append(self._select_best_entry_from_group(entries_for_keyword))
                            else:
                                final_glossary.append(GlossaryEntryDTO(**merged_entry_data)) # DTO 변경
                                logger.info(f"키워드 그룹 '{representative_keyword_for_prompt}' 충돌 해결 및 병합 성공.")
                        else:
                            logger.warning(f"병합된 용어집 항목 형식이 잘못됨: {merged_entry_dict}. 원본 중 첫 번째 항목 사용.")
                            final_glossary.append(self._select_best_entry_from_group(entries_for_keyword))
                    else:
                        logger.warning(f"키워드 그룹 '{representative_keyword_for_prompt}' 충돌 해결 API 응답 없음. 원본 중 가장 좋은 항목 선택.")
                        final_glossary.append(self._select_best_entry_from_group(entries_for_keyword))
                except Exception as e_conflict:
                    logger.error(f"키워드 그룹 '{representative_keyword_for_prompt}' 충돌 해결 중 오류: {e_conflict}. 원본 중 가장 좋은 항목 선택.")
                    final_glossary.append(self._select_best_entry_from_group(entries_for_keyword)) # 오류 발생 시 그룹 내 최선 항목 선택
        
        # 최종 로어북 정렬 (예: 중요도, 키워드 순)
        final_glossary.sort(key=lambda x: (-(x.importance or 0), x.keyword.lower()))
        
        logger.info(f"용어집 충돌 해결 완료. 최종 {len(final_glossary)}개 항목.")
        return final_glossary

    def _select_best_entry_from_group(self, entry_group: List[GlossaryEntryDTO]) -> GlossaryEntryDTO: # DTO 변경
        """주어진 용어집 항목 그룹에서 가장 좋은 항목을 선택합니다 (예: 가장 긴 설명, 가장 높은 중요도)."""
        if not entry_group:
            # 이 경우는 발생하지 않아야 하지만, 방어적으로 처리
            raise ValueError("빈 용어집 항목 그룹에서 최선 항목을 선택할 수 없습니다.")
        # 중요도 높은 순, 설명 긴 순, 스포일러 아닌 것 우선 등으로 정렬하여 첫 번째 항목 반환
        entry_group.sort(key=lambda e: (-(e.importance or 0), -len(e.description_ko or ""), e.isSpoiler is True))
        return entry_group[0]


    def extract_and_save_glossary(self, # 함수명 변경
                                  # all_text_segments: List[str], # 직접 세그먼트 리스트를 받는 대신 원본 텍스트를 받도록 변경
                                  novel_text_content: str, # 원본 텍스트 내용
                                  input_file_path_for_naming: Union[str, Path],
                                  novel_language_code: Optional[str] = None, # 소설의 언어 코드
                                  progress_callback: Optional[Callable[[LorebookExtractionProgressDTO], None]] = None,
                                  seed_lorebook_path: Optional[Union[str, Path]] = None # 시드 로어북 경로 추가
                                 ) -> Path:
        """
        주어진 텍스트 내용에서 로어북을 추출하고 JSON 파일에 저장합니다.

        Args:
            novel_text_content (str): 분석할 전체 텍스트 내용.
            input_file_path_for_naming (Union[str, Path]):
                출력 JSON 파일 이름 생성에 사용될 원본 입력 파일 경로. (파일명에 _glossary.json 등으로 사용)
            novel_language_code (Optional[str]): 로어북 항목에 설정할 소스 언어 코드.
            progress_callback (Optional[Callable[[GlossaryExtractionProgressDTO], None]], optional): # DTO 변경
                진행 상황을 알리기 위한 콜백 함수.
            seed_lorebook_path (Optional[Union[str, Path]], optional):
                참고할 기존 로어북 JSON 파일 경로.

        Returns:
            Path: 생성된 로어북 JSON 파일의 경로.

        Raises:
            BtgBusinessLogicException: 용어집 추출 또는 저장 과정에서 심각한 오류 발생 시.
        """
        all_extracted_entries_from_segments: List[GlossaryEntryDTO] = [] # DTO 변경
        seed_entries: List[GlossaryEntryDTO] = [] # DTO 변경
        
        # novel_language_code는 이제 LLM이 감지한 언어를 사용하기 위한 기본값/힌트로 사용될 수 있지만,
        # LLM의 응답에 있는 detected_language_code가 우선됩니다.
        # source_language_of_segment로 전달될 값은 novel_language_code가 "auto"가 아닐 경우 그 값을 사용하고,
        # "auto"일 경우 None으로 전달하여 LLM이 감지하도록 유도합니다.
        lang_for_segment_extraction_hint = novel_language_code if novel_language_code != "auto" else None

        if seed_lorebook_path:
            seed_path_obj = Path(seed_lorebook_path)
            if seed_path_obj.exists() and seed_path_obj.is_file():
                try:
                    logger.info(f"시드 용어집 파일 로드 중: {seed_path_obj}")
                    raw_seed_data = read_json_file(seed_path_obj)
                    if isinstance(raw_seed_data, list):
                        for item_dict in raw_seed_data:
                            if isinstance(item_dict, dict) and "keyword" in item_dict and "description" in item_dict:
                                try:
                                    entry = GlossaryEntryDTO( # DTO 변경
                                        keyword=item_dict.get("keyword", ""), # 시드 파일의 'description'을
                                        description_ko=item_dict.get("description", ""), # 'description_ko'에 매핑
                                        aliases=item_dict.get("aliases", []), # 별칭 로드
                                        term_type=item_dict.get("term_type"), # 타입 로드
                                        category=item_dict.get("category"),
                                        importance=int(item_dict.get("importance", 0)) if item_dict.get("importance") is not None else None,
                                        sourceSegmentTextPreview=item_dict.get("sourceSegmentTextPreview"),
                                        isSpoiler=bool(item_dict.get("isSpoiler", False)),
                                        source_language=item_dict.get("source_language", lang_for_segment_extraction_hint) # 시드 파일 내 언어 우선
                                    )
                                    if entry.keyword and entry.description_ko:
                                        seed_entries.append(entry)
                                except (TypeError, ValueError) as e_dto:
                                    logger.warning(f"시드 용어집 항목 DTO 변환 중 오류: {item_dict}, 오류: {e_dto}")
                        logger.info(f"{len(seed_entries)}개의 시드 용어집 항목 로드 완료.")
                except Exception as e_seed:
                    logger.error(f"시드 용어집 파일 로드 중 오류 ({seed_path_obj}): {e_seed}", exc_info=True)
            else:
                logger.warning(f"제공된 시드 로어북 경로를 찾을 수 없거나 파일이 아닙니다: {seed_lorebook_path}")
        
        # ChunkService를 사용하여 텍스트를 세그먼트로 분할
        lorebook_segment_size = self.config.get("lorebook_chunk_size", self.config.get("chunk_size", 8000))
        all_text_segments = self.chunk_service.create_chunks_from_file_content(
            novel_text_content, lorebook_segment_size
        )

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
                future_to_segment = {executor.submit(self._extract_glossary_entries_from_segment_via_api, segment, lang_for_segment_extraction_hint): segment # 변경된 함수 호출
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
        
        # 로어북 최대 항목 수 제한 (설정값 사용)
        max_total_glossary_entries = self.config.get("glossary_max_total_entries", 1000) # 설정 키 변경, 예시 기본값
        if len(final_glossary) > max_total_glossary_entries:
            logger.info(f"추출된 용어집 항목({len(final_glossary)}개)이 최대 제한({max_total_glossary_entries}개)을 초과하여 상위 항목만 저장합니다.")
            # 중요도 등으로 정렬되어 있으므로 상위 항목 선택
            final_glossary = final_glossary[:max_total_glossary_entries]

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
