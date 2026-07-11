@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在解析银行流水 PDF...
python parse_bank_pdfs.py --force
if %errorlevel% equ 0 (
    echo.
    echo 解析完成，正在打开报告...
    start "" "bank_summary_2025.html"
) else (
    echo.
    echo 解析出错，请检查 PDF 文件。
    pause
)
