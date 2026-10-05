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




def _interrupted_integrity_job(tmp_path, done_chunks):
    """10줄 원문을 2줄씩 5개 청크로 나누고, done_chunks만 임시 폴더에 결과가 있는 상태 (중단된 작업)."""
    import json
    source_file = tmp_path / "novel4.txt"
    source_file.write_text("\n".join(f"src{i}" for i in range(10)), encoding="utf-8")
    temp_dir = tmp_path / "novel4_translated_integrity_temp"
    temp_dir.mkdir()
    for c in done_chunks:
        data = {str(2 * c): f"T{2 * c}", str(2 * c + 1): f"T{2 * c + 1}"}
        (temp_dir / f"chunk_{c}.json").write_text(json.dumps(data), encoding="utf-8")

    mock_app_service = MagicMock()
    mock_app_service.config = {"chunk_size": 6000, "integrity_max_items": 2}
    return source_file, tmp_path / "novel4_translated.txt", IntegrityReviewProvider(mock_app_service)


def test_save_after_interruption_keeps_lines_aligned_with_source(tmp_path):
    """중간 청크가 빠진 상태에서 저장해도 뒤쪽 줄이 당겨지거나 잘리지 않는다. 빈 자리는 원문이다."""
    source_file, translated_file, provider = _interrupted_integrity_job(tmp_path, done_chunks=(0, 1, 3, 4))
    trans_chunks = provider.load_translated_chunks(str(source_file))
    assert sorted(trans_chunks) == [0, 1, 3, 4]

    trans_chunks[0] = "T0 수정\nT1"
    provider.save_translated_chunk(str(source_file), 0, trans_chunks[0], trans_chunks)

    assert translated_file.read_text(encoding="utf-8").splitlines() == [
        "T0 수정", "T1", "T2", "T3", "src4", "src5", "T6", "T7", "T8", "T9",
    ]


def test_save_after_interruption_keeps_untranslated_tail(tmp_path):
    """중단 지점까지만 번역된 상태에서 저장해도 그 뒤 원문이 사라지지 않는다."""
    source_file, translated_file, provider = _interrupted_integrity_job(tmp_path, done_chunks=(0, 1))
    trans_chunks = provider.load_translated_chunks(str(source_file))

    provider.save_translated_chunk(str(source_file), 1, trans_chunks[1], trans_chunks)

    assert translated_file.read_text(encoding="utf-8").splitlines() == (
        ["T0", "T1", "T2", "T3"] + [f"src{i}" for i in range(4, 10)]
    )


def test_generate_final_file_writes_current_chunks_after_interruption(tmp_path):
    """중단된 작업은 결과 파일이 없다. 최종 파일 생성이 경로만 돌려주지 않고 현재 청크로 파일을 쓴다."""
    source_file, translated_file, provider = _interrupted_integrity_job(tmp_path, done_chunks=(0, 2))
    assert not translated_file.exists()

    path = provider.generate_final_file(str(source_file), provider.load_translated_chunks(str(source_file)))

    assert path == str(translated_file)
    assert translated_file.read_text(encoding="utf-8").splitlines() == [
        "T0", "T1", "src2", "src3", "T4", "T5", "src6", "src7", "src8", "src9",
    ]
