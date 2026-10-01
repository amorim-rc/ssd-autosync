"""
Testes do conferir_ssd.py. Só biblioteca padrão:  python -m unittest -v

O último teste roda o sync_ssd.py de verdade e confere o resultado com o
conferir_ssd.py: as duas ferramentas têm código independente e precisam
concordar sobre o que é "SSD idêntico ao Drive".
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import conferir_ssd as c
import visual_ssd as v


def escrever(caminho, conteudo=b"x", mtime=1_600_000_000):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(conteudo)
    if mtime is not None:
        os.utime(caminho, (mtime, mtime))
    return caminho


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="conferir_ssd_test_"))
        self.origem = self.tmp / "drive"
        self.ssd = self.tmp / "ssd"
        self.origem.mkdir()
        (self.ssd / c.PASTA_SISTEMA).mkdir(parents=True)
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps({"serial": "TESTE", "id": "id-teste",
                                           "origem": str(self.origem)}), encoding="utf-8")
        self._orig = (c.localizar_ssd, c.LOG_LOCAL, c.RESULTADO_LOCAL, c.PASTA_LOCAL, v.notificar)
        c.localizar_ssd = lambda cfg: self.ssd if cfg["serial"] == "TESTE" else None
        c.PASTA_LOCAL = self.tmp / "local"
        c.LOG_LOCAL = c.PASTA_LOCAL / "conferencia.log"
        c.RESULTADO_LOCAL = c.PASTA_LOCAL / "ultima_conferencia.json"
        self.notificacoes = []
        v.notificar = lambda titulo, texto: self.notificacoes.append((titulo, texto)) or True

    def tearDown(self):
        c.localizar_ssd, c.LOG_LOCAL, c.RESULTADO_LOCAL, c.PASTA_LOCAL, v.notificar = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def par(self, rel, conteudo=b"x", mtime=1_600_000_000):
        escrever(self.origem / rel, conteudo, mtime)
        escrever(self.ssd / rel, conteudo, mtime)

    def rodar(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            codigo = c.main(["--config", str(self.config), *args])
        self.saida = buf.getvalue()
        return codigo

    def resultado(self):
        return json.loads(c.RESULTADO_LOCAL.read_text(encoding="utf-8"))


class TestConferir(unittest.TestCase):
    def test_classifica_cada_tipo(self):
        drive = {"ok": (1, 100.0, False), "falta": (1, 100.0, False), "tam": (1, 100.0, False),
                 "velho": (1, 200.0, False), "novo": (1, 100.0, False), "nb": (1, 100.0, True)}
        ssd = {"ok": (1, 101.5, False), "tam": (2, 100.0, False), "velho": (1, 100.0, False),
               "novo": (1, 200.0, False), "extra": (1, 1.0, False)}
        r = c.conferir(drive, ssd, tolerancia=2)
        self.assertEqual(r["faltando"], [("falta", ""), ("nb", "ainda não baixado pelo Google Drive")])
        self.assertEqual(r["tamanho"], [("tam", "Drive 1 B, SSD 2 B")])
        self.assertEqual(r["desatualizado"], [("velho", "")])
        self.assertEqual(r["mais_novo"], [("novo", "")])
        self.assertEqual(r["extras"], [("extra", "")])


class TestListar(Base):
    def test_exclusoes_e_pastas_de_sistema_na_raiz(self):
        escrever(self.ssd / "ok.txt")
        escrever(self.ssd / "planilha.gsheet")
        escrever(self.ssd / "sub" / "~$aberto.docx")
        escrever(self.ssd / c.PASTA_SISTEMA / "logs" / "x.log")
        escrever(self.ssd / "$RECYCLE.BIN" / "lixo")
        escrever(self.ssd / "projeto" / c.PASTA_SISTEMA / "dado.txt")   # só a raiz é ignorada
        escrever(self.ssd / "node_modules" / "x.js")
        arqs, erros = c.listar(self.ssd, c.EXCLUIR_PADRAO + ["node_modules"], c.RAIZ_IGNORADA)
        self.assertEqual(erros, [])
        self.assertEqual(set(arqs), {"ok.txt", os.path.join("projeto", c.PASTA_SISTEMA, "dado.txt")})


class TestFluxo(Base):
    def test_identico(self):
        self.par("a.txt", b"aaa")
        self.par(os.path.join("pasta funda", "b.docx"), b"bb")
        escrever(self.origem / "doc.gdoc")          # atalho de Google Docs: não conta
        escrever(self.ssd / c.PASTA_SISTEMA / "versoes" / "x" / "velho.txt")
        self.assertEqual(self.rodar(), 0)
        r = self.resultado()
        self.assertEqual((r["codigo"], r["arquivos_drive"], r["arquivos_ssd"]), (0, 2, 2))

    def test_divergencias_dao_codigo_1(self):
        self.par("ok.txt")
        escrever(self.origem / "falta.txt")
        escrever(self.origem / "editado.docx", b"v2 maior", mtime=1_600_000_500)
        escrever(self.ssd / "editado.docx", b"v1", mtime=1_600_000_000)
        self.assertEqual(self.rodar(), 1)
        r = self.resultado()
        self.assertEqual((r["faltando"], r["tamanho"]), (1, 1))

    def test_extras_nao_sao_divergencia(self):
        self.par("a.txt")
        escrever(self.ssd / "saiu_do_drive.txt")
        self.assertEqual(self.rodar(), 0)
        self.assertEqual(self.resultado()["extras"], 1)

    def test_nao_altera_nada(self):
        escrever(self.origem / "falta.txt", b"1")
        escrever(self.ssd / "extra.txt", b"2")
        antes = sorted(p.relative_to(self.tmp) for p in self.tmp.rglob("*") if "local" not in p.parts)
        self.rodar()
        proprios = {"historico.json", "historico.html"}      # registros da própria conferência
        depois = sorted(p.relative_to(self.tmp) for p in self.tmp.rglob("*")
                        if "local" not in p.parts and "logs" not in p.parts and p.name not in proprios)
        self.assertEqual(antes, depois)

    def test_log_nos_dois_lados(self):
        self.par("a.txt")
        self.rodar()
        self.assertTrue(c.LOG_LOCAL.exists())
        self.assertTrue(list((self.ssd / c.PASTA_SISTEMA / "logs").glob("conferencia-*.log")))

    def test_ssd_ausente_origem_indisponivel_vazia_e_sem_config(self):
        self.assertEqual(self.rodar(), 4)                    # origem vazia
        shutil.rmtree(self.origem)
        self.assertEqual(self.rodar(), 3)                    # origem sumiu
        c.localizar_ssd = lambda cfg: None
        self.assertEqual(self.rodar(), 2)                    # SSD não plugado
        self.config.unlink()
        self.assertEqual(self.rodar(), 10)                   # sem registro


class TestModosDeSaida(Base):
    def _divergente(self):
        self.par("ok.txt")
        escrever(self.origem / "Obra" / "falta.md")
        escrever(self.ssd / "saiu.txt")

    def test_tela(self):
        self.par("a.txt")
        self.assertEqual(self.rodar(), 0)
        self.assertIn("SSD idêntico ao Drive", self.saida)
        self.assertNotIn("\033", self.saida)
        self._divergente()
        self.assertEqual(self.rodar(), 1)
        self.assertIn("Faltando no SSD (1)", self.saida)
        self.assertIn("falta.md", self.saida)
        self.assertIn("só no SSD", self.saida)

    def test_detalhado(self):
        self._divergente()
        self.assertEqual(self.rodar("--detalhado"), 1)
        self.assertIn("FALTANDO NO SSD: 1", self.saida)

    def test_json(self):
        self._divergente()
        self.assertEqual(self.rodar("--json"), 1)
        d = json.loads(self.saida)
        self.assertEqual((d["codigo"], d["resultado"], d["divergencias"]), (1, "aviso", 1))
        self.assertEqual(d["faltando"], [{"caminho": os.path.join("Obra", "falta.md"), "nota": ""}])
        self.assertEqual(d["extras"], [{"caminho": "saiu.txt", "nota": ""}])
        c.localizar_ssd = lambda cfg: None
        self.assertEqual(self.rodar("--json"), 2)
        self.assertEqual(json.loads(self.saida)["codigo"], 2)

    def test_silencioso(self):
        self.par("a.txt")
        self.assertEqual(self.rodar("--silencioso"), 0)
        self.assertEqual((self.saida, self.notificacoes), ("", []))
        self._divergente()
        self.assertEqual(self.rodar("--silencioso"), 1)
        self.assertEqual(self.saida, "")
        self.assertEqual(len(self.notificacoes), 1)

    def test_historico_registra_conferencia(self):
        self.par("a.txt")
        self.rodar()
        regs = json.loads((self.ssd / c.PASTA_SISTEMA / "historico.json").read_text(encoding="utf-8"))
        self.assertEqual((regs[-1]["tipo"], regs[-1]["resultado"], regs[-1]["divergencias"]),
                         ("conferencia", "ok", 0))
        self.assertTrue((c.PASTA_LOCAL / "historico.html").exists())


class TestConcordaComOBackup(Base):
    """sync_ssd.py faz o backup; conferir_ssd.py tem que dizer 'idêntico'."""

    def test_backup_seguido_de_conferencia(self):
        import sync_ssd as s
        cfg = json.loads(self.config.read_text(encoding="utf-8"))
        cfg.update(limite_abs=10_000, margem_espaco_gb=0)
        self.config.write_text(json.dumps(cfg), encoding="utf-8")
        escrever(self.ssd / c.PASTA_SISTEMA / c.ARQ_IDENTIDADE, b"id=id-teste")
        orig = (s.localizar_ssd, s.LOCK, s.LOG_LOCAL, s.ESTADO_LOCAL, s.PASTA_LOCAL)
        s.localizar_ssd = lambda cfg: self.ssd
        s.LOCK, s.LOG_LOCAL = self.tmp / "sync.lock", self.tmp / "local" / "sync.log"
        s.ESTADO_LOCAL = self.tmp / "local" / "estado.json"
        s.PASTA_LOCAL = self.tmp / "local"

        def backup(*args):
            with contextlib.redirect_stdout(io.StringIO()):
                return s.main(["--config", str(self.config), "--silencioso", *args])
        try:
            for i in range(20):
                escrever(self.origem / f"p{i % 4}" / f"arq {i}.txt", b"x" * i, mtime=1_600_000_000 + i)
            escrever(self.origem / "planilha.gsheet")
            self.assertEqual(self.rodar(), 1)                              # antes do backup
            self.assertEqual(backup(), 0)                                  # backup
            self.assertEqual(self.rodar(), 0)                              # depois: idêntico
            escrever(self.origem / "p0" / "arq 0.txt", b"editado", mtime=1_600_009_999)
            self.assertEqual(self.rodar(), 1)                              # edição pega
            self.assertEqual(backup(), 0)
            self.assertEqual(self.rodar(), 0)
            (self.origem / "p1" / "arq 1.txt").unlink()                    # apagado no Drive
            self.assertEqual(self.rodar(), 0)                              # extra no SSD não é divergência
            self.assertEqual(backup("--quarentena"), 0)
            self.assertEqual(self.rodar("--json"), 0)
            self.assertEqual(json.loads(self.saida)["extras"], [])         # quarentena tirou do espelho
        finally:
            s.localizar_ssd, s.LOCK, s.LOG_LOCAL, s.ESTADO_LOCAL, s.PASTA_LOCAL = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
