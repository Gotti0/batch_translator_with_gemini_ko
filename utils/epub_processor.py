# utils/epub_processor.py
import copy
from typing import List, Dict, Optional, Set, Tuple
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

class EpubProcessor:
    def __init__(self):
        self.node_index = 0
        self.current_file_name = ""

    def process_chapter(self, html_content: bytes, file_name: str) -> EpubChapter:
        """
        HTML 콘텐츠를 파싱하여 EpubNode 리스트로 변환합니다.
        """
        self.node_index = 0
        self.current_file_name = file_name
        
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

        return EpubChapter(file_name=file_name, nodes=nodes, head_html=head_html)

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

    def _reconstruct_opening_tag(self, element: Tag) -> str:
        """
        BS4 Tag 객체에서 자식을 제외한 오프닝 태그 문자열만 생성합니다.
        """
        attrs = []
        for k, v in element.attrs.items():
            val = v if isinstance(v, str) else " ".join(v)
            attrs.append(f'{k}="{val}"')
        attr_str = " " + " ".join(attrs) if attrs else ""
        return f"<{element.name}{attr_str}>"

    def reconstruct_chapter(self, chapter: EpubChapter, translated_map: Dict[str, str]) -> str:
        """
        번역된 텍스트 맵을 사용하여 EpubChapter를 다시 HTML 문자열로 조립합니다.
        """
        html_parts = []
        
        # Head 복구 (Title 포함)
        html_parts.append("<html><head>")
        
        # Title 노드 처리
        title_node_id = f"{chapter.file_name}_title"
        title_text = translated_map.get(title_node_id)
        if title_text:
            html_parts.append(f"<title>{title_text}</title>")
            
        html_parts.append(chapter.head_html)
        html_parts.append("</head><body>")

        # Body 노드 처리
        for node in chapter.nodes:
            if node.id == title_node_id:
                continue

            if node.type == NodeType.TEXT:
                translated_text = translated_map.get(node.id, node.content)
                
                if not node.tag:
                    html_parts.append(f"{translated_text}\n")
                else:
                    # 속성 재구성
                    attrs = []
                    for k, v in node.attributes.items():
                        val = v if isinstance(v, str) else " ".join(v)
                        attrs.append(f'{k}="{val}"')
                    attr_str = " " + " ".join(attrs) if attrs else ""
                    html_parts.append(f"<{node.tag}{attr_str}>{translated_text}</{node.tag}>\n")
            else:
                # IMAGE 또는 IGNORED는 원본 HTML 그대로 사용
                html_parts.append((node.html or "") + "\n")

        html_parts.append("</body>\n</html>")
        return "".join(html_parts)
