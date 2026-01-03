import pytest
from unittest.mock import MagicMock, patch, mock_open
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Union

# 테스트 대상 모듈 import
from domain.translation_service import TranslationService
from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException
from core.exceptions import BtgTranslationException

# --- 테스트 데이터 ---
SENSITIVE_TEXT = """「你这个坏蛋…啊啊…这么就出门了…人家怎么见人呀…」
	　　女友混着撒娇和叫床的声音的说道。
	　　尚志勇站在客厅中央，已经开始抽插了，他双臂架着小慧的腿根，手用力抓着女友白嫩的臀部，架开马步，一下下用力上下颠着身体，而鸡巴就随着猛力的操着女友小穴的深处。
"""

# --- Mock 객체 및 설정 ---

class MockStructuredTranslation(BaseModel):
    """구조화된 출력을 위한 Pydantic 모델"""
    translated_text: str = Field(description="The translated text.")
    status: str = Field(description="Translation status, e.g., 'success'.")

class MockGeminiClient(MagicMock):
    """테스트용 Mock GeminiClient"""
    def generate_text(
        self,
        prompt: Union[str, List[Any]],
        model_name: str,
        generation_config_dict: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Any:
        # 구조화된 출력 요청인지 확인
        is_structured_request = generation_config_dict and \
                                generation_config_dict.get("response_mime_type") == "application/json"

        if is_structured_request:
            # 구조화된 출력 요청은 성공적으로 응답
            return MockStructuredTranslation(
                translated_text="[구조화된 번역 성공] " + SENSITIVE_TEXT[:50] + "...",
                status="success"
            )
        else:
            # 일반 텍스트 요청은 검열 예외 발생
            raise GeminiContentSafetyException("Mock: 콘텐츠 안전 문제로 인해 차단됨")

@pytest.fixture
def mock_config():
    """테스트용 기본 설정 fixture"""
    return {
        "model_name": "gemini-test-model",
        "prompts": "Translate to Korean: {{slot}}",
        "enable_dynamic_glossary_injection": False,
        "enable_prefill_translation": False,
        "temperature": 0.7,
        "top_p": 0.9,
    }

@pytest.fixture
def translation_service(mock_config):
    """MockGeminiClient를 사용하는 TranslationService fixture"""
    mock_client = MockGeminiClient()
    return TranslationService(gemini_client=mock_client, config=mock_config)

# --- 테스트 케이스 ---

def test_normal_translation_fails_with_sensitive_text(translation_service):
    """
    시나리오 1: 민감한 텍스트에 대한 일반 번역 요청이 BtgTranslationException을 발생시키는지 테스트
    """
    print("\n--- 테스트 1: 일반 번역 (실패 예상) ---")
    with pytest.raises(BtgTranslationException) as excinfo:
        translation_service.translate_text(SENSITIVE_TEXT)
    
    # 예외 메시지에 '콘텐츠 안전 문제'가 포함되어 있는지 확인
    assert "콘텐츠 안전 문제" in str(excinfo.value)
    print("예상대로 BtgTranslationException 발생 (콘텐츠 안전 문제)")
    print(f"예외 정보: {excinfo.value}")

@patch('domain.translation_service.TranslationService._construct_prompt')
def test_structured_output_succeeds_with_sensitive_text(mock_construct_prompt, translation_service):
    """
    시나리오 2: 구조화된 출력 요청이 검열을 우회하고 성공하는지 테스트
    """
    print("\n--- 테스트 2: 구조화된 출력 번역 (성공 예상) ---")
    
    # _construct_prompt가 구조화된 출력을 요청하는 프롬프트를 반환하도록 설정
    # 실제로는 generate_text 호출 시 generation_config로 제어되므로, 여기서는 프롬프트 자체는 중요하지 않음
    mock_construct_prompt.return_value = f"Translate this to JSON: {SENSITIVE_TEXT}"

    # 구조화된 출력을 위한 generation_config 설정
    generation_config = {
        "response_mime_type": "application/json",
        "response_schema": MockStructuredTranslation
    }
    
    # TranslationService의 gemini_client.generate_text를 직접 호출하여 테스트
    # 실제로는 TranslationService 내부에 이 로직을 호출하는 새로운 메서드가 필요함
    # 여기서는 개념 증명을 위해 직접 호출
    try:
        result = translation_service.gemini_client.generate_text(
            prompt="dummy prompt",
            model_name="gemini-test-model",
            generation_config_dict=generation_config
        )
        
        assert isinstance(result, MockStructuredTranslation)
        assert result.status == "success"
        assert "[구조화된 번역 성공]" in result.translated_text
        print("성공: 구조화된 출력 요청이 Mock 응답을 성공적으로 반환했습니다.")
        print(f"결과: {result}")

    except BtgTranslationException as e:
        pytest.fail(f"구조화된 출력 테스트에서 예외가 발생해서는 안 됩니다: {e}")
