---
id: 20260903T070040Z-qfluentwidgets-기반-windows-11-fluent-ui-현대화-및-2단
created_at: 2026-09-03T07:00:40Z
title: QFluentWidgets-기반-Windows-11-Fluent-UI-현대화-및-2단-분할-Diff-뷰어-구축
scope: dev-infra
project: Neo_Batch_Translator
summary: 웹 기반 GUI를 기각하고 QFluentWidgets(PySide6) 기반 MSFluentWindow와 2단 분할 동기화 Diff 뷰어로 5대 탭 전면 현대화
---

## 배경
Gemini Interactions API 기반 비동기 번역 도구인 Neo_Batch_Translator의 UI를 모던 데스크톱 스타일로 개편하기 위해 새로운 GUI 엔진 도입(Tauri, Electron, PyWebView, QML, Flet 등)에 대한 브레인스토밍을 진행하였다.

## 문제
1. 웹 기술 기반 런타임(Electron, Tauri, PyWebView)은 가벼운 순수 로컬 네이티브 데스크톱 유틸리티를 지향하는 철학에 부합하지 않으며, 추가 프로세스/IPC 오버헤드가 발생함.
2. 기존 PySide6 환경은 500여 줄의 수동 QSS에 의존하여 Windows 11 Fluent Design(Mica/Acrylic 머티리얼, 통일된 반응형 인터랙션) 구현과 유지보수가 번거로웠음.
3. 검수 탭(ReviewTab)의 원문-번역문 비교 화면이 상하 수직으로 배치되어 장편 소설 및 기술 문서의 문맥 대조 가독성이 떨어졌음.

## 결정과 근거
1. **웹 기반 프레임워크 기각 및 QFluentWidgets (PySide6-Fluent-Widgets) 채택**:
   - Flet이나 웹 기반 프레임워크는 대규모 텍스트의 정밀 Diff 하이라이팅 및 가상화 스크롤링 위젯 생태계가 부재하여 부적합함.
   - QFluentWidgets는 기존 Python 비동기 번역 코어(asyncio/qasync), 도메인 로직 및 QTextDocument 텍스트 엔진을 100% 보존하면서 완성형 Windows 11 컴포넌트를 조립할 수 있음.
2. **MSFluentWindow 및 좌측 사이드바 5대 탭 구조 구축**:
   - Mica/Acrylic 반투명 블러 효과와 커스텀 프레임리스 타이틀바를 적용하고, 접이식 좌측 사이드바에 5개 인터페이스(Settings, Glossary, Review, Activity, Log)를 배치함.
   - OS 테마(다크/라이트) 및 Windows 개인 설정 액센트 컬러를 자동 연동하고, 하단에 원클릭 테마 토글 버튼을 제공함.
3. **ReviewTab 좌우 2단 분할(Side-by-Side) 및 동기화 스크롤 구현**:
   - 원문(좌)과 번역문(우)을 QSplitter 기반 1:1 수평 분할로 배치하고 수직 스크롤 이벤트를 상호 바인딩하여 문맥 대조 생산성을 극대화함.
4. **InfoBarManager 및 SegmentedWidget 업그레이드**:
   - 작업 완료/오류 시 우측 상단 플로팅 토스트를 띄우는 InfoBarManager를 도입하고, ModeChipGroup을 SegmentedWidget 슬라이딩 캡슐로 교체함.

## 검증
- `test/test_settings_tab_qt_simple.py`: 14개 설정 탭 회귀 테스트 100% 통과 (14 passed).
- `test/test_fluent_window_lifecycle.py`: MSFluentWindow 5대 탭 인스턴스화, 테마 토글, 2단 분할 스크롤 동기화, InfoBarManager, SegmentedWidget 등 5개 신규 통합 테스트 100% 통과 (5 passed).
- `test/test_gui_lifecycle_pytest.py`: ModeSelectorGroup 하위 호환성 및 라이프사이클 테스트 100% 통과 (4 passed).
- 총 23건의 GUI 테스트 스위트 전수 통과 확인.

## 미해결
- 추후 실제 Windows 11 환경에서 시스템 트레이 최소화 모드 및 고해상도 다중 모니터 이동 시의 Mica 머티리얼 반응성 지속 모니터링 필요.
