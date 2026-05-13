"""Flask ルーティング層

画面 (HTML) と REST 風 API を提供する。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect,
    render_template, request, send_file, url_for,
)

from .config import get_paths, load_settings, save_settings
from .database import (
    ATTACHMENT_TYPES, PRIORITY_LABELS, STATUS_CODES, STATUS_LABELS,
)
from . import exports as exports_mod
from . import imports as imports_mod
from . import services as svc


bp = Blueprint("main", __name__)


# favicon (404 を抑制するため空応答)
@bp.route("/favicon.ico")
def favicon():
    return ("", 204)


# ---------------------------------------------------------------------------
# 共通: テンプレートのコンテキスト
# ---------------------------------------------------------------------------
@bp.app_context_processor
def inject_globals() -> Dict[str, Any]:
    return {
        "STATUS_CODES": STATUS_CODES,
        "STATUS_LABELS": STATUS_LABELS,
        "PRIORITY_LABELS": PRIORITY_LABELS,
        "ATTACHMENT_TYPES": ATTACHMENT_TYPES,
    }


# ---------------------------------------------------------------------------
# SC-01 ダッシュボード
# ---------------------------------------------------------------------------
@bp.route("/")
def dashboard():
    kpi = svc.get_dashboard_kpi()
    return render_template("dashboard.html", kpi=kpi)


# ---------------------------------------------------------------------------
# SC-02 工事一覧
# ---------------------------------------------------------------------------
@bp.route("/constructions")
def construction_list():
    filters = {
        "keyword": request.args.get("keyword", "").strip(),
        "status": request.args.get("status", ""),
        "property_id": request.args.get("property_id", type=int),
        "vendor_id": request.args.get("vendor_id", type=int),
        "staff_id": request.args.get("staff_id", type=int),
        "category_id": request.args.get("category_id", type=int),
        "priority": request.args.get("priority", type=int),
        "received_from": request.args.get("received_from", ""),
        "received_to": request.args.get("received_to", ""),
    }
    rows = svc.search_constructions(filters)
    return render_template(
        "construction_list.html",
        rows=rows,
        filters=filters,
        properties=svc.list_properties(),
        vendors=svc.list_vendors(),
        staffs=svc.list_staffs(),
        categories=svc.list_categories(),
    )


# ---------------------------------------------------------------------------
# SC-03 工事登録・編集
# ---------------------------------------------------------------------------
@bp.route("/constructions/new", methods=["GET", "POST"])
def construction_new():
    if request.method == "POST":
        data = {k: (v if v != "" else None) for k, v in request.form.items()}
        cid = svc.create_construction(data)
        flash(f"工事 {cid} を登録しました", "success")
        return redirect(url_for("main.construction_edit", construction_id=cid))
    return render_template(
        "construction_edit.html",
        record=None,
        attachments=[],
        history=[],
        properties=svc.list_properties(),
        vendors=svc.list_vendors(),
        staffs=svc.list_staffs(),
        categories=svc.list_categories(),
    )


@bp.route("/constructions/<construction_id>", methods=["GET", "POST"])
def construction_edit(construction_id: str):
    record = svc.get_construction(construction_id)
    if not record:
        abort(404)
    if request.method == "POST":
        data = {k: (v if v != "" else None) for k, v in request.form.items()}
        svc.update_construction(construction_id, data)
        flash("更新しました", "success")
        return redirect(url_for("main.construction_edit",
                                construction_id=construction_id))
    return render_template(
        "construction_edit.html",
        record=record,
        attachments=svc.list_attachments(construction_id),
        history=svc.get_status_history(construction_id),
        properties=svc.list_properties(),
        vendors=svc.list_vendors(),
        staffs=svc.list_staffs(),
        categories=svc.list_categories(),
    )


@bp.route("/constructions/<construction_id>/delete", methods=["POST"])
def construction_delete(construction_id: str):
    svc.delete_construction(construction_id)
    flash(f"工事 {construction_id} を削除しました", "success")
    return redirect(url_for("main.construction_list"))


# ---------------------------------------------------------------------------
# SC-04 添付管理
# ---------------------------------------------------------------------------
@bp.route("/constructions/<construction_id>/attachments", methods=["POST"])
def attachment_upload(construction_id: str):
    type_ = request.form.get("type", "other")
    if type_ not in ATTACHMENT_TYPES:
        type_ = "other"
    settings = load_settings()
    max_mb = int(settings.get("max_attachment_mb", 50))
    files = request.files.getlist("files")
    uploaded = 0
    for f in files:
        if not f or not f.filename:
            continue
        f.stream.seek(0, 2)
        size_mb = f.stream.tell() / 1024 / 1024
        f.stream.seek(0)
        if size_mb > max_mb:
            flash(f"{f.filename} はサイズ上限 {max_mb}MB を超えています", "error")
            continue
        svc.save_attachment(construction_id, type_, f)
        uploaded += 1
    if uploaded:
        flash(f"{uploaded} 件アップロードしました", "success")
    return redirect(url_for("main.construction_edit",
                            construction_id=construction_id) + "#tab-attachment")


@bp.route("/attachments/<int:attachment_id>")
def attachment_download(attachment_id: int):
    att = svc.get_attachment(attachment_id)
    if not att:
        abort(404)
    paths = get_paths()
    p = paths["root"] / att["file_path"]
    if not p.exists():
        abort(404)
    return send_file(str(p), as_attachment=True, download_name=att["file_name"])


@bp.route("/attachments/<int:attachment_id>/delete", methods=["POST"])
def attachment_delete(attachment_id: int):
    att = svc.get_attachment(attachment_id)
    if not att:
        abort(404)
    cid = att["construction_id"]
    svc.delete_attachment(attachment_id)
    flash("添付を削除しました", "success")
    return redirect(url_for("main.construction_edit",
                            construction_id=cid) + "#tab-attachment")


# ---------------------------------------------------------------------------
# SC-05 マスタ管理
# ---------------------------------------------------------------------------
MASTER_LABELS = {
    "properties": ("物件マスタ", ["property_id", "name", "address", "owner_name", "note"]),
    "vendors":    ("業者マスタ", ["vendor_id", "name", "contact", "phone", "email", "note"]),
    "staffs":     ("担当者マスタ", ["staff_id", "name", "role", "email"]),
    "categories": ("工事区分マスタ", ["category_id", "name"]),
}


@bp.route("/masters/<table>", methods=["GET", "POST"])
def master(table: str):
    if table not in MASTER_LABELS:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            rid = request.form.get("record_id", type=int)
            if rid:
                svc.delete_master(table, rid)
                flash("無効化しました", "success")
        else:
            cols = MASTER_LABELS[table][1]
            data = {c: (request.form.get(c) or None) for c in cols}
            id_col = cols[0]
            if data.get(id_col):
                data[id_col] = int(data[id_col])
            else:
                data.pop(id_col, None)
            svc.upsert_master(table, data)
            flash("保存しました", "success")
        return redirect(url_for("main.master", table=table))
    rows = {
        "properties": svc.list_properties(active_only=False),
        "vendors":    svc.list_vendors(active_only=False),
        "staffs":     svc.list_staffs(active_only=False),
        "categories": svc.list_categories(active_only=False),
    }[table]
    label, cols = MASTER_LABELS[table]
    return render_template("master.html", table=table, label=label,
                           cols=cols, rows=rows)


@bp.route("/masters")
def master_index():
    return render_template("master_index.html", masters=MASTER_LABELS)


# ---------------------------------------------------------------------------
# SC-07 出力
# ---------------------------------------------------------------------------
@bp.route("/export", methods=["GET", "POST"])
def export_view():
    if request.method == "POST":
        template = request.form.get("template", "list")
        # 絞り込みを引き継ぎ
        filters = {
            "keyword": request.form.get("keyword", ""),
            "status": request.form.get("status", ""),
            "property_id": request.form.get("property_id", type=int),
            "vendor_id": request.form.get("vendor_id", type=int),
            "received_from": request.form.get("received_from", ""),
            "received_to": request.form.get("received_to", ""),
        }
        rows = svc.search_constructions(filters)
        path = exports_mod.export(template, rows)
        return send_file(str(path), as_attachment=True, download_name=path.name)
    return render_template(
        "export.html",
        templates=exports_mod.TEMPLATES,
        properties=svc.list_properties(),
        vendors=svc.list_vendors(),
    )


# ---------------------------------------------------------------------------
# SC-06 インポート
# ---------------------------------------------------------------------------
@bp.route("/import", methods=["GET", "POST"])
def import_view():
    analyzed = None
    if request.method == "POST":
        action = request.form.get("action", "analyze")
        upload = request.files.get("file")
        if action == "analyze" and upload and upload.filename:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=Path(upload.filename).suffix
            ) as tmp:
                upload.save(tmp.name)
                tmp_path = Path(tmp.name)
            try:
                analyzed = imports_mod.analyze_import(tmp_path)
                # セッションは使わず一時ファイルパスを返す
                analyzed["_tmp_path"] = str(tmp_path)
            except Exception as e:  # noqa: BLE001
                flash(f"取り込みに失敗: {e}", "error")
        elif action == "commit":
            tmp_path = Path(request.form.get("tmp_path", ""))
            if tmp_path.exists():
                try:
                    analyzed = imports_mod.analyze_import(tmp_path)
                    result = imports_mod.commit_import(analyzed)
                    flash(
                        f"取り込み完了: 工事 {result['created_constructions']} 件 / "
                        f"物件 {result['created_properties']} / "
                        f"業者 {result['created_vendors']} / "
                        f"担当者 {result['created_staffs']} / "
                        f"区分 {result['created_categories']} 件 を作成",
                        "success",
                    )
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                except Exception as e:  # noqa: BLE001
                    flash(f"確定に失敗: {e}", "error")
                return redirect(url_for("main.import_view"))
            else:
                flash("一時ファイルが見つかりません。再度アップロードしてください", "error")
    return render_template("import.html", analyzed=analyzed)


# ---------------------------------------------------------------------------
# SC-08 バックアップ / 復元
# ---------------------------------------------------------------------------
@bp.route("/backup", methods=["GET", "POST"])
def backup_view():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            p = svc.create_backup()
            flash(f"バックアップを作成しました: {p.name}", "success")
        elif action == "restore":
            name = request.form.get("name")
            if name:
                try:
                    svc.restore_backup(name)
                    flash(f"{name} から復元しました", "success")
                except Exception as e:  # noqa: BLE001
                    flash(f"復元に失敗: {e}", "error")
        elif action == "check":
            result = svc.integrity_check()
            flash(
                f"整合性チェック: 欠損 {len(result['missing'])} / "
                f"孤立 {len(result['orphan_files'])} / "
                f"参照切れ案件 {len(result['orphan_constructions'])} 件",
                "info",
            )
        return redirect(url_for("main.backup_view"))
    return render_template("backup.html", backups=svc.list_backups())


@bp.route("/backup/download/<name>")
def backup_download(name: str):
    paths = get_paths()
    f = paths["backups"] / name
    if not f.exists():
        abort(404)
    return send_file(str(f), as_attachment=True, download_name=name)


# ---------------------------------------------------------------------------
# SC-09 設定
# ---------------------------------------------------------------------------
@bp.route("/settings", methods=["GET", "POST"])
def settings_view():
    if request.method == "POST":
        settings = load_settings()
        for k in ("data_root", "id_prefix", "id_reset_mode", "default_encoding"):
            v = request.form.get(k)
            if v is not None and v != "":
                settings[k] = v
        for k in ("backup_retention", "max_attachment_mb"):
            v = request.form.get(k, type=int)
            if v is not None:
                settings[k] = v
        settings["auto_backup_on_exit"] = bool(
            request.form.get("auto_backup_on_exit")
        )
        save_settings(settings)
        flash("設定を保存しました", "success")
        return redirect(url_for("main.settings_view"))
    return render_template("settings.html", settings=load_settings())
