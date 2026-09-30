@echo off
rem Duplo clique: faz o backup Google Drive -> SSD e, em seguida, confere o resultado.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python sync_ssd.py
set backup=%errorlevel%
echo.
if %backup% GTR 1 (
    echo O backup nao rodou. Codigo %backup% - veja a mensagem acima.
    goto fim
)
echo ---------------- Conferencia ----------------
python conferir_ssd.py
set conferencia=%errorlevel%
echo.
if %backup%==0 if %conferencia%==0 (
    echo Backup concluido e conferido: SSD identico ao Drive.
    goto fim
)
echo Atencao: backup com codigo %backup%, conferencia com codigo %conferencia%. Veja as mensagens acima.

:fim
echo.
pause
