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
    from dtos import TranslationJobProgressDTO, LorebookExtractionProgressDTO, ModelInfoDTO # Changed PronounExtractionProgressDTO
    from exceptions import BtgConfigException, BtgServiceException, BtgFileHandlerException, BtgApiClientException, BtgBusinessLogicException, BtgException # BtgPronounException removed, BtgBusinessLogicException added
    from logger_config import setup_logger
    from file_handler import get_metadata_file_path, load_metadata, _hash_config_for_metadata, delete_file # PRONOUN_CSV_HEADER removed
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
    class LorebookExtractionProgressDTO: # Changed from PronounExtractionProgressDTO
        total_segments: int = 0; processed_segments: int = 0; current_status_message: str = "대기 중"; extracted_entries_count: int = 0
    @dataclass
    class ModelInfoDTO: name: str; display_name: str; description: Optional[str] = None; version: Optional[str] = None
    class BtgBaseException(Exception): pass
    class BtgConfigException(BtgBaseException): pass
    class BtgServiceException(BtgBaseException): pass
    class BtgFileHandlerException(BtgBaseException): pass
    class BtgApiClientException(BtgBaseException): pass
    class BtgBusinessLogicException(BtgBaseException): pass # Added for fallback
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
            self.lorebook_service = self # Renamed from pronoun_service
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
                "lorebook_json_path": "mock_lorebook.json", # Changed from pronouns_csv
                "max_workers": os.cpu_count() or 1, 
                "requests_per_minute": 60,
                "auth_credentials": "",
                # Mock Lorebook settings
                "lorebook_sampling_method": "uniform",
                "lorebook_sampling_ratio": 25.0,
                "lorebook_max_entries_per_segment": 5,
                "lorebook_max_chars_per_entry": 200,
                "lorebook_keyword_sensitivity": "medium",
                "lorebook_priority_settings": {"character": 5, "worldview": 5, "story_element": 5},
                "lorebook_chunk_size": 8000,
                "lorebook_extraction_temperature": 0.2, # For lorebook extraction
                "novel_language": "auto", 
                "novel_language_fallback": "ja",
                "lorebook_output_json_filename_suffix": "_lorebook.json"
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
        def extract_lorebook(self, 
                             input_file_path: Union[str, Path], 
                             progress_callback: Optional[Callable[[LorebookExtractionProgressDTO], None]] = None, 
                             novel_language_code: Optional[str] = None, # Added
                             seed_lorebook_path: Optional[Union[str, Path]] = None, # Added
                             tqdm_file_stream=None # tqdm_file_stream is not directly used by AppService.extract_lorebook
                            ) -> Path:
            print(f"Mock AppService: extract_lorebook called for {input_file_path}, lang: {novel_language_code}, seed: {seed_lorebook_path}")
            total_segments_mock = 5
            iterable_segments = range(total_segments_mock) # tqdm handled by CLI or GUI directly if needed
            for i in iterable_segments:
                if hasattr(self, 'stop_requested') and self.stop_requested: break
                time.sleep(0.05)
                if progress_callback:
                    msg = f"표본 세그먼트 {i+1}/{total_segments_mock} 처리 중" if i < total_segments_mock -1 else "로어북 추출 완료"
                    progress_callback(LorebookExtractionProgressDTO(total_segments=total_segments_mock, processed_segments=i+1, current_status_message=msg, extracted_entries_count=i*2))
            output_suffix = self.config.get("lorebook_output_json_filename_suffix", "_lorebook.json")
            return Path(input_file_path).with_name(f"{Path(input_file_path).stem}{output_suffix}")

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
        self.lorebook_scroll = ScrollableFrame(self.notebook) # Renamed from pronouns_scroll
        self.log_tab = ttk.Frame(self.notebook, padding="10")  # 로그 탭은 기존 유지
        
        # 탭 추가
        self.notebook.add(self.settings_scroll.main_frame, text='설정 및 번역')
        self.notebook.add(self.lorebook_scroll.main_frame, text='로어북 관리') # Tab text changed
        self.notebook.add(self.log_tab, text='실행 로그')
        self.notebook.pack(expand=True, fill='both')
        
        # 위젯 생성 (스크롤 가능한 프레임 사용)
        self._create_settings_widgets()
        self._create_lorebook_widgets() # Renamed from _create_pronouns_widgets
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

            rpm_val = config.get("requests_per_minute", 60)
            logger.debug(f"Config에서 가져온 requests_per_minute: {rpm_val}")
            self.rpm_entry.delete(0, tk.END)
            self.rpm_entry.insert(0, str(rpm_val))

            # Language settings
            novel_lang_val = config.get("novel_language", "auto")
            self.novel_language_entry.delete(0, tk.END)
            self.novel_language_entry.insert(0, novel_lang_val)
            logger.debug(f"Config에서 가져온 novel_language: {novel_lang_val}")

            novel_lang_fallback_val = config.get("novel_language_fallback", "ja")
            self.novel_language_fallback_entry.delete(0, tk.END)
            self.novel_language_fallback_entry.insert(0, novel_lang_fallback_val)
            logger.debug(f"Config에서 가져온 novel_language_fallback: {novel_lang_fallback_val}")


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
            
            # Lorebook specific settings
            lorebook_json_path_val = config.get("lorebook_json_path") # Removed fallback to pronouns_csv
            logger.debug(f"Config에서 가져온 lorebook_json_path: {lorebook_json_path_val}")
            self.lorebook_json_path_entry.delete(0, tk.END)
            self.lorebook_json_path_entry.insert(0, lorebook_json_path_val if lorebook_json_path_val is not None else "")

            sample_ratio = config.get("lorebook_sampling_ratio", 25.0)
            self.sample_ratio_scale.set(sample_ratio)
            self.sample_ratio_label.config(text=f"{sample_ratio:.1f}%")
            
            max_entries_segment = config.get("lorebook_max_entries_per_segment", 5)
            self.max_entries_per_segment_spinbox.set(str(max_entries_segment))

            self.lorebook_sampling_method_combobox.set(config.get("lorebook_sampling_method", "uniform"))
            self.lorebook_max_chars_entry.delete(0, tk.END)
            self.lorebook_max_chars_entry.insert(0, str(config.get("lorebook_max_chars_per_entry", 200)))
            self.lorebook_keyword_sensitivity_combobox.set(config.get("lorebook_keyword_sensitivity", "medium"))
            # For priority_settings, ai_prompt_template, conflict_resolution_prompt_template - ScrolledText
            self.lorebook_priority_text.delete('1.0', tk.END)
            self.lorebook_priority_text.insert('1.0', json.dumps(config.get("lorebook_priority_settings", {"character": 5, "worldview": 5, "story_element": 5}), indent=2))
            self.lorebook_chunk_size_entry.delete(0, tk.END)
            self.lorebook_chunk_size_entry.insert(0, str(config.get("lorebook_chunk_size", 8000)))
            
            # Dynamic Lorebook Injection Settings
            self.enable_dynamic_lorebook_injection_var.set(config.get("enable_dynamic_lorebook_injection", False))
            self.max_lorebook_entries_injection_entry.delete(0, tk.END)
            self.max_lorebook_entries_injection_entry.insert(0, str(config.get("max_lorebook_entries_per_chunk_injection", 3)))
            self.max_lorebook_chars_injection_entry.delete(0, tk.END)
            self.max_lorebook_chars_injection_entry.insert(0, str(config.get("max_lorebook_chars_per_chunk_injection", 500)))
            lorebook_injection_path_val = config.get("lorebook_json_path_for_injection")
            self.lorebook_json_path_for_injection_entry.delete(0, tk.END)
            self.lorebook_json_path_for_injection_entry.insert(0, lorebook_injection_path_val if lorebook_injection_path_val is not None else "")

            extraction_temp = config.get("lorebook_extraction_temperature", 0.2)
            self.extraction_temp_scale.set(extraction_temp)
            self.extraction_temp_label.config(text=f"{extraction_temp:.2f}")

            # Content Safety Retry Settings
            use_content_safety_retry_val = config.get("use_content_safety_retry", True)
            self.use_content_safety_retry_var.set(use_content_safety_retry_val)

            max_split_attempts_val = config.get("max_content_safety_split_attempts", 3)
            self.max_split_attempts_entry.delete(0, tk.END)
            self.max_split_attempts_entry.insert(0, str(max_split_attempts_val))

            min_chunk_size_val = config.get("min_content_safety_chunk_size", 100)
            self.min_chunk_size_entry.delete(0, tk.END)
            self.min_chunk_size_entry.insert(0, str(min_chunk_size_val))
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
        # API 키 텍스트가 변경될 때마다 이벤트 핸들러 연결
        self.api_keys_text.bind('<KeyRelease>', self._on_api_key_changed)

        
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
        
        # RPM 설정
        ttk.Label(chunk_worker_frame, text="분당 요청 수 (RPM):").pack(side="left", padx=(10,5))
        self.rpm_entry = ttk.Entry(chunk_worker_frame, width=5)
        self.rpm_entry.pack(side="left")

        # Language Settings Frame
        language_settings_frame = ttk.LabelFrame(settings_frame, text="언어 설정", padding="10")
        language_settings_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(language_settings_frame, text="소설/번역 출발 언어:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.novel_language_entry = ttk.Entry(language_settings_frame, width=10)
        self.novel_language_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        self.novel_language_entry.insert(0, "auto") 
        ttk.Label(language_settings_frame, text="(예: ko, ja, en, auto)").grid(row=0, column=2, padx=5, pady=5, sticky="w")

        ttk.Label(language_settings_frame, text="언어 자동감지 실패 시 폴백:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.novel_language_fallback_entry = ttk.Entry(language_settings_frame, width=10)
        self.novel_language_fallback_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.novel_language_fallback_entry.insert(0, "ja")
        ttk.Label(language_settings_frame, text="(예: ko, ja, en)").grid(row=1, column=2, padx=5, pady=5, sticky="w")
        
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

        # 동적 로어북 주입 설정
        dynamic_lorebook_frame = ttk.LabelFrame(settings_frame, text="동적 로어북 주입 설정", padding="10")
        dynamic_lorebook_frame.pack(fill="x", padx=5, pady=5)

        self.enable_dynamic_lorebook_injection_var = tk.BooleanVar()
        self.enable_dynamic_lorebook_injection_check = ttk.Checkbutton(
            dynamic_lorebook_frame,
            text="동적 로어북 주입 활성화",
            variable=self.enable_dynamic_lorebook_injection_var
        )
        self.enable_dynamic_lorebook_injection_check.grid(row=0, column=0, columnspan=3, padx=5, pady=2, sticky="w")

        ttk.Label(dynamic_lorebook_frame, text="청크당 최대 주입 항목 수:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.max_lorebook_entries_injection_entry = ttk.Entry(dynamic_lorebook_frame, width=5)
        self.max_lorebook_entries_injection_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(dynamic_lorebook_frame, text="청크당 최대 주입 문자 수:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.max_lorebook_chars_injection_entry = ttk.Entry(dynamic_lorebook_frame, width=10)
        self.max_lorebook_chars_injection_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(dynamic_lorebook_frame, text="주입용 로어북 JSON 경로:").grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.lorebook_json_path_for_injection_entry = ttk.Entry(dynamic_lorebook_frame, width=50)
        self.lorebook_json_path_for_injection_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        self.browse_lorebook_injection_button = ttk.Button(dynamic_lorebook_frame, text="찾아보기", command=self._browse_lorebook_json_for_injection)
        self.browse_lorebook_injection_button.grid(row=3, column=2, padx=5, pady=5)
        
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


    def _create_lorebook_widgets(self): # Renamed from _create_pronouns_widgets
        # 스크롤 가능한 프레임의 내부 프레임 사용
        lorebook_frame = self.lorebook_scroll.scrollable_frame # Renamed
        
        # 로어북 JSON 파일 설정
        path_frame = ttk.LabelFrame(lorebook_frame, text="로어북 JSON 파일", padding="10") # Text changed
        path_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(path_frame, text="JSON 파일 경로:").grid(row=0, column=0, padx=5, pady=5, sticky="w") # Text changed
        self.lorebook_json_path_entry = ttk.Entry(path_frame, width=50) # Renamed
        self.lorebook_json_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.browse_lorebook_json_button = ttk.Button(path_frame, text="찾아보기", command=self._browse_lorebook_json) # Renamed
        self.browse_lorebook_json_button.grid(row=0, column=2, padx=5, pady=5)
        

        extract_button = ttk.Button(path_frame, text="선택한 입력 파일에서 로어북 추출", command=self._extract_lorebook_thread) # Text and command changed
        extract_button.grid(row=2, column=0, columnspan=3, padx=5, pady=10)
        
        self.lorebook_progress_label = ttk.Label(path_frame, text="로어북 추출 대기 중...") # Renamed
        self.lorebook_progress_label.grid(row=3, column=0, columnspan=3, padx=5, pady=2)

        # 로어북 추출 설정 프레임
        extraction_settings_frame = ttk.LabelFrame(lorebook_frame, text="로어북 추출 설정", padding="10") # Text changed
        extraction_settings_frame.pack(fill="x", padx=5, pady=5)
        
        # 샘플링 비율 설정 (lorebook_sampling_ratio)
        ttk.Label(extraction_settings_frame, text="샘플링 비율 (%):").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        
        sample_ratio_frame = ttk.Frame(extraction_settings_frame)
        sample_ratio_frame.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        
        self.sample_ratio_scale = ttk.Scale(
            sample_ratio_frame, 
            from_=5.0, 
            to=100.0, 
            orient="horizontal", 
            length=200,
            command=self._update_sample_ratio_label
        )
        self.sample_ratio_scale.pack(side="left", padx=(0,10))
        
        self.sample_ratio_label = ttk.Label(sample_ratio_frame, text="25.0%", width=8)
        self.sample_ratio_label.pack(side="left")
        
        # 도움말 레이블
        ttk.Label(extraction_settings_frame, 
                text="전체 텍스트에서 로어북 추출에 사용할 세그먼트 비율", # Text changed
                font=("Arial", 8), 
                foreground="gray").grid(row=1, column=1, columnspan=2, padx=5, sticky="w")
        
        # 최대 항목 수 (세그먼트 당) 설정 (lorebook_max_entries_per_segment)
        ttk.Label(extraction_settings_frame, text="세그먼트 당 최대 항목 수:").grid(row=2, column=0, padx=5, pady=(15,5), sticky="w") # Text changed
        
        max_entries_segment_frame = ttk.Frame(extraction_settings_frame)
        max_entries_segment_frame.grid(row=2, column=1, columnspan=2, padx=5, pady=(15,5), sticky="ew")
        
        self.max_entries_per_segment_spinbox = ttk.Spinbox( # Renamed
            max_entries_segment_frame,
            from_=1,
            to=20, # Adjusted range
            width=8,
            command=self._update_max_entries_segment_label, # Command changed
            validate="key",
            validatecommand=(self.master.register(self._validate_max_entries_segment), '%P') # Validation changed
        )
        self.max_entries_per_segment_spinbox.pack(side="left", padx=(0,10))
        self.max_entries_per_segment_spinbox.set("5")  # 기본값
        
        self.max_entries_per_segment_label = ttk.Label(max_entries_segment_frame, text="개 항목", width=8) # Renamed
        self.max_entries_per_segment_label.pack(side="left")

        # New Lorebook settings
        ttk.Label(extraction_settings_frame, text="샘플링 방식:").grid(row=6, column=0, padx=5, pady=5, sticky="w")
        self.lorebook_sampling_method_combobox = ttk.Combobox(extraction_settings_frame, values=["uniform", "random"], width=15)
        self.lorebook_sampling_method_combobox.grid(row=6, column=1, padx=5, pady=5, sticky="w")
        self.lorebook_sampling_method_combobox.set("uniform")

        ttk.Label(extraction_settings_frame, text="항목 당 최대 글자 수:").grid(row=7, column=0, padx=5, pady=5, sticky="w")
        self.lorebook_max_chars_entry = ttk.Entry(extraction_settings_frame, width=10)
        self.lorebook_max_chars_entry.grid(row=7, column=1, padx=5, pady=5, sticky="w")
        self.lorebook_max_chars_entry.insert(0, "200")

        ttk.Label(extraction_settings_frame, text="키워드 민감도:").grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.lorebook_keyword_sensitivity_combobox = ttk.Combobox(extraction_settings_frame, values=["low", "medium", "high"], width=15)
        self.lorebook_keyword_sensitivity_combobox.grid(row=8, column=1, padx=5, pady=5, sticky="w")
        self.lorebook_keyword_sensitivity_combobox.set("medium")

        ttk.Label(extraction_settings_frame, text="로어북 세그먼트 크기:").grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.lorebook_chunk_size_entry = ttk.Entry(extraction_settings_frame, width=10)
        self.lorebook_chunk_size_entry.grid(row=9, column=1, padx=5, pady=5, sticky="w")
        self.lorebook_chunk_size_entry.insert(0, "8000")

        ttk.Label(extraction_settings_frame, text="우선순위 설정 (JSON):").grid(row=10, column=0, padx=5, pady=5, sticky="nw")
        self.lorebook_priority_text = scrolledtext.ScrolledText(extraction_settings_frame, width=40, height=5, wrap=tk.WORD)
        self.lorebook_priority_text.grid(row=10, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self.lorebook_priority_text.insert('1.0', json.dumps({"character": 5, "worldview": 5, "story_element": 5}, indent=2))
        
        # 도움말 레이블
        ttk.Label(extraction_settings_frame, 
                text="번역 시 프롬프트에 포함할 최대 고유명사 개수", 
                font=("Arial", 8), 
                foreground="gray").grid(row=3, column=1, columnspan=2, padx=5, sticky="w")
        
        # 고급 설정 (접을 수 있는 형태)
        self.advanced_var = tk.BooleanVar()
        advanced_check = ttk.Checkbutton(
            extraction_settings_frame, 
            text="고급 설정 표시", 
            variable=self.advanced_var,
            command=self._toggle_advanced_settings
        )
        advanced_check.grid(row=4, column=0, columnspan=3, padx=5, pady=(15,5), sticky="w")
        
        # 고급 설정 프레임 (초기에는 숨김)
        self.advanced_frame = ttk.Frame(extraction_settings_frame)
        self.advanced_frame.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        
        # 온도 설정 (고유명사 추출용)
        ttk.Label(self.advanced_frame, text="추출 온도:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        
        self.extraction_temp_scale = ttk.Scale(
            self.advanced_frame,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            length=150,
            command=self._update_extraction_temp_label
        )
        self.extraction_temp_scale.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.extraction_temp_scale.set(0.2)  # 기본값
        
        self.extraction_temp_label = ttk.Label(self.advanced_frame, text="0.20", width=6)
        self.extraction_temp_label.grid(row=0, column=2, padx=5, pady=5)
        
        # 초기에는 고급 설정 숨김
        self.advanced_frame.grid_remove()

        # 액션 버튼 프레임 추가
        lorebook_action_frame = ttk.Frame(lorebook_frame, padding="10") # Renamed
        lorebook_action_frame.pack(fill="x", padx=5, pady=5)
        
        # 설정 저장 버튼
        self.save_lorebook_settings_button = ttk.Button( # Renamed
            lorebook_action_frame, 
            text="로어북 설정 저장", # Text changed
            command=self._save_lorebook_settings # Command changed
        )
        self.save_lorebook_settings_button.pack(side="left", padx=5)
        
        # 설정 초기화 버튼
        self.reset_lorebook_settings_button = ttk.Button( # Renamed
            lorebook_action_frame, 
            text="기본값으로 초기화", 
            command=self._reset_lorebook_settings # Command changed
        )
        self.reset_lorebook_settings_button.pack(side="left", padx=5)
        
        # 실시간 미리보기 버튼
        self.preview_lorebook_settings_button = ttk.Button( # Renamed
            lorebook_action_frame, 
            text="설정 미리보기", 
            command=self._preview_lorebook_settings # Command changed
        )
        self.preview_lorebook_settings_button.pack(side="right", padx=5)

        # 상태 표시 레이블
        self.lorebook_status_label = ttk.Label( # Renamed
            lorebook_action_frame, 
            text="⏸️ 설정 변경 대기 중...", 
            font=("Arial", 9),
            foreground="gray"
        )
        self.lorebook_status_label.pack(side="bottom", pady=5)

        # Lorebook Display Area
        lorebook_display_frame = ttk.LabelFrame(lorebook_frame, text="추출된 로어북 (JSON)", padding="10")
        lorebook_display_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.lorebook_display_text = scrolledtext.ScrolledText(lorebook_display_frame, wrap=tk.WORD, height=10, width=70)
        self.lorebook_display_text.pack(fill="both", expand=True, padx=5, pady=5)

        lorebook_display_buttons_frame = ttk.Frame(lorebook_display_frame)
        lorebook_display_buttons_frame.pack(fill="x", pady=5)

        self.load_lorebook_button = ttk.Button(lorebook_display_buttons_frame, text="로어북 불러오기", command=self._load_lorebook_to_display)
        self.load_lorebook_button.pack(side="left", padx=5)

        self.copy_lorebook_button = ttk.Button(lorebook_display_buttons_frame, text="JSON 복사", command=self._copy_lorebook_json)
        self.copy_lorebook_button.pack(side="left", padx=5)

        self.save_displayed_lorebook_button = ttk.Button(lorebook_display_buttons_frame, text="JSON 저장", command=self._save_displayed_lorebook_json)
        self.save_displayed_lorebook_button.pack(side="left", padx=5)

        # 설정 변경 감지 이벤트 바인딩
        self.sample_ratio_scale.bind("<ButtonRelease-1>", self._on_lorebook_setting_changed) # Changed
        self.max_entries_per_segment_spinbox.bind("<KeyRelease>", self._on_lorebook_setting_changed) # Changed
        self.extraction_temp_scale.bind("<ButtonRelease-1>", self._on_lorebook_setting_changed) # Changed
        # Bindings for new lorebook settings
        self.lorebook_sampling_method_combobox.bind("<<ComboboxSelected>>", self._on_lorebook_setting_changed)
        self.lorebook_max_chars_entry.bind("<KeyRelease>", self._on_lorebook_setting_changed)
        self.lorebook_keyword_sensitivity_combobox.bind("<<ComboboxSelected>>", self._on_lorebook_setting_changed)
        self.lorebook_chunk_size_entry.bind("<KeyRelease>", self._on_lorebook_setting_changed)
        self.lorebook_priority_text.bind("<KeyRelease>", self._on_lorebook_setting_changed)

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

        # 변수 스코프 문제 해결: try 블록 밖에서 정의
        current_user_input_model = self.model_name_combobox.get()
        
        try:
            self._log_message("모델 목록 새로고침 중...")
            
            # 1단계: 클라이언트가 없으면 자동으로 설정 저장 및 초기화
            if not self.app_service.gemini_client:
                self._log_message("클라이언트가 초기화되지 않아 설정을 자동 저장하여 초기화합니다...")
                try:
                    current_ui_config = self._get_config_from_ui()
                    self.app_service.save_app_config(current_ui_config)
                    self._log_message("설정 자동 저장 및 클라이언트 초기화 완료.")
                except Exception as e:
                    self._log_message(f"설정 자동 저장 실패: {e}", "ERROR")
                    messagebox.showerror("설정 오류", f"API 설정 저장 중 오류가 발생했습니다: {e}")
                    self._reset_model_combobox(current_user_input_model)
                    return

            # 2단계: 클라이언트 재확인 (자동 초기화 후에도 실패할 수 있음)
            if not self.app_service.gemini_client:
                self._log_message("클라이언트 초기화 후에도 사용할 수 없습니다. API 키 또는 Vertex AI 설정을 확인하세요.", "WARNING")
                messagebox.showwarning("인증 필요", "API 키가 유효하지 않거나 Vertex AI 설정을 확인해주세요.")
                self._reset_model_combobox(current_user_input_model)
                return

            # 3단계: 모델 목록 조회 (한 번만 호출)
            models_data = self.app_service.get_available_models()
            
            # 4단계: UI 모델 목록 구성
            model_display_names_for_ui = []
            for m in models_data:
                display_name = m.get("display_name")
                short_name = m.get("short_name")
                full_name = m.get("name")
                
                # 우선순위: short_name > display_name > full_name
                chosen_name_for_display = short_name or display_name or full_name
                
                if chosen_name_for_display and isinstance(chosen_name_for_display, str) and chosen_name_for_display.strip():
                    model_display_names_for_ui.append(chosen_name_for_display.strip())
            
            model_display_names_for_ui = sorted(list(set(model_display_names_for_ui)))
            self.model_name_combobox['values'] = model_display_names_for_ui
            
            # 5단계: 모델 선택 (우선순위에 따라)
            self._set_optimal_model_selection(current_user_input_model, model_display_names_for_ui)
            
            self._log_message(f"{len(model_display_names_for_ui)}개 모델 로드 완료.")

        except BtgApiClientException as e:
            messagebox.showerror("API 오류", f"모델 목록 조회 실패: {e}")
            self._log_message(f"모델 목록 조회 API 오류: {e}", "ERROR")
            self._reset_model_combobox(current_user_input_model)
        except BtgServiceException as e: 
            messagebox.showerror("서비스 오류", f"모델 목록 조회 실패: {e}")
            self._log_message(f"모델 목록 조회 서비스 오류: {e}", "ERROR")
            self._reset_model_combobox(current_user_input_model)
        except Exception as e:
            messagebox.showerror("오류", f"모델 목록 조회 중 예상치 못한 오류: {e}")
            self._log_message(f"모델 목록 조회 중 오류: {e}", "ERROR", exc_info=True)
            self._reset_model_combobox(current_user_input_model)


    def _browse_input_file(self):
        filepath = filedialog.askopenfilename(title="입력 파일 선택", filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*")))
        if filepath:
            self.input_file_entry.delete(0, tk.END)
            self.input_file_entry.insert(0, filepath)
            p = Path(filepath)
            suggested_output = p.parent / f"{p.stem}_translated{p.suffix}"
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, str(suggested_output))
            suggested_lorebook_json = p.parent / f"{p.stem}{self.app_service.config.get('lorebook_output_json_filename_suffix', '_lorebook.json') if self.app_service else '_lorebook.json'}"
            self.lorebook_json_path_entry.delete(0, tk.END) # Changed
            self.lorebook_json_path_entry.insert(0, str(suggested_lorebook_json))

    def _browse_output_file(self):
        filepath = filedialog.asksaveasfilename(title="출력 파일 선택", defaultextension=".txt", filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*")))
        if filepath:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, filepath)

    def _browse_lorebook_json(self): # Renamed
        initial_dir = ""
        input_file_path = self.input_file_entry.get()
        if input_file_path and Path(input_file_path).exists():
            initial_dir = str(Path(input_file_path).parent)
        
        filepath = filedialog.askopenfilename(
            title="로어북 JSON 파일 선택",  # Text changed
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*")), # Type changed
            initialdir=initial_dir
            )
        if filepath:
            self.lorebook_json_path_entry.delete(0, tk.END) # Changed
            self.lorebook_json_path_entry.insert(0, filepath)

    def _browse_lorebook_json_for_injection(self):
        filepath = filedialog.askopenfilename(
            title="주입용 로어북 JSON 파일 선택",
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*"))
        )
        if filepath:
            self.lorebook_json_path_for_injection_entry.delete(0, tk.END)
            self.lorebook_json_path_for_injection_entry.insert(0, filepath)


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

        try:
            rpm_val = int(self.rpm_entry.get() or "60")
            if rpm_val < 0: rpm_val = 0 # 0은 제한 없음, 음수는 0으로
        except ValueError:
            rpm_val = 60
            messagebox.showwarning("입력 오류", f"분당 요청 수는 숫자여야 합니다. 기본값 ({rpm_val})으로 설정됩니다.")
            self.rpm_entry.delete(0, tk.END)
            self.rpm_entry.insert(0, str(rpm_val))

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
            "requests_per_minute": rpm_val,
            "prompts": prompt_content,
            "novel_language": self.novel_language_entry.get().strip() or "auto",
            "novel_language_fallback": self.novel_language_fallback_entry.get().strip() or "ja",
            # Lorebook settings
            "lorebook_json_path": self.lorebook_json_path_entry.get().strip() or None,
            "lorebook_sampling_ratio": self.sample_ratio_scale.get(),
            "lorebook_max_entries_per_segment": int(self.max_entries_per_segment_spinbox.get()),
            "lorebook_extraction_temperature": self.extraction_temp_scale.get(),
            "lorebook_sampling_method": self.lorebook_sampling_method_combobox.get(),
            "lorebook_max_chars_per_entry": int(self.lorebook_max_chars_entry.get() or "200"),
            "lorebook_keyword_sensitivity": self.lorebook_keyword_sensitivity_combobox.get(),
            "lorebook_chunk_size": int(self.lorebook_chunk_size_entry.get() or "8000"),
                # Dynamic lorebook injection settings
                "enable_dynamic_lorebook_injection": self.enable_dynamic_lorebook_injection_var.get(),
                "max_lorebook_entries_per_chunk_injection": int(self.max_lorebook_entries_injection_entry.get() or "3"),
                "max_lorebook_chars_per_chunk_injection": int(self.max_lorebook_chars_injection_entry.get() or "500"),
                "lorebook_json_path_for_injection": self.lorebook_json_path_for_injection_entry.get().strip() or None,
            # Content Safety Retry settings
            "use_content_safety_retry": self.use_content_safety_retry_var.get(),
            "max_content_safety_split_attempts": int(self.max_split_attempts_entry.get() or "3"),
            "min_content_safety_chunk_size": int(self.min_chunk_size_entry.get() or "100"),


        }
        try:
            config_data["lorebook_priority_settings"] = json.loads(self.lorebook_priority_text.get("1.0", tk.END).strip() or "{}")
        except json.JSONDecodeError:
            messagebox.showwarning("입력 오류", "로어북 우선순위 설정이 유효한 JSON 형식이 아닙니다. 기본값으로 유지됩니다.")
            config_data["lorebook_priority_settings"] = self.app_service.config_manager.get_default_config().get("lorebook_priority_settings")
        
        return config_data

    def _save_settings(self):
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        try:
            # 기존 전체 설정을 유지하고 UI 변경사항만 업데이트
            current_config = self.app_service.config.copy()
            ui_config = self._get_config_from_ui()
            current_config.update(ui_config)
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

    def _update_lorebook_extraction_progress(self, dto: LorebookExtractionProgressDTO): # Renamed and DTO changed
        def _update():
            if not self.master.winfo_exists(): return
            msg = f"{dto.current_status_message} ({dto.processed_segments}/{dto.total_segments}, 추출 항목: {dto.extracted_entries_count})" # DTO fields changed
            self.lorebook_progress_label.config(text=msg) # Changed
        if self.master.winfo_exists():
            self.master.after(0, _update)

    def _extract_lorebook_thread(self): # Renamed
        if not self.app_service:
            messagebox.showerror("오류", "애플리케이션 서비스가 초기화되지 않았습니다.")
            return

        input_file = self.input_file_entry.get() # This should be the source novel file
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
            messagebox.showerror("오류", f"로어북 추출 시작 전 설정 오류: {e}") # Text changed
            self._log_message(f"로어북 추출 시작 전 설정 오류: {e}", "ERROR", exc_info=True) # Text changed
            return

        self.lorebook_progress_label.config(text="로어북 추출 시작 중...") # Changed
        self._log_message(f"로어북 추출 시작: {input_file}") # Text changed
        
        # GUI에서 직접 소설 언어를 입력받는 UI가 제거되었으므로, 항상 None을 전달하여 AppService가 설정을 따르도록 합니다.
        novel_lang_for_extraction = None
        def _extraction_task_wrapper():
            try:
                if self.app_service: 
                    result_json_path = self.app_service.extract_lorebook( # Method name changed
                        input_file,
                        progress_callback=self._update_lorebook_extraction_progress, # Callback changed
                        novel_language_code=novel_lang_for_extraction # Pass the language
                    )
                    self.master.after(0, lambda: messagebox.showinfo("성공", f"로어북 추출 완료!\n결과 파일: {result_json_path}")) # Text changed
                    self.master.after(0, lambda: self.lorebook_progress_label.config(text=f"추출 완료: {result_json_path.name}")) # Changed
                    self.master.after(0, lambda: self._update_lorebook_json_path_entry(str(result_json_path))) # Changed
                    # Load result to display
                    if result_json_path and result_json_path.exists(): # Check if result_json_path is not None
                        with open(result_json_path, 'r', encoding='utf-8') as f_res:
                            lore_content = f_res.read()
                        self.master.after(0, lambda: self._display_lorebook_content(lore_content))
            # BtgPronounException replaced with BtgBusinessLogicException as LorebookService might throw more general business logic errors
            except (BtgFileHandlerException, BtgApiClientException, BtgServiceException, BtgBusinessLogicException) as e_btg:
                logger.error(f"로어북 추출 중 BTG 예외 발생: {e_btg}", exc_info=True) # Text changed
                self.master.after(0, lambda: messagebox.showerror("추출 오류", f"로어북 추출 중 오류: {e_btg}")) # Text changed
                self.master.after(0, lambda: self.lorebook_progress_label.config(text="오류 발생")) # Changed
            except Exception as e_unknown: 
                logger.error(f"로어북 추출 중 알 수 없는 예외 발생: {e_unknown}", exc_info=True) # Text changed
                self.master.after(0, lambda: messagebox.showerror("알 수 없는 오류", f"로어북 추출 중 예상치 못한 오류: {e_unknown}")) # Text changed
                self.master.after(0, lambda: self.lorebook_progress_label.config(text="알 수 없는 오류 발생")) # Changed
            finally:
                self._log_message("로어북 추출 스레드 종료.") # Text changed

        thread = threading.Thread(target=_extraction_task_wrapper, daemon=True)
        thread.start()

    def _update_lorebook_json_path_entry(self, path_str: str): # Renamed
        self.lorebook_json_path_entry.delete(0, tk.END) # Changed
        self.lorebook_json_path_entry.insert(0, path_str)
        if self.app_service:
            self.app_service.config["lorebook_json_path"] = path_str # Changed

    def _on_closing(self):
        if self.app_service and self.app_service.is_translation_running:
            if messagebox.askokcancel("종료 확인", "번역 작업이 진행 중입니다. 정말로 종료하시겠습니까?"):
                self.app_service.request_stop_translation()
                logger.info("사용자 종료 요청으로 번역 중단 시도.")
                self.master.destroy()
        else:
            self.master.destroy()

    def _on_api_key_changed(self, event=None):
        """API 키가 변경되었을 때 클라이언트 초기화 상태 리셋"""
        if hasattr(self, 'app_service') and self.app_service:
            # 다음 모델 새로고침 시 자동으로 재초기화되도록 플래그 설정
            self._client_needs_refresh = True

    def _reset_model_combobox(self, current_user_input_model: str):
        """모델 콤보박스를 초기 상태로 리셋"""
        self.model_name_combobox['values'] = []
        self.model_name_combobox.set(current_user_input_model)

    def _set_optimal_model_selection(self, current_user_input_model: str, model_display_names_for_ui: List[str]):
        """최적의 모델 선택 로직"""
        config_model_name = self.app_service.config.get("model_name", "")
        config_model_short_name = config_model_name.split('/')[-1] if '/' in config_model_name else config_model_name

        # 우선순위에 따른 모델 선택
        if current_user_input_model and current_user_input_model.strip() in model_display_names_for_ui:
            self.model_name_combobox.set(current_user_input_model)
        elif config_model_short_name and config_model_short_name in model_display_names_for_ui:
            self.model_name_combobox.set(config_model_short_name)
        elif config_model_name and config_model_name in model_display_names_for_ui:
            self.model_name_combobox.set(config_model_name)
        elif model_display_names_for_ui:
            self.model_name_combobox.set(model_display_names_for_ui[0])
        else:
            self.model_name_combobox.set("")


    def _update_sample_ratio_label(self, value):
        """샘플링 비율 레이블 업데이트"""
        ratio = float(value)
        self.sample_ratio_label.config(text=f"{ratio:.1f}%")

    def _validate_max_entries_segment(self, value): # Renamed
        """세그먼트 당 최대 항목 수 유효성 검사"""
        if value == "":
            return True
        try:
            num = int(value)
            return 1 <= num <= 50 # Adjusted range
        except ValueError:
            return False

    def _update_max_entries_label(self):
        """최대 고유명사 수 변경 시 호출"""
        try:
            value = int(self.max_entries_spinbox.get())
            # This label might be removed or repurposed if max_entries_spinbox is for per_segment
            # self.max_entries_label.config(text="개 항목")
        except ValueError:
            pass
    def _update_max_entries_segment_label(self): # New or adapted
        pass # Label might not be needed if spinbox is clear

    def _update_extraction_temp_label(self, value):
        """추출 온도 레이블 업데이트"""
        temp = float(value)
        # extraction_temp_label이 존재하는지 확인
        if hasattr(self, 'extraction_temp_label'):
            self.extraction_temp_label.config(text=f"{temp:.2f}")

    def _toggle_advanced_settings(self):
        """고급 설정 표시/숨김 토글"""
        if self.advanced_var.get():
            self.advanced_frame.grid()
        else:
            self.advanced_frame.grid_remove()

    def _show_sampling_estimate(self):
        """샘플링 비율에 따른 예상 처리량 표시"""
        input_file = self.input_file_entry.get()
        if not input_file or not Path(input_file).exists():
            return
        
        try:
            # 파일 크기 기반 추정
            file_size = Path(input_file).stat().st_size
            chunk_size = int(self.chunk_size_entry.get() or "6000")
            estimated_chunks = file_size // chunk_size
            
            sample_ratio = self.sample_ratio_scale.get() / 100.0
            estimated_sample_chunks = int(estimated_chunks * sample_ratio)
            
            # 추정 정보를 툴팁이나 레이블로 표시
            estimate_text = f"예상 분석 청크: {estimated_sample_chunks}/{estimated_chunks}"
            
            # 기존 라벨이 있다면 업데이트, 없다면 생성
            if hasattr(self, 'sampling_estimate_label'):
                self.sampling_estimate_label.config(text=estimate_text)
            
        except Exception:
            pass  # 추정 실패 시 무시

    def _save_lorebook_settings(self): # Renamed
        """로어북 관련 설정만 저장"""
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        
        try:
            # 현재 전체 설정 가져오기
            current_config = self.app_service.config.copy()
            
            # 고유명사 관련 설정만 업데이트
            lorebook_config = self._get_lorebook_config_from_ui() # Changed
            current_config.update(lorebook_config)
            
            # 설정 저장
            success = self.app_service.save_app_config(current_config)
            
            if success:
                messagebox.showinfo("성공", "로어북 설정이 저장되었습니다.") # Text changed
                self._log_message("로어북 설정 저장 완료") # Text changed
                
                # 상태 레이블 업데이트
                self._update_lorebook_status_label("✅ 설정 저장됨") # Changed
            else:
                messagebox.showerror("오류", "설정 저장에 실패했습니다.")
                
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 중 오류: {e}")
            self._log_message(f"로어북 설정 저장 오류: {e}", "ERROR") # Text changed

    def _get_lorebook_config_from_ui(self) -> Dict[str, Any]: # Renamed
        """UI에서 로어북 관련 설정만 추출"""
        try:
            config = {
                "lorebook_json_path": self.lorebook_json_path_entry.get().strip() or None, # Changed
                "lorebook_sampling_ratio": self.sample_ratio_scale.get(),
                "lorebook_max_entries_per_segment": int(self.max_entries_per_segment_spinbox.get()), # Changed
                "lorebook_extraction_temperature": self.extraction_temp_scale.get(),
                "lorebook_sampling_method": self.lorebook_sampling_method_combobox.get(),
                "lorebook_max_chars_per_entry": int(self.lorebook_max_chars_entry.get() or "200"),
                "lorebook_keyword_sensitivity": self.lorebook_keyword_sensitivity_combobox.get(),
                "lorebook_chunk_size": int(self.lorebook_chunk_size_entry.get() or "8000"),
                # Dynamic lorebook injection settings
                "enable_dynamic_lorebook_injection": self.enable_dynamic_lorebook_injection_var.get(),
                "max_lorebook_entries_per_chunk_injection": int(self.max_lorebook_entries_injection_entry.get() or "3"),
                "max_lorebook_chars_per_chunk_injection": int(self.max_lorebook_chars_injection_entry.get() or "500"),
                "lorebook_json_path_for_injection": self.lorebook_json_path_for_injection_entry.get().strip() or None,

            }
            try:
                config["lorebook_priority_settings"] = json.loads(self.lorebook_priority_text.get("1.0", tk.END).strip() or "{}")
            except json.JSONDecodeError:
                # Use existing config value if UI is invalid, or default if not available
                config["lorebook_priority_settings"] = self.app_service.config.get("lorebook_priority_settings", 
                                                                                self.app_service.config_manager.get_default_config().get("lorebook_priority_settings"))
                self._log_message("로어북 우선순위 JSON 파싱 오류. 기존/기본값 사용.", "WARNING")

            return {k: v for k, v in config.items() if v is not None}
        except Exception as e:
            raise ValueError(f"로어북 설정 값 오류: {e}") # Text changed

    def _reset_lorebook_settings(self): # Renamed
        """로어북 설정을 기본값으로 초기화"""
        if not self.app_service:
            return
        
        result = messagebox.askyesno(
            "설정 초기화", 
            "로어북 설정을 기본값으로 초기화하시겠습니까?" # Text changed
        )
        
        if result:
            try:
                # 기본값 로드
                default_config = self.app_service.config_manager.get_default_config()
                # UI에 기본값 적용
                self.sample_ratio_scale.set(default_config.get("lorebook_sampling_ratio", 25.0))
                self.max_entries_per_segment_spinbox.set(str(default_config.get("lorebook_max_entries_per_segment", 5)))
                self.extraction_temp_scale.set(default_config.get("lorebook_extraction_temperature", 0.2))
                
                # Reset new lorebook fields
                self.lorebook_sampling_method_combobox.set(default_config.get("lorebook_sampling_method", "uniform"))
                self.lorebook_max_chars_entry.delete(0, tk.END)
                self.lorebook_max_chars_entry.insert(0, str(default_config.get("lorebook_max_chars_per_entry", 200)))
                self.lorebook_keyword_sensitivity_combobox.set(default_config.get("lorebook_keyword_sensitivity", "medium"))
                self.lorebook_chunk_size_entry.delete(0, tk.END)
                self.lorebook_chunk_size_entry.insert(0, str(default_config.get("lorebook_chunk_size", 8000)))
                self.lorebook_priority_text.delete('1.0', tk.END)
                self.lorebook_priority_text.insert('1.0', json.dumps(default_config.get("lorebook_priority_settings", {"character": 5, "worldview": 5, "story_element": 5}), indent=2))
                
                # 레이블 업데이트
                self._update_sample_ratio_label(str(self.sample_ratio_scale.get()))
                self._update_extraction_temp_label(str(self.extraction_temp_scale.get()))
                
                self._update_lorebook_status_label("🔄 기본값으로 초기화됨") # Changed
                self._log_message("로어북 설정이 기본값으로 초기화되었습니다.") # Text changed
                
            except Exception as e:
                messagebox.showerror("오류", f"기본값 로드 중 오류: {e}")

    def _preview_lorebook_settings(self): # Renamed
        """현재 설정의 예상 효과 미리보기"""
        try:
            input_file = self.input_file_entry.get()
            if not input_file or not Path(input_file).exists():
                messagebox.showwarning("파일 없음", "입력 파일을 선택해주세요.")
                return
            
            # 현재 설정 값들
            sample_ratio = self.sample_ratio_scale.get()
            max_entries_segment = int(self.max_entries_per_segment_spinbox.get()) # Changed
            extraction_temp = self.extraction_temp_scale.get() # This is lorebook_extraction_temperature
            
            # 파일 크기 기반 추정
            file_size = Path(input_file).stat().st_size
            chunk_size = int(self.chunk_size_entry.get() or "6000")
            estimated_chunks = max(1, file_size // chunk_size)
            estimated_sample_chunks = max(1, int(estimated_chunks * sample_ratio / 100.0))
            
            # 미리보기 정보 표시
            preview_msg = (
                f"📊 로어북 추출 설정 미리보기\n\n" # Text changed
                f"📁 입력 파일: {Path(input_file).name}\n"
                f"📏 파일 크기: {file_size:,} 바이트\n"
                f"🧩 예상 청크 수: {estimated_chunks:,}개\n"
                f"🎯 분석할 샘플: {estimated_sample_chunks:,}개 ({sample_ratio:.1f}%)\n"
                f"📋 세그먼트 당 최대 항목: {max_entries_segment}개\n" # Text changed
                f"🌡️ 추출 온도: {extraction_temp:.2f}\n\n"
                f"⏱️ 예상 처리 시간: {estimated_sample_chunks * 2:.0f}~{estimated_sample_chunks * 5:.0f}초"
            )
            
            messagebox.showinfo("설정 미리보기", preview_msg)
        except Exception as e:
            messagebox.showerror("오류", f"미리보기 생성 중 오류: {e}")

    def _update_lorebook_status_label(self, message: str): # Renamed
        """로어북 설정 상태 업데이트"""
        if hasattr(self, 'lorebook_status_label'): # Changed
            self.lorebook_status_label.config(text=message) # Changed
            
            # 3초 후 기본 메시지로 복귀
            self.master.after(3000, lambda: self.lorebook_status_label.config( # Changed
                text="⏸️ 설정 변경 대기 중..."
            ))

    def _on_lorebook_setting_changed(self, event=None): # Renamed
        """로어북 설정이 변경될 때 호출"""
        self._update_lorebook_status_label("⚠️ 설정이 변경됨 (저장 필요)") # Changed
        
        # 저장 버튼 강조
        if hasattr(self, 'save_lorebook_settings_button'): # Changed
            self.save_lorebook_settings_button.config(style="Accent.TButton") # Changed

    def _display_lorebook_content(self, content: str):
        self.lorebook_display_text.config(state=tk.NORMAL)
        self.lorebook_display_text.delete('1.0', tk.END)
        self.lorebook_display_text.insert('1.0', content)
        self.lorebook_display_text.config(state=tk.DISABLED)

    def _load_lorebook_to_display(self):
        filepath = filedialog.askopenfilename(title="로어북 JSON 파일 선택", filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*")))
        if filepath:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._display_lorebook_content(content)
                self.lorebook_json_path_entry.delete(0, tk.END)
                self.lorebook_json_path_entry.insert(0, filepath)
                self._log_message(f"로어북 파일 로드됨: {filepath}")
            except Exception as e:
                messagebox.showerror("오류", f"로어북 파일 로드 실패: {e}")
                self._log_message(f"로어북 파일 로드 실패: {e}", "ERROR")

    def _copy_lorebook_json(self):
        content = self.lorebook_display_text.get('1.0', tk.END).strip()
        if content:
            self.master.clipboard_clear()
            self.master.clipboard_append(content)
            messagebox.showinfo("성공", "로어북 JSON 내용이 클립보드에 복사되었습니다.")
            self._log_message("로어북 JSON 클립보드에 복사됨.")
        else:
            messagebox.showwarning("경고", "복사할 내용이 없습니다.")

    def _save_displayed_lorebook_json(self):
        content = self.lorebook_display_text.get('1.0', tk.END).strip()
        if not content:
            messagebox.showwarning("경고", "저장할 내용이 없습니다.")
            return
        
        filepath = filedialog.asksaveasfilename(title="로어북 JSON으로 저장", defaultextension=".json", filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*")))
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                messagebox.showinfo("성공", f"로어북이 성공적으로 저장되었습니다: {filepath}")
                self._log_message(f"표시된 로어북 저장됨: {filepath}")
            except Exception as e:
                messagebox.showerror("오류", f"로어북 저장 실패: {e}")
                self._log_message(f"표시된 로어북 저장 실패: {e}", "ERROR")

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