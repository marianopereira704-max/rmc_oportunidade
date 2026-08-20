"""Testes do motor de reconciliação de EAN (normalização + classificação +
fluxo completo de resolver_ean/fila, contra um SQLite em memória)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import (
    Base,
    BaseGenerico,
    EanGenerico,
    FilaResolucaoEAN,
    ItemTabelaGruppy,
    Loja,
    ModoCustoGruppy,
    OrigemFila,
    OrigemResolucao,
    RegistroCompraGPS,
    StatusFila,
    TabelaGruppy,
)
from reconciliation import motor
from reconciliation.normalizador import normalizar_ean, normalizar_percentual, normalizar_texto


# ---------------------------------------------------------------------------
# normalizador
# ---------------------------------------------------------------------------

def test_normalizar_remove_acento_e_maiuscula():
    assert normalizar_texto("dipirona sódica") == "DIPIRONA SODICA"


def test_normalizar_expande_abreviacao():
    assert normalizar_texto("Paracetamol 500mg Compr Rev") == "PARACETAMOL 500MG COMPRIMIDO REVESTIDO"


def test_normalizar_colapsa_espacos_e_pontuacao():
    assert normalizar_texto("  Dipirona   -  500MG,, ") == "DIPIRONA 500MG"


def test_normalizar_vazio():
    assert normalizar_texto("") == ""
    assert normalizar_texto(None) == ""


def test_normalizar_e_deterministico():
    # importante pro atalho de "EAN já resolvido" do motor
    assert normalizar_texto("Dipirona Compr") == normalizar_texto("Dipirona Compr")


# ---------------------------------------------------------------------------
# classificar (função pura)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "score,esperado",
    [
        (100, "auto"),
        (92, "auto"),
        (91.9, "fila_sugestao"),
        (75, "fila_sugestao"),
        (74.9, "fila_manual"),
        (0, "fila_manual"),
    ],
)
def test_classificar_niveis(score, esperado):
    assert motor.classificar(score, limiar_auto=92, limiar_medio=75) == esperado


# ---------------------------------------------------------------------------
# fluxo completo (banco em memória)
# ---------------------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _criar_generico(session, nome):
    g = BaseGenerico(nome_canonico=nome, criado_por="teste")
    session.add(g)
    session.flush()
    return g


def test_resolver_ean_aceite_automatico(session, monkeypatch):
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_auto_aceite", 92)
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_fila_media", 75)

    generico = _criar_generico(session, "Dipirona Sódica 500mg Comprimido")

    resultado = motor.resolver_ean(
        session, ean="7891000000001", descricao_origem="DIPIRONA SODICA 500MG COMPRIMIDO",
        origem=OrigemFila.GPS, valor=100, aparece_em_estoque=True,
    )

    assert resultado == generico.id
    ean_resolvido = session.query(EanGenerico).filter_by(ean="7891000000001").one()
    assert ean_resolvido.origem_resolucao.value == "automatica"
    assert session.query(FilaResolucaoEAN).count() == 0


def test_resolver_ean_sem_candidato_vai_pra_fila_manual(session):
    resultado = motor.resolver_ean(
        session, ean="7891000000002", descricao_origem="PRODUTO TOTALMENTE DESCONHECIDO XYZ",
        origem=OrigemFila.GRUPPY, valor=50,
    )
    assert resultado is None
    fila = session.query(FilaResolucaoEAN).filter_by(ean="7891000000002").one()
    assert fila.status == StatusFila.PENDENTE
    assert fila.sugestao_base_generico_id is None


def test_resolver_ean_atalho_ja_resolvido_nao_refaz_fuzzy(session, monkeypatch):
    generico = _criar_generico(session, "Losartana Potássica 50mg")
    session.add(
        EanGenerico(
            ean="7891000000003", base_generico_id=generico.id, origem_resolucao="manual",
            descricao_origem_snapshot="qualquer coisa", resolvido_por="admin",
        )
    )
    session.flush()

    chamou_buscar = {"vezes": 0}

    def _buscar_candidatos_espiao(*args, **kwargs):
        chamou_buscar["vezes"] += 1
        return []

    monkeypatch.setattr(motor, "buscar_candidatos", _buscar_candidatos_espiao)

    resultado = motor.resolver_ean(
        session, ean="7891000000003", descricao_origem="descricao completamente diferente",
        origem=OrigemFila.GPS,
    )
    assert resultado == generico.id
    assert chamou_buscar["vezes"] == 0


def test_upsert_fila_resolucao_acumula_por_ean(session):
    motor.upsert_fila_resolucao(session, ean="EAN-X", descricao_observada="Item X", origem=OrigemFila.GPS, valor=100)
    motor.upsert_fila_resolucao(session, ean="EAN-X", descricao_observada="Item X", origem=OrigemFila.GPS, valor=50, aparece_em_estoque=True)

    assert session.query(FilaResolucaoEAN).count() == 1
    fila = session.query(FilaResolucaoEAN).filter_by(ean="EAN-X").one()
    assert float(fila.valor_total_acumulado) == 150
    assert fila.qtd_ocorrencias == 2
    assert fila.aparece_em_estoque is True


def test_confirmar_resolucao_manual(session):
    generico = _criar_generico(session, "Omeprazol 20mg")
    fila = motor.upsert_fila_resolucao(session, ean="EAN-Y", descricao_observada="Omeprazol Caps", origem=OrigemFila.GRUPPY, valor=10)

    motor.confirmar_resolucao_manual(session, fila.id, generico.id, resolvido_por="admin")

    fila_atualizada = session.get(FilaResolucaoEAN, fila.id)
    assert fila_atualizada.status == StatusFila.RESOLVIDA
    ean_resolvido = session.query(EanGenerico).filter_by(ean="EAN-Y").one()
    assert ean_resolvido.base_generico_id == generico.id
    assert ean_resolvido.origem_resolucao.value == "manual"


def test_registrar_novo_generico(session):
    fila = motor.upsert_fila_resolucao(session, ean="EAN-Z", descricao_observada="Produto Novo 10mg", origem=OrigemFila.GPS, valor=10)

    novo = motor.registrar_novo_generico(session, fila.id, "Produto Novo 10mg", criado_por="admin")

    assert novo.id is not None
    fila_atualizada = session.get(FilaResolucaoEAN, fila.id)
    assert fila_atualizada.status == StatusFila.RESOLVIDA
    ean_resolvido = session.query(EanGenerico).filter_by(ean="EAN-Z").one()
    assert ean_resolvido.base_generico_id == novo.id


def test_ignorar_fila(session):
    fila = motor.upsert_fila_resolucao(session, ean="EAN-W", descricao_observada="Lixo", origem=OrigemFila.GPS, valor=1)
    motor.ignorar_fila(session, fila.id)
    fila_atualizada = session.get(FilaResolucaoEAN, fila.id)
    assert fila_atualizada.status == StatusFila.IGNORADA


def test_listar_fila_priorizada_ordem_estoque_depois_valor(session):
    motor.upsert_fila_resolucao(session, ean="A", descricao_observada="a", origem=OrigemFila.GPS, valor=1000, aparece_em_estoque=False)
    motor.upsert_fila_resolucao(session, ean="B", descricao_observada="b", origem=OrigemFila.GPS, valor=10, aparece_em_estoque=True)
    motor.upsert_fila_resolucao(session, ean="C", descricao_observada="c", origem=OrigemFila.GPS, valor=500, aparece_em_estoque=True)

    resultado = motor.listar_fila_priorizada(session)
    assert [f.ean for f in resultado] == ["C", "B", "A"]


# ---------------------------------------------------------------------------
# normalizar_ean
# ---------------------------------------------------------------------------

def test_normalizar_ean_float_com_sufixo_zero():
    # o caso real: pandas lê a coluna inteira como float quando alguma
    # célula está vazia em outra linha
    assert normalizar_ean(7896422507295.0) == "7896422507295"


def test_normalizar_ean_int():
    assert normalizar_ean(7896422507295) == "7896422507295"


def test_normalizar_ean_string_limpa():
    assert normalizar_ean("7896422507295") == "7896422507295"


def test_normalizar_ean_string_ja_com_sufixo_zero():
    # defesa extra: célula formatada como texto, não como float puro
    assert normalizar_ean("7896422507295.0") == "7896422507295"


def test_normalizar_ean_string_com_espaco():
    assert normalizar_ean("  7896422507295  ") == "7896422507295"


def test_normalizar_ean_nulo_e_nan():
    assert normalizar_ean(None) == ""
    assert normalizar_ean(float("nan")) == ""
    assert normalizar_ean("") == ""


# ---------------------------------------------------------------------------
# normalizar_percentual (compartilhada entre % de CMV do GPS e % de
# desconto da Gruppy — mesmo critério de escala nos dois lugares)
# ---------------------------------------------------------------------------

def test_normalizar_percentual_fracao_permanece():
    assert normalizar_percentual(0.84) == pytest.approx(0.84)


def test_normalizar_percentual_escala_0_100_vira_fracao():
    assert normalizar_percentual(84) == pytest.approx(0.84)


def test_normalizar_percentual_aceita_string_com_simbolo():
    assert normalizar_percentual("84%") == pytest.approx(0.84)
    assert normalizar_percentual("0,84") == pytest.approx(0.84)


def test_normalizar_percentual_caso_limite_em_1_e_fracao_nao_percentual():
    # valor == 1 não é "> 1", cai no ramo de fração: 1.0 permanece 1.0
    # (100%), nunca vira 0.01 (1%). Ambíguo por natureza — essa é a
    # convenção já validada pro % de CMV do GPS, preservada aqui.
    assert normalizar_percentual(1) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# importar_base_genericos
# ---------------------------------------------------------------------------

def test_importar_base_genericos_agrupa_por_descricao_identica(session):
    linhas = [
        ("111", "Dipirona Sódica 500mg Comprimido"),
        ("222", "Dipirona Sódica 500mg Comprimido"),
        ("333", "Dipirona Sódica 500mg Comprimido"),
    ]
    resultado = motor.importar_base_genericos(session, linhas, criado_por="admin")

    assert resultado.genericos_criados == 1
    assert resultado.eans_vinculados == 3
    assert session.query(BaseGenerico).count() == 1

    generico = session.query(BaseGenerico).one()
    eans = session.query(EanGenerico).all()
    assert len(eans) == 3
    assert all(e.base_generico_id == generico.id for e in eans)


def test_importar_base_genericos_reaproveita_generico_existente(session):
    existente = _criar_generico(session, "Losartana Potássica 50mg")

    resultado = motor.importar_base_genericos(
        session, [("999", "Losartana Potássica 50mg")], criado_por="admin"
    )

    assert resultado.genericos_criados == 0
    assert resultado.genericos_reaproveitados == 1
    assert session.query(BaseGenerico).count() == 1
    ean = session.query(EanGenerico).filter_by(ean="999").one()
    assert ean.base_generico_id == existente.id


def test_importar_base_genericos_nao_sobrescreve_ean_ja_resolvido(session):
    generico_antigo = _criar_generico(session, "Genérico Antigo")
    session.add(
        EanGenerico(
            ean="777", base_generico_id=generico_antigo.id, origem_resolucao=OrigemResolucao.AUTOMATICA,
            score_similaridade=95, descricao_origem_snapshot="qualquer coisa", resolvido_por="sistema",
        )
    )
    session.flush()

    resultado = motor.importar_base_genericos(
        session, [("777", "Descrição Totalmente Diferente Da Planilha Curada")], criado_por="admin"
    )

    assert resultado.eans_pulados == 1
    assert resultado.eans_vinculados == 0
    # não criou um genérico novo pra essa linha, nem mudou o vínculo existente
    assert session.query(BaseGenerico).count() == 1
    ean = session.query(EanGenerico).filter_by(ean="777").one()
    assert ean.base_generico_id == generico_antigo.id
    assert ean.origem_resolucao == OrigemResolucao.AUTOMATICA


def test_importar_base_genericos_marca_origem_importada(session):
    motor.importar_base_genericos(session, [("555", "Omeprazol 20mg Cápsula")], criado_por="admin")

    ean = session.query(EanGenerico).filter_by(ean="555").one()
    assert ean.origem_resolucao == OrigemResolucao.IMPORTADA
    assert ean.resolvido_por == "admin"


def test_importar_base_genericos_ignora_linha_com_ean_ou_descricao_vazios(session):
    resultado = motor.importar_base_genericos(
        session,
        [("", "Descrição sem EAN"), ("123", ""), ("456", "Válida")],
        criado_por="admin",
    )
    assert resultado.linhas_invalidas == 2
    assert resultado.eans_vinculados == 1


# ---------------------------------------------------------------------------
# limpar_eans_sujos
# ---------------------------------------------------------------------------

def _criar_loja(session, cnpj="30.208.213/0001-74", uf="SP") -> Loja:
    loja = Loja(cnpj=cnpj, razao_social="Farmácia Teste", uf=uf, cidade="São Paulo")
    session.add(loja)
    session.flush()
    return loja


def _criar_registro_gps(session, loja_id, ean, ano_mes="2026-08", **kwargs) -> RegistroCompraGPS:
    registro = RegistroCompraGPS(
        loja_id=loja_id, ean=ean, descricao_origem=kwargs.pop("descricao_origem", "Produto"),
        ano_mes=ano_mes, quantidade=kwargs.pop("quantidade", 1), fat_liquido=kwargs.pop("fat_liquido", 10.0),
        pct_cmv=kwargs.pop("pct_cmv", 0.5), custo_unitario=kwargs.pop("custo_unitario", 5.0),
        estoque=kwargs.pop("estoque", 0),
    )
    session.add(registro)
    session.flush()
    return registro


def _criar_tabela_gruppy(session) -> TabelaGruppy:
    tabela = TabelaGruppy(
        laboratorio="Lab Teste", modo_custo=ModoCustoGruppy.PRONTO, nome_arquivo_origem="t.xlsx", criado_por="admin",
    )
    session.add(tabela)
    session.flush()
    return tabela


def test_limpar_eans_sujos_corrige_registro_compra_gps(session):
    loja = _criar_loja(session)
    _criar_registro_gps(session, loja.id, "7896422507295.0")

    resultado = motor.limpar_eans_sujos(session)

    assert resultado.registros_compra_gps_corrigidos == 1
    registro = session.query(RegistroCompraGPS).one()
    assert registro.ean == "7896422507295"


def test_limpar_eans_sujos_corrige_item_tabela_gruppy(session):
    tabela = _criar_tabela_gruppy(session)
    session.add(ItemTabelaGruppy(
        tabela_gruppy_id=tabela.id, ean="7896422507295.0", descricao_origem="Produto", custo_liquido=10.0,
    ))
    session.flush()

    resultado = motor.limpar_eans_sujos(session)

    assert resultado.itens_tabela_gruppy_corrigidos == 1
    item = session.query(ItemTabelaGruppy).one()
    assert item.ean == "7896422507295"


def test_limpar_eans_sujos_corrige_fila_resolucao_ean(session):
    motor.upsert_fila_resolucao(
        session, ean="7896422507295.0", descricao_observada="Produto", origem=OrigemFila.GPS, valor=10,
    )

    resultado = motor.limpar_eans_sujos(session)

    assert resultado.fila_resolucao_ean_corrigidos == 1
    fila = session.query(FilaResolucaoEAN).one()
    assert fila.ean == "7896422507295"


def test_limpar_eans_sujos_pula_colisao_em_registro_compra_gps(session):
    # mesma loja+mês, um EAN já limpo e um sujo que normalizaria pro MESMO
    # valor — nunca sobrescrever/mesclar dado financeiro real às cegas.
    loja = _criar_loja(session)
    _criar_registro_gps(session, loja.id, "999", quantidade=1)
    _criar_registro_gps(session, loja.id, "999.0", quantidade=2)

    resultado = motor.limpar_eans_sujos(session)

    assert resultado.registros_compra_gps_corrigidos == 0
    assert resultado.registros_compra_gps_colisoes_puladas == 1
    eans = sorted(r.ean for r in session.query(RegistroCompraGPS).all())
    assert eans == ["999", "999.0"]  # nenhuma linha apagada ou sobrescrita


def test_limpar_eans_sujos_mescla_colisao_em_fila_resolucao_ean(session):
    motor.upsert_fila_resolucao(
        session, ean="888", descricao_observada="Já limpo", origem=OrigemFila.GPS,
        valor=50, aparece_em_estoque=False,
    )
    motor.upsert_fila_resolucao(
        session, ean="888.0", descricao_observada="Sujo duplicado", origem=OrigemFila.GPS,
        valor=100, aparece_em_estoque=True,
    )

    resultado = motor.limpar_eans_sujos(session)

    assert resultado.fila_resolucao_ean_colisoes_mescladas == 1
    itens = session.query(FilaResolucaoEAN).all()
    assert len(itens) == 1  # duplicata some, mesclada na linha limpa
    mesclado = itens[0]
    assert mesclado.ean == "888"
    assert float(mesclado.valor_total_acumulado) == pytest.approx(150.0)
    assert mesclado.qtd_ocorrencias == 2
    assert mesclado.aparece_em_estoque is True


# ---------------------------------------------------------------------------
# reprocessar_fila_resolucao
# ---------------------------------------------------------------------------

def test_reprocessar_fila_resolve_item_forte_automaticamente(session, monkeypatch):
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_auto_aceite", 92)
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_fila_media", 75)
    generico = _criar_generico(session, "Dipirona Sódica 500mg")
    fila = motor.upsert_fila_resolucao(
        session, ean="7891000000010", descricao_observada="Dipirona parecida",
        origem=OrigemFila.GPS, valor=100,
    )
    monkeypatch.setattr(motor, "buscar_candidatos", lambda *a, **k: (generico, 95.0))

    resultado = motor.reprocessar_fila_resolucao(session)

    assert resultado.itens_avaliados == 1
    assert resultado.resolvidos_automaticamente == 1
    fila_atualizada = session.get(FilaResolucaoEAN, fila.id)
    assert fila_atualizada.status == StatusFila.RESOLVIDA
    ean_resolvido = session.query(EanGenerico).filter_by(ean="7891000000010").one()
    assert ean_resolvido.base_generico_id == generico.id
    assert ean_resolvido.origem_resolucao == OrigemResolucao.AUTOMATICA
    assert ean_resolvido.resolvido_por == "sistema"


def test_reprocessar_fila_atualiza_sugestao_faixa_media_sem_resolver(session, monkeypatch):
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_auto_aceite", 92)
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_fila_media", 75)
    generico = _criar_generico(session, "Losartana Potássica 50mg")
    fila = motor.upsert_fila_resolucao(
        session, ean="7891000000011", descricao_observada="Losartana parecida",
        origem=OrigemFila.GPS, valor=100,
    )
    assert fila.sugestao_base_generico_id is None
    monkeypatch.setattr(motor, "buscar_candidatos", lambda *a, **k: (generico, 80.0))

    resultado = motor.reprocessar_fila_resolucao(session)

    assert resultado.resolvidos_automaticamente == 0
    assert resultado.sugestao_atualizada == 1
    fila_atualizada = session.get(FilaResolucaoEAN, fila.id)
    assert fila_atualizada.status == StatusFila.PENDENTE  # não resolve sozinho
    assert fila_atualizada.sugestao_base_generico_id == generico.id
    assert float(fila_atualizada.sugestao_score) == pytest.approx(80.0)
    assert session.query(EanGenerico).count() == 0


def test_reprocessar_fila_item_sem_match_bom_permanece_intocado(session, monkeypatch):
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_auto_aceite", 92)
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_fila_media", 75)
    fila = motor.upsert_fila_resolucao(
        session, ean="7891000000012", descricao_observada="Produto sem nada parecido",
        origem=OrigemFila.GPS, valor=100,
    )
    monkeypatch.setattr(motor, "buscar_candidatos", lambda *a, **k: None)

    resultado = motor.reprocessar_fila_resolucao(session)

    assert resultado.resolvidos_automaticamente == 0
    assert resultado.sugestao_atualizada == 0
    assert resultado.sem_mudanca == 1
    fila_atualizada = session.get(FilaResolucaoEAN, fila.id)
    assert fila_atualizada.status == StatusFila.PENDENTE
    assert fila_atualizada.sugestao_base_generico_id is None


def test_reprocessar_fila_e_idempotente_rodar_duas_vezes(session, monkeypatch):
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_auto_aceite", 92)
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_fila_media", 75)
    generico = _criar_generico(session, "Sinvastatina 20mg")
    motor.upsert_fila_resolucao(
        session, ean="7891000000013", descricao_observada="Sinvastatina parecida",
        origem=OrigemFila.GPS, valor=100,
    )
    monkeypatch.setattr(motor, "buscar_candidatos", lambda *a, **k: (generico, 95.0))

    primeiro = motor.reprocessar_fila_resolucao(session)
    segundo = motor.reprocessar_fila_resolucao(session)

    assert primeiro.resolvidos_automaticamente == 1
    assert segundo.itens_avaliados == 0  # já resolvido, nem entra na segunda passada
    assert segundo.resolvidos_automaticamente == 0
    assert session.query(EanGenerico).filter_by(ean="7891000000013").count() == 1  # não duplicou


def test_reprocessar_fila_rodar_duas_vezes_na_faixa_media_nao_conta_como_nova_sugestao(session, monkeypatch):
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_auto_aceite", 92)
    monkeypatch.setattr("reconciliation.motor.settings.reconciliacao.limiar_fila_media", 75)
    generico = _criar_generico(session, "Cinarizina 75mg")
    motor.upsert_fila_resolucao(
        session, ean="7891000000014", descricao_observada="Cinarizina parecida",
        origem=OrigemFila.GPS, valor=100,
    )
    monkeypatch.setattr(motor, "buscar_candidatos", lambda *a, **k: (generico, 80.0))

    primeiro = motor.reprocessar_fila_resolucao(session)
    segundo = motor.reprocessar_fila_resolucao(session)

    assert primeiro.sugestao_atualizada == 1
    assert segundo.itens_avaliados == 1  # ainda pendente, entra de novo
    assert segundo.sugestao_atualizada == 0  # mesma sugestão de antes -> sem mudança
    assert segundo.sem_mudanca == 1
