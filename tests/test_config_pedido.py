"""Configurações de Pedidos: validação (pedido/configuracao.py), gravação com
histórico (integrations/configuracao_pedido.py) e o efeito no cálculo."""
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base
from integrations import configuracao_pedido as integ
from pedido import calculo, configuracao
from pedido import categorias as cat
from pedido.armazenamento import ArmazenamentoLocal
from pedido.configuracao import ConfigPedido
from pedido.plano import Chaves
from tests.test_pedido_calculo import SEM_GENERICOS, _linha, _pronto


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_padrao_e_o_combinado_e_valido():
    c = ConfigPedido.padrao()
    assert (c.dias_medicamento, c.dias_perfumaria, c.dias_ruptura, c.meses_fechados) == (7, 15, 3, 3)
    assert (c.curva_a, c.curva_b, c.giro_baixo_dias, c.giro_baixo_max_unidades) == (0.5, 0.4, 90, 1)
    assert c.erros() == []


@pytest.mark.parametrize("mudanca, trecho", [
    ({"dias_medicamento": 0}, "medicamento"),
    ({"meses_fechados": 7}, "Meses fechados"),
    ({"curva_a": 0.6, "curva_b": 0.4}, "Curva ABC"),
    ({"dias_ruptura": 7}, "ruptura"),
    ({"dias_por_categoria": {"NAO EXISTE": 5}}, "desconhecida"),
    ({"fator_preco_fora": 1}, "fora do padrão"),
    ({"ruptura_unidades": -1}, "Ruptura (unidades)"),
    ({"dias_sem_classificacao": 0}, "Sem Classificação"),
    # Dashboard (21/10/2026): o excesso é uma régua própria (não depende do ideal do
    # Assistente), e a comparação de preço nunca com menos de 2 lojas (identificaria a loja).
    ({"dias_cobertura_excesso": 5}, "Cobertura do estoque em excesso"),
    ({"dias_cobertura_excesso": 400}, "Cobertura do estoque em excesso"),
    ({"minimo_lojas_comparacao": 1}, "Lojas mínimas"),
    ({"quantidade_comparavel": 0}, "Quantidade comparável"),
    ({"quantidade_comparavel": 1.5}, "Quantidade comparável"),
])
def test_erros_de_validacao(mudanca, trecho):
    erros = ConfigPedido.de_dict(mudanca).erros()
    assert any(trecho in e for e in erros), erros


def test_de_dict_completa_campo_novo_com_o_padrao():
    c = ConfigPedido.de_dict({"dias_medicamento": 10, "campo_que_nao_existe": 1})
    assert c.dias_medicamento == 10 and c.dias_perfumaria == 15


def test_parametros_do_dashboard_tem_padrao_e_config_antiga_continua_valendo():
    c = ConfigPedido.padrao()
    assert (c.dias_cobertura_excesso, c.quantidade_comparavel, c.minimo_lojas_comparacao) == (60, 0.5, 2)
    # Config gravada antes dos campos existirem (sem eles no JSON): usa o padrão, não quebra.
    antiga = {k: v for k, v in c.para_dict().items()
              if k not in ("dias_cobertura_excesso", "quantidade_comparavel", "minimo_lojas_comparacao")}
    lida = ConfigPedido.de_dict(antiga)
    assert lida.dias_cobertura_excesso == 60 and lida.erros() == []


def test_gravar_guarda_historico_e_a_ultima_vale(session):
    assert integ.vigente(session).alterado_por is None
    integ.salvar(session, ConfigPedido.de_dict({"dias_medicamento": 10}), "ana")
    integ.salvar(session, ConfigPedido.de_dict({"dias_medicamento": 10, "dias_ruptura": 2}), "bia")
    v = integ.vigente(session)
    assert v.config.dias_medicamento == 10 and v.config.dias_ruptura == 2 and v.alterado_por == "bia"
    h = integ.historico(session)
    assert [x["por"] for x in h] == ["bia", "ana"]
    assert h[0]["campos"] == ["dias_ruptura"] and h[1]["campos"] == ["dias_medicamento"]
    assert integ.versao(session) == 2


def test_gravar_valor_invalido_e_recusado(session):
    with pytest.raises(ValueError):
        integ.salvar(session, ConfigPedido.de_dict({"meses_fechados": 0}), "ana")
    assert integ.versao(session) == 0


def test_janela_chega_a_rotina_pelo_spaces(tmp_path):
    armaz, chaves = ArmazenamentoLocal(tmp_path), Chaves("pedido")
    assert configuracao.meses_da_rotina(armaz, chaves, 3) == 3             # nada gravado: padrão
    integ.publicar_para_rotina(armaz, chaves, ConfigPedido.de_dict({"meses_fechados": 5}), "ana")
    assert configuracao.meses_da_rotina(armaz, chaves, 3) == 5
    armaz.salvar_json(chaves.configuracao(), {"meses_fechados": 99})      # inválido: padrão
    assert configuracao.meses_da_rotina(armaz, chaves, 3) == 3


def test_dias_por_categoria_e_piso_mudam_o_maximo():
    base = pd.DataFrame([("200", "MIP/OTC"), ("100", "INFANTIL")], columns=["ean", "categoria"])
    p = _pronto([("1", "2026-09-20", 118, 1), ("2", "2026-09-20", 118, 1), ("3", "2026-09-20", 2, 1)],
                [("1", "200", "REMEDIO", "L", 0, 1), ("2", "100", "FRALDA", "L", 0, 1), ("3", "200", "RARO", "L", 0, 1)])
    cfg = ConfigPedido.de_dict({"dias_por_categoria": {"INFANTIL": 20}, "piso_maximo": 2})
    df = calculo.calcular(p, base, SEM_GENERICOS, cfg.parametros(0.10))
    assert _linha(df, "REMEDIO")["estoque_max"] == 7                        # grupo
    assert _linha(df, "FRALDA")["estoque_max"] == 20                        # categoria com dias próprios
    assert _linha(df, "RARO")["estoque_max"] == 2                           # 2/118 × 7 < 1 → piso 2
    assert cfg.dias_da_categoria("INFANTIL") == 20 and cfg.dias_da_categoria("HIGIENE") == 15
    assert cfg.dias_da_categoria(cat.GENERICO) == 7


def test_coluna_de_dias_proprios_da_tela():
    from views.config_pedido import _dias_proprios

    editada = pd.DataFrame({"Categoria": ["INFANTIL", "HIGIENE", "CUIDADOS"], "Dias próprios": ["20", "", "ab"]})
    dias, erros = _dias_proprios(editada)
    assert dias == {"INFANTIL": 20} and len(erros) == 1 and "CUIDADOS" in erros[0]


# ---------------------------------------------------------------------------
# Personalização por loja (01/10/2026)
# ---------------------------------------------------------------------------

def _lojas(session, *nomes):
    from core.models import Loja
    lojas = [Loja(cnpj=f"{i:014d}", razao_social=n, uf="MG", cidade="X") for i, n in enumerate(nomes, start=1)]
    session.add_all(lojas)
    session.flush()
    return [l.id for l in lojas]


def test_diferencas_e_aplicar_campo_a_campo():
    padrao = ConfigPedido.de_dict({"dias_por_categoria": {"INFANTIL": 20}})
    loja = configuracao.aplicar(padrao, {})
    loja.dias_medicamento = 10
    loja.dias_por_categoria = {"INFANTIL": 20, "MIP/OTC": 5}
    loja.meses_fechados = 6                                      # a janela não entra na personalização
    dif = configuracao.diferencas(padrao, loja)
    assert dif == {"dias_medicamento": 10, "dias_por_categoria": {"MIP/OTC": 5}}
    assert configuracao.quantos_campos(dif) == 2
    # O que a loja não mudou segue o padrão, inclusive quando o padrão muda depois.
    padrao_novo = ConfigPedido.de_dict({"dias_perfumaria": 20, "dias_por_categoria": {"INFANTIL": 25}})
    efetiva = configuracao.aplicar(padrao_novo, dif)
    assert (efetiva.dias_medicamento, efetiva.dias_perfumaria) == (10, 20)
    assert efetiva.dias_por_categoria == {"INFANTIL": 25, "MIP/OTC": 5}
    assert configuracao.aplicar(padrao_novo, {"meses_fechados": 1}).meses_fechados == padrao_novo.meses_fechados


def test_parametros_do_dashboard_sao_personalizaveis_por_loja(session):
    """21/10/2026: a loja pode ter o seu limite de excesso, e o resto segue o padrão."""
    (loja,) = _lojas(session, "REIS F1")
    padrao = integ.vigente(session).config
    dif = integ.salvar_loja(session, loja, configuracao.aplicar(padrao, {"dias_cobertura_excesso": 90}), "mariano")
    assert dif == {"dias_cobertura_excesso": 90}
    efetiva, _ = integ.config_da_loja(session, loja)
    assert efetiva.dias_cobertura_excesso == 90 and efetiva.minimo_lojas_comparacao == 2
    # O padrão muda depois: o que a loja não personalizou acompanha.
    integ.salvar(session, ConfigPedido.de_dict({"quantidade_comparavel": 0.7}), "ana")
    efetiva, _ = integ.config_da_loja(session, loja)
    assert efetiva.dias_cobertura_excesso == 90 and efetiva.quantidade_comparavel == 0.7
    # Valor fora do intervalo é recusado.
    with pytest.raises(ValueError):
        integ.salvar_loja(session, loja, configuracao.aplicar(efetiva, {"dias_cobertura_excesso": 3}), "ana")
    # O excesso NÃO depende dos dias de estoque do Assistente: 30 dias de excesso com 15 de perfumaria vale.
    assert ConfigPedido.de_dict({"dias_cobertura_excesso": 10}).erros() == []


def test_reis_cada_loja_com_seus_dias_e_a_4_no_padrao(session):
    """O exemplo do Mariano: Reis F1 e F2 com dias próprios, F3 com outros,
    F4 no padrão."""
    f1, f2, f3, f4 = _lojas(session, "REIS F1", "REIS F2", "REIS F3", "REIS F4")
    padrao = integ.vigente(session).config
    integ.salvar_loja(session, f1, configuracao.aplicar(padrao, {"dias_medicamento": 10}), "mariano")
    integ.salvar_loja(session, f2, configuracao.aplicar(padrao, integ.da_loja(session, f1)), "mariano")  # cópia
    integ.salvar_loja(session, f3, configuracao.aplicar(padrao, {"dias_por_categoria": {"INFANTIL": 30}}), "mariano")
    assert integ.config_da_loja(session, f1)[0].dias_medicamento == 10
    assert integ.config_da_loja(session, f2)[0].dias_medicamento == 10
    assert integ.config_da_loja(session, f3)[0].dias_da_categoria("INFANTIL") == 30
    assert integ.config_da_loja(session, f4) == (padrao, {})
    # Cópia, não ligação: mudar a F1 não muda a F2.
    integ.salvar_loja(session, f1, configuracao.aplicar(padrao, {"dias_medicamento": 12}), "mariano")
    assert integ.config_da_loja(session, f2)[0].dias_medicamento == 10
    assert [(l["nome"], l["campos"]) for l in integ.lojas_personalizadas(session)] == [
        ("REIS F1", 1), ("REIS F2", 1), ("REIS F3", 1)]
    # Remover: volta ao padrão, e o histórico fica.
    v = integ.versao_personalizacoes(session)
    integ.remover_loja(session, f3, "mariano")
    assert integ.config_da_loja(session, f3) == (padrao, {}) and integ.versao_personalizacoes(session) > v
    assert [h["valores"] for h in integ.historico_loja(session, f3)] == [{}, {"dias_por_categoria": {"INFANTIL": 30}}]
    assert "REIS F3" not in [l["nome"] for l in integ.lojas_personalizadas(session)]


def test_salvar_loja_igual_ao_padrao_nao_grava_e_invalido_e_recusado(session):
    (loja,) = _lojas(session, "LOJA")
    padrao = integ.vigente(session).config
    assert integ.salvar_loja(session, loja, padrao, "ana") == {} and integ.versao_personalizacoes(session) == 0
    with pytest.raises(ValueError):
        integ.salvar_loja(session, loja, configuracao.aplicar(padrao, {"dias_medicamento": 0}), "ana")


def test_faixa_de_custo_vai_do_config_para_o_calculo():
    c = ConfigPedido.de_dict({"custo_faixa_min": 0.2, "custo_faixa_max": 0.9, "fator_cadastro_nota": 3})
    p = c.parametros(0.10)
    assert (p.custo_faixa_min, p.custo_faixa_max, p.fator_cadastro_nota) == (0.2, 0.9, 3)
    assert ConfigPedido.de_dict({"custo_faixa_min": 0.9, "custo_faixa_max": 0.5}).erros()


def test_parametros_da_ruptura_padrao_validacao_e_personalizacao():
    """27/10/2026 (R13 do design da Ruptura): período da perda, risco e nível de serviço, personalizáveis por loja."""
    import dataclasses
    from core import dashboard_ruptura as dr
    from pedido import configuracao as cfg

    padrao = cfg.ConfigPedido()
    assert (padrao.ruptura_perda_dias, padrao.ruptura_risco_alto, padrao.ruptura_risco_medio) == (30, 0.14, 0.03)
    assert (padrao.ruptura_sla_vermelho, padrao.ruptura_sla_verde) == (0.65, 0.75) and padrao.erros() == []
    errada = dataclasses.replace(padrao, ruptura_risco_medio=0.2, ruptura_sla_verde=0.5, ruptura_perda_dias=3)
    assert len(errada.erros()) == 3
    loja = cfg.aplicar(padrao, {"ruptura_perda_dias": 15, "ruptura_risco_alto": 0.7})
    assert cfg.diferencas(padrao, loja) == {"ruptura_perda_dias": 15, "ruptura_risco_alto": 0.7}
    r = dr.Regras.da_config(loja)
    assert (r.periodo_perda, r.risco_alto, r.risco_medio, r.sla_verde) == (15, 0.7, 0.03, 0.75)
