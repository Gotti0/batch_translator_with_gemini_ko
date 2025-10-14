# batch_translator_cli.py
import argparse
import sys
import os
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any
import threading
import logging
import json

# 프로젝트 루트 디렉토리를 sys.path에 추가 (main_gui.py와 유사하게)
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

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
    from app.app_service import AppService
    from core.dtos import TranslationJobProgressDTO, GlossaryExtractionProgressDTO
    from core.exceptions import BtgException
    from infrastructure.logger_config import setup_logger
    from infrastructure.file_handler import (
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


def cli_glossary_extraction_progress_callback(dto: GlossaryExtractionProgressDTO): # 함수명 및 DTO 변경
    global tqdm_instances
    task_id = "glossary_extraction" # task_id 변경

    with tqdm_lock:
        if task_id not in tqdm_instances or tqdm_instances[task_id].total != dto.total_segments: # DTO 필드명 변경 (total_sample_chunks -> total_segments)
            if task_id in tqdm_instances: tqdm_instances[task_id].close()
            if dto.total_segments > 0: # DTO 필드명 변경
                tqdm_instances[task_id] = Tqdm(total=dto.total_segments, desc="용어집 추출 (표본)", unit="세그먼트", leave=False, file=sys.stdout) # 설명 변경
            else:
                if task_id in tqdm_instances: del tqdm_instances[task_id] # type: ignore
                return

        tqdm_instance = tqdm_instances.get(task_id)
        if not tqdm_instance: return

        if dto.processed_segments > tqdm_instance.n: # DTO 필드명 변경 (processed_sample_chunks -> processed_segments)
            update_amount = dto.processed_segments - tqdm_instance.n # DTO 필드명 변경
            if update_amount > 0:
                tqdm_instance.update(update_amount)

        if dto.current_status_message:
            postfix_info = {"상태": dto.current_status_message}
            if dto.extracted_entries_count > 0: postfix_info["추출항목"] = dto.extracted_entries_count
            tqdm_instance.set_postfix(postfix_info, refresh=True)

        if dto.processed_segments == dto.total_segments and dto.total_segments > 0: # DTO 필드명 변경
            tqdm_instance.close()
            if task_id in tqdm_instances: del tqdm_instances[task_id] # type: ignore


def parse_arguments():
    """명령줄 인수를 파싱합니다."""
    parser = argparse.ArgumentParser(description="BTG - 배치 번역기 CLI (4-Tier Refactored)")
    # 'input_file'을 'input_files'로 변경하고 nargs='+' 추가
    parser.add_argument("input_files", type=Path, nargs='+', help="번역할 입력 텍스트 파일 경로 (여러 개 지정 가능)")
    parser.add_argument("-o", "--output_file", type=Path, default=None, help="번역 결과를 저장할 파일 경로 (기본값: 입력파일_translated.txt)")
    parser.add_argument("-c", "--config", type=Path, default="config.json", help="설정 파일 경로 (기본값: config.json")

    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument("--auth-credentials", type=str, default=None, help="Gemini API 키 (단일) 또는 Vertex AI 서비스 계정 JSON 문자열")
    auth_group.add_argument("--auth-credentials-file", type=Path, default=None, help="Vertex AI 서비스 계정 JSON 파일 경로")
    # 여러 API 키를 위한 인수 추가
    auth_group.add_argument("--api-keys", type=str, default=None, help="쉼표로 구분된 Gemini API 키 목록 (예: key1,key2,key3)")


    parser.add_argument("--use-vertex-ai", action="store_true", help="Vertex AI API를 사용합니다 (서비스 계정 필요).")
    parser.add_argument("--gcp-project", type=str, default=None, help="Vertex AI 사용 시 GCP 프로젝트 ID")
    parser.add_argument("--gcp-location", type=str, default=None, help="Vertex AI 사용 시 GCP 리전")

    parser.add_argument("--seed-glossary-file", type=Path, default=None, help="용어집 생성 시 참고할 기존 용어집 JSON 파일 경로 (선택 사항)")
    parser.add_argument("--extract_glossary_only", action="store_true", help="번역 대신 용어집 추출만 수행합니다.") # 인수명 변경

    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", action="store_true", help="이전 번역 작업을 이어받아 계속합니다.")
    resume_group.add_argument("--force-new", action="store_true", help="기존 작업 내역을 무시하고 강제로 새로 번역을 시작합니다.")

    parser.add_argument("--log_level", choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default='INFO', help="로그 레벨 설정 (기본값: INFO)")
    parser.add_argument("--log_file", type=Path, default=None, help="로그를 저장할 파일 경로 (기본값: btg_cli.log)")

    # 용어집 추출 및 번역 시 소설/출발 언어 지정 (통합됨)
    parser.add_argument("--novel-language", type=str, default=None, help="소설/번역 출발 언어 코드 (예: ko, en, ja, auto). config의 novel_language를 덮어씁니다.")

    # 동적 용어집 주입 설정
    dyn_glossary_group = parser.add_argument_group('동적 용어집 주입 설정') # Group name changed
    dyn_glossary_group.add_argument("--enable-dynamic-glossary-injection", action="store_true", help="동적 용어집 주입 기능을 활성화합니다.") # Arg name and help text changed
    dyn_glossary_group.add_argument("--max-glossary-entries-injection", type=int, help="번역 청크당 주입할 최대 용어집 항목 수 (예: 3)") # Arg name and help text changed
    dyn_glossary_group.add_argument("--max-glossary-chars-injection", type=int, help="번역 청크당 주입할 용어집의 최대 총 문자 수 (예: 500)") # Arg name and help text changed
    # 설정 오버라이드
    config_override_group = parser.add_argument_group('Configuration Overrides')
    config_override_group.add_argument("--novel-language-override", type=str, help="설정 파일의 'novel_language' 값을 덮어씁니다. (--novel-language와 동일)")
    config_override_group.add_argument("--novel-language-fallback-override", type=str, help="설정 파일의 'novel_language_fallback' 값을 덮어씁니다.")
    config_override_group.add_argument("--rpm", type=float, help="분당 API 요청 수를 설정합니다. (예: 60). 0은 제한 없음을 의미합니다.")
    config_override_group.add_argument("--user-override-glossary-prompt", type=str, help="용어집 추출 시 사용할 사용자 정의 프롬프트를 설정합니다.")
    return parser.parse_args()

def main():
    args = parse_arguments()

    log_file_path = args.log_file if args.log_file else Path("btg_cli.log")
    setup_logger(logger_name="btg_cli", log_level=getattr(logging, args.log_level.upper()), log_file=log_file_path)

    cli_logger.info("BTG CLI 시작...")
    cli_logger.info(f"입력 파일: {args.input_files}")
    cli_logger.info(f"설정 파일: {args.config}")
    if args.resume: cli_logger.info("이어하기 옵션 (--resume) 활성화됨.")
    if args.force_new: cli_logger.info("새로 시작 옵션 (--force-new) 활성화됨.")


    try:
        app_service = AppService(config_file_path=args.config)
        
        cli_overrides: Dict[str, Any] = {}
        config_changed_by_cli = False

        if args.api_keys:
            api_keys_list = [key.strip() for key in args.api_keys.split(',') if key.strip()]
            if api_keys_list:
                cli_overrides["api_keys"] = api_keys_list
                cli_overrides["api_key"] = api_keys_list[0]
                cli_overrides["use_vertex_ai"] = False
                cli_overrides["service_account_file_path"] = None
                cli_overrides["auth_credentials"] = None
                config_changed_by_cli = True
                cli_logger.info(f"--api-keys로 {len(api_keys_list)}개의 API 키가 설정되었습니다.")
            else:
                cli_logger.warning("--api-keys 인수가 제공되었지만 유효한 키가 없습니다.")

        elif args.auth_credentials_file:
            cli_logger.info(f"서비스 계정 파일에서 인증 정보 로드 시도: {args.auth_credentials_file}")
            if not args.auth_credentials_file.exists():
                cli_logger.error(f"서비스 계정 파일을 찾을 수 없습니다: {args.auth_credentials_file}")
                sys.exit(1)
            try:
                cli_overrides["service_account_file_path"] = str(args.auth_credentials_file.resolve())
                cli_overrides["api_key"] = None
                cli_overrides["api_keys"] = []
                cli_overrides["auth_credentials"] = None
                cli_overrides["use_vertex_ai"] = True
                config_changed_by_cli = True
                cli_logger.info(f"서비스 계정 파일 경로 '{args.auth_credentials_file}'를 설정에 반영했습니다.")
            except Exception as e:
                cli_logger.error(f"서비스 계정 파일 경로 설정 중 오류: {e}")
                sys.exit(1)
        elif args.auth_credentials:
            cli_overrides["auth_credentials"] = args.auth_credentials
            cli_overrides["api_key"] = None
            cli_overrides["api_keys"] = []
            cli_overrides["service_account_file_path"] = None
            config_changed_by_cli = True
            cli_logger.info("명령줄 --auth-credentials 사용.")

        if args.use_vertex_ai and not args.api_keys: # --api-keys가 우선순위가 더 높음
            cli_overrides["use_vertex_ai"] = True
            cli_overrides["api_key"] = None
            cli_overrides["api_keys"] = []
            config_changed_by_cli = True
        if args.gcp_project:
            cli_overrides["gcp_project"] = args.gcp_project
            config_changed_by_cli = True
        if args.gcp_location:
            cli_overrides["gcp_location"] = args.gcp_location
            config_changed_by_cli = True
        
        # 동적 로어북 주입 설정 CLI 인자 처리
        if args.enable_dynamic_glossary_injection: # Arg name changed
            cli_overrides["enable_dynamic_glossary_injection"] = True # Key changed
            config_changed_by_cli = True
        if args.max_glossary_entries_injection is not None: # Arg name changed
            cli_overrides["max_glossary_entries_per_chunk_injection"] = args.max_glossary_entries_injection # Key changed
            config_changed_by_cli = True
        if args.max_glossary_chars_injection is not None: # Arg name changed
            cli_overrides["max_glossary_chars_per_chunk_injection"] = args.max_glossary_chars_injection # Key changed
            config_changed_by_cli = True

        # CLI 인자 --novel-language와 --novel-language-override 둘 다 novel_language 설정을 변경
        novel_lang_arg = args.novel_language or args.novel_language_override
        if novel_lang_arg:
            cli_overrides["novel_language"] = novel_lang_arg
            config_changed_by_cli = True
        if args.novel_language_fallback_override:
            cli_overrides["novel_language_fallback"] = args.novel_language_fallback_override
            config_changed_by_cli = True
        if args.rpm is not None:
            cli_overrides["requests_per_minute"] = args.rpm
            config_changed_by_cli = True
            cli_logger.info(f"분당 요청 수(RPM)가 CLI 인수로 인해 '{args.rpm}' (으)로 설정됩니다.")
        if args.user_override_glossary_prompt:
            cli_overrides["user_override_glossary_extraction_prompt"] = args.user_override_glossary_prompt
            config_changed_by_cli = True
            cli_logger.info(f"사용자 재정의 용어집 추출 프롬프트가 CLI 인수로 설정되었습니다.")

        if config_changed_by_cli:
            cli_logger.info("CLI 인수로 제공된 설정을 반영하기 위해 AppService 설정을 다시 로드합니다.")
            app_service.load_app_config(runtime_overrides=cli_overrides)

        if args.seed_glossary_file:
            cli_logger.info(f"용어집 생성 시 참고할 파일: {args.seed_glossary_file}")
            # seed_glossary_file은 extract_glossary 호출 시 직접 전달되므로,
            # config에 저장하고 load_app_config를 통해 보존할 필요는 현재 없습니다.
            # 만약 이 값도 config의 일부로 관리하고 싶다면, cli_overrides에 추가하고
            # app_service.load_app_config(runtime_overrides=cli_overrides)를 호출해야 합니다.
            # 현재는 AppService의 config에 직접 영향을 주지 않으므로 load_app_config 호출 불필요.
        
        # type: ignore
        if args.extract_glossary_only:
            # 용어집 추출은 첫 번째 파일에 대해서만 수행하거나, 각 파일에 대해 수행하도록 선택할 수 있습니다.
            # 여기서는 첫 번째 파일을 기준으로 합니다.
            first_input_file = args.input_files[0]
            cli_logger.info(f"용어집 추출 모드로 실행합니다. 대상 파일: {first_input_file}")
            if not first_input_file.exists():
                cli_logger.error(f"입력 파일을 찾을 수 없습니다: {first_input_file}")
                sys.exit(1)
        
            result_glossary_path = app_service.extract_glossary(
                first_input_file,
                progress_callback=cli_glossary_extraction_progress_callback,
                novel_language_code=app_service.config.get("novel_language"),
                seed_glossary_path=args.seed_glossary_file,
                user_override_glossary_extraction_prompt=app_service.config.get("user_override_glossary_extraction_prompt")
            )
            Tqdm.write(f"\n용어집 추출 완료. 결과 파일: {result_glossary_path}", file=sys.stdout)

        else: # 번역 모드
            # 번역 모드: 모든 파일을 순차적으로 처리
            total_files = len(args.input_files)
            cli_logger.info(f"총 {total_files}개의 파일에 대한 번역을 시작합니다.")

            for i, input_file in enumerate(args.input_files):
                cli_logger.info(f"--- 파일 {i+1}/{total_files} 처리 시작: {input_file} ---")

                if not input_file.exists():
                    cli_logger.error(f"입력 파일을 찾을 수 없습니다: {input_file}. 이 파일을 건너뜁니다.")
                    continue

                output_file = args.output_file
                if not output_file: # 출력 파일이 지정되지 않은 경우, 각 입력 파일에 맞춰 자동 생성
                    output_file = input_file.parent / f"{input_file.stem}_translated{input_file.suffix}"
                
                cli_logger.info(f"출력 파일: {output_file}")

                metadata_file_path = get_metadata_file_path(input_file)
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
                            f"'{input_file.name}'에 대한 이전 번역 작업 내역이 있습니다.\n"
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

                app_service.start_translation(
                    input_file,
                    output_file,
                    progress_callback=cli_translation_progress_callback,
                    status_callback=cli_translation_status_callback,
                    tqdm_file_stream=sys.stdout,
                    blocking=True # CLI에서는 순차 처리를 위해 blocking=True로 호출
                )
                cli_logger.info(f"--- 파일 {i+1}/{total_files} 처리 완료: {input_file} ---")
            
            Tqdm.write(f"\n모든 번역 작업 완료.", file=sys.stdout)

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
                    tqdm_instances[task_id].close() # type: ignore
                del tqdm_instances[task_id] # type: ignore
        cli_logger.info("BTG CLI 종료.")


if __name__ == "__main__":
    main()
