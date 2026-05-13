"""データベース接続・スキーマ初期化モジュール

基本設計書 9 章のテーブル定義に準拠する。
SQLite を採用し、同一プロセス内で 1 コネクションを使い回す方針。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_paths


# 状態コード (基本設計書 10章 + レビュー指摘で正式定義)
STATUS_CODES = [
    ("received", "受付"),
    ("quoting", "見積依頼"),
    ("quoted", "見積取得"),
    ("pending_approval", "承認待ち"),
    ("ordered", "発注済"),
    ("in_progress", "着工中"),
    ("completed", "完了"),
    ("invoiced", "請求済"),
    ("on_hold", "保留"),
    ("cancelled", "中止"),
]

STATUS_LABELS = dict(STATUS_CODES)

# 優先度コード (基本設計書 22.1)
PRIORITY_LABELS = {1: "通常", 2: "急ぎ", 3: "緊急"}

# 添付種別コード (基本設計書 22.2)
ATTACHMENT_TYPES = {
    "estimate": "見積書",
    "invoice": "請求書",
    "photo": "現場写真",
    "approval": "承認資料",
    "other": "その他",
}


SCHEMA_SQL = """
-- 物件マスタ
CREATE TABLE IF NOT EXISTS properties (
    property_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    address     TEXT,
    owner_name  TEXT,
    note        TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1
);

-- 業者マスタ
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    contact     TEXT,
    phone       TEXT,
    email       TEXT,
    note        TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1
);

-- 担当者マスタ
CREATE TABLE IF NOT EXISTS staffs (
    staff_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    role        TEXT,
    email       TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1
);

-- 工事区分マスタ
CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1
);

-- 工事案件
CREATE TABLE IF NOT EXISTS constructions (
    construction_id     TEXT PRIMARY KEY,
    property_id         INTEGER NOT NULL,
    room_no             TEXT,
    category_id         INTEGER,
    priority            INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'received',
    staff_id            INTEGER,
    vendor_id           INTEGER,
    description         TEXT,
    received_at         DATE NOT NULL,
    scheduled_start_at  DATE,
    scheduled_end_at    DATE,
    completed_at        DATE,
    estimate_amount     INTEGER,
    estimate_received_at DATE,
    estimate_valid_until DATE,
    invoice_amount      INTEGER,
    invoice_received_at DATE,
    payment_due_at      DATE,
    paid                INTEGER NOT NULL DEFAULT 0,
    note                TEXT,
    deleted_at          DATETIME,
    created_at          DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at          DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (property_id) REFERENCES properties(property_id),
    FOREIGN KEY (category_id) REFERENCES categories(category_id),
    FOREIGN KEY (staff_id)    REFERENCES staffs(staff_id),
    FOREIGN KEY (vendor_id)   REFERENCES vendors(vendor_id)
);

CREATE INDEX IF NOT EXISTS idx_constructions_status      ON constructions(status);
CREATE INDEX IF NOT EXISTS idx_constructions_property    ON constructions(property_id);
CREATE INDEX IF NOT EXISTS idx_constructions_vendor      ON constructions(vendor_id);
CREATE INDEX IF NOT EXISTS idx_constructions_received_at ON constructions(received_at);
CREATE INDEX IF NOT EXISTS idx_constructions_end_at      ON constructions(scheduled_end_at);

-- 添付ファイル
CREATE TABLE IF NOT EXISTS attachments (
    attachment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    construction_id TEXT NOT NULL,
    type            TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    file_size       INTEGER,
    checksum        TEXT,
    uploaded_at     DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (construction_id) REFERENCES constructions(construction_id)
);

CREATE INDEX IF NOT EXISTS idx_attachments_construction ON attachments(construction_id);

-- 状態履歴
CREATE TABLE IF NOT EXISTS status_history (
    history_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    construction_id TEXT NOT NULL,
    old_status      TEXT,
    new_status      TEXT NOT NULL,
    changed_by      INTEGER,
    changed_at      DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    comment         TEXT,
    FOREIGN KEY (construction_id) REFERENCES constructions(construction_id)
);

CREATE INDEX IF NOT EXISTS idx_status_history_construction ON status_history(construction_id);

-- 採番テーブル (基本設計書 9.3 + レビュー指摘)
CREATE TABLE IF NOT EXISTS id_sequences (
    prefix   TEXT NOT NULL,
    year     INTEGER NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (prefix, year)
);

-- 操作ログ (基本設計書 15.1)
CREATE TABLE IF NOT EXISTS operation_logs (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    screen      TEXT,
    operation   TEXT,
    target_id   TEXT,
    detail      TEXT
);
"""


# 工事区分マスタ初期値 (基本設計書 9.2.5)
INITIAL_CATEGORIES = [
    "原状回復", "漏水", "設備交換", "電気", "共用部", "内装", "その他"
]


def get_connection() -> sqlite3.Connection:
    """SQLite コネクションを取得する。"""
    paths = get_paths()
    db_path = paths["db_file"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_cursor() -> Iterator[sqlite3.Cursor]:
    """カーソルのコンテキストマネージャ。commit / rollback を自動処理。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
    """データベースを初期化する (スキーマ作成 + 初期マスタ投入)。"""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        # 工事区分マスタの初期値投入 (空のときのみ)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM categories")
        if cur.fetchone()[0] == 0:
            for name in INITIAL_CATEGORIES:
                cur.execute("INSERT INTO categories(name) VALUES (?)", (name,))
        conn.commit()
    finally:
        conn.close()


def next_construction_id(prefix: str, year: int) -> str:
    """工事 ID を採番する (トランザクション内で安全に連番取得)。

    形式: KOJI-YYYY-NNNN
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cur = conn.cursor()
        cur.execute(
            "SELECT last_seq FROM id_sequences WHERE prefix=? AND year=?",
            (prefix, year),
        )
        row = cur.fetchone()
        if row is None:
            new_seq = 1
            cur.execute(
                "INSERT INTO id_sequences(prefix, year, last_seq) VALUES (?, ?, ?)",
                (prefix, year, new_seq),
            )
        else:
            new_seq = row["last_seq"] + 1
            cur.execute(
                "UPDATE id_sequences SET last_seq=? WHERE prefix=? AND year=?",
                (new_seq, prefix, year),
            )
        conn.commit()
        return f"{prefix}-{year}-{new_seq:04d}"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_log(screen: str, operation: str, target_id: str = "", detail: str = "") -> None:
    """操作ログを DB に記録する。"""
    try:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO operation_logs(screen, operation, target_id, detail) "
                "VALUES (?, ?, ?, ?)",
                (screen, operation, target_id, detail),
            )
    except Exception:
        # ログ書き込み失敗は本処理を止めない
        pass
