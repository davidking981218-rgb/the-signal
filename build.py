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

    # 1. archive/ 전체(HTML + audio 하위 폴더)를 public/archive/로 복사
    os.makedirs("public", exist_ok=True)
    if os.path.exists("archive"):
        if os.path.exists("public/archive"):
            shutil.rmtree("public/archive")
        shutil.copytree("archive", "public/archive")
    os.makedirs("public/archive", exist_ok=True)

    # 2. favicon 복사 (archive copytree 이후에 해야 덮어쓰기 안 됨)
    # archive HTML은 상대경로로 favicon을 참조하므로 public/archive/ 에도 복사 필요
    for fav in ["favicon.png", "favicon-32.png", "favicon-180.png"]:
        if os.path.exists(fav):
            shutil.copy2(fav, f"public/{fav}")
            shutil.copy2(fav, f"public/archive/{fav}")

    # 오늘 archive가 이미 완성된 상태면(수동 재트리거 시) 전체 빌드 건너뛰고 재배포만.
    # Gemini/TTS 호출 0건. 기존 오늘 결과(Charon 음성 포함)가 그대로 보존됨.
    today_archive_html_path = f"archive/{today_str}.html"
    today_archive_audio_dir = f"archive/audio/{today_str}"
    if (os.path.exists(today_archive_html_path)
            and os.path.isdir(today_archive_audio_dir)
            and len(os.listdir(today_archive_audio_dir)) >= 18):
        print(f"✓ 오늘 archive 이미 완성 — 빌드 건너뛰고 재배포만 수행")
        # archive HTML을 메인 index.html로 변환 (audio 경로 + archive_link 치환)
        with open(today_archive_html_path, encoding="utf-8") as f:
            html = f.read()
        html = html.replace(f'src="audio/{today_str}/', 'src="audio/')
        html = html.replace('href="./" class="archive-link"', 'href="archive/" class="archive-link"')
        with open("public/index.html", "w", encoding="utf-8") as f:
            f.write(html)
        # 메인 페이지용 오디오 복사 (public/audio/)
        os.makedirs("public/audio", exist_ok=True)
        for f in os.listdir(today_archive_audio_dir):
            shutil.copy2(os.path.join(today_archive_audio_dir, f), os.path.join("public/audio", f))
        # archive 인덱스
        with open("public/archive/index.html", "w", encoding="utf-8") as f:
            f.write(build_archive_index("public/archive"))
        print("✓ 배포 준비 완료 (Gemini/TTS 호출 0건)")
        return

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

        # AI 관련성 필터를 임베딩 전으로 이동 — 비AI 기사는 벡터화하지 않아 API 호출 절감
        filtered_raw = filter_ai_relevant(raw)
        clustered = cluster_articles(filtered_raw, GEMINI_API_KEY)
        # 매체 통계 저장 (필터 통과 기사 기준)
        metrics = collect_source_metrics(filtered_raw, clustered)
        save_source_metrics(metrics)
        articles = curate_with_gemini(clustered, GEMINI_API_KEY)

        if not articles:
            raise RuntimeError("Gemini 큐레이션 결과가 비어있습니다.")

        # 오늘 archive/audio/가 이미 있으면(동일한 날 재빌드) TTS 재호출 없이 기존 파일 재사용.
        # quota 절약 + 기존 Charon 음성을 Edge 폴백으로 덮어쓰지 않음.
        archive_audio_git = f"archive/audio/{today_str}"
        archive_audio_public = f"public/archive/audio/{today_str}"
        if os.path.isdir(archive_audio_git) and len(os.listdir(archive_audio_git)) >= 18:
            print(f"[TTS] archive/audio/{today_str}/ 이미 존재 — TTS 재호출 건너뜀, 기존 오디오 재사용")
            os.makedirs("public/audio", exist_ok=True)
            os.makedirs(archive_audio_public, exist_ok=True)
            tts_data = {"kr": [""] * len(articles), "en": [""] * len(articles), "jp": [""] * len(articles)}
            for f in os.listdir(archive_audio_git):
                src_path = os.path.join(archive_audio_git, f)
                shutil.copy2(src_path, os.path.join("public/audio", f))
                shutil.copy2(src_path, os.path.join(archive_audio_public, f))
                # tts_data 재구성
                name = os.path.splitext(f)[0]
                if name.endswith("_all"):
                    tts_data[name] = f"audio/{f}"
                else:
                    lang, idx = name.rsplit("_", 1)
                    if lang in tts_data and idx.isdigit():
                        tts_data[lang][int(idx)] = f"audio/{f}"
        else:
            tts_raw = generate_tts(articles)
            tts_data = tts_to_files(tts_raw, "public/audio")
            tts_to_files(tts_raw, archive_audio_public)
            os.makedirs(archive_audio_git, exist_ok=True)
            for f in os.listdir(archive_audio_public):
                shutil.copy2(os.path.join(archive_audio_public, f), os.path.join(archive_audio_git, f))
        html = build_html(articles, archive_link="archive/", feed_status=feed_status, tts_data=tts_data)

    except Exception as e:
        print(f"✗ 빌드 실패: {e}")
        html = build_error_html(str(e))
        with open("public/index.html", "w", encoding="utf-8") as f:
            f.write(html)
        with open("public/archive/index.html", "w", encoding="utf-8") as f:
            f.write(build_archive_index("public/archive"))
        raise

    # 아카이브용 HTML — 해당 날짜의 전용 audio 폴더를 가리키도록 audio_prefix 지정
    # 결과: archive/2026-MM-DD.html 이 audio/2026-MM-DD/kr_0.mp3 를 참조 (과거 archive에서도 그날 오디오 영구 보존)
    archive_html = build_html(articles, archive_link="./", feed_status=feed_status, tts_data=tts_data, audio_prefix=f"audio/{today_str}/")

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
