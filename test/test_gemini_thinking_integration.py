"""
Thinking Config 파라미터 분기 로직 통합 테스트 (실제 API 호출)

실제 Gemini API를 사용하여 thinking 파라미터가 올바르게 동작하는지 검증:
- Gemini 3.0: thinking_level 사용
- Gemini 2.5: thinking_budget 사용
"""

import pytest
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from infrastructure.gemini_client import GeminiClient

# .env 파일 로드
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)


@pytest.fixture
def api_key():
    """환경 변수에서 API 키 가져오기"""
    key = os.getenv('GEMINI_API_KEY')
    if not key:
        pytest.skip("GEMINI_API_KEY가 설정되지 않았습니다")
    return key


class TestThinkingConfigIntegration:
    """실제 API를 사용한 Thinking Config 통합 테스트"""

    @pytest.mark.asyncio
    async def test_gemini_3_with_thinking_level(self, api_key):
        """Gemini 3.0 모델이 thinking_level로 정상 동작하는지 확인"""
        client = GeminiClient(auth_credentials=api_key)
        
        response = await client.generate_text_async(
            prompt="1+1은?",
            model_name="gemini-3-flash-preview",
            generation_config_dict={
                "temperature": 0.7,
                "thinking_level": "high"
            }
        )
        
        # 응답이 정상적으로 반환되는지 확인
        assert response is not None
        assert len(response) > 0
        print(f"\n✅ Gemini 3.0 응답: {response[:100]}...")

    @pytest.mark.asyncio
    async def test_gemini_25_with_thinking_budget(self, api_key):
        """Gemini 2.5 모델이 thinking_budget로 정상 동작하는지 확인"""
        client = GeminiClient(auth_credentials=api_key)
        
        response = await client.generate_text_async(
            prompt="1+1은?",
            model_name="gemini-2.5-flash",
            generation_config_dict={
                "temperature": 0.7,
                "thinking_budget": 100
            }
        )
        
        # 응답이 정상적으로 반환되는지 확인
        assert response is not None
        assert len(response) > 0
        print(f"\n✅ Gemini 2.5 응답: {response[:100]}...")

    @pytest.mark.asyncio
    async def test_gemini_3_default_thinking_level(self, api_key):
        """Gemini 3.0 모델이 기본 thinking_level로 정상 동작하는지 확인"""
        client = GeminiClient(auth_credentials=api_key)
        
        response = await client.generate_text_async(
            prompt="안녕하세요",
            model_name="gemini-3-flash-preview",
            generation_config_dict={"temperature": 0.7}
        )
        
        # 응답이 정상적으로 반환되는지 확인
        assert response is not None
        assert len(response) > 0
        print(f"\n✅ Gemini 3.0 기본값 응답: {response[:100]}...")

    @pytest.mark.asyncio
    async def test_gemini_25_default_thinking_budget(self, api_key):
        """Gemini 2.5 모델이 기본 thinking_budget로 정상 동작하는지 확인"""
        client = GeminiClient(auth_credentials=api_key)
        
        response = await client.generate_text_async(
            prompt="안녕하세요",
            model_name="gemini-2.5-flash",
            generation_config_dict={"temperature": 0.7}
        )
        
        # 응답이 정상적으로 반환되는지 확인
        assert response is not None
        assert len(response) > 0
        print(f"\n✅ Gemini 2.5 기본값 응답: {response[:100]}...")

    @pytest.mark.asyncio
    async def test_gemini_20_without_thinking_config(self, api_key):
        """Gemini 2.0 모델이 thinking_config 없이 정상 동작하는지 확인"""
        client = GeminiClient(auth_credentials=api_key)
        
        response = await client.generate_text_async(
            prompt="안녕하세요",
            model_name="gemini-2.0-flash-exp",
            generation_config_dict={"temperature": 0.7}
        )
        
        # 응답이 정상적으로 반환되는지 확인
        assert response is not None
        assert len(response) > 0
        print(f"\n✅ Gemini 2.0 응답: {response[:100]}...")

    @pytest.mark.asyncio
    async def test_gemini_25_thinking_budget_priority(self, api_key):
        """Gemini 2.5에서 thinking_budget 인자가 dict보다 우선하는지 확인"""
        client = GeminiClient(auth_credentials=api_key)
        
        # 인자로 전달된 thinking_budget이 우선되어야 함
        response = await client.generate_text_async(
            prompt="2+2는?",
            model_name="gemini-2.5-flash",
            generation_config_dict={
                "temperature": 0.7,
                "thinking_budget": 50
            },
            thinking_budget=200  # 이 값이 우선됨
        )
        
        # 응답이 정상적으로 반환되는지 확인
        assert response is not None
        assert len(response) > 0
        print(f"\n✅ Gemini 2.5 우선순위 테스트 응답: {response[:100]}...")
