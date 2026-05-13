"""Flask アプリケーションファクトリ"""
from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from flask import Flask

from .config import get_paths
from .database import init_database
from .views import bp


def _setup_logging(log_dir: Path) -> None:
    """エラーログを日次ローテーションで記録 (基本設計書 15章)。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "error.log"
    handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=90, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    ))
    handler.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = "koji-kanri-local-only"  # 社内ローカル用
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB

    paths = get_paths()
    _setup_logging(paths["logs"])
    init_database()

    app.register_blueprint(bp)

    # テンプレートで使う日付フォーマット
    @app.template_filter("yen")
    def _yen(v):
        if v is None or v == "":
            return ""
        try:
            return f"{int(v):,}"
        except (ValueError, TypeError):
            return str(v)

    @app.template_filter("ja_date")
    def _ja_date(v):
        if not v:
            return ""
        try:
            if isinstance(v, str):
                return v
            return v.strftime("%Y/%m/%d")
        except Exception:
            return str(v)

    @app.errorhandler(Exception)
    def _handle_exception(e):
        app.logger.exception("Unhandled error: %s", e)
        return (f"<h2>エラーが発生しました</h2><pre>{e}</pre>"
                f"<p>ログを確認してください: {paths['logs']}</pre>"), 500

    return app
