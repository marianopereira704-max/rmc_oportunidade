"""Rotina de dados do Pedido (pacote `pedido/`): plano, transformação,
vínculo de lojas e a execução com retomada — sem rede (cliente falso) e
sem Spaces (pasta temporária)."""
import datetime as dt

import pytest

from pedido import coleta, plano, transformar, vinculo
from pedido.armazenamento import ArmazenamentoLocal
from pedido.plano import COMPRAS, ESTOQUE, VENDAS, Chaves

HOJE = dt.date(2026, 9, 27)
CH = Chaves("pedido")


# ---------------------------------------------------------------------------
# Plano
# ---------------------------------------------------------------------------

def test_janela_tres_meses_fechados_mais_o_mes_corrente_ate_ontem():
    assert plano.janela(HOJE, 3) == (dt.date(2026, 6, 1), dt.date(2026, 9, 26))
    pers = plano.periodos(HOJE, 3)
    assert [(p.mes, p.fechado) for p in pers] == [("2026-06", True), ("2026-07", True), ("2026-08", True), ("2026-09", False)]
    assert pers[-1].dias[0] == dt.date(2026, 9, 1) and pers[-1].dias[-1] == dt.date(2026, 9, 26)


def test_dia_primeiro_do_mes_nao_tem_mes_corrente():
    pers = plano.periodos(dt.date(2026, 10, 1), 3)
    assert [p.mes for p in pers] == ["2026-07", "2026-08", "2026-09"]
    assert all(p.fechado for p in pers)


def test_janela_atravessa_a_virada_do_ano():
    assert plano.janela(dt.date(2027, 2, 10), 3)[0] == dt.date(2026, 11, 1)


def test_primeira_execucao_falta_tudo_e_na_ordem_compras_vendas_estoque():
    us = plano.unidades_da_empresa(CH, set(), "E1", ["1", "2"], HOJE, 3, None)
    tipos = [u.tipo for u in us]
    assert tipos == [COMPRAS] * 4 + [VENDAS] * 8 + [ESTOQUE]
    assert (us[0].inicio, us[0].fim, us[0].mes_fechado) == (dt.date(2026, 6, 1), dt.date(2026, 6, 30), True)
    corrente = [u for u in us if u.tipo == VENDAS and not u.mes_fechado]
    assert (corrente[0].inicio, corrente[0].fim) == (dt.date(2026, 9, 1), dt.date(2026, 9, 26))


def test_depois_da_carga_so_falta_o_dia_anterior_e_o_estoque():
    lojas = ["1"]
    existentes = set()
    for tipo in (VENDAS, COMPRAS):
        for mes in ("2026-06", "2026-07", "2026-08"):
            existentes.add(CH.mes("E1", "1", tipo, mes))
        for d in range(1, 26):  # até 25/09: falta o 26
            existentes.add(CH.dia("E1", "1", tipo, dt.date(2026, 9, d)))
    us = plano.unidades_da_empresa(CH, existentes, "E1", lojas, HOJE, 3, "2026-09-26")
    assert [(u.tipo, u.inicio, u.fim) for u in us] == [
        (COMPRAS, dt.date(2026, 9, 26), dt.date(2026, 9, 26)),
        (VENDAS, dt.date(2026, 9, 26), dt.date(2026, 9, 26)),
        (ESTOQUE, None, None),
    ]


def test_estoque_ja_tirado_hoje_nao_repete():
    us = plano.unidades_da_empresa(CH, set(), "E1", ["1"], HOJE, 3, HOJE.isoformat())
    assert ESTOQUE not in [u.tipo for u in us]


def test_compras_da_empresa_so_contam_quando_todas_as_lojas_tem_o_arquivo():
    existentes = {CH.mes("E1", "1", COMPRAS, "2026-06")}  # loja 2 ainda não
    us = plano.unidades_da_empresa(CH, existentes, "E1", ["1", "2"], HOJE, 3, None)
    assert any(u.tipo == COMPRAS and u.inicio == dt.date(2026, 6, 1) for u in us)


# ---------------------------------------------------------------------------
# Transformação
# ---------------------------------------------------------------------------

def test_compras_tiram_transferencias_e_limpam_ean():
    df = transformar.compras([
        {"TipoCompra": "0", "CodigoLoja": "1", "CodigoProduto": "10", "ProdutoRelacionado_CodigoBarras": " 7896.004 ",
         "DataEmissaoNF": "2026-09-02T00:00:00", "Quantidade": 2, "VlrUnitario": 89.8, "VlrDesconto": 0,
         "VlrTotalLiquido": 189.9, "Fracao": 1},
        {"TipoCompra": "1", "CodigoLoja": "1", "CodigoProduto": "11"},
    ])
    assert len(df) == 1
    assert df.loc[0, "ean"] == "7896004" and df.loc[0, "data"] == "2026-09-02"
    assert df.loc[0, "vlr_unitario"] == pytest.approx(89.8)


def test_estoque_tira_espaco_do_nome_e_mantem_colunas_mesmo_vazio():
    df = transformar.estoque([{"CodigoLoja": "2", "CodigoProduto": "7", "NomeProduto": " BALAO 10UN", "QtdEstoque": 0.0}])
    assert df.loc[0, "nome"] == "BALAO 10UN"
    vazio = transformar.estoque([])
    assert list(vazio.columns) == transformar.COLUNAS_ESTOQUE and vazio.empty


def test_por_dia_inclui_dias_sem_movimento():
    df = transformar.vendas([{"DataVenda": "2026-09-02T00:00:00", "CodigoLoja": "1", "CodigoProduto": "1", "Quantidade": 1}])
    dias = transformar.por_dia(df, dt.date(2026, 9, 1), dt.date(2026, 9, 3))
    assert [len(v) for v in dias.values()] == [0, 1, 0]


# ---------------------------------------------------------------------------
# Vínculo de lojas (casos reais de 27/09/2026)
# ---------------------------------------------------------------------------

RMC = [
    {"cnpj": "10296546000195", "nome_fantasia": "MEGA FARMA", "razao_social": "MEGA FARMA PRODUTOS FARMACEUTICOS LTDA",
     "numero": 37, "bairro": "CENTRO", "cidade": "GOIANINHA", "uf": "RN"},
    {"cnpj": "10296546000357", "nome_fantasia": "MEGA FARMA", "razao_social": "MEGA FARMA PRODUTOS FARMACEUTICOS LTDA - FILIAL 2",
     "numero": 15, "bairro": "CENTRO", "cidade": "SÃO JOSÉ DE MIPIBU", "uf": "RN"},
    {"cnpj": "23358507000117", "nome_fantasia": "MEGA FARMA", "razao_social": "GISELLE EUGENIA M DE ALMEIDA",
     "numero": 170, "bairro": "SAPUCAIA", "cidade": "GOIANINHA", "uf": "RN"},
    {"cnpj": "04076088000186", "nome_fantasia": "FARMÁCIA CENTRAL", "razao_social": "HUDSON VENZEL PÊGO",
     "numero": 1, "bairro": "", "cidade": "SANTANA DO MANHUAÇU", "uf": "MG"},
    {"cnpj": "11111111000111", "nome_fantasia": "DROGARIA BELA VISTA", "razao_social": "X",
     "numero": 265, "bairro": "", "cidade": "Coronel Fabriciano", "uf": "MG"},
    {"cnpj": "22222222000122", "nome_fantasia": "FAMARCIA DO TRABALHADOR DO PARA", "razao_social": "Y",
     "numero": 511, "bairro": "", "cidade": "TUCURUÍ", "uf": "PA"},
]


def _gps(cod, nome, numero, uf, cnpj=None, emp="E"):
    return {"id_empresa": emp, "codigo_loja": cod, "nome_loja": nome, "cnpj": cnpj, "numero": numero, "uf": uf}


def test_cnpj_igual_e_automatico():
    s = vinculo.sugerir([_gps("04076088000186", "FARMACIA CENTRAL", None, "MG", cnpj="04076088000186")], RMC)[0]
    assert (s.situacao, s.metodo, s.cnpj_rmc) == (vinculo.AUTOMATICO, vinculo.POR_CNPJ, "04076088000186")


def test_mega_farma_pelo_numero_do_endereco_mesmo_com_cidade_errada_na_api():
    ss = vinculo.sugerir([
        _gps("1", "MEGA FARMA 1 CENTRO GOIANINHA", "37", "RN"),
        _gps("3", "MEGA FARMA 3 CENTRO SÃO JOSÉ", "15", "RN"),       # a API diz Parnamirim
        _gps("6", "MEGA FARMA 6 SAPUCAIA", "170-A", "RN "),           # sufixo e UF com espaço
    ], RMC)
    assert [(s.cnpj_rmc, s.situacao) for s in ss] == [
        ("10296546000195", vinculo.AUTOMATICO), ("10296546000357", vinculo.AUTOMATICO), ("23358507000117", vinculo.AUTOMATICO),
    ]


def test_numero_bate_mas_nome_nao_vai_para_confirmacao():
    s = vinculo.sugerir([_gps("1", "OTIMAFARMA CORONEL FABRICIANO", "265 LJ 0", "MG")], RMC)[0]
    assert (s.situacao, s.cnpj_rmc) == (vinculo.CONFIRMAR, "11111111000111")


def test_duas_lojas_do_gps_na_mesma_loja_nossa_nenhuma_automatica():
    ss = vinculo.sugerir([_gps("1", "POUPE MAIS FARMA", "511", "PA", emp="A"),
                          _gps("1", "FARMACIA DO TRABALHADOR DO PARA", "511", "PA", emp="B")], RMC)
    assert {s.situacao for s in ss} == {vinculo.CONFIRMAR}


def test_nome_grudado_e_separado_casa_pelo_numero():
    rmc = [{"cnpj": "33333333000133", "nome_fantasia": "DROGA FARMA", "razao_social": "M NOVAES DROGARIA LTDA",
            "numero": "66", "cidade": "Itapitanga", "uf": "BA"},
           {"cnpj": "44444444000144", "nome_fantasia": "POUPE MAIS FARMA", "razao_social": "JOSE C F DOS SANTOS-ME",
            "numero": "29", "cidade": "MARIBONDO", "uf": "AL"}]
    ss = vinculo.sugerir([_gps("1", "DROGAFARMA", "66", "BA"), _gps("2", "POUPEMAIS FARMA", "29", "AL")], rmc)
    assert [s.situacao for s in ss] == [vinculo.AUTOMATICO, vinculo.AUTOMATICO]


def test_mesmo_numero_em_duas_lojas_da_uf_o_nome_desempata():
    rmc = [{"cnpj": "55555555000155", "nome_fantasia": "DROGARIA FERRARI", "razao_social": "IRMAOS FERRARI B & V LTDA",
            "numero": "683", "cidade": "VILA VALERIO", "uf": "ES"},
           {"cnpj": "66666666000166", "nome_fantasia": "FARMACIA SAO JUDAS", "razao_social": "Z LTDA",
            "numero": "683", "cidade": "SERRA", "uf": "ES"}]
    s = vinculo.sugerir([_gps("1", "DROGARIAS FERRARI", "683", "ES")], rmc)[0]
    assert (s.situacao, s.cnpj_rmc) == (vinculo.AUTOMATICO, "55555555000155")


def test_nome_so_parecido_com_o_mesmo_numero_vai_para_confirmacao():
    rmc = [{"cnpj": "77777777000177", "nome_fantasia": "DROGARIA JOSE DA LUZ", "razao_social": "DROGARIA JOSE DA LUZ LTDA",
            "numero": "345", "cidade": "TIROS", "uf": "MG"}]
    s = vinculo.sugerir([_gps("1", "DROGARIA JOSE SIQUEIRA", "345", "MG")], rmc)[0]
    assert s.situacao == vinculo.CONFIRMAR


def test_sem_candidato_na_uf():
    s = vinculo.sugerir([_gps("1", "FARMACIAS ECONOMICA", "1417", "RS")], RMC)[0]
    assert (s.situacao, s.cnpj_rmc) == (vinculo.SEM_CANDIDATO, None)


def test_numero_com_zero_a_esquerda():
    assert vinculo.numero_endereco("09") == "9" and vinculo.numero_endereco("S/N") is None


# ---------------------------------------------------------------------------
# Execução: retomada, fila de tentativas e orçamento de tempo
# ---------------------------------------------------------------------------

class ClienteFalso:
    def __init__(self, falhar_vendas_vezes=0, relogio=None, custo_por_chamada=0.0):
        self.chamadas = []
        self.falhar_vendas_vezes = falhar_vendas_vezes
        self.relogio = relogio
        self.custo = custo_por_chamada

    def _gasta(self):
        if self.relogio is not None:
            self.relogio.t += self.custo

    def listar_lojas(self, emp):
        return [{"CodigoLoja": "1", "NomeLoja": "L1"}, {"CodigoLoja": "2", "NomeLoja": "L2"}]

    def listar_compras(self, emp, data_inicio, data_fim):
        self.chamadas.append(("compras", data_inicio, data_fim)); self._gasta()
        return [{"TipoCompra": "0", "CodigoLoja": "1", "CodigoProduto": "9", "DataEmissaoNF": data_inicio.isoformat(),
                 "Quantidade": 1, "VlrUnitario": 5}]

    def listar_vendas_loja(self, emp, loja, ini, fim):
        self.chamadas.append(("vendas", loja, ini, fim)); self._gasta()
        if self.falhar_vendas_vezes > 0:
            self.falhar_vendas_vezes -= 1
            raise TimeoutError("estourou 8 min")
        return [{"DataVenda": ini.isoformat(), "CodigoLoja": loja, "CodigoProduto": "9", "Quantidade": 2}]

    def listar_estoque_empresa(self, emp):
        self.chamadas.append(("estoque",)); self._gasta()
        return [{"CodigoLoja": "1", "CodigoProduto": "9", "QtdEstoque": 3}, {"CodigoLoja": "2", "CodigoProduto": "9", "QtdEstoque": 0}]


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _rotina(tmp_path, cliente, orcamento=10_000, relogio=None, tentativas=3):
    return coleta.Coleta(cliente, ArmazenamentoLocal(tmp_path), CH, HOJE, 3, orcamento, tentativas,
                         relogio=relogio or Relogio())


def test_carga_completa_e_depois_nada_a_fazer(tmp_path):
    cli = ClienteFalso()
    rel = _rotina(tmp_path, cli).executar(["E1"], "p1")
    assert rel.unidades_com_falha == 0 and rel.unidades_ok == 4 + 8 + 1
    armaz = ArmazenamentoLocal(tmp_path)
    assert armaz.existe(CH.mes("E1", "2", COMPRAS, "2026-06"))           # loja sem compra: arquivo vazio
    assert armaz.existe(CH.dia("E1", "1", VENDAS, dt.date(2026, 9, 26)))
    assert armaz.ler_df(CH.estoque("E1", "1"))["qtd_estoque"].tolist() == [3.0]
    assert armaz.existe(CH.catalogo("E1"))  # produtos da empresa, pro relatório Sem Classificação
    assert rel.prontos_montados == 2 and armaz.existe("pedido/pronto/E1/1/meta.json")  # tela abre sem juntar brutos
    # Segunda execução no mesmo dia: nada a baixar.
    cli2 = ClienteFalso()
    rel2 = _rotina(tmp_path, cli2).executar(["E1"], "p1")
    assert cli2.chamadas == [] and rel2.unidades_ok == 0


def test_consulta_que_falha_volta_pra_fila_e_sai_na_segunda_volta(tmp_path):
    cli = ClienteFalso(falhar_vendas_vezes=1)
    rel = _rotina(tmp_path, cli).executar(["E1"], "p1")
    assert rel.unidades_com_falha == 1 and rel.pendentes_para_proxima == []
    assert ArmazenamentoLocal(tmp_path).existe(CH.mes("E1", "1", VENDAS, "2026-06"))


def test_falha_em_todas_as_voltas_fica_pendente_para_a_proxima_execucao(tmp_path):
    cli = ClienteFalso(falhar_vendas_vezes=99)
    rel = _rotina(tmp_path, cli, tentativas=2).executar(["E1"], "p1")
    assert len(rel.pendentes_para_proxima) == 8
    assert not ArmazenamentoLocal(tmp_path).existe(CH.mes("E1", "1", VENDAS, "2026-06"))  # nada parcial


def test_orcamento_de_tempo_para_e_a_proxima_execucao_continua(tmp_path):
    relogio = Relogio()
    cli = ClienteFalso(relogio=relogio, custo_por_chamada=60)
    rel = _rotina(tmp_path, cli, orcamento=300, relogio=relogio).executar(["E1"], "p1")
    assert rel.parou_por_tempo and rel.unidades_ok == 5
    cli2 = ClienteFalso()
    rel2 = _rotina(tmp_path, cli2).executar(["E1"], "p1")
    assert rel2.unidades_ok == 13 - 5  # só o que faltou


def test_filtro_de_lojas_ativas(tmp_path):
    rotina = coleta.Coleta(ClienteFalso(), ArmazenamentoLocal(tmp_path), CH, HOJE, 3, 10_000, 3,
                           filtro_lojas=lambda emp, lojas: [l for l in lojas if l["CodigoLoja"] == "1"], relogio=Relogio())
    rotina.executar(["E1"], "p1")
    armaz = ArmazenamentoLocal(tmp_path)
    assert armaz.existe(CH.mes("E1", "1", VENDAS, "2026-06"))
    assert not armaz.existe(CH.mes("E1", "2", VENDAS, "2026-06"))


def test_divisao_entre_execucoes_paralelas_e_estavel_e_completa():
    ids = [f"e{i:02d}" for i in range(10)]
    partes = [coleta.dividir(list(reversed(ids)), p, 4) for p in range(4)]
    assert sorted(sum(partes, [])) == ids
    assert partes[0] == coleta.dividir(ids, 0, 4)
