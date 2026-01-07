"""
Thinking Config 파라미터 분기 로직 테스트

Gemini 모델별로 적절한 thinking 파라미터가 전달되는지 검증:
- Gemini 3.0: thinking_level만 사용
- Gemini 2.5: thinking_budget만 사용
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from google import genai
from google.genai import types as genai_types

from infrastructure.gemini_client import GeminiClient


class TestThinkingConfigBranching:
    """Thinking Config 파라미터 분기 테스트"""

    @pytest.fixture
    def mock_genai_client(self):
        """Mock Gemini Client 생성"""
        with patch('infrastructure.gemini_client.genai.Client') as mock_client_class:
            mock_instance = MagicMock()
            mock_instance.aio = MagicMock()
            mock_instance.aio.models = MagicMock()
            
            # Mock 응답 설정
            mock_response = MagicMock()
            mock_response.text = "테스트 응답"
            mock_response.prompt_feedback = None
            mock_response.candidates = [MagicMock()]
            mock_response.candidates[0].finish_reason = genai_types.FinishReason.STOP
            
            # generate_content를 AsyncMock으로 설정
            mock_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
            
            mock_client_class.return_value = mock_instance
            yield mock_instance

    @pytest.mark.asyncio
    async def test_gemini_3_uses_thinking_level_only(self, mock_genai_client):
        """Gemini 3.0 모델은 thinking_level만 사용해야 함"""
        client = GeminiClient(auth_credentials="test_api_key")
        
        await client.generate_text_async(
            prompt="테스트",
            model_name="gemini-3.0-flash",
            generation_config_dict={
                "temperature": 0.7,
                "thinking_level": "high",
                "thinking_budget": 100
            }
        )
        
        config = mock_genai_client.aio.models.generate_content.call_args.kwargs['config']
        # ThinkingLevel enum의 value 속성 사용
        assert config.thinking_config.thinking_level.value == "HIGH"

    @pytest.mark.asyncio
    async def test_gemini_25_uses_thinking_budget_from_dict(self, mock_genai_client):
        """Gemini 2.5 모델은 dict의 thinking_budget을 사용해야 함"""
        client = GeminiClient(auth_credentials="test_api_key")
        
        await client.generate_text_async(
            prompt="테스트",
            model_name="gemini-2.5-flash",
            generation_config_dict={
                "temperature": 0.7,
                "thinking_level": "high",
                "thinking_budget": 200
            }
        )
        
        config = mock_genai_client.aio.models.generate_content.call_args.kwargs['config']
        assert config.thinking_config.thinking_budget == 200

    @pytest.mark.asyncio
    async def test_gemini_25_uses_thinking_budget_from_argument(self, mock_genai_client):
        """Gemini 2.5 모델은 인자의 thinking_budget을 우선 사용해야 함"""
        client = GeminiClient(auth_credentials="test_api_key")
        
        await client.generate_text_async(
            prompt="테스트",
            model_name="gemini-2.5-flash",
            generation_config_dict={"temperature": 0.7, "thinking_budget": 200},
            thinking_budget=300
        )
        
        config = mock_genai_client.aio.models.generate_content.call_args.kwargs['config']
        assert config.thinking_config.thinking_budget == 300

    @pytest.mark.asyncio
    async def test_gemini_25_uses_default_thinking_budget(self, mock_genai_client):
        """Gemini 2.5 모델은 값이 없으면 기본값 -1을 사용해야 함"""
        client = GeminiClient(auth_credentials="test_api_key")
        
        await client.generate_text_async(
            prompt="테스트",
            model_name="gemini-2.5-flash",
            generation_config_dict={"temperature": 0.7}
        )
        
        config = mock_genai_client.aio.models.generate_content.call_args.kwargs['config']
        assert config.thinking_config.thinking_budget == -1

    @pytest.mark.asyncio
    async def test_gemini_3_default_thinking_level(self, mock_genai_client):
        """Gemini 3.0 모델은 값이 없으면 기본값 'high'를 사용해야 함"""
        client = GeminiClient(auth_credentials="test_api_key")
        
        await client.generate_text_async(
            prompt="테스트",
            model_name="gemini-3.0-flash",
            generation_config_dict={"temperature": 0.7}
        )
        
        config = mock_genai_client.aio.models.generate_content.call_args.kwargs['config']
        # ThinkingLevel enum의 value 속성 사용
        assert config.thinking_config.thinking_level.value == "HIGH"
        assert config.thinking_config.thinking_budget is None  # Gemini 3.0은 thinking_budget 없음

    @pytest.mark.asyncio
    async def test_non_thinking_model_no_thinking_config(self, mock_genai_client):
        """Gemini 2.0/1.5 같은 비-thinking 모델은 thinking_config를 설정하지 않아야 함"""
        client = GeminiClient(auth_credentials="test_api_key")
        
        await client.generate_text_async(
            prompt="테스트",
            model_name="gemini-2.0-flash",
            generation_config_dict={
                "temperature": 0.7,
                "thinking_level": "high",
                "thinking_budget": 200
            }
        )
        
        config = mock_genai_client.aio.models.generate_content.call_args.kwargs['config']
        assert not hasattr(config, 'thinking_config') or config.thinking_config is None
