"""
THE SIGNAL — GitHub Actions 빌드 스크립트
RSS 수집 → 클러스터링 → Gemini 요약 → HTML 배포 + 아카이브 + Discord 알림.
"""

import os
import shutil
from html import escape
from signal_core import (
    fetch_rss, cluster_articles, filter_ai_relevant, curate_with_gemini,
    build_html, build_error_html, notify_discord, generate_tts, tts_to_files, now_kst,
    collect_source_metrics, save_source_metrics, update_source_weight,
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
PAGES_URL = os.environ.get("PAGES_URL", "https://davidking981218-rgb.github.io/the-signal/")


def build_archive_index(archive_dir: str) -> str:
    files = []
    if os.path.exists(archive_dir):
        for f in sorted(os.listdir(archive_dir), reverse=True):
            if f.endswith(".html") and f != "index.html":
                date_str = f.replace(".html", "")
                files.append((date_str, f))

    items = ""
    for date_str, filename in files:
        safe_filename = escape(filename, quote=True)
        safe_date = escape(date_str)
        items += f'<a href="{safe_filename}" style="display:block;padding:12px 0;border-bottom:1px solid #1e1e30;color:#c4b5fd;text-decoration:none;font-size:15px;">{safe_date}</a>\n'

    if not items:
        items = '<p style="color:#666;">아직 아카이브가 없습니다.</p>'

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>THE SIGNAL — Archive</title>
<style>body{{background:#0d0d12;color:#e0e0e0;font-family:'Noto Sans KR',sans-serif}}
.wrap{{max-width:500px;margin:0 auto;padding:48px 20px}}h1{{font-size:28px;font-weight:700;margin-bottom:8px}}
.sub{{color:#666;font-size:12px;margin-bottom:32px}}a:hover{{color:#a78bfa!important}}</style>
</head><body><div class="wrap"><h1>Archive</h1>
<div class="sub"><a href="../" style="color:#6366f1;text-decoration:none;">← Today's Briefing</a></div>
{items}</div></body></html>"""


def main():
    today_str = now_kst().strftime("%Y-%m-%d")

    os.makedirs("public/archive", exist_ok=True)
    for fav in ["favicon.png", "favicon-32.png", "favicon-180.png"]:
        if os.path.exists(fav):
            shutil.copy2(fav, f"public/{fav}")
    if os.path.exists("archive"):
        for f in os.listdir("archive"):
            if f.endswith(".html"):
                shutil.copy2(f"archive/{f}", f"public/archive/{f}")

    if not GEMINI_API_KEY:
        print("✗ GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        html = build_error_html("GEMINI_API_KEY가 설정되지 않았습니다.")
        with open("public/index.html", "w", encoding="utf-8") as f:
            f.write(html)
        return

    try:
        # 매체 신뢰도 자동 갱신 (7일 이상 데이터 있으면)
        update_source_weight()

        raw, feed_status = fetch_rss()
        if not raw:
            raise RuntimeError("RSS 피드에서 수집된 뉴스가 없습니다.")

        clustered_raw = cluster_articles(raw, GEMINI_API_KEY)
        # 매체 통계 저장 (필터 전 데이터로 교차 보도율 측정)
        metrics = collect_source_metrics(raw, clustered_raw)
        save_source_metrics(metrics)
        clustered = filter_ai_relevant(clustered_raw)
        articles = curate_with_gemini(clustered, GEMINI_API_KEY)

        if not articles:
            raise RuntimeError("Gemini 큐레이션 결과가 비어있습니다.")

        tts_raw = generate_tts(articles)
        tts_data = tts_to_files(tts_raw, "public/audio")
        html = build_html(articles, archive_link="archive/", feed_status=feed_status, tts_data=tts_data)

    except Exception as e:
        print(f"✗ 빌드 실패: {e}")
        html = build_error_html(str(e))
        with open("public/index.html", "w", encoding="utf-8") as f:
            f.write(html)
        with open("public/archive/index.html", "w", encoding="utf-8") as f:
            f.write(build_archive_index("public/archive"))
        raise

    # 아카이브용 HTML (archive_link를 상대경로 보정)
    archive_html = build_html(articles, archive_link="./", feed_status=feed_status, tts_data=tts_data)

    # 성공
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    with open(f"public/archive/{today_str}.html", "w", encoding="utf-8") as f:
        f.write(archive_html)
    os.makedirs("archive", exist_ok=True)
    with open(f"archive/{today_str}.html", "w", encoding="utf-8") as f:
        f.write(archive_html)
    with open("public/archive/index.html", "w", encoding="utf-8") as f:
        f.write(build_archive_index("public/archive"))

    print(f"✓ 배포 준비 완료 (archive/{today_str}.html 보존)")

    # Discord 알림
    notify_discord(DISCORD_WEBHOOK, articles, PAGES_URL)


if __name__ == "__main__":
    main()
