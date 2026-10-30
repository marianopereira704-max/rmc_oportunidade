"""Rotina dos e-mails para as lojas (pedido/emails.py, 29/10/2026). As lojas (os resultados prontos) entram como
objetos pequenos; o envio, o registro do dia, o estado da ruptura, o limite e a prévia são os de verdade."""
import datetime as dt
import types

import pandas as pd
import pytest

from pedido import emails
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves

CH = Chaves("pedido")
HOJE = dt.date(2026, 10, 30)


def _todas(*linhas):
    return pd.DataFrame([{"linha": l, "nome": f"P{l}", "curva": "AA", "curva_valor": "A", "ruptura": r,
                          "valor_90d": 100, "venda_90d": 10, "quantidade": 3} for l, r in linhas])


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    armaz = ArmazenamentoLocal(tmp_path)
    armaz.salvar_json("pedido/insumos/teste/destinatarios.json", {"destinatarios": [
        {"id": 1, "email": "dono@x.com", "nome": "Dono Reis", "nivel": "PROPRIETARIO", "linhas": None,
         "ruptura": True, "relatorio": False, "lojas": [{"id": 10}, {"id": 11}]},
        {"id": 2, "email": "compra@x.com", "nome": "Ana", "nivel": "COMPRADOR", "linhas": ["genericos"],
         "ruptura": False, "relatorio": True, "lojas": [{"id": 10}]},
    ]})
    dados = {10: ["2026-10-30|t", _todas(("1", True), ("2", False))], 11: ["2026-10-30|t", _todas(("5", True))]}

    class Loja:
        def __init__(self, armaz_, chaves, ins, loja, chave, limite, hoje):
            foto, todas = dados[loja["id"]]
            self.loja = {"id": loja["id"], "empresa_gps": f"E{loja['id']}", "loja_gps": "1", "legacy_id": loja["id"],
                         "razao_social": f"LOJA {loja['id']}"}
            self.foto, self.todas, self.d, self.motivo = foto, todas, pd.DataFrame(), None
            self.resumo = {"modo": "transicao"}

        rotulo = property(lambda s: f"{s.loja['legacy_id']} · {s.loja['razao_social']}")

        def cortes(self, linhas):
            return self.todas, self.d

    monkeypatch.setattr(emails, "Loja", Loja)
    monkeypatch.setattr(emails.precalculo, "ler_insumos", lambda a, c, b: types.SimpleNamespace(banco=b))
    monkeypatch.setattr(emails, "_relatorio", lambda lj, t, d: {"orcamento": 1, "itens": 1, "marcados": 1, "ruptura": 0, "perda": 0,
                                                                 "rotulo_perda": "Perda", "criticos": []})
    monkeypatch.setattr(emails, "_anexo", lambda lj, todas, hoje: (f"pedido_{lj.loja['id']}.xlsx", b"x"))
    caixa = []

    def enviar(destino, assunto, texto, html, anexos=()):
        if destino in getattr(enviar, "falha", ()):
            raise OSError("smtp")
        caixa.append((destino, assunto))
        enviar.anexos = list(anexos)
    return armaz, dados, caixa, enviar


def _rodar(armaz, enviar, hoje=HOJE, **k):
    return emails.rodar(armaz, CH, "chave", 0.1, hoje, "agora", enviar, "https://app", **k)[0]


def test_primeiro_dia_so_o_relatorio_e_um_email_por_pessoa(ambiente):
    armaz, dados, caixa, enviar = ambiente
    rel = _rodar(armaz, enviar)
    # O dono só tem o aviso, e no 1º dia não há foto anterior: nada a avisar. A Ana tem o relatório.
    assert caixa == [("compra@x.com", "Resumo do dia 30/10 · 10 · LOJA 10")]
    assert rel["pulados"] == {"nada a avisar": 1}


def test_foto_nova_avisa_o_que_zerou_com_as_duas_lojas_num_email(ambiente):
    armaz, dados, caixa, enviar = ambiente
    _rodar(armaz, enviar)
    dados[10] = ["2026-10-31|t", _todas(("1", True), ("2", True))]       # o 2 zerou
    dados[11] = ["2026-10-31|t", _todas(("5", True), ("6", True))]       # o 6 zerou
    caixa.clear()
    _rodar(armaz, enviar, hoje=HOJE + dt.timedelta(days=1))
    dono = [a for d, a in caixa if d == "dono@x.com"]
    assert dono == ["2 produtos importantes zeraram em 2 lojas"]


def test_nunca_duas_vezes_no_dia_e_de_novo_so_quem_falhou(ambiente):
    armaz, dados, caixa, enviar = ambiente
    enviar.falha = {"compra@x.com"}
    assert _rodar(armaz, enviar)["falhas"] == 1
    enviar.falha = set()
    _rodar(armaz, enviar)
    assert [d for d, _ in caixa] == ["compra@x.com"]
    caixa.clear()
    rel = _rodar(armaz, enviar)
    assert caixa == [] and rel["pulados"]["já recebeu hoje"] == 1


def test_limite_do_dia(ambiente, monkeypatch):
    armaz, dados, caixa, enviar = ambiente
    monkeypatch.setattr(emails, "LIMITE_DIA", 0)
    rel = _rodar(armaz, enviar)
    assert caixa == [] and rel["limite_atingido"] and rel["fora_pelo_limite"] == 1


def test_so_para_e_previa_nao_registram_nada(ambiente, tmp_path):
    armaz, dados, caixa, enviar = ambiente
    _rodar(armaz, enviar, so_para="mariano@x.com")
    assert caixa == [("mariano@x.com", "Resumo do dia 30/10 · 10 · LOJA 10")]
    assert not armaz.existe("pedido/controle/emails/teste/enviados/2026-10-30.json")
    assert not armaz.existe("pedido/controle/emails/teste/ruptura/E10/1.json")
    previa = tmp_path / "previa"
    _rodar(armaz, enviar, previa=previa)
    assert (previa / "teste_2.html").exists() and len(caixa) == 1


def test_sem_chave_ou_sem_lista_nao_envia(tmp_path, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings.pedido, "precalculo_chave", None)
    assert emails.main(["--destino", f"local:{tmp_path}"], enviar=lambda *a: None) == 1
    monkeypatch.setattr(settings.pedido, "precalculo_chave", "x")
    assert emails.main(["--destino", f"local:{tmp_path}"], enviar=lambda *a: None) == 1


def test_excel_do_pedido_vai_em_anexo_um_por_loja(ambiente):
    armaz, dados, caixa, enviar = ambiente
    _rodar(armaz, enviar)
    assert enviar.anexos == [("pedido_10.xlsx", b"x")]
    dados[10] = ["2026-10-31|t", _todas(("1", True), ("2", True))]
    dados[11] = ["2026-10-31|t", _todas(("5", True), ("6", True))]
    _rodar(armaz, enviar, hoje=HOJE + dt.timedelta(days=1))
    assert [n for n, _ in enviar.anexos] in (["pedido_10.xlsx", "pedido_11.xlsx"], ["pedido_10.xlsx"])
