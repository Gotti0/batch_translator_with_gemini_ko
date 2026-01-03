#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qasync 통합 테스트 스크립트

테스트 항목:
1. @asyncSlot() 데코레이터 동작 확인
2. asyncio.sleep() 중 GUI 반응성 확인
3. Task 취소 기능 검증
"""

import asyncio
import sys
import time
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QProgressBar
)
from PySide6.QtCore import Qt
from qasync import QEventLoop, asyncSlot


class QAsyncIntegrationTest(QMainWindow):
    """qasync 통합 테스트 윈도우"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("qasync Integration Test")
        self.setGeometry(100, 100, 500, 400)
        
        central = QWidget()
        layout = QVBoxLayout()
        
        # 테스트 1: asyncSlot 데코레이터
        self.test1_label = QLabel("Test 1: @asyncSlot() decorator")
        layout.addWidget(self.test1_label)
        
        self.test1_button = QPushButton("Run Test 1 (5 seconds)")
        self.test1_button.clicked.connect(self.test_async_slot)
        layout.addWidget(self.test1_button)
        
        # 테스트 2: GUI 반응성
        self.test2_label = QLabel("Test 2: GUI Responsiveness during async task")
        layout.addWidget(self.test2_label)
        
        self.test2_progress = QProgressBar()
        self.test2_progress.setValue(0)
        layout.addWidget(self.test2_progress)
        
        self.test2_button = QPushButton("Run Test 2 (10 seconds)")
        self.test2_button.clicked.connect(self.test_gui_responsiveness)
        layout.addWidget(self.test2_button)
        
        # 테스트 3: Task 취소
        self.test3_label = QLabel("Test 3: Task cancellation")
        layout.addWidget(self.test3_label)
        
        self.test3_button = QPushButton("Start Task (will cancel in 3s)")
        self.test3_button.clicked.connect(self.test_task_cancellation)
        layout.addWidget(self.test3_button)
        
        self.test3_result = QLabel("Ready")
        layout.addWidget(self.test3_result)
        
        central.setLayout(layout)
        self.setCentralWidget(central)
        
        self.current_task = None
        self.test_results = []
    
    @asyncSlot()
    async def test_async_slot(self):
        """Test 1: @asyncSlot() 데코레이터 동작"""
        self.test1_label.setText("Test 1: Running...")
        self.test1_button.setEnabled(False)
        
        try:
            # 5초 비동기 작업
            for i in range(5):
                self.test1_label.setText(f"Test 1: Step {i+1}/5")
                await asyncio.sleep(1)
            
            self.test1_label.setText("Test 1: PASSED!")
            self.test_results.append(("Test 1 (@asyncSlot)", True))
        except Exception as e:
            self.test1_label.setText(f"Test 1: FAILED - {e}")
            self.test_results.append(("Test 1 (@asyncSlot)", False))
        finally:
            self.test1_button.setEnabled(True)
    
    @asyncSlot()
    async def test_gui_responsiveness(self):
        """Test 2: GUI 반응성 (asyncio.sleep 중 GUI 업데이트 가능)"""
        self.test2_label.setText("Test 2: Running (button should be clickable)...")
        self.test2_button.setEnabled(False)
        self.test2_progress.setValue(0)
        
        try:
            total_steps = 10
            for step in range(1, total_steps + 1):
                # ✅ 이 부분이 실행되는 동안 GUI는 반응
                self.test2_progress.setValue(int((step / total_steps) * 100))
                self.test2_label.setText(
                    f"Test 2: Step {step}/{total_steps} "
                    f"(try clicking other buttons)"
                )
                await asyncio.sleep(1)
            
            self.test2_label.setText("Test 2: PASSED! (GUI was responsive)")
            self.test_results.append(("Test 2 (GUI Responsiveness)", True))
        except Exception as e:
            self.test2_label.setText(f"Test 2: FAILED - {e}")
            self.test_results.append(("Test 2 (GUI Responsiveness)", False))
        finally:
            self.test2_button.setEnabled(True)
    
    @asyncSlot()
    async def test_task_cancellation(self):
        """Test 3: Task 취소 (3초 후 자동 취소)"""
        self.test3_button.setEnabled(False)
        self.test3_result.setText("Running... (will auto-cancel in 3s)")
        
        # 취소 Task 생성 (3초 후 현재 Task 취소)
        async def auto_cancel():
            await asyncio.sleep(3)
            if self.current_task and not self.current_task.done():
                self.current_task.cancel()
        
        cancel_task = asyncio.create_task(auto_cancel())
        self.current_task = asyncio.current_task()
        
        try:
            # 10초 작업 시작 (3초 후 취소됨)
            for i in range(10):
                self.test3_result.setText(f"Working: Step {i+1}/10")
                await asyncio.sleep(1)
            
            # 이 부분에 도달하지 않음 (3초에 취소됨)
            self.test3_result.setText("Test 3: FAILED (not cancelled)")
            self.test_results.append(("Test 3 (Task Cancellation)", False))
        except asyncio.CancelledError:
            # ✅ 예상된 동작
            self.test3_result.setText("Test 3: PASSED! (cancelled as expected)")
            self.test_results.append(("Test 3 (Task Cancellation)", True))
        finally:
            cancel_task.cancel()
            self.test3_button.setEnabled(True)
            self.current_task = None
    
    def closeEvent(self, event):
        """윈도우 종료 시"""
        print("\n" + "=" * 60)
        print("qasync Integration Test Results")
        print("=" * 60)
        
        for test_name, passed in self.test_results:
            status = "PASSED" if passed else "FAILED"
            print(f"  [{status}] {test_name}")
        
        total = len(self.test_results)
        passed = sum(1 for _, p in self.test_results if p)
        print("\n" + "-" * 60)
        print(f"Total: {passed}/{total} tests passed")
        print("=" * 60)
        
        event.accept()


def main():
    """메인 함수"""
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = QAsyncIntegrationTest()
    window.show()
    
    print("\nqasync Integration Test Window Started")
    print("=" * 60)
    print("Instructions:")
    print("1. Click 'Run Test 1' - should complete in 5 seconds")
    print("2. Click 'Run Test 2' - GUI should be responsive during 10-second task")
    print("3. Click 'Run Test 3' - task should auto-cancel after 3 seconds")
    print("\nClose the window when done to see results")
    print("=" * 60 + "\n")
    
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
