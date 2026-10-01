"""
Testes do visual_ssd.py (apresentação, histórico). Só biblioteca padrão:  python -m unittest -v
"""

import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import visual_ssd as v


class TestFormatacao(unittest.TestCase):
    def test_bytes_e_numeros_em_pt_br(self):
        self.assertEqual(v.fmt_bytes(512), "512 B")
        self.assertEqual(v.fmt_bytes(17_613), "17,2 KB")
        self.assertEqual(v.fmt_bytes(3 * 1024 ** 3), "3,0 GB")
        self.assertEqual(v.fmt_num(2351), "2.351")
        self.assertEqual(v.fmt_num(7), "7")

    def test_duracao(self):
        self.assertEqual(v.fmt_duracao(0.42), "0,4 s")
        self.assertEqual(v.fmt_duracao(200), "3 min 20 s")
        self.assertEqual(v.fmt_duracao(3900), "1 h 5 min")

    def test_ha_quanto(self):
        agora = datetime(2026, 9, 30, 12, 0)
        self.assertEqual(v.ha_quanto(agora - timedelta(seconds=20), agora), "agora mesmo")
        self.assertEqual(v.ha_quanto(agora - timedelta(minutes=1), agora), "há 1 minuto")
        self.assertEqual(v.ha_quanto(agora - timedelta(minutes=5), agora), "há 5 minutos")
        self.assertEqual(v.ha_quanto(agora - timedelta(hours=18), agora), "há 18 horas")
        self.assertEqual(v.ha_quanto(agora - timedelta(days=3), agora), "há 3 dias")
        self.assertEqual(v.ha_quanto(None, agora), "nunca")

    def test_agrupar_por_pasta(self):
        itens = [("Obra\\b.md", "1 B"), ("a.txt", ""), ("Obra\\a.md", "2 B"), ("Vida\\x.md", "")]
        grupos = v.agrupar_por_pasta(itens)
        self.assertEqual([g[0] for g in grupos], ["", "Obra", "Vida"])
        self.assertEqual(grupos[1][1], [("b.md", "1 B"), ("a.md", "2 B")])   # mantém a ordem recebida


class TestEstilo(unittest.TestCase):
    def test_fora_de_terminal_nao_usa_cor(self):
        buf = io.StringIO()
        e = v.Estilo(stream=buf)
        self.assertFalse(e.cor)
        self.assertEqual(e.c("x", "verde"), "x")
        e.escrever(e.c("ok", "verde"))
        self.assertNotIn("\033", buf.getvalue())

    def test_codificacao_sem_simbolos_cai_para_ascii(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        e = v.Estilo(stream=stream)
        self.assertFalse(e.unicode)
        self.assertEqual(e.s["ok"], "OK")
        e.escrever(e.s["novo"] + " arquivo")          # não pode levantar

    def test_sem_stream_nao_levanta(self):
        e = v.Estilo(stream=None)
        e.stream = None
        e.escrever("nada")

    def test_secao_limita_e_resume(self):
        buf = io.StringIO()
        e = v.Estilo(stream=buf, cor=False, unicode=True)
        itens = [(f"Pasta\\arq{i:02}.md", "1 KB") for i in range(20)]
        v.secao(e, "novo", "Novos", itens, "verde", limite=15)
        saida = buf.getvalue()
        self.assertIn("Novos (20)", saida)
        self.assertIn("Pasta", saida)
        self.assertIn("arq14.md", saida)
        self.assertNotIn("arq15.md", saida)
        self.assertIn("e mais 5", saida)

    def test_secao_vazia_nao_imprime(self):
        buf = io.StringIO()
        v.secao(v.Estilo(stream=buf), "novo", "Novos", [], "verde")
        self.assertEqual(buf.getvalue(), "")


class TestHistorico(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="visual_ssd_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_limite_de_registros_e_html(self):
        pasta = self.tmp / "ssd" / "_sync_ssd"          # ainda não existe
        copia = self.tmp / "local"
        for i in range(205):
            v.registrar_historico(pasta, {"tipo": "backup", "quando": f"2026-09-30T10:{i // 60:02}:{i % 60:02}",
                                          "codigo": 0, "resultado": "ok", "frase": f"execução {i}"},
                                  copias_html=[copia])
        regs = json.loads((pasta / "historico.json").read_text(encoding="utf-8"))
        self.assertEqual(len(regs), 200)
        self.assertEqual(regs[-1]["frase"], "execução 204")
        self.assertEqual(regs[0]["frase"], "execução 5")
        self.assertTrue((pasta / "historico.html").exists())
        self.assertTrue((copia / "historico.html").exists())

    def test_html_escapa_e_ordena_mais_recente_primeiro(self):
        html = v.gerar_html([
            {"tipo": "backup", "quando": "2026-09-29T10:00:00", "codigo": 0, "resultado": "ok", "frase": "antigo"},
            {"tipo": "conferencia", "quando": "2026-09-30T10:00:00", "codigo": 1, "resultado": "aviso",
             "frase": "<b>perigo</b>"},
        ])
        self.assertIn("&lt;b&gt;perigo&lt;/b&gt;", html)
        self.assertNotIn("<b>perigo", html)
        tabela = html[html.index("<tbody>"):]
        self.assertLess(tabela.index("perigo"), tabela.index("antigo"))

    def test_limpeza_e_vencidos_pendentes(self):
        backup = {"tipo": "backup", "quando": "2026-09-30T10:00:00", "codigo": 0, "resultado": "ok",
                  "frase": "Tudo certo", "vencidos": 2, "politica_limpeza": "perguntar"}
        html = v.gerar_html([backup])
        self.assertIn("Passaram do prazo", html)
        self.assertIn("2 pastas", html)
        limpeza = {"tipo": "limpeza", "quando": "2026-09-30T11:00:00", "codigo": 0, "resultado": "ok",
                   "frase": "Apagado: 2 pastas (74,0 KB)", "pastas": 2, "bytes": 75776}
        html = v.gerar_html([backup, limpeza])
        self.assertIn("Limpeza", html)
        self.assertIn("2 pastas apagadas", html)
        self.assertNotIn("Passaram do prazo", html)          # a limpeza depois do backup resolveu

    def test_json_corrompido_recomeca(self):
        pasta = self.tmp / "s"
        pasta.mkdir()
        (pasta / "historico.json").write_text("{isto não é json", encoding="utf-8")
        v.registrar_historico(pasta, {"tipo": "backup", "quando": "2026-09-30T10:00:00", "codigo": 0,
                                      "resultado": "ok", "frase": "novo"})
        regs = json.loads((pasta / "historico.json").read_text(encoding="utf-8"))
        self.assertEqual([r["frase"] for r in regs], ["novo"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
