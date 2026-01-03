# chunk_service.py
from typing import List, Union, Optional
from pathlib import Path

try:
    from infrastructure.logger_config import setup_logger
    from core.exceptions import BtgChunkingException
except ImportError:
    from infrastructure.logging.logger_config import setup_logger # type: ignore
    from core.exceptions import BtgChunkingException # type: ignore

logger = setup_logger(__name__)

DEFAULT_MAX_CHUNK_SIZE = 6000

class ChunkService:
    """
    텍스트 콘텐츠를 지정된 크기의 청크로 분할하는 서비스를 제공합니다.
    """

    def split_text_into_chunks(self, text_content: str, max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE) -> List[str]:
        """
        주어진 텍스트 내용을 지정된 최대 크기의 청크 리스트로 분할합니다.
        분할은 주로 줄바꿈 문자를 기준으로 이루어지며, 각 청크는 max_chunk_size를 초과하지 않도록 합니다.

        Args:
            text_content (str): 분할할 전체 텍스트 내용.
            max_chunk_size (int, optional): 각 청크의 최대 문자 수. 
                                            기본값은 DEFAULT_MAX_CHUNK_SIZE.

        Returns:
            List[str]: 분할된 텍스트 청크의 리스트.

        Raises:
            ValueError: max_chunk_size가 0 이하인 경우.
            # BtgChunkingException: 청킹 중 예상치 못한 오류 발생 시 (현재는 ValueError만 발생)
        """
        if max_chunk_size <= 0:
            logger.error(f"max_chunk_size는 0보다 커야 합니다: {max_chunk_size}")
            raise ValueError("max_chunk_size는 0보다 커야 합니다.")

        chunks: List[str] = []
        current_chunk = ""
        
        # 텍스트 내용을 줄 단위로 분리 (개행 문자 유지)
        lines = text_content.splitlines(keepends=True)

        for line in lines:
            if len(current_chunk) + len(line) <= max_chunk_size:
                current_chunk += line
            else:
                # 현재 청크가 내용이 있으면 추가
                if current_chunk:
                    chunks.append(current_chunk)
                
                # 새 줄이 max_chunk_size보다 큰 경우 처리
                if len(line) > max_chunk_size:
                    logger.warning(f"단일 라인이 max_chunk_size({max_chunk_size})를 초과합니다. 강제 분할합니다. 라인 길이: {len(line)}")
                    # 긴 라인을 max_chunk_size에 맞춰 강제로 분할
                    for i in range(0, len(line), max_chunk_size):
                        chunks.append(line[i:i+max_chunk_size])
                    current_chunk = "" # 강제 분할 후 현재 청크는 비움
                else:
                    current_chunk = line
        
        # 마지막 남은 청크 추가
        if current_chunk:
            chunks.append(current_chunk)
        
        logger.info(f"텍스트가 {len(chunks)}개의 청크로 분할되었습니다 (최대 크기: {max_chunk_size}).")
        return chunks

    def create_chunks_from_file_content(self, file_content: str, max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE) -> List[str]:
        """
        파일에서 읽은 전체 텍스트 내용을 청크로 분할합니다.
        split_text_into_chunks 메소드의 래퍼 함수입니다.

        Args:
            file_content (str): 파일에서 읽은 전체 텍스트 내용.
            max_chunk_size (int, optional): 각 청크의 최대 문자 수.

        Returns:
            List[str]: 분할된 텍스트 청크의 리스트.
        """
        logger.debug(f"파일 내용으로부터 청크 생성 시작 (최대 크기: {max_chunk_size}). 내용 길이: {len(file_content)}")
        return self.split_text_into_chunks(file_content, max_chunk_size)
    
    def split_chunk_recursively(
    self, 
    chunk_text: str, 
    target_size: Optional[int] = None,
    min_chunk_size: int = 100,
    max_split_depth: int = 3,
    current_depth: int = 0
) -> List[str]:
        """
        청크를 재귀적으로 더 작은 청크로 분할합니다.
        
        Args:
            chunk_text: 분할할 텍스트
            target_size: 목표 청크 크기 (None이면 현재 크기의 절반)
            min_chunk_size: 최소 청크 크기
            max_split_depth: 최대 분할 깊이
            current_depth: 현재 분할 깊이
            
        Returns:
            분할된 청크 리스트
        """
        if current_depth >= max_split_depth:
            logger.warning(f"최대 분할 깊이({max_split_depth})에 도달했습니다.")
            return [chunk_text]
        
        if len(chunk_text.strip()) <= min_chunk_size:
            logger.info(f"최소 청크 크기({min_chunk_size})에 도달했습니다.")
            return [chunk_text]
        
        # 목표 크기가 지정되지 않으면 현재 크기의 절반으로 설정
        if target_size is None:
            target_size = len(chunk_text) // 2
        
        logger.info(f"청크 분할 시도 (깊이: {current_depth}, 현재 크기: {len(chunk_text)}, 목표 크기: {target_size})")
        
        # 기존 create_chunks_from_text 메서드 사용하여 분할
        sub_chunks = self.split_text_into_chunks(chunk_text, target_size)
        
        # 분할이 의미있게 되지 않은 경우 (1개 청크만 나온 경우)
        if len(sub_chunks) <= 1:
            # 더 작은 크기로 강제 분할 시도
            smaller_target = max(min_chunk_size, target_size // 2)
            if smaller_target < target_size:
                return self.split_chunk_recursively(
                    chunk_text, smaller_target, min_chunk_size, 
                    max_split_depth, current_depth + 1
                )
            else:
                return [chunk_text]
        
        logger.info(f"청크가 {len(sub_chunks)}개로 분할되었습니다.")
        return sub_chunks

    def split_chunk_into_two_halves(
        self,
        chunk_text: str,
        target_size: Optional[int] = None,
        min_chunk_ratio: float = 0.3
    ) -> List[str]:
        """
        텍스트를 정확히 2개의 청크로 분할합니다 (strict 이진 분할).
        마지막 청크가 너무 작으면 이전 청크와 병합하여 2개를 유지합니다.
        
        Args:
            chunk_text: 분할할 텍스트
            target_size: 목표 청크 크기 (None이면 현재 크기의 절반)
            min_chunk_ratio: 마지막 청크의 최소 비율 (기본 30%)
                           이보다 작으면 이전 청크와 병합
        
        Returns:
            정확히 2개의 청크 리스트 (분할 불가능하면 1개)
        """
        text_length = len(chunk_text)
        
        # 목표 크기 설정
        if target_size is None:
            target_size = text_length // 2
        
        # 최소 청크 크기 계산
        min_chunk_size = int(target_size * min_chunk_ratio)
        
        logger.info(f"이진 분할 시도: 전체 {text_length}자 → 목표 {target_size}자 (최소 {min_chunk_size}자)")
        
        # 기존 split_text_into_chunks로 분할 시도
        initial_chunks = self.split_text_into_chunks(chunk_text, target_size)
        
        # 분할 안됨
        if len(initial_chunks) <= 1:
            logger.warning("이진 분할 실패: 청크를 나눌 수 없음")
            return initial_chunks
        
        # 정확히 2개면 OK
        if len(initial_chunks) == 2:
            logger.info(f"✓ 이진 분할 성공: 2개 청크 ({len(initial_chunks[0])}자, {len(initial_chunks[1])}자)")
            return initial_chunks
        
        # 3개 이상: 마지막 청크가 작으면 병합
        if len(initial_chunks) >= 3:
            last_chunk_size = len(initial_chunks[-1])
            
            # 마지막 청크가 충분히 큰 경우: 앞의 청크들을 병합
            if last_chunk_size >= min_chunk_size:
                first_half = "".join(initial_chunks[:-1])
                second_half = initial_chunks[-1]
                logger.info(f"✓ 이진 분할 조정: {len(initial_chunks)}개 → 2개 (앞 병합: {len(first_half)}자, 마지막: {len(second_half)}자)")
                return [first_half, second_half]
            
            # 마지막 청크가 작은 경우: 마지막 2개를 병합
            else:
                if len(initial_chunks) == 2:
                    # 이미 2개인데 둘째가 작으면 그냥 반환 (분할 의미 없음)
                    logger.warning(f"⚠ 둘째 청크가 작음 ({last_chunk_size}자 < {min_chunk_size}자). 그대로 반환")
                    return initial_chunks
                else:
                    # 3개 이상: 마지막 2개 병합
                    first_half = "".join(initial_chunks[:-2])
                    second_half = "".join(initial_chunks[-2:])
                    logger.info(f"✓ 이진 분할 조정: {len(initial_chunks)}개 → 2개 (마지막 2개 병합: {len(first_half)}자, {len(second_half)}자)")
                    return [first_half, second_half]
        
        # 안전 장치
        return initial_chunks

    def split_chunk_by_sentences(self, chunk_text: str, max_sentences_per_chunk: int = 2) -> List[str]:
        """
        문장 단위로 청크를 분할합니다.
        """
        import re
        
        # 문장 분할 (한국어/영어 문장 부호 기준)
        sentence_patterns = [
            r'[.!?]+\s+',  # 영어 문장 종료
            r'[。！？]+\s*',  # 한국어 문장 종료
            r'[\n\r]+',  # 줄바꿈
        ]
        
        sentences = chunk_text
        for pattern in sentence_patterns:
            sentences = re.split(pattern, sentences)
        
        # 빈 문장 제거
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) <= 1:
            return [chunk_text]
        
        # 지정된 문장 수로 청크 생성
        chunks = []
        for i in range(0, len(sentences), max_sentences_per_chunk):
            chunk_sentences = sentences[i:i + max_sentences_per_chunk]
            chunks.append(' '.join(chunk_sentences))
        
        return chunks


if __name__ == '__main__':
    # ChunkService 테스트를 위한 간단한 예제 코드
    # 이 부분은 실제 애플리케이션에서는 호출되지 않으며, 개발 및 테스트 목적으로만 사용됩니다.
    
    # 로깅 설정 (테스트용)
    import logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger("chunk_service_test")

    chunk_service = ChunkService()

    # 테스트 케이스 1: 일반적인 텍스트
    print("\n--- 테스트 케이스 1: 일반 텍스트 ---")
    text1 = "이것은 첫 번째 줄입니다.\n이것은 두 번째 줄입니다.\n그리고 이것은 세 번째 줄입니다."
    chunks1 = chunk_service.split_text_into_chunks(text1, max_chunk_size=30)
    logger.info(f"원본 1 (길이 {len(text1)}):\n'''{text1}'''")
    for i, chunk in enumerate(chunks1):
        logger.info(f"청크 1-{i} (길이 {len(chunk)}):\n'''{chunk}'''")
    assert len(chunks1) > 0
    assert "".join(chunks1) == text1


    # 테스트 케이스 2: 긴 라인이 포함된 텍스트
    print("\n--- 테스트 케이스 2: 긴 라인 포함 ---")
    text2 = "짧은 첫 줄.\n매우 긴 한 줄입니다. 이 줄은 설정된 최대 청크 크기보다 길어서 강제로 분할되어야 합니다. 계속 이어지는 내용입니다.\n짧은 마지막 줄."
    chunks2 = chunk_service.split_text_into_chunks(text2, max_chunk_size=40)
    logger.info(f"원본 2 (길이 {len(text2)}):\n'''{text2}'''")
    for i, chunk in enumerate(chunks2):
        logger.info(f"청크 2-{i} (길이 {len(chunk)}):\n'''{chunk}'''")
    assert len(chunks2) > 1
    assert "".join(chunks2) == text2 # 원본 텍스트와 동일해야 함

    # 테스트 케이스 3: 청크 크기보다 작은 텍스트
    print("\n--- 테스트 케이스 3: 작은 텍스트 ---")
    text3 = "한 줄짜리 짧은 텍스트."
    chunks3 = chunk_service.split_text_into_chunks(text3, max_chunk_size=100)
    logger.info(f"원본 3 (길이 {len(text3)}):\n'''{text3}'''")
    for i, chunk in enumerate(chunks3):
        logger.info(f"청크 3-{i} (길이 {len(chunk)}):\n'''{chunk}'''")
    assert len(chunks3) == 1
    assert chunks3[0] == text3

    # 테스트 케이스 4: 빈 텍스트
    print("\n--- 테스트 케이스 4: 빈 텍스트 ---")
    text4 = ""
    chunks4 = chunk_service.split_text_into_chunks(text4, max_chunk_size=100)
    logger.info(f"원본 4 (길이 {len(text4)}):\n'''{text4}'''")
    logger.info(f"청크 4: {chunks4}")
    assert len(chunks4) == 0 # 빈 텍스트는 빈 청크 리스트 반환 (또는 [""], 현재는 [])

    # 테스트 케이스 5: 개행 문자로만 이루어진 텍스트
    print("\n--- 테스트 케이스 5: 개행 문자만 ---")
    text5 = "\n\n\n"
    chunks5 = chunk_service.split_text_into_chunks(text5, max_chunk_size=10)
    logger.info(f"원본 5 (길이 {len(text5)}):\n'''{text5}'''")
    for i, chunk in enumerate(chunks5):
        logger.info(f"청크 5-{i} (길이 {len(chunk)}):\n'''{chunk}'''")
    assert len(chunks5) > 0 # 각 개행이 청크가 될 수 있음 (splitlines(keepends=True) 동작에 따라)
    assert "".join(chunks5) == text5


    # 테스트 케이스 6: max_chunk_size 가 0 이하일 때 오류 발생
    print("\n--- 테스트 케이스 6: 잘못된 max_chunk_size ---")
    try:
        chunk_service.split_text_into_chunks("테스트", max_chunk_size=0)
    except ValueError as e:
        logger.info(f"예상된 오류 발생: {e}")
        assert "max_chunk_size는 0보다 커야 합니다" in str(e)

    logger.info("\nChunkService 테스트 완료.")
