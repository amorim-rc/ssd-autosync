@echo off
rem Duplo clique: envia as mudancas desta pasta para o repositorio GitHub dela (origin).
chcp 65001 >nul
setlocal
cd /d "%~dp0"

git add -A
git diff --cached --quiet
if %errorlevel%==0 (
    echo Nada mudou desde a ultima publicacao. Nada a enviar.
    goto fim
)

rem Trava: o registro do SSD (serial e ID) nunca pode ir para o GitHub.
python -c "import json,os,subprocess,sys; c=json.load(open('sync_ssd_config.json',encoding='utf-8')) if os.path.exists('sync_ssd_config.json') else {}; n=subprocess.run(['git','diff','--cached','--name-only'],capture_output=True,text=True).stdout.split(); t=[v for v in (c.get('serial'),c.get('id')) if v]; r=subprocess.run(['git','grep','--cached','-l','-i','-F']+sum([['-e',v] for v in t],[]),capture_output=True,text=True).stdout.strip() if t else ''; sys.exit(1 if 'sync_ssd_config.json' in n or r else 0)" 2>nul
if errorlevel 1 (
    git reset --quiet
    echo.
    echo PARADO: o registro do SSD ^(serial ou ID^) apareceria no GitHub.
    echo Nada foi enviado. Peca ajuda antes de continuar.
    goto fim
)

echo Arquivos que serao enviados:
git diff --cached --name-status
echo.
for /f "delims=" %%u in ('git remote get-url origin') do set repo=%%u
echo Destino: %repo%
set /p ok=Enviar para o GitHub? Se o repositorio for publico, qualquer pessoa vera. [S/N]
if /i not "%ok%"=="S" (
    git reset --quiet
    echo Cancelado. Nada foi enviado.
    goto fim
)
set /p msg=Descreva a mudanca em uma frase:
if "%msg%"=="" set msg=Atualizacao
git commit --quiet -m "%msg%"
git push --quiet origin main
if errorlevel 1 (
    echo.
    echo O envio falhou. O commit ficou salvo aqui; peca ajuda para concluir.
) else (
    echo.
    echo Publicado em %repo%
)

:fim
echo.
pause
