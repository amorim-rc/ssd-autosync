@echo off
rem Duplo clique: confere se o SSD esta identico ao Drive. So leitura, nao copia nada.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python programa\conferir_ssd.py
pause
