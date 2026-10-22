@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Ставлю Говорилку...
if not exist .venv (
    py -3.12 -m venv .venv || python -m venv .venv || goto :error
)
.venv\Scripts\python.exe -m pip install --upgrade pip -q || goto :error
.venv\Scripts\python.exe -m pip install -r requirements.txt -q || goto :error
echo Скачиваю модель GigaAM (около 850 МБ, один раз)...
.venv\Scripts\python.exe -c "from govorilka.recognizer import Recognizer; Recognizer('gigaam-v3-e2e-rnnt', print)" || goto :error
echo.
echo Готово. Запуск: Govorilka.bat
pause
exit /b 0
:error
echo Установка не удалась, смотрите ошибку выше.
pause
exit /b 1
