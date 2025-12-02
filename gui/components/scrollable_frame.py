"""
ScrollableFrame 컴포넌트

스크롤 가능한 프레임을 생성하는 클래스입니다.
"""

import tkinter as tk
import ttkbootstrap as ttk


class ScrollableFrame:
    """스크롤 가능한 프레임을 생성하는 클래스"""
    
    def __init__(self, parent: tk.Widget, height: int = None):
        """
        Args:
            parent: 부모 위젯
            height: 프레임의 높이 (선택사항)
        """
        # 메인 프레임 생성
        self.main_frame = ttk.Frame(parent)
        
        # Canvas와 Scrollbar 생성
        self.canvas = tk.Canvas(self.main_frame, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self.main_frame, 
            orient="vertical", 
            command=self.canvas.yview
        )
        
        # 스크롤 가능한 내용을 담을 프레임
        self.scrollable_frame = ttk.Frame(self.canvas)
        
        # 스크롤바 설정
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # 프레임이 변경될 때마다 스크롤 영역 업데이트
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        # Canvas에 프레임 추가
        self.canvas_frame = self.canvas.create_window(
            (0, 0), 
            window=self.scrollable_frame, 
            anchor="nw"
        )
        
        # Canvas 크기 변경 시 내부 프레임 크기 조정
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # 마우스 휠 스크롤 바인딩
        self._bind_mouse_wheel()
        
        # 위젯 배치
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # 높이 설정 (선택사항)
        if height:
            self.canvas.configure(height=height)
    
    def _on_canvas_configure(self, event):
        """Canvas 크기 변경 시 내부 프레임 너비 조정"""
        self.canvas.itemconfig(self.canvas_frame, width=event.width)
    
    def _bind_mouse_wheel(self):
        """마우스 휠 스크롤 이벤트 바인딩"""
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        def _bind_to_mousewheel(event):
            self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        self.main_frame.bind('<Enter>', _bind_to_mousewheel)
    
    def pack(self, **kwargs):
        """메인 프레임 pack"""
        self.main_frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        """메인 프레임 grid"""
        self.main_frame.grid(**kwargs)
