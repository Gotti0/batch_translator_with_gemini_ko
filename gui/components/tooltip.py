"""
Tooltip 컴포넌트

위젯 위에 마우스를 올렸을 때 툴팁을 표시하는 클래스입니다.
"""

import tkinter as tk


class Tooltip:
    """
    위젯 위에 마우스를 올렸을 때 툴팁을 표시하는 클래스입니다.
    wm_overrideredirect(True)를 사용하여 테두리 없는 팝업을 생성합니다.
    """
    
    def __init__(self, widget: tk.Widget, text: str):
        """
        Args:
            widget: 툴팁을 표시할 대상 위젯
            text: 툴팁에 표시할 텍스트
        """
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.id = None  # Timer ID for scheduling
        # Store mouse coordinates from <Enter> event
        self.enter_x_root = 0
        self.enter_y_root = 0
        
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave)  # 클릭 시에도 툴팁 숨김

    def enter(self, event=None):
        """마우스가 위젯 위로 진입했을 때"""
        if event:  # Store mouse position when entering the widget
            self.enter_x_root = event.x_root
            self.enter_y_root = event.y_root
        self.schedule()

    def leave(self, event=None):
        """마우스가 위젯을 벗어났을 때"""
        self.unschedule()
        self.hidetip()

    def schedule(self):
        """툴팁 표시 스케줄링"""
        self.unschedule()
        # 툴팁 표시 전 약간의 지연 (0.5초)
        self.id = self.widget.after(500, self.showtip)

    def unschedule(self):
        """스케줄링된 툴팁 표시 취소"""
        id = self.id
        self.id = None
        if id:
            self.widget.after_cancel(id)

    def showtip(self, event=None):
        """툴팁 표시"""
        if not self.widget.winfo_exists():
            self.hidetip()
            return

        # 이전 툴팁 창이 있다면 파괴
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None
        
        # Create new tooltip window
        if not self.widget.winfo_exists():  # Double check, as time might have passed
            self.hidetip()
            return

        # Position the tooltip relative to the mouse cursor's position at <Enter>
        final_tooltip_x = self.enter_x_root + 15  # Offset from cursor
        final_tooltip_y = self.enter_y_root + 10  # Offset from cursor

        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True) 
        self.tooltip_window.wm_geometry(f"+{int(final_tooltip_x)}+{int(final_tooltip_y)}")
        
        label = tk.Label(
            self.tooltip_window, 
            text=self.text, 
            justify='left',
            background="#ffffe0", 
            relief='solid', 
            borderwidth=1,
            font=("tahoma", "8", "normal")
        )
        label.pack(ipadx=1, ipady=1)

    def hidetip(self):
        """툴팁 숨기기"""
        tw = self.tooltip_window
        self.tooltip_window = None
        if tw:
            tw.destroy()
