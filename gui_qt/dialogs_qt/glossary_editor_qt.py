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
            self.list_widget.setCurrentRow(0)
            self._load_row(0)
        else:
            self._clear_fields()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter)

        # left pane: list + buttons
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        left_layout.addWidget(self.list_widget)

        list_btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("+ 추가")
        del_btn = QtWidgets.QPushButton("삭제")
        add_btn.clicked.connect(self._add_entry)
        del_btn.clicked.connect(self._delete_entry)
        list_btn_row.addWidget(add_btn)
        list_btn_row.addWidget(del_btn)
        left_layout.addLayout(list_btn_row)

        splitter.addWidget(left_widget)

        # right pane: detail form
        right_widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(right_widget)

        self.keyword_edit = QtWidgets.QLineEdit()
        self.translated_edit = QtWidgets.QLineEdit()
        self.target_lang_edit = QtWidgets.QLineEdit()
        self.occurrence_spin = QtWidgets.QSpinBox()
        self.occurrence_spin.setRange(0, 999999)

        for widget in [
            self.keyword_edit,
            self.translated_edit,
            self.target_lang_edit,
        ]:
            widget.textChanged.connect(self._on_field_changed)
        self.occurrence_spin.valueChanged.connect(self._on_field_changed)

        form.addRow("키워드", self.keyword_edit)
        form.addRow("번역된 키워드", self.translated_edit)
        form.addRow("도착 언어 (BCP-47)", self.target_lang_edit)
        form.addRow("등장 횟수", self.occurrence_spin)

        replace_row = QtWidgets.QHBoxLayout()
        replace_selected_btn = QtWidgets.QPushButton("선택 용어 치환")
        replace_all_btn = QtWidgets.QPushButton("모든 용어 치환")
        replace_selected_btn.clicked.connect(self._replace_selected)
        replace_all_btn.clicked.connect(self._replace_all)
        replace_row.addWidget(replace_selected_btn)
        replace_row.addWidget(replace_all_btn)
        form.addRow(replace_row)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 2)

        # bottom buttons
        btn_row = QtWidgets.QHBoxLayout()
        self.save_current_btn = QtWidgets.QPushButton("현재 항목 저장")
        self.save_close_btn = QtWidgets.QPushButton("저장 후 닫기")
        cancel_btn = QtWidgets.QPushButton("취소")
        self.save_current_btn.clicked.connect(self._save_current_entry)
        self.save_close_btn.clicked.connect(self._on_save_and_close)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(self.save_current_btn)
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
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for idx, entry in enumerate(self.entries):
            keyword = entry.get("keyword", "") or "(빈 키워드)"
            self.list_widget.addItem(f"{idx:03d}: {keyword}")
        self.list_widget.blockSignals(False)

    def _load_row(self, row: int) -> None:
        if not (0 <= row < len(self.entries)):
            self._clear_fields()
            return
        entry = self.entries[row]
        self._updating = True
        self.keyword_edit.setText(str(entry.get("keyword", "")))
        self.translated_edit.setText(str(entry.get("translated_keyword", "")))
        self.target_lang_edit.setText(str(entry.get("target_language", "")))
        self.occurrence_spin.setValue(int(entry.get("occurrence_count", 0) or 0))
        self._updating = False

    def _clear_fields(self) -> None:
        self._updating = True
        self.keyword_edit.clear()
        self.translated_edit.clear()
        self.target_lang_edit.clear()
        self.occurrence_spin.setValue(0)
        self._updating = False

    # ---------- slots ----------
    def _on_row_changed(self, row: int) -> None:
        self._load_row(row)

    def _on_field_changed(self) -> None:
        if self._updating:
            return
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self.entries)):
            return
        entry = self.entries[row]
        entry["keyword"] = self.keyword_edit.text().strip()
        entry["translated_keyword"] = self.translated_edit.text().strip()
        entry["target_language"] = self.target_lang_edit.text().strip()
        entry["occurrence_count"] = int(self.occurrence_spin.value())
        item = self.list_widget.item(row)
        if item:
            item.setText(f"{row:03d}: {entry.get('keyword') or '(빈 키워드)'}")

    def _add_entry(self) -> None:
        self.entries.append({
            "keyword": "",
            "translated_keyword": "",
            "target_language": "",
            "occurrence_count": 0,
        })
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self.entries) - 1)
        self.keyword_edit.setFocus()

    def _delete_entry(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self.entries)):
            return
        self.entries.pop(row)
        self._refresh_list()
        if self.entries:
            self.list_widget.setCurrentRow(min(row, len(self.entries) - 1))
        else:
            self._clear_fields()

    def _save_current_entry(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self.entries)):
            return
        if not self._validate_entry(self.entries[row]):
            QtWidgets.QMessageBox.warning(self, "검증 실패", "키워드, 번역된 키워드, 도착 언어는 필수입니다.")
            return
        QtWidgets.QMessageBox.information(self, "저장", "현재 항목이 저장되었습니다.")

    def _validate_entry(self, entry: Dict[str, Any]) -> bool:
        return bool(entry.get("keyword") and entry.get("translated_keyword") and entry.get("target_language"))

    def _on_save_and_close(self) -> None:
        # validate all entries
        for entry in self.entries:
            if not self._validate_entry(entry):
                QtWidgets.QMessageBox.warning(
                    self,
                    "검증 실패",
                    "모든 항목에 키워드, 번역된 키워드, 도착 언어가 필요합니다.",
                )
                return
        self.accept()

    # ---------- replace helpers ----------
    def _replace_selected(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self.entries)):
            QtWidgets.QMessageBox.information(self, "정보", "치환할 항목을 선택하세요.")
            return
        self._replace_entries([self.entries[row]])

    def _replace_all(self) -> None:
        if not self.entries:
            QtWidgets.QMessageBox.information(self, "정보", "치환할 용어집 데이터가 없습니다.")
            return
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
