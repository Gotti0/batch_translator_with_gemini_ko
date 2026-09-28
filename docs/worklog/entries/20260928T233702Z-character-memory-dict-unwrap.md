---
id: 20260928T233702Z-character-memory-dict-unwrap
created_at: 2026-09-28T23:37:02Z
title: 인물 메모 추출 딕셔너리 래퍼 언래핑
scope: data-pipeline
project: Neo_Batch_Translator
summary: LLM이 list[ExtractedEntity] 응답을 characters 딕셔너리로 감싸 반환할 때 스키마 검증 실패 및 인물 메모 유실을 방지하도록 언래핑 로직 개선
---

## 배경

번역 완료 후 비동기로 실행되는 인물 메모 추출기(MemoryExtractor)는 `response_schema=list[ExtractedEntity]`를 지정하여 LLM에 인물 정보 목록을 요청한다.

## 문제

z-ai/glm-5.3-flash 등 일부 LLM이 최상위 배열 대신 `{"characters": [{"name": ...}]}` 딕셔너리 구조로 응답을 감싸 반환했다. 이로 인해 두 가지 결함이 발생했다:
1. `OpenAICompatibleClient._coerce_to_schema`가 `TypeAdapter(list[ExtractedEntity]).validate_python(parsed)`를 직접 호출하면서 `Input should be a valid list` 검증 경고를 출력하고 원본 딕셔너리를 그대로 반환했다.
2. `memory_extractor._to_dicts`는 `entities`와 `items` 키만 확인하고 `characters` 키를 확인하지 않아 최외곽 딕셔너리 전체를 `[response]` 단일 객체로 취급했고, `name` 필드가 없어 추출된 인물 항목이 모두 버려졌다.

## 결정과 근거

클라이언트 스키마 강제 계층과 도메인 파싱 계층 양쪽에서 방어적 언래핑을 적용했다.

- `OpenAICompatibleClient._coerce_to_schema`(및 `ollama_client`, `antigravity_cli_client`): 스키마 검증 1차 실패 시 `characters`, `entities`, `items`, `terms`, `data`, `results` 등의 주요 키 또는 딕셔너리 내부의 임의의 리스트 값을 탐색하여 재검증하고 통과 시 인스턴스 목록을 반환하도록 개선했다.
- `memory_extractor._to_dicts`: 딕셔너리 응답 수신 시 `characters`, `entities`, `items` 등 다양한 래퍼 키를 검사해 내부 리스트를 올바르게 꺼내도록 보강했다.
- 버린 접근: 프롬프트에 `characters 키를 쓰지 말라`는 금지 문구만 추가하는 방안. LLM마다 학습된 고유 응답 패턴(소설/인물 관련 작업 시 characters 래핑)이 있어 프롬프트 제약만으로는 간헐적 누락을 원천 차단하기 어렵다.

## 검증

`{"characters": [{"name": "惠蓉", ...}]}` 응답을 모킹하여 `OpenAICompatibleClient.generate_text_async`가 `ExtractedEntity` 인스턴스 목록으로 정상 언래핑 및 검증하는 단위 테스트와 `_to_dicts` 정규화 테스트를 통과시켰다. 도메인 및 인프라 테스트 274건 모두 성공했다.

## 미해결

인물 메모뿐 아니라 향후 새로운 엔티티 추출기가 추가될 경우를 대비해 스키마 모델에 루트 필드 별칭(Field alias / validation_alias)을 선언적으로 매핑하는 방식을 고려할 수 있다.
