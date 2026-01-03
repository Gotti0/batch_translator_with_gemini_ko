import sys
import os
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

# Add project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.app_service import AppService

# --- Test Setup ---
print("--- 테스트 시작: 번역 결과가 비어있을 때의 실패 시나리오 검증 ---")
test_dir = project_root / "test" / "temp_empty_result_test"
if test_dir.exists():
    shutil.rmtree(test_dir)
test_dir.mkdir(exist_ok=True)

input_file = test_dir / "input.txt"
output_file = test_dir / "output.txt"
meta_file = test_dir / "input_metadata.json"
config_file = project_root / "config.json"

# Create a dummy input file
input_file.write_text("This is a test chunk.", encoding="utf-8")

# --- Mocking ---
# Mock the translation service to return an empty string
# The app uses content safety retry by default, so we mock that method.
mock_translation_service = MagicMock()
mock_translation_service.translate_text_with_content_safety_retry.return_value = ""
mock_translation_service.translate_text.return_value = "" # Just in case

# --- Test Execution ---
try:
    print("\n1. AppService 인스턴스 생성 및 빈 결과 반환 시뮬레이션 시작...")
    
    app_service = AppService(config_file)
    app_service.translation_service = mock_translation_service

    # Run translation
    app_service.start_translation(str(input_file), str(output_file))

    print("  - 번역 작업 (빈 결과 반환) 완료.")

    # --- Verification ---
    print("\n2. 메타데이터 파일 검증...")
    assert meta_file.exists(), f"❌ 실패: 메타데이터 파일이 생성되지 않았습니다: {meta_file}"
    
    with open(meta_file, 'r', encoding='utf-8') as f:
        metadata = json.load(f)
    
    print(f"  - 메타데이터 파일 로드 완료: {metadata}")

    translated_chunks = metadata.get("translated_chunks", [])
    assert not translated_chunks, f"❌ 실패: 'translated_chunks'가 비어있지 않습니다: {translated_chunks}"
    print("  - ✅ 성공: 'translated_chunks' 목록이 비어 있습니다.")

    total_chunks = metadata.get("total_chunks")
    assert total_chunks == 1, f"❌ 실패: 'total_chunks'가 1이 아닙니다: {total_chunks}"
    print(f"  - ✅ 성공: 'total_chunks'가 {total_chunks}입니다.")

    last_error = metadata.get("last_error")
    assert last_error is not None, "❌ 실패: 'last_error'가 기록되지 않았습니다."
    assert "비어있습니다" in last_error, f"❌ 실패: 'last_error' 메시지가 예상과 다릅니다: {last_error}"
    print(f"  - ✅ 성공: 'last_error'가 정확하게 기록되었습니다: '{last_error}'")

    print("\n✅ 테스트 성공: 번역 결과가 비어있을 때 메타데이터가 정확하게 처리되었습니다.")

except Exception as e:
    print(f"\n테스트 실행 중 예기치 않은 오류 발생: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
finally:
    # --- Cleanup ---
    print("\n3. 테스트 환경 정리...")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    print("  - 임시 디렉토리 및 파일 삭제 완료.")
    print("\n--- 테스트 종료 ---")
