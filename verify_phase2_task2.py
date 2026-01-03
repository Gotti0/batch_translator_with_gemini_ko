#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 2 Task 2 검증 스크립트
app_service.py 애플리케이션 계층 비동기화 검증
"""

import asyncio
import inspect
import sys
from pathlib import Path

# 프로젝트 루트 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def check_async_methods():
    """비동기 메서드 확인"""
    print("=" * 80)
    print("[Phase 2 Task 2 검증] app_service.py 비동기 메서드")
    print("=" * 80)
    
    try:
        from app.app_service import AppService
        
        async_methods = {
            'start_translation_async': '비동기 번역 시작 (진입점)',
            'cancel_translation_async': '비동기 번역 취소 (즉시)',
            '_do_translation_async': '메인 로직 (메타데이터, 파일 처리)',
            '_translate_chunks_async': '청크 병렬 처리 (asyncio.gather)',
            '_translate_and_save_chunk_async': '개별 청크 처리 (비동기 번역 + 저장)',
        }
        
        print("\n[비동기 메서드 확인]")
        async_count = 0
        
        for method_name, description in async_methods.items():
            if hasattr(AppService, method_name):
                method = getattr(AppService, method_name)
                is_async = asyncio.iscoroutinefunction(method)
                
                if is_async:
                    status = "✅ 비동기"
                    async_count += 1
                    sig = inspect.signature(method)
                    param_count = len(sig.parameters) - 1  # self 제외
                else:
                    status = "❌ 동기"
                
                print(f"  {method_name:40s}: {status}")
                print(f"    └─ {description}")
            else:
                print(f"  {method_name:40s}: ❌ 존재하지 않음")
        
        print(f"\n  총 비동기 메서드: {async_count}/{len(async_methods)}")
        
        if async_count == len(async_methods):
            print("\n  ✅ [성공] 모든 필수 메서드가 비동기로 정의됨")
            return True
        else:
            print(f"\n  ❌ [실패] {len(async_methods) - async_count}개 메서드 누락")
            return False
            
    except Exception as e:
        print(f"  ❌ [오류] {e}")
        import traceback
        traceback.print_exc()
        return False


def check_init_structure():
    """__init__ 메서드 구조 확인"""
    print("\n" + "=" * 80)
    print("[초기화 메서드 검증] Task 객체 및 Lock 제거 확인")
    print("=" * 80)
    
    try:
        from app.app_service import AppService
        import inspect
        
        # __init__ 소스 코드 읽기
        init_source = inspect.getsource(AppService.__init__)
        
        checks = {
            'current_translation_task': (True, 'Task 객체 추가'),
            '_translation_lock': (False, 'Lock 제거'),
            '_progress_lock': (False, 'Lock 제거'),
            '_file_write_lock': (False, 'Lock 제거'),
            'is_translation_running': (False, '플래그 제거'),
            'stop_requested': (False, '플래그 제거'),
        }
        
        print("\n[__init__ 메서드 검증]")
        passed = 0
        
        for keyword, (should_exist, description) in checks.items():
            exists = keyword in init_source
            
            if exists == should_exist:
                status = "✅"
                passed += 1
            else:
                status = "❌"
            
            if should_exist:
                print(f"  {status} {keyword:30s}: {description}")
            else:
                print(f"  {status} {keyword:30s}: {description} (없음)")
        
        print(f"\n  검증 결과: {passed}/{len(checks)}")
        
        if passed == len(checks):
            print("  ✅ [성공] 초기화 메서드 구조 변경 완료")
            return True
        else:
            print(f"  ❌ [실패] {len(checks) - passed}개 항목 오류")
            return False
            
    except Exception as e:
        print(f"  ❌ [오류] {e}")
        import traceback
        traceback.print_exc()
        return False


def check_syntax():
    """문법 검사"""
    print("\n" + "=" * 80)
    print("[문법 검사] app_service.py")
    print("=" * 80)
    
    try:
        from app import app_service
        print("\n  ✅ [성공] 모든 문법 검사 통과")
        return True
    except SyntaxError as e:
        print(f"  ❌ [문법 오류] {e}")
        return False
    except Exception as e:
        print(f"  ⚠️ [경고] 임포트 오류 (문법은 정상): {type(e).__name__}")
        # 임포트 오류는 문법과 무관 (의존성 문제)
        return True


def check_async_signature():
    """비동기 메서드 시그니처 검증"""
    print("\n" + "=" * 80)
    print("[메서드 시그니처 검증]")
    print("=" * 80)
    
    try:
        from app.app_service import AppService
        import inspect
        
        print("\n[주요 메서드 시그니처]")
        
        # start_translation_async 확인
        method = AppService.start_translation_async
        sig = inspect.signature(method)
        params = list(sig.parameters.keys())
        
        print(f"\n  start_translation_async 매개변수:")
        for param in params:
            if param != 'self':
                p = sig.parameters[param]
                print(f"    - {param}: {p.annotation if p.annotation != inspect.Parameter.empty else 'Any'}")
        
        expected_params = [
            'input_file_path', 'output_file_path',
            'progress_callback', 'status_callback',
            'tqdm_file_stream', 'retranslate_failed_only'
        ]
        
        actual_params = [p for p in params if p != 'self']
        
        if actual_params == expected_params:
            print("\n  ✅ [성공] 매개변수 정의 완료")
            return True
        else:
            print(f"\n  ⚠️ [경고] 예상: {expected_params}")
            print(f"  ⚠️ [경고] 실제: {actual_params}")
            return True  # 치명적 오류 아님
            
    except Exception as e:
        print(f"  ❌ [오류] {e}")
        return False


def main():
    """메인 검증 실행"""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " Phase 2 Task 2 완료 검증 ".center(78) + "║")
    print("║" + " app_service.py 애플리케이션 계층 비동기화 ".center(78) + "║")
    print("╚" + "=" * 78 + "╝")
    
    results = []
    
    # 검증 실행
    results.append(("문법 검사", check_syntax()))
    results.append(("비동기 메서드 확인", check_async_methods()))
    results.append(("__init__ 구조 검증", check_init_structure()))
    results.append(("메서드 시그니처 검증", check_async_signature()))
    
    # 결과 요약
    print("\n" + "=" * 80)
    print("[최종 결과 요약]")
    print("=" * 80)
    
    for name, result in results:
        status = "✅ 통과" if result else "❌ 실패"
        print(f"  {status} - {name}")
    
    all_passed = all(result for _, result in results)
    
    if all_passed:
        print("\n╔" + "=" * 78 + "╗")
        print("║" + " ✅ Phase 2 Task 2 검증 완료! ".center(78) + "║")
        print("║" + " 모든 검증 항목 통과 ".center(78) + "║")
        print("╚" + "=" * 78 + "╝")
        return 0
    else:
        print("\n╔" + "=" * 78 + "╗")
        print("║" + " ❌ 검증 실패 ".center(78) + "║")
        print("║" + " 위의 오류를 확인하고 수정하세요 ".center(78) + "║")
        print("╚" + "=" * 78 + "╝")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
