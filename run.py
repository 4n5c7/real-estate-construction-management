"""アプリ起動スクリプト

使い方:
    python run.py            # 既定ポート 8000 で起動
    python run.py --port 9000

ブラウザで http://127.0.0.1:8000/ を開く。
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

# 親ディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="不動産工事管理アプリ")
    parser.add_argument("--host", default="127.0.0.1",
                        help="バインドホスト (既定: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="ポート番号 (既定: 8000)")
    parser.add_argument("--no-browser", action="store_true",
                        help="ブラウザを自動起動しない")
    parser.add_argument("--debug", action="store_true",
                        help="デバッグモード")
    args = parser.parse_args()

    app = create_app()

    url = f"http://{args.host}:{args.port}/"
    print("=" * 60)
    print(" 不動産工事管理アプリ")
    print(f"   URL: {url}")
    print(f"   終了するには Ctrl+C")
    print("=" * 60)

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
