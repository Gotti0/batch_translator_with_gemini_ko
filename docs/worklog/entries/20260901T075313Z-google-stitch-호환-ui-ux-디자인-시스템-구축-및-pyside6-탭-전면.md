---
id: 20260901T075313Z-google-stitch-호환-ui-ux-디자인-시스템-구축-및-pyside6-탭-전면
created_at: 2026-09-01T07:53:13Z
title: Google-Stitch-호환-UI-UX-디자인-시스템-구축-및-PySide6-탭-전면-현대화
scope: dev-infra
project: Neo_Batch_Translator
summary: Google Stitch 표준 9개 카테고리 준수 DESIGN.md 사양서 및 토큰 시스템 구축, PySide6 5대 탭 컴포넌트 전면 현대화
---

## 배경
Gemini Interactions API 마이그레이션 이후 구형 UI/UX 구조와 혼재되어 있던 파라미터 제어기, 하드코딩된 색상 및 실시간 관측성 결여 문제를 해결하고 일관된 디자인 시스템 표준을 확립할 필요가 대두되었다.

## 문제
1. `gui_qt/` 전반에 하드코딩된 색상 코드와 임의 여백이 산재하여 테마 전환 및 가독성 유지가 어려웠음.
2. 번역, 용어집 추출, 무결성 검수 과정에서 실시간 진행 상태와 품질 지표(처리량, 속도, ETA, 오류/의심 청크 수)가 직관적으로 노출되지 못함.
3. 스크롤 중 마우스 휠에 의한 값 변조 사고 및 창 리사이징 시 레이아웃 잘림 현상이 발생함.

## 결정과 근거
1. **Google Stitch 9대 카테고리 완전 준수**:
   - `DESIGN.md`를 단일 진실 공급원으로 삼고, `gui_qt/design_tokens.py`와 `gui_qt/tokens.css`를 생성하여 Python 위젯과 웹/HTML 프리뷰 간 토큰을 100% 동기화함.
   - 인디고(`#8E75FF`)와 바이올렛(`#C3A1FF`)을 기본 액센트로 채택하고, WCAG AA(4.5:1 이상) 및 AAA 대비율을 전수 검증함.
2. **모던 KPI 및 상태 위젯 표준화**:
   - `StatCard`, `PillBadge`, `ModeCard`를 공통 컴포넌트로 분리하여 `SettingsTabQt`, `GlossaryTabQt`, `ReviewTabQt` 3대 핵심 탭에 일관되게 바인딩함.
3. **사용성 및 휠 충돌 방어**:
   - `NoWheelSpinBox`, `NoWheelSlider`를 전역 적용하여 휠 스크롤 오작동을 차단하고 `QScrollArea` 세이프가드로 최소 해상도(1024x720) 대응력을 확보함.

## 검증
- `pytest` 실행: UI 위젯 단위 테스트 14건, Interactions API 16건, 무결성 검수 4건 등 총 37건의 테스트를 100% 통과(37 passed, 1 skipped).
- `oh-my-product:design-verify` 실행: 위반 사항 0건, 토큰 일치율 100% 달성.

## 미해결
- 라이트 모드 전환 시 일부 복합 위젯(Custom QSplitter 핸들 등)의 미세 콘트라스트 추가 미세 조정 여지 검토.
