# batch_translator_gui.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import threading
import os
from pathlib import Path
import sys # sys 모듈 임포트

# 프로젝트 루트 디렉토리를 sys.path에 추가
project_root = Path(__file__).resolve().parent.parent # Assuming this file is in neo_batch_translator/
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from typing import Optional, Dict, Any, List, Callable, Union
from dataclasses import dataclass
import json
import time
import io
import logging

# 4계층 아키텍처의 AppService 및 DTOs, Exceptions 임포트
try:
    from app.app_service import AppService
    from core.dtos import TranslationJobProgressDTO, GlossaryExtractionProgressDTO, ModelInfoDTO
    from core.exceptions import BtgConfigException, BtgServiceException, BtgFileHandlerException, BtgApiClientException, BtgBusinessLogicException, BtgException
    from infrastructure.logger_config import setup_logger
    from infrastructure.file_handler import get_metadata_file_path, load_metadata, _hash_config_for_metadata, delete_file
except ImportError as e:
    # Critical error: GUI cannot function without these core components.
    # Print to stderr and a simple dialog if tkinter is available enough for that.
    error_message = (
        f"초기 임포트 오류: {e}.\n"
        "스크립트가 프로젝트 루트에서 실행되고 있는지, "
        "PYTHONPATH가 올바르게 설정되었는지 확인하세요.\n"
        "필수 모듈을 임포트할 수 없어 GUI를 시작할 수 없습니다."
    )
    print(error_message, file=sys.stderr)
    try:
        # Attempt a simple messagebox if tkinter's core is loaded enough
        import tkinter as tk # Keep this import local to the except block
        from tkinter import messagebox # Keep this import local
        # Need to create a dummy root for messagebox if no root window exists yet
        dummy_root = tk.Tk()
        dummy_root.withdraw() # Hide the dummy root window
        messagebox.showerror("치명적 임포트 오류", error_message)
        dummy_root.destroy()
    except Exception:
        pass # If even this fails, the console message is the best we can do.
    sys.exit(1) # Exit if essential imports fail

GUI_LOGGER_NAME = __name__ + "_gui" # Define once for consistent use
logger = setup_logger(GUI_LOGGER_NAME) # Use the defined name
    
class Tooltip:
    """
    위젯 위에 마우스를 올렸을 때 툴팁을 표시하는 클래스입니다.
    wm_overrideredirect(True)를 사용하지 않아 macOS 호환성을 높입니다.
    """
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.id = None
        self.x = self.y = 0
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave) # 클릭 시에도 툴팁 숨김

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        # 툴팁 표시 전 약간의 지연 (0.5초)
        self.id = self.widget.after(500, self.showtip)

    def unschedule(self):
        id = self.id
        self.id = None
        if id:
            self.widget.after_cancel(id)

    def showtip(self, event=None):
        if not self.widget.winfo_exists():
            self.hidetip()
            return

        # Default internal offsets if bbox fails or is not applicable
        # These are coordinates relative to the widget's top-left corner.
        cursor_x_in_widget = 0
        cursor_y_in_widget = 0

        bbox = self.widget.bbox("insert") # Get bbox relative to the widget
        if bbox:
            # If bbox is valid, use its x,y (relative to widget's top-left)
            cursor_x_in_widget, cursor_y_in_widget, _, _ = bbox
        
        # Calculate final screen coordinates for the tooltip
        # Add widget's screen coordinates, cursor's relative coordinates, and a fixed offset
        final_tooltip_x = self.widget.winfo_rootx() + cursor_x_in_widget + 25
        final_tooltip_y = self.widget.winfo_rooty() + cursor_y_in_widget + 20

        # 이전 툴팁 창이 있다면 파괴
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None
        
        # Create new tooltip window
        # Ensure widget still exists before making it a master of Toplevel
        if not self.widget.winfo_exists(): # Double check, as time might have passed
            self.hidetip()
            return

        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True) 
        self.tooltip_window.wm_geometry(f"+{int(final_tooltip_x)}+{int(final_tooltip_y)}")
        label = tk.Label(self.tooltip_window, text=self.text, justify='left',
                         background="#ffffe0", relief='solid', borderwidth=1,
                         font=("tahoma", "8", "normal"))
        label.pack(ipadx=1, ipady=1) # ipady 추가로 약간의 세로 여백

    def hidetip(self):
        tw = self.tooltip_window
        self.tooltip_window = None
        if tw:
            tw.destroy()


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
        self.glossary_scroll = ScrollableFrame(self.notebook) # Renamed from lorebook_scroll
        self.log_tab = ttk.Frame(self.notebook, padding="10")  # 로그 탭은 기존 유지
        
        # 탭 추가
        self.notebook.add(self.settings_scroll.main_frame, text='설정 및 번역')
        self.notebook.add(self.glossary_scroll.main_frame, text='용어집 관리') # Tab text changed
        
        self.notebook.add(self.log_tab, text='실행 로그')
        self.notebook.pack(expand=True, fill='both')
        
        # 위젯 생성 (스크롤 가능한 프레임 사용)
        self._create_settings_widgets()
        self._create_glossary_widgets() # Renamed from _create_lorebook_widgets
        
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

            if hasattr(self, 'api_keys_text'): # Check if widget exists
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
            
            model_name_from_config = config.get("model_name", "gemini-2.0-flash")
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


            # Prefill settings
            self.enable_prefill_var.set(config.get("enable_prefill_translation", False))
            
            prefill_system_instruction_val = config.get("prefill_system_instruction", "")
            self.prefill_system_instruction_text.delete('1.0', tk.END)
            self.prefill_system_instruction_text.insert('1.0', prefill_system_instruction_val)

            prefill_cached_history_obj = config.get("prefill_cached_history", [])
            try:
                prefill_cached_history_json_str = json.dumps(prefill_cached_history_obj, indent=2, ensure_ascii=False)
            except TypeError:
                prefill_cached_history_json_str = "[]" # 기본값
            self.prefill_cached_history_text.delete('1.0', tk.END)
            self.prefill_cached_history_text.insert('1.0', prefill_cached_history_json_str)

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
            glossary_json_path_val = config.get("glossary_json_path") # Key changed
            logger.debug(f"Config에서 가져온 glossary_json_path: {glossary_json_path_val}")
            self.glossary_json_path_entry.delete(0, tk.END) # Widget name changed
            self.glossary_json_path_entry.insert(0, glossary_json_path_val if glossary_json_path_val is not None else "")

            sample_ratio = config.get("glossary_sampling_ratio", 10.0) # Key changed, default to simpler
            self.sample_ratio_scale.set(sample_ratio)
            self.sample_ratio_label.config(text=f"{sample_ratio:.1f}%")
            
            # Removed UI elements for: max_entries_per_segment, sampling_method, max_chars_per_entry, keyword_sensitivity
            # These are not directly used by SimpleGlossaryService's prompt
            

            # For priority_settings, ai_prompt_template, conflict_resolution_prompt_template - ScrolledText
            # self.glossary_chunk_size_entry was removed, so no UI load needed.

            # Dynamic Lorebook Injection Settings
            self.enable_dynamic_glossary_injection_var.set(config.get("enable_dynamic_glossary_injection", False)) # Key changed, var name changed
            self.max_glossary_entries_injection_entry.delete(0, tk.END) # Widget name changed
            self.max_glossary_entries_injection_entry.insert(0, str(config.get("max_glossary_entries_per_chunk_injection", 3))) # Key changed
            self.max_glossary_chars_injection_entry.delete(0, tk.END) # Widget name changed
            self.max_glossary_chars_injection_entry.insert(0, str(config.get("max_glossary_chars_per_chunk_injection", 500))) # Key changed
            # lorebook_json_path_for_injection_entry 관련 UI 로드 코드는 제거 (아래 _create_settings_widgets 에서 해당 UI 요소 제거됨)

            extraction_temp = config.get("glossary_extraction_temperature", 0.3) # Key changed, default to simpler
            

            
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
        Tooltip(self.api_keys_label, "Gemini Developer API를 사용할 경우 API 키를 입력합니다.\n여러 개일 경우 한 줄에 하나씩 입력하세요.")
        
        self.api_keys_text = scrolledtext.ScrolledText(api_frame, width=58, height=3, wrap=tk.WORD)
        self.api_keys_text.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        # API 키 텍스트가 변경될 때마다 이벤트 핸들러 연결
        Tooltip(self.api_keys_text, "사용할 Gemini API 키 목록입니다.")
        self.api_keys_text.bind('<KeyRelease>', self._on_api_key_changed)

        
        # Vertex AI 설정
        self.use_vertex_ai_var = tk.BooleanVar()
        self.use_vertex_ai_check = ttk.Checkbutton(api_frame, text="Vertex AI 사용", 
                                                   variable=self.use_vertex_ai_var, 
                                                   command=self._toggle_vertex_fields)
        self.use_vertex_ai_check.grid(row=1, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        Tooltip(self.use_vertex_ai_check, "Google Cloud Vertex AI API를 사용하려면 선택하세요.\n서비스 계정 JSON 파일 또는 ADC 인증이 필요합니다.")

        self.service_account_file_label = ttk.Label(api_frame, text="서비스 계정 JSON 파일 (Vertex AI):")
        self.service_account_file_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        Tooltip(self.service_account_file_label, "Vertex AI 인증에 사용할 서비스 계정 JSON 파일의 경로입니다.")
        self.service_account_file_entry = ttk.Entry(api_frame, width=50)
        self.service_account_file_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.service_account_file_entry, "Vertex AI 서비스 계정 파일 경로를 입력하거나 '찾아보기'로 선택하세요.")
        self.browse_sa_file_button = ttk.Button(api_frame, text="찾아보기", command=self._browse_service_account_file)
        self.browse_sa_file_button.grid(row=2, column=2, padx=5, pady=5)
        Tooltip(self.browse_sa_file_button, "서비스 계정 JSON 파일을 찾습니다.")

        self.gcp_project_label = ttk.Label(api_frame, text="GCP 프로젝트 ID (Vertex AI):")
        self.gcp_project_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        Tooltip(self.gcp_project_label, "Vertex AI 사용 시 필요한 Google Cloud Project ID입니다.")
        self.gcp_project_entry = ttk.Entry(api_frame, width=30)
        self.gcp_project_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.gcp_project_entry, "GCP 프로젝트 ID를 입력하세요.")

        self.gcp_location_label = ttk.Label(api_frame, text="GCP 위치 (Vertex AI):")
        self.gcp_location_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        Tooltip(self.gcp_location_label, "Vertex AI 모델이 배포된 GCP 리전입니다 (예: asia-northeast3).")
        self.gcp_location_entry = ttk.Entry(api_frame, width=30)
        self.gcp_location_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.gcp_location_entry, "GCP 리전을 입력하세요.")

        model_name_label = ttk.Label(api_frame, text="모델 이름:")
        model_name_label.grid(row=5, column=0, padx=5, pady=5, sticky="w")
        Tooltip(model_name_label, "번역에 사용할 AI 모델의 이름입니다.")
        self.model_name_combobox = ttk.Combobox(api_frame, width=57) 
        self.model_name_combobox.grid(row=5, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.model_name_combobox, "사용 가능한 모델 목록에서 선택하거나 직접 입력하세요.\n'새로고침' 버튼으로 목록을 업데이트할 수 있습니다.")
        self.refresh_models_button = ttk.Button(api_frame, text="새로고침", command=self._update_model_list_ui)
        self.refresh_models_button.grid(row=5, column=2, padx=5, pady=5)
        Tooltip(self.refresh_models_button, "사용 가능한 모델 목록을 API에서 새로 가져옵니다.")

        # 생성 파라미터
        gen_param_frame = ttk.LabelFrame(settings_frame, text="생성 파라미터", padding="10")
        gen_param_frame.pack(fill="x", padx=5, pady=5)
        
        # Temperature 설정
        ttk.Label(gen_param_frame, text="Temperature:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(ttk.Label(gen_param_frame, text="Temperature:"), "모델 응답의 무작위성 조절 (낮을수록 결정적, 높을수록 다양).")
        self.temperature_scale = ttk.Scale(gen_param_frame, from_=0.0, to=2.0, orient="horizontal", length=200,
                                         command=lambda v: self.temperature_label.config(text=f"{float(v):.2f}"))
        self.temperature_scale.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.temperature_scale, "Temperature 값을 조절합니다 (0.0 ~ 2.0).")
        self.temperature_label = ttk.Label(gen_param_frame, text="0.00")
        self.temperature_label.grid(row=0, column=2, padx=5, pady=5)
        
        # Top P 설정
        ttk.Label(gen_param_frame, text="Top P:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(ttk.Label(gen_param_frame, text="Top P:"), "모델이 다음 단어를 선택할 때 고려하는 확률 분포의 누적합 (낮을수록 집중적, 높을수록 다양).")
        self.top_p_scale = ttk.Scale(gen_param_frame, from_=0.0, to=1.0, orient="horizontal", length=200,
                                   command=lambda v: self.top_p_label.config(text=f"{float(v):.2f}"))
        self.top_p_scale.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.top_p_scale, "Top P 값을 조절합니다 (0.0 ~ 1.0).")
        self.top_p_label = ttk.Label(gen_param_frame, text="0.00")
        self.top_p_label.grid(row=1, column=2, padx=5, pady=5)
        
        # 파일 및 처리 설정
        file_chunk_frame = ttk.LabelFrame(settings_frame, text="파일 및 처리 설정", padding="10")
        file_chunk_frame.pack(fill="x", padx=5, pady=5)
        
        # 입력 파일
        ttk.Label(file_chunk_frame, text="입력 파일:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(ttk.Label(file_chunk_frame, text="입력 파일:"), "번역할 원본 텍스트 파일입니다.")
        self.input_file_entry = ttk.Entry(file_chunk_frame, width=50)
        self.input_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.input_file_entry, "번역할 파일 경로를 입력하거나 '찾아보기'로 선택하세요.")
        self.browse_input_button = ttk.Button(file_chunk_frame, text="찾아보기", command=self._browse_input_file)
        self.browse_input_button.grid(row=0, column=2, padx=5, pady=5)
        Tooltip(self.browse_input_button, "번역할 입력 파일을 찾습니다.")
        
        # 출력 파일
        ttk.Label(file_chunk_frame, text="출력 파일:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(ttk.Label(file_chunk_frame, text="출력 파일:"), "번역된 결과를 저장할 파일입니다.")
        self.output_file_entry = ttk.Entry(file_chunk_frame, width=50)
        self.output_file_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.output_file_entry, "번역 결과를 저장할 파일 경로를 입력하거나 '찾아보기'로 선택하세요.")
        self.browse_output_button = ttk.Button(file_chunk_frame, text="찾아보기", command=self._browse_output_file)
        self.browse_output_button.grid(row=1, column=2, padx=5, pady=5)
        Tooltip(self.browse_output_button, "번역 결과를 저장할 출력 파일을 선택합니다.")
        
        # 청크 크기 및 작업자 수
        chunk_worker_frame = ttk.Frame(file_chunk_frame)
        chunk_worker_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=5)
        
        ttk.Label(chunk_worker_frame, text="청크 크기:").pack(side="left", padx=(0,5))
        Tooltip(ttk.Label(chunk_worker_frame, text="청크 크기:"), "API 요청당 처리할 텍스트의 최대 문자 수입니다.")
        self.chunk_size_entry = ttk.Entry(chunk_worker_frame, width=10)
        self.chunk_size_entry.pack(side="left", padx=(0,15))
        Tooltip(self.chunk_size_entry, "청크 크기를 입력하세요 (예: 6000).")
        
        ttk.Label(chunk_worker_frame, text="최대 작업자 수:").pack(side="left", padx=(10,5))
        Tooltip(ttk.Label(chunk_worker_frame, text="최대 작업자 수:"), "동시에 실행할 번역 스레드의 최대 개수입니다.")
        self.max_workers_entry = ttk.Entry(chunk_worker_frame, width=5)
        self.max_workers_entry.pack(side="left")
        self.max_workers_entry.insert(0, str(os.cpu_count() or 1))
        Tooltip(self.max_workers_entry, "최대 작업자 수를 입력하세요 (예: 4).")
        
        # RPM 설정
        ttk.Label(chunk_worker_frame, text="분당 요청 수 (RPM):").pack(side="left", padx=(10,5))
        Tooltip(ttk.Label(chunk_worker_frame, text="분당 요청 수 (RPM):"), "API에 분당 보낼 수 있는 최대 요청 수입니다. 0은 제한 없음을 의미합니다.")
        self.rpm_entry = ttk.Entry(chunk_worker_frame, width=5)
        self.rpm_entry.pack(side="left")
        Tooltip(self.rpm_entry, "분당 요청 수를 입력하세요 (예: 60).")

        # Helper method to update toggle button text
        def update_toggle_text(button, var, base_text):
            if var.get():
                button.config(text=f"▲ {base_text} 숨기기")
            else:
                button.config(text=f"▼ {base_text} 보기")
        # Language Settings Frame
        language_settings_frame = ttk.LabelFrame(settings_frame, text="언어 설정", padding="10")
        language_settings_frame.pack(fill="x", padx=5, pady=5)

        novel_lang_label = ttk.Label(language_settings_frame, text="소설/번역 출발 언어:")
        novel_lang_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(novel_lang_label, "번역할 원본 텍스트의 언어 코드입니다 (예: ko, ja, en).\n'auto'로 설정 시 언어를 자동으로 감지합니다.")
        self.novel_language_entry = ttk.Entry(language_settings_frame, width=10)
        self.novel_language_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        self.novel_language_entry.insert(0, "auto") 
        Tooltip(self.novel_language_entry, "언어 코드를 입력하세요 (BCP-47 형식).")
        ttk.Label(language_settings_frame, text="(예: ko, ja, en, auto)").grid(row=0, column=2, padx=5, pady=5, sticky="w")

        novel_lang_fallback_label = ttk.Label(language_settings_frame, text="언어 자동감지 실패 시 폴백:")
        novel_lang_fallback_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(novel_lang_fallback_label, "출발 언어 자동 감지 실패 시 사용할 기본 언어 코드입니다.")
        self.novel_language_fallback_entry = ttk.Entry(language_settings_frame, width=10)
        self.novel_language_fallback_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.novel_language_fallback_entry.insert(0, "ja")
        Tooltip(self.novel_language_fallback_entry, "폴백 언어 코드를 입력하세요.")
        ttk.Label(language_settings_frame, text="(예: ko, ja, en)").grid(row=1, column=2, padx=5, pady=5, sticky="w")

        # 시스템 지침 및 번역 프롬프트 프레임
        prompt_frame = ttk.LabelFrame(settings_frame, text="프롬프트 설정", padding="10")
        prompt_frame.pack(fill="both", expand=True, padx=5, pady=5)
        # 일반 시스템 지침 UI 제거

        # 번역 프롬프트 (기존 Chat Prompt 역할)
        chat_prompt_label = ttk.Label(prompt_frame, text="번역 프롬프트 (Chat/User Prompt):")
        chat_prompt_label.pack(anchor="w", padx=5, pady=(10,0))
        Tooltip(prompt_frame, "번역 모델에 전달할 프롬프트입니다.\n{{slot}}은 번역할 텍스트 청크로 대체됩니다.\n{{glossary_context}}는 용어집 내용으로 대체됩니다.")
        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, wrap=tk.WORD, height=8, width=70)
        self.prompt_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 프리필 번역 설정 프레임
        prefill_frame = ttk.LabelFrame(settings_frame, text="프리필(Prefill) 번역 설정", padding="10")
        prefill_frame.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(prefill_frame, "모델에 초기 컨텍스트(시스템 지침 및 대화 기록)를 제공하여 번역 품질을 향상시킬 수 있습니다.")

        self.enable_prefill_var = tk.BooleanVar()
        self.enable_prefill_check = ttk.Checkbutton(prefill_frame, text="프리필 번역 사용", variable=self.enable_prefill_var)
        self.enable_prefill_check.pack(anchor="w", padx=5, pady=(5,0))
        Tooltip(self.enable_prefill_check, "활성화 시 아래의 프리필 시스템 지침과 캐시된 히스토리를 사용합니다.")

        prefill_system_instruction_label = ttk.Label(prefill_frame, text="프리필 시스템 지침:")
        prefill_system_instruction_label.pack(anchor="w", padx=5, pady=(5,0))
        Tooltip(prefill_system_instruction_label, "프리필 모드에서 사용할 시스템 레벨 지침입니다.")
        self.prefill_system_instruction_text = scrolledtext.ScrolledText(prefill_frame, wrap=tk.WORD, height=4, width=70)
        self.prefill_system_instruction_text.pack(fill="both", expand=True, padx=5, pady=5)

        prefill_cached_history_label = ttk.Label(prefill_frame, text="프리필 캐시된 히스토리 (JSON 형식):")
        prefill_cached_history_label.pack(anchor="w", padx=5, pady=(5,0))
        Tooltip(prefill_cached_history_label, "미리 정의된 대화 기록을 JSON 형식으로 입력합니다.\n예: [{\"role\": \"user\", \"parts\": [\"안녕\"]}, {\"role\": \"model\", \"parts\": [\"안녕하세요.\"]}]")
        self.prefill_cached_history_text = scrolledtext.ScrolledText(prefill_frame, wrap=tk.WORD, height=6, width=70)
        self.prefill_cached_history_text.pack(fill="both", expand=True, padx=5, pady=5)



        # 콘텐츠 안전 재시도 설정
        content_safety_outer_frame = ttk.LabelFrame(settings_frame, text="콘텐츠 안전 재시도 설정", padding="10")
        content_safety_outer_frame.pack(fill="x", padx=5, pady=5)

        self.content_safety_expanded_var = tk.BooleanVar(value=True)
        self.content_safety_toggle_button = ttk.Checkbutton(
            content_safety_outer_frame,
            variable=self.content_safety_expanded_var,
            command=self._toggle_content_safety_details
        )
        Tooltip(self.content_safety_toggle_button, "콘텐츠 안전 재시도 관련 세부 설정을 보거나 숨깁니다.")
        self.content_safety_toggle_button.grid(row=0, column=0, sticky="w", padx=5, pady=2)
        
        self.content_safety_details_frame = ttk.Frame(content_safety_outer_frame)
        self.content_safety_details_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=5, pady=5)

        self.use_content_safety_retry_var = tk.BooleanVar()
        self.use_content_safety_retry_check = ttk.Checkbutton(
            self.content_safety_details_frame, # Parent changed
            text="검열 오류시 청크 분할 재시도 사용",
            variable=self.use_content_safety_retry_var
        )
        Tooltip(self.use_content_safety_retry_check, "API에서 콘텐츠 안전 문제로 응답이 차단될 경우,\n텍스트를 더 작은 조각으로 나누어 재시도합니다.")
        self.use_content_safety_retry_check.grid(row=0, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        
        ttk.Label(self.content_safety_details_frame, text="최대 분할 시도:").grid(row=1, column=0, padx=5, pady=5, sticky="w") # Parent changed
        Tooltip(ttk.Label(self.content_safety_details_frame, text="최대 분할 시도:"), "콘텐츠 안전 문제 발생 시 청크를 나누어 재시도할 최대 횟수입니다.")
        self.max_split_attempts_entry = ttk.Entry(self.content_safety_details_frame, width=5) # Parent changed
        self.max_split_attempts_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.max_split_attempts_entry.insert(0, "3")
        Tooltip(self.max_split_attempts_entry, "최대 분할 시도 횟수를 입력하세요.")
        
        ttk.Label(self.content_safety_details_frame, text="최소 청크 크기:").grid(row=2, column=0, padx=5, pady=5, sticky="w") # Parent changed
        Tooltip(ttk.Label(self.content_safety_details_frame, text="최소 청크 크기:"), "분할 재시도 시 청크가 이 크기보다 작아지지 않도록 합니다.")
        self.min_chunk_size_entry = ttk.Entry(self.content_safety_details_frame, width=10) # Parent changed
        self.min_chunk_size_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        self.min_chunk_size_entry.insert(0, "100")
        Tooltip(self.min_chunk_size_entry, "최소 청크 크기를 입력하세요.")

        self._toggle_content_safety_details() # Set initial state and text

        # 동적 로어북 주입 설정
        dynamic_glossary_outer_frame = ttk.LabelFrame(settings_frame, text="동적 용어집 주입 설정", padding="10") # Text changed
        dynamic_glossary_outer_frame.pack(fill="x", padx=5, pady=5)

        self.dynamic_glossary_expanded_var = tk.BooleanVar(value=True) # Var name changed
        self.dynamic_glossary_toggle_button = ttk.Checkbutton( # Widget name changed
            dynamic_glossary_outer_frame,
            variable=self.dynamic_glossary_expanded_var, # Var name changed
            command=self._toggle_dynamic_glossary_details # Command changed
        )
        Tooltip(self.dynamic_glossary_toggle_button, "번역 시 동적으로 용어집 내용을 프롬프트에 주입하는 기능의 세부 설정을 보거나 숨깁니다.") # Text changed
        self.dynamic_glossary_toggle_button.grid(row=0, column=0, sticky="w", padx=5, pady=2)

        self.dynamic_glossary_details_frame = ttk.Frame(dynamic_glossary_outer_frame) # Widget name changed
        self.dynamic_glossary_details_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=5, pady=5)

        self.enable_dynamic_glossary_injection_var = tk.BooleanVar() # Var name changed
        self.enable_dynamic_glossary_injection_check = ttk.Checkbutton( # Widget name changed
            self.dynamic_glossary_details_frame, # Parent changed
            text="동적 용어집 주입 활성화", # Text changed
            variable=self.enable_dynamic_glossary_injection_var # Var name changed
        
        )
        Tooltip(self.enable_dynamic_glossary_injection_check, "번역 시 용어집 탭에서 설정된 용어집 JSON 파일의 내용을\n프롬프트에 동적으로 주입하여 번역 일관성을 높입니다.") # Text changed
        self.enable_dynamic_glossary_injection_check.grid(row=0, column=0, columnspan=3, padx=5, pady=2, sticky="w")

        ttk.Label(self.dynamic_glossary_details_frame, text="청크당 최대 주입 항목 수:").grid(row=1, column=0, padx=5, pady=5, sticky="w") # Parent changed
        Tooltip(ttk.Label(self.dynamic_glossary_details_frame, text="청크당 최대 주입 항목 수:"), "하나의 번역 청크에 주입될 용어집 항목의 최대 개수입니다.") # Text changed
        self.max_glossary_entries_injection_entry = ttk.Entry(self.dynamic_glossary_details_frame, width=5) # Widget name changed, Parent changed
        self.max_glossary_entries_injection_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        Tooltip(self.max_glossary_entries_injection_entry, "최대 주입 항목 수를 입력하세요.")

        ttk.Label(self.dynamic_glossary_details_frame, text="청크당 최대 주입 문자 수:").grid(row=2, column=0, padx=5, pady=5, sticky="w") # Parent changed
        Tooltip(ttk.Label(self.dynamic_glossary_details_frame, text="청크당 최대 주입 문자 수:"), "하나의 번역 청크에 주입될 용어집 내용의 최대 총 문자 수입니다.") # Text changed
        self.max_glossary_chars_injection_entry = ttk.Entry(self.dynamic_glossary_details_frame, width=10) # Widget name changed, Parent changed
        self.max_glossary_chars_injection_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        Tooltip(self.max_glossary_chars_injection_entry, "최대 주입 문자 수를 입력하세요.")

        self._toggle_dynamic_glossary_details() # Set initial state and text, function name changed



        # 주입용 로어북 JSON 경로 입력 필드는 "로어북 관리" 탭의 경로를 사용하므로 여기서는 제거합니다.
        # 액션 버튼들
        action_frame = ttk.Frame(settings_frame, padding="10")
        action_frame.pack(fill="x", padx=5, pady=5)
        
        self.save_settings_button = ttk.Button(action_frame, text="설정 저장", command=self._save_settings)
        self.save_settings_button.pack(side="left", padx=5)
        Tooltip(self.save_settings_button, "현재 UI에 입력된 모든 설정을 config.json 파일에 저장합니다.")
        
        self.load_settings_button = ttk.Button(action_frame, text="설정 불러오기", command=self._load_settings_ui)
        self.load_settings_button.pack(side="left", padx=5)
        Tooltip(self.load_settings_button, "config.json 파일에서 설정을 불러와 UI에 적용합니다.")
        
        self.start_button = ttk.Button(action_frame, text="번역 시작", command=self._start_translation_thread_with_resume_check)
        self.start_button.pack(side="right", padx=5)
        Tooltip(self.start_button, "현재 설정으로 입력 파일의 번역 작업을 시작합니다.")
        
        self.stop_button = ttk.Button(action_frame, text="중지", command=self._request_stop_translation, state=tk.DISABLED)
        self.stop_button.pack(side="right", padx=5)
        Tooltip(self.stop_button, "현재 진행 중인 번역 작업을 중지 요청합니다.")
        
        # 진행률 표시
        progress_frame = ttk.Frame(settings_frame)
        progress_frame.pack(fill="x", padx=15, pady=10)
        
        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.progress_bar.pack(fill="x", pady=5)
        Tooltip(self.progress_bar, "번역 작업의 전체 진행률을 표시합니다.")
        
        self.progress_label = ttk.Label(progress_frame, text="대기 중...")
        self.progress_label.pack(pady=2)
        Tooltip(self.progress_label, "번역 작업의 현재 상태 및 진행 상황을 텍스트로 표시합니다.")

    def _toggle_content_safety_details(self):
        if self.content_safety_expanded_var.get():
            self.content_safety_details_frame.grid()
            self.content_safety_toggle_button.config(text="▲ 세부 설정 숨기기")
        else:
            self.content_safety_details_frame.grid_remove()
            self.content_safety_toggle_button.config(text="▼ 세부 설정 보기")

    def _toggle_dynamic_glossary_details(self): # Function name changed
        if self.dynamic_glossary_expanded_var.get(): # Var name changed
            self.dynamic_glossary_details_frame.grid() # Widget name changed
            self.dynamic_glossary_toggle_button.config(text="▲ 세부 설정 숨기기") # Widget name changed
        
        else:
            self.dynamic_glossary_details_frame.grid_remove() # Widget name changed
            self.dynamic_glossary_toggle_button.config(text="▼ 세부 설정 보기") # Widget name changed


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


    def _create_glossary_widgets(self): # Renamed from _create_lorebook_widgets
        # 스크롤 가능한 프레임의 내부 프레임 사용
        glossary_frame = self.glossary_scroll.scrollable_frame # Renamed

        
        # 로어북 JSON 파일 설정
        path_frame = ttk.LabelFrame(glossary_frame, text="용어집 JSON 파일", padding="10") # Text changed
        
        path_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(path_frame, text="JSON 파일 경로:").grid(row=0, column=0, padx=5, pady=5, sticky="w") # Text changed
        self.glossary_json_path_entry = ttk.Entry(path_frame, width=50) # Renamed
        self.glossary_json_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.glossary_json_path_entry, "사용할 용어집 JSON 파일의 경로입니다.\n추출 기능을 사용하면 자동으로 채워지거나, 직접 입력/선택할 수 있습니다.") # Text changed
        self.browse_glossary_json_button = ttk.Button(path_frame, text="찾아보기", command=self._browse_glossary_json) # Renamed
        self.browse_glossary_json_button.grid(row=0, column=2, padx=5, pady=5)
        

        extract_button = ttk.Button(path_frame, text="선택한 입력 파일에서 용어집 추출", command=self._extract_glossary_thread) # Text and command changed
        extract_button.grid(row=2, column=0, columnspan=3, padx=5, pady=10)
        Tooltip(extract_button, "'설정 및 번역' 탭에서 선택된 입력 파일을 분석하여 용어집을 추출하고, 그 결과를 아래 텍스트 영역에 표시합니다.") # Text changed
        
        self.glossary_progress_label = ttk.Label(path_frame, text="용어집 추출 대기 중...") # Renamed
        self.glossary_progress_label.grid(row=3, column=0, columnspan=3, padx=5, pady=2)
        Tooltip(self.glossary_progress_label, "용어집 추출 작업의 진행 상태를 표시합니다.") # Text changed


        # 용어집 추출 설정 프레임 (경량화)
        extraction_settings_frame = ttk.LabelFrame(glossary_frame, text="용어집 추출 설정", padding="10") # Text changed
        
        extraction_settings_frame.pack(fill="x", padx=5, pady=5)
        
        # 샘플링 비율 설정 (lorebook_sampling_ratio)
        ttk.Label(extraction_settings_frame, text="샘플링 비율 (%):").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(ttk.Label(extraction_settings_frame, text="샘플링 비율 (%):"), "용어집 추출 시 전체 텍스트 중 분석할 비율입니다.\n100%로 설정하면 전체 텍스트를 분석합니다.") # Text changed
        

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
        Tooltip(self.sample_ratio_scale, "용어집 추출 샘플링 비율을 조절합니다 (5.0% ~ 100.0%).") # Text changed
        

        self.sample_ratio_label = ttk.Label(sample_ratio_frame, text="25.0%", width=8)
        self.sample_ratio_label.pack(side="left")
        Tooltip(self.sample_ratio_label, "현재 설정된 샘플링 비율입니다.")
        
        # 제거된 UI 요소들:
        # - 세그먼트 당 최대 항목 수 (max_entries_per_segment_spinbox, max_entries_per_segment_label)
        # - 샘플링 방식 (glossary_sampling_method_combobox)
        # - 항목 당 최대 글자 수 (glossary_max_chars_entry)
        # - 키워드 민감도 (glossary_keyword_sensitivity_combobox)
        # - 용어집 세그먼트 크기 (glossary_chunk_size_entry)
        # - 우선순위 설정 (glossary_priority_text)
        # 이들은 SimpleGlossaryService에서 직접 사용하지 않으므로 UI에서 제거.
        

        
        # 고급 설정 (접을 수 있는 형태)
        self.advanced_var = tk.BooleanVar()
        advanced_check = ttk.Checkbutton(
            extraction_settings_frame, 
            text="고급 설정 표시", 
            variable=self.advanced_var,
            command=self._toggle_advanced_settings
        )
        Tooltip(advanced_check, "용어집 추출에 사용될 추출 온도 설정을 표시하거나 숨깁니다.") # Text changed              
        advanced_check.grid(row=4, column=0, columnspan=3, padx=5, pady=(15,5), sticky="w")
        
        # 고급 설정 프레임 (초기에는 숨김)
        self.advanced_frame = ttk.Frame(extraction_settings_frame)
        self.advanced_frame.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        
        # 온도 설정 (용어집 추출용)        
        ttk.Label(self.advanced_frame, text="추출 온도:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(ttk.Label(self.advanced_frame, text="추출 온도:"), "용어집 추출 시 모델 응답의 무작위성입니다.\n낮을수록 일관적, 높을수록 다양하지만 덜 정확할 수 있습니다.") # Text changed
    
        self.extraction_temp_scale = ttk.Scale(
            self.advanced_frame,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            length=150,
            command=self._update_extraction_temp_label
        )
        self.extraction_temp_scale.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.extraction_temp_scale, "용어집 추출 온도를 조절합니다 (0.0 ~ 1.0).") # Text changed
        
        self.extraction_temp_scale.set(0.3)  # 경량화된 서비스 기본값               
        self.extraction_temp_label = ttk.Label(self.advanced_frame, text="0.20", width=6)
        self.extraction_temp_label.grid(row=0, column=2, padx=5, pady=5)
        Tooltip(self.extraction_temp_label, "현재 설정된 용어집 추출 온도입니다.") # Text changed
        

        
        # 초기에는 고급 설정 숨김
        self.advanced_frame.grid_remove()

        # 액션 버튼 프레임 추가
        glossary_action_frame = ttk.Frame(glossary_frame, padding="10") # Renamed
        glossary_action_frame.pack(fill="x", padx=5, pady=5)
        

        # 설정 저장 버튼
        self.save_glossary_settings_button = ttk.Button( # Renamed
            glossary_action_frame,
            text="용어집 설정 저장", # Text changed
            command=self._save_glossary_settings # Command changed
        )
        Tooltip(self.save_glossary_settings_button, "현재 용어집 탭의 설정을 config.json 파일에 저장합니다.") # Text changed
        self.save_glossary_settings_button.pack(side="left", padx=5)
        

        # 설정 초기화 버튼
        self.reset_glossary_settings_button = ttk.Button( # Renamed
            glossary_action_frame, 
            text="기본값으로 초기화", 
            command=self._reset_glossary_settings # Command changed
        )
        Tooltip(self.reset_glossary_settings_button, "용어집 탭의 모든 설정을 프로그램 기본값으로 되돌립니다.") # Text changed
        self.reset_glossary_settings_button.pack(side="left", padx=5)
        

        
        # 실시간 미리보기 버튼
        self.preview_glossary_settings_button = ttk.Button( # Renamed
            glossary_action_frame,
            text="설정 미리보기", 
            command=self._preview_glossary_settings # Command changed
        )
        Tooltip(self.preview_glossary_settings_button, "현재 용어집 설정이 실제 추출에 미칠 영향을 간략하게 미리봅니다.") # Text changed
        self.preview_glossary_settings_button.pack(side="right", padx=5)



        # 상태 표시 레이블
        self.glossary_status_label = ttk.Label( # Renamed
            glossary_action_frame,

            font=("Arial", 9),
            foreground="gray"
        )
        Tooltip(self.glossary_status_label, "용어집 설정 변경 및 저장 상태를 표시합니다.") # Text changed
        self.glossary_status_label.pack(side="bottom", pady=5)



        

        # Lorebook Display Area
        glossary_display_frame = ttk.LabelFrame(glossary_frame, text="추출된 용어집 (JSON)", padding="10") # Text changed
        glossary_display_frame.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(glossary_display_frame, "추출되거나 불러온 용어집의 내용이 JSON 형식으로 표시됩니다.") # Text changed

        self.glossary_display_text = scrolledtext.ScrolledText(glossary_display_frame, wrap=tk.WORD, height=10, width=70) # Widget name changed
        self.glossary_display_text.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(self.glossary_display_text, "용어집 내용입니다. 직접 편집은 불가능하며, 'JSON 저장'으로 파일 저장 후 수정할 수 있습니다.") # Text changed

        glossary_display_buttons_frame = ttk.Frame(glossary_display_frame) # Widget name changed
        glossary_display_buttons_frame.pack(fill="x", pady=5)

        self.load_glossary_button = ttk.Button(glossary_display_buttons_frame, text="용어집 불러오기", command=self._load_glossary_to_display) # Widget name, text, command changed
        self.load_glossary_button.pack(side="left", padx=5)
        Tooltip(self.load_glossary_button, "기존 용어집 JSON 파일을 불러와 아래 텍스트 영역에 표시합니다.") # Text changed

        self.copy_glossary_button = ttk.Button(glossary_display_buttons_frame, text="JSON 복사", command=self._copy_glossary_json) # Widget name, command changed
        self.copy_glossary_button.pack(side="left", padx=5)
        Tooltip(self.copy_glossary_button, "아래 텍스트 영역에 표시된 용어집 JSON 내용을 클립보드에 복사합니다.") # Text changed

        self.save_displayed_glossary_button = ttk.Button(glossary_display_buttons_frame, text="JSON 저장", command=self._save_displayed_glossary_json) # Widget name, command changed
        self.save_displayed_glossary_button.pack(side="left", padx=5)
        Tooltip(self.save_displayed_glossary_button, "아래 텍스트 영역에 표시된 용어집 JSON 내용을 새 파일로 저장합니다.") # Text changed

        self.edit_glossary_button = ttk.Button(glossary_display_buttons_frame, text="용어집 편집", command=self._open_glossary_editor) # Widget name, text, command changed
        self.edit_glossary_button.pack(side="left", padx=5)
        Tooltip(self.edit_glossary_button, "표시된 용어집 내용을 별도의 편집기 창에서 수정합니다.") # Text changed




        # 설정 변경 감지 이벤트 바인딩
        self.sample_ratio_scale.bind("<ButtonRelease-1>", self._on_glossary_setting_changed) # Changed
        self.extraction_temp_scale.bind("<ButtonRelease-1>", self._on_glossary_setting_changed) # Changed
        
        # 제거된 UI 요소에 대한 바인딩도 제거


    def _create_log_widgets(self):
        self.log_text = scrolledtext.ScrolledText(self.log_tab, wrap=tk.WORD, state=tk.DISABLED, height=20)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(self.log_text, "애플리케이션의 주요 동작 및 오류 로그가 표시됩니다.")
        
        gui_log_handler = TextHandler(self.log_text)
        # Use the global logger instance
        logger.addHandler(gui_log_handler)
        logger.setLevel(logging.INFO) # logging.INFO should be recognized

        self.tqdm_stream = TqdmToTkinter(self.log_text)

    def _log_message(self, message: str, level: str = "INFO", exc_info=False):
        gui_specific_logger = logging.getLogger(__name__ + "_gui")
        if level.upper() == "INFO": gui_specific_logger.info(message, exc_info=exc_info)
        elif level.upper() == "WARNING": gui_specific_logger.warning(message, exc_info=exc_info)
        elif level.upper() == "ERROR": gui_specific_logger.error(message, exc_info=exc_info) # type: ignore
        elif level.upper() == "DEBUG": gui_specific_logger.debug(message, exc_info=exc_info) # type: ignore
        else: gui_specific_logger.info(message, exc_info=exc_info) # type: ignore

    def _update_model_list_ui(self):
        app_service = self.app_service
        if not app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            self._log_message("모델 목록 업데이트 시도 실패: AppService 없음", "ERROR")
            return

        # 변수 스코프 문제 해결: try 블록 밖에서 정의
        current_user_input_model = self.model_name_combobox.get()
        
        try:
            self._log_message("모델 목록 새로고침 중...")
            
            # 1단계: 클라이언트 유무 확인
            if not app_service.gemini_client:
                # 클라이언트가 없다면, 설정을 저장하지 않고 사용자에게 알림.
                # AppService의 load_app_config를 호출하여 (저장 없이) 클라이언트 재설정을 시도할 수 있으나,
                # 여기서는 단순히 사용자에게 알리고 모델 목록 조회를 중단하는 것이 안전합니다.
                # load_app_config는 이미 AppService 초기화 시 또는 설정 저장/불러오기 시 호출됩니다.
                self._log_message(
                    "모델 목록 업데이트: Gemini 클라이언트가 초기화되지 않았습니다. "
                    "API 키 또는 Vertex AI 설정을 확인하고 '설정 저장' 후 다시 시도해주세요.", "WARNING"
                )
                messagebox.showwarning("인증 필요", 
                                       "모델 목록을 가져오려면 API 키 또는 Vertex AI 설정이 유효해야 합니다.\n"
                                       "설정을 확인하고 '설정 저장' 버튼을 누른 후 다시 시도해주세요.")
                self._reset_model_combobox(current_user_input_model)
                return
            
            # 3단계: 모델 목록 조회 (한 번만 호출)
            models_data = app_service.get_available_models()
            
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
            app_service_instance = self.app_service
            suggested_output = p.parent / f"{p.stem}_translated{p.suffix}" # type: ignore
            self.output_file_entry.delete(0, tk.END) # type: ignore
            
            self.output_file_entry.insert(0, str(suggested_output))
            suggested_glossary_json = p.parent / f"{p.stem}{self.app_service.config.get('glossary_output_json_filename_suffix', '_glossary.json') if self.app_service else '_glossary.json'}" # Key changed
            self.glossary_json_path_entry.delete(0, tk.END) # Changed
            self.glossary_json_path_entry.insert(0, str(suggested_glossary_json))



    def _browse_output_file(self):
        filepath = filedialog.asksaveasfilename(title="출력 파일 선택", defaultextension=".txt", filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*")))
        if filepath:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, filepath)

    def _browse_glossary_json(self): # Renamed
        
        initial_dir = ""
        input_file_path = self.input_file_entry.get()
        if input_file_path and Path(input_file_path).exists():
            initial_dir = str(Path(input_file_path).parent)
        
        filepath = filedialog.askopenfilename(
           title="용어집 JSON 파일 선택",  # Text changed
            
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*")), # Type changed
            initialdir=initial_dir
            )
        if filepath:
            self.glossary_json_path_entry.delete(0, tk.END) # Changed
            self.glossary_json_path_entry.insert(0, filepath)



    def _get_config_from_ui(self) -> Dict[str, Any]:
        prompt_content = self.prompt_text.get("1.0", tk.END).strip()
        prefill_system_instruction_content = self.prefill_system_instruction_text.get("1.0", tk.END).strip()
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

        prefill_cached_history_json_str = self.prefill_cached_history_text.get("1.0", tk.END).strip()
        prefill_cached_history_obj = []
        if prefill_cached_history_json_str:
            try:
                prefill_cached_history_obj = json.loads(prefill_cached_history_json_str)
                if not isinstance(prefill_cached_history_obj, list):
                    messagebox.showwarning("입력 오류", "프리필 캐시된 히스토리는 JSON 배열이어야 합니다. 기본값 []으로 설정됩니다.")
                    prefill_cached_history_obj = []
            except json.JSONDecodeError:
                messagebox.showwarning("입력 오류", "프리필 캐시된 히스토리 형식이 잘못되었습니다 (JSON 파싱 실패). 기본값 []으로 설정됩니다.")
                prefill_cached_history_obj = []

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
            "enable_prefill_translation": self.enable_prefill_var.get(),
            "prefill_system_instruction": prefill_system_instruction_content,
            "prefill_cached_history": prefill_cached_history_obj,            
            "novel_language": self.novel_language_entry.get().strip() or "auto",
            "novel_language_fallback": self.novel_language_fallback_entry.get().strip() or "ja",
            # Lorebook settings
            "glossary_json_path": self.glossary_json_path_entry.get().strip() or None, # Key and widget name changed
            "glossary_sampling_ratio": self.sample_ratio_scale.get(), 
            "glossary_extraction_temperature": self.extraction_temp_scale.get(), # Key changed
                
                # Dynamic lorebook injection settings
                "enable_dynamic_glossary_injection": self.enable_dynamic_glossary_injection_var.get(), # Key and var name changed
                "max_glossary_entries_per_chunk_injection": int(self.max_glossary_entries_injection_entry.get() or "3"), # Key and widget name changed
                "max_glossary_chars_per_chunk_injection": int(self.max_glossary_chars_injection_entry.get() or "500"), # Key and widget name changed
                
                # lorebook_json_path_for_injection 은 lorebook_json_path 로 통합되었으므로 여기서 제거
            "use_content_safety_retry": self.use_content_safety_retry_var.get(), # type: ignore
            "max_content_safety_split_attempts": int(self.max_split_attempts_entry.get() or "3"), # type: ignore
            "min_content_safety_chunk_size": int(self.min_chunk_size_entry.get() or "100"), # type: ignore
        }
        
        return config_data

    def _save_settings(self):
        app_service = self.app_service
        if not app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        try:
            current_config = app_service.config.copy()
            ui_config = self._get_config_from_ui()
            current_config.update(ui_config)
            app_service.save_app_config(current_config)
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
        app_service = self.app_service
        if not app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        try:
            app_service.load_app_config()
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
        app_service = self.app_service
        if not app_service:
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
        config_for_hash_check = app_service.config.copy()
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
        app_service = self.app_service
        if not app_service: return

        input_file = self.input_file_entry.get()
        output_file = self.output_file_entry.get()
        
        try:
            current_ui_config = self._get_config_from_ui()
            app_service.config.update(current_ui_config)
            app_service.load_app_config()


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
            target=app_service.start_translation,
            args=(input_file, output_file,
                  self._update_translation_progress,
                  self._update_translation_status,
                  self.tqdm_stream),
            daemon=True
        )
        thread.start()

    def _request_stop_translation(self):
        app_service = self.app_service
        if not app_service: return
        if app_service.is_translation_running:
            app_service.request_stop_translation()
            self._log_message("번역 중지 요청됨.")
        else:
            self._log_message("실행 중인 번역 작업이 없습니다.")

    def _update_glossary_extraction_progress(self, dto: GlossaryExtractionProgressDTO): # Renamed and DTO changed
        
        def _update():
            if not self.master.winfo_exists(): return
            msg = f"{dto.current_status_message} ({dto.processed_segments}/{dto.total_segments}, 추출 항목: {dto.extracted_entries_count})" # DTO fields changed
            self.glossary_progress_label.config(text=msg) # Changed
        
        if self.master.winfo_exists():
            self.master.after(0, _update)

    def _extract_glossary_thread(self): # Renamed
        
        app_service = self.app_service
        if not app_service:
            messagebox.showerror("오류", "애플리케이션 서비스가 초기화되지 않았습니다.")
            return

        input_file = self.input_file_entry.get() # This should be the source novel file
        if not input_file:
            messagebox.showwarning("경고", "용어집을 추출할 입력 파일을 먼저 선택해주세요.")          
            return
        if not Path(input_file).exists():
            messagebox.showerror("오류", f"입력 파일을 찾을 수 없습니다: {input_file}")
            return

        try:
            current_ui_config = self._get_config_from_ui()
            app_service.config.update(current_ui_config)
            app_service.load_app_config()

            if not app_service.gemini_client:
                 if not messagebox.askyesno("API 설정 경고", "API 클라이언트가 초기화되지 않았습니다. 계속 진행하시겠습니까?"):
                    return
                 
            # type: ignore

        except ValueError as ve:
             messagebox.showerror("입력 오류", f"설정값 오류: {ve}")
             return
        except Exception as e:
            messagebox.showerror("오류", f"용어집 추출 시작 전 설정 오류: {e}") # Text changed
            self._log_message(f"용어집 추출 시작 전 설정 오류: {e}", "ERROR", exc_info=True) # Text changed
            return

        self.glossary_progress_label.config(text="용어집 추출 시작 중...") # Changed
        self._log_message(f"용어집 추출 시작: {input_file}") # Text changed
        

        
        # GUI에서 직접 소설 언어를 입력받는 UI가 제거되었으므로, 항상 None을 전달하여 AppService가 설정을 따르도록 합니다.
        novel_lang_for_extraction = None
        def _extraction_task_wrapper():
            try:
                if app_service:
                    result_json_path = app_service.extract_glossary(
                        input_file,
                        progress_callback=self._update_glossary_extraction_progress, # Callback changed                      
                        seed_glossary_path=app_service.config.get("glossary_json_path") # Use current glossary as seed
                    )
                    self.master.after(0, lambda: messagebox.showinfo("성공", f"용어집 추출 완료!\n결과 파일: {result_json_path}")) # Text changed
                    self.master.after(0, lambda: self.glossary_progress_label.config(text=f"추출 완료: {result_json_path.name}")) # Changed
                    self.master.after(0, lambda: self._update_glossary_json_path_entry(str(result_json_path))) # Changed
                    # Load result to display
                    if result_json_path and result_json_path.exists(): # Check if result_json_path is not None
                        with open(result_json_path, 'r', encoding='utf-8') as f_res:
                            lore_content = f_res.read()
                        self.master.after(0, lambda: self._display_glossary_content(lore_content)) # Function name changed
            
            # BtgPronounException replaced with BtgBusinessLogicException as SimpleGlossaryService might throw more general business logic errors
            except (BtgFileHandlerException, BtgApiClientException, BtgServiceException, BtgBusinessLogicException) as e_btg:
                logger.error(f"용어집 추출 중 BTG 예외 발생: {e_btg}", exc_info=True) # Text changed
                self.master.after(0, lambda: messagebox.showerror("추출 오류", f"용어집 추출 중 오류: {e_btg}")) # Text changed
                self.master.after(0, lambda: self.glossary_progress_label.config(text="오류 발생")) # Changed
            except Exception as e_unknown: 
                logger.error(f"용어집 추출 중 알 수 없는 예외 발생: {e_unknown}", exc_info=True) # Text changed
                self.master.after(0, lambda: messagebox.showerror("알 수 없는 오류", f"용어집 추출 중 예상치 못한 오류: {e_unknown}")) # Text changed
                self.master.after(0, lambda: self.glossary_progress_label.config(text="알 수 없는 오류 발생")) # Changed
            finally:
                self._log_message("용어집 추출 스레드 종료.") # Text changed

        thread = threading.Thread(target=_extraction_task_wrapper, daemon=True)
        thread.start()

    def _update_glossary_json_path_entry(self, path_str: str): # Renamed
        self.glossary_json_path_entry.delete(0, tk.END) # Changed
        self.glossary_json_path_entry.insert(0, path_str)
        
        if self.app_service:
            self.app_service.config["glossary_json_path"] = path_str # type: ignore # Key changed



    def _on_closing(self):
        app_service = self.app_service
        if app_service and app_service.is_translation_running:
            if messagebox.askokcancel("종료 확인", "번역 작업이 진행 중입니다. 정말로 종료하시겠습니까?"):
                app_service.request_stop_translation()
                logger.info("사용자 종료 요청으로 번역 중단 시도.")
                self.master.destroy()
        else:
            self.master.destroy()

    def _on_api_key_changed(self, event=None):
        # type: ignore
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
        app_service = self.app_service
        config_model_name = app_service.config.get("model_name", "") if app_service else "" # type: ignore
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
            
            # 기존 레이블이 있다면 업데이트, 없다면 생성
            
            # if hasattr(self, 'sampling_estimate_label'):
            #     self.sampling_estimate_label.config(text=estimate_text)
            
            
        except Exception:
            pass  # 추정 실패 시 무시

    def _save_glossary_settings(self): # Renamed
        
        """로어북 관련 설정만 저장"""
        app_service = self.app_service
        if not app_service: # type: ignore

            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        
        try:
            # 현재 전체 설정 가져오기
            current_config = app_service.config.copy()
            
            # 고유명사 관련 설정만 업데이트
            glossary_config = self._get_glossary_config_from_ui()
            current_config.update(glossary_config)
            
            # type: ignore
            # 설정 저장
            # AppService의 save_app_config가 load_app_config를 호출하므로, UI 업데이트는 거기서 처리될 수 있음
            if self.app_service.save_app_config(current_config): # type: ignore
                messagebox.showinfo("성공", "용어집 설정이 저장되었습니다.")
                self._log_message("용어집 설정 저장 완료.")
                self._update_glossary_status_label("✅ 설정 저장됨")
            else:
                messagebox.showerror("오류", "용어집 설정 저장에 실패했습니다.")
                
                
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 중 오류: {e}")
            self._log_message(f"용어집 설정 저장 오류: {e}", "ERROR") # Text changed

    def _get_glossary_config_from_ui(self) -> Dict[str, Any]: # Renamed        
        """UI에서 로어북 관련 설정만 추출"""
        app_service = self.app_service
        if not app_service:
            logger.error("AppService not initialized in _get_glossary_config_from_ui") # Text changed
            
            return {}
        try:
            config = {
                "glossary_json_path": self.glossary_json_path_entry.get().strip() or None, # Key and widget name changed
                "glossary_sampling_ratio": self.sample_ratio_scale.get(),
                "glossary_extraction_temperature": self.extraction_temp_scale.get(),                     
                # Dynamic lorebook injection settings
                "enable_dynamic_glossary_injection": self.enable_dynamic_glossary_injection_var.get(), # Key and var name changed
                "max_glossary_entries_per_chunk_injection": int(self.max_glossary_entries_injection_entry.get() or "3"), # Key and widget name changed
                "max_glossary_chars_per_chunk_injection": int(self.max_glossary_chars_injection_entry.get() or "500") # Key and widget name changed
            }
            
            # 제거된 UI 요소에 대한 설정 추출 로직도 제거
            # 예: glossary_max_entries_per_segment, glossary_sampling_method 등


            return {k: v for k, v in config.items() if v is not None}
        except Exception as e:
            raise ValueError(f"용어집 설정 값 오류: {e}") # Text changed

    def _reset_glossary_settings(self): # Renamed      
        """로어북 설정을 기본값으로 초기화"""
        app_service = self.app_service
        if not app_service or not app_service.config_manager:
            messagebox.showerror("오류", "AppService 또는 ConfigManager가 초기화되지 않았습니다.")
            return # type: ignore
        
        result = messagebox.askyesno( # type: ignor            
            "설정 초기화", 
            "용어집 설정을 기본값으로 초기화하시겠습니까?" # Text changed     
        )
        
        if result:
            try:
                # 기본값 로드
                default_config = app_service.config_manager.get_default_config()
                # UI에 기본값 적용
                self.sample_ratio_scale.set(default_config.get("glossary_sampling_ratio", 10.0))
                self.extraction_temp_scale.set(default_config.get("glossary_extraction_temperature", 0.3))
                # 제거된 UI 요소에 대한 초기화 로직도 제거
                

                # 레이블 업데이트
                self._update_sample_ratio_label(str(self.sample_ratio_scale.get()))
                self._update_extraction_temp_label(str(self.extraction_temp_scale.get()))
                
                self._update_glossary_status_label("🔄 기본값으로 초기화됨") # Changed
                self._log_message("용어집 설정이 기본값으로 초기화되었습니다.") # Text changed
                              
            except Exception as e:
                messagebox.showerror("오류", f"기본값 로드 중 오류: {e}")

    def _preview_glossary_settings(self): # Renamed        
        """현재 설정의 예상 효과 미리보기"""
        try:
            input_file = self.input_file_entry.get()
            if not input_file or not Path(input_file).exists():
                messagebox.showwarning("파일 없음", "입력 파일을 선택해주세요.")
                return
            
            # type: ignore
            # 현재 설정 값들
            sample_ratio = self.sample_ratio_scale.get()
            extraction_temp = self.extraction_temp_scale.get() # This is lorebook_extraction_temperature
            
            # 파일 크기 기반 추정
            file_size = Path(input_file).stat().st_size
            chunk_size = int(self.chunk_size_entry.get() or "6000")
            estimated_chunks = max(1, file_size // chunk_size)
            estimated_sample_chunks = max(1, int(estimated_chunks * sample_ratio / 100.0))
            
            # 미리보기 정보 표시
            preview_msg = (
                f"📊 용어집 추출 설정 미리보기\n\n" # Text changed               
                f"📁 입력 파일: {Path(input_file).name}\n"
                f"📏 파일 크기: {file_size:,} 바이트\n"
                f"🧩 예상 청크 수: {estimated_chunks:,}개\n"
                f"🎯 분석할 샘플: {estimated_sample_chunks:,}개 ({sample_ratio:.1f}%)\n"
                f"🌡️ 추출 온도: {extraction_temp:.2f}\n\n"
                f"⏱️ 예상 처리 시간: {estimated_sample_chunks * 2:.0f}~{estimated_sample_chunks * 5:.0f}초"
            )
            
            messagebox.showinfo("설정 미리보기", preview_msg)
        except Exception as e:
            messagebox.showerror("오류", f"미리보기 생성 중 오류: {e}")

    def _update_glossary_status_label(self, message: str): # Renamed       
        """로어북 설정 상태 업데이트"""
        if hasattr(self, 'glossary_status_label'): # Changed
            self.glossary_status_label.config(text=message) # Changed
                       
            # 3초 후 기본 메시지로 복귀
            self.master.after(3000, lambda: self.glossary_status_label.config( # Changed             
                text="⏸️ 설정 변경 대기 중..."
            ))

    def _on_glossary_setting_changed(self, event=None): # Renamed      
        """로어북 설정이 변경될 때 호출"""
        self._update_glossary_status_label("⚠️ 설정이 변경됨 (저장 필요)") # Changed
        
        # 저장 버튼 강조
        if hasattr(self, 'save_glossary_settings_button'): # Changed
            self.save_glossary_settings_button.config(style="Accent.TButton") # Changed

    def _display_glossary_content(self, content: str): # Renamed
        self.glossary_display_text.config(state=tk.NORMAL) # Widget name changed
        self.glossary_display_text.delete('1.0', tk.END)
        self.glossary_display_text.insert('1.0', content)
        self.glossary_display_text.config(state=tk.DISABLED)

    def _load_glossary_to_display(self): # Renamed
        filepath = filedialog.askopenfilename(title="용어집 JSON 파일 선택", filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*"))) # Text changed
        
        if filepath:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._display_glossary_content(content) # Function name changed
                self.glossary_json_path_entry.delete(0, tk.END) # Widget name changed
                self.glossary_json_path_entry.insert(0, filepath)
                self._log_message(f"용어집 파일 로드됨: {filepath}") # Text changed
            
            except Exception as e:
                messagebox.showerror("오류", f"용어집 파일 로드 실패: {e}") # Text changed
                self._log_message(f"용어집 파일 로드 실패: {e}", "ERROR") # Text changed

    def _copy_glossary_json(self): # Renamed
        content = self.glossary_display_text.get('1.0', tk.END).strip() # Widget name changed      
        if content:
            self.master.clipboard_clear()
            self.master.clipboard_append(content)
            messagebox.showinfo("성공", "용어집 JSON 내용이 클립보드에 복사되었습니다.") # Text changed
            self._log_message("용어집 JSON 클립보드에 복사됨.") # Text changed        
        else:
            messagebox.showwarning("경고", "복사할 내용이 없습니다.")

    def _save_displayed_glossary_json(self): # Renamed
        content = self.glossary_display_text.get('1.0', tk.END).strip() # Widget name changed       
        if not content:
            messagebox.showwarning("경고", "저장할 내용이 없습니다.")
            return
        
        filepath = filedialog.asksaveasfilename(title="용어집 JSON으로 저장", defaultextension=".json", filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*"))) # Text changed    
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                messagebox.showinfo("성공", f"로어북이 성공적으로 저장되었습니다: {filepath}")
                self._log_message(f"표시된 로어북 저장됨: {filepath}")
            except Exception as e:
                messagebox.showerror("오류", f"용어집 저장 실패: {e}") # Text changed
                self._log_message(f"표시된 용어집 저장 실패: {e}", "ERROR") # Text changed

    def _open_glossary_editor(self): # Renamed
        current_json_str = self.glossary_display_text.get('1.0', tk.END).strip() # Widget name changed       
        if not current_json_str:
            if not messagebox.askyesno("용어집 비어있음", "표시된 용어집 내용이 없습니다. 새 용어집을 만드시겠습니까?"): # Text changed
                return
            current_json_str = "[]" # 새 용어집을 위한 빈 리스트

        try:
            # JSON 유효성 검사
            json.loads(current_json_str)
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON 오류", f"용어집 내용이 유효한 JSON 형식이 아닙니다: {e}") # Text changed           
            return

        editor_window = GlossaryEditorWindow(self.master, current_json_str, self._handle_glossary_editor_save) # Class and callback changed       
        editor_window.grab_set() # Modal-like behavior

    def _handle_glossary_editor_save(self, updated_json_str: str): # Renamed
        self._display_glossary_content(updated_json_str) # Function name changed
        self._log_message("용어집 편집기에서 변경 사항이 적용되었습니다.") # Text changed      
        # Optionally, ask user if they want to save to the file now
        if messagebox.askyesno("파일 저장 확인", "편집된 용어집을 현재 설정된 JSON 파일 경로에 저장하시겠습니까?"): # Text changed
            glossary_file_path = self.glossary_json_path_entry.get() # Widget name changed
            if glossary_file_path:
                try:
                    with open(glossary_file_path, 'w', encoding='utf-8') as f:
                        
                        f.write(updated_json_str)
                    messagebox.showinfo("저장 완료", f"용어집이 '{glossary_file_path}'에 저장되었습니다.") # Text changed
                    self._log_message(f"편집된 용어집 파일 저장됨: {glossary_file_path}") # Text changed
                
                except Exception as e:
                    messagebox.showerror("파일 저장 오류", f"용어집 파일 저장 실패: {e}") # Text changed
                    self._log_message(f"편집된 용어집 파일 저장 실패: {e}", "ERROR") # Text changed
            
            else:
                messagebox.showwarning("경로 없음", "용어집 JSON 파일 경로가 설정되지 않았습니다. 'JSON 저장' 버튼을 사용하거나 경로를 설정해주세요.") # Text changed


class GlossaryEditorWindow(tk.Toplevel): # Class name changed
    def __init__(self, master, glossary_json_str: str, save_callback: Callable[[str], None]): # Parameter name changed      
        super().__init__(master)
        self.title("용어집 편집기") # Text changed
        self.geometry("800x600")
        self.save_callback = save_callback

        try:
            self.glossary_data: List[Dict[str, Any]] = json.loads(glossary_json_str) # Var name changed
            if not isinstance(self.glossary_data, list): # Ensure it's a list
                raise ValueError("Glossary data must be a list of entries.") # Text changed      
        except (json.JSONDecodeError, ValueError) as e:
            messagebox.showerror("데이터 오류", f"용어집 데이터를 불러오는 중 오류 발생: {e}", parent=self) # Text changed
            self.glossary_data = [] # Fallback to empty list, var name changed

        self.current_selection_index: Optional[int] = None

        # Main frame
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left: Listbox for keywords
        listbox_frame = ttk.Frame(main_frame)
        listbox_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        self.listbox_scrollbar = ttk.Scrollbar(listbox_frame, orient=tk.VERTICAL)
        self.listbox_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox = tk.Listbox(listbox_frame, width=30, height=20, exportselection=False, yscrollcommand=self.listbox_scrollbar.set)
        self.listbox.pack(side=tk.TOP, fill=tk.Y, expand=True)
        self.listbox_scrollbar.config(command=self.listbox.yview)

        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        listbox_buttons_frame = ttk.Frame(listbox_frame)
        listbox_buttons_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5,0))
        ttk.Button(listbox_buttons_frame, text="새 항목", command=self._add_new_entry).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(listbox_buttons_frame, text="항목 삭제", command=self._delete_selected_entry).pack(side=tk.LEFT, expand=True, fill=tk.X)


        # Right: Entry fields for selected item
        self.entry_fields_frame = ttk.Frame(main_frame)
        self.entry_fields_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        fields = {
            "keyword": {"label": "키워드:", "widget": ttk.Entry, "height": 1},
            "translated_keyword": {"label": "번역된 키워드:", "widget": ttk.Entry, "height": 1},
            "source_language": {"label": "출발 언어 (BCP-47):", "widget": ttk.Entry, "height": 1},
            "target_language": {"label": "도착 언어 (BCP-47):", "widget": ttk.Entry, "height": 1},
            "occurrence_count": {"label": "등장 횟수:", "widget": ttk.Spinbox, "height": 1, "extra_args": {"from_": 0, "to": 9999}},
        }

        self.entry_widgets: Dict[str, Union[ttk.Entry, tk.Text, ttk.Spinbox, ttk.Checkbutton]] = {}

        for i, (field_name, config) in enumerate(fields.items()):
            ttk.Label(self.entry_fields_frame, text=config["label"]).grid(row=i, column=0, sticky=tk.NW, padx=5, pady=2)
            if config["widget"] == tk.Text:
                widget = tk.Text(self.entry_fields_frame, height=config["height"], width=50, wrap=tk.WORD)
            elif config["widget"] == ttk.Spinbox:
                widget = ttk.Spinbox(self.entry_fields_frame, width=48, **config.get("extra_args", {}))
            else: # ttk.Entry
                widget = ttk.Entry(self.entry_fields_frame, width=50)

            if config.get("readonly"):
                widget.config(state=tk.DISABLED)
            widget.grid(row=i, column=1, sticky=tk.EW, padx=5, pady=2)
            self.entry_widgets[field_name] = widget
      
        # Bottom: Save/Cancel buttons
        buttons_frame = ttk.Frame(self, padding="10")
        buttons_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(buttons_frame, text="변경사항 저장 후 닫기", command=self._save_and_close).pack(side=tk.RIGHT, padx=5) # type: ignore
        ttk.Button(buttons_frame, text="현재 항목 저장", command=self._save_current_entry_button_action).pack(side=tk.RIGHT, padx=5)
        ttk.Button(buttons_frame, text="취소", command=self.destroy).pack(side=tk.RIGHT)

        self._populate_listbox()
        if self.glossary_data: # Var name changed            
            self.listbox.selection_set(0)
            self._load_entry_to_fields(0)
        else:
            self._clear_entry_fields()

    def _populate_listbox(self):
        self.listbox.delete(0, tk.END)
        for i, entry in enumerate(self.glossary_data): # Var name changed          
            self.listbox.insert(tk.END, f"{i:03d}: {entry.get('keyword', 'N/A')}")

    def _on_listbox_select(self, event):
        selection = self.listbox.curselection()
        if not selection:
            # 사용자가 리스트박스의 빈 공간을 클릭하여 선택이 해제된 경우
            if self.current_selection_index is not None:
                self._save_current_entry() # Save data of the item that was deselected
            self._clear_entry_fields()
            self.current_selection_index = None # Update state
            return


        # 여기까지 왔다면, selection이 비어있지 않음
        new_index = selection[0] 


        # If there was a previously selected item and it's different from the new one
        if self.current_selection_index is not None and self.current_selection_index != new_index:
            if not self._save_current_entry(): # Save the old item's data
                # If save failed (e.g. validation), revert selection to the old item
                if self.current_selection_index is not None: # Ensure index is valid
                    self.listbox.selection_set(self.current_selection_index) # type: ignore              
                # Do not proceed to load the new_index if saving the old one failed.
                return 
        
        # Load the newly selected item's data into entry fields
        self._load_entry_to_fields(new_index)
        # self.current_selection_index is updated inside _load_entry_to_fields

    def _load_entry_to_fields(self, index: int):
        if not (0 <= index < len(self.glossary_data)): # Var name changed           
            self._clear_entry_fields()
            return

        entry = self.glossary_data[index]
        for field_name, widget in self.entry_widgets.items():
            value = entry.get(field_name)
            if isinstance(widget, tk.Text):
                is_readonly = widget.cget("state") == tk.DISABLED
                if is_readonly: widget.config(state=tk.NORMAL)             
                widget.delete('1.0', tk.END)
                widget.insert('1.0', str(value) if value is not None else "")
                if is_readonly: widget.config(state=tk.DISABLED)
            elif isinstance(widget, ttk.Entry):
                widget.delete(0, tk.END)
                widget.insert(0, str(value) if value is not None else "")
            elif isinstance(widget, ttk.Spinbox):
                widget.set(str(value) if value is not None else "0")
            elif isinstance(widget, ttk.Checkbutton):
                self.is_spoiler_var.set(bool(value))
        self.current_selection_index = index # Ensure this is set after loading


    def _clear_entry_fields(self):
        for field_name, widget in self.entry_widgets.items():
            if isinstance(widget, tk.Text):
                is_readonly = widget.cget("state") == tk.DISABLED
                if is_readonly: widget.config(state=tk.NORMAL)
                widget.delete('1.0', tk.END)
                if is_readonly: widget.config(state=tk.DISABLED)
            elif isinstance(widget, ttk.Entry):
                widget.delete(0, tk.END)
            elif isinstance(widget, ttk.Spinbox):
                widget.set("0")
        self.current_selection_index = None
        if "keyword" in self.entry_widgets:
            self.entry_widgets["keyword"].focus_set()

    def _save_current_entry_button_action(self):
        idx = self.current_selection_index
        if idx is not None:
            if self._save_current_entry(): # _save_current_entry now doesn't re-select
                # After saving, ensure the (potentially updated) item remains selected
                self.listbox.selection_set(idx)
                self.listbox.see(idx) # Ensure it's visible

    def _save_current_entry(self) -> bool: # Added return type
        if self.current_selection_index is None or not (0 <= self.current_selection_index < len(self.glossary_data)): # Var name changed
            return True # Nothing to save if no valid selection

        index_to_save = self.current_selection_index
        if not (0 <= index_to_save < len(self.glossary_data)): return True # Var name changed

        updated_entry: Dict[str, Any] = {}
        for field_name, widget_instance in self.entry_widgets.items():
            if isinstance(widget_instance, tk.Text):
                updated_entry[field_name] = widget_instance.get('1.0', tk.END).strip()
            elif isinstance(widget_instance, ttk.Entry):
                updated_entry[field_name] = widget_instance.get().strip()
            elif isinstance(widget_instance, ttk.Spinbox):
                try:
                    updated_entry[field_name] = int(widget_instance.get())
                except ValueError:
                    updated_entry[field_name] = 0 

        if not updated_entry.get("keyword") or not updated_entry.get("translated_keyword") or \
           not updated_entry.get("source_language") or not updated_entry.get("target_language"):          
            messagebox.showwarning("경고", "키워드는 비워둘 수 없습니다.", parent=self)
            self.entry_widgets["keyword"].focus_set()
            return False

        # Get the old display text from the listbox before updating the data
        # This is to check if the listbox item's text actually needs to be changed
        old_listbox_text = self.listbox.get(index_to_save)

        self.glossary_data[index_to_save] = updated_entry # Var name changed
        
        # Update only the specific listbox item if its display text changed
        new_listbox_text = f"{index_to_save:03d}: {updated_entry.get('keyword', 'N/A')}"
        if old_listbox_text != new_listbox_text:
            self.listbox.delete(index_to_save)
            self.listbox.insert(index_to_save, new_listbox_text)

        # REMOVED: self.listbox.selection_set(index_to_save) # Re-select
        return True

    def _add_new_entry(self):
        if self.current_selection_index is not None: # If an item is selected
            if not self._save_current_entry(): # Try to save it first
                return # Don't add new if save failed (e.g. validation)
        
        self._clear_entry_fields()
        # Create a new blank entry and add it to the data
        new_entry_template = {
            "keyword": "", "translated_keyword": "", 
            "source_language": "", "target_language": "",
+            "occurrence_count": 0
        }
        self.glossary_data.append(new_entry_template) # Var name changed
        self._populate_listbox()
        new_index = len(self.glossary_data) - 1 # Var name changed
        self.listbox.selection_set(new_index)
        self.listbox.see(new_index)
        self._load_entry_to_fields(new_index)
        self.entry_widgets["keyword"].focus_set()

    def _delete_selected_entry(self):
        if self.current_selection_index is None:
            messagebox.showwarning("경고", "삭제할 항목을 선택하세요.", parent=self)
            return

        if messagebox.askyesno("삭제 확인", f"'{self.glossary_data[self.current_selection_index].get('keyword')}' 항목을 정말 삭제하시겠습니까?", parent=self): # Var name changed
            del self.glossary_data[self.current_selection_index] # Var name changed
            self._populate_listbox()
            self._clear_entry_fields()
            if self.glossary_data: # If list is not empty, select first item # Var name changed
                self.listbox.selection_set(0)
                self._load_entry_to_fields(0)

    def _save_and_close(self):
        if self.current_selection_index is not None: # If an item is selected or was being edited
            if not self._save_current_entry(): # Try to save the currently edited/new item
                if not messagebox.askokcancel("저장 오류", "현재 항목 저장에 실패했습니다 (예: 키워드 누락). 저장하지 않고 닫으시겠습니까?", parent=self):
                    return

        # Filter out any entries that might have been added but left with an empty keyword
        self.glossary_data = [entry for entry in self.glossary_data if entry.get("keyword", "").strip()] # Var name changed

        final_json_str = json.dumps(self.glossary_data, indent=2, ensure_ascii=False) # Var name changed
        self.save_callback(final_json_str)
        self.destroy()


class TextHandler(logging.Handler):
    def __init__(self, text_widget: scrolledtext.ScrolledText): # type: ignore
        super().__init__() # Corrected indentation
        self.text_widget = text_widget
        self.text_widget.tag_config("INFO", foreground="black")
        self.text_widget.tag_config("DEBUG", foreground="gray")
        self.text_widget.tag_config("WARNING", foreground="orange")
        self.text_widget.tag_config("ERROR", foreground="red", font=('Helvetica', 9, 'bold'))
        self.text_widget.tag_config("CRITICAL", foreground="red", background="yellow", font=('Helvetica', 9, 'bold'))
        self.text_widget.tag_config("TQDM", foreground="blue") 

    def emit(self, record: logging.LogRecord): # Corrected indentation relative to class
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
