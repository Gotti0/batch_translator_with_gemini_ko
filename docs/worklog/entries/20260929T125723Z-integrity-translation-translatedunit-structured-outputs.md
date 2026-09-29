---
id: 20260929T125723Z-integrity-translation-translatedunit-structured-outputs
created_at: 2026-09-29T12:57:23Z
title: 무결성 번역 파이프라인 TranslatedUnit Structured Outputs 연동
scope: data-pipeline
project: Neo_Batch_Translator
summary: TranslationService 무결성 번역 호출 시 response_schema에 list[TranslatedUnit]을 주입하여 서버단 JSON Schema 강제 및 OpenRouter 힐링 플러그인이 번역 호출에도 100% 작동하도록 연동했다.
---

## 배경

`TranslationService`의 무결성 번역은 100~200개 문단을 JSON 배열로 번역하는 구조다. 과거에는 OpenAI의 `json_object` 규격이 루트 배열(`[...]`)을 거부한다는 이유로 `response_schema` 없이 `response_mime_type: "application/json"`만 전달하고 프롬프트 텍스트에만 의존했다.

## 문제

`response_schema`가 생략되면서 `OpenAICompatibleClient`는 `response_format`을 비워둔 채 자유 텍스트 생성 모드로 요청을 보냈다. 이로 인해 OpenRouter의 `response-healing` 플러그인(포맷 명시 시에만 발동)이 작동하지 못했고, 모델(`z-ai/glm-5.3-flash`)이 키 앞의 여는 큰따옴표를 빠뜨린 `{"id": "24574", text": ...}`와 같은 오타를 출력하여 `json.loads` 파싱 실패와 정규식 Fallback을 유발했다.

## 결정과 근거

1. **`list[TranslatedUnit]` 스키마 주입**: `TranslationService`의 무결성 번역 호출 `gen_config`에 `"response_schema": list[TranslatedUnit]`를 공식 등록했다.
2. **클라이언트 래핑 및 플러그인 연동 자동화**: 클라이언트(`OpenAICompatibleClient`)가 `list[TranslatedUnit]`를 수신하면 최상위를 `items` 오브젝트로 감싼 OpenAI 규격 `json_schema`(`TranslatedUnit_list`)로 자동 변환하고 OpenRouter `response-healing` 플러그인을 함께 전송한다.
3. **인스턴스 및 딕셔너리 이중 수용**: 응답 수신 루프에서 클라이언트가 이미 파싱한 `TranslatedUnit` 인스턴스는 즉시 수용하고, 원시 딕셔너리(`dict`) 형태도 그대로 변환할 수 있도록 타입 분기를 추가하여 모든 백엔드 클라이언트(Gemini, OpenAICompatible, Ollama 등)와의 완전한 호환성을 유지했다.

- 버린 접근: 프롬프트 지시문만 더 강화하는 방안. LLM 특성상 프롬프트 지시만으로는 수만 개의 문단을 번역할 때 간헐적인 따옴표 누락 오타를 물리적으로 0%로 만들 수 없으므로, API 레벨의 문법 제약(Structured Outputs)을 거는 것이 근본 해결책이었다.

## 검증

OpenRouter 실제 엔드포인트(`z-ai/glm-5.3-flash`)로 `list[TranslatedUnit]` 스키마를 전달한 실호출 테스트에서 1.09초 만에 문법 결함 없이 Pydantic `TranslatedUnit` 인스턴스로 파싱됨을 확인했다. `test_integrity_recovery.py`에 단위 테스트를 추가하고, 전체 테스트 462개가 전원 통과했다.

## 미해결

오픈소스 로컬 모델 중 Pydantic의 `Union[str, int]` 타입을 지원하지 못하는 구형 엔진이 있을 수 있다. 이 경우 `OpenAICompatibleClient`의 400 fallback(`json_object`)이 작동하므로 작업 중단은 없으나, 필요시 `TranslatedUnit.id` 타입을 단일 문자열로 통일하는 방안을 검토할 수 있다.
