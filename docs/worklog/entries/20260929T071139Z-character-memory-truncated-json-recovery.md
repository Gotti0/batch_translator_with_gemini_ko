---
id: 20260929T071139Z-character-memory-truncated-json-recovery
created_at: 2026-09-29T07:11:39Z
title: 인물 메모 추출 잘린 JSON 복구 및 단일 엔티티 보존
scope: data-pipeline
project: Neo_Batch_Translator
summary: 인물 메모 추출 응답이 토큰 한도로 잘리거나 aliases 리스트를 포함한 단일 엔티티일 때의 파싱 실패 및 데이터 유실 방지
---

## 배경

번역 청크가 완료된 후 백그라운드에서 실행되는 인물 메모 추출기(MemoryExtractor)는 LLM 응답을 정규화하여 번역 기억 그래프에 새 인물 정보와 관계를 축적한다.

## 문제

두 가지 인물 메모 누락 요인이 식별되었다:
1. LLM 응답이 생성 길이 한도로 인해 중간에 잘린 채 끝나는 경우(예: note 필드 작성 중 따옴표 및 중괄호 미닫힘), `json.loads`가 실패하여 온전하게 작성된 앞부분의 인물 정보까지 전부 버려졌다.
2. `_to_dicts`의 딕셔너리 언래핑 과정에서 `name` 필드를 가진 단일 객체(`{"name": "...", "aliases": [...]}`)가 전달되었을 때, `response.values()`의 리스트 탐색 로직이 `aliases`를 발견하고 엔티티 객체 대신 문자열 리스트로 덮어써 인물이 누락되는 버그가 있었다.

## 결정과 근거

`domain/memory_extractor.py`에 접미사 보정, 정규식 추출기, 그리고 단일 엔티티 우선 보존 로직을 추가했다:
- `json.loads` 실패 시 1단계로 접미사 `'"}'`, `'"}]'` 등을 덧붙여 즉시 복구를 시도하고, 2단계로 `_recover_entities_from_raw_text`를 통해 완결된 필드들을 정규식으로 안전하게 추출한다.
- `_to_dicts`에서 `name in response`인 경우 이미 유효한 단일 엔티티이므로 리스트 탐색을 건너뛰고 `[response]`로 보존한다.
- 버린 접근: 생성 프롬프트에 `짧게만 쓰라`는 지침만 강화하는 방안. 복잡한 소설 인물 관계에서는 여전히 토큰 초과나 간헐적 잘림이 발생할 수 있어 수신 계층의 복구력이 필수적이다.

## 검증

잘린 인물 메모 JSON 텍스트를 모킹하여 접미사 복구 및 정규식 복구로 `name`과 `translated_name`이 정확히 추출되는지 `test_extractor_response_normalization` 단위 테스트로 검증했다. 전체 관련 11개 단위 테스트 모두 통과했다.

## 미해결

없음.
