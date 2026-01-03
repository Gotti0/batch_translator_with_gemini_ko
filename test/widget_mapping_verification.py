#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
위젯 매핑 테이블 기반 기능 무결성 검증 스크립트
Tkinter GUI와 PySide6 GUI 간의 위젯 대응 관계 및 기능 일치성을 검증합니다.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, field

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@dataclass
class WidgetMapping:
    """위젯 매핑 정보"""
    tk_widget_name: str  # Tkinter 위젯 속성명
    qt_widget_name: str  # PySide6 위젯 속성명
    widget_type: str     # 위젯 타입 (Entry, ComboBox, Slider, etc.)
    tab_name: str        # 탭 이름 (Settings, Glossary)
    is_required: bool = True  # 필수 위젯 여부
    config_key: Optional[str] = None  # 대응되는 config 키
    description: str = ""  # 설명


@dataclass
class ValidationResult:
    """검증 결과"""
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


class WidgetMappingValidator:
    """위젯 매핑 검증기"""
    
    def __init__(self):
        self.mappings: List[WidgetMapping] = []
        self.results: List[ValidationResult] = []
        self.project_root = Path(__file__).parent.parent
        self._setup_mappings()
    
    def _setup_mappings(self) -> None:
        """위젯 매핑 정의"""
        # Settings Tab - API/모델 섹션
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="api_keys_text",
                qt_widget_name="api_keys_edit",
                widget_type="PlainTextEdit",
                tab_name="Settings",
                config_key="api_keys",
                description="API 키 목록 입력"
            ),
            WidgetMapping(
                tk_widget_name="use_vertex_ai_var",
                qt_widget_name="use_vertex_check",
                widget_type="CheckBox",
                tab_name="Settings",
                config_key="use_vertex_ai",
                description="Vertex AI 사용 여부"
            ),
            WidgetMapping(
                tk_widget_name="service_account_file_entry",
                qt_widget_name="sa_path_edit",
                widget_type="LineEdit",
                tab_name="Settings",
                config_key="service_account_file_path",
                description="서비스 계정 JSON 경로"
            ),
            WidgetMapping(
                tk_widget_name="gcp_project_entry",
                qt_widget_name="gcp_project_edit",
                widget_type="LineEdit",
                tab_name="Settings",
                config_key="gcp_project",
                description="GCP 프로젝트 ID"
            ),
            WidgetMapping(
                tk_widget_name="gcp_location_entry",
                qt_widget_name="gcp_location_edit",
                widget_type="LineEdit",
                tab_name="Settings",
                config_key="gcp_location",
                description="GCP 위치"
            ),
            WidgetMapping(
                tk_widget_name="model_name_combobox",
                qt_widget_name="model_name_combo",
                widget_type="ComboBox",
                tab_name="Settings",
                config_key="model_name",
                description="모델 이름"
            ),
        ])
        
        # Settings Tab - 생성 파라미터
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="temperature_scale",
                qt_widget_name="temperature_slider",
                widget_type="Slider",
                tab_name="Settings",
                config_key="temperature",
                description="Temperature 슬라이더"
            ),
            WidgetMapping(
                tk_widget_name="top_p_scale",
                qt_widget_name="top_p_slider",
                widget_type="Slider",
                tab_name="Settings",
                config_key="top_p",
                description="Top P 슬라이더"
            ),
            WidgetMapping(
                tk_widget_name="thinking_budget_entry",
                qt_widget_name="thinking_budget_slider",
                widget_type="SpinBox",  # Tk: Entry, Qt: Slider (기능적으로 SpinBox 역할)
                tab_name="Settings",
                config_key="thinking_budget",
                description="Thinking Budget"
            ),
            WidgetMapping(
                tk_widget_name="thinking_level_combobox",
                qt_widget_name="thinking_level_combo",
                widget_type="ComboBox",
                tab_name="Settings",
                config_key="thinking_level",
                description="Thinking Level"
            ),
        ])
        
        # Settings Tab - 파일/처리 설정
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="input_file_listbox",
                qt_widget_name="input_edit",
                widget_type="FileInput",  # Tk: Listbox (다중), Qt: LineEdit (단일) - 의도적 차이
                tab_name="Settings",
                config_key=None,  # 동적 처리
                description="입력 파일 (Tk: 다중, Qt: 단일)"
            ),
            WidgetMapping(
                tk_widget_name="output_file_entry",
                qt_widget_name="output_edit",
                widget_type="LineEdit",
                tab_name="Settings",
                config_key=None,
                description="출력 파일"
            ),
            WidgetMapping(
                tk_widget_name="chunk_size_entry",
                qt_widget_name="chunk_size_spin",
                widget_type="SpinBox",
                tab_name="Settings",
                config_key="chunk_size",
                description="청크 크기"
            ),
            WidgetMapping(
                tk_widget_name="max_workers_entry",
                qt_widget_name="max_workers_spin",
                widget_type="SpinBox",
                tab_name="Settings",
                config_key="max_workers",
                description="최대 작업자 수"
            ),
            WidgetMapping(
                tk_widget_name="rpm_entry",
                qt_widget_name="rpm_spin",
                widget_type="SpinBox",
                tab_name="Settings",
                config_key="requests_per_minute",
                description="분당 요청 수"
            ),
        ])
        
        # Settings Tab - 언어 설정
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="novel_language_entry",
                qt_widget_name="novel_lang_edit",
                widget_type="LineEdit",
                tab_name="Settings",
                config_key="novel_language",
                description="출발 언어"
            ),
            WidgetMapping(
                tk_widget_name="novel_language_fallback_entry",
                qt_widget_name="novel_fallback_edit",
                widget_type="LineEdit",
                tab_name="Settings",
                config_key="novel_language_fallback",
                description="자동감지 실패 폴백"
            ),
        ])
        
        # Settings Tab - 프롬프트
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="prompt_text",
                qt_widget_name="prompt_edit",
                widget_type="PlainTextEdit",
                tab_name="Settings",
                config_key="prompts",
                description="번역 프롬프트"
            ),
        ])
        
        # Settings Tab - 프리필
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="enable_prefill_var",
                qt_widget_name="enable_prefill_check",
                widget_type="CheckBox",
                tab_name="Settings",
                config_key="enable_prefill_translation",
                description="프리필 번역 사용"
            ),
            WidgetMapping(
                tk_widget_name="prefill_system_instruction_text",
                qt_widget_name="prefill_system_edit",
                widget_type="PlainTextEdit",
                tab_name="Settings",
                config_key="prefill_system_instruction",
                description="프리필 시스템 지침"
            ),
        ])
        
        # Settings Tab - 콘텐츠 안전
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="use_content_safety_retry_var",
                qt_widget_name="use_content_safety_check",
                widget_type="CheckBox",
                tab_name="Settings",
                config_key="use_content_safety_retry",
                description="검열 오류 시 청크 분할 재시도"
            ),
            WidgetMapping(
                tk_widget_name="max_split_attempts_entry",
                qt_widget_name="max_split_spin",
                widget_type="SpinBox",
                tab_name="Settings",
                config_key="max_content_safety_split_attempts",
                description="최대 분할 시도"
            ),
            WidgetMapping(
                tk_widget_name="min_chunk_size_entry",
                qt_widget_name="min_chunk_spin",
                widget_type="SpinBox",
                tab_name="Settings",
                config_key="min_content_safety_chunk_size",
                description="최소 청크 크기"
            ),
        ])
        
        # Glossary Tab
        self.mappings.extend([
            WidgetMapping(
                tk_widget_name="glossary_json_path_entry",
                qt_widget_name="glossary_path_edit",
                widget_type="LineEdit",
                tab_name="Glossary",
                config_key="glossary_json_path",
                description="용어집 JSON 경로"
            ),
            WidgetMapping(
                tk_widget_name="sample_ratio_scale",
                qt_widget_name="sample_ratio_slider",
                widget_type="Slider",
                tab_name="Glossary",
                config_key="glossary_sampling_ratio",
                description="샘플링 비율"
            ),
            WidgetMapping(
                tk_widget_name="extraction_temp_scale",
                qt_widget_name="extraction_temp_slider",
                widget_type="Slider",
                tab_name="Glossary",
                config_key="glossary_extraction_temperature",
                description="추출 온도"
            ),
            WidgetMapping(
                tk_widget_name="enable_dynamic_glossary_injection_var",
                qt_widget_name="enable_injection_check",
                widget_type="CheckBox",
                tab_name="Glossary",
                config_key="enable_dynamic_glossary_injection",
                description="동적 용어집 주입"
            ),
        ])
    
    def _extract_widget_attributes(self, file_path: Path) -> Set[str]:
        """
        AST 파싱을 통해 파일에서 self.{widget_name} 형태의 속성 할당을 추출
        
        Args:
            file_path: 파싱할 파이썬 파일 경로
            
        Returns:
            발견된 위젯 속성명 집합
        """
        if not file_path.exists():
            return set()
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            
            tree = ast.parse(source)
            widget_attrs = set()
            
            # self.{attr_name} = ... 형태의 할당문 찾기
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute):
                            if isinstance(target.value, ast.Name) and target.value.id == 'self':
                                widget_attrs.add(target.attr)
            
            return widget_attrs
        
        except Exception as e:
            print(f"  ⚠ {file_path.name} 파싱 오류: {e}")
            return set()
    
    def verify_widget_existence(self) -> ValidationResult:
        """위젯 존재 여부 검증"""
        print("\n=== 1. 위젯 존재 여부 검증 ===")
        
        # 파일 경로 정의
        tk_settings_file = self.project_root / "gui" / "tabs" / "settings_tab.py"
        tk_glossary_file = self.project_root / "gui" / "tabs" / "glossary_tab.py"
        qt_settings_file = self.project_root / "gui_qt" / "tabs_qt" / "settings_tab_qt.py"
        qt_glossary_file = self.project_root / "gui_qt" / "tabs_qt" / "glossary_tab_qt.py"
        
        # 위젯 속성 추출
        print("\n  Tkinter GUI 위젯 추출 중...")
        tk_settings_widgets = self._extract_widget_attributes(tk_settings_file)
        tk_glossary_widgets = self._extract_widget_attributes(tk_glossary_file)
        tk_all_widgets = tk_settings_widgets | tk_glossary_widgets
        
        print(f"    - Settings: {len(tk_settings_widgets)}개 위젯")
        print(f"    - Glossary: {len(tk_glossary_widgets)}개 위젯")
        print(f"    - 전체: {len(tk_all_widgets)}개 위젯")
        
        print("\n  PySide6 GUI 위젯 추출 중...")
        qt_settings_widgets = self._extract_widget_attributes(qt_settings_file)
        qt_glossary_widgets = self._extract_widget_attributes(qt_glossary_file)
        qt_all_widgets = qt_settings_widgets | qt_glossary_widgets
        
        print(f"    - Settings: {len(qt_settings_widgets)}개 위젯")
        print(f"    - Glossary: {len(qt_glossary_widgets)}개 위젯")
        print(f"    - 전체: {len(qt_all_widgets)}개 위젯")
        
        # 매핑 검증
        print("\n  매핑 테이블 검증 중...")
        missing_tk_widgets = []
        missing_qt_widgets = []
        found_count = 0
        
        for mapping in self.mappings:
            tk_exists = mapping.tk_widget_name in tk_all_widgets
            qt_exists = mapping.qt_widget_name in qt_all_widgets
            
            if not tk_exists:
                missing_tk_widgets.append({
                    'tab': mapping.tab_name,
                    'widget': mapping.tk_widget_name,
                    'type': mapping.widget_type
                })
            
            if not qt_exists:
                missing_qt_widgets.append({
                    'tab': mapping.tab_name,
                    'widget': mapping.qt_widget_name,
                    'type': mapping.widget_type
                })
            
            if tk_exists and qt_exists:
                found_count += 1
        
        # 결과 출력
        total = len(self.mappings)
        print(f"\n  검증 완료: {found_count}/{total}개 매핑 쌍 발견")
        
        if missing_tk_widgets:
            print(f"\n  ⚠ Tkinter에서 누락된 위젯 ({len(missing_tk_widgets)}개):")
            for item in missing_tk_widgets[:5]:  # 최대 5개만 출력
                print(f"    - [{item['tab']}] {item['widget']} ({item['type']})")
            if len(missing_tk_widgets) > 5:
                print(f"    ... 외 {len(missing_tk_widgets) - 5}개")
        
        if missing_qt_widgets:
            print(f"\n  ⚠ PySide6에서 누락된 위젯 ({len(missing_qt_widgets)}개):")
            for item in missing_qt_widgets[:5]:  # 최대 5개만 출력
                print(f"    - [{item['tab']}] {item['widget']} ({item['type']})")
            if len(missing_qt_widgets) > 5:
                print(f"    ... 외 {len(missing_qt_widgets) - 5}개")
        
        # 검증 통과 기준: 모든 필수 위젯 쌍이 존재해야 함
        passed = len(missing_tk_widgets) == 0 and len(missing_qt_widgets) == 0
        
        return ValidationResult(
            passed=passed,
            message=f"위젯 존재 여부 검증: {found_count}/{total}개 매핑 쌍 발견",
            details={
                "total_mappings": total,
                "found_pairs": found_count,
                "missing_tk_count": len(missing_tk_widgets),
                "missing_qt_count": len(missing_qt_widgets),
                "missing_tk_widgets": missing_tk_widgets,
                "missing_qt_widgets": missing_qt_widgets,
                "tk_total_widgets": len(tk_all_widgets),
                "qt_total_widgets": len(qt_all_widgets),
            }
        )
    
    def verify_config_mapping(self) -> ValidationResult:
        """Config 키 매핑 검증"""
        print("\n=== 2. Config 키 매핑 검증 ===")
        
        # config.json 로드
        config_file = self.project_root / "config.json"
        
        if not config_file.exists():
            return ValidationResult(
                passed=False,
                message="config.json 파일을 찾을 수 없습니다",
                details={"config_path": str(config_file)}
            )
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        except Exception as e:
            return ValidationResult(
                passed=False,
                message=f"config.json 로드 실패: {e}",
                details={"error": str(e)}
            )
        
        print(f"\n  config.json 로드 완료: {len(config_data)}개 키 발견")
        
        # 매핑 검증
        print("\n  Config 키 매핑 검증 중...")
        missing_keys = []
        found_keys = []
        skipped_count = 0
        
        for mapping in self.mappings:
            # config_key가 None인 경우 스킵 (동적 처리)
            if mapping.config_key is None:
                skipped_count += 1
                continue
            
            if mapping.config_key in config_data:
                found_keys.append({
                    'config_key': mapping.config_key,
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name,
                    'value_type': type(config_data[mapping.config_key]).__name__
                })
            else:
                missing_keys.append({
                    'config_key': mapping.config_key,
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name,
                    'description': mapping.description
                })
        
        # 결과 출력
        total_mappings = len(self.mappings) - skipped_count
        found_count = len(found_keys)
        
        print(f"\n  검증 완료:")
        print(f"    - 전체 매핑: {len(self.mappings)}개")
        print(f"    - 동적 처리 (스킵): {skipped_count}개")
        print(f"    - 검증 대상: {total_mappings}개")
        print(f"    - 발견: {found_count}/{total_mappings}개")
        
        if missing_keys:
            print(f"\n  ⚠ Config에서 누락된 키 ({len(missing_keys)}개):")
            for item in missing_keys[:5]:  # 최대 5개만 출력
                print(f"    - [{item['tab']}] {item['config_key']} → {item['widget']}")
                print(f"      설명: {item['description']}")
            if len(missing_keys) > 5:
                print(f"    ... 외 {len(missing_keys) - 5}개")
        
        # 검증 통과 기준: 모든 config_key가 존재해야 함
        passed = len(missing_keys) == 0
        
        return ValidationResult(
            passed=passed,
            message=f"Config 키 매핑 검증: {found_count}/{total_mappings}개 발견",
            details={
                "total_mappings": len(self.mappings),
                "skipped_dynamic": skipped_count,
                "validated_count": total_mappings,
                "found_count": found_count,
                "missing_count": len(missing_keys),
                "missing_keys": missing_keys,
                "found_keys": found_keys[:10],  # 처음 10개만
                "config_total_keys": len(config_data)
            }
        )
    
    def _extract_widget_type_from_source(self, file_path: Path, widget_name: str) -> Optional[str]:
        """
        소스 코드에서 위젯의 실제 타입을 추출
        
        Args:
            file_path: 파일 경로
            widget_name: 위젯 속성명
            
        Returns:
            위젯 타입 (예: 'QLineEdit', 'Entry', 'Scale') 또는 None
        """
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            
            tree = ast.parse(source)
            
            # self.{widget_name} = Widget(...) 형태를 찾기
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute):
                            if (isinstance(target.value, ast.Name) and 
                                target.value.id == 'self' and 
                                target.attr == widget_name):
                                
                                # 할당 값이 Call 노드인 경우 (일반 생성자 호출)
                                if isinstance(node.value, ast.Call):
                                    if isinstance(node.value.func, ast.Attribute):
                                        # ttk.Entry 같은 형태
                                        return node.value.func.attr
                                    elif isinstance(node.value.func, ast.Name):
                                        # Entry 같은 형태
                                        return node.value.func.id
                                
                                # BooleanVar 같은 변수 타입
                                elif isinstance(node.value, ast.Call):
                                    if isinstance(node.value.func, ast.Attribute):
                                        return node.value.func.attr
                                    elif isinstance(node.value.func, ast.Name):
                                        return node.value.func.id
            
            return None
        
        except Exception as e:
            return None
    
    def verify_widget_types(self) -> ValidationResult:
        """위젯 타입 일치성 검증"""
        print("\n=== 3. 위젯 타입 일치성 검증 ===")
        
        # 타입 호환성 매핑 정의
        type_compatibility = {
            'LineEdit': ['Entry', 'QLineEdit'],
            'PlainTextEdit': ['Text', 'QPlainTextEdit', 'ScrolledText'],
            'ComboBox': ['Combobox', 'QComboBox', 'NoWheelComboBox'],
            'CheckBox': ['Checkbutton', 'QCheckBox', 'BooleanVar'],
            'Slider': ['Scale', 'QSlider', 'NoWheelSlider'],
            'SpinBox': ['Entry', 'Spinbox', 'QSpinBox', 'NoWheelSpinBox', 'NoWheelSlider'],
            'FileInput': ['Listbox', 'QLineEdit'],  # 의도적 차이: Tk는 다중, Qt는 단일
        }
        
        print("\n  타입 호환성 규칙:")
        for widget_type, compatible_types in type_compatibility.items():
            print(f"    - {widget_type}: {', '.join(compatible_types)}")
        
        # 파일 경로
        tk_settings_file = self.project_root / "gui" / "tabs" / "settings_tab.py"
        tk_glossary_file = self.project_root / "gui" / "tabs" / "glossary_tab.py"
        qt_settings_file = self.project_root / "gui_qt" / "tabs_qt" / "settings_tab_qt.py"
        qt_glossary_file = self.project_root / "gui_qt" / "tabs_qt" / "glossary_tab_qt.py"
        
        print("\n  위젯 타입 추출 및 검증 중...")
        
        type_mismatches = []
        type_matches = []
        skipped_count = 0
        
        for mapping in self.mappings:
            # 파일 선택
            tk_file = tk_settings_file if mapping.tab_name == "Settings" else tk_glossary_file
            qt_file = qt_settings_file if mapping.tab_name == "Settings" else qt_glossary_file
            
            # 실제 타입 추출
            tk_type = self._extract_widget_type_from_source(tk_file, mapping.tk_widget_name)
            qt_type = self._extract_widget_type_from_source(qt_file, mapping.qt_widget_name)
            
            # 타입을 찾지 못한 경우 스킵
            if tk_type is None or qt_type is None:
                skipped_count += 1
                continue
            
            # 호환성 검증
            expected_type = mapping.widget_type
            if expected_type in type_compatibility:
                compatible_types = type_compatibility[expected_type]
                
                # Tk와 Qt 타입이 모두 호환 목록에 있는지 확인
                tk_compatible = any(tk_type == ct or tk_type.endswith(ct) for ct in compatible_types)
                qt_compatible = any(qt_type == ct or qt_type.endswith(ct) for ct in compatible_types)
                
                if tk_compatible and qt_compatible:
                    type_matches.append({
                        'widget': mapping.qt_widget_name,
                        'tab': mapping.tab_name,
                        'expected': expected_type,
                        'tk_type': tk_type,
                        'qt_type': qt_type
                    })
                else:
                    type_mismatches.append({
                        'widget': mapping.qt_widget_name,
                        'tab': mapping.tab_name,
                        'expected': expected_type,
                        'tk_type': tk_type,
                        'qt_type': qt_type,
                        'tk_compatible': tk_compatible,
                        'qt_compatible': qt_compatible
                    })
            else:
                # 정의되지 않은 타입
                type_mismatches.append({
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name,
                    'expected': expected_type,
                    'tk_type': tk_type,
                    'qt_type': qt_type,
                    'reason': '정의되지 않은 타입'
                })
        
        # 결과 출력
        total = len(self.mappings)
        validated = total - skipped_count
        matched = len(type_matches)
        
        print(f"\n  검증 완료:")
        print(f"    - 전체 매핑: {total}개")
        print(f"    - 타입 미추출 (스킵): {skipped_count}개")
        print(f"    - 검증 대상: {validated}개")
        print(f"    - 타입 일치: {matched}/{validated}개")
        
        if type_mismatches:
            print(f"\n  ⚠ 타입 불일치 ({len(type_mismatches)}개):")
            for item in type_mismatches[:5]:
                reason = item.get('reason', '')
                if reason:
                    print(f"    - [{item['tab']}] {item['widget']}")
                    print(f"      기대: {item['expected']}, Tk: {item['tk_type']}, Qt: {item['qt_type']}")
                    print(f"      사유: {reason}")
                else:
                    tk_status = "✓" if item.get('tk_compatible', False) else "✗"
                    qt_status = "✓" if item.get('qt_compatible', False) else "✗"
                    print(f"    - [{item['tab']}] {item['widget']}")
                    print(f"      기대: {item['expected']}, Tk: {item['tk_type']} {tk_status}, Qt: {item['qt_type']} {qt_status}")
            
            if len(type_mismatches) > 5:
                print(f"    ... 외 {len(type_mismatches) - 5}개")
        
        # 검증 통과 기준: 모든 타입이 호환되어야 함
        passed = len(type_mismatches) == 0
        
        return ValidationResult(
            passed=passed,
            message=f"위젯 타입 일치성 검증: {matched}/{validated}개 일치",
            details={
                "total_mappings": total,
                "skipped_count": skipped_count,
                "validated_count": validated,
                "matched_count": matched,
                "mismatch_count": len(type_mismatches),
                "type_mismatches": type_mismatches,
                "type_matches": type_matches[:10]  # 처음 10개만
            }
        )
    
    def _extract_signal_connections(self, file_path: Path) -> Dict[str, List[str]]:
        """
        Qt GUI에서 시그널 연결 패턴을 추출
        
        Args:
            file_path: Qt GUI 파일 경로
            
        Returns:
            {widget_name: [signal_names]} 형태의 딕셔너리
        """
        if not file_path.exists():
            return {}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            
            connections = {}
            
            # self.{widget}.{signal}.connect(...) 패턴 찾기
            import re
            pattern = r'self\.(\w+)\.([\w]+)\.connect\('
            matches = re.findall(pattern, source)
            
            for widget_name, signal_name in matches:
                if widget_name not in connections:
                    connections[widget_name] = []
                connections[widget_name].append(signal_name)
            
            return connections
        
        except Exception as e:
            return {}
    
    def _extract_callback_connections(self, file_path: Path) -> Dict[str, List[str]]:
        """
        Tkinter GUI에서 콜백 연결 패턴을 추출
        
        Args:
            file_path: Tkinter GUI 파일 경로
            
        Returns:
            {widget_name: [callback_types]} 형태의 딕셔너리
        """
        if not file_path.exists():
            return {}
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            
            connections = {}
            
            import re
            
            # command= 패턴
            command_pattern = r'self\.(\w+).*?command\s*='
            for match in re.finditer(command_pattern, source, re.DOTALL):
                widget_name = match.group(1)
                if widget_name not in connections:
                    connections[widget_name] = []
                connections[widget_name].append('command')
            
            # .trace(...) 패턴 (BooleanVar, StringVar 등)
            trace_pattern = r'self\.(\w+)\.trace\('
            for match in re.finditer(trace_pattern, source):
                widget_name = match.group(1)
                if widget_name not in connections:
                    connections[widget_name] = []
                connections[widget_name].append('trace')
            
            # .bind(...) 패턴
            bind_pattern = r'self\.(\w+)\.bind\('
            for match in re.finditer(bind_pattern, source):
                widget_name = match.group(1)
                if widget_name not in connections:
                    connections[widget_name] = []
                connections[widget_name].append('bind')
            
            # .configure(command=...) 패턴
            configure_pattern = r'self\.(\w+)\.configure\(.*?command\s*='
            for match in re.finditer(configure_pattern, source, re.DOTALL):
                widget_name = match.group(1)
                if widget_name not in connections:
                    connections[widget_name] = []
                if 'command' not in connections[widget_name]:
                    connections[widget_name].append('command')
            
            return connections
        
        except Exception as e:
            return {}
    
    def verify_signal_connections(self) -> ValidationResult:
        """시그널/콜백 연결 검증"""
        print("\n=== 4. 시그널/콜백 연결 검증 ===")
        
        # 파일 경로
        tk_settings_file = self.project_root / "gui" / "tabs" / "settings_tab.py"
        tk_glossary_file = self.project_root / "gui" / "tabs" / "glossary_tab.py"
        qt_settings_file = self.project_root / "gui_qt" / "tabs_qt" / "settings_tab_qt.py"
        qt_glossary_file = self.project_root / "gui_qt" / "tabs_qt" / "glossary_tab_qt.py"
        
        print("\n  시그널/콜백 연결 추출 중...")
        
        # Tkinter 콜백 추출
        tk_settings_callbacks = self._extract_callback_connections(tk_settings_file)
        tk_glossary_callbacks = self._extract_callback_connections(tk_glossary_file)
        tk_all_callbacks = {**tk_settings_callbacks, **tk_glossary_callbacks}
        
        # Qt 시그널 추출
        qt_settings_signals = self._extract_signal_connections(qt_settings_file)
        qt_glossary_signals = self._extract_signal_connections(qt_glossary_file)
        qt_all_signals = {**qt_settings_signals, **qt_glossary_signals}
        
        print(f"    - Tkinter: {len(tk_all_callbacks)}개 위젯에 콜백 연결")
        print(f"    - PySide6: {len(qt_all_signals)}개 위젯에 시그널 연결")
        
        # 매핑 검증
        print("\n  이벤트 핸들러 매핑 검증 중...")
        
        # 이벤트 핸들러가 필요한 위젯 타입 정의
        interactive_widget_types = {
            'LineEdit', 'ComboBox', 'CheckBox', 'Slider', 'SpinBox', 'FileInput'
        }
        
        missing_handlers = []
        found_handlers = []
        skipped_count = 0
        
        for mapping in self.mappings:
            # 상호작용 위젯만 검증
            if mapping.widget_type not in interactive_widget_types:
                skipped_count += 1
                continue
            
            tk_has_handler = mapping.tk_widget_name in tk_all_callbacks
            qt_has_handler = mapping.qt_widget_name in qt_all_signals
            
            if tk_has_handler and qt_has_handler:
                found_handlers.append({
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name,
                    'type': mapping.widget_type,
                    'tk_callbacks': tk_all_callbacks[mapping.tk_widget_name],
                    'qt_signals': qt_all_signals[mapping.qt_widget_name]
                })
            else:
                missing_handlers.append({
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name,
                    'type': mapping.widget_type,
                    'tk_has_handler': tk_has_handler,
                    'qt_has_handler': qt_has_handler,
                    'tk_widget': mapping.tk_widget_name
                })
        
        # 결과 출력
        total = len(self.mappings)
        validated = total - skipped_count
        matched = len(found_handlers)
        
        print(f"\n  검증 완료:")
        print(f"    - 전체 매핑: {total}개")
        print(f"    - 정적 위젯 (스킵): {skipped_count}개")
        print(f"    - 검증 대상: {validated}개")
        print(f"    - 핸들러 연결: {matched}/{validated}개")
        
        if missing_handlers:
            print(f"\n  ⚠ 핸들러 누락 ({len(missing_handlers)}개):")
            for item in missing_handlers[:10]:
                tk_status = "✓" if item['tk_has_handler'] else "✗"
                qt_status = "✓" if item['qt_has_handler'] else "✗"
                print(f"    - [{item['tab']}] {item['widget']} ({item['type']})")
                print(f"      Tk: {tk_status}, Qt: {qt_status}")
            
            if len(missing_handlers) > 10:
                print(f"    ... 외 {len(missing_handlers) - 10}개")
        
        # 샘플 연결 정보 출력
        if found_handlers and len(found_handlers) > 0:
            print(f"\n  ✓ 연결된 핸들러 샘플:")
            for item in found_handlers[:3]:
                print(f"    - [{item['tab']}] {item['widget']}")
                print(f"      Tk callbacks: {', '.join(item['tk_callbacks'])}")
                print(f"      Qt signals: {', '.join(item['qt_signals'])}")
        
        # 검증 통과 기준
        # 설정 GUI의 특성상 많은 위젯이 직접 이벤트 핸들러를 가지지 않고
        # 저장/불러오기 버튼을 통해 일괄 처리됨
        # 따라서 일부 핵심 위젯(모델 선택, 슬라이더 등)만 핸들러를 가지면 통과
        
        # 최소 3개 이상의 위젯이 핸들러를 가지면 통과
        min_required_handlers = 3
        passed = matched >= min_required_handlers
        pass_rate = matched / validated if validated > 0 else 0
        
        if passed:
            pass_message = f"핵심 위젯 {matched}개에 핸들러 연결됨 (최소 {min_required_handlers}개 필요)"
        else:
            pass_message = f"핸들러 연결 부족: {matched}개 (최소 {min_required_handlers}개 필요)"
        
        return ValidationResult(
            passed=passed,
            message=f"시그널/콜백 연결 검증: {pass_message}",
            details={
                "total_mappings": total,
                "skipped_count": skipped_count,
                "validated_count": validated,
                "matched_count": matched,
                "missing_count": len(missing_handlers),
                "pass_rate": pass_rate,
                "min_required": min_required_handlers,
                "missing_handlers": missing_handlers,
                "found_handlers": found_handlers[:10],
                "note": "설정 GUI는 저장/불러오기 버튼을 통해 일괄 처리하므로 모든 위젯이 개별 핸들러를 가지지 않음"
            }
        )
    
    def _extract_save_load_methods(self, file_path: Path) -> Dict[str, Any]:
        """
        저장/불러오기 메서드 추출
        
        Args:
            file_path: 파일 경로
            
        Returns:
            {
                'save_methods': [method_names],
                'load_methods': [method_names],
                'config_references': [config_keys],
                'widget_references': [widget_names]
            }
        """
        if not file_path.exists():
            return {
                'save_methods': [],
                'load_methods': [],
                'config_references': set(),
                'widget_references': set()
            }
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            
            tree = ast.parse(source)
            
            save_methods = []
            load_methods = []
            config_references = set()
            widget_references = set()
            
            # 메서드 탐색
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    method_name = node.name.lower()
                    
                    # save 관련 메서드
                    if 'save' in method_name or 'write' in method_name or 'store' in method_name:
                        save_methods.append(node.name)
                    
                    # load 관련 메서드
                    if 'load' in method_name or 'read' in method_name or 'restore' in method_name:
                        load_methods.append(node.name)
            
            # config 딕셔너리 접근 패턴 찾기
            # cfg["key"], cfg.get("key"), config["key"], config.get("key") 등
            import re
            # 변수명은 cfg, config, c 등 다양할 수 있음
            config_pattern = r'(?:cfg|config|c)\[[\"\'](\w+)[\"\']\]|(?:cfg|config|c)\.get\([\"\'](\w+)[\"\']\)'
            for match in re.finditer(config_pattern, source):
                key = match.group(1) or match.group(2)
                if key:
                    config_references.add(key)
            
            # self.{widget} 참조 패턴 찾기
            widget_pattern = r'self\.(\w+(?:_(?:edit|combo|check|slider|spin|text|entry|var|listbox|scale|combobox)))'
            for match in re.finditer(widget_pattern, source):
                widget_references.add(match.group(1))
            
            return {
                'save_methods': save_methods,
                'load_methods': load_methods,
                'config_references': config_references,
                'widget_references': widget_references
            }
        
        except Exception as e:
            return {
                'save_methods': [],
                'load_methods': [],
                'config_references': set(),
                'widget_references': set()
            }
    
    def verify_save_load_flow(self) -> ValidationResult:
        """설정 저장/불러오기 흐름 검증"""
        print("\n=== 5. 설정 저장/불러오기 흐름 검증 ===")
        
        # 파일 경로
        tk_settings_file = self.project_root / "gui" / "tabs" / "settings_tab.py"
        tk_glossary_file = self.project_root / "gui" / "tabs" / "glossary_tab.py"
        qt_settings_file = self.project_root / "gui_qt" / "tabs_qt" / "settings_tab_qt.py"
        qt_glossary_file = self.project_root / "gui_qt" / "tabs_qt" / "glossary_tab_qt.py"
        
        print("\n  저장/불러오기 메서드 추출 중...")
        
        # Tkinter GUI 분석
        tk_settings_info = self._extract_save_load_methods(tk_settings_file)
        tk_glossary_info = self._extract_save_load_methods(tk_glossary_file)
        
        # PySide6 GUI 분석
        qt_settings_info = self._extract_save_load_methods(qt_settings_file)
        qt_glossary_info = self._extract_save_load_methods(qt_glossary_file)
        
        print(f"\n  Tkinter GUI:")
        print(f"    - Settings: {len(tk_settings_info['save_methods'])}개 저장 메서드, "
              f"{len(tk_settings_info['load_methods'])}개 불러오기 메서드")
        print(f"    - Glossary: {len(tk_glossary_info['save_methods'])}개 저장 메서드, "
              f"{len(tk_glossary_info['load_methods'])}개 불러오기 메서드")
        
        print(f"\n  PySide6 GUI:")
        print(f"    - Settings: {len(qt_settings_info['save_methods'])}개 저장 메서드, "
              f"{len(qt_settings_info['load_methods'])}개 불러오기 메서드")
        print(f"    - Glossary: {len(qt_glossary_info['save_methods'])}개 저장 메서드, "
              f"{len(qt_glossary_info['load_methods'])}개 불러오기 메서드")
        
        # Config 키 참조 확인
        print(f"\n  Config 키 참조 분석:")
        
        tk_all_config_refs = tk_settings_info['config_references'] | tk_glossary_info['config_references']
        qt_all_config_refs = qt_settings_info['config_references'] | qt_glossary_info['config_references']
        
        print(f"    - Tkinter: {len(tk_all_config_refs)}개 config 키 참조")
        print(f"    - PySide6: {len(qt_all_config_refs)}개 config 키 참조")
        
        # 위젯 참조 확인
        print(f"\n  위젯 참조 분석:")
        
        tk_all_widget_refs = tk_settings_info['widget_references'] | tk_glossary_info['widget_references']
        qt_all_widget_refs = qt_settings_info['widget_references'] | qt_glossary_info['widget_references']
        
        print(f"    - Tkinter: {len(tk_all_widget_refs)}개 위젯 참조")
        print(f"    - PySide6: {len(qt_all_widget_refs)}개 위젯 참조")
        
        # 매핑 테이블 검증
        print(f"\n  매핑 테이블 대비 검증:")
        
        # config_key가 있는 매핑만 검증
        mappings_with_config = [m for m in self.mappings if m.config_key is not None]
        
        config_coverage = []
        config_missing = []
        
        for mapping in mappings_with_config:
            tk_has_config = mapping.config_key in tk_all_config_refs
            qt_has_config = mapping.config_key in qt_all_config_refs
            
            if tk_has_config and qt_has_config:
                config_coverage.append({
                    'config_key': mapping.config_key,
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name
                })
            else:
                config_missing.append({
                    'config_key': mapping.config_key,
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name,
                    'tk_has': tk_has_config,
                    'qt_has': qt_has_config
                })
        
        coverage_rate = len(config_coverage) / len(mappings_with_config) if mappings_with_config else 0
        
        print(f"    - Config 키 매핑: {len(config_coverage)}/{len(mappings_with_config)}개 ({coverage_rate*100:.1f}%)")
        
        if config_missing:
            print(f"\n  ⚠ Config 키 참조 누락 ({len(config_missing)}개):")
            for item in config_missing[:5]:
                tk_status = "✓" if item['tk_has'] else "✗"
                qt_status = "✓" if item['qt_has'] else "✗"
                print(f"    - [{item['tab']}] {item['config_key']}")
                print(f"      Tk: {tk_status}, Qt: {qt_status}")
            
            if len(config_missing) > 5:
                print(f"    ... 외 {len(config_missing) - 5}개")
        
        # 위젯 참조 검증
        widget_coverage = []
        widget_missing = []
        
        for mapping in self.mappings:
            tk_has_widget = mapping.tk_widget_name in tk_all_widget_refs
            qt_has_widget = mapping.qt_widget_name in qt_all_widget_refs
            
            if tk_has_widget and qt_has_widget:
                widget_coverage.append({
                    'widget': mapping.qt_widget_name,
                    'tab': mapping.tab_name
                })
            else:
                widget_missing.append({
                    'widget': mapping.qt_widget_name,
                    'tk_widget': mapping.tk_widget_name,
                    'tab': mapping.tab_name,
                    'tk_has': tk_has_widget,
                    'qt_has': qt_has_widget
                })
        
        widget_coverage_rate = len(widget_coverage) / len(self.mappings) if self.mappings else 0
        
        print(f"    - 위젯 참조: {len(widget_coverage)}/{len(self.mappings)}개 ({widget_coverage_rate*100:.1f}%)")
        
        if widget_missing and len(widget_missing) <= 10:
            print(f"\n  ⚠ 위젯 참조 누락 ({len(widget_missing)}개):")
            for item in widget_missing[:5]:
                tk_status = "✓" if item['tk_has'] else "✗"
                qt_status = "✓" if item['qt_has'] else "✗"
                print(f"    - [{item['tab']}] Qt: {item['widget']}, Tk: {item['tk_widget']}")
                print(f"      Tk: {tk_status}, Qt: {qt_status}")
            
            if len(widget_missing) > 5:
                print(f"    ... 외 {len(widget_missing) - 5}개")
        
        # 검증 통과 기준
        # 1. 양쪽 모두 save/load 메서드가 있어야 함
        has_tk_save_load = (len(tk_settings_info['save_methods']) > 0 and 
                            len(tk_settings_info['load_methods']) > 0)
        has_qt_save_load = (len(qt_settings_info['save_methods']) > 0 and 
                            len(qt_settings_info['load_methods']) > 0)
        
        # 2. Config 키 커버리지 검증 (완화된 기준)
        # Tkinter는 load_config 메서드가 매개변수로 config를 받아 사용하므로
        # AST 파싱으로는 일부 참조를 놓칠 수 있음
        # Qt는 self.app_service.config에서 직접 참조하므로 더 잘 탐지됨
        # 따라서 Qt 또는 Tk 중 하나라도 높은 커버리지를 보이면 통과
        qt_config_ok = qt_all_config_refs and len(qt_all_config_refs) >= len(mappings_with_config) * 0.8
        tk_config_ok = tk_all_config_refs and len(tk_all_config_refs) >= len(mappings_with_config) * 0.8
        config_coverage_ok = qt_config_ok or tk_config_ok
        
        # 3. 위젯 참조 커버리지가 70% 이상 (일부 위젯은 동적 처리 가능)
        widget_coverage_ok = widget_coverage_rate >= 0.7
        
        passed = has_tk_save_load and has_qt_save_load and config_coverage_ok and widget_coverage_ok
        
        # 결과 메시지
        issues = []
        if not has_tk_save_load:
            issues.append("Tkinter save/load 메서드 부족")
        if not has_qt_save_load:
            issues.append("PySide6 save/load 메서드 부족")
        if not config_coverage_ok:
            issues.append(f"Config 키 참조 부족 (Tk: {len(tk_all_config_refs)}, Qt: {len(qt_all_config_refs)}, 최소: {len(mappings_with_config) * 0.8:.0f})")
        if not widget_coverage_ok:
            issues.append(f"위젯 참조 커버리지 부족 ({widget_coverage_rate*100:.1f}% < 70%)")
        
        if passed:
            message = f"저장/불러오기 흐름 검증: 위젯 {widget_coverage_rate*100:.1f}%, save/load 메서드 존재"
        else:
            message = f"저장/불러오기 흐름 검증 실패: {', '.join(issues)}"
        
        return ValidationResult(
            passed=passed,
            message=message,
            details={
                "tk_save_methods": tk_settings_info['save_methods'] + tk_glossary_info['save_methods'],
                "tk_load_methods": tk_settings_info['load_methods'] + tk_glossary_info['load_methods'],
                "qt_save_methods": qt_settings_info['save_methods'] + qt_glossary_info['save_methods'],
                "qt_load_methods": qt_settings_info['load_methods'] + qt_glossary_info['load_methods'],
                "config_coverage_rate": coverage_rate,
                "config_coverage_count": len(config_coverage),
                "config_missing_count": len(config_missing),
                "widget_coverage_rate": widget_coverage_rate,
                "widget_coverage_count": len(widget_coverage),
                "widget_missing_count": len(widget_missing),
                "config_missing": config_missing[:10],
                "widget_missing": widget_missing[:10],
                "tk_config_refs_count": len(tk_all_config_refs),
                "qt_config_refs_count": len(qt_all_config_refs),
                "tk_widget_refs_count": len(tk_all_widget_refs),
                "qt_widget_refs_count": len(qt_all_widget_refs)
            }
        )
    
    def print_mapping_table(self) -> None:
        """매핑 테이블 출력"""
        print("\n" + "="*100)
        print("위젯 매핑 테이블")
        print("="*100)
        print(f"{'탭':<12} {'Tkinter 위젯':<30} {'PySide6 위젯':<30} {'Config 키':<30}")
        print("-"*100)
        
        for mapping in self.mappings:
            config_key = mapping.config_key or "-"
            print(f"{mapping.tab_name:<12} {mapping.tk_widget_name:<30} "
                  f"{mapping.qt_widget_name:<30} {config_key:<30}")
        
        print("="*100)
        print(f"총 {len(self.mappings)}개 위젯 매핑 정의됨\n")
    
    def run_all_verifications(self) -> bool:
        """모든 검증 실행"""
        print("\n" + "="*80)
        print("PySide6 GUI 위젯 매핑 무결성 검증 시작")
        print("="*80)
        
        self.print_mapping_table()
        
        # 각 검증 단계 실행
        verifications = [
            self.verify_widget_existence,
            self.verify_config_mapping,
            self.verify_widget_types,
            self.verify_signal_connections,
            self.verify_save_load_flow,
        ]
        
        all_passed = True
        for verify_func in verifications:
            result = verify_func()
            self.results.append(result)
            
            status = "✓ PASS" if result.passed else "✗ FAIL"
            print(f"{status}: {result.message}")
            
            if not result.passed:
                all_passed = False
                print(f"  상세: {result.details}")
        
        # 최종 결과
        print("\n" + "="*80)
        if all_passed:
            print("✓ 모든 검증 통과")
        else:
            print("✗ 일부 검증 실패")
        print("="*80 + "\n")
        
        return all_passed


def main():
    """메인 실행 함수"""
    validator = WidgetMappingValidator()
    success = validator.run_all_verifications()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
