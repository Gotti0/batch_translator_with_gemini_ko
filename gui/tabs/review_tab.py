"""
검토 및 수정 탭

번역된 청크의 품질 검토, 재번역, 수동 수정 기능을 제공합니다.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import logging
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable, Tuple

from gui.tabs.base_tab import BaseTab
from gui.components.tooltip import Tooltip

# 인프라 임포트
from infrastructure import file_handler
from infrastructure.file_handler import read_text_file, write_text_file
from utils.quality_check_service import QualityCheckService
from utils.chunk_service import ChunkService
from utils.post_processing_service import PostProcessingService


class ReviewTab(BaseTab):
    """검토 및 수정 탭 클래스"""
    
    # 상태별 색상 태그 정의
    TAG_SUCCESS = "success"      # 성공 - 녹색
    TAG_FAILED = "failed"        # 실패 - 빨간색
    TAG_OMISSION = "omission"    # 번역 누락 의심 - 주황색
    TAG_HALLUCINATION = "hallucination"  # 환각 의심 - 보라색
    TAG_PENDING = "pending"      # 미번역 - 회색
    TAG_SELECTED = "selected"    # 선택됨
    
    def __init__(
        self,
        parent: tk.Widget,
        app_service,
        logger,
        # 콜백 함수들
        get_input_files: Optional[Callable[[], List[str]]] = None,
        on_chunk_retranslated: Optional[Callable[[int, str], None]] = None,
    ):
        """
        Args:
            parent: 부모 위젯 (Notebook)
            app_service: AppService 인스턴스
            logger: 로거 인스턴스
            get_input_files: 입력 파일 목록을 가져오는 콜백 (SettingsTab에서)
            on_chunk_retranslated: 청크 재번역 완료 시 호출되는 콜백
        """
        super().__init__(parent, app_service, logger)
        
        # 콜백 함수 저장
        self._get_input_files = get_input_files
        self._on_chunk_retranslated = on_chunk_retranslated
        
        # 품질 검사 서비스
        self.quality_service = QualityCheckService()
        
        # 청크 서비스 (원문 청킹용)
        self.chunk_service = ChunkService()
        
        # 후처리 서비스 (최종 파일 생성용)
        self.post_processing_service = PostProcessingService()
        
        # 원문 청크 캐시 (파일별로 캐싱)
        self._source_chunks_cache: Dict[str, Dict[int, str]] = {}  # {파일경로: {청크인덱스: 텍스트}}
        self._source_chunks_cache_info: Dict[str, tuple] = {}  # {파일경로: (mtime, size)}
        
        # 상태 변수
        self.current_input_file: Optional[str] = None
        self.current_metadata: Optional[Dict[str, Any]] = None
        self.source_chunks: Dict[int, str] = {}  # 원문 청크
        self.translated_chunks: Dict[int, str] = {}  # 번역된 청크
        self.suspicious_chunks: List[Dict[str, Any]] = []  # 품질 의심 청크
        
        # UI 위젯 참조
        self.file_path_var: Optional[tk.StringVar] = None
        self.tree: Optional[ttk.Treeview] = None
        self.source_text: Optional[scrolledtext.ScrolledText] = None
        self.translated_text: Optional[scrolledtext.ScrolledText] = None
        self.status_label: Optional[ttk.Label] = None
        self.stats_label: Optional[ttk.Label] = None
        
        # 컨텍스트 메뉴
        self.context_menu: Optional[tk.Menu] = None
        
        # 정렬 상태 (컬럼명: 오름차순 여부)
        self._sort_column: Optional[str] = None
        self._sort_reverse: bool = False
        
        # 컬럼 기본 헤더 텍스트
        self._column_headers: Dict[str, str] = {
            "id": "ID",
            "status": "상태",
            "src_len": "원문",
            "trans_len": "번역",
            "ratio": "비율",
            "z_score": "Z-Score"
        }
        
    def create_widgets(self) -> ttk.Frame:
        """탭 위젯 생성"""
        self.frame = ttk.Frame(self.parent)
        
        # === 상단: 파일 선택 영역 ===
        self._create_file_selection_section()
        
        # === 중앙: 메인 콘텐츠 (Panedwindow) ===
        self._create_main_content()
        
        # === 하단: 상태바 ===
        self._create_status_bar()
        
        # 컨텍스트 메뉴 생성
        self._create_context_menu()
        
        # Treeview 색상 태그 설정
        self._setup_treeview_tags()
        
        return self.frame
    
    def _create_file_selection_section(self) -> None:
        """파일 선택 영역 생성"""
        file_frame = ttk.Labelframe(self.frame, text="입력 파일 선택", padding=10)
        file_frame.pack(fill='x', padx=10, pady=5)
        
        # 파일 경로 입력
        self.file_path_var = tk.StringVar()
        
        path_frame = ttk.Frame(file_frame)
        path_frame.pack(fill='x')
        
        ttk.Label(path_frame, text="입력 파일:").pack(side='left', padx=(0, 5))
        
        path_entry = ttk.Entry(path_frame, textvariable=self.file_path_var, width=60)
        path_entry.pack(side='left', fill='x', expand=True, padx=5)
        
        # 버튼들
        btn_frame = ttk.Frame(path_frame)
        btn_frame.pack(side='right')
        
        browse_btn = ttk.Button(
            btn_frame, 
            text="찾아보기", 
            command=self._browse_file,
            bootstyle="secondary"
        )
        browse_btn.pack(side='left', padx=2)
        Tooltip(browse_btn, "입력 파일을 선택합니다. 해당 파일의 메타데이터가 자동으로 로드됩니다.")
        
        sync_btn = ttk.Button(
            btn_frame,
            text="설정탭 동기화",
            command=self._sync_from_settings,
            bootstyle="info"
        )
        sync_btn.pack(side='left', padx=2)
        Tooltip(sync_btn, "설정 탭에서 선택된 입력 파일을 가져옵니다.")
        
        load_btn = ttk.Button(
            btn_frame, 
            text="로드", 
            command=self._load_metadata,
            bootstyle="primary"
        )
        load_btn.pack(side='left', padx=2)
        Tooltip(load_btn, "선택된 파일의 메타데이터와 청크를 로드합니다.")
        
        refresh_btn = ttk.Button(
            btn_frame,
            text="새로고침",
            command=self._refresh_data,
            bootstyle="success"
        )
        refresh_btn.pack(side='left', padx=2)
        Tooltip(refresh_btn, "현재 로드된 데이터를 다시 불러옵니다.")
    
    def _create_main_content(self) -> None:
        """메인 콘텐츠 영역 생성 (Panedwindow)"""
        # 수평 분할: 왼쪽(청크 목록) | 오른쪽(미리보기)
        main_paned = ttk.Panedwindow(self.frame, orient='horizontal')
        main_paned.pack(fill='both', expand=True, padx=10, pady=5)
        
        # === 왼쪽: 청크 목록 (Treeview) ===
        left_frame = self._create_chunk_list_section(main_paned)

        main_paned.add(left_frame, weight=2)
        
        # === 오른쪽: 미리보기 ===
        right_frame = self._create_preview_section(main_paned)

        main_paned.add(right_frame, weight=1)
        
    
    def _create_chunk_list_section(self, parent: ttk.Panedwindow) -> ttk.Frame:
        """청크 목록 섹션 생성"""
        frame = ttk.Labelframe(parent, text="청크 목록", padding=5)
        
        # 통계 레이블
        self.stats_label = ttk.Label(frame, text="청크: 0개 | 성공: 0 | 실패: 0 | 의심: 0")
        self.stats_label.pack(fill='x', pady=(0, 5))
        
        # Treeview 컨테이너 (Treeview + 스크롤바)
        tree_container = ttk.Frame(frame)
        tree_container.pack(fill='both', expand=True)
        
        # Treeview 생성 (extended: Ctrl+클릭으로 다중 선택 가능)
        columns = ("id", "status", "src_len", "trans_len", "ratio", "z_score")
        self.tree = ttk.Treeview(tree_container, columns=columns, show='headings', height=20, selectmode='extended')
        
        # 컬럼 설정 (클릭 시 정렬 기능 포함)
        for col in columns:
            self.tree.heading(
                col, 
                text=self._column_headers[col], 
                anchor='center',
                command=lambda c=col: self._sort_by_column(c)
            )
        
        self.tree.column("id", width=50, anchor='center')
        self.tree.column("status", width=60, anchor='center')
        self.tree.column("src_len", width=60, anchor='center')
        self.tree.column("trans_len", width=60, anchor='center')
        self.tree.column("ratio", width=60, anchor='center')
        self.tree.column("z_score", width=70, anchor='center')
        
        # 스크롤바
        scrollbar = ttk.Scrollbar(tree_container, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # 이벤트 바인딩
        self.tree.bind('<<TreeviewSelect>>', self._on_chunk_selected)
        self.tree.bind('<Button-3>', self._show_context_menu)  # 우클릭
        self.tree.bind('<Double-1>', self._on_chunk_double_click)  # 더블클릭
        
        # 액션 버튼들 (1열 6행 세로 배치)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill='x', pady=(5, 0))
        
        retry_btn = ttk.Button(
            btn_frame,
            text="재번역",
            command=self._retry_selected_chunk,
            bootstyle="warning"
        )
        retry_btn.pack(fill='x', pady=1)
        Tooltip(retry_btn, "선택한 청크를 다시 번역합니다.")
        
        edit_btn = ttk.Button(
            btn_frame,
            text="수동 수정",
            command=self._edit_selected_chunk,
            bootstyle="info"
        )
        edit_btn.pack(fill='x', pady=1)
        Tooltip(edit_btn, "선택한 청크의 번역문을 직접 수정합니다.")
        
        reset_btn = ttk.Button(
            btn_frame,
            text="초기화",
            command=self._reset_selected_chunk,
            bootstyle="secondary"
        )
        reset_btn.pack(fill='x', pady=1)
        Tooltip(reset_btn, "선택한 청크의 번역 기록을 삭제합니다.")
        
        confirm_btn = ttk.Button(
            btn_frame,
            text="확정",
            command=self._confirm_selected_chunk,
            bootstyle="success"
        )
        confirm_btn.pack(fill='x', pady=1)
        Tooltip(confirm_btn, "선택한 청크의 경고를 해제합니다.")
        
        copy_src_btn = ttk.Button(
            btn_frame,
            text="원문 복사",
            command=self._copy_source_text,
            bootstyle="outline"
        )
        copy_src_btn.pack(fill='x', pady=1)
        Tooltip(copy_src_btn, "선택한 청크의 원문을 클립보드에 복사합니다.")
        
        copy_trans_btn = ttk.Button(
            btn_frame,
            text="번역 복사",
            command=self._copy_translated_text,
            bootstyle="outline"
        )
        copy_trans_btn.pack(fill='x', pady=1)
        Tooltip(copy_trans_btn, "선택한 청크의 번역문을 클립보드에 복사합니다.")
        
        return frame
    
    def _create_preview_section(self, parent: ttk.Panedwindow) -> ttk.Frame:
        """미리보기 섹션 생성"""
        frame = ttk.Labelframe(parent, text="청크 미리보기", padding=5)
        
        # 수직 분할: 위(원문) | 아래(번역문)
        paned = ttk.Panedwindow(frame, orient='vertical')
        paned.pack(fill='both', expand=True)
        
        # 원문 영역
        source_frame = ttk.Labelframe(paned, text="원문", padding=5)
        self.source_text = scrolledtext.ScrolledText(
            source_frame, 
            wrap='word', 
            width=30,   # 최소 너비 축소
            height=5,   # 최소 높이 축소 (10 → 5)
            font=('맑은 고딕', 10)
        )
        self.source_text.pack(fill='both', expand=True)
        self.source_text.config(state='disabled')
        paned.add(source_frame, weight=1)
        
        # 번역문 영역
        translated_frame = ttk.Labelframe(paned, text="번역문", padding=5)
        self.translated_text = scrolledtext.ScrolledText(
            translated_frame, 
            wrap='word', 
            width=30,   # 최소 너비 축소
            height=5,   # 최소 높이 축소 (10 → 5)
            font=('맑은 고딕', 10)
        )
        self.translated_text.pack(fill='both', expand=True)
        self.translated_text.config(state='disabled')
        paned.add(translated_frame, weight=1)
        
        return frame
    
    def _create_status_bar(self) -> None:
        """상태바 생성"""
        status_frame = ttk.Frame(self.frame)
        status_frame.pack(fill='x', padx=10, pady=5)
        
        self.status_label = ttk.Label(
            status_frame, 
            text="파일을 선택하고 로드 버튼을 클릭하세요.",
            bootstyle="secondary"
        )
        self.status_label.pack(side='left')
        
        # 메타데이터 관리 버튼들
        btn_frame = ttk.Frame(status_frame)
        btn_frame.pack(side='right')
        
        generate_final_btn = ttk.Button(
            btn_frame,
            text="최종 파일 생성",
            command=self._generate_final_file,
            bootstyle="success"
        )
        generate_final_btn.pack(side='left', padx=2)
        Tooltip(generate_final_btn, "현재 청크들을 병합하여 최종 번역 파일(_translated.txt)을 생성합니다.")
        
        integrity_btn = ttk.Button(
            btn_frame,
            text="무결성 검사",
            command=self._check_integrity,
            bootstyle="info-outline"
        )
        integrity_btn.pack(side='left', padx=2)
        Tooltip(integrity_btn, "메타데이터와 청크 파일의 일관성을 검사합니다.")
    
    def _create_context_menu(self) -> None:
        """컨텍스트 메뉴 생성"""
        self.context_menu = tk.Menu(self.frame, tearoff=0)
        self.context_menu.add_command(label="재번역", command=self._retry_selected_chunk)
        self.context_menu.add_command(label="수동 수정", command=self._edit_selected_chunk)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="초기화", command=self._reset_selected_chunk)
        self.context_menu.add_command(label="확정 (경고 해제)", command=self._confirm_selected_chunk)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="원문 복사", command=self._copy_source_text)
        self.context_menu.add_command(label="번역문 복사", command=self._copy_translated_text)
    
    def _setup_treeview_tags(self) -> None:
        """Treeview 색상 태그 설정"""
        if self.tree:
            self.tree.tag_configure(self.TAG_SUCCESS, background='#d4edda')  # 연한 녹색
            self.tree.tag_configure(self.TAG_FAILED, background='#f8d7da')   # 연한 빨간색
            self.tree.tag_configure(self.TAG_OMISSION, background='#fff3cd')  # 연한 주황색
            self.tree.tag_configure(self.TAG_HALLUCINATION, background='#e2d5f1')  # 연한 보라색
            self.tree.tag_configure(self.TAG_PENDING, background='#e2e3e5')  # 연한 회색
    
    # === Treeview 정렬 메서드 ===
    
    def _sort_by_column(self, column: str) -> None:
        """컬럼 기준으로 Treeview 정렬
        
        Args:
            column: 정렬할 컬럼명
        """
        if not self.tree:
            return
        
        # 같은 컬럼 클릭 시 정렬 방향 토글
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        
        # 현재 데이터 수집
        items = []
        for item_id in self.tree.get_children(''):
            values = self.tree.item(item_id, 'values')
            tags = self.tree.item(item_id, 'tags')
            items.append((item_id, values, tags))
        
        # 컬럼 인덱스 찾기
        columns = ("id", "status", "src_len", "trans_len", "ratio", "z_score")
        col_idx = columns.index(column)
        
        # 정렬 키 함수 정의
        def sort_key(item):
            value = item[1][col_idx]  # values[col_idx]
            
            if column == "id":
                # ID: 숫자 정렬
                try:
                    return int(value)
                except (ValueError, TypeError):
                    return 0
            
            elif column == "status":
                # 상태: 커스텀 우선순위 (실패 → 누락 → 환각 → 미번역 → 성공)
                priority = {
                    "❌": 0,
                    "⚠️ 누락": 1,
                    "⚠️ 환각": 2,
                    "⏳": 3,
                    "✅": 4
                }
                return priority.get(value, 5)
            
            elif column in ("src_len", "trans_len"):
                # 길이: 숫자 정렬 ('-'는 -1로 처리)
                if value == '-':
                    return -1
                try:
                    return int(value)
                except (ValueError, TypeError):
                    return -1
            
            elif column in ("ratio", "z_score"):
                # 비율/Z-Score: 실수 정렬 ('-'는 -999로 처리)
                if value == '-':
                    return -999.0
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return -999.0
            
            return str(value)
        
        # 정렬 수행
        items.sort(key=sort_key, reverse=self._sort_reverse)
        
        # Treeview 재배치
        for idx, (item_id, _, _) in enumerate(items):
            self.tree.move(item_id, '', idx)
        
        # 헤더 텍스트 업데이트 (정렬 방향 표시)
        self._update_column_headers()
    
    def _update_column_headers(self) -> None:
        """컬럼 헤더에 정렬 방향 표시 업데이트"""
        if not self.tree:
            return
        
        for col, base_text in self._column_headers.items():
            if col == self._sort_column:
                # 현재 정렬 중인 컬럼: 방향 표시 추가
                arrow = "▼" if self._sort_reverse else "▲"
                text = f"{base_text} {arrow}"
            else:
                # 다른 컬럼: 기본 텍스트
                text = base_text
            
            self.tree.heading(col, text=text)
    
    def _reset_sort_state(self) -> None:
        """정렬 상태 초기화 (데이터 새로 로드 시 호출)"""
        self._sort_column = None
        self._sort_reverse = False
        self._update_column_headers()
    
    # === 파일 경로 헬퍼 메서드 ===
    
    def _get_translated_chunked_file_path(self, input_file_path: str) -> Path:
        """번역된 청크 파일 경로 반환 (_translated_chunked.txt)
        
        번역 시 output_file은 input_file에서 '_translated' 접미사가 붙어 생성됨.
        예: input.txt → input_translated.txt → input_translated_chunked.txt
        """
        p = Path(input_file_path)
        # 청크 백업 파일명: stem + '_translated_chunked.txt'
        return p.parent / f"{p.stem}_translated_chunked.txt"
    
    def _get_file_cache_key(self, file_path: str) -> tuple:
        """파일의 캐시 키 생성 (수정시간, 크기)"""
        try:
            p = Path(file_path)
            stat = p.stat()
            return (stat.st_mtime, stat.st_size)
        except Exception:
            return (0, 0)
    
    def _is_cache_valid(self, file_path: str) -> bool:
        """캐시가 유효한지 확인 (파일이 변경되지 않았는지)"""
        if file_path not in self._source_chunks_cache:
            return False
        if file_path not in self._source_chunks_cache_info:
            return False
        
        current_key = self._get_file_cache_key(file_path)
        cached_key = self._source_chunks_cache_info.get(file_path)
        
        return current_key == cached_key
    
    def _load_source_chunks_from_file(self, input_file_path: str) -> Dict[int, str]:
        """원본 파일에서 원문 청크를 동적으로 생성 (캐싱 적용)
        
        원문은 별도 파일에 저장되지 않으므로, 원본 파일을 읽어서
        청크 서비스를 사용해 동적으로 청킹합니다.
        
        캐싱: 파일이 변경되지 않았으면 이전에 생성한 청크를 재사용합니다.
        """
        try:
            # 캐시 유효성 확인
            if self._is_cache_valid(input_file_path):
                self.log_message(f"원문 청크 캐시 사용: {Path(input_file_path).name}")
                return self._source_chunks_cache[input_file_path]
            
            # 캐시 미스 - 새로 생성
            self.log_message(f"원문 청크 생성 중: {Path(input_file_path).name}")
            
            file_content = read_text_file(input_file_path)
            if not file_content:
                return {}
            
            # 설정에서 chunk_size 가져오기
            chunk_size = 6000  # 기본값
            if self.app_service and self.app_service.config:
                chunk_size = self.app_service.config.get('chunk_size', 6000)
            
            chunks_list = self.chunk_service.create_chunks_from_file_content(
                file_content, chunk_size
            )
            
            # 리스트를 {인덱스: 텍스트} 딕셔너리로 변환
            result = {i: chunk for i, chunk in enumerate(chunks_list)}
            
            # 캐시 저장
            self._source_chunks_cache[input_file_path] = result
            self._source_chunks_cache_info[input_file_path] = self._get_file_cache_key(input_file_path)
            
            self.log_message(f"원문 청크 {len(result)}개 생성 완료 (캐시 저장됨)")
            
            return result
        except Exception as e:
            self.log_message(f"원문 청크 로드 실패: {e}", level="ERROR")
            return {}
    
    def _get_single_source_chunk(self, input_file_path: str, chunk_index: int) -> Optional[str]:
        """특정 청크만 가져오기 (캐시 활용)
        
        전체 청크가 이미 캐시되어 있으면 캐시에서 반환.
        아니면 전체를 로드 후 해당 청크 반환.
        """
        chunks = self._load_source_chunks_from_file(input_file_path)
        return chunks.get(chunk_index)
    
    def clear_source_chunks_cache(self, file_path: Optional[str] = None) -> None:
        """원문 청크 캐시 초기화
        
        Args:
            file_path: 특정 파일만 초기화. None이면 전체 초기화.
        """
        if file_path:
            self._source_chunks_cache.pop(file_path, None)
            self._source_chunks_cache_info.pop(file_path, None)
        else:
            self._source_chunks_cache.clear()
            self._source_chunks_cache_info.clear()
    
    # === 파일/데이터 로드 메서드 ===
    
    def _browse_file(self) -> None:
        """파일 찾아보기"""
        file_path = filedialog.askopenfilename(
            title="입력 파일 선택",
            filetypes=[
                ("텍스트 파일", "*.txt"),
                ("모든 파일", "*.*")
            ]
        )
        if file_path:
            self.file_path_var.set(file_path)
    
    def _sync_from_settings(self) -> None:
        """설정 탭에서 입력 파일 동기화"""
        if self._get_input_files:
            files = self._get_input_files()
            if files:
                self.file_path_var.set(files[0])
                self._update_status(f"설정 탭에서 파일 동기화: {Path(files[0]).name}")
            else:
                messagebox.showwarning("경고", "설정 탭에 선택된 입력 파일이 없습니다.")
        else:
            messagebox.showwarning("경고", "설정 탭 연결이 없습니다.")
    
    def _load_metadata(self) -> None:
        """메타데이터 로드"""
        file_path = self.file_path_var.get().strip()
        if not file_path:
            messagebox.showwarning("경고", "파일 경로를 입력하세요.")
            return
        
        if not Path(file_path).exists():
            messagebox.showerror("오류", f"파일이 존재하지 않습니다: {file_path}")
            return
        
        try:
            self._update_status("데이터 로드 중...")
            
            # 현재 파일 저장
            self.current_input_file = file_path
            
            # 메타데이터 로드
            self.current_metadata = file_handler.load_metadata(file_path)
            if not self.current_metadata:
                messagebox.showinfo("정보", "메타데이터가 없습니다. 먼저 번역을 실행하세요.")
                return
            
            # 원문 청크 로드 (원본 파일에서 동적 생성)
            self.source_chunks = self._load_source_chunks_from_file(file_path)
            if not self.source_chunks:
                self.log_message("원본 파일에서 청크를 생성할 수 없습니다.", level="WARNING")
            
            # 번역된 청크 로드 (.chunked.txt - 번역 결과 파일)
            translated_path = self._get_translated_chunked_file_path(file_path)
            self.log_message(f"[DEBUG] 입력 파일: {file_path}", level="DEBUG")
            self.log_message(f"[DEBUG] 번역 청크 경로: {translated_path}", level="DEBUG")
            self.log_message(f"[DEBUG] 번역 청크 파일 존재: {Path(translated_path).exists()}", level="DEBUG")
            
            if Path(translated_path).exists():
                self.translated_chunks = file_handler.load_chunks_from_file(translated_path)
                self.log_message(f"[DEBUG] 로드된 번역 청크 수: {len(self.translated_chunks)}", level="DEBUG")
            else:
                self.translated_chunks = {}
                self.log_message(f"[DEBUG] 번역 청크 파일 없음 - 빈 딕셔너리 반환", level="DEBUG")
            
            # 품질 분석
            self.suspicious_chunks = self.quality_service.analyze_translation_quality(
                self.current_metadata
            )
            
            # Treeview 갱신
            self._populate_treeview()
            
            # 통계 업데이트
            self._update_statistics()
            
            self._update_status(f"로드 완료: {Path(file_path).name}")
            self.log_message(f"메타데이터 로드 완료: {file_path}")
            
        except Exception as e:
            self.log_message(f"메타데이터 로드 실패: {e}", level="ERROR", exc_info=True)
            messagebox.showerror("오류", f"데이터 로드 중 오류 발생: {e}")
    
    def _refresh_data(self) -> None:
        """현재 데이터 새로고침"""
        if self.current_input_file:
            self._load_metadata()
        else:
            messagebox.showinfo("정보", "먼저 파일을 로드하세요.")
    
    def _populate_treeview(self) -> None:
        """Treeview에 청크 데이터 채우기"""
        if not self.tree:
            return
        
        # 기존 항목 삭제
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        if not self.current_metadata:
            return
        
        total_chunks = self.current_metadata.get('total_chunks', 0)
        translated_chunks = self.current_metadata.get('translated_chunks', {})
        failed_chunks = self.current_metadata.get('failed_chunks', {})
        
        # 의심 청크 인덱스 맵 생성
        suspicious_map = {}
        for susp in self.suspicious_chunks:
            idx = susp.get('chunk_index')
            if idx is not None:
                suspicious_map[idx] = susp
        
        for i in range(total_chunks):
            idx_str = str(i)
            
            # 상태 및 정보 결정
            status = "⏳"  # 미번역
            tag = self.TAG_PENDING
            src_len = "-"
            trans_len = "-"
            ratio = "-"
            z_score = "-"
            
            if idx_str in translated_chunks:
                chunk_info = translated_chunks[idx_str]
                if isinstance(chunk_info, dict):
                    src_len = chunk_info.get('source_length', '-')
                    trans_len = chunk_info.get('translated_length', '-')
                    ratio = chunk_info.get('ratio', '-')
                    if isinstance(ratio, float):
                        ratio = f"{ratio:.2f}"
                
                # 의심 청크 확인
                if i in suspicious_map:
                    susp_info = suspicious_map[i]
                    issue_type = susp_info.get('issue_type', '')
                    z = susp_info.get('z_score', 0)
                    z_score = f"{z:.2f}"
                    
                    if issue_type == 'omission':
                        status = "⚠️ 누락"
                        tag = self.TAG_OMISSION
                    elif issue_type == 'hallucination':
                        status = "⚠️ 환각"
                        tag = self.TAG_HALLUCINATION
                    else:
                        status = "✅"
                        tag = self.TAG_SUCCESS
                else:
                    status = "✅"
                    tag = self.TAG_SUCCESS
                    
            elif idx_str in failed_chunks:
                status = "❌"
                tag = self.TAG_FAILED
            
            # Treeview에 추가
            self.tree.insert(
                '', 'end',
                iid=str(i),
                values=(i, status, src_len, trans_len, ratio, z_score),
                tags=(tag,)
            )
        
        # 정렬 상태가 있으면 다시 적용 (방향 유지)
        if self._sort_column:
            # 현재 정렬 상태 백업
            saved_reverse = self._sort_reverse
            # 정렬 호출 (내부에서 토글되므로 반대로 설정)
            self._sort_reverse = not saved_reverse
            self._sort_by_column(self._sort_column)
    
    def _update_statistics(self) -> None:
        """통계 레이블 업데이트"""
        if not self.current_metadata or not self.stats_label:
            return
        
        total = self.current_metadata.get('total_chunks', 0)
        success = len(self.current_metadata.get('translated_chunks', {}))
        failed = len(self.current_metadata.get('failed_chunks', {}))
        suspicious = len(self.suspicious_chunks)
        
        self.stats_label.config(
            text=f"청크: {total}개 | 성공: {success} | 실패: {failed} | 의심: {suspicious}"
        )
    
    def _update_status(self, message: str) -> None:
        """상태 레이블 업데이트"""
        if self.status_label:
            self.status_label.config(text=message)
    
    # === 이벤트 핸들러 ===
    
    def _on_chunk_selected(self, event=None) -> None:
        """청크 선택 시 미리보기 업데이트"""
        if not self.tree:
            return
        
        selection = self.tree.selection()
        if not selection:
            return
        
        try:
            chunk_idx = int(selection[0])
            self._show_chunk_preview(chunk_idx)
        except ValueError:
            pass
    
    def _on_chunk_double_click(self, event=None) -> None:
        """청크 더블클릭 시 수정 다이얼로그"""
        self._edit_selected_chunk()
    
    def _show_context_menu(self, event) -> None:
        """컨텍스트 메뉴 표시"""
        if self.context_menu and self.tree:
            # 클릭한 위치의 항목 선택
            item = self.tree.identify_row(event.y)
            if item:
                self.tree.selection_set(item)
                self.context_menu.tk_popup(event.x_root, event.y_root)
    
    def _show_chunk_preview(self, chunk_idx: int) -> None:
        """청크 미리보기 표시"""
        # 원문 표시
        if self.source_text:
            self.source_text.config(state='normal')
            self.source_text.delete('1.0', tk.END)
            if chunk_idx in self.source_chunks:
                self.source_text.insert('1.0', self.source_chunks[chunk_idx])
            else:
                self.source_text.insert('1.0', "(원문을 찾을 수 없습니다)")
            self.source_text.config(state='disabled')
        
        # 번역문 표시
        if self.translated_text:
            self.translated_text.config(state='normal')
            self.translated_text.delete('1.0', tk.END)
            if chunk_idx in self.translated_chunks:
                self.translated_text.insert('1.0', self.translated_chunks[chunk_idx])
            else:
                self.translated_text.insert('1.0', "(번역문을 찾을 수 없습니다)")
            self.translated_text.config(state='disabled')
    
    # === 청크 액션 메서드 ===
    
    def _get_selected_chunk_index(self) -> Optional[int]:
        """선택된 청크 인덱스 반환 (단일 선택)"""
        if not self.tree:
            return None
        
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("경고", "청크를 선택하세요.")
            return None
        
        try:
            return int(selection[0])
        except ValueError:
            return None
    
    def _get_selected_chunk_indices(self) -> List[int]:
        """선택된 모든 청크 인덱스 반환 (다중 선택 지원)
        
        Ctrl+클릭으로 여러 청크를 선택할 수 있습니다.
        """
        if not self.tree:
            return []
        
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("경고", "청크를 선택하세요.")
            return []
        
        indices = []
        for item in selection:
            try:
                indices.append(int(item))
            except ValueError:
                pass
        
        return sorted(indices)
    
    def _retry_selected_chunk(self) -> None:
        """선택한 청크 재번역"""
        chunk_idx = self._get_selected_chunk_index()
        if chunk_idx is None:
            return
        
        if not self.current_input_file:
            messagebox.showerror("오류", "파일이 로드되지 않았습니다.")
            return
        
        # 번역 서비스 확인
        if not self.app_service or not self.app_service.translation_service:
            messagebox.showerror("오류", "번역 서비스가 초기화되지 않았습니다.\n설정 탭에서 API 키를 확인하세요.")
            return
        
        # 현재 번역 중인지 확인
        if self.app_service.is_translation_running:
            messagebox.showwarning("경고", "현재 번역 작업이 진행 중입니다.\n작업 완료 후 다시 시도하세요.")
            return
        
        # 확인 대화상자
        if not messagebox.askyesno(
            "재번역 확인",
            f"청크 #{chunk_idx}를 재번역하시겠습니까?\n\n"
            "현재 번역 설정(프롬프트, 모델 등)이 적용됩니다."
        ):
            return
        
        # 재번역 스레드 시작
        self._start_retranslation_thread(chunk_idx)
    
    def _start_retranslation_thread(self, chunk_idx: int) -> None:
        """재번역 스레드 시작"""
        self._update_status(f"청크 #{chunk_idx} 재번역 중...")
        self.log_message(f"청크 #{chunk_idx} 재번역 시작")
        
        # UI 버튼 비활성화 (재번역 중 중복 클릭 방지)
        self._set_action_buttons_state('disabled')
        
        def retranslation_task():
            try:
                def progress_callback(msg: str):
                    # UI 스레드에서 상태 업데이트
                    self.frame.after(0, lambda: self._update_status(msg))

                # 재번역 결과를 저장할 정확한 청크 파일 경로를 가져옵니다.
                chunk_file_path = self._get_translated_chunked_file_path(self.current_input_file)
                if not chunk_file_path.exists():
                    raise FileNotFoundError(f"청크 파일을 찾을 수 없습니다: {chunk_file_path}")

                success, result = self.app_service.translate_single_chunk(
                    self.current_input_file,
                    str(chunk_file_path),  # 정확한 청크 파일 경로 전달
                    chunk_idx,
                    progress_callback=progress_callback
                )
                
                # 결과 처리 (UI 스레드에서)
                self.frame.after(0, lambda: self._on_retranslation_complete(chunk_idx, success, result))
                
            except Exception as e:
                error_msg = f"재번역 중 오류: {e}"
                self.frame.after(0, lambda: self._on_retranslation_complete(chunk_idx, False, error_msg))
        
        thread = threading.Thread(target=retranslation_task, daemon=True)
        thread.start()
    
    def _on_retranslation_complete(self, chunk_idx: int, success: bool, result: str) -> None:
        """재번역 완료 콜백"""
        # 버튼 다시 활성화
        self._set_action_buttons_state('normal')
        
        if success:
            # 내부 데이터 업데이트
            self.translated_chunks[chunk_idx] = result
            
            # 미리보기 업데이트
            self._show_chunk_preview(chunk_idx)
            
            # Treeview 갱신
            self._refresh_data()
            
            self._update_status(f"청크 #{chunk_idx} 재번역 완료!")
            self.log_message(f"청크 #{chunk_idx} 재번역 성공 ({len(result)}자)")
            
            # 콜백 호출 (있는 경우)
            if self._on_chunk_retranslated:
                self._on_chunk_retranslated(chunk_idx, result)
            
            messagebox.showinfo("성공", f"청크 #{chunk_idx} 재번역이 완료되었습니다.")
        else:
            self._update_status(f"청크 #{chunk_idx} 재번역 실패")
            self.log_message(f"청크 #{chunk_idx} 재번역 실패: {result}", level="ERROR")
            messagebox.showerror("재번역 실패", f"청크 #{chunk_idx} 재번역에 실패했습니다.\n\n{result}")
    
    def _set_action_buttons_state(self, state: str) -> None:
        """액션 버튼들의 상태 설정"""
        # frame 내의 모든 버튼 찾기는 복잡하므로, 
        # 버튼들을 인스턴스 변수로 저장하지 않은 현재 구조에서는
        # status_label을 통해 사용자에게 상태만 알림
        # 추후 버튼들을 인스턴스 변수로 관리하면 여기서 상태 변경 가능
        pass
    
    def _edit_selected_chunk(self) -> None:
        """선택한 청크 수동 수정"""
        chunk_idx = self._get_selected_chunk_index()
        if chunk_idx is None:
            return
        
        if not self.current_input_file:
            messagebox.showerror("오류", "파일이 로드되지 않았습니다.")
            return
        
        # 수정 다이얼로그 표시
        self._show_edit_dialog(chunk_idx)
    
    def _show_edit_dialog(self, chunk_idx: int) -> None:
        """청크 수정 다이얼로그 표시"""
        dialog = tk.Toplevel(self.frame)
        dialog.title(f"청크 #{chunk_idx} 수정")
        dialog.geometry("600x500")
        dialog.transient(self.frame.winfo_toplevel())
        dialog.grab_set()
        
        # 원문 (읽기 전용)
        ttk.Label(dialog, text="원문:", font=('맑은 고딕', 10, 'bold')).pack(anchor='w', padx=10, pady=(10, 0))
        source_text = scrolledtext.ScrolledText(dialog, wrap='word', height=8, font=('맑은 고딕', 10))
        source_text.pack(fill='x', padx=10, pady=5)
        if chunk_idx in self.source_chunks:
            source_text.insert('1.0', self.source_chunks[chunk_idx])
        source_text.config(state='disabled')
        
        # 번역문 (편집 가능)
        ttk.Label(dialog, text="번역문 (수정 가능):", font=('맑은 고딕', 10, 'bold')).pack(anchor='w', padx=10, pady=(10, 0))
        translated_edit = scrolledtext.ScrolledText(dialog, wrap='word', height=10, font=('맑은 고딕', 10))
        translated_edit.pack(fill='both', expand=True, padx=10, pady=5)
        if chunk_idx in self.translated_chunks:
            translated_edit.insert('1.0', self.translated_chunks[chunk_idx])
        
        # 버튼
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill='x', padx=10, pady=10)
        
        def save_changes():
            new_translation = translated_edit.get('1.0', tk.END).strip()
            if not new_translation:
                messagebox.showwarning("경고", "번역문을 입력하세요.")
                return
            
            try:
                # 번역 파일 업데이트
                self._save_chunk_translation(chunk_idx, new_translation)
                messagebox.showinfo("성공", f"청크 #{chunk_idx} 번역문이 저장되었습니다.")
                dialog.destroy()
                self._refresh_data()
            except Exception as e:
                messagebox.showerror("오류", f"저장 실패: {e}")
        
        ttk.Button(
            btn_frame, 
            text="저장", 
            command=save_changes,
            bootstyle="success"
        ).pack(side='right', padx=5)
        
        ttk.Button(
            btn_frame, 
            text="취소", 
            command=dialog.destroy,
            bootstyle="secondary"
        ).pack(side='right', padx=5)
    
    def _save_chunk_translation(self, chunk_idx: int, translation: str) -> None:
        """청크 번역 저장"""
        if not self.current_input_file:
            raise ValueError("파일이 로드되지 않았습니다.")
        
        # 번역 청크 딕셔너리 업데이트
        self.translated_chunks[chunk_idx] = translation
        
        # 번역 파일에 저장
        translated_path = self._get_translated_chunked_file_path(self.current_input_file)
        file_handler.save_merged_chunks_to_file(translated_path, self.translated_chunks)
        
        # 메타데이터 업데이트
        source_len = len(self.source_chunks.get(chunk_idx, ''))
        trans_len = len(translation)
        file_handler.update_metadata_for_chunk_completion(
            self.current_input_file,
            chunk_idx,
            source_length=source_len,
            translated_length=trans_len
        )
        
        self.log_message(f"청크 #{chunk_idx} 수동 수정 저장 완료")
    
    def _reset_selected_chunk(self) -> None:
        """선택한 청크 초기화 (메타데이터에서 제거) - 다중 선택 지원"""
        chunk_indices = self._get_selected_chunk_indices()
        if not chunk_indices:
            return
        
        if not self.current_input_file:
            messagebox.showerror("오류", "파일이 로드되지 않았습니다.")
            return
        
        count = len(chunk_indices)
        chunk_list = ", ".join(f"#{idx}" for idx in chunk_indices[:5])
        if count > 5:
            chunk_list += f" 외 {count - 5}개"
        
        if not messagebox.askyesno(
            "확인", 
            f"선택한 {count}개 청크의 번역 기록을 삭제하시겠습니까?\n"
            f"대상: {chunk_list}\n\n"
            "다음 번역 시 이 청크들이 다시 번역됩니다."
        ):
            return
        
        try:
            reset_count = 0
            
            if self.current_metadata:
                translated = self.current_metadata.get('translated_chunks', {})
                failed = self.current_metadata.get('failed_chunks', {})
                
                for chunk_idx in chunk_indices:
                    idx_str = str(chunk_idx)
                    if idx_str in translated:
                        del translated[idx_str]
                        reset_count += 1
                    if idx_str in failed:
                        del failed[idx_str]
                        reset_count += 1
                
                self.current_metadata['translated_chunks'] = translated
                self.current_metadata['failed_chunks'] = failed
                self.current_metadata['status'] = 'in_progress'
                
                file_handler.save_metadata(self.current_input_file, self.current_metadata)
            
            messagebox.showinfo("성공", f"{count}개 청크 초기화 완료")
            self._refresh_data()
            self.log_message(f"{count}개 청크 초기화 완료: {chunk_list}")
            
        except Exception as e:
            self.log_message(f"청크 초기화 실패: {e}", level="ERROR", exc_info=True)
            messagebox.showerror("오류", f"초기화 실패: {e}")
    
    def _confirm_selected_chunk(self) -> None:
        """선택한 청크 확정 (경고 해제) - 다중 선택 지원"""
        chunk_indices = self._get_selected_chunk_indices()
        if not chunk_indices:
            return
        
        # 내부 suspicious_chunks 목록에서 선택된 청크들 제거 (UI만 업데이트)
        confirmed_count = 0
        for chunk_idx in chunk_indices:
            before_len = len(self.suspicious_chunks)
            self.suspicious_chunks = [
                s for s in self.suspicious_chunks 
                if s.get('chunk_index') != chunk_idx
            ]
            if len(self.suspicious_chunks) < before_len:
                confirmed_count += 1
        
        self._populate_treeview()
        self._update_statistics()
        
        if len(chunk_indices) == 1:
            self._update_status(f"청크 #{chunk_indices[0]} 확정됨 (경고 해제)")
            self.log_message(f"청크 #{chunk_indices[0]} 확정 (경고 해제)")
        else:
            self._update_status(f"{len(chunk_indices)}개 청크 확정됨 (경고 해제: {confirmed_count}개)")
            self.log_message(f"{len(chunk_indices)}개 청크 확정 (경고 해제: {confirmed_count}개)")
    
    # === 최종 파일 생성 메서드 ===
    
    def _get_final_output_file_path(self, input_file_path: str) -> Path:
        """최종 출력 파일 경로 반환 (_translated.txt)
        
        예: input.txt → input_translated.txt
        """
        p = Path(input_file_path)
        return p.parent / f"{p.stem}_translated{p.suffix}"
    
    def _generate_final_file(self) -> None:
        """현재 청크들을 병합하여 최종 번역 파일 생성"""
        if not self.current_input_file:
            messagebox.showwarning("경고", "먼저 파일을 로드하세요.")
            return
        
        if not self.translated_chunks:
            messagebox.showwarning("경고", "번역된 청크가 없습니다.")
            return
        
        # 미번역 청크 확인
        total_chunks = self.current_metadata.get('total_chunks', 0) if self.current_metadata else 0
        translated_count = len(self.translated_chunks)
        
        if translated_count < total_chunks:
            if not messagebox.askyesno(
                "미완료 번역 경고",
                f"전체 {total_chunks}개 청크 중 {translated_count}개만 번역되었습니다.\n"
                f"미번역 청크 {total_chunks - translated_count}개가 있습니다.\n\n"
                "그래도 최종 파일을 생성하시겠습니까?"
            ):
                return
        
        try:
            self._update_status("최종 파일 생성 중...")
            
            final_output_path = self._get_final_output_file_path(self.current_input_file)
            chunked_path = self._get_translated_chunked_file_path(self.current_input_file)
            
            # 1. 먼저 현재 청크들을 chunked 파일에 저장 (최신 상태 반영)
            file_handler.save_merged_chunks_to_file(chunked_path, self.translated_chunks)
            self.log_message(f"청크 파일 업데이트: {chunked_path}")
            
            # 2. 후처리 설정 확인
            enable_post_processing = True
            if self.app_service and self.app_service.config:
                enable_post_processing = self.app_service.config.get("enable_post_processing", True)
            
            # 3. 청크 내용 후처리 (옵션)
            chunks_to_merge = self.translated_chunks.copy()
            if enable_post_processing:
                try:
                    config = self.app_service.config if self.app_service else {}
                    chunks_to_merge = self.post_processing_service.post_process_merged_chunks(
                        chunks_to_merge, config
                    )
                    self.log_message("청크 후처리 완료")
                except Exception as e:
                    self.log_message(f"후처리 중 오류 (원본 유지): {e}", level="WARNING")
            
            # 4. 청크들을 순서대로 병합하여 최종 파일 생성
            sorted_indices = sorted(chunks_to_merge.keys())
            merged_content_parts = []
            
            for idx in sorted_indices:
                chunk_content = chunks_to_merge[idx]
                merged_content_parts.append(chunk_content)
            
            # 청크 사이에 빈 줄 추가하여 병합
            final_content = "\n\n".join(merged_content_parts)
            
            # 연속된 빈 줄 정리 (3개 이상을 2개로)
            final_content = re.sub(r'\n{3,}', '\n\n', final_content)
            final_content = final_content.strip()
            
            # 5. 최종 파일 저장
            write_text_file(final_output_path, final_content)
            
            self._update_status(f"최종 파일 생성 완료: {final_output_path.name}")
            self.log_message(f"최종 번역 파일 생성 완료: {final_output_path}")
            self.log_message(f"  - 청크 수: {len(sorted_indices)}개")
            self.log_message(f"  - 파일 크기: {len(final_content):,}자")
            
            messagebox.showinfo(
                "성공",
                f"최종 번역 파일이 생성되었습니다.\n\n"
                f"파일: {final_output_path.name}\n"
                f"청크 수: {len(sorted_indices)}개\n"
                f"파일 크기: {len(final_content):,}자"
            )
            
        except Exception as e:
            self.log_message(f"최종 파일 생성 실패: {e}", level="ERROR", exc_info=True)
            messagebox.showerror("오류", f"최종 파일 생성 실패: {e}")
            self._update_status("최종 파일 생성 실패")
    
    # === 메타데이터 관리 메서드 ===
    
    def _check_integrity(self) -> None:
        """메타데이터 무결성 검사"""
        if not self.current_input_file or not self.current_metadata:
            messagebox.showwarning("경고", "먼저 파일을 로드하세요.")
            return
        
        issues = []
        
        # 청크 수 검사
        meta_total = self.current_metadata.get('total_chunks', 0)
        actual_source = len(self.source_chunks)
        actual_translated = len(self.translated_chunks)
        
        if meta_total != actual_source and actual_source > 0:
            issues.append(f"• 메타데이터 청크 수({meta_total})와 원문 청크 수({actual_source}) 불일치")
        
        # 번역 완료 청크 검사
        translated_meta = self.current_metadata.get('translated_chunks', {})
        for idx_str in translated_meta:
            try:
                idx = int(idx_str)
                if idx not in self.translated_chunks:
                    issues.append(f"• 청크 #{idx}: 메타데이터에 있으나 번역 파일에 없음")
            except ValueError:
                issues.append(f"• 잘못된 청크 인덱스: {idx_str}")
        
        # 결과 표시
        if issues:
            issue_text = "\n".join(issues)
            messagebox.showwarning(
                "무결성 검사 결과",
                f"다음 문제가 발견되었습니다:\n\n{issue_text}"
            )
        else:
            messagebox.showinfo("무결성 검사 결과", "문제가 발견되지 않았습니다. ✅")
        
        self.log_message(f"무결성 검사 완료: {len(issues)}개 문제 발견")
    
    # === 클립보드 기능 ===
    
    def _copy_source_text(self) -> None:
        """원문 복사 - 다중 선택 지원"""
        chunk_indices = self._get_selected_chunk_indices()
        if not chunk_indices:
            return
        
        texts = []
        for idx in chunk_indices:
            if idx in self.source_chunks:
                texts.append(self.source_chunks[idx])
        
        if texts:
            combined = "\n\n".join(texts)
            self.frame.clipboard_clear()
            self.frame.clipboard_append(combined)
            if len(chunk_indices) == 1:
                self._update_status("원문이 클립보드에 복사되었습니다.")
            else:
                self._update_status(f"{len(texts)}개 원문이 클립보드에 복사되었습니다.")
    
    def _copy_translated_text(self) -> None:
        """번역문 복사 - 다중 선택 지원"""
        chunk_indices = self._get_selected_chunk_indices()
        if not chunk_indices:
            return
        
        texts = []
        for idx in chunk_indices:
            if idx in self.translated_chunks:
                texts.append(self.translated_chunks[idx])
        
        if texts:
            combined = "\n\n".join(texts)
            self.frame.clipboard_clear()
            self.frame.clipboard_append(combined)
            if len(chunk_indices) == 1:
                self._update_status("번역문이 클립보드에 복사되었습니다.")
            else:
                self._update_status(f"{len(texts)}개 번역문이 클립보드에 복사되었습니다.")
    
    # === BaseTab 추상 메서드 구현 ===
    
    def get_config(self) -> Dict[str, Any]:
        """현재 UI 상태에서 설정값 추출 (이 탭은 설정 저장 불필요)"""
        return {}
    
    def load_config(self, config: Dict[str, Any]) -> None:
        """설정값을 UI에 반영 (이 탭은 설정 로드 불필요)"""
        pass
