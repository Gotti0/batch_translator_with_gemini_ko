---
id: 20260924T133923Z-gemini-batch-translation-mode
created_at: 2026-09-24T13:39:23Z
title: Gemini Batch API 배치 번역 모드 추가
scope: data-pipeline
project: Neo_Batch_Translator
summary: 표준 모드 청크 번역을 Gemini Batch API로 제출·수거하는 배치 번역 모드를 추가해 번역 비용을 50% 줄이고, 미완료 청크는 표준 모드 이어하기로 실시간 마무리하게 했다
---

## 배경

Gemini Batch API는 같은 모델을 표준 요금의 50%로 비동기 처리한다(목표 24시간, 48시간 초과 시 만료, 인라인 요청은 작업당 20MB 미만). 소설 전체를 한 번에 번역하는 이 앱은 즉시 응답이 필요하지 않아 절감 효과가 크다.

## 문제

1. 실시간 경로는 프롬프트 조립(용어집 주입, 프리필, `<pdf>` 태그)과 요청 설정(thinking, 안전 설정 OFF, PageFold 해상도)을 호출 함수 안에서 만들어, 배치가 같은 요청을 재현할 수 없었다.
2. 결과가 최대 24시간 뒤에 오므로 앱을 껐다 켜도 작업을 이어받아야 한다. 작업 생성은 멱등성이 없어 중복 제출은 곧 중복 과금이다.
3. 배치 작업과 업로드 파일은 제출한 키의 프로젝트에만 보이므로 키 로테이션을 쓸 수 없다.
4. 검열 분할 재시도는 응답을 보고 즉시 다시 부르는 구조라 배치 안에서는 할 수 없다.

## 결정과 근거

- **요청 조립 분리 (리팩터링, 동작 변화 없음)**: `TranslationService.build_translation_request`·`finalize_translation_text`, `GeminiClient.build_sdk_contents`·`build_generate_config(for_batch)`, `AppService._merge_and_finalize_async`. 실시간 경로도 같은 함수를 써서 두 경로의 프롬프트가 어긋나지 않는다.
- **인라인 요청을 18MB 단위 작업 여러 개로 제출**: SDK `InlinedRequest`는 `GenerateContentConfig`를 그대로 받아 REST로 변환한다(시스템 지시문·안전 설정·thinking·file_data 변환을 확인). JSONL 파일 입력은 비공개 변환기를 다시 써야 해 보류했다.
- **PageFold 용어집 PDF는 File API로 한 번 업로드**하고 `file_data` URI로 참조한다. 청크마다 PDF 바이트를 넣으면 20MB 예산을 빠르게 쓴다.
- **상태 저장**: 기존 메타데이터에 `batch` 항목(모델, 키 지문, 라운드, 작업 목록)을 둔다. 키 자체는 저장하지 않는다. 제출 전에 `display_name`을 먼저 기록해, 응답 전에 앱이 꺼져도 `batches.list()`에서 찾아 이어간다(10분 안에 못 찾으면 유실 처리).
- **결과는 표준 모드와 같은 곳에 기록**: 청크 백업 파일과 `translated_chunks`. 검열·오류·만료 청크는 `failed_chunks`에 `[배치] 검열:`/`[배치] 오류:` 접두어로 남는다. 그래서 "실시간으로 마무리"는 `translation_mode_override="standard"`로 표준 모드 이어하기를 부르기만 하면 되고, 검열 분할 재시도도 거기서 동작한다.
- **시작 버튼은 재시도하지 않는다**: 진행 중이면 조회, 한 번도 제출하지 않은 청크가 있으면 제출. 이미 실패한 청크의 재처리(실시간 / 재제출 / 실패 표시로 저장)는 사용자가 고른다. 검열 청크를 같은 요청으로 다시 배치에 넣으면 또 실패할 가능성이 높기 때문이다.
- **키**: 여러 키가 있으면 첫 키로 제출하고, 조회 때는 저장된 지문과 같은 키를 찾는다. 없으면 제출 키를 다시 추가하라고 안내한다.
- **UI/CLI**: "배치 번역" 모드 카드(Gemini API·Vertex 미사용일 때만), 배치 작업 패널(작업 목록, 지금 확인·취소·실시간 마무리·재제출·그대로 저장), 60초 폴링 타이머. CLI는 `--batch-submit`, `--batch-status`, `--batch-finish realtime|resubmit|keep`.

## 검증

- 리팩터링 전후 기존 테스트 267개 통과, 요청 빌더 테스트 10개 추가(프리필·동적 용어집·PageFold·태그 조합, 실시간/배치 설정 차이).
- `GeminiBatchClient` 단위 테스트 10개(상태 매핑, 사고 파트 제외, 검열 판정, 오류 매핑, 이름 검색).
- 메모리 가짜 배치 서버로 AppService 통합 테스트 12개: 제출 → 앱 재시작 → 조회 → 수거 → 실시간 마무리, 전부 성공 시 자동 병합, 만료 → 재제출(2라운드) → 실패 표시 저장, 취소, 제출 중 종료 복구, 제출 실패 기록, 키 불일치, 크기 분할·초과 청크, PageFold 1회 업로드, CLI 흐름.
- 설정 탭 UI 테스트 6개, qasync로 실제 설정 탭 버튼을 눌러 제출 → 폴링 수거 → 실시간 마무리까지 확인.
- 전체: 305 passed, 7 skipped.
- 실제 Batch API로는 검증하지 않았다. 무료 티어 키의 배치 사용 가능 여부도 확인하지 못했다.
