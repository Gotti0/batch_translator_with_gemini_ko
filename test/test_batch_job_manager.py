# test/test_batch_job_manager.py
import unittest
from unittest.mock import MagicMock, patch, mock_open
import os
import json

# 테스트 대상 모듈 임포트
from app.batch_job_manager import BatchJobManager, STATE_FILE, REQUESTS_FILE
from domain.batch_translation_service import BatchTranslationService
from utils.request_builder import BatchRequestBuilder

class TestBatchJobManager(unittest.TestCase):

    def setUp(self):
        """테스트 실행 전, 서비스와 빌더를 모의 객체로 설정합니다."""
        self.mock_service = MagicMock(spec=BatchTranslationService)
        self.mock_builder = MagicMock(spec=BatchRequestBuilder)
        self.mock_builder.model_name = "models/test-model"
        self.mock_log_callback = MagicMock()

        self.job_manager = BatchJobManager(
            service=self.mock_service,
            builder=self.mock_builder,
            log_callback=self.mock_log_callback
        )

        # 테스트용 파일 경로
        self.source_file = "source.txt"
        self.results_file = "results.txt"
        self.job_name = "batches/test-job"

    def tearDown(self):
        """테스트 후 상태 파일을 삭제합니다."""
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    @patch('app.batch_job_manager.BatchJobManager._monitor_and_process_results')
    @patch('app.batch_job_manager.BatchJobManager.save_state')
    def test_start_new_job_flow(self, mock_save_state, mock_monitor):
        """
        start_new_job이 올바른 순서로 메서드들을 호출하는지 테스트합니다.
        """
        self.mock_service.start_new_translation_job.return_value = self.job_name

        self.job_manager.start_new_job(self.source_file, self.results_file)

        self.mock_builder.create_requests_from_file.assert_called_once_with(self.source_file, REQUESTS_FILE)
        self.mock_service.start_new_translation_job.assert_called_once_with(REQUESTS_FILE, "test-model")
        mock_save_state.assert_called_once_with(self.job_name, self.source_file, self.results_file, 'RUNNING')
        mock_monitor.assert_called_once_with(self.job_name, self.results_file)

    @patch('app.batch_job_manager.BatchJobManager._save_results_to_file')
    @patch('app.batch_job_manager.BatchJobManager.save_state')
    def test_monitor_and_process_results_success(self, mock_save_state, mock_save_results):
        """
        작업 모니터링 및 성공적 결과 처리를 테스트합니다.
        """
        self.job_manager.state = {'source_file': self.source_file} # 초기 상태 설정
        self.mock_service.monitor_job_status.return_value = iter([('JOB_STATE_SUCCEEDED', self.job_name)])
        self.mock_service.get_translation_results.return_value = {1: "Result 1"}

        self.job_manager._monitor_and_process_results(self.job_name, self.results_file)

        mock_save_results.assert_called_once_with({1: "Result 1"}, self.results_file)
        mock_save_state.assert_called_once_with(self.job_name, self.source_file, self.results_file, 'SUCCEEDED')

    @patch('app.batch_job_manager.BatchJobManager._monitor_and_process_results')
    @patch('app.batch_job_manager.BatchJobManager.load_state')
    def test_resume_job(self, mock_load_state, mock_monitor):
        """
        기존 작업을 이어받아 재개하는 흐름을 테스트합니다.
        """
        # 상태 파일이 존재하고, 'RUNNING' 상태라고 가정
        mock_load_state.return_value = {
            'job_name': self.job_name,
            'results_file': self.results_file,
            'status': 'RUNNING'
        }
        # load_state가 호출된 후 job_manager의 state 속성도 설정
        self.job_manager.state = mock_load_state.return_value

        # 메서드 실행
        self.job_manager.resume_job()

        # 1. 상태를 로드했는지 확인
        mock_load_state.assert_called_once()
        
        # 2. 모니터링을 재개했는지 확인
        mock_monitor.assert_called_once_with(self.job_name, self.results_file)

    def test_save_and_load_state(self):
        """상태 저장 및 로드 기능을 테스트합니다."""
        status = "TESTING"
        self.job_manager.save_state(self.job_name, self.source_file, self.results_file, status)

        # 파일이 생성되었는지 확인
        self.assertTrue(os.path.exists(STATE_FILE))

        # 새로운 JobManager 인스턴스로 상태를 로드
        new_job_manager = BatchJobManager(self.mock_service, self.mock_builder, self.mock_log_callback)
        loaded_state = new_job_manager.load_state()

        self.assertIsNotNone(loaded_state)
        self.assertEqual(loaded_state['job_name'], self.job_name)
        self.assertEqual(loaded_state['status'], status)

    def test_clear_state(self):
        """상태 초기화(파일 ��제) 기능을 테스트합니다."""
        # 먼저 상태 파일을 생성
        with open(STATE_FILE, 'w') as f:
            f.write("test")
        
        self.assertTrue(os.path.exists(STATE_FILE))

        # 초기화 메서드 실행
        self.job_manager.clear_state()

        # 파일이 삭제되었는지 확인
        self.assertFalse(os.path.exists(STATE_FILE))
        # 내부 상태도 초기화되었는지 확인
        self.assertEqual(self.job_manager.state, {})

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
