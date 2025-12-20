import customtkinter as ctk
from typing import List, Dict, Any, Callable

class PrefillHistoryEditor(ctk.CTkScrollableFrame):
    def __init__(self, master, history_data: List[Dict[str, Any]], on_change: Callable, **kwargs):
        super().__init__(master, **kwargs)
        self.history_data = history_data  # [{"role": "user", "parts": ["..."]}, ...]
        self.on_change = on_change        # 데이터 변경 시 호출될 콜백 함수
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
        """개별 대화 카드 생성 (React의 map 부분)"""
        role = item.get("role", "user")
        # parts가 리스트일 수 있으므로 첫 번째 요소만 사용하거나 결합
        content_text = item.get("parts", [""])[0] if item.get("parts") else ""

        # 1. 카드 컨테이너 (bg-white, border shadow 효과는 색상 차이로 대체)
        card_color = "#F0F4F8" if ctk.get_appearance_mode() == "Light" else "#2B2B2B"
        card_frame = ctk.CTkFrame(self, fg_color=card_color, corner_radius=10)
        card_frame.pack(fill="x", pady=5, padx=5)

        # 2. 헤더 영역 (Role 표시 및 삭제 버튼)
        header_frame = ctk.CTkFrame(card_frame, fg_color="transparent", height=25)
        header_frame.pack(fill="x", padx=10, pady=(10, 5))

        # A. 역할 배지 (Role Badge)
        # React: bg-blue-100 text-blue-700 / bg-purple-100 text-purple-700
        badge_color = "#DBEAFE" if role == "user" else "#F3E8FF" # Light mode 기준
        text_color = "#1E40AF" if role == "user" else "#6B21A8"
        
        # 다크 모드 대응 색상 조정 (간소화)
        if ctk.get_appearance_mode() == "Dark":
             badge_color = "#1E3A8A" if role == "user" else "#581C87"
             text_color = "#DBEAFE" if role == "user" else "#F3E8FF"

        role_badge = ctk.CTkLabel(
            header_frame, 
            text=f"  {role.upper()}  ", # 아이콘 대신 텍스트 패딩 사용
            fg_color=badge_color,
            text_color=text_color,
            font=("Arial", 11, "bold"),
            corner_radius=6
        )
        role_badge.pack(side="left")

        # B. 삭제 버튼 (Hover 효과는 복잡하므로, 작고 눈에 띄지 않는 버튼으로 대체하거나 항상 표시)
        delete_btn = ctk.CTkButton(
            header_frame,
            text="✕",
            width=24, height=24,
            fg_color="transparent",
            text_color="gray",
            hover_color="#FFCDD2", # 붉은색 호버
            command=lambda i=index: self._remove_item(i)
        )
        delete_btn.pack(side="right")

        # 3. 내용 편집 영역 (textarea)
        textbox = ctk.CTkTextbox(
            card_frame, 
            height=80, 
            fg_color="transparent", # 배경 투명하게 하여 카드와 일체감
            font=("Consolas", 12)   # font-mono
        )
        textbox.pack(fill="x", padx=10, pady=(0, 10))
        textbox.insert("0.0", content_text)
        
        # 데이터 바인딩: 텍스트 변경 시 history_data 업데이트
        def on_text_change(event):
            # 텍스트박스에서 내용 가져오기 (마지막 줄바꿈 제거)
            new_text = textbox.get("0.0", "end-1c")
            self._update_item(index, new_text)

        textbox.bind("<KeyRelease>", on_text_change)

    def _add_item(self, role: str):
        self.history_data.append({"role": role, "parts": [""]})
        self._render_items()
        self.on_change(self.history_data)

    def _remove_item(self, index: int):
        if 0 <= index < len(self.history_data):
            self.history_data.pop(index)
            self._render_items()
            self.on_change(self.history_data)

    def _update_item(self, index: int, new_text: str):
        if 0 <= index < len(self.history_data):
            self.history_data[index]["parts"] = [new_text]
            # 여기서는 렌더링을 다시 하지 않고 데이터만 업데이트 (입력 끊김 방지)
            self.on_change(self.history_data)

