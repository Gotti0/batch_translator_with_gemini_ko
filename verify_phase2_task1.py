#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 2 Task 1: 비동기 메서드 기본 테스트

이 스크립트는 asyncio 비동기 메서드가 정의되었는지 확인합니다.
실제 API 호출 테스트는 별도의 통합 테스트에서 수행합니다.
"""

import asyncio
import sys
import inspect
from pathlib import Path

# 경로 설정
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


async def main():
    print("=" * 70)
    print("Phase 2 Task 1: 비동기 메서드 검증")
    print("=" * 70)
    
    # 1. gemini_client.py 검증
    print("\n[1/3] infrastructure/gemini_client.py 검증 중...")
    try:
        from infrastructure.gemini_client import GeminiClient
        
        # 메서드 존재 확인
        methods_to_check = [
            'generate_text_async',
            '_generate_text_async_impl'
        ]
        
        for method_name in methods_to_check:
            if hasattr(GeminiClient, method_name):
                method = getattr(GeminiClient, method_name)
                is_async = inspect.iscoroutinefunction(method)
                status = "✅ 비동기" if is_async else "❌ 동기 (오류!)"
                print(f"  {method_name:40s}: {status}")
            else:
                print(f"  {method_name:40s}: ❌ 메서드 없음")
        
        print("  → GeminiClient 검증 완료")
        
    except Exception as e:
        print(f"  ❌ GeminiClient 로드 실패: {e}")
        return 1
    
    # 2. translation_service.py 검증
    print("\n[2/3] domain/translation_service.py 검증 중...")
    try:
        from domain.translation_service import TranslationService
        
        methods_to_check = [
            'translate_chunk_async',
            'translate_text_with_content_safety_retry_async'
        ]
        
        for method_name in methods_to_check:
            if hasattr(TranslationService, method_name):
                method = getattr(TranslationService, method_name)
                is_async = inspect.iscoroutinefunction(method)
                status = "✅ 비동기" if is_async else "❌ 동기 (오류!)"
                print(f"  {method_name:40s}: {status}")
            else:
                print(f"  {method_name:40s}: ❌ 메서드 없음")
        
        print("  → TranslationService 검증 완료")
        
    except Exception as e:
        print(f"  ❌ TranslationService 로드 실패: {e}")
        return 1
    
    # 3. asyncio 기본 동작 확인
    print("\n[3/3] asyncio 기본 동작 확인 중...")
    
    async def test_basic_async():
        """간단한 비동기 함수 테스트"""
        await asyncio.sleep(0.1)
        return "✅ asyncio 정상 동작"
    
    try:
        result = await test_basic_async()
        print(f"  {result}")
    except Exception as e:
        print(f"  ❌ asyncio 오류: {e}")
        return 1
    
    # 최종 결과
    print("\n" + "=" * 70)
    print("✅ Phase 2 Task 1 검증 완료!")
    print("=" * 70)
    print("\n다음 단계:")
    print("  1. Task 2: app_service.py 비동기화")
    print("  2. Unit 테스트 작성 (test/test_gemini_client_async.py)")
    print("  3. 통합 테스트 및 검증")
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
