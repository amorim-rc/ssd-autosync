@echo off
rem Duplo clique: mostra o que o backup faria, sem copiar nada.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python programa\mnemosine.py --simular --quarentena
pause
