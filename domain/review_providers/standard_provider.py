from pathlib import Path
from typing import Dict, Any

from infrastructure.file_handler import load_metadata, load_chunks_from_file, read_text_file, save_merged_chunks_to_file, write_text_file
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

    async def retranslate_chunk(self, chunk_id: str, new_prompt: str, split_level: int = 1) -> str:
        # Standard retranslation uses force split async if split_level is provided
        max_split = self.app_service.config.get("max_content_safety_split_attempts", 3)
        min_size = self.app_service.config.get("min_content_safety_chunk_size", 100)
        return await self.translation_service.translate_text_force_split_async(
            new_prompt, max_split, min_size, split_level=split_level
        )

    def save_translated_chunk(self, file_path: str, chunk_id: int, new_text: str, current_all_chunks: Dict[int, str]) -> None:
        p = Path(file_path)
        translated_path = p.parent / f"{p.stem}_translated_chunked.txt"
        # 키를 str로 변환하면 사전순(1, 10, 2...) 정렬되므로 원본 int 키 유지
        save_merged_chunks_to_file(translated_path, current_all_chunks)

    def generate_final_file(self, file_path: str, current_all_chunks: Dict[int, str]) -> str:
        p = Path(file_path)
        final_output_path = p.parent / f"{p.stem}_translated{p.suffix}"
        chunked_path = p.parent / f"{p.stem}_translated_chunked.txt"
        
        # 1. Update the chunked file just in case
        save_merged_chunks_to_file(chunked_path, current_all_chunks)
        
        # 2. Generate final text
        enable_post_processing = self.app_service.config.get("enable_post_processing", True)
        if enable_post_processing:
            final_content = self.app_service.post_processing_service.post_process_and_clean_chunks(
                current_all_chunks, self.app_service.config
            )
        else:
            sorted_indices = sorted(current_all_chunks.keys())
            final_content = "\n\n".join([current_all_chunks[i] for i in sorted_indices])
            
        # 3. Write to final file
        write_text_file(final_output_path, final_content)
        
        return str(final_output_path)
