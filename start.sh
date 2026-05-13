#!/usr/bin/env bash
# ================================================
#  不動産工事管理アプリ 起動スクリプト (Mac/Linux)
# ================================================
cd "$(dirname "$0")"

# 仮想環境があれば有効化
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# 依存ライブラリの確認・インストール
python3 -c "import flask, openpyxl" 2>/dev/null || {
    echo "必要なライブラリをインストールしています..."
    python3 -m pip install -r requirements.txt
}

# アプリ起動
python3 run.py "$@"
