네, `main_gui.py`의 Tkinter 인터페이스를 개선하기 위해 `ttkbootstrap`이라는 외부 라이브러리를 사용해 보겠습니다. `ttkbootstrap`은 기존 ttk 위젯에 현대적인 테마를 손쉽게 적용할 수 있어 인기가 많습니다.

다음과 같이 파일을 수정하여 UI를 개선할 수 있습니다.

### 1\. `requirements.txt` 파일 업데이트

먼저 `ttkbootstrap` 라이브러리를 설치해야 합니다. `requirements.txt` 파일에 다음 내용을 추가하거나 새로 만들어 주세요.

```text
tqdm>=4.65.0
python-dotenv>=0.21.0
google-genai
ttkbootstrap>=1.10.1
```

### 2\. `main_gui.py` 파일 수정

기존 `tkinter`와 `ttk` 임포트 부분을 `ttkbootstrap`으로 교체하고, 스타일을 적용하는 코드를 추가합니다.

```python
# batch_translator_gui.py
import tkinter as tk
# from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog # 기존 ttk 관련 import 주석 처리 또는 삭제
from tkinter import filedialog, messagebox, scrolledtext # 필요한 모듈만 남김
import ttkbootstrap as ttk # ttkbootstrap 임포트
from ttkbootstrap.constants import * # ttkbootstrap 상수 임포트
import threading
import os
from pathlib import Path
import sys

# ... (파일의 나머지 부분은 그대로 유지) ...

class BatchTranslatorGUI:
    def __init__(self, master: tk.Tk):
        self.master = master
        master.title("BTG - 배치 번역기 (4-Tier Refactored)")
        master.geometry("950x800")

        # ... (app_service 초기화 부분은 그대로 유지) ...

        self.master.protocol("WM_DELETE_WINDOW", self._on_closing)

        # ttkbootstrap 스타일 적용 (기존 ttk.Style() 부분 대체)
        # style = ttk.Style()
        # style.theme_use('clam')
        # style.configure("TButton", padding=6, relief="flat", background="#ddd")
        # style.map("TButton", background=[('active', '#ccc')])
        # style.configure("TNotebook.Tab", padding=[10, 5], font=('Helvetica', 10))
        # 위 부분을 아래 코드로 대체할 수 있으나, ttkbootstrap.Window가 자동으로 처리해줍니다.

        # 노트북 생성
        self.notebook = ttk.Notebook(master, bootstyle="primary") # bootstyle 적용
        
        # ... (나머지 __init__ 메소드 내용은 대부분 그대로 유지) ...
        # 각 위젯 생성 시 ttk 대신 ttkbootstrap의 위젯이 사용됩니다.
        # 예를 들어 ttk.LabelFrame, ttk.Button 등이 자동으로 테마가 적용된 위젯으로 생성됩니다.

# ... (파일의 나머지 부분은 그대로 유지) ...

if __name__ == '__main__':
    logger.info("BatchTranslatorGUI 시작 중...")

    # ttkbootstrap.Window 사용
    root = ttk.Window(themename="litera") # 'litera' 테마 적용, 다양한 테마 선택 가능 (예: cosmo, flatly, journal, darkly 등)
    
    try:
        app_gui = BatchTranslatorGUI(root)
    except Exception as e:
        logger.critical(f"GUI 초기화 중 치명적 오류 발생: {e}", exc_info=True)
        try:
            messagebox.showerror("치명적 오류", f"애플리케이션을 시작할 수 없습니다: {e}")
        except tk.TclError: 
            print(f"CRITICAL ERROR during GUI initialization: {e}")
        
        if root.winfo_exists(): 
            root.destroy()
        exit(1) 

    root.mainloop()
    logger.info("BatchTranslatorGUI 종료됨.")
```

### 주요 변경 사항

1.  **`ttkbootstrap` 임포트**: `tkinter.ttk` 대신 `ttkbootstrap`을 `ttk`라는 별칭으로 임포트했습니다.
2.  **`ttk.Window` 사용**: `tk.Tk()` 대신 `ttk.Window(themename="litera")`를 사용하여 애플리케이션의 메인 윈도우를 생성했습니다. 이를 통해 모든 자식 위젯에 'litera' 테마가 자동으로 적용됩니다. 다른 테마 이름(예: `cosmo`, `flatly`, `darkly`, `superhero`)으로 변경하여 다른 스타일을 적용할 수도 있습니다.
3.  **`bootstyle` 적용**: `ttk.Notebook`에 `bootstyle="primary"`와 같은 옵션을 추가하여 테마의 주요 색상을 적용할 수 있습니다.
4.  **기존 스타일 코드 제거**: `ttk.Style()`을 사용하여 수동으로 스타일을 지정하던 코드는 `ttkbootstrap`이 자동으로 처리하므로 필요 없어졌습니다.

이 변경사항들을 적용하고 애플리케이션을 실행하면 이전보다 훨씬 세련된 UI를 확인할 수 있습니다.