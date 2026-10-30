# test/test_utils/test_epub_xhtml_standards.py
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
import pytest

from core.dtos import NodeType
from utils.epub_processor import EpubProcessor


def test_process_and_reconstruct_preserves_xml_and_doctype():
    raw_html = b"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" class="vrtl" xml:lang="ja">
<head>
    <title>\xe7\xac\xac\xe4\xb8\x80\xe7\xab\xa0</title>
    <link rel="stylesheet" href="../Styles/style.css" type="text/css"/>
</head>
<body class="p-text">
    <p class="calibre1">\xe3\x81\x93\xe3\x82\x93\xe3\x81\xab\xe3\x81\xa1\xe3\x81\xaf</p>
</body>
</html>"""

    processor = EpubProcessor()
    chapter = processor.process_chapter(raw_html, "Text/ch1.xhtml")

    assert chapter.xml_declaration == '<?xml version="1.0" encoding="utf-8"?>'
    assert chapter.doctype == '<!DOCTYPE html>'
    # 파싱 단계는 원본 세로쓰기 표식을 유지한다
    assert chapter.html_attributes.get("class") == "vrtl"
    assert chapter.html_attributes.get("xml:lang") == "ja"
    assert chapter.html_attributes.get("xmlns") == "http://www.w3.org/1999/xhtml"
    assert chapter.body_attributes.get("class") == "p-text"

    # 번역 맵 매핑
    translated_map = {
        "Text/ch1.xhtml_title": "제1장",
        "Text/ch1.xhtml_0": "안녕하세요",
    }
    reconstructed = processor.reconstruct_chapter(chapter, translated_map)

    # 1. 선언부 및 태그 확인 — 재조립 시 가로쓰기(hltr)로 정규화
    assert reconstructed.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert '<!DOCTYPE html>' in reconstructed
    assert '<html xmlns="http://www.w3.org/1999/xhtml"' in reconstructed
    assert 'class="hltr"' in reconstructed
    assert 'class="vrtl"' not in reconstructed
    assert '<body class="p-text">' in reconstructed
    assert "writing-mode:horizontal-tb!important" in reconstructed
    assert '<title>제1장</title>' in reconstructed
    assert '<p class="calibre1">안녕하세요</p>' in reconstructed

    # 2. 엄격한 XML 파서 파싱 검증 (XHTML 유효성 검증)
    root = ET.fromstring(reconstructed)
    assert root.tag.endswith("html")


def test_normalize_css_for_horizontal_layout_rewrites_vertical_rules():
    css = """
    .vrtl { writing-mode: vertical-rl; -epub-writing-mode: vertical-rl; }
    .vrtl img { max-width: 100%; }
    body { -webkit-writing-mode: vertical-lr; }
    .cover-vrtl-badge { color: red; }
    """
    normalized = EpubProcessor.normalize_css_for_horizontal_layout(css)

    assert ".hltr {" in normalized
    assert ".hltr img" in normalized
    assert "writing-mode: horizontal-tb" in normalized
    assert "-epub-writing-mode: horizontal-tb" in normalized
    assert "-webkit-writing-mode: horizontal-tb" in normalized
    assert "vertical-rl" not in normalized
    assert "vertical-lr" not in normalized
    # 클래스명 일부가 우연히 겹치는 토큰은 건드리지 않는다
    assert ".cover-vrtl-badge" in normalized


def test_reconstruct_converts_body_vrtl_and_rtl_dir():
    raw_html = b"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" dir="rtl">
<head><title>t</title></head>
<body class="vrtl p-text" style="writing-mode: vertical-rl; color: #111;">
<p>a</p>
</body>
</html>"""
    processor = EpubProcessor()
    chapter = processor.process_chapter(raw_html, "c.xhtml")
    reconstructed = processor.reconstruct_chapter(chapter, {"c.xhtml_0": "가"})

    assert 'dir="ltr"' in reconstructed
    assert 'class="hltr p-text"' in reconstructed
    soup = BeautifulSoup(reconstructed, "lxml")
    assert "vertical-rl" not in (soup.body.get("style") or "")
    assert "horizontal-tb" in (soup.body.get("style") or "")
    assert "color: #111" in (soup.body.get("style") or "")


def test_process_and_reconstruct_preserves_epub2_doctype():
    raw_html = b"""<?xml version="1.0" encoding="utf-8" standalone="no"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"
  "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body class="hltr"><p>Hello</p></body>
</html>"""

    processor = EpubProcessor()
    chapter = processor.process_chapter(raw_html, "chapter1.xhtml")

    assert chapter.xml_declaration == '<?xml version="1.0" encoding="utf-8" standalone="no"?>'
    assert "DTD XHTML 1.1" in chapter.doctype
    assert chapter.body_attributes.get("class") == "hltr"

    reconstructed = processor.reconstruct_chapter(chapter, {})
    assert 'standalone="no"' in reconstructed
    assert "DTD XHTML 1.1" in reconstructed
    assert '<body class="hltr">' in reconstructed


def test_missing_declarations_defaults_to_valid_xhtml():
    # 선언부가 아예 없는 불완전한 HTML 입력
    raw_html = b"""<html><head><title>Title</title></head><body><p>Text</p></body></html>"""

    processor = EpubProcessor()
    chapter = processor.process_chapter(raw_html, "test.html")

    assert chapter.xml_declaration == ""
    assert chapter.doctype == ""

    reconstructed = processor.reconstruct_chapter(chapter, {})

    # 표준 XML 선언, DOCTYPE 및 xmlns 자동 보충 확인
    assert reconstructed.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert '<!DOCTYPE html>' in reconstructed
    assert 'xmlns="http://www.w3.org/1999/xhtml"' in reconstructed

    root = ET.fromstring(reconstructed)
    assert root.tag.endswith("html")


def test_xml_entity_escaping_for_special_characters():
    raw_html = b"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body><p>placeholder</p></body></html>"""

    processor = EpubProcessor()
    chapter = processor.process_chapter(raw_html, "ch.xhtml")

    # 번역 텍스트에 XML 특수문자(<, >, &)가 포함된 경우
    translated_map = {
        "ch.xhtml_0": "Tom & Jerry <비밀의 숲>",
    }
    reconstructed = processor.reconstruct_chapter(chapter, translated_map)

    # 본문에 raw '<' 또는 '&'가 그대로 노출되어 XML 파서를 파괴하지 않는지 검증
    assert "Tom &amp; Jerry &lt;비밀의 숲&gt;" in reconstructed

    # 엄격한 XML 파서 파싱 검증
    root = ET.fromstring(reconstructed)
    # XML 파서 디코딩 시 원문 텍스트 복원 확인
    p_text = "".join(root.itertext()).strip()
    assert "Tom & Jerry <비밀의 숲>" in p_text


def test_title_fallback_preserves_original_when_untranslated():
    raw_html = b"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title class="header-title">Original Title</title></head>
<body><p>Content</p></body>
</html>"""

    processor = EpubProcessor()
    chapter = processor.process_chapter(raw_html, "ch.xhtml")

    # 번역 맵에 title 번역이 없는 경우
    reconstructed = processor.reconstruct_chapter(chapter, {})

    assert '<title class="header-title">Original Title</title>' in reconstructed
