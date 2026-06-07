# dtos.py
# Path: neo_batch_translator/core/dtos.py
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
class GlossaryEntryDTO: # 클래스명 변경 LorebookEntryDTO -> GlossaryEntryDTO
    """
    용어집의 각 항목을 나타냅니다.
    """
    keyword: str
    translated_keyword: str # 번역된 용어
    # source_language: str    # 번역 출발 언어 (예: "en", "ja", "ko") # 제거됨
    target_language: str    # 번역 도착 언어 (예: "ko", "en")
    occurrence_count: int = field(default=0) # 등장 횟수

@dataclass
class GlossaryExtractionProgressDTO: # 클래스명 변경 LorebookExtractionProgressDTO -> GlossaryExtractionProgressDTO
    """
    용어집 추출 작업의 진행 상황을 나타내는 DTO입니다.
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

    glossary_entry_example = GlossaryEntryDTO( # 변수명 및 클래스명 변경
        keyword="아르카나 스톤",
        translated_keyword="Arcana Stone",
        source_language="ko",
        target_language="en",
        occurrence_count=15
    )
    print(f"용어집 항목 예시: {glossary_entry_example}") # 출력 메시지 변경

    glossary_progress = GlossaryExtractionProgressDTO( # 변수명 및 클래스명 변경
        total_segments=100,
        processed_segments=30,
        current_status_message="세그먼트 31/100 분석 중...",
        extracted_entries_count=15
    )
    print(f"용어집 추출 진행: {glossary_progress}") # 출력 메시지 변경

    config_display = AppConfigDisplayDTO(
        model_name="gemini-2.0-flash", 
        temperature=0.8,
        top_p=0.9,
        chunk_size=5000,
        pronouns_csv_path="data/my_glossary.json" # 경로 예시 변경
    )
    print(f"애플리케이션 설정 (표시용): {config_display}")

    trans_request = TranslationRequestDTO(
        input_file_path="input/source_text.txt",
        output_file_path="output/translated_text.txt"
    )
    print(f"번역 요청: {trans_request}")


# --- 3-Way 파이프라인 DTO (Pydantic) ---
from pydantic import BaseModel, Field
from enum import Enum

class TranslationUnit(BaseModel):
    """무결성/EPUB 번역의 최소 단위"""
    id: str = Field(description="고유 식별자 (Line Index 또는 UUID)")
    text: str = Field(description="번역할 원문 텍스트")
    context: Optional[str] = Field(None, description="주변 문맥 (이전/다음 문장)")

class TranslatedUnit(BaseModel):
    """LLM 응답 스키마"""
    id: str
    translated_text: str

class NodeType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    IGNORED = "ignored"  # 태그 구조 유지만 하는 노드 (<div...>, </div>, <br/> 등)

class EpubNode(BaseModel):
    """EPUB 내부 노드 구조"""
    id: str             # 결정론적 ID (파일명_인덱스)
    type: NodeType
    tag: str            # p, div, span, img 등
    html: Optional[str] = None  # IGNORED/IMAGE 타입일 때 원본 HTML (<div class="a">)
    content: Optional[str] = None # TEXT 타입일 때 번역 대상 텍스트
    attributes: Dict[str, str] = {} # 태그 속성 (class, style, href 등)
    image_path: Optional[str] = None # 이미지 경로 (상대 경로 리졸브 후)

class EpubChapter(BaseModel):
    """EPUB 챕터 정보"""
    file_name: str      # ZIP 내부 전체 경로 (예: OEBPS/Text/ch1.xhtml)
    nodes: List[EpubNode]
    head_html: str      # <head> 내부 콘텐츠 보존용
