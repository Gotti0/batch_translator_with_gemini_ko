import pytest
from pathlib import Path
from unittest.mock import MagicMock
from infrastructure.file_handler import save_metadata, load_metadata
from domain.review_providers.factory import get_review_provider
from domain.review_providers.integrity_provider import IntegrityReviewProvider

def test_integrity_review_provider_compatibility(tmp_path):
    # Setup sample source file and translated file
    source_file = tmp_path / "sample_novel.txt"
    translated_file = tmp_path / "sample_novel_translated.txt"
    
    source_lines = ["Line 1 text", "Line 2 text", "Line 3 text"]
    translated_lines = ["1줄 번역", "2줄 번역", "3줄 번역"]
    
    source_file.write_text("\n".join(source_lines), encoding="utf-8")
    translated_file.write_text("\n".join(translated_lines), encoding="utf-8")
    
    # Save integrity metadata (as produced by translate_text_integrity)
    final_metadata = {
        "pipeline_type": "integrity",
        "total_chunks": 1,
        "translated_chunks": {"0": {"status": "success"}},
        "failed_chunks": {},
    }
    save_metadata(translated_file, final_metadata)
    save_metadata(source_file, final_metadata)
    
    mock_app_service = MagicMock()
    mock_app_service.config = {"chunk_size": 6000, "integrity_max_items": 100}
    
    # 1. Test load_metadata fallback for both paths
    meta_from_source = load_metadata(source_file)
    meta_from_trans = load_metadata(translated_file)
    
    assert meta_from_source.get("pipeline_type") == "integrity"
    assert meta_from_trans.get("pipeline_type") == "integrity"
    
    # 2. Test get_review_provider returns IntegrityReviewProvider for both paths
    provider_source = get_review_provider(str(source_file), mock_app_service)
    provider_trans = get_review_provider(str(translated_file), mock_app_service)
    
    assert isinstance(provider_source, IntegrityReviewProvider)
    assert isinstance(provider_trans, IntegrityReviewProvider)
    
    # 3. Test load_source_chunks and load_translated_chunks for both paths
    source_chunks = provider_source.load_source_chunks(str(source_file))
    trans_chunks = provider_source.load_translated_chunks(str(source_file))
    
    assert len(source_chunks) == 1
    assert "Line 1 text" in source_chunks[0]
    assert len(trans_chunks) == 1
    assert "1줄 번역" in trans_chunks[0]
    
    # Check loading when passed translated_file path directly
    source_chunks_from_trans_path = provider_trans.load_source_chunks(str(translated_file))
    trans_chunks_from_trans_path = provider_trans.load_translated_chunks(str(translated_file))
    
    assert len(source_chunks_from_trans_path) == 1
    assert "Line 1 text" in source_chunks_from_trans_path[0]
    assert len(trans_chunks_from_trans_path) == 1
    assert "1줄 번역" in trans_chunks_from_trans_path[0]
