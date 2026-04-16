"""
THE SIGNAL — 로컬 실행 스크립트
Edge 앱 모드로 브리핑을 팝업으로 띄운다.
"""

import os
import subprocess
import tempfile

from signal_core import fetch_rss, cluster_articles, curate_with_gemini, build_html

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def main():
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
    articles = curate_with_gemini(clustered, GEMINI_API_KEY)
    html = build_html(articles)

    # 임시 파일에 저장 후 Edge 앱 모드로 열기
    print("[5/5] 앱 창 열기...")
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="signal_brief_",
        delete=False, encoding="utf-8",
    )
    tmp.write(html)
    tmp.close()
    file_url = "file:///" + tmp.name.replace("\\", "/")

    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    if os.path.exists(edge):
        subprocess.Popen([edge, f"--app={file_url}", "--window-size=760,920"])
    elif os.path.exists(chrome):
        subprocess.Popen([chrome, f"--app={file_url}", "--window-size=760,920"])
    else:
        import webbrowser
        webbrowser.open(file_url)

    print("   ✓ 완료!")


if __name__ == "__main__":
    main()
