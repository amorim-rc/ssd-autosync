"""
sync_ssd.py - Backup de mão única: Google Drive (local) -> SSD externo específico.

Regras:
  * Copia arquivos novos e arquivos cuja data de modificação ou tamanho mudou.
  * Detecta arquivos/pastas movidos ou renomeados e os move dentro do SSD, em
    vez de copiar de novo e deixar a cópia antiga para trás.
  * Ignora atalhos do Google Docs (.gdoc, .gsheet...), desktop.ini e o que
    mais estiver em "excluir" na configuração.
  * NUNCA apaga nada do SSD. Antes de sobrescrever um arquivo, guarda a versão
    antiga em _sync_ssd/versoes/<data>/ (mantidas por "dias_versoes" dias).
    Órfãos (arquivos que saíram do Drive) só vão para essa mesma pasta se você
    pedir com --quarentena.
  * Só roda no SSD registrado: exige o número de série do volume E o arquivo
    de identidade com o ID gerado no registro. Outro disco com a mesma letra
    ou o mesmo nome é recusado.
  * Trava de segurança: se uma execução for alterar arquivos demais de uma vez
    (sinal típico de ransomware ou de erro), aborta sem copiar nada.
  * Confere espaço livre antes de começar, não roda duas vezes ao mesmo tempo
    e pula arquivos que o Google Drive ainda não baixou (modo "stream").

Uso:
  python sync_ssd.py --registrar D:        # uma vez: marca este SSD como o destino
  python sync_ssd.py --simular             # mostra o que faria, sem copiar
  python sync_ssd.py                       # executa o backup
  python sync_ssd.py --forcar              # executa ignorando a trava de segurança
  python sync_ssd.py --orfaos              # só lista o que está no SSD mas saiu do Drive
  python sync_ssd.py --quarentena          # backup + move órfãos para _sync_ssd/versoes/
  python sync_ssd.py --verificar [N]       # backup + confere hash de N arquivos (0 = todos)
  python sync_ssd.py --status              # mostra o resultado da última execução
  python sync_ssd.py --alertar-se-velho 7  # se o SSD não estiver plugado e o último
                                           # backup tiver mais de 7 dias, avisa na tela

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

VERSAO = "2.0"
WINDOWS = os.name == "nt"

# ---------------------------------------------------------------- configuração
PASTA_SISTEMA = "_sync_ssd"        # na raiz do SSD: identidade, logs, versões
ARQ_IDENTIDADE = "IDENTIDADE_SSD.txt"
ARQ_ESTADO = "ultimo_resultado.json"
SUFIXO_TMP = ".sync_tmp"
FORMATO_CARIMBO = "%Y-%m-%d_%H%M%S"

# Valores padrão. Qualquer um pode ser sobrescrito no sync_ssd_config.json.
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
    "max_versoes_gb": 0,            # teto de espaço para versões (0 = sem teto)
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
PASTA_LOCAL = Path(os.environ.get("LOCALAPPDATA", str(AQUI))) / "SyncSSD"
CONFIG_PADRAO = AQUI / "sync_ssd_config.json"
LOG_LOCAL = PASTA_LOCAL / "sync.log"
ESTADO_LOCAL = PASTA_LOCAL / ARQ_ESTADO
LOCK = PASTA_LOCAL / "sync.lock"


# ---------------------------------------------------------------------- log
class Log:
    def __init__(self):
        self.linhas = []
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # pythonw não tem console

    def __call__(self, msg):
        linha = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        self.linhas.append(linha)
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
    """Prefixo \\?\ para o Windows aceitar caminhos com mais de 260 caracteres."""
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


def localizar_ssd(cfg):
    """Procura em todas as letras o volume com serial e identidade corretos."""
    for raiz in unidades():
        serial, _ = info_volume(raiz)
        if serial != cfg["serial"]:
            continue
        ident = Path(raiz) / PASTA_SISTEMA / ARQ_IDENTIDADE
        try:
            if cfg["id"] in ident.read_text(encoding="utf-8"):
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
    ident = Path(raiz) / PASTA_SISTEMA / ARQ_IDENTIDADE
    ident.parent.mkdir(exist_ok=True)
    id_ = str(uuid.uuid4())
    ident.write_text(
        "Este disco é o destino de backup do sync_ssd.py.\n"
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


def limpar_versoes(pasta_versoes, dias, max_bytes=0, agora=None):
    """Remove pastas de versão mais velhas que `dias` (pela data no nome) e, se
    `max_bytes` > 0, as mais antigas até o total caber no teto. -> nomes removidos."""
    removidas = []
    if not pasta_versoes.is_dir():
        return removidas
    datadas = []
    for p in pasta_versoes.iterdir():
        if not p.is_dir():
            continue
        try:
            datadas.append((datetime.strptime(p.name[:17], FORMATO_CARIMBO), p))
        except ValueError:
            continue     # pasta que não é nossa: não mexe
    datadas.sort()
    limite = (agora or datetime.now()) - timedelta(days=dias)
    restantes = []
    for data, p in datadas:
        if data < limite:
            shutil.rmtree(p, ignore_errors=True)
            removidas.append(p.name)
        else:
            restantes.append(p)
    if max_bytes > 0 and restantes:
        tamanhos = {p: tamanho_pasta(p) for p in restantes}
        total = sum(tamanhos.values())
        for p in restantes:           # mais antigas primeiro
            if total <= max_bytes:
                break
            shutil.rmtree(p, ignore_errors=True)
            total -= tamanhos[p]
            removidas.append(p.name)
    return removidas


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
    if WINDOWS:
        try:
            # MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST
            ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x30 | 0x10000 | 0x40000)
        except Exception:      # noqa: BLE001 - alerta é melhor esforço
            pass


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
            f"Plugue o SSD de backup para que o sync_ssd.py rode.")
    est["ultimo_alerta"] = agora.isoformat(timespec="seconds")
    gravar_estado(est, ESTADO_LOCAL)
    return 9


# ------------------------------------------------------------------- backup
def sincronizar(a, cfg, ssd, simular, estado):
    L = cfg["caminhos_longos"]
    tol = cfg["tolerancia_seg"]
    sistema = ssd / PASTA_SISTEMA
    destino = ssd / cfg["pasta_destino"] if cfg["pasta_destino"] else ssd
    origem = Path(cfg["origem"])

    if not origem.is_dir():
        log(f"Origem {origem} indisponível (Google Drive fechado?). Nada foi feito.")
        return 3

    log(f"Início{' (SIMULAÇÃO)' if simular else ''}: {origem} -> {destino}  [sync_ssd {VERSAO}]")
    excluido = excluidor(cfg["excluir"])
    arq_o, erros_o = varrer(origem, excluido, caminhos_longos=L)
    arq_d, erros_d = varrer(destino, excluido, pular_raiz=[PASTA_SISTEMA], caminhos_longos=L)
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
    estado["nao_baixados"] = len(nao_baixados)

    # Movidos/renomeados: mover dentro do SSD em vez de copiar de novo.
    movidos = detectar_movidos(
        novos, orfaos, arq_o, arq_d, tol,
        confirmar=lambda n, v: mesmo_conteudo_rapido(longo(origem / n, L), longo(destino / v, L)))
    if movidos:
        novos = [r for r in novos if r not in movidos]
        antigos = set(movidos.values())
        orfaos = [r for r in orfaos if r not in antigos]

    log(f"Origem: {len(arq_o)} arquivos | novos: {len(novos)} | alterados: {len(alterados)} | "
        f"movidos: {len(movidos)} | órfãos: {len(orfaos)}")
    estado.update(origem_arquivos=len(arq_o), novos=len(novos), alterados=len(alterados),
                  movidos=len(movidos), orfaos=len(orfaos))

    if a.orfaos:
        for r in orfaos:
            log(f"  ORFAO    {r}")
        return 0

    total = len(novos) + len(alterados) + (len(orfaos) if a.quarentena else 0)
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
    if not simular:
        livre = shutil.disk_usage(str(ssd)).free
        margem = cfg["margem_espaco_gb"] * 1024 ** 3
        if necessario + margem > livre:
            log(f"ESPAÇO INSUFICIENTE: precisa de {fmt_bytes(necessario)} + margem de "
                f"{fmt_bytes(margem)}, mas só há {fmt_bytes(livre)} livres. Nada foi copiado.")
            return 6

    carimbo = f"{datetime.now():{FORMATO_CARIMBO}}"
    pasta_versao = sistema / "versoes" / carimbo
    ok = falhas = quarentenados = 0
    bytes_copiados = 0
    copiados = []

    for novo, antigo in sorted(movidos.items()):
        log(f"  MOVIDO   {antigo}  ->  {novo}")
        if simular:
            continue
        try:
            mover(destino / antigo, destino / novo, L)
            remover_pastas_vazias((destino / antigo).parent, destino, L)
            arq_d[novo] = arq_d.pop(antigo)
        except OSError as ex:
            log(f"  ERRO     mover {antigo}: {ex}. Vai copiar em vez de mover.")
            novos.append(novo)
            orfaos.append(antigo)

    for rel in sorted(novos) + alterados:
        tipo = "ALTERADO" if rel in arq_d else "NOVO"
        log(f"  {tipo:8} {rel}")
        if simular:
            continue
        src, dst = origem / rel, destino / rel
        try:
            copiar(src, dst, antigo=(pasta_versao / rel) if tipo == "ALTERADO" else None, caminhos_longos=L)
            ok += 1
            bytes_copiados += arq_o[rel][0]
            copiados.append((arq_o[rel][0], rel))
            arq_d[rel] = arq_o[rel]
        except OSError as ex:
            falhas += 1
            log(f"  ERRO     {rel}: {ex}")

    if a.quarentena:
        for rel in sorted(orfaos):
            log(f"  QUARENT. {rel}")
            if simular:
                continue
            try:
                mover(destino / rel, pasta_versao / rel, L)
                remover_pastas_vazias((destino / rel).parent, destino, L)
                quarentenados += 1
            except OSError as ex:
                falhas += 1
                log(f"  ERRO     quarentena {rel}: {ex}")

    codigo = 0
    if not simular:
        for nome in limpar_versoes(sistema / "versoes", cfg["dias_versoes"],
                                   cfg["max_versoes_gb"] * 1024 ** 3):
            log(f"Versões antigas removidas: {nome}")
        if a.verificar is not None:
            n, divergentes = verificar_hashes(arq_o, arq_d, origem, destino, a.verificar, tol, L)
            for r in divergentes:
                log(f"  DIVERGE  {r}")
            log(f"Verificação: {n} arquivos conferidos por hash, {len(divergentes)} divergentes.")
            estado.update(verificados=n, divergentes=len(divergentes))
            if divergentes:
                codigo = 8
        if len(copiados) > 1:
            log("Maiores arquivos copiados:")
            for tam, rel in sorted(copiados, reverse=True)[:10]:
                log(f"  {fmt_bytes(tam):>10}  {rel}")
        log(f"Fim: {ok} copiados ({fmt_bytes(bytes_copiados)}), {len(movidos)} movidos, "
            f"{quarentenados} em quarentena, {falhas} falhas.")

    estado.update(copiados=ok, falhas=falhas, quarentenados=quarentenados, bytes_copiados=bytes_copiados)
    if codigo == 0 and falhas:
        codigo = 1
    return codigo


def executar(a, cfg):
    inicio = time.time()
    simular = a.simular or a.orfaos
    ssd = localizar_ssd(cfg)
    if not ssd:
        log("SSD de backup não encontrado. Nada foi feito.")
        codigo = 2
        if a.alertar_se_velho:
            codigo = alertar_se_velho(a.alertar_se_velho) or 2
        log.gravar(LOG_LOCAL)
        return codigo

    estado = ler_estado(ESTADO_LOCAL)
    estado.update(inicio=datetime.now().isoformat(timespec="seconds"), simulacao=simular, codigo=None)
    for k in ("origem_arquivos", "novos", "alterados", "movidos", "orfaos", "quarentenados",
              "copiados", "falhas", "nao_baixados", "bytes_copiados", "verificados", "divergentes"):
        estado.pop(k, None)
    codigo = 11
    try:
        codigo = sincronizar(a, cfg, ssd, simular, estado)
        return codigo
    except Exception:      # noqa: BLE001 - registrar no log, não morrer em silêncio no pythonw
        log("ERRO INESPERADO:\n" + traceback.format_exc())
        return codigo
    finally:
        estado.update(fim=datetime.now().isoformat(timespec="seconds"),
                      duracao_seg=round(time.time() - inicio, 1), codigo=codigo)
        if not simular and codigo in (0, 1, 8):
            estado["ultimo_backup"] = estado["fim"]
        destinos_estado = [ESTADO_LOCAL] + ([] if simular else [ssd / PASTA_SISTEMA / ARQ_ESTADO])
        gravar_estado(estado, *destinos_estado)
        log_ssd = ssd / PASTA_SISTEMA / "logs" / f"{datetime.now():%Y-%m}.log"
        log.gravar(LOG_LOCAL, *([] if simular else [log_ssd]))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Backup de mão única Google Drive -> SSD específico",
        epilog="Códigos de saída: " + "; ".join(f"{k}={v}" for k, v in CODIGOS.items()))
    ap.add_argument("--registrar", metavar="LETRA", help="registra o SSD nessa letra como destino")
    ap.add_argument("--simular", action="store_true", help="mostra o que faria, sem alterar nada")
    ap.add_argument("--forcar", action="store_true", help="ignora a trava de segurança")
    ap.add_argument("--orfaos", action="store_true", help="só lista o que está no SSD e saiu do Drive")
    ap.add_argument("--quarentena", action="store_true",
                    help="move os órfãos para _sync_ssd/versoes/ (nunca apaga)")
    ap.add_argument("--verificar", nargs="?", const=200, type=int, metavar="N",
                    help="após o backup, confere o hash de N arquivos (padrão 200; 0 = todos)")
    ap.add_argument("--baixar", action="store_true",
                    help="copia também arquivos que o Drive ainda não baixou (força download)")
    ap.add_argument("--status", action="store_true", help="mostra o resultado da última execução")
    ap.add_argument("--alertar-se-velho", type=int, metavar="DIAS",
                    help="se o SSD não estiver presente e o último backup tiver mais de DIAS dias, avisa")
    ap.add_argument("--config", metavar="ARQUIVO", help="caminho do sync_ssd_config.json")
    ap.add_argument("--versao", action="version", version=f"sync_ssd {VERSAO}")
    a = ap.parse_args(argv)

    caminho_config = resolver_config(a.config)
    if a.registrar:
        registrar(a.registrar, caminho_config, a.forcar)
        return 0
    if a.status:
        return mostrar_status()
    if not caminho_config.exists():
        print(f"SSD ainda não registrado ({caminho_config} não existe). "
              "Rode: python sync_ssd.py --registrar D:")
        return 10
    cfg = carregar_config(caminho_config)
    if not cfg.get("serial") or not cfg.get("id"):
        print(f"{caminho_config} não tem serial/id. Rode: python sync_ssd.py --registrar D:")
        return 10

    lock = Lock(LOCK, cfg["horas_lock_velho"])
    if not lock.adquirir():
        log(f"Já existe uma execução em andamento ({LOCK}). Nada foi feito.")
        return 7
    try:
        return executar(a, cfg)
    finally:
        lock.liberar()


if __name__ == "__main__":
    sys.exit(main())
