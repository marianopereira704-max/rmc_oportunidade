"""Assistente de pedido: área de trabalho por usuário + loja, seleção,
listas salvas com nome, cards do pedido inteiro, avisos vistos e as
exportações (tabela completa, giro baixo). Os filtros da linha da tela:
tests/test_pedido_calculo.py."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.models import Base, ExportacaoPedido, ListaPedido, Loja
from integrations import pedido_area as area
from pedido import calculo
from tests.test_pedido_calculo import BASE, SEM_GENERICOS, _linha, _pronto


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Loja(id=1, cnpj="1", razao_social="L", uf="MG", cidade="X"))
        s.flush()
        yield s


def test_area_guarda_so_o_que_difere_e_volta_ao_padrao(session):
    area.alterar_quantidade(session, 7, 1, "G1", 10, 4)
    area.marcar(session, 7, 1, {"P2": False, "P3": False})
    a = area.area(session, 7, 1)
    assert a.itens["G1"].quantidade == 10 and a.itens["G1"].selecionado is None
    assert a.itens["P2"].selecionado is False and a.atualizado_em is not None
    area.alterar_quantidade(session, 7, 1, "G1", 4, 4)                # igual à sugestão: some
    area.marcar(session, 7, 1, {"P2": True})
    assert set(area.area(session, 7, 1).itens) == {"P2", "P3"}
    assert area.area(session, 8, 1).itens == {}                        # outro usuário: área própria
    assert area.descartar(session, 7, 1) == 2 and area.area(session, 7, 1).itens == {}


def test_editar_quantidade_devolve_a_marcacao_automatica(session):
    area.marcar(session, 7, 1, {"G1": False})
    area.alterar_quantidade(session, 7, 1, "G1", 5, 2)                 # digitou > 0: volta a marcar sozinho
    assert area.area(session, 7, 1).itens["G1"].selecionado is None


def test_listas_visiveis_abrem_na_area_e_somem_ao_exportar(session):
    lid = area.salvar_lista(session, 7, "Ana", 1, "Reposição semanal",
                            [("G1", 10, True), ("P2", 3, False), ("P3", 0, False)], 10, 99.9)
    l = area.listas(session, 1)[0]
    assert (l.nome, l.criado_por, l.itens, l.unidades, l.valor) == ("Reposição semanal", "Ana", 1, 10, 99.9)
    assert area.area(session, 7, 1).lista_aberta_id == lid
    # Bruno vê o aviso e abre a lista da Ana.
    assert any("Ana tem a lista" in a for a in area.avisos(session, 8, "Bruno", 1, dt.date.today()))
    assert area.abrir_lista(session, 8, 1, lid) == 3
    b = area.area(session, 8, 1)
    assert b.itens["G1"].quantidade == 10 and b.itens["P2"].selecionado is False and b.lista_aberta_id == lid
    assert area.ao_exportar(session, 8, 1) == "Reposição semanal"
    assert session.query(ListaPedido).count() == 0 and area.area(session, 7, 1).lista_aberta_id is None
    with pytest.raises(ValueError):
        area.salvar_lista(session, 7, "Ana", 1, "  ", [], 0, 0)


def test_aviso_de_exportacao_de_outra_pessoa(session):
    session.add(ExportacaoPedido(loja_id=1, formato="Excel", itens=1, unidades=1, valor=1, exportado_por="Bruno",
                                 exportado_em=dt.datetime.utcnow()))
    session.flush()
    hoje_br = dt.datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    assert any("Bruno exportou" in a for a in area.avisos(session, 7, "Ana", 1, hoje_br))
    assert area.avisos(session, 9, "Bruno", 1, hoje_br) == []


def _pedido():
    vendas = [(c, "2026-09-20", 118, 1) for c in ("1", "2", "3")]
    estoque = [("1", "200", "ZERO", "LAB A", 0, 1), ("2", "200", "DOIS", "LAB B", 2, 1), ("3", "100", "SABAO", "LAB A", 0, 1)]
    compras = [("1", "200", "2026-09-01", 2, 0, 1, "LAB A", "DISTRIB X")]
    return calculo.calcular(_pronto(vendas, estoque, compras), BASE, SEM_GENERICOS)


def test_selecao_e_cards_do_pedido_inteiro():
    df = _pedido()
    zero, dois = _linha(df, "ZERO")["linha"], _linha(df, "DOIS")["linha"]
    com = calculo.aplicar_area(df, {zero: area.ItemArea(20, None), dois: area.ItemArea(None, False)})
    z, d, s = _linha(com, "ZERO"), _linha(com, "DOIS"), _linha(com, "SABAO")
    assert z["quantidade"] == 20 and z["selecionado"] and z["alterado"] and calculo.ALTERADO in z["status"]
    assert z["subtotal"] == pytest.approx(40)                           # última compra 2,00 × 20
    assert not d["selecionado"] and s["selecionado"]
    i = calculo.indicadores(calculo.lista(com))
    # Marcados: ZERO (20) e SABAO (15). DOIS (5) desmarcado.
    assert (i.unidades, i.itens, i.marcados, i.ruptura, i.ruptura_sem_pedido) == (35, 3, 2, 2, 0)
    com2 = calculo.aplicar_area(df, {zero: area.ItemArea(None, False)})
    assert calculo.indicadores(calculo.lista(com2)).ruptura_sem_pedido == 1


def test_avisos_vistos_valem_por_foto_e_por_usuario(session):
    """Pop-ups do Assistente de pedido (01/10/2026): uma vez por foto do GPS,
    por usuário + loja; foto nova, pergunta de novo."""
    assert area.avisos_vistos(session, 7, 1) == {}
    area.marcar_aviso_visto(session, 7, 1, area.AVISO_NEGATIVO, "2026-09-27", "zero")
    area.marcar_aviso_visto(session, 7, 1, area.AVISO_SEM_CLASSIFICACAO, "2026-09-27", "entendi")
    assert area.avisos_vistos(session, 7, 1) == {area.AVISO_NEGATIVO: "2026-09-27",
                                                 area.AVISO_SEM_CLASSIFICACAO: "2026-09-27"}
    area.marcar_aviso_visto(session, 7, 1, area.AVISO_NEGATIVO, "2026-09-28", "corrigir")    # foto nova
    assert area.avisos_vistos(session, 7, 1)[area.AVISO_NEGATIVO] == "2026-09-28"
    assert area.avisos_vistos(session, 8, 1) == {}                                            # outro usuário
    # Não mexe no "Salvo automaticamente · hh:mm" da área de trabalho.
    assert area.area(session, 7, 1).atualizado_em is None


def test_exportacoes_tabela_completa_e_giro_baixo():
    """Exportação de 01/10/2026: formato Gruppy (o de sempre), tabela
    completa (pedido inteiro, marcado ou não) e só giro baixo (maior valor
    parado primeiro)."""
    df = calculo.aplicar_area(_pedido(), {})
    lst = calculo.lista(df, ocultar_giro_baixo=False)
    completa = calculo.tabela_completa(lst)
    assert len(completa) == len(lst) and {"PRODUTO", "QUANTIDADE", "MARCADO", "VENDA 90 DIAS", "EAN 1"} <= set(completa)
    df.loc[df["nome"].isin(["DOIS", "SABAO"]), "giro_baixo"] = True
    df.loc[df["nome"] == "DOIS", ["estoque", "preco"]] = [2, 5.0]       # R$ 10 parados
    df.loc[df["nome"] == "SABAO", ["estoque", "preco"]] = [4, 7.5]      # R$ 30 parados
    giro = calculo.giro_baixo(df)
    assert list(giro["PRODUTO"]) == ["SABAO", "DOIS"]                    # maior valor parado primeiro
    assert list(giro["VALOR PARADO EM ESTOQUE"]) == [30.0, 10.0]
    df.loc[df["nome"] == "DOIS", "estoque"] = 0                         # sem estoque: nada parado, sai
    assert list(calculo.giro_baixo(df)["PRODUTO"]) == ["SABAO"]


def test_ruptura_segue_o_parametro_de_unidades():
    vendas = [(c, "2026-09-20", 118, 1) for c in ("1", "2")]
    estoque = [("1", "200", "ZERO", "L", 0, 1), ("2", "200", "DOIS", "L", 2, 1)]
    df = calculo.calcular(_pronto(vendas, estoque), BASE, SEM_GENERICOS, calculo.Parametros(ruptura_unidades=2))
    lst = calculo.lista(calculo.aplicar_area(df, {}))
    assert {n for n, st in zip(lst["nome"], lst["status"]) if calculo.RUPTURA in st} == {"ZERO", "DOIS"}


def test_regras_em_memoria_dao_o_mesmo_que_o_banco(session):
    """A tela atualiza a cópia da área na sessão com `com_quantidade` e
    `com_marcacoes` em vez de reler o banco (02/10/2026): têm de dar o mesmo
    resultado que gravar e ler de novo."""
    memoria = {}
    passos = [
        ("qtd", "A", 10, 7), ("marca", {"A": False, "B": True}), ("qtd", "A", 7, 7),   # volta à sugestão, marca fica
        ("marca", {"C": False}), ("qtd", "C", 3, 5), ("qtd", "B", 4, 4),
    ]
    for passo in passos:
        if passo[0] == "qtd":
            _, linha, q, sug = passo
            area.alterar_quantidade(session, 7, 1, linha, q, sug)
            memoria = area.com_quantidade(memoria, linha, q, sug)
        else:
            area.marcar(session, 7, 1, passo[1])
            memoria = area.com_marcacoes(memoria, passo[1])
        assert area.area(session, 7, 1).itens == memoria, passo
