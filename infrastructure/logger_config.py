"""Central logging configuration utilities."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Optional
from logging.handlers import RotatingFileHandler

DEFAULT_LOG_FILENAME = "btg_app.log"
DEFAULT_LOG_LEVEL = logging.DEBUG  # 기본 로깅 레벨
DEFAULT_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5
DEFAULT_LOG_ROOT = Path("logs")

try:  # pragma: no cover - optional dependency
    from concurrent_log_handler import ConcurrentRotatingFileHandler  # type: ignore
except ImportError:  # pragma: no cover - fallback path covered elsewhere
    ConcurrentRotatingFileHandler = None  # type: ignore


class LoggingManager:
    """Encapsulates log handler creation to keep SRP boundaries clear."""

    def __init__(self, log_root: Path = DEFAULT_LOG_ROOT) -> None:
        self._log_root = log_root
        self._session_dir = self._build_session_dir()
        self._lock = RLock()

    def _build_session_dir(self) -> Path:
        """Create a unique directory per run to avoid cross-process collisions."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pid = os.getpid()
        session_dir = (self._log_root / f"run_{timestamp}_{pid}").resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    @property
    def session_dir(self) -> Path:
        """Return the folder that stores the current run's default log files."""
        return self._session_dir

    def _resolve_log_file(self, log_file: Optional[Path]) -> Path:
        if log_file is not None:
            log_file = Path(log_file)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            return log_file
        default_path = self._session_dir / DEFAULT_LOG_FILENAME
        default_path.parent.mkdir(parents=True, exist_ok=True)
        return default_path

    def _build_file_handler(
        self,
        log_file: Path,
        max_bytes: int,
        backup_count: int,
    ) -> logging.Handler:
        if ConcurrentRotatingFileHandler is not None:
            return ConcurrentRotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                delay=True,
            )

        # Fallback: make filename process-unique to reduce rename contention.
        pid = os.getpid()
        log_file = log_file.with_name(f"{log_file.stem}_{pid}{log_file.suffix or '.log'}")
        return RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )

    def setup_logger(
        self,
        logger_name: str = "btg",
        log_level: int = DEFAULT_LOG_LEVEL,
        log_file: Optional[Path] = None,
        log_to_console: bool = True,
        log_to_file: bool = True,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> logging.Logger:
        logger = logging.getLogger(logger_name)
        with self._lock:
            if logger.hasHandlers():
                return logger

            logger.setLevel(log_level)
            formatter = logging.Formatter(DEFAULT_LOG_FORMAT)

            if log_to_console:
                logger.addHandler(self._build_console_handler(formatter))

            if log_to_file:
                target_file = self._resolve_log_file(log_file)
                file_handler = self._build_file_handler(target_file, max_bytes, backup_count)
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)

            if not log_to_console and not log_to_file:
                logger.addHandler(logging.NullHandler())

            return logger

    def _build_console_handler(self, formatter: logging.Formatter) -> logging.Handler:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            console_handler = logging.StreamHandler(sys.stdout)
        except (TypeError, AttributeError):
            import io

            safe_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            console_handler = logging.StreamHandler(safe_stdout)

        console_handler.setFormatter(formatter)
        return console_handler


_LOGGING_MANAGER = LoggingManager()


def setup_logger(
    logger_name: str = "btg",
    log_level: int = DEFAULT_LOG_LEVEL,
    log_file: Optional[Path] = None,
    log_to_console: bool = True,
    log_to_file: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """Public wrapper that delegates to the singleton logging manager."""

    return _LOGGING_MANAGER.setup_logger(
        logger_name=logger_name,
        log_level=log_level,
        log_file=log_file,
        log_to_console=log_to_console,
        log_to_file=log_to_file,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )


def get_log_session_dir() -> Path:
    """Expose the active session directory so callers can locate log files."""

    return _LOGGING_MANAGER.session_dir

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
    print(f" - 세션 로그 디렉터리: {get_log_session_dir()}")
    if custom_log_file.exists():
        print(f" - 커스텀 로그: {custom_log_file.resolve()}")
