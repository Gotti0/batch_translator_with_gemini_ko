# Phase 2 Task 1: infrastructure/gemini_client.py 비동기화 완료

**완료 날짜**: 2026년 1월 3일  
**상태**: ✅ 완료

---

## 📋 작업 내용

### ✅ gemini_client.py 변경 사항

#### 1. Import 추가
```python
import asyncio  # ← 추가됨
```

#### 2. 비동기 메서드 추가

##### `generate_text_async()` - 메인 비동기 메서드
- **용도**: generate_text의 비동기 버전
- **특징**:
  - asyncio.wait_for를 사용한 타임아웃 지원
  - CancelledError 정의 처리
  - 동기 메서드(generate_text)를 executor로 래핑
  
**코드**:
```python
async def generate_text_async(
    self,
    prompt: Union[str, List[genai_types.Content]],
    model_name: str,
    generation_config_dict: Optional[Dict[str, Any]] = None,
    safety_settings_list_of_dicts: Optional[List[Dict[str, Any]]] = None,
    thinking_budget: Optional[int] = None,
    system_instruction_text: Optional[str] = None,
    max_retries: int = 5,
    initial_backoff: float = 2.0,
    max_backoff: float = 60.0,
    stream: bool = False,
    timeout: Optional[float] = None
) -> Optional[Union[str, Any]]:
    """
    비동기 텍스트 생성 메서드
    - asyncio.wait_for()로 타임아웃 처리
    - 기존 동기 메서드를 executor로 실행
    """
```

##### `_generate_text_async_impl()` - 구현 세부
```python
async def _generate_text_async_impl(
    self,
    ...
) -> Optional[Union[str, Any]]:
    """
    generate_text의 실제 비동기 구현
    - loop.run_in_executor()로 동기 작업을 비동기로 변환
    - Google-genai SDK의 비동기 지원 한계로 이 방식 사용
    """
```

---

### ✅ translation_service.py 변경 사항

#### 1. Import 추가
```python
import asyncio  # ← 추가됨
```

#### 2. 비동기 메서드 추가

##### `translate_chunk_async()` - 청크 비동기 번역
- **용도**: translate_chunk의 비동기 버전
- **특징**:
  - 타임아웃 지원
  - asyncio.CancelledError 처리
  - 용어집 포맷팅은 동기 진행 (빠름)
  
**코드**:
```python
async def translate_chunk_async(
    self,
    chunk_text: str,
    stream: bool = False,
    timeout: Optional[float] = None
) -> str:
    """
    비동기 청크 번역 메서드
    - 용어집 포맷팅은 동기적으로 수행
    - API 호출만 비동기
    """
```

##### `translate_text_with_content_safety_retry_async()` - 안전성 재시도 포함
- **용도**: 콘텐츠 안전성 문제 발생 시 자동 분할 + 재시도 (비동기)
- **특징**:
  - 청크 자동 분할 로직 포함
  - 타임아웃 지원
  - 기존 동기 로직 그대로 사용 (executor로 래핑)

**코드**:
```python
async def translate_text_with_content_safety_retry_async(
    self,
    chunk_text: str,
    max_split_attempts: int = 3,
    min_chunk_size: int = 100,
    timeout: Optional[float] = None
) -> str:
    """
    비동기 콘텐츠 안전성 재시도와 함께 청크 번역
    """
```

---

## 🏗️ 구현 전략

### 왜 `run_in_executor` 사용?

Google-genai SDK는 아직 완전한 비동기 지원이 부족합니다. 따라서:

```python
# 동기 메서드를 스레드 풀에서 실행
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(None, sync_function)
```

**장점**:
- GUI가 블로킹되지 않음 (UI 스레드 ≠ 작업 스레드)
- Task.cancel()로 즉시 취소 가능
- 기존 동기 코드 재사용 (중복 없음)

**단점**:
- 완전한 비동기는 아님 (스레드 풀 사용)
- 하지만 충분함 (취소 시 CancelledError 발생)

---

## 🧪 테스트 방법

### Unit 테스트 작성 (다음 단계)

```python
# test/test_gemini_client_async.py
import pytest

@pytest.mark.asyncio
async def test_generate_text_async():
    client = GeminiClient()
    result = await client.generate_text_async(
        "Hello", 
        model_name="gemini-2.0-flash"
    )
    assert isinstance(result, str)

@pytest.mark.asyncio
async def test_generate_text_async_timeout():
    client = GeminiClient()
    with pytest.raises(asyncio.TimeoutError):
        await client.generate_text_async(
            "Test",
            model_name="gemini-2.0-flash",
            timeout=0.001  # 1ms로 타임아웃
        )

@pytest.mark.asyncio
async def test_translate_chunk_async_cancel():
    service = TranslationService(client, config)
    task = asyncio.create_task(
        service.translate_chunk_async("Long text")
    )
    await asyncio.sleep(0.1)
    task.cancel()
    
    with pytest.raises(asyncio.CancelledError):
        await task
```

---

## ✅ 완료 항목

- ✅ gemini_client.py `generate_text_async()` 추가
- ✅ gemini_client.py `_generate_text_async_impl()` 추가
- ✅ translation_service.py `translate_chunk_async()` 추가
- ✅ translation_service.py `translate_text_with_content_safety_retry_async()` 추가
- ✅ 모든 메서드에 타임아웃 지원
- ✅ CancelledError 정의 처리
- ✅ 문서화 주석 추가

---

## 📊 코드 통계

| 항목 | 수치 |
|------|------|
| **추가된 줄 수** | ~180줄 |
| **새 메서드** | 4개 |
| **비동기 함수** | 4개 |
| **타임아웃 지원** | ✅ 모든 메서드 |

---

## 🚀 다음 단계: Task 2

### Task 2: translation_service.py 추가 비동기화 (선택)

현재 구현에서:
- 용어집 포맷팅: 동기 (빠르므로 OK)
- 프롬프트 생성: 동기 (빠르므로 OK)
- API 호출: 비동기 ✅

필요하면 aiofiles를 사용한 파일 I/O 비동기화 추가 가능하지만,
현재는 필수 아님.

---

## 💾 Git 커밋

```bash
git add infrastructure/gemini_client.py domain/translation_service.py
git commit -m "Phase 2 Task 1: Infrastructure 계층 비동기화 - gemini_client, translation_service 비동기 메서드 추가"
```

---

**작성자**: AI Assistant  
**검토 상태**: Phase 2-1 완료 ✅
