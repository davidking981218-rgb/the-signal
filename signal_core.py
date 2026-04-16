"""
THE SIGNAL — 공통 코어 모듈
RSS 수집, TF-IDF 클러스터링, Gemini 요약, HTML 생성을 담당한다.
ai_briefing.py(로컬)와 build.py(GitHub Actions)가 공통으로 사용.
"""

import json
import re
import time
from datetime import datetime, timezone, timedelta
from html import unescape

import feedparser
import google.generativeai as genai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ── 설정값 ──────────────────────────────────────────────
NEWS_COUNT = 5
MODEL = "gemini-2.5-flash"
SIMILARITY_THRESHOLD = 0.25
GEMINI_MAX_RETRIES = 3
KST = timezone(timedelta(hours=9))

RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://news.mit.edu/topic/mitartificial-intelligence2-rss.xml",
    "https://openai.com/blog/rss.xml",
    "https://blog.google/technology/ai/rss/",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://venturebeat.com/category/ai/feed/",
    "https://the-decoder.com/feed/",
    "https://www.artificialintelligence-news.com/feed/",
]


def now_kst() -> datetime:
    """한국 시간 기준 현재 시각."""
    return datetime.now(KST)


def _strip_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", unescape(text))
    return re.sub(r"\s+", " ", clean).strip()


# ── 1. RSS 수집 ──────────────────────────────────────────

def fetch_rss() -> list[dict]:
    """RSS 피드에서 최신 AI 뉴스를 수집한다."""
    print("[1/4] RSS 피드 수집 중...")

    entries = []
    feed_ok = 0
    feed_fail = 0
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
            feed_ok += 1
        except Exception as e:
            feed_fail += 1
            print(f"   ⚠ 피드 실패: {url} ({e})")

    entries.sort(key=lambda x: x["date"] or (0,), reverse=True)
    print(f"   ✓ {len(entries)}개 원문 수집 (성공 {feed_ok}/{len(RSS_FEEDS)}, 실패 {feed_fail})")
    return entries


# ── 2. TF-IDF 클러스터링 ─────────────────────────────────

def cluster_articles(entries: list[dict]) -> list[dict]:
    """TF-IDF 코사인 유사도로 같은 토픽의 기사를 클러스터링한다."""
    print("[2/4] TF-IDF 중복 감지 중...")

    if not entries:
        return []

    texts = [f"{e['title']} {e['summary']}" for e in entries]
    vectorizer = TfidfVectorizer(stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(texts)
    sim_matrix = cosine_similarity(tfidf_matrix)

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
            if sim_matrix[i][j] >= SIMILARITY_THRESHOLD:
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
1. 상위 {NEWS_COUNT}개 토픽만 선택하세요 (중복 보도 수가 많은 순).
2. 각 토픽의 "원문 요약"에 있는 정보만 사용하세요. 원문에 없는 내용을 추가하거나 추측하지 마세요.
3. 한국어로 번역/요약하되, 사실관계를 변경하지 마세요.
4. "sources" 값은 위에 표기된 "중복 보도: N개 매체"의 N을 그대로 사용하세요.
5. "link" 값은 위 목록의 URL을 한 글자도 바꾸지 말고 그대로 복사하세요.

다른 텍스트 없이 JSON 배열만 출력하세요.

[
  {{
    "tag": "카테고리 (LLM, 이미지AI, 로보틱스, 규제, 기업동향, 연구 중 하나)",
    "title": "한국어 제목 (20자 이내, 원문 제목의 번역)",
    "company": "관련 회사 도메인 (openai.com, google.com 등). 없으면 빈 문자열",
    "summary": "한국어 핵심 요약 (2~3문장, 원문 요약의 번역만)",
    "why": "왜 중요한가 (원문 내용 기반 1문장)",
    "sources": "N개 매체 보도",
    "link": "원문 URL (위 목록에서 그대로 복사)"
  }}
]"""

    # 재시도 로직
    last_error = None
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()

            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines)

            articles = json.loads(text)
            break
        except Exception as e:
            last_error = e
            wait = 2 ** attempt
            print(f"   ⚠ 시도 {attempt+1}/{GEMINI_MAX_RETRIES} 실패: {e} ({wait}초 후 재시도)")
            time.sleep(wait)
    else:
        raise RuntimeError(f"Gemini {GEMINI_MAX_RETRIES}회 시도 모두 실패: {last_error}")

    # 검증: URL 일치 + 매체수 강제 덮어쓰기 + 원문 제목 보존
    verified = []
    for a in articles:
        link = a.get("link", "")
        if link not in valid_urls:
            print(f"   ⚠ 검증 실패 (URL 불일치): {a.get('title', '')}")
            continue
        a["sources"] = f"{source_count_map[link]}개 매체 보도"
        a["original_title"] = original_title_map.get(link, "")
        verified.append(a)

    print(f"   ✓ {len(verified)}개 뉴스 검증 통과")
    return verified


# ── 4. HTML 생성 ─────────────────────────────────────────

def build_html(articles: list[dict], archive_link: str = "") -> str:
    """뉴스 기사 목록을 다크 테마 HTML로 변환한다."""
    print("[4/4] HTML 생성 중...")

    today = now_kst().strftime("%Y. %m. %d. %A")

    cards = ""
    for i, a in enumerate(articles):
        tag = a.get("tag", "AI")
        num = f"{i + 1:02d}"
        link = a.get("link", "#")
        sources = a.get("sources", "")
        company = a.get("company", "")
        original = a.get("original_title", "")
        sources_html = f'<span class="sources">{sources}</span>' if sources else ""
        company_html = f'<img class="company-icon" src="https://www.google.com/s2/favicons?domain={company}&sz=64" alt="">' if company else ""
        original_html = f'<div class="card-original">{original}</div>' if original else ""
        cards += f"""
        <article class="card" style="animation-delay:{i * 0.12}s">
          <div class="card-head">
            <span class="num">{num}</span>
            <span class="badge">{tag}</span>
            {sources_html}
          </div>
          <a href="{link}" target="_blank" class="card-link">
            <h2 class="card-title">{company_html}{a.get("title", "")}</h2>
          </a>
          {original_html}
          <p class="card-summary">{a.get("summary", "")}</p>
          <div class="card-why">
            <span class="why-label">WHY IT MATTERS</span>
            <span class="why-text">{a.get("why", "")}</span>
          </div>
        </article>"""

    ticker_items = "  ///  ".join([a.get("title", "") for a in articles])
    ticker_text = f"{ticker_items}  ///  {ticker_items}"

    archive_html = f'<a href="{archive_link}" style="font-family:\'DM Mono\',monospace;font-size:11px;color:#6366f1;text-decoration:none;margin-top:8px;display:inline-block;">Past Briefings →</a>' if archive_link else ""

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
  body {{ background:#0d0d12; color:#e0e0e0; font-family:'Noto Sans KR',sans-serif; font-weight:500; min-height:100vh; }}

  .masthead {{ text-align:center; padding:48px 20px 32px; border-bottom:1px solid #1a1a2e; }}
  .masthead-rule {{ width:60px; height:2px; background:linear-gradient(90deg,#6366f1,#a78bfa); margin:0 auto 18px; }}
  .masthead h1 {{ font-size:42px; font-weight:700; letter-spacing:6px; text-transform:uppercase; color:#f0f0f0; margin-bottom:8px; }}
  .masthead .date {{ font-family:'DM Mono',monospace; font-size:13px; color:#777; letter-spacing:1px; font-weight:500; }}
  .masthead .edition {{ font-family:'DM Mono',monospace; font-size:11px; color:#666; font-weight:500; margin-top:4px; }}

  .container {{ max-width:680px; margin:0 auto; padding:28px 20px 100px; display:flex; flex-direction:column; gap:16px; }}

  .card {{ background:#12121c; border:1px solid #1e1e30; border-radius:12px; padding:24px; animation:fadeUp .5s ease both; transition:border-color .2s; }}
  .card:hover {{ border-color:#6366f180; }}
  .card-head {{ display:flex; align-items:center; gap:10px; margin-bottom:14px; }}
  .num {{ font-family:'DM Mono',monospace; font-size:13px; color:#6366f180; font-weight:700; }}
  .badge {{ font-size:12px; color:#c4b5fd; padding:4px 12px; border:1px solid #a78bfa40; border-radius:20px; font-weight:600; background:#a78bfa15; }}
  .sources {{ font-family:'DM Mono',monospace; font-size:11px; color:#777; font-weight:500; margin-left:auto; }}

  .card-link {{ text-decoration:none; color:inherit; }}
  .card-link:hover .card-title {{ color:#a78bfa; }}
  .company-icon {{ width:24px; height:24px; vertical-align:middle; margin-right:8px; border-radius:4px; opacity:.9; }}
  .card-title {{ font-size:20px; font-weight:700; line-height:1.4; margin-bottom:12px; color:#f0f0f0; transition:color .2s; }}
  .card-original {{ font-family:'DM Mono',monospace; font-size:11px; color:#666; margin-bottom:10px; }}
  .card-summary {{ font-size:14px; line-height:1.8; color:#bbb; margin-bottom:16px; font-weight:400; }}

  .card-why {{ background:#16162a; border-left:2px solid #6366f1; padding:12px 16px; border-radius:0 8px 8px 0; }}
  .why-label {{ font-family:'DM Mono',monospace; font-size:10px; color:#6366f1; letter-spacing:1.5px; display:block; margin-bottom:4px; font-weight:700; }}
  .why-text {{ font-size:13px; color:#aaa; line-height:1.6; font-weight:500; }}

  .ticker {{ position:fixed; bottom:0; left:0; right:0; background:#0a0a14; border-top:1px solid #1e1e30; color:#6366f1; padding:10px 0; overflow:hidden; white-space:nowrap; z-index:100; }}
  .ticker-track {{ display:inline-block; animation:scroll 30s linear infinite; font-family:'DM Mono',monospace; font-size:12px; letter-spacing:.5px; }}

  @keyframes fadeUp {{ from {{ opacity:0; transform:translateY(20px); }} to {{ opacity:1; transform:translateY(0); }} }}
  @keyframes scroll {{ from {{ transform:translateX(0); }} to {{ transform:translateX(-50%); }} }}
</style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-rule"></div>
    <h1>The Signal</h1>
    <div class="date">{today}</div>
    <div class="edition">AI Daily Briefing — {len(articles)} stories from 10 sources</div>
    {archive_html}
  </header>
  <main class="container">{cards}</main>
  <div class="ticker"><div class="ticker-track">{ticker_text}</div></div>
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
