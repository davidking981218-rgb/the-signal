"""
THE SIGNAL — 공통 코어 모듈
RSS 수집, TF-IDF 클러스터링, Gemini 요약, HTML 생성을 담당한다.
ai_briefing.py(로컬)와 build.py(GitHub Actions)가 공통으로 사용.
"""

import asyncio
import base64
import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from html import unescape
from io import BytesIO

import edge_tts
import feedparser
import google.generativeai as genai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ── 설정값 ──────────────────────────────────────────────
NEWS_COUNT = 5
MODEL = "gemini-2.5-flash"
SIMILARITY_THRESHOLD = 0.20  # TF-IDF 기본 임계값 (고유명사 매칭과 병행)
ENTITY_MATCH_THRESHOLD = 2   # 고유명사 2개 이상 겹치면 같은 토픽
GEMINI_MAX_RETRIES = 3
KST = timezone(timedelta(hours=9))

# Edge TTS 음성 (자연스러운 뉴스 스타일)
TTS_VOICES = {
    "kr": "ko-KR-SunHiNeural",
    "en": "en-US-AriaNeural",
    "jp": "ja-JP-NanamiNeural",
}

# AI 분야 주요 고유명사 사전 (TF-IDF가 못 잡는 동의어/약칭 매핑)
ENTITY_ALIASES = {
    "openai": "openai", "open ai": "openai", "chatgpt": "openai", "gpt-4": "openai", "gpt-5": "openai", "sam altman": "openai",
    "google": "google", "deepmind": "google", "gemini": "google",
    "anthropic": "anthropic", "claude": "anthropic", "dario amodei": "anthropic",
    "meta": "meta", "llama": "meta", "zuckerberg": "meta",
    "microsoft": "microsoft", "copilot": "microsoft", "satya nadella": "microsoft",
    "apple": "apple", "apple intelligence": "apple",
    "nvidia": "nvidia", "jensen huang": "nvidia",
    "mistral": "mistral", "stability": "stability", "midjourney": "midjourney",
    "xai": "xai", "grok": "xai", "elon musk": "xai",
}

RSS_FEEDS = [
    # 종합 테크 (AI 섹션) — 속보 겹침 가능성 높음
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    # AI 전문 매체 — 매일 다수 기사, 같은 사건 중복 보도
    "https://the-decoder.com/feed/",
    "https://www.marktechpost.com/feed/",
    "https://dailyai.com/feed/",
    "https://www.artificialintelligence-news.com/feed/",
    "https://syncedreview.com/feed/",
    # AI 뉴스레터 — 주요 뉴스 큐레이션
    "https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml",  # The Rundown AI
]


def now_kst() -> datetime:
    """한국 시간 기준 현재 시각."""
    return datetime.now(KST)


def _strip_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", unescape(text))
    return re.sub(r"\s+", " ", clean).strip()


# ── 1. RSS 수집 ──────────────────────────────────────────

def fetch_rss() -> tuple[list[dict], dict]:
    """RSS 피드에서 최신 AI 뉴스를 수집한다. (기사 목록, 피드 건강 상태) 반환."""
    print("[1/4] RSS 피드 수집 중...")

    entries = []
    feed_status = {"ok": [], "fail": []}
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                raise ValueError(feed.bozo_exception)
            source = feed.feed.get("title", "Unknown")
            for entry in feed.entries[:5]:
                title = _strip_html(entry.get("title", ""))
                summary = _strip_html(entry.get("summary", "") or entry.get("description", ""))
                link = entry.get("link", "")
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                entries.append({
                    "source": source,
                    "title": title,
                    "summary": summary[:300],
                    "link": link,
                    "date": published,
                })
            feed_status["ok"].append(source)
        except Exception as e:
            feed_status["fail"].append(url.split("/")[2])  # 도메인만 저장
            print(f"   ⚠ 피드 실패: {url} ({e})")

    entries.sort(key=lambda x: x["date"] or (0,), reverse=True)
    ok, fail = len(feed_status["ok"]), len(feed_status["fail"])
    print(f"   ✓ {len(entries)}개 원문 수집 (성공 {ok}/{len(RSS_FEEDS)}, 실패 {fail})")

    if fail > len(RSS_FEEDS) // 2:
        print(f"   ⚠ 경고: 절반 이상 피드 실패! ({fail}/{len(RSS_FEEDS)})")

    return entries, feed_status


# ── 2. TF-IDF 클러스터링 ─────────────────────────────────

def _extract_entities(text: str) -> set[str]:
    """텍스트에서 고유명사(회사/인물/제품)를 추출한다."""
    text_lower = text.lower()
    found = set()
    for keyword, canonical in ENTITY_ALIASES.items():
        if keyword in text_lower:
            found.add(canonical)
    return found


def cluster_articles(entries: list[dict]) -> list[dict]:
    """TF-IDF 코사인 유사도 + 고유명사 매칭으로 같은 토픽의 기사를 클러스터링한다."""
    print("[2/4] TF-IDF + 고유명사 중복 감지 중...")

    if not entries:
        return []

    # TF-IDF 유사도
    texts = [f"{e['title']} {e['summary']}" for e in entries]
    vectorizer = TfidfVectorizer(stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(texts)
    sim_matrix = cosine_similarity(tfidf_matrix)

    # 고유명사 추출
    entity_sets = [_extract_entities(t) for t in texts]

    # Union-Find
    n = len(entries)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if entries[i]["source"] == entries[j]["source"]:
                continue
            # 조건 1: TF-IDF 유사도 충족
            tfidf_match = sim_matrix[i][j] >= SIMILARITY_THRESHOLD
            # 조건 2: 고유명사 N개 이상 겹침
            shared_entities = entity_sets[i] & entity_sets[j]
            entity_match = len(shared_entities) >= ENTITY_MATCH_THRESHOLD
            if tfidf_match or entity_match:
                union(i, j)

    clusters = {}
    for i in range(n):
        root = find(i)
        if root not in clusters:
            clusters[root] = []
        clusters[root].append(entries[i])

    grouped = []
    for arts in clusters.values():
        sources = list({a["source"] for a in arts})
        representative = max(arts, key=lambda a: len(a["summary"]))
        grouped.append({
            "title": representative["title"],
            "summary": representative["summary"],
            "link": representative["link"],
            "source_count": len(sources),
            "sources": sources,
        })

    grouped.sort(key=lambda x: x["source_count"], reverse=True)
    if grouped:
        print(f"   ✓ {len(grouped)}개 토픽 (최다 {grouped[0]['source_count']}개 매체 중복)")
    return grouped


# ── 3. Gemini 요약 + 검증 ────────────────────────────────

def curate_with_gemini(clustered: list[dict], api_key: str) -> list[dict]:
    """사전 클러스터링된 뉴스를 Gemini로 한국어 요약한다. 재시도 포함."""
    print("[3/4] Gemini로 한국어 요약 중...")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL)

    top_topics = clustered[:8]
    valid_urls = {t["link"] for t in top_topics}
    source_count_map = {t["link"]: t["source_count"] for t in top_topics}
    original_title_map = {t["link"]: t["title"] for t in top_topics}

    articles_text = ""
    for i, t in enumerate(top_topics):
        articles_text += (
            f"\n[토픽 {i+1}] (중복 보도: {t['source_count']}개 매체)\n"
            f"대표 제목: {t['title']}\n"
            f"원문 요약: {t['summary']}\n"
            f"보도 매체: {', '.join(t['sources'])}\n"
            f"URL: {t['link']}\n"
        )

    today = now_kst().strftime("%Y년 %m월 %d일")

    prompt = f"""오늘은 {today}입니다.
아래는 AI 뉴스를 중복 보도 수 기준으로 정렬한 토픽 목록입니다.
중복 보도 수는 이미 코드에서 계산된 정확한 값입니다.

{articles_text}

## 규칙 (엄격히 준수)
1. AI/인공지능과 직접 관련 없는 토픽은 무조건 제외하세요 (영화, 스포츠, 일반 테크 기기 리뷰 등).
2. 남은 토픽 중 상위 {NEWS_COUNT}개를 선택하세요 (중복 보도 수가 많은 순).
3. 각 토픽의 "원문 요약"에 있는 정보만 사용하세요. 원문에 없는 내용을 추가하거나 추측하지 마세요.
4. 한국어로 번역/요약하되, 사실관계를 변경하지 마세요.
5. "sources" 값은 위에 표기된 "중복 보도: N개 매체"의 N을 그대로 사용하세요.
6. "link" 값은 위 목록의 URL을 한 글자도 바꾸지 말고 그대로 복사하세요.

다른 텍스트 없이 JSON 배열만 출력하세요.

[
  {{
    "tag_kr": "카테고리 한국어 (LLM, 이미지AI, 로보틱스, 규제, 기업동향, 연구 중 하나)",
    "tag_en": "카테고리 영어 (LLM, Image AI, Robotics, Regulation, Industry, Research 중 하나)",
    "tag_jp": "카테고리 일본어 (LLM, 画像AI, ロボティクス, 規制, 企業動向, 研究 중 하나)",
    "title_kr": "한국어 제목 (20자 이내)",
    "title_en": "영어 제목 (20자 이내)",
    "title_jp": "일본어 제목 (20자 이내)",
    "company": "관련 회사 도메인 (openai.com, google.com 등). 없으면 빈 문자열",
    "summary_kr": "한국어 핵심 요약 (2~3문장)",
    "summary_en": "영어 핵심 요약 (2~3문장)",
    "summary_jp": "일본어 핵심 요약 (2~3문장)",
    "why_kr": "왜 중요한가 (한국어 1문장)",
    "why_en": "왜 중요한가 (영어 1문장)",
    "why_jp": "왜 중요한가 (일본어 1문장)",
    "sources": "N개 매체 보도",
    "link": "원문 URL (위 목록에서 그대로 복사)"
  }}
]"""

    # 재시도 로직 (검증 통과 수 부족 시에도 재시도)
    verified = []
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()

            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines)

            articles = json.loads(text)

            # 검증: URL 일치 + 매체수 강제 덮어쓰기 + 원문 제목 보존
            verified = []
            for a in articles:
                link = a.get("link", "")
                if link not in valid_urls:
                    print(f"   ⚠ 검증 실패 (URL 불일치): {a.get('title_kr', '')}")
                    continue
                a["sources"] = f"{source_count_map[link]}개 매체 보도"
                a["original_title"] = original_title_map.get(link, "")
                verified.append(a)

            if len(verified) >= NEWS_COUNT:
                break
            print(f"   ⚠ 검증 통과 {len(verified)}개 < {NEWS_COUNT}개, 재시도...")
            time.sleep(2)

        except Exception as e:
            wait = 2 ** attempt
            print(f"   ⚠ 시도 {attempt+1}/{GEMINI_MAX_RETRIES} 실패: {e} ({wait}초 후 재시도)")
            time.sleep(wait)

    if not verified:
        raise RuntimeError(f"Gemini {GEMINI_MAX_RETRIES}회 시도 후에도 검증 통과 뉴스 없음")

    print(f"   ✓ {len(verified)}개 뉴스 검증 통과")
    return verified


# ── 4. HTML 생성 ─────────────────────────────────────────

def build_html(articles: list[dict], archive_link: str = "", feed_status: dict = None, tts_data: dict = None) -> str:
    """뉴스 기사 목록을 다크 테마 HTML로 변환한다."""
    print("[4/4] HTML 생성 중...")

    today = now_kst().strftime("%Y. %m. %d. %A")

    # 피드 건강 상태 배너
    feed_banner = ""
    if feed_status:
        ok = len(feed_status.get("ok", []))
        fail = len(feed_status.get("fail", []))
        total = ok + fail
        if fail > 0:
            failed_domains = ", ".join(feed_status["fail"])
            color = "#e74c3c" if fail > total // 2 else "#e67e22"
            feed_banner = f'<div style="text-align:center;padding:8px;background:{color}20;border:1px solid {color}40;border-radius:8px;margin-bottom:16px;font-size:12px;color:{color};font-family:DM Mono,monospace;">피드 {ok}/{total} 활성 — 실패: {failed_domains}</div>'

    cards = ""
    for i, a in enumerate(articles):
        num = f"{i + 1:02d}"
        link = a.get("link", "#")
        sources = a.get("sources", "")
        company = a.get("company", "")
        original = a.get("original_title", "")
        tag_kr = a.get("tag_kr", a.get("tag", "AI"))
        tag_en = a.get("tag_en", "AI")
        tag_jp = a.get("tag_jp", "AI")
        sources_html = f'<span class="sources">{sources}</span>' if sources else ""
        company_html = f'<img class="company-icon" src="https://www.google.com/s2/favicons?domain={company}&sz=64" alt="">' if company else ""
        original_html = f'<div class="card-original">{original}</div>' if original else ""
        # TTS audio 태그 생성
        audio_tags = ""
        if tts_data:
            for lang in ["kr", "en", "jp"]:
                b64 = tts_data.get(lang, [""] * len(articles))[i] if i < len(tts_data.get(lang, [])) else ""
                if b64:
                    audio_tags += f'<audio class="tts-audio tts-{lang}" preload="none" src="data:audio/mp3;base64,{b64}"></audio>'
        cards += f"""
        <article class="card" style="animation-delay:{i * 0.12}s">
          {audio_tags}
          <div class="card-head">
            <span class="num">{num}</span>
            <span class="badge"><span class="lang kr">{tag_kr}</span><span class="lang en" style="display:none">{tag_en}</span><span class="lang jp" style="display:none">{tag_jp}</span></span>
            {sources_html}
          </div>
          <a href="{link}" target="_blank" class="card-link">
            <h2 class="card-title">{company_html}<span class="lang kr">{a.get("title_kr", "")}</span><span class="lang en" style="display:none">{a.get("title_en", "")}</span><span class="lang jp" style="display:none">{a.get("title_jp", "")}</span></h2>
          </a>
          {original_html}
          <p class="card-summary"><span class="lang kr">{a.get("summary_kr", "")}</span><span class="lang en" style="display:none">{a.get("summary_en", "")}</span><span class="lang jp" style="display:none">{a.get("summary_jp", "")}</span></p>
          <div class="card-why">
            <span class="why-label">WHY IT MATTERS</span>
            <span class="why-text"><span class="lang kr">{a.get("why_kr", "")}</span><span class="lang en" style="display:none">{a.get("why_en", "")}</span><span class="lang jp" style="display:none">{a.get("why_jp", "")}</span></span>
          </div>
        </article>"""

    archive_html = f'<a href="{archive_link}" style="font-family:\'DM Mono\',monospace;font-size:11px;color:#6366f1;text-decoration:none;margin-top:8px;display:inline-block;">Past Briefings →</a>' if archive_link else ""

    # 전체 재생용 audio 태그
    tts_all_audio = ""
    if tts_data:
        for lang in ["kr", "en", "jp"]:
            b64 = tts_data.get(f"{lang}_all", "")
            if b64:
                tts_all_audio += f'<audio id="tts-all-{lang}" preload="none" src="data:audio/mp3;base64,{b64}"></audio>\n'

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>THE SIGNAL — AI Daily Briefing</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500;700&family=Noto+Sans+KR:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0d0d12; color:#e0e0e0; font-family:'Noto Sans KR',sans-serif; font-weight:600; min-height:100vh; }}
  #aurora {{ position:fixed; inset:0; z-index:0; pointer-events:none; }}

  .masthead {{ text-align:center; padding:48px 20px 32px; border-bottom:1px solid #1a1a2e; position:relative; z-index:1; }}
  .masthead-rule {{ width:60px; height:2px; background:linear-gradient(90deg,#6366f1,#a78bfa); margin:0 auto 18px; }}
  .masthead h1 {{ font-size:42px; font-weight:700; letter-spacing:6px; text-transform:uppercase; color:#f0f0f0; margin-bottom:8px; }}
  .masthead .date {{ font-family:'DM Mono',monospace; font-size:13px; color:#777; letter-spacing:1px; font-weight:500; }}
  .masthead .edition {{ font-family:'DM Mono',monospace; font-size:11px; color:#666; font-weight:500; margin-top:4px; }}

  .container {{ max-width:680px; margin:0 auto; padding:28px 20px 100px; display:flex; flex-direction:column; gap:16px; position:relative; z-index:1; }}

  .card {{ background:#12121c; border:1px solid #1e1e30; border-radius:12px; padding:24px; animation:fadeUp .5s ease both; transition:border-color .2s; }}
  .card:hover {{ border-color:#6366f180; }}
  .card.now-playing {{ border-color:#6366f1; box-shadow:0 0 20px #6366f120; }}
  .card-head {{ display:flex; align-items:center; gap:10px; margin-bottom:14px; }}

  /* ── 하단 플레이어 바 ── */
  .player {{ position:fixed; bottom:0; left:0; right:0; z-index:100; background:#0d0d12; padding:16px 0 20px; }}
  .player-inner {{ max-width:680px; margin:0 auto; padding:0 24px; display:flex; align-items:center; gap:24px; }}
  .player-info {{ flex:1; min-width:0; }}
  .player-title {{ font-size:14px; color:#e0e0e0; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .player-sub {{ font-size:12px; color:#555; font-weight:600; margin-top:2px; }}
  .player-controls {{ display:flex; align-items:center; gap:24px; }}
  .p-btn {{ border:none; background:transparent; color:#888; cursor:pointer; font-size:20px; padding:4px; transition:color .15s; font-weight:700; }}
  .p-btn:hover {{ color:#e0e0e0; }}
  .p-btn.main {{ color:#e0e0e0; font-size:26px; font-weight:700; }}
  .p-btn.main:hover {{ color:#fff; }}
  .player-lang {{ display:flex; gap:4px; }}
  .pl-btn {{ font-family:'DM Mono',monospace; font-size:13px; padding:4px 10px; border:none; background:transparent; color:#444; cursor:pointer; transition:color .15s; font-weight:700; }}
  .pl-btn:hover {{ color:#999; }}
  .pl-btn.active {{ color:#c4b5fd; }}
  .player-progress {{ position:absolute; top:0; left:0; right:0; height:2px; background:#1a1a2e; }}
  .player-progress-bar {{ height:100%; background:#6366f1; width:0%; transition:width .3s; }}
  .num {{ font-family:'DM Mono',monospace; font-size:13px; color:#6366f180; font-weight:700; }}
  .badge {{ font-size:12px; color:#c4b5fd; padding:4px 12px; border:1px solid #a78bfa40; border-radius:20px; font-weight:600; background:#a78bfa15; }}
  .sources {{ font-family:'DM Mono',monospace; font-size:11px; color:#777; font-weight:700; margin-left:auto; }}

  .card-link {{ text-decoration:none; color:inherit; }}
  .card-link:hover .card-title {{ color:#a78bfa; }}
  .company-icon {{ width:24px; height:24px; vertical-align:middle; margin-right:8px; border-radius:4px; opacity:.9; }}
  .card-title {{ font-size:20px; font-weight:700; line-height:1.4; margin-bottom:12px; color:#f0f0f0; transition:color .2s; }}
  .card-original {{ font-family:'DM Mono',monospace; font-size:11px; color:#666; margin-bottom:10px; font-weight:500; }}
  .card-summary {{ font-size:14px; line-height:1.8; color:#bbb; margin-bottom:16px; font-weight:500; }}

  .card-why {{ background:#16162a; border-left:2px solid #6366f1; padding:12px 16px; border-radius:0 8px 8px 0; }}
  .why-label {{ font-family:'DM Mono',monospace; font-size:10px; color:#6366f1; letter-spacing:1.5px; display:block; margin-bottom:4px; font-weight:700; }}
  .why-text {{ font-size:13px; color:#aaa; line-height:1.6; font-weight:600; }}

  @keyframes fadeUp {{ from {{ opacity:0; transform:translateY(20px); }} to {{ opacity:1; transform:translateY(0); }} }}
</style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-rule"></div>
    <h1>The Signal</h1>
    <div class="date">{today}</div>
    <div class="edition">AI Daily Briefing — {len(articles)} stories from 11 sources</div>
    {archive_html}
  </header>
  <main class="container">{feed_banner}{cards}</main>
{tts_all_audio}

  <div class="player">
    <div class="player-progress"><div class="player-progress-bar" id="pbar"></div></div>
    <div class="player-inner">
      <div class="player-info">
        <div class="player-title" id="ptitle">재생 대기 중</div>
        <div class="player-sub" id="psub">뉴스를 재생하려면 ▶ 를 누르세요</div>
      </div>
      <div class="player-controls">
        <button class="p-btn" onclick="prevTrack()">⏮</button>
        <button class="p-btn main" id="pbtn" onclick="togglePlay()">▶</button>
        <button class="p-btn" onclick="nextTrack()">⏭</button>
      </div>
      <div class="player-lang">
        <button class="pl-btn active" onclick="setLang('kr')">KR</button>
        <button class="pl-btn" onclick="setLang('en')">EN</button>
        <button class="pl-btn" onclick="setLang('jp')">JP</button>
      </div>
    </div>
  </div>

<canvas id="aurora"></canvas>
<script>
(function(){{
  const c=document.getElementById('aurora'),x=c.getContext('2d');
  let w,h;
  function resize(){{ w=c.width=innerWidth; h=c.height=innerHeight; }}
  resize(); addEventListener('resize',resize);
  const blobs=[
    {{x:.2,y:.25,r:300,dx:.0003,dy:.0002,color:[99,102,241]}},
    {{x:.75,y:.55,r:250,dx:-.0004,dy:.0003,color:[167,139,250]}},
    {{x:.5,y:.8,r:200,dx:.0002,dy:-.0003,color:[99,102,241]}},
  ];
  let t=0;
  function draw(){{
    x.clearRect(0,0,w,h);
    t+=.005;
    blobs.forEach(b=>{{
      const bx=(b.x+Math.sin(t*2+b.dx*1000)*.08)*w;
      const by=(b.y+Math.cos(t*1.5+b.dy*1000)*.06)*h;
      const r=b.r+Math.sin(t*3)*.15*b.r;
      const g=x.createRadialGradient(bx,by,0,bx,by,r);
      g.addColorStop(0,`rgba(${{b.color.join(',')}},0.12)`);
      g.addColorStop(1,'transparent');
      x.fillStyle=g;
      x.fillRect(0,0,w,h);
    }});
    requestAnimationFrame(draw);
  }}
  draw();
}})();

// ── 플레이어 ──
let curLang='kr';
let curIdx=0;
let isPlaying=false;
let curAudio=null;
const cards=document.querySelectorAll('.card');
const pbtn=document.getElementById('pbtn');
const ptitle=document.getElementById('ptitle');
const psub=document.getElementById('psub');
const pbar=document.getElementById('pbar');

function getTitle(i){{
  const card=cards[i];
  if(!card) return '';
  const el=card.querySelector('.card-title .lang.'+curLang);
  return el?el.textContent:'';
}}

function highlight(i){{
  cards.forEach(c=>c.classList.remove('now-playing'));
  if(cards[i]) cards[i].classList.add('now-playing');
  ptitle.textContent=getTitle(i)||'재생 대기 중';
  psub.textContent=`${{i+1}} / ${{cards.length}}`;
  pbar.style.width=`${{((i+1)/cards.length)*100}}%`;
  if(cards[i]) cards[i].scrollIntoView({{behavior:'smooth',block:'center'}});
}}

function playIdx(i){{
  if(curAudio){{ curAudio.pause(); curAudio.currentTime=0; }}
  curIdx=i;
  const card=cards[i];
  if(!card){{ stop(); return; }}
  const audio=card.querySelector('.tts-audio.tts-'+curLang);
  if(!audio){{ nextTrack(); return; }}
  curAudio=audio;
  highlight(i);
  isPlaying=true;
  pbtn.textContent='⏸';
  audio.onended=()=>nextTrack();
  audio.play();
}}

function togglePlay(){{
  if(isPlaying && curAudio){{
    curAudio.pause();
    isPlaying=false;
    pbtn.textContent='▶';
  }} else if(!isPlaying && curAudio && curAudio.currentTime>0){{
    curAudio.play();
    isPlaying=true;
    pbtn.textContent='⏸';
  }} else {{
    playIdx(curIdx);
  }}
}}

function nextTrack(){{
  if(curIdx<cards.length-1) playIdx(curIdx+1);
  else stop();
}}

function prevTrack(){{
  if(curAudio && curAudio.currentTime>3){{ curAudio.currentTime=0; return; }}
  if(curIdx>0) playIdx(curIdx-1);
}}

function stop(){{
  if(curAudio){{ curAudio.pause(); curAudio.currentTime=0; curAudio=null; }}
  isPlaying=false;
  pbtn.textContent='▶';
  cards.forEach(c=>c.classList.remove('now-playing'));
  ptitle.textContent='재생 완료';
  psub.textContent='다시 들으려면 ▶ 를 누르세요';
  pbar.style.width='100%';
  curIdx=0;
}}

function setLang(lang){{
  const wasPlaying=isPlaying;
  if(curAudio){{ curAudio.pause(); curAudio.currentTime=0; }}
  curLang=lang;
  document.querySelectorAll('.lang').forEach(el=>{{
    el.style.display=el.classList.contains(lang)?'inline':'none';
  }});
  document.querySelectorAll('.pl-btn').forEach(btn=>{{
    btn.classList.toggle('active',btn.textContent.toLowerCase()===lang);
  }});
  highlight(curIdx);
  if(wasPlaying) playIdx(curIdx);
}}

// 카드 클릭으로 해당 뉴스 재생
cards.forEach((card,i)=>{{
  card.style.cursor='pointer';
  card.addEventListener('click',e=>{{
    if(e.target.closest('a')) return;
    playIdx(i);
  }});
}});
</script>
</body>
</html>"""

    print("   ✓ HTML 생성 완료")
    return html


def build_error_html(error_msg: str) -> str:
    """빌드 실패 시 에러 페이지."""
    t = now_kst().strftime("%Y. %m. %d. %A %H:%M KST")
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>THE SIGNAL — Build Failed</title>
<style>body{{background:#0d0d12;color:#e0e0e0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.err{{text-align:center;max-width:500px;padding:40px}}.err h1{{color:#e74c3c;font-size:24px;margin-bottom:16px}}
.err p{{color:#999;line-height:1.6;margin-bottom:8px}}.err .time{{color:#6366f1;font-family:monospace;font-size:12px}}</style>
</head><body><div class="err"><h1>빌드 실패</h1><p>오늘의 브리핑을 생성하지 못했습니다.</p>
<p style="color:#777;font-size:13px;">{error_msg}</p><p class="time">{t}</p>
<p><a href="archive/" style="color:#6366f1;">지난 브리핑 보기 →</a></p></div></body></html>"""


# ── 5. Discord 알림 ──────────────────────────────────────

def notify_discord(webhook_url: str, articles: list[dict], page_url: str):
    """Discord 웹훅으로 오늘의 브리핑 알림을 보낸다."""
    if not webhook_url:
        return

    today = now_kst().strftime("%Y. %m. %d.")
    headlines = "\n".join([f"**{i+1}.** {a.get('title_kr', '')}" for i, a in enumerate(articles)])

    payload = json.dumps({
        "embeds": [{
            "title": f"📡 THE SIGNAL — {today}",
            "description": f"{headlines}\n\n[브리핑 보기]({page_url})",
            "color": 0x6366f1,
            "footer": {"text": f"{len(articles)}개 뉴스 · 10개 매체에서 수집"}
        }]
    })

    req = urllib.request.Request(
        webhook_url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
        print("   ✓ Discord 알림 전송 완료")
    except Exception as e:
        print(f"   ⚠ Discord 알림 실패: {e}")


# ── 6. Edge TTS ──────────────────────────────────────────

async def _generate_tts_async(text: str, voice: str) -> bytes:
    """Edge TTS로 mp3 바이트를 생성한다."""
    comm = edge_tts.Communicate(text, voice, rate="+5%")
    buf = BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


def generate_tts(articles: list[dict]) -> dict[str, list[str]]:
    """각 기사를 3개 언어로 TTS 생성, base64 인코딩된 mp3를 반환한다."""
    print("[TTS] Edge TTS 음성 생성 중...")

    result = {"kr": [], "en": [], "jp": []}

    for i, a in enumerate(articles):
        for lang, voice in TTS_VOICES.items():
            title = a.get(f"title_{lang}", "")
            summary = a.get(f"summary_{lang}", "")
            why = a.get(f"why_{lang}", "")
            text = f"{title}. {summary} {why}"
            try:
                mp3_bytes = asyncio.run(_generate_tts_async(text, voice))
                b64 = base64.b64encode(mp3_bytes).decode("ascii")
                result[lang].append(b64)
            except Exception as e:
                print(f"   ⚠ TTS 실패 [{lang}][{i+1}]: {e}")
                result[lang].append("")

    # 전체 읽기용 결합 TTS
    for lang, voice in TTS_VOICES.items():
        full_text = ""
        for j, a in enumerate(articles):
            full_text += f"{j+1}번 뉴스. {a.get(f'title_{lang}', '')}. {a.get(f'summary_{lang}', '')} "
        try:
            mp3_bytes = asyncio.run(_generate_tts_async(full_text, voice))
            b64 = base64.b64encode(mp3_bytes).decode("ascii")
            result[f"{lang}_all"] = b64
        except Exception as e:
            print(f"   ⚠ 전체 TTS 실패 [{lang}]: {e}")
            result[f"{lang}_all"] = ""

    total = sum(len(v) for v in result.values() if isinstance(v, list))
    print(f"   ✓ {total}개 음성 생성 완료")
    return result
