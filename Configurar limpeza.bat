@echo off
rem Duplo clique: escolhe se o que passa do prazo e apagado sozinho, com autorizacao ou nunca.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python programa\mnemosine.py --configurar-limpeza
pause
