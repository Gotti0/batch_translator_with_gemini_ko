# test/test_app_service.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path
import json

# 테스트 대상 모듈을 import하기 위해 경로 추가
import sys
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.app_service import AppService

# --- Mocks and Fixtures ---

@pytest.fixture
def mock_dependencies():
    """AppService의 모든 외부 의존성을 모킹합니다."""
    with patch('app.app_service.ConfigManager') as mock_config_manager, \
         patch('app.app_service.GeminiClient') as mock_gemini_client, \
         patch('app.app_service.TranslationService') as mock_translation_service, \
         patch('app.app_service.SimpleGlossaryService') as mock_glossary_service, \
         patch('app.app_service.ChunkService') as mock_chunk_service, \
         patch('app.app_service.PostProcessingService') as mock_post_processing_service, \
         patch('infrastructure.file_handler.save_metadata') as mock_save_meta, \
         patch('infrastructure.file_handler.load_metadata') as mock_load_meta, \
         patch('infrastructure.file_handler.delete_file') as mock_delete_file, \
         patch('infrastructure.file_handler.write_text_file') as mock_write_text, \
         patch('infrastructure.file_handler._hash_config_for_metadata') as mock_hash_content:
        
        # 기본 설정 모킹 (실제 딕셔너리 사용)
        mock_config = {
            "api_keys": ["test_api_key"],
            "chunk_size": 100,
            "model_name": "gemini-test-model",
            # asyncio.Semaphore에 그대로 넘어가므로 실제 정수여야 한다.
            "max_workers": 1,
        }
        mock_config_manager.return_value.load_config.return_value = mock_config
        
        yield {
            "config": mock_config,
            "config_manager": mock_config_manager,
            "gemini_client": mock_gemini_client,
            "translation_service": mock_translation_service,
            "glossary_service": mock_glossary_service,
            "chunk_service": mock_chunk_service,
            "post_processing_service": mock_post_processing_service,
            "save_metadata": mock_save_meta,
            "load_metadata": mock_load_meta,
        }

@pytest.fixture
def app_service_instance(mock_dependencies):
    """테스트를 위한 AppService 인스턴스를 생성합니다."""
    # AppService 초기화 시 mock ConfigManager를 사용하도록 patch
    with patch('app.app_service.ConfigManager', return_value=mock_dependencies['config_manager']):
        service = AppService()
    
    # 실제 config 딕셔너리를 주입.
    # 이전에는 클래스 목에서 바로 load_config()를 불러 MagicMock이 들어갔고, 설정값을 정수로
    # 쓰는 곳(asyncio.Semaphore 등)에서 TypeError가 났다.
    service.config = mock_dependencies['config']

    # 나머지 의존성들을 직접 주입
    service.gemini_client = mock_dependencies['gemini_client']
    service.translation_service = mock_dependencies['translation_service']
    service.glossary_service = mock_dependencies['glossary_service']
    service.chunk_service = mock_dependencies['chunk_service']
    service.post_processing_service = mock_dependencies['post_processing_service']
    return service

# --- Test Class ---

class TestAppService:
    """AppService의 핵심 로직을 테스트합니다."""

    @pytest.mark.skip(reason="비동기 마이그레이션으로 인해 비활성화됨 - start_translation 제거 예정")
    def test_start_translation_happy_path(self, app_service_instance, mock_dependencies, tmp_path):
        """정상적인 번역 작업 흐름을 테스트합니다."""
        # Arrange
        input_file = tmp_path / "input.txt"
        output_file = tmp_path / "output.txt"
        input_file.write_text("Hello world.")

        mock_chunk_service = mock_dependencies['chunk_service']
        mock_translation_service = mock_dependencies['translation_service']
        mock_post_processing_service = mock_dependencies['post_processing_service']
        
        mock_chunk_service.create_chunks_from_file_content.return_value = ["Hello world."]
        mock_translation_service.translate_chunks.return_value = {0: "안녕하세요."}
        mock_dependencies['load_metadata'].return_value = {}

        # Act
        # app_service_instance.start_translation(str(input_file), str(output_file))  # 비동기 메서드 사용 필요

        # Assert
        # mock_chunk_service.create_chunks_from_file_content.assert_called_once()
        # mock_translation_service.translate_chunks.assert_called_once()
        # # 최종 결과가 파일에 쓰여졌는지 확인
        # mock_post_processing_service.merge_and_save_chunks.assert_called_once()

    def test_extract_glossary(self, app_service_instance, mock_dependencies, tmp_path):
        """용어집 추출이 도메인 서비스의 단계들을 순서대로 엮는지 본다.

        예전에는 `SimpleGlossaryService.extract_glossary()` 한 메서드가 전부를 했고 이
        테스트는 그것이 한 번 불렸는지만 확인했다. 지금은 AppService가 시드 로드 →
        세그먼트 준비 → 세그먼트별 API 추출 → 최종화 → 저장을 직접 조합하므로, 그 메서드는
        존재하지 않아 호출 횟수가 영영 0이었다.
        """
        input_file = tmp_path / "novel.txt"
        input_file.write_text("The hero named Elize.", encoding="utf-8")
        output_path = tmp_path / "novel_simple_glossary.json"

        glossary_service = app_service_instance.glossary_service
        seed_entries = []
        extracted = [MagicMock(name="entry")]
        finalized = [MagicMock(name="final-entry")]

        glossary_service.load_seed_glossary.return_value = seed_entries
        glossary_service.prepare_segments.return_value = ["The hero named Elize."]
        glossary_service._extract_glossary_entries_from_segment_via_api_async = AsyncMock(
            return_value=extracted
        )
        glossary_service.finalize_glossary.return_value = finalized
        glossary_service.get_glossary_output_path.return_value = output_path

        result_path = app_service_instance.extract_glossary(str(input_file))

        glossary_service.prepare_segments.assert_called_once()
        glossary_service._extract_glossary_entries_from_segment_via_api_async.assert_awaited_once()
        # 추출 결과와 시드가 함께 최종화로 넘어간다.
        glossary_service.finalize_glossary.assert_called_once_with(extracted, seed_entries)
        glossary_service.save_glossary_to_json.assert_called_once_with(finalized, output_path)
        assert result_path == output_path

# 주석 처리: 이 테스트 클래스는 이전 배치 아키텍처에 의존하므로 비활성화합니다.
# class TestAppServiceBatchMethods:
#     """AppService의 배치 번역 관련 메서드를 테스트합니다."""
# 
#     def test_prepare_batch_input_file(self, app_service_instance, mock_dependencies, tmp_path):
#         pass
# 
#     def test_parse_and_reassemble_batch_results(self, app_service_instance, mock_dependencies, tmp_path):
#         pass
# 
#     @patch('app.app_service.threading.Thread')
#     def test_start_batch_translation_starts_thread(self, mock_thread, app_service_instance):
#         pass
# 
#     def test_resume_incomplete_batch_jobs(self, mock_dependencies, tmp_path):
#         pass
# 
#     def test_resume_aborts_if_file_changed(self, mock_dependencies, tmp_path):
#         pass


# --- 헬스체크 로깅 ---

class TestHealthCheckLogging:
    """연결 테스트의 시작과 결과가 실행 로그에 남는지 확인한다."""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_llm_health_success_logged(self, app_service_instance):
        client = MagicMock()
        client.check_health_async = AsyncMock(return_value=(True, "Claude CLI 인증 성공"))
        with patch('app.app_service.LLMClientFactory.create_client', return_value=client), \
             patch('app.app_service.logger') as log:
            result = self._run(app_service_instance.check_llm_health_async({"llm_provider": "claude_cli"}))
        assert result == (True, "Claude CLI 인증 성공")
        messages = [c.args[0] for c in log.info.call_args_list]
        assert any("LLM 헬스체크 시작" in m and "claude_cli" in m for m in messages)
        assert any("LLM 헬스체크 성공" in m and "Claude CLI 인증 성공" in m for m in messages)

    def test_llm_health_failure_logged_as_warning(self, app_service_instance):
        client = MagicMock()
        client.check_health_async = AsyncMock(return_value=(False, "로그인 필요"))
        with patch('app.app_service.LLMClientFactory.create_client', return_value=client), \
             patch('app.app_service.logger') as log:
            ok, _ = self._run(app_service_instance.check_llm_health_async({"llm_provider": "codex_cli"}))
        assert ok is False
        log.warning.assert_called_once()
        assert "LLM 헬스체크 실패" in log.warning.call_args.args[0]
        assert "로그인 필요" in log.warning.call_args.args[0]

    def test_embedding_health_result_logged(self, app_service_instance):
        client = MagicMock()
        client.check_health_async = AsyncMock(return_value=(False, "voyage 연결 실패: 401"))
        app_service_instance.embedding_client_factory = lambda cfg: client
        with patch('app.app_service.logger') as log:
            self._run(app_service_instance.check_embedding_health_async({"embedding_provider": "voyage"}))
        assert any("임베딩 헬스체크 시작" in c.args[0] for c in log.info.call_args_list)
        assert "임베딩 헬스체크 실패" in log.warning.call_args.args[0]

    def test_embedding_health_exception_logged(self, app_service_instance):
        def broken_factory(cfg):
            raise ValueError("키 없음")
        app_service_instance.embedding_client_factory = broken_factory
        with patch('app.app_service.logger') as log:
            ok, message = self._run(app_service_instance.check_embedding_health_async({}))
        assert ok is False and "키 없음" in message
        log.error.assert_called_once()
