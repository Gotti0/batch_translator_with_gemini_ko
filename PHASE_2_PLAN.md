# Phase 2: 서비스 레이어 리팩토링 계획

**시작일**: 2026년 1월 3일  
**예상 소요시간**: 16시간  
**목표**: threading + ThreadPoolExecutor → asyncio 전환

---

## 📋 Phase 2 구조

### 의존성 순서 (하향식 구현)

```
Infrastructure 계층 (가장 하위)
  └─ gemini_client.py: API 호출 비동기화
     ↓
Domain 계층
  └─ translation_service.py: 함수 시그니처 변경
     ↓
Application 계층 (가장 상위)
  └─ app_service.py: 오케스트레이션 비동기화
```

**중요**: 이 순서대로 구현해야 상위 계층이 하위 계층 변경을 의존할 수 있습니다.

---

## 🎯 Task 1: infrastructure/gemini_client.py 비동기화 (~4시간)

### 현재 상태
```python
def translate_text(
    self, text: str,
    source_language: str = "auto",
    target_language: str = "ko"
) -> str:
    response = self.client.models.generate_content(...)  # ❌ 동기 호출
    return response.text
```

### 변경 목표
```python
async def translate_text_async(
    self, text: str,
    source_language: str = "auto",
    target_language: str = "ko",
    timeout: Optional[float] = None
) -> str:
    async with self.client.aio as aclient:  # ✅ 비동기 클라이언트
        coro = aclient.models.generate_content(...)
        if timeout:
            response = await asyncio.wait_for(coro, timeout=timeout)
        else:
            response = await coro
        return response.text
```

### 변경 사항 체크리스트
- [ ] `async def translate_text_async()` 추가
- [ ] `async def translate_text_with_content_safety_retry_async()` 추가
- [ ] 재시도 로직 비동기화 (time.sleep → await asyncio.sleep)
- [ ] 타임아웃 처리 추가 (asyncio.wait_for)
- [ ] CancelledError 예외 처리
- [ ] 동기 버전 유지 (호환성)

**완료 조건**:
- 모든 public 메서드가 `_async` 버전 제공
- 단위 테스트 작성 (mock asyncio)
- 기존 동기 코드와 호환

---

## 🎯 Task 2: domain/translation_service.py 수정 (~3시간)

### 현재 상태
```python
def translate_chunk(self, chunk_text: str, ...) -> str:
    translated = self.gemini_client.translate_text(...)  # ❌ 동기 호출
    return translated
```

### 변경 목표
```python
async def translate_chunk_async(self, chunk_text: str, ...) -> str:
    translated = await self.gemini_client.translate_text_async(...)  # ✅ 비동기 호출
    return translated
```

### 변경 사항 체크리스트
- [ ] `async def translate_chunk_async()` 추가
- [ ] `async def translate_text_with_content_safety_retry_async()` 추가
- [ ] 용어집 포맷팅 로직 유지 (동기, 빠름)
- [ ] 프롬프트 생성 로직 유지 (동기)
- [ ] 동기 버전 유지 (호환성)

**완료 조건**:
- 번역 로직 변경 없음 (함수 호출만 async)
- 용어집/프롬프트 처리는 그대로

---

## 🎯 Task 3: app/app_service.py 비동기화 (~9시간, 가장 큼)

### 핵심 변경

#### A. Lock 제거 (asyncio는 단일 스레드)

**AS-IS**:
```python
self._translation_lock = threading.Lock()      # ❌ 제거
self._progress_lock = threading.Lock()         # ❌ 제거
self._file_write_lock = threading.Lock()       # ❌ 제거

with self._progress_lock:
    self.processed_chunks_count += 1
    self.successful_chunks_count += 1
```

**TO-BE**:
```python
# Lock 없음 - asyncio는 단일 스레드에서만 실행
self.processed_chunks_count = 0                # ✅ 안전
self.successful_chunks_count = 0

# 협력적 멀티태스킹만 사용
self.processed_chunks_count += 1               # ✅ Race condition 없음
self.successful_chunks_count += 1
```

#### B. ThreadPoolExecutor 제거 → asyncio.gather() 사용

**AS-IS**:
```python
executor = ThreadPoolExecutor(max_workers=10)
futures = {}
for i, chunk in enumerate(chunks):
    future = executor.submit(self._translate_and_save_chunk, i, chunk, ...)
    futures[future] = i

for future in as_completed(futures):
    if self.stop_requested:
        future.cancel()  # ❌ 실행 중이면 안 됨
```

**TO-BE**:
```python
tasks = []
for i, chunk in enumerate(chunks):
    task = asyncio.create_task(
        self._translate_and_save_chunk_async(i, chunk, ...)
    )
    tasks.append(task)

# 모두 완료 대기
results = await asyncio.gather(*tasks, return_exceptions=True)

# 취소 (GUI에서)
for task in tasks:
    task.cancel()  # ✅ 즉시 취소됨
```

#### C. threading.Thread 제거

**AS-IS**:
```python
def start_translation(self, ...):
    thread = threading.Thread(
        target=self._translation_task,
        args=(input_file, output_file, ...)
    )
    thread.start()
```

**TO-BE**:
```python
# GUI에서 @asyncSlot() 호출 (PySide6)
@asyncSlot()
async def on_translate_clicked(self):
    await self.app_service.start_translation_async(...)
```

#### D. 플래그 기반 상태 관리 → Task 객체

**AS-IS**:
```python
self.is_translation_running = False
self.stop_requested = False

if self.is_translation_running:
    raise BtgServiceException("이미 실행 중")
self.is_translation_running = True
```

**TO-BE**:
```python
self.current_translation_task: Optional[asyncio.Task] = None

if self.current_translation_task and not self.current_translation_task.done():
    raise BtgServiceException("이미 실행 중")

self.current_translation_task = asyncio.create_task(
    self._do_translation_async(...)
)
```

### 변경 사항 체크리스트

**상태 관리**:
- [ ] `is_translation_running` 제거
- [ ] `stop_requested` 플래그 제거
- [ ] `current_translation_task` Task 객체 추가
- [ ] `_translation_lock`, `_progress_lock`, `_file_write_lock` 제거

**함수 변환**:
- [ ] `def start_translation()` → `async def start_translation_async()`
- [ ] `def _translation_task()` → `async def _do_translation_async()`
- [ ] `def _translate_and_save_chunk()` → `async def _translate_and_save_chunk_async()`

**구현 세부**:
- [ ] ThreadPoolExecutor 제거
- [ ] as_completed() → asyncio.gather()로 변경
- [ ] threading.Event → asyncio.Event로 변경
- [ ] time.sleep() → await asyncio.sleep()으로 변경
- [ ] 취소 기능: `self.current_translation_task.cancel()`
- [ ] progress_callback 유지 (동기 호출 가능)
- [ ] status_callback 유지 (동기 호출 가능)

**완료 조건**:
- 모든 public 메서드에 `_async` 버전 제공
- 기존 기능 100% 유지 (용어집, 이어하기, 품질 검사)
- 중단 반응 시간 <1초
- 단위 테스트 80% 커버리지

---

## 🧪 Task 4: Unit 테스트 작성 (~0시간, 동시 진행)

### 테스트 대상

**test/test_gemini_client_async.py**:
```python
@pytest.mark.asyncio
async def test_translate_text_async():
    client = GeminiClient(...)
    result = await client.translate_text_async("Hello")
    assert isinstance(result, str)

@pytest.mark.asyncio
async def test_translate_text_async_timeout():
    client = GeminiClient(...)
    with pytest.raises(asyncio.TimeoutError):
        await client.translate_text_async("Long text", timeout=0.001)
```

**test/test_translation_service_async.py**:
```python
@pytest.mark.asyncio
async def test_translate_chunk_async():
    service = TranslationService(...)
    result = await service.translate_chunk_async("Test")
    assert isinstance(result, str)
```

**test/test_app_service_async.py**:
```python
@pytest.mark.asyncio
async def test_start_translation_async():
    app_service = AppService(...)
    await app_service.start_translation_async(input_file, output_file)
    # 검증

@pytest.mark.asyncio
async def test_cancel_translation_async():
    app_service = AppService(...)
    task = asyncio.create_task(
        app_service.start_translation_async(input_file, output_file)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    
    with pytest.raises(asyncio.CancelledError):
        await task
```

---

## ✅ 완료 조건

### 기능 검증
- ✅ 기존 기능 100% 동작
  - 용어집 처리
  - 이어하기
  - 품질 검사
  - 진행률 콜백
  
- ✅ 비동기 기능
  - 모든 API 호출 비동기
  - Task.cancel()로 즉시 취소
  - 중단 반응 시간 <1초
  
- ✅ 테스트
  - Unit 테스트 80% 커버리지
  - 동기/비동기 호환성 테스트
  - 취소 기능 테스트

### 코드 품질
- ✅ 복잡도 감소
  - Lock 사용 최소화
  - 플래그 변수 최소화
  - 콜백 체인 단순화
  
- ✅ 문서화
  - docstring 추가
  - 타입 힌트 완성
  - 변경 사항 주석

---

## 📊 예상 효과

| 지표 | 현재 | 목표 | 개선율 |
|------|------|------|--------|
| **중단 반응 시간** | 5~30초 | <1초 | **95% ↓** |
| **Lock 개수** | 3개 | 0개 | **100% ↓** |
| **플래그 변수** | 5개 | 1개 | **80% ↓** |
| **코드 줄 수** | ~1,300줄 | ~1,200줄 | **8% ↓** |
| **복잡도** | McCabe 15~20 | 8~12 | **40% ↓** |

---

## 🚀 시작 순서

1. **gemini_client.py** 비동기화 (가장 하위 계층)
2. **translation_service.py** 수정 (중간 계층)
3. **app_service.py** 비동기화 (최상위 계층)
4. **테스트** 작성 (동시 진행)
5. **통합 테스트** 및 검증

---

**다음**: app_service.py의 상세 변경 계획 시작
