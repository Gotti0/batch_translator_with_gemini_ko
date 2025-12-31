"""
Prefill History Editor Dialog

Pop-up dialog for editing prefill conversation history.
"""

import tkinter as tk
from tkinter import scrolledtext
import ttkbootstrap as ttk
from typing import List, Dict, Any, Callable, Optional
from gui.components.prefill_history_editor import PrefillHistoryEditor

class PrefillHistoryEditorDialog(tk.Toplevel):
    """
    Dialog window for editing prefill history.
    """
    def __init__(
        self,
        master: tk.Widget,
        history_data: List[Dict[str, Any]],
        on_save: Callable[..., None], # Callback can accept (history) or (history, instruction)
        title: str = "프리필 히스토리 편집",
        system_instruction: Optional[str] = None, # If None, hides the system instruction field
    ):
        super().__init__(master)
        self.title(title)
        self.geometry("800x700" if system_instruction is not None else "800x600")
        
        self.history_data = history_data
        self.on_save = on_save
        self.current_data = [item.copy() for item in history_data] # Deep copy
        
        # System Instruction State
        self.show_system_instruction = system_instruction is not None
        self.current_system_instruction = system_instruction if system_instruction is not None else ""

        self._create_widgets()
        
        # Modal logic
        self.transient(master)
        self.grab_set()
        self.focus_set()

    def _create_widgets(self):
        # 1. System Instruction Area (Optional)
        if self.show_system_instruction:
            sys_frame = ttk.Labelframe(self, text="시스템 지침 (System Instruction)", padding=10)
            sys_frame.pack(fill="x", padx=10, pady=(10, 5))
            
            self.sys_text = scrolledtext.ScrolledText(sys_frame, height=5, width=80, wrap=tk.WORD)
            self.sys_text.pack(fill="x", expand=True)
            self.sys_text.insert("1.0", self.current_system_instruction)

        # 2. Editor Area
        editor_frame = ttk.Labelframe(self, text="대화 예시 (Few-shot History)", padding=10)
        editor_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.editor = PrefillHistoryEditor(
            editor_frame,
            history_data=self.current_data,
            on_change=self._on_editor_change
        )
        self.editor.pack(fill="both", expand=True)

        # 2. Button Area
        button_frame = ttk.Frame(self, padding=10)
        button_frame.pack(fill="x", side="bottom")

        # Save Button
        save_btn = ttk.Button(
            button_frame,
            text="저장 (Save)",
            bootstyle="primary",
            command=self._save_and_close
        )
        save_btn.pack(side="right", padx=5)

        # Cancel Button
        cancel_btn = ttk.Button(
            button_frame,
            text="취소 (Cancel)",
            bootstyle="secondary",
            command=self.destroy
        )
        cancel_btn.pack(side="right", padx=5)

    def _on_editor_change(self, new_data: List[Dict[str, Any]]):
        """Update local data when editor changes."""
        self.current_data = new_data

    def _save_and_close(self):
        """Invoke callback and close."""
        if self.show_system_instruction:
            # Get current system instruction text
            new_sys_inst = self.sys_text.get("1.0", "end-1c").strip()
            self.on_save(self.current_data, new_sys_inst)
        else:
            self.on_save(self.current_data)
        
        self.destroy()
