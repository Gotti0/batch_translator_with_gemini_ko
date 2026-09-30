---
id: 20260930T021710Z-multi-provider-content-safety-exception
created_at: 2026-09-30T02:17:10Z
title: 멀티 프로바이더 콘텐츠 안전 예외 분류 및 무결성 번역 검열 복구 연동
scope: data-pipeline
project: Neo_Batch_Translator
summary: Antigravity CLI 등 멀티 프로바이더에서 안전 필터 거부 시 일반 에러 대신 BtgApiContentSafetyException을 발생시키고 TranslationService 검열 복구(Binary Split)와 연결했다
---

## 배경

대용량 소설 번역 중 성인/민감 콘텐츠가 포함된 청크 번역 시, 모델이 안전 정책(Google Generative AI Prohibited Use Policy 등)에 따라 출력을 거절할 수 있다. 번역 서비스(`TranslationService`)는 검열 예외를 감지하면 해당 청크를 이진 분할(`_binary_split_integrity_retry`)하여 민감하지 않은 나머지 단락들을 구제하고 문제 단락만 원문으로 보존하도록 설계되어 있다.

## 문제

1. `AntigravityCliClient`는 CLI가 Google 안전 필터 거절 안내(`status: "ERROR"`, 성적으로 노골적인 묘사 관련 정책 위반 등)를 반환해도 `_is_rate_limited`만 검사하고 검열 분류 로직이 없어, 이를 일반 클라이언트 통신 오류인 `BtgApiClientException`으로 던졌다.
2. `TranslationService`는 검열 복구 핸들러에서 오직 `GeminiContentSafetyException`만 감시하고 있었다.
3. `core/exceptions.py`에 선언된 `BtgApiContentSafetyException`은 CLI나 호환 클라이언트에서 전혀 raise되지 않았고, `GeminiContentSafetyException`과의 상속 관계도 단절되어 있었다.
4. 이로 인해 Antigravity CLI 등에서 검열 발생 시 분할 복구 루틴이 전혀 발동하지 않고 일반 예외로 전체 번역 작업이 즉시 중단되었다.

## 결정과 근거

1. **공통 콘텐츠 안전 오류 판별기 도입 (`infrastructure/base_client.py`)**: `is_content_safety_error(err_msg)`를 정의하여 "안전 필터", "prohibited use policy", "content_filter", "safety guideline" 등 주요 공급자의 검열 패턴을 표준화했다. 오탐(False Positive)을 막기 위해 CLI 비정상 종료, `status: "ERROR"`, 구조화 출력 JSON 파싱 실패 등 오류 문맥에서만 검사한다.
2. **클라이언트별 `BtgApiContentSafetyException` 분기 추가**: `AntigravityCliClient`, `ClaudeCliClient`, `CodexCliClient`, `OpenAICompatibleClient`에서 거절 메시지 및 `content_filter` finish_reason 감지 시 `BtgApiContentSafetyException`을 발생시키도록 구현했다.
3. **예외 계층 통합 및 다중 상속**: `GeminiContentSafetyException`이 `(GeminiApiException, BtgApiContentSafetyException)`을 다중 상속하도록 수정하여 하위 호환성을 유지하면서 공통 예외 트리로 통합했다.
4. **도메인 계층 캐치 확장**: `TranslationService`의 무결성 번역(`_translate_integrity_chunk_with_retry`) 및 일반 번역(`translate_text_async`)에서 `except (GeminiContentSafetyException, BtgApiContentSafetyException)`을 감지하도록 연결하여 CLI 프로바이더에서도 Binary Split 검열 복구가 정상 발동하도록 했다.

- 버린 접근: `GeminiContentSafetyException`을 단일 공통 예외로 강제 통일하고 기존 예외 클래스를 삭제하는 방안. 기존 수십 개의 테스트와 SDK 직접 종속 모듈에서 회귀 위험이 커, 다중 상속 및 복수 캐치를 병행하는 무파괴(non-breaking) 확장을 선택했다.

## 검증

- `test_antigravity_cli_client.py`에 Google Prohibited Use Policy 거절 응답 수신 시 `BtgApiContentSafetyException` 발생 단위 테스트 2건 추가 및 통과.
- `test_claude_cli_client.py`, `test_codex_cli_client.py`, `test_openai_compatible_client.py`에 각각 콘텐츠 안전 감지 단위 테스트 추가 및 통과.
- `test_integrity_recovery.py`에 `BtgApiContentSafetyException` 발생 시 Binary Split 재시도로 성공 복구되는 통합 테스트 추가 및 통과.
- 전체 단위 테스트 476개 전원 통과 (469 passed, 7 skipped).

## 미해결

- CLI 프로바이더가 향후 신규 오류 문구(새로운 언어의 거절 문구 등)를 도입할 경우 패턴 카탈로그의 지속적인 업데이트가 필요하다.
