"""Fila de EAN pelas compras da API (integrations/fila_compras.py,
05/10/2026): só genérico comprado e fora da Base Genéricos entra; o valor é
o comprado no período, regravado."""
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, BaseGenerico, EanGenerico, FilaResolucaoEAN, OrigemFila, OrigemResolucao, StatusFila
from integrations import fila_compras
from pedido import categorias as cat
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import Chaves

BASE = pd.DataFrame([("111", cat.GENERICO), ("222", cat.GENERICO), ("333", "HIGIENE"), ("444", cat.GENERICO),
                     ("555", cat.GENERICO)], columns=["ean", "categoria"])


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        g = BaseGenerico(nome_canonico="DIPIRONA SODICA 500MG 10 COMPRIMIDOS")
        s.add(g)
        s.flush()
        s.add(EanGenerico(ean="444", base_generico_id=g.id, origem_resolucao=OrigemResolucao.IMPORTADA,
                          descricao_origem_snapshot="x", resolvido_por="admin"))
        s.add(FilaResolucaoEAN(ean="555", descricao_observada="DA GRUPPY", origem=OrigemFila.GRUPPY,
                               valor_total_acumulado=0, qtd_ocorrencias=0))
        s.add(FilaResolucaoEAN(ean="999", descricao_observada="DA PLANILHA", origem=OrigemFila.GPS,
                               valor_total_acumulado=80, qtd_ocorrencias=4))
        s.add(FilaResolucaoEAN(ean="888", descricao_observada="LOJA", origem=OrigemFila.LOJA_API,
                               valor_total_acumulado=70, qtd_ocorrencias=2))
        s.flush()
        yield s


def _resumo(linhas):
    return pd.DataFrame(linhas, columns=["ean", "nome", "valor", "compras", "lojas"])


def test_so_generico_comprado_fora_da_base_entra_e_o_valor_e_regravado(session):
    r = fila_compras.atualizar(session, _resumo([
        ("111", "DIPIRONA SODICA 500MG 10 COMPRIMIDOS", 300.0, 5, 3),   # mesmo nome: resolve sozinho (≥ 92)
        ("222", "XPTO QUALQUER", 120.0, 2, 1),                           # genérico sem parecido: fila
        ("333", "SABONETE", 99.0, 1, 1),                                 # não é genérico: compara pelo EAN
        ("444", "DIPIRONA", 10.0, 1, 1),                                 # já na Base Genéricos
        ("555", "DA GRUPPY", 45.0, 3, 2),                                # já na fila pela Gruppy: ganha valor
        ("888", "LOJA", 1.0, 1, 1),                                      # Loja (API): valor próprio, não mexe
    ]), BASE)
    assert (r.genericos_comprados, r.ja_na_base, r.novos_na_fila, r.resolvidos_automaticamente) == (4, 1, 1, 1)
    itens = {i.ean: i for i in session.query(FilaResolucaoEAN)}
    assert itens["222"].origem == OrigemFila.GPS and itens["222"].status == StatusFila.PENDENTE
    assert float(itens["222"].valor_total_acumulado) == 120 and itens["222"].qtd_ocorrencias == 2
    assert "111" not in itens and "333" not in itens
    assert float(itens["555"].valor_total_acumulado) == 45 and itens["555"].origem == OrigemFila.GRUPPY
    assert float(itens["999"].valor_total_acumulado) == 0                      # não comprado no período
    assert float(itens["888"].valor_total_acumulado) == 70
    # De novo: nada novo, valores regravados (não somados).
    r2 = fila_compras.atualizar(session, _resumo([("222", "XPTO QUALQUER", 120.0, 2, 1)]), BASE)
    assert r2.novos_na_fila == 0
    assert float(session.get(FilaResolucaoEAN, itens["222"].id).valor_total_acumulado) == 120


def test_resumo_soma_os_meses(tmp_path):
    armaz, ch = ArmazenamentoLocal(tmp_path), Chaves("pedido")
    armaz.salvar_df(ch.compras_rede_resumo("2026-07"), _resumo([("1", "A", 10.0, 1, 1)]))
    armaz.salvar_df(ch.compras_rede_resumo("2026-08"), _resumo([("1", "A", 5.0, 2, 3), ("2", "B", 1.0, 1, 1)]))
    r = fila_compras.resumo_dos_meses(armaz, ch, ["2026-06", "2026-07", "2026-08"]).set_index("ean")
    assert r.loc["1", "valor"] == 15 and r.loc["1", "compras"] == 3 and r.loc["2", "valor"] == 1
