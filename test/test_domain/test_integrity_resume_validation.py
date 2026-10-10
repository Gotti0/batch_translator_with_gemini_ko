"""무결성 이어하기가 임시 폴더의 결과를 어떻게 이어받는지 고정한다.

- 청크 파일은 그 청크의 비공백 줄 ID를 모두 담을 때만 완료로 본다. 청크 경계가 바뀐 뒤의 옛 파일을
  번호만 보고 건너뛰면 해당 줄이 원문으로 남는다.
- 배치가 남긴 부분 결과(partial_<i>.json)는 받은 줄을 두고 빠진 줄만 다시 묻는다.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from domain.translation_service import TranslationService

TEXT = "\n".join(["a0", "a1", "", "a3", "a4", "a5"])


def _service(respond, **config):
    client = MagicMock()
    client.generate_text_async = AsyncMock(side_effect=respond)
    base = {"model_name": "gemini-test", "integrity_max_items": 3, "chunk_size": 6000, "enable_pagefold": False}
    base.update(config)
    return TranslationService(gemini_client=client, config=base)


def _ids(kwargs):
    text = kwargs["prompt"][-1].parts[0].text
    return [str(i["id"]) for i in json.loads(text[text.index("["): text.rindex("]") + 1])]


def _echo(asked):
    async def respond(**kwargs):
        ids = _ids(kwargs)
        asked.append(ids)
        return [{"id": i, "translated_text": f"R{i}"} for i in ids]
    return respond


def test_stale_chunk_file_from_other_layout_is_retranslated(tmp_path):
    out = tmp_path / "out.txt"
    temp_dir = TranslationService.integrity_temp_dir_for(out)
    temp_dir.mkdir()
    # 예전 경계(항목 2개)의 청크 1 파일: 줄 2, 3을 담는다. 지금 청크 1은 줄 3~5다.
    (temp_dir / "chunk_1.json").write_text(json.dumps({"2": "", "3": "OLD3"}), encoding="utf-8")
    (temp_dir / "chunk_0.json").write_text(json.dumps({"0": "T0", "1": "T1", "2": ""}), encoding="utf-8")

    asked = []
    service = _service(_echo(asked))
    result = asyncio.run(service.translate_text_integrity(TEXT, output_path_for_progress=out))

    assert asked == [["3", "4", "5"]]  # 청크 0은 이어받고, 줄을 다 담지 못한 청크 1은 다시 번역
    assert result.split("\n") == ["T0", "T1", "", "R3", "R4", "R5"]


def test_partial_result_asks_only_missing_lines_and_is_replaced(tmp_path):
    out = tmp_path / "out.txt"
    temp_dir = TranslationService.integrity_temp_dir_for(out)
    temp_dir.mkdir()
    (temp_dir / "chunk_0.json").write_text(json.dumps({"0": "T0", "1": "T1"}), encoding="utf-8")
    (temp_dir / "partial_1.json").write_text(json.dumps({"3": "B3", "5": "B5", "9": "다른 청크"}), encoding="utf-8")

    asked = []
    service = _service(_echo(asked))
    result = asyncio.run(service.translate_text_integrity(TEXT, output_path_for_progress=out))

    assert asked == [["4"]]
    assert result.split("\n") == ["T0", "T1", "", "B3", "R4", "B5"]
    assert not (temp_dir / "partial_1.json").exists()
    assert json.loads((temp_dir / "chunk_1.json").read_text(encoding="utf-8")) == {"3": "B3", "4": "R4", "5": "B5"}


def test_partial_missing_lines_use_targeted_retry_budget(tmp_path):
    """배치 응답을 첫 시도로 친다. 빠진 줄 재요청에서도 빠지면 더 묻지 않고 원문을 둔다(기본 예산 1)."""
    out = tmp_path / "out.txt"
    temp_dir = TranslationService.integrity_temp_dir_for(out)
    temp_dir.mkdir()
    (temp_dir / "chunk_0.json").write_text(json.dumps({"0": "T0", "1": "T1"}), encoding="utf-8")
    (temp_dir / "partial_1.json").write_text(json.dumps({"3": "B3"}), encoding="utf-8")

    asked = []

    async def drops_four(**kwargs):
        ids = _ids(kwargs)
        asked.append(ids)
        return [{"id": i, "translated_text": f"R{i}"} for i in ids if i != "4"]

    service = _service(drops_four)
    result = asyncio.run(service.translate_text_integrity(TEXT, output_path_for_progress=out))

    assert asked == [["4", "5"]]
    assert result.split("\n") == ["T0", "T1", "", "B3", "a4", "R5"]
