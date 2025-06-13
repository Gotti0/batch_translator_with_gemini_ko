# exceptions.py
# Path: neo_batch_translator/core/exceptions.py

class BtgException(Exception):
    """BTG 애플리케이션의 모든 사용자 정의 예외에 대한 기본 클래스입니다."""
    def __init__(self, message: str, original_exception: Exception = None):
        super().__init__(message)
        self.original_exception = original_exception
        self.message = message

    def __str__(self):
        if self.original_exception:
            return f"{self.message}: {type(self.original_exception).__name__} - {str(self.original_exception)}"
        return self.message

# --- 데이터 접근/인프라 계층 예외 ---

class BtgDataAccessException(BtgException):
    """데이터 접근/인프라 계층에서 발생하는 일반적인 오류에 대한 기본 예외입니다."""
    pass

class BtgFileHandlerException(BtgDataAccessException):
    """파일 처리 중 발생하는 오류에 대한 예외입니다 (읽기, 쓰기, 경로 문제 등)."""
    pass

class BtgConfigException(BtgDataAccessException):
    """설정 파일 로드, 저장 또는 유효성 검사 중 발생하는 오류에 대한 예외입니다."""
    pass

class BtgApiClientException(BtgDataAccessException):
    """외부 API(예: Gemini) 통신 중 발생하는 일반적인 오류에 대한 예외입니다."""
    pass

class BtgApiRateLimitException(BtgApiClientException):
    """API 사용량 제한 관련 예외입니다."""
    pass

class BtgApiContentSafetyException(BtgApiClientException):
    """API 콘텐츠 안전 관련 예외입니다."""
    pass

class BtgApiInvalidRequestException(BtgApiClientException):
    """잘못된 API 요청 관련 예외입니다."""
    pass

# --- 비즈니스 로직 계층 예외 ---

class BtgBusinessLogicException(BtgException):
    """비즈니스 로직 계층에서 발생하는 일반적인 오류에 대한 기본 예외입니다."""
    pass

class BtgTranslationException(BtgBusinessLogicException):
    """번역 로직 수행 중 발생하는 특정 오류에 대한 예외입니다."""
    pass

class BtgInvalidTranslationLengthException(BtgTranslationException):
    """번역 결과의 길이가 원본과 비교하여 비정상적일 때 발생하는 예외입니다."""
    pass

class BtgChunkingException(BtgBusinessLogicException):
    """텍스트 청킹(분할) 로직 중 발생하는 오류에 대한 예외입니다."""
    pass

# --- 애플리케이션 계층 예외 ---

class BtgServiceException(BtgException):
    """애플리케이션 서비스 계층의 워크플로우 조정 중 발생하는 오류에 대한 예외입니다."""
    pass

# --- 프레젠테이션 계층 예외 (필요시) ---
class BtgUiException(BtgException):
    """사용자 인터페이스 관련 오류에 대한 예외입니다."""
    pass


if __name__ == '__main__':
    # 예외 클래스 테스트 (간단한 예시)
    def test_file_operation(fail: bool):
        if fail:
            raise BtgFileHandlerException("테스트 파일 읽기 실패", original_exception=IOError("디스크 공간 부족"))
        return "파일 읽기 성공"

    def test_api_call(status_code: int):
        if status_code == 429:
            raise BtgApiRateLimitException(f"API 사용량 초과 (상태 코드: {status_code})")
        elif status_code == 400:
            raise BtgApiInvalidRequestException(f"잘못된 API 요청 (상태 코드: {status_code})")
        elif status_code == 500:
            raise BtgApiClientException(f"API 서버 오류 (상태 코드: {status_code})")
        return "API 호출 성공"

    print("--- 예외 발생 테스트 ---")
    try:
        test_file_operation(True)
    except BtgFileHandlerException as e:
        print(f"잡힌 예외: {e}")
        if e.original_exception:
            print(f"  원래 예외: {type(e.original_exception).__name__} - {e.original_exception}")

    try:
        test_api_call(429)
    except BtgApiClientException as e: # BtgApiRateLimitException은 BtgApiClientException의 하위 클래스
        print(f"잡힌 예외: {e}")

    try:
        test_api_call(400)
    except BtgApiInvalidRequestException as e:
        print(f"잡힌 예외: {e}")
    
    print("\n--- 정상 작동 테스트 ---")
    try:
        result_file = test_file_operation(False)
        print(f"파일 작업 결과: {result_file}")
        result_api = test_api_call(200)
        print(f"API 호출 결과: {result_api}")
    except BtgException as e: # 모든 Btg 관련 예외를 잡을 수 있음
        print(f"예상치 못한 BTG 예외 발생: {e}")
