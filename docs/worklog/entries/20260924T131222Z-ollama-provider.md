---
id: 20260924T131222Z-ollama-provider
created_at: 2026-09-24T13:12:22Z
title: Ollama 네이티브 API 프로바이더 추가
scope: data-pipeline
project: Neo_Batch_Translator
summary: 로컬 GPU의 오픈 모델로 API 과금 없이 번역하도록 Ollama 네이티브 REST API(/api/chat, /api/tags) 기반 OllamaClient를 추가하고 팩토리·AppService·설정 탭에 연결했다
---

## 배경

기존에는 Ollama를 "OpenAI 호환 API" 프로바이더에 `/v1/chat/completions` 주소를 넣어 쓰는 방법만 안내했다.

## 문제

1. **컨텍스트 잘림**: OpenAI 호환 엔드포인트로는 `num_ctx`를 지정할 수 없다. Ollama 기본 컨텍스트(2K~4K)는 청크 6,000자와 시스템 지시문·프리필 히스토리를 합친 번역 프롬프트보다 짧아 앞부분이 경고 없이 잘린다. 번역 누락으로 이어지지만 오류가 나지 않아 알아채기 어렵다.
2. **호출 형식 불일치**: 도메인 서비스는 GeminiClient 시그니처(google-genai `Content` 리스트, `system_instruction_text`, `generation_config_dict`)로 호출한다. `OpenAICompatibleClient._prepare_messages`는 `Content` 객체를 받지 못해 실제 번역 경로에서 ValueError가 난다.
3. **구조화 출력**: 무결성 모드는 `response_mime_type=application/json`, 용어집 추출은 `response_schema=list[ApiGlossaryTerm]`에 의존하는데, 이를 전달할 경로가 없었다.
4. **모델 목록**: 설치된 모델을 조회할 수 없어 모델 태그를 직접 입력해야 했다.

## 결정과 근거

- **네이티브 API 사용 (`infrastructure/ollama_client.py`)**: `/api/chat`에 `options.num_ctx`(기본 16384)를 실어 보낸다. 응답의 `prompt_eval_count`가 `num_ctx`에 닿거나 `done_reason=length`이면 경고 로그를 남긴다.
- **Gemini 호출 형식 변환**: `Content` 리스트의 `model` 역할을 `assistant`로 바꿔 멀티턴(프리필 히스토리)을 보존한다. `system_instruction_text`는 system 메시지로 넣는다. `generation_config_dict`의 temperature/top_p/top_k는 options로 옮긴다. `model_name` kwarg는 Gemini 설정값이므로 무시하고 `ollama_model`을 쓴다.
- **구조화 출력**: `response_schema`가 있으면 Pydantic `TypeAdapter`로 JSON Schema를 만들고 `$defs`/`$ref`를 펼쳐 `format`에 넣는다. 응답은 같은 타입으로 검증해 GeminiClient의 `response.parsed`와 같은 형태로 반환한다. 스키마 없이 JSON만 요구하면 `format: "json"`을 쓰고 파싱된 객체를 반환한다. 파싱에 실패하면 원문 텍스트를 반환해 기존의 분할 재시도 경로로 넘긴다.
- **추론 모델**: qwen3·deepseek-r1 계열이 본문 앞에 붙이는 `<think>...</think>` 블록을 제거한다.
- **오류 안내**: 연결 거부는 `ollama serve` 실행을, 404 모델 없음은 `ollama pull <모델>`을 안내한다. 429/503(대기열 초과)은 `BtgApiRateLimitException`으로 매핑한다.
- **헬스체크**: `/api/version`과 `/api/tags`로 서버 버전과 선택 모델 설치 여부를 확인한다. `:latest` 태그는 생략해도 설치된 것으로 본다.
- **설정 탭**: 프로바이더 목록에 "Ollama (로컬 LLM 서버)"를 추가했다. API Base URL 행을 서버 주소로 재사용하고, Ollama일 때만 "컨텍스트 길이" 스핀박스를 보인다. OpenAI 호환과 Ollama를 오갈 때 서로의 주소가 남아 있으면 각자 저장된 주소로 바꾼다.
- **모델 새로고침**: 비-Gemini 프로바이더는 저장된 클라이언트가 아니라 현재 UI 설정으로 목록을 조회하도록 `AppService.get_available_models(config_override)`를 추가했다. 이전에는 프로바이더를 바꾼 직후 새로고침하면 저장 전의 이전 프로바이더 목록이 나왔다. Gemini 경로는 바꾸지 않았다.
- **AppService 초기화**: `antigravity_cli`가 Gemini 분기로 빠져 Gemini API 키가 없으면 클라이언트가 만들어지지 않던 문제를 Ollama 분기를 추가하면서 함께 고쳤다.

## 검증

- `test/test_infrastructure/test_ollama_client.py`: 16개 통과. Base URL 정규화, Gemini 형식 변환, think 블록 제거, JSON/스키마 모드, Bearer 헤더, 오류 매핑, 모델 목록, 헬스체크를 다룬다.
- `test_llm_client_factory.py`(Ollama 생성)와 `test_settings_tab_provider.py`(전환·저장·로드·UI 설정 구성) 추가분 통과.
- 가짜 Ollama HTTP 서버를 띄우고 AppService로 표준 번역, 무결성 번역, 용어집 추출, 헬스체크, 모델 조회를 끝까지 실행해 확인했다. 실제 Ollama 서버와 모델로는 검증하지 않았다.
- 전체: 267 passed, 7 skipped.
