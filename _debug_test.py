"""마이그레이션 디버깅 — signal_core 각 단계 확인 (HTML/TTS/Edge 팝업 제외)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from signal_core import fetch_rss, cluster_articles, filter_ai_relevant, curate_with_gemini

api_key = os.environ.get("GEMINI_API_KEY", "")
if not api_key:
    print("ERR: GEMINI_API_KEY 미설정")
    sys.exit(1)
print(f"key 끝자리: ...{api_key[-8:]}")

t0 = time.time()

print("\n=== [1/3] fetch_rss ===")
raw, feed_status = fetch_rss()
print(f"총 수집: {len(raw)}개")
print(f"피드 성공: {len(feed_status['ok'])}, 실패: {len(feed_status['fail'])}")
if feed_status["fail"]:
    print(f"실패 목록: {feed_status['fail']}")
print(f"경과: {time.time()-t0:.1f}s")

print("\n=== [2/3] cluster_articles (임베딩) ===")
t1 = time.time()
clustered = cluster_articles(raw, api_key)
print(f"클러스터 수: {len(clustered)}")
print(f"임베딩 단계 경과: {time.time()-t1:.1f}s")
print("상위 15개 토픽:")
for i, t in enumerate(clustered[:15]):
    title = t["title"][:70]
    print(f"  [{i+1:2d}] 매체={t['source_count']:2d}, {title}")
    if t["source_count"] > 1:
        print(f"       sources: {t['sources']}")

print("\n=== [2.5] filter_ai_relevant ===")
filtered = filter_ai_relevant(clustered)
print(f"필터 통과: {len(filtered)}/{len(clustered)}")

print("\n=== [3/3] curate_with_gemini (새 SDK) ===")
t2 = time.time()
articles = curate_with_gemini(filtered, api_key)
print(f"Gemini 요약 경과: {time.time()-t2:.1f}s")
print(f"요약 결과: {len(articles)}개")
for a in articles:
    print(f"  - KR: {a.get('title_kr','')}")
    print(f"    EN: {a.get('title_en','')}")
    print(f"    JP: {a.get('title_jp','')}")
    print(f"    매체: {a.get('sources_kr','')} / 링크: {a.get('link','')[:60]}")

print(f"\n총 경과: {time.time()-t0:.1f}s")
print("OK — 마이그레이션 성공")
