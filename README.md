# BTG - Batch Translator for Gemini

BTG는 Google Gemini API를 사용하여 대용량 텍스트를 효율적으로 번역하는 배치 번역 도구입니다[1][3][4]. 성인 소설 번역에 특화되어 있으며, 고유명사 관리와 병렬 처리를 통한 고성능 번역을 제공합니다.

## 주요 기능

### 🔄 배치 번역
- **병렬 처리**: 멀티스레딩을 통한 고속 번역 처리[3]
- **이어하기 기능**: 메타데이터 기반으로 중단된 번역 작업 재개[3]
- **콘텐츠 안전 재시도**: 검열 오류 시 자동 청크 분할 및 재시도[3]
- **진행률 추적**: 실시간 번역 진행 상황 모니터링[3][4]

### 📝 고유명사 관리
- **자동 추출**: 텍스트에서 외국어 고유명사 자동 추출 및 한국어 번역[1]
- **CSV 관리**: 고유명사-번역 매핑을 CSV 파일로 관리[1][2]
- **등장 횟수 추적**: 고유명사별 출현 빈도 분석[1]

### 🎯 성인 콘텐츠 특화
- **무검열 번역**: 성인 콘텐츠를 검열 없이 자연스럽게 번역[6]
- **전문 프롬프트**: 성인 소설 번역에 최적화된 프롬프트 템플릿[6]
- **콘텐츠 필터링 우회**: 안전 필터 우회 메커니즘 내장[3]

### 🖥️ 사용자 친화적 GUI
- **직관적 인터페이스**: Tkinter 기반의 사용하기 쉬운 GUI[4]
- **설정 관리**: API 키, 모델 선택, 생성 파라미터 등 통합 관리[4]
- **실시간 로그**: 번역 과정의 상세한 로그 출력[4]

## 시스템 아키텍처

BTG는 4계층 아키텍처를 채택하여 유지보수성과 확장성을 보장합니다[3]:

```
┌─────────────────────────────────┐
│     Presentation Layer          │
│  (batch_translator_gui.py)      │
├─────────────────────────────────┤
│      Service Layer              │
│    (app_service.py)             │
├─────────────────────────────────┤
│   Business Logic Layer          │
│ (translation_service,           │
│  pronoun_service,               │
│  chunk_service)                 │
├─────────────────────────────────┤
│   Infrastructure Layer          │
│ (gemini_client,                 │
│  file_handler)                  │
└─────────────────────────────────┘
```

## 설치 및 설정

### 필수 요구사항
- Python 3.8 이상
- Google Gemini API 키 또는 Vertex AI 서비스 계정
- 필요한 Python 패키지:
  ```bash
  pip install google-genai tkinter tqdm pathlib
  ```

### API 설정

#### Gemini Developer API 사용
1. [Google AI Studio](https://aistudio.google.com/)에서 API 키 발급
2. GUI의 "API 키 목록" 필드에 키 입력 (한 줄에 하나씩)[4]

#### Vertex AI 사용
1. GCP 프로젝트 생성 및 Vertex AI API 활성화
2. 서비스 계정 생성 및 JSON 키 다운로드
3. GUI에서 "Vertex AI 사용" 체크 후 설정 입력[4]

## 사용법

### GUI 실행
```bash
python batch_translator_gui.py
```

### 기본 번역 워크플로우

1. **설정 구성**[4]
   - API 키 또는 Vertex AI 설정 입력
   - 모델 선택 (gemini-2.0-flash 권장)
   - 번역 파라미터 조정 (Temperature, Top-P)

2. **파일 선택**[4]
   - 입력 파일: 번역할 텍스트 파일
   - 출력 파일: 번역 결과가 저장될 경로

3. **고유명사 추출 (선택사항)**[1]
   - "선택한 입력 파일에서 고유명사 추출" 버튼 클릭
   - 추출된 고유명사를 검토 및 수정

4. **번역 실행**[3]
   - "번역 시작" 버튼 클릭
   - 진행률과 로그를 실시간으로 확인

### 고급 설정

#### 청킹 설정[5]
- **청크 크기**: 기본 6000자, 메모리와 API 제한에 따라 조정
- **최대 작업자 수**: CPU 코어 수에 따라 자동 설정, 수동 조정 가능

#### 콘텐츠 안전 재시도[3]
- **최대 분할 시도**: 검열 오류 시 청크 분할 재시도 횟수 (기본 3회)
- **최소 청크 크기**: 분할 시 최소 크기 제한 (기본 100자)

## 설정 파일 구조

`config.json` 파일로 모든 설정을 관리합니다[6]:

```json
{
  "api_keys": ["your-api-key-1", "your-api-key-2"],
  "use_vertex_ai": false,
  "model_name": "gemini-2.0-flash",
  "temperature": 0.7,
  "top_p": 0.9,
  "chunk_size": 6000,
  "max_workers": 4,
  "use_content_safety_retry": true,
  "max_content_safety_split_attempts": 3,
  "min_content_safety_chunk_size": 100
}
```

## 파일 구조

```
BTG/
├── batch_translator_gui.py      # GUI 애플리케이션
├── app_service.py               # 서비스 레이어
├── gemini_client.py             # Gemini API 클라이언트
├── translation_service.py       # 번역 비즈니스 로직
├── pronoun_service.py           # 고유명사 추출 서비스
├── chunk_service.py             # 텍스트 청킹 서비스
├── file_handler.py              # 파일 처리 유틸리티
├── config_manager.py            # 설정 관리
├── post_processing_service.py   # 후처리 서비스
├── logger_config.py             # 로깅 설정
├── dtos.py                      # 데이터 전송 객체
├── exceptions.py                # 커스텀 예외
└── config.json                  # 설정 파일
```

## 특징적인 기능

### 메타데이터 기반 이어하기[2][3]
- 번역 작업의 진행 상황을 메타데이터 파일에 저장
- 중단된 작업을 정확히 이어서 진행 가능
- 설정 변경 감지 및 호환성 확인

### 후처리 기능[3]
- 번역 헤더 제거
- 마크다운 블록 정리
- HTML 구조 검증
- 청크 인덱스 마커 제거

### 오류 처리 및 복구[7]
- API 사용량 제한 시 자동 대기 및 재시도
- 여러 API 키 간 자동 순환
- 콘텐츠 안전 필터링 우회 메커니즘

## 로그 및 모니터링

- **실시간 진행률**: 청크별 번역 진행 상황
- **성공률 통계**: 번역 성공/실패 비율
- **상세 로그**: API 호출, 오류, 성능 메트릭
- **시각적 진행 표시**: 진행률 바 및 상태 메시지

## 라이선스

이 프로젝트는 개인 및 상업적 용도로 자유롭게 사용할 수 있습니다.

## 주의사항

- API 사용량에 따른 비용이 발생할 수 있습니다
- 대용량 파일 번역 시 충분한 디스크 공간을 확보하세요
- 성인 콘텐츠 번역 시 관련 법규를 준수하세요

