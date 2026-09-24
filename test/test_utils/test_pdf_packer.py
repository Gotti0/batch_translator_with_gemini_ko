"""
test/test_utils/test_pdf_packer.py
Unit tests for utils/pdf_packer.py
"""

import pytest
from utils.pdf_packer import (
    calculate_page_grid,
    escape_transcript_newlines,
    wrap_text,
    create_character_map,
    create_to_unicode_cmap,
    pack_text_to_pdf,
    restore_response_newlines,
    visual_order,
    PAGE_WIDTH,
    PAGE_HEIGHT,
)


def test_calculate_page_grid():
    cols, rows = calculate_page_grid(font_size=1.0, margin=0.0)
    assert cols > 1000
    assert rows > 800

    cols_m, rows_m = calculate_page_grid(font_size=2.0, margin=10.0)
    assert cols_m < cols
    assert rows_m < rows

    with pytest.raises(ValueError):
        calculate_page_grid(font_size=-1.0)

    with pytest.raises(ValueError):
        calculate_page_grid(font_size=1.0, margin=1000.0)


def test_escape_transcript_newlines():
    text = "Hello\r\nWorld\rAgain\nLast"
    escaped = escape_transcript_newlines(text)
    assert escaped == "Hello\\nWorld\\nAgain\\nLast"


def test_wrap_text():
    wrapped = wrap_text("1234567890", columns=4)
    assert wrapped == [["1", "2", "3", "4"], ["5", "6", "7", "8"], ["9", "0"]]


def test_character_map_and_cmap():
    lines = [
        list("안녕하세요"),
        list("日本語テスト"),
        list("Hello World! 123"),
        list("특수문자: 🔥🎉✨")
    ]
    char_map = create_character_map(lines)
    assert "안" in char_map
    assert "日" in char_map
    assert "🔥" in char_map
    assert len(char_map) > 20

    cmap_bytes = create_to_unicode_cmap(char_map)
    assert b"/CIDInit /ProcSet findresource" in cmap_bytes
    assert b"begincmap" in cmap_bytes
    assert b"endcmap" in cmap_bytes
    assert b"beginbfchar" in cmap_bytes


def test_visual_order_rtl_and_marks():
    # Combining mark
    line = ["e", "\u0301"]  # e + acute accent
    ordered = visual_order(line)
    assert ordered == ["e", "\u0301"]

    # Simple ASCII
    ascii_line = list("Hello")
    assert visual_order(ascii_line) == ascii_line


def test_pack_text_to_pdf_single_page():
    sample_text = (
        "=== GLOSSARY CONTEXT ===\n"
        "- 카라스마 -> 烏丸 (ko) (등장: 42회)\n"
        "- 엘리시아 -> Elysia (ko) (등장: 15회)\n"
        "이 텍스트는 2pt 고밀도 PDF로 압축되어 Gemini에 전송됩니다."
    )
    result = pack_text_to_pdf(sample_text, font_size=2.0)

    assert result.page_count == 1
    assert result.source_characters == len(sample_text)
    assert result.pdf_bytes.startswith(b"%PDF-1.7")
    assert result.pdf_bytes.strip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in result.pdf_bytes
    assert b"/Type /Pages" in result.pdf_bytes
    assert b"/ToUnicode" in result.pdf_bytes
    assert b"/FlateDecode" in result.pdf_bytes


def test_pack_text_to_pdf_multipage():
    # Generate large repetitive text that forces page break
    large_text = "\n".join([f"Line {i}: 용어집 엔트리 항목 번호 {i} -> 번역어_{i}" for i in range(2000)])
    result = pack_text_to_pdf(large_text, font_size=4.0)

    assert result.page_count > 1
    assert result.pdf_bytes.startswith(b"%PDF-1.7")
    assert result.pdf_bytes.strip().endswith(b"%%EOF")


def test_restore_response_newlines():
    # Regular text with literal \n
    raw_response = "첫 번째 줄입니다.\\n두 번째 줄입니다.\\n세 번째 줄입니다."
    restored = restore_response_newlines(raw_response)
    assert restored == "첫 번째 줄입니다.\n두 번째 줄입니다.\n세 번째 줄입니다."

    # Code block preservation: ```python\ncode\n```
    code_response = "앞 문장\\n```python\nprint('hello\\nworld')\n```\\n뒷 문장"
    restored_code = restore_response_newlines(code_response)
    assert "앞 문장\n```python\nprint('hello\\nworld')\n```\n뒷 문장" == restored_code

    # Escaped backslash preservation: \\n -> \\n
    escaped_response = "경로: C:\\\\Users\\\\name"
    assert restore_response_newlines(escaped_response) == escaped_response
