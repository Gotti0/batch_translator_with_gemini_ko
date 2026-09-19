---
id: 20260919T091926Z-app-domain의-rpm-계산-제거와-rpm-기본값-단일화
created_at: 2026-09-19T09:19:26Z
title: App·Domain의 RPM 계산 제거와 RPM 기본값 단일화
scope: dev-infra
project: Neo_Batch_Translator
summary: AppService의 두 루프와 GlossaryService의 미사용 루프에서 60/RPM 간격 계산을 없애고, RPM 기본값을 config_manager의 상수 하나로 모았다. RPM 0/None은 문서대로 간격 제한 없음으로 처리한다
---

## 배경

RPM 제어 리팩터 T6다. T3에서 요청 간격과 동시 진행 1개를 `GeminiClient`의 스케줄러가 맡게 되었지만, AppService의
용어집 추출 루프와 번역 루프, 그리고 호출되지 않는 `GlossaryService.extract_and_save_glossary_async`에 복붙된
60/RPM 간격 계산이 남아 있었다. RPM 기본값도 config_manager 2.0, AppService·GUI 폴백 60, GeminiClient 140으로
제각각이었다.

## 문제

- 앱 계층의 간격 계산은 스케줄러와 이중으로 걸린다. 스케줄러가 이미 보장하므로 남겨 둘 이유가 없다.
- "RPM 0 = 제한 없음"이 설정 주석, GUI 툴팁, CLI 도움말 세 곳에 문서화되어 있는데 `GeminiClient`는
  `requests_per_minute or 140.0`으로 0과 None을 140 RPM(0.43초 간격)으로 바꾸고 있었다.

## 결정과 근거

- AppService의 두 루프에서 간격 계산과 관련 취소 확인을 지우고 세마포어만 남겼다. 세마포어는 동시에 떠 있는 작업 수만
  제한하고, API 요청 간격과 동시 진행 1개는 스케줄러가 보장한다. 용어집 루프 함수 이름은 `rate_limited_extract`에서
  `run_extract`로 바꿨다.
- 호출되지 않는 `GlossaryService.extract_and_save_glossary_async`를 삭제했다. 같은 루프가 AppService에 있었다.
- RPM 기본값은 `core.config.config_manager.DEFAULT_REQUESTS_PER_MINUTE = 2.0` 하나로 모았다. 설정 기본값, AppService 로그의
  폴백, GUI 설정 탭의 폴백이 이 상수를 쓴다. config_manager의 기본값을 기준으로 삼은 것은, 설정을 불러올 때 기본값이
  합쳐지므로 실제 사용자는 이미 이 값을 받고 있었기 때문이다. 60과 140은 설정에 값이 없을 때만 쓰이던 폴백이다.
- `GeminiClient`는 0과 None을 그대로 스케줄러에 넘긴다. 스케줄러는 간격 없이 동시 진행 1개만 지킨다. 문서와 동작을
  맞춘 것이고, 동시 진행 1개는 유지되므로 여러 키 동시 호출 위험은 생기지 않는다.

## 검증

- `app`, `domain`, `gui_qt`, `core`, `main_cli.py`에서 `60.0 / rpm`, `request_interval`, `last_request_time`이 0건이다.
- 수정한 다섯 모듈이 모두 import된다.
- 전체 pytest: 150 passed, 새 실패 없음. 실행 시간이 56초에서 20초로 줄었다. RPM 없이 만든 클라이언트가 더 이상
  140 RPM 간격으로 기다리지 않기 때문이다.
- config_manager의 `__main__` 자체 점검은 RPM 검사 전의 `enable_prefill_translation` 검사에서 실패한다. 이번 변경과
  무관하게 원래 맞지 않던 점검이며 고치지 않았다.

## 미해결

- RPM 0으로 설정한 사용자는 이제 실제로 간격 없이 요청한다(이전에는 0.43초 간격). 응답을 기다린 뒤 다음 요청을
  보내므로 병렬은 아니지만, 무료 티어 분당 한도에는 더 빨리 닿을 수 있다.
- GUI 검수 탭과 서브 청크 분할의 중첩 세마포어 정리는 T7.
