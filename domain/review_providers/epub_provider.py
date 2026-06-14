import json
import zipfile
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple

from core.dtos import TranslationUnit, EpubChapter, NodeType
from infrastructure.file_handler import load_metadata
from domain.review_providers.base_provider import BaseReviewProvider
from utils.epub_processor import EpubProcessor

logger = logging.getLogger("epub_provider")

class EpubReviewProvider(BaseReviewProvider):
    def __init__(self, app_service):
        super().__init__(app_service)
        self.processor = EpubProcessor()

    def load_metadata(self, file_path: str) -> Dict[str, Any]:
        meta = load_metadata(file_path)
        if "total_chunks" not in meta:
            chunks, _ = self._get_all_epub_chunks(file_path)
            meta["total_chunks"] = len(chunks)
            meta["translated_chunks"] = {str(i): {"status": "success"} for i in range(len(chunks))}
        return meta

    def _get_all_epub_chunks(self, file_path: str) -> Tuple[List[List[TranslationUnit]], List[str]]:
        """
        EPUB의 모든 챕터를 순회하여 전역 청크 리스트와 각 청크가 속한 파일명을 반환합니다.
        """
        chunks_all = []
        chunk_files = []
        
        max_chunk_size = self.app_service.config.get("chunk_size", 6000)
        max_items = self.app_service.config.get("integrity_max_items", 200)

        if not Path(file_path).exists():
            return [], []

        try:
            with zipfile.ZipFile(file_path, 'r') as zin:
                for item in zin.infolist():
                    if item.filename.lower().endswith(('.xhtml', '.html', '.htm')):
                        content = zin.read(item.filename)
                        chapter = self.processor.process_chapter(content, item.filename)
                        translatable_nodes = [n for n in chapter.nodes if n.type == NodeType.TEXT]
                        if translatable_nodes:
                            units = [TranslationUnit(id=n.id, text=n.content or "") for n in translatable_nodes]
                            chunks = self.chunk_service.split_nodes_into_chunks(units, max_chunk_size, max_items)
                            for c in chunks:
                                chunks_all.append(c)
                                chunk_files.append(item.filename)
        except zipfile.BadZipFile:
            logger.warning(f"손상되거나 번역이 완료되지 않은 EPUB 파일입니다 (무시됨): {file_path}")
            return [], []
        except Exception as e:
            logger.error(f"EPUB 파일 읽기 중 예기치 못한 오류 발생 ({file_path}): {e}")
            return [], []
                            
        return chunks_all, chunk_files

    def load_source_chunks(self, file_path: str) -> Dict[int, str]:
        chunks, _ = self._get_all_epub_chunks(file_path)
        return {i: "\n".join(u.text for u in chunk) for i, chunk in enumerate(chunks)}

    def load_translated_chunks(self, file_path: str) -> Dict[int, str]:
        p = Path(file_path)
        translated_path = p.parent / f"{p.stem}_translated{p.suffix}"
        
        if not translated_path.exists():
            return {}
            
        # 번역된 EPUB에서 텍스트 노드를 다시 추출하여 매핑
        translated_chunks_map = {}
        
        # 원본 및 번역본 구조 로드
        chunks_src, _ = self._get_all_epub_chunks(file_path)
        chunks_trans, _ = self._get_all_epub_chunks(str(translated_path))
        
        # 원본 구조와 번역본 구조 매핑
        for i in range(min(len(chunks_src), len(chunks_trans))):
            translated_chunks_map[i] = "\n".join(u.text for u in chunks_trans[i])
            
        # 버퍼에 캐싱된 수정본 덮어쓰기
        buffer_path = p.parent / f"{p.stem}_epub_temp" / "edited_chunks.json"
        if buffer_path.exists():
            try:
                with open(buffer_path, 'r', encoding='utf-8') as f:
                    edited = json.load(f)
                for k, v in edited.items():
                    translated_chunks_map[int(k)] = v
            except Exception as e:
                logger.warning(f"수정 버퍼 파일 읽기 실패: {e}")
            
        return translated_chunks_map

    async def retranslate_chunk(self, chunk_id: str, new_prompt: str) -> str:
        # new_prompt는 수정된 원문
        lines = new_prompt.splitlines()
        units = [TranslationUnit(id=str(i), text=line) for i, line in enumerate(lines)]
        
        result_map = await self.translation_service._translate_integrity_chunk_with_retry(units)
        
        result_lines = []
        for i in range(len(units)):
            result_lines.append(result_map.get(str(i), lines[i]))
            
        return "\n".join(result_lines)

    def save_translated_chunk(self, file_path: str, chunk_id: int, new_text: str, current_all_chunks: Dict[int, str]) -> None:
        p = Path(file_path)
        temp_dir = p.parent / f"{p.stem}_epub_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        buffer_path = temp_dir / "edited_chunks.json"
        
        edited = {}
        if buffer_path.exists():
            try:
                with open(buffer_path, 'r', encoding='utf-8') as f:
                    edited = json.load(f)
            except:
                pass
                
        edited[str(chunk_id)] = new_text
        with open(buffer_path, 'w', encoding='utf-8') as f:
            json.dump(edited, f, ensure_ascii=False)

    def generate_final_file(self, file_path: str, current_all_chunks: Dict[int, str]) -> str:
        p = Path(file_path)
        final_path = p.parent / f"{p.stem}_edited{p.suffix}"
        
        chunks_src, files_src = self._get_all_epub_chunks(file_path)
        
        # current_all_chunks (청크 ID -> 번역 텍스트)를
        # TranslationUnit.id -> 번역 텍스트 맵으로 변환해야 함
        node_translation_map = {}
        for i, chunk in enumerate(chunks_src):
            if i in current_all_chunks:
                # \n으로 결합된 텍스트를 다시 분리
                trans_lines = current_all_chunks[i].splitlines()
                # 원본 노드 수와 일치하게 매핑
                for j, unit in enumerate(chunk):
                    if j < len(trans_lines):
                        node_translation_map[unit.id] = trans_lines[j]
                    else:
                        node_translation_map[unit.id] = unit.text # fallback
                        
        # 원본 EPUB을 열어 재조립
        with zipfile.ZipFile(file_path, 'r') as zin:
            with zipfile.ZipFile(final_path, 'w') as zout:
                if 'mimetype' in zin.namelist():
                    zout.writestr('mimetype', zin.read('mimetype'), compress_type=zipfile.ZIP_STORED)
                
                for item in zin.infolist():
                    if item.filename == 'mimetype':
                        continue
                        
                    content = zin.read(item.filename)
                    if item.filename.lower().endswith(('.xhtml', '.html', '.htm')):
                        chapter = self.processor.process_chapter(content, item.filename)
                        
                        # 텍스트 노드가 하나라도 있으면 재조립 수행
                        translatable_nodes = [n for n in chapter.nodes if n.type == NodeType.TEXT]
                        if translatable_nodes:
                            html = self.processor.reconstruct_chapter(chapter, node_translation_map)
                            zout.writestr(item.filename, html.encode('utf-8'), compress_type=zipfile.ZIP_DEFLATED)
                        else:
                            zout.writestr(item.filename, content, compress_type=zipfile.ZIP_DEFLATED)
                    else:
                        if item.filename.lower().endswith('.opf'):
                            try:
                                import re
                                opf_text = content.decode('utf-8')
                                opf_text = re.sub(r'page-progression-direction\s*=\s*["\']rtl["\']', 'page-progression-direction="ltr"', opf_text, flags=re.IGNORECASE)
                                zout.writestr(item, opf_text.encode('utf-8'), compress_type=zipfile.ZIP_DEFLATED)
                            except Exception as e_opf:
                                logger.error(f"OPF 방향 수정 중 오류: {e_opf}")
                                zout.writestr(item, content, compress_type=zipfile.ZIP_DEFLATED)
                        else:
                            zout.writestr(item, content, compress_type=zipfile.ZIP_DEFLATED)
                        
        # 병합 성공 시 버퍼 삭제
        buffer_path = p.parent / f"{p.stem}_epub_temp" / "edited_chunks.json"
        if buffer_path.exists():
            buffer_path.unlink()
            
        return str(final_path)
