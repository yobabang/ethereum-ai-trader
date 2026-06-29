@echo off
REM 以太 AI Trader — 一键启动 (Windows)
set DIR=%~dp0
set TRADE_DIR=%DIR%..\freqtrade
set CONFIG=%DIR%config.json
set PYTHON=%TRADE_DIR%\freqtrade\.venv\Scripts\python.exe

echo ========================================
echo   以太 AI Trader — 启动中...
echo ========================================

echo [1/3] Starting AI API Bridge on :8081...
start "AI-Bridge" "%PYTHON%" -m freqtrade.ai.api_bridge --config "%CONFIG%" --host 127.0.0.1 --port 8081
timeout /t 2 >nul

echo [2/3] Starting Freqtrade Bot (dry-run)...
cd /d "%TRADE_DIR%"
start "Freqtrade-Bot" "%PYTHON%" -m freqtrade trade -c "%CONFIG%" --dry-run
timeout /t 3 >nul

echo [3/3] Starting Web Dashboard...
cd /d "%TRADE_DIR%\web"
if not exist "node_modules" (
    echo   Installing dependencies...
    call npm install
)
start "Web-Dashboard" npm run dev

echo.
echo ========================================
echo   全部启动完成!
echo   Dashboard: http://localhost:3000
echo   API:       http://localhost:8080
echo   AI Bridge: http://localhost:8081
echo ========================================
pause
