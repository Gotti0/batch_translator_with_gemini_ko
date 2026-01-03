# -*- coding: utf-8 -*-
import pytest
import json
from unittest.mock import MagicMock, patch, call
import json
import sys

# 의존성 모듈 모의 처리
mock_gemini_client = MagicMock()
sys.modules['infrastructure.gemini_batch_client'] = mock_gemini_client

from domain.batch_translation_service import BatchTranslationService
import sys

@pytest.fixture
def mock_batch_client():
    """테스트에 사용할 모의 GeminiBatchClient 객체를 생성하는 Fixture"""
    client = MagicMock()
    # 각 메서드에 대한 기본 모의 객체 설정
    client.upload_file = MagicMock()
    client.create_batch_job = MagicMock()
    client.get_batch_job = MagicMock()
    client.download_result_file = MagicMock()
    return client

@pytest.fixture
def service(mock_batch_client):
    """테스트 대상인 BatchTranslationService 인스턴스를 생성하는 Fixture"""
    return BatchTranslationService(mock_batch_client)

def test_start_new_translation_job(service, mock_batch_client):
    """새로운 번역 작업을 시작하는 로직을 테스트합니다."""
    requests_file = "requests.jsonl"
    model_id = "gemini-pro"
    
    # 모의 객체의 반환값 설정
    mock_uploaded_file = MagicMock()
    mock_uploaded_file.name = "files/uploaded_file_name"
    mock_batch_client.upload_file.return_value = mock_uploaded_file
    
    mock_batch_job = MagicMock()
    mock_batch_job.name = "batches/new_job_name"
    mock_batch_client.create_batch_job.return_value = mock_batch_job
    
    # 테스트할 메서드 호출
    job_name = service.start_new_translation_job(requests_file, model_id)
    
    # 검증
    mock_batch_client.upload_file.assert_called_once_with(file_path=requests_file)
    mock_batch_client.create_batch_job.assert_called_once_with(
        model_id=model_id,
        input_file_name=mock_uploaded_file.name
    )
    assert job_name == mock_batch_job.name

@patch('domain.batch_translation_service.time.sleep', return_value=None)
def test_monitor_job_status_success_flow(mock_sleep, service, mock_batch_client):
    """작업 상태가 성공적으로 완료될 때까지 모니터링하는 흐름을 테스트합니다."""
    job_name = "batches/test_job"
    
    # 상태 변화를 시뮬레이션하는 모의 작업 객체 리스트
    mock_job_pending = MagicMock()
    mock_job_pending.state.name = 'JOB_STATE_PENDING'
    
    mock_job_running = MagicMock()
    mock_job_running.state.name = 'JOB_STATE_RUNNING'
    
    mock_job_succeeded = MagicMock()
    mock_job_succeeded.state.name = 'JOB_STATE_SUCCEEDED'
    
    # get_batch_job 호출 시마다 다른 상태를 반환하도록 설정
    mock_batch_client.get_batch_job.side_effect = [
        mock_job_pending,
        mock_job_running,
        mock_job_succeeded
    ]
    
    # 제너레이터 실행 및 결과 수집
    status_generator = service.monitor_job_status(job_name, polling_interval=1)
    statuses = list(status_generator)
    
    # 검증
    assert statuses == [
        ('JOB_STATE_PENDING', job_name),
        ('JOB_STATE_RUNNING', job_name),
        ('JOB_STATE_SUCCEEDED', job_name)
    ]
    assert mock_batch_client.get_batch_job.call_count == 3
    # PENDING, RUNNING 상태 이후에 sleep이 호출되어야 함
    assert mock_sleep.call_count == 2

def test_get_translation_results_success(service, mock_batch_client):
    """성공한 작업의 번역 결과를 가져오는 로직을 테스트합니다."""
    job_name = "batches/succeeded_job"
    result_file_name = "files/result_file"
    
    # 모의 작업 객체 설정 (성공 상태)
    mock_job_succeeded = MagicMock()
    mock_job_succeeded.state.name = 'JOB_STATE_SUCCEEDED'
    mock_job_succeeded.dest.file_name = result_file_name
    mock_batch_client.get_batch_job.return_value = mock_job_succeeded
    
    # 모의 결과 파일 내용 설정
    result_content = (
        '{"key": "paragraph_1", "request": {}, "response": {"candidates": [{"content": {"parts": [{"text": "번역된 텍스트 1"}]}}]}}\n'
        '{"key": "paragraph_2", "request": {}, "response": {"candidates": [{"content": {"parts": [{"text": "번역된 텍스트 2"}]}}]}}'
    )
    mock_batch_client.download_result_file.return_value = result_content.encode('utf-8')
    
    # 테스트할 메서드 호출
    results = service.get_translation_results(job_name)
    
    # 검증
    mock_batch_client.get_batch_job.assert_called_once_with(job_name=job_name)
    mock_batch_client.download_result_file.assert_called_once_with(result_file_name)
    assert len(results) == 2
    assert results[1] == "번역된 텍스트 1"
    assert results[2] == "번역된 텍스트 2"

def test_get_translation_results_job_not_succeeded(service, mock_batch_client):
    """작업이 성공하지 않았을 때 빈 결과를 반환하는지 테스트합니다."""
    job_name = "batches/failed_job"
    
    mock_job_failed = MagicMock()
    mock_job_failed.state.name = 'JOB_STATE_FAILED'
    mock_batch_client.get_batch_job.return_value = mock_job_failed
    
    results = service.get_translation_results(job_name)
    
    assert results == {}
    mock_batch_client.download_result_file.assert_not_called()

def test_parse_result_content_with_various_cases(service, mock_batch_client):
    """다양한 케이스가 포함된 결과 파일을 올바르게 파싱하는지 테스트합니다."""
    job_name = "batches/mixed_results_job"
    result_file_name = "files/mixed_result_file"

    mock_job_succeeded = MagicMock()
    mock_job_succeeded.state.name = 'JOB_STATE_SUCCEEDED'
    mock_job_succeeded.dest.file_name = result_file_name
    mock_batch_client.get_batch_job.return_value = mock_job_succeeded

    # 다양한 케이스가 포함된 결과 파일 내용
    result_content = (
        '{"key": "paragraph_1", "response": {"candidates": [{"content": {"parts": [{"text": "정상 번역"}]}}]}}\n'
        '{"key": "paragraph_2", "response": {"prompt_feedback": {"block_reason": "SAFETY"}}}\n' # 차단된 경우
        '{"key": "paragraph_3", "invalid_json"}\n' # 잘못된 JSON 형식
        '\n' # 빈 줄
        '{"key": "paragraph_4", "response": {"candidates": [{"content": {"parts": [{"text": "또 다른 정상 번역"}]}}]}}'
    )
    mock_batch_client.download_result_file.return_value = result_content.encode('utf-8')

    results = service.get_translation_results(job_name)

    assert len(results) == 3
    assert results[1] == "정상 번역"
    assert "[번역 실패 또는 차단됨" in results[2] # 실패 메시지 확인
    assert results[4] == "또 다른 정상 번역"
    # key 3은 파싱 에러로 포함되지 않아야 함
    assert 3 not in results
