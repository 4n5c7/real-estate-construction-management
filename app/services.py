"""ビジネスロジック層

工事案件・マスタ・添付・状態遷移などのドメイン操作をまとめる。
ルーティング(views) から呼ぶ。
"""
from __future__ import annotations

import hashlib
import shutil
import zipfile
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import get_paths, load_settings
from .database import (
    ATTACHMENT_TYPES,
    STATUS_LABELS,
    db_cursor,
    next_construction_id,
    write_log,
)


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return date.today().isoformat()


def _to_int(v: Any) -> Optional[int]:
    """カンマ・円記号・全角数字を許容して整数化する (基本設計書 13.3)。"""
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    # 全角→半角
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    s = s.replace(",", "").replace("，", "").replace("¥", "").replace("円", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# マスタ
# ---------------------------------------------------------------------------
def list_properties(active_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM properties"
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY name"
    with db_cursor() as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def list_vendors(active_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM vendors"
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY name"
    with db_cursor() as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def list_staffs(active_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM staffs"
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY name"
    with db_cursor() as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def list_categories(active_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM categories"
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY category_id"
    with db_cursor() as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def upsert_master(table: str, data: Dict[str, Any]) -> int:
    """マスタの追加/更新。table = properties / vendors / staffs / categories"""
    id_col = {
        "properties": "property_id",
        "vendors": "vendor_id",
        "staffs": "staff_id",
        "categories": "category_id",
    }[table]
    record_id = data.get(id_col)
    fields = {k: v for k, v in data.items() if k != id_col}
    with db_cursor() as cur:
        if record_id:
            sets = ", ".join(f"{k}=?" for k in fields.keys())
            cur.execute(
                f"UPDATE {table} SET {sets} WHERE {id_col}=?",
                list(fields.values()) + [record_id],
            )
            return int(record_id)
        else:
            cols = ", ".join(fields.keys())
            qs = ", ".join("?" for _ in fields)
            cur.execute(
                f"INSERT INTO {table}({cols}) VALUES ({qs})",
                list(fields.values()),
            )
            return int(cur.lastrowid)


def delete_master(table: str, record_id: int) -> None:
    """マスタの論理削除 (is_active=0)。"""
    id_col = {
        "properties": "property_id",
        "vendors": "vendor_id",
        "staffs": "staff_id",
        "categories": "category_id",
    }[table]
    with db_cursor() as cur:
        cur.execute(f"UPDATE {table} SET is_active=0 WHERE {id_col}=?", (record_id,))


# ---------------------------------------------------------------------------
# 工事案件
# ---------------------------------------------------------------------------
CONSTRUCTION_FIELDS = [
    "property_id", "room_no", "category_id", "priority", "status",
    "staff_id", "vendor_id", "description",
    "received_at", "scheduled_start_at", "scheduled_end_at", "completed_at",
    "estimate_amount", "estimate_received_at", "estimate_valid_until",
    "invoice_amount", "invoice_received_at", "payment_due_at", "paid", "note",
]


def _generate_id() -> str:
    settings = load_settings()
    prefix = settings.get("id_prefix", "KOJI")
    mode = settings.get("id_reset_mode", "calendar_year")
    today = date.today()
    if mode == "fiscal_year":
        # 4月開始
        year = today.year if today.month >= 4 else today.year - 1
    else:
        year = today.year
    return next_construction_id(prefix, year)


def create_construction(data: Dict[str, Any]) -> str:
    """工事案件を新規登録し、工事 ID を返す。"""
    construction_id = data.get("construction_id") or _generate_id()
    # 必須・既定値
    if not data.get("received_at"):
        data["received_at"] = _today()
    if not data.get("priority"):
        data["priority"] = 1
    if not data.get("status"):
        data["status"] = "received"
    # 数値変換
    for f in ("estimate_amount", "invoice_amount"):
        if f in data:
            data[f] = _to_int(data[f])
    # paid フラグ
    data["paid"] = 1 if data.get("paid") in (1, "1", True, "on", "true") else 0

    cols = ["construction_id"] + [f for f in CONSTRUCTION_FIELDS if f in data]
    vals = [construction_id] + [data.get(f) for f in CONSTRUCTION_FIELDS if f in data]
    placeholders = ", ".join("?" for _ in cols)

    with db_cursor() as cur:
        cur.execute(
            f"INSERT INTO constructions({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        # 状態履歴
        cur.execute(
            "INSERT INTO status_history(construction_id, old_status, new_status, comment) "
            "VALUES (?, ?, ?, ?)",
            (construction_id, None, data["status"], "新規登録"),
        )
    write_log("SC-03", "create", construction_id, "工事案件登録")
    return construction_id


def update_construction(construction_id: str, data: Dict[str, Any]) -> None:
    """工事案件を更新する。状態が変化した場合は履歴も記録。"""
    # 現状取得
    current = get_construction(construction_id)
    if not current:
        raise ValueError(f"工事 ID {construction_id} は存在しません")

    for f in ("estimate_amount", "invoice_amount"):
        if f in data:
            data[f] = _to_int(data[f])
    data["paid"] = 1 if data.get("paid") in (1, "1", True, "on", "true") else 0

    fields = [f for f in CONSTRUCTION_FIELDS if f in data]
    if not fields:
        return
    sets = ", ".join(f"{f}=?" for f in fields) + ", updated_at=datetime('now','localtime')"
    vals = [data.get(f) for f in fields] + [construction_id]
    with db_cursor() as cur:
        cur.execute(f"UPDATE constructions SET {sets} WHERE construction_id=?", vals)
        new_status = data.get("status")
        if new_status and new_status != current["status"]:
            cur.execute(
                "INSERT INTO status_history(construction_id, old_status, new_status, comment) "
                "VALUES (?, ?, ?, ?)",
                (construction_id, current["status"], new_status, data.get("status_comment", "")),
            )
    write_log("SC-03", "update", construction_id, "工事案件更新")


def get_construction(construction_id: str) -> Optional[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT c.*, p.name AS property_name, v.name AS vendor_name,
                   s.name AS staff_name, cat.name AS category_name
            FROM constructions c
            LEFT JOIN properties p ON c.property_id = p.property_id
            LEFT JOIN vendors    v ON c.vendor_id   = v.vendor_id
            LEFT JOIN staffs     s ON c.staff_id    = s.staff_id
            LEFT JOIN categories cat ON c.category_id = cat.category_id
            WHERE c.construction_id=? AND c.deleted_at IS NULL
            """,
            (construction_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_construction(construction_id: str) -> None:
    """工事案件を論理削除する。"""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE constructions SET deleted_at=datetime('now','localtime') "
            "WHERE construction_id=?",
            (construction_id,),
        )
    write_log("SC-02", "delete", construction_id, "工事案件削除(論理)")


def search_constructions(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """工事一覧の検索 (基本設計書 8.3.2)。"""
    where = ["c.deleted_at IS NULL"]
    params: List[Any] = []

    if filters.get("keyword"):
        kw = f"%{filters['keyword']}%"
        where.append(
            "(c.construction_id LIKE ? OR p.name LIKE ? OR c.room_no LIKE ? "
            "OR c.description LIKE ? OR c.note LIKE ?)"
        )
        params.extend([kw, kw, kw, kw, kw])

    if filters.get("status"):
        where.append("c.status = ?")
        params.append(filters["status"])

    if filters.get("property_id"):
        where.append("c.property_id = ?")
        params.append(filters["property_id"])

    if filters.get("vendor_id"):
        where.append("c.vendor_id = ?")
        params.append(filters["vendor_id"])

    if filters.get("staff_id"):
        where.append("c.staff_id = ?")
        params.append(filters["staff_id"])

    if filters.get("category_id"):
        where.append("c.category_id = ?")
        params.append(filters["category_id"])

    if filters.get("priority"):
        where.append("c.priority = ?")
        params.append(filters["priority"])

    if filters.get("received_from"):
        where.append("c.received_at >= ?")
        params.append(filters["received_from"])
    if filters.get("received_to"):
        where.append("c.received_at <= ?")
        params.append(filters["received_to"])

    sql = f"""
        SELECT c.*, p.name AS property_name, v.name AS vendor_name,
               s.name AS staff_name, cat.name AS category_name,
               (SELECT COUNT(*) FROM attachments a
                 WHERE a.construction_id=c.construction_id AND a.type='estimate') AS has_estimate,
               (SELECT COUNT(*) FROM attachments a
                 WHERE a.construction_id=c.construction_id AND a.type='invoice') AS has_invoice
        FROM constructions c
        LEFT JOIN properties p ON c.property_id = p.property_id
        LEFT JOIN vendors    v ON c.vendor_id   = v.vendor_id
        LEFT JOIN staffs     s ON c.staff_id    = s.staff_id
        LEFT JOIN categories cat ON c.category_id = cat.category_id
        WHERE {' AND '.join(where)}
        ORDER BY c.received_at DESC, c.construction_id DESC
    """
    with db_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_status_history(construction_id: str) -> List[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT h.*, s.name AS changed_by_name "
            "FROM status_history h LEFT JOIN staffs s ON h.changed_by=s.staff_id "
            "WHERE h.construction_id=? ORDER BY h.changed_at DESC",
            (construction_id,),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# ダッシュボード KPI
# ---------------------------------------------------------------------------
def get_dashboard_kpi() -> Dict[str, Any]:
    today = _today()
    with db_cursor() as cur:
        cur.execute(
            "SELECT status, COUNT(*) AS cnt FROM constructions "
            "WHERE deleted_at IS NULL GROUP BY status"
        )
        by_status = {r["status"]: r["cnt"] for r in cur.fetchall()}

        cur.execute(
            "SELECT COALESCE(SUM(estimate_amount),0) AS s_est, "
            "COALESCE(SUM(invoice_amount),0) AS s_inv "
            "FROM constructions WHERE deleted_at IS NULL"
        )
        sums = dict(cur.fetchone())

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM constructions "
            "WHERE deleted_at IS NULL AND priority=3 "
            "AND status NOT IN ('completed','invoiced','cancelled')"
        )
        urgent = cur.fetchone()["cnt"]

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM constructions "
            "WHERE deleted_at IS NULL AND scheduled_end_at IS NOT NULL "
            "AND scheduled_end_at < ? "
            "AND status NOT IN ('completed','invoiced','cancelled')",
            (today,),
        )
        overdue = cur.fetchone()["cnt"]

        cur.execute(
            "SELECT operation, target_id, occurred_at, screen "
            "FROM operation_logs ORDER BY log_id DESC LIMIT 10"
        )
        recent_ops = [dict(r) for r in cur.fetchall()]

    in_progress = sum(
        by_status.get(s, 0)
        for s in ("received", "quoting", "quoted", "pending_approval",
                  "ordered", "in_progress")
    )
    completed = by_status.get("completed", 0) + by_status.get("invoiced", 0)

    return {
        "by_status": by_status,
        "in_progress": in_progress,
        "completed": completed,
        "on_hold": by_status.get("on_hold", 0),
        "estimate_sum": sums["s_est"],
        "invoice_sum": sums["s_inv"],
        "urgent": urgent,
        "overdue": overdue,
        "recent_ops": recent_ops,
    }


# ---------------------------------------------------------------------------
# 添付ファイル
# ---------------------------------------------------------------------------
def _sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def save_attachment(construction_id: str, type_: str, file_storage) -> Dict[str, Any]:
    """添付ファイルを保存。

    保存先: data/attachments/<construction_id>/<type>/
    同名は _yyyymmddHHMMSS 付与で重複回避 (基本設計書 11.2)。
    """
    paths = get_paths()
    target_dir = paths["attachments"] / construction_id / type_
    target_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(file_storage.filename).name
    target_path = target_dir / original_name
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        target_path = target_dir / f"{stem}_{ts}{suffix}"

    file_storage.save(str(target_path))
    size = target_path.stat().st_size
    checksum = _sha256(target_path)

    # 相対パスで保存 (data_root からの相対)
    rel_path = target_path.relative_to(paths["root"]).as_posix()

    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO attachments(construction_id, type, file_name, file_path, "
            "file_size, checksum) VALUES (?, ?, ?, ?, ?, ?)",
            (construction_id, type_, original_name, rel_path, size, checksum),
        )
        attachment_id = cur.lastrowid
    write_log("SC-04", "upload", construction_id, f"{type_}: {original_name}")
    return {
        "attachment_id": attachment_id,
        "file_name": original_name,
        "file_path": rel_path,
        "file_size": size,
        "type": type_,
    }


def list_attachments(construction_id: str) -> List[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM attachments WHERE construction_id=? "
            "ORDER BY type, uploaded_at DESC",
            (construction_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_attachment(attachment_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM attachments WHERE attachment_id=?", (attachment_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def delete_attachment(attachment_id: int) -> None:
    att = get_attachment(attachment_id)
    if not att:
        return
    paths = get_paths()
    file_path = paths["root"] / att["file_path"]
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError:
        pass
    with db_cursor() as cur:
        cur.execute("DELETE FROM attachments WHERE attachment_id=?", (attachment_id,))
    write_log("SC-04", "delete_attachment", att["construction_id"], att["file_name"])


# ---------------------------------------------------------------------------
# バックアップ / 復元 / 整合性チェック
# ---------------------------------------------------------------------------
def create_backup() -> Path:
    """data フォルダ全体を ZIP 圧縮してバックアップ。"""
    paths = get_paths()
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    backup_file = paths["backups"] / f"工事管理バックアップ_{ts}.zip"

    # 含めるディレクトリ
    targets = ["database", "attachments", "config"]
    with zipfile.ZipFile(backup_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in targets:
            src = paths["root"] / t
            if not src.exists():
                continue
            for p in src.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(paths["root"]))
    # 保持世代の整理
    _cleanup_backups()
    write_log("SC-08", "backup", "", backup_file.name)
    return backup_file


def _cleanup_backups() -> None:
    settings = load_settings()
    keep = int(settings.get("backup_retention", 30))
    paths = get_paths()
    files = sorted(
        paths["backups"].glob("工事管理バックアップ_*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass


def list_backups() -> List[Dict[str, Any]]:
    paths = get_paths()
    result = []
    for f in sorted(paths["backups"].glob("*.zip"), reverse=True):
        st = f.stat()
        result.append({
            "name": f.name,
            "size_kb": st.st_size // 1024,
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return result


def restore_backup(zip_name: str) -> None:
    """指定バックアップから復元。事前に現状を退避する。"""
    paths = get_paths()
    target = paths["backups"] / zip_name
    if not target.exists():
        raise FileNotFoundError(zip_name)

    # 現状退避
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    safe_zip = paths["backups"] / f"before_restore_{ts}.zip"
    with zipfile.ZipFile(safe_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in ("database", "attachments", "config"):
            src = paths["root"] / t
            if not src.exists():
                continue
            for p in src.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(paths["root"]))

    # 既存削除
    for t in ("database", "attachments", "config"):
        d = paths["root"] / t
        if d.exists():
            shutil.rmtree(d)

    # 展開
    with zipfile.ZipFile(target, "r") as zf:
        zf.extractall(paths["root"])

    write_log("SC-08", "restore", "", zip_name)


def integrity_check() -> Dict[str, Any]:
    """DB と添付ファイルの整合性チェック (基本設計書 14.3)。"""
    paths = get_paths()
    missing: List[Dict[str, Any]] = []
    with db_cursor() as cur:
        cur.execute("SELECT * FROM attachments")
        attachments = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT c.* FROM constructions c "
            "LEFT JOIN properties p ON c.property_id=p.property_id "
            "WHERE c.deleted_at IS NULL AND p.property_id IS NULL"
        )
        orphan_const = [dict(r) for r in cur.fetchall()]

    db_paths = set()
    for att in attachments:
        full = paths["root"] / att["file_path"]
        db_paths.add(full.resolve())
        if not full.exists():
            missing.append(att)

    # 添付フォルダ内の孤立ファイル
    orphan_files: List[str] = []
    for p in paths["attachments"].rglob("*"):
        if p.is_file() and p.resolve() not in db_paths:
            orphan_files.append(str(p.relative_to(paths["root"])))

    write_log("SC-08", "integrity_check", "",
              f"missing={len(missing)}, orphan={len(orphan_files)}")
    return {
        "missing": missing,
        "orphan_files": orphan_files,
        "orphan_constructions": orphan_const,
    }
