#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTG - 배치 번역기 GUI 엔트리포인트

이 파일은 GUI 애플리케이션의 진입점입니다.
실제 GUI 구현은 gui/ 모듈에 있습니다.
"""

import sys
from pathlib import Path

# 프로젝트 루트 디렉토리를 sys.path에 추가
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# GUI 모듈 임포트
try:
    from gui.main_window import run_gui
except ImportError as e:
    error_message = (
        f"GUI 모듈 임포트 오류: {e}.\n"
        "스크립트가 프로젝트 루트에서 실행되고 있는지, "
        "PYTHONPATH가 올바르게 설정되었는지 확인하세요.\n"
        "필수 모듈을 임포트할 수 없어 GUI를 시작할 수 없습니다."
    )
    print(error_message, file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        dummy_root = tk.Tk()
        dummy_root.withdraw()
        messagebox.showerror("치명적 임포트 오류", error_message)
        dummy_root.destroy()
    except Exception:
        pass
    sys.exit(1)


def main():
    """GUI 애플리케이션 시작"""
    run_gui()


if __name__ == "__main__":
    main()
