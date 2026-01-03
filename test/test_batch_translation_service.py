# test/test_batch_translation_service.py
import unittest
from unittest.mock import MagicMock, patch
import time

# 테스트 대상 모듈 임포트
from domain.batch_translation_service import BatchTranslationService
from infrastructure.gemini_batch_client import GeminiBatchClient

# google.genai.types를 모의 처리하기 위한 가짜 클래스
class MockFile:
    def __init__(self, name):
        self.name = name

class MockJob:
    def __init__(self, name, state, dest_file_name=None, error=None):
        self.name = name
        self.state = MagicMock()
        self.state.name = state
        self.dest = MagicMock()
        self.dest.file_name = dest_file_name
        self.error = error

class TestBatchTranslationService(unittest.TestCase):

    def setUp(self):
        """테스트 실행 전, GeminiBatchClient를 모의 객체로 설정합니다."""
        self.mock_batch_client = MagicMock(spec=GeminiBatchClient)
        self.service = BatchTranslationService(batch_client=self.mock_batch_client)

    def test_start_new_translation_job(self):
        """
        새로운 번역 작업을 시작하는 로직을 테스트합니다.
        파일 업로드 -> 배치 작업 생성의 흐름을 확인합니다.
        """
        requests_file = "requests.jsonl"
        model_id = "test-model"
        
        # 모의 객체의 반환값 설정
        mock_uploaded_file = MockFile(name="files/uploaded-file")
        self.mock_batch_client.upload_file.return_value = mock_uploaded_file
        
        mock_batch_job = MockJob(name="batches/new-job", state="JOB_STATE_CREATING")
        self.mock_batch_client.create_batch_job.return_value = mock_batch_job

        # 메서드 실행
        job_name = self.service.start_new_translation_job(requests_file, model_id)

        # 내부 메서드 호출 확인
        self.mock_batch_client.upload_file.assert_called_once_with(file_path=requests_file)
        self.mock_batch_client.create_batch_job.assert_called_once_with(
            model_id=model_id,
            input_file_name=mock_uploaded_file.name
        )
        
        # 반환된 작업 이름 확인
        self.assertEqual(job_name, mock_batch_job.name)

    @patch('time.sleep', return_value=None) # time.sleep을 비활성화
    def test_monitor_job_status(self, mock_sleep):
        """
        작업 상태를 모니터링하는 로직을 테스트합니다.
        상태가 바뀜에 따라 올바른 값을 yield하는지 확인합니다.
        """
        job_name = "batches/monitoring-job"
        
        # 상태 변화 시나리오 설정
        running_job = MockJob(name=job_name, state="JOB_STATE_RUNNING")
        succeeded_job = MockJob(name=job_name, state="JOB_STATE_SUCCEEDED")
        
        self.mock_batch_client.get_batch_job.side_effect = [running_job, succeeded_job]

        # 제너레이터 실행 및 결과 확인
        status_generator = self.service.monitor_job_status(job_name, polling_interval=1)
        
        state1, name1 = next(status_generator)
        self.assertEqual(state1, "JOB_STATE_RUNNING")
        self.assertEqual(name1, job_name)
        
        state2, name2 = next(status_generator)
        self.assertEqual(state2, "JOB_STATE_SUCCEEDED")
        self.assertEqual(name2, job_name)

        # 루프가 종료되었으므로 StopIteration이 발생해야 함
        with self.assertRaises(StopIteration):
            next(status_generator)

        # get_batch_job이 두 번 호출되었는지 확인
        self.assertEqual(self.mock_batch_client.get_batch_job.call_count, 2)
        # time.sleep이 한 번 호출되었는지 확인
        mock_sleep.assert_called_once()

    def test_get_translation_results(self):
        """
        완료된 작업의 결과를 가져와 파싱하는 로직을 테스트합니다.
        """
        job_name = "batches/completed-job"
        result_file_name = "files/result-file"
        
        # 모의 객체 설정
        succeeded_job = MockJob(name=job_name, state="JOB_STATE_SUCCEEDED", dest_file_name=result_file_name)
        self.mock_batch_client.get_batch_job.return_value = succeeded_job
        
        # 다운로드될 모의 jsonl 콘텐츠
        mock_jsonl_content = (
            '{"key": "paragraph_1", "request": {}, "response": {"candidates": [{"content": {"parts": [{"text": "Translated text 1."}]}}]}}\n'
            '{"key": "paragraph_2", "request": {}, "response": {"candidates": [{"content": {"parts": [{"text": "Translated text 2."}]}}]}}\n'
        )
        self.mock_batch_client.download_result_file.return_value = mock_jsonl_content.encode('utf-8')

        # 메서드 실행
        translations = self.service.get_translation_results(job_name)

        # 내부 메서드 호출 확인
        self.mock_batch_client.get_batch_job.assert_called_once_with(job_name=job_name)
        self.mock_batch_client.download_result_file.assert_called_once_with(result_file_name)

        # 파싱 결과 확인
        expected_translations = {
            1: "Translated text 1.",
            2: "Translated text 2."
        }
        self.assertEqual(translations, expected_translations)

    def test_get_translation_results_for_failed_job(self):
        """작업이 성공하지 않았을 때 빈 결과를 반환하는지 테스트합니다."""
        job_name = "batches/failed-job"
        
        failed_job = MockJob(name=job_name, state="JOB_STATE_FAILED")
        self.mock_batch_client.get_batch_job.return_value = failed_job

        # 메서드 실행
        translations = self.service.get_translation_results(job_name)

        # 결과가 빈 딕셔너리인지 확인
        self.assertEqual(translations, {})
        # 파일 다운로드가 호출되지 않았는지 확인
        self.mock_batch_client.download_result_file.assert_not_called()

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
