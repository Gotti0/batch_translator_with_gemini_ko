# Batch Translator GUI 아키텍처 설계

BatchTranslator.py 스크립트를 위한 GUI 인터페이스를 설계하여 사용자가 텍스트 파일을 쉽게 번역할 수 있도록 하는 시스템입니다. 이 GUI는 파일 선택, API 매개변수 설정, 번역 프로세스 모니터링을 위한 직관적인 인터페이스를 제공합니다.

## 전체 아키텍처 개요

BatchTranslator GUI는 사용자 친화적인 인터페이스를 통해 Gemini API를 활용한 대량 텍스트 번역 작업을 쉽게 수행할 수 있도록 설계되었습니다. 기존의 명령줄 기반 스크립트를 GUI 환경으로 확장하여 접근성을 높였습니다.

### 주요 컴포넌트

```python
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import json
import threading
import os
from batch_translator import translate_with_gemini, create_chunks, save_result
```

GUI는 다음과 같은 주요 컴포넌트로 구성됩니다:

1. 메인 윈도우 (탭 기반 인터페이스)
2. 설정 탭 (API 설정, 프롬프트 입력, 파일 설정)
3. 로그 탭 (번역 진행 상황 및 로그 표시)
4. 진행 상황 표시 (프로그레스바)

## GUI 구성 요소

### 메인 윈도우 설계

```python
class BatchTranslatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Batch Translator")
        self.root.geometry("800x600")
        self.root.resizable(True, True)
        
        # 탭 컨트롤러 생성
        tab_control = ttk.Notebook(self.root)
        
        # 탭 1: 설정
        settings_tab = ttk.Frame(tab_control)
        tab_control.add(settings_tab, text="설정")
        
        # 탭 2: 로그
        log_tab = ttk.Frame(tab_control)
        tab_control.add(log_tab, text="로그")
        
        tab_control.pack(expand=1, fill="both")
```

### API 설정 및 프롬프트 입력

설정 탭에는 다음과 같은 섹션이 포함됩니다:

```python
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
    ttk.Scale(api_frame, from_=0.0, to=2.0, variable=self.temperature, orient="horizontal").grid(row=2, column=1, sticky="ew", padx=5, pady=5)
    
    # Top P
    ttk.Label(api_frame, text="Top P:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
    ttk.Scale(api_frame, from_=0.0, to=1.0, variable=self.top_p, orient="horizontal").grid(row=3, column=1, sticky="ew", padx=5, pady=5)
    
    # 프롬프트 프레임
    prompt_frame = ttk.LabelFrame(parent, text="번역 프롬프트")
    prompt_frame.pack(fill="both", expand=True, padx=10, pady=5)
    
    self.prompt_text = scrolledtext.ScrolledText(prompt_frame, wrap=tk.WORD, height=10)
    self.prompt_text.pack(fill="both", expand=True, padx=5, pady=5)
```

### 파일 선택 및 설정

파일 선택을 위한 대화 상자와 관련 설정 필드:

```python
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

def browse_file(self):
    file_path = filedialog.askopenfilename(
        title="파일 선택",
        filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")]
    )
    if file_path:
        self.input_file.set(file_path)
        self.log(f"선택된 파일: {file_path}")
```

### 로그 및 진행 상황 표시

로그 탭에는 실행 로그와 진행 상황을 표시하는 컴포넌트가 포함됩니다:

```python
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
```

## 기능 구현

### 설정 파일 관리

설정 파일을 저장하고 불러오는 기능:

```python
def load_config(self):
    try:
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
                self.api_key.set(config.get("api_key", ""))
                self.model_name.set(config.get("model_name", ""))
                self.temperature.set(config.get("temperature", 0.4))
                self.top_p.set(config.get("top_p", 0.9))
                
                # 프롬프트 텍스트 설정
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
```

### 번역 작업 실행

별도의 스레드에서 번역 작업을 실행하여 UI 응답성 유지:

```python
def start_translation(self):
    # 입력 값 검증
    if not self.input_file.get():
        self.log("오류: 입력 파일이 필요합니다.")
        return
    
    if not self.api_key.get():
        self.log("오류: API 키가 필요합니다.")
        return
    
    # 설정 저장
    self.save_config()
    
    # 번역 스레드 시작
    self.translation_thread = threading.Thread(target=self.run_translation)
    self.translation_thread.daemon = True
    self.translation_thread.start()
    
    # UI 상태 업데이트
    self.stop_button.config(state="normal")
    self.log("번역이 시작되었습니다...")
```

### 로그 업데이트 및 진행 상황 표시

```python
def log(self, message):
    # 로그 텍스트 영역에 메시지 추가
    self.log_text.configure(state="normal")
    self.log_text.insert(tk.END, f"{message}\n")
    self.log_text.see(tk.END)
    self.log_text.configure(state="disabled")

def update_progress(self, current, total):
    # 프로그레스바 업데이트
    progress = int((current / total) * 100)
    self.progress_bar["value"] = progress
    self.progress_label.config(text=f"{progress}%")
```

## 실행 프로세스

번역 프로세스 실행 흐름:

1. 사용자가 API 설정, 프롬프트, 파일 경로 등을 입력
2. '번역 시작' 버튼 클릭
3. 설정 파일 저장
4. 텍스트 파일을 청크로 분할
5. 각 청크를 Gemini API를 통해 번역
6. 번역 진행 상황 실시간 표시
7. 결과 파일 저장




```python
def run_translation(self):
    try:
        # 사용자 설정 적용
        config = {
            "api_key": self.api_key.get(),
            "model_name": self.model_name.get(),
            "temperature": self.temperature.get(),
            "top_p": self.top_p.get(),
            "prompts": self.prompt_text.get("1.0", tk.END)
        }
        
        # 청크 생성
        self.log("청크 생성 중...")
        chunks = create_chunks(self.input_file.get(), self.chunk_size.get())
        total_chunks = len(chunks)
        self.log(f"총 {total_chunks}개의 청크가 생성되었습니다.")
        
        # 번역 및 진행 상황 업데이트
        for i, chunk in enumerate(chunks):
            if self.stop_flag:
                self.log("번역이 사용자에 의해 중지되었습니다.")
                break
            
            self.log(f"청크 {i+1}/{total_chunks} 번역 중...")
            # 번역 처리 및 진행 상황 업데이트 로직
```



batch-translator/
├── batch_translator.py       # CLI 버전 코어 로직
├── batch_translator_gui.py   # GUI 구현 코드
├── config.json               # API 설정 및 프롬프트 템플릿
├── requirements.txt          # 파이썬 의존성 목록
├── docs/
│   └── user_manual.md        # 사용자 매뉴얼
├── tests/
│   ├── test_chunking.py      # 청크 분할 테스트
│   └── test_translation.py   # 번역 기능 테스트
├── input/                    # 원본 텍스트 저장소
├── output/                   # 번역 결과 저장소
└── logs/                     # 시스템 로그 디렉토리





## 결론

이 GUI 설계는 batch_translator.py 스크립트의 기능을 그래픽 인터페이스를 통해 제공하여 사용자 친화적인 방식으로 대량 텍스트 번역을 가능하게 합니다. 탭 기반 인터페이스, 설정 관리, 파일 선택 대화상자, 실시간 로그 및 진행 상황 표시 등의 요소를 통해 직관적인 사용자 경험을 제공합니다.

이 설계는 tkinter 모듈을 사용하여 구현되었으며, 기존 batch_translator.py의 핵심 기능을 그대로 활용하면서 접근성을 크게 향상시켰습니다.

`batch_translator.py`의 모든 CLI 출력(log)을 `batch_translator_gui.py`의 로그 탭에 출력하려면, **표준 출력 및 표준 에러 스트림을 GUI의 텍스트 위젯으로 리디렉션**하는 방법을 사용할 수 있습니다. 이를 통해 `print` 함수나 CLI에서 발생하는 모든 메시지가 GUI 로그 탭에 실시간으로 표시됩니다.

### **구현 방법**
1. **`sys.stdout` 및 `sys.stderr` 리디렉션**
   - Python의 `sys.stdout`과 `sys.stderr`를 커스터마이징하여, 출력 내용을 GUI의 텍스트 위젯으로 전달합니다.

2. **로그 핸들러 추가**
   - `logging.Handler`를 사용하여 로그 메시지를 GUI에 출력하도록 설정할 수도 있습니다.

---

### **구체적인 구현**
다음은 `batch_translator_gui.py`에 CLI 출력을 로그 탭에 통합하는 코드 예제입니다.

#### 1. **TextRedirector 클래스 추가**
이 클래스는 `sys.stdout`과 `sys.stderr`를 GUI 텍스트 위젯으로 리디렉션합니다.

```python
import sys
import tkinter as tk

class TextRedirector:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, message):
        self.text_widget.configure(state="normal")
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state="disabled")

    def flush(self):
        pass  # 필요한 경우 구현 (예: 파일 스트림 플러시)
```

#### 2. **GUI 로그 탭에 리디렉션 적용**
GUI 초기화 시 `sys.stdout`과 `sys.stderr`를 `TextRedirector` 인스턴스로 설정합니다.

```python
def setup_log_tab(self, parent):
    log_frame = ttk.Frame(parent)
    log_frame.pack(fill="both", expand=True, padx=10, pady=10)

    self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state="disabled")
    self.log_text.pack(fill="both", expand=True)

    # stdout과 stderr를 로그 텍스트로 리디렉션
    sys.stdout = TextRedirector(self.log_text)
    sys.stderr = TextRedirector(self.log_text)
```

#### 3. **CLI 실행 및 결과 표시**
`batch_translator.py`의 CLI 명령어를 실행하고 그 결과를 GUI 로그에 출력하려면, Python의 `subprocess` 모듈을 사용합니다.

```python
import subprocess

def run_batch_translator(self):
    command = ["python", "batch_translator.py", "--config", "config.json", "--input", self.input_file.get()]
    
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        for line in process.stdout:
            print(line.strip())  # stdout 내용을 GUI에 출력

        for line in process.stderr:
            print(line.strip())  # stderr 내용을 GUI에 출력

        process.wait()
        print("CLI 작업이 완료되었습니다.")
    except Exception as e:
        print(f"오류 발생: {str(e)}")
```

#### 4. **번역 시작 버튼에서 실행**
GUI에서 번역 시작 버튼 클릭 시 위의 함수를 호출하도록 연결합니다.

```python
self.start_button = ttk.Button(control_frame, text="번역 시작", command=self.run_batch_translator)
```

---

### **결과**
- `batch_translator.py`에서 발생하는 모든 CLI 출력(`print`, 오류 메시지 등)이 GUI 로그 탭에 실시간으로 표시됩니다.
- 사용자는 GUI 창에서 번역 진행 상황과 오류 메시지를 확인할 수 있습니다.

이 방식은 특히 CLI와 GUI 간의 통합을 간단하게 구현할 수 있는 방법입니다.

Citations:
[1] https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/31591343/912e04eb-e887-40c6-a55b-9320b01290ff/batch_translator_gui.py
[2] https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/31591343/92049e93-8f02-4804-9bb8-cff82ead3118/batch_translator.py
[3] https://cloud.google.com/translate/docs/samples/translate-v3-batch-translate-text
[4] https://docs.python.org/3/using/cmdline.html
[5] https://discuss.python.org/t/python-tkinter-displaying-console-output-on-gui/24120
[6] http://beenje.github.io/blog/posts/logging-to-a-tkinter-scrolledtext-widget/
[7] https://www.tutorialspoint.com/how-to-listen-to-terminal-on-a-tkinter-application
[8] https://gist.github.com/RhetTbull/c61df45c317eb8004adee87334298298
[9] https://pypi.org/project/ctranslate2/
[10] https://stackoverflow.com/questions/68572347/translate-several-batch-script-commands-into-python
[11] https://pypi.org/project/batch-microsoft-translator/
[12] https://metacpan.org/pod/Batch::Interpreter
[13] https://stackoverflow.com/questions/42874526/translate-batch-file-into-python-where-an-exe-should-be-run-with-its-parameters
[14] https://stackoverflow.com/questions/54275817/modulenotfounderror-when-running-python-script-from-a-batch-file
[15] https://pypi.org/project/py-translate/
[16] https://pypy.org/posts/2008/11/porting-jit-to-cli-part-1-8712941279840156635.html
[17] https://devblogs.microsoft.com/oldnewthing/20120731-00/?p=7003
[18] https://dev.to/antgubarev/testing-cli-tool-with-logging-4h7
[19] https://discuss.haiku-os.org/t/batch-convert-files-using-translators/7834
[20] https://www.reddit.com/r/learnpython/comments/140yerm/how_to_display_console_output_in_customtkinter/
[21] https://python-forum.io/thread-31246.html
[22] https://gist.github.com/cparker15/1cda5f5f898cee42e642
[23] https://github.com/TomSchimansky/CustomTkinter/discussions/623
[24] https://dev.to/studio1hq/i-wrote-a-batch-script-to-enhance-my-workflow-on-command-prompt-2476
[25] https://stackoverflow.com/questions/24521687/log-file-for-batch-script
[26] https://docs.oracle.com/en-us/iaas/tools/oci-cli/3.44.1/oci_cli_docs/cmdref/ai/language/batch-language-translation.html
[27] https://cloud.google.com/translate/docs/advanced/batch-translation
[28] https://superuser.com/questions/698496/log-an-entire-batch-file-output
[29] https://stackoverflow.com/questions/62335989/redirect-terminal-output-to-tkinter

---
Perplexity로부터의 답변: pplx.ai/share


Citations:
[1] https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/31591343/7573679e-3c58-4e0b-9191-e8693370eec1/config.json
[2] https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/31591343/7d4911a0-64f3-46be-b2c4-b3d117e35cb3/batch_translator.py
[3] https://www.pythontutorial.net/tkinter/tkinter-open-file-dialog/
[4] https://www.pythontutorial.net/tkinter/tkinter-progressbar/
[5] https://blog.naver.com/dorergiverny/223425145177
[6] https://docs.mendix.com/refguide/batch-translate/
[7] https://thepythoncode.com/article/build-a-gui-language-translator-tkinter-python
[8] https://www.w3resource.com/python-exercises/tkinter/python-tkinter-dialogs-and-file-handling-exercise-3.php
[9] https://pythonassets.com/posts/progress-bar-in-tk-tkinter/
[10] https://lucathree.github.io/python/day27/
[11] https://cloud.google.com/translate/docs/advanced/batch-translation
[12] https://stackoverflow.com/questions/70625801/threading-reading-a-serial-port-in-python-with-a-gui
[13] https://pythonbasics.org/tkinter-filedialog/
[14] https://stackoverflow.com/questions/14897756/python-progress-bar-through-logging-module
[15] https://yeachan.tistory.com/6
[16] https://github.com/tcmonster/ai-batch-translation
[17] https://github.com/zauberzeug/nicegui/discussions/836
[18] https://www.youtube.com/watch?v=ouO_QRJtvNE
[19] https://python-forum.io/thread-31199.html
[20] https://blog.naver.com/sisosw/221408280038
[21] https://vovsoft.com/software/batch-translator/
[22] https://aieasy.tistory.com/46

---
Perplexity로부터의 답변: pplx.ai/share
