import pytest
import asyncio
import os
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from app.app_service import AppService
from core.dtos import TranslationJobProgressDTO, TranslationUnit, TranslatedUnit

@pytest.fixture
def mock_gemini_client():
    client = MagicMock()
    
    # Default behavior for generate_text_async
    async def side_effect(prompt, **kwargs):
        # Determine response based on prompt content or mode
        prompt_str = ""
        if isinstance(prompt, str):
            prompt_str = prompt
        elif isinstance(prompt, list):
            # Extract text from contents
            for content in prompt:
                if hasattr(content, 'parts'):
                    for part in content.parts:
                        if hasattr(part, 'text'):
                            prompt_str += part.text
        
        if "JSON array" in str(kwargs.get("system_instruction_text", "")) or "application/json" in str(kwargs.get("generation_config_dict", {})):
            # Integrity/EPUB mode JSON response
            # Try to parse the input JSON from prompt to maintain IDs
            try:
                # Simple extraction of JSON from prompt
                json_start = prompt_str.find("[")
                json_end = prompt_str.rfind("]") + 1
                input_json = json.loads(prompt_str[json_start:json_end])
                return [{"id": item["id"], "translated_text": f"번역됨: {item['text']}"} for item in input_json]
            except:
                return [{"id": "0", "translated_text": "번역됨"}]
        else:
            # Standard mode plain text response
            return "Translated: " + prompt_str[:50]
            
    client.generate_text_async = AsyncMock(side_effect=side_effect)
    return client

@pytest.fixture
def app_service(mock_gemini_client, tmp_path):
    # Create a dummy config file
    config_path = tmp_path / "config.json"
    config_data = {
        "api_keys": ["fake-key"],
        "model_name": "gemini-2.0-flash",
        "chunk_size": 1000,
        "translation_mode": "standard"
    }
    config_path.write_text(json.dumps(config_data))
    
    service = AppService(config_file_path=str(config_path))
    # Inject mock client
    service.gemini_client = mock_gemini_client
    # The initialization in AppService might overwrite translation_service, 
    # so we need to ensure it uses our mock client.
    if service.translation_service:
        service.translation_service.gemini_client = mock_gemini_client
        
    return service


@pytest.fixture
def text_input(tmp_path):
    """입력 파일은 테스트마다 새로 만든다.

    이전 판은 저장소 루트의 `sample_input.txt`를 쓰고 지우지 않아, 남은 메타데이터 때문에
    두 번째 실행부터 이어하기로 들어가 출력 파일을 만들지 않았다. 저장소도 더럽혔다.
    """
    path = tmp_path / "sample_input.txt"
    path.write_text("Line 1\nLine 2", encoding="utf-8")
    return path


@pytest.fixture
def epub_input(tmp_path):
    """챕터 하나짜리 최소 EPUB.

    이전 판은 없는 `scripts.generate_sample_epub`을 불러 항상 실패했다. 파이프라인은
    mimetype과 .xhtml 항목만 보므로 OPF나 container.xml 없이도 검증할 수 있다.
    """
    path = tmp_path / "sample_input.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "OEBPS/chapter1.xhtml",
            "<?xml version='1.0' encoding='utf-8'?>"
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            "<p>First paragraph.</p><p>Second paragraph.</p>"
            "</body></html>",
        )
    return path


@pytest.mark.asyncio
async def test_standard_pipeline_integration(app_service, text_input, tmp_path):
    """표준(Standard) 파이프라인 종합 테스트"""
    output_file = tmp_path / "sample_output_standard.txt"
    app_service.config["translation_mode"] = "standard"

    await app_service.start_translation_async(
        input_file_path=str(text_input),
        output_file_path=str(output_file),
    )

    assert output_file.exists()
    assert "Translated:" in output_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_integrity_pipeline_integration(app_service, text_input, tmp_path):
    """무결성(Integrity) 파이프라인 종합 테스트"""
    output_file = tmp_path / "sample_output_integrity.txt"
    app_service.config["translation_mode"] = "integrity"

    await app_service.start_translation_async(
        input_file_path=str(text_input),
        output_file_path=str(output_file),
    )

    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    # 무결성 모드에서는 줄 수가 유지되어야 한다.
    assert len(text_input.read_text(encoding="utf-8").splitlines()) == len(content.splitlines())
    assert "번역됨:" in content


@pytest.mark.asyncio
async def test_epub_pipeline_integration(app_service, epub_input, tmp_path):
    """EPUB 파이프라인 종합 테스트"""
    output_file = tmp_path / "sample_output.epub"
    app_service.config["translation_mode"] = "epub"

    await app_service.start_translation_async(
        input_file_path=str(epub_input),
        output_file_path=str(output_file),
    )

    assert output_file.exists()
    with zipfile.ZipFile(output_file, "r") as z:
        assert "mimetype" in z.namelist()
        assert "OEBPS/chapter1.xhtml" in z.namelist()
        assert "번역됨:" in z.read("OEBPS/chapter1.xhtml").decode("utf-8")
