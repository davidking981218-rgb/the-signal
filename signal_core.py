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
import wave as _wave
from datetime import datetime, timezone, timedelta
from html import escape, unescape
from io import BytesIO

import edge_tts
import feedparser
import numpy as np
from google import genai
from google.genai import types

# ── 설정값 ──────────────────────────────────────────────
NEWS_COUNT = 5
MODEL = "gemini-2.5-flash"
EMBEDDING_MODEL = "gemini-embedding-001"
SIMILARITY_THRESHOLD = 0.85   # 임베딩 코사인 유사도 기준 (의미 기반, 다국어 호환). 0.80은 과묶음
EMBEDDING_BATCH_SIZE = 100    # 배치당 텍스트 수 (요청당 20K 토큰 제한 안전 마진)
EMBEDDING_BATCH_SLEEP = 20    # 배치 사이 대기 (초) — RPM/TPM 여유 확보
GEMINI_MAX_RETRIES = 5
KST = timezone(timedelta(hours=9))

# Edge TTS 음성 (Gemini 실패 시 폴백용)
TTS_VOICES = {
    "kr": "ko-KR-SunHiNeural",
    "en": "en-US-AriaNeural",
    "jp": "ja-JP-NanamiNeural",
}

# Gemini TTS (1차 엔진) — 실패 시 Edge TTS로 폴백
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_TTS_VOICE = "Charon"
GEMINI_TTS_DELAY = 25  # 무료 티어 3 RPM 준수 (60/3 + 여유)
GEMINI_TTS_MAX_FAILS = 3  # 연속 N회 실패 시 나머지 전부 Edge로 전환

# AI 분야 주요 고유명사 사전 (제목/본문 매칭 → 파비콘 도메인 매핑)
# 주의: substring 매칭이므로 "intel", "arm", "character", "figure" 처럼
# 일반 단어에 섞여 오탐될 수 있는 키워드는 피한다.
ENTITY_ALIASES = {
    # 빅5
    "openai": "openai", "open ai": "openai", "chatgpt": "openai", "codex": "openai", "gpt-4": "openai", "gpt-5": "openai", "gpt-4o": "openai", "sora": "openai", "dall-e": "openai", "sam altman": "openai",
    "google": "google", "deepmind": "google", "gemini": "google",
    "anthropic": "anthropic", "claude": "anthropic", "dario amodei": "anthropic",
    "meta": "meta", "llama": "meta", "zuckerberg": "meta",
    "microsoft": "microsoft", "copilot": "microsoft", "satya nadella": "microsoft",
    # 빅테크
    "apple": "apple", "apple intelligence": "apple",
    "nvidia": "nvidia", "jensen huang": "nvidia",
    "amazon": "amazon", "aws": "amazon",
    "ibm": "ibm", "oracle": "oracle",
    "samsung": "samsung",
    "tencent": "tencent", "baidu": "baidu",
    # 주요 AI 스타트업/모델
    "mistral": "mistral",
    "stability": "stability",
    "midjourney": "midjourney",
    "xai": "xai", "grok": "xai", "elon musk": "xai",
    "cohere": "cohere",
    "perplexity": "perplexity",
    "databricks": "databricks",
    "character.ai": "character_ai", "character ai": "character_ai",
    "elevenlabs": "elevenlabs", "eleven labs": "elevenlabs",
    "runway": "runway", "runwayml": "runway",
    "suno": "suno",
    "groq": "groq",
    "sakana": "sakana",
    # 중국 AI
    "alibaba": "alibaba", "qwen": "alibaba",
    "deepseek": "deepseek",
    "bytedance": "bytedance", "tiktok": "bytedance",
    # 클라우드/인프라
    "cloudflare": "cloudflare",
    "hugging face": "huggingface", "huggingface": "huggingface",
    "vercel": "vercel",
    # 로봇
    "unitree": "unitree", "유니트리": "unitree",
    "figure.ai": "figure_ai", "figure ai": "figure_ai",
    "boston dynamics": "boston_dynamics",
    # 반도체/하드웨어
    "tsmc": "tsmc",
    "amd ": "amd",  # 뒤 공백으로 일반 단어 오탐 방지
    # 기타 자주 등장
    "palantir": "palantir",
    # 한국어 별칭 (AI타임스 등 한국어 매체 카드 요약 매칭용)
    # 주의: "메타"(메타버스/메타데이터), "라마"(드라마) 등 일반 단어 오탐 가능한 것은 제외
    "오픈ai": "openai", "오픈에이아이": "openai", "챗gpt": "openai", "챗지피티": "openai", "코덱스": "openai", "소라": "openai", "달리": "openai",
    "구글": "google", "딥마인드": "google", "제미나이": "google", "제미니": "google",
    "앤트로픽": "anthropic", "클로드": "anthropic",
    "마이크로소프트": "microsoft", "코파일럿": "microsoft",
    "애플": "apple",
    "엔비디아": "nvidia",
    "아마존": "amazon",
    "삼성": "samsung",
    "퍼플렉시티": "perplexity",
    "허깅페이스": "huggingface",
}

# 고유명사 → 파비콘 도메인 (Gemini 힌트 실패 시 백업 매칭용)
# 주 경로는 Gemini company_domain 힌트이며, 이 딕셔너리는 Gemini가 실패했을 때만 사용된다.
ENTITY_DOMAINS = {
    # 빅5
    "openai": "openai.com",
    "google": "google.com",
    "anthropic": "anthropic.com",
    "meta": "meta.com",
    "microsoft": "microsoft.com",
    # 빅테크
    "apple": "apple.com",
    "nvidia": "nvidia.com",
    "amazon": "aws.amazon.com",
    "ibm": "ibm.com",
    "oracle": "oracle.com",
    "samsung": "samsung.com",
    "tencent": "tencent.com",
    "baidu": "baidu.com",
    # AI 스타트업/모델
    "mistral": "mistral.ai",
    "stability": "stability.ai",
    "midjourney": "midjourney.com",
    "xai": "x.ai",
    "cohere": "cohere.com",
    "perplexity": "perplexity.ai",
    "databricks": "databricks.com",
    "character_ai": "character.ai",
    "elevenlabs": "elevenlabs.io",
    "runway": "runwayml.com",
    "suno": "suno.com",
    "groq": "groq.com",
    "sakana": "sakana.ai",
    # 중국 AI
    "alibaba": "alibabagroup.com",
    "deepseek": "deepseek.com",
    "bytedance": "bytedance.com",
    # 클라우드/인프라
    "cloudflare": "cloudflare.com",
    "huggingface": "huggingface.co",
    "vercel": "vercel.com",
    # 로봇
    "unitree": "unitree.com",
    "figure_ai": "figure.ai",
    "boston_dynamics": "bostondynamics.com",
    # 반도체
    "tsmc": "tsmc.com",
    "amd": "amd.com",
    # 기타
    "palantir": "palantir.com",
}

# 도메인 형식 검증 정규식 — Gemini가 반환한 company_domain이 실제 도메인 형태인지만 확인
# 화이트리스트 대신 형식 검증만 수행 → 스탠포드/소프트뱅크 등 미리 등록 안 된 기관도 자동 커버
# 통과 예: "openai.com", "stanford.edu", "softbank.jp", "group.softbank"
# 탈락 예: "Stanford University", "www.stanford.edu/page", "openai", ""
_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def _is_valid_domain_format(s: str) -> bool:
    """Gemini 힌트를 형식만으로 검증. DNS 조회는 하지 않는다."""
    if not s or len(s) > 253:
        return False
    return bool(_DOMAIN_RE.match(s))


def _most_mentioned_entity_domain(*texts: str) -> str:
    """여러 텍스트를 합쳐서 가장 많이 언급된 엔티티의 도메인을 반환한다.
    빈도수 기반이라 '구글이 오픈AI에 대응...' 같은 경우에도
    실제 주인공(더 많이 언급된 쪽)을 정확히 잡는다.
    """
    combined = " ".join(t for t in texts if t).lower()
    if not combined:
        return ""
    counts: dict[str, int] = {}  # canonical → 등장 횟수
    for keyword, canonical in ENTITY_ALIASES.items():
        if canonical not in ENTITY_DOMAINS:
            continue
        n = combined.count(keyword)
        if n > 0:
            counts[canonical] = counts.get(canonical, 0) + n
    if not counts:
        return ""
    winner = max(counts, key=counts.get)
    return ENTITY_DOMAINS[winner]

# 매체별 신뢰도 가중치 (AI 기술 기사 품질 기준, 매체 수 동일 시 정렬에 사용)
TIER_0_WEIGHT = 4  # 공식 1차 소스: 단 1곳만 보도해도 최상위로 올림
# Tier 0 매체 → 브랜드 키 (카드 컬러링용). Tier 0가 아닌 매체는 등록하지 않는다.
SOURCE_BRAND = {
    "OpenAI News": "openai",
    "The latest research from Google": "google",
    "Google DeepMind News": "google",  # Gemini/DeepMind 모두 구글 4색으로 통일
    "Anthropic News": "anthropic",
    "NVIDIA Blog": "nvidia",
    "Microsoft Research": "microsoft",
}
# 브랜드 키 → 공식 파비콘 도메인. Tier 0 매체가 포함된 기사는 요약에 회사명이 없어도
# 이 매핑으로 파비콘을 강제한다 (Gemini 힌트가 비어 있는 경우 대비).
BRAND_FAVICON_DOMAIN = {
    "openai": "openai.com",
    "google": "google.com",
    "anthropic": "anthropic.com",
    "nvidia": "nvidia.com",
    "microsoft": "microsoft.com",
}

# 공식 Tier 0 도메인 → brand_key. URL 기반이라 매체명 매핑(SOURCE_BRAND)보다 정확.
_OFFICIAL_DOMAINS = (
    ("openai.com", "openai"),
    ("research.google", "google"),
    ("deepmind.google", "google"),
    ("blog.google", "google"),
    ("nvidia.com", "nvidia"),
    ("microsoft.com", "microsoft"),
    ("anthropic.com", "anthropic"),
)


def _brand_from_link(link: str) -> str:
    """대표 기사 URL의 도메인이 Tier 0 공식 도메인이면 brand_key 반환, 아니면 빈 문자열."""
    if not link:
        return ""
    from urllib.parse import urlparse
    netloc = urlparse(link).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    for official, key in _OFFICIAL_DOMAINS:
        if netloc == official or netloc.endswith("." + official):
            return key
    return ""
SOURCE_WEIGHT = {
    # Tier 0 — 공식 1차 소스 (단독 보도라도 무조건 최상위)
    "OpenAI News": 4,
    "The latest research from Google": 4,              # Google Research
    "Google DeepMind News": 4,                          # DeepMind (Gemma, AlphaFold 등 프론티어 연구 발표)
    "Anthropic News": 4,                                # 커뮤니티 피드 (taobojlen/anthropic-rss-feed)
    "NVIDIA Blog": 4,                                   # AI/로봇/GPU 공식
    "Microsoft Research": 4,                            # Copilot/Azure AI 연구
    # Tier 1 — 진짜 권위지 (학회지 + 권위 저널리즘 + 권위 종합지)
    "IEEE Spectrum": 3,                                  # IEEE 학회 공식 매거진
    "Artificial intelligence – MIT Technology Review": 3,  # MIT 운영 권위 저널리즘
    "AI (artificial intelligence) | The Guardian": 3,    # Guardian AI 전용 섹션
    "NYT > Technology": 3,                               # NYT Technology 섹션 (AI 외 기사 섞임)
    "BBC News": 3,                                       # BBC Technology 섹션 (피드 타이틀이 "BBC News")
    # Tier 2 — AI 속보 매체 + 전문지 + 실용지
    "AI News & Artificial Intelligence | TechCrunch": 2,
    "AI | The Verge": 2,
    "AI | VentureBeat": 2,
    "The Decoder": 2,
    "The Rundown AI": 2,
    "Latest stories for ZDNET in Artificial Intelligence": 2,
    "Simon Willison's Weblog": 2,                        # AI 해설 개인 블로그 (매일 업데이트)
    "AI타임스 - AI기술": 2,
    "AI타임스 - AI산업": 2,
    "ITmedia AI＋ 最新記事一覧": 2,
    # Tier 3 — 종합 테크 (AI 외 기사 많음)
    "Feed: Artificial Intelligence Latest": 1,           # Wired
}
_DEFAULT_WEIGHT = 1

# Tier 1 내에서 대표 기사 선정 시 우선권을 갖는 권위 종합지 집합.
# 같은 사건을 여러 Tier 1 매체가 다룰 때 이 세 곳 기사를 대표로 채택.
PREMIUM_TIER1_SOURCES = {
    "AI (artificial intelligence) | The Guardian",
    "NYT > Technology",
    "BBC News",
}

RSS_FEEDS = [
    # Tier 0 — 공식 1차 소스 (공식 발표, 논문, 연구 블로그)
    "https://openai.com/news/rss.xml",                                          # OpenAI News
    "https://research.google/blog/rss",                                         # Google Research
    "https://deepmind.google/blog/rss.xml",                                     # Google DeepMind
    "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml",  # Anthropic News (커뮤니티 피드)
    "https://blogs.nvidia.com/feed/",                                           # NVIDIA Blog
    "https://www.microsoft.com/en-us/research/feed/",                           # Microsoft Research
    # 종합 테크 (AI 섹션) — 속보 겹침 가능성 높음
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://www.theguardian.com/technology/artificialintelligenceai/rss",      # Guardian AI (AI 전용 섹션)
    # AI 전문 매체 — 매일 다수 기사, 같은 사건 중복 보도
    "https://the-decoder.com/feed/",
    # 권위 있는 기술 저널 / 권위 종합지 Tech 섹션
    "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",        # IEEE Spectrum (AI)
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/",     # MIT Technology Review (AI)
    "https://www.zdnet.com/topic/artificial-intelligence/news/rss.xml",         # ZDNet AI
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",              # NYT Technology (AI 외 기사는 필터로 제거)
    "https://feeds.bbci.co.uk/news/technology/rss.xml",                         # BBC Technology
    # AI 뉴스레터 — 주요 뉴스 큐레이션
    "https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml",                             # The Rundown AI
    # 개인 AI 해설 블로그
    "https://simonwillison.net/atom/everything/",                               # Simon Willison's Weblog
    # 한국 AI 전문 (카테고리 분리 — allArticle 오염 제거)
    "https://www.aitimes.com/rss/S1N24.xml",                                    # AI타임스 - AI기술
    "https://www.aitimes.com/rss/S1N3.xml",                                     # AI타임스 - AI산업
    # 일본 AI 전문
    "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",                             # ITmedia AI+
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

    from calendar import timegm

    entries = []
    feed_status = {"ok": [], "fail": []}

    def _collect(cutoff_hours):
        """지정된 시간 이내의 기사만 수집한다."""
        result = []
        cutoff = datetime.now(KST) - timedelta(hours=cutoff_hours)
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                if feed.bozo and not feed.entries:
                    raise ValueError(feed.bozo_exception)
                source = feed.feed.get("title", "Unknown")
                for entry in feed.entries:
                    published = entry.get("published_parsed") or entry.get("updated_parsed")
                    if not published:
                        continue
                    pub_dt = datetime.fromtimestamp(timegm(published), tz=KST)
                    if pub_dt < cutoff:
                        continue
                    title = _strip_html(entry.get("title", ""))
                    summary = _strip_html(entry.get("summary", "") or entry.get("description", ""))
                    link = entry.get("link", "")
                    result.append({
                        "source": source,
                        "title": title,
                        "summary": summary[:300],
                        "link": link,
                        "date": published,
                    })
                if not feed_status.get("_done"):
                    feed_status["ok"].append(source)
            except Exception as e:
                if not feed_status.get("_done"):
                    feed_status["fail"].append(url.split("/")[2])
                    print(f"   ⚠ 피드 실패: {url} ({e})")
        return result

    entries = _collect(24)
    feed_status["_done"] = True

    if len(entries) < NEWS_COUNT:
        print(f"   ⚠ 24시간 이내 기사 {len(entries)}개 < {NEWS_COUNT}개, 36시간으로 확장...")
        entries = _collect(36)

    entries.sort(key=lambda x: x["date"], reverse=True)
    ok, fail = len(feed_status["ok"]), len(feed_status["fail"])
    print(f"   ✓ {len(entries)}개 원문 수집 (성공 {ok}/{len(RSS_FEEDS)}, 실패 {fail})")

    if fail > len(RSS_FEEDS) // 2:
        print(f"   ⚠ 경고: 절반 이상 피드 실패! ({fail}/{len(RSS_FEEDS)})")

    return entries, feed_status


# ── 2. 임베딩 기반 클러스터링 ─────────────────────────────

def _embed_texts(texts: list[str], api_key: str) -> np.ndarray:
    """Gemini embedding API로 텍스트를 벡터화한다. 배치 분할 + rate limit 여유 확보."""
    client = genai.Client(api_key=api_key)
    all_vectors: list[list[float]] = []
    total_batches = (len(texts) + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE
    for b in range(total_batches):
        batch = texts[b * EMBEDDING_BATCH_SIZE : (b + 1) * EMBEDDING_BATCH_SIZE]
        print(f"   · 임베딩 배치 {b + 1}/{total_batches} ({len(batch)}개)...")
        resp = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(task_type="SEMANTIC_SIMILARITY"),
        )
        for emb in resp.embeddings:
            all_vectors.append(emb.values)
        if b < total_batches - 1:
            time.sleep(EMBEDDING_BATCH_SLEEP)
    return np.array(all_vectors, dtype=np.float32)


def cluster_articles(entries: list[dict], api_key: str) -> list[dict]:
    """Gemini 임베딩으로 같은 사건의 기사를 묶는다. 의미 기반 + 다국어 크로스링구얼 지원."""
    print("[2/4] Gemini 임베딩 기반 중복 감지 중...")

    if not entries:
        return []

    texts = [f"{e['title']} {e['summary']}" for e in entries]
    vectors = _embed_texts(texts, api_key)

    # 코사인 유사도 = 정규화 후 내적
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = vectors / np.where(norms == 0, 1, norms)
    sim_matrix = normalized @ normalized.T

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

    clusters: dict[int, list[dict]] = {}
    for i in range(n):
        root = find(i)
        clusters.setdefault(root, []).append(entries[i])

    grouped = []
    for arts in clusters.values():
        sources = list({a["source"] for a in arts})
        # 대표 기사: 신뢰도(Tier) → 프리미엄 Tier 1 매체(Guardian/NYT/BBC) 우선 → 요약 길이
        representative = max(
            arts,
            key=lambda a: (
                SOURCE_WEIGHT.get(a["source"], _DEFAULT_WEIGHT),
                a["source"] in PREMIUM_TIER1_SOURCES,
                len(a["summary"]),
            ),
        )
        # brand_key는 대표 기사의 URL 도메인 기준으로 결정.
        # 클러스터에 Tier 0 피드 기사가 "섞여있다"는 이유만으로 강제하지 않음
        # (임베딩 오분류 대응). 대표 기사가 실제 공식 도메인에서 왔을 때만 강제.
        brand_key = _brand_from_link(representative.get("link", ""))
        grouped.append({
            "title": representative["title"],
            "summary": representative["summary"],
            "link": representative["link"],
            "source_count": len(sources),
            "sources": sources,
            "date": representative.get("date"),
            "brand_key": brand_key,
        })

    # 정렬: Tier 0 포함 여부 → 매체 수 → 매체 신뢰도
    # Tier 0(공식 1차 소스)이 단독 보도해도 무조건 최상위로 올림
    def _sort_key(topic):
        best_weight = max(SOURCE_WEIGHT.get(s, _DEFAULT_WEIGHT) for s in topic["sources"])
        has_tier0 = best_weight >= TIER_0_WEIGHT
        return (has_tier0, topic["source_count"], best_weight)

    grouped.sort(key=_sort_key, reverse=True)
    if grouped:
        print(f"   ✓ {len(grouped)}개 토픽 (최다 {grouped[0]['source_count']}개 매체 중복)")
    return grouped


# ── 2.5. AI 관련성 필터 ────────────────────────────────

# AI 기술과 무관한 뉴스를 걸러내는 블랙리스트 (영어 정규식)
_BLACKLIST_PATTERNS = [
    r'\bthreat\b', r'\bsanction', r'\bmilitary\b', r'\bwar\b', r'\bweapon',
    r'\btariff', r'\btrade war', r'\belection', r'\bvote\b',
    r'\bkill', r'\bdeath\b', r'\bcrime\b', r'\barrest',
    r'\bdatacenter attack', r'\bcyberattack(?!.*detect)', r'\bhack(?!athon)',
]
# AI 기술 관련 화이트리스트 (영어 정규식, 하나라도 매치되면 통과)
_WHITELIST_PATTERNS = [
    r'\bLLM\b', r'\bGPT', r'\btransformer\b', r'\bfine.?tun', r'\bRAG\b',
    r'\bmodel\b.*\b(?:train|launch|release|param)', r'\bagent\b',
    r'\bdiffusion\b', r'\bimage generat', r'\bvideo generat',
    r'\brobot', r'\bautonomous', r'\bself.?driving',
    r'\bchip\b.*\b(?:AI|GPU|NPU)', r'\bGPU\b', r'\bTPU\b',
    r'\bopen.?source.*model', r'\bbenchmark\b', r'\btoken\b',
    r'\bprompt', r'\bchatbot\b', r'\bcopilot\b', r'\bassistant\b',
    r'\bAPI\b.*\b(?:launch|release|update)', r'\bsafety\b.*\bAI\b',
    r'\bregulat.*\bAI\b', r'\bAI act\b', r'\bAI policy',
]

# 한국어/일본어 키워드 (단어 경계 없이 substring 매칭, 원문 대소문자 유지)
_WHITELIST_SUBSTRINGS = [
    # 한국어
    "인공지능", "머신러닝", "딥러닝", "생성형", "생성 AI",
    "대규모 언어모델", "언어모델", "언어 모델",
    "클로드", "제미나이", "라마", "챗봇", "챗GPT",
    "로봇", "자율주행", "에이전트", "프롬프트",
    "오픈소스 모델", "파인튜닝", "미세 조정",
    "이미지 생성", "영상 생성", "비디오 생성",
    "거대언어모델", "초거대",
    # 일본어
    "人工知能", "機械学習", "深層学習", "生成AI", "生成 AI",
    "大規模言語モデル", "言語モデル",
    "クロード", "ジェミニ", "ロボット", "自動運転",
    "エージェント", "プロンプト", "ファインチューニング",
    "画像生成", "動画生成",
]
# 한국어/일본어 블랙리스트 (substring 매칭)
_BLACKLIST_SUBSTRINGS = [
    # 한국어 — AI와 확실히 무관한 주제
    "재생에너지", "태양광", "풍력", "가정용 배터리", "에너지 대전환",
    "기후환경에너지 대전", "지역축제", "녹색 산업",
    "추경 예산", "추경예산", "유류비 지원",
    "선거", "투표", "전쟁", "군사", "무기", "관세",
    # 일본어
    "エネルギー転換", "再生可能エネルギー", "戦争", "選挙",
]


def filter_ai_relevant(items: list[dict]) -> list[dict]:
    """AI 기술과 직접 관련 없는 항목을 제거한다. 기사(entry) 리스트와 토픽(cluster) 리스트 모두 지원.
    title/summary 필드만 참조하므로 두 경우 모두 동일하게 동작한다.
    영어 정규식 + 한/일 substring 매칭을 병행. 블랙 단독 → 제외, 블랙+화이트 → Gemini 위임."""
    print("[1.5/4] AI 관련성 필터링 중 (임베딩 전)...")
    filtered = []
    for item in items:
        text_lower = f"{item['title']} {item['summary']}".lower()
        text_raw = f"{item['title']} {item['summary']}"

        has_whitelist = (
            any(re.search(p, text_lower) for p in _WHITELIST_PATTERNS)
            or any(sub in text_raw for sub in _WHITELIST_SUBSTRINGS)
        )
        has_blacklist = (
            any(re.search(p, text_lower) for p in _BLACKLIST_PATTERNS)
            or any(sub in text_raw for sub in _BLACKLIST_SUBSTRINGS)
        )

        if has_blacklist and not has_whitelist:
            print(f"   ✗ 제외: {item['title'][:60]}")
            continue
        if has_blacklist and has_whitelist:
            print(f"   △ 애매: {item['title'][:60]} → Gemini 판단 위임")
        filtered.append(item)
    print(f"   ✓ {len(filtered)}/{len(items)}개 통과")
    return filtered


# ── 3. Gemini 요약 + 검증 ────────────────────────────────

def curate_with_gemini(clustered: list[dict], api_key: str) -> list[dict]:
    """사전 클러스터링된 뉴스를 Gemini로 한국어 요약한다. 재시도 포함."""
    print("[3/4] Gemini로 한국어 요약 중...")

    client = genai.Client(api_key=api_key)

    top_topics = clustered[:8]
    idx_to_topic = {i: t for i, t in enumerate(top_topics)}

    articles_text = ""
    for i, t in enumerate(top_topics):
        articles_text += (
            f"\n[토픽 {i+1}] (중복 보도: {t['source_count']}개 매체)\n"
            f"대표 제목: {t['title']}\n"
            f"원문 요약: {t['summary']}\n"
            f"보도 매체: {', '.join(t['sources'])}\n"
        )

    today = now_kst().strftime("%Y년 %m월 %d일")

    prompt = f"""오늘은 {today}입니다.
아래는 AI 뉴스를 중복 보도 수 기준으로 정렬한 토픽 목록입니다.
중복 보도 수는 이미 코드에서 계산된 정확한 값입니다.

{articles_text}

## 규칙 (엄격히 준수)
1. AI 기술/제품/연구와 직접 관련 없는 토픽은 무조건 제외하세요. AI 회사가 언급되더라도 내용이 정치, 군사, 외교, 부동산, 데이터센터 물리적 위협 등이면 제외합니다.
2. 남은 토픽 중 상위 {NEWS_COUNT}개를 선택하세요 (중복 보도 수가 많은 순).
3. 각 토픽의 "원문 요약"에 있는 정보만 사용하세요. 원문에 없는 내용을 추가하거나 추측하지 마세요.
4. 한국어로 번역/요약하되, 사실관계를 변경하지 마세요.
5. "topic_index"는 위 목록의 [토픽 N]에서 N을 그대로 사용하세요.

다른 텍스트 없이 JSON 배열만 출력하세요.

[
  {{
    "topic_index": 토픽 번호 (정수),
    "tag_kr": "카테고리 한국어 (LLM, 이미지AI, 로보틱스, 규제, 기업동향, 연구 중 하나)",
    "tag_en": "카테고리 영어 (LLM, Image AI, Robotics, Regulation, Industry, Research 중 하나)",
    "tag_jp": "카테고리 일본어 (LLM, 画像AI, ロボティクス, 規制, 企業動向, 研究 중 하나)",
    "title_kr": "한국어 제목 (20자 이내)",
    "title_en": "영어 제목 (20자 이내)",
    "title_jp": "일본어 제목 (20자 이내)",
    "summary_kr": "한국어 핵심 요약 (2~3문장)",
    "summary_en": "영어 핵심 요약 (2~3문장)",
    "summary_jp": "일본어 핵심 요약 (2~3문장)",
    "why_kr": "왜 중요한가 (한국어 1문장)",
    "why_en": "왜 중요한가 (영어 1문장)",
    "why_jp": "왜 중요한가 (일본어 1문장)",
    "company_domain": "기사의 주체(subject) 기업/기관/대학의 공식 웹사이트 도메인. 반드시 순수 도메인만 반환 (예: 'openai.com', 'stanford.edu', 'softbank.jp', 'naver.com', 'nec.com'). URL/프로토콜/경로 포함 금지(https://, www., /path 금지). 한국/일본 기업/기관도 반드시 도메인으로 반환 (예: 소프트뱅크→softbank.jp, 스탠포드→stanford.edu, 혼다→honda.com, NEC→nec.com). 주체가 여러 곳이면 가장 핵심 주어 1개만. 정말 모를 때만 빈 문자열 '' 반환."
  }}
]"""

    # 재시도 로직 (검증 통과 수 부족 시에도 재시도)
    verified = []
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            text = response.text.strip()

            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines)

            articles = json.loads(text)

            # topic_index로 원본 매칭 → URL·매체수·제목은 코드에서 직접 매핑 (Gemini는 요약만 담당)
            verified = []
            seen_indices = set()
            for a in articles:
                idx = a.get("topic_index")
                if not isinstance(idx, int):
                    continue
                idx -= 1  # 1-based → 0-based
                topic = idx_to_topic.get(idx)
                if topic is None or idx in seen_indices:
                    continue
                seen_indices.add(idx)

                # 원본 데이터로 덮어쓰기 (Gemini에게 맡기지 않는 필드)
                a["link"] = topic["link"]
                count = topic["source_count"]
                a["sources_kr"] = f"{count}개 매체 보도"
                a["sources_en"] = f"Covered by {count} sources"
                a["sources_jp"] = f"{count}社が報道"
                a["original_title"] = topic["title"]
                a["published"] = topic.get("date")
                a["brand_key"] = topic.get("brand_key", "")
                # 파비콘 도메인 결정 순서
                # 0차: Tier 0 매체가 클러스터에 있으면 해당 브랜드의 공식 도메인 강제
                domain = ""
                brand_key_for_icon = topic.get("brand_key", "")
                if brand_key_for_icon and brand_key_for_icon in BRAND_FAVICON_DOMAIN:
                    domain = BRAND_FAVICON_DOMAIN[brand_key_for_icon]
                # 1차: 텍스트 빈도수 기반 (AI 안 씀, 환각 없음)
                #      모든 텍스트를 합쳐서 가장 많이 언급된 회사 = 주인공
                if not domain:
                    domain = _most_mentioned_entity_domain(
                        a.get("summary_kr", ""), a.get("title_kr", ""),
                        topic.get("title", ""), topic.get("summary", ""),
                    )
                # 2차: Gemini 힌트 (빈도수로 못 찾았을 때만, 형식 검증)
                if not domain:
                    hint = str(a.get("company_domain", "")).strip().lower()
                    if hint.startswith("http://") or hint.startswith("https://"):
                        hint = hint.split("://", 1)[1]
                    if hint.startswith("www."):
                        hint = hint[4:]
                    hint = hint.split("/", 1)[0].strip()
                    if _is_valid_domain_format(hint):
                        domain = hint
                a["company"] = domain
                verified.append(a)

            if len(verified) >= NEWS_COUNT:
                break
            print(f"   ⚠ 검증 통과 {len(verified)}개 < {NEWS_COUNT}개, 재시도...")
            time.sleep(2)

        except Exception as e:
            if attempt < GEMINI_MAX_RETRIES - 1:
                wait = [30, 90, 180, 360][attempt]  # 503 스파이크가 분 단위로 지속되는 경우 대응 (총 ~11분)
                print(f"   ⚠ 시도 {attempt+1}/{GEMINI_MAX_RETRIES} 실패: {e} ({wait}초 후 재시도)")
                time.sleep(wait)
            else:
                print(f"   ⚠ 시도 {attempt+1}/{GEMINI_MAX_RETRIES} 실패: {e}")

    if not verified:
        raise RuntimeError(f"Gemini {GEMINI_MAX_RETRIES}회 시도 후에도 검증 통과 뉴스 없음")

    # cluster_articles의 정렬 순서 복원 (Tier 0 우선 → 매체 수 → 신뢰도)
    # Gemini가 프롬프트 규칙에 따라 순서를 바꿔 반환할 수 있으므로 topic_index 기준으로 재정렬
    verified.sort(key=lambda a: a.get("topic_index", 999))

    print(f"   ✓ {len(verified)}개 뉴스 검증 통과")
    return verified


# ── 4. HTML 생성 ─────────────────────────────────────────

def _format_published(date_tuple) -> str:
    """time.struct_time을 읽기 쉬운 문자열로 변환한다."""
    if not date_tuple:
        return ""
    try:
        from calendar import timegm
        dt = datetime.fromtimestamp(timegm(date_tuple), tz=KST)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return ""


def build_html(articles: list[dict], archive_link: str = "", feed_status: dict = None, tts_data: dict = None, audio_prefix: str = "audio/") -> str:
    """뉴스 기사 목록을 다크 테마 HTML로 변환한다."""
    print("[4/4] HTML 생성 중...")

    today = now_kst().strftime("%Y. %m. %d. %A")
    today_iso = now_kst().strftime("%Y-%m-%d")
    first_title = escape(articles[0].get("title_kr", "AI Daily Briefing")) if articles else "AI Daily Briefing"
    first_summary = escape(articles[0].get("summary_kr", "")[:120]) if articles else ""

    # 피드 건강 상태 배너
    feed_banner = ""
    if feed_status:
        ok = len(feed_status.get("ok", []))
        fail = len(feed_status.get("fail", []))
        total = ok + fail
        if fail > 0:
            failed_domains = escape(", ".join(feed_status["fail"]))
            color = "#e74c3c" if fail > total // 2 else "#e67e22"
            feed_banner = f'<div style="text-align:center;padding:8px;background:{color}20;border:1px solid {color}40;border-radius:8px;margin-bottom:16px;font-size:12px;color:{color};font-family:DM Mono,monospace;">피드 {ok}/{total} 활성 — 실패: {failed_domains}</div>'

    cards = ""
    for i, a in enumerate(articles):
        num = f"{i + 1:02d}"
        link = escape(a.get("link", "#"), quote=True)
        sources_kr = escape(a.get("sources_kr", ""))
        sources_en = escape(a.get("sources_en", ""))
        sources_jp = escape(a.get("sources_jp", ""))
        company = escape(a.get("company", ""), quote=True)
        tag_kr = escape(a.get("tag_kr", a.get("tag", "AI")))
        tag_en = escape(a.get("tag_en", "AI"))
        tag_jp = escape(a.get("tag_jp", "AI"))
        published = _format_published(a.get("published"))
        sources_html = f'<span class="sources"><span class="lang kr">{sources_kr}</span><span class="lang en" style="display:none">{sources_en}</span><span class="lang jp" style="display:none">{sources_jp}</span></span>' if sources_kr else ""
        company_html = f'<img class="company-icon" src="https://www.google.com/s2/favicons?domain={company}&amp;sz=64" alt="">' if company else ""
        published_html = f'<time class="card-time">{escape(published)}</time>' if published else ""
        featured = " featured" if i == 0 else ""
        brand_key = a.get("brand_key", "")
        brand_class = f" official brand-{brand_key}" if brand_key else ""
        brand_attr = f' data-brand="{brand_key}"' if brand_key else ""
        # TTS audio 태그 생성
        audio_tags = ""
        if tts_data:
            for lang in ["kr", "en", "jp"]:
                src = tts_data.get(lang, [""] * len(articles))[i] if i < len(tts_data.get(lang, [])) else ""
                if src:
                    if src.startswith("audio/"):
                        src = audio_prefix + src[len("audio/"):]
                    audio_tags += f'<audio class="tts-audio tts-{lang}" preload="none" src="{src}"></audio>'
        title_kr = escape(a.get("title_kr", ""))
        title_en = escape(a.get("title_en", ""))
        title_jp = escape(a.get("title_jp", ""))
        summary_kr = escape(a.get("summary_kr", ""))
        summary_en = escape(a.get("summary_en", ""))
        summary_jp = escape(a.get("summary_jp", ""))
        why_kr = escape(a.get("why_kr", ""))
        why_en = escape(a.get("why_en", ""))
        why_jp = escape(a.get("why_jp", ""))
        cards += f"""
        <article class="card{featured}{brand_class}"{brand_attr} style="animation-delay:{i * 0.12}s" tabindex="0" role="article" aria-label="{title_kr}">
          {audio_tags}
          <div class="card-head">
            <span class="num">{num}</span>
            <span class="badge"><span class="lang kr">{tag_kr}</span><span class="lang en" style="display:none">{tag_en}</span><span class="lang jp" style="display:none">{tag_jp}</span></span>
            {sources_html}
          </div>
          <h2 class="card-title">{company_html}<span class="lang kr">{title_kr}</span><span class="lang en" style="display:none">{title_en}</span><span class="lang jp" style="display:none">{title_jp}</span></h2>
          <p class="card-summary"><span class="lang kr">{summary_kr}</span><span class="lang en" style="display:none">{summary_en}</span><span class="lang jp" style="display:none">{summary_jp}</span></p>
          <div class="card-why">
            <span class="why-label">WHY IT MATTERS</span>
            <span class="why-text"><span class="lang kr">{why_kr}</span><span class="lang en" style="display:none">{why_en}</span><span class="lang jp" style="display:none">{why_jp}</span></span>
          </div>
          <div class="card-actions">
            <button class="card-play" onclick="playIdx({i})" aria-label="재생">▶</button>
            <a href="{link}" target="_blank" rel="noopener noreferrer" class="card-source"><span class="lang kr">원문 보기 ↗</span><span class="lang en" style="display:none">Read original ↗</span><span class="lang jp" style="display:none">原文を見る ↗</span></a>
            {published_html}
          </div>
        </article>"""

    archive_html = f'<a href="{archive_link}" class="archive-link">Past Briefings →</a>' if archive_link else ""

    # 전체 재생용 audio 태그
    tts_all_audio = ""
    if tts_data:
        for lang in ["kr", "en", "jp"]:
            src = tts_data.get(f"{lang}_all", "")
            if src:
                if src.startswith("audio/"):
                    src = audio_prefix + src[len("audio/"):]
                tts_all_audio += f'<audio id="tts-all-{lang}" preload="none" src="{src}"></audio>\n'

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>THE SIGNAL — AI Daily Briefing</title>
<meta name="description" content="AI 뉴스 데일리 브리핑 — {len(RSS_FEEDS)}개 글로벌 매체에서 수집한 오늘의 AI 뉴스">
<meta property="og:title" content="THE SIGNAL — {today_iso}">
<meta property="og:description" content="{first_title}. {first_summary}">
<meta property="og:type" content="article">
<meta property="og:locale" content="ko_KR">
<link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
<link rel="icon" type="image/png" sizes="512x512" href="favicon.png">
<link rel="apple-touch-icon" sizes="180x180" href="favicon-180.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500;700&family=Noto+Sans+KR:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0d0d12; color:#e0e0e0; font-family:'Noto Sans KR',sans-serif; font-weight:600; min-height:100vh; }}
  #aurora {{ position:fixed; inset:0; z-index:0; pointer-events:none; }}

  .masthead {{ text-align:center; padding:48px 20px 28px; border-bottom:1px solid #1a1a2e; position:relative; z-index:1; }}
  .masthead-rule {{ width:60px; height:2px; background:linear-gradient(90deg,#6366f1,#a78bfa); margin:0 auto 18px; }}
  .masthead h1 {{ font-size:42px; font-weight:700; letter-spacing:6px; text-transform:uppercase; color:#f0f0f0; margin-bottom:8px; }}
  .masthead .date {{ font-family:'DM Mono',monospace; font-size:13px; color:#777; letter-spacing:1px; font-weight:500; }}
  .masthead .edition {{ font-family:'DM Mono',monospace; font-size:11px; color:#666; font-weight:500; margin-top:4px; }}
  .masthead .subtitle {{ font-size:12px; color:#555; margin-top:10px; font-weight:400; }}
  .archive-link {{ font-family:'DM Mono',monospace; font-size:11px; color:#6366f1; text-decoration:none; margin-top:8px; display:inline-block; }}
  .archive-link:hover {{ color:#a78bfa; }}

  /* ── 언어 전환 (헤더) ── */
  .lang-switch {{ display:flex; justify-content:center; gap:4px; margin-top:14px; }}
  .ls-btn {{ font-family:'DM Mono',monospace; font-size:12px; padding:5px 14px; border:1px solid #333; border-radius:4px; background:transparent; color:#555; cursor:pointer; transition:all .15s; font-weight:600; }}
  .ls-btn:hover,.ls-btn:focus {{ color:#999; border-color:#555; outline:none; }}
  .ls-btn.active {{ color:#c4b5fd; border-color:#6366f180; background:#6366f115; }}

  .container {{ max-width:680px; margin:0 auto; padding:28px 20px 60px; display:flex; flex-direction:column; gap:16px; position:relative; z-index:1; }}

  /* ── 카드 ── */
  .card {{ background:#12121c; border:1px solid #1e1e30; border-radius:12px; padding:24px; animation:fadeUp .5s ease both; transition:border-color .2s; }}
  .card:hover {{ border-color:#6366f130; }}
  .card:focus {{ outline:2px solid #6366f1; outline-offset:2px; }}
  .card.now-playing {{ border-color:#6366f1; box-shadow:0 0 20px #6366f120; }}
  .card-head {{ display:flex; align-items:center; gap:10px; margin-bottom:14px; flex-wrap:wrap; }}

  /* ── Tier 0 공식 매체 브랜드 컬러 ── */
  /* 상단 액센트 바(4px) + 은은한 배경 방사 글로우, 카드 기본 스타일 위에 덮어씀 */
  .card.official {{ position:relative; overflow:hidden; }}
  .card.official::before {{
    content:""; position:absolute; top:0; left:0; right:0; height:4px;
    background:var(--brand-bar, #6366f1); z-index:2;
  }}
  .card.official::after {{
    content:""; position:absolute; inset:0; pointer-events:none;
    background:var(--brand-glow, radial-gradient(ellipse at top right, #6366f118, transparent 70%));
    z-index:0;
  }}
  .card.official > * {{ position:relative; z-index:1; }}
  /* OpenAI — 민트 그린 #10a37f */
  .card.brand-openai {{ --brand-bar:#10a37f; --brand-glow:radial-gradient(ellipse at top right,#10a37f22,transparent 70%); border-color:#10a37f40; }}
  .card.brand-openai:hover {{ border-color:#10a37f80; }}
  /* Anthropic — Claude 주황 #cc785c */
  .card.brand-anthropic {{ --brand-bar:#cc785c; --brand-glow:radial-gradient(ellipse at top right,#cc785c22,transparent 70%); border-color:#cc785c40; }}
  .card.brand-anthropic:hover {{ border-color:#cc785c80; }}
  /* Google/DeepMind — Gemini 4색 그라데이션 */
  .card.brand-google {{ --brand-bar:linear-gradient(90deg,#4285f4 0%,#ea4335 33%,#fbbc04 66%,#34a853 100%); --brand-glow:radial-gradient(ellipse at top right,#4285f420,transparent 70%); border-color:#4285f440; }}
  .card.brand-google:hover {{ border-color:#4285f480; }}
  .card.brand-google::before {{ background:linear-gradient(90deg,#4285f4 0%,#ea4335 33%,#fbbc04 66%,#34a853 100%); }}
  /* NVIDIA — 시그니처 그린 #76b900 */
  .card.brand-nvidia {{ --brand-bar:#76b900; --brand-glow:radial-gradient(ellipse at top right,#76b90022,transparent 70%); border-color:#76b90040; }}
  .card.brand-nvidia:hover {{ border-color:#76b90080; }}
  /* Microsoft — Azure 블루 #0078d4 */
  .card.brand-microsoft {{ --brand-bar:#0078d4; --brand-glow:radial-gradient(ellipse at top right,#0078d422,transparent 70%); border-color:#0078d440; }}
  .card.brand-microsoft:hover {{ border-color:#0078d480; }}

  /* ── 1번 뉴스 강조 ── */
  .card.featured {{ padding:28px; border-color:#6366f130; background:linear-gradient(160deg,#12121c 0%,#14142a 100%); }}
  .card.featured .card-title {{ font-size:24px; }}
  .card.featured .badge {{ background:#6366f120; border-color:#6366f160; }}
  .card.featured .num {{ color:#6366f1; font-size:15px; }}

  .num {{ font-family:'DM Mono',monospace; font-size:13px; color:#6366f180; font-weight:700; }}
  .badge {{ font-size:12px; color:#c4b5fd; padding:4px 12px; border:1px solid #a78bfa40; border-radius:20px; font-weight:600; background:#a78bfa15; }}
  .sources {{ font-family:'DM Mono',monospace; font-size:11px; color:#777; font-weight:700; margin-left:auto; }}

  .company-icon {{ width:24px; height:24px; vertical-align:middle; margin-right:8px; border-radius:4px; opacity:.9; }}
  .card-title {{ font-size:20px; font-weight:700; line-height:1.4; margin-bottom:12px; color:#f0f0f0; }}
  .card-summary {{ font-size:14px; line-height:1.8; color:#bbb; margin-bottom:16px; font-weight:500; }}

  .card-why {{ background:#16162a; border-left:2px solid #6366f1; padding:12px 16px; border-radius:0 8px 8px 0; margin-bottom:16px; }}
  .why-label {{ font-family:'DM Mono',monospace; font-size:10px; color:#6366f1; letter-spacing:1.5px; display:block; margin-bottom:4px; font-weight:700; }}
  .why-text {{ font-size:13px; color:#aaa; line-height:1.6; font-weight:600; }}

  /* ── 카드 하단 액션 행 ── */
  .card-actions {{ display:flex; align-items:center; gap:12px; }}
  .card-play {{ width:34px; height:34px; border-radius:50%; border:1px solid #6366f140; background:transparent; color:#6366f1; cursor:pointer; font-size:13px; transition:all .15s; display:flex; align-items:center; justify-content:center; flex-shrink:0; }}
  .card-play:hover,.card-play:focus {{ background:#6366f120; border-color:#6366f180; color:#a78bfa; outline:none; }}
  .card-source {{ font-family:'DM Mono',monospace; font-size:11px; color:#6366f1; text-decoration:none; padding:8px 16px; border:1px solid #6366f140; border-radius:6px; transition:all .15s; font-weight:600; }}
  .card-source:hover,.card-source:focus {{ background:#6366f115; border-color:#6366f180; color:#a78bfa; outline:none; }}
  .card-time {{ font-family:'DM Mono',monospace; font-size:10px; color:#555; font-weight:500; margin-left:auto; }}

  /* ── 하단 플레이어 바 (재생 시에만 표시) ── */
  .player {{ position:fixed; bottom:0; left:0; right:0; z-index:100; background:#0d0d12; border-top:1px solid #1e1e30; padding:12px 0 16px; transform:translateY(100%); transition:transform .3s ease; }}
  .player.active {{ transform:translateY(0); }}
  .player-inner {{ max-width:680px; margin:0 auto; padding:0 24px; display:flex; align-items:center; gap:16px; }}
  .player-info {{ flex:1; min-width:0; }}
  .player-title {{ font-size:14px; color:#e0e0e0; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .player-sub {{ font-size:12px; color:#555; font-weight:600; margin-top:2px; }}
  .player-controls {{ display:flex; align-items:center; gap:20px; }}
  .p-btn {{ border:none; background:transparent; color:#888; cursor:pointer; font-size:20px; padding:4px; transition:color .15s; font-weight:700; }}
  .p-btn:hover,.p-btn:focus {{ color:#e0e0e0; outline:none; }}
  .p-btn.main {{ color:#e0e0e0; font-size:26px; font-weight:700; }}
  .p-btn.main:hover,.p-btn.main:focus {{ color:#fff; }}
  .player-progress {{ position:absolute; top:0; left:0; right:0; height:2px; background:#1a1a2e; }}
  .player-progress-bar {{ height:100%; background:#6366f1; width:0%; transition:width .3s; }}

  @keyframes fadeUp {{ from {{ opacity:0; transform:translateY(20px); }} to {{ opacity:1; transform:translateY(0); }} }}

  /* ── 모바일 반응형 ── */
  @media (max-width: 600px) {{
    .masthead {{ padding:28px 16px 20px; }}
    .masthead h1 {{ font-size:26px; letter-spacing:4px; }}
    .masthead .date {{ font-size:11px; }}
    .masthead .subtitle {{ font-size:11px; }}
    .lang-switch {{ margin-top:10px; }}
    .ls-btn {{ font-size:11px; padding:4px 10px; }}
    .container {{ padding:14px 12px 50px; gap:12px; }}
    .card {{ padding:16px; }}
    .card.featured {{ padding:18px; }}
    .card.featured .card-title {{ font-size:18px; }}
    .card-title {{ font-size:15px; }}
    .card-summary {{ font-size:13px; line-height:1.7; }}
    .card-head {{ gap:6px; }}
    .badge {{ font-size:10px; padding:3px 8px; }}
    .sources {{ font-size:10px; }}
    .why-text {{ font-size:12px; }}
    .card-actions {{ gap:8px; }}
    .card-play {{ width:30px; height:30px; font-size:11px; }}
    .card-source {{ font-size:10px; padding:6px 12px; }}
    .player-inner {{ padding:0 12px; gap:10px; }}
    .player-title {{ font-size:12px; }}
    .player-controls {{ gap:16px; }}
    .p-btn {{ font-size:18px; }}
    .p-btn.main {{ font-size:22px; }}
    .player {{ padding:10px 0 14px; }}
  }}
</style>
</head>
<body>
  <header class="masthead" role="banner">
    <div class="masthead-rule"></div>
    <h1>The Signal</h1>
    <div class="date">{today}</div>
    <div class="edition">AI Daily Briefing — {len(articles)} stories from {len(RSS_FEEDS)} sources</div>
    <div class="subtitle">
      <span class="lang kr">{len(RSS_FEEDS)}개 글로벌 AI 매체의 중복 보도를 분석해 오늘의 핵심 뉴스만 선별합니다</span>
      <span class="lang en" style="display:none">Curated from {len(RSS_FEEDS)} global AI sources — ranked by cross-coverage frequency</span>
      <span class="lang jp" style="display:none">{len(RSS_FEEDS)}のグローバルAIメディアの重複報道を分析し、今日の重要ニュースを厳選</span>
    </div>
    <div class="lang-switch" role="group" aria-label="언어 선택">
      <button class="ls-btn active" onclick="setLang('kr')" aria-label="한국어">KR</button>
      <button class="ls-btn" onclick="setLang('en')" aria-label="English">EN</button>
      <button class="ls-btn" onclick="setLang('jp')" aria-label="日本語">JP</button>
    </div>
    {archive_html}
  </header>
  <main class="container" role="main">{feed_banner}{cards}</main>
{tts_all_audio}

  <nav class="player" id="player" role="region" aria-label="오디오 플레이어">
    <div class="player-progress"><div class="player-progress-bar" id="pbar"></div></div>
    <div class="player-inner">
      <div class="player-info">
        <div class="player-title" id="ptitle" aria-live="polite">재생 대기 중</div>
        <div class="player-sub" id="psub"></div>
      </div>
      <div class="player-controls">
        <button class="p-btn" onclick="prevTrack()" aria-label="이전 뉴스">⏮</button>
        <button class="p-btn main" id="pbtn" onclick="togglePlay()" aria-label="재생">▶</button>
        <button class="p-btn" onclick="nextTrack()" aria-label="다음 뉴스">⏭</button>
      </div>
    </div>
  </nav>

<canvas id="aurora" aria-hidden="true"></canvas>
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
const player=document.getElementById('player');
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

function showPlayer(){{ player.classList.add('active'); }}
function hidePlayer(){{ player.classList.remove('active'); }}

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
  showPlayer();
  highlight(i);
  isPlaying=true;
  pbtn.textContent='⏸';
  pbtn.setAttribute('aria-label','일시정지');
  audio.onended=()=>nextTrack();
  audio.play();
}}

function togglePlay(){{
  if(isPlaying && curAudio){{
    curAudio.pause();
    isPlaying=false;
    pbtn.textContent='▶';
    pbtn.setAttribute('aria-label','재생');
  }} else if(!isPlaying && curAudio && curAudio.currentTime>0){{
    curAudio.play();
    isPlaying=true;
    pbtn.textContent='⏸';
    pbtn.setAttribute('aria-label','일시정지');
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
  pbtn.setAttribute('aria-label','재생');
  cards.forEach(c=>c.classList.remove('now-playing'));
  ptitle.textContent='재생 완료';
  psub.textContent='';
  pbar.style.width='100%';
  curIdx=0;
  setTimeout(hidePlayer,2000);
}}

function setLang(lang){{
  const wasPlaying=isPlaying;
  if(curAudio){{ curAudio.pause(); curAudio.currentTime=0; }}
  curLang=lang;
  document.querySelectorAll('.lang').forEach(el=>{{
    el.style.display=el.classList.contains(lang)?'inline':'none';
  }});
  document.querySelectorAll('.ls-btn').forEach(btn=>{{
    btn.classList.toggle('active',btn.textContent.toLowerCase()===lang);
  }});
  if(isPlaying) playIdx(curIdx);
}}

// 키보드 네비게이션: Space → 재생/일시정지, 좌우 화살표 → 이전/다음
document.addEventListener('keydown',e=>{{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA') return;
  if(e.code==='Space'){{ e.preventDefault(); togglePlay(); }}
  else if(e.code==='ArrowRight'){{ nextTrack(); }}
  else if(e.code==='ArrowLeft'){{ prevTrack(); }}
}});
</script>
</body>
</html>"""

    print("   ✓ HTML 생성 완료")
    return html


def build_error_html(error_msg: str) -> str:
    """빌드 실패 시 에러 페이지."""
    t = now_kst().strftime("%Y. %m. %d. %A %H:%M KST")
    safe_msg = escape(error_msg)
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>THE SIGNAL — Build Failed</title>
<style>body{{background:#0d0d12;color:#e0e0e0;font-family:sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.err{{text-align:center;max-width:500px;padding:40px}}.err h1{{color:#e74c3c;font-size:24px;margin-bottom:16px}}
.err p{{color:#999;line-height:1.6;margin-bottom:8px}}.err .time{{color:#6366f1;font-family:monospace;font-size:12px}}</style>
</head><body><div class="err"><h1>빌드 실패</h1><p>오늘의 브리핑을 생성하지 못했습니다.</p>
<p style="color:#777;font-size:13px;">{safe_msg}</p><p class="time">{t}</p>
<p><a href="archive/" style="color:#6366f1;">지난 브리핑 보기 →</a></p></div></body></html>"""


# ── 5. 매체 통계 수집 ─────────────────────────────────────

def collect_source_metrics(entries: list[dict], clustered: list[dict]) -> dict:
    """매체별 교차 보도율을 계산한다. 매일 기록하여 가중치 산출에 사용."""
    from collections import defaultdict
    source_total = defaultdict(int)
    source_cross = defaultdict(int)

    for e in entries:
        source_total[e["source"]] += 1

    for topic in clustered:
        if topic["source_count"] >= 2:
            for src in topic["sources"]:
                source_cross[src] += 1

    metrics = {}
    for src in source_total:
        total = source_total[src]
        cross = source_cross[src]
        metrics[src] = {"total": total, "cross": cross, "rate": round(cross / total, 3) if total else 0}
    return metrics


def save_source_metrics(metrics: dict, metrics_dir: str = "metrics"):
    """매체 통계를 날짜별 JSON으로 저장한다."""
    import os
    os.makedirs(metrics_dir, exist_ok=True)
    date_str = now_kst().strftime("%Y-%m-%d")
    path = os.path.join(metrics_dir, f"{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": date_str, "sources": metrics}, f, ensure_ascii=False, indent=2)
    print(f"   ✓ 매체 통계 저장: {path}")


def update_source_weight(metrics_dir: str = "metrics", min_days: int = 7):
    """축적된 매체 통계로 SOURCE_WEIGHT를 자동 갱신한다. min_days 이상 데이터가 있을 때만."""
    import os
    from collections import defaultdict

    global SOURCE_WEIGHT

    if not os.path.exists(metrics_dir):
        return False

    files = [f for f in os.listdir(metrics_dir) if f.endswith(".json")]
    if len(files) < min_days:
        print(f"   ⏳ 매체 통계 {len(files)}일치 (최소 {min_days}일 필요) — 하드코딩 가중치 유지")
        return False

    totals = defaultdict(int)
    crosses = defaultdict(int)

    for f in files:
        path = os.path.join(metrics_dir, f)
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        for src, vals in data.get("sources", {}).items():
            totals[src] += vals.get("total", 0)
            crosses[src] += vals.get("cross", 0)

    # 교차 보도율 기준 자동 갱신. Tier 1 이상(수동 지정 권위 매체)은 건드리지 않는다.
    # Tier 3 → Tier 2 승급만 허용. 자동 로직이 Tier 1로 올려버리는 일 없음.
    new_weights = {}
    for src in totals:
        current = SOURCE_WEIGHT.get(src, _DEFAULT_WEIGHT)
        if current >= 3:
            continue  # Tier 0·Tier 1은 수동 유지, 자동 갱신 제외
        total = totals[src]
        cross = crosses[src]
        rate = cross / total if total > 0 else 0
        if rate >= 0.10:
            new_weights[src] = 2  # Tier 2: 교차 보도율 10% 이상
        else:
            new_weights[src] = 1  # Tier 3: 10% 미만

    SOURCE_WEIGHT.update(new_weights)
    print(f"   ✓ 매체 신뢰도 자동 갱신 ({len(files)}일 데이터 기반)")
    for src, w in sorted(new_weights.items(), key=lambda x: x[1], reverse=True):
        rate = crosses[src] / totals[src] * 100 if totals[src] else 0
        print(f"     Tier {4-w}: {rate:.0f}% — {src}")
    return True


# ── 6. Discord 알림 ──────────────────────────────────────

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
            "footer": {"text": f"{len(articles)}개 뉴스 · {len(RSS_FEEDS)}개 매체에서 수집"}
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


# ── 7. TTS (Gemini 3.1 → Edge 폴백) ─────────────────────


def _detect_audio_fmt(data: bytes) -> str:
    """오디오 바이트의 포맷을 감지한다 (wav/mp3)."""
    if data[:4] == b"RIFF":
        return "wav"
    return "mp3"


def _pcm_to_mp3(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, bitrate: int = 64) -> bytes:
    """raw PCM (signed 16-bit LE) 바이트를 MP3로 인코딩 (lameenc 사용). 브라우저 호환성·용량 최적화."""
    import lameenc
    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate)
    enc.set_in_sample_rate(sample_rate)
    enc.set_channels(channels)
    enc.set_quality(5)
    # lameenc는 bytearray를 반환. tts_to_files의 isinstance(val, bytes) 분기에 걸리도록 bytes로 변환.
    return bytes(enc.encode(pcm_bytes) + enc.flush())


def _gemini_tts_single(text: str, api_key: str) -> bytes | None:
    """Gemini TTS로 wav 바이트를 생성한다. 실패하면 None."""
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=GEMINI_TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=GEMINI_TTS_VOICE
                        )
                    )
                ),
            ),
        )
        pcm = resp.candidates[0].content.parts[0].inline_data.data
        # raw PCM (24kHz 16-bit mono)를 바로 MP3로 인코딩 (WAV 래핑 생략).
        # MP3는 모든 모던 브라우저/모바일에서 확실히 재생됨.
        return _pcm_to_mp3(pcm, sample_rate=24000, channels=1, bitrate=64)
    except Exception as e:
        print(f"   ⚠ Gemini TTS 실패: {e}")
        return None


async def _edge_tts_single(text: str, voice: str) -> bytes:
    """Edge TTS로 mp3 바이트를 생성한다 (폴백용)."""
    comm = edge_tts.Communicate(text, voice, rate="+5%")
    buf = BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


def generate_tts(articles: list[dict]) -> dict:
    """Gemini TTS(1차) → Edge TTS(폴백)로 각 기사 3개 언어 음성 생성."""
    import os
    tts_api_key = os.environ.get("GEMINI_TTS_API_KEY", "")

    engine = "Gemini 3.1 Flash TTS (Charon)" if tts_api_key else "Edge TTS"
    print(f"[TTS] {engine} 음성 생성 중...")

    result = {"kr": [], "en": [], "jp": []}

    ordinal = {
        "kr": [f"{n}번째 뉴스입니다." for n in range(1, len(articles) + 1)],
        "en": [f"News number {n}." for n in range(1, len(articles) + 1)],
        "jp": [f"{n}番目のニュースです。" for n in range(1, len(articles) + 1)],
    }

    gemini_calls = 0
    gemini_fails = 0

    for i, a in enumerate(articles):
        for lang, voice in TTS_VOICES.items():
            intro = ordinal[lang][i]
            title = a.get(f"title_{lang}", "")
            summary = a.get(f"summary_{lang}", "")
            why = a.get(f"why_{lang}", "")
            text = f"{intro} {title}. {summary} {why}"

            audio = None

            # 1차: Gemini TTS
            if tts_api_key and gemini_fails < GEMINI_TTS_MAX_FAILS:
                if gemini_calls > 0:
                    time.sleep(GEMINI_TTS_DELAY)
                audio = _gemini_tts_single(text, tts_api_key)
                gemini_calls += 1
                if audio:
                    gemini_fails = 0
                else:
                    gemini_fails += 1

            # 2차: Edge TTS 폴백
            if audio is None:
                try:
                    audio = asyncio.run(_edge_tts_single(text, voice))
                except Exception as e:
                    print(f"   ⚠ Edge TTS도 실패 [{lang}][{i+1}]: {e}")
                    audio = b""

            result[lang].append(audio)

    # 전체 읽기용 결합 TTS
    for lang, voice in TTS_VOICES.items():
        full_text = ""
        for j, a in enumerate(articles):
            if lang == "en":
                full_text += f"News number {j+1}. {a.get(f'title_{lang}', '')}. {a.get(f'summary_{lang}', '')} "
            elif lang == "jp":
                full_text += f"{j+1}番目のニュース。 {a.get(f'title_{lang}', '')}。 {a.get(f'summary_{lang}', '')} "
            else:
                full_text += f"{j+1}번 뉴스. {a.get(f'title_{lang}', '')}. {a.get(f'summary_{lang}', '')} "

        audio = None
        if tts_api_key and gemini_fails < GEMINI_TTS_MAX_FAILS:
            if gemini_calls > 0:
                time.sleep(GEMINI_TTS_DELAY)
            audio = _gemini_tts_single(full_text, tts_api_key)
            gemini_calls += 1
            if audio:
                gemini_fails = 0
            else:
                gemini_fails += 1

        if audio is None:
            try:
                audio = asyncio.run(_edge_tts_single(full_text, voice))
            except Exception as e:
                print(f"   ⚠ 전체 TTS 실패 [{lang}]: {e}")
                audio = b""

        result[f"{lang}_all"] = audio

    total = sum(len(v) for v in result.values() if isinstance(v, list))
    print(f"   ✓ {total}개 음성 생성 완료 (Gemini {gemini_calls}건 시도)")
    return result


def tts_to_data_uris(tts_raw: dict) -> dict:
    """generate_tts()의 raw bytes를 data URI로 변환한다 (로컬 실행용)."""
    result = {}
    for key, val in tts_raw.items():
        if isinstance(val, list):
            result[key] = [
                f"data:audio/{_detect_audio_fmt(b)};base64,{base64.b64encode(b).decode('ascii')}" if b else ""
                for b in val
            ]
        elif isinstance(val, bytes):
            result[key] = (
                f"data:audio/{_detect_audio_fmt(val)};base64,{base64.b64encode(val).decode('ascii')}"
                if val else ""
            )
        else:
            result[key] = val
    return result


def tts_to_files(tts_raw: dict, out_dir: str) -> dict:
    """generate_tts()의 raw bytes를 오디오 파일로 저장하고 경로를 반환한다 (배포용)."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    result = {}
    for key, val in tts_raw.items():
        if isinstance(val, list):
            paths = []
            for i, b in enumerate(val):
                if b:
                    ext = _detect_audio_fmt(b)
                    filename = f"{key}_{i}.{ext}"
                    filepath = os.path.join(out_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(b)
                    paths.append(f"audio/{filename}")
                else:
                    paths.append("")
            result[key] = paths
        elif isinstance(val, bytes):
            if val:
                ext = _detect_audio_fmt(val)
                filename = f"{key}.{ext}"
                filepath = os.path.join(out_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(val)
                result[key] = f"audio/{filename}"
            else:
                result[key] = ""
        else:
            result[key] = val
    return result
