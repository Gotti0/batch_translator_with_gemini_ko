import pytest
import asyncio
import os
import json
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

@pytest.mark.asyncio
async def test_standard_pipeline_integration(app_service, tmp_path):
    """표준(Standard) 파이프라인 종합 테스트"""
    input_file = Path("sample_input.txt")
    if not input_file.exists():
        input_file.write_text("Line 1\nLine 2")
        
    output_file = tmp_path / "sample_output_standard.txt"
    
    app_service.config["translation_mode"] = "standard"
    
    await app_service.start_translation_async(
        input_file_path=str(input_file),
        output_file_path=str(output_file)
    )
    
    assert output_file.exists()
    content = output_file.read_text(encoding='utf-8')
    assert "Translated:" in content
    print("\n✅ Standard Pipeline Test Passed")

@pytest.mark.asyncio
async def test_integrity_pipeline_integration(app_service, tmp_path):
    """무결성(Integrity) 파이프라인 종합 테스트"""
    input_file = Path("sample_input.txt")
    output_file = tmp_path / "sample_output_integrity.txt"
    
    app_service.config["translation_mode"] = "integrity"
    
    await app_service.start_translation_async(
        input_file_path=str(input_file),
        output_file_path=str(output_file)
    )
    
    assert output_file.exists()
    content = output_file.read_text(encoding='utf-8')
    # 무결성 모드에서는 줄 수가 유지되어야 함
    original_lines = input_file.read_text(encoding='utf-8').splitlines()
    output_lines = content.splitlines()
    assert len(original_lines) == len(output_lines)
    assert "번역됨:" in content
    print("✅ Integrity Pipeline Test Passed")

@pytest.mark.asyncio
async def test_epub_pipeline_integration(app_service, tmp_path):
    """EPUB 파이프라인 종합 테스트"""
    input_file = Path("sample_input.epub")
    # EPUB 파일이 없으면 생성 스크립트 실행 (이미 실행했으므로 존재할 것)
    if not input_file.exists():
         from scripts.generate_sample_epub import create_sample_epub
         create_sample_epub(str(input_file))
         
    output_file = tmp_path / "sample_output.epub"
    
    app_service.config["translation_mode"] = "epub"
    
    await app_service.start_translation_async(
        input_file_path=str(input_file),
        output_file_path=str(output_file)
    )
    
    assert output_file.exists()
    # Zip 구조 확인
    import zipfile
    with zipfile.ZipFile(output_file, 'r') as z:
        assert "mimetype" in z.namelist()
        assert "OEBPS/chapter1.xhtml" in z.namelist()
        # 내용에 번역된 텍스트가 포함되어 있는지 확인
        chapter_content = z.read("OEBPS/chapter1.xhtml").decode('utf-8')
        assert "번역됨:" in chapter_content
    print("✅ EPUB Pipeline Test Passed")
