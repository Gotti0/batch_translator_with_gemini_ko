---
id: 20260924T224145Z-ollama-and-antigravity-live-verification
created_at: 2026-09-24T22:41:45Z
title: ollama-and-antigravity-live-verification
scope: data-pipeline
project: Neo_Batch_Translator
summary: Ollama 및 Antigravity CLI 프로바이더 실호출 검증 과정에서 규명된 CLI 프롬프트 전달 한계, JSON 스키마 규격 불일치, 응답 키 매핑 결함을 해결하고 문서와 회귀 테스트를 정비함.
---

## 배경

PR #28 ~ #35에서 도입된 로컬 및 CLI 기반 LLM 프로바이더(Ollama, Google Antigravity CLI)의 실제 런타임 환경 연동 및 파이프라인 동작을 검증하고 관련 문서를 최신화함.

## 문제

1. Ollama 응답 파싱: 모델이 사고 과정(`thought`)이나 마크다운 코드 블록, 설명문을 섞어 반환할 때 단순 문자열 파싱이 실패함.
2. Antigravity CLI 프롬프트 전달 및 Windows 명령줄 한도: `agy -p -` 파이프 입력은 CLI 비대화형 실행에서 표준 입력을 읽지 않고 문자열 `"-"`로 해석하여 프롬프트가 무시됨. 또한 프롬프트를 명령줄 인자로 넘길 경우 Windows 명령줄 길이 상한(8,191자/32,767자)을 초과하여 `[WinError 206]`이 발생함.
3. Antigravity CLI `--json-schema` 규격 불일치: Pydantic의 `list[T]` 모델 등 최상위가 배열(`type: "array"`)인 스키마를 전달하면, `agy` 내부에서 Gemini Tool Function Declaration(`parameters.properties: only allowed for OBJECT type`) 규격 위반으로 HTTP 400 에러를 반환함.
4. 스키마 래핑 시 포인터 깨짐: 배열 스키마를 객체로 단순 래핑하면 내부 `$ref`가 참조하는 `#/$defs/...`가 하위 레벨로 이동하여 `json-pointer not found` 오류가 발생하고 모델이 `items` 배열을 채우지 않는 현상 발생.
5. 무결성 번역 키 불일치: 모델이 프롬프트 입력 형식에 영향을 받아 `translated_text` 대신 `text` 또는 `translation` 키로 응답할 경우 유닛 검증 실패로 누락 처리되어 불필요한 재시도와 원문 유지가 발생함.

## 결정과 근거

1. 정규식 및 점진적 디코딩: Ollama와 Antigravity 클라이언트 모두 코드 블록 추출 정규식과 `json.JSONDecoder().raw_decode`를 적용하여 앞뒤 설명문과 무관하게 첫 번째 완전한 JSON을 디코딩하도록 통일함.
2. 파일 참조 문법(`-p @filepath`) 도입: stdin 파이프 방식은 CLI 구현상 비대화형 파이프라인에서 신뢰성이 떨어져 버리고, `agy`가 기본 지원하는 `@filepath` 구문을 채택함. 프롬프트를 임시 파일에 기록한 후 인자로 넘겨 OS 수준의 명령줄 길이 제약을 원천 차단함.
3. 배열 스키마 래핑 및 `$defs` 보존: 최상위가 `array`인 스키마는 `{"type": "object", "properties": {"items": ...}, "required": ["items"]}`로 감싸되, 루트의 `$defs`를 최상위 레벨로 보존하여 `$ref` 포인터 경로를 유지함. 프롬프트에 `items` 배열 출력을 지시하고, 응답 파싱 시 `items`/`terms`/`units`를 자동 언래핑함.
4. 번역 유닛 키 폴백 지원: `domain/translation_service.py`에서 응답 dict에 `translated_text`가 없더라도 `text` 또는 `translation` 키가 존재하면 이를 `translated_text`로 자동 보정하여 단일 호출에서 100% 매핑을 달성하도록 수정함.

## 검증

- Ollama 및 Antigravity CLI 실호출 파이프라인 전체(헬스체크, 모델 목록 동적 조회, 단문 번역, 표준 파일 번역, 무결성 파일 번역, 용어집 자동 추출) 실행 및 100% 성공 확인.
- 프로젝트 전체 회귀 테스트 실행: 376개 테스트 전원 통과 (`376 passed, 7 skipped`).

## 미해결

- 없음.
