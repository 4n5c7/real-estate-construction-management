"""アプリケーション設定モジュール

基本設計書 22.4 の settings.json を読み込み、各モジュールから参照できるようにする。
設定ファイルが存在しない場合は既定値で自動生成する。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

# プロジェクトルート（このファイルから2階層上）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT_DEFAULT = PROJECT_ROOT / "data"
CONFIG_DIR = DATA_ROOT_DEFAULT / "config"
CONFIG_FILE = CONFIG_DIR / "settings.json"

# 既定設定 (基本設計書 22.4 準拠)
DEFAULT_SETTINGS: Dict[str, Any] = {
    "data_root": str(DATA_ROOT_DEFAULT),
    "backup_retention": 30,
    "id_reset_mode": "calendar_year",  # calendar_year / fiscal_year
    "id_prefix": "KOJI",
    "default_encoding": "utf-8-sig",
    "auto_backup_on_exit": True,
    "max_attachment_mb": 50,
}


def load_settings() -> Dict[str, Any]:
    """設定ファイルを読み込む。存在しなければ作成して既定値を返す。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 既定値とマージ (新規キー追加に対応)
        merged = DEFAULT_SETTINGS.copy()
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: Dict[str, Any]) -> None:
    """設定ファイルを保存する。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def get_data_root() -> Path:
    """データ保存先のルートパスを取得する。"""
    settings = load_settings()
    return Path(settings["data_root"])


def get_paths() -> Dict[str, Path]:
    """各種データフォルダのパスを返す。

    基本設計書 11.1 のフォルダ構成に準拠する。
    """
    root = get_data_root()
    paths = {
        "root": root,
        "database": root / "database",
        "db_file": root / "database" / "construction.sqlite",
        "attachments": root / "attachments",
        "exports": root / "exports",
        "backups": root / "backups",
        "logs": root / "logs",
        "config": root / "config",
    }
    # 必要なディレクトリを作成
    for key, p in paths.items():
        if key == "db_file":
            continue
        p.mkdir(parents=True, exist_ok=True)
    return paths
