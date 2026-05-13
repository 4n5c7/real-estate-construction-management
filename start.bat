@echo off
REM ================================================
REM  不動産工事管理アプリ 起動スクリプト (Windows)
REM ================================================
cd /d "%~dp0"

REM 仮想環境があれば有効化
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM 依存ライブラリの確認・インストール
python -c "import flask, openpyxl" 2>nul
if errorlevel 1 (
    echo 必要なライブラリをインストールしています...
    python -m pip install -r requirements.txt
)

REM アプリ起動
python run.py
pause
