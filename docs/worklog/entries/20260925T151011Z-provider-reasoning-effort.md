---
id: 20260925T151011Z-provider-reasoning-effort
created_at: 2026-09-25T15:10:11Z
title: provider-reasoning-effort
scope: data-pipeline
project: Neo_Batch_Translator
summary: 프로바이더별 추론 강도 옵션 명세를 한 모듈에 두고, Claude·Codex·Antigravity CLI와 Ollama·OpenAI 호환 클라이언트가 설정값을 실제 호출 인자로 넘기게 함. UI는 후속 작업.
---

## 배경

추론 강도는 Gemini만 설정 탭의 Thinking Level·Budget으로 조절할 수 있었다. 다른 프로바이더도 CLI나 API가 추론 옵션을 지원하지만 앱은 넘기지 않았고, Antigravity는 설정 키만 있고 UI가 없었다.

## 문제

1. 프로바이더마다 옵션 이름과 허용 값이 다르다. Claude는 `--effort`, Codex는 `model_reasoning_effort` 설정 덮어쓰기, Antigravity는 `--effort`, Ollama는 요청 본문의 `think`, OpenAI 호환은 `reasoning_effort`다.
2. 추론 옵션 규칙(모델별 허용 값)이 설정 탭 코드에 박혀 있어, UI와 실제 호출이 같은 기준을 본다는 보장이 없었다.
3. Antigravity 클라이언트는 low/medium/high만 받아들여, agy가 지원하는 max를 조용히 버렸다.

## 결정과 근거

- 명세를 클라이언트 클래스마다 두는 대신 한 모듈의 표로 모았다. UI가 Gemini 클라이언트 등 무거운 모듈을 불러오지 않고도 같은 표를 쓸 수 있다.
- 모든 비 Gemini 옵션의 기본값은 "넘기지 않음"(None)이다. 기존 사용자의 동작이 그대로이고, 모르는 파라미터를 거부하는 OpenAI 호환 서버에서도 안전하다. OpenAI 호환은 서버마다 지원 여부가 달라 사용자가 고른 경우에만 넣는다.
- Gemini는 기존 동작을 유지한다. Gemini 3은 기본값 항목 없이 high로 돌아가고, 그 외 Gemini 모델은 기존 UI와 같이 예산을 쓴다.
- 허용 값은 추측하지 않고 확인했다. Claude·Antigravity·Ollama는 각 CLI 도움말에서, Codex는 도움말에 목록이 없어 잘못된 값을 넣어 gpt-5.5 서버 오류 메시지에서 목록을 얻었다. Codex CLI는 설정 단계에서 값을 검사하지 않으므로 앱이 명세로 걸러야 한다.

## 검증

- 신규 테스트 12개: 명세 조회와 값 정규화, 각 CLI 인자(Claude `--effort`, Codex `-c model_reasoning_effort`, Antigravity `max`), Ollama `think` 본문 값, OpenAI 호환 `reasoning_effort`, 팩토리의 프로바이더별 전달과 값 비혼입. 전체 스위트가 통과했다.
- 실제 Claude CLI와 Codex CLI(gpt-5.5)에 추론 강도 low를 넘겨 짧은 프롬프트가 정상 응답하는 것을 확인했다.
- Ollama `think`와 OpenAI 호환 `reasoning_effort`는 실제 서버로 확인하지 않았다.

## 미해결

- 설정 탭 UI와 프로바이더별 값 보관(다른 프로바이더에서 저장하면 Gemini thinking_level이 None으로 지워지는 문제 포함)은 후속 작업에서 한다.
- 추론을 지원하지 않는 Ollama 모델에 think 값을 넘기면 서버가 거부할 수 있다.
