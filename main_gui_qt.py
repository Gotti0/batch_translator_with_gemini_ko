#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTG - 배치 번역기 (PySide6/qasync 엔트리포인트)
기존 tkinter GUI와 병행 제공. 준비가 되면 이 엔트리포인트를 사용하세요.
"""

from __future__ import annotations

import asyncio
import sys

from PySide6 import QtWidgets
import qasync
import qdarktheme

from gui_qt.main_window_qt import BatchTranslatorWindow


def main() -> None:
    """PySide6 GUI 시작"""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    qdarktheme.setup_theme(theme="dark", custom_colors={"primary": "#29B6F6"})
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = BatchTranslatorWindow(loop=loop)
    window.show()

    # 모든 창이 닫히면 이벤트 루프 정지
    app.lastWindowClosed.connect(loop.stop)

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
