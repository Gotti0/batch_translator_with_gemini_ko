import json
import os
import time
import argparse
from pathlib import Path
from tqdm import tqdm
import google.generativeai as genai
import random
import re

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

def translate_with_gemini(text, config, retry_count=0, max_retries=5):
    """Gemini API를 사용하여 텍스트를 번역합니다."""
    try:
        api_key = config["api_key"]
        model_name = config["model_name"]
        temperature = config.get("temperature", 0.4)
        top_p = config.get("top_p", 0.9)
        
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
                def translate_split_chunk(chunk_text, depth=0, max_depth=10):
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
                            first_result = translate_split_chunk(first_half, depth + 1, max_depth)
                            
                            print(f"\n청크 뒷부분 번역 시작 (크기: {len(second_half)})")
                            second_result = translate_split_chunk(second_half, depth + 1, max_depth)
                            
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
                            return translate_split_chunk(chunk_text, depth, max_depth)
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

def parse_arguments():
    """명령줄 인수를 파싱합니다."""
    parser = argparse.ArgumentParser(description='텍스트 파일을 청크로 나누어 Gemini API로 번역합니다.')
    parser.add_argument('--config', type=str, default='config.json', help='JSON 설정 파일 경로')
    parser.add_argument('--input', type=str, help='번역할 텍스트 파일 경로')
    parser.add_argument('--chunk-size', type=int, default=6000, help='청크 최대 크기 (기본값: 6000)')
    parser.add_argument('--delay', type=float, default=2.0, help='API 호출 사이의 지연 시간(초) (기본값: 2.0)')
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
    
    # 청크 생성
    chunks = create_chunks(txt_file_path, args.chunk_size)
    total_chunks = len(chunks)
    
    print(f"총 {total_chunks}개의 청크로 분할되었습니다.")
    print(f"출력 파일: {output_path}")
    
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
    print(f"번역이 완료되었습니다. 결과가 {output_path}에 저장되었습니다.")

if __name__ == "__main__":
    main()
