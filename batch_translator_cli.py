# batch_translator_cli.py
import argparse
import sys
import os
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any
import threading
import logging
import json

# tqdm 및 기타 필요한 모듈 임포트
try:
    from tqdm import tqdm
except ImportError:
    class tqdm: # Fallback tqdm
        def __init__(self, iterable=None, total=None, desc=None, unit="it", leave=True, file=None, bar_format=None):
            self.iterable = iterable
            self.total = total if total is not None else (len(iterable) if hasattr(iterable, '__len__') else None)
            self.desc = desc
            self.unit = unit
            self.n = 0
            self.leave = leave
            self.file = file if file else sys.stderr
            self.bar_format = bar_format

        def __iter__(self):
            if self.iterable is None:
                raise ValueError("iterable is required for tqdm iteration")
            for obj in self.iterable:
                yield obj
                self.update(1)
            if self.leave:
                self.close()

        def update(self, n=1):
            self.n += n
            if self.total:
                percent = int((self.n / self.total) * 100)
                filled_len = int(20 * self.n // self.total)
                bar = '#' * filled_len + '-' * (20 - filled_len)
                status = f"{self.desc or ''}: {percent}%|{bar}| {self.n}/{self.total} [{self.unit}]"
                self.file.write(f"\r{status}")
                self.file.flush()
                if self.n == self.total and self.leave:
                    self.file.write('\n')
                    self.file.flush()

        def close(self):
            if self.total and self.n < self.total and self.leave:
                self.file.write('\n')
                self.file.flush()
            elif self.n == self.total and self.leave:
                pass

        def __enter__(self): return self
        def __exit__(self,et,ev,tb): self.close()
        @staticmethod
        def write(s, file=None, end="\n", nolock=False):
            _file = file if file else sys.stderr
            _file.write(s + end); _file.flush()
        def set_postfix(self, ordered_dict=None, refresh=True, **kwargs):
            postfix_parts = []
            if ordered_dict: postfix_parts.extend([f"{k}={v}" for k, v in ordered_dict.items()])
            if kwargs: postfix_parts.extend([f"{k}={v}" for k, v in kwargs.items()])
            if postfix_parts: Tqdm.write(f"  {', '.join(postfix_parts)}", file=self.file)

Tqdm = tqdm

try:
    from app_service import AppService
    from dtos import TranslationJobProgressDTO, LorebookExtractionProgressDTO # DTO 변경
    from exceptions import BtgException
    from logger_config import setup_logger
    from file_handler import (
        read_text_file, get_metadata_file_path, load_metadata,
        _hash_config_for_metadata, delete_file
    )
except ImportError as e:
    print(f"오류: 필요한 모듈을 임포트할 수 없습니다. PYTHONPATH를 확인하거나 "
          f"프로젝트 루트에서 스크립트를 실행하세요. ({e})")
    sys.exit(1)

cli_logger = setup_logger("btg_cli")

tqdm_instances: Dict[str, Tqdm] = {}
tqdm_lock = threading.Lock()

def cli_translation_progress_callback(dto: TranslationJobProgressDTO):
    global tqdm_instances
    task_id = "translation"

    with tqdm_lock:
        if task_id not in tqdm_instances or tqdm_instances[task_id].total != dto.total_chunks:
            if task_id in tqdm_instances: tqdm_instances[task_id].close()
            if dto.total_chunks > 0 :
                tqdm_instances[task_id] = Tqdm(total=dto.total_chunks, desc="청크 번역", unit="청크", leave=False, file=sys.stdout )
            else:
                if task_id in tqdm_instances: del tqdm_instances[task_id]
                return

        tqdm_instance = tqdm_instances.get(task_id)
        if not tqdm_instance: return

        if dto.processed_chunks > tqdm_instance.n :
             update_amount = dto.processed_chunks - tqdm_instance.n
             if update_amount > 0:
                tqdm_instance.update(update_amount)


        postfix_info = {}
        if dto.successful_chunks > 0: postfix_info["성공"] = dto.successful_chunks
        if dto.failed_chunks > 0: postfix_info["실패"] = dto.failed_chunks
        if dto.last_error_message: postfix_info["마지막오류"] = dto.last_error_message[:30]

        if postfix_info: tqdm_instance.set_postfix(postfix_info, refresh=True)

        if dto.processed_chunks == dto.total_chunks and dto.total_chunks > 0:
            tqdm_instance.close()
            if task_id in tqdm_instances: del tqdm_instances[task_id]

def cli_translation_status_callback(message: str):
    Tqdm.write(f"번역 상태: {message}", file=sys.stdout)


def cli_lorebook_extraction_progress_callback(dto: LorebookExtractionProgressDTO): # 함수명 및 DTO 변경
    global tqdm_instances
    task_id = "lorebook_extraction" # task_id 변경

    with tqdm_lock:
        if task_id not in tqdm_instances or tqdm_instances[task_id].total != dto.total_segments: # DTO 필드명 변경 (total_sample_chunks -> total_segments)
            if task_id in tqdm_instances: tqdm_instances[task_id].close()
            if dto.total_segments > 0: # DTO 필드명 변경
                tqdm_instances[task_id] = Tqdm(total=dto.total_segments, desc="로어북 추출 (표본)", unit="세그먼트", leave=False, file=sys.stdout) # 설명 변경
            else:
                if task_id in tqdm_instances: del tqdm_instances[task_id]
                return

        tqdm_instance = tqdm_instances.get(task_id)
        if not tqdm_instance: return

        if dto.processed_segments > tqdm_instance.n: # DTO 필드명 변경 (processed_sample_chunks -> processed_segments)
            update_amount = dto.processed_segments - tqdm_instance.n # DTO 필드명 변경
            if update_amount > 0:
                tqdm_instance.update(update_amount)

        if dto.current_status_message:
            postfix_info = {"status": dto.current_status_message}
            if dto.extracted_entries_count > 0: postfix_info["추출항목"] = dto.extracted_entries_count
            tqdm_instance.set_postfix(postfix_info, refresh=True)

        if dto.processed_segments == dto.total_segments and dto.total_segments > 0: # DTO 필드명 변경
            tqdm_instance.close()
            if task_id in tqdm_instances: del tqdm_instances[task_id]


def parse_arguments():
    """명령줄 인수를 파싱합니다."""
    parser = argparse.ArgumentParser(description="BTG - 배치 번역기 CLI (4-Tier Refactored)")
    parser.add_argument("input_file", type=Path, help="번역할 입력 텍스트 파일 경로")
    parser.add_argument("-o", "--output_file", type=Path, default=None, help="번역 결과를 저장할 파일 경로 (기본값: 입력파일_translated.txt)")
    parser.add_argument("-c", "--config", type=Path, default="config.json", help="설정 파일 경로 (기본값: config.json)")

    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument("--auth-credentials", type=str, default=None, help="Gemini API 키 (단일) 또는 Vertex AI 서비스 계정 JSON 문자열")
    auth_group.add_argument("--auth-credentials-file", type=Path, default=None, help="Vertex AI 서비스 계정 JSON 파일 경로")
    # 여러 API 키를 위한 인수 추가
    auth_group.add_argument("--api-keys", type=str, default=None, help="쉼표로 구분된 Gemini API 키 목록 (예: key1,key2,key3)")


    parser.add_argument("--use-vertex-ai", action="store_true", help="Vertex AI API를 사용합니다 (서비스 계정 필요).")
    parser.add_argument("--gcp-project", type=str, default=None, help="Vertex AI 사용 시 GCP 프로젝트 ID")
    parser.add_argument("--gcp-location", type=str, default=None, help="Vertex AI 사용 시 GCP 리전")

    parser.add_argument("--lorebook_seed_file", type=Path, default=None, help="로어북 생성 시 참고할 기존 로어북 JSON 파일 경로 (선택 사항)") # 인수명 변경 및 설명 수정
    parser.add_argument("--extract_lorebook_only", action="store_true", help="번역 대신 로어북 추출만 수행합니다.") # 인수명 변경

    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", action="store_true", help="이전 번역 작업을 이어받아 계속합니다.")
    resume_group.add_argument("--force-new", action="store_true", help="기존 작업 내역을 무시하고 강제로 새로 번역을 시작합니다.")

    parser.add_argument("--log_level", choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default='INFO', help="로그 레벨 설정 (기본값: INFO)")
    parser.add_argument("--log_file", type=Path, default=None, help="로그를 저장할 파일 경로 (기본값: btg_cli.log)")

    # 로어북 추출 및 번역 시 소설/출발 언어 지정 (통합됨)
    parser.add_argument("--novel-language", type=str, default=None, help="소설/번역 출발 언어 코드 (예: ko, en, ja, auto). config의 novel_language를 덮어씁니다.")

    # 동적 로어북 주입 설정
    dyn_lorebook_group = parser.add_argument_group('Dynamic Lorebook Injection Settings')
    dyn_lorebook_group.add_argument("--enable-dynamic-lorebook-injection", action="store_true", help="동적 로어북 주입 기능을 활성화합니다.")
    dyn_lorebook_group.add_argument("--max-lorebook-entries-injection", type=int, help="번역 청크당 주입할 최대 로어북 항목 수 (예: 3)")
    dyn_lorebook_group.add_argument("--max-lorebook-chars-injection", type=int, help="번역 청크당 주입할 로어북의 최대 총 문자 수 (예: 500)")
    # 설정 오버라이드
    config_override_group = parser.add_argument_group('Configuration Overrides')
    config_override_group.add_argument("--novel-language-override", type=str, help="설정 파일의 'novel_language' 값을 덮어씁니다. (--novel-language와 동일)")
    config_override_group.add_argument("--novel-language-fallback-override", type=str, help="설정 파일의 'novel_language_fallback' 값을 덮어씁니다.")
    config_override_group.add_argument("--rpm", type=int, help="분당 API 요청 수를 설정합니다. (예: 60). 0은 제한 없음을 의미합니다.")
    return parser.parse_args()

def main():
    args = parse_arguments()

    log_file_path = args.log_file if args.log_file else Path("btg_cli.log")
    setup_logger(logger_name="btg_cli", log_level=getattr(logging, args.log_level.upper()), log_file=log_file_path)

    cli_logger.info("BTG CLI 시작...")
    cli_logger.info(f"입력 파일: {args.input_file}")
    cli_logger.info(f"설정 파일: {args.config}")
    if args.resume: cli_logger.info("이어하기 옵션 (--resume) 활성화됨.")
    if args.force_new: cli_logger.info("새로 시작 옵션 (--force-new) 활성화됨.")


    try:
        app_service = AppService(config_file_path=args.config)
        cli_auth_applied = False

        if args.api_keys:
            api_keys_list = [key.strip() for key in args.api_keys.split(',') if key.strip()]
            if api_keys_list:
                app_service.config["api_keys"] = api_keys_list
                app_service.config["api_key"] = api_keys_list[0] # 첫 번째 키를 대표로 설정
                app_service.config["use_vertex_ai"] = False # API 키 사용 시 Vertex AI는 비활성화
                app_service.config["service_account_file_path"] = None
                app_service.config["auth_credentials"] = None # 다른 인증 방식 무시
                cli_auth_applied = True
                cli_logger.info(f"--api-keys로 {len(api_keys_list)}개의 API 키가 설정되었습니다.")
            else:
                cli_logger.warning("--api-keys 인수가 제공되었지만 유효한 키가 없습니다.")

        elif args.auth_credentials_file: # --api-keys가 제공되지 않았을 때만 처리
            cli_logger.info(f"서비스 계정 파일에서 인증 정보 로드 시도: {args.auth_credentials_file}")
            if not args.auth_credentials_file.exists():
                cli_logger.error(f"서비스 계정 파일을 찾을 수 없습니다: {args.auth_credentials_file}")
                sys.exit(1)
            try:
                app_service.config["service_account_file_path"] = str(args.auth_credentials_file.resolve())
                app_service.config["api_key"] = None
                app_service.config["api_keys"] = []
                app_service.config["auth_credentials"] = None
                app_service.config["use_vertex_ai"] = True
                cli_auth_applied = True
                cli_logger.info(f"서비스 계정 파일 경로 '{args.auth_credentials_file}'를 설정에 반영했습니다.")
            except Exception as e:
                cli_logger.error(f"서비스 계정 파일 경로 설정 중 오류: {e}")
                sys.exit(1)
        elif args.auth_credentials: # --api-keys 및 --auth-credentials-file이 없을 때 처리
            # 이 auth_credentials는 단일 API 키 또는 JSON 문자열일 수 있음
            # AppService.load_app_config()에서 이를 판단하여 api_key 또는 service_account_file_path로 변환
            app_service.config["auth_credentials"] = args.auth_credentials
            app_service.config["api_key"] = None # auth_credentials가 우선
            app_service.config["api_keys"] = []
            app_service.config["service_account_file_path"] = None
            cli_auth_applied = True
            cli_logger.info("명령줄 --auth-credentials 사용.")


        if args.use_vertex_ai and not args.api_keys: # --api-keys가 우선순위가 더 높음
            app_service.config["use_vertex_ai"] = True
            # use_vertex_ai가 True이면 api_key/api_keys는 사용 안 함을 명시
            app_service.config["api_key"] = None
            app_service.config["api_keys"] = []
            cli_auth_applied = True
        if args.gcp_project:
            app_service.config["gcp_project"] = args.gcp_project
            cli_auth_applied = True
        if args.gcp_location:
            app_service.config["gcp_location"] = args.gcp_location
            cli_auth_applied = True
        
        # 동적 로어북 주입 설정 CLI 인자 처리
        if args.enable_dynamic_lorebook_injection:
            app_service.config["enable_dynamic_lorebook_injection"] = True
            cli_auth_applied = True # config 변경 플래그로 사용
        if args.max_lorebook_entries_injection is not None:
            app_service.config["max_lorebook_entries_per_chunk_injection"] = args.max_lorebook_entries_injection
            cli_auth_applied = True
        if args.max_lorebook_chars_injection is not None:
            app_service.config["max_lorebook_chars_per_chunk_injection"] = args.max_lorebook_chars_injection
            cli_auth_applied = True
        # lorebook_json_path_injection 인자는 제거되었으므로, 관련 CLI 로직도 제거합니다.
        # 동적 주입 시에는 config.json의 "lorebook_json_path"를 사용합니다.

        # CLI 인자 --novel-language와 --novel-language-override 둘 다 novel_language 설정을 변경
        novel_lang_arg = args.novel_language or args.novel_language_override
        if novel_lang_arg:
            app_service.config["novel_language"] = novel_lang_arg
            cli_auth_applied = True
        if args.novel_language_fallback_override:
            app_service.config["novel_language_fallback"] = args.novel_language_fallback_override
            cli_auth_applied = True
        if args.rpm is not None:
            app_service.config["requests_per_minute"] = args.rpm
            cli_auth_applied = True
            cli_logger.info(f"분당 요청 수(RPM)가 CLI 인수로 인해 '{args.rpm}' (으)로 설정됩니다.")

        if cli_auth_applied:
            cli_logger.info("CLI 인수로 제공된 인증/Vertex 정보를 반영하기 위해 설정을 다시 로드합니다.")
            app_service.load_app_config()

        if args.lorebook_seed_file: # 인수명 변경
            cli_logger.info(f"로어북 생성 시 참고할 파일: {args.lorebook_seed_file}")
            # 이 파일은 AppService.extract_lorebook 메서드에 전달되거나,
            # ConfigManager를 통해 설정에 저장되어 LorebookService에서 사용될 수 있습니다.
            # 여기서는 AppService.config에 직접 저장하는 대신, extract_lorebook 호출 시 전달하는 것을 가정합니다.
            # app_service.config["lorebook_seed_file"] = str(args.lorebook_seed_file) # 필요시 AppService에서 처리
            app_service.load_app_config()


        if args.extract_lorebook_only: # 인수명 변경
            cli_logger.info("로어북 추출 모드로 실행합니다.") # 메시지 변경
            if not args.input_file.exists():
                cli_logger.error(f"입력 파일을 찾을 수 없습니다: {args.input_file}")
                sys.exit(1)

            result_lorebook_path = app_service.extract_lorebook( # app_service 메서드명 변경 가정
                args.input_file,
                progress_callback=cli_lorebook_extraction_progress_callback, # 콜백 함수명 변경
                novel_language_code=app_service.config.get("novel_language"), # 설정에서 가져온 novel_language 사용
                seed_lorebook_path=args.lorebook_seed_file
            )
            Tqdm.write(f"\n로어북 추출 완료. 결과 파일: {result_lorebook_path}", file=sys.stdout) # 메시지 변경

        else: # 번역 모드
            output_file = args.output_file
            if not output_file:
                output_file = args.input_file.parent / f"{args.input_file.stem}_translated{args.input_file.suffix}"
            cli_logger.info(f"출력 파일: {output_file}")

            if not args.input_file.exists():
                cli_logger.error(f"입력 파일을 찾을 수 없습니다: {args.input_file}")
                sys.exit(1)

            metadata_file_path = get_metadata_file_path(args.input_file)
            loaded_metadata = load_metadata(metadata_file_path)
            current_config_hash = _hash_config_for_metadata(app_service.config)
            previous_config_hash = loaded_metadata.get("config_hash")

            should_start_new = False

            if args.force_new:
                cli_logger.info("--force-new 옵션으로 강제 새로 번역을 시작합니다.")
                should_start_new = True
            elif args.resume:
                if previous_config_hash and previous_config_hash == current_config_hash:
                    cli_logger.info("--resume 옵션 및 설정 일치로 이어하기를 시도합니다.")
                elif previous_config_hash:
                    cli_logger.warning("설정이 이전 작업과 다릅니다. --resume 옵션이 무시되고 새로 번역을 시작합니다.")
                    should_start_new = True
                else:
                    cli_logger.info("이전 작업 내역이 없습니다. 새로 번역을 시작합니다. (--resume 옵션 무시)")
                    should_start_new = True
            else:
                if previous_config_hash and previous_config_hash == current_config_hash:
                    Tqdm.write(
                        f"'{args.input_file.name}'에 대한 이전 번역 작업 내역이 있습니다.\n"
                        f"  이어하려면: --resume\n"
                        f"  새로 시작하려면: --force-new\n"
                        "옵션 없이 실행하면 새로 번역을 시작합니다 (기존 내역 삭제). 계속하시겠습니까? [y/N]: ",
                        file=sys.stdout
                    )
                    try:
                        answer = input().lower()
                        if answer != 'y':
                            cli_logger.info("사용자 취소.")
                            sys.exit(0)
                        cli_logger.info("사용자 동의 하에 새로 번역을 시작합니다.")
                        should_start_new = True
                    except Exception:
                        cli_logger.info("입력 오류 또는 시간 초과로 프로그램을 종료합니다. 옵션을 명시하여 다시 실행해주세요.")
                        sys.exit(0)


                elif previous_config_hash and previous_config_hash != current_config_hash:
                    cli_logger.info("설정이 이전 작업과 다릅니다. 새로 번역을 시작합니다.")
                    should_start_new = True
                else:
                    cli_logger.info("이전 작업 내역이 없습니다. 새로 번역을 시작합니다.")
                    should_start_new = True

            if should_start_new:
                cli_logger.info("새로 번역을 위해 기존 메타데이터 및 출력 파일을 삭제합니다.")
                if metadata_file_path.exists(): delete_file(metadata_file_path)
                if output_file.exists(): delete_file(output_file)

            cli_logger.info("번역 모드로 실행합니다.")
            app_service.start_translation(
                args.input_file,
                output_file,
                progress_callback=cli_translation_progress_callback,
                status_callback=cli_translation_status_callback,
                tqdm_file_stream=sys.stdout
            )
            Tqdm.write(f"\n번역 작업 완료. 결과 파일: {output_file}", file=sys.stdout)

    except BtgException as e:
        cli_logger.error(f"BTG 애플리케이션 오류: {e}", exc_info=True)
        Tqdm.write(f"\n오류 발생: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        cli_logger.error(f"예상치 못한 오류 발생: {e}", exc_info=True)
        Tqdm.write(f"\n예상치 못한 오류: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        with tqdm_lock:
            for task_id in list(tqdm_instances.keys()):
                if tqdm_instances[task_id]:
                    tqdm_instances[task_id].close()
                del tqdm_instances[task_id]
        cli_logger.info("BTG CLI 종료.")

if __name__ == "__main__":
    main()
# batch_translator_cli.py
import argparse
import sys
import os
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any
import threading
import logging
import json

# tqdm 및 기타 필요한 모듈 임포트
try:
    from tqdm import tqdm
except ImportError:
    class tqdm: # Fallback tqdm
        def __init__(self, iterable=None, total=None, desc=None, unit="it", leave=True, file=None, bar_format=None):
            self.iterable = iterable
            self.total = total if total is not None else (len(iterable) if hasattr(iterable, '__len__') else None)
            self.desc = desc
            self.unit = unit
            self.n = 0
            self.leave = leave
            self.file = file if file else sys.stderr
            self.bar_format = bar_format

        def __iter__(self):
            if self.iterable is None:
                raise ValueError("iterable is required for tqdm iteration")
            for obj in self.iterable:
                yield obj
                self.update(1)
            if self.leave:
                self.close()

        def update(self, n=1):
            self.n += n
            if self.total:
                percent = int((self.n / self.total) * 100)
                filled_len = int(20 * self.n // self.total)
                bar = '#' * filled_len + '-' * (20 - filled_len)
                status = f"{self.desc or ''}: {percent}%|{bar}| {self.n}/{self.total} [{self.unit}]"
                self.file.write(f"\r{status}")
                self.file.flush()
                if self.n == self.total and self.leave:
                    self.file.write('\n')
                    self.file.flush()

        def close(self):
            if self.total and self.n < self.total and self.leave:
                self.file.write('\n')
                self.file.flush()
            elif self.n == self.total and self.leave:
                pass

        def __enter__(self): return self
        def __exit__(self,et,ev,tb): self.close()
        @staticmethod
        def write(s, file=None, end="\n", nolock=False):
            _file = file if file else sys.stderr
            _file.write(s + end); _file.flush()
        def set_postfix(self, ordered_dict=None, refresh=True, **kwargs):
            postfix_parts = []
            if ordered_dict: postfix_parts.extend([f"{k}={v}" for k, v in ordered_dict.items()])
            if kwargs: postfix_parts.extend([f"{k}={v}" for k, v in kwargs.items()])
            if postfix_parts: Tqdm.write(f"  {', '.join(postfix_parts)}", file=self.file)

Tqdm = tqdm

try:
    from app_service import AppService
    from dtos import TranslationJobProgressDTO, LorebookExtractionProgressDTO # DTO 변경
    from exceptions import BtgException
    from logger_config import setup_logger
    from file_handler import (
        read_text_file, get_metadata_file_path, load_metadata,
        _hash_config_for_metadata, delete_file
    )
except ImportError as e:
    print(f"오류: 필요한 모듈을 임포트할 수 없습니다. PYTHONPATH를 확인하거나 "
          f"프로젝트 루트에서 스크립트를 실행하세요. ({e})")
    sys.exit(1)

cli_logger = setup_logger("btg_cli")

tqdm_instances: Dict[str, Tqdm] = {}
tqdm_lock = threading.Lock()

def cli_translation_progress_callback(dto: TranslationJobProgressDTO):
    global tqdm_instances
    task_id = "translation"

    with tqdm_lock:
        if task_id not in tqdm_instances or tqdm_instances[task_id].total != dto.total_chunks:
            if task_id in tqdm_instances: tqdm_instances[task_id].close()
            if dto.total_chunks > 0 :
                tqdm_instances[task_id] = Tqdm(total=dto.total_chunks, desc="청크 번역", unit="청크", leave=False, file=sys.stdout )
            else:
                if task_id in tqdm_instances: del tqdm_instances[task_id]
                return

        tqdm_instance = tqdm_instances.get(task_id)
        if not tqdm_instance: return

        if dto.processed_chunks > tqdm_instance.n :
             update_amount = dto.processed_chunks - tqdm_instance.n
             if update_amount > 0:
                tqdm_instance.update(update_amount)


        postfix_info = {}
        if dto.successful_chunks > 0: postfix_info["성공"] = dto.successful_chunks
        if dto.failed_chunks > 0: postfix_info["실패"] = dto.failed_chunks
        if dto.last_error_message: postfix_info["마지막오류"] = dto.last_error_message[:30]

        if postfix_info: tqdm_instance.set_postfix(postfix_info, refresh=True)

        if dto.processed_chunks == dto.total_chunks and dto.total_chunks > 0:
            tqdm_instance.close()
            if task_id in tqdm_instances: del tqdm_instances[task_id]

def cli_translation_status_callback(message: str):
    Tqdm.write(f"번역 상태: {message}", file=sys.stdout)


def cli_lorebook_extraction_progress_callback(dto: LorebookExtractionProgressDTO): # 함수명 및 DTO 변경
    global tqdm_instances
    task_id = "lorebook_extraction" # task_id 변경

    with tqdm_lock:
        if task_id not in tqdm_instances or tqdm_instances[task_id].total != dto.total_segments: # DTO 필드명 변경 (total_sample_chunks -> total_segments)
            if task_id in tqdm_instances: tqdm_instances[task_id].close()
            if dto.total_segments > 0: # DTO 필드명 변경
                tqdm_instances[task_id] = Tqdm(total=dto.total_segments, desc="로어북 추출 (표본)", unit="세그먼트", leave=False, file=sys.stdout) # 설명 변경
            else:
                if task_id in tqdm_instances: del tqdm_instances[task_id]
                return

        tqdm_instance = tqdm_instances.get(task_id)
        if not tqdm_instance: return

        if dto.processed_segments > tqdm_instance.n: # DTO 필드명 변경 (processed_sample_chunks -> processed_segments)
            update_amount = dto.processed_segments - tqdm_instance.n # DTO 필드명 변경
            if update_amount > 0:
                tqdm_instance.update(update_amount)

        if dto.current_status_message:
            postfix_info = {"status": dto.current_status_message}
            if dto.extracted_entries_count > 0: postfix_info["추출항목"] = dto.extracted_entries_count
            tqdm_instance.set_postfix(postfix_info, refresh=True)

        if dto.processed_segments == dto.total_segments and dto.total_segments > 0: # DTO 필드명 변경
            tqdm_instance.close()
            if task_id in tqdm_instances: del tqdm_instances[task_id]


def parse_arguments():
    """명령줄 인수를 파싱합니다."""
    parser = argparse.ArgumentParser(description="BTG - 배치 번역기 CLI (4-Tier Refactored)")
    parser.add_argument("input_file", type=Path, help="번역할 입력 텍스트 파일 경로")
    parser.add_argument("-o", "--output_file", type=Path, default=None, help="번역 결과를 저장할 파일 경로 (기본값: 입력파일_translated.txt)")
    parser.add_argument("-c", "--config", type=Path, default="config.json", help="설정 파일 경로 (기본값: config.json)")

    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument("--auth-credentials", type=str, default=None, help="Gemini API 키 (단일) 또는 Vertex AI 서비스 계정 JSON 문자열")
    auth_group.add_argument("--auth-credentials-file", type=Path, default=None, help="Vertex AI 서비스 계정 JSON 파일 경로")
    # 여러 API 키를 위한 인수 추가
    auth_group.add_argument("--api-keys", type=str, default=None, help="쉼표로 구분된 Gemini API 키 목록 (예: key1,key2,key3)")


    parser.add_argument("--use-vertex-ai", action="store_true", help="Vertex AI API를 사용합니다 (서비스 계정 필요).")
    parser.add_argument("--gcp-project", type=str, default=None, help="Vertex AI 사용 시 GCP 프로젝트 ID")
    parser.add_argument("--gcp-location", type=str, default=None, help="Vertex AI 사용 시 GCP 리전")

    parser.add_argument("--lorebook_seed_file", type=Path, default=None, help="로어북 생성 시 참고할 기존 로어북 JSON 파일 경로 (선택 사항)") # 인수명 변경 및 설명 수정
    parser.add_argument("--extract_lorebook_only", action="store_true", help="번역 대신 로어북 추출만 수행합니다.") # 인수명 변경

    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", action="store_true", help="이전 번역 작업을 이어받아 계속합니다.")
    resume_group.add_argument("--force-new", action="store_true", help="기존 작업 내역을 무시하고 강제로 새로 번역을 시작합니다.")

    parser.add_argument("--log_level", choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default='INFO', help="로그 레벨 설정 (기본값: INFO)")
    parser.add_argument("--log_file", type=Path, default=None, help="로그를 저장할 파일 경로 (기본값: btg_cli.log)")

    # 로어북 추출 및 번역 시 소설/출발 언어 지정 (통합됨)
    parser.add_argument("--novel-language", type=str, default=None, help="소설/번역 출발 언어 코드 (예: ko, en, ja, auto). config의 novel_language를 덮어씁니다.")

    # 동적 로어북 주입 설정
    dyn_lorebook_group = parser.add_argument_group('Dynamic Lorebook Injection Settings')
    dyn_lorebook_group.add_argument("--enable-dynamic-lorebook-injection", action="store_true", help="동적 로어북 주입 기능을 활성화합니다.")
    dyn_lorebook_group.add_argument("--max-lorebook-entries-injection", type=int, help="번역 청크당 주입할 최대 로어북 항목 수 (예: 3)")
    dyn_lorebook_group.add_argument("--max-lorebook-chars-injection", type=int, help="번역 청크당 주입할 로어북의 최대 총 문자 수 (예: 500)")
    dyn_lorebook_group.add_argument("--lorebook-json-path-injection", type=Path, help="동적 주입에 사용할 로어북 JSON 파일 경로")
    # 설정 오버라이드
    config_override_group = parser.add_argument_group('Configuration Overrides')
    config_override_group.add_argument("--novel-language-override", type=str, help="설정 파일의 'novel_language' 값을 덮어씁니다. (--novel-language와 동일)")
    config_override_group.add_argument("--novel-language-fallback-override", type=str, help="설정 파일의 'novel_language_fallback' 값을 덮어씁니다.")
    config_override_group.add_argument("--rpm", type=int, help="분당 API 요청 수를 설정합니다. (예: 60). 0은 제한 없음을 의미합니다.")
    return parser.parse_args()

def main():
    args = parse_arguments()

    log_file_path = args.log_file if args.log_file else Path("btg_cli.log")
    setup_logger(logger_name="btg_cli", log_level=getattr(logging, args.log_level.upper()), log_file=log_file_path)

    cli_logger.info("BTG CLI 시작...")
    cli_logger.info(f"입력 파일: {args.input_file}")
    cli_logger.info(f"설정 파일: {args.config}")
    if args.resume: cli_logger.info("이어하기 옵션 (--resume) 활성화됨.")
    if args.force_new: cli_logger.info("새로 시작 옵션 (--force-new) 활성화됨.")


    try:
        app_service = AppService(config_file_path=args.config)
        cli_auth_applied = False

        if args.api_keys:
            api_keys_list = [key.strip() for key in args.api_keys.split(',') if key.strip()]
            if api_keys_list:
                app_service.config["api_keys"] = api_keys_list
                app_service.config["api_key"] = api_keys_list[0] # 첫 번째 키를 대표로 설정
                app_service.config["use_vertex_ai"] = False # API 키 사용 시 Vertex AI는 비활성화
                app_service.config["service_account_file_path"] = None
                app_service.config["auth_credentials"] = None # 다른 인증 방식 무시
                cli_auth_applied = True
                cli_logger.info(f"--api-keys로 {len(api_keys_list)}개의 API 키가 설정되었습니다.")
            else:
                cli_logger.warning("--api-keys 인수가 제공되었지만 유효한 키가 없습니다.")

        elif args.auth_credentials_file: # --api-keys가 제공되지 않았을 때만 처리
            cli_logger.info(f"서비스 계정 파일에서 인증 정보 로드 시도: {args.auth_credentials_file}")
            if not args.auth_credentials_file.exists():
                cli_logger.error(f"서비스 계정 파일을 찾을 수 없습니다: {args.auth_credentials_file}")
                sys.exit(1)
            try:
                app_service.config["service_account_file_path"] = str(args.auth_credentials_file.resolve())
                app_service.config["api_key"] = None
                app_service.config["api_keys"] = []
                app_service.config["auth_credentials"] = None
                app_service.config["use_vertex_ai"] = True
                cli_auth_applied = True
                cli_logger.info(f"서비스 계정 파일 경로 '{args.auth_credentials_file}'를 설정에 반영했습니다.")
            except Exception as e:
                cli_logger.error(f"서비스 계정 파일 경로 설정 중 오류: {e}")
                sys.exit(1)
        elif args.auth_credentials: # --api-keys 및 --auth-credentials-file이 없을 때 처리
            # 이 auth_credentials는 단일 API 키 또는 JSON 문자열일 수 있음
            # AppService.load_app_config()에서 이를 판단하여 api_key 또는 service_account_file_path로 변환
            app_service.config["auth_credentials"] = args.auth_credentials
            app_service.config["api_key"] = None # auth_credentials가 우선
            app_service.config["api_keys"] = []
            app_service.config["service_account_file_path"] = None
            cli_auth_applied = True
            cli_logger.info("명령줄 --auth-credentials 사용.")


        if args.use_vertex_ai and not args.api_keys: # --api-keys가 우선순위가 더 높음
            app_service.config["use_vertex_ai"] = True
            # use_vertex_ai가 True이면 api_key/api_keys는 사용 안 함을 명시
            app_service.config["api_key"] = None
            app_service.config["api_keys"] = []
            cli_auth_applied = True
        if args.gcp_project:
            app_service.config["gcp_project"] = args.gcp_project
            cli_auth_applied = True
        if args.gcp_location:
            app_service.config["gcp_location"] = args.gcp_location
            cli_auth_applied = True
        
        # 동적 로어북 주입 설정 CLI 인자 처리
        if args.enable_dynamic_lorebook_injection:
            app_service.config["enable_dynamic_lorebook_injection"] = True
            cli_auth_applied = True # config 변경 플래그로 사용
        if args.max_lorebook_entries_injection is not None:
            app_service.config["max_lorebook_entries_per_chunk_injection"] = args.max_lorebook_entries_injection
            cli_auth_applied = True
        if args.max_lorebook_chars_injection is not None:
            app_service.config["max_lorebook_chars_per_chunk_injection"] = args.max_lorebook_chars_injection
            cli_auth_applied = True
        if args.lorebook_json_path_injection:
            app_service.config["lorebook_json_path_for_injection"] = str(args.lorebook_json_path_injection.resolve())
            cli_auth_applied = True

        # CLI 인자 --novel-language와 --novel-language-override 둘 다 novel_language 설정을 변경
        novel_lang_arg = args.novel_language or args.novel_language_override
        if novel_lang_arg:
            app_service.config["novel_language"] = novel_lang_arg
            cli_auth_applied = True
        if args.novel_language_fallback_override:
            app_service.config["novel_language_fallback"] = args.novel_language_fallback_override
            cli_auth_applied = True
        if args.rpm is not None:
            app_service.config["requests_per_minute"] = args.rpm
            cli_auth_applied = True
            cli_logger.info(f"분당 요청 수(RPM)가 CLI 인수로 인해 '{args.rpm}' (으)로 설정됩니다.")

        if cli_auth_applied:
            cli_logger.info("CLI 인수로 제공된 인증/Vertex 정보를 반영하기 위해 설정을 다시 로드합니다.")
            app_service.load_app_config()

        if args.lorebook_seed_file: # 인수명 변경
            cli_logger.info(f"로어북 생성 시 참고할 파일: {args.lorebook_seed_file}")
            # 이 파일은 AppService.extract_lorebook 메서드에 전달되거나,
            # ConfigManager를 통해 설정에 저장되어 LorebookService에서 사용될 수 있습니다.
            # 여기서는 AppService.config에 직접 저장하는 대신, extract_lorebook 호출 시 전달하는 것을 가정합니다.
            # app_service.config["lorebook_seed_file"] = str(args.lorebook_seed_file) # 필요시 AppService에서 처리
            app_service.load_app_config()


        if args.extract_lorebook_only: # 인수명 변경
            cli_logger.info("로어북 추출 모드로 실행합니다.") # 메시지 변경
            if not args.input_file.exists():
                cli_logger.error(f"입력 파일을 찾을 수 없습니다: {args.input_file}")
                sys.exit(1)

            result_lorebook_path = app_service.extract_lorebook( # app_service 메서드명 변경 가정
                args.input_file,
                progress_callback=cli_lorebook_extraction_progress_callback, # 콜백 함수명 변경
                novel_language_code=app_service.config.get("novel_language"), # 설정에서 가져온 novel_language 사용
                seed_lorebook_path=args.lorebook_seed_file
            )
            Tqdm.write(f"\n로어북 추출 완료. 결과 파일: {result_lorebook_path}", file=sys.stdout) # 메시지 변경

        else: # 번역 모드
            output_file = args.output_file
            if not output_file:
                output_file = args.input_file.parent / f"{args.input_file.stem}_translated{args.input_file.suffix}"
            cli_logger.info(f"출력 파일: {output_file}")

            if not args.input_file.exists():
                cli_logger.error(f"입력 파일을 찾을 수 없습니다: {args.input_file}")
                sys.exit(1)

            metadata_file_path = get_metadata_file_path(args.input_file)
            loaded_metadata = load_metadata(metadata_file_path)
            current_config_hash = _hash_config_for_metadata(app_service.config)
            previous_config_hash = loaded_metadata.get("config_hash")

            should_start_new = False

            if args.force_new:
                cli_logger.info("--force-new 옵션으로 강제 새로 번역을 시작합니다.")
                should_start_new = True
            elif args.resume:
                if previous_config_hash and previous_config_hash == current_config_hash:
                    cli_logger.info("--resume 옵션 및 설정 일치로 이어하기를 시도합니다.")
                elif previous_config_hash:
                    cli_logger.warning("설정이 이전 작업과 다릅니다. --resume 옵션이 무시되고 새로 번역을 시작합니다.")
                    should_start_new = True
                else:
                    cli_logger.info("이전 작업 내역이 없습니다. 새로 번역을 시작합니다. (--resume 옵션 무시)")
                    should_start_new = True
            else:
                if previous_config_hash and previous_config_hash == current_config_hash:
                    Tqdm.write(
                        f"'{args.input_file.name}'에 대한 이전 번역 작업 내역이 있습니다.\n"
                        f"  이어하려면: --resume\n"
                        f"  새로 시작하려면: --force-new\n"
                        "옵션 없이 실행하면 새로 번역을 시작합니다 (기존 내역 삭제). 계속하시겠습니까? [y/N]: ",
                        file=sys.stdout
                    )
                    try:
                        answer = input().lower()
                        if answer != 'y':
                            cli_logger.info("사용자 취소.")
                            sys.exit(0)
                        cli_logger.info("사용자 동의 하에 새로 번역을 시작합니다.")
                        should_start_new = True
                    except Exception:
                        cli_logger.info("입력 오류 또는 시간 초과로 프로그램을 종료합니다. 옵션을 명시하여 다시 실행해주세요.")
                        sys.exit(0)


                elif previous_config_hash and previous_config_hash != current_config_hash:
                    cli_logger.info("설정이 이전 작업과 다릅니다. 새로 번역을 시작합니다.")
                    should_start_new = True
                else:
                    cli_logger.info("이전 작업 내역이 없습니다. 새로 번역을 시작합니다.")
                    should_start_new = True

            if should_start_new:
                cli_logger.info("새로 번역을 위해 기존 메타데이터 및 출력 파일을 삭제합니다.")
                if metadata_file_path.exists(): delete_file(metadata_file_path)
                if output_file.exists(): delete_file(output_file)

            cli_logger.info("번역 모드로 실행합니다.")
            app_service.start_translation(
                args.input_file,
                output_file,
                progress_callback=cli_translation_progress_callback,
                status_callback=cli_translation_status_callback,
                tqdm_file_stream=sys.stdout
            )
            Tqdm.write(f"\n번역 작업 완료. 결과 파일: {output_file}", file=sys.stdout)

    except BtgException as e:
        cli_logger.error(f"BTG 애플리케이션 오류: {e}", exc_info=True)
        Tqdm.write(f"\n오류 발생: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        cli_logger.error(f"예상치 못한 오류 발생: {e}", exc_info=True)
        Tqdm.write(f"\n예상치 못한 오류: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        with tqdm_lock:
            for task_id in list(tqdm_instances.keys()):
                if tqdm_instances[task_id]:
                    tqdm_instances[task_id].close()
                del tqdm_instances[task_id]
        cli_logger.info("BTG CLI 종료.")

if __name__ == "__main__":
    main()
