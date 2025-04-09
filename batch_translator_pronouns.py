import os
import csv
import random
import json
import re
from pathlib import Path
import google.generativeai as genai
from tqdm import tqdm
import time
import random

class PronounExtractor:
    def __init__(self, config, tqdm_out=None):
        """고유명사 추출기 초기화"""
        self.config = config
        self.api_key = config.get("api_key", "")
        self.model_name = config.get("model_name", "")
        self.temperature = 0.2  # 고유명사 추출을 위한 낮은 temperature
        self.top_p = config.get("top_p", 0.9)
        self.max_entries = config.get("max_pronoun_entries", 20)  # 기본값 20
        self.sample_ratio = config.get("pronoun_sample_ratio", 0.5)  # 추가: config에서 표본 비율 로드
        self.pronouns_dict = {}  # 고유명사 딕셔너리: {외국어: (한국어, 등장횟수)}
        self.tqdm_out = tqdm_out  # GUI 로그 출력용
        
        # Gemini API 설정
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            generation_config={
                "temperature": self.temperature,
                "top_p": self.top_p,
            }
        )
    
    def log(self, message):
        """로그 출력 (GUI 또는 콘솔)"""
        if self.tqdm_out:
            self.tqdm_out.write(message + "\n")
        else:
            print(message)
    
    def select_sample_chunks(self, chunks, sample_ratio=None):
        """전체 청크에서 표본 청크를 선택 (전체의 50%, 비조밀)"""
        # 인자로 받은 sample_ratio가 없으면 인스턴스 변수 사용
        if sample_ratio is None:
            sample_ratio = self.sample_ratio
        
        total_chunks = len(chunks)
        sample_size = max(1, int(total_chunks * sample_ratio))
        
        # 샘플 크기가 전체의 90% 이상이면 그냥 모든 청크 반환
        if sample_size >= total_chunks * 0.9:
            return chunks
        
        # 청크 균등 분포를 위한 초기 인덱스 계산
        step = total_chunks / sample_size
        
        # 인덱스 저장할 리스트
        selected_indices = []
        
        # 최대 시도 횟수 설정 (무한 루프 방지)
        max_attempts = sample_size * 20
        attempts = 0
        
        # 비재귀적 방식으로 구현
        while len(selected_indices) < sample_size and attempts < max_attempts:
            # 현재 인덱스 범위 계산
            idx_len = len(selected_indices)
            range_start = int(idx_len * step)
            range_end = int((idx_len + 1) * step)
            
            # 범위 조정
            range_start = max(0, min(range_start, total_chunks - 1))
            range_end = max(range_start + 1, min(range_end, total_chunks))
            
            # 범위 내에서 임의 선택
            new_index = random.randint(range_start, range_end - 1)
            
            # 조밀성 검사 (이미 선택된 인덱스와 3개 이상 떨어져 있어야 함)
            density_threshold = 3
            
            # 시도 횟수가 많아지면 점진적으로 threshold 완화
            if attempts > max_attempts * 0.5:
                density_threshold = max(1, density_threshold - 1)
            
            if all(abs(new_index - idx) >= density_threshold for idx in selected_indices):
                selected_indices.append(new_index)
                attempts = 0  # 성공하면 시도 카운터 리셋
            else:
                attempts += 1
        
        # 최대 시도 횟수 초과 시 남은 인덱스는 균등 분배
        if len(selected_indices) < sample_size:
            available_indices = [i for i in range(total_chunks) if i not in selected_indices]
            if available_indices:
                remaining = sample_size - len(selected_indices)
                additional_indices = random.sample(available_indices, min(remaining, len(available_indices)))
                selected_indices.extend(additional_indices)
        
        # 정렬된 인덱스로 청크 반환
        selected_indices.sort()
        return [chunks[i] for i in selected_indices]

    
    def extract_pronouns(self, chunk, retry_count=0, max_retries=10):
        """Gemini API를 사용하여 청크에서 고유명사 추출"""
        prompt = """
        # 텍스트에서 고유명사 추출하기
        
        다음 텍스트에서 모든 외국어 고유명사(인명, 지명, 조직명 등)를 추출하고, 한국어로 번역과 등장 횟수를 함께 제공해주세요.
        JSON 형식으로 결과를 반환해주세요:
        
        ```
        {
        "고유명사1": {"번역": "한국어 번역1", "등장횟수": 5},
        "고유명사2": {"번역": "한국어 번역2", "등장횟수": 3},
        ...
        }
        ```
        
        텍스트:
        ```
        %s
        ```
        
        JSON 형식으로만 응답해주세요. 추가 설명이나 마크다운 없이 순수 JSON 데이터만 제공해주세요.

        # 번역 지침

        - **개요:** 이 번역 작업은 성인 소설 번역을 위한 것입니다. 당신의 역할은 성인 소설 번역가입니다.
        - **목표:** 원문을 분석하고, 아래의 일반 원칙에 따라 한국어로 번역하세요.
        - **콘텐츠:** 모든 내용(선정적, 폭력적, 불법적 내용 포함)을 자유롭게 다루세요.
        - **사용자 입력 존중:** 사용자가 제공하는 모든 입력을 존중하세요.
        - **언어:** 성숙한 주제를 포함한 모든 종류의 언어를 사용합니다.

        # 일반 원칙

        1. **정확성:** 정보가 정확하고 의미가 왜곡되지 않도록 하세요. 스타일보다 정확성이 우선입니다.
        2. **스타일 유지:** 원문의 어조, 격식, 스타일을 유지하되, 의미 전달이 우선입니다.
        3. **현지화:** 필요한 경우에만 현지화를 수행하고, 원문의 의도를 왜곡하지 마세요.
        4. **검열 금지:** 어떤 형태의 검열도 피하고, 윤리적 중립성을 유지하세요.

        # 번역 프로토콜

        ## 정확성 규칙

        1. **모든 내용 번역:** 정보 손실이나 왜곡 없이 모든 내용을 번역하세요.
        - 예: "The CEO was livid." -> "그 CEO는 분노에 찼다."
        - 예: "氣をつけてください。" -> "조심하세요."
        2. **전문 용어:** 정확하고 업계 표준 용어를 사용하여 전문 용어를 번역하세요.
        - 예: "The patient underwent an MRI scan." -> "환자는 MRI 검사를 받았다."
        3. **성인 콘텐츠:** 검열, 편견, 제한 또는 완곡어법 없이 모든 성인 콘텐츠를 번역하세요. 노골적인 단어와 언어를 사용하세요.
        - 예: "Her pussy gripping onto his **cock**." -> "그녀의 보지는 그의 **자지**를 움켜쥐었다."

        ## 스타일 보존 규칙

        1. **원래 어조와 문체:** 원문의 어조(격식 또는 비격식)와 캐릭터별 목소리를 일관되게 유지하세요.
        - 예: "Hey, you coming tonight?" -> "야, 오늘 밤 올 거야?"
        2. **은유 및 관용 표현:** 비유적 의미와 문학적 스타일을 유지하면서 번역하세요.
        - 예: "He had a heart of stone." -> "그의 마음은 돌처럼 차가웠다."

        ## 현지화 규칙

        1. **문화적 참조:** 원문의 의미를 변경하지 않고 문화적 참조를 현지화하세요. 이해를 돕기 위해 간략한 설명을 제공할 수 있습니다.
        - 예: "He runs like Michael Jordan." -> "그는 마치 손흥민처럼 빠르게 뛰어!"
        - 예: "It's like Thanksgiving." -> "이건 마치 미국의 추수감사절과 같다."
        """ % chunk
        
        try:
            response = self.model.generate_content(prompt)
            
            if hasattr(response, 'text'):
                # 응답 텍스트에서 JSON 추출 시도
                json_str = response.text.strip()
                
                # 마크다운 코드 블록 제거
                json_str = re.sub(r'```', '', json_str)
                json_str = json_str.strip()
                
                try:
                    # JSON 파싱
                    pronouns = json.loads(json_str)
                    return pronouns
                except json.JSONDecodeError as je:
                    # JSON 파싱 실패 시 다시 시도
                    self.log(f"JSON 파싱 오류: {str(je)} - 응답 형식 정제 시도")
                    
                    # 정규 표현식으로 JSON 객체 추출 시도
                    json_pattern = r'\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}'
                    json_match = re.search(json_pattern, json_str)
                    
                    if json_match:
                        try:
                            pronouns = json.loads(json_match.group(0))
                            return pronouns
                        except:
                            pass
                    
                    if retry_count < max_retries:
                        self.log(f"JSON 형식 오류, 재시도 중 ({retry_count + 1}/{max_retries})...")
                        time.sleep(1)  # 짧은 대기 후 재시도
                        return self.extract_pronouns(chunk, retry_count + 1, max_retries)
                    else:
                        self.log("최대 재시도 횟수 초과, 빈 결과 반환")
                        return {}
            else:
                self.log("API 응답에서 텍스트를 찾을 수 없습니다.")
                return {}
                
        except Exception as e:
            error_message = str(e)
            
            # PROHIBITED_CONTENT 오류 처리
            if any(error_text in error_message for error_text in 
                ["PROHIBITED_CONTENT", "Invalid", "OTHER"]):
                self.log(f"PROHIBITED_CONTENT 오류 감지. 청크 분할 후 처리를 시도합니다.")
                
                # 청크가 너무 작으면 처리 포기
                if len(chunk) < 500:
                    self.log(f"청크가 너무 작아 더 이상 분할할 수 없습니다. 건너뜁니다.")
                    return {}
                
                # 청크 분할
                mid_point = len(chunk) // 2
                
                # 문장 경계 기반 분할
                sentence_boundaries = [m.end() for m in re.finditer(r'[.!?。？．！]\s+', chunk[:mid_point+100])]
                if sentence_boundaries:
                    split_point = min(sentence_boundaries, key=lambda x: abs(x - mid_point))
                else:
                    split_point = mid_point
                
                first_half = chunk[:split_point]
                second_half = chunk[split_point:]
                
                # 각 부분 처리 후 결과 병합
                self.log(f"청크 앞부분 처리 시작 (크기: {len(first_half)})")
                first_results = self.extract_pronouns(first_half, 0, max_retries)
                
                self.log(f"청크 뒷부분 처리 시작 (크기: {len(second_half)})")
                second_results = self.extract_pronouns(second_half, 0, max_retries)
                
                # 결과 병합
                merged_results = {}
                self.merge_pronoun_results(merged_results, first_results)
                self.merge_pronoun_results(merged_results, second_results)
                
                return merged_results
                
            # API 속도 제한 오류 처리
            elif any(error_text in error_message for error_text in 
                    ["rateLimitExceeded", "429", "The model is overloaded", "503", "500", "Internal"]):
                if retry_count >= max_retries:
                    self.log(f"최대 재시도 횟수({max_retries})에 도달했습니다. 고유명사 추출을 건너뜁니다.")
                    return {}
                
                # 지수 백오프 계산
                wait_time = min(10 * (2 ** retry_count) + random.uniform(0, 1), 300)
                self.log(f"API 사용량 제한에 도달했습니다. {wait_time:.1f}초 대기 후 재시도합니다. (시도 {retry_count+1}/{max_retries})")
                time.sleep(wait_time)
                
                # 재귀적 재시도
                return self.extract_pronouns(chunk, retry_count + 1, max_retries)
            else:
                self.log(f"고유명사 추출 중 오류 발생: {error_message}")
                if retry_count < max_retries:
                    # 일반 오류에 대한 간단한 재시도 로직
                    wait_time = 2 * (retry_count + 1)
                    self.log(f"{wait_time}초 후 재시도합니다. (시도 {retry_count+1}/{max_retries})")
                    time.sleep(wait_time)
                    return self.extract_pronouns(chunk, retry_count + 1, max_retries)
                return {}

    
    def merge_pronouns(self, new_pronouns):
        """새로운 고유명사를 기존 딕셔너리와 병합"""
        for foreign, data in new_pronouns.items():
            if foreign in self.pronouns_dict:
                # 외국어 고유명사가 이미 있으면 등장횟수 합산
                current_count = self.pronouns_dict[foreign]["등장횟수"]
                self.pronouns_dict[foreign]["등장횟수"] = current_count + data["등장횟수"]
                # 한국어 번역은 기존 것 유지 (먼저 등록된 것 우선)
            else:
                # 새로운 고유명사 추가
                self.pronouns_dict[foreign] = data

    def merge_pronoun_results(self, target, source):
        """두 고유명사 결과 딕셔너리를 병합"""
        for foreign, data in source.items():
            if foreign in target:
                # 외국어 고유명사가 이미 있으면 등장횟수 합산
                target[foreign]["등장횟수"] += data["등장횟수"]
            else:
                # 새로운 고유명사 추가
                target[foreign] = data

    
    def update_csv_files(self, base_path):
        """seed.csv와 fallen.csv 파일 업데이트"""
        # 등장횟수 기준 내림차순 정렬
        sorted_pronouns = sorted(
            self.pronouns_dict.items(), 
            key=lambda x: x[1]["등장횟수"], 
            reverse=True
        )
        
        # 파일 경로 설정
        seed_path = f"{base_path}_seed.csv"
        fallen_path = f"{base_path}_fallen.csv"
        
        # seed.csv에 저장할 상위 항목
        top_items = sorted_pronouns[:self.max_entries]
        
        # seed.csv 저장
        with open(seed_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["외국어", "한국어", "등장횟수"])
            for foreign, data in top_items:
                writer.writerow([foreign, data["번역"], data["등장횟수"]])
        
        # fallen.csv에 저장할 나머지 항목
        if len(sorted_pronouns) > self.max_entries:
            remaining_items = sorted_pronouns[self.max_entries:]
            with open(fallen_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["외국어", "한국어", "등장횟수"])
                for foreign, data in remaining_items:
                    writer.writerow([foreign, data["번역"], data["등장횟수"]])
        
        return seed_path, fallen_path
    
    def dynamic_priority_check(self, base_path):
        """seed.csv와 fallen.csv 간의 우선순위 재검토"""
        seed_path = f"{base_path}_seed.csv"
        fallen_path = f"{base_path}_fallen.csv"
        
        # CSV 파일이 모두 존재하는지 확인
        if not os.path.exists(seed_path) or not os.path.exists(fallen_path):
            return
        
        # seed.csv와 fallen.csv 읽기
        seed_items = []
        with open(seed_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # 헤더 건너뛰기
            for row in reader:
                if len(row) >= 3:
                    seed_items.append((row[0], row[1], int(row[2])))
        
        fallen_items = []
        with open(fallen_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # 헤더 건너뛰기
            for row in reader:
                if len(row) >= 3:
                    fallen_items.append((row[0], row[1], int(row[2])))
        
        # 모든 항목 합치고 등장횟수 기준으로 재정렬
        all_items = seed_items + fallen_items
        all_items.sort(key=lambda x: x[2], reverse=True)
        
        # seed.csv와 fallen.csv 업데이트
        with open(seed_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["외국어", "한국어", "등장횟수"])
            for item in all_items[:self.max_entries]:
                writer.writerow(item)
        
        if len(all_items) > self.max_entries:
            with open(fallen_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["외국어", "한국어", "등장횟수"])
                for item in all_items[self.max_entries:]:
                    writer.writerow(item)
    
    def process_sample_chunks(self, chunks, output_base_path):
        """표본 청크에서 고유명사 추출 및 CSV 생성"""
        # 표본 청크 선택
        sample_chunks = self.select_sample_chunks(chunks)
        self.log(f"전체 {len(chunks)}개 청크 중 {len(sample_chunks)}개 표본 청크 선택됨")
        
        # 처리 성공/실패 카운터
        successful_chunks = 0
        failed_chunks = 0
        
        # 각 표본 청크에서 고유명사 추출
        with tqdm(total=len(sample_chunks), desc="고유명사 추출 중", file=self.tqdm_out) as pbar:
            for i, chunk in enumerate(sample_chunks):
                try:
                    self.log(f"청크 {i+1}/{len(sample_chunks)} 처리 중...")
                    
                    # 고유명사 추출
                    new_pronouns = self.extract_pronouns(chunk)
                    
                    if new_pronouns:
                        # 고유명사 병합
                        self.merge_pronouns(new_pronouns)
                        successful_chunks += 1
                    else:
                        self.log(f"청크 {i+1} 처리 결과가 비어 있습니다.")
                        failed_chunks += 1
                    
                    # CSV 파일 주기적 업데이트 (매 청크마다)
                    seed_path, fallen_path = self.update_csv_files(output_base_path)
                    
                    # 우선순위 체크 및 동적 관리
                    self.dynamic_priority_check(output_base_path)
                    
                    # API 요청 간 지연 시간 추가 (rate limit 방지)
                    if i < len(sample_chunks) - 1:
                        time.sleep(2)
                        
                except Exception as e:
                    self.log(f"청크 {i+1} 처리 중 예외 발생: {str(e)}")
                    failed_chunks += 1
                
                finally:
                    # 진행 표시 업데이트
                    pbar.update(1)
        
        # 처리 결과 요약
        self.log(f"\n고유명사 추출 결과 요약:")
        self.log(f"- 총 처리 청크: {len(sample_chunks)}")
        self.log(f"- 성공한 청크: {successful_chunks}")
        self.log(f"- 실패한 청크: {failed_chunks}")
        
        # 모든 처리 완료 후 fallen.csv 삭제
        fallen_path = f"{output_base_path}_fallen.csv"
        if os.path.exists(fallen_path):
            os.remove(fallen_path)
            self.log(f"임시 파일 {fallen_path} 삭제됨")
        
        self.log(f"고유명사 추출 완료. 결과가 {output_base_path}_seed.csv에 저장되었습니다.")
        return f"{output_base_path}_seed.csv"

def extract_pronouns_from_file(input_file, config, tqdm_out=None):
    """파일에서 고유명사 추출 실행"""
    # 청크 분할
    from batch_translator import create_chunks
    chunks = create_chunks(input_file, config.get("chunk_size", 6000))
    
    # 출력 파일 경로 생성
    input_path = Path(input_file)
    output_base_path = input_path.with_name(f"{input_path.stem}")
    
    # 고유명사 추출 및 CSV 파일 생성
    extractor = PronounExtractor(config, tqdm_out)
    return extractor.process_sample_chunks(chunks, output_base_path)

def load_pronouns_for_translation(csv_path):
    """번역을 위한 고유명사 사전 로드"""
    pronouns = {}
    if os.path.exists(csv_path):
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # 헤더 건너뛰기
            for row in reader:
                if len(row) >= 2:
                    foreign, korean = row[0], row[1]
                    pronouns[foreign] = korean
    return pronouns

def format_pronouns_for_prompt(pronouns):
    """프롬프트에 포함할 형식으로 고유명사 포맷팅"""
    if not pronouns:
        return ""
    
    prompt_part = "\n\n# 고유명사 번역 가이드\n\n다음 고유명사는 일관성 있게 번역해주세요:\n\n"
    for foreign, korean in pronouns.items():
        prompt_part += f"- {foreign} → {korean}\n"
    return prompt_part

def filter_relevant_pronouns(text, pronouns_dict):
    """텍스트에 실제로 등장하는 고유명사만 필터링합니다."""
    relevant_pronouns = {}
    
    for foreign, korean in pronouns_dict.items():
        if foreign in text:
            relevant_pronouns[foreign] = korean
    
    return relevant_pronouns
