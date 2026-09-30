@echo off
rem Duplo clique: faz o backup Google Drive -> SSD e mostra o resultado.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python sync_ssd.py
echo.
if %errorlevel%==0 (echo Backup concluido com sucesso.) else (echo Algo deu errado. Codigo %errorlevel% - veja a mensagem acima.)
echo.
pause
