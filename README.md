# THE SIGNAL — AI 모닝 브리핑

매일 아침 최신 AI 뉴스를 RSS 피드에서 자동으로 가져와 보여주는 데스크탑 서비스.
API 키 불필요, 완전 무료.

## 설치

```bash
pip install feedparser
```

## 실행

```bash
cd ai_briefing
python ai_briefing.py
```

실행하면 브라우저에 오늘의 AI 뉴스 브리핑 페이지가 자동으로 열립니다.

## 매일 자동 실행 설정

### Windows 작업 스케줄러

1. `Win + R` → `taskschd.msc` 입력 → 확인
2. 우측 패널에서 **"작업 만들기"** 클릭
3. **일반** 탭:
   - 이름: `THE SIGNAL AI Briefing`
   - "사용자의 로그온 여부에 관계없이 실행" 선택
4. **트리거** 탭 → 새로 만들기:
   - 시작: 매일
   - 시간: `08:00:00`
5. **동작** 탭 → 새로 만들기:
   - 동작: 프로그램 시작
   - 프로그램: `python` (또는 Python 전체 경로)
   - 인수 추가: `ai_briefing.py`
   - 시작 위치: `C:\Users\user\ai_briefing`
6. **확인** 클릭 → 비밀번호 입력 후 저장

### Mac (crontab)

```bash
crontab -e
```

아래 줄 추가:

```
0 8 * * * cd /path/to/ai_briefing && python3 ai_briefing.py
```

## 뉴스 소스

- TechCrunch AI
- The Verge AI
- MIT News AI
- OpenAI Blog
- Google AI Blog

`RSS_FEEDS` 리스트에서 추가/삭제 가능.

## 문제 해결

| 증상 | 해결 |
|------|------|
| `ModuleNotFoundError: feedparser` | `pip install feedparser` 실행 |
| 브라우저가 안 열림 | `webbrowser` 모듈이 기본 브라우저를 찾지 못함. 환경변수 `BROWSER` 설정 |
| 뉴스가 0개 | 인터넷 연결 확인. RSS 피드 URL이 변경되었을 수 있음 |
| 뉴스가 오래된 내용 | RSS 피드 업데이트 주기에 따라 다름. 다른 피드 추가 권장 |
