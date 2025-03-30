# batch_translator_with_gemini
This is a novel translation project utilizing batch logic and the Gemini API.

Okay, here is a `README.md` file for your `batch_translator_with_gemini` project, incorporating information from the provided Python scripts and architecture description.


# Batch Translator with Gemini API (Gemini API를 활용한 배치 번역기)

This project provides a tool for translating large text files in batches using the Google Gemini API. It offers both a graphical user interface (GUI) built with Tkinter and a command-line interface (CLI) for flexibility. The tool automatically chunks large text files to fit within API limits and handles potential API errors gracefully.

이것은 Google Gemini API를 사용하여 대용량 텍스트 파일을 배치로 번역하는 도구입니다. Tkinter로 구축된 그래픽 사용자 인터페이스(GUI)와 유연성을 위한 명령줄 인터페이스(CLI)를 모두 제공합니다. 이 도구는 API 제한에 맞게 큰 텍스트 파일을 자동으로 청크로 나누고 잠재적인 API 오류를 적절히 처리합니다.

## Features (주요 기능)

*   **GUI Interface:** An intuitive interface built with Tkinter for easy configuration and operation. (Tkinter 기반의 직관적인 GUI 제공)
*   **CLI Interface:** Option to run translations directly from the command line for scripting or automation. (스크립팅 또는 자동화를 위한 CLI 옵션 제공)
*   **Batch Translation:** Translate entire `.txt` files efficiently. (전체 `.txt` 파일 효율적 번역)
*   **Google Gemini API Integration:** Leverages the power of Google's Gemini models for translation. (Google Gemini 모델의 번역 능력 활용)
*   **Automatic Text Chunking:** Splits large text files into smaller chunks to avoid API request size limits. (API 요청 크기 제한을 피하기 위해 큰 텍스트 파일을 작은 청크로 자동 분할)
*   **Customizable API Parameters:** Configure API Key, Model Name, Temperature, and Top-P. (API 키, 모델 이름, Temperature, Top-P 설정 가능)
*   **Customizable Translation Prompt:** Define your own prompt template for translation requests. (번역 요청을 위한 사용자 정의 프롬프트 템플릿 정의 가능)
*   **Configuration Management:** Save and load settings (API key, model, prompt, etc.) using a `config.json` file. (`config.json` 파일을 사용하여 설정 저장 및 불러오기)
*   **Real-time Progress Tracking:** Monitor the translation progress via a progress bar (GUI) or `tqdm` (CLI), along with detailed logs. (진행률 표시줄(GUI) 또는 `tqdm`(CLI) 및 상세 로그를 통해 번역 진행 상황 실시간 모니터링)
*   **Robust Error Handling:** Includes retries with exponential backoff for rate limits and other transient errors. Automatically splits chunks further and retries if "PROHIBITED_CONTENT" or similar errors occur. (API 제한 및 일시적 오류에 대한 지수 백오프 재시도 포함. "PROHIBITED_CONTENT" 등 오류 발생 시 청크 추가 분할 및 재시도)
*   **HTML Tag Removal:** Cleans potential HTML tags from the final translated output. (최종 번역 결과에서 잠재적인 HTML 태그 제거)

## Prerequisites (사전 요구 사항)

*   **Python:** Version 3.7 or higher. (Python 3.7 이상)
*   **Google Cloud Project & Gemini API Key:** You need an active Google Cloud project with the Generative Language API (Gemini API) enabled and a valid API Key. (활성화된 Generative Language API(Gemini API)와 유효한 API 키가 있는 활성 Google Cloud 프로젝트 필요)

## Installation (설치)

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Gotti0/batch_translator_with_gemini
    cd batch_translator_gui
    ```
2.  **Install dependencies:**
    It's recommended to use a virtual environment.
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    pip install -r requirements.txt
    ```


## Usage (사용법)

### 1. GUI Mode (GUI 모드)

Run the GUI application:
```bash
python batch_translator_gui.py
```

*   **Settings Tab (설정 탭):**
    *   **API Settings (API 설정):** Enter your Gemini API Key and the desired Model Name (e.g., `gemini-1.5-flash-latest`, `gemini-1.0-pro`). Adjust Temperature and Top-P using the sliders.
    *   **File Settings (파일 설정):**
        *   **Input File (입력 파일):** Click "Browse..." (찾아보기...) to select the `.txt` file you want to translate.
        *   **Chunk Size (청크 크기):** Set the maximum character size for each chunk (default: 6000).
    *   **Translation Prompt (번역 프롬프트):** Enter the prompt template. Use `{{slot}}` as a placeholder where the text chunk will be inserted. Example: `Translate the following English text to Korean:\n\n{{slot}}`
*   **Log Tab (로그 탭):** View real-time progress updates, chunk information, and any errors encountered during the translation process. A progress bar shows the overall completion status.
*   **Buttons (버튼):**
    *   **Start Translation (번역 시작):** Begins the translation process using the current settings.
    *   **Stop (중지):** Requests to stop the translation after the current chunk is finished.
    *   **Save Config (설정 저장):** Saves the current API settings and prompt to `config.json`.
    *   **Load Config (설정 불러오기):** Loads settings from `config.json`.

### 2. CLI Mode (CLI 모드)

Run the script from your terminal:
```bash
python batch_translator.py --input /path/to/your/novel.txt --config config.json
```

**Command-line arguments:**

*   `--config` (optional): Path to the JSON configuration file (default: `config.json`). This file should contain `api_key`, `model_name`, `temperature`, `top_p`, and `prompts`.
*   `--input`: Path to the input `.txt` file to be translated. (Required if not provided via prompt).
*   `--chunk-size` (optional): Maximum size of each text chunk (default: 6000).
*   `--delay` (optional): Delay in seconds between API calls to avoid rate limits (default: 2.0).

**Example `config.json`:**
```json
{
    "api_key": "YOUR_GEMINI_API_KEY",
    "model_name": "gemini-1.5-flash-latest",
    "temperature": 1.0,
    "top_p": 0.9,
    "prompts": "Translate the following text into Korean. Maintain the original tone and style. Ensure accuracy for dialogue and descriptions:\n\n{{slot}}"
}
```

## File Structure (파일 구조)

```
batch-translator/
├── batch_translator.py       # CLI version core logic
├── batch_translator_gui.py   # GUI implementation code
├── config.json               # API settings and prompt template
├── requirements.txt          # Python dependencies list
├── GUI_아키텍쳐.txt          # GUI architecture description (Optional)
├── input/                    # (Optional) Directory for source text files
│   └── example.txt
├── output/                   # Directory where translated results are saved
│   └── example_result.txt
└── logs/                     # (Optional) Directory for system logs (if implemented)
```

*   The script automatically creates the `output/` directory if it doesn't exist.
*   Translated files are saved in the `output/` directory with `_result` appended to the original filename (e.g., `input/novel.txt` -> `output/novel_result.txt`).

## Error Handling (오류 처리)

*   **Rate Limits/Server Errors:** The script automatically waits and retries using exponential backoff (up to 5 times by default) if it encounters API rate limits (429) or temporary server issues (5xx).
*   **Content Safety Errors:** If the API returns errors related to content safety ("PROHIBITED_CONTENT", "Invalid"), the script attempts to split the problematic chunk into smaller pieces and translate them recursively using a streaming approach. If splitting fails repeatedly, it logs an error for that chunk.
*   **File Errors:** Checks for the existence of input files and configuration files.
*   **Configuration Errors:** Validates the JSON format of the configuration file.

## License (라이선스)

(Optional: Add your preferred license, e.g., MIT License)

This project is licensed under the MIT License. See the LICENSE file for details.
```

**To make this complete:**

1.  **Save:** Save the content above as `README.md` in the root directory of your `batch-translator` project.
2.  **`requirements.txt`:** Create the `requirements.txt` file as shown in the Installation section.
3.  **`config.json`:** Ensure you have a `config.json` file (or create one based on the example) and **replace `"YOUR_GEMINI_API_KEY"` with your actual key**. **Never commit your API key to a public repository!** Consider using environment variables or other secure methods for production use.
4.  **(Optional) License:** If you choose a license like MIT, create a `LICENSE` file containing the license text.
