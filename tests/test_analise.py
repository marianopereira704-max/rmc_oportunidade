"""Regras da Análise de Oportunidade (core/analise.py), decididas em 09/2026.

`calcular` é pura: os testes montam as compras à mão e conferem cada regra.
No fim, um teste de ponta a ponta contra SQLite prova a consulta ao banco
(filtro por laboratório, UF coberta, EAN resolvido) e a junção com a loja.
"""
from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core import analise
from core.models import (
    Base,
    BaseGenerico,
    EanGenerico,
    ItemTabelaGruppy,
    Loja,
    ModoCustoGruppy,
    OrigemResolucao,
    RegistroCompraGPS,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
)

AGO, JUL, JUN, MAI = "2026-08", "2026-07", "2026-06", "2026-05"
PRECO_LAB = 8.0


def _compra(lab="SANDOZ", vlr=10.0, qtd=10, mes=AGO, loja=1, bg=1, cmv=None, custo_medio=None, preco=PRECO_LAB):
    return {
        "loja_id": loja, "base_generico_id": bg, "nome_canonico": f"GENERICO {bg}", "ano_mes": mes,
        "laboratorio_compra": lab, "descricao_origem": f"PRODUTO {lab}", "quantidade": float(qtd),
        "custo_unitario": float(vlr), "custo_cmv_unitario": cmv, "custo_medio_planilha": custo_medio,
        "preco_laboratorio": preco,
    }


def _calcular(compras, periodo=(AGO,), media=(AGO, JUL, JUN)):
    df = pd.DataFrame(compras)
    for coluna in ("custo_cmv_unitario", "custo_medio_planilha"):
        df[coluna] = df[coluna].astype(float)
    resultado = analise.calcular(df, list(periodo), list(media), limite_bonificacao=0.10, tolerancia=0.5)
    return resultado.set_index(["loja_id", "base_generico_id"])


# ---------------------------------------------------------------------------
# Vencedor, quantidade, diferença e economia
# ---------------------------------------------------------------------------

def test_menor_preco_pago_vence_e_so_a_quantidade_dele_conta():
    """10 un. Medley a R$ 10 e 30 un. Sandoz a R$ 9 contra laboratório a
    R$ 8: referência é a Sandoz, Qtd. = 30 (só a do vencedor), diferença =
    R$ 1, economia = R$ 30. A compra da Medley não entra na conta."""
    linha = _calcular([_compra("MEDLEY", 10.0, 10), _compra("SANDOZ", 9.0, 30)]).loc[(1, 1)]
    assert linha["laboratorio"] == "SANDOZ"
    assert linha["preco_pago"] == pytest.approx(9.0)
    assert linha["quantidade"] == 30
    assert linha["diferenca"] == pytest.approx(1.0)
    assert linha["economia"] == pytest.approx(30.0)


def test_loja_que_ja_paga_menos_tem_diferenca_negativa_e_economia_zero():
    linha = _calcular([_compra("MEDLEY", 10.0, 10), _compra("SANDOZ", 6.0, 10)]).loc[(1, 1)]
    assert linha["diferenca"] == pytest.approx(-2.0)
    assert linha["economia"] == 0


def test_empate_no_menor_preco_soma_as_linhas_e_junta_os_laboratorios():
    linha = _calcular([_compra("TEUTO", 9.0, 4), _compra("SANDOZ", 9.0, 6)]).loc[(1, 1)]
    assert linha["laboratorio"] == "SANDOZ / TEUTO"
    assert linha["quantidade"] == 10
    assert linha["economia"] == pytest.approx(10.0)


def test_periodo_maior_usa_a_linha_de_menor_preco_e_so_a_quantidade_dela():
    """Q21: Sandoz a R$ 6 em julho (10 un.) e a R$ 7 em agosto (30 un.) —
    com 2 meses, a referência é julho e a quantidade é só a de julho."""
    compras = [_compra("SANDOZ", 7.0, 30, AGO), _compra("SANDOZ", 6.0, 10, JUL)]
    linha = _calcular(compras, periodo=(AGO, JUL)).loc[(1, 1)]
    assert linha["preco_pago"] == pytest.approx(6.0)
    assert linha["quantidade"] == 10


def test_compra_fora_do_periodo_nao_disputa():
    linha = _calcular([_compra("SANDOZ", 9.0, 10, AGO), _compra("TEUTO", 6.0, 10, JUL)], periodo=(AGO,)).loc[(1, 1)]
    assert linha["laboratorio"] == "SANDOZ"


# ---------------------------------------------------------------------------
# Bonificação e recuo de preço
# ---------------------------------------------------------------------------

def test_bonificacao_fica_fora_da_disputa_e_e_contada():
    linha = _calcular([_compra("RANBAXY", 0.01, 5), _compra("SANDOZ", 9.0, 10)]).loc[(1, 1)]
    assert linha["laboratorio"] == "SANDOZ"
    assert linha["bonificacoes"] == 1


def test_bonificacao_vem_antes_do_recuo():
    """R$ 0,01 também está "fora de ±50%" — se o recuo viesse antes, ele a
    transformaria num preço normal e ela deixaria de ser bonificação."""
    resultado = _calcular([_compra("RANBAXY", 0.01, 5, cmv=8.5)])
    assert resultado.empty


def test_vlrunitario_coerente_e_usado_direto():
    linha = _calcular([_compra(vlr=11.0, cmv=9.0, custo_medio=9.5)]).loc[(1, 1)]
    assert linha["fonte"] == analise.FONTE_VLR
    assert linha["preco_pago"] == pytest.approx(11.0)


def test_vlrunitario_fora_usa_o_custo_cmv():
    linha = _calcular([_compra(vlr=184.77, cmv=9.0, custo_medio=9.5)]).loc[(1, 1)]
    assert linha["fonte"] == analise.FONTE_CMV
    assert linha["preco_pago"] == pytest.approx(9.0)
    assert linha["vlr_unitario_original"] == pytest.approx(184.77)
    assert linha["economia"] == pytest.approx(10.0)


def test_sem_venda_usa_o_custo_medio():
    linha = _calcular([_compra(vlr=184.77, cmv=None, custo_medio=9.5)]).loc[(1, 1)]
    assert linha["fonte"] == analise.FONTE_CUSTO_MEDIO
    assert linha["preco_pago"] == pytest.approx(9.5)


def test_cmv_tambem_fora_cai_pro_custo_medio():
    linha = _calcular([_compra(vlr=184.77, cmv=90.0, custo_medio=9.5)]).loc[(1, 1)]
    assert linha["fonte"] == analise.FONTE_CUSTO_MEDIO


def test_nenhum_preco_coerente_fica_a_revisar_com_economia_zero():
    linha = _calcular([_compra(vlr=184.77, cmv=90.0, custo_medio=None)]).loc[(1, 1)]
    assert linha["fonte"] == analise.FONTE_FORA
    assert linha["preco_pago"] == pytest.approx(184.77)
    assert linha["diferenca"] == pytest.approx(176.77)
    assert linha["economia"] == 0


def test_compra_fora_do_padrao_nao_esconde_compra_coerente():
    """Q29: a compra "fora do padrão" (R$ 2, abaixo de ±50%) seria a de
    menor preço — mas havendo uma coerente, a coerente é a referência."""
    linha = _calcular([_compra("GERMED", 2.0, 50), _compra("SANDOZ", 9.0, 10)]).loc[(1, 1)]
    assert linha["laboratorio"] == "SANDOZ"
    assert linha["economia"] == pytest.approx(10.0)


def test_limites_de_50_por_cento_sao_inclusivos():
    assert _calcular([_compra(vlr=12.0)]).loc[(1, 1), "fonte"] == analise.FONTE_VLR
    assert _calcular([_compra(vlr=4.0)]).loc[(1, 1), "fonte"] == analise.FONTE_VLR


# ---------------------------------------------------------------------------
# Preço médio
# ---------------------------------------------------------------------------

def test_preco_medio_pondera_todos_os_laboratorios_nos_meses_da_janela():
    compras = [
        _compra("MEDLEY", 10.0, 10, AGO), _compra("SANDOZ", 6.0, 10, AGO),
        _compra("SANDOZ", 8.0, 20, JUL), _compra("SANDOZ", 5.0, 100, MAI),  # maio fora da janela
    ]
    linha = _calcular(compras).loc[(1, 1)]
    assert linha["preco_medio"] == pytest.approx((100 + 60 + 160) / 40)
    assert linha["meses_preco_medio"] == 2


def test_preco_medio_ignora_bonificacao_e_fora_do_padrao_e_usa_preco_efetivo():
    compras = [
        _compra("SANDOZ", 9.0, 10), _compra("RANBAXY", 0.01, 100),
        _compra("GERMED", 184.77, 10, cmv=7.0), _compra("EMS", 500.0, 10),
    ]
    linha = _calcular(compras).loc[(1, 1)]
    assert linha["preco_medio"] == pytest.approx((90 + 70) / 20)


# ---------------------------------------------------------------------------
# Filtros, ordenação e agregação por loja (em memória)
# ---------------------------------------------------------------------------

def _tabela():
    df = pd.DataFrame([
        {"loja_id": 1, "base_generico_id": 1, "razao_social": "Farmácia B", "cnpj": "1", "uf": "MG",
         "atendente_comercial": "Ana", "grupo_economico": None, "nome_canonico": "DIPIRONA", "laboratorio": "SANDOZ",
         "descricao": "DIPIRONA SANDOZ", "economia": 10.0},
        {"loja_id": 2, "base_generico_id": 1, "razao_social": "farmácia a", "cnpj": "2", "uf": "SP",
         "atendente_comercial": "Bia", "grupo_economico": "G1", "nome_canonico": "DIPIRONA", "laboratorio": None,
         "descricao": "DIPIRONA", "economia": 30.0},
        {"loja_id": 2, "base_generico_id": 2, "razao_social": "farmácia a", "cnpj": "2", "uf": "SP",
         "atendente_comercial": "Bia", "grupo_economico": "G1", "nome_canonico": "LOSARTANA", "laboratorio": "EMS",
         "descricao": "LOSARTANA EMS", "economia": 5.0},
    ])
    for coluna in analise.COLUNAS_LOJA:
        if coluna not in df.columns:
            df[coluna] = None
    return df


def test_ordenar_texto_ignora_maiuscula_e_vazio_fica_no_fim_nos_dois_sentidos():
    df = _tabela()
    assert analise.ordenar(df, "razao_social", True)["loja_id"].tolist() == [2, 2, 1]
    assert pd.isna(analise.ordenar(df, "laboratorio", True)["laboratorio"].tolist()[-1])
    assert pd.isna(analise.ordenar(df, "laboratorio", False)["laboratorio"].tolist()[-1])


def test_ordenar_texto_ignora_espaco_nas_pontas():
    df = pd.DataFrame({"loja_id": [1, 2, 3], "razao_social": [" DROGARIA LIZ", "AFONSO", "BELA VISTA "]})
    assert analise.ordenar(df, "razao_social", True)["loja_id"].tolist() == [2, 3, 1]


def test_ordenar_numero():
    assert analise.ordenar(_tabela(), "economia", False)["economia"].tolist() == [30.0, 10.0, 5.0]


def test_filtros_em_memoria():
    df = _tabela()
    assert len(analise.filtrar(df, analise.Filtros(uf="SP"))) == 2
    assert len(analise.filtrar(df, analise.Filtros(busca="ems"))) == 1
    assert len(analise.filtrar(df, analise.Filtros(grupo_economico="G1", loja_ids=[2]))) == 2


def test_por_loja_soma_economia_e_conta_genericos():
    lojas = analise.por_loja(_tabela()).set_index("loja_id")
    assert lojas.loc[2, "economia"] == pytest.approx(35.0)
    assert lojas.loc[2, "qtd_produtos"] == 2


# ---------------------------------------------------------------------------
# Ponta a ponta no banco
# ---------------------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_carregar_filtra_pelo_laboratorio_e_pela_uf_coberta(session):
    bg = BaseGenerico(nome_canonico="DIPIRONA 500MG")
    session.add(bg)
    session.flush()
    for ean in ("111", "222"):
        session.add(EanGenerico(ean=ean, base_generico_id=bg.id, origem_resolucao=OrigemResolucao.MANUAL,
                                descricao_origem_snapshot="x", resolvido_por="t"))
    loja_mg = Loja(cnpj="11.111.111/0001-11", razao_social="Farmácia MG", uf="MG", cidade="BH")
    loja_rj = Loja(cnpj="22.222.222/0001-22", razao_social="Farmácia RJ", uf="RJ", cidade="Rio")
    ranbaxy = TabelaGruppy(laboratorio="Ranbaxy", modo_custo=ModoCustoGruppy.PRONTO, nome_arquivo_origem="r", criado_por="t")
    medley = TabelaGruppy(laboratorio="Medley", modo_custo=ModoCustoGruppy.PRONTO, nome_arquivo_origem="m", criado_por="t")
    session.add_all([loja_mg, loja_rj, ranbaxy, medley])
    session.flush()
    session.add_all([
        TabelaGruppyCobertura(tabela_gruppy_id=ranbaxy.id, uf="MG", status=StatusCobertura.ATIVA),
        TabelaGruppyCobertura(tabela_gruppy_id=medley.id, uf="MG", status=StatusCobertura.ATIVA),
        ItemTabelaGruppy(tabela_gruppy_id=ranbaxy.id, ean="111", descricao_origem="d", custo_liquido=8.0),
        ItemTabelaGruppy(tabela_gruppy_id=medley.id, ean="111", descricao_origem="d", custo_liquido=5.0),
    ])
    for loja in (loja_mg, loja_rj):
        session.add(RegistroCompraGPS(
            loja_id=loja.id, ean="222", descricao_origem="DIPIRONA SANDOZ", laboratorio_compra="SANDOZ",
            ano_mes=AGO, quantidade=10, custo_unitario=9.0,
        ))
    session.flush()

    resultado = analise.carregar(session, "Ranbaxy", periodo_meses=1)

    # Só a loja de MG: RJ não tem tabela Ranbaxy. Preço é o da Ranbaxy (8),
    # não o menor entre laboratórios (Medley, 5). EAN 222 casa pelo genérico.
    assert resultado["razao_social"].tolist() == ["Farmácia MG"]
    linha = resultado.iloc[0]
    assert linha["preco_laboratorio"] == pytest.approx(8.0)
    assert linha["economia"] == pytest.approx(10.0)
    assert analise.laboratorios_disponiveis(session) == ["Medley", "Ranbaxy"]


def test_carregar_sem_compras_devolve_vazio(session):
    assert analise.carregar(session, "Ranbaxy", periodo_meses=1).empty


def test_texto_ausente_sai_como_none_e_nao_nan():
    """Na tela, `valor or "—"` mostraria "nan" se o vazio viesse como NaN
    (NaN é verdadeiro em Python) — aconteceu no detalhe da loja."""
    lojas = analise.por_loja(_tabela())
    assert lojas.set_index("loja_id").loc[1, "grupo_economico"] is None


def _para_busca() -> pd.DataFrame:
    return pd.DataFrame({
        "loja_id": [1, 2], "razao_social": ["DROGARIA UM", "FARMACIA 2020"], "cnpj": ["10482949000129", "20643529000130"],
        "nome_canonico": ["LOSARTANA", "DIPIRONA"], "laboratorio": ["EMS", "TEUTO"], "descricao": [None, None],
        "uf": ["MG", "MG"], "atendente_comercial": [None, None], "grupo_economico": [None, None],
    })


def test_busca_acha_cnpj_digitado_com_pontuacao():
    df = _para_busca()
    for termo in ("10.482.949/0001-29", "10.482.949", "10482949000129"):
        assert analise.filtrar(df, analise.Filtros(laboratorio="X", periodo_meses=1, busca=termo))["loja_id"].tolist() == [1]


def test_busca_de_texto_com_numero_continua_por_texto():
    df = _para_busca()
    assert analise.filtrar(df, analise.Filtros(laboratorio="X", periodo_meses=1, busca="farmacia 2020"))["loja_id"].tolist() == [2]
