@echo off
rem Duplo clique: backup Google Drive -> SSD e, em seguida, a conferencia.
rem --quarentena: o que foi apagado no Drive sai do espelho e fica guardado em _mnemosine\quarentena.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
python programa\mnemosine.py --quarentena
rem Codigos 0 e 1: o backup rodou (1 = alguns arquivos falharam). Acima disso, nao ha o que conferir.
if %errorlevel% GTR 1 goto fim
python programa\conferir_ssd.py
:fim
pause
