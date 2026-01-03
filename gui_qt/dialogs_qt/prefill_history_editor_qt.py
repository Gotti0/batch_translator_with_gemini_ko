"""
Prefill History Editor Dialog (PySide6)
- Edit list of {role, parts:[text]} turns
- Optional system instruction field
"""

from __future__ import annotations

from typing import Callable, List, Dict, Any, Optional
import copy

from PySide6 import QtCore, QtGui, QtWidgets


class PrefillHistoryEditorDialogQt(QtWidgets.QDialog):
    """Simple dialog to edit prefill history and system instruction."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        history_data: List[Dict[str, Any]],
        system_instruction: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("프리필 히스토리 편집")
        self.resize(820, 640 if system_instruction is not None else 560)

        # local copies to avoid mutating callers
        self._history: List[Dict[str, Any]] = copy.deepcopy(history_data)
        self._system_instruction = system_instruction if system_instruction is not None else ""

        self._build_ui(system_instruction is not None)
        self._populate_list()

    def _build_ui(self, show_system: bool) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        if show_system:
            sys_group = QtWidgets.QGroupBox("시스템 지침 (System Instruction)")
            sys_layout = QtWidgets.QVBoxLayout(sys_group)
            self.sys_edit = QtWidgets.QPlainTextEdit()
            self.sys_edit.setPlainText(self._system_instruction)
            sys_layout.addWidget(self.sys_edit)
            layout.addWidget(sys_group)
        else:
            self.sys_edit = None

        main_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # left: list
        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        left_layout.addWidget(self.list_widget)

        btn_row = QtWidgets.QHBoxLayout()
        add_user = QtWidgets.QPushButton("+ User 추가")
        add_model = QtWidgets.QPushButton("+ Model 추가")
        delete_btn = QtWidgets.QPushButton("선택 삭제")
        add_user.clicked.connect(lambda: self._add_item("user"))
        add_model.clicked.connect(lambda: self._add_item("model"))
        delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(add_user)
        btn_row.addWidget(add_model)
        btn_row.addWidget(delete_btn)
        left_layout.addLayout(btn_row)

        # right: detail editor
        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QFormLayout(right_widget)
        self.role_combo = QtWidgets.QComboBox()
        self.role_combo.addItems(["user", "model"])
        self.role_combo.currentTextChanged.connect(self._on_role_changed)
        self.content_edit = QtWidgets.QPlainTextEdit()
        self.content_edit.textChanged.connect(self._on_content_changed)
        right_layout.addRow("Role", self.role_combo)
        right_layout.addRow("내용", self.content_edit)

        main_split.addWidget(left_widget)
        main_split.addWidget(right_widget)
        main_split.setStretchFactor(1, 2)
        layout.addWidget(main_split)

        # action buttons
        btn_box = QtWidgets.QDialogButtonBox()
        btn_box.setStandardButtons(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    # ---------- data helpers ----------
    def _populate_list(self) -> None:
        self.list_widget.clear()
        for item in self._history:
            role = item.get("role", "user")
            content = (item.get("parts") or [""])[0]
            preview = content.strip().split("\n", 1)[0]
            if len(preview) > 60:
                preview = preview[:57] + "..."
            self.list_widget.addItem(f"[{role}] {preview}")
        if self._history:
            self.list_widget.setCurrentRow(0)
        else:
            self._clear_detail()

    def _clear_detail(self) -> None:
        self.role_combo.setCurrentText("user")
        self.content_edit.blockSignals(True)
        self.content_edit.setPlainText("")
        self.content_edit.blockSignals(False)

    def _sync_detail_to_history(self, index: int) -> None:
        if index < 0 or index >= len(self._history):
            self._clear_detail()
            return
        item = self._history[index]
        self.role_combo.blockSignals(True)
        self.role_combo.setCurrentText(item.get("role", "user"))
        self.role_combo.blockSignals(False)
        self.content_edit.blockSignals(True)
        content = (item.get("parts") or [""])[0]
        self.content_edit.setPlainText(content)
        self.content_edit.blockSignals(False)

    # ---------- slots ----------
    def _on_row_changed(self, row: int) -> None:
        self._sync_detail_to_history(row)

    def _add_item(self, role: str) -> None:
        self._history.append({"role": role, "parts": [""]})
        self._populate_list()
        self.list_widget.setCurrentRow(len(self._history) - 1)
        self.content_edit.setFocus()

    def _delete_selected(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._history):
            self._history.pop(row)
            self._populate_list()

    def _on_role_changed(self, role: str) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._history):
            self._history[row]["role"] = role
            self._update_list_row(row)

    def _on_content_changed(self) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._history):
            self._history[row]["parts"] = [self.content_edit.toPlainText()]
            self._update_list_row(row)

    def _update_list_row(self, row: int) -> None:
        if 0 <= row < self.list_widget.count():
            item = self._history[row]
            content = (item.get("parts") or [""])[0]
            preview = content.strip().split("\n", 1)[0]
            if len(preview) > 60:
                preview = preview[:57] + "..."
            self.list_widget.item(row).setText(f"[{item.get('role', 'user')}] {preview}")

    # ---------- results ----------
    def get_history(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._history)

    def get_system_instruction(self) -> Optional[str]:
        if self.sys_edit is None:
            return None
        return self.sys_edit.toPlainText().strip()

    @staticmethod
    def edit(
        parent: Optional[QtWidgets.QWidget],
        history_data: List[Dict[str, Any]],
        system_instruction: Optional[str] = None,
    ) -> Optional[tuple[List[Dict[str, Any]], Optional[str]]]:
        dlg = PrefillHistoryEditorDialogQt(parent, history_data, system_instruction)
        result = dlg.exec()
        if result == QtWidgets.QDialog.Accepted:
            return dlg.get_history(), dlg.get_system_instruction()
        return None
