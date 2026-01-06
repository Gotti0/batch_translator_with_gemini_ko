"""
Promise.race 패턴 번역 취소 로직 유닛 테스트

테스트 시나리오:
1. 정상 번역 완료 (번역이 취소보다 먼저 완료)
2. 번역 중 취소 (취소가 번역보다 먼저 완료)
3. 번역 시작 전 취소 시도
4. 동시 취소 요청 처리
5. 취소 이벤트 정리 확인
"""

import asyncio
import pytest
import pytest_asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.app_service import AppService
from core.dtos import TranslationJobProgressDTO


class TestTranslationCancellation:
    """번역 취소 로직 테스트 스위트"""
    
    @pytest_asyncio.fixture
    async def app_service(self):
        """AppService 인스턴스 생성 (Gemini 클라이언트는 Mock)"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config = {
                "api_keys": ["test_api_key"],
                "use_vertex_ai": False,
                "model_name": "gemini-2.0-flash",
                "temperature": 0.7,
                "chunk_size": 100,
                "max_workers": 2,
                "requests_per_minute": 60,
                "prompts": "Translate: {{slot}}"
            }
            json.dump(config, f)
            config_path = f.name
        
        app = None
        try:
            app = AppService(config_file_path=config_path)
            
            # Gemini 클라이언트 Mock 설정
            app.gemini_client = Mock()
            
            # TranslationService Mock 설정
            app.translation_service = Mock()
            app.translation_service.config = app.config
            app.translation_service._load_glossary_data = Mock()
            
            yield app
        finally:
            Path(config_path).unlink(missing_ok=True)
    
    @pytest.fixture
    def temp_files(self):
        """임시 입력/출력 파일 생성"""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "input.txt"
            output_file = Path(tmpdir) / "output.txt"
            
            input_file.write_text("Test content for translation.\nSecond line.\nThird line.")
            
            yield input_file, output_file
    
    @pytest.mark.asyncio
    async def test_normal_translation_completion(self, app_service, temp_files):
        """시나리오 1: 정상 번역 완료 (취소 없음)"""
        input_file, output_file = temp_files
        
        # 빠른 번역 시뮬레이션 (0.1초)
        async def mock_translate(text, timeout=None):
            await asyncio.sleep(0.1)
            return f"Translated: {text}"
        
        app_service.translation_service.translate_chunk_async = mock_translate
        
        # 진행 상황 추적
        progress_calls = []
        def progress_callback(dto: TranslationJobProgressDTO):
            progress_calls.append(dto)
        
        # 번역 시작
        await app_service.start_translation_async(
            input_file_path=str(input_file),
            output_file_path=str(output_file),
            progress_callback=progress_callback
        )
        
        # 검증
        assert app_service.current_translation_task is None, "번역 Task가 정리되어야 함"
        assert not app_service.cancel_event.is_set(), "취소 이벤트가 초기화되어야 함"
        assert len(progress_calls) > 0, "진행 콜백이 호출되어야 함"
        assert output_file.exists(), "출력 파일이 생성되어야 함"
    
    @pytest.mark.asyncio
    async def test_translation_cancelled_during_processing(self, app_service, temp_files):
        """시나리오 2: 번역 중 취소 (취소가 승리)"""
        input_file, output_file = temp_files
        
        # 느린 번역 시뮬레이션 (2초)
        async def mock_slow_translate(text, timeout=None):
            await asyncio.sleep(2.0)
            return f"Translated: {text}"
        
        app_service.translation_service.translate_chunk_async = mock_slow_translate
        
        # 번역 Task 생성 (await하지 않음)
        translation_task = asyncio.create_task(
            app_service.start_translation_async(
                input_file_path=str(input_file),
                output_file_path=str(output_file)
            )
        )
        
        # 0.3초 후 취소 요청 (번역보다 먼저 완료)
        await asyncio.sleep(0.3)
        await app_service.cancel_translation_async()
        
        # 번역 Task가 CancelledError로 종료되는지 확인
        with pytest.raises(asyncio.CancelledError):
            await translation_task
        
        # 검증
        assert app_service.current_translation_task is None, "번역 Task가 정리되어야 함"
        assert app_service.cancel_event.is_set(), "취소 이벤트가 설정되어야 함"
    
    @pytest.mark.asyncio
    async def test_cancel_before_translation_starts(self, app_service, temp_files):
        """시나리오 3: 번역 시작 전 취소 시도"""
        # 번역이 시작되지 않은 상태에서 취소 시도
        await app_service.cancel_translation_async()
        
        # 검증: 경고만 로그되고 예외는 발생하지 않음
        assert app_service.current_translation_task is None
        assert not app_service.cancel_event.is_set()
    
    @pytest.mark.asyncio
    async def test_multiple_cancel_requests(self, app_service, temp_files):
        """시나리오 4: 동시 취소 요청 처리"""
        input_file, output_file = temp_files
        
        # 느린 번역 시뮬레이션
        async def mock_slow_translate(text, timeout=None):
            await asyncio.sleep(2.0)
            return f"Translated: {text}"
        
        app_service.translation_service.translate_chunk_async = mock_slow_translate
        
        # 번역 Task 생성
        translation_task = asyncio.create_task(
            app_service.start_translation_async(
                input_file_path=str(input_file),
                output_file_path=str(output_file)
            )
        )
        
        await asyncio.sleep(0.2)
        
        # 여러 번 취소 요청 (동시성 테스트)
        cancel_tasks = [
            asyncio.create_task(app_service.cancel_translation_async()),
            asyncio.create_task(app_service.cancel_translation_async()),
            asyncio.create_task(app_service.cancel_translation_async())
        ]
        
        # 모든 취소 요청 완료 대기
        await asyncio.gather(*cancel_tasks)
        
        # 번역 Task 종료 확인
        with pytest.raises(asyncio.CancelledError):
            await translation_task
        
        # 검증: 여러 번 호출해도 안전하게 처리
        assert app_service.current_translation_task is None
        assert app_service.cancel_event.is_set()
    
    @pytest.mark.asyncio
    async def test_cancel_event_cleanup(self, app_service, temp_files):
        """시나리오 5: 취소 이벤트 정리 확인 (새 번역 시작 시)"""
        input_file, output_file = temp_files
        
        # 빠른 번역 시뮬레이션
        async def mock_translate(text, timeout=None):
            await asyncio.sleep(0.05)
            return f"Translated: {text}"
        
        app_service.translation_service.translate_chunk_async = mock_translate
        
        # 1차 번역 (정상 완료)
        await app_service.start_translation_async(
            input_file_path=str(input_file),
            output_file_path=str(output_file)
        )
        
        assert not app_service.cancel_event.is_set(), "1차 번역 후 취소 이벤트 초기화"
        
        # 취소 이벤트를 수동으로 설정 (이전 상태 시뮬레이션)
        app_service.cancel_event.set()
        
        # 2차 번역 시작 (새로운 번역)
        output_file2 = output_file.parent / "output2.txt"
        await app_service.start_translation_async(
            input_file_path=str(input_file),
            output_file_path=str(output_file2)
        )
        
        # 검증: 새 번역 시작 시 취소 이벤트가 초기화되어야 함
        assert not app_service.cancel_event.is_set(), "새 번역 시작 시 취소 이벤트 초기화"
    
    @pytest.mark.asyncio
    async def test_cancel_race_timing(self, app_service, temp_files):
        """시나리오 6: 정확한 race 타이밍 테스트 (번역 vs 취소)"""
        input_file, output_file = temp_files
        
        race_winner = None
        
        # 번역 완료 시간 기록
        async def mock_translate_with_timing(text, timeout=None):
            nonlocal race_winner
            await asyncio.sleep(0.5)
            if race_winner is None:
                race_winner = "translation"
            return f"Translated: {text}"
        
        app_service.translation_service.translate_chunk_async = mock_translate_with_timing
        
        # 번역 Task 생성
        translation_task = asyncio.create_task(
            app_service.start_translation_async(
                input_file_path=str(input_file),
                output_file_path=str(output_file)
            )
        )
        
        # 0.2초 후 취소 (번역보다 먼저)
        await asyncio.sleep(0.2)
        if race_winner is None:
            race_winner = "cancel"
        await app_service.cancel_translation_async()
        
        # 결과 확인
        with pytest.raises(asyncio.CancelledError):
            await translation_task
        
        # 검증: 취소가 먼저 완료되어야 함
        assert race_winner == "cancel", "취소가 번역보다 먼저 완료되어야 함"
    
    @pytest.mark.asyncio
    async def test_already_running_translation_error(self, app_service, temp_files):
        """시나리오 7: 이미 실행 중인 번역 시 오류 발생"""
        input_file, output_file = temp_files
        
        # 느린 번역 시뮬레이션
        async def mock_slow_translate(text, timeout=None):
            await asyncio.sleep(2.0)
            return f"Translated: {text}"
        
        app_service.translation_service.translate_chunk_async = mock_slow_translate
        
        # 1차 번역 시작 (완료 대기 안함)
        translation_task1 = asyncio.create_task(
            app_service.start_translation_async(
                input_file_path=str(input_file),
                output_file_path=str(output_file)
            )
        )
        
        await asyncio.sleep(0.1)  # 번역이 시작되도록 대기
        
        # 2차 번역 시도 (실행 중이므로 오류 발생)
        from core.exceptions import BtgServiceException
        with pytest.raises(BtgServiceException, match="번역이 이미 실행 중입니다"):
            await app_service.start_translation_async(
                input_file_path=str(input_file),
                output_file_path=str(output_file)
            )
        
        # 정리
        await app_service.cancel_translation_async()
        with pytest.raises(asyncio.CancelledError):
            await translation_task1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
