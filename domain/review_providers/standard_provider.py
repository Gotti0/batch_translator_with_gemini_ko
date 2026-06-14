from pathlib import Path
from typing import Dict, Any

from infrastructure.file_handler import load_metadata, load_chunks_from_file, read_text_file, save_merged_chunks_to_file
from domain.review_providers.base_provider import BaseReviewProvider

class StandardReviewProvider(BaseReviewProvider):
    def load_metadata(self, file_path: str) -> Dict[str, Any]:
        return load_metadata(file_path)

    def load_source_chunks(self, file_path: str) -> Dict[int, str]:
        content = read_text_file(file_path)
        if not content:
            return {}
        chunk_size = self.app_service.config.get("chunk_size", 6000)
        chunks_list = self.chunk_service.create_chunks_from_file_content(content, chunk_size)
        return {i: chunk for i, chunk in enumerate(chunks_list)}

    def load_translated_chunks(self, file_path: str) -> Dict[int, str]:
        p = Path(file_path)
        translated_path = p.parent / f"{p.stem}_translated_chunked.txt"
        if translated_path.exists():
            return load_chunks_from_file(translated_path)
        return {}

    async def retranslate_chunk(self, chunk_id: str, new_prompt: str) -> str:
        # Standard retranslation uses translation_service.translate_text_async for a single chunk
        # Since it's a raw string, we can just call it
        return await self.translation_service.translate_text_async(new_prompt)

    def save_translated_chunk(self, file_path: str, chunk_id: int, new_text: str, current_all_chunks: Dict[int, str]) -> None:
        p = Path(file_path)
        translated_path = p.parent / f"{p.stem}_translated_chunked.txt"
        str_chunks = {str(k): v for k, v in current_all_chunks.items()}
        save_merged_chunks_to_file(translated_path, str_chunks)

    def generate_final_file(self, file_path: str, current_all_chunks: Dict[int, str]) -> str:
        # Using post_processing_service to generate final
        self.app_service.post_processing_service.process_final_file(file_path)
        p = Path(file_path)
        return str(p.parent / f"{p.stem}_translated{p.suffix}")
