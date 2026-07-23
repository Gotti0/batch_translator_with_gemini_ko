from pathlib import Path
from typing import Dict, Any, List

from core.dtos import TranslationUnit
from infrastructure.file_handler import load_metadata, read_text_file
from domain.review_providers.base_provider import BaseReviewProvider

class IntegrityReviewProvider(BaseReviewProvider):
    def _resolve_paths(self, file_path: str) -> tuple[Path, Path]:
        p = Path(file_path)
        if p.stem.endswith("_translated"):
            orig_stem = p.stem[:-11]
            source_path = p.parent / f"{orig_stem}{p.suffix}"
            translated_path = p
        else:
            source_path = p
            translated_path = p.parent / f"{p.stem}_translated{p.suffix}"
        return source_path, translated_path

    def load_metadata(self, file_path: str) -> Dict[str, Any]:
        return load_metadata(file_path)

    def _get_chunked_units(self, file_path: str) -> List[List[TranslationUnit]]:
        content = read_text_file(file_path)
        if not content:
            return []
        lines = content.splitlines()
        units = [TranslationUnit(id=str(i), text=line) for i, line in enumerate(lines)]
        
        config = getattr(self.app_service, "config", {}) or {}
        max_chunk_size = config.get("chunk_size", 6000) if isinstance(config, dict) else 6000
        max_items = config.get("integrity_max_items", 200) if isinstance(config, dict) else 200
        if not isinstance(max_chunk_size, int):
            max_chunk_size = 6000
        if not isinstance(max_items, int):
            max_items = 200

        return self.chunk_service.split_nodes_into_chunks(units, max_chunk_size, max_items)

    def load_source_chunks(self, file_path: str) -> Dict[int, str]:
        source_path, _ = self._resolve_paths(file_path)
        chunks = self._get_chunked_units(str(source_path))
        return {i: "\n".join(u.text for u in chunk) for i, chunk in enumerate(chunks)}

    def load_translated_chunks(self, file_path: str) -> Dict[int, str]:
        source_path, translated_path = self._resolve_paths(file_path)
        
        if not translated_path.exists():
            return {}
            
        translated_content = read_text_file(translated_path)
        if not translated_content:
            return {}
            
        translated_lines = translated_content.splitlines()
        chunks = self._get_chunked_units(str(source_path))
        
        translated_chunks_map = {}
        line_idx = 0
        for i, chunk in enumerate(chunks):
            chunk_length = len(chunk)
            chunk_trans_lines = translated_lines[line_idx : line_idx + chunk_length]
            translated_chunks_map[i] = "\n".join(chunk_trans_lines)
            line_idx += chunk_length
            
        return translated_chunks_map

    async def retranslate_chunk(self, chunk_id: str, new_prompt: str, split_level: int = 1) -> str:
        # new_prompt는 재번역할 원문 텍스트입니다.
        lines = new_prompt.splitlines()
        units = [TranslationUnit(id=str(i), text=line) for i, line in enumerate(lines)]
        
        # split_level에 따라 units 리스트를 분할
        if split_level == 3:
            sub_chunks = self.chunk_service.split_nodes_into_chunks(units, max_chunk_size=1000, max_items_per_chunk=50)
        elif split_level == 2:
            mid1 = len(units) // 4
            mid2 = len(units) // 2
            mid3 = len(units) * 3 // 4
            sub_chunks = [units[:mid1], units[mid1:mid2], units[mid2:mid3], units[mid3:]]
            sub_chunks = [c for c in sub_chunks if c]
        elif split_level == 1:
            mid = len(units) // 2
            sub_chunks = [units[:mid], units[mid:]]
            sub_chunks = [c for c in sub_chunks if c]
        else:
            sub_chunks = [units]
            
        result_map = {}
        for sub_chunk in sub_chunks:
            if not sub_chunk:
                continue
            res = await self.translation_service._translate_integrity_chunk_with_retry(sub_chunk)
            result_map.update(res)
        
        # 결과를 라인 순서대로 합침
        result_lines = []
        for i in range(len(units)):
            result_lines.append(result_map.get(str(i), lines[i]))
            
        return "\n".join(result_lines)

    def save_translated_chunk(self, file_path: str, chunk_id: int, new_text: str, current_all_chunks: Dict[int, str]) -> None:
        _, translated_path = self._resolve_paths(file_path)
        
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
