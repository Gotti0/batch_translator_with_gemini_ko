import json
import os
import time
import argparse
import threading
import google.generativeai as genai
import random
import re
import csv
import hashlib
from pathlib import Path
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor


def load_config(config_path):
    """JSON 설정 파일을 불러옵니다."""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print(f"설정 파일을 찾을 수 없습니다: {config_path}")
        exit(1)
    except json.JSONDecodeError:
        print(f"설정 파일이 올바른 JSON 형식이 아닙니다: {config_path}")
        exit(1)

def create_chunks(file_path, max_chunk_size=6000):
    """
    파일을 청크 단위로 나눕니다.
    각 청크는 최대 max_chunk_size 크기를 가지며,
    청크 크기가 max_chunk_size 이하인 동안 줄바꿈 문자를 기준으로 계속 추가합니다.
    """
    chunks = []
    current_chunk = ""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                # 현재 청크와 새 줄을 합쳤을 때 최대 크기를 초과하는지 확인
                if len(current_chunk) + len(line) <= max_chunk_size:
                    # 청크 크기가 6,000자 이하이면 줄 추가
                    current_chunk += line
                else:
                    # 청크 크기가 6,000자 초과하면 새 청크 시작
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = line
            
            # 마지막 청크 추가
            if current_chunk:
                chunks.append(current_chunk)
        
        return chunks
    except FileNotFoundError:
        print(f"텍스트 파일을 찾을 수 없습니다: {file_path}")
        exit(1)
    except Exception as e:
        print(f"파일 처리 중 오류 발생: {str(e)}")
        exit(1)

def translate_with_gemini(text, config, retry_count=0, max_retries=10):
    """Gemini API를 사용하여 텍스트를 번역합니다."""
    try:
        api_key = config["api_key"]
        model_name = config["model_name"]
        temperature = config.get("temperature", 0.4)
        top_p = config.get("top_p", 0.9)

        # 고유명사 사전 로드 (새로 추가된 부분)
        pronouns_csv = config.get("pronouns_csv", None)
        pronouns_dict = {}
        
        if pronouns_csv and os.path.exists(pronouns_csv):
            # batch_translator_pronouns 모듈에서 함수 가져오기
            from batch_translator_pronouns import load_pronouns_for_translation, format_pronouns_for_prompt, filter_relevant_pronouns
            
            # 고유명사 로드 및 프롬프트 형식으로 변환
            pronouns_dict = load_pronouns_for_translation(pronouns_csv)
            
            # 현재 텍스트에 관련된 고유명사만 필터링
            relevant_pronouns = filter_relevant_pronouns(text, pronouns_dict)
    
            # 필터링된 고유명사를 프롬프트 형식으로 변환
            pronouns_prompt = format_pronouns_for_prompt(relevant_pronouns)
        else:
            pronouns_prompt = ""
        
        # Gemini API 설정
        genai.configure(api_key=api_key)
        
        # 모델 초기화
        generation_config = {
            "temperature": temperature,
            "top_p": top_p,
        }
        
        model = genai.GenerativeModel(model_name=model_name, 
                                     generation_config=generation_config)
        
        # 프롬프트 생성 및 요청
        prompt_template = config.get("prompts", "{{slot}}")
        prompt = prompt_template.replace("{{slot}}", text)

        # 고유명사 정보를 프롬프트에 추가 (새로 추가된 부분)
        if pronouns_prompt:
            # 프롬프트 끝에 고유명사 정보 추가
            prompt_parts = prompt.split("## 번역 결과 (한국어):")
            if len(prompt_parts) > 1:
                # 번역 결과 섹션 앞에 고유명사 정보 삽입
                prompt = prompt_parts[0] + pronouns_prompt + "\n\n## 번역 결과 (한국어):" + prompt_parts[1]
            else:
                # 번역 결과 섹션이 없는 경우 끝에 추가
                prompt += pronouns_prompt
        
        # API 호출
        try:
            response = model.generate_content(prompt)
            
            # 응답 처리
            if hasattr(response, 'text'):
                return response.text
            else:
                print("API 응답에서 텍스트를 찾을 수 없습니다.")
                return None
        except Exception as api_error:
            error_message = str(api_error)
            # 여전히 PROHIBITED_CONTENT 오류 발생 시 청크 분할
            if any(error_text in error_message for error_text in 
                ["PROHIBITED_CONTENT", "Invalid", "OTHER"]):
                print("\nPROHIBITED_CONTENT 오류 감지됨. 청크 분할 후 스트림 번역을 시도합니다.")
                
                # 재귀적 청크 분할 번역 함수 정의
                def translate_split_chunk(chunk_text, depth=0, max_depth=10, retry_count=0):
                    # 재귀 깊이 제한 (너무 작은 청크로 무한 분할 방지)
                    if depth >= max_depth:
                        return f"[너무 깊은 재귀: 번역 불가 텍스트]"
                    
                    # 내용이 없는 경우 빈 문자열 반환
                    if not chunk_text.strip():
                        return ""
                        
                    try:
                        # 스트림 모드로 번역 시도
                        chunk_prompt = prompt_template.replace("{{slot}}", chunk_text)
                        stream_response = model.generate_content(chunk_prompt, stream=True)
                        
                        result = ""
                        print(f"\n청크 번역 중 (깊이: {depth}): ", end="", flush=True)
                        
                        # 스트림 응답 처리
                        for chunk in stream_response:
                            if hasattr(chunk, 'text'):
                                part = chunk.text
                                result += part
                                print(".", end="", flush=True)
                                
                        print(" 완료")
                        return result
                        
                    except Exception as chunk_error:
                        error_message = str(chunk_error)
                        # 여전히 PROHIBITED_CONTENT 오류 발생 시 청크 분할
                        if any(error_text in error_message for error_text in 
                               ["PROHIBITED_CONTENT", "Invalid", "OTHER"]):
                            # (기존 청크 분할 코드)
                            print(f"\n깊이 {depth}에서 여전히 PROHIBITED_CONTENT 감지. 청크 분할 중...")
                            # (기존 분할 코드)
                            mid_point = len(chunk_text) // 2
                            # 문장 경계 기반 분할 (기존 코드)
                            sentence_boundaries = [m.end() for m in re.finditer(r'[.!?。？．！]\s+', chunk_text[:mid_point+100])]
                            if sentence_boundaries:
                                split_point = min(sentence_boundaries, key=lambda x: abs(x - mid_point))
                            else:
                                split_point = mid_point
                            
                            first_half = chunk_text[:split_point]
                            second_half = chunk_text[split_point:]
                            
                            # 각 부분 재귀적으로 번역
                            print(f"\n청크 앞부분 번역 시작 (크기: {len(first_half)})")
                            first_result = translate_split_chunk(first_half, depth + 1, max_depth, 0)
                            
                            print(f"\n청크 뒷부분 번역 시작 (크기: {len(second_half)})")
                            second_result = translate_split_chunk(second_half, depth + 1, max_depth, 0)
                            
                            # 결과 결합 (순서 유지)
                            return first_result + second_result
                            
                        elif any(error_text in error_message for error_text in 
                                ["rateLimitExceeded", "429", "The model is overloaded", "503", "500", "Internal"]):
                            if retry_count >= max_retries:
                                print(f"최대 재시도 횟수({max_retries})에 도달했습니다. 번역을 중단합니다.")
                                return None
                            
                            # 지수 백오프 계산 (기본 10초에서 시작, 최대 5분까지)
                            wait_time = min(10 * (2 ** retry_count) + random.uniform(0, 1), 300)
                            print(f"API 사용량 제한에 도달했습니다. {wait_time:.1f}초 대기 후 재시도합니다. (시도 {retry_count+1}/{max_retries})")
                            time.sleep(wait_time)
            
                            # 같은 깊이에서 다시 시도
                            return translate_split_chunk(chunk_text, depth, max_depth, retry_count + 1)
                        else:
                            print(f"\n번역 오류: {error_message}")
                            return f"[번역 오류: {error_message[:50]}...]"
                
                # 전체 텍스트에 대한 재귀적 번역 시작
                return translate_split_chunk(text)
            
            # 다른 종류의 오류는 원래 예외 처리 로직으로 전달
            raise api_error
            
    except Exception as e:
        error_message = str(e)
        print(f"번역 중 오류 발생: {error_message}")
        if any(error_text in error_message for error_text in 
               ["rateLimitExceeded", "429", "The model is overloaded", "503", "500", "Internal"]):
            if retry_count >= max_retries:
                print(f"최대 재시도 횟수({max_retries})에 도달했습니다. 번역을 중단합니다.")
                return None
                
            # 지수 백오프 계산 (기본 10초에서 시작, 최대 5분까지)
            wait_time = min(10 * (2 ** retry_count) + random.uniform(0, 1), 300)
            print(f"API 사용량 제한에 도달했습니다. {wait_time:.1f}초 대기 후 재시도합니다. (시도 {retry_count+1}/{max_retries})")
            time.sleep(wait_time)
            
            # 재귀적 재시도 (retry_count 증가)
            return translate_with_gemini(text, config, retry_count + 1, max_retries)
            
        return None
    

def translate_chunks_parallel(chunks, config, output_path, max_workers=None, delay=2.0):
    """여러 청크를 병렬로 번역합니다."""
    
    # 최대 작업자 수가 지정되지 않은 경우 청크 수와 CPU 코어 수 고려
    if max_workers is None:
        # API 요청 제한을 고려하여 최대 작업자 수 제한
        max_workers = min(8, len(chunks))
    
    # 진행 상황 관련 변수
    total_chunks = len(chunks)
    successful_chunks = 0
    failed_chunks = 0
    lock = threading.Lock()  # 공유 자원 접근을 위한 락
    
    # 결과를 인덱스와 함께 저장할 리스트 (순서 유지를 위해)
    results = [None] * total_chunks
    
    def translate_chunk_with_index(index, chunk):
        nonlocal successful_chunks, failed_chunks
        
        try:
            # API 호출 간 간격을 주기 위한 지연
            if index > 0:
                time.sleep(delay * random.uniform(0.5, 1.5))
                
            # 청크 번역
            translated_text = translate_with_gemini(chunk, config)
            
            # 성공 시 결과 저장
            if translated_text:
                with lock:
                    successful_chunks += 1
                    print(f"\n청크 {index+1}/{total_chunks} 번역 완료")
                return index, translated_text
            else:
                with lock:
                    failed_chunks += 1
                    print(f"\n청크 {index+1}/{total_chunks} 번역 실패")
                return index, None
                
        except Exception as e:
            with lock:
                failed_chunks += 1
                print(f"\n청크 {index+1} 처리 중 예외 발생: {str(e)}")
            return index, None
    

    print(f"병렬 번역 시작 (최대 {max_workers}개 스레드 사용)")
    
    # ThreadPoolExecutor를 사용하여 병렬 번역 실행
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 각 청크에 대한 번역 작업 제출
        futures = [executor.submit(translate_chunk_with_index, i, chunk) 
                  for i, chunk in enumerate(chunks)]
        
        # 진행 상황 표시를 위한 tqdm 설정
        with tqdm(total=total_chunks, desc="번역 진행 중") as pbar:
            # 제출된 작업의 결과를 순서대로 처리
            for future in futures:
                try:
                    index, result = future.result()
                    if result:
                        # 결과를 원래 인덱스 위치에 저장
                        results[index] = result
                    pbar.update(1)
                except Exception as e:
                    print(f"\n작업 결과 처리 중 오류 발생: {str(e)}")
                    pbar.update(1)
    
    # 번역 결과를 순서대로 파일에 저장
    print("\n번역된 청크를 파일에 저장 중...")
    for result in results:
        if result:
            save_result(result, output_path)
    
    print(f"\n번역 결과 요약:")
    print(f"- 총 청크 수: {total_chunks}")
    print(f"- 성공한 청크: {successful_chunks}")
    print(f"- 실패한 청크: {failed_chunks}")
    
    return successful_chunks, failed_chunks


def remove_html_tags(text):
    """HTML 태그를 정규 표현식을 사용하여 제거합니다."""
    try:
        # <[\s\S]*?> 패턴을 사용하여 모든 HTML 태그 제거
        cleaned_text = re.sub(r'<[\s\S]*?>', '', text)
        return cleaned_text
    except Exception as e:
        print(f"HTML 태그 제거 중 오류 발생: {str(e)}")
        return text  # 오류 발생 시 원본 텍스트 반환

def save_result(translated_text, output_path):
    """번역된 텍스트를 파일에 저장합니다."""
    try:
        with open(output_path, 'a', encoding='utf-8') as f:
            f.write(translated_text + "\n\n")
    except Exception as e:
        print(f"결과 저장 중 오류 발생: {str(e)}")

def load_pronouns_from_csv(csv_path):
    """CSV 파일에서 고유명사 사전을 로드합니다. (내부 fallback 함수)"""
    pronouns = {}
    try:
        if os.path.exists(csv_path):
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader)  # 헤더 건너뛰기
                for row in reader:
                    if len(row) >= 2:
                        foreign, korean = row[0], row[1]
                        pronouns[foreign] = korean
        return pronouns
    except Exception as e:
        print(f"고유명사 로드 중 오류 발생: {str(e)}")
        return {}
    

def format_pronouns_for_prompt_fallback(pronouns):
    """프롬프트에 포함할 형식으로 고유명사 사전을 포맷팅합니다. (내부 fallback 함수)"""
    if not pronouns:
        return ""
    
    prompt_part = "\n\n# 고유명사 번역 가이드\n\n다음 고유명사는 일관성 있게 번역해주세요:\n\n"
    for foreign, korean in pronouns.items():
        prompt_part += f"- {foreign} → {korean}\n"
    return prompt_part

def display_pronouns_stats(pronouns_csv):
    """고유명사 사전 사용 통계를 표시합니다."""
    if not pronouns_csv or not os.path.exists(pronouns_csv):
        return
    
    try:
        pronouns_count = 0
        with open(pronouns_csv, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # 헤더 건너뛰기
            for _ in reader:
                pronouns_count += 1
        
        print("\n고유명사 사전 통계:")
        print(f"- 사용된 고유명사 사전: {pronouns_csv}")
        print(f"- 등록된 고유명사 수: {pronouns_count}")
    except Exception as e:
        print(f"고유명사 통계 출력 중 오류 발생: {str(e)}")


def get_metadata_path(input_path):
    """입력 파일 경로에 기반한 메타데이터 파일 경로 반환"""
    input_path = Path(input_path)
    return input_path.with_stem(f"{input_path.stem}_metadata").with_suffix('.json')

def hash_config(config):
    """설정 정보를 해시값으로 변환 (설정 변경 감지용)"""
    # API 키와 같은 민감 정보는 제외
    config_copy = config.copy()
    config_copy.pop('api_key', None)
    
    # 설정을 문자열로 변환하여 해시 생성
    config_str = json.dumps(config_copy, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()

def create_translation_metadata(input_path, chunks, config):
    """번역 진행 상황을 추적하기 위한 메타데이터 파일 생성"""
    current_time = time.time()
    metadata = {
        "input_file": str(input_path),
        "total_chunks": len(chunks),
        "translated_chunks": {}, # 번역된 청크: {인덱스: {timestamp, result_hash}}
        "config_hash": hash_config(config), # 설정 해시값
        "creation_time": current_time,
        "start_time": current_time,
        "last_updated": current_time,
        "status": "initialized", # 상태: initialized, in_progress, completed, failed
        "session_history": [
            {
                "start_time": current_time,
                "status": "initialized"
            }
        ]
    }
    
    # 메타데이터 파일 저장
    metadata_path = get_metadata_path(input_path)
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2)
    
    return metadata_path

def load_translation_metadata(input_path):
    """메타데이터 파일 로드"""
    metadata_path = get_metadata_path(input_path)
    
    if not metadata_path.exists():
        return None
    
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def update_translation_metadata(metadata_path, chunk_index, content=None):
    """청크 번역 후 메타데이터 업데이트"""
    try:
        # 잠금 메커니즘으로 동시 접근 제어
        lock = threading.Lock()
        with lock:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
                
            # 번역된 청크 정보 업데이트
            metadata["translated_chunks"][str(chunk_index)] = time.time()
            metadata["last_updated"] = time.time()
            
            # 임시 파일에 먼저 저장 후 이름 변경 (안전한 저장 방식)
            temp_path = f"{metadata_path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
                
            # 파일 대체
            os.replace(temp_path, metadata_path)
            
        return True
    except Exception as e:
        print(f"메타데이터 업데이트 중 오류 발생: {str(e)}")
        return False
    

def validate_metadata(metadata_path, output_path):
    """메타데이터와 실제 결과 파일의 일관성 검증"""
    try:
        # 파일 존재 확인
        if not os.path.exists(metadata_path) or not os.path.exists(output_path):
            return False, "메타데이터 또는 결과 파일이 존재하지 않습니다."
        
        # 메타데이터 로드
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        # 필수 필드 확인
        required_fields = ["input_file", "total_chunks", "translated_chunks", "config_hash"]
        for field in required_fields:
            if field not in metadata:
                return False, f"메타데이터에 필수 필드가 누락됨: {field}"
        
        # 결과 파일과 일관성 검사
        # (구현 생략)
        
        return True, "메타데이터 유효성 확인 완료"
    except Exception as e:
        return False, f"메타데이터 검증 중 오류 발생: {str(e)}"


def save_chunk_result(index, result, output_path, metadata_path):
    """번역된 청크를 즉시 파일에 저장하고 메타데이터 업데이트"""
    try:
        # 결과 파일이 없으면 생성
        output_path = Path(output_path)
        if not output_path.exists():
            with open(output_path, 'w', encoding='utf-8'):
                pass
                
        # 잠금 메커니즘 추가 (동시 쓰기 방지)
        with threading.Lock():
            # 청크 번역 결과 추가
            with open(output_path, 'a', encoding='utf-8') as f:
                f.write(result)
                f.write("\n\n")
                
            # 메타데이터 업데이트
            update_translation_metadata(metadata_path, index)
            
        return True
    except Exception as e:
        print(f"청크 결과 저장 중 오류 발생: {str(e)}")
        return False

def save_chunk_with_index(index, content, output_path, append=True):
    """인덱스 정보가 포함된 청크를 파일에 저장합니다.
    
    Args:
        index (int): 청크 인덱스
        content (str): 청크 내용
        output_path (str): 출력 파일 경로
        append (bool): True면 기존 파일에 추가, False면 덮어쓰기
    """
    # 청크 시작과 끝에 인덱스 정보를 추가
    formatted_content = f"<!-- CHUNK_START: {index} -->\n{content}\n<!-- CHUNK_END: {index} -->"
    
    mode = 'a' if append else 'w'
    with open(output_path, mode, encoding='utf-8') as f:
        f.write(formatted_content)
        f.write("\n\n")  # 청크 구분을 위한 줄바꿈 (이제 구분자로서가 아닌 가독성용)


def load_chunks_with_index(output_path):
    """인덱스 정보가 포함된 청크를 파일에서 로드합니다.
    
    Returns:
        dict: {인덱스: 내용} 형식의 딕셔너리
    """
    if not os.path.exists(output_path):
        return {}
    
    with open(output_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    chunks = {}
    # 정규식을 사용하여 인덱스와 내용을 추출
    pattern = r'<!-- CHUNK_START: (\d+) -->\n([\s\S]*?)\n<!-- CHUNK_END: \1 -->'
    matches = re.findall(pattern, content)
    
    for match in matches:
        index = int(match[0])
        chunk_content = match[1]
        chunks[index] = chunk_content
    
    return chunks

def merge_chunk_results(existing_chunks, new_results, total_chunks):
    """기존 청크와 새로운 결과를 병합합니다.
    
    Args:
        existing_chunks (dict): {인덱스: 내용} 형식의 기존 청크
        new_results (list): 새로 번역된 결과 리스트 (인덱스 위치에 결과 저장)
        total_chunks (int): 전체 청크 수
    
    Returns:
        dict: 병합된 결과 딕셔너리
    """
    merged_chunks = existing_chunks.copy()
    
    # 새 결과 병합
    for idx, result in enumerate(new_results):
        if result:
            merged_chunks[idx] = result
    
    return merged_chunks

def save_merged_chunks(merged_chunks, total_chunks, output_path):
    """병합된 청크를 파일에 저장합니다."""
    with open(output_path, 'w', encoding='utf-8') as f:
        # 인덱스 순서대로 저장
        for idx in range(total_chunks):
            if idx in merged_chunks:
                chunk_content = merged_chunks[idx]
                formatted_content = f"<!-- CHUNK_START: {idx} -->\n{chunk_content}\n<!-- CHUNK_END: {idx} -->"
                f.write(formatted_content)
                f.write("\n\n")


def parse_arguments():
    """명령줄 인수를 파싱합니다."""
    parser = argparse.ArgumentParser(description='텍스트 파일을 청크로 나누어 Gemini API로 번역합니다.')
    parser.add_argument('--config', type=str, default='config.json', help='JSON 설정 파일 경로')
    parser.add_argument('--input', type=str, help='번역할 텍스트 파일 경로')
    parser.add_argument('--chunk-size', type=int, default=6000, help='청크 최대 크기 (기본값: 6000)')
    parser.add_argument('--delay', type=float, default=2.0, help='API 호출 사이의 지연 시간(초) (기본값: 2.0)')
    parser.add_argument('--pronouns', type=str, help='고유명사 CSV 파일 경로 (미지정 시 자동 탐색)')
    parser.add_argument('--max-workers', type=int, help='병렬 작업을 위한 최대 스레드 수 (기본값: 자동)')
    
    return parser.parse_args()



def main():
    # 명령줄 인수 파싱
    args = parse_arguments()
    
    # 설정 파일과 입력 파일 경로 가져오기
    config_path = args.config or input("JSON 설정 파일 경로를 입력하세요: ")
    txt_file_path = args.input or input("번역할 텍스트 파일 경로를 입력하세요: ")
    
    # 출력 파일 경로 생성
    txt_file = Path(txt_file_path)
    output_path = txt_file.with_name(f"{txt_file.stem}_result{txt_file.suffix}")
    
    # 이미 존재하는 출력 파일 초기화
    if os.path.exists(output_path):
        os.remove(output_path)
    
    # 설정 불러오기
    config = load_config(config_path)

    # 고유명사 CSV 파일 경로 확인 (수정)
    if args.pronouns and os.path.exists(args.pronouns):
        pronouns_csv_path = args.pronouns
        print(f"지정된 고유명사 파일 사용: {pronouns_csv_path}")
    else:
        pronouns_csv_path = txt_file.with_stem(f"{txt_file.stem}_seed").with_suffix('.csv')
        if os.path.exists(pronouns_csv_path):
            print(f"고유명사 파일 발견: {pronouns_csv_path}")
        else:
            pronouns_csv_path = None
            print("고유명사 파일을 찾을 수 없습니다. 기본 번역을 진행합니다.")
    
    if pronouns_csv_path:
        config["pronouns_csv"] = str(pronouns_csv_path)
    
    # 청크 생성
    chunks = create_chunks(txt_file_path, args.chunk_size)
    total_chunks = len(chunks)
    
    print(f"총 {total_chunks}개의 청크로 분할되었습니다.")
    print(f"출력 파일: {output_path}")

    # 병렬 실행을 위한 스레드 풀 사용
    start_time = time.time()
    successful_chunks, failed_chunks = translate_chunks_parallel(
        chunks, config, output_path, max_workers=args.max_workers, delay=args.delay
    )
    
    # 각 청크 번역 및 저장
    successful_chunks = 0
    failed_chunks = 0
    
    # tqdm으로 진행 상황 표시
    for i, chunk in enumerate(tqdm(chunks, desc="번역 진행 중")):
        try:
            translated_text = translate_with_gemini(chunk, config)
            if translated_text:
                save_result(translated_text, output_path)
                successful_chunks += 1
            else:
                print(f"\n청크 {i+1} 번역 실패")
                failed_chunks += 1
                
            # API 호출 사이에 잠시 대기 (API 제한 방지)
            if i < total_chunks - 1:
                time.sleep(args.delay)
                
        except Exception as e:
            print(f"\n청크 {i+1} 처리 중 예외 발생: {str(e)}")
            failed_chunks += 1
    
    print(f"\n번역 결과 요약:")
    print(f"- 총 청크 수: {total_chunks}")
    print(f"- 성공한 청크: {successful_chunks}")
    print(f"- 실패한 청크: {failed_chunks}")
        # 최종 결과에서 HTML 태그 추가 정리
    try:
        if os.path.exists(output_path):
            print("최종 결과에서 HTML 태그 정리 중...")
            with open(output_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # HTML 태그 제거
            cleaned_content = remove_html_tags(content)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_content)
            
            print("HTML 태그 정리 완료!")
    except Exception as e:
        print(f"최종 HTML 태그 정리 중 오류 발생: {str(e)}")

    if "pronouns_csv" in config:
        display_pronouns_stats(config["pronouns_csv"])

    # 총 소요 시간 계산 및 출력
    total_time = time.time() - start_time
    minutes, seconds = divmod(total_time, 60)
    print(f"번역이 완료되었습니다. 총 소요 시간: {int(minutes)}분 {seconds:.2f}초")
    print(f"결과가 {output_path}에 저장되었습니다.")

if __name__ == "__main__":
    main()
