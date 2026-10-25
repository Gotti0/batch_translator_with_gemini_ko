---
id: 20261025T080950Z-cursor-cli를-grok-build-cli로-교체하고-사용자-환경과-격리
created_at: 2026-10-25T08:09:50Z
title: Cursor CLI를 Grok Build CLI로 교체하고 사용자 환경과 격리
scope: data-pipeline
project: Neo_Batch_Translator
corrects: 20261025T065459Z-cursor-agent-cli-프로바이더-추가와-런처-우회-설정-격리
summary: cursor_cli 프로바이더를 제거하고 xAI Grok Build CLI(grok_cli)로 대체했다. grok이 기본으로 읽는 Claude·Cursor 규칙·스킬·훅을 끄고, 전용 GROK_HOME과 읽기 전용 도구로 실행한다.
---

## 배경

2026-10-25 사용자가 Grok의 1st party 앱 Grok Build(grok 1.0.70)로 cursor-agent를 대체하겠다고 했다. 같은 날 추가한 Cursor 클라이언트는 런처 우회, 명령줄 길이 제한, 스키마 미지원이라는 제약을 안고 있었다.

## 문제

grok은 번역 클라이언트로 쓰기에 조건이 좋았다. 단일 exe이고, `--prompt-file`을 지원하고, `--json-schema`가 최상위 array를 그대로 받고, `--system-prompt-override`가 있다. 하지만 기본 상태로 쓰면 사용자 환경이 번역 호출에 섞였다. 실측 결과는 다음과 같다.
- 임시 폴더에서 호출해도 `~/.agent-rules`의 Bard 매니페스토와 스킬 목록이 시스템 문맥에 들어갔다.
- `~/.claude/settings.json`의 Stop 훅(WORKLOG 동기화 스크립트, 제한 300초)이 호출마다 실행됐다(pwsh 3개 관찰).
- 기본 도구 27개에 셸, 쓰기, 웹, 이미지 생성이 들어 있고, 한 줄짜리 프롬프트도 입력 토큰이 약 17K였다.
- 호출마다 `~/.grok/sessions`에 파일이 약 17개 쌓였다.
- `--tools`에 없는 이름을 주면 경고만 하고 모든 도구를 켠 채 실행한다.

## 결정과 근거

- `GROK_{CLAUDE,CURSOR}_{AGENTS,RULES,SKILLS,HOOKS,MCPS}_ENABLED=false`로 호환 스캔을 끈다. 끈 뒤에는 Stop 훅 실행이 0회였고 규칙 주입 질의에 "NONE"이 왔다. `grok inspect`는 끈 뒤에도 훅과 `~/.claude/agents`를 목록에 보여 주지만, 실제 실행은 프로세스 감시로 확인했다.
- `GROK_HOME`을 임시 폴더의 앱 전용 홈(`btg_grok_home`)으로 바꾸고, `GROK_AUTH_PATH`로 사용자 `auth.json`을 가리킨다. 로그인은 유지되고 사용자 세션 폴더 파일 수가 그대로였다. 새 홈은 처음에 번들 파일 15MB를 풀어 놓으므로 호출마다 만들지 않고 재사용한다. 이 홈의 `config.toml`에 `storage.cleanup_ttl_days = 1`을 둔다.
- 도구는 `--tools read_file`로 제한하고 `--no-subagents --disable-web-search --no-plan --no-auto-update`를 붙인다. 입력 토큰이 17,108에서 4,458로 줄었다. 도구 이름 오타는 모든 도구를 다시 켜므로 상수로 고정하고 테스트로 지킨다.
- 프롬프트는 `--prompt-file`과 `--verbatim`으로 보낸다. 시스템 지침은 `--system-prompt-override`로 보내되, Windows 명령줄 상한을 넘으면 프롬프트 파일 앞에 붙인다. 번역 기억 블록이 길어질 수 있기 때문이다.
- 스키마는 감싸지 않고 `--json-schema`에 JSON 문자열로 넘긴다(파일 경로는 거부됨). `stopReason == "refusal"`은 검열 예외로 분류한다.
- cursor_cli를 함께 두지 않고 제거했다. 하루 전에 추가된 프로바이더라 사용자가 대체를 명시했다. 남아 있는 `llm_provider: cursor_cli` 설정은 로드할 때 grok_cli로 바꾼다. 바꾸지 않으면 팩토리 기본 분기로 빠져 Gemini 키로 번역하기 때문이다.
- 앞 항목의 Cursor 런처 우회, `CURSOR_CONFIG_DIR` 격리, ActionRequiredError 분류 결정은 이 교체로 더 이상 쓰이지 않는다.

## 검증

- 2026-10-25 전체 테스트: 577 passed, 7 skipped. Grok 단위 테스트 18건과 설정 이전 테스트 2건이 포함된다.
- 클라이언트 실호출(grok 기본 모델): 스키마 3종(무결성, 용어집, 인물 메모)이 모두 배열로 왔고, 시스템 지침 표식이 전달됐다. 사용자 `~/.grok/sessions` 파일 수는 315→315였다.
- 클라이언트 경유 격리 확인: 규칙 질의에 "NONE", Stop 훅 pwsh 0개. 4초 뒤 취소하자 CancelledError가 올라왔고 임시 폴더와 grok 프로세스가 남지 않았다. 헬스체크 성공, 모델 5개(default 포함). PATH에 grok이 없는 프로세스에서도 기본 설치 위치를 찾았다.
- `main_cli.py` 실제 번역: integrity와 standard 모드 모두 종료 코드 0. integrity 첫 실행은 앞선 Cursor 테스트의 무결성 진행 폴더를 재사용해 grok을 호출하지 않았다. 폴더를 지우고 다시 돌려 실제 호출(34초)을 확인했다.

## 미해결

- grok의 검열 거부와 사용량 제한 오류 문구는 실제로 받아 보지 못했다. 문서의 오류 객체 형식과 일반 패턴으로만 분류한다.
- 여러 번역을 동시에 돌릴 때, 같은 `auth.json`을 여러 프로세스가 갱신하면서 충돌하는지 확인하지 않았다.
- `~/.claude/agents`와 `~/.agents/skills`는 호환 환경변수로 꺼지지 않는다. 서브에이전트 도구를 빼서 실행만 막았다.
- standard 모드 3줄 번역에 약 100초가 걸린 원인은 조사하지 않았다.
- `--force-new`가 무결성 진행 폴더(`*_integrity_temp`)를 지우지 않아 이전 결과를 재사용하는 기존 동작을 발견했다. 이번 범위에서 고치지 않았다.
