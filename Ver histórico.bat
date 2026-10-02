@echo off
rem Duplo clique: abre no navegador o historico dos backups e conferencias.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python programa\guarda_ssd.py --historico
if errorlevel 1 pause
