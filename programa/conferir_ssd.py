"""
conferir_ssd.py - Confere se o SSD de backup está idêntico ao Google Drive.

Só leitura: nunca copia, move ou apaga nada. Compara TODOS os arquivos da origem
com o espelho no SSD por metadados (existe? mesmo tamanho? mesma data?), o que
não força o Google Drive a baixar nada e leva poucos segundos.

É independente do mnemosine.py de propósito: não importa nada dele. A varredura e
a comparação são reimplementadas aqui, para que um erro na lógica do backup não
seja "confirmado" pela mesma lógica na conferência. Só compartilha o arquivo de
configuração (mnemosine_config.json), a identificação do SSD (serial + ID) e o
módulo de apresentação visual_ssd.py (tela, histórico, notificação), que não
tem lógica de comparação.

Uso:
  python conferir_ssd.py                 # confere e mostra o resultado
  python conferir_ssd.py --limite 0      # lista todas as divergências (padrão: 30 por tipo)
  python conferir_ssd.py --extras        # lista também o que só existe no SSD

Saída (a conferência é a mesma; muda só como o resultado aparece):
  (padrão)       tela resumida, com cores e símbolos
  --detalhado    relatório técnico
  --silencioso   nada na tela; notificação do Windows se houver divergência
  --json         o resultado em JSON

Códigos de saída:
  0  SSD idêntico ao Drive
  1  há divergências (arquivo faltando, tamanho ou data diferentes)
  2  SSD de backup não encontrado
  3  origem indisponível (Google Drive fechado?)
  4  origem vazia, conferência não faz sentido
  10 configuração não encontrada (rode mnemosine.py --registrar)
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

import visual_ssd as v

WINDOWS = os.name == "nt"
PASTA_SISTEMA = "_mnemosine"
PASTA_SISTEMA_LEGADA = "_guarda_ssd"    # nome até a 2.2; o backup renomeia, a conferência só lê
PASTAS_SISTEMA = {PASTA_SISTEMA.lower(), PASTA_SISTEMA_LEGADA.lower()}
ARQ_IDENTIDADE = "IDENTIDADE_SSD.txt"
RAIZ_IGNORADA = PASTAS_SISTEMA | {"$recycle.bin", "system volume information", "found.000"}
# Usados só se a configuração não trouxer "excluir" (o mnemosine.py tem a mesma lista).
EXCLUIR_PADRAO = [
    "*.gdoc", "*.gsheet", "*.gslides", "*.gform", "*.gdraw", "*.gmap",
    "*.gsite", "*.gjam", "*.glink", "*.gscript", "*.gtable", "*.gnote",
    "desktop.ini", "thumbs.db", ".ds_store", "~$*", ".~lock*", "*.sync_tmp",
]
ATRIB_NAO_BAIXADO = 0x1000 | 0x40000 | 0x400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

AQUI = Path(__file__).resolve().parent
PASTA_LOCAL = Path(os.environ.get("LOCALAPPDATA", str(AQUI))) / "Mnemosine"
PASTA_LOCAL_LEGADA = Path(os.environ.get("LOCALAPPDATA", str(AQUI))) / "GuardaSSD"   # até a 2.2
CONFIG = "mnemosine_config.json"
CONFIG_LEGADO = "guarda_ssd_config.json"                                             # até a 2.2
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
    # Os nomes antigos só valem até o próximo backup, que os renomeia.
    for p in (AQUI / CONFIG, PASTA_LOCAL / CONFIG, AQUI / CONFIG_LEGADO,
              PASTA_LOCAL / CONFIG_LEGADO, PASTA_LOCAL_LEGADA / CONFIG_LEGADO):
        if p.exists():
            return p
    return AQUI / CONFIG


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
        for pasta in (PASTA_SISTEMA, PASTA_SISTEMA_LEGADA):
            try:
                if cfg["id"] in (Path(raiz) / pasta / ARQ_IDENTIDADE).read_text(encoding="utf-8"):
                    return Path(raiz)
            except (OSError, KeyError):
                pass
    return None


def sistema_de(ssd):
    """Pasta de sistema no SSD: a nova, ou a de nome antigo enquanto o backup não a renomear."""
    nova = Path(ssd) / PASTA_SISTEMA
    antiga = Path(ssd) / PASTA_SISTEMA_LEGADA
    return antiga if not nova.exists() and antiga.is_dir() else nova


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
    """-> {tipo: [(caminho, nota)]} para cada tipo de TIPOS, em ordem alfabética."""
    r = {k: [] for k, _, _ in TIPOS}
    for rel, (tam, mt, nao_baixado) in drive.items():
        d = ssd.get(rel)
        if d is None:
            r["faltando"].append((rel, "ainda não baixado pelo Google Drive" if nao_baixado else ""))
        elif d[0] != tam:
            r["tamanho"].append((rel, f"Drive {tam} B, SSD {d[0]} B"))
        elif mt - d[1] > tolerancia:
            r["desatualizado"].append((rel, ""))
        elif d[1] - mt > tolerancia:
            r["mais_novo"].append((rel, ""))
    r["extras"] = [(rel, "") for rel in ssd if rel not in drive]
    return {k: sorted(v_) for k, v_ in r.items()}


# ------------------------------------------------------------------- saída
class Relatorio:
    """Relatório técnico: sempre vai para os logs; aparece na tela só no --detalhado."""

    def __init__(self, ecoar=True):
        self.linhas = []
        self.ecoar = ecoar
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    def __call__(self, msg=""):
        self.linhas.append(msg)
        if not self.ecoar:
            return
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


FRASES = {
    2: "SSD de backup não encontrado. Ele está plugado?",
    3: "A origem ({origem}) não está disponível. O Google Drive está aberto?",
    4: "A origem está vazia. Não há o que conferir.",
    10: "O SSD ainda não foi registrado. Rode: python mnemosine.py --registrar D:",
}
TELA = {   # tipo -> (símbolo, título, cor)
    "faltando": ("falha", "Faltando no SSD", "vermelho"),
    "tamanho": ("falha", "Tamanho diferente", "vermelho"),
    "desatualizado": ("aviso", "SSD desatualizado", "amarelo"),
    "mais_novo": ("aviso", "SSD mais novo que o Drive", "amarelo"),
    "extras": ("fora", "Só no SSD", "cinza"),
}
NOTIFICAR = {1, 3, 4}


def severidade(codigo):
    return "ok" if codigo == 0 else "aviso" if codigo == 1 else "erro"


def frase(ctx):
    codigo = ctx["codigo"]
    if codigo == 0:
        return "SSD idêntico ao Drive"
    if codigo == 1:
        return (v.plural(ctx["divergencias"], "divergência", "divergências")
                + ". Rode o backup de novo; se continuarem, veja a lista acima.")
    return FRASES.get(codigo, "Erro desconhecido.").format(origem=ctx["origem"])


def mostrar_tela(e, ctx, limite, extras):
    r = ctx["r"]
    destino = v.nome_ssd(ctx["ssd"]) if ctx["ssd"] else "SSD de backup"
    origem = Path(ctx["origem"]).name or ctx["origem"]
    e.escrever()
    e.escrever("  " + e.c(f"CONFERÊNCIA  {origem} × {destino}", "negrito"))
    sub = v.fmt_data_hora(ctx["inicio"])
    if ctx["arquivos_drive"] is not None:
        sub += (f" {e.s['ponto']} {v.plural(ctx['arquivos_drive'], 'arquivo', 'arquivos')} no Drive "
                f"{e.s['ponto']} {v.fmt_num(ctx['arquivos_ssd'])} no SSD {e.s['ponto']} {v.fmt_duracao(ctx['duracao'])}")
    e.escrever("  " + e.c(sub, "cinza"))
    e.escrever("  " + e.c("Só leitura: nada é copiado nem alterado.", "cinza"))
    e.escrever()
    if r:
        for chave, (simbolo, titulo, cor) in TELA.items():
            if chave == "extras" and not extras:
                continue
            nota = "saíram do Drive; o backup não apaga sozinho" if chave == "extras" else ""
            v.secao(e, simbolo, titulo, r[chave], cor, nota=nota, limite=limite)
    v.separador(e)
    v.linha_final(e, severidade(ctx["codigo"]), frase(ctx))
    if r and r["extras"] and not extras:
        e.escrever("  " + e.c(f"{e.s['info']} {v.plural(len(r['extras']), 'arquivo está', 'arquivos estão')} "
                              "só no SSD (saíram do Drive). Não é erro; veja com --extras ou tire do espelho "
                              "com o backup --quarentena.", "cinza"))
    if ctx["erros"]:
        e.escrever("  " + e.c(f"{e.s['aviso']} {v.plural(ctx['erros'], 'erro', 'erros')} de leitura: veja o log.",
                              "amarelo"))
    e.escrever()


def saida_json(ctx):
    r = ctx["r"] or {k: [] for k in TELA}
    return {
        "programa": "conferir_ssd", "codigo": ctx["codigo"], "resultado": severidade(ctx["codigo"]),
        "mensagem": frase(ctx), "inicio": ctx["inicio"].isoformat(timespec="seconds"),
        "duracao_seg": round(ctx["duracao"], 2), "origem": ctx["origem"],
        "ssd": {"raiz": str(ctx["ssd"]), "nome": v.nome_ssd(ctx["ssd"])} if ctx["ssd"] else None,
        "arquivos_drive": ctx["arquivos_drive"], "arquivos_ssd": ctx["arquivos_ssd"],
        "divergencias": ctx["divergencias"], "erros_leitura": ctx["erros"],
        **{k: [{"caminho": c_, "nota": n} for c_, n in r[k]] for k in TELA},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Confere se o SSD de backup está idêntico ao Drive (só leitura).")
    ap.add_argument("--limite", type=int, default=30, metavar="N",
                    help="quantos arquivos listar por tipo de divergência (0 = todos; padrão 30)")
    ap.add_argument("--extras", action="store_true", help="lista também os arquivos que só existem no SSD")
    ap.add_argument("--config", metavar="ARQUIVO", help="caminho do mnemosine_config.json")
    saida = ap.add_mutually_exclusive_group()
    saida.add_argument("--detalhado", action="store_true", help="relatório técnico completo")
    saida.add_argument("--silencioso", action="store_true",
                       help="nada na tela; notificação do Windows se houver divergência")
    saida.add_argument("--json", action="store_true", help="resultado em JSON")
    a = ap.parse_args(argv)
    modo = "json" if a.json else "silencioso" if a.silencioso else "detalhado" if a.detalhado else "tela"
    rel = Relatorio(ecoar=modo == "detalhado")
    ctx = {"codigo": 10, "origem": "", "ssd": None, "r": None, "inicio": datetime.now(), "duracao": 0.0,
           "arquivos_drive": None, "arquivos_ssd": None, "divergencias": 0, "erros": 0}
    ctx["codigo"] = conferencia(a, rel, ctx)
    ctx["duracao"] = (datetime.now() - ctx["inicio"]).total_seconds()

    if ctx["ssd"] and ctx["codigo"] in (0, 1):
        v.registrar_historico(sistema_de(ctx["ssd"]), {
            "tipo": "conferencia", "quando": ctx["inicio"].isoformat(timespec="seconds"),
            "codigo": ctx["codigo"], "resultado": severidade(ctx["codigo"]), "frase": frase(ctx),
            "arquivos_drive": ctx["arquivos_drive"], "divergencias": ctx["divergencias"],
            "extras": len(ctx["r"]["extras"]), "duracao_seg": round(ctx["duracao"], 1),
        }, copias_html=[PASTA_LOCAL])

    if modo == "json":
        print(json.dumps(saida_json(ctx), ensure_ascii=False, indent=2))
    elif modo == "tela":
        mostrar_tela(v.Estilo(), ctx, a.limite, a.extras)
    elif modo == "silencioso" and ctx["codigo"] in NOTIFICAR:
        v.notificar("Conferência do SSD", frase(ctx))
    return ctx["codigo"]


def conferencia(a, rel, ctx):
    """Faz a conferência, escreve o relatório técnico em `rel`, preenche `ctx`. -> código."""
    caminho = achar_config(a.config)
    try:
        cfg = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rel(f"Configuração não encontrada ou inválida ({caminho}). Rode: python mnemosine.py --registrar D:")
        return 10
    if not cfg.get("serial") or not cfg.get("id"):
        rel(f"{caminho} não tem serial/id. Rode: python mnemosine.py --registrar D:")
        return 10

    origem = Path(cfg.get("origem", r"G:\Meu Drive"))
    ctx["origem"] = str(origem)
    padroes = cfg.get("excluir", EXCLUIR_PADRAO)
    tolerancia = cfg.get("tolerancia_seg", 2)

    ssd = localizar_ssd(cfg)
    if not ssd:
        rel("SSD de backup não encontrado. Nada foi conferido.")
        rel.gravar(LOG_LOCAL)
        return 2
    ctx["ssd"] = ssd
    destino = ssd / cfg["pasta_destino"] if cfg.get("pasta_destino") else ssd
    log_ssd = sistema_de(ssd) / "logs" / f"conferencia-{datetime.now():%Y-%m}.log"

    if not origem.is_dir():
        rel(f"Origem {origem} indisponível (Google Drive fechado?). Nada foi conferido.")
        rel.gravar(LOG_LOCAL, log_ssd)
        return 3

    inicio = datetime.now()
    rel(f"Conferência: {origem}  x  {destino}")
    drive, erros_d = listar(origem, padroes, raiz_ignorada=RAIZ_IGNORADA - PASTAS_SISTEMA)
    espelho, erros_s = listar(destino, padroes, raiz_ignorada=RAIZ_IGNORADA)
    if not drive:
        rel("A origem está vazia. Conferência cancelada.")
        rel.gravar(LOG_LOCAL, log_ssd)
        return 4

    r = conferir(drive, espelho, tolerancia)
    divergencias = sum(len(r[k]) for k, _, conta in TIPOS if conta)
    ctx.update(r=r, arquivos_drive=len(drive), arquivos_ssd=len(espelho), divergencias=divergencias,
               erros=len(erros_d) + len(erros_s))
    rel(f"Drive: {len(drive)} arquivos | SSD: {len(espelho)} arquivos | "
        f"conferidos em {(datetime.now() - inicio).total_seconds():.1f}s")
    rel()

    for chave, rotulo, conta in TIPOS:
        itens = r[chave]
        if not itens or (not conta and not a.extras):
            continue
        rel(f"{rotulo}: {len(itens)}")
        mostrar = itens if a.limite <= 0 else itens[:a.limite]
        for caminho_, nota in mostrar:
            rel(f"    {caminho_}" + (f"   ({nota})" if nota else ""))
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

    codigo = 1 if divergencias else 0
    resultado = {
        "quando": inicio.isoformat(timespec="seconds"), "codigo": codigo,
        "arquivos_drive": len(drive), "arquivos_ssd": len(espelho),
        **{k: len(v_) for k, v_ in r.items()}, "erros_leitura": len(erros_d) + len(erros_s),
    }
    try:
        PASTA_LOCAL.mkdir(parents=True, exist_ok=True)
        RESULTADO_LOCAL.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    rel.gravar(LOG_LOCAL, log_ssd)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
