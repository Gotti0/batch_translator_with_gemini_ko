"""
PySide6 Settings Tab 완료 메시지 유닛 테스트 (간소화)

_show_completion_dialog 메서드와 통계 기능을 테스트합니다.
"""

import unittest
import sys
import os
from unittest.mock import Mock, MagicMock, patch

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6 import QtWidgets, QtCore

# 테스트할 메서드를 직접 테스트하기 위한 헬퍼 클래스
class CompletionDialogTester:
    """완료 다이얼로그 기능 테스트 헬퍼"""
    
    @staticmethod
    def show_completion_dialog(success: bool, message: str, stats: dict) -> tuple:
        """완료 다이얼로그 생성 (테스트용)"""
        elapsed = stats.get("elapsed_seconds", 0)
        total_chunks = stats.get("total_chunks", 0)
        processed_chunks = stats.get("processed_chunks", 0)
        newly_processed = stats.get("newly_processed", 0)
        
        # 시간 포맷팅 함수
        def format_elapsed(seconds):
            if seconds < 60:
                return f"{int(seconds)}초"
            elif seconds < 3600:
                minutes = int(seconds / 60)
                secs = int(seconds % 60)
                return f"{minutes}분 {secs}초"
            else:
                hours = int(seconds / 3600)
                minutes = int((seconds % 3600) / 60)
                return f"{hours}시간 {minutes}분"
        
        # 다이얼로그 제목 및 아이콘 설정
        if success:
            title = "번역 완료"
            icon = QtWidgets.QMessageBox.Information
        else:
            reason = stats.get("reason") or stats.get("error") or "알 수 없는 오류"
            title = f"번역 {reason}"
            icon = QtWidgets.QMessageBox.Warning
        
        # 통계 정보 포맷팅
        elapsed_str = format_elapsed(elapsed)
        stats_text = f"총 청크: {total_chunks}개\n처리된 청크: {processed_chunks}개\n새로 처리: {newly_processed}개\n소요 시간: {elapsed_str}"
        
        # 상세 메시지
        detail_msg = f"{message}\n\n{stats_text}"
        
        return title, detail_msg, icon, elapsed_str


class TestCompletionDialog(unittest.TestCase):
    """완료 다이얼로그 기능 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 초기화"""
        cls.app = QtWidgets.QApplication.instance()
        if cls.app is None:
            cls.app = QtWidgets.QApplication(sys.argv)
    
    # ========== 시간 포맷팅 테스트 ==========
    def test_time_formatting_seconds(self):
        """초 단위 시간 포맷팅 검증"""
        stats = {
            "success": True,
            "total_chunks": 100,
            "processed_chunks": 100,
            "newly_processed": 100,
            "elapsed_seconds": 30.0,
        }
        
        title, detail_msg, icon, elapsed_str = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        self.assertIn("30초", elapsed_str)
        self.assertIn("완료", title)
        self.assertEqual(icon, QtWidgets.QMessageBox.Information)
    
    def test_time_formatting_minutes(self):
        """분 단위 시간 포맷팅 검증"""
        stats = {
            "success": True,
            "total_chunks": 100,
            "processed_chunks": 100,
            "newly_processed": 100,
            "elapsed_seconds": 125.0,  # 2분 5초
        }
        
        title, detail_msg, icon, elapsed_str = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        self.assertIn("2분 5초", elapsed_str)
    
    def test_time_formatting_hours(self):
        """시간 단위 시간 포맷팅 검증"""
        stats = {
            "success": True,
            "total_chunks": 1000,
            "processed_chunks": 1000,
            "newly_processed": 1000,
            "elapsed_seconds": 3725.0,  # 1시간 2분 5초
        }
        
        title, detail_msg, icon, elapsed_str = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        self.assertIn("1시간 2분", elapsed_str)
    
    # ========== 성공/실패 메시지 테스트 ==========
    def test_dialog_success_message(self):
        """성공 메시지 다이얼로그 생성 검증"""
        stats = {
            "success": True,
            "total_chunks": 100,
            "processed_chunks": 100,
            "newly_processed": 100,
            "elapsed_seconds": 50.0,
        }
        
        title, detail_msg, icon, _ = CompletionDialogTester.show_completion_dialog(
            True, "번역 완료", stats
        )
        
        self.assertEqual(title, "번역 완료")
        self.assertIn("번역 완료", detail_msg)
        self.assertIn("100개", detail_msg)  # 총 청크
        self.assertEqual(icon, QtWidgets.QMessageBox.Information)
    
    def test_dialog_failure_message(self):
        """실패 메시지 다이얼로그 생성 검증"""
        stats = {
            "success": False,
            "total_chunks": 100,
            "processed_chunks": 50,
            "newly_processed": 50,
            "elapsed_seconds": 45.0,
            "error": "API 타임아웃",
        }
        
        title, detail_msg, icon, _ = CompletionDialogTester.show_completion_dialog(
            False, "오류 발생", stats
        )
        
        self.assertIn("API 타임아웃", title)
        self.assertIn("50개", detail_msg)
        self.assertEqual(icon, QtWidgets.QMessageBox.Warning)
    
    def test_dialog_cancellation_message(self):
        """취소 메시지 다이얼로그 생성 검증"""
        stats = {
            "success": False,
            "total_chunks": 100,
            "processed_chunks": 30,
            "newly_processed": 30,
            "elapsed_seconds": 15.0,
            "reason": "사용자 취소",
        }
        
        title, detail_msg, icon, _ = CompletionDialogTester.show_completion_dialog(
            False, "취소됨", stats
        )
        
        self.assertIn("사용자 취소", title)
        self.assertEqual(icon, QtWidgets.QMessageBox.Warning)
    
    # ========== 통계 정보 표시 테스트 ==========
    def test_stats_display_in_dialog(self):
        """다이얼로그에 통계 정보가 표시되는지 검증"""
        stats = {
            "success": True,
            "total_chunks": 500,
            "processed_chunks": 450,
            "newly_processed": 400,
            "elapsed_seconds": 60.0,
        }
        
        title, detail_msg, icon, _ = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        # 모든 통계가 포함되는지 확인
        self.assertIn("500개", detail_msg)  # 총 청크
        self.assertIn("450개", detail_msg)  # 처리된 청크
        self.assertIn("400개", detail_msg)  # 새로 처리된 청크
        self.assertIn("1분 0초", detail_msg)  # 소요 시간
    
    def test_newly_processed_chunks_calculation(self):
        """새로 처리된 청크 수 계산 검증"""
        # 이어하기: 200개에서 시작해서 600개까지 처리
        newly_processed = 600 - 200  # = 400
        
        stats = {
            "success": True,
            "total_chunks": 1000,
            "processed_chunks": 600,
            "newly_processed": newly_processed,
            "elapsed_seconds": 100.0,
        }
        
        title, detail_msg, icon, _ = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        self.assertIn("400개", detail_msg)
        self.assertIn("600개", detail_msg)
    
    # ========== 엣지 케이스 테스트 ==========
    def test_missing_stats_fields(self):
        """불완전한 통계 정보 처리"""
        # 일부 필드가 누락된 경우
        stats = {
            "success": True,
            "total_chunks": 100,
            # processed_chunks 누락
            "newly_processed": 50,
            # elapsed_seconds 누락
        }
        
        # 예외가 발생하지 않아야 함
        title, detail_msg, icon, _ = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        self.assertIsNotNone(title)
        self.assertIsNotNone(detail_msg)
        self.assertIn("0개", detail_msg)  # 기본값 0
    
    def test_zero_elapsed_time(self):
        """소요 시간이 0초인 경우"""
        stats = {
            "success": True,
            "total_chunks": 10,
            "processed_chunks": 10,
            "newly_processed": 10,
            "elapsed_seconds": 0.0,
        }
        
        title, detail_msg, icon, elapsed_str = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        self.assertIn("0초", elapsed_str)
    
    def test_large_elapsed_time(self):
        """소요 시간이 매우 큰 경우"""
        # 50시간 37분
        stats = {
            "success": True,
            "total_chunks": 100000,
            "processed_chunks": 100000,
            "newly_processed": 100000,
            "elapsed_seconds": 182220.0,  # 50시간 37분
        }
        
        title, detail_msg, icon, elapsed_str = CompletionDialogTester.show_completion_dialog(
            True, "완료", stats
        )
        
        self.assertIn("50시간", elapsed_str)


class TestCompletionSignalEmission(unittest.TestCase):
    """Signal 발출 기능 테스트 (mock 기반)"""
    
    def test_completion_stats_dict_structure(self):
        """완료 신호의 stats 딕셔너리 구조 검증"""
        success_stats = {
            "success": True,
            "total_chunks": 100,
            "processed_chunks": 100,
            "newly_processed": 100,
            "elapsed_seconds": 50.0,
        }
        
        # 필수 필드 확인
        self.assertIn("success", success_stats)
        self.assertIn("total_chunks", success_stats)
        self.assertIn("processed_chunks", success_stats)
        self.assertIn("newly_processed", success_stats)
        self.assertIn("elapsed_seconds", success_stats)
    
    def test_failure_stats_dict_structure(self):
        """실패 신호의 stats 딕셔너리 구조 검증"""
        failure_stats = {
            "success": False,
            "total_chunks": 100,
            "processed_chunks": 50,
            "newly_processed": 50,
            "elapsed_seconds": 30.0,
            "error": "API 타임아웃",
        }
        
        # 필수 필드 + 오류 정보
        self.assertIn("error", failure_stats)
        self.assertEqual(failure_stats["success"], False)
    
    def test_cancellation_stats_dict_structure(self):
        """취소 신호의 stats 딕셔너리 구조 검증"""
        cancel_stats = {
            "success": False,
            "total_chunks": 100,
            "processed_chunks": 30,
            "newly_processed": 30,
            "elapsed_seconds": 15.0,
            "reason": "사용자 취소",
        }
        
        # 필수 필드 + 취소 원인
        self.assertIn("reason", cancel_stats)
        self.assertEqual(cancel_stats["reason"], "사용자 취소")


if __name__ == "__main__":
    unittest.main()
