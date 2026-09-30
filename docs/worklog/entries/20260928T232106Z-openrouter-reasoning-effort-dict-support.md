---
id: 20260928T232106Z-openrouter-reasoning-effort-dict-support
created_at: 2026-09-28T23:21:06Z
title: OpenRouter 추론 강도 딕셔너리 포맷 지원
scope: data-pipeline
project: Neo_Batch_Translator
summary: OpenRouter 엔드포인트 호출 시 추론 강도 파라미터를 reasoning.effort 딕셔너리로 분기 전달해 GLM-5.3-flash 등 추론 모델의 12분 지연을 6초대로 단축
---

## 배경

OpenAI 호환 API 클라이언트는 프로바이더 설정에 따라 reasoning_effort 값을 페이로드 최상위 키로 전달하도록 구성되어 있었다. 사용자는 OpenRouter 엔드포인트와 z-ai/glm-5.3-flash 모델을 연동하여 번역 작업을 진행했다.

## 문제

z-ai/glm-5.3-flash 모델을 통한 무결성 번역 청크 호출 시 단일 요청에 12분(755초) 이상이 소요되는 극심한 지연이 발생했다. 조사 결과 OpenRouter의 통합 추론 제어 사양은 최상위 reasoning_effort 문자열 대신 reasoning: {"effort": "low"} 형태의 딕셔너리 구조를 요구하며, 최상위 reasoning_effort는 무시되어 모델의 기본 추론 강도인 max로 동작하고 있었다.

## 결정과 근거

OpenAICompatibleClient의 페이로드 구성부에서 엔드포인트 URL에 openrouter.ai가 포함되어 있을 경우 reasoning: {"effort": effort} 딕셔너리로 변환하고, 그 외 표준 OpenAI 호환 엔드포인트는 기존의 최상위 reasoning_effort 문자열을 유지하도록 분기했다.

- 버린 접근: 모든 프로바이더에 일률적으로 reasoning: {"effort": ...} 딕셔너리를 보내는 방안. 표준 OpenAI API 및 vLLM 등 일부 서빙 프레임워크는 최상위 reasoning_effort 문자열만 허용하므로 400 Bad Request 오류를 유발할 위험이 있어 URL 기반으로 분기했다.
- 버린 접근: reasoning_effort를 None으로 강제하여 모델 기본 동작에 맡기는 방안. GLM-5.3은 추론이 필수 활성화되어 있고 기본값이 max이므로 명시적으로 low를 지정하지 않으면 지속적으로 수백 초 지연이 발생한다.

## 검증

OpenRouter 실호출 테스트 스크립트를 통해 reasoning: {"effort": "low"} 페이로드 전송 시 z-ai/glm-5.3-flash가 약 6초 만에 정상 응답(HTTP 200)을 반환함을 확인했다. 또한 단위 테스트를 추가해 openrouter.ai 주소에서는 reasoning 객체로 주입되고 표준 OpenAI 주소에서는 reasoning_effort 문자열로 주입됨을 검증했으며, 전체 테스트 스위트 455건이 모두 통과했다.

## 미해결

OpenRouter 외에 자체 프록시 도메인을 사용하여 OpenRouter를 경유하는 환경에서는 URL 기반 분기로 감지되지 않을 수 있다. 향후 프로바이더 식별자를 URL 외에 명시적 타입으로 분리하는 구조 개선을 검토할 수 있다.
