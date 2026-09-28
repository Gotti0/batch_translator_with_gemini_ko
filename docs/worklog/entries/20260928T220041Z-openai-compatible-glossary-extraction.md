---
id: 20260928T220041Z-openai-compatible-glossary-extraction
created_at: 2026-09-28T22:00:41Z
title: openai-compatible-glossary-extraction
scope: data-pipeline
project: Neo_Batch_Translator
summary: OpenAI 호환 API 연동 시 엔드포인트 누락(404), 모델명 우선순위 역전 및 마크다운 JSON 응답 파싱 실패로 인한 용어집 누락 문제 해결
---

## 배경

OpenRouter를 OpenAI 호환 프로바이더(`openai_compatible`)로 설정하고 `z-ai/glm-5.3-flash` 모델을 지정한 뒤 헬스체크 및 용어집 추출을 수행했다.

## 문제

1. 초기 헬스체크에서 404 Not Found 오류가 발생했다. 처음에는 모델 ID가 OpenRouter에 존재하지 않는 것으로 의심했으나 실제로는 등록된 모델이었고, OpenAICompatibleClient의 Base URL에 `/chat/completions` 엔드포인트 경로가 누락된 것이 원인이었다.
2. 헬스체크 성공 후 용어집 추출 단계에서 설정된 `z-ai/glm-5.3-flash` 대신 Gemini 모델명(`gemini-3.8-flash`)으로 요청이 발송되었다. 도메인 서비스가 호출 시 인자로 넘기는 `model_name`(Gemini 설정 기본값)이 OpenAICompatibleClient의 `default_model`보다 우선순위가 높았기 때문이다.
3. 모델이 마크다운 코드 블록(```json)으로 둘러싸인 JSON 문자열을 반환했을 때, SimpleGlossaryService가 문자열 응답을 모두 파싱 실패로 간주하고 빈 리스트를 반환하여 추출된 용어들이 모두 유실되었다.

## 결정과 근거

- Base URL 정규화: `OpenAICompatibleClient` 초기화 시 URL 끝이 `/chat/completions`로 끝나지 않으면 자동으로 붙여 사용자가 호스트/v1 주소만 입력해도 404가 발생하지 않도록 했다.
- 모델명 우선순위 조정: 도메인 서비스가 넘기는 `gemini-*` 계열의 기본 모델 인자는 무시하고, 클라이언트에 명시적으로 설정된 `default_model`을 최우선으로 적용하도록 했다.
- 메시지 및 스키마 변환 보강: `_prepare_messages`에서 `Content` 객체 지원을 추가하고, `generate_text_async`에서 `generation_config_dict` 및 `response_schema`를 처리하여 `TypeAdapter` 기반 Pydantic 변환 및 코드 블록 벗기기를 수행하게 했다.
- SimpleGlossaryService 폴백 파싱: 클라이언트가 문자열 형태로 JSON을 반환하더라도 마크다운 코드 블록과 외곽 괄호를 정규식으로 벗겨 `_parse_dict_list_to_dto`로 복구하도록 방어 로직을 추가했다.

## 검증

- `test_glossary_schema.py`: 마크다운 코드 블록으로 감싸진 문자열 응답으로부터 용어 DTO를 정상 추출하는 비동기 테스트 추가 통과.
- `test_openai_compatible_client.py`: Base URL 자동 완성, 모델명 우선순위, 메시지 변환, 코드 블록 제거, Pydantic 객체 변환 테스트 5종 추가 통과.
- 전체 테스트 스위트 455개 항목(448 통과, 7 스킵) 회귀 확인.

## 미해결

- OpenAI 호환 공급자마다 `response_format` 지원 여부가 다르므로, 스키마 제약이 동작하지 않는 일부 엔드포인트에서는 모델 자체의 프롬프트 준수 능력에 의존한다.
