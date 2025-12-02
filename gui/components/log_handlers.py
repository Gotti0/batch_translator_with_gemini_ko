"""
로그 핸들러 컴포넌트

GUI에서 로그를 표시하기 위한 핸들러 클래스들입니다.
"""

import tkinter as tk
from tkinter import scrolledtext
import logging
import io
import time


class GuiLogHandler(logging.Handler):
    """
    로깅 메시지를 Tkinter ScrolledText 위젯으로 리다이렉션하는 핸들러.
    스레드 안전성을 위해 widget.after()를 사용합니다.
    사용자 요청에 따라 '⚠️'(품질 이슈) 또는 ERROR 레벨 이상의 로그만 출력하도록 필터링합니다.
    """
    
    def __init__(self, text_widget: scrolledtext.ScrolledText):
        """
        Args:
            text_widget: 로그를 표시할 ScrolledText 위젯
        """
        super().__init__()
        self.text_widget = text_widget
        
        # 로그 레벨별 태그 설정
        self.text_widget.tag_config("INFO", foreground="black")
        self.text_widget.tag_config("DEBUG", foreground="gray")
        self.text_widget.tag_config("WARNING", foreground="#FF8C00")  # 진한 주황색
        self.text_widget.tag_config("ERROR", foreground="red", font=('Helvetica', 9, 'bold'))
        self.text_widget.tag_config(
            "CRITICAL", 
            foreground="red", 
            background="yellow", 
            font=('Helvetica', 9, 'bold')
        )
        self.text_widget.tag_config("TQDM", foreground="blue")

    def emit(self, record: logging.LogRecord):
        """로그 레코드 처리"""
        try:
            msg = self.format(record)
            level_tag = record.levelname
            
            # 필터링: 품질 이슈(⚠️), 청크 전체 처리 완료(🎯 청크 ... 전체 처리 완료) 또는 에러 이상만 허용
            is_chunk_complete_log = "🎯" in msg and "전체 처리 완료" in msg
            if "⚠️" not in msg and not is_chunk_complete_log and record.levelno < logging.ERROR:
                return
            
            def append_message_to_widget():
                try:
                    if not self.text_widget.winfo_exists(): 
                        return
                    
                    current_state = self.text_widget.cget("state") 
                    self.text_widget.configure(state='normal') 
                    self.text_widget.insert(tk.END, msg + "\n", level_tag)
                    self.text_widget.configure(state=current_state) 
                    self.text_widget.see(tk.END)
                except tk.TclError:
                    pass 

            if self.text_widget.winfo_exists():
                self.text_widget.after(0, append_message_to_widget)
        except Exception:
            self.handleError(record)


class TqdmToTkinter(io.StringIO):
    """
    TQDM 진행률 출력을 Tkinter ScrolledText 위젯으로 리다이렉션하는 스트림.
    """
    
    def __init__(self, widget: scrolledtext.ScrolledText):
        """
        Args:
            widget: 출력을 표시할 ScrolledText 위젯
        """
        super().__init__()
        self.widget = widget
        self.widget.tag_config("TQDM", foreground="green")

    def write(self, buf: str):
        """버퍼에 쓰기"""
        stripped_buf = buf.strip()
        if not stripped_buf:
            return

        def append_to_widget():
            if not self.widget.winfo_exists():
                return
            
            timestamp = time.strftime('%H:%M:%S')
            log_message = f"{timestamp} - {stripped_buf}\n"
            
            current_state = self.widget.cget("state")
            self.widget.config(state=tk.NORMAL)
            self.widget.insert(tk.END, log_message, "TQDM")
            self.widget.config(state=current_state) 
            self.widget.see(tk.END)
            
        if self.widget.winfo_exists(): 
            self.widget.after(0, append_to_widget)

    def flush(self):
        """버퍼 플러시 (no-op)"""
        pass
