"""
conferir_ssd.py - Confere se o SSD de backup está idêntico ao Google Drive.

Só leitura: nunca copia, move ou apaga nada. Compara TODOS os arquivos da origem
com o espelho no SSD por metadados (existe? mesmo tamanho? mesma data?), o que
não força o Google Drive a baixar nada e leva poucos segundos.

É independente do sync_ssd.py de propósito: não importa nada dele. A varredura e
a comparação são reimplementadas aqui, para que um erro na lógica do backup não
seja "confirmado" pela mesma lógica na conferência. Só compartilha o arquivo de
configuração (sync_ssd_config.json) e a identificação do SSD (serial + ID).

Uso:
  python conferir_ssd.py                 # confere e mostra o relatório
  python conferir_ssd.py --limite 0      # lista todas as divergências (padrão: 30 por tipo)
  python conferir_ssd.py --extras        # lista também o que só existe no SSD

Códigos de saída:
  0  SSD idêntico ao Drive
  1  há divergências (arquivo faltando, tamanho ou data diferentes)
  2  SSD de backup não encontrado
  3  origem indisponível (Google Drive fechado?)
  4  origem vazia, conferência não faz sentido
  10 configuração não encontrada (rode sync_ssd.py --registrar)
"""

import argparse
import ctypes
import fnmatch
import json
import os
import string
import sys
from datetime import datetime
from pathlib import Path

WINDOWS = os.name == "nt"
PASTA_SISTEMA = "_sync_ssd"
ARQ_IDENTIDADE = "IDENTIDADE_SSD.txt"
RAIZ_IGNORADA = {PASTA_SISTEMA.lower(), "$recycle.bin", "system volume information", "found.000"}
# Usados só se a configuração não trouxer "excluir" (o sync_ssd.py tem a mesma lista).
EXCLUIR_PADRAO = [
    "*.gdoc", "*.gsheet", "*.gslides", "*.gform", "*.gdraw", "*.gmap",
    "*.gsite", "*.gjam", "*.glink", "*.gscript", "*.gtable", "*.gnote",
    "desktop.ini", "thumbs.db", ".ds_store", "~$*", ".~lock*", "*.sync_tmp",
]
ATRIB_NAO_BAIXADO = 0x1000 | 0x40000 | 0x400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

AQUI = Path(__file__).resolve().parent
PASTA_LOCAL = Path(os.environ.get("LOCALAPPDATA", str(AQUI))) / "SyncSSD"
LOG_LOCAL = PASTA_LOCAL / "conferencia.log"
RESULTADO_LOCAL = PASTA_LOCAL / "ultima_conferencia.json"

TIPOS = [   # (chave, rótulo, conta como divergência?)
    ("faltando", "FALTANDO NO SSD", True),
    ("tamanho", "TAMANHO DIFERENTE", True),
    ("desatualizado", "SSD DESATUALIZADO (data mais antiga)", True),
    ("mais_novo", "SSD MAIS NOVO QUE O DRIVE", True),
    ("extras", "SÓ NO SSD (saiu do Drive; o backup nunca apaga)", False),
]


# ------------------------------------------------------------------ config
def achar_config(arg):
    if arg:
        return Path(arg).expanduser().resolve()
    for p in (AQUI / "sync_ssd_config.json", PASTA_LOCAL / "sync_ssd_config.json"):
        if p.exists():
            return p
    return AQUI / "sync_ssd_config.json"


# ------------------------------------------------------------------- SSD
def serial_do_volume(raiz):
    k32 = ctypes.windll.kernel32
    k32.SetErrorMode(0x0001 | 0x8000)   # sem janela "Insira um disco"
    serial = ctypes.c_uint32()
    if k32.GetVolumeInformationW(ctypes.c_wchar_p(raiz), None, 0, ctypes.byref(serial),
                                 None, None, None, 0):
        return f"{serial.value:08X}"
    return None


def localizar_ssd(cfg):
    """Raiz do volume com o serial E o arquivo de identidade do registro, ou None."""
    if not WINDOWS:
        return None
    mascara = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letra in enumerate(string.ascii_uppercase):
        raiz = f"{letra}:\\"
        if not mascara >> i & 1 or serial_do_volume(raiz) != cfg.get("serial"):
            continue
        try:
            if cfg["id"] in (Path(raiz) / PASTA_SISTEMA / ARQ_IDENTIDADE).read_text(encoding="utf-8"):
                return Path(raiz)
        except (OSError, KeyError):
            pass
    return None


# --------------------------------------------------------------- varredura
def prefixo_longo(p):
    s = os.path.abspath(str(p))
    return "\\\\?\\" + s if WINDOWS and not s.startswith("\\\\") else s


def listar(base, padroes, raiz_ignorada=frozenset()):
    """{caminho_relativo: (tamanho, mtime, nao_baixado)} via os.walk. Erros vão para a lista."""
    padroes = [p.lower() for p in padroes]

    def excluido(nome, rel):
        n, r = nome.lower(), rel.replace("\\", "/").lower()
        return any(fnmatch.fnmatchcase(n, p) or fnmatch.fnmatchcase(r, p) for p in padroes)

    topo = prefixo_longo(base)
    arquivos, erros = {}, []
    for pasta, subpastas, nomes in os.walk(topo, onerror=lambda e: erros.append(str(e))):
        rel_pasta = os.path.relpath(pasta, topo)
        rel_pasta = "" if rel_pasta == "." else rel_pasta
        mantidas = []
        for d in subpastas:
            rel = os.path.join(rel_pasta, d)
            if (not rel_pasta and d.lower() in raiz_ignorada) or excluido(d, rel):
                continue
            if os.path.islink(os.path.join(pasta, d)):
                continue
            mantidas.append(d)
        subpastas[:] = mantidas
        for n in nomes:
            rel = os.path.join(rel_pasta, n)
            if excluido(n, rel):
                continue
            try:
                st = os.lstat(os.path.join(pasta, n))
            except OSError as ex:
                erros.append(f"{rel}: {ex}")
                continue
            atrib = getattr(st, "st_file_attributes", 0)
            if atrib & 0x400:     # REPARSE_POINT (atalho simbólico): o backup também ignora
                continue
            arquivos[rel] = (st.st_size, st.st_mtime, bool(atrib & ATRIB_NAO_BAIXADO))
    return arquivos, erros


def conferir(drive, ssd, tolerancia):
    r = {k: [] for k, _, _ in TIPOS}
    for rel, (tam, mt, nao_baixado) in drive.items():
        d = ssd.get(rel)
        if d is None:
            r["faltando"].append(rel + ("   (ainda não baixado pelo Google Drive)" if nao_baixado else ""))
        elif d[0] != tam:
            r["tamanho"].append(f"{rel}   (Drive {tam} B, SSD {d[0]} B)")
        elif mt - d[1] > tolerancia:
            r["desatualizado"].append(rel)
        elif d[1] - mt > tolerancia:
            r["mais_novo"].append(rel)
    r["extras"] = [rel for rel in ssd if rel not in drive]
    return {k: sorted(v) for k, v in r.items()}


# ------------------------------------------------------------------- saída
class Relatorio:
    def __init__(self):
        self.linhas = []
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    def __call__(self, msg=""):
        self.linhas.append(msg)
        try:
            print(msg)
        except (UnicodeEncodeError, AttributeError, OSError):
            pass

    def gravar(self, *destinos):
        carimbo = f"===== {datetime.now():%Y-%m-%d %H:%M:%S} ====="
        for arq in destinos:
            try:
                arq.parent.mkdir(parents=True, exist_ok=True)
                with open(arq, "a", encoding="utf-8") as f:
                    f.write(carimbo + "\n" + "\n".join(self.linhas) + "\n\n")
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Confere se o SSD de backup está idêntico ao Drive (só leitura).")
    ap.add_argument("--limite", type=int, default=30, metavar="N",
                    help="quantos arquivos listar por tipo de divergência (0 = todos; padrão 30)")
    ap.add_argument("--extras", action="store_true", help="lista também os arquivos que só existem no SSD")
    ap.add_argument("--config", metavar="ARQUIVO", help="caminho do sync_ssd_config.json")
    a = ap.parse_args(argv)
    rel = Relatorio()

    caminho = achar_config(a.config)
    try:
        cfg = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rel(f"Configuração não encontrada ou inválida ({caminho}). Rode: python sync_ssd.py --registrar D:")
        return 10
    if not cfg.get("serial") or not cfg.get("id"):
        rel(f"{caminho} não tem serial/id. Rode: python sync_ssd.py --registrar D:")
        return 10

    origem = Path(cfg.get("origem", r"G:\Meu Drive"))
    padroes = cfg.get("excluir", EXCLUIR_PADRAO)
    tolerancia = cfg.get("tolerancia_seg", 2)

    ssd = localizar_ssd(cfg)
    if not ssd:
        rel("SSD de backup não encontrado. Nada foi conferido.")
        rel.gravar(LOG_LOCAL)
        return 2
    destino = ssd / cfg["pasta_destino"] if cfg.get("pasta_destino") else ssd
    log_ssd = ssd / PASTA_SISTEMA / "logs" / f"conferencia-{datetime.now():%Y-%m}.log"

    if not origem.is_dir():
        rel(f"Origem {origem} indisponível (Google Drive fechado?). Nada foi conferido.")
        rel.gravar(LOG_LOCAL, log_ssd)
        return 3

    inicio = datetime.now()
    rel(f"Conferência: {origem}  x  {destino}")
    drive, erros_d = listar(origem, padroes, raiz_ignorada=RAIZ_IGNORADA - {PASTA_SISTEMA.lower()})
    espelho, erros_s = listar(destino, padroes, raiz_ignorada=RAIZ_IGNORADA)
    if not drive:
        rel("A origem está vazia. Conferência cancelada.")
        rel.gravar(LOG_LOCAL, log_ssd)
        return 4

    r = conferir(drive, espelho, tolerancia)
    divergencias = sum(len(r[k]) for k, _, conta in TIPOS if conta)
    rel(f"Drive: {len(drive)} arquivos | SSD: {len(espelho)} arquivos | "
        f"conferidos em {(datetime.now() - inicio).total_seconds():.1f}s")
    rel()

    for chave, rotulo, conta in TIPOS:
        itens = r[chave]
        if not itens or (not conta and not a.extras):
            continue
        rel(f"{rotulo}: {len(itens)}")
        mostrar = itens if a.limite <= 0 else itens[:a.limite]
        for i in mostrar:
            rel(f"    {i}")
        if len(mostrar) < len(itens):
            rel(f"    ... e mais {len(itens) - len(mostrar)} (use --limite 0 para ver todos)")
        rel()
    for e in erros_d + erros_s:
        rel(f"ERRO de leitura: {e}")

    if divergencias:
        rel(f"RESULTADO: {divergencias} divergência(s). Rode o backup de novo; se persistirem, veja a lista acima.")
    else:
        rel("RESULTADO: SSD idêntico ao Drive.")
    if r["extras"] and not a.extras:
        rel(f"(Há {len(r['extras'])} arquivo(s) só no SSD, que saíram do Drive. Não é erro: o backup "
            "nunca apaga. Veja com --extras.)")

    resultado = {
        "quando": inicio.isoformat(timespec="seconds"), "codigo": 1 if divergencias else 0,
        "arquivos_drive": len(drive), "arquivos_ssd": len(espelho),
        **{k: len(v) for k, v in r.items()}, "erros_leitura": len(erros_d) + len(erros_s),
    }
    try:
        PASTA_LOCAL.mkdir(parents=True, exist_ok=True)
        RESULTADO_LOCAL.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    rel.gravar(LOG_LOCAL, log_ssd)
    return resultado["codigo"]


if __name__ == "__main__":
    sys.exit(main())
