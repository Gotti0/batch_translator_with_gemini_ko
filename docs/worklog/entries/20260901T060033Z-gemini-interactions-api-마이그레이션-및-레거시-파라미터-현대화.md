---
id: 20260901T060033Z-gemini-interactions-api-마이그레이션-및-레거시-파라미터-현대화
created_at: 2026-09-01T06:00:33Z
title: Gemini Interactions API 마이그레이션 및 레거시 파라미터 현대화
scope: dev-infra
project: Neo_Batch_Translator
summary: 구형 generateContent 기반 호출 로직과 폐기된 temperature/top_p/thinking_budget을 최신 Interactions API 및 thinking_level 체계로 전면 전환
---

## 배경
기존 코드베이스는 `google-genai` SDK의 구형 `client.aio.models.generate_content` 및 `GenerateContentConfig`를 사용하고 있었으며, Gemini 3.6+에서 완전히 지원 중단된 `temperature`, `top_p`, `thinking_budget` 등의 파라미터가 설정 파일, 도메인 서비스, GUI 레이어 전반에 잔존해 있었다.

## 문제
1. Gemini 3.6+ 모델 호출 시 `temperature`, `top_p`, `top_k`, `candidate_count`, `thinking_budget` 전달 시 HTTP 400 오류가 발생함.
2. `generate_content_stream` 청크 파싱과 일반 생성 파싱이 구형 응답 객체 구조에 의존하고 있어 최신 Interactions API의 `output_text` 및 `step.delta` 스트림과 호환되지 않음.
3. 구형 모델 식별자(`gemini-2.0-flash` 등)가 기본값으로 지정되어 최신 Gemini 3 모델의 성능과 동적 사고 이점을 활용하지 못함.

## 결정과 근거
1. **Interactions API 표준화**: `GeminiClient` 비동기 호출 엔진을 `client.aio.interactions.create`로 전면 교체함. 스트리밍 시 `event.event_type == "step.delta"` 및 `event.delta.type == "text"`를 검증하여 텍스트를 누적하고, 비스트리밍 시 `interaction.output_text`를 직접 활용하도록 구현함.
2. **사고 제어 및 폐기 파라미터 제거**:
   - `temperature`, `top_p`, `thinking_budget`을 `config_manager`, DTO, 도메인(`TranslationService`, `GlossaryService`), GUI(`SettingsTabQt`, `GlossaryTabQt`) 전 계층에서 제거함.
   - 사고 제어는 `thinking_level`(`"minimal"`, `"low"`, `"medium"`, `"high"`)로 일원화하고, 기본 모델을 `gemini-3.6-flash`로 갱신함.
3. **정형 출력 포맷 (`response_format`) 통합**: 용어집 추출 및 무결성 번역의 JSON 스키마 전달 시 최상위 `response_format={"type": "text", "mime_type": "application/json", "schema": ...}` 규격을 준수하도록 정규화함.
4. **하위 호환 및 레거시 모델 자동 승격 및 예외처리**:
   - `LEGACY_MODEL_MAP` 사전을 정의하여 20여 종의 구형 모델명(`gemini-2.0-flash`, `gemini-1.5-pro`, `gemini-pro` 등) 및 `models/` 접두사 인입 시 Gemini 3 최신 대응 모델로 자동 매핑.
   - 모델명이 `None`이거나 빈 문자열인 경우 기본 모델(`gemini-3.6-flash`)로 안전하게 대체.
   - 런타임 API 호출 중 404 / Model Not Found 예외 발생 시 `gemini-3.6-flash`로 자동 폴백하여 재시도하도록 2중 안전장치 구현.

## 검증
1. `pytest test/test_gemini_thinking_config.py`: `thinking_level` 전달, 레거시 모델 매핑 11개 패턴(None, 빈 문자열 포함), 런타임 404 자동 폴백 재시도 검증 통과 (16/16 passed).
2. `pytest` 전체 통합/단위 테스트: 48 passed, 1 skipped.

## 미해결
없음. 전체 인프라, 도메인, 설정, GUI 및 테스트 스위트의 마이그레이션 및 구형 모델 예외처리가 완료됨.
