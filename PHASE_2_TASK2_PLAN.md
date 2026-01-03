# Phase 2 Task 2: app_service.py 비동기화 (상세 계획)

**목표**: ThreadPoolExecutor + threading.Thread 제거 → asyncio 기반 아키텍처로 전환  
**예상 소요시간**: 9시간  
**우선순위**: 최우선 (Task 3의 기반)

---

## 📋 app_service.py 현황 분석

### 파일 규모
- **총 줄 수**: 1,300줄
- **클래스**: `AppService` (1개)
- **주요 메서드**: 30+ 개

### 핵심 메서드 (변경 필요)

| 메서드 | 줄 수 | 변경 | 설명 |
|--------|-------|------|------|
| `start_translation()` | ~50 | 🔴 전면 | 진입점, threading.Thread 제거 |
| `_translation_task()` | ~400 | 🔴 전면 | 실제 번역 로직, Lock 제거 |
| `_do_translation()` | ~300 | 🔴 전면 | ThreadPoolExecutor 제거 |
| `_translate_and_save_chunk()` | ~200 | 🔴 전면 | 청크 처리, 비동기화 |
| 기타 메서드 | ~350 | 🟡 부분 | 헬퍼 함수들, 필요시 수정 |

**변경 대상 총 줄 수**: ~900줄 (70%)

---

## 🔧 변경 전략

### Phase 1: 상태 관리 리팩토링

#### AS-IS (Thread + Lock 기반)
```python
class AppService:
    def __init__(self):
        # 플래그 기반 상태
        self.is_translation_running = False
        self.stop_requested = False
        
        # 3개의 Lock
        self._translation_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._file_write_lock = threading.Lock()
        
        # 카운터
        self.processed_chunks_count = 0
        self.successful_chunks_count = 0
        self.failed_chunks_count = 0
```

#### TO-BE (Asyncio + Task 기반)
```python
class AppService:
    def __init__(self):
        # Task 객체로 상태 관리 (Lock 불필요)
        self.current_translation_task: Optional[asyncio.Task] = None
        
        # Lock 제거 (asyncio는 단일 스레드)
        # self._translation_lock = None
        # self._progress_lock = None
        # self._file_write_lock = None
        
        # 카운터 (asyncio는 단일 스레드이므로 동기화 불필요)
        self.processed_chunks_count = 0
        self.successful_chunks_count = 0
        self.failed_chunks_count = 0
```

### Phase 2: 진입점 변환

#### AS-IS
```python
def start_translation(self, input_file_path, output_file_path, ...):
    # threading.Thread 생성
    thread = threading.Thread(
        target=self._translation_task,
        args=(input_file_path, output_file_path, ...),
        daemon=not blocking
    )
    thread.start()
    if blocking:
        thread.join()
```

#### TO-BE
```python
async def start_translation_async(
    self,
    input_file_path: Union[str, Path],
    output_file_path: Union[str, Path],
    progress_callback: Optional[Callable] = None,
    status_callback: Optional[Callable] = None,
    retranslate_failed_only: bool = False
) -> None:
    """
    비동기 번역 시작
    - GUI에서 @asyncSlot()으로 호출
    - Task 객체로 상태 관리
    - 취소 시 Task.cancel() 사용
    """
    # 이미 실행 중이면 예외 발생
    if self.current_translation_task and not self.current_translation_task.done():
        raise BtgServiceException("번역이 이미 실행 중입니다")
    
    # Task 생성 및 저장
    self.current_translation_task = asyncio.create_task(
        self._do_translation_async(
            input_file_path,
            output_file_path,
            progress_callback,
            status_callback,
            retranslate_failed_only
        )
    )
    
    # 예외 처리
    try:
        await self.current_translation_task
    except asyncio.CancelledError:
        logger.info("번역이 사용자에 의해 취소되었습니다")
        if status_callback:
            status_callback("중단됨")
    except Exception as e:
        logger.error(f"번역 중 오류: {e}", exc_info=True)
        if status_callback:
            status_callback(f"오류: {e}")
    finally:
        self.current_translation_task = None

async def cancel_translation_async(self) -> None:
    """비동기 번역 취소 (즉시 반응)"""
    if self.current_translation_task and not self.current_translation_task.done():
        self.current_translation_task.cancel()
        logger.info("번역 취소 요청됨")
```

### Phase 3: 메인 로직 변환

#### AS-IS
```python
def _translation_task(self, input_file_path, ...):
    with self._translation_lock:
        if self.is_translation_running:
            raise BtgServiceException("이미 실행 중")
        self.is_translation_running = True
        self.stop_requested = False
        self.processed_chunks_count = 0
    
    try:
        # ... 복잡한 로직 ...
        self._do_translation(input_file_path, ...)
    finally:
        with self._translation_lock:
            self.is_translation_running = False
```

#### TO-BE
```python
async def _do_translation_async(
    self,
    input_file_path: Union[str, Path],
    output_file_path: Union[str, Path],
    progress_callback: Optional[Callable] = None,
    status_callback: Optional[Callable] = None,
    retranslate_failed_only: bool = False
) -> None:
    """
    비동기 번역 메인 로직
    - Lock 제거 (asyncio 단일 스레드)
    - 상태는 Task 객체로 관리
    """
    # 상태 초기화 (Lock 불필요)
    self.processed_chunks_count = 0
    self.successful_chunks_count = 0
    self.failed_chunks_count = 0
    
    logger.info(f"비동기 번역 시작: {input_file_path}")
    if status_callback:
        status_callback("번역 준비 중...")
    
    try:
        # 파일 읽기 (비동기 선택)
        input_file_path_obj = Path(input_file_path)
        file_content = read_text_file(input_file_path_obj)  # 동기 (빠르므로 OK)
        
        # 청크 분할
        all_chunks = self.chunk_service.create_chunks_from_file_content(
            file_content,
            self.config.get("chunk_size", 6000)
        )
        total_chunks = len(all_chunks)
        
        logger.info(f"총 {total_chunks}개 청크로 분할됨")
        
        # 청크 처리 (비동기 병렬)
        await self._translate_chunks_async(
            all_chunks,
            Path(output_file_path),
            total_chunks,
            progress_callback
        )
        
        logger.info("번역 완료")
        if status_callback:
            status_callback("완료!")
            
    except asyncio.CancelledError:
        logger.info("번역이 취소되었습니다")
        raise
    except Exception as e:
        logger.error(f"번역 중 오류: {e}", exc_info=True)
        raise
```

### Phase 4: 병렬 처리 변환

#### AS-IS
```python
# ThreadPoolExecutor 사용
executor = ThreadPoolExecutor(max_workers=10)
future_to_chunk_index = {}

for i, chunk in enumerate(all_chunks):
    future = executor.submit(
        self._translate_and_save_chunk,
        i, chunk, ...
    )
    future_to_chunk_index[future] = i

for future in as_completed(future_to_chunk_index.keys()):
    if self.stop_requested:
        future.cancel()  # ❌ 실행 중이면 안 됨
```

#### TO-BE
```python
async def _translate_chunks_async(
    self,
    chunks: List[str],
    output_file: Path,
    total_chunks: int,
    progress_callback: Optional[Callable] = None
) -> None:
    """
    청크들을 비동기로 병렬 처리
    - asyncio.gather() 사용
    - 취소 시 모든 Task 즉시 취소
    """
    # Task 리스트 생성
    tasks = []
    for i, chunk_text in enumerate(chunks):
        task = asyncio.create_task(
            self._translate_and_save_chunk_async(
                i,
                chunk_text,
                output_file,
                total_chunks,
                progress_callback
            )
        )
        tasks.append(task)
    
    # 모든 Task 완료 대기 (예외 무시)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 결과 분석
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"청크 {i} 처리 실패: {result}")
        else:
            logger.debug(f"청크 {i} 처리 완료: {result}")
```

### Phase 5: 청크 처리 변환

#### AS-IS
```python
def _translate_and_save_chunk(
    self,
    chunk_index: int,
    chunk_text: str,
    ...
) -> bool:
    """동기 청크 처리"""
    try:
        if self.stop_requested:  # 플래그 확인
            return False
        
        # 동기 번역
        translated = self.translation_service.translate_chunk(chunk_text)
        
        with self._file_write_lock:  # Lock 필요
            save_chunk_with_index_to_file(output_file, chunk_index, translated)
        
        with self._progress_lock:  # Lock 필요
            self.processed_chunks_count += 1
            self.successful_chunks_count += 1
        
        return True
    except Exception as e:
        with self._progress_lock:
            self.failed_chunks_count += 1
        return False
```

#### TO-BE
```python
async def _translate_and_save_chunk_async(
    self,
    chunk_index: int,
    chunk_text: str,
    output_file: Path,
    total_chunks: int,
    progress_callback: Optional[Callable] = None
) -> bool:
    """
    비동기 청크 처리
    - Lock 제거
    - 비동기 번역 호출
    - 파일 쓰기는 순차 처리 (필요시 asyncio.Lock 사용)
    """
    try:
        logger.info(f"청크 {chunk_index+1}/{total_chunks} 처리 시작")
        
        # 비동기 번역
        translated = await self.translation_service.translate_chunk_async(
            chunk_text,
            timeout=300.0  # 5분 타임아웃
        )
        
        # 파일 저장 (Lock 불필요, 순차 처리)
        save_chunk_with_index_to_file(output_file, chunk_index, translated)
        
        # 상태 업데이트 (Lock 불필요, 단일 스레드)
        self.processed_chunks_count += 1
        self.successful_chunks_count += 1
        
        # 진행률 콜백
        if progress_callback:
            progress_percentage = (self.processed_chunks_count / total_chunks) * 100
            progress_callback(TranslationJobProgressDTO(
                total_chunks=total_chunks,
                processed_chunks=self.processed_chunks_count,
                successful_chunks=self.successful_chunks_count,
                failed_chunks=self.failed_chunks_count,
                current_status_message=f"✅ 청크 {chunk_index+1}/{total_chunks} 완료",
                current_chunk_processing=chunk_index + 1
            ))
        
        return True
        
    except asyncio.TimeoutError:
        logger.error(f"청크 {chunk_index} 타임아웃")
        self.failed_chunks_count += 1
        return False
    except asyncio.CancelledError:
        logger.info(f"청크 {chunk_index} 취소됨")
        raise
    except Exception as e:
        logger.error(f"청크 {chunk_index} 오류: {e}", exc_info=True)
        self.failed_chunks_count += 1
        return False
```

---

## 🎯 Task 2 세부 작업 계획

### Task 2-1: 초기화 메서드 수정 (30분)
```python
def __init__(self):
    # Lock 제거
    # self._translation_lock = threading.Lock()
    # self._progress_lock = threading.Lock()
    # self._file_write_lock = threading.Lock()
    
    # Task 객체 추가
    self.current_translation_task: Optional[asyncio.Task] = None
```

### Task 2-2: 진입점 메서드 작성 (1시간)
- `start_translation_async()` 작성
- `cancel_translation_async()` 작성
- 기존 `start_translation()` 유지 (호환성)

### Task 2-3: 메인 로직 작성 (2시간)
- `_do_translation_async()` 작성
- 파일 I/O 처리
- 메타데이터 처리

### Task 2-4: 병렬 처리 작성 (2시간)
- `_translate_chunks_async()` 작성
- asyncio.gather() 사용
- 예외 처리

### Task 2-5: 청크 처리 작성 (2시간)
- `_translate_and_save_chunk_async()` 작성
- 타임아웃 처리
- 진행률 콜백

### Task 2-6: 테스트 및 검증 (1.5시간)
- Unit 테스트 작성
- 통합 테스트
- 성능 검증

---

## 📊 변경 영향도

| 항목 | 영향 | 설명 |
|------|------|------|
| **Lock 제거** | 높음 | Race condition 완벽 제거 |
| **성능** | 중간 | 컨텍스트 스위칭 감소 |
| **코드 복잡도** | 낮음 | async/await로 가독성 ↑ |
| **기존 기능** | 무 | 모든 기능 유지 |

---

## ⚠️ 주의사항

### 1. 동기 메서드와의 호환성
- 기존 `start_translation()` 유지 필요
- GUI에서만 `_async` 버전 호출
- 기타 호출부는 유지

### 2. 콜백 안정성
- progress_callback은 동기 호출 (async 콜백 아님)
- status_callback도 동기 호출
- GUI 스레드에서 안전하게 처리 필요

### 3. 파일 I/O
- read_text_file: 동기 유지 (빠르므로 OK)
- save_chunk_with_index_to_file: 순차 처리
- 필요시 asyncio.Lock으로 동기화

### 4. 메타데이터 처리
- 기존 로직 유지
- Lock 제거하고 순차 처리로 변경

---

## ✅ 완료 기준

- [ ] 모든 `_async` 메서드 작성
- [ ] 기존 기능 100% 유지
- [ ] 취소 반응 시간 <1초
- [ ] 단위 테스트 80% 커버리지
- [ ] 통합 테스트 통과

---

**시작 준비 완료!** 🚀
