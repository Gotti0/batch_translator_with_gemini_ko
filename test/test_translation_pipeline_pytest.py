import pytest
from unittest.mock import MagicMock, AsyncMock
from core.dtos import TranslationJobProgressDTO
from app.app_service import AppService

@pytest.fixture
def mock_gemini_client():
    client = MagicMock()
    client.translate_async = AsyncMock(return_value="Translated text")
    return client

@pytest.fixture
def app_service(mock_gemini_client):
    service = AppService()
    service.gemini_client = mock_gemini_client
    
    # Mock core services to avoid BtgServiceException
    service.translation_service = MagicMock()
    service.chunk_service = MagicMock()
    
    # Mock translation_service.translate_async behavior
    service.translation_service.translate_text_async = AsyncMock(return_value="Translated text")
    
    # Mock chunk_service.create_chunks_from_file_content behavior
    service.chunk_service.create_chunks_from_file_content = MagicMock(return_value=["Hello world", "This is a test."])
    
    return service

@pytest.mark.asyncio
async def test_standard_translation_pipeline(app_service, tmp_path):
    """표준 번역 파이프라인(Standard) 기본 동작 검증"""
    input_file = tmp_path / "input.txt"
    input_file.write_text("Hello world\nThis is a test.")
    output_file = tmp_path / "output.txt"
    
    # Mock progress callback
    progress_calls = []
    def progress_callback(dto):
        progress_calls.append(dto)
        
    # Set translation mode in config
    app_service.config["translation_mode"] = "standard"
    
    await app_service.start_translation_async(
        input_file_path=str(input_file),
        output_file_path=str(output_file),
        progress_callback=progress_callback
    )
    
    # 결과 파일 생성 확인
    assert output_file.exists()
    # 최소 하나 이상의 진행률 콜백이 발생했는지 확인
    assert len(progress_calls) > 0
    assert progress_calls[-1].processed_chunks > 0

@pytest.mark.asyncio
async def test_translation_cancellation(app_service, tmp_path):
    """번역 작업 취소 로직 검증"""
    input_file = tmp_path / "input.txt"
    input_file.write_text("Long text... " * 100)
    output_file = tmp_path / "output.txt"
    
    # 가상의 긴 작업을 위해 translate_async가 약간 대기하도록 설정
    import asyncio
    async def slow_translate(*args, **kwargs):
        await asyncio.sleep(0.5)
        return "Translated"
    
    app_service.gemini_client.translate_async = MagicMock(side_effect=slow_translate)
    
    # 번역 시작
    app_service.config["translation_mode"] = "standard"
    task = asyncio.create_task(app_service.start_translation_async(
        input_file_path=str(input_file),
        output_file_path=str(output_file)
    ))
    
    # 잠시 후 취소
    await asyncio.sleep(0.1)
    await app_service.cancel_translation_async()
    
    try:
        await task
    except asyncio.CancelledError:
        pass # 정상
    
    assert app_service.current_translation_task is None or app_service.current_translation_task.cancelled()
