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

def test_integrity_review_provider_temp_json_cache(tmp_path):
    import json
    source_file = tmp_path / "novel2.txt"
    translated_file = tmp_path / "novel2_translated.txt"
    temp_dir = tmp_path / "novel2_translated_integrity_temp"
    temp_dir.mkdir()

    source_lines = ["Line 1", "Line 2"]
    source_file.write_text("\n".join(source_lines), encoding="utf-8")
    translated_file.write_text("1줄 번역\n2줄 번역", encoding="utf-8")

    # Create chunk_0.json inside temp_dir mapping unit IDs to translated lines
    chunk_0_data = {"0": "1줄 번역 (JSON)", "1": "2줄 번역 (JSON)"}
    (temp_dir / "chunk_0.json").write_text(json.dumps(chunk_0_data, ensure_ascii=False), encoding="utf-8")

    mock_app_service = MagicMock()
    mock_app_service.config = {"chunk_size": 6000, "integrity_max_items": 100}

    provider = IntegrityReviewProvider(mock_app_service)
    trans_chunks = provider.load_translated_chunks(str(source_file))

    # Should prefer JSON cache over translated_file text
    assert len(trans_chunks) == 1
    assert trans_chunks[0] == "1줄 번역 (JSON)\n2줄 번역 (JSON)"

    # Test saving modified chunk updates both JSON cache and translated_file
    new_text = "1줄 수정\n2줄 수정"
    trans_chunks[0] = new_text
    provider.save_translated_chunk(str(source_file), 0, new_text, trans_chunks)
    
    updated_json = json.loads((temp_dir / "chunk_0.json").read_text(encoding="utf-8"))
    assert updated_json["0"] == "1줄 수정"
    assert updated_json["1"] == "2줄 수정"
    assert translated_file.read_text(encoding="utf-8") == "1줄 수정\n2줄 수정"

def test_integrity_review_provider_internal_newlines(tmp_path):
    import json
    source_file = tmp_path / "novel3.txt"
    translated_file = tmp_path / "novel3_translated.txt"
    temp_dir = tmp_path / "novel3_translated_integrity_temp"
    temp_dir.mkdir()

    source_lines = ["Line 1", "Line 2"]
    source_file.write_text("\n".join(source_lines), encoding="utf-8")

    # Unit 0 has internal newlines (\n\n)
    chunk_0_data = {"0": "1줄 번역\n\n두번째 단락", "1": "2줄 번역"}
    (temp_dir / "chunk_0.json").write_text(json.dumps(chunk_0_data, ensure_ascii=False), encoding="utf-8")

    mock_app_service = MagicMock()
    mock_app_service.config = {"chunk_size": 6000, "integrity_max_items": 100}

    provider = IntegrityReviewProvider(mock_app_service)
    trans_chunks = provider.load_translated_chunks(str(source_file))

    assert len(trans_chunks) == 1
    assert trans_chunks[0] == "1줄 번역\n\n두번째 단락\n2줄 번역"


