# batch_thread.py

import threading
import queue
import time
import random
import logging
import re
from typing import List, Dict, Callable, Any, Optional
from pathlib import Path

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 청크 상태 정의
class ChunkStatus:
    PENDING = "pending"        # 처리 대기 중
    PROCESSING = "processing"  # 처리 중
    COMPLETED = "completed"    # 처리 완료
    FAILED = "failed"          # 처리 실패
    RATE_LIMITED = "rate_limited"  # API 제한으로 인한 지연

# 청크 정보 클래스
class ChunkInfo:
    def __init__(self, chunk_id: int, content: str):
        self.id = chunk_id
        self.content = content
        self.status = ChunkStatus.PENDING
        self.result = None
        self.retry_count = 0
        self.last_error = None
        self.next_retry_time = 0
        self.backoff_time = 0

class BatchThreadManager:
    def __init__(self, 
                 process_func: Callable, 
                 config: Dict[str, Any], 
                 tqdm_out=None, 
                 initial_threads: int = 3, 
                 max_threads: int = 10, 
                 min_threads: int = 1, 
                 max_retries: int = 10):
        """병렬 처리 관리자 초기화"""
        self.process_func = process_func
        self.config = config
        self.tqdm_out = tqdm_out
        
        self.max_threads = max_threads
        self.min_threads = min_threads
        self.active_threads = initial_threads
        self.max_retries = max_retries
        
        self.chunk_queue = queue.Queue()  # 처리할 청크 큐
        self.result_dict = {}  # 결과 저장 딕셔너리 (id -> 결과)
        self.active_chunks = {}  # 현재 처리 중인 청크 (id -> ChunkInfo)
        
        self.worker_threads = []  # 작업자 스레드 목록
        self.running = False  # 실행 상태
        self.rate_limited = False  # API 제한 상태
        self.rate_limit_time = 0  # API 제한 해제 시간
        
        self.lock = threading.Lock()  # 스레드 동기화용 락
        self.completed_count = 0  # 완료된 청크 수
        self.rate_limit_count = 0  # API 제한 횟수
        self.failed_count = 0  # 실패한 청크 수
        
        # API 제한 감지를 위한 패턴
        self.rate_limit_patterns = [
            r"rateLimitExceeded", 
            r"429", 
            r"The model is overloaded", 
            r"503", 
            r"500", 
            r"Internal",
            r"API 사용량 제한에 도달했습니다"
        ]
        
        # 로그 캡처 객체 생성
        self.log_capture = self._create_log_capture()
        
    def log(self, message: str) -> None:
        """로그 출력 (GUI 또는 콘솔)"""
        if self.tqdm_out:
            self.tqdm_out.write(f"{message}\n")
        else:
            logger.info(message)
    
    def is_api_limit_log(self, message: str) -> bool:
        """로그 메시지에서 API 제한 여부 확인"""
        return any(re.search(pattern, message) for pattern in self.rate_limit_patterns)
    
    def _create_log_capture(self):
        """로그 캡처 객체 생성"""
        return LogCapture(self)
    
    def _worker(self) -> None:
        """작업자 스레드 함수"""
        while self.running:
            # API 제한 상태 확인
            if self.rate_limited and time.time() < self.rate_limit_time:
                time.sleep(1.0)  # API 제한 상태에서는 대기
                continue
            
            try:
                # 큐에서 청크 가져오기 (타임아웃 설정)
                try:
                    chunk_info = self.chunk_queue.get(block=True, timeout=1.0)
                except queue.Empty:
                    continue
                
                # 재시도 대기 시간 확인
                if chunk_info.next_retry_time > 0 and time.time() < chunk_info.next_retry_time:
                    # 아직 대기 시간이 지나지 않았으면 다시 큐에 넣기
                    self.chunk_queue.put(chunk_info)
                    time.sleep(0.5)  # 짧은 대기 후 다음 청크 처리
                    continue
                
                # 청크 처리 시작
                with self.lock:
                    chunk_info.status = ChunkStatus.PROCESSING
                    self.active_chunks[chunk_info.id] = chunk_info
                
                try:
                    # 청크 처리 함수 호출
                    result = self.process_func(chunk_info.content, self.config)
                    
                    # 처리 성공
                    if result:
                        with self.lock:
                            chunk_info.status = ChunkStatus.COMPLETED
                            chunk_info.result = result
                            self.result_dict[chunk_info.id] = result
                            self.completed_count += 1
                            
                        self.log(f"청크 {chunk_info.id+1} 처리 완료")
                        self._consider_increasing_threads()  # 스레드 수 증가 고려
                    else:
                        # 결과가 없으면 실패로 간주
                        with self.lock:
                            chunk_info.status = ChunkStatus.FAILED
                            chunk_info.retry_count += 1
                            self.failed_count += 1
                            
                        if chunk_info.retry_count < self.max_retries:
                            # 재시도 가능
                            self.log(f"청크 {chunk_info.id+1} 처리 실패, 재시도 예정 ({chunk_info.retry_count}/{self.max_retries})")
                            chunk_info.next_retry_time = time.time() + 2 * chunk_info.retry_count
                            self.chunk_queue.put(chunk_info)
                        else:
                            # 최대 재시도 횟수 초과
                            self.log(f"청크 {chunk_info.id+1} 처리 최종 실패 (최대 재시도 횟수 초과)")
                
                except Exception as e:
                    error_message = str(e)
                    # API 제한 감지 및 처리
                    if self._detect_api_limit(error_message, chunk_info):
                        # API 제한 감지됨 -> 이미 처리됨
                        pass
                    else:
                        # 기타 오류 처리
                        with self.lock:
                            chunk_info.status = ChunkStatus.FAILED
                            chunk_info.last_error = error_message
                            chunk_info.retry_count += 1
                            
                        self.log(f"청크 {chunk_info.id+1} 처리 중 오류 발생: {error_message[:100]}...")
                        
                        if chunk_info.retry_count < self.max_retries:
                            # 지수 백오프로 재시도
                            backoff_time = min(5 * (2 ** chunk_info.retry_count) + random.uniform(0, 1), 120)
                            chunk_info.next_retry_time = time.time() + backoff_time
                            self.log(f"청크 {chunk_info.id+1} {backoff_time:.1f}초 후 재시도 예정 ({chunk_info.retry_count}/{self.max_retries})")
                            self.chunk_queue.put(chunk_info)
                        else:
                            # 최대 재시도 횟수 초과
                            self.log(f"청크 {chunk_info.id+1} 처리 최종 실패 (최대 재시도 횟수 초과)")
                            with self.lock:
                                self.failed_count += 1
                
                finally:
                    # 큐 작업 완료 표시
                    self.chunk_queue.task_done()
            
            except Exception as e:
                # 워커 스레드 내부 오류 처리
                self.log(f"작업자 스레드 내부 오류: {str(e)}")
                time.sleep(1.0)  # 오류 발생시 잠시 대기
    
    def _detect_api_limit(self, error_message: str, chunk_info: ChunkInfo) -> bool:
        """API 제한 감지 및 처리"""
        if self.is_api_limit_log(error_message):
            with self.lock:
                chunk_info.status = ChunkStatus.RATE_LIMITED
                self.rate_limited = True
                self.rate_limit_count += 1
            
            # 지수 백오프 계산
            backoff_time = min(10 * (2 ** chunk_info.retry_count) + random.uniform(0, 1), 300)
            
            with self.lock:
                chunk_info.retry_count += 1
                chunk_info.last_error = error_message
                chunk_info.next_retry_time = time.time() + backoff_time
                chunk_info.backoff_time = backoff_time
                self.rate_limit_time = time.time() + backoff_time
            
            self.log(f"청크 {chunk_info.id+1}에서 API 제한 감지. {backoff_time:.1f}초 후 재시도 예정.")
            
            # 청크 다시 큐에 넣기
            self.chunk_queue.put(chunk_info)
            
            # 스레드 수 감소
            self._reduce_threads()
            return True
        return False
    
    def _reduce_threads(self) -> None:
        """API 제한 발생 시 스레드 수 감소"""
        with self.lock:
            if self.active_threads > self.min_threads:
                self.active_threads = max(self.active_threads - 1, self.min_threads)
                self.log(f"API 제한으로 인해 스레드 수 감소: {self.active_threads}")
    
    def _consider_increasing_threads(self) -> None:
        """성공적인 처리 후 스레드 수 증가 고려"""
        with self.lock:
            if not self.rate_limited and time.time() - self.rate_limit_time > 60 and self.active_threads < self.max_threads:
                # 10개의 성공적인 처리마다 스레드 증가
                if self.completed_count % 10 == 0 and self.completed_count > 0:
                    self.active_threads = min(self.active_threads + 1, self.max_threads)
                    self.log(f"처리가 원활하여 스레드 수 증가: {self.active_threads}")
    
    def start(self) -> None:
        """병렬 처리 시작"""
        if self.running:
            return
            
        self.running = True
        self.log(f"병렬 처리 시작 (초기 스레드 수: {self.active_threads})")
        
        # 작업자 스레드 생성 및 시작
        for _ in range(self.max_threads):
            thread = threading.Thread(target=self._worker)
            thread.daemon = True
            thread.start()
            self.worker_threads.append(thread)
    
    def stop(self) -> None:
        """병렬 처리 중지"""
        if not self.running:
            return
            
        self.log("병렬 처리 중지 요청...")
        self.running = False
        
        # 모든 작업자 스레드 종료 대기
        for thread in self.worker_threads:
            if thread.is_alive():
                thread.join(timeout=2.0)
        
        self.worker_threads = []
        self.log("병렬 처리 중지 완료")
    
    def add_chunk(self, chunk_id: int, content: str) -> None:
        """처리할 청크 추가"""
        chunk_info = ChunkInfo(chunk_id, content)
        self.chunk_queue.put(chunk_info)
    
    def wait_completion(self, timeout: Optional[float] = None) -> bool:
        """모든 청크 처리 완료 대기"""
        try:
            self.chunk_queue.join()
            return True
        except Exception:
            return False
    
    def get_progress(self) -> Dict[str, int]:
        """현재 진행 상황 반환"""
        with self.lock:
            return {
                "total": len(self.result_dict) + self.chunk_queue.qsize() + len(self.active_chunks),
                "completed": self.completed_count,
                "failed": self.failed_count,
                "pending": self.chunk_queue.qsize(),
                "processing": len(self.active_chunks),
                "rate_limited": self.rate_limit_count
            }
    
    def get_ordered_results(self) -> List[str]:
        """순서대로 정렬된 결과 반환"""
        ordered_results = []
        with self.lock:
            for i in range(max(self.result_dict.keys()) + 1 if self.result_dict else 0):
                result = self.result_dict.get(i)
                if result:
                    ordered_results.append(result)
                else:
                    ordered_results.append("[처리 실패]")
        
        return ordered_results

class LogCapture:
    """로그 메시지를 캡처하여 API 제한 등을 감지"""
    
    def __init__(self, batch_manager):
        self.batch_manager = batch_manager
        self.log_buffer = []
        self.max_log_buffer = 100  # 최대 로그 버퍼 크기
    
    def write(self, message):
        """로그 메시지 쓰기 (tqdm_out 인터페이스와 호환)"""
        # 로그 버퍼에 추가
        self.log_buffer.append(message)
        if len(self.log_buffer) > self.max_log_buffer:
            self.log_buffer.pop(0)  # 가장 오래된 로그 제거
        
        # API 제한 감지
        if self.batch_manager.is_api_limit_log(message):
            self.batch_manager.log("LogCapture: API 제한 감지됨")
            with self.batch_manager.lock:
                self.batch_manager.rate_limited = True
                self.batch_manager.rate_limit_time = time.time() + 30  # 기본 30초 대기
        
        # 원래 로그 출력
        if self.batch_manager.tqdm_out:
            self.batch_manager.tqdm_out.write(message)
    
    def flush(self):
        """tqdm_out 인터페이스와 호환"""
        pass

def translate_file_parallel(input_file, config, max_threads=5, chunk_size=6000, tqdm_out=None):
    """병렬로 파일 번역"""
    from batch_translator import translate_with_gemini, create_chunks, save_result
    
    # 청크 생성
    chunks = create_chunks(input_file, chunk_size)
    total_chunks = len(chunks)
    
    # 출력 파일 경로 생성
    input_path = Path(input_file)
    output_path = input_path.with_name(f"{input_path.stem}_result{input_path.suffix}")
    
    # 이미 존재하는 출력 파일 초기화
    if output_path.exists():
        output_path.unlink()
    
    # 병렬 처리 관리자 설정
    batch_manager = BatchThreadManager(
        process_func=translate_with_gemini,
        config=config,
        tqdm_out=tqdm_out,
        initial_threads=2,
        max_threads=max_threads,
        min_threads=1,
        max_retries=10
    )
    
    # 모든 청크 추가
    for i, chunk in enumerate(chunks):
        batch_manager.add_chunk(i, chunk)
    
    # 처리 시작
    batch_manager.start()
    
    try:
        # 진행 상황 업데이트용 타이머
        start_time = time.time()
        last_progress_update = 0
        
        # 완료 대기 루프
        while True:
            # 현재 진행 상황 가져오기
            progress = batch_manager.get_progress()
            completed = progress["completed"]
            failed = progress["failed"]
            total = progress["total"]
            
            # 모든 처리가 완료되었는지 확인
            if completed + failed >= total and total > 0:
                break
            
            # 진행 상황 업데이트 (1초에 한 번)
            current_time = time.time()
            if current_time - last_progress_update >= 1.0:
                last_progress_update = current_time
                elapsed = current_time - start_time
                completion_percent = (completed + failed) / total * 100 if total > 0 else 0
                
                if tqdm_out:
                    tqdm_out.write(f"진행 상황: {completed}/{total} 완료, {failed} 실패 ({completion_percent:.1f}%) - 경과 시간: {elapsed:.1f}초")
            
            # 잠시 대기
            time.sleep(0.5)
        
        # 결과 저장
        ordered_results = batch_manager.get_ordered_results()
        
        # 순서대로 결과 저장
        for result in ordered_results:
            if result and result != "[처리 실패]":
                save_result(result, output_path)
        
        # 처리 요약 정보
        final_progress = batch_manager.get_progress()
        elapsed_time = time.time() - start_time
        
        if tqdm_out:
            tqdm_out.write("\n번역 결과 요약:")
            tqdm_out.write(f"- 총 청크 수: {total_chunks}")
            tqdm_out.write(f"- 성공한 청크: {final_progress['completed']}")
            tqdm_out.write(f"- 실패한 청크: {final_progress['failed']}")
            tqdm_out.write(f"- API 제한 발생 횟수: {final_progress['rate_limited']}")
            tqdm_out.write(f"- 총 소요 시간: {elapsed_time:.1f}초")
            tqdm_out.write(f"번역이 완료되었습니다. 결과가 {output_path}에 저장되었습니다.")
        
        return output_path
    
    finally:
        # 종료 전 반드시 스레드 정리
        batch_manager.stop()

def extract_pronouns_parallel(input_file, config, tqdm_out=None):
    """병렬로 고유명사 추출"""
    from batch_translator import create_chunks
    from batch_translator_pronouns import extract_pronouns_from_file, PronounExtractor
    
    # 청크 생성
    chunks = create_chunks(input_file, config.get("chunk_size", 6000))
    
    # 표본 비율 설정
    sample_ratio = config.get("pronoun_sample_ratio", 0.5)
    
    # PronounExtractor 인스턴스 생성
    extractor = PronounExtractor(config, tqdm_out)
    
    # 표본 청크 선택
    sample_chunks = extractor.select_sample_chunks(chunks, sample_ratio)
    
    # 출력 파일 경로 생성
    input_path = Path(input_file)
    output_base_path = input_path.with_name(f"{input_path.stem}")
    
    # 표본 청크에서 고유명사 추출 및 CSV 생성
    return extractor.process_sample_chunks(sample_chunks, output_base_path)
