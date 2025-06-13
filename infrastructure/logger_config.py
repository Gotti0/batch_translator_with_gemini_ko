# logger_config.py
# Path: neo_batch_translator/infrastructure/logging/logger_config.py
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

DEFAULT_LOG_FILENAME = "btg_app.log"
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DEFAULT_MAX_BYTES = 10*1024*1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5

def setup_logger(
    logger_name: str = 'btg', 
    log_level: int = DEFAULT_LOG_LEVEL, 
    log_file: Path = None,
    log_to_console: bool = True,
    log_to_file: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT
) -> logging.Logger:
    """
    지정된 이름과 설정으로 로거를 설정하고 반환합니다.

    Args:
        logger_name (str, optional): 로거의 이름. 기본값은 'btg'.
        log_level (int, optional): 로깅 레벨 (예: logging.INFO, logging.DEBUG). 기본값은 logging.INFO.
        log_file (Path, optional): 로그를 저장할 파일 경로. 
                                   None이면 기본값 'btg_app.log'를 사용합니다.
                                   log_to_file이 False이면 무시됩니다.
        log_to_console (bool, optional): 콘솔에 로그를 출력할지 여부. 기본값은 True.
        log_to_file (bool, optional): 파일에 로그를 저장할지 여부. 기본값은 True.
        max_bytes (int, optional): 로그 파일의 최대 크기 (바이트 단위). 
                                   RotatingFileHandler에 사용됩니다. 기본값은 10MB.
        backup_count (int, optional): 유지할 백업 로그 파일의 수. 
                                      RotatingFileHandler에 사용됩니다. 기본값은 5.

    Returns:
        logging.Logger: 설정된 로거 객체.
    """
    logger = logging.getLogger(logger_name)
    
    # 로거에 이미 핸들러가 설정되어 있는 경우 중복 추가 방지
    if logger.hasHandlers():
        # logger.debug(f"Logger '{logger_name}' already has handlers. Skipping setup.")
        return logger

    logger.setLevel(log_level)
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)

    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        # logger.debug(f"Console handler added to logger '{logger_name}'.")

    if log_to_file:
        if log_file is None:
            log_file = Path(DEFAULT_LOG_FILENAME)
        
        # 로그 파일 디렉토리 생성 (존재하지 않는 경우)
        log_file.parent.mkdir(parents=True, exist_ok=True)
            
        # 파일 핸들러 (RotatingFileHandler 사용)
        file_handler = RotatingFileHandler(
            log_file, 
            maxBytes=max_bytes, 
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        # logger.debug(f"File handler added to logger '{logger_name}' for file '{log_file}'.")
    
    if not log_to_console and not log_to_file:
        logger.addHandler(logging.NullHandler()) # 아무데도 출력 안 할 경우
        # logger.debug(f"Null handler added to logger '{logger_name}' as no output was specified.")


    # 전파(propagation) 설정: 루트 로거로 메시지가 전파되지 않도록 하여 중복 로깅 방지 (필요에 따라)
    # logger.propagate = False 
    # logger.info(f"Logger '{logger_name}' setup complete with level {logging.getLevelName(log_level)}.")
    return logger

if __name__ == '__main__':
    # 로거 설정 테스트
    # 1. 기본 설정으로 로거 생성 (콘솔 및 파일 출력)
    logger1 = setup_logger("my_app_logger", log_level=logging.DEBUG)
    logger1.debug("디버그 메시지 from logger1 (파일 및 콘솔)")
    logger1.info("정보 메시지 from logger1 (파일 및 콘솔)")
    logger1.warning("경고 메시지 from logger1 (파일 및 콘솔)")
    logger1.error("오류 메시지 from logger1 (파일 및 콘솔)")
    logger1.critical("치명적 오류 메시지 from logger1 (파일 및 콘솔)")

    # 2. 다른 이름과 다른 파일로 로거 생성 (콘솔 출력 없이)
    custom_log_file = Path("logs") / "custom_app.log"
    logger2 = setup_logger(
        "custom_logger", 
        log_level=logging.INFO, 
        log_file=custom_log_file, 
        log_to_console=False,
        log_to_file=True
    )
    logger2.info(f"이 메시지는 '{custom_log_file.name}' 파일에만 기록됩니다.")
    logger2.warning(f"이 경고도 '{custom_log_file.name}' 파일에만 기록됩니다.")

    # 3. 콘솔에만 출력하는 로거
    logger3 = setup_logger("console_only_logger", log_to_file=False)
    logger3.info("이 메시지는 콘솔에만 출력됩니다.")

    # 4. 이미 설정된 로거 다시 호출 (중복 설정 안됨 확인)
    logger1_again = setup_logger("my_app_logger")
    logger1_again.info("이 메시지는 logger1에 의해 한 번만 출력되어야 합니다 (중복 핸들러 없음).")


    print(f"\n로그 파일은 다음 위치에서 확인할 수 있습니다:")
    print(f" - 기본 로그: {Path(DEFAULT_LOG_FILENAME).resolve()}")
    if custom_log_file.exists():
        print(f" - 커스텀 로그: {custom_log_file.resolve()}")
