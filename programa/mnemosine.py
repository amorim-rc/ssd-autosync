"""
mnemosine.py - Backup de mão única: Google Drive (local) -> SSD externo específico.

Regras:
  * Copia arquivos novos e arquivos cuja data de modificação ou tamanho mudou.
  * Detecta arquivos/pastas movidos ou renomeados e os move dentro do SSD, em
    vez de copiar de novo e deixar a cópia antiga para trás.
  * Ignora atalhos do Google Docs (.gdoc, .gsheet...), desktop.ini e o que
    mais estiver em "excluir" na configuração.
  * NUNCA apaga nada do SSD. Antes de sobrescrever um arquivo, guarda a versão
    antiga em _mnemosine/versoes-antigas/<data>/ (por "dias_versoes" dias).
    Órfãos (arquivos que saíram do Drive) só saem do espelho se você pedir com
    --quarentena, e vão para _mnemosine/quarentena/<data>/ (por "dias_quarentena" dias).
  * Só roda no SSD registrado: exige o número de série do volume E o arquivo
    de identidade com o ID gerado no registro. Outro disco com a mesma letra
    ou o mesmo nome é recusado.
  * Trava de segurança: se uma execução for alterar arquivos demais de uma vez
    (sinal típico de ransomware ou de erro), aborta sem copiar nada.
  * Confere espaço livre antes de começar, não roda duas vezes ao mesmo tempo
    e pula arquivos que o Google Drive ainda não baixou (modo "stream").

Uso:
  python mnemosine.py --registrar D:        # uma vez: marca este SSD como o destino
  python mnemosine.py --simular             # mostra o que faria, sem copiar
  python mnemosine.py                       # executa o backup
  python mnemosine.py --forcar              # executa ignorando a trava de segurança
  python mnemosine.py --orfaos              # só lista o que está no SSD mas saiu do Drive
  python mnemosine.py --quarentena          # backup + move órfãos para _mnemosine/quarentena/
  python mnemosine.py --verificar [N]       # backup + confere hash de N arquivos (0 = todos)
  python mnemosine.py --status              # mostra o resultado da última execução
  python mnemosine.py --historico           # abre o histórico de execuções no navegador
  python mnemosine.py --limpar              # o que venceu nas pastas de guarda, com confirmação
  python mnemosine.py --configurar-limpeza  # apagar o que vence: automático, perguntar ou nunca
  python mnemosine.py --testar-notificacao  # envia uma notificação de teste
  python mnemosine.py --alertar-se-velho 7  # se o SSD não estiver plugado e o último
                                           # backup tiver mais de 7 dias, avisa na tela

Saída (o backup é o mesmo; muda só como o resultado aparece):
  (padrão)       tela resumida, com cores e símbolos
  --detalhado    formato técnico, hora em cada linha
  --silencioso   nada na tela; notificação do Windows se houver problema (agendamento)
  --json         o resultado em JSON, para outro programa ler

Códigos de saída: veja CODIGOS no início do arquivo (ou o README).
"""

import argparse
import ctypes
import fnmatch
import hashlib
import json
import os
import random
import shutil
import stat
import string
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import visual_ssd as v

VERSAO = "3.0"
WINDOWS = os.name == "nt"

# ---------------------------------------------------------------- configuração
PASTA_SISTEMA = "_mnemosine"      # na raiz do SSD: identidade, logs, versões
PASTA_SISTEMA_LEGADA = "_guarda_ssd" # nome usado até a 2.2 (o programa se chamava guarda_ssd); migrado
PASTA_VERSOES = "versoes-antigas"  # dentro de PASTA_SISTEMA: versão anterior de arquivos sobrescritos
PASTA_QUARENTENA = "quarentena"    # dentro de PASTA_SISTEMA: arquivos que saíram da origem
PASTA_LEGADA = "versoes"           # nome usado até a 2.0 (migrado para PASTA_VERSOES)
ARQ_IDENTIDADE = "IDENTIDADE_SSD.txt"
ARQ_ESTADO = "ultimo_resultado.json"
SUFIXO_TMP = ".sync_tmp"
FORMATO_CARIMBO = "%Y-%m-%d_%H%M%S"

# Valores padrão. Qualquer um pode ser sobrescrito no mnemosine_config.json.
PADRAO = {
    "origem": r"G:\Meu Drive",
    "pasta_destino": "",            # subpasta no SSD ("" = raiz do SSD)
    "excluir": [                    # padrões fnmatch, testados no nome e no caminho relativo
        "*.gdoc", "*.gsheet", "*.gslides", "*.gform", "*.gdraw", "*.gmap",
        "*.gsite", "*.gjam", "*.glink", "*.gscript", "*.gtable", "*.gnote",
        "desktop.ini", "thumbs.db", ".ds_store", "~$*", ".~lock*", "*" + SUFIXO_TMP,
    ],
    "tolerancia_seg": 2,            # diferença de data abaixo disso é ignorada
    "limite_abs": 300,              # trava: mais que isso de alterações...
    "limite_pct": 0.25,             # ...E mais que 25% do total -> aborta
    "dias_versoes": 90,             # versões antigas ficam por tantos dias
    "dias_quarentena": 90,          # arquivos em quarentena ficam por tantos dias
    "apagar_vencidos": "automatico",   # o que passou do prazo: automatico | perguntar | nunca
    "max_versoes_gb": 0,            # teto de espaço para versões antigas (0 = sem teto)
    "margem_espaco_gb": 2,          # espaço livre mínimo que deve sobrar no SSD
    "caminhos_longos": True,        # usa o prefixo \\?\ para caminhos > 260 caracteres
    "horas_lock_velho": 12,         # lock mais velho que isso é considerado abandonado
}
RAIZ_IGNORADA = {"$recycle.bin", "system volume information", "found.000"}

# Atributos do Windows que marcam arquivo ainda não baixado (Drive em modo stream).
ATRIB_PLACEHOLDER = 0x1000 | 0x40000 | 0x400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

CODIGOS = {
    0: "sucesso",
    1: "backup feito, mas alguns arquivos falharam",
    2: "SSD de backup não encontrado",
    3: "origem indisponível (Google Drive fechado?)",
    4: "origem vazia, abortado por segurança",
    5: "trava de segurança acionada, nada copiado",
    6: "espaço insuficiente no SSD, nada copiado",
    7: "já existe outra execução em andamento",
    8: "verificação de hash encontrou divergências",
    9: "alerta: último backup velho demais",
    10: "configuração não encontrada (rode --registrar)",
    11: "erro inesperado (veja o log)",
}

AQUI = Path(__file__).resolve().parent
PASTA_LOCAL = Path(os.environ.get("LOCALAPPDATA", str(AQUI))) / "Mnemosine"
PASTA_LOCAL_LEGADA = Path(os.environ.get("LOCALAPPDATA", str(AQUI))) / "GuardaSSD"   # até a 2.2
CONFIG_PADRAO = AQUI / "mnemosine_config.json"
CONFIG_LEGADO = AQUI / "guarda_ssd_config.json"                                       # até a 2.2
LOG_LOCAL = PASTA_LOCAL / "sync.log"
ESTADO_LOCAL = PASTA_LOCAL / ARQ_ESTADO
LOCK = PASTA_LOCAL / "sync.lock"


# ---------------------------------------------------------------------- log
class Log:
    """Log técnico (hora em cada linha). Sempre vai para os arquivos de log; só
    aparece na tela quando `ecoar` é verdadeiro (modo --detalhado)."""

    def __init__(self):
        self.linhas = []
        self.ecoar = True
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # pythonw não tem console

    def __call__(self, msg):
        linha = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        self.linhas.append(linha)
        if not self.ecoar:
            return
        try:
            print(linha)
        except (UnicodeEncodeError, AttributeError, OSError):
            pass

    def gravar(self, *destinos):
        if not self.linhas:
            return
        for arq in destinos:
            try:
                arq.parent.mkdir(parents=True, exist_ok=True)
                with open(arq, "a", encoding="utf-8") as f:
                    f.write("\n".join(self.linhas) + "\n\n")
            except OSError:
                pass


log = Log()


def fmt_bytes(n):
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidade == "TB":
            return f"{n:.1f} {unidade}" if unidade != "B" else f"{int(n)} B"
        n /= 1024


def fmt_duracao(seg):
    seg = int(seg)
    if seg < 60:
        return f"{seg}s"
    if seg < 3600:
        return f"{seg // 60}min {seg % 60}s"
    return f"{seg // 3600}h {seg % 3600 // 60}min"


def longo(p, ativar=True):
    r"""Prefixo \\?\ para o Windows aceitar caminhos com mais de 260 caracteres."""
    s = str(p)
    if WINDOWS and ativar and not s.startswith("\\\\"):
        return "\\\\?\\" + s
    return s


# ----------------------------------------------------- identificação do SSD
def info_volume(raiz):
    """(número de série, sistema de arquivos) do volume, ou (None, None)."""
    if not WINDOWS:
        return None, None
    k32 = ctypes.windll.kernel32
    k32.SetErrorMode(0x0001 | 0x8000)   # não abre "Insira um disco na unidade X:"
    serial = ctypes.c_uint32()
    fs = ctypes.create_unicode_buffer(64)
    ok = k32.GetVolumeInformationW(
        ctypes.c_wchar_p(raiz), None, 0, ctypes.byref(serial), None, None, fs, 64)
    return (f"{serial.value:08X}", fs.value) if ok else (None, None)


def unidades():
    """Letras de unidades fixas e removíveis (pula rede, CD e RAM disk)."""
    if not WINDOWS:
        return []
    k32 = ctypes.windll.kernel32
    mascara = k32.GetLogicalDrives()
    saida = []
    for i, letra in enumerate(string.ascii_uppercase):
        if mascara >> i & 1:
            raiz = f"{letra}:\\"
            if k32.GetDriveTypeW(ctypes.c_wchar_p(raiz)) in (2, 3):   # REMOVABLE, FIXED
                saida.append(raiz)
    return saida


def sistema_de(ssd):
    """A pasta de sistema no SSD. Enquanto a pasta de nome antigo (_guarda_ssd) não for
    migrada, é ela que vale; depois, sempre a nova."""
    nova = Path(ssd) / PASTA_SISTEMA
    antiga = Path(ssd) / PASTA_SISTEMA_LEGADA
    return antiga if not nova.exists() and antiga.is_dir() else nova


def migrar_pasta_sistema(ssd):
    """Renomeia _guarda_ssd para _mnemosine no SSD, se for o caso. -> True se renomeou."""
    nova = Path(ssd) / PASTA_SISTEMA
    antiga = Path(ssd) / PASTA_SISTEMA_LEGADA
    if antiga.is_dir() and not nova.exists():
        os.replace(antiga, nova)
        return True
    return False


def migrar_nomes_antigos():
    """Até a 2.2 o programa se chamava guarda_ssd. Leva a pasta local e o arquivo de
    configuração para os nomes novos, sem sobrescrever nada. -> avisos para a tela."""
    avisos = []
    if v.migrar_pasta(PASTA_LOCAL_LEGADA, PASTA_LOCAL):
        avisos.append(f"A pasta local {PASTA_LOCAL_LEGADA.name} agora se chama {PASTA_LOCAL.name}.")
    for velho, novo in ((CONFIG_LEGADO, CONFIG_PADRAO),
                        (PASTA_LOCAL / CONFIG_LEGADO.name, PASTA_LOCAL / CONFIG_PADRAO.name)):
        if velho.exists() and not novo.exists():
            try:
                os.replace(velho, novo)
                avisos.append(f"O arquivo {velho.name} agora se chama {novo.name}.")
            except OSError:
                pass
    return avisos


def localizar_ssd(cfg):
    """Procura em todas as letras o volume com serial e identidade corretos."""
    for raiz in unidades():
        serial, _ = info_volume(raiz)
        if serial != cfg["serial"]:
            continue
        for pasta in (PASTA_SISTEMA, PASTA_SISTEMA_LEGADA):
            try:
                if cfg["id"] in (Path(raiz) / pasta / ARQ_IDENTIDADE).read_text(encoding="utf-8"):
                    return Path(raiz)
            except OSError:
                pass
        log(f"AVISO: {raiz} tem o serial certo mas não tem a identidade. Ignorado.")
    return None


def registrar(letra, caminho_config, forcar):
    if not WINDOWS:
        sys.exit("--registrar só funciona no Windows.")
    raiz = letra.rstrip(":\\/").upper() + ":\\"
    serial, fs = info_volume(raiz)
    if not serial:
        sys.exit(f"Não consegui ler o volume {raiz}.")
    if caminho_config.exists() and not forcar:
        cfg = carregar_config(caminho_config)
        if cfg.get("serial"):
            sys.exit(f"Já existe um SSD registrado em {caminho_config}.\n"
                     "Para trocar o disco de destino, rode de novo com --forcar.")
    migrar_pasta_sistema(raiz)
    ident = Path(raiz) / PASTA_SISTEMA / ARQ_IDENTIDADE
    ident.parent.mkdir(exist_ok=True)
    id_ = str(uuid.uuid4())
    ident.write_text(
        "Este disco é o destino de backup do mnemosine.py.\n"
        "Não apague este arquivo nem esta pasta.\n"
        f"id={id_}\nserial={serial}\nsistema_de_arquivos={fs}\n"
        f"registrado={datetime.now():%Y-%m-%d %H:%M}\n",
        encoding="utf-8")
    cfg = carregar_config(caminho_config) if caminho_config.exists() else dict(PADRAO)
    cfg["serial"], cfg["id"] = serial, id_
    salvar_config(caminho_config, cfg)
    print(f"SSD registrado: {raiz} (serial {serial}, {fs}).\nConfig salva em {caminho_config}")
    if fs and fs.upper().startswith("FAT"):
        print("AVISO: este disco está em FAT32. Arquivos acima de 4 GB vão falhar e as datas\n"
              "têm precisão de 2 s. Prefira formatar como exFAT ou NTFS antes de usar.")


# ---------------------------------------------------------------- config
def resolver_config(caminho_arg):
    if caminho_arg:
        return Path(caminho_arg).expanduser().resolve()
    if CONFIG_PADRAO.exists():
        return CONFIG_PADRAO
    alternativo = PASTA_LOCAL / CONFIG_PADRAO.name
    return alternativo if alternativo.exists() else CONFIG_PADRAO


def carregar_config(caminho):
    cfg = dict(PADRAO)
    cfg.update(json.loads(Path(caminho).read_text(encoding="utf-8")))
    return cfg


def salvar_config(caminho, cfg):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- varredura
def excluidor(padroes):
    padroes = [p.lower() for p in padroes]

    def excluido(nome, rel):
        n, r = nome.lower(), rel.replace("\\", "/").lower()
        return any(fnmatch.fnmatchcase(n, p) or fnmatch.fnmatchcase(r, p) for p in padroes)
    return excluido


def varrer(base, excluido, pular_raiz=(), caminhos_longos=True):
    """{caminho_relativo: (tamanho, mtime, placeholder)} de todos os arquivos sob base."""
    base_s = longo(base, caminhos_longos)
    pular = {p.lower() for p in pular_raiz} | RAIZ_IGNORADA
    arquivos, erros = {}, []
    pilha = [(base_s, True)]
    while pilha:
        pasta, na_raiz = pilha.pop()
        try:
            with os.scandir(pasta) as it:
                for e in it:
                    try:
                        rel = e.path[len(base_s):].lstrip("\\/")
                        if na_raiz and e.name.lower() in pular:
                            continue
                        if excluido(e.name, rel) or e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            pilha.append((e.path, False))
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            placeholder = bool(getattr(st, "st_file_attributes", 0) & ATRIB_PLACEHOLDER)
                            arquivos[rel] = (st.st_size, st.st_mtime, placeholder)
                    except OSError as ex:
                        erros.append(f"{e.path}: {ex}")
        except OSError as ex:
            erros.append(f"{pasta}: {ex}")
    return arquivos, erros


def comparar(origem, destino, tolerancia):
    """-> (novos, alterados, orfaos), listas ordenadas de caminhos relativos."""
    novos, alterados = [], []
    for rel, (tam, mt, _) in origem.items():
        d = destino.get(rel)
        if d is None:
            novos.append(rel)
        elif d[0] != tam or abs(d[1] - mt) > tolerancia:
            alterados.append(rel)
    orfaos = sorted(set(destino) - set(origem))
    return sorted(novos), sorted(alterados), orfaos


def detectar_movidos(novos, orfaos, arq_o, arq_d, tolerancia, confirmar=None):
    """Casa cada 'novo' com um único órfão de mesmo tamanho e data (dentro da tolerância).

    Se houver mais de um órfão possível, o nome do arquivo desempata. Só aceita
    casamentos 1-para-1 (um novo <-> um órfão). `confirmar(novo, orfao)` pode
    vetar o par (por exemplo comparando um trecho do conteúdo).
    -> {caminho_novo: caminho_antigo_no_ssd}
    """
    por_tamanho = {}
    for r in orfaos:
        por_tamanho.setdefault(arq_d[r][0], []).append(r)
    candidatos = {}                       # órfão -> [novos que casam com ele]
    for r in novos:
        tam, mt = arq_o[r][:2]
        if tam == 0:
            continue
        c = [v for v in por_tamanho.get(tam, ()) if abs(arq_d[v][1] - mt) <= tolerancia]
        if len(c) > 1:
            nome = os.path.basename(r)
            mesmo_nome = [v for v in c if os.path.basename(v) == nome]
            if len(mesmo_nome) == 1:
                c = mesmo_nome
        if len(c) == 1:
            candidatos.setdefault(c[0], []).append(r)
    movidos = {}
    for antigo, lista in candidatos.items():
        if len(lista) == 1 and (confirmar is None or confirmar(lista[0], antigo)):
            movidos[lista[0]] = antigo
    return movidos


def mesmo_conteudo_rapido(a, b, bloco=65536):
    """Compara o início e o fim de dois arquivos. Barato; usado só para confirmar movidos."""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            if fa.read(bloco) != fb.read(bloco):
                return False
            tam = os.fstat(fa.fileno()).st_size
            if tam > bloco:
                fa.seek(tam - bloco)
                fb.seek(tam - bloco)
                return fa.read(bloco) == fb.read(bloco)
            return True
    except OSError:
        return False


# ------------------------------------------------------------------ cópia
def _replace(a, b):
    """os.replace que também vence o atributo somente-leitura do destino."""
    try:
        os.replace(a, b)
    except PermissionError:
        if os.path.exists(b):
            os.chmod(b, stat.S_IWRITE)
        os.replace(a, b)


def copiar(src, dst, antigo=None, caminhos_longos=True):
    """Copia preservando datas. Grava em .sync_tmp e troca, para nunca deixar arquivo
    pela metade. Se `antigo` for dado, a versão anterior de dst é MOVIDA para lá
    (instantâneo, sem segunda cópia) antes da troca."""
    def L(p):
        return longo(p, caminhos_longos)

    os.makedirs(L(dst.parent), exist_ok=True)
    tmp = dst.with_name(dst.name + SUFIXO_TMP)
    shutil.copy2(L(src), L(tmp))
    if os.path.getsize(L(tmp)) != os.path.getsize(L(src)):
        os.remove(L(tmp))
        raise OSError("tamanho diferente após a cópia (arquivo mudou durante o backup?)")
    if antigo is not None and os.path.exists(L(dst)):
        os.makedirs(L(antigo.parent), exist_ok=True)
        _replace(L(dst), L(antigo))
    _replace(L(tmp), L(dst))


def mover(src, dst, caminhos_longos=True):
    os.makedirs(longo(dst.parent, caminhos_longos), exist_ok=True)
    _replace(longo(src, caminhos_longos), longo(dst, caminhos_longos))


def remover_pastas_vazias(pasta, limite, caminhos_longos=True):
    """Sobe a partir de `pasta` apagando só diretórios vazios, parando em `limite`."""
    limite = Path(limite)
    pasta = Path(pasta)
    while pasta != limite and limite in pasta.parents:
        try:
            os.rmdir(longo(pasta, caminhos_longos))
        except OSError:
            return
        pasta = pasta.parent


def sha256(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def tamanho_pasta(pasta):
    total = 0
    for raiz, _, nomes in os.walk(pasta):
        for n in nomes:
            try:
                total += os.stat(os.path.join(raiz, n)).st_size
            except OSError:
                pass
    return total


def _data_da_pasta(p):
    """Data no nome de uma pasta de guarda ('2026-09-29_163650...'), ou None."""
    try:
        return datetime.strptime(p.name[:17], FORMATO_CARIMBO)
    except ValueError:
        return None


def vencidos(pasta_guarda, dias, max_bytes=0, agora=None):
    """Pastas datadas que passaram do prazo de `dias` (pela data no nome) e, se
    `max_bytes` > 0, as mais antigas que estouram o teto. Não apaga nada.
    Pastas sem data no nome não são nossas e nunca entram. -> [Path], mais antigas primeiro."""
    if not pasta_guarda.is_dir():
        return []
    datadas = sorted((d, p) for p in pasta_guarda.iterdir()
                     if p.is_dir() and (d := _data_da_pasta(p)) is not None)
    limite = (agora or datetime.now()) - timedelta(days=dias)
    saem = [p for d, p in datadas if d < limite]
    restantes = [p for d, p in datadas if d >= limite]
    if max_bytes > 0 and restantes:
        tamanhos = {p: tamanho_pasta(p) for p in restantes}
        total = sum(tamanhos.values())
        for p in restantes:           # mais antigas primeiro
            if total <= max_bytes:
                break
            saem.append(p)
            total -= tamanhos[p]
    return saem


def limpar_versoes(pasta_versoes, dias, max_bytes=0, agora=None):
    """Apaga o que `vencidos()` aponta. -> nomes removidos."""
    removidas = []
    for p in vencidos(pasta_versoes, dias, max_bytes, agora):
        shutil.rmtree(p, ignore_errors=True)
        removidas.append(p.name)
    return removidas


# ----------------------------------------------- limpeza do que venceu
POLITICAS = {
    "automatico": "apaga sozinho depois do prazo",
    "perguntar": "pergunta antes de apagar",
    "nunca": "nunca apaga",
}


def politica(cfg):
    """automatico | perguntar | nunca. Valor desconhecido vira 'perguntar' (o mais seguro
    que ainda avisa)."""
    p = str(cfg.get("apagar_vencidos", "automatico")).strip().lower()
    return p if p in POLITICAS else "perguntar"


def descrever_regra(cfg):
    """A regra de limpeza numa frase (começa em minúscula, para vir depois de 'Hoje: ')."""
    dv = v.plural(cfg["dias_versoes"], "dia", "dias")
    dq = v.plural(cfg["dias_quarentena"], "dia", "dias")
    p = politica(cfg)
    if p == "nunca":
        return (f"nunca apaga nada. O prazo serve só de referência: {dv} para as versões antigas "
                f"e {dq} para a quarentena.")
    acao = "apaga sozinho" if p == "automatico" else "pergunta antes de apagar"
    return (f"{acao} o que passar do prazo, que é de {dv} para as versões antigas "
            f"e de {dq} para a quarentena.")


def vencidos_ssd(sistema, cfg, agora=None):
    """O que venceu nas duas pastas de guarda. -> [{tipo, pasta, data, arquivos, bytes}]."""
    itens = []
    for tipo, nome, dias, teto in (
            ("versoes", PASTA_VERSOES, cfg["dias_versoes"], cfg["max_versoes_gb"] * 1024 ** 3),
            ("quarentena", PASTA_QUARENTENA, cfg["dias_quarentena"], 0)):
        for p in vencidos(Path(sistema) / nome, dias, teto, agora):
            arquivos = sum(len(n) for _, _, n in os.walk(p))
            itens.append({"tipo": tipo, "pasta": p, "data": _data_da_pasta(p),
                          "arquivos": arquivos, "bytes": tamanho_pasta(p)})
    return itens


def apagar_vencidos(itens):
    """Apaga as pastas e registra no log. -> (pastas apagadas, bytes)."""
    n = total = 0
    for i in itens:
        shutil.rmtree(i["pasta"], ignore_errors=True)
        if not i["pasta"].exists():
            n += 1
            total += i["bytes"]
            log(f"Pasta de guarda vencida apagada: {i['tipo']}/{i['pasta'].name}")
    return n, total


def _interativo():
    """Há alguém na janela para responder? (falso no agendamento e em pipes)"""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def perguntar(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt, OSError):
        return ""


def _mesclar_pasta(origem, destino):
    """Move o conteúdo de `origem` para dentro de `destino` sem sobrescrever nada.
    Arquivo que já existe no destino fica com o sufixo '.migrado'."""
    for raiz, _, nomes in os.walk(origem):
        alvo_dir = Path(destino) / os.path.relpath(raiz, origem)
        alvo_dir.mkdir(parents=True, exist_ok=True)
        for n in nomes:
            alvo = alvo_dir / n
            if alvo.exists():
                alvo = alvo_dir / (n + ".migrado")
            os.replace(os.path.join(raiz, n), alvo)
    for raiz, _, _ in sorted(os.walk(origem), key=lambda t: len(t[0]), reverse=True):
        try:
            os.rmdir(raiz)
        except OSError:
            pass


def migrar_pasta_legada(sistema):
    """Até a 2.0, versões antigas e quarentena ficavam juntas em <pasta de sistema>/versoes/.
    Não há como separá-las depois; tudo vai para versoes-antigas/. -> pastas migradas."""
    legado = Path(sistema) / PASTA_LEGADA
    if not legado.is_dir():
        return 0
    destino = Path(sistema) / PASTA_VERSOES
    destino.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in list(legado.iterdir()):
        alvo = destino / p.name
        if p.is_dir() and alvo.exists():
            _mesclar_pasta(p, alvo)
        elif not alvo.exists():
            os.replace(p, alvo)
        else:
            os.replace(p, destino / (p.name + ".migrado"))
        n += 1
    try:
        legado.rmdir()
    except OSError:
        pass
    return n


def inventario(pasta, dias, agora=None):
    """{"arquivos", "bytes", "expira"} de uma pasta de guarda (versoes-antigas ou
    quarentena). `expira` é quando a pasta datada mais antiga será removida."""
    arquivos = total = 0
    mais_antiga = None
    pasta = Path(pasta)
    if pasta.is_dir():
        for p in pasta.iterdir():
            if not p.is_dir():
                continue
            try:
                data = datetime.strptime(p.name[:17], FORMATO_CARIMBO)
                mais_antiga = data if mais_antiga is None else min(mais_antiga, data)
            except ValueError:
                pass
            for raiz, _, nomes in os.walk(p):
                for n in nomes:
                    try:
                        total += os.stat(os.path.join(raiz, n)).st_size
                        arquivos += 1
                    except OSError:
                        pass
    expira = mais_antiga + timedelta(days=dias) if mais_antiga else None
    return {"arquivos": arquivos, "bytes": total, "expira": expira}


def somar_inventarios(*invs):
    datas = [i["expira"] for i in invs if i["expira"]]
    return {"arquivos": sum(i["arquivos"] for i in invs), "bytes": sum(i["bytes"] for i in invs),
            "expira": min(datas) if datas else None}


def verificar_hashes(arq_o, arq_d, origem, destino, n, tolerancia, caminhos_longos=True):
    """Compara o SHA-256 de `n` arquivos iguais em ambos os lados (0 = todos)."""
    comuns = [r for r, (tam, mt, ph) in arq_o.items()
              if not ph and r in arq_d and arq_d[r][0] == tam and abs(arq_d[r][1] - mt) <= tolerancia]
    amostra = comuns if n <= 0 or n >= len(comuns) else random.sample(comuns, n)
    divergentes = []
    for r in amostra:
        try:
            if sha256(longo(origem / r, caminhos_longos)) != sha256(longo(destino / r, caminhos_longos)):
                divergentes.append(r)
        except OSError as ex:
            divergentes.append(f"{r} ({ex})")
    return len(amostra), divergentes


# ------------------------------------------------------------------- lock
class Lock:
    def __init__(self, caminho, horas_velho):
        self.caminho = Path(caminho)
        self.horas_velho = horas_velho
        self.meu = False

    def adquirir(self):
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.caminho.exists() and time.time() - self.caminho.stat().st_mtime > self.horas_velho * 3600:
                self.caminho.unlink()          # lock abandonado
            fd = os.open(self.caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError:
            return True                        # sem lock não é motivo para não fazer backup
        with os.fdopen(fd, "w") as f:
            f.write(f"{os.getpid()}\n")
        self.meu = True
        return True

    def liberar(self):
        if self.meu:
            try:
                self.caminho.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------ estado
def ler_estado(caminho):
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def gravar_estado(estado, *destinos):
    for arq in destinos:
        try:
            arq.parent.mkdir(parents=True, exist_ok=True)
            arq.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def mostrar_status():
    est = ler_estado(ESTADO_LOCAL)
    if not est:
        print(f"Nenhuma execução registrada ainda ({ESTADO_LOCAL}).")
        return 0
    print(f"Última execução : {est.get('inicio')}  ({'simulação' if est.get('simulacao') else 'real'})")
    print(f"Resultado       : {est.get('codigo')} - {CODIGOS.get(est.get('codigo'), '?')}")
    print(f"Duração         : {fmt_duracao(est.get('duracao_seg', 0))}")
    for k in ("origem_arquivos", "novos", "alterados", "movidos", "orfaos", "quarentenados",
              "copiados", "falhas", "nao_baixados"):
        if k in est:
            print(f"{k:<16}: {est[k]}")
    if "bytes_copiados" in est:
        print(f"bytes_copiados  : {fmt_bytes(est['bytes_copiados'])}")
    print(f"Último backup OK: {est.get('ultimo_backup') or 'nunca'}")
    return 0


def alertar(titulo, texto):
    log(f"ALERTA: {texto}")
    v.notificar(titulo, texto)


def alertar_se_velho(dias):
    """Avisa (no máximo 1x por dia) se o último backup real tiver mais de `dias` dias."""
    est = ler_estado(ESTADO_LOCAL)
    ultimo = est.get("ultimo_backup")
    agora = datetime.now()
    try:
        idade = agora - datetime.fromisoformat(ultimo) if ultimo else None
    except ValueError:
        idade = None
    if idade is not None and idade < timedelta(days=dias):
        return 0
    ultimo_alerta = est.get("ultimo_alerta")
    try:
        if ultimo_alerta and agora - datetime.fromisoformat(ultimo_alerta) < timedelta(hours=24):
            return 9
    except ValueError:
        pass
    quando = f"há {idade.days} dias ({ultimo[:16]})" if idade is not None else "nunca"
    alertar("Backup do SSD atrasado",
            f"O último backup para o SSD foi feito {quando}.\n"
            f"Plugue o SSD de backup para que o mnemosine.py rode.")
    est["ultimo_alerta"] = agora.isoformat(timespec="seconds")
    gravar_estado(est, ESTADO_LOCAL)
    return 9


# ------------------------------------------------------------------- backup
def novo_resultado():
    """O que aconteceu na execução, preenchido por sincronizar() e lido pela apresentação."""
    return {
        "novos": [], "atualizados": [],          # [(caminho, bytes)]
        "movidos": [],                           # [(de, para)]
        "quarentena": [], "orfaos": [],          # [caminho]
        "falhas": [],                            # [(caminho, erro)]
        "nao_baixados": 0, "bytes_copiados": 0, "a_copiar_bytes": 0,
        "trava_total": 0, "espaco": None,        # espaco = (necessário, margem, livre)
        "verificados": None, "divergentes": [], "migradas": 0,
    }


def sincronizar(a, cfg, ssd, simular, estado, res, tela=None):
    L = cfg["caminhos_longos"]
    tol = cfg["tolerancia_seg"]
    sistema = sistema_de(ssd)
    destino = ssd / cfg["pasta_destino"] if cfg["pasta_destino"] else ssd
    origem = Path(cfg["origem"])

    if not origem.is_dir():
        log(f"Origem {origem} indisponível (Google Drive fechado?). Nada foi feito.")
        return 3

    if not simular:
        try:
            res["migradas"] = migrar_pasta_legada(sistema)
            if res["migradas"]:
                log(f"Pasta '{PASTA_LEGADA}' migrada para '{PASTA_VERSOES}' ({res['migradas']} itens).")
        except OSError as ex:
            log(f"AVISO: não consegui migrar a pasta '{PASTA_LEGADA}': {ex}")

    log(f"Início{' (SIMULAÇÃO)' if simular else ''}: {origem} -> {destino}  [mnemosine {VERSAO}]")
    excluido = excluidor(cfg["excluir"])
    arq_o, erros_o = varrer(origem, excluido, caminhos_longos=L)
    arq_d, erros_d = varrer(destino, excluido, pular_raiz=[PASTA_SISTEMA, PASTA_SISTEMA_LEGADA], caminhos_longos=L)
    for e in erros_o:
        log(f"ERRO leitura origem: {e}")
    for e in erros_d:
        log(f"ERRO leitura destino: {e}")
    if not arq_o:
        log("Origem vazia. Abortado por segurança.")
        return 4

    novos, alterados, orfaos = comparar(arq_o, arq_d, tol)

    # Arquivos que o Drive ainda não baixou (modo stream): copiar forçaria o download.
    nao_baixados = [r for r in novos + alterados if arq_o[r][2]]
    if nao_baixados and not a.baixar:
        pular = set(nao_baixados)
        novos = [r for r in novos if r not in pular]
        alterados = [r for r in alterados if r not in pular]
        log(f"AVISO: {len(nao_baixados)} arquivos ainda não baixados pelo Google Drive foram "
            "pulados. Marque a pasta como 'Disponível offline' ou rode com --baixar.")
    estado["nao_baixados"] = res["nao_baixados"] = len(nao_baixados) if not a.baixar else 0

    # Movidos/renomeados: mover dentro do SSD em vez de copiar de novo.
    movidos = detectar_movidos(
        novos, orfaos, arq_o, arq_d, tol,
        confirmar=lambda n, v_: mesmo_conteudo_rapido(longo(origem / n, L), longo(destino / v_, L)))
    if movidos:
        novos = [r for r in novos if r not in movidos]
        antigos = set(movidos.values())
        orfaos = [r for r in orfaos if r not in antigos]

    log(f"Origem: {len(arq_o)} arquivos | novos: {len(novos)} | alterados: {len(alterados)} | "
        f"movidos: {len(movidos)} | órfãos: {len(orfaos)}")
    estado.update(origem_arquivos=len(arq_o), novos=len(novos), alterados=len(alterados),
                  movidos=len(movidos), orfaos=len(orfaos))
    res["orfaos"] = list(orfaos)

    if a.orfaos:
        for r in orfaos:
            log(f"  ORFAO    {r}")
        return 0

    total = len(novos) + len(alterados) + (len(orfaos) if a.quarentena else 0)
    res["trava_total"] = total
    trava = total > cfg["limite_abs"] and total > cfg["limite_pct"] * len(arq_o)
    if trava and not a.forcar:
        msg = (f"{total} arquivos mudariam de uma vez ({total / len(arq_o):.0%} do total). "
               "Verifique o Drive (ransomware? pasta movida? exclusão em massa?) e, se estiver "
               "tudo certo, rode com --forcar.")
        if simular:
            log(f"AVISO: a trava de segurança seria acionada: {msg}")
        else:
            log(f"TRAVA DE SEGURANÇA: {msg} Nada foi copiado.")
            return 5

    necessario = sum(arq_o[r][0] for r in novos + alterados)
    res["a_copiar_bytes"] = necessario
    if not simular:
        livre = shutil.disk_usage(str(ssd)).free
        margem = cfg["margem_espaco_gb"] * 1024 ** 3
        if necessario + margem > livre:
            res["espaco"] = (necessario, margem, livre)
            log(f"ESPAÇO INSUFICIENTE: precisa de {fmt_bytes(necessario)} + margem de "
                f"{fmt_bytes(margem)}, mas só há {fmt_bytes(livre)} livres. Nada foi copiado.")
            return 6

    carimbo = f"{datetime.now():{FORMATO_CARIMBO}}"
    pasta_versao = sistema / PASTA_VERSOES / carimbo
    pasta_quarentena = sistema / PASTA_QUARENTENA / carimbo
    ok = falhas = quarentenados = 0
    bytes_copiados = 0
    copiados = []

    for novo, antigo in sorted(movidos.items()):
        log(f"  MOVIDO   {antigo}  ->  {novo}")
        if simular:
            res["movidos"].append((antigo, novo))
            continue
        try:
            mover(destino / antigo, destino / novo, L)
            remover_pastas_vazias((destino / antigo).parent, destino, L)
            arq_d[novo] = arq_d.pop(antigo)
            res["movidos"].append((antigo, novo))
        except OSError as ex:
            log(f"  ERRO     mover {antigo}: {ex}. Vai copiar em vez de mover.")
            novos.append(novo)
            orfaos.append(antigo)

    lista = sorted(novos) + alterados
    prog = None
    if tela is not None and not simular and (len(lista) > 50 or necessario > 100 * 1024 ** 2):
        prog = v.Progresso(tela, len(lista), necessario)
    try:
        for rel in lista:
            tipo = "ALTERADO" if rel in arq_d else "NOVO"
            chave = "atualizados" if tipo == "ALTERADO" else "novos"
            log(f"  {tipo:8} {rel}")
            if simular:
                res[chave].append((rel, arq_o[rel][0]))
                continue
            src, dst = origem / rel, destino / rel
            try:
                copiar(src, dst, antigo=(pasta_versao / rel) if tipo == "ALTERADO" else None, caminhos_longos=L)
                ok += 1
                bytes_copiados += arq_o[rel][0]
                copiados.append((arq_o[rel][0], rel))
                res[chave].append((rel, arq_o[rel][0]))
                arq_d[rel] = arq_o[rel]
            except OSError as ex:
                falhas += 1
                res["falhas"].append((rel, str(ex)))
                log(f"  ERRO     {rel}: {ex}")
            if prog:
                prog.avancar(arq_o[rel][0])
    finally:
        if prog:
            prog.fim()

    if a.quarentena:
        for rel in sorted(orfaos):
            log(f"  QUARENT. {rel}")
            if simular:
                res["quarentena"].append(rel)
                continue
            try:
                mover(destino / rel, pasta_quarentena / rel, L)
                remover_pastas_vazias((destino / rel).parent, destino, L)
                quarentenados += 1
                res["quarentena"].append(rel)
            except OSError as ex:
                falhas += 1
                res["falhas"].append((rel, str(ex)))
                log(f"  ERRO     quarentena {rel}: {ex}")

    codigo = 0
    if not simular:
        if politica(cfg) == "automatico":
            apagar_vencidos(vencidos_ssd(sistema, cfg))
        if a.verificar is not None:
            n, divergentes = verificar_hashes(arq_o, arq_d, origem, destino, a.verificar, tol, L)
            for r in divergentes:
                log(f"  DIVERGE  {r}")
            log(f"Verificação: {n} arquivos conferidos por hash, {len(divergentes)} divergentes.")
            estado.update(verificados=n, divergentes=len(divergentes))
            res["verificados"], res["divergentes"] = n, divergentes
            if divergentes:
                codigo = 8
        if len(copiados) > 1:
            log("Maiores arquivos copiados:")
            for tam, rel in sorted(copiados, reverse=True)[:10]:
                log(f"  {fmt_bytes(tam):>10}  {rel}")
        log(f"Fim: {ok} copiados ({fmt_bytes(bytes_copiados)}), {len(res['movidos'])} movidos, "
            f"{quarentenados} em quarentena, {falhas} falhas.")

    res["bytes_copiados"] = bytes_copiados
    estado.update(copiados=ok, falhas=falhas, quarentenados=quarentenados, bytes_copiados=bytes_copiados)
    if codigo == 0 and falhas:
        codigo = 1
    return codigo


def contexto(a, cfg):
    """Tudo o que a apresentação precisa saber sobre a execução."""
    return {
        "codigo": 11, "cfg": cfg, "ssd": None, "res": novo_resultado(), "inventario": None,
        "inicio": datetime.now(), "duracao": 0.0, "ultimo_backup": None,
        "simular": bool(a.simular or a.orfaos), "modo_orfaos": bool(a.orfaos),
        "quarentena_ativa": bool(a.quarentena), "baixar": bool(a.baixar),
        "politica": politica(cfg), "vencidos": [], "vai_perguntar": False,
        "avisos": [],                     # renomeações de versões anteriores, para a tela
    }


def _data_ultimo_backup(estado):
    try:
        return datetime.fromisoformat(estado["ultimo_backup"])
    except (KeyError, TypeError, ValueError):
        return None


def executar(a, cfg, tela=None):
    ctx = contexto(a, cfg)
    inicio = time.time()
    simular = ctx["simular"]
    estado = ler_estado(ESTADO_LOCAL)
    ctx["ultimo_backup"] = _data_ultimo_backup(estado)

    ssd = localizar_ssd(cfg)
    if not ssd:
        log("SSD de backup não encontrado. Nada foi feito.")
        codigo = 2
        if a.alertar_se_velho:
            codigo = alertar_se_velho(a.alertar_se_velho) or 2
        log.gravar(LOG_LOCAL)
        ctx.update(codigo=codigo, duracao=time.time() - inicio)
        return ctx
    ctx["ssd"] = ssd
    if not simular:
        try:
            if migrar_pasta_sistema(ssd):
                msg = f"A pasta {PASTA_SISTEMA_LEGADA} no SSD agora se chama {PASTA_SISTEMA}."
                log(msg)
                ctx["avisos"].append(msg)
        except OSError as ex:
            log(f"AVISO: não consegui renomear {PASTA_SISTEMA_LEGADA} para {PASTA_SISTEMA}: {ex}")
    # O SSD também guarda o último resultado; vale o mais recente dos dois
    # (o estado local pode ser de outro computador, ou ter sido apagado).
    datas = [d for d in (ctx["ultimo_backup"],
                         _data_ultimo_backup(ler_estado(sistema_de(ssd) / ARQ_ESTADO))) if d]
    ctx["ultimo_backup"] = max(datas) if datas else None

    estado.update(inicio=datetime.now().isoformat(timespec="seconds"), simulacao=simular, codigo=None)
    for k in ("origem_arquivos", "novos", "alterados", "movidos", "orfaos", "quarentenados",
              "copiados", "falhas", "nao_baixados", "bytes_copiados", "verificados", "divergentes"):
        estado.pop(k, None)
    codigo = 11
    try:
        codigo = sincronizar(a, cfg, ssd, simular, estado, ctx["res"], tela)
    except Exception:      # noqa: BLE001 - registrar no log, não morrer em silêncio no pythonw
        log("ERRO INESPERADO:\n" + traceback.format_exc())
    finally:
        estado.update(fim=datetime.now().isoformat(timespec="seconds"),
                      duracao_seg=round(time.time() - inicio, 1), codigo=codigo)
        if not simular and codigo in (0, 1, 8):
            estado["ultimo_backup"] = estado["fim"]
        destinos_estado = [ESTADO_LOCAL] + ([] if simular else [sistema_de(ssd) / ARQ_ESTADO])
        gravar_estado(estado, *destinos_estado)
        log_ssd = sistema_de(ssd) / "logs" / f"{datetime.now():%Y-%m}.log"
        log.gravar(LOG_LOCAL, *([] if simular else [log_ssd]))
        sistema = sistema_de(ssd)
        ctx["inventario"] = {       # a pasta legada só existe até a primeira execução real
            "versoes": somar_inventarios(inventario(sistema / PASTA_VERSOES, cfg["dias_versoes"]),
                                         inventario(sistema / PASTA_LEGADA, cfg["dias_versoes"])),
            "quarentena": inventario(sistema / PASTA_QUARENTENA, cfg["dias_quarentena"]),
        }
        try:
            ctx["vencidos"] = vencidos_ssd(sistema, cfg)
        except OSError:
            ctx["vencidos"] = []
        ctx.update(codigo=codigo, duracao=time.time() - inicio)
    return ctx


# --------------------------------------------------------------- apresentação
FRASES = {
    1: "Backup feito, mas {falhas} não puderam ser copiados. Em geral é arquivo aberto: "
       "feche o programa e rode de novo.",
    2: "SSD de backup não encontrado. Ele está plugado?",
    3: "A origem ({origem}) não está disponível. O Google Drive está aberto?",
    4: "A origem está vazia. Nada foi feito, por segurança.",
    5: "Trava de segurança: {trava} mudariam de uma vez. Nada foi copiado. Se foi você "
       "(reorganizou pastas, por exemplo), rode com --forcar.",
    6: "Espaço insuficiente no SSD: faltam {falta}. Nada foi copiado.",
    7: "Já há um backup em andamento. Espere ele terminar.",
    8: "A verificação de conteúdo encontrou {divergentes} diferentes do Drive.",
    9: "O último backup está velho demais. Plugue o SSD de backup.",
    10: "O SSD ainda não foi registrado. Rode: python mnemosine.py --registrar D:",
    11: "Erro inesperado. Detalhes no log: {log}",
}
NOTIFICAR = {1, 3, 4, 5, 6, 8, 11}     # códigos que viram notificação no modo --silencioso


def severidade(codigo):
    if codigo == 0:
        return "ok"
    return "aviso" if codigo in (1, 8, 9) else "erro"


def frase(ctx):
    """O resultado em uma frase, em português, sem código numérico."""
    codigo, res = ctx["codigo"], ctx["res"]
    if codigo == 0:
        if ctx["modo_orfaos"]:
            n = len(res["orfaos"])
            return (v.plural(n, "arquivo saiu", "arquivos saíram") + " do Drive e continua(m) no SSD."
                    if n else "Nenhum arquivo saiu do Drive.")
        copiar = len(res["novos"]) + len(res["atualizados"])
        if ctx["simular"]:
            partes = []
            if copiar:
                partes.append(v.plural(copiar, "arquivo seria copiado", "arquivos seriam copiados")
                              + f" ({v.fmt_bytes(res['a_copiar_bytes'])})")
            if res["movidos"]:
                partes.append(v.plural(len(res["movidos"]), "seria movido", "seriam movidos"))
            if res["quarentena"]:
                partes.append(v.plural(len(res["quarentena"]), "iria", "iriam") + " para a quarentena")
            return "Simulação: " + (", ".join(partes) if partes else "não haveria nada a fazer") + "."
        partes = []
        if copiar:
            partes.append(v.plural(copiar, "copiado", "copiados") + f" ({v.fmt_bytes(res['bytes_copiados'])})")
        if res["movidos"]:
            partes.append(v.plural(len(res["movidos"]), "movido", "movidos"))
        if res["quarentena"]:
            partes.append(v.plural(len(res["quarentena"]), "para a quarentena", "para a quarentena"))
        if not partes:
            return "Tudo certo · nada mudou desde o último backup"
        return f"Tudo certo · {', '.join(partes)} em {v.fmt_duracao(ctx['duracao'])}"
    necessario, margem, livre = res["espaco"] or (0, 0, 0)
    return FRASES.get(codigo, "Erro desconhecido.").format(
        falhas=v.plural(len(res["falhas"]), "arquivo", "arquivos"),
        origem=ctx["cfg"].get("origem", "?"),
        trava=v.plural(res["trava_total"], "arquivo", "arquivos"),
        falta=v.fmt_bytes(max(necessario + margem - livre, 0)),
        divergentes=v.plural(len(res["divergentes"]), "arquivo", "arquivos"),
        log=LOG_LOCAL)


def _inventario_json(inv):
    if not inv:
        return None
    return {k: {"arquivos": i["arquivos"], "bytes": i["bytes"],
                "expira": i["expira"].isoformat(timespec="seconds") if i["expira"] else None}
            for k, i in inv.items()}


def saida_json(ctx):
    res, ssd = ctx["res"], ctx["ssd"]
    return {
        "programa": "mnemosine", "versao": VERSAO,
        "codigo": ctx["codigo"], "resultado": severidade(ctx["codigo"]), "mensagem": frase(ctx),
        "simulacao": ctx["simular"],
        "inicio": ctx["inicio"].isoformat(timespec="seconds"), "duracao_seg": round(ctx["duracao"], 2),
        "origem": ctx["cfg"].get("origem"),
        "ssd": {"raiz": str(ssd), "nome": v.nome_ssd(ssd)} if ssd else None,
        "novos": [{"caminho": r, "bytes": b} for r, b in res["novos"]],
        "atualizados": [{"caminho": r, "bytes": b} for r, b in res["atualizados"]],
        "movidos": [{"de": de, "para": para} for de, para in res["movidos"]],
        "quarentena": res["quarentena"],
        "orfaos": res["orfaos"],
        "falhas": [{"caminho": r, "erro": e} for r, e in res["falhas"]],
        "nao_baixados": res["nao_baixados"],
        "bytes_copiados": res["bytes_copiados"],
        "verificacao": (None if res["verificados"] is None
                        else {"conferidos": res["verificados"], "divergentes": res["divergentes"]}),
        "inventario": _inventario_json(ctx["inventario"]),
        "politica_limpeza": ctx["politica"],
        "vencidos": [{"tipo": i["tipo"], "pasta": i["pasta"].name,
                      "guardada_em": i["data"].isoformat(timespec="seconds"),
                      "arquivos": i["arquivos"], "bytes": i["bytes"]} for i in ctx["vencidos"]],
    }


def registro_historico(ctx):
    res = ctx["res"]
    return {
        "tipo": "backup", "quando": ctx["inicio"].isoformat(timespec="seconds"),
        "codigo": ctx["codigo"], "resultado": severidade(ctx["codigo"]), "frase": frase(ctx),
        "novos": len(res["novos"]), "atualizados": len(res["atualizados"]),
        "movidos": len(res["movidos"]), "quarentena": len(res["quarentena"]),
        "falhas": len(res["falhas"]), "bytes": res["bytes_copiados"],
        "duracao_seg": round(ctx["duracao"], 1),
        "inventario": _inventario_json(ctx["inventario"]),
        "vencidos": len(ctx["vencidos"]), "politica_limpeza": ctx["politica"],
    }


def _era(de, para):
    """Texto curto para um arquivo movido: o nome antigo, ou a pasta de onde veio."""
    if os.path.dirname(de) == os.path.dirname(para):
        return f"era {os.path.basename(de)}"
    return f"veio de {os.path.dirname(de) or '(raiz)'}"


def mostrar_tela(e, ctx):
    res, cfg = ctx["res"], ctx["cfg"]
    origem = Path(cfg.get("origem", "")).name or cfg.get("origem", "origem")
    destino = v.nome_ssd(ctx["ssd"]) if ctx["ssd"] else "SSD de backup"
    if ctx["modo_orfaos"]:
        titulo = "ARQUIVOS QUE SAÍRAM DO DRIVE"
    elif ctx["simular"]:
        titulo = "SIMULAÇÃO DO BACKUP"
    else:
        titulo = "BACKUP"

    e.escrever()
    e.escrever("  " + e.c(f"{titulo}  {origem} {e.s['seta']} {destino}", "negrito"))
    e.escrever("  " + e.c(f"{v.fmt_data_hora(ctx['inicio'])} {e.s['ponto']} último backup "
                          f"{v.ha_quanto(ctx['ultimo_backup'], ctx['inicio'])}", "cinza"))
    if ctx["simular"] and not ctx["modo_orfaos"]:
        e.escrever("  " + e.c("Nada é copiado na simulação: isto mostra o que o backup faria.", "cinza"))
    e.escrever()

    def com_tamanho(itens):
        return [(r, v.fmt_bytes(b)) for r, b in sorted(itens, key=lambda t: -t[1])]

    v.secao(e, "novo", "Novos", com_tamanho(res["novos"]), "verde")
    v.secao(e, "atualizado", "Atualizados", com_tamanho(res["atualizados"]), "ciano",
            nota=f"versão anterior guardada por {cfg['dias_versoes']} dias")
    v.secao(e, "movido", "Movidos ou renomeados", [(p, _era(d, p)) for d, p in res["movidos"]], "ciano",
            nota="sem copiar de novo")
    v.secao(e, "fora", "Tirados do espelho", [(r, "") for r in res["quarentena"]], "amarelo",
            nota=f"saíram do Drive {e.s['ponto']} ficam na quarentena por {cfg['dias_quarentena']} dias")
    if ctx["modo_orfaos"]:
        v.secao(e, "fora", "Saíram do Drive", [(r, "") for r in res["orfaos"]], "amarelo",
                nota="continuam no SSD")
    v.secao(e, "falha", "Não copiados", [(r, m[:60]) for r, m in res["falhas"]], "vermelho")
    v.secao(e, "falha", "Conteúdo diferente do Drive", [(r, "") for r in res["divergentes"]], "vermelho")

    v.separador(e)
    v.linha_final(e, severidade(ctx["codigo"]), frase(ctx))
    notas = []
    if res["nao_baixados"]:
        notas.append(f"{v.plural(res['nao_baixados'], 'arquivo ainda não baixado', 'arquivos ainda não baixados')}"
                     " pelo Google Drive ficou para depois (marque a pasta como 'Disponível offline').")
    if res["orfaos"] and not ctx["quarentena_ativa"] and not ctx["modo_orfaos"]:
        notas.append(f"{v.plural(len(res['orfaos']), 'arquivo saiu', 'arquivos saíram')} do Drive e "
                     "continua(m) no SSD. Para tirá-los do espelho: --quarentena.")
    if res["verificados"] is not None and not res["divergentes"]:
        notas.append(f"Conteúdo conferido byte a byte em {v.plural(res['verificados'], 'arquivo', 'arquivos')}: idêntico.")
    if res["migradas"]:
        notas.append(f"A pasta '{PASTA_LEGADA}' agora se chama '{PASTA_VERSOES}'.")
    notas.extend(ctx["avisos"])
    for n in notas:
        e.escrever("  " + e.c(f"{e.s['info']} {n}", "cinza"))

    if ctx["inventario"]:
        e.escrever()
        e.escrever("  " + e.c("Guardados no SSD", "negrito"))
        for chave, rotulo in (("versoes", "Versões antigas"), ("quarentena", "Quarentena")):
            i = ctx["inventario"][chave]
            if i["arquivos"]:
                txt = f"{v.plural(i['arquivos'], 'arquivo', 'arquivos')} {e.s['ponto']} {v.fmt_bytes(i['bytes'])}"
                if i["expira"]:
                    txt += f" {e.s['ponto']} a mais antiga sai em {v.fmt_data(i['expira'])}"
            else:
                txt = "vazia"
            e.escrever(f"    {rotulo:<17} {e.c(txt, 'cinza')}")
        venc = ctx["vencidos"]
        if venc and not ctx["vai_perguntar"]:
            qtd = (v.plural(len(venc), "pasta passou", "pastas passaram")
                   + f" do prazo ({v.fmt_bytes(sum(i['bytes'] for i in venc))})")
            if ctx["politica"] == "nunca":
                msg = f"{qtd} {e.s['ponto']} limpeza automática desligada; para limpar: Limpar guardados.bat"
            elif ctx["politica"] == "automatico":
                msg = f"{qtd} e será(ão) apagada(s) no próximo backup"
            else:
                msg = f"{qtd} e aguardam sua autorização (Limpar guardados.bat ou o próximo Fazer backup)"
            e.escrever("    " + e.c(f"{e.s['aviso']} {msg}", "amarelo"))
    e.escrever()


def descrever_vencidos(e, itens):
    for tipo, rotulo in (("versoes", "Versões antigas"), ("quarentena", "Quarentena")):
        grupo = [i for i in itens if i["tipo"] == tipo]
        if not grupo:
            continue
        datas = sorted(v.fmt_data(i["data"]) for i in grupo)
        if len(datas) == 1:
            quando = f"guardada em {datas[0]}"
        elif len(datas) == 2:
            quando = f"guardadas em {datas[0]} e {datas[1]}"
        else:
            quando = f"guardadas de {datas[0]} a {datas[-1]}"
        txt = (f"{v.plural(len(grupo), 'pasta', 'pastas')} {e.s['ponto']} "
               f"{v.plural(sum(i['arquivos'] for i in grupo), 'arquivo', 'arquivos')} {e.s['ponto']} "
               f"{v.fmt_bytes(sum(i['bytes'] for i in grupo))} ({quando})")
        e.escrever(f"      {rotulo:<17} {e.c(txt, 'cinza')}")


def _gravar_log_desde(indice, ssd):
    """Grava nos logs as linhas acrescentadas depois que o log da execução já foi salvo."""
    novas = Log()
    novas.linhas = log.linhas[indice:]
    if novas.linhas:
        novas.gravar(LOG_LOCAL, sistema_de(ssd) / "logs" / f"{datetime.now():%Y-%m}.log")


def perguntar_e_apagar(e, ssd, itens, volta):
    """Mostra o que venceu e pede confirmação. -> True se apagou."""
    e.escrever("  " + e.c(f"{e.s['aviso']} Passaram do prazo e aguardam sua autorização:", "amarelo", "negrito"))
    descrever_vencidos(e, itens)
    e.escrever()
    resposta = perguntar("  Apagar agora? [S/N] ").strip().lower()
    if resposta not in ("s", "sim", "y", "yes"):
        resto = " A pergunta volta no próximo backup." if volta else ""
        e.escrever("  " + e.c(f"{e.s['info']} Nada foi apagado.{resto}", "cinza"))
        e.escrever()
        return False
    inicio = len(log.linhas)
    n, total = apagar_vencidos(itens)
    _gravar_log_desde(inicio, ssd)
    texto = f"Apagado: {v.plural(n, 'pasta', 'pastas')} ({v.fmt_bytes(total)})"
    v.linha_final(e, "ok" if n == len(itens) else "aviso",
                  texto if n == len(itens) else f"{texto}; {len(itens) - n} não puderam ser apagadas")
    e.escrever()
    v.registrar_historico(sistema_de(ssd), {
        "tipo": "limpeza", "quando": datetime.now().isoformat(timespec="seconds"), "codigo": 0,
        "resultado": "ok" if n == len(itens) else "aviso", "frase": texto, "pastas": n, "bytes": total,
    }, copias_html=[PASTA_LOCAL])
    return True


def avisar_vencidos(ctx):
    """Agendamento com 'perguntar': não apaga; notifica no máximo uma vez por dia."""
    est = ler_estado(ESTADO_LOCAL)
    agora = datetime.now()
    try:
        if agora - datetime.fromisoformat(est["ultimo_aviso_vencidos"]) < timedelta(hours=24):
            return
    except (KeyError, TypeError, ValueError):
        pass
    n = len(ctx["vencidos"])
    v.notificar("Backup do SSD",
                f"Itens guardados no SSD venceram o prazo ({v.plural(n, 'pasta', 'pastas')}). "
                "Abra o Fazer backup ou o Limpar guardados para decidir.")
    est["ultimo_aviso_vencidos"] = agora.isoformat(timespec="seconds")
    gravar_estado(est, ESTADO_LOCAL)


def comando_limpar(cfg, e):
    """--limpar: o que venceu, com confirmação, sem fazer backup."""
    ssd = localizar_ssd(cfg)
    if not ssd:
        e.escrever()
        v.linha_final(e, "erro", FRASES[2])
        e.escrever()
        return 2
    lock = Lock(LOCK, cfg["horas_lock_velho"])
    if not lock.adquirir():
        e.escrever()
        v.linha_final(e, "erro", FRASES[7])
        e.escrever()
        return 7
    try:
        try:
            migrar_pasta_sistema(ssd)
        except OSError:
            pass
        sistema = sistema_de(ssd)
        itens = vencidos_ssd(sistema, cfg)
        e.escrever()
        e.escrever("  " + e.c(f"LIMPEZA DO QUE ESTÁ GUARDADO  {v.nome_ssd(ssd)}", "negrito"))
        e.escrever("  " + e.c(f"Hoje: {descrever_regra(cfg)}", "cinza"))
        e.escrever()
        if not itens:
            invs = [inventario(sistema / PASTA_VERSOES, cfg["dias_versoes"]),
                    inventario(sistema / PASTA_QUARENTENA, cfg["dias_quarentena"])]
            datas = [i["expira"] for i in invs if i["expira"]]
            v.linha_final(e, "ok", "Nada passou do prazo.")
            prox = (f"O próximo vencimento é em {v.fmt_data(min(datas))}." if datas
                    else "Não há nada guardado nas pastas de versões antigas e quarentena.")
            e.escrever("  " + e.c(f"{e.s['info']} {prox}", "cinza"))
            e.escrever()
            return 0
        if not _interativo():
            e.escrever("  " + e.c(f"{e.s['aviso']} Passaram do prazo:", "amarelo"))
            descrever_vencidos(e, itens)
            e.escrever("  " + e.c(f"{e.s['info']} Nada foi apagado: para confirmar, use o Limpar guardados.bat.", "cinza"))
            e.escrever()
            return 0
        perguntar_e_apagar(e, ssd, itens, volta=False)
        return 0
    finally:
        lock.liberar()


ROTULOS_POLITICA = {
    "automatico": "Apagar sozinho (automático)",
    "perguntar": "Perguntar antes de apagar",
    "nunca": "Nunca apagar",
}


def configurar_limpeza(caminho_config, e):
    """--configurar-limpeza: menu para escolher a política e os prazos."""
    if not caminho_config.exists():
        e.escrever()
        v.linha_final(e, "erro", FRASES[10])
        e.escrever()
        return 10
    bruto = json.loads(caminho_config.read_text(encoding="utf-8"))
    cfg = dict(PADRAO)
    cfg.update(bruto)
    atual = politica(cfg)
    e.escrever()
    e.escrever("  " + e.c("LIMPEZA DO QUE ESTÁ GUARDADO NO SSD", "negrito"))
    e.escrever("  " + e.c(f"Hoje: {descrever_regra(cfg)}", "cinza"))
    e.escrever()
    if not _interativo():
        e.escrever("  " + e.c(f"{e.s['info']} Para mudar, dê dois cliques em Configurar limpeza.bat.", "cinza"))
        e.escrever()
        return 0

    opcoes = list(POLITICAS)
    e.escrever("  O que fazer com o que passar do prazo?")
    for n, chave in enumerate(opcoes, 1):
        marca = e.c("  (atual)", "cinza") if chave == atual else ""
        e.escrever(f"    {n}  {ROTULOS_POLITICA[chave]}{marca}")
    e.escrever()
    while True:
        r = perguntar("  Escolha [1/2/3, Enter mantém]: ").strip()
        if r == "":
            nova = atual
            break
        if r in ("1", "2", "3"):
            nova = opcoes[int(r) - 1]
            break
        e.escrever("  Digite 1, 2 ou 3.")

    def pedir_dias(rotulo, chave):
        while True:
            r = perguntar(f"  Prazo {rotulo}, em dias [{cfg[chave]}]: ").strip()
            if r == "":
                return None
            if r.isdigit() and int(r) >= 1:
                return int(r)
            e.escrever("  Digite um número de dias (1 ou mais), ou Enter para manter.")

    dias_v = pedir_dias("das versões antigas", "dias_versoes")
    dias_q = pedir_dias("da quarentena", "dias_quarentena")
    bruto["apagar_vencidos"] = nova
    if dias_v:
        bruto["dias_versoes"] = dias_v
    if dias_q:
        bruto["dias_quarentena"] = dias_q
    salvar_config(caminho_config, bruto)
    novo = dict(cfg, apagar_vencidos=nova, dias_versoes=dias_v or cfg["dias_versoes"],
                dias_quarentena=dias_q or cfg["dias_quarentena"])
    e.escrever()
    v.linha_final(e, "ok", "Salvo.")
    e.escrever("  " + e.c(f"A partir de agora, o backup {descrever_regra(novo)}", "cinza"))
    if nova == "perguntar":
        e.escrever("  " + e.c(f"{e.s['info']} No backup agendado nada é apagado: você recebe um aviso para decidir.",
                              "cinza"))
    e.escrever()
    return 0


def testar_notificacao(e):
    ok = v.notificar("Backup do SSD", "Teste: se você está lendo isto, as notificações do backup funcionam.")
    e.escrever()
    if ok:
        v.linha_final(e, "ok", "Notificação enviada.")
        e.escrever("  " + e.c(f"{e.s['info']} Se ela não apareceu no canto da tela, abra a central (Windows + N) e "
                              "confira se o 'Não incomodar' está ligado.", "cinza"))
    else:
        v.linha_final(e, "erro", "Não consegui enviar a notificação.")
    e.escrever()
    return 0 if ok else 1


def abrir_historico(cfg):
    candidatos = []
    ssd = localizar_ssd(cfg) if cfg else None
    if ssd:
        candidatos.append(sistema_de(ssd) / "historico.html")
    candidatos.append(PASTA_LOCAL / "historico.html")
    for p in candidatos:
        if p.exists():
            print(f"Abrindo {p}")
            v.abrir(p)
            return 0
    print("Ainda não há histórico: ele é criado no primeiro backup.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Backup de mão única Google Drive -> SSD específico",
        epilog="Códigos de saída: " + "; ".join(f"{k}={v_}" for k, v_ in CODIGOS.items()))
    ap.add_argument("--registrar", metavar="LETRA", help="registra o SSD nessa letra como destino")
    ap.add_argument("--simular", action="store_true", help="mostra o que faria, sem alterar nada")
    ap.add_argument("--forcar", action="store_true", help="ignora a trava de segurança")
    ap.add_argument("--orfaos", action="store_true", help="só lista o que está no SSD e saiu do Drive")
    ap.add_argument("--quarentena", action="store_true",
                    help=f"move os órfãos para {PASTA_SISTEMA}/{PASTA_QUARENTENA}/ (nunca apaga)")
    ap.add_argument("--verificar", nargs="?", const=200, type=int, metavar="N",
                    help="após o backup, confere o hash de N arquivos (padrão 200; 0 = todos)")
    ap.add_argument("--baixar", action="store_true",
                    help="copia também arquivos que o Drive ainda não baixou (força download)")
    ap.add_argument("--status", action="store_true", help="mostra o resultado da última execução")
    ap.add_argument("--historico", action="store_true", help="abre o histórico de execuções no navegador")
    ap.add_argument("--limpar", action="store_true",
                    help="mostra o que venceu nas pastas de guarda e pergunta se apaga (sem fazer backup)")
    ap.add_argument("--configurar-limpeza", action="store_true",
                    help="escolhe se o que vence é apagado sozinho, com autorização ou nunca")
    ap.add_argument("--testar-notificacao", action="store_true", help="envia uma notificação de teste")
    ap.add_argument("--alertar-se-velho", type=int, metavar="DIAS",
                    help="se o SSD não estiver presente e o último backup tiver mais de DIAS dias, avisa")
    ap.add_argument("--config", metavar="ARQUIVO", help="caminho do mnemosine_config.json")
    saida = ap.add_mutually_exclusive_group()
    saida.add_argument("--detalhado", action="store_true", help="saída técnica, hora em cada linha")
    saida.add_argument("--silencioso", action="store_true",
                       help="nada na tela; notificação do Windows se houver problema")
    saida.add_argument("--json", action="store_true", help="resultado em JSON")
    ap.add_argument("--versao", action="version", version=f"mnemosine {VERSAO}")
    a = ap.parse_args(argv)

    modo = "json" if a.json else "silencioso" if a.silencioso else "detalhado" if a.detalhado else "tela"
    log.ecoar = modo == "detalhado"

    avisos_iniciais = migrar_nomes_antigos()      # quem vinha da versão chamada guarda_ssd
    for aviso in avisos_iniciais:
        log(aviso)
    caminho_config = resolver_config(a.config)
    if a.registrar:
        registrar(a.registrar, caminho_config, a.forcar)
        return 0
    if a.status:
        return mostrar_status()
    if a.testar_notificacao:
        return testar_notificacao(v.Estilo())
    if a.configurar_limpeza:
        return configurar_limpeza(caminho_config, v.Estilo())

    cfg, codigo_previo = None, None
    if caminho_config.exists():
        cfg = carregar_config(caminho_config)
        if not cfg.get("serial") or not cfg.get("id"):
            codigo_previo = 10
    else:
        codigo_previo = 10
    if a.historico:
        return abrir_historico(cfg if codigo_previo is None else None)
    if a.limpar:
        if codigo_previo is None:
            return comando_limpar(cfg, v.Estilo())
        print(FRASES[10])
        return 10
    if codigo_previo == 10:
        log(f"SSD ainda não registrado ({caminho_config}). Rode: python mnemosine.py --registrar D:")

    tela = v.Estilo() if modo == "tela" else None
    lock = None
    if codigo_previo is None:
        lock = Lock(LOCK, cfg["horas_lock_velho"])
        if not lock.adquirir():
            log(f"Já existe uma execução em andamento ({LOCK}). Nada foi feito.")
            codigo_previo, lock = 7, None

    if codigo_previo is not None:
        ctx = contexto(a, cfg or dict(PADRAO))
        ctx["codigo"] = codigo_previo
    else:
        try:
            ctx = executar(a, cfg, tela)
        finally:
            lock.liberar()
    ctx["avisos"] = avisos_iniciais + ctx["avisos"]

    if ctx["ssd"] and not ctx["simular"]:
        v.registrar_historico(sistema_de(ctx["ssd"]), registro_historico(ctx), copias_html=[PASTA_LOCAL])

    pendente = bool(ctx["vencidos"]) and ctx["politica"] == "perguntar" and not ctx["simular"]
    ctx["vai_perguntar"] = pendente and modo == "tela" and _interativo()

    if modo == "json":
        print(json.dumps(saida_json(ctx), ensure_ascii=False, indent=2))
    elif modo == "tela":
        mostrar_tela(tela, ctx)
        if ctx["vai_perguntar"]:
            perguntar_e_apagar(tela, ctx["ssd"], ctx["vencidos"], volta=True)
    elif modo == "silencioso":
        if ctx["codigo"] in NOTIFICAR:
            v.notificar("Backup do SSD", frase(ctx))
        if pendente:
            avisar_vencidos(ctx)
    return ctx["codigo"]


if __name__ == "__main__":
    sys.exit(main())
