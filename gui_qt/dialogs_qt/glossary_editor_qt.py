"""
Glossary Editor Dialog (PySide6)
- Edit glossary entries (keyword, translated_keyword, target_language, occurrence_count)
- Replace terms in the source file (selected/all)
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from gui_qt.components_qt.tooltip_qt import TooltipQt


class GlossaryEditorDialogQt(QtWidgets.QDialog):
    """PySide6 glossary editor dialog with replace helpers."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        glossary_json_str: str,
        input_file_path: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("용어집 편집")
        self.resize(900, 620)

        self.input_file_path = Path(input_file_path) if input_file_path else None
        self.entries: List[Dict[str, Any]] = self._parse_json(glossary_json_str)
        self._updating = False

        self._build_ui()
        self._refresh_list()
        if self.entries:
            self.table.setCurrentCell(0, 0)
        
    # ---------- UI ----------
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        # Top Button Row
        top_btn_layout = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("+ 항목 추가")
        TooltipQt(add_btn, "새 용어집 항목을 테이블 끝에 추가합니다.")
        del_btn = QtWidgets.QPushButton("- 선택 삭제")
        TooltipQt(del_btn, "테이블에서 선택한 항목을 삭제합니다.")
        
        replace_selected_btn = QtWidgets.QPushButton("선택 용어 치환")
        TooltipQt(replace_selected_btn, "현재 행의 용어만 입력 파일에서 치환합니다.")
        replace_all_btn = QtWidgets.QPushButton("전체 용어 치환")
        TooltipQt(replace_all_btn, "모든 용어집 항목을 입력 파일에서 치환합니다.")

        add_btn.clicked.connect(self._add_entry)
        del_btn.clicked.connect(self._delete_entry)
        replace_selected_btn.clicked.connect(self._replace_selected)
        replace_all_btn.clicked.connect(self._replace_all)

        top_btn_layout.addWidget(add_btn)
        top_btn_layout.addWidget(del_btn)
        top_btn_layout.addStretch(1)
        top_btn_layout.addWidget(replace_selected_btn)
        top_btn_layout.addWidget(replace_all_btn)
        layout.addLayout(top_btn_layout)

        # Main Table
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["키워드", "번역된 키워드", "등장 횟수"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        TooltipQt(self.table, "용어집 목록입니다. 셀을 더블 클릭하여 직접 수정할 수 있습니다.")
        layout.addWidget(self.table)

        # Bottom buttons
        btn_row = QtWidgets.QHBoxLayout()
        self.save_close_btn = QtWidgets.QPushButton("저장 후 닫기")
        TooltipQt(self.save_close_btn, "모든 항목의 유효성을 검사한 후 저장하고 닫습니다.")
        cancel_btn = QtWidgets.QPushButton("취소")
        TooltipQt(cancel_btn, "변경 사항을 저장하지 않고 닫습니다.")
        
        self.save_close_btn.clicked.connect(self._on_save_and_close)
        cancel_btn.clicked.connect(self.reject)
        
        btn_row.addStretch(1)
        btn_row.addWidget(self.save_close_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # ---------- data helpers ----------
    def _parse_json(self, text: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(text or "[]")
            if not isinstance(data, list):
                raise ValueError("Glossary JSON은 리스트여야 합니다.")
            return [entry for entry in data if isinstance(entry, dict)]
        except Exception:
            QtWidgets.QMessageBox.warning(self, "JSON 오류", "유효한 용어집 JSON이 아닙니다. 빈 목록으로 시작합니다.")
            return []

    def _refresh_list(self) -> None:
        self._updating = True
        self.table.setRowCount(0)
        for entry in self.entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(entry.get("keyword", ""))))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(entry.get("translated_keyword", ""))))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(entry.get("occurrence_count", 0))))
        self._updating = False

    # ---------- slots ----------
    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._updating:
            return
        row = item.row()
        col = item.column()
        if not (0 <= row < len(self.entries)):
            return
            
        entry = self.entries[row]
        val = item.text().strip()
        
        if col == 0: entry["keyword"] = val
        elif col == 1: entry["translated_keyword"] = val
        elif col == 2:
            try:
                entry["occurrence_count"] = int(val) if val else 0
            except ValueError:
                item.setText(str(entry.get("occurrence_count", 0)))

    def _add_entry(self) -> None:
        # 기존 항목에서 도착 언어 정보를 가져와서 기본값으로 사용 (없으면 'ko')
        default_lang = "ko"
        if self.entries and self.entries[0].get("target_language"):
            default_lang = self.entries[0]["target_language"]

        new_entry = {
            "keyword": "",
            "translated_keyword": "",
            "target_language": default_lang,
            "occurrence_count": 0,
        }
        self.entries.append(new_entry)
        
        self._updating = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        for i in range(3):
            self.table.setItem(row, i, QtWidgets.QTableWidgetItem(""))
        self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("0"))
        self._updating = False
        
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))

    def _delete_entry(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self.entries)):
            return
        
        self.entries.pop(row)
        self._updating = True
        self.table.removeRow(row)
        self._updating = False

    def _on_save_and_close(self) -> None:
        # validate all entries
        for entry in self.entries:
            if not self._validate_entry(entry):
                QtWidgets.QMessageBox.warning(
                    self,
                    "검증 실패",
                    f"항목 '{entry.get('keyword') or '명칭없음'}'의 필수 정보가 누락되었습니다.\n키워드, 번역된 키워드, 도착 언어가 필요합니다.",
                )
                return
        self.accept()

    def _validate_entry(self, entry: Dict[str, Any]) -> bool:
        return bool(entry.get("keyword") and entry.get("translated_keyword"))

    # ---------- replace helpers ----------
    def _replace_selected(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self.entries)):
            QtWidgets.QMessageBox.information(self, "정보", "치환할 항목을 선택하세요.")
            return
        
        entry = self.entries[row]
        keyword = entry.get("keyword", "")
        translated = entry.get("translated_keyword", "")
        
        reply = QtWidgets.QMessageBox.question(
            self,
            "치환 확인",
            f"선택한 항목 '{keyword}' → '{translated}'를 입력 파일에서 치환하시겠습니까?\n이 작업은 파일을 직접 수정하며 되돌릴 수 없습니다.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._replace_entries([entry])

    def _replace_all(self) -> None:
        if not self.entries:
            QtWidgets.QMessageBox.information(self, "정보", "치환할 용어집 데이터가 없습니다.")
            return

        reply = QtWidgets.QMessageBox.question(
            self,
            "전체 치환 확인",
            f"용어집의 모든 항목({len(self.entries)}개)을 입력 파일에서 치환하시겠습니까?\n이 작업은 파일을 직접 수정하며 되돌릴 수 없습니다.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._replace_entries(self.entries)

    def _replace_entries(self, entries: List[Dict[str, Any]]) -> None:
        if not self.input_file_path or not self.input_file_path.exists():
            QtWidgets.QMessageBox.warning(self, "파일 없음", "입력 파일 경로가 유효하지 않습니다. Settings 탭에서 입력 파일을 선택하세요.")
            return

        try:
            content = self.input_file_path.read_text(encoding="utf-8")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "읽기 실패", f"파일을 읽는 중 오류: {e}")
            return

        total_replacements = 0
        for entry in sorted(entries, key=lambda x: len(x.get("keyword", "")), reverse=True):
            keyword = entry.get("keyword") or ""
            translated = entry.get("translated_keyword") or ""
            if not keyword or not translated:
                continue
            pattern = re.escape(keyword)
            content, count = re.subn(pattern, translated, content)
            total_replacements += count

        if total_replacements == 0:
            QtWidgets.QMessageBox.information(self, "치환", "치환된 항목이 없습니다.")
            return

        try:
            self.input_file_path.write_text(content, encoding="utf-8")
            QtWidgets.QMessageBox.information(self, "치환 완료", f"총 {total_replacements}개 단어를 치환했습니다.")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "쓰기 실패", f"파일 저장 중 오류: {e}")

    # ---------- results ----------
    def get_json_str(self) -> str:
        return json.dumps(self.entries, ensure_ascii=False, indent=2)

    @staticmethod
    def edit(
        parent: Optional[QtWidgets.QWidget],
        glossary_json_str: str,
        input_file_path: Optional[str] = None,
    ) -> Optional[str]:
        dlg = GlossaryEditorDialogQt(parent, glossary_json_str, input_file_path)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            return dlg.get_json_str()
        return None
