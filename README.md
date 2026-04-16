# THE SIGNAL — AI Daily Briefing

13개 글로벌 AI 매체(영어/한국어/일본어)에서 뉴스를 수집하고, TF-IDF + 고유명사 매칭으로 중복 보도를 감지하여 중요도를 판별한 뒤, Gemini로 한국어/영어/일본어 3개 국어 요약을 제공하는 AI 뉴스 큐레이션 서비스.

**Live:** https://davidking981218-rgb.github.io/the-signal/

## Architecture

```
RSS 13개 매체 (매일 65+ 기사, EN/KR/JP)
    ↓
TF-IDF 코사인 유사도 + 고유명사 매칭 (30+ 동의어 사전)
    ↓
중복 보도 클러스터링 → 매체 수 기반 중요도 정렬
    ↓
Gemini API → 3개 국어 요약 + URL/매체수 교차 검증 (환각 방지)
    ↓
Edge TTS → 뉴스 음성 생성 (KR/EN/JP)
    ↓
GitHub Actions (매일 09:00 KST) → GitHub Pages 자동 배포
```

## Features

- **중복 보도 기반 중요도 판단** — 여러 매체가 동시에 다룬 뉴스 = 중요한 뉴스
- **LLM 환각 방지** — URL 교차 검증, 매체수 강제 덮어쓰기, 원문 제목 병기
- **3개 국어 지원** — 한국어 / English / 日本語 실시간 전환
- **Edge TTS 음성 브리핑** — Spotify 스타일 플레이어로 뉴스 청취
- **아카이브** — 과거 브리핑 열람 가능
- **Discord 알림** — 웹훅으로 매일 새 브리핑 알림 발송
- **에러 핸들링** — 빌드 실패 시 에러 페이지 생성 + 3회 재시도
- **피드 건강 모니터링** — 실패 피드 실시간 표시

## Security

- **XSS 방지** — LLM 출력 및 외부 입력값을 `html.escape()`로 이스케이프 처리
- **API 키 보호** — 환경변수/GitHub Secrets로만 관리, 코드 하드코딩 금지
- **최소 권한 원칙** — GitHub Actions job별 필요한 권한만 부여
- **외부 링크 보호** — `rel="noopener noreferrer"`로 탭내빙 방지

## Tech Stack

- Python 3.12
- Gemini API (무료 티어)
- scikit-learn (TF-IDF)
- edge-tts (음성 합성)
- feedparser (RSS)
- GitHub Actions + GitHub Pages

## File Structure

```
signal_core.py   ← 공통 코어 (RSS, 클러스터링, Gemini, HTML, TTS)
ai_briefing.py   ← 로컬 실행 (Edge 앱 모드 팝업)
build.py         ← GitHub Actions 빌드 (Pages 배포 + 아카이브)
archive/         ← 과거 브리핑 HTML (git으로 영구 보존)
```

## Setup

### 1. 의존성 설치

```bash
pip install feedparser google-generativeai scikit-learn edge-tts
```

### 2. Gemini API 키 발급

[aistudio.google.com/apikey](https://aistudio.google.com/apikey) 에서 무료 발급

### 3. 환경변수 설정

```bash
# Windows
setx GEMINI_API_KEY "your-key-here"

# Mac/Linux
export GEMINI_API_KEY="your-key-here"
```

### 4. 로컬 실행

```bash
python ai_briefing.py
```

### 5. GitHub Pages 배포

1. 레포 fork 또는 clone
2. GitHub Settings > Secrets에 `GEMINI_API_KEY` 등록
3. (선택) `DISCORD_WEBHOOK` 등록하면 매일 알림 발송
4. Actions가 매일 09:00 KST에 자동 빌드 + 배포

## News Sources (13)

| 매체 | 유형 | 언어 |
|------|------|------|
| TechCrunch AI | 종합 테크 | EN |
| The Verge AI | 종합 테크 | EN |
| VentureBeat AI | 종합 테크 | EN |
| Wired AI | 종합 테크 | EN |
| Ars Technica | 종합 테크 | EN |
| The Decoder | AI 전문 | EN |
| MarkTechPost | AI 전문 | EN |
| DailyAI | AI 전문 | EN |
| Synced Review | AI 전문 | EN |
| AI News | AI 전문 | EN |
| The Rundown AI | AI 뉴스레터 | EN |
| AI타임스 | AI 전문 | KR |
| ITmedia AI+ | AI 전문 | JP |

`signal_core.py`의 `RSS_FEEDS`에서 추가/삭제 가능.

## Troubleshooting

| 증상 | 해결 |
|------|------|
| `GEMINI_API_KEY 환경변수를 설정하세요` | 환경변수 설정 확인 |
| `Gemini 3회 시도 모두 실패` | API 키 유효성 확인, 무료 한도 초과 여부 확인 |
| 뉴스가 AI와 무관 | Gemini 프롬프트에 필터링 규칙 포함됨, 재실행 시 개선 |
| TTS 음성 없음 | `pip install edge-tts` 확인, 인터넷 연결 필요 |
| 피드 N/13 실패 | 일시적 네트워크 문제, 나머지 피드로 정상 작동 |
| Actions에서 아카이브 push 실패 | 동일 날짜 재실행 시 발생 가능, `git pull --rebase`로 자동 해결 |
