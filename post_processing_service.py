# post_processing_service.py
import re
from typing import Dict, List, Tuple
from pathlib import Path
from logger_config import setup_logger

logger = setup_logger(__name__)

class PostProcessingService:
    """번역 결과 후처리를 담당하는 서비스"""
    
    def __init__(self):
        # 제거할 패턴들 정의
        self.removal_patterns = [
            # 번역 헤더들 - OR 조건 최적화 및 공통 패턴 추출
            r'^(?:##\s*)?(?:번역\s*결과\s*:?\s*|(?:Translation|Korean|korean)(?:\s*:?\s*.*)?|한국어\s*:?\s*)$',
            
            # 전자책 관련 패턴들 통합 - 공통 키워드 기반 최적화
            r'.*?(?:본\s*)?전자책(?:은)?.*?(?:네트워크|업로드|공유|다운로드|txt|무료|완결본|제공|읽기).*?',
            
            # 네티즌/업로드 관련 패턴
            r'.*?네티즌이?\s*업로드.*?',
            
            # URL/사이트 패턴들 통합 - 문자 클래스 최적화
            r'\((?:www\.\s*[^)]*|[^)]*www\.[^)]*|베이커\([^)]*|\s*\)\s*무료.*?다운로드.*?)\)',
            
            # 보물창고/광고성 문구 통합
            r'.*?(?:보물창고|(?:최고|최대|최신)의?\s*(?:전자책|소설)\s*(?:사이트|플랫폼)|독자들?의?\s*(?:보물창고|천국)|가장\s*간단하고\s*직접적인.*?읽기).*?',
            
            # 네트워크 사이트 정보 - 앵커 최적화
            r'(?:.*?네트워크\s*\(www\..*?\).*?|주소는\s+입니다\.?|를\s*지원합니다[!\.]*)',
            
            # 기타 정리 패턴들 - Non-capturing groups 적용
            r'(?:```)',
        ]

        # HTML/XML 태그 정리 패턴
        self.html_cleanup_patterns = [
            (r'<(?:"[^"]*"[\'\"]*|\'[^\']*\'[\'\"]*|[^\'\">])+>', ''),
        ]
    
    def clean_translated_content(self, content: str) -> str:
        """개별 청크 내용을 정리 (청크 인덱스는 유지)"""
        if not content:
            return content
            
        cleaned = content.strip()
        
        # 기본 패턴 제거
        for pattern in self.removal_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.MULTILINE | re.IGNORECASE)
        
        # HTML 태그 정리
        for pattern, replacement in self.html_cleanup_patterns:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        
        # 연속된 빈 줄 정리 (3개 이상의 연속 개행을 2개로)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        
        # 앞뒤 공백 제거
        cleaned = cleaned.strip()
        
        return cleaned
    
    def post_process_merged_chunks(self, merged_chunks: Dict[int, str]) -> Dict[int, str]:
        """병합된 청크들에 대해 후처리 수행 (청크 인덱스는 아직 유지)"""
        logger.info(f"청크 내용 후처리 시작: {len(merged_chunks)}개 청크 처리")
        
        processed_chunks = {}
        
        for chunk_index, chunk_content in merged_chunks.items():
            try:
                # 개별 청크 정리 (청크 마커는 유지)
                cleaned_content = self.clean_translated_content(chunk_content)
                processed_chunks[chunk_index] = cleaned_content
                
                # 로깅 (디버깅용)
                if chunk_content != cleaned_content:
                    logger.debug(f"청크 {chunk_index} 내용 후처리 완료 (길이: {len(chunk_content)} -> {len(cleaned_content)})")
                else:
                    logger.debug(f"청크 {chunk_index} 변경사항 없음")
                    
            except Exception as e:
                logger.warning(f"청크 {chunk_index} 후처리 중 오류: {e}. 원본 내용 유지")
                processed_chunks[chunk_index] = chunk_content
        
        logger.info(f"청크 내용 후처리 완료: {len(processed_chunks)}개 청크 처리됨")
        return processed_chunks
    
    def remove_chunk_indexes_from_final_file(self, file_path: Path) -> bool:
        """
        최종 파일에서 청크 인덱스 마커들을 제거합니다.
        모든 청크가 병합되고 파일이 저장된 후에 호출되어야 합니다.
        """
        logger.info(f"최종 파일에서 청크 인덱스 제거 시작: {file_path}")
        
        try:
            # 파일 내용 읽기
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if not content.strip():
                logger.warning(f"파일이 비어있습니다: {file_path}")
                return True
            
            original_length = len(content)
            
            # 청크 인덱스 마커 패턴들 제거
            chunk_patterns_to_remove = [
                r'##CHUNK_INDEX:\s*\d+##\n',  # ##CHUNK_INDEX: 0##
                r'\n##END_CHUNK##\n*',        # ##END_CHUNK##
                r'^##CHUNK_INDEX:\s*\d+##\n', # 파일 시작 부분의 청크 인덱스
                r'##END_CHUNK##$',             # 파일 끝 부분의 END_CHUNK
            ]
            
            cleaned_content = content
            for pattern in chunk_patterns_to_remove:
                cleaned_content = re.sub(pattern, '', cleaned_content, flags=re.MULTILINE)
            
            # 연속된 빈 줄 정리 (3개 이상을 2개로)
            cleaned_content = re.sub(r'\n{3,}', '\n\n', cleaned_content)
            
            # 앞뒤 공백 정리
            cleaned_content = cleaned_content.strip()
            
            # 정리된 내용을 다시 파일에 저장
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_content)
            
            final_length = len(cleaned_content)
            logger.info(f"청크 인덱스 제거 완료: {file_path}")
            logger.info(f"파일 크기 변화: {original_length} -> {final_length} 글자 ({original_length - final_length} 글자 제거)")
            
            return True
            
        except Exception as e:
            logger.error(f"청크 인덱스 제거 중 오류 발생 ({file_path}): {e}", exc_info=True)
            return False
    
    def validate_html_structure(self, content: str) -> bool:
        """HTML 구조가 올바른지 간단히 검증"""
        try:
            # 기본적인 태그 균형 검사
            main_open = content.count('<main')
            main_close = content.count('</main>')
            
            if main_open != main_close:
                logger.warning(f"main 태그 불균형: 열림={main_open}, 닫힘={main_close}")
                return False
                
            return True
        except Exception as e:
            logger.warning(f"HTML 구조 검증 중 오류: {e}")
            return False
