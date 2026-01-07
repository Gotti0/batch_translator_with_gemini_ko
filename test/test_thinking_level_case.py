"""
ThinkingLevel enum 대소문자 테스트
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.genai import types as genai_types

print("🔍 ThinkingLevel enum 테스트\n")

# 1. Enum 상수 직접 사용
print("1️⃣ Enum 상수:")
print(f"   genai_types.ThinkingLevel.HIGH = {genai_types.ThinkingLevel.HIGH}")
print(f"   타입: {type(genai_types.ThinkingLevel.HIGH)}")
print(f"   값: {genai_types.ThinkingLevel.HIGH.value}")
print()

# 2. ThinkingConfig에 소문자 문자열 전달
print("2️⃣ 소문자 문자열 'high' 전달:")
try:
    config1 = genai_types.ThinkingConfig(thinking_level="high")
    print(f"   ✅ 성공: {config1.thinking_level}")
    print(f"   타입: {type(config1.thinking_level)}")
    print(f"   값: {config1.thinking_level.value}")
except Exception as e:
    print(f"   ❌ 실패: {e}")
print()

# 3. ThinkingConfig에 대문자 문자열 전달
print("3️⃣ 대문자 문자열 'HIGH' 전달:")
try:
    config2 = genai_types.ThinkingConfig(thinking_level="HIGH")
    print(f"   ✅ 성공: {config2.thinking_level}")
    print(f"   타입: {type(config2.thinking_level)}")
    print(f"   값: {config2.thinking_level.value}")
except Exception as e:
    print(f"   ❌ 실패: {e}")
print()

# 4. ThinkingConfig에 enum 상수 직접 전달
print("4️⃣ Enum 상수 직접 전달:")
try:
    config3 = genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.HIGH)
    print(f"   ✅ 성공: {config3.thinking_level}")
    print(f"   타입: {type(config3.thinking_level)}")
    print(f"   값: {config3.thinking_level.value}")
except Exception as e:
    print(f"   ❌ 실패: {e}")
print()

# 5. 비교 테스트
print("5️⃣ 비교 테스트:")
print(f"   config1.thinking_level == config2.thinking_level: {config1.thinking_level == config2.thinking_level}")
print(f"   config1.thinking_level == config3.thinking_level: {config1.thinking_level == config3.thinking_level}")
print(f"   config2.thinking_level == config3.thinking_level: {config2.thinking_level == config3.thinking_level}")
print()

# 6. CaseInSensitiveEnum 확인
print("6️⃣ CaseInSensitiveEnum 상속 확인:")
from google.genai._common import CaseInSensitiveEnum
print(f"   ThinkingLevel이 CaseInSensitiveEnum을 상속? {issubclass(genai_types.ThinkingLevel, CaseInSensitiveEnum)}")
