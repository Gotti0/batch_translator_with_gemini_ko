import json
import os
import logging
import urllib.request
import re
from google import genai
from google.genai import types

from .job_tracker import JobTracker

logger = logging.getLogger(__name__)

class GeminiApiService:
    def __init__(self, config_manager):
        self.config = config_manager
        self.client = None
        self.job_tracker = JobTracker()
        api_key = self.config.get('gemini_api_key')
        if api_key and api_key != "YOUR_GEMINI_API_KEY":
            self.client = genai.Client(api_key=api_key)

    def _split_text_into_chunks(self, text, max_chunk_size):
        """
        줄바꿈을 존중하면서 텍스트를 지정된 최대 크기의 청크로 분할합니다.
        한 줄이 최대 크기를 초과하면 강제로 분할합니다.
        """
        chunks = []
        current_chunk_lines = []
        current_chunk_size = 0
        
        lines = text.splitlines(keepends=True)

        for line in lines:
            line_len = len(line)

            # 한 줄이 max_chunk_size보다 큰 경우 강제 분할
            if line_len > max_chunk_size:
                # 현재까지의 청크를 먼저 추가
                if current_chunk_lines:
                    chunks.append("".join(current_chunk_lines))
                    current_chunk_lines = []
                    current_chunk_size = 0
                
                # 긴 라인을 max_chunk_size에 맞춰 분할
                for i in range(0, line_len, max_chunk_size):
                    chunks.append(line[i:i + max_chunk_size])
                continue

            # 이 줄을 추가하면 청크가 너무 커지는 경우, 현재 청크를 완료하고 새 청크 시작
            if current_chunk_size + line_len > max_chunk_size and current_chunk_lines:
                chunks.append("".join(current_chunk_lines))
                current_chunk_lines = [line]
                current_chunk_size = line_len
            # 그렇지 않으면 현재 청크에 줄 추가
            else:
                current_chunk_lines.append(line)
                current_chunk_size += line_len

        # 마지막 남은 청크 추가
        if current_chunk_lines:
            chunks.append("".join(current_chunk_lines))
            
        return chunks

    def _prepare_requests(self, source_file, model_id):
        """ConfigManager의 설정을 사용하여 요청 파일을 생성합니다."""
        requests_file = "temp_requests.jsonl"

        system_instruction = {"parts": [{"text": self.config.get('system_instruction')}]}
        prefill = self.config.get('prefill_cached_history', [])
        generation_config = {
            'temperature': self.config.get('temperature', 1.0),
            'top_p': self.config.get('top_p', 0.95),
            'thinkingConfig': {'thinking_budget': self.config.get('thinking_budget', 128) },
        }
        
        max_chunk_size = self.config.get('chunk_size', 6000)

        with open(source_file, 'r', encoding='utf-8') as f_in:
            content = f_in.read()

        chunks = self._split_text_into_chunks(content, max_chunk_size)
        logger.info(f"Content split into {len(chunks)} chunks with max size {max_chunk_size}, respecting newlines.")

        with open(requests_file, 'w', encoding='utf-8') as f_out:
            for i, chunk in enumerate(chunks):
                if not chunk: continue # Skip empty chunks
                
                request_contents = prefill + [{'role': 'user', 'parts': [{'text': chunk}]}]
                request = {
                    "model": f"models/{model_id}",
                    "contents": request_contents,
                    "system_instruction": system_instruction,
                    "generation_config": generation_config,
                    "safety_settings": [
                        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                    ]
                }
                f_out.write(json.dumps({"key": f"chunk_{i+1}", "request": request}, ensure_ascii=False) + '\n')

        return requests_file

    def create_batch_job(self, source_file_path):
        """소스 파일로부터 배치 번역 작업을 생성하고 실행합니다."""
        if not self.client:
            raise ValueError("API client is not initialized. Check your API key.")

        model_id = self.config.get('model_name', 'gemini-2.5-flash')
        requests_file = self._prepare_requests(source_file_path, model_id)

        try:
            # 1. 파일 업로드
            logger.info(f"Uploading request file ('{requests_file}') to the File API.")
            uploaded_file = self.client.files.upload(
                file=requests_file,
                config=types.UploadFileConfig(mime_type='application/json')
            )
            logger.info(f"File uploaded successfully: {uploaded_file.name}")

            # 2. 배치 작업 생성
            logger.info("Creating the batch translation job.")
            model_name = f"models/{model_id}"
            batch_job = self.client.batches.create(
                model=model_name,
                src=uploaded_file.name,
                config={'display_name': f'translation-{os.path.basename(source_file_path)}'}
            )
            logger.info(f"Batch job created successfully: {batch_job.name}")
            
            # Track the new job with its source file
            self.job_tracker.add_job(batch_job.name, source_file_path)
            
            return batch_job

        except Exception as e:
            logger.error(f"An error occurred during batch job creation: {e}", exc_info=True)
            # Re-raise the exception to be caught by the ViewModel
            raise e
        finally:
            # 3. 임시 파일 삭제
            if os.path.exists(requests_file):
                # os.remove(requests_file) # 디버깅을 위해 임시 주석 처리
                logger.info(f"Debugging: Temporary request file '{requests_file}' was not deleted.")

    def list_batch_jobs(self):
        if not self.client:
            return []
        # Add page_size to the config as per the example
        return self.client.batches.list(config={'page_size': 50})

    def _retry_chunk_with_divide_and_conquer(self, text_to_translate, original_request):
        """
        Recursively tries to translate a failed chunk by splitting it in half.
        """
        # Base case: If the text is too short to split, give up.
        if len(text_to_translate) < 50:
            logger.error(f"Chunk is too short to split further and failed: '{text_to_translate}'")
            return f"[번역 실패: 최소 단위 도달] {text_to_translate}"

        # Prepare a new request for synchronous translation
        model_id = original_request['model']
        sync_request = {
            "contents": original_request['contents'][:-1] + [{'role': 'user', 'parts': [{'text': text_to_translate}]}],
            "system_instruction": original_request.get('system_instruction'),
            "generation_config": original_request.get('generation_config'),
            "safety_settings": original_request.get('safety_settings')
        }

        try:
            # Try to translate the whole chunk synchronously
            response = self.client.generate_content(
                model=model_id,
                **sync_request
            )
            logger.info(f"Successfully translated a sub-chunk: '{text_to_translate[:30]}...' ")
            return response.text
        except Exception as e:
            logger.warning(f"Sub-chunk failed, splitting in half. Error: {e}. Text: '{text_to_translate[:30]}...' ")
            # If it fails, split and recurse
            mid = len(text_to_translate) // 2
            first_half = text_to_translate[:mid]
            second_half = text_to_translate[mid:]
            
            translated_first = self._retry_chunk_with_divide_and_conquer(first_half, original_request)
            translated_second = self._retry_chunk_with_divide_and_conquer(second_half, original_request)
            
            return translated_first + translated_second

    def download_and_process_results(self, job, save_path):
        """결과 파일을 다운로드하여 파싱하고 최종 텍스트 파일로 저장합니다."""
        result_file_name = job.dest.file_name
        logger.info(f"결과가 파일에 저장되었습니다: {result_file_name}")
        logger.info("결과 파일 다운로드 및 파싱 중...")
        
        try:
            file_content_bytes = self.client.files.download(file=result_file_name)
            file_content = file_content_bytes.decode('utf-8')
        except Exception as e:
            logger.error(f"결과 파일 다운로드 중 오류 발생: {e}", exc_info=True)
            return
            
        translations = {}
        max_key = 0
        for line in file_content.splitlines():
            if not line:
                continue
                        
            try:
                parsed_response = json.loads(line)
                key_num = int(parsed_response['key'].split('_')[1])
                max_key = max(max_key, key_num)
                full_response_str = json.dumps(parsed_response, indent=2, ensure_ascii=False)

                if 'response' in parsed_response and parsed_response['response'].get('candidates'):
                    candidate = parsed_response['response']['candidates'][0]
                    finish_reason = candidate.get('finish_reason', 'UNKNOWN')
                                        
                    if finish_reason == "SAFETY":
                        translations[key_num] = f"[번역 차단됨 (SAFETY) - 전체 응답 객체:]\n{full_response_str}"
                        logger.error(f"문단 {key_num} 처리 실패/차단됨: Finish reason was SAFETY.")
                    else:
                        translations[key_num] = candidate.get('content', {}).get('parts', [{}])[0].get('text', '[번역 내용 없음]')
                                
                elif 'response' in parsed_response:
                    translations[key_num] = f"[번역 차단됨 (Candidates 없음) - 전체 응답 객체:]\n{full_response_str}"
                    feedback = parsed_response['response'].get('prompt_feedback', {})
                    logger.error(f"문단 {key_num} 처리 실패/차단됨: Candidates 리스트가 비어있습니다. Feedback: {feedback}")
                
                else:
                    translations[key_num] = f"[번역 실패 (No Response) - 전체 응답 객체:]\n{full_response_str}"
                    error_message = parsed_response.get('error', {}).get('message', '알 수 없는 오류')
                    logger.error(f"문단 {key_num} 처리 실패/차단됨: {error_message}")

            except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
                key_str = f"'{line.split(',')[0]}'" if ',' in line else "알 수 없는 키"
                max_key += 1 
                translations[max_key] = f"[결과 라인 파싱 오류 - 원본 라인:]\n{line}"
                logger.warning(f"{key_str}에 해당하는 결과 라인 파싱 중 예외 발생: {e}")

        logger.info(f"결과를 '{save_path}' 파일에 저장합니다.")
        with open(save_path, 'w', encoding='utf-8') as f:
            for i in range(1, max_key + 1):
                f.write(translations.get(i, f"[문단 {i} 결과 누락]"))
                f.write("\n\n")
                
        logger.info("모든 작업이 완료되었습니다.")

    def delete_batch_job(self, job_name):
        if not self.client:
            raise ValueError("API client is not initialized.")
        self.client.batches.delete(name=job_name)
        # Also remove from tracker
        self.job_tracker.remove_job(job_name)
        logger.info(f"Job '{job_name}' deleted from API and tracker.")
