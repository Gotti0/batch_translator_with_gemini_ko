# batch_translator_gui.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import threading
import os
from pathlib import Path
import sys # sys 모듈 임포트

# 프로젝트 루트 디렉토리를 sys.path에 추가
project_root = Path(__file__).resolve().parent
sys.path.append(str(project_root))

from typing import Optional, Dict, Any, List, Callable, Union
from dataclasses import dataclass
import json
import time
import io
from tqdm import tqdm
import logging

# 4계층 아키텍처의 AppService 및 DTOs, Exceptions 임포트
try:
    from app_service import AppService
    from dtos import TranslationJobProgressDTO, PronounExtractionProgressDTO, ModelInfoDTO
    from exceptions import BtgConfigException, BtgServiceException, BtgFileHandlerException, BtgApiClientException, BtgPronounException, BtgException
    from logger_config import setup_logger
    from file_handler import get_metadata_file_path, load_metadata, _hash_config_for_metadata, delete_file
except ImportError as e:
    print(f"초기 임포트 오류: {e}. 스크립트가 프로젝트 루트에서 실행되고 있는지, "
          f"PYTHONPATH가 올바르게 설정되었는지 확인하세요.")
    # Fallback imports for mock objects if main imports fail
    def setup_logger(name, level=logging.DEBUG):
        mock_logger = logging.getLogger(name)
        mock_logger.setLevel(level)
        if not mock_logger.hasHandlers():
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            mock_logger.addHandler(handler)
        return mock_logger

    @dataclass
    class TranslationJobProgressDTO:
        total_chunks: int = 0; processed_chunks: int = 0; successful_chunks: int = 0; failed_chunks: int = 0
        current_status_message: str = "대기 중"; current_chunk_processing: Optional[int] = None; last_error_message: Optional[str] = None
    @dataclass
    class PronounExtractionProgressDTO:
        total_sample_chunks: int = 0; processed_sample_chunks: int = 0; current_status_message: str = "대기 중"
    @dataclass
    class ModelInfoDTO: name: str; display_name: str; description: Optional[str] = None; version: Optional[str] = None
    class BtgBaseException(Exception): pass
    class BtgConfigException(BtgBaseException): pass
    class BtgServiceException(BtgBaseException): pass
    class BtgFileHandlerException(BtgBaseException): pass
    class BtgApiClientException(BtgBaseException): pass
    class BtgPronounException(BtgBaseException): pass
    BtgException = BtgBaseException

    def get_metadata_file_path(p): return Path(str(p) + "_metadata.json")
    def load_metadata(p): return {}
    def _hash_config_for_metadata(c): return "mock_hash"
    def delete_file(p): pass




    class AppService: # Mock AppService
        def __init__(self, config_file_path: Optional[Union[str, Path]] = None):
            self.config_file_path = config_file_path
            self.config: Dict[str, Any] = self._get_mock_default_config()
            self.is_translation_running = False
            self.stop_requested = False
            self._translation_lock = threading.Lock()
            self._progress_lock = threading.Lock()
            self.processed_chunks_count = 0
            self.successful_chunks_count = 0
            self.failed_chunks_count = 0
            self.pronoun_service = self
            self.translation_service = self
            self.gemini_client = True 
            self.config_manager = self 
            print(f"Mock AppService initialized. Config path: {config_file_path}")
            self.load_app_config() 

        def _get_mock_default_config(self) -> Dict[str, Any]:
            return {
                "api_key": "",
                "api_keys": ["mock_api_key_1", "mock_api_key_2"],
                "service_account_file_path": None,
                "use_vertex_ai": False,
                "gcp_project": "mock-project-id",
                "gcp_location": "mock-location",
                "model_name": "gemini-2.0-flash", 
                "temperature": 0.7, "top_p": 0.9, "chunk_size": 100,
                "prompts": "Translate to Korean: {{slot}}",
                "pronouns_csv": "mock_pronouns.csv", 
                "max_workers": os.cpu_count() or 1, 
                "auth_credentials": ""
            }
        def load_app_config(self) -> Dict[str, Any]:
            print("Mock AppService: load_app_config called.")
            if self.config_file_path and Path(self.config_file_path).exists():
                try:
                    with open(self.config_file_path, 'r', encoding='utf-8') as f:
                        loaded_data = json.load(f)
                        default_conf = self._get_mock_default_config()
                        default_conf.update(loaded_data) 
                        self.config = default_conf 
                        if not self.config.get("api_keys") and self.config.get("api_key"):
                            self.config["api_keys"] = [self.config["api_key"]]
                        print(f"Mock AppService: Loaded config from {self.config_file_path}")
                except Exception as e:
                    print(f"Mock AppService: Error loading config file {self.config_file_path}: {e}")
                    self.config = self._get_mock_default_config() 
            else: self.config = self._get_mock_default_config() 
            return self.config.copy() 
        
        def save_app_config(self, config_data: Dict[str, Any]) -> bool:
            print(f"Mock AppService: save_app_config called with data: {config_data}")
            self.config = config_data 
            if self.config_file_path:
                try:
                    with open(self.config_file_path, 'w', encoding='utf-8') as f:
                        json.dump(config_data, f, indent=4)
                    print(f"Mock AppService: Saved config to {self.config_file_path}")
                    self.load_app_config()
                    return True
                except Exception as e:
                    print(f"Mock AppService: Error saving config to {self.config_file_path}: {e}")
                    return False
            return True
        def get_available_models(self) -> List[Dict[str, Any]]:
            print("Mock AppService: get_available_models called.")
            time.sleep(0.1) 
            return [
                {"name": "models/gemini-2.0-flash", "display_name": "Gemini 2.0 Flash", "short_name": "gemini-2.0-flash", "description": "Mock Flash model"},
                {"name": "models/gemini-pro", "display_name": "Gemini Pro", "short_name": "gemini-pro", "description": "Mock Pro model"},
                {"name": "models/text-embedding-004", "display_name": "Text Embedding 004", "short_name": "text-embedding-004"}
            ]
        def extract_pronouns(self, input_file_path: Union[str, Path], progress_callback: Optional[Callable[[PronounExtractionProgressDTO], None]] = None, tqdm_file_stream=None) -> Path:
            print(f"Mock AppService: extract_pronouns called for {input_file_path}")
            total_samples = 5
            iterable_chunks = range(total_samples)
            if tqdm_file_stream:
                iterable_chunks = tqdm(iterable_chunks, total=total_samples, desc="고유명사 샘플 처리(Mock)", file=tqdm_file_stream, unit="청크", leave=False)
            for i in iterable_chunks:
                if hasattr(self, 'stop_requested') and self.stop_requested: break
                time.sleep(0.05)
                if progress_callback:
                    msg = f"표본 청크 {i+1}/{total_samples} 처리 중" if i < total_samples -1 else "고유명사 추출 완료"
                    progress_callback(PronounExtractionProgressDTO(total_sample_chunks=total_samples, processed_sample_chunks=i+1, current_status_message=msg))
            return Path(str(input_file_path) + "_seed.csv")

        def start_translation(self, input_file_path: Union[str, Path], output_file_path: Union[str, Path],
                              progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
                              status_callback: Optional[Callable[[str], None]] = None,
                              tqdm_file_stream: Optional[Any] = None):
            print(f"Mock AppService: start_translation called for {input_file_path} to {output_file_path}")
            print(f"Mock AppService: Using max_workers: {self.config.get('max_workers')}") 
            with self._translation_lock: self.is_translation_running = True; self.stop_requested = False; self.processed_chunks_count = 0; self.successful_chunks_count = 0; self.failed_chunks_count = 0
            if status_callback: status_callback("번역 시작됨 (Mock)")
            total_mock_chunks = 10
            if progress_callback:
                progress_callback(TranslationJobProgressDTO(total_mock_chunks,0,0,0,"분할 완료 (Mock)", current_chunk_processing=1 if total_mock_chunks > 0 else None))
            chunk_indices = range(total_mock_chunks)
            if tqdm_file_stream:
                chunk_indices = tqdm(chunk_indices, total=total_mock_chunks, desc="청크 번역(Mock)", file=tqdm_file_stream, unit="청크", leave=False)
            for i in chunk_indices:
                if self.stop_requested:
                    print("Mock AppService: Translation stopped by user.")
                    if status_callback: status_callback("번역 중단됨 (사용자 요청)")
                    break
                time.sleep(0.2)
                self.processed_chunks_count += 1;
                if i % 4 == 0 and i > 0 :
                    self.failed_chunks_count +=1
                    if progress_callback:
                        progress_callback(TranslationJobProgressDTO(total_mock_chunks, self.processed_chunks_count, self.successful_chunks_count, self.failed_chunks_count, f"청크 {i+1}/{total_mock_chunks} 실패 (Mock)", i+1, "Mock API 오류"))
                    continue
                self.successful_chunks_count += 1
                if progress_callback:
                    progress_callback(TranslationJobProgressDTO(total_mock_chunks, self.processed_chunks_count, self.successful_chunks_count, self.failed_chunks_count, f"청크 {self.processed_chunks_count}/{total_mock_chunks} 번역 중 (Mock)", self.processed_chunks_count))
            final_status = "번역 완료 (Mock)"
            if self.stop_requested: final_status = "번역 중단됨 (Mock)"
            elif self.failed_chunks_count > 0: final_status = f"번역 완료 (실패 {self.failed_chunks_count}개) (Mock)"
            if status_callback: status_callback(final_status)
            if progress_callback:
                progress_callback(TranslationJobProgressDTO(total_mock_chunks, self.processed_chunks_count, self.successful_chunks_count, self.failed_chunks_count, final_status))
            with self._translation_lock: self.is_translation_running = False
        def request_stop_translation(self):
            print("Mock AppService: request_stop_translation called.")
            if self.is_translation_running:
                with self._translation_lock: self.stop_requested = True

logger = setup_logger(__name__ + "_gui")

class TqdmToTkinter(io.StringIO):
    def __init__(self, widget: scrolledtext.ScrolledText):
        super().__init__()
        self.widget = widget
        self.widget.tag_config("TQDM", foreground="green")

    def write(self, buf):
        def append_to_widget():
            if not self.widget.winfo_exists(): return
            current_state = self.widget.cget("state")
            self.widget.config(state=tk.NORMAL)
            self.widget.insert(tk.END, buf.strip() + '\n', "TQDM")
            self.widget.config(state=current_state) 
            self.widget.see(tk.END)
        if self.widget.winfo_exists(): 
            self.widget.after(0, append_to_widget)

    def flush(self):
        pass

class ScrollableFrame:
    """스크롤 가능한 프레임을 생성하는 클래스"""
    
    def __init__(self, parent, height=None):
        # 메인 프레임 생성
        self.main_frame = ttk.Frame(parent)
        
        # Canvas와 Scrollbar 생성
        self.canvas = tk.Canvas(self.main_frame, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.main_frame, orient="vertical", command=self.canvas.yview)
        
        # 스크롤 가능한 내용을 담을 프레임
        self.scrollable_frame = ttk.Frame(self.canvas)
        
        # 스크롤바 설정
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # 프레임이 변경될 때마다 스크롤 영역 업데이트
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        # Canvas에 프레임 추가
        self.canvas_frame = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        # Canvas 크기 변경 시 내부 프레임 크기 조정
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # 마우스 휠 스크롤 바인딩
        self._bind_mouse_wheel()
        
        # 위젯 배치
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # 높이 설정 (선택사항)
        if height:
            self.canvas.configure(height=height)
    
    def _on_canvas_configure(self, event):
        """Canvas 크기 변경 시 내부 프레임 너비 조정"""
        self.canvas.itemconfig(self.canvas_frame, width=event.width)
    
    def _bind_mouse_wheel(self):
        """마우스 휠 스크롤 이벤트 바인딩"""
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def _bind_to_mousewheel(event):
            self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        def _unbind_from_mousewheel(event):
            self.canvas.unbind_all("<MouseWheel>")
        
        self.main_frame.bind('<Enter>', _bind_to_mousewheel)
        self.main_frame.bind('<Leave>', _unbind_from_mousewheel)
    
    def pack(self, **kwargs):
        """메인 프레임 pack"""
        self.main_frame.pack(**kwargs)
    
    def grid(self, **kwargs):
        """메인 프레임 grid"""
        self.main_frame.grid(**kwargs)


class BatchTranslatorGUI:
    def __init__(self, master: tk.Tk):
        self.master = master
        master.title("BTG - 배치 번역기 (4-Tier Refactored)")
        master.geometry("950x800") 

        self.app_service: Optional[AppService] = None
        try:
            config_file = Path("config.json")
            self.app_service = AppService(config_file_path=config_file) 
            logger.info(f"AppService 인스턴스가 '{config_file}' 설정으로 생성되었습니다.")
        except BtgConfigException as e:
            logger.error(f"설정 파일 오류로 AppService 초기화 실패: {e}")
            messagebox.showerror("설정 오류", f"설정 파일 처리 중 오류가 발생했습니다: {e}\n기본 설정으로 시도합니다.")
            try:
                self.app_service = AppService() 
                logger.info("AppService가 기본 설정으로 초기화되었습니다.")
            except Exception as e_fallback:
                logger.critical(f"AppService 기본 설정 초기화마저 실패: {e_fallback}")
                messagebox.showerror("치명적 오류", f"애플리케이션 서비스 초기화에 실패했습니다: {e_fallback}")
                return 
        except Exception as e:
            logger.critical(f"AppService 초기화 중 예상치 못한 오류: {e}", exc_info=True)
            messagebox.showerror("초기화 오류", f"애플리케이션 서비스 초기화 중 심각한 오류 발생: {e}")
            return

        self.master.protocol("WM_DELETE_WINDOW", self._on_closing)

        style = ttk.Style()
        style.theme_use('clam') 
        style.configure("TButton", padding=6, relief="flat", background="#ddd")
        style.map("TButton", background=[('active', '#ccc')])
        style.configure("TNotebook.Tab", padding=[10, 5], font=('Helvetica', 10))

        # 노트북 생성
        self.notebook = ttk.Notebook(master)
        
        # 스크롤 가능한 프레임들로 탭 생성
        self.settings_scroll = ScrollableFrame(self.notebook)
        self.pronouns_scroll = ScrollableFrame(self.notebook)
        self.log_tab = ttk.Frame(self.notebook, padding="10")  # 로그 탭은 기존 유지
        
        # 탭 추가
        self.notebook.add(self.settings_scroll.main_frame, text='설정 및 번역')
        self.notebook.add(self.pronouns_scroll.main_frame, text='고유명사 관리')
        self.notebook.add(self.log_tab, text='실행 로그')
        self.notebook.pack(expand=True, fill='both')
        
        # 위젯 생성 (스크롤 가능한 프레임 사용)
        self._create_settings_widgets()
        self._create_pronouns_widgets()
        self._create_log_widgets()

        if self.app_service:
            self._load_initial_config_to_ui() 
        else:
            self._log_message("AppService 초기화 실패로 UI에 설정을 로드할 수 없습니다.", "ERROR")

        

    def _load_initial_config_to_ui(self):
        if not self.app_service:
            logger.warning("AppService가 초기화되지 않아 UI에 설정을 로드할 수 없습니다.")
            return
        try:
            config = self.app_service.config 
            logger.info(f"초기 UI 로드 시작. AppService.config 사용: {json.dumps(config, indent=2, ensure_ascii=False)}")

            self.api_keys_text.config(state=tk.NORMAL)
            self.api_keys_text.delete('1.0', tk.END)
            api_keys_list = config.get("api_keys", [])
            logger.debug(f"Config에서 가져온 api_keys: {api_keys_list}")
            if api_keys_list:
                self.api_keys_text.insert('1.0', "\n".join(api_keys_list))
            
            self.service_account_file_entry.delete(0, tk.END)
            sa_file_path = config.get("service_account_file_path")
            logger.debug(f"Config에서 가져온 service_account_file_path: {sa_file_path}")
            self.service_account_file_entry.insert(0, sa_file_path if sa_file_path is not None else "")

            use_vertex_ai_val = config.get("use_vertex_ai", False)
            logger.debug(f"Config에서 가져온 use_vertex_ai: {use_vertex_ai_val}")
            self.use_vertex_ai_var.set(use_vertex_ai_val) 
            
            self.gcp_project_entry.delete(0, tk.END)
            gcp_project_val = config.get("gcp_project")
            logger.debug(f"Config에서 가져온 gcp_project: {gcp_project_val}")
            self.gcp_project_entry.insert(0, gcp_project_val if gcp_project_val is not None else "")

            self.gcp_location_entry.delete(0, tk.END)
            gcp_location_val = config.get("gcp_location")
            logger.debug(f"Config에서 가져온 gcp_location: {gcp_location_val}")
            self.gcp_location_entry.insert(0, gcp_location_val if gcp_location_val is not None else "")

            self._toggle_vertex_fields() 
            
            model_name_from_config = config.get("model_name", "gemini-1.5-flash-latest")
            logger.debug(f"Config에서 가져온 model_name: {model_name_from_config}")
            self.model_name_combobox.set(model_name_from_config) 
            self._update_model_list_ui() 

            temperature_val = config.get("temperature", 0.7)
            logger.debug(f"Config에서 가져온 temperature: {temperature_val}, 타입: {type(temperature_val)}")
            try:
                self.temperature_scale.set(float(temperature_val))
                self.temperature_label.config(text=f"{self.temperature_scale.get():.2f}") 
            except (ValueError, TypeError) as e:
                logger.warning(f"온도 값 설정 오류 ({temperature_val}): {e}. 기본값 사용.")
                default_temp = self.app_service.config_manager.get_default_config().get("temperature", 0.7)
                self.temperature_scale.set(default_temp)
                self.temperature_label.config(text=f"{default_temp:.2f}")


            top_p_val = config.get("top_p", 0.9)
            logger.debug(f"Config에서 가져온 top_p: {top_p_val}, 타입: {type(top_p_val)}")
            try:
                self.top_p_scale.set(float(top_p_val))
                self.top_p_label.config(text=f"{self.top_p_scale.get():.2f}") 
            except (ValueError, TypeError) as e:
                logger.warning(f"Top P 값 설정 오류 ({top_p_val}): {e}. 기본값 사용.")
                default_top_p = self.app_service.config_manager.get_default_config().get("top_p", 0.9)
                self.top_p_scale.set(default_top_p)
                self.top_p_label.config(text=f"{default_top_p:.2f}")

            chunk_size_val = config.get("chunk_size", 6000)
            logger.debug(f"Config에서 가져온 chunk_size: {chunk_size_val}")
            self.chunk_size_entry.delete(0, tk.END)
            self.chunk_size_entry.insert(0, str(chunk_size_val))
            
            max_workers_val = config.get("max_workers", os.cpu_count() or 1)
            logger.debug(f"Config에서 가져온 max_workers: {max_workers_val}")
            self.max_workers_entry.delete(0, tk.END)
            self.max_workers_entry.insert(0, str(max_workers_val))

            prompts_val = config.get("prompts", "") 
            logger.debug(f"Config에서 가져온 prompts: '{str(prompts_val)[:100]}...', 타입: {type(prompts_val)}")
            self.prompt_text.delete('1.0', tk.END)
            if isinstance(prompts_val, str):
                self.prompt_text.insert('1.0', prompts_val)
            elif isinstance(prompts_val, (list, tuple)) and prompts_val: 
                self.prompt_text.insert('1.0', str(prompts_val[0]))
            else: 
                default_prompt_config = self.app_service.config_manager.get_default_config().get("prompts", "")
                default_prompt_str = default_prompt_config[0] if isinstance(default_prompt_config, tuple) and default_prompt_config else str(default_prompt_config)
                self.prompt_text.insert('1.0', default_prompt_str)
                logger.warning(f"Prompts 타입이 예상과 다릅니다 ({type(prompts_val)}). 기본 프롬프트 사용.")

            pronoun_csv_val = config.get("pronouns_csv")
            logger.debug(f"Config에서 가져온 pronouns_csv: {pronoun_csv_val}")
            self.pronoun_csv_path_entry.delete(0, tk.END)
            self.pronoun_csv_path_entry.insert(0, pronoun_csv_val if pronoun_csv_val is not None else "")

            logger.info("UI에 설정 로드 완료.")
        except BtgConfigException as e: 
            messagebox.showerror("설정 로드 오류", f"설정 로드 중 오류: {e}")
            self._log_message(f"설정 로드 오류: {e}", "ERROR")
        except Exception as e:
            messagebox.showerror("오류", f"설정 UI 반영 중 예상치 못한 오류: {e}")
            self._log_message(f"설정 UI 반영 중 오류: {e}", "ERROR", exc_info=True)


    def _create_settings_widgets(self):
        # 스크롤 가능한 프레임의 내부 프레임 사용
        settings_frame = self.settings_scroll.scrollable_frame   

        # API 및 인증 설정
        api_frame = ttk.LabelFrame(settings_frame, text="API 및 인증 설정", padding="10")
        api_frame.pack(fill="x", padx=5, pady=5)
        
        self.api_keys_label = ttk.Label(api_frame, text="API 키 목록 (Gemini Developer, 한 줄에 하나씩):")
        self.api_keys_label.grid(row=0, column=0, padx=5, pady=5, sticky="nw")
        
        self.api_keys_text = scrolledtext.ScrolledText(api_frame, width=58, height=3, wrap=tk.WORD)
        self.api_keys_text.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        
        # Vertex AI 설정
        self.use_vertex_ai_var = tk.BooleanVar()
        self.use_vertex_ai_check = ttk.Checkbutton(api_frame, text="Vertex AI 사용", 
                                                   variable=self.use_vertex_ai_var, 
                                                   command=self._toggle_vertex_fields)
        self.use_vertex_ai_check.grid(row=1, column=0, columnspan=3, padx=5, pady=2, sticky="w")

        self.service_account_file_label = ttk.Label(api_frame, text="서비스 계정 JSON 파일 (Vertex AI):")
        self.service_account_file_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.service_account_file_entry = ttk.Entry(api_frame, width=50)
        self.service_account_file_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        self.browse_sa_file_button = ttk.Button(api_frame, text="찾아보기", command=self._browse_service_account_file)
        self.browse_sa_file_button.grid(row=2, column=2, padx=5, pady=5)

        self.gcp_project_label = ttk.Label(api_frame, text="GCP 프로젝트 ID (Vertex AI):")
        self.gcp_project_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.gcp_project_entry = ttk.Entry(api_frame, width=30)
        self.gcp_project_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")

        self.gcp_location_label = ttk.Label(api_frame, text="GCP 위치 (Vertex AI):")
        self.gcp_location_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.gcp_location_entry = ttk.Entry(api_frame, width=30)
        self.gcp_location_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")

        ttk.Label(api_frame, text="모델 이름:").grid(row=5, column=0, padx=5, pady=5, sticky="w")
        self.model_name_combobox = ttk.Combobox(api_frame, width=57) 
        self.model_name_combobox.grid(row=5, column=1, padx=5, pady=5, sticky="ew")
        self.refresh_models_button = ttk.Button(api_frame, text="새로고침", command=self._update_model_list_ui)
        self.refresh_models_button.grid(row=5, column=2, padx=5, pady=5)

        # 생성 파라미터
        gen_param_frame = ttk.LabelFrame(settings_frame, text="생성 파라미터", padding="10")
        gen_param_frame.pack(fill="x", padx=5, pady=5)
        
        # Temperature 설정
        ttk.Label(gen_param_frame, text="Temperature:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.temperature_scale = ttk.Scale(gen_param_frame, from_=0.0, to=2.0, orient="horizontal", length=200,
                                         command=lambda v: self.temperature_label.config(text=f"{float(v):.2f}"))
        self.temperature_scale.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.temperature_label = ttk.Label(gen_param_frame, text="0.00")
        self.temperature_label.grid(row=0, column=2, padx=5, pady=5)
        
        # Top P 설정
        ttk.Label(gen_param_frame, text="Top P:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.top_p_scale = ttk.Scale(gen_param_frame, from_=0.0, to=1.0, orient="horizontal", length=200,
                                   command=lambda v: self.top_p_label.config(text=f"{float(v):.2f}"))
        self.top_p_scale.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self.top_p_label = ttk.Label(gen_param_frame, text="0.00")
        self.top_p_label.grid(row=1, column=2, padx=5, pady=5)
        
        # 파일 및 처리 설정
        file_chunk_frame = ttk.LabelFrame(settings_frame, text="파일 및 처리 설정", padding="10")
        file_chunk_frame.pack(fill="x", padx=5, pady=5)
        
        # 입력 파일
        ttk.Label(file_chunk_frame, text="입력 파일:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.input_file_entry = ttk.Entry(file_chunk_frame, width=50)
        self.input_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.browse_input_button = ttk.Button(file_chunk_frame, text="찾아보기", command=self._browse_input_file)
        self.browse_input_button.grid(row=0, column=2, padx=5, pady=5)
        
        # 출력 파일
        ttk.Label(file_chunk_frame, text="출력 파일:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.output_file_entry = ttk.Entry(file_chunk_frame, width=50)
        self.output_file_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self.browse_output_button = ttk.Button(file_chunk_frame, text="찾아보기", command=self._browse_output_file)
        self.browse_output_button.grid(row=1, column=2, padx=5, pady=5)
        
        # 청크 크기 및 작업자 수
        chunk_worker_frame = ttk.Frame(file_chunk_frame)
        chunk_worker_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=5)
        
        ttk.Label(chunk_worker_frame, text="청크 크기:").pack(side="left", padx=(0,5))
        self.chunk_size_entry = ttk.Entry(chunk_worker_frame, width=10)
        self.chunk_size_entry.pack(side="left", padx=(0,15))
        
        ttk.Label(chunk_worker_frame, text="최대 작업자 수:").pack(side="left", padx=(10,5))
        self.max_workers_entry = ttk.Entry(chunk_worker_frame, width=5)
        self.max_workers_entry.pack(side="left")
        self.max_workers_entry.insert(0, str(os.cpu_count() or 1))
        
        # 번역 프롬프트
        prompt_frame = ttk.LabelFrame(settings_frame, text="번역 프롬프트", padding="10")
        prompt_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, wrap=tk.WORD, height=8, width=70)
        self.prompt_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 콘텐츠 안전 재시도 설정
        content_safety_frame = ttk.LabelFrame(settings_frame, text="콘텐츠 안전 재시도 설정", padding="10")
        content_safety_frame.pack(fill="x", padx=5, pady=5)
        
        self.use_content_safety_retry_var = tk.BooleanVar()
        self.use_content_safety_retry_check = ttk.Checkbutton(
            content_safety_frame,
            text="검열 오류시 청크 분할 재시도 사용",
            variable=self.use_content_safety_retry_var
        )
        self.use_content_safety_retry_check.grid(row=0, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        
        ttk.Label(content_safety_frame, text="최대 분할 시도:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.max_split_attempts_entry = ttk.Entry(content_safety_frame, width=5)
        self.max_split_attempts_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.max_split_attempts_entry.insert(0, "3")
        
        ttk.Label(content_safety_frame, text="최소 청크 크기:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.min_chunk_size_entry = ttk.Entry(content_safety_frame, width=10)
        self.min_chunk_size_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        self.min_chunk_size_entry.insert(0, "100")
        
        # 액션 버튼들
        action_frame = ttk.Frame(settings_frame, padding="10")
        action_frame.pack(fill="x", padx=5, pady=5)
        
        self.save_settings_button = ttk.Button(action_frame, text="설정 저장", command=self._save_settings)
        self.save_settings_button.pack(side="left", padx=5)
        
        self.load_settings_button = ttk.Button(action_frame, text="설정 불러오기", command=self._load_settings_ui)
        self.load_settings_button.pack(side="left", padx=5)
        
        self.start_button = ttk.Button(action_frame, text="번역 시작", command=self._start_translation_thread_with_resume_check)
        self.start_button.pack(side="right", padx=5)
        
        self.stop_button = ttk.Button(action_frame, text="중지", command=self._request_stop_translation, state=tk.DISABLED)
        self.stop_button.pack(side="right", padx=5)
        
        # 진행률 표시
        progress_frame = ttk.Frame(settings_frame)
        progress_frame.pack(fill="x", padx=15, pady=10)
        
        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.progress_bar.pack(fill="x", pady=5)
        
        self.progress_label = ttk.Label(progress_frame, text="대기 중...")
        self.progress_label.pack(pady=2)

    def _browse_service_account_file(self):
        filepath = filedialog.askopenfilename(
            title="서비스 계정 JSON 파일 선택",
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*"))
        )
        if filepath:
            self.service_account_file_entry.delete(0, tk.END)
            self.service_account_file_entry.insert(0, filepath)

    def _toggle_vertex_fields(self):
        use_vertex = self.use_vertex_ai_var.get()
        logger.debug(f"_toggle_vertex_fields 호출됨. use_vertex_ai_var: {use_vertex}")
        api_related_state = tk.DISABLED if use_vertex else tk.NORMAL
        vertex_related_state = tk.NORMAL if use_vertex else tk.DISABLED

        if hasattr(self, 'api_keys_label'): self.api_keys_label.config(state=api_related_state)
        if hasattr(self, 'api_keys_text'): self.api_keys_text.config(state=api_related_state)
        
        if hasattr(self, 'service_account_file_label'): self.service_account_file_label.config(state=vertex_related_state)
        if hasattr(self, 'service_account_file_entry'): self.service_account_file_entry.config(state=vertex_related_state)
        if hasattr(self, 'browse_sa_file_button'): self.browse_sa_file_button.config(state=vertex_related_state)
        if hasattr(self, 'gcp_project_label'): self.gcp_project_label.config(state=vertex_related_state)
        if hasattr(self, 'gcp_project_entry'): self.gcp_project_entry.config(state=vertex_related_state)
        if hasattr(self, 'gcp_location_label'): self.gcp_location_label.config(state=vertex_related_state)
        if hasattr(self, 'gcp_location_entry'): self.gcp_location_entry.config(state=vertex_related_state)
        logger.debug(f"Vertex 필드 상태: {vertex_related_state}, API 키 필드 상태: {api_related_state}")


    def _create_pronouns_widgets(self):
        # 스크롤 가능한 프레임의 내부 프레임 사용
        pronouns_frame = self.pronouns_scroll.scrollable_frame
        
        # 고유명사 CSV 파일 설정
        path_frame = ttk.LabelFrame(pronouns_frame, text="고유명사 CSV 파일", padding="10")
        path_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(path_frame, text="CSV 파일 경로:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.pronoun_csv_path_entry = ttk.Entry(path_frame, width=50)
        self.pronoun_csv_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.browse_pronoun_csv_button = ttk.Button(path_frame, text="찾아보기", command=self._browse_pronoun_csv)
        self.browse_pronoun_csv_button.grid(row=0, column=2, padx=5, pady=5)
        
        extract_button = ttk.Button(path_frame, text="선택한 입력 파일에서 고유명사 추출", command=self._extract_pronouns_thread)
        extract_button.grid(row=1, column=0, columnspan=3, padx=5, pady=10)
        
        self.pronoun_progress_label = ttk.Label(path_frame, text="고유명사 추출 대기 중...")
        self.pronoun_progress_label.grid(row=2, column=0, columnspan=3, padx=5, pady=2)

    def _create_log_widgets(self):
        self.log_text = scrolledtext.ScrolledText(self.log_tab, wrap=tk.WORD, state=tk.DISABLED, height=20)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        gui_log_handler = TextHandler(self.log_text)
        logging.getLogger(__name__ + "_gui").addHandler(gui_log_handler)
        logging.getLogger(__name__ + "_gui").setLevel(logging.INFO) 

        self.tqdm_stream = TqdmToTkinter(self.log_text)

    def _log_message(self, message: str, level: str = "INFO", exc_info=False):
        gui_specific_logger = logging.getLogger(__name__ + "_gui")
        if level.upper() == "INFO": gui_specific_logger.info(message, exc_info=exc_info)
        elif level.upper() == "WARNING": gui_specific_logger.warning(message, exc_info=exc_info)
        elif level.upper() == "ERROR": gui_specific_logger.error(message, exc_info=exc_info)
        elif level.upper() == "DEBUG": gui_specific_logger.debug(message, exc_info=exc_info)
        else: gui_specific_logger.info(message, exc_info=exc_info)

    def _update_model_list_ui(self):
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            self._log_message("모델 목록 업데이트 시도 실패: AppService 없음", "ERROR")
            return
        try:
            self._log_message("모델 목록 새로고침 중...")
            current_user_input_model = self.model_name_combobox.get() 

            if not self.app_service.gemini_client:
                self.model_name_combobox['values'] = []
                self.model_name_combobox.set(current_user_input_model) 
                logger.warning("모델 목록 조회 실패: Gemini 클라이언트가 AppService에 초기화되지 않음.")
                self._log_message("모델 목록 조회 실패: Gemini 클라이언트가 초기화되지 않았습니다. API 키 또는 Vertex AI 설정을 확인하세요.", "WARNING")
                return

            models_data = self.app_service.get_available_models() 
            
            model_display_names_for_ui = []
            for m in models_data:
                display_name = m.get("display_name")
                short_name = m.get("short_name") # gemini_client.py에서 추가된 short_name 사용
                full_name = m.get("name")
                
                # 우선순위 변경: short_name > display_name > full_name
                chosen_name_for_display = short_name if short_name else (display_name if display_name else full_name)
                
                if chosen_name_for_display and isinstance(chosen_name_for_display, str) and chosen_name_for_display.strip():
                    model_display_names_for_ui.append(chosen_name_for_display.strip())
            
            model_display_names_for_ui = sorted(list(set(model_display_names_for_ui)))

            self.model_name_combobox['values'] = model_display_names_for_ui
            
            # 현재 설정된 모델 이름(config) 또는 사용자가 입력한 모델 이름 유지 시도
            config_model_name = self.app_service.config.get("model_name", "")
            # config_model_name이 전체 경로일 수 있으므로, UI 목록과 비교 시 짧은 이름 형태로 변환하여 비교
            config_model_short_name = config_model_name.split('/')[-1] if '/' in config_model_name else config_model_name

            if current_user_input_model and current_user_input_model.strip() in model_display_names_for_ui:
                self.model_name_combobox.set(current_user_input_model)
            elif config_model_short_name and config_model_short_name in model_display_names_for_ui: # config의 short_name과 비교
                self.model_name_combobox.set(config_model_short_name)
            elif config_model_name and config_model_name in model_display_names_for_ui: # config의 full_name과 비교
                self.model_name_combobox.set(config_model_name)
            elif model_display_names_for_ui: # 위 조건 모두 만족 못하면 목록의 첫 번째 모델 선택
                self.model_name_combobox.set(model_display_names_for_ui[0])
            else: # 사용 가능한 모델이 없으면 빈 문자열로 설정
                self.model_name_combobox.set("") 
            
            self._log_message(f"{len(model_display_names_for_ui)}개 모델 로드 완료.")

        except BtgApiClientException as e:
            messagebox.showerror("API 오류", f"모델 목록 조회 실패: {e}")
            self._log_message(f"모델 목록 조회 API 오류: {e}", "ERROR")
            self.model_name_combobox['values'] = []
            self.model_name_combobox.set(current_user_input_model if 'current_user_input_model' in locals() else "")
        except BtgServiceException as e: 
            messagebox.showerror("서비스 오류", f"모델 목록 조회 실패: {e}")
            self._log_message(f"모델 목록 조회 서비스 오류: {e}", "ERROR")
            self.model_name_combobox['values'] = []
            self.model_name_combobox.set(current_user_input_model if 'current_user_input_model' in locals() else "")
        except Exception as e:
            messagebox.showerror("오류", f"모델 목록 조회 중 예상치 못한 오류: {e}")
            self._log_message(f"모델 목록 조회 중 오류: {e}", "ERROR", exc_info=True)
            self.model_name_combobox['values'] = []
            self.model_name_combobox.set(current_user_input_model if 'current_user_input_model' in locals() else "")


    def _browse_input_file(self):
        filepath = filedialog.askopenfilename(title="입력 파일 선택", filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*")))
        if filepath:
            self.input_file_entry.delete(0, tk.END)
            self.input_file_entry.insert(0, filepath)
            p = Path(filepath)
            suggested_output = p.parent / f"{p.stem}_translated{p.suffix}"
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, str(suggested_output))
            suggested_pronoun_csv = p.parent / f"{p.stem}_seed.csv" 
            self.pronoun_csv_path_entry.delete(0, tk.END)
            self.pronoun_csv_path_entry.insert(0, str(suggested_pronoun_csv))

    def _browse_output_file(self):
        filepath = filedialog.asksaveasfilename(title="출력 파일 선택", defaultextension=".txt", filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*")))
        if filepath:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, filepath)

    def _browse_pronoun_csv(self):
        initial_dir = ""
        input_file_path = self.input_file_entry.get()
        if input_file_path and Path(input_file_path).exists():
            initial_dir = str(Path(input_file_path).parent)
        
        filepath = filedialog.askopenfilename(
            title="고유명사 CSV 파일 선택", 
            filetypes=(("CSV 파일", "*.csv"), ("모든 파일", "*.*")),
            initialdir=initial_dir
            )
        if filepath:
            self.pronoun_csv_path_entry.delete(0, tk.END)
            self.pronoun_csv_path_entry.insert(0, filepath)

    def _get_config_from_ui(self) -> Dict[str, Any]:
        prompt_content = self.prompt_text.get("1.0", tk.END).strip()
        use_vertex = self.use_vertex_ai_var.get()

        api_keys_str = self.api_keys_text.get("1.0", tk.END).strip()
        api_keys_list = [key.strip() for key in api_keys_str.splitlines() if key.strip()]
       
        try:
            max_workers_val = int(self.max_workers_entry.get())
            if max_workers_val <= 0:
                max_workers_val = os.cpu_count() or 1 
                messagebox.showwarning("입력 오류", f"최대 작업자 수는 1 이상이어야 합니다. 기본값 ({max_workers_val})으로 설정됩니다.")
                self.max_workers_entry.delete(0, tk.END)
                self.max_workers_entry.insert(0, str(max_workers_val))
        except ValueError:
            max_workers_val = os.cpu_count() or 1 
            messagebox.showwarning("입력 오류", f"최대 작업자 수는 숫자여야 합니다. 기본값 ({max_workers_val})으로 설정됩니다.")
            self.max_workers_entry.delete(0, tk.END)
            self.max_workers_entry.insert(0, str(max_workers_val))

        config_data = {
            "api_keys": api_keys_list if not use_vertex else [],
            "service_account_file_path": self.service_account_file_entry.get().strip() if use_vertex else None,
            "use_vertex_ai": use_vertex,
            "gcp_project": self.gcp_project_entry.get().strip() if use_vertex else None,
            "gcp_location": self.gcp_location_entry.get().strip() if use_vertex else None,
            "model_name": self.model_name_combobox.get().strip(), 
            "temperature": self.temperature_scale.get(),
            "top_p": self.top_p_scale.get(),
            "chunk_size": int(self.chunk_size_entry.get() or "6000"), 
            "max_workers": max_workers_val, 
            "prompts": prompt_content,
            "pronouns_csv": self.pronoun_csv_path_entry.get().strip() or None, 
        }
        if self.app_service and self.app_service.config:
            for key in ["max_pronoun_entries", "pronoun_sample_ratio"]: 
                if key not in config_data and key in self.app_service.config: 
                    config_data[key] = self.app_service.config[key]
        return config_data

    def _save_settings(self):
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        try:
            current_config = self._get_config_from_ui()
            self.app_service.save_app_config(current_config) 
            messagebox.showinfo("성공", "설정이 성공적으로 저장되었습니다.")
            self._log_message("설정 저장됨.")
            self._load_initial_config_to_ui() 
        except ValueError as ve: 
            messagebox.showerror("입력 오류", f"설정값 오류: {ve}")
            self._log_message(f"설정값 입력 오류: {ve}", "ERROR")
        except BtgConfigException as e:
            messagebox.showerror("설정 저장 오류", f"설정 저장 중 오류: {e}")
            self._log_message(f"설정 저장 오류: {e}", "ERROR")
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 중 예상치 못한 오류: {e}")
            self._log_message(f"설정 저장 중 예상치 못한 오류: {e}", "ERROR", exc_info=True)

    def _load_settings_ui(self):
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        try:
            self.app_service.load_app_config() 
            self._load_initial_config_to_ui() 
            messagebox.showinfo("성공", "설정을 성공적으로 불러왔습니다.")
            self._log_message("설정 불러옴.")
        except BtgConfigException as e:
            messagebox.showerror("설정 불러오기 오류", f"설정 불러오기 중 오류: {e}")
            self._log_message(f"설정 불러오기 오류: {e}", "ERROR")
        except Exception as e:
            messagebox.showerror("오류", f"설정 불러오기 중 예상치 못한 오류: {e}")
            self._log_message(f"설정 불러오기 중 오류: {e}", "ERROR", exc_info=True)

    def _update_translation_progress(self, dto: TranslationJobProgressDTO):
        def _update():
            if not self.master.winfo_exists(): return 
            self.progress_bar['value'] = (dto.processed_chunks / dto.total_chunks) * 100 if dto.total_chunks > 0 else 0
            status_text = f"{dto.current_status_message} ({dto.processed_chunks}/{dto.total_chunks})"
            if dto.failed_chunks > 0:
                status_text += f" - 실패: {dto.failed_chunks}"
            if dto.last_error_message:
                status_text += f" (마지막 오류: {dto.last_error_message[:30]}...)"
            self.progress_label.config(text=status_text)
        if self.master.winfo_exists():
            self.master.after(0, _update)

    def _update_translation_status(self, message: str):
        def _update():
            if not self.master.winfo_exists(): return
            self.progress_label.config(text=message)
            self._log_message(f"번역 상태: {message}")
            if "번역 시작됨" in message or "번역 중..." in message or "처리 중" in message or "준비 중" in message :
                self.start_button.config(state=tk.DISABLED)
                self.stop_button.config(state=tk.NORMAL)
            elif "완료" in message or "오류" in message or "중단" in message:
                self.start_button.config(state=tk.NORMAL)
                self.stop_button.config(state=tk.DISABLED)
        if self.master.winfo_exists():
            self.master.after(0, _update)

    def _start_translation_thread_with_resume_check(self):
        if not self.app_service:
            messagebox.showerror("오류", "애플리케이션 서비스가 초기화되지 않았습니다.")
            return

        input_file = self.input_file_entry.get()
        output_file = self.output_file_entry.get()

        if not input_file or not output_file:
            messagebox.showwarning("경고", "입력 파일과 출력 파일을 모두 선택해주세요.")
            return

        input_file_path_obj = Path(input_file)
        output_file_path_obj = Path(output_file)

        if not input_file_path_obj.exists():
            messagebox.showerror("오류", f"입력 파일을 찾을 수 없습니다: {input_file}")
            return

        temp_current_config_from_ui = self._get_config_from_ui()
        config_for_hash_check = self.app_service.config.copy() 
        config_for_hash_check.update(temp_current_config_from_ui)

        metadata_file_path = get_metadata_file_path(input_file_path_obj)
        loaded_metadata = load_metadata(metadata_file_path) 
        current_config_hash = _hash_config_for_metadata(config_for_hash_check)
        previous_config_hash = loaded_metadata.get("config_hash")

        start_new_translation_flag = False 

        if previous_config_hash and loaded_metadata.get("status") not in ["completed", "completed_with_errors", "error", None, ""]: 
            if previous_config_hash == current_config_hash:
                user_choice = messagebox.askyesnocancel(
                    "이어하기 확인",
                    "이전 번역 작업 내역이 있습니다. 이어하시겠습니까?\n\n"
                    "(예: 이어하기 / 아니오: 새로 번역 / 취소: 작업 취소)"
                )
                if user_choice is None: 
                    return
                elif not user_choice: 
                    logger.info("사용자 선택: 새로 번역을 시작합니다.")
                    start_new_translation_flag = True
            else: 
                if messagebox.askyesno("설정 변경 알림", "설정이 이전 작업과 다릅니다. 새로 번역을 시작하시겠습니까?\n\n(아니오 선택 시 현재 설정으로 이어하기 시도)"):
                    logger.info("설정 변경으로 인해 새로 번역을 시작합니다.")
                    start_new_translation_flag = True
        else: 
            if previous_config_hash : 
                 logger.info(f"이전 작업 상태({loaded_metadata.get('status')})로 인해 새로 번역을 시작합니다.")
            start_new_translation_flag = True

        if start_new_translation_flag:
            logger.info(f"새 번역을 위해 기존 메타데이터 파일 '{metadata_file_path}' 및 출력 파일 '{output_file_path_obj}'을 삭제합니다 (필요시).")
            delete_file(metadata_file_path)

        self._start_translation_thread(start_new_translation=start_new_translation_flag)

    def _start_translation_thread(self, start_new_translation: bool = False): 
        if not self.app_service: return 

        input_file = self.input_file_entry.get()
        output_file = self.output_file_entry.get()
        
        try:
            current_ui_config = self._get_config_from_ui()
            self.app_service.config.update(current_ui_config) 
            self.app_service.load_app_config() 


            if not self.app_service.gemini_client:
                 if not messagebox.askyesno("API 설정 경고", "API 클라이언트가 초기화되지 않았습니다. (인증 정보 확인 필요)\n계속 진행하시겠습니까?"):
                    self.start_button.config(state=tk.NORMAL) 
                    return
        except ValueError as ve: 
             messagebox.showerror("입력 오류", f"설정값 오류: {ve}")
             self.start_button.config(state=tk.NORMAL)
             return
        except Exception as e:
            messagebox.showerror("오류", f"번역 시작 전 설정 오류: {e}")
            self._log_message(f"번역 시작 전 설정 오류: {e}", "ERROR", exc_info=True)
            self.start_button.config(state=tk.NORMAL)
            return

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.progress_bar['value'] = 0
        self.progress_label.config(text="번역 준비 중...")

        thread = threading.Thread(
            target=self.app_service.start_translation, 
            args=(input_file, output_file,
                  self._update_translation_progress,
                  self._update_translation_status,
                  self.tqdm_stream),
            daemon=True
        )
        thread.start()

    def _request_stop_translation(self):
        if not self.app_service: return
        if self.app_service.is_translation_running:
            self.app_service.request_stop_translation()
            self._log_message("번역 중지 요청됨.")
        else:
            self._log_message("실행 중인 번역 작업이 없습니다.")

    def _update_pronoun_extraction_progress(self, dto: PronounExtractionProgressDTO):
        def _update():
            if not self.master.winfo_exists(): return
            msg = f"{dto.current_status_message} ({dto.processed_sample_chunks}/{dto.total_sample_chunks})"
            self.pronoun_progress_label.config(text=msg)
        if self.master.winfo_exists():
            self.master.after(0, _update)

    def _extract_pronouns_thread(self):
        if not self.app_service:
            messagebox.showerror("오류", "애플리케이션 서비스가 초기화되지 않았습니다.")
            return

        input_file = self.input_file_entry.get()
        if not input_file:
            messagebox.showwarning("경고", "고유명사를 추출할 입력 파일을 먼저 선택해주세요.")
            return
        if not Path(input_file).exists():
            messagebox.showerror("오류", f"입력 파일을 찾을 수 없습니다: {input_file}")
            return

        try:
            current_ui_config = self._get_config_from_ui()
            self.app_service.config.update(current_ui_config)
            self.app_service.load_app_config() 

            if not self.app_service.gemini_client: 
                 if not messagebox.askyesno("API 설정 경고", "API 클라이언트가 초기화되지 않았습니다. 계속 진행하시겠습니까?"):
                    return
        except ValueError as ve:
             messagebox.showerror("입력 오류", f"설정값 오류: {ve}")
             return
        except Exception as e:
            messagebox.showerror("오류", f"고유명사 추출 시작 전 설정 오류: {e}")
            self._log_message(f"고유명사 추출 시작 전 설정 오류: {e}", "ERROR", exc_info=True)
            return

        self.pronoun_progress_label.config(text="고유명사 추출 시작 중...")
        self._log_message(f"고유명사 추출 시작: {input_file}")

        def _extraction_task_wrapper():
            try:
                if self.app_service: 
                    result_csv_path = self.app_service.extract_pronouns(
                        input_file,
                        self._update_pronoun_extraction_progress
                    )
                    self.master.after(0, lambda: messagebox.showinfo("성공", f"고유명사 추출 완료!\n결과 파일: {result_csv_path}"))
                    self.master.after(0, lambda: self.pronoun_progress_label.config(text=f"추출 완료: {result_csv_path.name}"))
                    self.master.after(0, lambda: self._update_pronoun_csv_path_entry(str(result_csv_path))) 
            except (BtgFileHandlerException, BtgApiClientException, BtgServiceException, BtgPronounException) as e_btg:
                logger.error(f"고유명사 추출 중 BTG 예외 발생: {e_btg}", exc_info=True)
                self.master.after(0, lambda: messagebox.showerror("추출 오류", f"고유명사 추출 중 오류: {e_btg}"))
                self.master.after(0, lambda: self.pronoun_progress_label.config(text="오류 발생"))
            except Exception as e_unknown: 
                logger.error(f"고유명사 추출 중 알 수 없는 예외 발생: {e_unknown}", exc_info=True)
                self.master.after(0, lambda: messagebox.showerror("알 수 없는 오류", f"고유명사 추출 중 예상치 못한 오류: {e_unknown}"))
                self.master.after(0, lambda: self.pronoun_progress_label.config(text="알 수 없는 오류 발생"))
            finally:
                self._log_message("고유명사 추출 스레드 종료.")

        thread = threading.Thread(target=_extraction_task_wrapper, daemon=True)
        thread.start()

    def _update_pronoun_csv_path_entry(self, path_str: str):
        self.pronoun_csv_path_entry.delete(0, tk.END)
        self.pronoun_csv_path_entry.insert(0, path_str)
        if self.app_service:
            self.app_service.config["pronouns_csv"] = path_str

    def _on_closing(self):
        if self.app_service and self.app_service.is_translation_running:
            if messagebox.askokcancel("종료 확인", "번역 작업이 진행 중입니다. 정말로 종료하시겠습니까?"):
                self.app_service.request_stop_translation()
                logger.info("사용자 종료 요청으로 번역 중단 시도.")
                self.master.destroy()
        else:
            self.master.destroy()

    

class TextHandler(logging.Handler):
    def __init__(self, text_widget: scrolledtext.ScrolledText):
        super().__init__()
        self.text_widget = text_widget
        self.text_widget.tag_config("INFO", foreground="black")
        self.text_widget.tag_config("DEBUG", foreground="gray")
        self.text_widget.tag_config("WARNING", foreground="orange")
        self.text_widget.tag_config("ERROR", foreground="red", font=('Helvetica', 9, 'bold'))
        self.text_widget.tag_config("CRITICAL", foreground="red", background="yellow", font=('Helvetica', 9, 'bold'))
        self.text_widget.tag_config("TQDM", foreground="blue") 

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        level_tag = record.levelname
        
        def append_message_to_widget():
            if not self.text_widget.winfo_exists(): 
                return
            
            current_state = self.text_widget.cget("state") 
            self.text_widget.config(state=tk.NORMAL) 
            self.text_widget.insert(tk.END, msg + "\n", level_tag)
            self.text_widget.config(state=current_state) 
            self.text_widget.see(tk.END) 

        if self.text_widget.winfo_exists():
             self.text_widget.after(0, append_message_to_widget)

if __name__ == '__main__':
    logger.info("BatchTranslatorGUI 시작 중...")

    root = tk.Tk()
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
