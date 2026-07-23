from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pathlib import Path

class BaseReviewProvider(ABC):
    """
    Review 탭에서 텍스트, 무결성, EPUB 파이프라인의 결과물을 
    다형성으로 처리하기 위한 추상 베이스 클래스입니다.
    """
    def __init__(self, app_service):
        self.app_service = app_service
        self.translation_service = getattr(app_service, 'translation_service', None)
        
        from utils.chunk_service import ChunkService
        chunk_svc = getattr(self.translation_service, 'chunk_service', None) if self.translation_service else None
        if isinstance(chunk_svc, ChunkService):
            self.chunk_service = chunk_svc
        else:
            self.chunk_service = ChunkService()
        self.quality_service = app_service.review_tab.quality_service if hasattr(app_service, 'review_tab') else None
        
        # fallback to direct import if needed
        if not self.quality_service:
            from utils.quality_check_service import QualityCheckService
            self.quality_service = QualityCheckService()

    @abstractmethod
    def load_metadata(self, file_path: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def load_source_chunks(self, file_path: str) -> Dict[int, str]:
        pass

    @abstractmethod
    def load_translated_chunks(self, file_path: str) -> Dict[int, str]:
        pass

    @abstractmethod
    async def retranslate_chunk(self, chunk_id: str, new_prompt: str, split_level: int = 1) -> str:
        """
        주어진 청크를 새로운 프롬프트(원문)로 재번역합니다.
        각 파이프라인(Standard/Integrity/Epub)에 맞는 API를 호출해야 합니다.
        """
        pass

    @abstractmethod
    def save_translated_chunk(self, file_path: str, chunk_id: int, new_text: str, current_all_chunks: Dict[int, str]) -> None:
        """
        단일 청크의 수정된 번역본을 저장하거나 버퍼에 캐싱합니다.
        """
        pass

    @abstractmethod
    def generate_final_file(self, file_path: str, current_all_chunks: Dict[int, str]) -> str:
        """
        수정사항이 모두 반영된 최종 출력 파일을 생성하고 경로를 반환합니다.
        """
        pass
