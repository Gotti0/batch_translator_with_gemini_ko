---
id: 20260928T231434Z-integrity-regex-fallback-recovery
created_at: 2026-09-28T23:14:34Z
title: integrity-regex-fallback-recovery
scope: data-pipeline
project: Neo_Batch_Translator
summary: 무결성 번역 JSON 파싱 실패 시 대사 인용부호 및 문법 결함을 단위별로 구제하는 정규식 Fallback 복구 로직 구현
---

## 배경

무결성 번역 모드에서 대용량 청크(99개 항목) 번역 시, LLM(`z-ai/glm-5.3-flash`)이 반환한 장문 응답(5,656자 이상)에서 `Expecting ',' delimiter: line 1 column 5657 (char 5656)` JSON 파싱 오류가 발생하여 연쇄적인 Binary Split이 촉발되었다.

## 문제

웹소설 번역문에는 대화문 큰따옴표(`"..."`)가 빈번하게 포함되는데, 모델이 내부 따옴표를 `\"`로 이스케이프하지 않거나 객체 간 구분 쉼표를 누락하면 표준 `json.loads`는 전체 배열 파싱을 중단한다.
기존 복구 로직은 단순 앞뒤 대괄호 슬라이싱뿐이어서 내부 문법 오류를 복구하지 못하고 전체 단위를 실패 처리하여 분할 재시도로 이어졌다.
또한 `TranslatedUnit.id`가 `str`로 엄격히 제한되어 있어, 모델이 정수형 ID(`{"id": 0}`)를 반환할 때 Pydantic 유효성 검사 오류로 파싱된 단위가 조용히 버려지는 결함도 존재했다.

## 결정과 근거

- `TranslationService`:
  - `json.loads(..., strict=False)`를 적용해 비이스케이프 제어 문자를 허용했다.
  - 전체 파싱 실패 시 단위별 정규식 Fallback 복구 메서드(`_recover_integrity_units_from_raw_text`)를 호출하도록 구현했다. 따옴표 이스케이프 누락이나 쉼표 누락이 있더라도 블록 분할 및 정규식 매칭을 통해 유효한 단위들을 개별 추출해 복구한다.
  - 프롬프트 지시문(`integrity_prompt_suffix`)에 번역문 내부 따옴표 이스케이프 또는 한국어 인용부호 사용 권장 문구를 보강했다.
  - 단위 조립 시 ID를 명시적으로 문자열로 강제 변환하여 누락 오판정을 차단했다.
- `core.dtos.TranslatedUnit`: `id` 필드 타입을 `Union[str, int]`로 확장하여 정수형과 문자열형 ID를 모두 수용하도록 정정했다.
- `OpenAICompatibleClient`: JSON 파싱 시 `strict=False`를 지정하여 제어 문자 오류를 완화했다.

## 검증

- 단위 테스트: `test/test_domain/test_integrity_recovery.py`를 작성하여 다음을 검증했다.
  - 대화문 내부 비이스케이프 큰따옴표 복구
  - 객체 간 쉼표 누락 복구
  - 대체 키(`text`, `translation`) 및 문자열 ID 호환
  - 문법 오류 응답 수신 시 Binary Split 없이 정규식 fallback으로 단 1회에 복구 완료
- 회귀 테스트: 전체 277개 단위 테스트 통과 확인.

## 미해결

- 정규식 Fallback은 대다수 문법 오류를 구제하지만, 모델 출력이 극심하게 손상되어 ID 패턴조차 식별 불가능한 단위는 여전히 누락으로 감지된다. 이는 Targeted Retry 예산 내에서 해당 단위만 선별 재요청하여 보완한다.
