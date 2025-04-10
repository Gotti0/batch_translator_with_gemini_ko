import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import json
import threading
import os
import time
import random
import re
import csv
from pathlib import Path
from tqdm import tqdm  # tqdm 라이브러리 추가
from concurrent.futures import ThreadPoolExecutor
from batch_translator import (
    translate_with_gemini, create_chunks, save_chunk_with_index, get_metadata_path, 
    hash_config, create_translation_metadata, load_translation_metadata, update_translation_metadata, 
    save_merged_chunks, merge_chunk_results,load_chunks_with_index
)
from listed_models import fetch_models_async, fetch_recommended_models

class TqdmToLogText:
    """tqdm 출력을 GUI 로그로 리디렉션하는 클래스"""
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, s):
        if s.strip():  # 빈 문자열 무시
            self.text_widget.configure(state="normal")
            self.text_widget.insert(tk.END, s)
            self.text_widget.see(tk.END)
            self.text_widget.configure(state="disabled")
            
    def flush(self):
        pass


class BatchTranslatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Batch Translator")
        self.root.geometry("800x600")
        self.root.resizable(True, True)
        
        # 설정 변수 초기화
        self.api_key = tk.StringVar()
        self.model_name = tk.StringVar()
        self.temperature = tk.DoubleVar(value=1.8)
        self.top_p = tk.DoubleVar(value=0.9)
        self.input_file = tk.StringVar()
        self.chunk_size = tk.IntVar(value=6000)
        self.max_workers = tk.IntVar(value=8)
        self.stop_flag = False
        self.config_file = "config.json"
        
        # 탭 컨트롤러 생성
        tab_control = ttk.Notebook(root)
        
        # 설정 탭 구성
        settings_tab = ttk.Frame(tab_control)
        self.setup_settings_tab(settings_tab)
        tab_control.add(settings_tab, text="설정")
        
        # 로그 탭 구성
        log_tab = ttk.Frame(tab_control)
        self.setup_log_tab(log_tab)
        tab_control.add(log_tab, text="로그")
        tab_control.pack(expand=1, fill="both")
        

        # 제어 버튼 프레임
        control_frame = ttk.Frame(root)
        control_frame.pack(fill="x", padx=10, pady=10)
        
        self.start_button = ttk.Button(control_frame, text="번역 시작", command=self.start_translation)
        self.start_button.pack(side="left", padx=5)
        
        self.stop_button = ttk.Button(control_frame, text="중지", command=self.stop_translation, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        
        ttk.Button(control_frame, text="설정 저장", command=self.save_config).pack(side="right", padx=5)
        ttk.Button(control_frame, text="설정 불러오기", command=self.load_config).pack(side="right", padx=5)

        # 고유명사 변수 초기화
        self.pronouns_csv_path = None  # 고유명사 CSV 파일 경로 저장
        self.max_pronoun_entries = tk.IntVar(value=20)  # 최대 고유명사 항목 수 (기본값 20)
        self.pronoun_sample_ratio = tk.DoubleVar(value=0.5)
        
        # 탭 컨트롤러에 고유명사 관리 탭 추가
        pronouns_tab = ttk.Frame(tab_control)
        self.setup_pronouns_tab(pronouns_tab)
        tab_control.add(pronouns_tab, text="고유명사 관리")

        # 설정 파일 로드 UI 전부 로드 한 다음 초기화 해야 함!
        self.load_config()

    def setup_settings_tab(self, parent):
        # API 설정 프레임
        api_frame = ttk.LabelFrame(parent, text="API 설정")
        api_frame.pack(fill="x", padx=10, pady=5)
        
        # API 키
        ttk.Label(api_frame, text="API 키:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(api_frame, textvariable=self.api_key, width=50).grid(row=0, column=1, padx=5, pady=5)
        
        # 모델 선택 드롭다운 (기존 Entry 대신)
        ttk.Label(api_frame, text="모델 이름:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.model_dropdown = ttk.Combobox(api_frame, textvariable=self.model_name, width=40, state="readonly")
        self.model_dropdown.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
    
        # 모델 불러오기 버튼
        ttk.Button(api_frame, text="모델 목록 가져오기", command=self.load_recommended_models).grid(row=1, column=2, padx=5, pady=5)
        
        # 온도(Temperature)
        ttk.Label(api_frame, text="Temperature:").grid(row=2, column=0, sticky="w", padx=5, pady=5)

        # Temperature 슬라이더와 값 표시
        temperature_scale = ttk.Scale(api_frame, from_=0.0, to=2.0, variable=self.temperature, orient="horizontal", command=lambda v: temperature_value_label.config(text=f"{float(v):.2f}"))
        temperature_scale.grid(row=2, column=1, sticky="ew", padx=5, pady=5)

        # Temperature 값 라벨
        temperature_value_label = ttk.Label(api_frame, text=f"{self.temperature.get():.2f}")
        temperature_value_label.grid(row=2, column=2, sticky="w", padx=5)

        # Top P
        ttk.Label(api_frame, text="Top P:").grid(row=3, column=0, sticky="w", padx=5, pady=5)

        # Top P 슬라이더와 값 표시
        top_p_scale = ttk.Scale(api_frame, from_=0.0, to=1.0, variable=self.top_p, orient="horizontal", command=lambda v: top_p_value_label.config(text=f"{float(v):.2f}"))
        top_p_scale.grid(row=3, column=1, sticky="ew", padx=5, pady=5)

        # Top P 값 라벨
        top_p_value_label = ttk.Label(api_frame, text=f"{self.top_p.get():.2f}")
        top_p_value_label.grid(row=3, column=2, sticky="w", padx=5)

        # 파일 설정 프레임
        file_frame = ttk.LabelFrame(parent, text="파일 설정")
        file_frame.pack(fill="x", padx=10, pady=5)
        
        # 입력 파일
        ttk.Label(file_frame, text="입력 파일:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(file_frame, textvariable=self.input_file, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="찾아보기...", command=self.browse_file).grid(row=0, column=2, padx=5, pady=5)
        
        # 청크 크기
        ttk.Label(file_frame, text="청크 크기:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(file_frame, textvariable=self.chunk_size, width=10).grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        # 최대 작업자 수 (max_workers) 추가
        ttk.Label(file_frame, text="동시 번역 개수:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        
        # 슬라이더와 입력 필드를 조합한 UI
        max_workers_frame = ttk.Frame(file_frame)
        max_workers_frame.grid(row=2, column=1, sticky="w", padx=5, pady=5)
        
        max_workers_scale = ttk.Scale(max_workers_frame, from_=1, to=16, variable=self.max_workers, 
                                orient="horizontal", length=150, 
                                command=lambda v: max_workers_label.config(text=f"{int(float(v))}"))
        max_workers_scale.pack(side="left")
        
        max_workers_label = ttk.Label(max_workers_frame, text=str(self.max_workers.get()))
        max_workers_label.pack(side="left", padx=5)
        
        # 도움말 텍스트
        ttk.Label(file_frame, text="(병렬 번역 스레드 수, 낮을수록 API 제한에 안전)").grid(
            row=2, column=2, sticky="w", padx=5, pady=5)


        # 프롬프트 프레임
        prompt_frame = ttk.LabelFrame(parent, text="번역 프롬프트")
        prompt_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, wrap=tk.WORD, height=10)
        self.prompt_text.pack(fill="both", expand=True, padx=5, pady=5)

    def setup_log_tab(self, parent):
        # 로그 텍스트 영역
        log_frame = ttk.Frame(parent)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state="disabled")
        self.log_text.pack(fill="both", expand=True)
        
         # tqdm의 출력을 로그 텍스트로 리디렉션
        self.tqdm_out = TqdmToLogText(self.log_text)

        # 프로그레스바
        progress_frame = ttk.Frame(parent)
        progress_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(progress_frame, text="진행 상황:").pack(side="left", padx=5)
        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=5)
        
        self.progress_label = ttk.Label(progress_frame, text="0%")
        self.progress_label.pack(side="left", padx=5)

    def setup_pronouns_tab(self, parent):
        """고유명사 관리 탭 구성"""
        # 컨트롤 프레임: 고유명사 추출/수정 버튼
        control_frame = ttk.Frame(parent)
        control_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Button(control_frame, text="고유명사 추출", command=self.extract_pronouns).pack(side="left", padx=5)
        ttk.Button(control_frame, text="고유명사 수정", command=self.edit_pronouns).pack(side="left", padx=5)

        # 새로운 파일 로드 섹션 추가
        file_frame = ttk.LabelFrame(parent, text="고유명사 사전 파일")
        file_frame.pack(fill="x", padx=10, pady=5)
        
        self.pronoun_file = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.pronoun_file, width=50).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="찾아보기...", command=self.browse_pronoun_file).grid(row=0, column=2, padx=5, pady=5)
        ttk.Button(file_frame, text="언로드", command=self.unload_pronoun_file).grid(row=0, column=4, padx=5, pady=5)
        
        # 슬라이더 프레임 수정: 두 개의 슬라이더를 포함하도록 변경
        slider_frame = ttk.LabelFrame(parent, text="고유명사 설정")
        slider_frame.pack(fill="x", padx=10, pady=5)

        # 슬라이더 프레임: 최대 항목 수 설정
        slider_frame = ttk.LabelFrame(parent, text="최대 고유명사 항목 수")
        slider_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(slider_frame, text="최대 항목 수:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        
        # 슬라이더 구성 (5부터 100까지)
        max_entries_scale = ttk.Scale(slider_frame, from_=5, to=100, variable=self.max_pronoun_entries, 
                                orient="horizontal", command=lambda v: max_entries_label.config(text=f"{int(float(v))}"))
        max_entries_scale.grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        # 표본 비율 슬라이더 (새로 추가)
        ttk.Label(slider_frame, text="표본 비율(%):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        sample_ratio_scale = ttk.Scale(slider_frame, from_=10, to=100, variable=self.pronoun_sample_ratio, 
                                orient="horizontal", command=lambda v: sample_ratio_label.config(text=f"{int(float(v))}%"))
        sample_ratio_scale.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        sample_ratio_label = ttk.Label(slider_frame, text=f"{int(self.pronoun_sample_ratio.get()*100)}%")
        sample_ratio_label.grid(row=1, column=2, sticky="w", padx=5)

        # 표본 비율에 대한 설명 추가
        ttk.Label(slider_frame, text="(분석할 청크의 비율, 높을수록 정확하지만 시간이 오래 걸림)").grid(
            row=1, column=3, sticky="w", padx=5, pady=5)
        
        # 현재 값 표시 라벨
        max_entries_label = ttk.Label(slider_frame, text=f"{self.max_pronoun_entries.get()}")
        max_entries_label.grid(row=0, column=2, sticky="w", padx=5)
        
        # 고유명사 표시 프레임
        pronouns_frame = ttk.LabelFrame(parent, text="추출된 고유명사")
        pronouns_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        # 트리뷰 구성 (고유명사 목록 표시)
        columns = ("foreign", "korean", "count")
        self.pronouns_tree = ttk.Treeview(pronouns_frame, columns=columns, show="headings")
        
        # 열 제목 설정
        self.pronouns_tree.heading("foreign", text="외국어")
        self.pronouns_tree.heading("korean", text="한국어")
        self.pronouns_tree.heading("count", text="등장횟수")
        
        # 열 너비 설정
        self.pronouns_tree.column("foreign", width=150)
        self.pronouns_tree.column("korean", width=150)
        self.pronouns_tree.column("count", width=80)
        
        # 스크롤바 추가
        scrollbar = ttk.Scrollbar(pronouns_frame, orient="vertical", command=self.pronouns_tree.yview)
        self.pronouns_tree.configure(yscrollcommand=scrollbar.set)
        
        # 배치
        self.pronouns_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

         # 트리뷰에 더블클릭 이벤트 추가
        def on_double_click(event):
            selected_item = self.pronouns_tree.selection()
            if selected_item:
                self.edit_pronouns()

        self.pronouns_tree.bind("<Double-1>", on_double_click)

    def browse_file(self):
        file_path = filedialog.askopenfilename(
            title="파일 선택",
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")]
        )
        if file_path:
            self.input_file.set(file_path)
            self.log(f"선택된 파일: {file_path}")

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                    self.api_key.set(config.get("api_key", ""))
                    self.model_name.set(config.get("model_name", ""))
                    self.temperature.set(config.get("temperature", 1.8))
                    self.top_p.set(config.get("top_p", 0.9))
                    self.max_pronoun_entries.set(config.get("max_pronoun_entries", 20))  
                    self.pronoun_sample_ratio.set(config.get("pronoun_sample_ratio", 0.5))
                    self.max_workers.set(config.get("max_workers", 3))
                    self.prompt_text.delete("1.0", tk.END)
                    self.prompt_text.insert("1.0", config.get("prompts", ""))
                    
                    self.log("설정 파일을 성공적으로 불러왔습니다.")
        except Exception as e:
            self.log(f"설정 불러오기 오류: {str(e)}")

    def save_config(self):
        try:
            config = {
                "api_key": self.api_key.get(),
                "model_name": self.model_name.get(),
                "temperature": self.temperature.get(),
                "top_p": self.top_p.get(),
                "prompts": self.prompt_text.get("1.0", tk.END),
                "max_pronoun_entries": self.max_pronoun_entries.get(),
                "pronoun_sample_ratio": self.pronoun_sample_ratio.get(),
                "max_workers": self.max_workers.get()
            }

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            
            self.log("설정이 성공적으로 저장되었습니다.")
        except Exception as e:
            self.log(f"설정 저장 오류: {str(e)}")

    def start_translation(self):
        if not self.input_file.get():
            self.log("오류: 입력 파일이 필요합니다.")
            return
        
        if not self.api_key.get():
            self.log("오류: API 키가 필요합니다.")
            return
        
        self.save_config()
        self.stop_flag = False
        self.progress_bar["value"] = 0
        self.progress_label.config(text="0%")
        
        self.translation_thread = threading.Thread(target=self.run_translation)
        self.translation_thread.daemon = True
        self.translation_thread.start()
        
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.log("번역이 시작되었습니다...")

    def stop_translation(self):
        self.stop_flag = True
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.log("번역 중지 요청이 발생했습니다. 현재 청크 완료 후 중단됩니다.")

    def run_translation(self):
        try:
            # 번역 시작 시간 기록
            total_start_time = time.time()
            
            config = {
                "api_key": self.api_key.get(),
                "model_name": self.model_name.get(),
                "temperature": self.temperature.get(),
                "top_p": self.top_p.get(),
                "prompts": self.prompt_text.get("1.0", tk.END),
                "max_workers": self.max_workers.get()
            }
            
             # 입력 및 출력 파일 경로
            input_file_path = Path(self.input_file.get())
            output_path = input_file_path.with_name(f"{input_file_path.stem}_result{input_file_path.suffix}")
            metadata_path = get_metadata_path(input_file_path)

            # 고유명사 사전 설정
            input_file_path = Path(self.input_file.get())
            if hasattr(self, 'pronouns_csv_path') and self.pronouns_csv_path and os.path.exists(self.pronouns_csv_path):
                self.log(f"사용자 지정 고유명사 사전 사용: {self.pronouns_csv_path}")
                config["pronouns_csv"] = self.pronouns_csv_path
            elif os.path.exists(input_file_path.with_stem(f"{input_file_path.stem}_seed").with_suffix('.csv')):
                pronouns_csv_path = input_file_path.with_stem(f"{input_file_path.stem}_seed").with_suffix('.csv')
                self.log(f"입력 파일 기반 고유명사 사전 사용: {pronouns_csv_path}")
                config["pronouns_csv"] = str(pronouns_csv_path)
            else:
                self.log("고유명사 사전 파일을 찾을 수 없습니다. 일반 번역을 진행합니다.")
                
            # 청크 생성 
            self.log("청크 생성 중...")
            chunks = create_chunks(self.input_file.get(), self.chunk_size.get())
            total_chunks = len(chunks)
            self.log(f"총 {total_chunks}개의 청크가 생성되었습니다.")
            
            # 진행 상황 확인을 위한 메타데이터 확인
            resume_translation = False
            already_translated = {}
            
            if metadata_path.exists() and output_path.exists():
                metadata = load_translation_metadata(input_file_path)
                
                # 기존 진행 상황이 있는지 확인
                if (metadata and metadata["input_file"] == str(input_file_path) and 
                    metadata["config_hash"] == hash_config(config) and 
                    len(metadata["translated_chunks"]) > 0):
                    
                    # 사용자에게 기존 번역 이어서 할지 확인
                    if messagebox.askyesno("이어서 번역", 
                        f"이전 번역 진행 상황이 있습니다 ({len(metadata['translated_chunks'])}/{metadata['total_chunks']} 청크 완료). "
                        f"이어서 번역하시겠습니까?"):
                        resume_translation = True
                        already_translated = metadata["translated_chunks"]
                        self.log(f"이전 번역에서 {len(already_translated)}개 청크를 완료했습니다. 이어서 번역합니다.")
                    else:
                        # 사용자가 처음부터 시작하기를 원함
                        os.remove(metadata_path)
                        os.remove(output_path)
                        self.log("처음부터 번역을 시작합니다.")
            
            # 새 메타데이터 생성 (이어서 번역하지 않는 경우)
            if not resume_translation:
                create_translation_metadata(input_file_path, chunks, config)
                # 출력 파일도 초기화
                if output_path.exists():
                    os.remove(output_path)
                    self.log("이전 번역 파일을 초기화했습니다.")
            elif resume_translation and os.path.exists(output_path):
                # 이어서 번역할 경우의 처리
                self.log("이전 번역 파일을 유지합니다.")
            
            # 번역할 청크만 필터링 (이미 번역된 청크 제외)
            chunks_to_translate = []
            for i, chunk in enumerate(chunks):
                if str(i) not in already_translated:
                    chunks_to_translate.append((i, chunk))
            
            # 진행 상황 관련 변수
            self.processed_chunks = len(already_translated)  # 이미 처리된 청크 수
            total_chunks = len(chunks)  # 전체 청크 수
            remaining_chunks = len(chunks_to_translate)  # 남은 청크 수
            self.lock = threading.Lock()

            output_path = input_file_path.with_name(f"{input_file_path.stem}_result{input_file_path.suffix}")
            
            
            # 결과를 인덱스와 함께 저장할 리스트 (순서 유지)
            results = [None] * total_chunks
            
            # API 제한을 고려한 최대 작업자 수 설정 (사용자 설정 사용)
            user_max_workers = self.max_workers.get()
            max_workers = max(1, min(user_max_workers, total_chunks))  # 최소 1, 최대는 청크 수
            
            self.log(f"병렬 번역 시작 (최대 {max_workers}개 스레드 사용)")
            
            def translate_chunk_with_index(index, chunk):
                """청크 번역 함수 (인덱스 유지)"""
                if self.stop_flag:
                    return index, None

                # 이미 번역된 청크인지 확인 (안전장치)
                if str(index) in already_translated:
                    self.log(f"청크 {index+1}/{total_chunks}는 이미 번역되어 있습니다. 건너뜁니다.")
                    return index, None

                try:
                    # API 호출 간격을 위한 지연
                    if index > 0:
                        time.sleep(random.uniform(0.5, 1.0))
                    
                    chunk_start_time = time.time()
                    self.log(f"청크 {index+1}/{total_chunks} 번역 중...")
                    
                    # 청크 번역
                    translated = translate_with_gemini(chunk, config)

                    # 경과 시간 계산
                    chunk_elapsed_time = time.time() - chunk_start_time
                    minutes, seconds = divmod(chunk_elapsed_time, 60)
                    formatted_time = f"{int(minutes)}:{int(seconds):02d}"

                    # 성공한 경우에만 결과 저장
                    if translated:
                        # 진행 상황 업데이트
                        with self.lock:
                            self.processed_chunks += 1
                            self.update_progress(self.processed_chunks, total_chunks)

                        # 변경된 부분: 파일에 즉시 저장하는 대신 배열에 저장
                        results[index] = translated
                            
                        # 메타데이터는 여전히 업데이트
                        update_translation_metadata(metadata_path, index)
                        
                        self.log(f"청크 {index+1}/{total_chunks} 번역 완료 (소요시간: {formatted_time})")
                        return index, True
                    
                    else:
                        with self.lock:
                            self.processed_chunks += 1
                            self.update_progress(self.processed_chunks, total_chunks)
                        self.log(f"청크 {index+1}/{total_chunks} 번역 실패")
                        return index, None
                
                
                except Exception as e:
                    with self.lock:
                        self.processed_chunks += 1
                        self.update_progress(self.processed_chunks, total_chunks)
                        self.log(f"청크 {index+1} 번역 중 오류 발생: {str(e)}")
                    return index, None
            
            # ThreadPoolExecutor를 사용한 병렬 처리
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 각 청크에 대한 번역 작업 제출
                futures = [executor.submit(translate_chunk_with_index, i, chunk) 
                        for i, chunk in chunks_to_translate]
                
                # tqdm 진행 표시줄
                with tqdm(total=total_chunks, desc="번역 진행 중", file=self.tqdm_out,
                        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
                    
                    # 작업 완료 순서대로 결과 처리
                    for future in futures:
                        try:
                            future.result()
                            pbar.update(1)  # 명시적 업데이트 추가
                        except Exception as e:
                            self.log(f"오류 발생: {str(e)}")
                            pbar.update(1)  # 예외 발생시에도 업데이트
  
            

            # 번역된 결과를 순서대로 파일에 저장합니다...
            self.log("번역된 결과를 순서대로 파일에 저장합니다...")
            # 백업 파일 생성
            if output_path.exists():
                import shutil
                backup_path = output_path.with_name(f"{output_path.stem}_backup{output_path.suffix}")
                shutil.copy2(output_path, backup_path)
                self.log(f"기존 결과 파일을 {backup_path}로 백업했습니다.")

            # 결과 병합 및 저장 (새로운 코드)
            if resume_translation and os.path.exists(output_path):
                # 인덱스 정보가 포함된 청크 로드
                existing_chunks = load_chunks_with_index(output_path)
                
                # 새로 번역한 결과 병합
                merged_chunks = merge_chunk_results(existing_chunks, results, total_chunks)
                
                # 병합된 결과 저장
                save_merged_chunks(merged_chunks, total_chunks, output_path)
                # 추가: 메타데이터 다시 로드하고 병합된 모든 청크에 대해 메타데이터 업데이트
                for chunk_idx in merged_chunks.keys():
                    update_translation_metadata(metadata_path, chunk_idx)
                
                self.log(f"기존 결과({len(already_translated)}개 청크)와 새 번역 결과를 병합했습니다.")
            else:
                # 새 파일 생성 및 결과 저장
                with open(output_path, 'w', encoding='utf-8') as f:
                    pass  # 파일 초기화
                
                # 각 결과를 인덱스와 함께 저장
                for idx, result in enumerate(results):
                    if result:
                        save_chunk_with_index(idx, result, output_path)
                
            self.log("모든 결과가 순서대로 저장되었습니다.")
                
            # 총 소요 시간 계산 및 출력
            total_elapsed_time = time.time() - total_start_time
            total_minutes, total_seconds = divmod(total_elapsed_time, 60)
            total_hours, total_minutes = divmod(total_minutes, 60)
            
            if total_hours > 0:
                total_formatted_time = f"{int(total_hours)}:{int(total_minutes):02d}:{int(total_seconds):02d}"
            else:
                total_formatted_time = f"{int(total_minutes)}:{int(total_seconds):02d}"
            
            self.log(f"번역 프로세스가 완료되었습니다. 총 작업 시간: {total_formatted_time}")
            self.log(f"{output_path}에 결과가 저장되었습니다.")
            # 모든 청크가 번역되었는지 확인하여 후처리 수행
            metadata = load_translation_metadata(input_file_path)
            if metadata and len(metadata["translated_chunks"]) == total_chunks:
                self.post_process_result(output_path)
            
        except Exception as e:
            self.log(f"치명적 오류 발생: {str(e)}")
            # 백업 파일 확인 및 복구
            backup_path = output_path.with_name(f"{output_path.stem}_backup{output_path.suffix}")
            if os.path.exists(backup_path):
                import shutil
                shutil.copy2(backup_path, output_path)
                self.log(f"오류 발생으로 인해 백업 파일에서 복구했습니다.")
            
        finally:
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")


    def finalize_translation_result(output_path):
        """청크 경계 표시를 제거하고 최종 결과 파일 정리"""
        try:
            if not os.path.exists(output_path):
                return False
                
            # 파일 내용 읽기
            with open(output_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 청크 경계 표시 제거
            cleaned_content = re.sub(r'<!-- CHUNK_START:\d+ -->\n', '', content)
            cleaned_content = re.sub(r'\n<!-- CHUNK_END:\d+ -->\n', '\n', cleaned_content)
            
            # 정리된 내용 저장
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(cleaned_content)
                
            return True
        except Exception as e:
            print(f"최종 결과 파일 정리 중 오류 발생: {str(e)}")
            return False


                    
    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    # 진행률 업데이트 함수 수정
    def update_progress(self, current, total, already_done=0):
        progress = int(((current + already_done) / total) * 100)
        self.progress_bar["value"] = progress
        self.progress_label.config(text=f"{progress}%")
        

    def remove_html_tags(self, text):
            """HTML 태그를 정규 표현식을 사용하여 제거합니다."""
            try:
                # 모든 HTML 태그 제거
                cleaned_text = re.sub(r'<[\s\S]*?>', '', text)
                return cleaned_text
            except Exception as e:
                self.log(f"HTML 태그 제거 중 오류 발생: {str(e)}")
                return text  # 오류 발생 시 원본 텍스트 반환

    def remove_translation_header(self, text):
        """번역 결과 헤더를 정규표현식으로 제거합니다."""
        try:
            # "# 번역 결과 (한국어):" 문구와 그 뒤의 빈 줄 제거
            cleaned_text = re.sub(r'#.*\n', '', text)
            return cleaned_text
        except Exception as e:
            self.log(f"번역 헤더 제거 중 오류 발생: {str(e)}")
            return text  # 오류 발생 시 원본 텍스트 반환

    def remove_markdown_code_block_markers(self, text):
        """마크다운 코드 블록 마커(```korean 등)를 정규표현식으로 제거합니다."""
        try:
            # "```
            cleaned_text = re.sub(r'```.*\n', '', text)
            # 코드 블록 닫는 부분(```
            cleaned_text = re.sub(r'\n\s*```', '', cleaned_text)
            return cleaned_text
        except Exception as e:
            self.log(f"마크다운 코드 블록 마커 제거 중 오류 발생: {str(e)}")
            return text  # 오류 발생 시 원본 텍스트 반환

    def post_process_result(self, output_path):
        """번역 결과 파일에서 불필요한 요소를 제거합니다."""
        try:
            # 출력 파일이 존재하는지 확인
            if not os.path.exists(output_path):
                self.log("결과 파일이 존재하지 않습니다.")
                return
                
            # 입력 파일 경로에서 메타데이터 파일 경로 얻기
            input_path = Path(self.input_file.get())
            metadata_path = get_metadata_path(input_path)
            
            # 메타데이터 파일이 존재하는지 확인
            if not os.path.exists(metadata_path):
                self.log("메타데이터 파일이 존재하지 않습니다.")
                return
                
            # 메타데이터 로드
            metadata = load_translation_metadata(input_path)
            
            # 메타데이터가 올바른지, 모든 청크가 번역되었는지 확인
            if metadata and len(metadata["translated_chunks"]) == metadata["total_chunks"]:
                self.log("결과 파일에서 불필요한 요소 제거 중...")
                # 파일 내용 읽기
                with open(output_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # HTML 태그 제거
                cleaned_content = self.remove_html_tags(content)
                
                # 번역 결과 헤더 제거
                cleaned_content = self.remove_translation_header(cleaned_content)
                
                # 마크다운 코드 블록 마커 제거
                cleaned_content = self.remove_markdown_code_block_markers(cleaned_content)
                
                # 정리된 내용 저장
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(cleaned_content)
                    
                self.log("불필요한 요소 제거 완료!")
                
                # 추가된 부분: 메타데이터 파일 삭제
                try:
                    if os.path.exists(metadata_path):
                        os.remove(metadata_path)
                        self.log(f"메타데이터 파일 삭제 완료: {metadata_path}")
                except Exception as del_error:
                    self.log(f"메타데이터 파일 삭제 중 오류 발생: {str(del_error)}")
            else:
                self.log("모든 청크가 번역되지 않았습니다. 후처리를 건너뜁니다.")
        except Exception as e:
            self.log(f"후처리 중 오류 발생: {str(e)}")

    def extract_pronouns(self):
        """고유명사 추출 작업 시작"""
        if not self.input_file.get():
            self.log("오류: 입력 파일이 필요합니다.")
            return
        
        # 현재 구성 저장
        self.save_config()
        
        # 구성에 최대 고유명사 항목 수 추가
        config = {
            "api_key": self.api_key.get(),
            "model_name": self.model_name.get(),
            "temperature": self.temperature.get(),
            "top_p": self.top_p.get(),
            "prompts": self.prompt_text.get("1.0", tk.END),
            "chunk_size": self.chunk_size.get(),
            "max_pronoun_entries": self.max_pronoun_entries.get(),
            "pronoun_sample_ratio": self.pronoun_sample_ratio.get() / 100.0  # 퍼센트를 소수로 변환
        }
        
        # 고유명사 추출 스레드 시작
        self.log("고유명사 추출 작업을 시작합니다...")
        self.log(f"고유명사 추출 작업을 시작합니다 (표본 비율: {self.pronoun_sample_ratio.get():.1f}%)...")
        pronouns_thread = threading.Thread(target=self.run_pronouns_extraction, args=(config,))
        pronouns_thread.daemon = True
        pronouns_thread.start()

    def run_pronouns_extraction(self, config):
        """고유명사 추출 처리 실행"""
        try:
            from batch_translator_pronouns import extract_pronouns_from_file
            
            # 동시 번역 개수 설정이 config에 있는지 확인
            if "max_workers" not in config:
                config["max_workers"] = self.max_workers.get()

            self.log("고유명사 추출 시작...")
            self.pronouns_csv_path = extract_pronouns_from_file(
                self.input_file.get(), 
                config, 
                self.tqdm_out
            )
            self.log(f"고유명사 추출 완료: {self.pronouns_csv_path}")
            
            # 추출 완료 후 트리뷰 업데이트
            self.update_pronouns_treeview()
        except Exception as e:
            self.log(f"고유명사 추출 중 오류 발생: {str(e)}")

    def update_pronouns_treeview(self):
        """고유명사 트리뷰 업데이트"""
        # 기존 항목 삭제
        for item in self.pronouns_tree.get_children():
            self.pronouns_tree.delete(item)
        
        # CSV 파일 존재 확인
        if not self.pronouns_csv_path or not os.path.exists(self.pronouns_csv_path):
            return
        
        # CSV 파일에서 데이터 로드 및 트리뷰 업데이트
        try:
            with open(self.pronouns_csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader)  # 헤더 건너뛰기
                for row in reader:
                    if len(row) >= 3:
                        foreign, korean, count = row[0], row[1], row[2]
                        self.pronouns_tree.insert("", "end", values=(foreign, korean, count))
        except Exception as e:
            self.log(f"고유명사 목록 로드 중 오류: {str(e)}")

    def edit_pronouns(self):
        """고유명사 수정 대화상자 표시"""
        if not self.pronouns_csv_path or not os.path.exists(self.pronouns_csv_path):
            self.log("수정할 고유명사 파일이 없습니다. 먼저 고유명사를 추출하세요.")
            return
        
        # 선택된 항목 확인
        selected_item = self.pronouns_tree.selection()
        if not selected_item:
            self.log("수정할 항목을 선택하세요.")
            return
        
        # 선택된 항목 데이터 가져오기
        values = self.pronouns_tree.item(selected_item, "values")
        foreign, korean, count = values
        
        # 수정 대화상자 생성
        edit_window = tk.Toplevel(self.root)
        edit_window.title("고유명사 수정")
        edit_window.geometry("400x150")
        edit_window.resizable(False, False)
        
        # 입력 폼 구성
        ttk.Label(edit_window, text="외국어:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ttk.Label(edit_window, text="한국어:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        
        foreign_var = tk.StringVar(value=foreign)
        korean_var = tk.StringVar(value=korean)
        
        ttk.Entry(edit_window, textvariable=foreign_var, width=30).grid(row=0, column=1, padx=10, pady=5)
        ttk.Entry(edit_window, textvariable=korean_var, width=30).grid(row=1, column=1, padx=10, pady=5)
        
        # 버튼 프레임
        button_frame = ttk.Frame(edit_window)
        button_frame.grid(row=2, column=0, columnspan=2, pady=10)
        
        # 저장 및 취소 버튼
        ttk.Button(button_frame, text="저장", command=lambda: self.save_pronoun_edit(
            selected_item, foreign_var.get(), korean_var.get(), count, edit_window
        )).pack(side="left", padx=10)
        
        ttk.Button(button_frame, text="취소", command=edit_window.destroy).pack(side="left", padx=10)

    def save_pronoun_edit(self, item_id, new_foreign, new_korean, count, window):
        """고유명사 수정 내용 저장"""
        try:
            # CSV 파일에서 모든 데이터 로드
            rows = []
            with open(self.pronouns_csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                headers = next(reader)
                for row in reader:
                    rows.append(row)
            
            # 선택된 항목의 인덱스 찾기
            index = self.pronouns_tree.index(item_id)
            
            # 해당 항목 수정
            rows[index][0] = new_foreign
            rows[index][1] = new_korean
            
            # CSV 파일에 저장
            with open(self.pronouns_csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)
            
            # 트리뷰 항목 업데이트
            self.pronouns_tree.item(item_id, values=(new_foreign, new_korean, count))
            
            self.log(f"고유명사 '{new_foreign}'의 수정 내용이 저장되었습니다.")
            window.destroy()
        except Exception as e:
            self.log(f"고유명사 수정 저장 중 오류 발생: {str(e)}")

    def browse_pronoun_file(self):
        """고유명사 사전 파일 선택 대화상자"""
        file_path = filedialog.askopenfilename(
            title="고유명사 사전 파일 선택",
            filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")]
        )
        
        if file_path:
            self.pronoun_file.set(file_path)
            self.log(f"선택된 고유명사 사전 파일: {file_path}")
            self.load_pronoun_file()  # 자동 로드 실행

    def load_pronoun_file(self):
        """선택된 고유명사 사전 파일 로드"""
        file_path = self.pronoun_file.get()
        
        if not file_path or not os.path.exists(file_path):
            self.log("오류: 유효한 고유명사 사전 파일을 선택해주세요.")
            return
        
        try:
            # 기존 데이터 초기화
            for item in self.pronouns_tree.get_children():
                self.pronouns_tree.delete(item)
            
            # CSV 파일 로드
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader)  # 헤더 건너뛰기
                
                if len(header) < 3:
                    self.log("오류: 올바른 형식의 고유명사 사전 파일이 아닙니다.")
                    return
                
                # 트리뷰에 데이터 추가
                for row in reader:
                    if len(row) >= 3:
                        foreign, korean, count = row[0], row[1], row[2]
                        self.pronouns_tree.insert("", "end", values=(foreign, korean, count))
            
            # 파일 경로 저장
            self.pronouns_csv_path = file_path
            self.log(f"고유명사 사전이 성공적으로 로드되었습니다: {len(self.pronouns_tree.get_children())}개 항목")
            
        except Exception as e:
            self.log(f"고유명사 사전 로드 중 오류 발생: {str(e)}")

    
    def unload_pronoun_file(self):
        """로드된 고유명사 사전 파일을 언로드"""
        if not self.pronouns_csv_path:
            self.log("언로드할 고유명사 사전 파일이 없습니다.")
            return
            
        # 확인 대화상자 표시
        if not messagebox.askyesno("고유명사 사전 언로드", "현재 로드된 고유명사 사전을 언로드하시겠습니까?"):
            return
            
        # 트리뷰 초기화
        for item in self.pronouns_tree.get_children():
            self.pronouns_tree.delete(item)
            
        # 변수 초기화
        old_path = self.pronouns_csv_path
        self.pronouns_csv_path = None
        self.pronoun_file.set("")
        
        self.log(f"고유명사 사전이 언로드되었습니다: {old_path}")


    # 권장 모델만 로드하는 추가 메서드 
    def load_recommended_models(self):
        """번역에 권장되는 모델 목록만 가져오기"""
        api_key = self.api_key.get().strip()
        if not api_key:
            messagebox.showerror("오류", "API 키를 입력하세요.")
            return
        
        self.log("권장 모델 목록을 가져오는 중...")
        
        # 비동기로 권장 모델 목록 가져오기
        def on_recommended_models_loaded(models):
            if models:
                self.model_dropdown['values'] = models
                current_model = self.model_name.get()
                if not current_model or current_model not in models:
                    self.model_name.set(models[0])
                self.log(f"{len(models)}개의 권장 모델을 가져왔습니다.")
            else:
                messagebox.showerror("오류", "권장 모델 목록을 가져오지 못했습니다.")
        
        # 비동기 권장 모델 로드
        thread = threading.Thread(
            target=lambda: on_recommended_models_loaded(
                fetch_recommended_models(api_key, self.tqdm_out)
            )
        )
        thread.daemon = True
        thread.start()




if __name__ == "__main__":
    root = tk.Tk()
    app = BatchTranslatorGUI(root)
    root.mainloop()
