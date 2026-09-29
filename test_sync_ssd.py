"""
Testes do sync_ssd.py. Só biblioteca padrão:  python -m unittest -v

Cobrem a lógica de varredura, comparação, movidos, cópia/versionamento, limpeza
de versões e o fluxo completo do backup em pastas temporárias. A parte que fala
com o Windows (serial do volume, letras de unidade) é substituída por um stub,
então os testes rodam em qualquer sistema.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import sync_ssd as s


def escrever(caminho, conteudo=b"x", mtime=None):
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(conteudo)
    if mtime is not None:
        os.utime(caminho, (mtime, mtime))
    return caminho


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sync_ssd_test_"))
        self.origem = self.tmp / "drive"
        self.ssd = self.tmp / "ssd"
        self.origem.mkdir()
        self.ssd.mkdir()
        (self.ssd / s.PASTA_SISTEMA).mkdir()
        self.excluido = s.excluidor(s.PADRAO["excluir"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestVarrer(Base):
    def test_lista_arquivos_com_caminho_relativo(self):
        escrever(self.origem / "a.txt", b"aaa")
        escrever(self.origem / "sub" / "b.txt", b"bb")
        arqs, erros = s.varrer(self.origem, self.excluido)
        self.assertEqual(erros, [])
        self.assertEqual(set(arqs), {"a.txt", os.path.join("sub", "b.txt")})
        self.assertEqual(arqs["a.txt"][0], 3)
        self.assertFalse(arqs["a.txt"][2])   # não é placeholder

    def test_exclusoes_por_nome_e_por_caminho(self):
        escrever(self.origem / "doc.gdoc")
        escrever(self.origem / "desktop.ini")
        escrever(self.origem / "~$aberto.docx")
        escrever(self.origem / "node_modules" / "x.js")
        escrever(self.origem / "ok.txt")
        excl = s.excluidor(s.PADRAO["excluir"] + ["node_modules"])
        arqs, _ = s.varrer(self.origem, excl)
        self.assertEqual(set(arqs), {"ok.txt"})

    def test_pula_pasta_sistema_so_na_raiz(self):
        escrever(self.ssd / s.PASTA_SISTEMA / "logs" / "x.log")
        escrever(self.ssd / "projeto" / s.PASTA_SISTEMA / "dado.txt")
        escrever(self.ssd / "$RECYCLE.BIN" / "lixo")
        escrever(self.ssd / "ok.txt")
        arqs, _ = s.varrer(self.ssd, self.excluido, pular_raiz=[s.PASTA_SISTEMA])
        self.assertEqual(set(arqs), {"ok.txt", os.path.join("projeto", s.PASTA_SISTEMA, "dado.txt")})

    def test_pasta_inexistente_gera_erro_e_nao_excecao(self):
        arqs, erros = s.varrer(self.tmp / "nao_existe", self.excluido)
        self.assertEqual(arqs, {})
        self.assertEqual(len(erros), 1)


class TestComparar(unittest.TestCase):
    def test_novos_alterados_orfaos(self):
        o = {"a": (1, 100.0, False), "b": (2, 200.0, False), "c": (3, 300.0, False)}
        d = {"a": (1, 100.5, False), "b": (9, 200.0, False), "z": (1, 1.0, False)}
        novos, alterados, orfaos = s.comparar(o, d, tolerancia=2)
        self.assertEqual(novos, ["c"])
        self.assertEqual(alterados, ["b"])
        self.assertEqual(orfaos, ["z"])

    def test_tolerancia_de_data(self):
        o = {"a": (1, 100.0, False)}
        self.assertEqual(s.comparar(o, {"a": (1, 101.9, False)}, 2)[1], [])
        self.assertEqual(s.comparar(o, {"a": (1, 103.0, False)}, 2)[1], ["a"])


class TestMovidos(unittest.TestCase):
    def test_pasta_renomeada(self):
        arq_o = {"nova/x.txt": (10, 1000.0, False), "nova/y.txt": (20, 2000.0, False)}
        arq_d = {"velha/x.txt": (10, 1000.4, False), "velha/y.txt": (20, 2000.0, False)}
        mov = s.detectar_movidos(list(arq_o), list(arq_d), arq_o, arq_d, 2)
        self.assertEqual(mov, {"nova/x.txt": "velha/x.txt", "nova/y.txt": "velha/y.txt"})

    def test_ambiguidade_nao_move(self):
        arq_o = {"n1.txt": (10, 1000.0, False), "n2.txt": (10, 1000.0, False)}
        arq_d = {"v1.txt": (10, 1000.0, False), "v2.txt": (10, 1000.0, False)}
        self.assertEqual(s.detectar_movidos(list(arq_o), list(arq_d), arq_o, arq_d, 2), {})
        arq_d = {"v1.txt": (10, 1000.0, False)}
        self.assertEqual(s.detectar_movidos(list(arq_o), list(arq_d), arq_o, arq_d, 2), {})

    def test_nome_desempata_quando_tamanho_e_data_coincidem(self):
        # exportação em lote: mesmo tamanho e mesma data, nomes diferentes
        arq_o = {"nova/a.png": (10, 1000.0, False), "nova/b.png": (10, 1000.0, False)}
        arq_d = {"velha/a.png": (10, 1000.0, False), "velha/b.png": (10, 1000.0, False)}
        mov = s.detectar_movidos(list(arq_o), list(arq_d), arq_o, arq_d, 2)
        self.assertEqual(mov, {"nova/a.png": "velha/a.png", "nova/b.png": "velha/b.png"})
        # mesmo nome nos dois órfãos: continua ambíguo, não move
        arq_d = {"v1/a.png": (10, 1000.0, False), "v2/a.png": (10, 1000.0, False)}
        self.assertEqual(s.detectar_movidos(["nova/a.png"], list(arq_d), arq_o, arq_d, 2), {})

    def test_data_dentro_da_tolerancia_casa(self):
        arq_o = {"n.txt": (10, 1000.9, False)}
        arq_d = {"v.txt": (10, 1002.8, False)}
        self.assertEqual(s.detectar_movidos(["n.txt"], ["v.txt"], arq_o, arq_d, 2), {"n.txt": "v.txt"})
        arq_d = {"v.txt": (10, 1003.0, False)}
        self.assertEqual(s.detectar_movidos(["n.txt"], ["v.txt"], arq_o, arq_d, 2), {})

    def test_arquivo_vazio_e_veto_do_confirmador(self):
        arq_o = {"n.txt": (0, 1000.0, False), "m.txt": (5, 50.0, False)}
        arq_d = {"v.txt": (0, 1000.0, False), "w.txt": (5, 50.0, False)}
        mov = s.detectar_movidos(list(arq_o), list(arq_d), arq_o, arq_d, 2)
        self.assertEqual(mov, {"m.txt": "w.txt"})
        mov = s.detectar_movidos(list(arq_o), list(arq_d), arq_o, arq_d, 2, confirmar=lambda n, v: False)
        self.assertEqual(mov, {})


class TestCopiar(Base):
    def test_copia_preserva_data_e_nao_deixa_tmp(self):
        src = escrever(self.origem / "a.txt", b"conteudo", mtime=1_600_000_000)
        dst = self.ssd / "a.txt"
        s.copiar(src, dst)
        self.assertEqual(dst.read_bytes(), b"conteudo")
        self.assertAlmostEqual(dst.stat().st_mtime, 1_600_000_000, delta=2)
        self.assertEqual([p.name for p in self.ssd.iterdir() if p.name.endswith(s.SUFIXO_TMP)], [])

    def test_versao_antiga_vai_para_pasta_de_versoes(self):
        src = escrever(self.origem / "a.txt", b"novo")
        dst = escrever(self.ssd / "a.txt", b"velho")
        antigo = self.ssd / s.PASTA_SISTEMA / "versoes" / "2026-01-01_000000" / "a.txt"
        s.copiar(src, dst, antigo=antigo)
        self.assertEqual(dst.read_bytes(), b"novo")
        self.assertEqual(antigo.read_bytes(), b"velho")

    def test_sobrescreve_destino_somente_leitura(self):
        src = escrever(self.origem / "a.txt", b"novo")
        dst = escrever(self.ssd / "a.txt", b"velho")
        os.chmod(dst, 0o444)
        s.copiar(src, dst)
        self.assertEqual(dst.read_bytes(), b"novo")

    def test_mesmo_conteudo_rapido(self):
        a = escrever(self.origem / "a", b"0" * 200_000 + b"fim")
        b = escrever(self.origem / "b", b"0" * 200_000 + b"fim")
        c = escrever(self.origem / "c", b"0" * 200_000 + b"FIM")
        self.assertTrue(s.mesmo_conteudo_rapido(a, b))
        self.assertFalse(s.mesmo_conteudo_rapido(a, c))
        self.assertFalse(s.mesmo_conteudo_rapido(a, self.origem / "nao_existe"))

    def test_remover_pastas_vazias_para_no_limite(self):
        fundo = self.ssd / "a" / "b" / "c"
        fundo.mkdir(parents=True)
        s.remover_pastas_vazias(fundo, self.ssd)
        self.assertFalse((self.ssd / "a").exists())
        self.assertTrue(self.ssd.exists())


class TestLimparVersoes(Base):
    def _versao(self, quando, tamanho=10):
        p = self.ssd / s.PASTA_SISTEMA / "versoes" / quando.strftime(s.FORMATO_CARIMBO)
        escrever(p / "f.bin", b"x" * tamanho)
        return p

    def test_remove_pela_data_do_nome_e_nao_pelo_mtime(self):
        agora = datetime(2026, 6, 1)
        velha = self._versao(agora - timedelta(days=100))
        nova = self._versao(agora - timedelta(days=10))
        estranha = self.ssd / s.PASTA_SISTEMA / "versoes" / "manual"
        estranha.mkdir()
        # mtime da pasta velha é "agora": a limpeza deve olhar o nome, não o mtime
        os.utime(velha, None)
        removidas = s.limpar_versoes(self.ssd / s.PASTA_SISTEMA / "versoes", 90, agora=agora)
        self.assertEqual(removidas, [velha.name])
        self.assertTrue(nova.exists())
        self.assertTrue(estranha.exists())

    def test_teto_por_tamanho_remove_as_mais_antigas(self):
        agora = datetime(2026, 6, 1)
        v1 = self._versao(agora - timedelta(days=3), 100)
        v2 = self._versao(agora - timedelta(days=2), 100)
        v3 = self._versao(agora - timedelta(days=1), 100)
        removidas = s.limpar_versoes(self.ssd / s.PASTA_SISTEMA / "versoes", 90, max_bytes=250, agora=agora)
        self.assertEqual(removidas, [v1.name])
        self.assertTrue(v2.exists() and v3.exists())


class TestFluxoCompleto(Base):
    """Roda main() de ponta a ponta com o SSD 'encontrado' por um stub."""

    def setUp(self):
        super().setUp()
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps({
            "serial": "TESTE", "id": "id-teste", "origem": str(self.origem),
            "limite_abs": 3, "limite_pct": 0.5, "margem_espaco_gb": 0,
        }), encoding="utf-8")
        self._orig = (s.localizar_ssd, s.LOCK, s.LOG_LOCAL, s.ESTADO_LOCAL)
        s.localizar_ssd = lambda cfg: self.ssd if cfg["serial"] == "TESTE" else None
        s.LOCK = self.tmp / "sync.lock"
        s.LOG_LOCAL = self.tmp / "local" / "sync.log"
        s.ESTADO_LOCAL = self.tmp / "local" / s.ARQ_ESTADO
        escrever(self.ssd / s.PASTA_SISTEMA / s.ARQ_IDENTIDADE, b"id=id-teste")

    def tearDown(self):
        s.localizar_ssd, s.LOCK, s.LOG_LOCAL, s.ESTADO_LOCAL = self._orig
        super().tearDown()

    def rodar(self, *args):
        s.log.linhas.clear()
        return s.main(["--config", str(self.config), *args])

    def test_backup_simples_e_versionamento(self):
        escrever(self.origem / "a.txt", b"v1", mtime=1_600_000_000)
        escrever(self.origem / "p" / "b.txt", b"bbb")
        escrever(self.origem / "doc.gdoc")
        self.assertEqual(self.rodar(), 0)
        self.assertEqual((self.ssd / "a.txt").read_bytes(), b"v1")
        self.assertEqual((self.ssd / "p" / "b.txt").read_bytes(), b"bbb")
        self.assertFalse((self.ssd / "doc.gdoc").exists())

        # segunda execução sem mudanças: nada copiado
        self.assertEqual(self.rodar(), 0)
        self.assertIn("novos: 0 | alterados: 0", "\n".join(s.log.linhas))

        # alteração: versão antiga guardada
        escrever(self.origem / "a.txt", b"v2 maior", mtime=1_600_000_500)
        self.assertEqual(self.rodar(), 0)
        self.assertEqual((self.ssd / "a.txt").read_bytes(), b"v2 maior")
        versoes = list((self.ssd / s.PASTA_SISTEMA / "versoes").rglob("a.txt"))
        self.assertEqual(len(versoes), 1)
        self.assertEqual(versoes[0].read_bytes(), b"v1")

        # estado gravado nos dois lados
        est = json.loads(s.ESTADO_LOCAL.read_text(encoding="utf-8"))
        self.assertEqual(est["codigo"], 0)
        self.assertEqual(est["copiados"], 1)
        self.assertTrue(est["ultimo_backup"])
        self.assertTrue((self.ssd / s.PASTA_SISTEMA / s.ARQ_ESTADO).exists())
        self.assertTrue(s.LOG_LOCAL.exists())
        self.assertTrue(list((self.ssd / s.PASTA_SISTEMA / "logs").glob("*.log")))

    def test_simular_nao_toca_no_ssd(self):
        escrever(self.origem / "a.txt", b"v1")
        self.assertEqual(self.rodar("--simular"), 0)
        self.assertFalse((self.ssd / "a.txt").exists())
        self.assertFalse((self.ssd / s.PASTA_SISTEMA / "logs").exists())

    def test_pasta_renomeada_e_movida_no_ssd(self):
        for i in range(5):
            escrever(self.origem / "antiga" / f"f{i}.bin", bytes([i]) * 1000, mtime=1_600_000_000 + i)
        self.assertEqual(self.rodar("--forcar"), 0)   # primeiro backup: tudo é novo, precisa de --forcar
        (self.origem / "antiga").rename(self.origem / "nova")
        self.assertEqual(self.rodar(), 0)   # 5 "novos" + 5 órfãos casariam a trava (limite 3); movidos não contam
        self.assertIn("movidos: 5 | órfãos: 0", "\n".join(s.log.linhas))
        self.assertFalse((self.ssd / "antiga").exists())   # pasta vazia removida
        for i in range(5):
            self.assertEqual((self.ssd / "nova" / f"f{i}.bin").read_bytes(), bytes([i]) * 1000)
        est = json.loads(s.ESTADO_LOCAL.read_text(encoding="utf-8"))
        self.assertEqual((est["movidos"], est["copiados"]), (5, 0))

    def test_trava_de_seguranca_e_forcar(self):
        for i in range(4):
            escrever(self.origem / f"f{i}.txt", b"x")
        self.assertEqual(self.rodar(), 5)             # SSD vazio: 100% novo aciona a trava
        self.assertEqual(self.rodar("--forcar"), 0)
        for i in range(4):
            escrever(self.origem / f"f{i}.txt", b"criptografado!!")
        self.assertEqual(self.rodar(), 5)
        self.assertEqual((self.ssd / "f0.txt").read_bytes(), b"x")
        self.assertEqual(self.rodar("--forcar"), 0)
        self.assertEqual((self.ssd / "f0.txt").read_bytes(), b"criptografado!!")

    def test_orfaos_so_lista_e_quarentena_move(self):
        escrever(self.origem / "fica.txt", b"1")
        escrever(self.origem / "sai.txt", b"2")
        self.assertEqual(self.rodar(), 0)
        (self.origem / "sai.txt").unlink()
        escrever(self.origem / "novo.txt", b"3")

        self.assertEqual(self.rodar("--orfaos"), 0)
        self.assertIn("ORFAO    sai.txt", "\n".join(s.log.linhas))
        self.assertTrue((self.ssd / "sai.txt").exists())
        self.assertFalse((self.ssd / "novo.txt").exists())   # --orfaos é só leitura

        self.assertEqual(self.rodar("--quarentena"), 0)
        self.assertFalse((self.ssd / "sai.txt").exists())
        self.assertTrue((self.ssd / "novo.txt").exists())
        self.assertEqual(list((self.ssd / s.PASTA_SISTEMA / "versoes").rglob("sai.txt"))[0].read_bytes(), b"2")

    def test_verificar_hash_detecta_corrupcao(self):
        escrever(self.origem / "a.txt", b"integro", mtime=1_600_000_000)
        self.assertEqual(self.rodar("--verificar", "0"), 0)
        # corrompe o SSD mantendo tamanho e data (bit rot)
        escrever(self.ssd / "a.txt", b"INTEGRO", mtime=1_600_000_000)
        self.assertEqual(self.rodar("--verificar", "0"), 8)
        self.assertIn("DIVERGE  a.txt", "\n".join(s.log.linhas))

    def test_origem_vazia_e_indisponivel(self):
        self.assertEqual(self.rodar(), 4)
        shutil.rmtree(self.origem)
        self.assertEqual(self.rodar(), 3)

    def test_ssd_ausente_e_alerta(self):
        s.localizar_ssd = lambda cfg: None
        self.assertEqual(self.rodar(), 2)
        self.assertEqual(self.rodar("--alertar-se-velho", "7"), 9)     # nunca fez backup -> alerta
        self.assertEqual(self.rodar("--alertar-se-velho", "7"), 9)     # de novo, dentro do cooldown
        est = json.loads(s.ESTADO_LOCAL.read_text(encoding="utf-8"))
        self.assertTrue(est.get("ultimo_alerta"))

    def test_lock_impede_execucao_concorrente(self):
        s.LOCK.write_text("123")
        escrever(self.origem / "a.txt", b"1")
        self.assertEqual(self.rodar(), 7)
        velho = time.time() - 24 * 3600
        os.utime(s.LOCK, (velho, velho))
        self.assertEqual(self.rodar(), 0)          # lock abandonado é descartado
        self.assertFalse(s.LOCK.exists())

    def test_config_ausente(self):
        self.config.unlink()
        self.assertEqual(self.rodar(), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
