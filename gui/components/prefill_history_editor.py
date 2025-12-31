import customtkinter as ctk
from typing import List, Dict, Any, Callable

class PrefillHistoryEditor(ctk.CTkScrollableFrame):
    def __init__(self, master, history_data: List[Dict[str, Any]], on_change: Callable, **kwargs):
        super().__init__(master, **kwargs)
        self.history_data = history_data  # [{"role": "user", "parts": ["..."]}, ...]
        self.on_change = on_change        # 데이터 변경 시 호출될 콜백 함수
        self.editing_indices = set()      # 현재 편집 중인 아이템의 인덱스 집합
        self._render_items()

    def _render_items(self):
        """현재 history_data를 기반으로 UI를 다시 그립니다."""
        # 기존 위젯 모두 제거
        for widget in self.winfo_children():
            widget.destroy()

        for index, item in enumerate(self.history_data):
            self._create_card(index, item)

        # '새 턴 추가' 버튼
        add_btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        add_btn_frame.pack(fill="x", pady=10)
        
        ctk.CTkButton(add_btn_frame, text="+ User Turn 추가", 
                      fg_color="#3B8ED0", width=120,
                      command=lambda: self._add_item("user")).pack(side="left", padx=5)
        
        ctk.CTkButton(add_btn_frame, text="+ Model Turn 추가", 
                      fg_color="#8E24AA", width=120,
                      command=lambda: self._add_item("model")).pack(side="left", padx=5)

    def _create_card(self, index: int, item: Dict[str, Any]):
        """개별 대화 카드 생성 (View/Edit 모드 지원)"""
        role = item.get("role", "user")
        content_text = item.get("parts", [""])[0] if item.get("parts") else ""
        is_editing = index in self.editing_indices

        # 1. 카드 컨테이너
        card_color = "#F0F4F8" if ctk.get_appearance_mode() == "Light" else "#2B2B2B"
        card_frame = ctk.CTkFrame(self, fg_color=card_color, corner_radius=10)
        card_frame.pack(fill="x", pady=5, padx=5)

        # 2. 헤더 영역 (Role 표시 및 액션 버튼)
        header_frame = ctk.CTkFrame(card_frame, fg_color="transparent", height=25)
        header_frame.pack(fill="x", padx=10, pady=(10, 5))

        # A. 역할 배지
        badge_color = "#DBEAFE" if role == "user" else "#F3E8FF" # Light mode
        text_color = "#1E40AF" if role == "user" else "#6B21A8"
        if ctk.get_appearance_mode() == "Dark":
             badge_color = "#1E3A8A" if role == "user" else "#581C87"
             text_color = "#DBEAFE" if role == "user" else "#F3E8FF"

        role_badge = ctk.CTkLabel(
            header_frame, 
            text=f"  {role.upper()}  ", 
            fg_color=badge_color,
            text_color=text_color,
            font=("Arial", 11, "bold"),
            corner_radius=6
        )
        role_badge.pack(side="left")

        # B. 버튼 영역 (삭제, 편집/완료)
        btn_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        btn_frame.pack(side="right")

        # 삭제 버튼
        delete_btn = ctk.CTkButton(
            btn_frame,
            text="✕",
            width=24, height=24,
            fg_color="transparent",
            text_color="gray",
            hover_color="#FFCDD2",
            command=lambda i=index: self._remove_item(i)
        )
        delete_btn.pack(side="right", padx=(5, 0))

        if is_editing:
            # [Edit Mode] 완료 버튼
            done_btn = ctk.CTkButton(
                btn_frame,
                text="✔ 완료",
                width=60, height=24,
                fg_color="#3B8ED0",
                command=lambda i=index: self._toggle_edit(i)
            )
            done_btn.pack(side="right")
        else:
            # [View Mode] 편집 버튼
            edit_btn = ctk.CTkButton(
                btn_frame,
                text="✎ 편집",
                width=60, height=24,
                fg_color="transparent",
                border_width=1,
                border_color="gray",
                text_color=("gray10", "gray90"),
                command=lambda i=index: self._toggle_edit(i)
            )
            edit_btn.pack(side="right")

        # 3. 내용 영역
        if is_editing:
            # [Edit Mode] 텍스트 박스
            textbox = ctk.CTkTextbox(
                card_frame, 
                height=150, # 편집 시에는 더 크게
                fg_color="transparent", 
                font=("Consolas", 12)
            )
            textbox.pack(fill="x", padx=10, pady=(0, 10))
            textbox.insert("0.0", content_text)
            
            # 입력 바인딩
            def on_text_change(event):
                new_text = textbox.get("0.0", "end-1c")
                self._update_item(index, new_text)
            
            textbox.bind("<KeyRelease>", on_text_change)
            # 포커스 자동 설정
            self.after(50, textbox.focus_set)
            
        else:
            # [View Mode] 텍스트 라벨 (요약)
            # 텍스트 길 경우 자르기
            display_text = content_text
            if len(display_text) > 200:
                display_text = display_text[:200] + "... (더보기)"
            elif not display_text.strip():
                display_text = "(내용 없음)"

            label = ctk.CTkLabel(
                card_frame,
                text=display_text,
                anchor="w",
                justify="left",
                wraplength=600, # 적절한 줄바꿈
                font=("Consolas", 12)
            )
            label.pack(fill="x", padx=10, pady=(0, 10))
            
            # 라벨 클릭 시에도 편집 모드로 전환 (UX 향상)
            label.bind("<Button-1>", lambda e, i=index: self._toggle_edit(i))

    def _add_item(self, role: str):
        self.history_data.append({"role": role, "parts": [""]})
        # 새 항목은 바로 편집 모드로
        new_index = len(self.history_data) - 1
        self.editing_indices.add(new_index)
        
        self._render_items()
        self.on_change(self.history_data)

    def _remove_item(self, index: int):
        if 0 <= index < len(self.history_data):
            self.history_data.pop(index)
            
            # 인덱스 조정 (삭제된 인덱스보다 큰 인덱스들은 -1)
            new_indices = set()
            for i in self.editing_indices:
                if i < index:
                    new_indices.add(i)
                elif i > index:
                    new_indices.add(i - 1)
            self.editing_indices = new_indices
            
            self._render_items()
            self.on_change(self.history_data)

    def _update_item(self, index: int, new_text: str):
        if 0 <= index < len(self.history_data):
            self.history_data[index]["parts"] = [new_text]
            # 여기서는 렌더링을 다시 하지 않고 데이터만 업데이트 (입력 끊김 방지)
            self.on_change(self.history_data)
            
    def _toggle_edit(self, index: int):
        """편집 모드 토글"""
        if index in self.editing_indices:
            self.editing_indices.remove(index)
        else:
            self.editing_indices.add(index)
        self._render_items()

