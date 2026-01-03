# -*- coding: utf-8 -*-
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 테스트 대상 모듈 임포트
from utils.request_builder import BatchRequestBuilder, create_default_request_builder

# 테스트용 설정값
TEST_MODEL_ID = "gemini-test-model"
TEST_SYSTEM_INSTRUCTION = "Test instruction"
TEST_BASE_PROMPT = [{"role": "user", "parts": [{"text": "base prompt"}]}]
TEST_GEN_CONFIG = {"temperature": 0.5}

@pytest.fixture
def builder():
    """BatchRequestBuilder의 테스트용 인스턴스를 생성하는 Fixture"""
    return BatchRequestBuilder(
        model_id=TEST_MODEL_ID,
        system_instruction=TEST_SYSTEM_INSTRUCTION,
        base_prompt=TEST_BASE_PROMPT,
        generation_config=TEST_GEN_CONFIG,
    )

@pytest.fixture
def temp_files(tmp_path):
    """테스트용 임시 소스 파일과 출력 파일 경로를 제공하는 Fixture"""
    source_file = tmp_path / "source.txt"
    output_file = tmp_path / "output.jsonl"
    return source_file, output_file

def test_request_builder_initialization(builder):
    """BatchRequestBuilder가 올바르게 초기화되는지 테스트"""
    assert builder.model_name == f"models/{TEST_MODEL_ID}"
    assert builder.system_instruction == {"parts": [{"text": TEST_SYSTEM_INSTRUCTION}]}
    assert builder.base_prompt == TEST_BASE_PROMPT
    assert builder.generation_config == TEST_GEN_CONFIG

def test_create_requests_from_file(builder, temp_files):
    """파일을 읽고 jsonl 요청을 성공적으로 생성하는지 테스트"""
    source_file, output_file = temp_files
    source_content = "첫 번째 문단입니다.\n\n두 번째 문단입니다.\n  \n세 번째 문단입니다."
    source_file.write_text(source_content, encoding="utf-8")

    request_count = builder.create_requests_from_file(str(source_file), str(output_file))

    # 1. 요청 개수가 올바른지 확인 (빈 줄은 무시되어야 함)
    assert request_count == 3

    # 2. 출력 파일이 생성되었는지 확인
    assert output_file.exists()

    # 3. 출력 파일의 내용을 검증
    lines = output_file.read_text(encoding="utf-8").strip().split('\n')
    assert len(lines) == 3

    # 첫 번째 줄 검증
    first_request_data = json.loads(lines[0])
    assert first_request_data["key"] == "paragraph_1"
    assert first_request_data["request"]["model"] == f"models/{TEST_MODEL_ID}"
    assert first_request_data["request"]["system_instruction"]["parts"][0]["text"] == TEST_SYSTEM_INSTRUCTION
    
    # base_prompt + user_prompt가 contents에 잘 들어갔는지 확인
    expected_contents = TEST_BASE_PROMPT + [{"role": "user", "parts": [{"text": "첫 번째 문단입니다."}]}]
    assert first_request_data["request"]["contents"] == expected_contents

    # 마지막 줄 검증
    last_request_data = json.loads(lines[2])
    assert last_request_data["key"] == "paragraph_3"
    assert last_request_data["request"]["contents"][-1]["parts"][0]["text"] == "세 번째 문단입니다."


def test_create_requests_from_nonexistent_file(builder, temp_files):
    """존재하지 않는 소스 파일에 대해 FileNotFoundError를 발생시키는지 테스트"""
    _, output_file = temp_files
    non_existent_source = Path("non_existent_file.txt")

    with pytest.raises(FileNotFoundError):
        builder.create_requests_from_file(str(non_existent_source), str(output_file))

def test_write_requests_to_file_structure(builder, tmp_path):
    """_write_requests_to_file 메서드가 올바른 구조의 jsonl을 생성하는지 테스트"""
    output_file = tmp_path / "output.jsonl"
    paragraphs = ["para 1", "para 2"]
    
    # _write_requests_to_file는 private이므로 직접 ��출 대신 create_requests_from_file을 통해 테스트
    source_file = tmp_path / "source.txt"
    source_file.write_text("\n".join(paragraphs), encoding="utf-8")
    builder.create_requests_from_file(str(source_file), str(output_file))

    lines = output_file.read_text(encoding="utf-8").strip().split('\n')
    for i, line in enumerate(lines):
        data = json.loads(line)
        assert "key" in data
        assert "request" in data
        assert data["key"] == f"paragraph_{i+1}"
        
        request = data["request"]
        assert "model" in request
        assert "contents" in request
        assert "system_instruction" in request
        assert "generation_config" in request
        assert request["generation_config"] == TEST_GEN_CONFIG

def test_create_default_request_builder():
    """팩토리 함수가 기본 설정으로 빌더를 생성하는지 테스트"""
    model_id = "gemini-1.5-pro"
    builder = create_default_request_builder(model_id)
    
    assert isinstance(builder, BatchRequestBuilder)
    assert builder.model_name == f"models/{model_id}"
    # 기본 시스템 지침이 잘 설정되었는지 간단히 확인
    assert "Translate" in builder.system_instruction["parts"][0]["text"]
    assert len(builder.base_prompt) > 0
    assert "temperature" in builder.generation_config
