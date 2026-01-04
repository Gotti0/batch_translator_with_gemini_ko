# test/test_app_service.py
import pytest
from unittest.mock import patch, MagicMock
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
        }
        mock_config_manager.return_value.load_config.return_value = mock_config
        
        yield {
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
    
    # 실제 config 딕셔너리를 주입
    service.config = mock_dependencies['config_manager'].load_config()

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
        """용어집 추출 기능 테스트."""
        # Arrange
        input_file = tmp_path / "novel.txt"
        input_file.write_text("The hero named Elize.")
        
        mock_glossary_service = mock_dependencies['glossary_service']
        expected_glossary = [{"keyword": "Elize", "translated_keyword": "엘리즈"}]
        mock_glossary_service.extract_glossary.return_value = expected_glossary
        
        # Act
        result_path = app_service_instance.extract_glossary(str(input_file))

        # Assert
        mock_glossary_service.extract_glossary.assert_called_once()
        self.assertTrue(result_path.exists())
        with open(result_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data, expected_glossary)

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
