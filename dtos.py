# dtos.py
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union
from pathlib import Path

# --- 모델 정보 DTO ---
@dataclass
class ModelInfoDTO:
    """
    사용 가능한 API 모델 정보를 전달하기 위한 DTO입니다.
    """
    name: str # 예: "models/gemini-2.0-flash"
    display_name: str # 예: "gemini-2.0-flash"
    description: Optional[str] = None
    version: Optional[str] = None
    input_token_limit: Optional[int] = None
    output_token_limit: Optional[int] = None

# --- 번역 작업 상태 DTO ---
@dataclass
class TranslationChunkStatusDTO:
    """
    개별 청크의 번역 상태를 나타내는 DTO입니다.
    """
    chunk_index: int
    status: str # 예: "PENDING", "PROCESSING", "COMPLETED", "FAILED"
    error_message: Optional[str] = None
    translated_content_preview: Optional[str] = None

@dataclass
class TranslationJobProgressDTO:
    """
    전체 번역 작업의 진행 상황을 나타내는 DTO입니다.
    """
    total_chunks: int
    processed_chunks: int
    successful_chunks: int
    failed_chunks: int
    current_status_message: str
    current_chunk_processing: Optional[int] = None # 수정: 필드 추가
    last_error_message: Optional[str] = None 


# --- 고유명사 추출 작업 상태 DTO ---
# LorebookExtractionProgressDTO로 대체됨
# @dataclass
# class PronounExtractionProgressDTO:
#     """
#     고유명사 추출 작업의 진행 상황을 나타내는 DTO입니다.
#     """
#     total_sample_chunks: int
#     processed_sample_chunks: int
#     current_status_message: str

@dataclass
class LorebookEntryDTO:
    """
    로어북의 각 항목을 나타냅니다.
    """
    keyword: str
    description_ko: str # 한국어 설명임을 명시
    category: Optional[str] = None # 예: "인물", "장소", "아이템", "설정"
    importance: Optional[int] = None # 1-10
    sourceSegmentTextPreview: Optional[str] = None # 추출된 원본 세그먼트 미리보기
    isSpoiler: Optional[bool] = False
    source_language: Optional[str] = None # 로어북 키워드의 원본 언어 (예: "en", "ja", "ko")
    # 추가 필드 가능

@dataclass
class LorebookExtractionProgressDTO: # 기존 PronounExtractionProgressDTO 와 유사하게
    """
    로어북 추출 작업의 진행 상황을 나타내는 DTO입니다.
    """
    total_segments: int
    processed_segments: int
    current_status_message: str
    extracted_entries_count: int = 0

# --- 설정 관련 DTO (필요시) ---
@dataclass
class AppConfigDisplayDTO:
    """
    UI에 표시하거나 전달하기 위한 간소화된 애플리케이션 설정 DTO입니다.
    (API 키와 같이 민감한 정보는 제외)
    """
    model_name: str
    temperature: float
    top_p: float
    chunk_size: int
    pronouns_csv_path: Optional[str] = None

# --- 사용자 입력 DTO (프레젠테이션 -> 애플리케이션) ---
@dataclass
class TranslationRequestDTO:
    """
    번역 시작 요청을 위한 DTO입니다.
    """
    input_file_path: Union[str, Path]
    output_file_path: Union[str, Path]


if __name__ == '__main__':
    # DTO 사용 예시
    print("--- DTO 사용 예시 ---")

    model1 = ModelInfoDTO(name="models/gemini-2.0-flash", display_name="gemini-2.0-flash", input_token_limit=1048576)
    print(f"모델 정보: {model1}")

    progress1 = TranslationJobProgressDTO(
        total_chunks=100,
        processed_chunks=25,
        successful_chunks=20,
        failed_chunks=5,
        current_status_message="청크 26/100 번역 중...",
        current_chunk_processing=25 # 추가된 필드 사용 예시
    )
    print(f"번역 진행: {progress1}")

    lorebook_entry_example = LorebookEntryDTO(
        keyword="아르카나 스톤",
        description_ko="고대 유물, 소유자에게 막대한 힘을 부여함",
        category="아이템",
        importance=9,
        isSpoiler=True,
        source_language="ko"
    )
    print(f"로어북 항목 예시: {lorebook_entry_example}")

    lorebook_progress = LorebookExtractionProgressDTO(
        total_segments=100,
        processed_segments=30,
        current_status_message="세그먼트 31/100 분석 중...",
        extracted_entries_count=15
    )
    print(f"로어북 추출 진행: {lorebook_progress}")

    config_display = AppConfigDisplayDTO(
        model_name="gemini-2.0-flash", 
        temperature=0.8,
        top_p=0.9,
        chunk_size=5000,
        pronouns_csv_path="data/my_pronouns.csv"
    )
    print(f"애플리케이션 설정 (표시용): {config_display}")

    trans_request = TranslationRequestDTO(
        input_file_path="input/source_text.txt",
        output_file_path="output/translated_text.txt"
    )
    print(f"번역 요청: {trans_request}")
