"""
visual_ssd.py - Apresentação compartilhada pelo sync_ssd.py e pelo conferir_ssd.py.

Só apresentação e registro: estilo do terminal (cores, símbolos, fallback),
formatação em português, seções agrupadas por pasta, barra de progresso,
notificação do Windows e o histórico (historico.json + historico.html).

Não tem nenhuma lógica de varredura ou comparação de arquivos: a conferência
continua independente do backup.
"""

import ctypes
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

WINDOWS = os.name == "nt"

SIMBOLOS = {
    "novo": "✚", "atualizado": "↻", "movido": "⇢", "fora": "⇢", "falha": "✖",
    "ok": "✔", "aviso": "⚠", "erro": "✖", "seta": "→", "sep": "›", "linha": "─",
    "cheio": "█", "vazio": "░", "ponto": "·", "info": "·",
}
ASCII = {
    "novo": "+", "atualizado": "~", "movido": ">", "fora": ">", "falha": "x",
    "ok": "OK", "aviso": "!", "erro": "x", "seta": "->", "sep": ">", "linha": "-",
    "cheio": "#", "vazio": ".", "ponto": "-", "info": "-",
}
CORES = {"verde": "32", "amarelo": "33", "vermelho": "31", "ciano": "36", "cinza": "90", "negrito": "1"}
COR_RESULTADO = {"ok": "verde", "aviso": "amarelo", "erro": "vermelho"}
LARGURA_NOME = 56
MAX_HISTORICO = 200


# ------------------------------------------------------------------- estilo
class Estilo:
    """Decide cores e símbolos conforme o terminal. Fora de um terminal (arquivo,
    pipe, pythonw) ou com NO_COLOR definido, não usa cor. Se a codificação não
    aceita os símbolos, usa ASCII."""

    def __init__(self, stream=None, cor=None, unicode=None):
        self.stream = sys.stdout if stream is None else stream
        self.cor = self._detectar_cor() if cor is None else cor
        self.unicode = self._detectar_unicode() if unicode is None else unicode
        self.s = SIMBOLOS if self.unicode else ASCII

    def _detectar_cor(self):
        if "NO_COLOR" in os.environ:
            return False
        try:
            if not self.stream.isatty():
                return False
        except (AttributeError, ValueError):
            return False
        if not WINDOWS:
            return True
        try:
            k32 = ctypes.windll.kernel32
            h = k32.GetStdHandle(-11)
            modo = ctypes.c_uint32()
            if not k32.GetConsoleMode(h, ctypes.byref(modo)):
                return False
            return bool(k32.SetConsoleMode(h, modo.value | 0x0004))   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:      # noqa: BLE001 - sem cor não é motivo para falhar
            return False

    def _detectar_unicode(self):
        enc = getattr(self.stream, "encoding", None) or "ascii"
        try:
            "".join(SIMBOLOS.values()).encode(enc)
            return True
        except (UnicodeEncodeError, LookupError):
            return False

    def c(self, texto, *cores):
        if not self.cor or not cores:
            return texto
        return "\033[" + ";".join(CORES[c] for c in cores) + "m" + texto + "\033[0m"

    def escrever(self, texto=""):
        try:
            self.stream.write(texto + "\n")
            self.stream.flush()
        except (UnicodeEncodeError, OSError, AttributeError, ValueError):
            pass


# --------------------------------------------------------------- formatação
def fmt_num(n):
    return f"{n:,}".replace(",", ".")


def fmt_bytes(n):
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidade == "TB":
            return f"{int(n)} B" if unidade == "B" else f"{n:.1f} {unidade}".replace(".", ",")
        n /= 1024


def fmt_duracao(seg):
    if seg < 60:
        return f"{seg:.1f} s".replace(".", ",")
    seg = int(seg)
    if seg < 3600:
        return f"{seg // 60} min {seg % 60} s"
    return f"{seg // 3600} h {seg % 3600 // 60} min"


def fmt_data(dt):
    return f"{dt:%d/%m}"


def fmt_data_hora(dt):
    return f"{dt:%d/%m/%Y, %H:%M}"


def _plural(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


def ha_quanto(dt, agora=None):
    if dt is None:
        return "nunca"
    seg = ((agora or datetime.now()) - dt).total_seconds()
    if seg < 60:
        return "agora mesmo"
    if seg < 3600:
        return "há " + _plural(int(seg // 60), "minuto", "minutos")
    if seg < 86400:
        return "há " + _plural(int(seg // 3600), "hora", "horas")
    return "há " + _plural(int(seg // 86400), "dia", "dias")


def plural(n, singular, plural_):
    """'1 arquivo', '2.351 arquivos'."""
    return f"{fmt_num(n)} {singular if n == 1 else plural_}"


# ------------------------------------------------------------------ seções
def agrupar_por_pasta(itens):
    """[(caminho_relativo, texto_à_direita)] -> [(pasta, [(nome, texto)])].
    Pastas em ordem alfabética (raiz primeiro); arquivos na ordem recebida."""
    grupos = {}
    for rel, direita in itens:
        pasta, nome = os.path.split(rel.replace("/", "\\"))
        grupos.setdefault(pasta, []).append((nome, direita))
    return [(p, grupos[p]) for p in sorted(grupos, key=str.lower)]


def pasta_bonita(pasta, e):
    if not pasta:
        return "(raiz)"
    return f" {e.s['sep']} ".join(pasta.split("\\"))


def secao(e, simbolo, titulo, itens, cor, nota="", limite=15):
    """Imprime um grupo de arquivos: cabeçalho, pastas uma vez, nomes embaixo."""
    if not itens:
        return
    cab = f"  {e.c(e.s[simbolo], cor)} {e.c(f'{titulo} ({fmt_num(len(itens))})', 'negrito')}"
    if nota:
        cab += e.c(f" {e.s['ponto']} {nota}", "cinza")
    e.escrever(cab)
    mostrar = itens if not limite else itens[:limite]
    for pasta, arquivos in agrupar_por_pasta(mostrar):
        e.escrever("      " + e.c(pasta_bonita(pasta, e), "cinza"))
        for nome, direita in arquivos:
            if not direita:
                e.escrever(f"        {nome}")
            elif len(nome) <= LARGURA_NOME:
                e.escrever(f"        {nome:<{LARGURA_NOME}} {e.c(f'{direita:>10}', 'cinza')}")
            else:
                e.escrever(f"        {nome}  {e.c(direita, 'cinza')}")
    if len(mostrar) < len(itens):
        e.escrever(e.c(f"      ... e mais {fmt_num(len(itens) - len(mostrar))} (lista completa no log)", "cinza"))
    e.escrever()


def linha_final(e, resultado, texto):
    simbolo = {"ok": "ok", "aviso": "aviso", "erro": "erro"}[resultado]
    e.escrever(f"  {e.c(e.s[simbolo] + ' ' + texto, COR_RESULTADO[resultado], 'negrito')}")


def separador(e):
    e.escrever("  " + e.c(e.s["linha"] * 58, "cinza"))


# ---------------------------------------------------------------- progresso
class Progresso:
    """Barra de progresso na mesma linha. Só desenha em terminal de verdade."""

    def __init__(self, e, total_arquivos, total_bytes):
        self.e = e
        self.total_arquivos = max(total_arquivos, 1)
        self.total_bytes = max(total_bytes, 1)
        self.arquivos = self.bytes = 0
        self.inicio = time.time()
        self.ultimo_desenho = 0.0
        try:
            self.ativo = bool(e.stream.isatty())
        except (AttributeError, ValueError):
            self.ativo = False

    def avancar(self, nbytes):
        self.arquivos += 1
        self.bytes += nbytes
        agora = time.time()
        if self.ativo and (agora - self.ultimo_desenho > 0.1 or self.arquivos == self.total_arquivos):
            self.ultimo_desenho = agora
            self._desenhar(agora)

    def _desenhar(self, agora):
        frac = min(self.bytes / self.total_bytes, 1.0)
        cheio = int(frac * 24)
        barra = self.e.s["cheio"] * cheio + self.e.s["vazio"] * (24 - cheio)
        texto = f"  {barra} {frac:4.0%} {self.e.s['ponto']} {fmt_bytes(self.bytes)} de {fmt_bytes(self.total_bytes)}"
        passado = agora - self.inicio
        if 0 < frac < 1 and passado > 2:
            texto += f" {self.e.s['ponto']} falta ~{fmt_duracao(passado / frac - passado)}"
        try:
            self.e.stream.write("\r" + texto.ljust(78))
            self.e.stream.flush()
        except (UnicodeEncodeError, OSError, AttributeError, ValueError):
            self.ativo = False

    def fim(self):
        if self.ativo:
            try:
                self.e.stream.write("\r" + " " * 78 + "\r")
                self.e.stream.flush()
            except (OSError, AttributeError, ValueError):
                pass


# ------------------------------------------------------------------ volume
def rotulo_volume(raiz):
    """Nome do volume ('SSD EXTERNO'), ou '' se não der para ler."""
    if not WINDOWS:
        return ""
    try:
        nome = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(str(raiz)), nome, 261, None, None, None, None, 0):
            return nome.value
    except Exception:      # noqa: BLE001
        pass
    return ""


def nome_ssd(raiz):
    rotulo = rotulo_volume(raiz)
    letra = str(raiz).rstrip("\\/")
    return f"{rotulo} ({letra})" if rotulo else letra


# -------------------------------------------------------------- notificação
_SCRIPT_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:SYNC_SSD_TITULO)
$m = [Security.SecurityElement]::Escape($env:SYNC_SSD_TEXTO)
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def notificar(titulo, texto):
    """Notificação no canto da tela (toast). Se falhar, caixa de mensagem. -> bool."""
    if not WINDOWS:
        return False
    env = dict(os.environ, SYNC_SSD_TITULO=titulo, SYNC_SSD_TEXTO=texto)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _SCRIPT_TOAST],
            env=env, capture_output=True, timeout=20, creationflags=0x08000000)   # CREATE_NO_WINDOW
        if r.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        # MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x30 | 0x10000 | 0x40000)
        return True
    except Exception:      # noqa: BLE001 - notificação é melhor esforço
        return False


# ---------------------------------------------------------------- histórico
def registrar_historico(pasta_json, registro, copias_html=()):
    """Acrescenta `registro` ao historico.json (mantém os 200 mais recentes) e
    regenera o historico.html na mesma pasta e em `copias_html`. Nunca levanta."""
    try:
        pasta_json = Path(pasta_json)
        pasta_json.mkdir(parents=True, exist_ok=True)
        arq = pasta_json / "historico.json"
        try:
            registros = json.loads(arq.read_text(encoding="utf-8"))
            if not isinstance(registros, list):
                registros = []
        except (OSError, ValueError):
            registros = []
        registros = (registros + [registro])[-MAX_HISTORICO:]
        arq.write_text(json.dumps(registros, indent=1, ensure_ascii=False), encoding="utf-8")
        pagina = gerar_html(registros)
        for pasta in [pasta_json, *map(Path, copias_html)]:
            try:
                pasta.mkdir(parents=True, exist_ok=True)
                (pasta / "historico.html").write_text(pagina, encoding="utf-8")
            except OSError:
                pass
    except Exception:      # noqa: BLE001 - histórico nunca derruba o backup
        pass


def _detalhe(r):
    if r.get("tipo") == "conferencia":
        partes = [plural(r.get("arquivos_drive", 0), "arquivo conferido", "arquivos conferidos"),
                  plural(r.get("divergencias", 0), "divergência", "divergências")]
        if r.get("extras"):
            partes.append(f"{fmt_num(r['extras'])} só no SSD")
    else:
        partes = [plural(r.get("novos", 0), "novo", "novos"),
                  plural(r.get("atualizados", 0), "atualizado", "atualizados")]
        for chave, sing, plur in (("movidos", "movido", "movidos"),
                                  ("quarentena", "para a quarentena", "para a quarentena"),
                                  ("falhas", "falha", "falhas")):
            if r.get(chave):
                partes.append(plural(r[chave], sing, plur))
        if r.get("bytes"):
            partes.append(fmt_bytes(r["bytes"]))
    return " · ".join(partes)


def _quando(r):
    try:
        return fmt_data_hora(datetime.fromisoformat(r["quando"]))
    except (KeyError, ValueError, TypeError):
        return str(r.get("quando", ""))


def gerar_html(registros):
    esc = html.escape
    icone = {"ok": "✔", "aviso": "⚠", "erro": "✖"}
    linhas = []
    for r in reversed(registros):
        res = r.get("resultado", "erro")
        res = res if res in icone else "erro"
        tipo = "Conferência" if r.get("tipo") == "conferencia" else "Backup"
        dur = r.get("duracao_seg")
        linhas.append(
            f"<tr class='{res}'><td class='quando'>{esc(_quando(r))}</td><td>{tipo}</td>"
            f"<td class='res'><span class='ic'>{icone[res]}</span> {esc(str(r.get('frase', '')))}</td>"
            f"<td class='det'>{esc(_detalhe(r))}</td>"
            f"<td class='dur'>{esc(fmt_duracao(dur)) if isinstance(dur, (int, float)) else ''}</td></tr>")

    ultimo = {t: next((r for r in reversed(registros) if r.get("tipo") == t), None)
              for t in ("backup", "conferencia")}
    cartoes = []
    for t, titulo in (("backup", "Último backup"), ("conferencia", "Última conferência")):
        r = ultimo[t]
        if r:
            res = r.get("resultado", "erro") if r.get("resultado") in icone else "erro"
            cartoes.append(f"<div class='cartao {res}'><div class='rot'>{titulo}</div>"
                           f"<div class='val'>{icone[res]} {esc(str(r.get('frase', '')))}</div>"
                           f"<div class='sub'>{esc(_quando(r))}</div></div>")
    inv = (ultimo["backup"] or {}).get("inventario") or {}
    for chave, titulo in (("versoes", "Versões antigas"), ("quarentena", "Quarentena")):
        i = inv.get(chave)
        if i is None:
            continue
        if i.get("arquivos"):
            sub = f"{fmt_bytes(i.get('bytes', 0))}"
            if i.get("expira"):
                try:
                    sub += f" · a mais antiga sai em {fmt_data(datetime.fromisoformat(i['expira']))}"
                except ValueError:
                    pass
            val = plural(i["arquivos"], "arquivo", "arquivos")
        else:
            val, sub = "vazia", "nada guardado"
        cartoes.append(f"<div class='cartao'><div class='rot'>{titulo}</div>"
                       f"<div class='val'>{esc(val)}</div><div class='sub'>{esc(sub)}</div></div>")

    gerado = fmt_data_hora(datetime.now())
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Histórico do backup</title>
<style>
:root {{ --fundo:#f7f7f5; --papel:#fff; --texto:#1d1d1b; --suave:#6b6b66; --borda:#e4e4df;
        --ok:#1e7a46; --aviso:#9a6200; --erro:#b3261e; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fundo:#161615; --papel:#1f1f1d; --texto:#ecece8; --suave:#9d9d97; --borda:#34342f;
          --ok:#5cc98a; --aviso:#e0a948; --erro:#f08b84; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--fundo); color:var(--texto);
       font:15px/1.5 "Segoe UI", system-ui, sans-serif; }}
main {{ max-width:1080px; margin:0 auto; padding:32px 16px 48px }}
h1 {{ font-size:22px; margin:0 0 4px }}
.gerado {{ color:var(--suave); font-size:13px; margin-bottom:24px }}
.cartoes {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-bottom:28px }}
.cartao {{ background:var(--papel); border:1px solid var(--borda); border-radius:10px; padding:14px 16px }}
.cartao .rot {{ color:var(--suave); font-size:12px; text-transform:uppercase; letter-spacing:.04em }}
.cartao .val {{ font-size:16px; font-weight:600; margin:4px 0 2px }}
.cartao .sub {{ color:var(--suave); font-size:13px }}
.cartao.ok .val, tr.ok .ic {{ color:var(--ok) }}
.cartao.aviso .val, tr.aviso .ic {{ color:var(--aviso) }}
.cartao.erro .val, tr.erro .ic {{ color:var(--erro) }}
.tabela {{ overflow-x:auto; background:var(--papel); border:1px solid var(--borda); border-radius:10px }}
table {{ border-collapse:collapse; width:100%; min-width:720px }}
th, td {{ text-align:left; padding:9px 14px; border-bottom:1px solid var(--borda); vertical-align:top }}
th {{ font-size:12px; color:var(--suave); text-transform:uppercase; letter-spacing:.04em; font-weight:600 }}
tr:last-child td {{ border-bottom:0 }}
td.quando, td.dur {{ white-space:nowrap; color:var(--suave); font-variant-numeric:tabular-nums }}
td.det {{ color:var(--suave) }}
.ic {{ font-weight:700 }}
</style></head>
<body><main>
<h1>Histórico do backup</h1>
<div class="gerado">Atualizado em {esc(gerado)} · últimas {len(registros)} execuções</div>
<div class="cartoes">{''.join(cartoes)}</div>
<div class="tabela"><table>
<thead><tr><th>Quando</th><th>O quê</th><th>Resultado</th><th>Detalhes</th><th>Duração</th></tr></thead>
<tbody>
{chr(10).join(linhas)}
</tbody></table></div>
</main></body></html>
"""


def abrir(caminho):
    """Abre um arquivo com o programa padrão do Windows. -> bool."""
    try:
        os.startfile(str(caminho))      # type: ignore[attr-defined]
        return True
    except (AttributeError, OSError):
        return False


def expiracao(carimbo_mais_antigo, dias):
    """datetime em que a pasta mais antiga será removida."""
    return carimbo_mais_antigo + timedelta(days=dias)
