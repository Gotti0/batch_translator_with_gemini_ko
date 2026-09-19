---
id: 20260919T085017Z-geminiclient에-requestscheduler-연결-슬롯이-요청-전체를-덮는다
created_at: 2026-09-19T08:50:17Z
title: GeminiClient에 RequestScheduler 연결: 슬롯이 요청 전체를 덮는다
scope: data-pipeline
project: Neo_Batch_Translator
summary: _apply_rpm_delay를 없애고 생성·모델 목록 요청을 스케줄러 슬롯으로 감쌌다. 슬롯은 요청이 끝날 때까지 쥐고, 실패 후 백오프는 슬롯 밖에서 기다린다
---

## 배경

RPM 제어 리팩터 T3다. T2에서 만든 `RequestScheduler`(시작 간격 60/RPM + 전역 동시 진행 1개)를
실제 요청 경로에 연결한다. 연결 전에는 `_apply_rpm_delay`가 시작 간격만 조절해, 응답이 간격보다
느리면 요청이 동시에 진행됐다.

## 문제

슬롯을 어디서 잡고 어디서 놓을지가 핵심이었다. 간격 대기 구간만 감싸면 예전처럼 요청이 겹친다.
반대로 재시도 루프 전체를 감싸면, 실패한 요청이 백오프로 기다리는 동안에도 다른 요청이 전부 멈춘다.

## 결정과 근거

- `generate_text_async`와 `list_models_async`에서, 요청 로그부터 API 호출과 응답 처리까지를
  `async with self._scheduler.slot():`로 감쌌다. 스트림 응답은 마지막 조각을 받을 때까지 슬롯을 쥔다.
- 오류 처리와 백오프 대기는 `except` 쪽, 즉 슬롯 밖에 둔다. 실패한 요청이 백오프하는 동안 다른 요청이
  먼저 나가고, 재시도는 다음 빈 슬롯을 새로 받는다.
- 요청 로그("텍스트 생성 요청 (시도: …)")는 슬롯 안으로 옮겼다. 로그 시각이 실제 전송 시각과 일치한다.
- `GeminiClient`가 `scheduler`를 선택 인자로 받는다. 기본값은 `requests_per_minute`로 만든 스케줄러다.
  테스트는 가상 시계를 쓰는 스케줄러를 주입한다. 스케줄러의 기본 시계 인자는 정의 시점에 묶이므로,
  모듈 패치만으로는 가상 시계를 끼울 수 없었다.
- 대기 로그의 출처 로거가 `infrastructure.gemini_client`에서 `infrastructure.request_scheduler`로 바뀐다.
  문구는 같다. 로거 이름으로 로그를 거르는 사용자는 영향을 받는다.

## 검증

- T1 특성 테스트 `test_slow_requests_overlap_in_flight`를 `test_slow_requests_never_overlap_in_flight`로
  뒤집었다. 느린 요청의 두 번째 시작이 첫 요청 종료 시각과 같다.
- `test_backoff_happens_outside_the_slot` 추가: 0.1초에 503을 받고 5초 백오프하는 요청이 있을 때,
  다른 요청이 1.0초에 먼저 나가고 재시도는 5.1초에 나간다.
- 변형 확인: 슬롯이 간격 대기만 감싸도록 바꾸면 비중첩 테스트가 실패한다. 처음에는 구 코드에 새 테스트를
  돌려 확인하려 했으나, 구 생성자에 `scheduler` 인자가 없어 전부 TypeError로 실패했을 뿐이라 근거가
  되지 못했다. 그래서 변형 방식으로 바꿨다.
- 가짜 키로 실제 SDK 경로 확인: 동시 요청 2개 중 두 번째가 첫 요청 종료 후 간격을 채우고 나갔다.
- 전체 pytest: 78 passed, 새 실패 없음.

## 미해결

- 슬롯이 요청 전체를 덮으므로, 걸린 요청 하나가 `api_timeout`(기본 1000초)까지 전체를 막는다.
- 키 순환·재시도 규칙은 아직 그대로다(T4·T5·T5b).
