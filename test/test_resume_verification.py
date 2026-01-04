import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import time
import json
from pathlib import Path

# 테스트 스크립트에서 프로젝트의 다른 모듈을 가져올 수 있도록 프로젝트 루트를 경로에 추가합니다.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.app_service import AppService
from core.exceptions import BtgTranslationException
from infrastructure.file_handler import get_metadata_file_path, read_text_file, save_chunk_with_index_to_file
from domain.translation_service import TranslationService

# -- 테스트 설정 --
TEST_DIR = Path(__file__).resolve().parent
INPUT_FILE = TEST_DIR / "resume_test_input.txt"
OUTPUT_FILE = TEST_DIR / "resume_test_output.txt"
METADATA_FILE = get_metadata_file_path(INPUT_FILE)

# 테스트용 더미 입력 파일을 생성합니다.
INPUT_FILE.write_text(
    "This is the first chunk.\n"
    "This is the second chunk which will fail.\n"
    "This is the third chunk.",
    encoding='utf-8'
)

# -- 테스트 주석: 비동기 마이그레이션으로 인해 이 테스트는 더 이상 유효하지 않습니다 --
# 기존 동기 API를 사용하는 테스트는 비활성화되었습니다.
# 이 기능을 테스트하려면 start_translation_async() 및 asyncio를 사용하여 재작성 필요
#
# def mocked_translate(self, chunk_text, *args, **kwargs):
#     print(f"모의 번역기 호출됨: '{chunk_text.strip()}'")
#     if "which will fail" in chunk_text:
#         print("  -> 이 청크에 대한 실패를 시뮬레이션합니다.")
#         raise BtgTranslationException("테스트를 위한 의도된 API 실패")
#     else:
#         print("  -> 이 청크에 대한 성공을 시뮬레이션합니다.")
#         return f"[Translated] {chunk_text}"
#
# def run_test():
#     """테스트의 전체 흐름을 관리하고 검증합니다."""
    print("--- 테스트 시작: 실패한 청크가 완료로 표시되지 않는지 확인 ---")

    # 이전 테스트 실행으로 남은 파일들을 정리합니다.
    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()
    if METADATA_FILE.exists():
        METADATA_FILE.unlink()

    # --- 1차 실행: 실패 유도 ---
    print("\n--- 1. 첫 실행: 두 번째 청크에서 실패 유도 ---")
    app_service = AppService()
    # 각 줄이 별도의 청크가 되도록 청크 크기를 작게 설정하고, 클라이언트 초기화를 위해 더미 키를 제공합니다.
    app_service.load_app_config(runtime_overrides={"chunk_size": 50, "api_keys": ["dummy-key-for-init"], "max_threads": 1})

    # 'patch'를 사용하여 실제 번역 함수를 위에서 정의한 모의 함수로 일시적으로 교체합니다.
    with patch.object(TranslationService, 'translate_text_with_content_safety_retry', new=mocked_translate):
        app_service.start_translation(INPUT_FILE, OUTPUT_FILE)

    print("\n--- 첫 실행 후 검증 ---")
    if not METADATA_FILE.exists():
        print(f"❌ 실패: 메타데이터 파일 '{METADATA_FILE}'이 생성되지 않았습니다.")
        return

    with open(METADATA_FILE, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    print(f"메타데이터 내용: {json.dumps(metadata, indent=2)}")

    completed_chunks = metadata.get("translated_chunks", [])
    print(f"메타데이터에 기록된 성공 청크: {completed_chunks}")

    # 청크는 0부터 시작합니다. 0번과 2번 청크는 성공하고, 1번 청크는 실패해야 합니다.
    if 1 in completed_chunks:
        print("❌ 테스트 실패: 실패한 청크(인덱스 1)가 'translated_chunks' 목록에 잘못 추가되었습니다.")
    elif 0 in completed_chunks and 2 in completed_chunks:
        print("✅ 테스트 통과: 실패한 청크(인덱스 1)는 'translated_chunks'에 없으며, 성공한 청크(0, 2)만 존재합니다.")
    else:
        print(f"❌ 테스트 실패: 'translated_chunks' 내용이 예상과 다릅니다: {completed_chunks}. 예상: [0, 2] 또는 [2, 0]")


    # --- 2차 실행: 재시도 검증 ---
    print("\n--- 2. 두 번째 실행: 실패한 청크만 재시도되는지 확인 ---")
    
    # 두 번째 실행에서는 실패했던 청크가 성공하도록 모의 함수를 변경합니다.
    def mocked_translate_second_run(self, chunk_text, *args, **kwargs):
        print(f"모의 번역기 (2차 실행) 호출됨: '{chunk_text.strip()}'")
        # 이번에는 모든 호출이 성공합니다.
        return f"[Translated on Retry] {chunk_text}"

    # 'new_callable'을 사용하여 mock 객체를 직접 제어합니다.
    with patch.object(TranslationService, 'translate_text_with_content_safety_retry', new=mocked_translate_second_run) as mock_translator:
        # AppService를 다시 초기화하여 내부 상태를 리셋하지만, 디스크의 메타데이터는 읽어옵니다.
        app_service_resume = AppService()
        app_service_resume.load_app_config(runtime_overrides={"chunk_size": 50, "api_keys": ["dummy-key-for-init"], "max_threads": 1})
        app_service_resume.start_translation(INPUT_FILE, OUTPUT_FILE)

        print("\n--- 두 번째 실행 후 검증 ---")
        # mock_translator가 몇 번 호출되었는지 확인합니다.
        call_count = mock_translator.call_count
        print(f"번역 함수는 {call_count}번 호출되었습니다.")
        
        if call_count == 1:
            # 어떤 인자(청크 텍스트)로 호출되었는지 확인합니다.
            processed_chunk_text = mock_translator.call_args.args[0]
            if "which will fail" in processed_chunk_text:
                print("✅ 테스트 통과: 번역 함수가 이전에 실패했던 청크의 내용으로 정확히 한 번만 호출되었습니다.")
            else:
                print(f"❌ 테스트 실패: 잘못된 청크가 재시도되었습니다. 내용: '{processed_chunk_text.strip()}'")
        else:
            print(f"❌ 테스트 실패: 번역 함수가 1번 호출될 것으로 예상했지만, {call_count}번 호출되었습니다.")

    # --- 최종 확인 ---
    print("\n--- 최종 확인: 최종 출력 파일 및 메타데이터 검사 ---")
    with open(METADATA_FILE, 'r', encoding='utf-8') as f:
        final_metadata = json.load(f)
    
    final_completed = sorted(final_metadata.get("translated_chunks", []))
    print(f"최종 메타데이터의 완료된 청크: {final_completed}")
    if final_completed == [0, 1, 2]:
        print("✅ 테스트 통과: 최종 메타데이터에 모든 청크가 완료된 것으로 올바르게 기록되었습니다.")
    else:
        print(f"❌ 테스트 실패: 최종 메타데이터가 정확하지 않습니다. 예상: [0, 1, 2], 실제: {final_completed}")


if __name__ == "__main__":
    run_test()
