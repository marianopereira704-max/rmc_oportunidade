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
from integrations import gps_processamento
from integrations.planilha_navegador import PlanilhaRecebida
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

def _enviar_gps(session, pasta_raiz_id, linhas: list[dict], ano_mes: str = "2026-07", nome: str = "gps.xlsx"):
    """Envio GPS pelo MESMO caminho da tela (gps_processamento.iniciar),
    rodando na própria chamada e dentro da sessão do teste."""
    from contextlib import nullcontext

    df = pd.DataFrame(linhas)
    mapa = gps_integ.mapear_colunas(df)
    planilha = PlanilhaRecebida(nome=nome, tamanho_bytes=3, df=df, storage_key=None, conteudo=b"xls")
    iniciou, motivo = gps_processamento.iniciar(
        planilha, gps_integ.preparar_compras(df, mapa), mapa, ano_mes, pasta_raiz_id, "admin",
        fabrica_sessao=lambda: nullcontext(session), em_segundo_plano=False,
    )
    assert iniciou, motivo
    estado = gps_processamento.estado_atual()
    assert estado.sucesso, estado.erro
    return estado


def _criar_loja(session, cnpj, uf="SP") -> Loja:
    loja = Loja(cnpj=cnpj, razao_social="Farmácia Teste", uf=uf, cidade="São Paulo")
    session.add(loja)
    session.flush()
    return loja


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

    _enviar_gps(session, pasta_raiz_id, [{
        "cnpj": "30.208.213/0001-74", "ean": "777777", "descricao": "Produto GPS",
        "Quantidade": 1, "VlrUnitario": 10.0,
    }])

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
    _enviar_gps(session, pasta_raiz_id, [{
        "cnpj": "30.208.213/0001-74", "ean": "555555", "descricao": "Produto GPS",
        "Quantidade": 1, "VlrUnitario": 10.0,
    }])
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


# ---------------------------------------------------------------------------
# Planilha enviada na seção errada (24/09/2026: uma tabela Gruppy subiu na
# Base Genéricos e virou 22 genéricos canônicos falsos)
# ---------------------------------------------------------------------------

_CABECALHO_GRUPPY_REAL = [
    "Família", "EAN", "Produto", "Quantidade Solicitada", "R$ Unitário Bruto", "Desconto", "Preço",
    "R$ Total Líquido Total",
]


def test_base_genericos_recusa_tabela_gruppy_e_nao_grava_nada(session, pasta_raiz_id):
    linha = dict(zip(_CABECALHO_GRUPPY_REAL, ["F1", 7891234567890, "AMOXICILINA 500MG", 10, 20.0, 0.1, 18.0, 180.0]))
    with pytest.raises(ValueError, match="não parece a Base Genéricos"):
        base_genericos_integ.processar_planilha_base_genericos(
            session, _xlsx_bytes([linha]), "ACRESCIMO 3%. - SET 26.xlsx", "admin", pasta_raiz_id,
        )
    assert session.execute(select(func.count()).select_from(BaseGenerico)).scalar_one() == 0
    assert session.execute(select(func.count()).select_from(EanGenerico)).scalar_one() == 0
    assert session.execute(
        select(func.count()).select_from(FSNode).where(FSNode.nome == "ACRESCIMO 3%. - SET 26.xlsx")
    ).scalar_one() == 0


def test_base_genericos_aceita_o_cabecalho_real_da_base():
    base_genericos_integ.validar_planilha(pd.DataFrame(columns=["FCC", "EAN", "DESCRIÇÃO MARCOS"]))


def test_gruppy_recusa_planilha_de_compras_gps():
    df = pd.DataFrame(columns=["cnpj", "EAN", "Produto", "VlrUnitario", "Quantidade"])
    with pytest.raises(ValueError, match="CNPJ"):
        gruppy_integ.validar_planilha(df)
    gruppy_integ.validar_planilha(pd.DataFrame(columns=_CABECALHO_GRUPPY_REAL))


def test_gps_recusa_planilha_sem_nenhuma_compra(session, pasta_raiz_id):
    from contextlib import nullcontext

    df = pd.DataFrame([{"cnpj": "30.208.213/0001-74", "EAN": "7891", "Produto": "X", "Quantidade": None,
                        "VlrUnitario": None}])
    mapa = gps_integ.mapear_colunas(df)
    iniciou, motivo = gps_processamento.iniciar(
        PlanilhaRecebida("x.xlsx", 1, df, None, b"x"), gps_integ.preparar_compras(df, mapa), mapa, "2026-08",
        pasta_raiz_id, "admin", fabrica_sessao=lambda: nullcontext(session), em_segundo_plano=False,
    )
    assert not iniciou
    assert "Nenhuma compra" in motivo
    assert session.execute(select(func.count()).select_from(UploadGPS)).scalar_one() == 0
