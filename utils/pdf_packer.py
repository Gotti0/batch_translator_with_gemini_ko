"""
utils/pdf_packer.py
Zero-dependency PDF 1.7 generator optimized for PageFold token compression.
Adapted from pagefold-0.2.5.js for Python.

Uses Adobe ToUnicode CMap to embed arbitrary Unicode text (Hangul, Hanja, CJK, Emojis, etc.)
with 100% fidelity without needing external TTF/OTF font files.
Compresses streams using Python's standard zlib (FlateDecode).
"""

import zlib
import re
from typing import Dict, List, Tuple, Optional, NamedTuple


PAGE_WIDTH = 595.28   # A4 width in points
PAGE_HEIGHT = 841.89  # A4 height in points
DEFAULT_FONT_SIZE = 1.0
DEFAULT_MARGIN = 0.0
GLYPH_WIDTH_RATIO = 0.5

PDF_NEWLINE_MARKER_DIRECTIVE = (
    "Inside the PDF text layer, every real line break is serialized as a literal \\n marker. "
    "Treat each \\n marker as one line break, and when you respond use real line breaks "
    "instead of printing the \\n marker unless you are quoting text that already contains it verbatim."
)


class PackedPdfResult(NamedTuple):
    pdf_bytes: bytes
    page_count: int
    source_characters: int
    font_size: float
    columns: int
    rows: int


def calculate_page_grid(font_size: float = DEFAULT_FONT_SIZE, margin: float = DEFAULT_MARGIN) -> Tuple[int, int]:
    """Calculate maximum columns and rows on an A4 page for given font size and margin."""
    if font_size <= 0:
        raise ValueError("font_size must be a positive number")
    if margin < 0:
        raise ValueError("margin must be non-negative")

    usable_width = PAGE_WIDTH - margin * 2
    usable_height = PAGE_HEIGHT - margin * 2

    columns = int(usable_width / (font_size * GLYPH_WIDTH_RATIO))
    rows = int(usable_height / font_size)

    if columns < 1 or rows < 1:
        raise ValueError(f"font_size ({font_size}) and margin ({margin}) leave no usable page area")

    return columns, rows


def escape_transcript_newlines(text: str) -> str:
    """Serialize all line breaks as literal '\\n' characters."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\\n")


def wrap_text(text: str, columns: int) -> List[List[str]]:
    """Wrap text to fixed columns."""
    lines: List[List[str]] = []
    # Split on any real newline, although escape_transcript_newlines removes them,
    # we still defensively handle existing newlines.
    for hard_line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        chars = list(hard_line)
        if not chars:
            lines.append([])
            continue
        for i in range(0, len(chars), columns):
            lines.append(chars[i:i + columns])
    return lines


def _hex4(val: int) -> str:
    return f"{val:04X}"


def _unicode_hex(char: str) -> str:
    """Return UTF-16 hex representation of a unicode character."""
    code_point = ord(char)
    if code_point <= 0xFFFF:
        return _hex4(code_point)
    # Supplementary plane (e.g. emojis) -> surrogate pair
    supplementary = code_point - 0x10000
    high = 0xD800 + (supplementary >> 10)
    low = 0xDC00 + (supplementary & 0x3FF)
    return f"{_hex4(high)}{_hex4(low)}"


def create_character_map(lines: List[List[str]]) -> Dict[str, int]:
    """Map every unique character in the text to a unique 16-bit CID (1..65535)."""
    char_map: Dict[str, int] = {}
    for line in lines:
        for char in line:
            if char not in char_map:
                if len(char_map) >= 65535:
                    raise ValueError("PDF can contain at most 65,535 distinct characters")
                char_map[char] = len(char_map) + 1
    return char_map


def create_to_unicode_cmap(char_map: Dict[str, int]) -> bytes:
    """
    Build Adobe ToUnicode CMap mapping CIDs to actual Unicode code points.
    This enables PDF text extraction engines (like Gemini's) to decode characters
    accurately without font files.
    """
    entries = [f"<{_hex4(cid)}><{_unicode_hex(char)}>" for char, cid in char_map.items()]
    mappings = []
    # bfchar block can have at most 100 entries per block in PDF specs
    for i in range(0, len(entries), 100):
        chunk = entries[i:i + 100]
        mappings.append(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar")

    cmap_content = "\n".join([
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /PMUnicode-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000><FFFF>",
        "endcodespacerange",
        *mappings,
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end"
    ])
    return cmap_content.encode("ascii")


def _is_rtl(char: str) -> bool:
    cp = ord(char)
    return (
        (1424 <= cp <= 2303) or
        (64285 <= cp <= 65023) or
        (65136 <= cp <= 65279) or
        (67584 <= cp <= 69631) or
        (124928 <= cp <= 126975)
    )


def visual_order(line: List[str]) -> List[str]:
    """Preserve visual ordering for combining mark clusters and RTL segments."""
    clusters: List[List[str]] = []
    # Unicode combining characters regex
    mark_pattern = re.compile(r"^[\u0300-\u036F\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]$")
    for char in line:
        if mark_pattern.match(char) and clusters:
            clusters[-1].append(char)
        else:
            clusters.append([char])

    visual: List[str] = []
    idx = 0
    while idx < len(clusters):
        first_char = clusters[idx][0]
        if not _is_rtl(first_char):
            visual.extend(clusters[idx])
            idx += 1
            continue

        end = idx + 1
        while end < len(clusters) and _is_rtl(clusters[end][0]):
            end += 1

        for cursor in range(end - 1, idx - 1, -1):
            visual.extend(clusters[cursor])
        idx = end

    return visual


def create_page_content(
    page_lines: List[List[str]],
    char_map: Dict[str, int],
    font_size: float,
    margin: float
) -> bytes:
    """Generate PDF content stream rendering lines of text using font /F0."""
    fs_str = f"{font_size:.4f}".rstrip("0").rstrip(".")
    margin_str = f"{margin:.4f}".rstrip("0").rstrip(".")
    start_y = f"{PAGE_HEIGHT - margin - font_size:.4f}".rstrip("0").rstrip(".")

    commands = [
        "BT",
        f"/F0 {fs_str} Tf",
        f"{fs_str} TL",
        f"1 0 0 1 {margin_str} {start_y} Tm"
    ]

    for i, line in enumerate(page_lines):
        ordered_chars = visual_order(line)
        encoded_hex = "".join(_hex4(char_map[c]) for c in ordered_chars if c in char_map)
        commands.append(f"<{encoded_hex}> Tj")
        if i < len(page_lines) - 1:
            commands.append("T*")

    commands.append("ET")
    return "\n".join(commands).encode("ascii")


def _stream_object(data: bytes) -> bytes:
    """Compress data using zlib.compress and format as a PDF stream object."""
    compressed = zlib.compress(data)
    header = f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode("ascii")
    footer = b"\nendstream"
    return header + compressed + footer


def serialize_pdf(objects: List[bytes]) -> bytes:
    """Serialize PDF objects into a valid PDF 1.7 binary document."""
    header = b"%PDF-1.7\n%\xFF\xFF\xFF\xFF\n"
    chunks = [header]
    offsets = [0]
    total_length = len(header)

    for i, obj in enumerate(objects):
        offsets.append(total_length)
        obj_bytes = f"{i + 1} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
        chunks.append(obj_bytes)
        total_length += len(obj_bytes)

    xref_offset = total_length
    xref_lines = [
        f"xref",
        f"0 {len(objects) + 1}",
        "0000000000 65535 f "
    ]
    for offset in offsets[1:]:
        xref_lines.append(f"{offset:010d} 00000 n ")

    trailer = [
        "trailer",
        f"<< /Size {len(objects) + 1} /Root 1 0 R >>",
        "startxref",
        str(xref_offset),
        "%%EOF"
    ]

    xref_bytes = "\n".join(xref_lines + trailer).encode("ascii")
    chunks.append(xref_bytes)
    return b"".join(chunks)


def pack_text_to_pdf(
    text: str,
    font_size: float = DEFAULT_FONT_SIZE,
    margin: float = DEFAULT_MARGIN
) -> PackedPdfResult:
    """
    Pack an arbitrary Unicode string into a high-density, zero-dependency PDF 1.7 document.
    
    Args:
        text: Input text (e.g. lorebook, glossary, or conversation logs)
        font_size: Font size in points (0.5 to 12.0, default 1.0)
        margin: Page margin in points (default 0.0)
        
    Returns:
        PackedPdfResult containing PDF bytes, page count, and grid metadata.
    """
    font_size = max(0.5, min(12.0, float(font_size)))
    margin = max(0.0, float(margin))

    columns, rows = calculate_page_grid(font_size, margin)
    escaped_text = escape_transcript_newlines(text)
    wrapped_lines = wrap_text(escaped_text, columns)

    # Split lines into pages
    pages: List[List[List[str]]] = []
    for i in range(0, len(wrapped_lines), rows):
        pages.append(wrapped_lines[i:i + rows])

    if not pages:
        pages.append([[]])

    char_map = create_character_map(wrapped_lines)

    first_page_obj = 7
    page_ids = [first_page_obj + i * 2 for i in range(len(pages))]

    kids_str = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects: List[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids_str}] /Count {len(pages)} /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] /Resources << /Font << /F0 3 0 R >> >> >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /PMUnicode /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 6 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /PMUnicode /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 5 0 R /DW 500 /CIDToGIDMap /Identity >>",
        b"<< /Type /FontDescriptor /FontName /PMUnicode /Flags 4 /FontBBox [0 -200 1000 800] /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 /MissingWidth 500 >>",
        _stream_object(create_to_unicode_cmap(char_map))
    ]

    for i, page in enumerate(pages):
        page_id = page_ids[i]
        content_id = page_id + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /Contents {content_id} 0 R >>".encode("ascii"))
        objects.append(_stream_object(create_page_content(page, char_map, font_size, margin)))

    pdf_bytes = serialize_pdf(objects)

    return PackedPdfResult(
        pdf_bytes=pdf_bytes,
        page_count=len(pages),
        source_characters=len(text or ""),
        font_size=font_size,
        columns=columns,
        rows=rows
    )


PROTECTED_RESPONSE_REGION = re.compile(r"(`{3,}[\s\S]*?`{3,}|`{3,}[\s\S]*$|`[^`\n\r]*`)")


def restore_response_newlines(text: Optional[str]) -> str:
    """
    Restore literal '\\n' sequences into actual newlines in model responses,
    while carefully preserving code blocks (```) and escaped backslashes (\\\\n).
    """
    source = str(text or "")
    if "\\" not in source:
        return source

    segments = []
    cursor = 0
    for match in PROTECTED_RESPONSE_REGION.finditer(source):
        start, end = match.span()
        if start > cursor:
            segments.append((False, source[cursor:start]))
        segments.append((True, match.group(0)))
        cursor = end

    if cursor < len(source):
        segments.append((False, source[cursor:]))

    result = []
    for is_protected, seg_text in segments:
        if is_protected:
            result.append(seg_text)
        else:
            # Replace unescaped \r\n and \n with actual newlines
            replaced = re.sub(r"(?<!\\)\\r\\n", "\n", seg_text)
            replaced = re.sub(r"(?<!\\)\\n", "\n", replaced)
            result.append(replaced)

    return "".join(result)
