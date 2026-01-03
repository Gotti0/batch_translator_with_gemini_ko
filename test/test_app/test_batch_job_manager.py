# -*- coding: utf-8 -*-
import pytest
from unittest.mock import MagicMock, patch, mock_open, call
import json
import sys

# 의존성 모듈 모의 처리
sys.modules['domain.batch_translation_service'] = MagicMock()
sys.modules['utils.request_builder'] = MagicMock()

from app.batch_job_manager import BatchJobManager, STATE_FILE, REQUESTS_FILE
import sys

@pytest.fixture
def mock_service():
    """모의 BatchTranslationService를 생성하는 Fixture"""
    service = MagicMock()
    service.start_new_translation_job.return_value = "batches/test_job_123"
    # 모니터링 제너레이터 모의 처리
    service.monitor_job_status.return_value = iter([('JOB_STATE_SUCCEEDED', 'batches/test_job_123')])
    # 결과 모의 처리
    service.get_translation_results.return_value = {1: "Translated text."}
    return service

@pytest.fixture
def mock_builder():
    """모의 BatchRequestBuilder를 생성하는 Fixture"""
    builder = MagicMock()
    builder.model_name = "models/gemini-test"
    return builder

@pytest.fixture
def mock_log_callback():
    """모의 로그 콜백 함수를 생성하는 Fixture"""
    return MagicMock()

@pytest.fixture
def manager(mock_service, mock_builder, mock_log_callback):
    """테스트 대상 BatchJobManager 인스턴스를 생성하는 Fixture"""
    return BatchJobManager(mock_service, mock_builder, mock_log_callback)

# --- 테스트 케이스 ---

@patch('app.batch_job_manager.open', new_callable=mock_open)
@patch('app.batch_job_manager.json.dump')
def test_start_new_job_success_path(mock_json_dump, mock_file_open, manager, mock_builder, mock_service):
    """새 작업 시작부터 성공적인 완료까지의 전체 흐름을 테스트합니다."""
    source_file = "source.txt"
    results_file = "results.txt"
    
    manager.start_new_job(source_file, results_file)
    
    # 1. 요청 파일 생성 확인
    mock_builder.create_requests_from_file.assert_called_once_with(source_file, REQUESTS_FILE)
    
    # 2. 도메인 서비스 호출 확인
    mock_service.start_new_translation_job.assert_called_once_with(REQUESTS_FILE, "gemini-test")
    
    # 3. 모니터링 및 결과 처리 확인
    mock_service.monitor_job_status.assert_called_once_with("batches/test_job_123")
    mock_service.get_translation_results.assert_called_once_with("batches/test_job_123")
    
    # 4. 결과 파일 저장 확인
    # mock_open은 컨텍스트 관리자(__enter__)를 통해 파일 핸들을 반환합니다.
    mock_file_open.assert_any_call(results_file, 'w', encoding='utf-8')
    file_handle = mock_file_open()
    
    # write가 두 번 호출되는 것을 확인
    expected_calls = [
        call("Translated text."),
        call("\n\n")
    ]
    file_handle.write.assert_has_calls(expected_calls)
    
    # 5. 상태 저장 확인 (RUNNING -> SUCCEEDED)
    # save_state가 두 번 호출되었는지 확인
    assert mock_json_dump.call_count == 2
    first_call_args = mock_json_dump.call_args_list[0].args[0]
    second_call_args = mock_json_dump.call_args_list[1].args[0]
    assert first_call_args['status'] == 'RUNNING'
    assert second_call_args['status'] == 'SUCCEEDED'

@patch('app.batch_job_manager.open', new_callable=mock_open)
@patch('app.batch_job_manager.json.load')
@patch('app.batch_job_manager.os.path.exists', return_value=True)
def test_resume_job_running(mock_exists, mock_json_load, mock_file_open, manager, mock_service):
    """진행 중인 작업을 성공적으로 이어받는지 테스트합니다."""
    # 이어하기할 상태를 모의 처리
    mock_state = {
        'job_name': 'batches/resumed_job',
        'source_file': 's.txt',
        'results_file': 'r.txt',
        'status': 'RUNNING'
    }
    mock_json_load.return_value = mock_state
    
    manager.resume_job()
    
    # 1. 상태 로드 확인
    mock_file_open.assert_any_call(STATE_FILE, 'r', encoding='utf-8')
    assert manager.state['job_name'] == 'batches/resumed_job'
    
    # 2. 모니터링 및 결과 처리 확인 (이어하기된 정보로 호출)
    mock_service.monitor_job_status.assert_called_once_with('batches/resumed_job')
    mock_service.get_translation_results.assert_called_once_with('batches/resumed_job')
    mock_file_open.assert_any_call('r.txt', 'w', encoding='utf-8')

def test_resume_job_not_running(manager, mock_log_callback):
    """진행 중이 아닌 작업은 이어하지 않는지 테스트합니다."""
    # 상태가 없거나 'RUNNING'이 아닌 경우를 시뮬레이션하기 위해 load_state가 빈 상태를 반환하도록 설정
    with patch.object(manager, 'load_state', return_value=None):
        manager.resume_job()
        mock_log_callback.assert_any_call("이어할 진행 중인 작업이 없습니다.")

@patch('app.batch_job_manager.open', new_callable=mock_open)
@patch('app.batch_job_manager.json.dump')
def test_monitor_and_process_results_failure(mock_json_dump, mock_file_open, manager, mock_service):
    """작업 모니터링 결과가 실패일 경우를 테스트합니다."""
    job_name = "batches/failed_job"
    results_file = "failed_results.txt"
    
    # 모니터링 결과가 'FAILED'를 반환하도록 설정
    mock_service.monitor_job_status.return_value = iter([('JOB_STATE_FAILED', job_name)])
    
    # manager.state를 미리 설정
    manager.state = {'source_file': 'source.txt'}

    manager._monitor_and_process_results(job_name, results_file)
    
    mock_service.get_translation_results.assert_not_called()
    # 최종 상태가 'FAILED'로 저장되는지 확인
    final_state_saved = mock_json_dump.call_args.args[0]
    assert final_state_saved['status'] == 'JOB_STATE_FAILED'

@patch('app.batch_job_manager.os.remove')
@patch('app.batch_job_manager.os.path.exists', return_value=True)
def test_clear_state(mock_exists, mock_os_remove, manager):
    """상태 파일이 정상적으로 삭제되는지 테스트합니다."""
    manager.clear_state()
    mock_os_remove.assert_called_once_with(STATE_FILE)
    assert manager.state == {}
