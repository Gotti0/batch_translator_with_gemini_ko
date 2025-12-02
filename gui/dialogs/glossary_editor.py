"""
용어집 편집기 다이얼로그

용어집 항목을 편집할 수 있는 팝업 창입니다.
"""

import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk
from typing import Dict, Any, List, Optional, Union, Callable
import json
import re
import os


class GlossaryEditorWindow(tk.Toplevel):
    """용어집 편집기 창"""
    
    def __init__(
        self, 
        master: tk.Tk, 
        glossary_json_str: str, 
        save_callback: Callable[[str], None], 
        input_file_path: str
    ):
        """
        Args:
            master: 부모 윈도우
            glossary_json_str: 용어집 JSON 문자열
            save_callback: 저장 시 호출될 콜백 함수
            input_file_path: 입력 파일 경로 (용어 치환용)
        """
        super().__init__(master)
        self.title("용어집 편집기")
        self.geometry("800x600")
        self.save_callback = save_callback
        self.input_file_path = input_file_path

        try:
            self.glossary_data: List[Dict[str, Any]] = json.loads(glossary_json_str)
            if not isinstance(self.glossary_data, list):
                raise ValueError("Glossary data must be a list of entries.")
        except (json.JSONDecodeError, ValueError) as e:
            messagebox.showerror(
                "데이터 오류", 
                f"용어집 데이터를 불러오는 중 오류 발생: {e}", 
                parent=self
            )
            self.glossary_data = []

        self.current_selection_index: Optional[int] = None
        self.entry_widgets: Dict[str, Union[ttk.Entry, tk.Text, ttk.Spinbox, ttk.Checkbutton]] = {}

        self._create_widgets()
        self._populate_listbox()
        
        if self.glossary_data:
            self.listbox.selection_set(0)
            self._load_entry_to_fields(0)
        else:
            self._clear_entry_fields()

    def _create_widgets(self):
        """위젯 생성"""
        # Main frame
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left: Listbox for keywords
        listbox_frame = ttk.Frame(main_frame)
        listbox_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        self.listbox_scrollbar = ttk.Scrollbar(listbox_frame, orient=tk.VERTICAL)
        self.listbox_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox = tk.Listbox(
            listbox_frame, 
            width=30, 
            height=20, 
            exportselection=False, 
            yscrollcommand=self.listbox_scrollbar.set
        )
        self.listbox.pack(side=tk.TOP, fill=tk.Y, expand=True)
        self.listbox_scrollbar.config(command=self.listbox.yview)

        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        listbox_buttons_frame = ttk.Frame(listbox_frame)
        listbox_buttons_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        ttk.Button(
            listbox_buttons_frame, 
            text="새 항목", 
            command=self._add_new_entry
        ).pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(
            listbox_buttons_frame, 
            text="항목 삭제", 
            command=self._delete_selected_entry
        ).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # Right: Entry fields for selected item
        self.entry_fields_frame = ttk.Frame(main_frame)
        self.entry_fields_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        fields = {
            "keyword": {"label": "키워드:", "widget": ttk.Entry, "height": 1},
            "translated_keyword": {"label": "번역된 키워드:", "widget": ttk.Entry, "height": 1},
            "target_language": {"label": "도착 언어 (BCP-47):", "widget": ttk.Entry, "height": 1},
            "occurrence_count": {
                "label": "등장 횟수:", 
                "widget": ttk.Spinbox, 
                "height": 1, 
                "extra_args": {"from_": 0, "to": 9999}
            },
        }

        for i, (field_name, config) in enumerate(fields.items()):
            ttk.Label(
                self.entry_fields_frame, 
                text=config["label"]
            ).grid(row=i, column=0, sticky=tk.NW, padx=5, pady=2)
            
            if config["widget"] == tk.Text:
                widget = tk.Text(
                    self.entry_fields_frame, 
                    height=config["height"], 
                    width=50, 
                    wrap=tk.WORD
                )
            elif config["widget"] == ttk.Spinbox:
                widget = ttk.Spinbox(
                    self.entry_fields_frame, 
                    width=48, 
                    **config.get("extra_args", {})
                )
            else:  # ttk.Entry
                widget = ttk.Entry(self.entry_fields_frame, width=50)

            if config.get("readonly"):
                widget.config(state=tk.DISABLED)
            widget.grid(row=i, column=1, sticky=tk.EW, padx=5, pady=2)
            self.entry_widgets[field_name] = widget

        # Replace buttons
        replace_buttons_frame = ttk.Frame(self.entry_fields_frame)
        replace_buttons_frame.grid(row=len(fields), column=0, columnspan=2, pady=10, sticky="ew")
        ttk.Button(
            replace_buttons_frame, 
            text="선택한 용어 치환", 
            command=self._replace_selected_term
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            replace_buttons_frame, 
            text="모든 용어 치환", 
            command=self._replace_all_terms
        ).pack(side=tk.LEFT, padx=5)

        # Bottom: Save/Cancel buttons
        buttons_frame = ttk.Frame(self, padding="10")
        buttons_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(
            buttons_frame, 
            text="변경사항 저장 후 닫기", 
            command=self._save_and_close
        ).pack(side=tk.RIGHT, padx=5)
        ttk.Button(
            buttons_frame, 
            text="현재 항목 저장", 
            command=self._save_current_entry_button_action
        ).pack(side=tk.RIGHT, padx=5)
        ttk.Button(
            buttons_frame, 
            text="취소", 
            command=self.destroy
        ).pack(side=tk.RIGHT)

    def _replace_all_terms(self):
        """모든 용어 치환"""
        if not self.input_file_path or not os.path.exists(self.input_file_path):
            messagebox.showerror(
                "오류", 
                "입력 파일 경로가 유효하지 않습니다. 입력 파일을 선택해주세요.", 
                parent=self
            )
            return

        if not self.glossary_data:
            messagebox.showinfo("정보", "치환할 용어집 데이터가 없습니다.", parent=self)
            return

        if not messagebox.askyesno(
            "전체 치환 확인", 
            f"총 {len(self.glossary_data)}개의 용어를 파일 전체에서 치환하시겠습니까?\n"
            "이 작업은 되돌릴 수 없습니다.", 
            parent=self
        ):
            return

        try:
            with open(self.input_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror(
                "파일 읽기 오류", 
                f"파일을 읽는 중 오류가 발생했습니다: {e}", 
                parent=self
            )
            return

        total_replacements = 0
        # Sort by length of keyword, descending, to replace longer words first
        sorted_glossary = sorted(
            self.glossary_data, 
            key=lambda x: len(x.get("keyword", "")), 
            reverse=True
        )

        for entry in sorted_glossary:
            keyword = entry.get("keyword")
            translated_keyword = entry.get("translated_keyword")

            if not keyword or not translated_keyword:
                continue
            
            pattern = re.escape(keyword)
            new_content, num_replacements = re.subn(pattern, translated_keyword, content)
            
            if num_replacements > 0:
                content = new_content
                total_replacements += num_replacements

        try:
            with open(self.input_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            messagebox.showinfo(
                "치환 완료", 
                f"총 {total_replacements}개의 단어가 성공적으로 치환되었습니다.", 
                parent=self
            )
        except Exception as e:
            messagebox.showerror(
                "파일 쓰기 오류", 
                f"파일을 저장하는 중 오류가 발생했습니다: {e}", 
                parent=self
            )

    def _replace_selected_term(self):
        """선택한 용어 치환"""
        if self.current_selection_index is None:
            messagebox.showinfo("정보", "치환할 용어를 선택해주세요.", parent=self)
            return

        if not self.input_file_path or not os.path.exists(self.input_file_path):
            messagebox.showerror(
                "오류", 
                "입력 파일 경로가 유효하지 않습니다. 입력 파일을 선택해주세요.", 
                parent=self
            )
            return

        entry = self.glossary_data[self.current_selection_index]
        keyword = entry.get("keyword")
        translated_keyword = entry.get("translated_keyword")

        if not keyword or not translated_keyword:
            messagebox.showerror(
                "오류", 
                "선택된 항목에 키워드 또는 번역된 키워드가 없습니다.", 
                parent=self
            )
            return

        if len(keyword) == 1:
            if not messagebox.askyesno(
                "경고", 
                "한 글자로 된 용어를 치환할 경우, 문맥상 오류가 발생할 가능성이 높습니다. "
                "그래도 바꾸시겠습니까?", 
                parent=self
            ):
                return

        if not messagebox.askyesno(
            "치환 확인", 
            f"'{keyword}'을(를) '{translated_keyword}'(으)로 치환하시겠습니까?", 
            parent=self
        ):
            return

        try:
            with open(self.input_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror(
                "파일 읽기 오류", 
                f"파일을 읽는 중 오류가 발생했습니다: {e}", 
                parent=self
            )
            return

        pattern = re.escape(keyword)
        new_content, num_replacements = re.subn(pattern, translated_keyword, content)

        if num_replacements == 0:
            messagebox.showinfo(
                "정보", 
                f"'{keyword}'을(를) 파일에서 찾을 수 없습니다.", 
                parent=self
            )
            return

        try:
            with open(self.input_file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            messagebox.showinfo(
                "치환 완료", 
                f"{num_replacements}개의 단어가 성공적으로 치환되었습니다.", 
                parent=self
            )
        except Exception as e:
            messagebox.showerror(
                "파일 쓰기 오류", 
                f"파일을 저장하는 중 오류가 발생했습니다: {e}", 
                parent=self
            )

    def _populate_listbox(self):
        """리스트박스 채우기"""
        self.listbox.delete(0, tk.END)
        for i, entry in enumerate(self.glossary_data):
            self.listbox.insert(tk.END, f"{i:03d}: {entry.get('keyword', 'N/A')}")

    def _on_listbox_select(self, event):
        """리스트박스 선택 이벤트 핸들러"""
        selection = self.listbox.curselection()
        if not selection:
            if self.current_selection_index is not None:
                self._save_current_entry()
            self._clear_entry_fields()
            self.current_selection_index = None
            return

        new_index = selection[0]

        if self.current_selection_index is not None and self.current_selection_index != new_index:
            if not self._save_current_entry():
                if self.current_selection_index is not None:
                    self.listbox.selection_set(self.current_selection_index)
                return
        
        self._load_entry_to_fields(new_index)

    def _load_entry_to_fields(self, index: int):
        """항목 데이터를 입력 필드에 로드"""
        if not (0 <= index < len(self.glossary_data)):
            self._clear_entry_fields()
            return

        entry = self.glossary_data[index]
        for field_name, widget in self.entry_widgets.items():
            value = entry.get(field_name)
            if isinstance(widget, tk.Text):
                is_readonly = widget.cget("state") == tk.DISABLED
                if is_readonly:
                    widget.config(state=tk.NORMAL)
                widget.delete('1.0', tk.END)
                widget.insert('1.0', str(value) if value is not None else "")
                if is_readonly:
                    widget.config(state=tk.DISABLED)
            elif isinstance(widget, ttk.Entry):
                widget.delete(0, tk.END)
                widget.insert(0, str(value) if value is not None else "")
            elif isinstance(widget, ttk.Spinbox):
                widget.set(str(value) if value is not None else "0")
        self.current_selection_index = index

    def _clear_entry_fields(self):
        """입력 필드 초기화"""
        for field_name, widget in self.entry_widgets.items():
            if isinstance(widget, tk.Text):
                is_readonly = widget.cget("state") == tk.DISABLED
                if is_readonly:
                    widget.config(state=tk.NORMAL)
                widget.delete('1.0', tk.END)
                if is_readonly:
                    widget.config(state=tk.DISABLED)
            elif isinstance(widget, ttk.Entry):
                widget.delete(0, tk.END)
            elif isinstance(widget, ttk.Spinbox):
                widget.set("0")
        self.current_selection_index = None
        if "keyword" in self.entry_widgets:
            self.entry_widgets["keyword"].focus_set()

    def _save_current_entry_button_action(self):
        """현재 항목 저장 버튼 액션"""
        idx = self.current_selection_index
        if idx is not None:
            if self._save_current_entry():
                self.listbox.selection_set(idx)
                self.listbox.see(idx)

    def _save_current_entry(self) -> bool:
        """현재 항목 저장"""
        if self.current_selection_index is None or \
           not (0 <= self.current_selection_index < len(self.glossary_data)):
            return True

        index_to_save = self.current_selection_index
        if not (0 <= index_to_save < len(self.glossary_data)):
            return True

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
           not updated_entry.get("target_language"):
            messagebox.showwarning(
                "경고", 
                "키워드, 번역된 키워드, 도착 언어는 비워둘 수 없습니다.", 
                parent=self
            )
            self.entry_widgets["keyword"].focus_set()
            return False

        old_listbox_text = self.listbox.get(index_to_save)
        self.glossary_data[index_to_save] = updated_entry
        
        new_listbox_text = f"{index_to_save:03d}: {updated_entry.get('keyword', 'N/A')}"
        if old_listbox_text != new_listbox_text:
            self.listbox.delete(index_to_save)
            self.listbox.insert(index_to_save, new_listbox_text)

        return True

    def _add_new_entry(self):
        """새 항목 추가"""
        if self.current_selection_index is not None:
            if not self._save_current_entry():
                return
        
        self._clear_entry_fields()
        new_entry_template = {
            "keyword": "", 
            "translated_keyword": "", 
            "target_language": "",
            "occurrence_count": 0
        }
        self.glossary_data.append(new_entry_template)
        self._populate_listbox()
        new_index = len(self.glossary_data) - 1
        self.listbox.selection_set(new_index)
        self.listbox.see(new_index)
        self._load_entry_to_fields(new_index)
        self.entry_widgets["keyword"].focus_set()

    def _delete_selected_entry(self):
        """선택된 항목 삭제"""
        if self.current_selection_index is None:
            messagebox.showwarning("경고", "삭제할 항목을 선택하세요.", parent=self)
            return

        if messagebox.askyesno(
            "삭제 확인", 
            f"'{self.glossary_data[self.current_selection_index].get('keyword')}' "
            "항목을 정말 삭제하시겠습니까?", 
            parent=self
        ):
            del self.glossary_data[self.current_selection_index]
            self._populate_listbox()
            self._clear_entry_fields()
            if self.glossary_data:
                self.listbox.selection_set(0)
                self._load_entry_to_fields(0)

    def _save_and_close(self):
        """저장 후 닫기"""
        if self.current_selection_index is not None:
            if not self._save_current_entry():
                if not messagebox.askokcancel(
                    "저장 오류", 
                    "현재 항목 저장에 실패했습니다 (예: 키워드 누락). "
                    "저장하지 않고 닫으시겠습니까?", 
                    parent=self
                ):
                    return

        # Filter out entries with empty keyword
        self.glossary_data = [
            entry for entry in self.glossary_data 
            if entry.get("keyword", "").strip()
        ]

        final_json_str = json.dumps(self.glossary_data, indent=2, ensure_ascii=False)
        self.save_callback(final_json_str)
        self.destroy()
