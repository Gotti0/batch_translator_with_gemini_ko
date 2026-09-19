---
id: 20260903T072236Z-windows-11-fluent-design-기반-settingstab-전면-시각-리디
created_at: 2026-09-03T07:22:36Z
title: Windows-11-Fluent-Design-기반-SettingsTab-전면-시각-리디자인-및-Hero-드롭존-구축
scope: dev-infra
project: Neo_Batch_Translator
summary: 구형 폼 나열 방식을 폐기하고 Hero File DropZone, SettingCardGroup, ApiKeyManagerDialog, 다크 Mica Glass 테마를 전면 적용
---

## 배경
QFluentWidgets 셸 도입 후 실제 구동 화면을 검토한 결과, 창 껍데기만 현대화되었을 뿐 메인 설정 탭 내부가 구형 QFormLayout의 날것 폼(Raw Form) 형태로 노출되어 심각한 시각적 조악함을 유발하고 있었다.

## 문제
1. 카드(Card) 그룹핑 없이 휑한 회색 배경 위에 라벨과 인풋 박스가 무분별하게 나열되어 시각적 위계와 입체감이 전무함.
2. 45개의 비밀 API 키가 마스킹 없이 5줄짜리 거대한 텍스트 박스에 평문 노출되어 완성도와 보안성을 훼손함.
3. 비활성화된 Vertex AI 관련 GCP 입력 필드 3줄이 화면 30%를 불필요하게 잠식함.
4. 번역기 본연의 작업(파일 선택 및 번역 시작)이 하단의 자질구레한 설정 뒤로 밀려나 초점(Hero)이 실종됨.

## 결정과 근거
1. **작업 중심 Hero 레이아웃 전환**:
   - 상단에 대형 점선 테두리와 실시간 글자수/청크수 분석 뱃지를 탑재한 `DropZoneCard`를 전면 배치함.
   - 번역 모드 선택(`ModeChipGroup`)과 대형 `PrimaryPushButton`("번역 시작 🚀") 및 4대 KPI 대시보드를 최상단으로 끌어올림.
2. **API 키 관리의 모달화 (`ApiKeyManagerDialogQt`)**:
   - 메인 화면의 거대한 평문 텍스트박스를 완전히 제거하고, `PushSettingCard`("현재 N개 활성 키 로드됨")와 [키 관리 ⚙️] 버튼으로 축약함.
   - 클릭 시 전용 팝업 다이얼로그에서 마스킹된 키 목록 조회, 일괄 붙여넣기, 개별 삭제를 안전하게 지원함.
3. **Vertex AI 및 고급 파라미터의 아코디언 격리 (`ExpandSettingCard`)**:
   - Vertex AI는 평소 닫혀 있으며, 스위치 활성화 시에만 아래로 GCP 필드가 펼쳐지도록 아코디언화함.
   - Thinking Level, 청크 크기, RPM, 프롬프트 템플릿 등 2차 파라미터들도 단일 접이식 카드 안으로 격리하여 초기 화면을 극단적으로 심플하게 유지함.
4. **다크 모드(Theme.DARK) 기본 적용**:
   - 밋밋하고 탁한 라이트 모드 대신 Windows 11 Mica Glass 감성의 다크 차콜 표면과 블루 액센트를 기본 적용함.

## 검증
- `test/test_fluent_window_lifecycle.py`: `DropZoneCard` 메타데이터 파싱 및 시그널 방출, `ApiKeyManagerDialogQt` 마스킹 및 일괄 추가/삭제, `SettingsTabQt` Hero 구조 및 시그널 연동 등 8개 테스트 100% 통과 (8 passed).
- `test/test_settings_tab_qt_simple.py`: 다이얼로그 및 완료 시그널 14개 테스트 100% 통과 (14 passed).
- `test/test_gui_lifecycle_pytest.py`: 모드 셀렉터 및 하위 호환성 4개 테스트 100% 통과 (4 passed).
- 총 26건의 GUI 통합 테스트 스위트 전수 통과 확인.

## 미해결
- 실제 번역 실행 시 대용량 파일(1,000 청크 이상)에 대한 프로그레스 바 렌더링 퍼포먼스 모니터링 필요.
