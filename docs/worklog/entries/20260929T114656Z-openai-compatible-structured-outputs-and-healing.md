---
id: 20260929T114656Z-openai-compatible-structured-outputs-and-healing
created_at: 2026-09-29T11:46:56Z
title: OpenAI 호환 클라이언트 Structured Outputs 규격 준수 및 OpenRouter 힐링 플러그인 도입
scope: data-pipeline
project: Neo_Batch_Translator
summary: OpenAICompatibleClient에 json_schema 기반 Structured Outputs 변환, root array 래핑 어댑터, OpenRouter response-healing 플러그인 주입 및 400 fallback을 구현해 JSON 문법 파싱 결함을 차단했다.
---

## 배경

OpenAI API는 2024년 8월 이후 Pydantic 모델을 JSON Schema로 강제하는 `type: "json_schema"` Structured Outputs를 지원한다. 이전 구현의 `OpenAICompatibleClient`는 구형 JSON 모드(`type: "json_object"`)만 다루었으며, `list[ExtractedEntity]`나 `list[ApiGlossaryTerm]` 같은 루트 배열 스키마는 `json_object`의 최상위 객체 제약과 충돌한다는 이유로 `response_format`을 비워둔 채 프롬프트에만 의존했다.

## 문제

OpenRouter를 통해 서빙되는 `z-ai/glm-5.3-flash` 등 타사/오픈소스 모델은 `response_format` 제약이 없으면 소설 대화문 내 큰따옴표(`"..."`)를 이스케이프(`\"`)하지 않고 날것으로 출력하거나, 토큰 컷오프 시 쉼표가 누락된 채 닫히지 않는 JSON을 생성했다. 이로 인해 백그라운드 인물 메모 추출 및 용어집 추출에서 빈번한 `JSONDecodeError`와 파싱 복구 비용이 발생했다.

## 결정과 근거

1. **`json_schema` Structured Outputs 변환 탑재**: `response_schema`가 제공되면 Pydantic의 `model_json_schema()` 또는 `TypeAdapter.json_schema()`를 추출하고 `$defs` 참조를 펼친 뒤(`_inline_json_schema_refs`), `response_format: {"type": "json_schema", "json_schema": {"strict": true, ...}}` 페이로드를 생성해 모델에 스키마 준수를 강제한다.
2. **최상위 배열(`array`) 객체 래핑**: OpenAI 및 OpenRouter API 규격은 `json_schema`의 최상위가 반드시 `type: "object"`여야 하며 루트 배열을 거부한다. 이를 해결하기 위해 `list[T]` 스키마가 들어오면 `{"type": "object", "properties": {"items": array_schema}, "required": ["items"]}`로 감싸서 전송하고, 수신 시 기존 `_coerce_to_schema`가 `items` 키를 풀어 단일/리스트 모델로 안전하게 역직렬화하도록 연결했다.
3. **OpenRouter `response-healing` 플러그인 연동**: `base_url`이 `openrouter.ai`인 경우 페이로드에 `"plugins": [{"id": "response-healing"}]`을 주입하여, LLM이 문법 실수를 하더라도 OpenRouter 서버단에서 1ms 이내에 따옴표 이스케이프와 쉼표를 자동 치료하도록 했다.
4. **호환 프로바이더 400 에러 Fallback**: 일부 로컬 호환 서버(구형 Ollama, vLLM 등)가 `json_schema`를 지원하지 않아 400을 반환할 경우, 자동으로 `type: "json_object"`로 대체하여 1회 재시도하도록 안전망을 뒀다.

- 버린 접근: `TranslationService`의 무결성 번역에까지 `json_object`를 강제하는 방안. 무결성 번역 프롬프트는 `[{"id": 0, ...}]` 배열을 요구하므로 객체를 강제하면 응답이 깨질 위험이 있어, 명시적 Pydantic 스키마가 제공된 호출에 한해 `json_schema`를 자동 구성하도록 유지했다.

## 검증

`test_openai_compatible_client.py`에서 단일 모델 및 `list[T]` 모델의 `json_schema` 구성, OpenRouter `response-healing` 플러그인 주입, 400 발생 시 `json_object` 모드 fallback 재시도 단위 테스트 14개를 작성하고 전원 통과를 확인했다. `pytest test/test_infrastructure/ test/test_domain/ -q` 279개 테스트가 모두 통과했다.

## 미해결

OpenRouter의 `response-healing` 플러그인은 비스트리밍(non-streaming) 요청에서만 작동한다. 향후 스트리밍 기반 번역 파이프라인으로 전환할 경우 클라이언트 측 실시간 JSON 복구 스트림 파서가 별도로 필요하다.
