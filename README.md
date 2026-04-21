# THE SIGNAL — AI Daily Briefing

22개 글로벌 AI 매체(영어/한국어/일본어)에서 뉴스를 수집하고, **Gemini 임베딩**으로 의미 기반 중복 보도를 감지(다국어 크로스링구얼 매칭)한 뒤, **Gemini 2.5 Flash**로 한국어/영어/일본어 3개 국어 요약을 제공하는 AI 뉴스 큐레이션 서비스.

**Live:** https://davidking981218-rgb.github.io/the-signal/

## Architecture

```
RSS 22개 매체 (EN/KR/JP, Tier 0 공식 소스 6개 포함)
    ↓
AI 관련성 2단계 필터 (영어 정규식 + 한/일 substring)
    ↓
Gemini Embedding (gemini-embedding-001) — 의미 벡터화
    ↓
코사인 유사도 0.85 이상 → Union-Find 클러스터링 (다국어 매칭)
    ↓
정렬: Tier 0 여부 → 매체 수 → 매체 신뢰도
    ↓
Gemini 2.5 Flash → 3개 국어 요약 + URL/매체수 교차 검증 (환각 방지)
    ↓
Gemini 3.1 Flash TTS (Charon) → 중저음 남성 음성 생성 (KR/EN/JP)
  ↓ (API 실패 시 Edge TTS 자동 폴백)
GitHub Actions (매일 05:00 KST) → GitHub Pages 자동 배포
```

## Features

- **Gemini 임베딩 기반 크로스링구얼 매칭** — 영어/한국어/일본어를 넘나들며 같은 사건을 묶음 (예: Sam Altman 관련 영 2곳 + 일 1곳 + 한 1곳 동시 매칭)
- **Tier 0 공식 1차 소스** — OpenAI / Google Research / Google DeepMind / Anthropic / NVIDIA / Microsoft Research. 단독 보도라도 최상위로 올림
- **중복 보도 기반 중요도** — 여러 매체가 동시에 다룬 뉴스 = 중요한 뉴스
- **매체 신뢰도 가중치** — Tier 0~3 계층. 같은 매체 수일 때 우선순위 결정
- **AI 관련성 2단계 필터** — 영어 정규식 + 한국어/일본어 substring 리스트로 비-AI 기사 차단
- **LLM 환각 방지** — AI는 요약·번역만, 팩트(URL/매체수/원문 제목)는 코드가 직접 덮어씀
- **기사 파비콘 — Gemini 힌트 우선 + 형식 검증** — Gemini가 반환한 `company_domain`을 정규식으로 형식만 검증해 수용. 하드코딩 화이트리스트 없이 스탠포드/소프트뱅크 등 모든 기관 자동 커버. 실패 시 빅5 하드코딩 사전(`ENTITY_ALIASES`)이 백업
- **Tier 0 공식 매체 브랜드 컬러** — 클러스터에 공식 1차 소스가 포함되면 카드 상단에 4px 브랜드 액센트 바 + 테두리 + 배경 방사 글로우를 해당 회사 색으로 표시. OpenAI 민트(#10a37f) / Anthropic Claude 주황(#cc785c) / Google·DeepMind Gemini 4색 그라데이션 / NVIDIA 시그니처 그린(#76b900) / Microsoft Azure 블루(#0078d4)
- **3개 국어 지원** — 한국어 / English / 日本語 실시간 전환 (원문 보기 버튼까지 번역)
- **Gemini 3.1 Flash TTS 음성 브리핑** — Google 최신 TTS 모델(Charon, 중저음 남성)로 뉴스 낭독. 무료 티어 3RPM 준수(25초 간격). API 장애 시 Edge TTS 자동 폴백. Spotify 스타일 플레이어
- **매체 신뢰도 자동 학습** — 7일치 통계 쌓이면 교차 보도율 기반 자동 재분류. **Tier 0·Tier 1은 수동 지정이 유지되어 자동 갱신 대상 아님** (권위 매체 보호). Tier 3 ↔ Tier 2 사이에서만 이동 (교차 보도율 10% 기준)
- **아카이브** — 과거 브리핑 HTML을 git 커밋으로 영구 보존
- **Discord 알림** — 웹훅 설정 시 매일 새 브리핑 헤드라인 전송
- **에러 핸들링** — 빌드 실패 시 에러 페이지 + 3회 재시도 (간격 10/20/40초, 503 과부하 대응)
- **피드 건강 모니터링** — 실패 피드 상단 배너로 실시간 표시

## Security

- **XSS 방지** — LLM 출력 및 외부 입력값을 `html.escape()`로 이스케이프 처리
- **API 키 보호** — 환경변수/GitHub Secrets로만 관리, 코드 하드코딩 금지
- **최소 권한 원칙** — GitHub Actions job별 필요한 권한만 부여
- **외부 링크 보호** — `rel="noopener noreferrer"`로 탭내빙 방지

## Tech Stack

- Python 3.12
- Gemini API 2.5 Flash (요약) + gemini-embedding-001 (클러스터링) + **3.1 Flash TTS (음성)** — 무료 티어
- google-genai (신규 SDK, 구 `google-generativeai`는 deprecated)
- numpy (코사인 유사도 계산)
- edge-tts (TTS 폴백용)
- feedparser (RSS)
- GitHub Actions + GitHub Pages

## File Structure

```
signal_core.py   ← 공통 코어 (RSS, 임베딩 클러스터링, Gemini 요약, HTML, TTS)
ai_briefing.py   ← 로컬 실행 (Edge 앱 모드 팝업)
build.py         ← GitHub Actions 빌드 (Pages 배포 + 아카이브 + 통계)
archive/         ← 과거 브리핑 HTML (git으로 영구 보존)
metrics/         ← 매체별 교차 보도율 통계 (날짜별 JSON)
favicon*.png     ← 사이트 파비콘 (32/180/512px)
```

## Setup

### 1. 의존성 설치

```bash
pip install feedparser google-genai numpy edge-tts
```

### 2. Gemini API 키 발급

[aistudio.google.com/apikey](https://aistudio.google.com/apikey) 에서 무료 발급

### 3. 환경변수 설정

```bash
# Windows
setx GEMINI_API_KEY "your-key-here"
setx GEMINI_TTS_API_KEY "your-tts-key-here"

# Mac/Linux
export GEMINI_API_KEY="your-key-here"
export GEMINI_TTS_API_KEY="your-tts-key-here"
```

> `GEMINI_TTS_API_KEY`는 별도 GCP 프로젝트에서 발급하면 요약용과 한도가 분리됩니다.
> 미설정 시 Edge TTS로 자동 폴백되므로 선택사항입니다.

### 4. 로컬 실행

```bash
python ai_briefing.py
```

### 5. GitHub Pages 배포

1. 레포 fork 또는 clone
2. GitHub Settings > Secrets에 `GEMINI_API_KEY` 등록
3. (선택) `GEMINI_TTS_API_KEY` 등록하면 Gemini 3.1 Flash TTS 사용 (미등록 시 Edge TTS)
4. (선택) `DISCORD_WEBHOOK` 등록하면 매일 알림 발송
4. Actions가 매일 05:00 KST에 자동 빌드 + 배포

## News Sources (22)

| 매체 | 유형 | 언어 | 신뢰도 |
|------|------|------|--------|
| OpenAI News | 공식 1차 소스 | EN | **Tier 0** |
| Google Research | 공식 1차 소스 | EN | **Tier 0** |
| Google DeepMind | 공식 1차 소스 | EN | **Tier 0** |
| Anthropic News (커뮤니티 피드) | 공식 1차 소스 | EN | **Tier 0** |
| NVIDIA Blog | 공식 1차 소스 | EN | **Tier 0** |
| Microsoft Research | 공식 1차 소스 | EN | **Tier 0** |
| TechCrunch AI | 종합 테크 | EN | Tier 1 |
| The Verge AI | 종합 테크 | EN | Tier 1 |
| VentureBeat AI | 종합 테크 | EN | Tier 1 |
| The Decoder | AI 전문 | EN | Tier 1 |
| IEEE Spectrum AI | 기술 학회지 | EN | Tier 1 |
| MIT Technology Review AI | 권위 저널리즘 | EN | Tier 1 |
| **The Guardian AI** | 권위 종합지 (AI 섹션) | EN | Tier 1 ⭐ |
| **NYT Technology** | 권위 종합지 (Tech 섹션) | EN | Tier 1 ⭐ |
| **BBC Technology** | 권위 종합지 (Tech 섹션) | EN | Tier 1 ⭐ |
| The Rundown AI | AI 뉴스레터 | EN | Tier 2 |
| ZDNet AI | AI 실용지 | EN | Tier 2 |
| Simon Willison's Weblog | AI 해설 블로그 | EN | Tier 2 |
| AI타임스 - AI기술 (S1N24) | AI 전문 | KR | Tier 2 |
| AI타임스 - AI산업 (S1N3) | AI 전문 | KR | Tier 2 |
| ITmedia AI+ | AI 전문 | JP | Tier 2 |
| Wired AI | 종합 테크 | EN | Tier 3 |

`signal_core.py`의 `RSS_FEEDS`에서 추가/삭제 가능.

### Tier 0 우선 정렬

Tier 0(OpenAI/Google/Anthropic 등)가 단독으로 보도한 뉴스는 **다른 매체가 보도 안 해도 무조건 최상위**로 올라갑니다. 정렬 우선순위:
1. Tier 0 매체 포함 여부
2. 중복 매체 수
3. 매체 신뢰도

### 대표 기사 선정 — Tier 1 내 프리미엄 우선

여러 매체가 같은 사건을 다룰 때, 어느 기사를 대표로 쓸지 결정하는 순서:
1. **매체 신뢰도 점수 (Tier 0=4 > Tier 1=3 > Tier 2=2 > Tier 3=1)**
2. **Tier 1 내 프리미엄 매체 (Guardian AI / NYT Tech / BBC Tech)** — 같은 Tier 1끼리 겹치면 이 세 곳 우선
3. 요약(summary) 길이 — 모두 같으면 긴 요약 우선

`signal_core.py`의 `PREMIUM_TIER1_SOURCES` 집합에서 관리.

## Troubleshooting

| 증상 | 해결 |
|------|------|
| `GEMINI_API_KEY 환경변수를 설정하세요` | 환경변수 설정 확인 |
| `Gemini 3회 시도 모두 실패` (503 UNAVAILABLE) | Google 쪽 일시 과부하. 몇 시간 뒤 자동 회복. 재시도 간격이 10/20/40초로 설정됨 |
| `검증 통과 N개 < 5개` | RSS에서 AI 기사가 부족한 날. 24시간 cutoff가 36시간으로 자동 확장됨 |
| 뉴스가 AI와 무관 | 2단계 필터 통과한 것. `filter_ai_relevant`의 블랙/화이트리스트 확장 가능 |
| 기사 파비콘이 다른 회사 | 주 경로는 Gemini `company_domain` 힌트(형식 검증만). 형식 오류/빈 값이면 `ENTITY_ALIASES` 백업이 동작. Gemini 프롬프트(`summarize_articles` 내 `company_domain` 설명)를 강화하거나 백업 별칭 추가 |
| TTS 음성 없음 | `GEMINI_TTS_API_KEY` 확인. 미설정이면 Edge TTS 사용 (`pip install edge-tts`) |
| Gemini TTS 429 에러 | 무료 티어 3RPM 초과. 25초 간격이 기본 적용되어 있으나, 연속 3회 실패 시 Edge TTS로 자동 전환 |
| 피드 N/22 실패 (주황 배너) | 일시적 네트워크 문제, 나머지 피드로 정상 작동 |
| 피드 절반 이상 실패 (빨강 배너) | RSS URL 변경 가능성, `RSS_FEEDS` 확인 |
| Actions cron 미실행 | GitHub cron은 최대 30~60분 지연 가능, 수동 실행으로 대체 |
| 임베딩이 전혀 다른 기사를 묶음 | `SIMILARITY_THRESHOLD`(기본 0.85)를 0.88로 올림 |
| 같은 사건인데 못 묶음 | `SIMILARITY_THRESHOLD`를 0.82로 낮춤 |
