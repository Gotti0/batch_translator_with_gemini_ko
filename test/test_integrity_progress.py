import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from core.dtos import TranslationJobProgressDTO
from domain.translation_service import TranslationService

@pytest.mark.asyncio
async def test_integrity_progress_callbacks():
    mock_gemini = MagicMock()
    # mock response for _translate_integrity_chunk_with_retry
    # raw_response format expected: [{"id": "0", "translated_text": "line1 translated"}, ...]
    mock_gemini.generate_text = AsyncMock(return_value=[
        {"id": "0", "translated_text": "줄1 번역"},
        {"id": "1", "translated_text": "줄2 번역"}
    ])
    
    config = {
        "chunk_size": 10,
        "integrity_max_items": 1, # force 1 line per chunk -> 2 chunks
        "model_name": "gemini-2.0-flash"
    }
    service = TranslationService(gemini_client=mock_gemini, config=config)
    
    progress_records = []
    status_records = []
    
    def on_progress(dto: TranslationJobProgressDTO):
        progress_records.append(dto)
        
    def on_status(msg: str):
        status_records.append(msg)
        
    text = "line1\nline2"
    result = await service.translate_text_integrity(
        text,
        progress_callback=on_progress,
        status_callback=on_status
    )
    
    assert "줄1 번역" in result or "line1" in result
    assert len(progress_records) >= 3  # initial + 2 chunks
    assert progress_records[0].total_chunks == 2
    assert progress_records[0].processed_chunks == 0
    assert progress_records[-1].processed_chunks == 2
    assert progress_records[-1].total_chunks == 2
    assert len(status_records) >= 2
    assert any("청크 1/2" in s for s in status_records)
    assert any("청크 2/2" in s for s in status_records)
