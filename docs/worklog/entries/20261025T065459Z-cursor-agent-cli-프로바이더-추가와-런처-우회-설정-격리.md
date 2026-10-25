---
id: 20261025T065459Z-cursor-agent-cli-프로바이더-추가와-런처-우회-설정-격리
created_at: 2026-10-25T06:54:59Z
title: Cursor Agent CLI 프로바이더 추가와 런처 우회·설정 격리
scope: data-pipeline
project: Neo_Batch_Translator
summary: Cursor Agent CLI를 cursor_cli 프로바이더로 추가했다. Windows 런처를 건너뛰어 node를 직접 실행하고, CURSOR_CONFIG_DIR를 호출마다 격리해 사용자 기본 모델과 대화 기록을 건드리지 않는다.
---

## 배경

2026-10-25 사용자가 Cursor 구독 하나로 여러 프런티어 모델을 쓰려고 Cursor Agent CLI(cursor-agent 2026.10.22-27dcf5f)를 번역 프로바이더로 추가해 달라고 요청했다. 범위는 번역 경로와 설정 탭이다.

## 문제

- print 모드(`-p`)는 stdin을 읽지 않는다. 프롬프트를 명령줄 인자로만 받는다. 다른 CLI 클라이언트가 쓰는 stdin 주입이나 Antigravity의 `@파일` 방식을 쓸 수 없다.
- Windows 런처는 `.cmd`에서 `powershell -File .ps1`을 거쳐 `node.exe index.js`로 넘긴다. 여러 줄이나 특수문자가 든 인자는 cmd.exe에서 깨진다.
- print 모드에서도 `--model`을 넘기면 `~/.cursor/cli-config.json`의 기본 모델이 바뀐다. 실측 중 사용자의 기본 모델이 auto에서 gemini-3.8-flash로 바뀌었고, `--model auto` 호출로 되돌렸다. 대화 기록도 같은 설정 폴더의 `chats/`에 쌓인다. 번역 호출이 사용자의 대화형 Cursor 환경을 계속 바꾸게 된다.

## 결정과 근거

- 런처를 건너뛰고 `versions/` 아래 최신 버전 폴더의 `node.exe index.js`를 직접 실행한다. 버전을 고르는 규칙은 런처 ps1과 같다. 자동 업데이트로 폴더가 바뀌므로 호출할 때마다 다시 찾는다. 찾지 못하면 `.cmd`로 폴백하지 않고 오류를 낸다. 폴백하면 프롬프트가 조용히 깨진다. 대가로 런처의 promote-staged 단계를 건너뛰므로, 받아 둔 업데이트는 사용자가 터미널에서 한 번 실행해야 적용된다.
- 프롬프트를 임시 파일에 쓰고 "읽고 따르라"고 지시하는 방식은 버렸다. 응답 앞에 "I'll read prompt.md…" 같은 서두가 붙었고, 입력 토큰도 23K로 늘었다(직접 인자는 120).
- 호출마다 임시 폴더를 만들어 그 안에 `--workspace`와 `CURSOR_CONFIG_DIR`를 둔다. 로그인 정보는 이 폴더 밖에 있어 인증은 유지되고, 전역 설정 파일 해시와 사용자 chats 수가 그대로임을 확인했다. `--mode ask`(읽기 전용)와 빈 작업 폴더로 셸 실행과 사용자 파일 접근을 막는다.
- Windows 명령줄 상한(32,766자)을 넘는 프롬프트는 실행 전에 InvalidRequest로 거부한다. 이때 청크 크기를 줄이라고 안내한다.
- `ActionRequiredError`(요금제에 포함되지 않은 모델)는 기다려도 풀리지 않는다. 사용량 제한으로 분류하면 재시도만 반복하므로 일반 오류로 올린다.
- 추론 강도는 모델 ID 접미사로 고르므로 별도 effort 옵션을 두지 않는다(NO_REASONING). API 키는 연결하지 않고 로그인 세션만 쓴다.

## 검증

- 2026-10-25 전체 테스트를 실행했다. 571 passed, 7 skipped, 종료 코드 0. 신규 Cursor 클라이언트 테스트는 21건이다.
- raw 바이트로 확인한 결과, node를 직접 실행하면 줄바꿈, `"&|%^`, 한·일 문자, 이모지가 그대로 전달되고 stdout은 UTF-8이었다.
- 팩토리 실호출(grok-4.7-low)을 확인했다. 헬스체크 성공(11.4초), 모델 265개 파싱, 일본어 여러 줄 번역에서 특수문자와 줄바꿈이 보존됐고, 스키마 지침 JSON 응답도 정상이었다.
- `main_cli.py`로 실제 앱 경로를 확인했다. integrity 모드와 standard 모드 모두 3줄 일본어 입력의 번역을 완료했다(종료 코드 0).
- 설정 탭 전환, 저장, 불러오기는 위젯 단위 테스트로만 확인했다. GUI를 직접 조작하지는 않았다.

## 미해결

- Cursor의 일시적 사용량 제한(rate limit) 오류 문구는 실제로 받아 보지 못했다. 일반적인 패턴으로만 분류한다.
- 격리 전 실측 호출 몇 건이 사용자 `~/.cursor/chats`에 대화 기록을 남겼을 수 있다. 사용자 것과 구분할 수 없어 지우지 않았다.
- macOS/Linux 런처 구조는 확인하지 못했다. Windows가 아니면 해석된 경로를 그대로 실행한다.
- 버전 폴더 구조나 `CURSOR_CONFIG_DIR` 동작은 Cursor 내부 구현이라 업데이트로 바뀔 수 있다.
