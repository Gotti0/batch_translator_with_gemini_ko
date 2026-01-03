# -*- coding: utf-8 -*-
import pytest
from unittest.mock import patch, MagicMock, mock_open

# google.genai 모듈이 설치되지 않았을 수 있으므로, 테스트 실행 전에 모의 처리합니다.
# 이렇게 하면 실제 모듈이 없어도 테스트를 정의하고 실행할 수 있습니다.
mock_genai = MagicMock()
mock_genai.types = MagicMock()

# 모듈 경로에 가짜 모듈을 등록
import sys
sys.modules['google.genai'] = mock_genai
sys.modules['google.genai.types'] = mock_genai.types

# 이제 테스트 대상 클래스를 임포트합니다.
from infrastructure.gemini_batch_client import GeminiBatchClient

@pytest.fixture(autouse=True)
def reset_mocks():
    """각 테스트 실행 전에 모의 객체를 초기화합니다."""
    mock_genai.reset_mock()
    mock_genai.types.reset_mock()

@patch('infrastructure.gemini_batch_client.genai.Client')
def test_init_with_api_key(mock_client_constructor):
    """API 키가 직접 제공될 때 클라이언트가 올바르게 초기화되는지 테스트합니다."""
    api_key = "test_api_key"
    client = GeminiBatchClient(api_key=api_key)
    mock_client_constructor.assert_called_once_with(api_key=api_key)
    assert client.client is not None

@patch('infrastructure.gemini_batch_client.os.getenv')
@patch('infrastructure.gemini_batch_client.load_dotenv')
@patch('infrastructure.gemini_batch_client.genai.Client')
def test_init_with_env_var(mock_client_constructor, mock_load_dotenv, mock_getenv):
    """환경 변수에서 API 키를 로드하여 클라이언트를 초기화하는지 테스트합니다."""
    env_api_key = "env_api_key"
    mock_getenv.return_value = env_api_key
    
    client = GeminiBatchClient()
    
    mock_load_dotenv.assert_called_once()
    mock_getenv.assert_called_once_with("GEMINI_API_KEY")
    mock_client_constructor.assert_called_once_with(api_key=env_api_key)
    assert client.client is not None

@patch('infrastructure.gemini_batch_client.os.getenv', return_value=None)
@patch('infrastructure.gemini_batch_client.load_dotenv')
def test_init_no_api_key_raises_error(mock_load_dotenv, mock_getenv):
    """API 키가 없을 때 ValueError를 발생시키는지 테스트합니다."""
    with pytest.raises(ValueError, match="API 키가 제공되지 않았거나"):
        GeminiBatchClient()

@patch('infrastructure.gemini_batch_client.genai.Client')
def test_upload_file(mock_client_constructor):
    """파일 업로드 메서드가 내부 클라이언트를 올바르게 호출하는지 테스트합니다."""
    mock_sdk_client = MagicMock()
    mock_client_constructor.return_value = mock_sdk_client
    
    client = GeminiBatchClient(api_key="fake_key")
    file_path = "test.jsonl"
    mime_type = "application/json"
    
    mock_uploaded_file = MagicMock()
    mock_sdk_client.files.upload.return_value = mock_uploaded_file
    
    result = client.upload_file(file_path, mime_type)
    
    mock_genai.types.UploadFileConfig.assert_called_once_with(mime_type=mime_type)
    mock_sdk_client.files.upload.assert_called_once_with(
        file=file_path,
        config=mock_genai.types.UploadFileConfig.return_value
    )
    assert result == mock_uploaded_file

@patch('infrastructure.gemini_batch_client.genai.Client')
def test_create_batch_job(mock_client_constructor):
    """배치 작업 생성 메서드가 내부 클라이언트를 올바르게 호출하는지 테스트합니다."""
    mock_sdk_client = MagicMock()
    mock_client_constructor.return_value = mock_sdk_client

    client = GeminiBatchClient(api_key="fake_key")
    model_id = "gemini-1.5-flash"
    input_file_name = "files/some_file"
    display_name = "my-job"

    mock_batch_job = MagicMock()
    mock_sdk_client.batches.create.return_value = mock_batch_job

    result = client.create_batch_job(model_id, input_file_name, display_name)

    mock_sdk_client.batches.create.assert_called_once_with(
        model=f"models/{model_id}",
        src=input_file_name,
        config={'display_name': display_name}
    )
    assert result == mock_batch_job

@patch('infrastructure.gemini_batch_client.genai.Client')
def test_get_batch_job(mock_client_constructor):
    """배치 작업 조회 메서드가 내부 클라이언트를 올바르게 호출하는지 테스트합니다."""
    mock_sdk_client = MagicMock()
    mock_client_constructor.return_value = mock_sdk_client

    client = GeminiBatchClient(api_key="fake_key")
    job_name = "batches/some_job"

    mock_batch_job = MagicMock()
    mock_sdk_client.batches.get.return_value = mock_batch_job

    result = client.get_batch_job(job_name)

    mock_sdk_client.batches.get.assert_called_once_with(name=job_name)
    assert result == mock_batch_job

@patch('infrastructure.gemini_batch_client.genai.Client')
def test_list_recent_jobs(mock_client_constructor):
    """최근 작업 목록 조회 메서드가 내부 클라이언트를 올바르게 호출하는지 테스트합니다."""
    mock_sdk_client = MagicMock()
    mock_client_constructor.return_value = mock_sdk_client

    client = GeminiBatchClient(api_key="fake_key")
    limit = 5
    
    # 이터레이터 모의 처리
    mock_jobs = [MagicMock() for _ in range(10)]
    mock_sdk_client.batches.list.return_value = iter(mock_jobs)

    result = client.list_recent_jobs(limit=limit)

    mock_sdk_client.batches.list.assert_called_once()
    assert len(result) == limit
    assert result == mock_jobs[:limit]

@patch('infrastructure.gemini_batch_client.genai.Client')
def test_download_result_file(mock_client_constructor):
    """결과 파일 다운로드 메서드가 내부 클라이언트를 올바르게 호출하는지 테스트합니다."""
    mock_sdk_client = MagicMock()
    mock_client_constructor.return_value = mock_sdk_client

    client = GeminiBatchClient(api_key="fake_key")
    result_file_name = "files/result_file"
    file_content = b"This is the result."

    mock_sdk_client.files.download.return_value = file_content

    result = client.download_result_file(result_file_name)

    mock_sdk_client.files.download.assert_called_once_with(file=result_file_name)
    assert result == file_content
