# utils/epub_processor.py
import copy
import html
import re
from typing import List, Dict, Optional, Set, Tuple, Any, Union
from bs4 import BeautifulSoup, Tag, NavigableString
from core.dtos import EpubNode, EpubChapter, NodeType
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

IMAGE_TAGS = {'img', 'svg', 'image'}
ATOMIC_TAGS = {'hr', 'br'}
# 구조 유지가 필수적인 태그들
STRUCTURAL_TAGS = {
    'nav', 'ol', 'ul', 'li', 'table', 'tr', 'td', 'th', 
    'thead', 'tbody', 'dl', 'dt', 'dd', 'blockquote'
}
# 내부를 쪼개서 들어가야 하는 복합 태그들
COMPLEX_TAGS = STRUCTURAL_TAGS | {
    'p', 'div', 'section', 'article', 'aside', 
    'header', 'footer', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'
}

# Calibre/Kobo 관례: 세로쓰기 클래스 → 가로쓰기. 셀렉터(.vrtl img)도 같이 바꿔 이미지 CSS를 유지한다.
_VERTICAL_LAYOUT_CLASSES = {"vrtl": "hltr", "vltr": "hltr"}
_WRITING_MODE_VALUE_RE = re.compile(
    r"((?:-webkit-|-epub-|-moz-)?writing-mode)\s*:\s*(vertical-rl|vertical-lr)\b",
    re.IGNORECASE,
)
_VERTICAL_CLASS_TOKEN_RE = re.compile(r"(?<![\w-])(vrtl|vltr)(?![\w-])", re.IGNORECASE)
_HORIZONTAL_LAYOUT_STYLE = (
    '<style type="text/css">'
    "/* Neo_Batch_Translator: 번역본은 가로쓰기 */"
    "html,body{writing-mode:horizontal-tb!important;"
    "-webkit-writing-mode:horizontal-tb!important;"
    "-epub-writing-mode:horizontal-tb!important;"
    "direction:ltr!important;}"
    "</style>"
)

class EpubProcessor:
    def __init__(self):
        self.node_index = 0
        self.current_file_name = ""

    def process_chapter(self, html_content: Union[bytes, str], file_name: str) -> EpubChapter:
        """
        HTML 콘텐츠를 파싱하여 EpubNode 리스트 및 메타데이터(선언부, 태그 속성 등)로 변환합니다.
        """
        self.node_index = 0
        self.current_file_name = file_name

        # 선언부 추출 (XML Declaration & DOCTYPE)
        if isinstance(html_content, bytes):
            prefix_str = html_content[:2048].decode('utf-8', errors='ignore').lstrip('\ufeff \t\r\n')
        else:
            prefix_str = str(html_content)[:2048].lstrip('\ufeff \t\r\n')

        xml_declaration = ""
        xml_m = re.match(r'<\?xml[^>]*\?>', prefix_str, re.IGNORECASE)
        if xml_m:
            xml_declaration = xml_m.group(0).strip()

        doctype = ""
        doc_m = re.search(r'<!DOCTYPE[^>]*>', prefix_str, re.IGNORECASE | re.DOTALL)
        if doc_m:
            doctype = doc_m.group(0).strip()
        
        soup = BeautifulSoup(html_content, 'lxml')
        nodes: List[EpubNode] = []

        # 1. Head 처리
        head_html = ""
        if soup.head:
            # Title 태그 별도 추출
            title_tag = soup.head.find('title')
            if title_tag:
                normalized_attrs = {k: " ".join(v) if isinstance(v, list) else str(v) for k, v in title_tag.attrs.items()}
                nodes.append(EpubNode(
                    id=f"{file_name}_title",
                    type=NodeType.TEXT,
                    tag="title",
                    content=title_tag.get_text(),
                    attributes=normalized_attrs
                ))
                title_tag.decompose()
            head_html = "".join([str(x) for x in soup.head.contents])

        # 2. Body 순회
        if soup.body:
            self._traverse(soup.body, nodes)

        # 3. HTML / Body 속성 보존 (스타일시트 셀렉터 및 뷰어 네임스페이스 무결성)
        html_attrs = {}
        if soup.html and soup.html.attrs:
            html_attrs = {k: " ".join(v) if isinstance(v, list) else str(v) for k, v in soup.html.attrs.items()}

        body_attrs = {}
        if soup.body and soup.body.attrs:
            body_attrs = {k: " ".join(v) if isinstance(v, list) else str(v) for k, v in soup.body.attrs.items()}

        return EpubChapter(
            file_name=file_name,
            nodes=nodes,
            head_html=head_html,
            xml_declaration=xml_declaration,
            doctype=doctype,
            html_attributes=html_attrs,
            body_attributes=body_attrs
        )

    def _traverse(self, element: Tag, nodes: List[EpubNode]):
        """
        태그 트리를 재귀적으로 순회하며 평탄화된 노드 리스트를 생성합니다.
        """
        for child in element.children:
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    # 부모가 TEXT 타입으로 처리되지 않는 독립적인 텍스트 노드 처리
                    # 복합 태그 내부의 텍스트가 유실되지 않도록 TEXT 노드로 추가
                    deterministic_id = f"{self.current_file_name}_{self.node_index}"
                    self.node_index += 1
                    nodes.append(EpubNode(
                        id=deterministic_id,
                        type=NodeType.TEXT,
                        tag="",
                        content=text,
                        attributes={}
                    ))
                continue
            
            tag_name = child.name.lower()
            deterministic_id = f"{self.current_file_name}_{self.node_index}"
            self.node_index += 1

            # Case 1: 이미지 태그
            if tag_name in IMAGE_TAGS:
                img_src = child.get('src') or child.get('href')
                nodes.append(EpubNode(
                    id=deterministic_id,
                    type=NodeType.IMAGE,
                    tag=tag_name,
                    html=str(child),
                    image_path=img_src
                ))
                continue

            # Case 2: 원자적 태그 (hr, br)
            if tag_name in ATOMIC_TAGS:
                nodes.append(EpubNode(
                    id=deterministic_id, 
                    type=NodeType.IGNORED, 
                    tag=tag_name, 
                    html=str(child)
                ))
                continue

            # Case 3: 동적 컨테이너 판별
            # 자식 중에 이미지, 원자적 태그, 또는 다른 복합 태그가 있는지 확인
            has_complex_content = child.find(list(COMPLEX_TAGS | IMAGE_TAGS | ATOMIC_TAGS)) is not None
            is_structural = tag_name in STRUCTURAL_TAGS

            if is_structural or has_complex_content:
                # [Opening Tag]
                opening_html = self._reconstruct_opening_tag(child)
                
                nodes.append(EpubNode(
                    id=deterministic_id,
                    type=NodeType.IGNORED,
                    tag=tag_name,
                    html=opening_html
                ))

                # 재귀 호출
                self._traverse(child, nodes)

                # [Closing Tag]
                nodes.append(EpubNode(
                    id=f"{self.current_file_name}_{self.node_index}",
                    type=NodeType.IGNORED,
                    tag=tag_name,
                    html=f"</{tag_name}>"
                ))
                self.node_index += 1
            
            else:
                # Case 4: 말단 텍스트 블록 (번역 대상)
                pure_text = self._extract_pure_text(child)
                if pure_text:
                    normalized_attrs = {k: " ".join(v) if isinstance(v, list) else str(v) for k, v in child.attrs.items()}
                    nodes.append(EpubNode(
                        id=deterministic_id,
                        type=NodeType.TEXT,
                        tag=tag_name,
                        content=pure_text,
                        attributes=normalized_attrs
                    ))
                else:
                    # 텍스트는 없지만 빈 태그인 경우 보존
                    nodes.append(EpubNode(
                        id=deterministic_id,
                        type=NodeType.IGNORED,
                        tag=tag_name,
                        html=str(child)
                    ))

    def _extract_pure_text(self, element: Tag) -> str:
        """
        태그에서 루비 문자(rt, rp) 등을 제거하고 순수 텍스트만 추출합니다.
        """
        clone = copy.copy(element)
        for rt in clone.find_all(['rt', 'rp']):
            rt.decompose()
        return clone.get_text().strip()

    def _dict_to_attr_str(self, attributes: Dict[str, Any]) -> str:
        """
        딕셔너리 속성을 HTML/XML 속성 문자열로 변환합니다.
        """
        if not attributes:
            return ""
        attrs = []
        for k, v in attributes.items():
            val = v if isinstance(v, str) else " ".join(str(item) for item in v)
            safe_val = str(val).replace('"', '&quot;')
            attrs.append(f'{k}="{safe_val}"')
        return " " + " ".join(attrs) if attrs else ""

    @staticmethod
    def _normalize_layout_class_value(class_value: str) -> str:
        tokens = class_value.split()
        normalized = []
        for token in tokens:
            replacement = _VERTICAL_LAYOUT_CLASSES.get(token.lower())
            if replacement is None:
                normalized.append(token)
            elif replacement not in normalized:
                normalized.append(replacement)
        return " ".join(normalized)

    @classmethod
    def _normalize_horizontal_layout_attrs(cls, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """번역 결과물은 가로쓰기(ltr)로 맞춘다. 원본 파싱 속성은 그대로 두고 재조립 시에만 적용."""
        if not attributes:
            return {}
        attrs = dict(attributes)
        if "class" in attrs and attrs["class"]:
            attrs["class"] = cls._normalize_layout_class_value(str(attrs["class"]))
        if str(attrs.get("dir", "")).lower() == "rtl":
            attrs["dir"] = "ltr"
        style = attrs.get("style")
        if style:
            attrs["style"] = _WRITING_MODE_VALUE_RE.sub(
                lambda m: f"{m.group(1)}: horizontal-tb",
                str(style),
            )
        return attrs

    @staticmethod
    def normalize_css_for_horizontal_layout(css_text: str) -> str:
        """
        번들 CSS의 세로쓰기 writing-mode와 Calibre 세로 클래스를 가로쓰기로 바꾼다.
        .vrtl → .hltr 치환으로 이미지 크기 셀렉터 매칭을 유지한다.
        """
        css_text = _WRITING_MODE_VALUE_RE.sub(
            lambda m: f"{m.group(1)}: horizontal-tb",
            css_text,
        )

        def _replace_class_token(match: re.Match) -> str:
            return _VERTICAL_LAYOUT_CLASSES.get(match.group(1).lower(), match.group(1))

        return _VERTICAL_CLASS_TOKEN_RE.sub(_replace_class_token, css_text)

    def _reconstruct_opening_tag(self, element: Tag) -> str:
        """
        BS4 Tag 객체에서 자식을 제외한 오프닝 태그 문자열만 생성합니다.
        """
        attr_str = self._dict_to_attr_str(element.attrs)
        return f"<{element.name}{attr_str}>"

    def reconstruct_chapter(self, chapter: EpubChapter, translated_map: Dict[str, str]) -> str:
        """
        번역된 텍스트 맵을 사용하여 EpubChapter를 유효한 XHTML 문자열로 다시 조립합니다.
        """
        html_parts = []
        
        # 1. XML 선언 및 DOCTYPE 복구
        if chapter.xml_declaration:
            html_parts.append(f"{chapter.xml_declaration}\n")
        else:
            html_parts.append('<?xml version="1.0" encoding="utf-8"?>\n')

        if chapter.doctype:
            html_parts.append(f"{chapter.doctype}\n")
        else:
            html_parts.append('<!DOCTYPE html>\n')

        # 2. HTML 루트 태그 (네임스페이스 복원 + 세로쓰기 클래스를 가로쓰기로 정규화)
        html_attrs = self._normalize_horizontal_layout_attrs(chapter.html_attributes)
        if "xmlns" not in html_attrs:
            html_attrs["xmlns"] = "http://www.w3.org/1999/xhtml"
        html_attr_str = self._dict_to_attr_str(html_attrs)
        html_parts.append(f"<html{html_attr_str}>\n<head>\n")

        # Title 노드 및 Head 내용 복원
        title_node_id = f"{chapter.file_name}_title"
        title_node = next((n for n in chapter.nodes if n.id == title_node_id), None)
        if title_node:
            title_text = translated_map.get(title_node_id, title_node.content or "")
            attr_str = self._dict_to_attr_str(title_node.attributes)
            html_parts.append(f"<title{attr_str}>{html.escape(title_text)}</title>\n")

        html_parts.append(chapter.head_html)
        # 인라인/외부 CSS가 세로쓰기를 강제해도 번역본은 가로쓰기로 고정
        html_parts.append("\n")
        html_parts.append(_HORIZONTAL_LAYOUT_STYLE)
        html_parts.append("\n</head>\n")

        # 3. Body 태그 (p-text 등 스타일 클래스는 유지, vrtl만 hltr로)
        body_attrs = self._normalize_horizontal_layout_attrs(chapter.body_attributes)
        body_attr_str = self._dict_to_attr_str(body_attrs)
        html_parts.append(f"<body{body_attr_str}>\n")

        # Body 노드 처리
        for node in chapter.nodes:
            if node.id == title_node_id:
                continue

            if node.type == NodeType.TEXT:
                translated_text = translated_map.get(node.id, node.content or "")
                escaped_text = html.escape(translated_text, quote=False)
                if not node.tag:
                    html_parts.append(f"{escaped_text}\n")
                else:
                    attr_str = self._dict_to_attr_str(node.attributes)
                    html_parts.append(f"<{node.tag}{attr_str}>{escaped_text}</{node.tag}>\n")
            else:
                # IMAGE 또는 IGNORED는 원본 HTML 그대로 사용
                html_parts.append((node.html or "") + "\n")

        html_parts.append("</body>\n</html>\n")
        return "".join(html_parts)
