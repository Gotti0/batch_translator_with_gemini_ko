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

    def reset_chunks(self, file_path: str, metadata: Dict[str, Any], chunk_indices: List[int]) -> None:
        """
        선택 청크의 번역 기록을 지워 다음 번역 실행에서 다시 번역되게 합니다.

        기본 구현은 메타데이터만 지웁니다. 표준 파이프라인은 메타데이터의 translated_chunks로
        이어하기를 판단하므로 이것으로 충분합니다. 다른 곳에서 진행 상태를 복원하는
        파이프라인은 그 저장소도 함께 지워야 합니다.
        """
        from infrastructure.file_handler import save_metadata

        translated = metadata.get("translated_chunks") or {}
        failed = metadata.get("failed_chunks") or {}
        for idx in chunk_indices:
            translated.pop(str(idx), None)
            failed.pop(str(idx), None)
        metadata["translated_chunks"] = translated
        metadata["failed_chunks"] = failed
        metadata["status"] = "in_progress"
        save_metadata(file_path, metadata)
