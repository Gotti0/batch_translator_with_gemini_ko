import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import json
import threading
import os
import time
from pathlib import Path
from tqdm import tqdm  # tqdm 라이브러리 추가
from batch_translator import translate_with_gemini, create_chunks, save_result
import re

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
        
        # 설정 파일 로드
        self.load_config()
        
        # 제어 버튼 프레임
        control_frame = ttk.Frame(root)
        control_frame.pack(fill="x", padx=10, pady=10)
        
        self.start_button = ttk.Button(control_frame, text="번역 시작", command=self.start_translation)
        self.start_button.pack(side="left", padx=5)
        
        self.stop_button = ttk.Button(control_frame, text="중지", command=self.stop_translation, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        
        ttk.Button(control_frame, text="설정 저장", command=self.save_config).pack(side="right", padx=5)
        ttk.Button(control_frame, text="설정 불러오기", command=self.load_config).pack(side="right", padx=5)

    def setup_settings_tab(self, parent):
        # API 설정 프레임
        api_frame = ttk.LabelFrame(parent, text="API 설정")
        api_frame.pack(fill="x", padx=10, pady=5)
        
        # API 키
        ttk.Label(api_frame, text="API 키:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(api_frame, textvariable=self.api_key, width=50).grid(row=0, column=1, padx=5, pady=5)
        
        # 모델 이름
        ttk.Label(api_frame, text="모델 이름:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(api_frame, textvariable=self.model_name, width=50).grid(row=1, column=1, padx=5, pady=5)
        
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
                "prompts": self.prompt_text.get("1.0", tk.END)
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
                "prompts": self.prompt_text.get("1.0", tk.END)
            }
            
            self.log("청크 생성 중...")
            chunks = create_chunks(self.input_file.get(), self.chunk_size.get())
            total_chunks = len(chunks)
            self.log(f"총 {total_chunks}개의 청크가 생성되었습니다.")
            
            input_file_path = Path(self.input_file.get())
            output_path = input_file_path.with_name(f"{input_file_path.stem}_result{input_file_path.suffix}")
            
            # tqdm을 사용하여 번역 진행 상황 표시
            with tqdm(total=total_chunks, desc="번역 진행 중", file=self.tqdm_out, 
                  bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:

                for i, chunk in enumerate(chunks):
                    if self.stop_flag:
                        self.log("번역이 사용자에 의해 중지되었습니다.")
                        break
                    
                    # 청크 번역 시작 시간 기록
                    chunk_start_time = time.time()

                    self.log(f"청크 {i+1}/{total_chunks} 번역 중...")
                    translated = translate_with_gemini(chunk, config)
                    
                    # 청크 번역 종료 시간 기록 및 경과 시간 계산
                    chunk_end_time = time.time()
                    chunk_elapsed_time = chunk_end_time - chunk_start_time
                    
                    # 경과 시간을 분:초 형식으로 변환
                    minutes, seconds = divmod(chunk_elapsed_time, 60)
                    formatted_time = f"{int(minutes)}:{int(seconds):02d}"

                    if translated:
                        save_result(translated, output_path)
                        self.log(f"청크 {i+1} 번역 완료")
                    else:
                        self.log(f"청크 {i+1} 번역 실패")
                    
                    # 진행 상황 업데이트
                    self.update_progress(i+1, total_chunks)
                    pbar.update(1)
                
                self.log("번역된 결과 후처리 중...")
                self.post_process_result(output_path)

                # 전체 번역 완료 시간 계산
                total_end_time = time.time()
                total_elapsed_time = total_end_time - total_start_time
                
                # 총 작업 시간 형식화
                total_minutes, total_seconds = divmod(total_elapsed_time, 60)
                total_hours, total_minutes = divmod(total_minutes, 60)
                
                if total_hours > 0:
                    total_formatted_time = f"{int(total_hours)}:{int(total_minutes):02d}:{int(total_seconds):02d}"
                else:
                    total_formatted_time = f"{int(total_minutes)}:{int(total_seconds):02d}"
                
                self.log(f"번역 프로세스가 완료되었습니다. 총 작업 시간: {{{total_formatted_time}}}")
                self.log(f"{input_file_path} 에 {output_path} 이/가 생성되었습니다.")
                      
        except Exception as e:
            self.log(f"치명적 오류 발생: {str(e)}")
        
        finally:
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
                    
    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def update_progress(self, current, total):
        progress = int((current / total) * 100)
        self.progress_bar["value"] = progress
        self.progress_label.config(text=f"{progress}%")
        self.root.update_idletasks()

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
            cleaned_text = re.sub(r'# 번역 결과 \(한국어\):\s*\n+', '', text)
            return cleaned_text
        except Exception as e:
            self.log(f"번역 헤더 제거 중 오류 발생: {str(e)}")
            return text  # 오류 발생 시 원본 텍스트 반환

    def remove_markdown_code_block_markers(self, text):
        """마크다운 코드 블록 마커(```korean 등)를 정규표현식으로 제거합니다."""
        try:
            # "```
            cleaned_text = re.sub(r'```korean\s*\n', '', text)
            # 코드 블록 닫는 부분(```
            cleaned_text = re.sub(r'\n\s*```', '', cleaned_text)
            return cleaned_text
        except Exception as e:
            self.log(f"마크다운 코드 블록 마커 제거 중 오류 발생: {str(e)}")
            return text  # 오류 발생 시 원본 텍스트 반환

    def post_process_result(self, output_path):
        """번역 결과 파일에서 불필요한 요소를 제거합니다."""
        try:
            if os.path.exists(output_path):
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
        except Exception as e:
            self.log(f"후처리 중 오류 발생: {str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = BatchTranslatorGUI(root)
    root.mainloop()
