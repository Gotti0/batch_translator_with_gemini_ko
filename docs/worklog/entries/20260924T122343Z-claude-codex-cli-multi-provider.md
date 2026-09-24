---
id: 20260924T122343Z-claude-codex-cli-multi-provider
created_at: 2026-09-24T12:23:43Z
title: Claude, Codex 및 Antigravity CLI 런타임 어댑터 구현과 멀티 프로바이더 확장
scope: data-pipeline
project: Neo_Batch_Translator
summary: 종량제 API 키 없이 로컬 구독 세션(Claude Code, Codex CLI, Antigravity CLI)으로 대량 번역을 수행할 수 있도록 BaseLLMClient 공통 인터페이스와 CLI 어댑터, 동적 팩토리 및 PySide6 설정 탭을 구현했다
---

## 배경

Gemini API 및 종량제 클라우드 API는 대용량 소설 번역 시 호출 비용이 누적되거나 계정당 분당 요청 수(RPM/RPD) 및 토큰 한도에 직면하기 쉽다. 한편 Paperclip 프로젝트의 로컬 에이전트 런타임(`claude_local`, `codex_local`)과 같이, 사용자가 이미 구독 중인 Claude Pro/Max, ChatGPT Plus 또는 Google Antigravity 세션의 로컬 CLI 도구(`claude.exe`, `codex.exe`, `agy.exe`)를 대화형/비대화형 서브프로세스로 구동하면 추가 API 비용 없이 번역을 수행할 수 있다.

## 문제

1. **클라이언트 인터페이스 결합도**: 기존 번역 파이프라인(`TranslationService`, `SimpleGlossaryService`, `AppService`)은 `GeminiClient` 인스턴스에 강하게 결합되어 있어, 다른 공급자(Claude, Codex, Antigravity, OpenAI Compatible)를 일관되게 다루기 어려웠다.
2. **CLI 런타임 제어 및 인증 격리**:
   - Claude Code CLI에 `--bare` 플래그를 적용하면 로컬 키체인 인증 세션까지 비활성화되어 `Not logged in` 오류가 발생했다. 반면 일반 실행 시 파일 수정·커맨드 실행 등 에이전트 도구를 호출하려 하거나 사용자 권한 승인 프롬프트에서 블로킹되는 문제가 있었다.
   - Codex CLI는 기본 설정 모델명(`gpt-6-sol` 등)으로 호출할 경우 웹 토큰(ChatGPT Plus) 계정에서 HTTP 400 거절이 발생했다. 또한 CLI마다 지원 모델 목록을 실시간 조회하는 방식이 상이했다.
   - Antigravity CLI(`agy`)는 첫 프로세스 호출(Cold start) 시 데몬 검사 및 서비스 초기화로 인해 응답 시간이 30~40초까지 소요되어 표준 20초 타임아웃으로는 헬스체크가 실패하는 현상이 있었다.
   - 대규모 번역 청크(6,000~10,000자)와 복잡한 프롬프트를 Windows 명령줄 인자(`argv`)로 전달할 경우 Windows의 8,191자 명령줄 길이 상한에 걸려 프로세스가 즉시 실패했다.
3. **공급자별 기능 호환성 차이**: 앞서 도입한 PageFold(PDF 텍스트 패킹)는 Gemini의 멀티모달 PDF 인덱싱에 특화된 기능이므로, 비-Gemini 프로바이더 호출 시 PDF 태그나 바이너리가 전달되면 모델이 오류를 내거나 오작동했다.

## 결정과 근거

- **`BaseLLMClient` 추상 기반 클래스 정의 (`infrastructure/base_client.py`)**:
  모든 AI 공급자가 준수해야 할 최소 공통 규약(`generate_text_async`, `list_models_async`, `provider_name`, `supports_pagefold`)을 정립하고, `GeminiClient`, `OpenAICompatibleClient`, `ClaudeCliClient`, `CodexCliClient`, `AntigravityCliClient`가 이를 구현하도록 통일했다.
- **Claude Code CLI 어댑터 (`infrastructure/claude_cli_client.py`)**:
  - `claude.exe -p --output-format json --tools "" --dangerously-skip-permissions` 플래그 조합을 확정했다. 도구를 빈 문자열(`--tools ""`)로 비활성화하여 순수 텍스트 생성기 역할을 강제하고, 권한 프롬프트 대기를 차단했다.
  - `--bare`는 키체인 인증을 무력화하므로 배제했다.
  - Windows 인자 상한 회피를 위해 긴 프롬프트를 서브프로세스의 `stdin` 파이프로 공급하고 스트림 EOF를 전달하는 방식을 채택했다.
  - Claude CLI에는 `claude models` 커맨드가 없으나, 초기 가정과 달리 로컬 `~/.claude/cache/model-catalog/*.json`에 최신 모델 카탈로그(Opus 5.5, Sonnet 5, Fable 등)가 캐시됨을 실측하여 이를 `list_models_async`에서 동적 파싱하도록 정정 구현했다.
- **Codex CLI 어댑터 (`infrastructure/codex_cli_client.py`)**:
  - `codex.exe exec - --ephemeral --dangerously-bypass-approvals-and-sandbox --json` 조합을 확정하고, 표준 입력(`stdin`) 스트림으로 프롬프트를 전송했다.
  - 구조화 출력(무결성 모드 JSON 스키마) 지원 시 `--output-schema` 인자로 스키마 임시 파일을 전달하고, JSONL 스트림 이벤트 중 `agent_message`의 텍스트를 추출했다.
  - ChatGPT 계정 호환 모델로 `gpt-5.5`를 기본 안전 모델로 설정하고, 로컬 `~/.codex/models_cache.json`에서 유효 모델을 동적으로 추출하도록 설계했다.
- **Google Antigravity CLI 어댑터 (`infrastructure/antigravity_cli_client.py`)**:
  - `agy.exe -p - --output-format json --dangerously-skip-permissions --disable-slash-commands` 플래그 조합을 확정했다. 슬래시 커맨드 해석 방지 및 비대화형 자동 승인을 통해 번역 루프 중단 요소를 차단했다.
  - 구조화 출력 시 `--json-schema` 임시 파일 옵션을 활용하고, `agy models` 명령 출력(탭 구분 텍스트)을 파싱하여 가용 모델(`gemini-3.8-flash-high`, `claude-sonnet-4-6` 등) 목록을 동적으로 획득하도록 구현했다.
  - 초기 기동 및 데몬 연결 지연을 감안하여 `check_health_async`의 기본 타임아웃을 60초로 확장 설정했다.
- **동적 공급자 팩토리 (`infrastructure/llm_client_factory.py`)**:
  `llm_provider` 설정값(`gemini`, `claude_cli`, `codex_cli`, `antigravity_cli`, `openai_compatible`)에 따라 적절한 클라이언트를 지연/동적 생성하여 `AppService`에 주입했다.
- **PageFold 비-Gemini 격리 및 무결성 JSON 복원 (`domain/translation_service.py`)**:
  - `supports_pagefold`가 `False`인 클라이언트에서는 PDF 생성 및 주입 로직을 안전하게 건너뛰고 프롬프트 내 `<pdf>...</pdf>` 태그를 unescape하여 일반 텍스트 슬롯으로 자동 치환했다.
  - 무결성 번역 모드에서 CLI가 반환한 JSON 문자열 응답을 `json.loads`로 역직렬화하여 Gemini SDK 파싱 객체와 동일한 구조로 변환했다.
- **PySide6 설정 탭 멀티 프로바이더 동적 폼 (`gui_qt/tabs_qt/settings_tab_qt.py`)**:
  프로바이더 선택에 따라 CLI 실행 경로, Base URL, API 키 및 Vertex AI 입력란의 가시성을 `setRowVisible`로 제어하고, Gemini가 아닐 때는 PageFold 스위치를 비활성화·미체크 처리했다. 공급자별 추천 모델 목록과 설정 직렬화/역직렬화를 완비했다.
- **Zero-Credential 세션 상속 및 선택적 API 키 오버라이드**:
  앱 내에 민감한 OAuth 토큰을 저장하지 않고 OS 자격 증명 관리자 및 로컬 세션(`~/.claude.json`, `~/.codex/auth.json`, Antigravity Google Auth)을 상속받아 구동했다. 구독 대신 API 키를 선호하는 사용자를 위해 `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` 환경변수 주입도 병행 지원했다.
- **공통 인증 헬스체크(`check_health_async`) 및 GUI 원클릭 진단 도입**:
  CLI 미로그인 또는 세션 만료 시 발생하는 조용한 실패(Silent Failure)를 방지하기 위해 각 클라이언트에 사전 헬스체크 메서드를 배치하고, GUI 설정 탭에 **[인증 / 연결 테스트]** 버튼을 추가하여 터미널 로그인 가이드 팝업을 연동했다.

## 검증

- **로컬 실제 CLI 런타임 헬스체크 실측 성공**:
  - Claude Code CLI (`~/.local/bin/claude.EXE`): 별도 API 키 없이 로컬 Claude Pro 세션으로 `OK` 응답 및 인증 성공 확인.
  - OpenAI Codex CLI (`~/AppData/Local/.../codex.EXE`): 별도 API 키 없이 ChatGPT Plus 세션으로 `OK` 응답 및 인증 성공 확인.
  - Google Antigravity CLI (`~/AppData/Local/agy/bin/agy.exe`): 별도 API 키 없이 Google 로그인 세션으로 `OK` 응답 및 인증 성공 확인.
- **Claude CLI 런타임 테스트 (`test/test_infrastructure/test_claude_cli_client.py`, 6 passed)**:
  정상 JSON 파싱, CLI 실행 실패/오류 코드 예외 처리, 비-JSON 출력 폴백, CLI 미설치(`FileNotFoundError`) 처리 통과.
- **Codex CLI 런타임 테스트 (`test/test_infrastructure/test_codex_cli_client.py`, 7 passed)**:
  JSONL 스트림의 `agent_message` 파싱, 구조화 스키마 플래그 주입, 모델 캐시 로드 및 정적 폴백 통과.
- **Antigravity CLI 런타임 테스트 (`test/test_infrastructure/test_antigravity_cli_client.py`, 8 passed)**:
  `agy -p -` JSON 출력 및 사용량 파싱, 타임아웃 시 안전 프로세스 종료, 미인증 에러 매핑, `agy models` 파싱 통과.
- **팩토리 및 클라이언트 변환 테스트 (`test/test_infrastructure/test_llm_client_factory.py`, 5 passed)**:
  `llm_provider`에 따른 5가지 클라이언트 인스턴스 정상 생성 통과.
- **설정 탭 UI 동적 제어 테스트 (`test/test_settings_tab_provider.py`, 8 passed)**:
  프로바이더 전환에 따른 UI 위젯 가시성 제어, PageFold 잠금, 인증 테스트 버튼 동작, 프로바이더별 설정 직렬화/역직렬화 통과.
- **전체 회귀 테스트**: 전체 245개 단위 테스트 통과 (7 skipped, 0 failed).

## 미해결

- 로컬 CLI가 장시간(수십 청크) 연속 실행될 때 프로세스 좀비화나 메모리 누수가 발생하는지 대규모 배치 벤치마크 검증이 필요하다.
- Claude CLI, Codex CLI, Antigravity CLI의 버전 업데이트로 인해 CLI 플래그나 출력 포맷(JSON/JSONL 스키마)이 변경될 경우에 대한 호환성 유지보수 전략이 필요하다.
