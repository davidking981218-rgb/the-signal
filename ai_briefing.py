"""
THE SIGNAL — AI 모닝 브리핑
RSS 10개 소스에서 뉴스를 수집 → TF-IDF 코사인 유사도로 중복 감지 → Gemini로 한국어 요약.
"""

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from html import unescape

import feedparser
import google.generativeai as genai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ── 설정값 ──────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
NEWS_COUNT = 5
MODEL = "gemini-2.5-flash"
SIMILARITY_THRESHOLD = 0.25  # 제목 유사도 이 이상이면 같은 토픽

# AI 뉴스 RSS 피드 (10개 — 중복 감지를 위해 다양한 매체)
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


def _strip_html(text: str) -> str:
    """HTML 태그 제거."""
    clean = re.sub(r"<[^>]+>", "", unescape(text))
    return re.sub(r"\s+", " ", clean).strip()


def fetch_rss() -> list[dict]:
    """RSS 피드에서 최신 AI 뉴스 원문을 수집한다."""
    print("[1/5] RSS 피드 수집 중...")

    entries = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
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
        except Exception as e:
            print(f"   ⚠ 피드 실패: {url} ({e})")

    entries.sort(key=lambda x: x["date"] or (0,), reverse=True)
    print(f"   ✓ {len(entries)}개 원문 수집 ({len(RSS_FEEDS)}개 매체)")
    return entries


def cluster_articles(entries: list[dict]) -> list[dict]:
    """TF-IDF 코사인 유사도로 같은 토픽의 기사를 클러스터링한다."""
    print("[2/5] TF-IDF 중복 감지 중...")

    if not entries:
        return []

    # 제목 + 요약 결합해서 TF-IDF 벡터화
    texts = [f"{e['title']} {e['summary']}" for e in entries]
    vectorizer = TfidfVectorizer(stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(texts)
    sim_matrix = cosine_similarity(tfidf_matrix)

    # Union-Find로 유사 기사 클러스터링
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
            # 같은 매체의 기사는 클러스터링하지 않음
            if entries[i]["source"] == entries[j]["source"]:
                continue
            if sim_matrix[i][j] >= SIMILARITY_THRESHOLD:
                union(i, j)

    # 클러스터별로 그룹화
    clusters = {}
    for i in range(n):
        root = find(i)
        if root not in clusters:
            clusters[root] = []
        clusters[root].append(entries[i])

    # 클러스터를 매체 수 기준 내림차순 정렬
    grouped = []
    for articles in clusters.values():
        sources = list({a["source"] for a in articles})
        # 가장 긴 요약을 가진 기사를 대표로 선정
        representative = max(articles, key=lambda a: len(a["summary"]))
        grouped.append({
            "title": representative["title"],
            "summary": representative["summary"],
            "link": representative["link"],
            "source_count": len(sources),
            "sources": sources,
            "all_titles": [a["title"] for a in articles],
        })

    grouped.sort(key=lambda x: x["source_count"], reverse=True)
    print(f"   ✓ {len(grouped)}개 토픽으로 클러스터링 (최다 {grouped[0]['source_count']}개 매체 중복)" if grouped else "   ✗ 클러스터 없음")
    return grouped


def curate_with_gemini(clustered: list[dict]) -> list[dict]:
    """사전 클러스터링된 뉴스를 Gemini로 한국어 요약만 시킨다."""
    print("[3/5] Gemini로 한국어 요약 중...")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL)

    # 상위 토픽만 Gemini에게 전달 (최대 8개)
    top_topics = clustered[:8]

    articles_text = ""
    for i, t in enumerate(top_topics):
        articles_text += (
            f"\n[토픽 {i+1}] (중복 보도: {t['source_count']}개 매체)\n"
            f"대표 제목: {t['title']}\n"
            f"원문 요약: {t['summary']}\n"
            f"보도 매체: {', '.join(t['sources'])}\n"
            f"URL: {t['link']}\n"
        )

    today = datetime.now().strftime("%Y년 %m월 %d일")

    prompt = f"""오늘은 {today}입니다.
아래는 AI 뉴스를 중복 보도 수 기준으로 정렬한 토픽 목록입니다.
중복 보도 수는 이미 코드에서 계산된 정확한 값입니다.

{articles_text}

## 규칙 (엄격히 준수)
1. 상위 {NEWS_COUNT}개 토픽만 선택하세요 (중복 보도 수가 많은 순).
2. 각 토픽의 "원문 요약"에 있는 정보만 사용하세요. 원문에 없는 내용을 추가하거나 추측하지 마세요.
3. 한국어로 번역/요약하되, 사실관계를 변경하지 마세요.
4. "sources" 값은 위에 표기된 "중복 보도: N개 매체"의 N을 그대로 사용하세요.

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

    response = model.generate_content(prompt)
    text = response.text.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    articles = json.loads(text)
    print(f"   ✓ {len(articles)}개 뉴스 요약 완료")
    return articles


def _tag_color(tag: str) -> str:
    return "#333"


def build_html(articles: list[dict]) -> str:
    """뉴스 기사 목록을 신문 스타일 HTML로 변환한다."""
    print("[4/5] HTML 브리핑 생성 중...")

    today = datetime.now().strftime("%Y. %m. %d. %A")

    cards = ""
    for i, a in enumerate(articles):
        tag = a.get("tag", "AI")
        color = _tag_color(tag)
        num = f"{i + 1:02d}"
        link = a.get("link", "#")
        sources = a.get("sources", "")
        company = a.get("company", "")
        sources_html = f'<span class="sources">{sources}</span>' if sources else ""
        company_html = f'<img class="company-icon" src="https://www.google.com/s2/favicons?domain={company}&sz=64" alt="">' if company else ""
        cards += f"""
        <article class="card" style="animation-delay:{i * 0.12}s">
          <div class="card-head">
            <span class="num">{num}</span>
            <span class="badge" style="background:{color}">{tag}</span>
            {sources_html}
          </div>
          <a href="{link}" target="_blank" class="card-link">
            <h2 class="card-title">{company_html}{a.get("title", "")}</h2>
          </a>
          <p class="card-summary">{a.get("summary", "")}</p>
          <div class="card-why">
            <span class="why-label">WHY IT MATTERS</span>
            <span class="why-text">{a.get("why", "")}</span>
          </div>
        </article>"""

    ticker_items = "  ///  ".join([a.get("title", "") for a in articles])
    ticker_text = f"{ticker_items}  ///  {ticker_items}"

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

  body {{
    background: #0d0d12;
    color: #e0e0e0;
    font-family: 'Noto Sans KR', sans-serif;
    font-weight: 500;
    min-height: 100vh;
  }}

  .masthead {{
    text-align: center;
    padding: 48px 20px 32px;
    border-bottom: 1px solid #1a1a2e;
  }}
  .masthead-rule {{
    width: 60px; height: 2px;
    background: linear-gradient(90deg, #6366f1, #a78bfa);
    margin: 0 auto 18px;
  }}
  .masthead h1 {{
    font-family: 'Noto Sans KR', sans-serif;
    font-size: 42px;
    font-weight: 700;
    letter-spacing: 6px;
    text-transform: uppercase;
    color: #f0f0f0;
    margin-bottom: 8px;
  }}
  .masthead .date {{
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    color: #777;
    letter-spacing: 1px;
    font-weight: 500;
  }}
  .masthead .edition {{
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: #666;
    font-weight: 500;
    margin-top: 4px;
  }}

  .container {{
    max-width: 680px;
    margin: 0 auto;
    padding: 28px 20px 100px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }}

  .card {{
    background: #12121c;
    border: 1px solid #1e1e30;
    border-radius: 12px;
    padding: 24px;
    animation: fadeUp 0.5s ease both;
    transition: border-color 0.2s;
  }}
  .card:hover {{
    border-color: #6366f180;
  }}

  .card-head {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }}
  .num {{
    font-family: 'DM Mono', monospace;
    font-size: 13px;
    color: #6366f180;
    font-weight: 700;
  }}
  .badge {{
    font-family: 'Noto Sans KR', sans-serif;
    font-size: 12px;
    color: #c4b5fd;
    padding: 4px 12px;
    border: 1px solid #a78bfa40;
    border-radius: 20px;
    letter-spacing: 0.5px;
    font-weight: 600;
    background: #a78bfa15 !important;
  }}
  .sources {{
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: #777;
    font-weight: 500;
    margin-left: auto;
  }}

  .card-link {{
    text-decoration: none;
    color: inherit;
  }}
  .card-link:hover .card-title {{
    color: #a78bfa;
  }}

  .company-icon {{
    width: 24px;
    height: 24px;
    vertical-align: middle;
    margin-right: 8px;
    border-radius: 4px;
    opacity: 0.9;
  }}

  .card-title {{
    font-family: 'Noto Sans KR', sans-serif;
    font-size: 20px;
    font-weight: 700;
    line-height: 1.4;
    margin-bottom: 12px;
    color: #f0f0f0;
    transition: color 0.2s;
  }}

  .card-summary {{
    font-size: 14px;
    line-height: 1.8;
    color: #bbb;
    margin-bottom: 16px;
    font-weight: 400;
  }}

  .card-why {{
    background: #16162a;
    border-left: 2px solid #6366f1;
    padding: 12px 16px;
    border-radius: 0 8px 8px 0;
  }}
  .why-label {{
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    color: #6366f1;
    letter-spacing: 1.5px;
    display: block;
    margin-bottom: 4px;
    font-weight: 700;
  }}
  .why-text {{
    font-size: 13px;
    color: #aaa;
    line-height: 1.6;
    font-weight: 500;
  }}

  .ticker {{
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: #0a0a14;
    border-top: 1px solid #1e1e30;
    color: #6366f1;
    padding: 10px 0;
    overflow: hidden;
    white-space: nowrap;
    z-index: 100;
  }}
  .ticker-track {{
    display: inline-block;
    animation: scroll 30s linear infinite;
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.5px;
  }}

  @keyframes fadeUp {{
    from {{ opacity: 0; transform: translateY(20px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}
  @keyframes scroll {{
    from {{ transform: translateX(0); }}
    to   {{ transform: translateX(-50%); }}
  }}
</style>
</head>
<body>

  <header class="masthead">
    <div class="masthead-rule"></div>
    <h1>The Signal</h1>
    <div class="date">{today}</div>
    <div class="edition">AI Daily Briefing — {len(articles)} stories from 10 sources</div>
  </header>

  <main class="container">
    {cards}
  </main>

  <div class="ticker">
    <div class="ticker-track">{ticker_text}</div>
  </div>

</body>
</html>"""

    print("   ✓ HTML 생성 완료")
    return html


def main():
    """메인 실행: RSS 수집 → 클러스터링 → Gemini 요약 → HTML 생성 → 앱 오픈"""
    print("=" * 44)
    print("  THE SIGNAL — AI Daily Briefing")
    print("=" * 44)

    if not GEMINI_API_KEY:
        print("   ✗ GEMINI_API_KEY 환경변수를 설정하세요.")
        return

    raw = fetch_rss()
    if not raw:
        print("   ✗ RSS 수집 실패")
        return

    clustered = cluster_articles(raw)
    articles = curate_with_gemini(clustered)
    html = build_html(articles)

    print("[5/5] 앱 창 열기...")
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="signal_brief_",
        delete=False, encoding="utf-8",
    )
    tmp.write(html)
    tmp.close()
    file_url = "file:///" + tmp.name.replace("\\", "/")

    # Edge 앱 모드로 열기 (주소창/탭 없는 깔끔한 창)
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    if os.path.exists(edge):
        subprocess.Popen([edge, f"--app={file_url}", "--window-size=760,920"])
    elif os.path.exists(chrome):
        subprocess.Popen([chrome, f"--app={file_url}", "--window-size=760,920"])
    else:
        import webbrowser
        webbrowser.open(file_url)

    print(f"   ✓ 완료!")


if __name__ == "__main__":
    main()
