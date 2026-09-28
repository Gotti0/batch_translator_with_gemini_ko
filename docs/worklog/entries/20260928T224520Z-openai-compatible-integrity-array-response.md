---
id: 20260928T224520Z-openai-compatible-integrity-array-response
created_at: 2026-09-28T22:45:20Z
title: openai-compatible-integrity-array-response
scope: data-pipeline
project: Neo_Batch_Translator
summary: OpenAI 호환 API 무결성 번역 시 response_format json_object 강제로 인한 JSON 배열 출력 억제 및 이진 분할 연쇄 결함 정정
---

## 배경

OpenAI 호환 프로바이더(`openai_compatible`, `z-ai/glm-5.3-flash`)로 무결성(integrity) 모드 번역을 시작하자, 청크 1에서 JSON 파싱 실패가 연속 발생하며 이진 분할(Binary Split: 99 -> 49 -> 24 -> 12)이 반복되었다.

## 문제

이전 작업에서 `response_mime_type == "application/json"` 요청 시 API 페이로드에 `response_format = {"type": "json_object"}`를 전달하도록 구현했다.
그러나 OpenAI 호환 API 규격에서 `json_object`는 최상위 출력을 `{...}` 단일 객체로만 강제하며 `[...]` 배열 시작을 금지한다.
무결성 번역은 프롬프트로 각 문단이 담긴 JSON 배열(`[...]`) 출력을 요구하는데, 엔드포인트 측 문법 제약으로 인해 모델이 배열을 출력하지 못하고 임의의 단일 객체(`{'id': 0, 'text': ...}`)로 출력하여 반환했다.
이로 인해 TranslationService의 `isinstance(raw_response, list)` 검증에 실패하고 이진 분할이 연쇄적으로 발생했다.

## 결정과 근거

- `OpenAICompatibleClient`: `response_format = {"type": "json_object"}`의 무조건적 주입을 제거했다. 사용자가 명시적으로 `response_format`을 지정했거나 스키마가 단일 객체 타입인 경우에만 전달하고, 무결성 번역이나 용어집처럼 배열을 요구하는 호출에서는 페이로드에서 제외하여 모델이 자유롭게 `[...]` 배열을 출력할 수 있게 했다.
- `TranslationService`: 모델 응답이 마크다운 블록이 섞인 문자열이거나, 혹여 딕셔너리로 래핑되어(`units`, `translations`, `items`, `data` 등) 반환되더라도 안전하게 내부 배열을 추출할 수 있도록 파싱 방어 로직을 보강했다.

## 검증

- 실호출 검증: 실제 OpenRouter의 `z-ai/glm-5.3-flash`로 무결성 번역 청크 10개 항목을 요청하여, 이진 분할 없이 단 한 번의 호출로 10개 번역문이 담긴 유효한 JSON 배열이 정상 수신 및 파싱되는 것을 확인했다.
- 단위 테스트: `test_openai_compatible_client.py`에 배열 요청 시 `response_format`이 누락되지 않고 온전한 배열 결과를 반환함을 검증하는 테스트를 추가했다.
- 회귀 테스트: `test_glossary_schema.py`, `test_domain/`, `test_infrastructure/` 등 관련 테스트 273개 전체 통과 확인.

## 미해결

- 프롬프트 제약만으로 JSON 배열 출력을 유도하므로, 지시문 준수율이 현저히 떨어지는 소형 로컬 LLM의 경우 가끔 배열 대신 잡담이나 래핑 객체를 반환할 수 있다. 이는 보강된 파싱 정규식 및 딕셔너리 언래핑으로 1차 방어한다.
