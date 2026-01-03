# test/test_gemini_batch_client.py
import unittest
from unittest.mock import patch, MagicMock

# 테스트 대상 모듈 임포트
# google.genai가 설치되지 않았을 경우를 대비하여 try-except 처리
try:
    from infrastructure.gemini_batch_client import GeminiBatchClient
    from google.genai import types
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False

# google.genai 라이브러리가 없는 환경에서는 테스트를 스킵합니다.
@unittest.skipIf(not GOOGLE_GENAI_AVAILABLE, "google-genai library not found, skipping GeminiBatchClient tests")
class TestGeminiBatchClient(unittest.TestCase):

    def setUp(self):
        """테스트 실행 전, genai 클라이언트를 모의(mock) 객체로 설정합니다."""
        self.api_key = "test_api_key"
        
        # genai.Client를 모의 객체로 패치합니다.
        self.client_patcher = patch('google.genai.Client')
        self.mock_client_constructor = self.client_patcher.start()
        
        # Client() 생성자가 반환할 모의 인스턴스를 설정합니다.
        self.mock_client_instance = MagicMock()
        self.mock_client_constructor.return_value = self.mock_client_instance
        
        # 테스트 대상 클라이언트 인스턴스 생성
        self.batch_client = GeminiBatchClient(api_key=self.api_key)

    def tearDown(self):
        """테스트 실행 후 패치를 중지합니다."""
        self.client_patcher.stop()

    def test_initialization(self):
        """클라이언트가 올바른 API 키로 초기화되는지 테스트합니다."""
        self.mock_client_constructor.assert_called_once_with(api_key=self.api_key)
        self.assertIsNotNone(self.batch_client.client)

    def test_upload_file(self):
        """파일 업로드 메서드가 SDK의 upload 메서드를 올바르게 호출하는지 테스트합니다."""
        file_path = "test.jsonl"
        mime_type = "application/json"
        
        # 모의 파일 객체 설정
        mock_uploaded_file = MagicMock(spec=types.File)
        mock_uploaded_file.name = "files/mock-file-name"
        self.mock_client_instance.files.upload.return_value = mock_uploaded_file

        # 메서드 실행
        result = self.batch_client.upload_file(file_path, mime_type)

        # SDK 메서드가 올바른 인자와 함께 호출되었는지 확인
        self.mock_client_instance.files.upload.assert_called_once()
        call_args = self.mock_client_instance.files.upload.call_args
        self.assertEqual(call_args.kwargs['file'], file_path)
        self.assertIsInstance(call_args.kwargs['config'], types.UploadFileConfig)
        self.assertEqual(call_args.kwargs['config'].mime_type, mime_type)
        
        # 반환값이 모의 객체와 동일한지 확인
        self.assertEqual(result, mock_uploaded_file)

    def test_create_batch_job(self):
        """배치 작업 생성 메서드가 SDK의 create 메서드를 올바르게 호출하는지 테스트합니다."""
        model_id = "gemini-1.5-flash"
        input_file_name = "files/mock-input-file"
        display_name = "test-job"

        # 모의 배치 작업 객체 설정
        mock_batch_job = MagicMock(spec=types.BatchJob)
        mock_batch_job.name = "batches/mock-job-name"
        self.mock_client_instance.batches.create.return_value = mock_batch_job

        # 메서드 실행
        result = self.batch_client.create_batch_job(model_id, input_file_name, display_name)

        # SDK 메서드가 올바른 인자와 함께 호출되었는지 확인
        self.mock_client_instance.batches.create.assert_called_once_with(
            model=f"models/{model_id}",
            src=input_file_name,
            config={'display_name': display_name}
        )
        
        # 반환값이 모의 객체와 동일한지 확인
        self.assertEqual(result, mock_batch_job)

    def test_get_batch_job(self):
        """배치 작업 조회 메서드가 SDK의 get 메서드를 올바르게 호출하는지 테스트합니다."""
        job_name = "batches/mock-job-name"
        
        # 메서드 실행
        self.batch_client.get_batch_job(job_name)

        # SDK 메서드가 올바른 인자와 함께 호출되었는지 확인
        self.mock_client_instance.batches.get.assert_called_once_with(name=job_name)

    def test_download_result_file(self):
        """결과 파일 다운로드 메서드가 SDK의 download 메서드를 올바르게 호출하는지 테스트합니다."""
        result_file_name = "files/mock-result-file"
        mock_content = b"This is the result."
        self.mock_client_instance.files.download.return_value = mock_content

        # 메서드 실행
        result = self.batch_client.download_result_file(result_file_name)

        # SDK 메서드가 올바른 인자와 함께 호출되었는지 확인
        self.mock_client_instance.files.download.assert_called_once_with(file=result_file_name)
        
        # 반환값이 모의 콘텐츠와 동일한지 확인
        self.assertEqual(result, mock_content)

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
