#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
최소 PySide6 + qasync 프로토타입 애플리케이션

목표:
1. PySide6 창이 뜨는지 확인
2. @asyncSlot() 데코레이터 동작 확인
3. asyncio.sleep() 중 GUI가 프리징되지 않는지 확인
"""

import asyncio
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QProgressBar
)
from PySide6.QtCore import Qt
from qasync import QEventLoop, asyncSlot


class TestWindow(QMainWindow):
    """최소 테스트 윈도우"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PySide6 + qasync Prototype")
        self.setGeometry(100, 100, 400, 300)
        
        # 중앙 위젯
        central = QWidget()
        layout = QVBoxLayout()
        
        # 레이블
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
        # 진행률 바
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # 시작 버튼
        self.start_button = QPushButton("Start 10-sec Task")
        self.start_button.clicked.connect(self.on_start_clicked)
        layout.addWidget(self.start_button)
        
        # 중단 버튼
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.on_cancel_clicked)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        
        central.setLayout(layout)
        self.setCentralWidget(central)
        
        # 현재 작업 Task
        self.current_task = None
    
    @asyncSlot()
    async def on_start_clicked(self):
        """시작 버튼 클릭 이벤트 (비동기)"""
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status_label.setText("Running...")
        self.progress_bar.setValue(0)
        
        # 작업 생성
        self.current_task = asyncio.create_task(self.long_running_task())
        
        try:
            await self.current_task
            self.status_label.setText("Completed!")
        except asyncio.CancelledError:
            self.status_label.setText("Cancelled!")
        finally:
            self.start_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.current_task = None
    
    def on_cancel_clicked(self):
        """중단 버튼 클릭 이벤트 (동기)"""
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
    
    async def long_running_task(self):
        """10초 걸리는 비동기 작업"""
        total_steps = 10
        
        for step in range(1, total_steps + 1):
            # GUI 업데이트 (논블로킹)
            self.status_label.setText(f"Step {step}/{total_steps}")
            self.progress_bar.setValue(int((step / total_steps) * 100))
            
            # 비동기 대기 (asyncio.sleep)
            # ✅ 이 동안 GUI는 반응성 있음
            await asyncio.sleep(1.0)
        
        self.status_label.setText("All steps completed!")
        self.progress_bar.setValue(100)


def main():
    """메인 함수"""
    # Qt Application 생성
    app = QApplication(sys.argv)
    
    # asyncio Event Loop를 Qt Event Loop와 통합
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    # 윈도우 생성 및 표시
    window = TestWindow()
    window.show()
    
    # 이벤트 루프 실행
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
