---
id: 20260929T020331Z-single-dict-list-schema-coercion
created_at: 2026-09-29T02:03:31Z
title: 단일 객체 응답의 리스트 스키마 강제 변환 지원
scope: data-pipeline
project: Neo_Batch_Translator
summary: LLM이 list[ExtractedEntity] 스키마에 대해 단일 엔티티 {...} 딕셔너리를 반환할 때 [parsed] 리스트로 감싸 유효성 검사 경고를 방지하도록 개선
---

## 배경

인물 메모 추출 등 `list[T]` 형태의 Pydantic 스키마를 사용하는 API 호출에서, 추출 대상 인물이 1명만 발견될 경우 일부 LLM이 `[{...}]` 배열이 아닌 `{...}` 단일 JSON 객체만을 반환한다.

## 문제

`OpenAICompatibleClient._coerce_to_schema`는 `characters`, `entities` 등 딕셔너리 내부 키 언래핑을 지원했으나, 모델이 키 감싸기 없이 `{name: ..., note: ...}` 단일 객체를 직접 반환했을 때 `TypeAdapter(list[ExtractedEntity])`가 `Input should be a valid list` 오류를 발생시켰다. 이로 인해 스키마 검증 실패 경고 로그가 지속적으로 발생했다.

## 결정과 근거

`_coerce_to_schema`에서 수신된 데이터가 딕셔너리이고 단일 객체 검증이 실패한 경우, `adapter.validate_python([parsed])`로 단일 요소를 원소로 갖는 리스트 형태의 검증을 우선 시도하도록 추가했다.

- 근거: 스키마가 `list[T]`일 때 단일 객체 `{...}`는 항목 1개짜리 목록을 의도한 응답이 명백하므로, 불필요한 경고 로깅 없이 즉시 `[T(...)]` 목록으로 복원하는 것이 가장 자연스럽다.
- 동일한 구조를 `ollama_client` 및 `antigravity_cli_client`에도 동일하게 적용하여 프로바이더 간 일관성을 확보했다.

## 검증

단일 엔티티 딕셔너리(`{"name": "...", "note": "..."}`)가 주어졌을 때 `list[ExtractedEntity]` 스키마 검증을 거쳐 요소 1개의 리스트로 정상 변환되는지 단위 테스트(`test_generate_text_async_unwraps_single_dict_into_list_for_schema`)를 작성해 검증했다. 전체 관련 단위 테스트 21건 모두 통과했다.

## 미해결

없음.
