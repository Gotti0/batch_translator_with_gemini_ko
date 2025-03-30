import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import json
import threading
import os
from batch_translator import translate_with_gemini, create_chunks, save_result

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
            
            output_path = os.path.join("output", os.path.basename(self.input_file.get()))
            if os.path.exists(output_path):
                os.remove(output_path)
            
            for i, chunk in enumerate(chunks):
                if self.stop_flag:
                    self.log("번역이 사용자에 의해 중지되었습니다.")
                    break
                
                self.log(f"청크 {i+1}/{total_chunks} 번역 중...")
                translated = translate_with_gemini(chunk, config)
                
                if translated:
                    save_result(translated, output_path)
                    self.log(f"청크 {i+1} 번역 완료")
                else:
                    self.log(f"청크 {i+1} 번역 실패")
                
                self.update_progress(i+1, total_chunks)
            
            self.log("번역 프로세스가 완료되었습니다.")
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            
        except Exception as e:
            self.log(f"치명적 오류 발생: {str(e)}")
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

if __name__ == "__main__":
    root = tk.Tk()
    app = BatchTranslatorGUI(root)
    root.mainloop()
