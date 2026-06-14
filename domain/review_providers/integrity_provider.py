from pathlib import Path
from typing import Dict, Any, List

from core.dtos import TranslationUnit
from infrastructure.file_handler import load_metadata, read_text_file
from domain.review_providers.base_provider import BaseReviewProvider

class IntegrityReviewProvider(BaseReviewProvider):
    def load_metadata(self, file_path: str) -> Dict[str, Any]:
        return load_metadata(file_path)

    def _get_chunked_units(self, file_path: str) -> List[List[TranslationUnit]]:
        content = read_text_file(file_path)
        if not content:
            return []
        lines = content.splitlines()
        units = [TranslationUnit(id=str(i), text=line) for i, line in enumerate(lines)]
        
        max_chunk_size = self.app_service.config.get("chunk_size", 6000)
        max_items = self.app_service.config.get("integrity_max_items", 200)
        return self.chunk_service.split_nodes_into_chunks(units, max_chunk_size, max_items)

    def load_source_chunks(self, file_path: str) -> Dict[int, str]:
        chunks = self._get_chunked_units(file_path)
        return {i: "\n".join(u.text for u in chunk) for i, chunk in enumerate(chunks)}

    def load_translated_chunks(self, file_path: str) -> Dict[int, str]:
        p = Path(file_path)
        # 무결성 모드는 최종 결과물이 file_path의 translated 파일로 나옵니다.
        # 원본과 1:1 라인 매핑이 되므로, 다시 청크 길이만큼 잘라서 반환합니다.
        translated_path = p.parent / f"{p.stem}_translated{p.suffix}"
        
        if not translated_path.exists():
            return {}
            
        translated_content = read_text_file(translated_path)
        if not translated_content:
            return {}
            
        translated_lines = translated_content.splitlines()
        chunks = self._get_chunked_units(file_path)
        
        translated_chunks_map = {}
        line_idx = 0
        for i, chunk in enumerate(chunks):
            chunk_length = len(chunk)
            chunk_trans_lines = translated_lines[line_idx : line_idx + chunk_length]
            translated_chunks_map[i] = "\n".join(chunk_trans_lines)
            line_idx += chunk_length
            
        return translated_chunks_map

    async def retranslate_chunk(self, chunk_id: str, new_prompt: str) -> str:
        # new_prompt는 재번역할 원문 텍스트입니다.
        lines = new_prompt.splitlines()
        units = [TranslationUnit(id=str(i), text=line) for i, line in enumerate(lines)]
        
        # 무결성 재번역 호출
        result_map = await self.translation_service._translate_integrity_chunk_with_retry(units)
        
        # 결과를 라인 순서대로 합침
        result_lines = []
        for i in range(len(units)):
            result_lines.append(result_map.get(str(i), lines[i]))
            
        return "\n".join(result_lines)

    def save_translated_chunk(self, file_path: str, chunk_id: int, new_text: str, current_all_chunks: Dict[int, str]) -> None:
        p = Path(file_path)
        translated_path = p.parent / f"{p.stem}_translated{p.suffix}"
        
        # 무결성은 모든 청크를 순서대로 합치면 최종 파일이 됩니다.
        final_lines = []
        for i in range(len(current_all_chunks)):
            if i in current_all_chunks:
                final_lines.append(current_all_chunks[i])
        
        with open(translated_path, "w", encoding="utf-8") as f:
            f.write("\n".join(final_lines))

    def generate_final_file(self, file_path: str, current_all_chunks: Dict[int, str]) -> str:
        # 이미 save_translated_chunk에서 직접 덮어쓰므로 경로만 반환합니다.
        p = Path(file_path)
        return str(p.parent / f"{p.stem}_translated{p.suffix}")
