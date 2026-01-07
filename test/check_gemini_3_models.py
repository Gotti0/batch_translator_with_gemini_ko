"""
Gemini 3.0 모델 출시 여부 확인 스크립트
"""

import asyncio
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


async def check_gemini_3_models():
    """사용 가능한 모델 목록을 조회하고 Gemini 3.0 모델이 있는지 확인"""
    
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("❌ GEMINI_API_KEY가 설정되지 않았습니다")
        return
    
    print("🔍 Gemini API에서 사용 가능한 모델 목록 조회 중...\n")
    
    try:
        client = GeminiClient(auth_credentials=api_key)
        models = await client.list_models_async()
        
        print(f"✅ 총 {len(models)}개의 모델을 찾았습니다.\n")
        
        # Gemini 3.0 모델 필터링
        gemini_3_models = [m for m in models if 'gemini-3' in m.get('short_name', '').lower()]
        
        if gemini_3_models:
            print(f"🎉 Gemini 3.0 모델이 {len(gemini_3_models)}개 발견되었습니다!\n")
            for model in gemini_3_models:
                print(f"  📌 {model.get('short_name', 'N/A')}")
                print(f"     - 표시 이름: {model.get('display_name', 'N/A')}")
                print(f"     - 설명: {model.get('description', 'N/A')[:100]}...")
                print(f"     - 입력 토큰 제한: {model.get('input_token_limit', 0):,}")
                print(f"     - 출력 토큰 제한: {model.get('output_token_limit', 0):,}")
                print()
        else:
            print("❌ Gemini 3.0 모델을 찾지 못했습니다.\n")
        
        # 전체 모델 목록 출력
        print("=" * 80)
        print("전체 사용 가능한 모델 목록:")
        print("=" * 80)
        
        # 버전별로 그룹화
        gemini_2_5 = [m for m in models if 'gemini-2.5' in m.get('short_name', '').lower()]
        gemini_2_0 = [m for m in models if 'gemini-2.0' in m.get('short_name', '').lower()]
        gemini_1_5 = [m for m in models if 'gemini-1.5' in m.get('short_name', '').lower()]
        others = [m for m in models if m not in gemini_3_models + gemini_2_5 + gemini_2_0 + gemini_1_5]
        
        if gemini_3_models:
            print("\n🔥 Gemini 3.0 모델:")
            for m in gemini_3_models:
                print(f"  - {m.get('short_name', 'N/A')}")
        
        if gemini_2_5:
            print("\n⚡ Gemini 2.5 모델:")
            for m in gemini_2_5:
                print(f"  - {m.get('short_name', 'N/A')}")
        
        if gemini_2_0:
            print("\n🚀 Gemini 2.0 모델:")
            for m in gemini_2_0:
                print(f"  - {m.get('short_name', 'N/A')}")
        
        if gemini_1_5:
            print("\n📦 Gemini 1.5 모델:")
            for m in gemini_1_5:
                print(f"  - {m.get('short_name', 'N/A')}")
        
        if others:
            print("\n📋 기타 모델:")
            for m in others:
                print(f"  - {m.get('short_name', 'N/A')}")
                
    except Exception as e:
        print(f"❌ 오류 발생: {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(check_gemini_3_models())
