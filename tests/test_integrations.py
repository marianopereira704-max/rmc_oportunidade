"""Testes de integrações (Gruppy/GPS/Base Genéricos) contra um SQLite em
memória + storage local apontando pra um diretório temporário."""
from __future__ import annotations

import io

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from core.models import (
    Base,
    BaseGenerico,
    EanGenerico,
    FilaCnpjOrfao,
    FilaResolucaoEAN,
    FSNode,
    ItemTabelaGruppy,
    Loja,
    ModoCustoGruppy,
    OrigemFila,
    OrigemResolucao,
    Papel,
    RegistroCompraGPS,
    StatusCobertura,
    StatusFila,
    TabelaGruppy,
    TabelaGruppyCobertura,
    UltimoMapeamentoColuna,
    UploadGPS,
    Usuario,
)
from integrations import base_genericos as base_genericos_integ
from integrations import gps as gps_integ
from integrations import gruppy as gruppy_integ
from integrations import mapeamento as mapeamento_integ
from storage import filesystem as fs


def _xlsx_bytes(linhas: list[dict]) -> bytes:
    df = pd.DataFrame(linhas)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    return buffer.getvalue()


@pytest.fixture()
def session(tmp_path, monkeypatch):
    from core import config as core_config

    monkeypatch.setattr(core_config.settings, "local_storage_dir", tmp_path)
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def pasta_raiz_id(session):
    return fs.garantir_raiz(session).id


# ---------------------------------------------------------------------------
# Gruppy
# ---------------------------------------------------------------------------

def test_gruppy_vigencia_granular_por_uf(session, pasta_raiz_id):
    conteudo_a = _xlsx_bytes([
        {"ean": "111", "descricao": "Produto A", "custo": 10.0},
    ])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo_a, "tabela_a.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab X", ufs=["SP", "RJ"], modo_custo=ModoCustoGruppy.PRONTO,
    )

    conteudo_b = _xlsx_bytes([
        {"ean": "222", "descricao": "Produto B", "custo": 20.0},
    ])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo_b, "tabela_b.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab X", ufs=["RJ", "MG"], modo_custo=ModoCustoGruppy.PRONTO,
    )

    coberturas = session.execute(
        select(TabelaGruppyCobertura)
        .join(TabelaGruppy)
        .where(TabelaGruppy.laboratorio == "Lab X")
    ).scalars().all()
    por_uf_e_tabela = {(c.uf, c.tabela.nome_arquivo_origem): c.status for c in coberturas}

    # SP só existia na tabela A e não foi tocado por B -> continua ATIVA
    assert por_uf_e_tabela[("SP", "tabela_a.xlsx")] == StatusCobertura.ATIVA
    # RJ existia nas duas -> a de A foi inativada, a de B está ativa
    assert por_uf_e_tabela[("RJ", "tabela_a.xlsx")] == StatusCobertura.INATIVA
    assert por_uf_e_tabela[("RJ", "tabela_b.xlsx")] == StatusCobertura.ATIVA
    # MG só existia em B -> ATIVA
    assert por_uf_e_tabela[("MG", "tabela_b.xlsx")] == StatusCobertura.ATIVA


def test_gruppy_sem_uf_falha(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([{"ean": "1", "descricao": "X", "custo": 1.0}])
    with pytest.raises(ValueError):
        gruppy_integ.processar_planilha_gruppy(
            session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
            laboratorio="Lab Y", ufs=[], modo_custo=ModoCustoGruppy.PRONTO,
        )


def test_gruppy_modo_bruto_desconto_calcula_custo(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([{"ean": "333", "descricao": "Produto C", "preco_bruto": 100.0, "desconto": 20.0}])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab Z", ufs=["SP"], modo_custo=ModoCustoGruppy.BRUTO_DESCONTO,
    )
    item = session.execute(select(ItemTabelaGruppy).where(ItemTabelaGruppy.ean == "333")).scalar_one()
    assert item.custo_liquido == pytest.approx(80.0)


def test_gruppy_desconto_fracao_nao_e_dividido_de_novo(session, pasta_raiz_id):
    # planilha que já traz o desconto em fração (0.84 = 84%) — dividir por
    # 100 de novo daria 0,0084 (0,84%), ~100x menor que o real.
    conteudo = _xlsx_bytes([{"ean": "600", "descricao": "Produto Fração", "preco_bruto": 100.0, "desconto": 0.84}])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab Fracao", ufs=["SP"], modo_custo=ModoCustoGruppy.BRUTO_DESCONTO,
    )
    item = session.execute(select(ItemTabelaGruppy).where(ItemTabelaGruppy.ean == "600")).scalar_one()
    assert float(item.percentual_desconto) == pytest.approx(0.84)
    assert float(item.custo_liquido) == pytest.approx(16.0)  # 100 * (1 - 0.84)


def test_gruppy_desconto_percentual_e_convertido_pra_fracao(session, pasta_raiz_id):
    # mesmo desconto de 84%, agora vindo na escala percentual (0-100) —
    # tem que produzir exatamente o mesmo resultado do teste de fração acima.
    conteudo = _xlsx_bytes([{"ean": "601", "descricao": "Produto Percentual", "preco_bruto": 100.0, "desconto": 84}])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab Percentual", ufs=["SP"], modo_custo=ModoCustoGruppy.BRUTO_DESCONTO,
    )
    item = session.execute(select(ItemTabelaGruppy).where(ItemTabelaGruppy.ean == "601")).scalar_one()
    assert float(item.percentual_desconto) == pytest.approx(0.84)
    assert float(item.custo_liquido) == pytest.approx(16.0)


def test_gruppy_desconto_caso_limite_em_1_e_100_por_cento(session, pasta_raiz_id):
    # valor == 1 não é "> 1" -> cai no ramo de fração: 1.0 permanece 1.0
    # (100% de desconto), não vira 0.01 (1%). Mesma convenção já validada
    # pro % de CMV do GPS, replicada aqui sem alterar o critério.
    conteudo = _xlsx_bytes([{"ean": "602", "descricao": "Produto Limite", "preco_bruto": 100.0, "desconto": 1}])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab Limite", ufs=["SP"], modo_custo=ModoCustoGruppy.BRUTO_DESCONTO,
    )
    item = session.execute(select(ItemTabelaGruppy).where(ItemTabelaGruppy.ean == "602")).scalar_one()
    assert float(item.percentual_desconto) == pytest.approx(1.0)
    assert float(item.custo_liquido) == pytest.approx(0.0)  # 100 * (1 - 1.0)


def test_gruppy_ean_sem_generico_vai_pra_fila_sem_acumular_valor(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([{"ean": "444", "descricao": "Produto Desconhecido XYZ", "custo": 55.0}])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab W", ufs=["SP"], modo_custo=ModoCustoGruppy.PRONTO,
    )
    fila = session.execute(select(FilaResolucaoEAN).where(FilaResolucaoEAN.ean == "444")).scalar_one()
    # Gruppy é catálogo de preço unitário, não valor transacionado -> não conta pro Pareto da fila
    assert float(fila.valor_total_acumulado) == 0


def test_gruppy_coluna_com_pontuacao_e_reconhecida(session, pasta_raiz_id):
    # Cabeçalho real de planilha Gruppy vem com pontuação (cifrão, ponto,
    # hífen) — a normalização de coluna precisa remover isso, não só
    # espaço/underscore (mesmo bug já corrigido em integrations/gps.py pro
    # caso de "Fat. líquido"/"% CMV").
    conteudo = _xlsx_bytes([{"ean": "555", "descricao": "Produto Pontuado", "Preço-Tabela": 42.0}])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab Pontuacao", ufs=["SP"], modo_custo=ModoCustoGruppy.PRONTO,
    )
    item = session.execute(select(ItemTabelaGruppy).where(ItemTabelaGruppy.ean == "555")).scalar_one()
    assert item.custo_liquido == pytest.approx(42.0)


def test_gruppy_planilha_real_com_celula_vazia_nao_gruda_sufixo_no_ean(session, pasta_raiz_id):
    # Mesmo cenário da Base Genéricos/GPS: uma célula de EAN vazia em OUTRA
    # linha faz o pandas ler a coluna inteira como float, e um EAN limpo como
    # 7896422507295 vira 7896422507295.0 se lido com str() ingênuo.
    conteudo = _xlsx_bytes([
        {"ean": 7896422507295, "descricao": "Produto Real", "custo": 42.0},
        {"ean": None, "descricao": "Linha Com EAN Vazio", "custo": None},
    ])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab EAN Float", ufs=["SP"], modo_custo=ModoCustoGruppy.PRONTO,
    )
    item = session.execute(
        select(ItemTabelaGruppy).where(ItemTabelaGruppy.descricao_origem == "Produto Real")
    ).scalar_one()
    assert item.ean == "7896422507295"
    assert ".0" not in item.ean


# ---------------------------------------------------------------------------
# GPS
# ---------------------------------------------------------------------------

def _criar_loja(session, cnpj, uf="SP") -> Loja:
    loja = Loja(cnpj=cnpj, razao_social="Farmácia Teste", uf=uf, cidade="São Paulo")
    session.add(loja)
    session.flush()
    return loja


def test_gps_custo_unitario_formula(session, pasta_raiz_id):
    loja = _criar_loja(session, "30.208.213/0001-74")
    conteudo = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "555", "descricao": "Produto D",
        "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.6, "estoque": 5,
    }])
    gps_integ.processar_planilha_gps(session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")

    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.loja_id == loja.id)).scalar_one()
    assert float(registro.custo_unitario) == pytest.approx((1000.0 * 0.6) / 10)


def test_gps_percentual_cmv_aceita_escala_0_100(session, pasta_raiz_id):
    _criar_loja(session, "30.208.213/0001-74")
    conteudo = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "556", "descricao": "Produto E",
        "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 60, "estoque": 0,  # 60, não 0.6
    }])
    gps_integ.processar_planilha_gps(session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")

    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.ean == "556")).scalar_one()
    assert float(registro.pct_cmv) == pytest.approx(0.6)


def test_gps_linha_lixo_ignorada(session, pasta_raiz_id):
    _criar_loja(session, "30.208.213/0001-74")
    conteudo = _xlsx_bytes([
        {"cnpj": "30.208.213/0001-74", "ean": "557", "descricao": "Produto F",
         "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.5, "estoque": 0},
        {"cnpj": "", "ean": "TOTAL", "descricao": "TOTAL GERAL",
         "quantidade": "abc", "fat_liquido": "abc", "pct_cmv": "abc", "estoque": ""},
    ])
    resultado = gps_integ.processar_planilha_gps(session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")
    assert resultado.registros_processados == 1
    assert "ignoradas" in resultado.mensagem


def test_gps_cnpj_orfao_depois_resolvido_reprocessa_automaticamente(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([{
        "cnpj": "11.111.111/0001-11", "ean": "999", "descricao": "Produto G",
        "quantidade": 5, "fat_liquido": 500.0, "pct_cmv": 0.5, "estoque": 2,
        "razaosocial": "Farmácia Desconhecida",
    }])
    resultado = gps_integ.processar_planilha_gps(session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")

    assert resultado.registros_processados == 0
    fila = session.execute(select(FilaCnpjOrfao).where(FilaCnpjOrfao.cnpj == "11111111000111")).scalar_one()
    assert fila.status == StatusFila.PENDENTE
    assert float(fila.valor_total_acumulado) == 500.0
    assert session.execute(select(RegistroCompraGPS)).scalar_one_or_none() is None

    # admin cadastra a loja e resolve o órfão -> reprocessamento automático
    loja = _criar_loja(session, "11.111.111/0001-11")
    total_inseridas = gps_integ.resolver_cnpj_orfao(session, fila.id, loja.id, resolvido_por="admin")

    assert total_inseridas == 1
    fila_atualizada = session.get(FilaCnpjOrfao, fila.id)
    assert fila_atualizada.status == StatusFila.RESOLVIDA
    assert fila_atualizada.resolvido_para_loja_id == loja.id

    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.loja_id == loja.id)).scalar_one()
    assert registro.ean == "999"
    assert registro.ano_mes == "2026-07"


def test_gps_reupload_mesmo_mes_atualiza_em_vez_de_duplicar(session, pasta_raiz_id):
    _criar_loja(session, "30.208.213/0001-74")
    conteudo_v1 = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "700", "descricao": "Produto H",
        "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.5, "estoque": 3,
    }])
    gps_integ.processar_planilha_gps(session, conteudo_v1, "gps_v1.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")

    conteudo_v2 = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "700", "descricao": "Produto H (corrigido)",
        "quantidade": 12, "fat_liquido": 1200.0, "pct_cmv": 0.5, "estoque": 4,
    }])
    resultado = gps_integ.processar_planilha_gps(session, conteudo_v2, "gps_v2.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")

    assert resultado.registros_processados == 0
    assert session.execute(select(RegistroCompraGPS)).scalars().all().__len__() == 1
    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.ean == "700")).scalar_one()
    assert float(registro.quantidade) == 12


def test_gps_planilha_real_com_celula_vazia_nao_gruda_sufixo_no_ean(session, pasta_raiz_id):
    # Mesmo cenário da Base Genéricos: uma célula de EAN vazia em OUTRA linha
    # faz o pandas ler a coluna inteira como float, e um EAN limpo como
    # 7896422507295 vira 7896422507295.0 se lido com str() ingênuo.
    loja = _criar_loja(session, "30.208.213/0001-74")
    conteudo = _xlsx_bytes([
        {"cnpj": "30.208.213/0001-74", "ean": 7896422507295, "descricao": "Produto Real",
         "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.6, "estoque": 5},
        {"cnpj": "30.208.213/0001-74", "ean": None, "descricao": "Linha Com EAN Vazio",
         "quantidade": None, "fat_liquido": None, "pct_cmv": None, "estoque": None},
    ])
    resultado = gps_integ.processar_planilha_gps(session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")

    assert resultado.registros_processados == 1  # linha do EAN vazio é lixo, não conta
    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.loja_id == loja.id)).scalar_one()
    assert registro.ean == "7896422507295"
    assert ".0" not in registro.ean


# ---------------------------------------------------------------------------
# Mapeamento de Colunas (popup de confirmação antes de processar)
# ---------------------------------------------------------------------------

def test_sugerir_mapeamento_prioriza_ultimo_confirmado_sobre_sinonimo(session):
    # sinônimo automático acharia "Preco" pra "custo", mas o admin já
    # confirmou antes que "Preco Liquido Final" é a coluna certa -> a
    # sugestão da próxima planilha tem que respeitar essa escolha manual,
    # não voltar a insistir no sinônimo.
    session.add(UltimoMapeamentoColuna(
        fornecedor=OrigemFila.GRUPPY, campo="custo", nome_coluna="Preco Liquido Final", atualizado_por="admin",
    ))
    session.flush()

    sugestao = mapeamento_integ.sugerir_mapeamento(
        session,
        OrigemFila.GRUPPY,
        campos_obrigatorios={"ean", "descricao", "custo"},
        colunas_planilha=["EAN", "Descricao", "Preco", "Preco Liquido Final"],
        mapa_automatico={"ean": "EAN", "descricao": "Descricao", "custo": "Preco"},
    )

    assert sugestao["custo"] == "Preco Liquido Final"
    assert sugestao["ean"] == "EAN"  # sem confirmação anterior pra esse campo -> cai no automático
    assert sugestao["descricao"] == "Descricao"


def test_sugerir_mapeamento_cai_pro_sinonimo_sem_confirmacao_anterior(session):
    sugestao = mapeamento_integ.sugerir_mapeamento(
        session,
        OrigemFila.GPS,
        campos_obrigatorios={"ean", "descricao"},
        colunas_planilha=["EAN", "Descricao"],
        mapa_automatico={"ean": "EAN", "descricao": "Descricao"},
    )
    assert sugestao == {"ean": "EAN", "descricao": "Descricao"}


def test_sugerir_mapeamento_ignora_confirmacao_antiga_se_coluna_sumiu(session):
    # planilha anterior tinha "Preco Antigo"; a planilha atual não tem mais
    # essa coluna -> não dá pra sugerir algo que não existe, cai no automático.
    session.add(UltimoMapeamentoColuna(
        fornecedor=OrigemFila.GRUPPY, campo="custo", nome_coluna="Preco Antigo", atualizado_por="admin",
    ))
    session.flush()

    sugestao = mapeamento_integ.sugerir_mapeamento(
        session,
        OrigemFila.GRUPPY,
        campos_obrigatorios={"custo"},
        colunas_planilha=["EAN", "Descricao", "Preco Novo"],
        mapa_automatico={"custo": "Preco Novo"},
    )
    assert sugestao["custo"] == "Preco Novo"


def test_confirmar_mapeamento_atualiza_ultimo_confirmado(session):
    mapeamento_integ.confirmar_mapeamento(
        session, OrigemFila.GPS, {"ean": "Codigo EAN", "descricao": "Produto"}, "admin",
    )
    registros = {
        r.campo: r.nome_coluna
        for r in session.execute(
            select(UltimoMapeamentoColuna).where(UltimoMapeamentoColuna.fornecedor == OrigemFila.GPS)
        ).scalars().all()
    }
    assert registros == {"ean": "Codigo EAN", "descricao": "Produto"}

    # confirmar de novo (planilha seguinte) atualiza em vez de duplicar linha
    mapeamento_integ.confirmar_mapeamento(session, OrigemFila.GPS, {"ean": "EAN2"}, "admin2")
    todos = session.execute(
        select(UltimoMapeamentoColuna).where(UltimoMapeamentoColuna.fornecedor == OrigemFila.GPS)
    ).scalars().all()
    assert len(todos) == 2  # ainda só "ean" e "descricao", nenhuma linha nova
    ean_atualizado = next(r for r in todos if r.campo == "ean")
    assert ean_atualizado.nome_coluna == "EAN2"
    assert ean_atualizado.atualizado_por == "admin2"


def test_gps_processar_planilha_bloqueia_mapeamento_confirmado_incompleto(session, pasta_raiz_id):
    _criar_loja(session, "30.208.213/0001-74")
    conteudo = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "555", "descricao": "Produto D",
        "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.6, "estoque": 5,
    }])
    mapa_incompleto = {  # falta "estoque"
        "cnpj": "cnpj", "ean": "ean", "descricao": "descricao",
        "quantidade": "quantidade", "fat_liquido": "fat_liquido", "pct_cmv": "pct_cmv",
    }
    with pytest.raises(ValueError, match="estoque"):
        gps_integ.processar_planilha_gps(
            session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07",
            mapa_confirmado=mapa_incompleto,
        )
    assert session.execute(select(RegistroCompraGPS)).scalar_one_or_none() is None


def test_gruppy_processar_planilha_bloqueia_mapeamento_confirmado_incompleto(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([{"ean": "333", "descricao": "Produto C", "custo": 100.0}])
    mapa_incompleto = {"ean": "ean"}  # falta "descricao"
    with pytest.raises(ValueError, match="descricao"):
        gruppy_integ.processar_planilha_gruppy(
            session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
            laboratorio="Lab Incompleto", ufs=["SP"], modo_custo=ModoCustoGruppy.PRONTO,
            mapa_confirmado=mapa_incompleto,
        )
    assert session.execute(select(TabelaGruppy)).scalar_one_or_none() is None


def test_gps_processar_planilha_usa_mapeamento_confirmado_em_vez_do_automatico(session, pasta_raiz_id):
    # "IdentificadorUnico" não bate com nenhum sinônimo configurado pra "ean"
    # (settings.colunas.gps) -> a heurística sozinha falharia; só processa
    # porque o mapeamento confirmado aponta pra ela diretamente.
    loja = _criar_loja(session, "30.208.213/0001-74")
    conteudo = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "IdentificadorUnico": "999888", "descricao": "Produto Z",
        "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.6, "estoque": 5,
    }])
    mapa_confirmado = {
        "cnpj": "cnpj", "ean": "IdentificadorUnico", "descricao": "descricao",
        "quantidade": "quantidade", "fat_liquido": "fat_liquido", "pct_cmv": "pct_cmv", "estoque": "estoque",
    }
    resultado = gps_integ.processar_planilha_gps(
        session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07",
        mapa_confirmado=mapa_confirmado,
    )
    assert resultado.registros_processados == 1
    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.loja_id == loja.id)).scalar_one()
    assert registro.ean == "999888"


def test_gps_processar_planilha_grava_mapeamento_exato_do_upload(session, pasta_raiz_id):
    _criar_loja(session, "30.208.213/0001-74")
    conteudo = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "555", "descricao": "Produto D",
        "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.6, "estoque": 5,
    }])
    mapa_confirmado = {
        "cnpj": "cnpj", "ean": "ean", "descricao": "descricao", "quantidade": "quantidade",
        "fat_liquido": "fat_liquido", "pct_cmv": "pct_cmv", "estoque": "estoque",
    }
    gps_integ.processar_planilha_gps(
        session, conteudo, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07",
        mapa_confirmado=mapa_confirmado,
    )
    upload = session.execute(select(UploadGPS)).scalar_one()
    assert mapeamento_integ.desserializar_mapa(upload.mapa_colunas_json) == mapa_confirmado


def test_gruppy_processar_planilha_grava_mapeamento_exato_do_upload(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([{"ean": "333", "descricao": "Produto C", "custo": 100.0}])
    mapa_confirmado = {"ean": "ean", "descricao": "descricao", "custo": "custo"}
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, "t.xlsx", "admin", pasta_raiz_id,
        laboratorio="Lab Mapa", ufs=["SP"], modo_custo=ModoCustoGruppy.PRONTO,
        mapa_confirmado=mapa_confirmado,
    )
    tabela = session.execute(select(TabelaGruppy).where(TabelaGruppy.laboratorio == "Lab Mapa")).scalar_one()
    assert mapeamento_integ.desserializar_mapa(tabela.mapa_colunas_json) == mapa_confirmado


def test_gps_dois_uploads_seguidos_mantem_cada_um_seu_proprio_mapeamento(session, pasta_raiz_id):
    # o "último mapeamento global" (UltimoMapeamentoColuna) muda a cada
    # confirmação -> isso NÃO pode sobrescrever o que já ficou gravado no
    # upload anterior, cada UploadGPS guarda o seu próprio.
    _criar_loja(session, "30.208.213/0001-74")

    conteudo_1 = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "CodBarra": "111", "descricao": "Produto 1",
        "quantidade": 10, "fat_liquido": 1000.0, "pct_cmv": 0.6, "estoque": 5,
    }])
    mapa_1 = {
        "cnpj": "cnpj", "ean": "CodBarra", "descricao": "descricao", "quantidade": "quantidade",
        "fat_liquido": "fat_liquido", "pct_cmv": "pct_cmv", "estoque": "estoque",
    }
    gps_integ.processar_planilha_gps(
        session, conteudo_1, "gps_v1.xlsx", "admin", pasta_raiz_id, ano_mes="2026-06", mapa_confirmado=mapa_1,
    )
    mapeamento_integ.confirmar_mapeamento(session, OrigemFila.GPS, mapa_1, "admin")

    conteudo_2 = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "EANProduto": "222", "descricao": "Produto 2",
        "quantidade": 8, "fat_liquido": 800.0, "pct_cmv": 0.4, "estoque": 3,
    }])
    mapa_2 = {
        "cnpj": "cnpj", "ean": "EANProduto", "descricao": "descricao", "quantidade": "quantidade",
        "fat_liquido": "fat_liquido", "pct_cmv": "pct_cmv", "estoque": "estoque",
    }
    gps_integ.processar_planilha_gps(
        session, conteudo_2, "gps_v2.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07", mapa_confirmado=mapa_2,
    )
    mapeamento_integ.confirmar_mapeamento(session, OrigemFila.GPS, mapa_2, "admin")

    upload_1 = session.execute(select(UploadGPS).where(UploadGPS.ano_mes == "2026-06")).scalar_one()
    upload_2 = session.execute(select(UploadGPS).where(UploadGPS.ano_mes == "2026-07")).scalar_one()
    assert mapeamento_integ.desserializar_mapa(upload_1.mapa_colunas_json) == mapa_1
    assert mapeamento_integ.desserializar_mapa(upload_2.mapa_colunas_json) == mapa_2

    # o "último mapeamento global" reflete o mais recente (mapa_2) — só a
    # sugestão da PRÓXIMA planilha, nunca reescreve o que já foi gravado acima
    ultimo_ean = session.execute(
        select(UltimoMapeamentoColuna).where(
            UltimoMapeamentoColuna.fornecedor == OrigemFila.GPS, UltimoMapeamentoColuna.campo == "ean",
        )
    ).scalar_one()
    assert ultimo_ean.nome_coluna == "EANProduto"


def test_gps_reprocesso_cnpj_orfao_usa_mapeamento_salvo_do_upload_nao_o_atual(session, pasta_raiz_id):
    # nomes de coluna propositalmente fora de qualquer sinônimo configurado
    # (settings.colunas.gps) -> se o reprocesso recalculasse a heurística do
    # zero, falharia (ValueError). Se usasse o "último mapeamento global"
    # (que aqui é deliberadamente diferente/errado), estouraria KeyError ao
    # tentar ler uma coluna que não existe nesse arquivo. Só passa se usar o
    # mapeamento exato salvo NESTE upload.
    conteudo = _xlsx_bytes([{
        "ColCNPJ": "11.111.111/0001-11", "ColEAN": "123", "ColDescricao": "Produto Reprocesso",
        "ColQtd": 10, "ColFatLiq": 1000.0, "ColPctCmv": 0.5, "ColEstoque": 5,
    }])
    mapa_original = {
        "cnpj": "ColCNPJ", "ean": "ColEAN", "descricao": "ColDescricao", "quantidade": "ColQtd",
        "fat_liquido": "ColFatLiq", "pct_cmv": "ColPctCmv", "estoque": "ColEstoque",
    }
    resultado = gps_integ.processar_planilha_gps(
        session, conteudo, "gps_orfao.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07",
        mapa_confirmado=mapa_original,
    )
    assert resultado.registros_processados == 0  # CNPJ ainda não tem loja -> vai pra fila de órfão
    fila = session.execute(select(FilaCnpjOrfao).where(FilaCnpjOrfao.cnpj == "11111111000111")).scalar_one()

    # simula deriva do "mapeamento global": alguém confirmou depois um
    # mapeamento diferente (e incompatível com este arquivo antigo) pra GPS
    mapeamento_integ.confirmar_mapeamento(
        session, OrigemFila.GPS,
        {campo: "ColunaQueNaoExisteNesteArquivo" for campo in gps_integ.CAMPOS_OBRIGATORIOS},
        "outro_admin",
    )

    loja = _criar_loja(session, "11.111.111/0001-11")
    total_inseridas = gps_integ.resolver_cnpj_orfao(session, fila.id, loja.id, resolvido_por="admin")

    assert total_inseridas == 1
    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.loja_id == loja.id)).scalar_one()
    assert registro.ean == "123"


def test_gps_reprocesso_cnpj_orfao_upload_antigo_sem_mapeamento_cai_pra_heuristica(session, pasta_raiz_id, caplog):
    # simula um upload de ANTES deste passo existir: UploadGPS sem
    # mapa_colunas_json, com colunas de nome padrão (a heurística automática
    # dá conta) -> fallback documentado, com aviso no log.
    conteudo = _xlsx_bytes([{
        "cnpj": "22.222.222/0001-22", "ean": "456", "descricao": "Produto Legado",
        "quantidade": 5, "fat_liquido": 500.0, "pct_cmv": 0.5, "estoque": 2,
    }])
    node = fs.salvar_arquivo(session, pasta_raiz_id, "gps_legado.xlsx", conteudo, "sistema")
    session.add(UploadGPS(fs_node_id=node.id, ano_mes="2025-01", criado_por="sistema", mapa_colunas_json=None))
    session.add(FilaCnpjOrfao(
        cnpj="22222222000122", valor_total_acumulado=500.0, qtd_ocorrencias=1, status=StatusFila.PENDENTE,
    ))
    session.flush()
    fila = session.execute(select(FilaCnpjOrfao).where(FilaCnpjOrfao.cnpj == "22222222000122")).scalar_one()

    loja = _criar_loja(session, "22.222.222/0001-22")
    with caplog.at_level("WARNING"):
        total_inseridas = gps_integ.resolver_cnpj_orfao(session, fila.id, loja.id, resolvido_por="admin")

    assert total_inseridas == 1
    registro = session.execute(select(RegistroCompraGPS).where(RegistroCompraGPS.loja_id == loja.id)).scalar_one()
    assert registro.ean == "456"
    assert any("não tem mapeamento" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Base Genéricos (planilha curada)
# ---------------------------------------------------------------------------

def test_base_genericos_planilha_real_com_celula_vazia_nao_gruda_sufixo_no_ean(session, pasta_raiz_id):
    # Reproduz o cenário real relatado: célula de EAN vazia numa linha faz o
    # pandas ler a coluna inteira como float — o EAN limpo de OUTRA linha
    # não pode sair com sufixo ".0" grudado por causa disso.
    conteudo = _xlsx_bytes([
        {"FCC": "F001", "EAN": 7896422507295, "DESCRIÇÃO MARCOS": "Dipirona Sódica 500mg"},
        {"FCC": "F002", "EAN": None, "DESCRIÇÃO MARCOS": "Linha Com EAN Vazio"},
        {"FCC": "F003", "EAN": 7891000000042, "DESCRIÇÃO MARCOS": "Omeprazol 20mg"},
    ])

    resultado = base_genericos_integ.processar_planilha_base_genericos(
        session, conteudo, "base_completa.xlsx", "admin", pasta_raiz_id,
    )

    assert resultado.registros_processados == 2  # linha do EAN vazio é inválida, não conta
    ean1 = session.execute(select(EanGenerico).where(EanGenerico.descricao_origem_snapshot == "Dipirona Sódica 500mg")).scalar_one()
    ean2 = session.execute(select(EanGenerico).where(EanGenerico.descricao_origem_snapshot == "Omeprazol 20mg")).scalar_one()
    assert ean1.ean == "7896422507295"
    assert ean2.ean == "7891000000042"
    assert ".0" not in ean1.ean and ".0" not in ean2.ean


def test_base_genericos_agrupa_multiplos_ean_na_mesma_descricao(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([
        {"FCC": "F010", "EAN": 111, "DESCRIÇÃO MARCOS": "Losartana Potássica 50mg"},
        {"FCC": "F011", "EAN": 222, "DESCRIÇÃO MARCOS": "Losartana Potássica 50mg"},
        {"FCC": "F012", "EAN": 333, "DESCRIÇÃO MARCOS": "Losartana Potássica 50mg"},
    ])

    base_genericos_integ.processar_planilha_base_genericos(
        session, conteudo, "base_completa.xlsx", "admin", pasta_raiz_id,
    )

    assert session.query(BaseGenerico).count() == 1
    generico = session.query(BaseGenerico).one()
    eans = session.execute(select(EanGenerico)).scalars().all()
    assert len(eans) == 3
    assert all(e.base_generico_id == generico.id for e in eans)
    assert all(e.origem_resolucao == OrigemResolucao.IMPORTADA for e in eans)


def test_base_genericos_coluna_fcc_e_ignorada_sem_quebrar(session, pasta_raiz_id):
    conteudo = _xlsx_bytes([{"FCC": "F999", "EAN": 444, "DESCRIÇÃO MARCOS": "Produto Qualquer"}])
    resultado = base_genericos_integ.processar_planilha_base_genericos(
        session, conteudo, "base_completa.xlsx", "admin", pasta_raiz_id,
    )
    assert resultado.registros_processados == 1


# ---------------------------------------------------------------------------
# Excluir tabela Gruppy definitivamente (Explorador de Arquivos)
# ---------------------------------------------------------------------------

def _criar_upload_gruppy(session, pasta_raiz_id, laboratorio, ean, custo=10.0, ufs=None):
    conteudo = _xlsx_bytes([{"ean": ean, "descricao": f"Produto {laboratorio}", "custo": custo}])
    gruppy_integ.processar_planilha_gruppy(
        session, conteudo, f"{laboratorio}.xlsx", "admin", pasta_raiz_id,
        laboratorio=laboratorio, ufs=ufs or ["SP"], modo_custo=ModoCustoGruppy.PRONTO,
    )
    tabela = session.execute(select(TabelaGruppy).where(TabelaGruppy.laboratorio == laboratorio)).scalar_one()
    return tabela.upload_fs_node_id, tabela.id


def test_excluir_tabela_gruppy_remove_itens_cobertura_tabela_e_fsnode(session, pasta_raiz_id):
    fs_node_id, tabela_id = _criar_upload_gruppy(session, pasta_raiz_id, "Lab Exclusao", "111")

    resultado = gruppy_integ.excluir_tabela_gruppy_definitivamente(session, fs_node_id)

    assert resultado.laboratorio == "Lab Exclusao"
    assert resultado.itens_removidos == 1
    assert resultado.coberturas_removidas == 1
    assert session.execute(
        select(ItemTabelaGruppy).where(ItemTabelaGruppy.tabela_gruppy_id == tabela_id)
    ).first() is None
    assert session.execute(
        select(TabelaGruppyCobertura).where(TabelaGruppyCobertura.tabela_gruppy_id == tabela_id)
    ).first() is None
    assert session.get(TabelaGruppy, tabela_id) is None
    assert session.get(FSNode, fs_node_id) is None


def test_excluir_tabela_gruppy_nao_afeta_outra_tabela_gruppy(session, pasta_raiz_id):
    # "uma tabela sai, as outras continuam" — dois laboratórios distintos,
    # mesma UF (não interfere: _inativar_cobertura_sobreposta só olha o
    # MESMO laboratório, ver integrations/gruppy.py).
    fs_node_id_1, tabela_id_1 = _criar_upload_gruppy(session, pasta_raiz_id, "Lab Um", "111")
    fs_node_id_2, tabela_id_2 = _criar_upload_gruppy(session, pasta_raiz_id, "Lab Dois", "222")

    gruppy_integ.excluir_tabela_gruppy_definitivamente(session, fs_node_id_1)

    assert session.get(TabelaGruppy, tabela_id_1) is None
    tabela_2 = session.get(TabelaGruppy, tabela_id_2)
    assert tabela_2 is not None
    assert tabela_2.laboratorio == "Lab Dois"
    item_2 = session.execute(
        select(ItemTabelaGruppy).where(ItemTabelaGruppy.tabela_gruppy_id == tabela_id_2)
    ).scalar_one()
    assert item_2.ean == "222"
    assert session.get(FSNode, fs_node_id_2) is not None


def test_excluir_tabela_gruppy_nao_afeta_outras_tabelas_do_sistema(session, pasta_raiz_id):
    # baseline de dados NÃO relacionados à Gruppy, pra provar que ficam
    # intocados — não basta contagem zero->zero, precisa ter dado de verdade
    # que poderia ser afetado por um bug de escopo.
    _criar_loja(session, "30.208.213/0001-74")
    session.add(Usuario(
        cnpj_login="30.208.213/0001-74", senha_hash="hash-teste", papel=Papel.ADMIN, nome_exibicao="Admin Teste",
    ))
    generico = BaseGenerico(nome_canonico="Genérico Base", criado_por="admin")
    session.add(generico)
    session.flush()
    session.add(EanGenerico(
        ean="999999", base_generico_id=generico.id, origem_resolucao=OrigemResolucao.MANUAL,
        descricao_origem_snapshot="x", resolvido_por="admin",
    ))
    session.add(FilaResolucaoEAN(
        ean="888888", descricao_observada="Pendente", origem=OrigemFila.GPS, status=StatusFila.PENDENTE,
    ))
    session.add(FilaCnpjOrfao(cnpj="11111111000100", status=StatusFila.PENDENTE))

    conteudo_gps = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "777777", "descricao": "Produto GPS",
        "quantidade": 1, "fat_liquido": 10.0, "pct_cmv": 0.5, "estoque": 0,
    }])
    gps_integ.processar_planilha_gps(session, conteudo_gps, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")

    fs_node_id, _tabela_id = _criar_upload_gruppy(session, pasta_raiz_id, "Lab Isolado", "333")

    def contagens():
        return {
            "registros_compra_gps": session.execute(select(func.count()).select_from(RegistroCompraGPS)).scalar_one(),
            "uploads_gps": session.execute(select(func.count()).select_from(UploadGPS)).scalar_one(),
            "base_genericos": session.execute(select(func.count()).select_from(BaseGenerico)).scalar_one(),
            "ean_genericos": session.execute(select(func.count()).select_from(EanGenerico)).scalar_one(),
            "fila_resolucao_ean": session.execute(select(func.count()).select_from(FilaResolucaoEAN)).scalar_one(),
            "fila_cnpj_orfao": session.execute(select(func.count()).select_from(FilaCnpjOrfao)).scalar_one(),
            "lojas": session.execute(select(func.count()).select_from(Loja)).scalar_one(),
            "usuarios": session.execute(select(func.count()).select_from(Usuario)).scalar_one(),
        }

    antes = contagens()
    gruppy_integ.excluir_tabela_gruppy_definitivamente(session, fs_node_id)
    depois = contagens()

    assert depois == antes
    assert antes["registros_compra_gps"] == 1  # confirma que a baseline tinha dado de verdade, não só zero
    assert antes["usuarios"] == 1


def test_buscar_tabela_por_fs_node_so_acha_arquivo_gruppy(session, pasta_raiz_id):
    # É essa função que decide se "Excluir definitivamente" aparece no menu
    # do Explorador (views/dados.py::_explorador) — GPS e Base Genéricos não
    # têm elo de volta pro FSNode (só TabelaGruppy.upload_fs_node_id existe),
    # então nunca devem "achar" uma tabela pra esse botão aparecer.
    _criar_loja(session, "30.208.213/0001-74")
    conteudo_gps = _xlsx_bytes([{
        "cnpj": "30.208.213/0001-74", "ean": "555555", "descricao": "Produto GPS",
        "quantidade": 1, "fat_liquido": 10.0, "pct_cmv": 0.5, "estoque": 0,
    }])
    gps_integ.processar_planilha_gps(session, conteudo_gps, "gps.xlsx", "admin", pasta_raiz_id, ano_mes="2026-07")
    upload_gps = session.execute(select(UploadGPS)).scalar_one()

    conteudo_base = _xlsx_bytes([{"FCC": "F001", "EAN": 666666, "DESCRIÇÃO MARCOS": "Produto Base"}])
    base_genericos_integ.processar_planilha_base_genericos(
        session, conteudo_base, "base_completa.xlsx", "admin", pasta_raiz_id,
    )
    fs_node_base = session.execute(select(FSNode).where(FSNode.nome == "base_completa.xlsx")).scalar_one()

    fs_node_id_gruppy, tabela_id = _criar_upload_gruppy(session, pasta_raiz_id, "Lab Visivel", "777")

    assert gruppy_integ.buscar_tabela_por_fs_node(session, upload_gps.fs_node_id) is None
    assert gruppy_integ.buscar_tabela_por_fs_node(session, fs_node_base.id) is None
    tabela_encontrada = gruppy_integ.buscar_tabela_por_fs_node(session, fs_node_id_gruppy)
    assert tabela_encontrada is not None
    assert tabela_encontrada.id == tabela_id
