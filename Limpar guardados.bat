@echo off
rem Duplo clique: mostra o que passou do prazo nas versoes antigas e na quarentena e pergunta se apaga.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python programa\sync_ssd.py --limpar
pause
