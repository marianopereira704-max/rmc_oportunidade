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
    # Compras: 6 meses fechados + o corrente (Análise); vendas: 3 + o corrente.
    assert rel.unidades_com_falha == 0 and rel.unidades_ok == 7 + 8 + 1
    armaz = ArmazenamentoLocal(tmp_path)
    assert armaz.existe(CH.mes("E1", "2", COMPRAS, "2026-06"))           # loja sem compra: arquivo vazio
    assert armaz.existe(CH.dia("E1", "1", VENDAS, dt.date(2026, 9, 26)))
    assert armaz.ler_df(CH.estoque("E1", "1"))["qtd_estoque"].tolist() == [3.0]
    assert armaz.existe(CH.catalogo("E1"))  # produtos da empresa, pro relatório Sem Classificação
    assert rel.prontos_montados == 2 and armaz.existe("pedido/pronto/E1/1/meta.json")  # tela abre sem juntar brutos
    # Modelo de estoque do Dashboard (22/10/2026): montado na mesma passada, um por loja, sem banco.
    assert rel.modelos_montados == 2 and armaz.existe("pedido/estoque/E1/1/meta.json")
    assert armaz.ler_json("pedido/estoque/E1/1/meta.json")["skus"] == 1          # o produto com 3 un. em estoque
    assert armaz.ler_json(CH.marcador_compras("E1"))["meses"]["2026-08"] == "2026-09-27"
    # Segunda execução no mesmo dia: só as compras dos últimos 5 dias, de
    # novo (nota lançada com atraso — 05/10/2026).
    cli2 = ClienteFalso()
    rel2 = _rotina(tmp_path, cli2).executar(["E1"], "p1")
    assert cli2.chamadas == [("compras", dt.date(2026, 9, 22), dt.date(2026, 9, 26))] and rel2.unidades_ok == 1


def test_falha_no_modelo_de_estoque_nao_derruba_o_pronto_e_vai_pro_relatorio(tmp_path, monkeypatch):
    def quebrar(*a, **k):
        raise RuntimeError("deu ruim no modelo")
    monkeypatch.setattr(coleta.estoque_modelo, "atualizar", quebrar)
    rel = _rotina(tmp_path, ClienteFalso()).executar(["E1"], "p1")
    assert rel.prontos_montados == 2 and rel.modelos_montados == 0                 # o pronto saiu normalmente
    assert [f["unidade"] for f in rel.falhas] == ["modelo de estoque E1/1", "modelo de estoque E1/2"]
    assert ArmazenamentoLocal(tmp_path).existe("pedido/pronto/E1/1/meta.json")


def test_foto_de_estoque_vai_para_o_historico_de_cada_loja(tmp_path):
    """27/10/2026: a perda por ruptura é medida; a rotina guarda o que mudou no estoque a cada foto."""
    rel = _rotina(tmp_path, ClienteFalso()).executar(["E1"], "p1")
    armaz = ArmazenamentoLocal(tmp_path)
    assert rel.historicos_gravados == 2
    loja1 = armaz.ler_df(CH.historico_estoque("E1", "1", "2026-09"))
    assert list(zip(loja1["codigo_produto"], loja1["data"], loja1["estoque"])) == [("9", "2026-09-27", 3.0)]
    assert armaz.ler_json(CH.historico_estoque_fotos("E1", "2"))["dias"] == ["2026-09-27"]


def test_so_estoque_baixa_so_a_foto_e_grava_o_historico(tmp_path):
    """--so-estoque (27/10/2026): começar o histórico num dia sem rotina completa."""
    cli = ClienteFalso()
    rot = coleta.Coleta(cli, ArmazenamentoLocal(tmp_path), CH, HOJE, 3, 10_000, 3, relogio=Relogio(), so_estoque=True)
    rel = rot.executar(["E1"], "p1")
    armaz = ArmazenamentoLocal(tmp_path)
    assert cli.chamadas == [("estoque",)] and rel.unidades_ok == 1 and rel.historicos_gravados == 2
    assert rel.prontos_montados == 0 and not armaz.existe("pedido/pronto/E1/1/meta.json")
    assert armaz.existe(CH.historico_estoque("E1", "1", "2026-09"))


def test_falha_no_historico_nao_derruba_a_foto_e_vai_pro_relatorio(tmp_path, monkeypatch):
    def quebrar(*a, **k):
        raise RuntimeError("deu ruim no histórico")
    monkeypatch.setattr(coleta.historico_estoque, "registrar", quebrar)
    rel = _rotina(tmp_path, ClienteFalso()).executar(["E1"], "p1")
    armaz = ArmazenamentoLocal(tmp_path)
    assert armaz.ler_df(CH.estoque("E1", "1"))["qtd_estoque"].tolist() == [3.0]          # a foto saiu
    assert armaz.existe(CH.marcador_estoque("E1")) and rel.historicos_gravados == 0
    assert [f["unidade"] for f in rel.falhas] == ["histórico do estoque E1/1", "histórico do estoque E1/2"]


def test_segunda_passada_nao_remonta_o_modelo_se_nada_mudou(tmp_path):
    _rotina(tmp_path, ClienteFalso()).executar(["E1"], "p1")
    rel2 = _rotina(tmp_path, ClienteFalso()).executar(["E1"], "p1")
    assert rel2.prontos_montados == 2 and rel2.modelos_montados == 0                # em dia: só relê


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
    assert rel2.unidades_ok == 16 - 5  # só o que faltou


def test_filtro_de_lojas_ativas(tmp_path):
    rotina = coleta.Coleta(ClienteFalso(), ArmazenamentoLocal(tmp_path), CH, HOJE, 3, 10_000, 3,
                           filtro_lojas=lambda emp, lojas: [l for l in lojas if l["CodigoLoja"] == "1"], relogio=Relogio())
    rotina.executar(["E1"], "p1")
    armaz = ArmazenamentoLocal(tmp_path)
    assert armaz.existe(CH.mes("E1", "1", VENDAS, "2026-06"))
    assert not armaz.existe(CH.mes("E1", "2", VENDAS, "2026-06"))


class ClienteLojasInstavel(ClienteFalso):
    """A lista de lojas responde 502 nas primeiras N chamadas (05/10/2026:
    366 de 388 empresas assim)."""
    def __init__(self, falhar_lojas_vezes, **kw):
        super().__init__(**kw)
        self.falhar_lojas_vezes = falhar_lojas_vezes

    def listar_lojas(self, emp):
        if self.falhar_lojas_vezes > 0:
            self.falhar_lojas_vezes -= 1
            raise RuntimeError("A API do GPS respondeu 502 em /lojas")
        return super().listar_lojas(emp)


def test_empresa_cuja_lista_de_lojas_falhou_volta_depois_de_uma_pausa(tmp_path):
    pausas = []
    rotina = coleta.Coleta(ClienteLojasInstavel(1), ArmazenamentoLocal(tmp_path), CH, HOJE, 3, 10_000, 3,
                           relogio=Relogio(), dormir=pausas.append)
    rel = rotina.executar(["E1"], "p1")
    assert pausas == [120.0] and rel.empresas_processadas == 1 and rel.unidades_ok == 16
    assert rel.empresas_sem_lista_de_lojas == 0 and not [f for f in rel.falhas if f["unidade"].startswith("lojas")]


def test_lista_de_lojas_que_falha_em_todas_as_voltas_entra_uma_vez_no_relatorio(tmp_path):
    pausas = []
    rotina = coleta.Coleta(ClienteLojasInstavel(99), ArmazenamentoLocal(tmp_path), CH, HOJE, 3, 10_000, 3,
                           relogio=Relogio(), dormir=pausas.append)
    rel = rotina.executar(["E1", "E2"], "p1")
    assert len(pausas) == 2 and rel.empresas_processadas == 0 and rel.empresas_sem_lista_de_lojas == 2
    assert sorted(f["unidade"] for f in rel.falhas) == ["lojas E1", "lojas E2"] and "502" in rel.falhas[0]["erro"]


def test_chamada_inicial_tenta_de_novo_com_espera():
    from pedido import __main__ as rotina

    esperas, respostas = [], [RuntimeError("502"), RuntimeError("502"), ["E1"]]

    def chamada():
        r = respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    assert rotina._com_tentativas(chamada, "teste", esperas=(1, 2, 3), dormir=esperas.append) == ["E1"]
    assert esperas == [1, 2]
    with pytest.raises(RuntimeError):
        rotina._com_tentativas(lambda: (_ for _ in ()).throw(RuntimeError("fora")), "teste", esperas=(1,),
                               dormir=esperas.append)


def test_execucao_que_cai_no_inicio_grava_relatorio_com_o_erro(tmp_path, monkeypatch):
    """Execuções de 03 a 07/10/2026: morriam em ~30 s sem relatório nenhum."""
    from pedido import __main__ as rotina

    class SemEmpresas:
        def listar_empresas(self):
            raise RuntimeError("A API do GPS respondeu 502 em /empresas")

    monkeypatch.setattr(rotina, "cliente_das_configuracoes", lambda **kw: SemEmpresas())
    monkeypatch.setattr(rotina, "ESPERAS_INICIAIS", ())
    monkeypatch.setattr(rotina, "_com_tentativas", lambda f, d, esperas=(), dormir=None: f())
    assert rotina.main(["--destino", f"local:{tmp_path}", "--hoje", "2026-10-07", "--todas-as-lojas"]) == 1
    data, relatorios = coleta.ultimas_execucoes(ArmazenamentoLocal(tmp_path), CH)
    assert data == "2026-10-07" and "502" in relatorios[0]["erro_geral"] and relatorios[0]["parte"] == "parte1de1"


def test_divisao_entre_execucoes_paralelas_e_estavel_e_completa():
    ids = [f"e{i:02d}" for i in range(10)]
    partes = [coleta.dividir(list(reversed(ids)), p, 4) for p in range(4)]
    assert sorted(sum(partes, [])) == ids
    assert partes[0] == coleta.dividir(ids, 0, 4)


# ---------------------------------------------------------------------------
# Compras da Análise: 6 meses, mês fechado até o dia 6, últimos 5 dias
# ---------------------------------------------------------------------------

def test_mes_fechado_e_baixado_de_novo_ate_o_dia_6():
    """Nota emitida em setembro e lançada em 3/10 só aparece baixando setembro
    de novo; a baixa do dia 6 (ou depois) é a definitiva."""
    assert plano.dia_definitivo("2026-09") == dt.date(2026, 10, 6)
    assert plano.dia_definitivo("2026-12") == dt.date(2027, 1, 6)
    dia3 = dt.date(2026, 10, 3)
    assert plano.compras_a_rebaixar(dia3, 6, {"2026-09": "2026-10-02"}) == ({"2026-09"}, {dt.date(2026, 10, 1), dt.date(2026, 10, 2)})
    assert plano.compras_a_rebaixar(dia3, 6, {"2026-09": "2026-10-03"})[0] == set()        # já baixado hoje
    assert plano.compras_a_rebaixar(dt.date(2026, 10, 9), 6, {"2026-09": "2026-10-06"})[0] == set()  # definitivo
    assert plano.compras_a_rebaixar(dt.date(2026, 10, 9), 6, {})[0] == {"2026-09"}            # sem registro
    assert plano.compras_a_rebaixar(dt.date(2026, 10, 9), 6, {"2026-09": "2026-10-05"})[0] == {"2026-09"}


def test_compras_seis_meses_e_vendas_tres():
    existentes = set()
    for mes in ("2026-03", "2026-04", "2026-05", "2026-06", "2026-07"):
        existentes.add(CH.mes("E1", "1", COMPRAS, mes))
    us = plano.unidades_da_empresa(CH, existentes, "E1", ["1"], HOJE, 3, HOJE.isoformat(),
                                   meses_compras=6, compras_baixadas={"2026-08": "2026-09-10"})
    compras = [(u.inicio, u.fim) for u in us if u.tipo == COMPRAS]
    # Falta agosto (o arquivo) e o mês corrente; março–julho já estão.
    assert compras == [(dt.date(2026, 8, 1), dt.date(2026, 8, 31)), (dt.date(2026, 9, 1), dt.date(2026, 9, 26))]
    assert min(u.inicio for u in us if u.tipo == VENDAS) == dt.date(2026, 6, 1)


def test_compras_guardam_o_nome_do_produto():
    df = transformar.compras([{"TipoCompra": "0", "CodigoLoja": "1", "CodigoProduto": "10",
                               "ProdutoRelacionado_NomeProduto": " LOSARTANA 50MG 30CP ", "Quantidade": 1}])
    assert df.loc[0, "nome"] == "LOSARTANA 50MG 30CP"



# ---------------------------------------------------------------------------
# Compras da rede por mês (pedido/rede.py)
# ---------------------------------------------------------------------------

def _compra(loja, produto, ean, data, qtd, unit, desc=0, fracao=1, nome=None):
    return {"TipoCompra": "0", "CodigoLoja": loja, "CodigoProduto": produto, "ProdutoRelacionado_CodigoBarras": ean,
            "DataEmissaoNF": data, "Quantidade": qtd, "VlrUnitario": unit, "VlrDesconto": desc, "Fracao": fracao,
            "NroNF": "123", "NomeRazaoSocialFornecedor": "DIST", "ProdutoRelacionado_NomeProduto": nome}


def test_rede_junta_as_lojas_por_mes_e_so_refaz_quando_muda(tmp_path):
    from pedido import rede

    armaz = ArmazenamentoLocal(tmp_path)
    c = transformar.compras([_compra("1", "10", "0789", "2026-09-03", 2, 30.0, desc=10, fracao=3, nome="DISPLAY X"),
                             _compra("2", "11", "0123", "2026-09-04", 1, 5.0)])
    for loja, fatia in transformar.por_loja(c, ["1", "2"]).items():
        armaz.salvar_df(CH.mes("E1", loja, COMPRAS, "2026-09"), fatia)
    armaz.salvar_df(CH.estoque("E1", "1"), transformar.estoque([
        {"CodigoLoja": "1", "CodigoProduto": "10", "NomeProduto": "DISPLAY X", "VlrPrecoCompra": 9.0, "VlrPrecoVenda": 15.0}]))
    armaz.salvar_json(CH.marcador_compras("E1"), {"meses": {"2026-09": "2026-10-06"}})

    assert rede.meses_visiveis(dt.date(2026, 10, 5)) == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    resumo = rede.atualizar(armaz, CH, dt.date(2026, 10, 6))
    assert {"mes": "2026-09", "situacao": "montado", "lojas": 2, "linhas": 2} == {k: v for k, v in resumo[-1].items() if k != "segundos"}
    df = armaz.ler_df(CH.compras_rede("2026-09"))
    assert df["ean"].tolist() == ["123", "789"]                          # ordenado pelo EAN (chave, sem zeros)
    x = df[df["ean"] == "789"].iloc[0]
    assert x["quantidade"] == 6 and x["preco"] == pytest.approx(9.0)      # 2 displays de 3 a R$ 30 com 10%
    assert x["custo_cadastro"] == 9.0 and x["preco_venda"] == 15.0 and x["nome"] == "DISPLAY X"
    so_um = armaz.ler_df(CH.compras_rede("2026-09"), filtros=[("ean", "in", ["123"])])
    assert so_um["codigo_loja"].tolist() == ["2"]
    resumo = armaz.ler_df(CH.compras_rede_resumo("2026-09")).set_index("ean")
    assert resumo.loc["789", "valor"] == pytest.approx(54) and resumo.loc["789", "lojas"] == 1

    assert rede.atualizar(armaz, CH, dt.date(2026, 10, 7))[-1]["situacao"] == "em dia"
    armaz.salvar_json(CH.marcador_compras("E1"), {"meses": {"2026-09": "2026-10-08"}})  # mês baixado de novo
    assert rede.atualizar(armaz, CH, dt.date(2026, 10, 8))[-1]["situacao"] == "montado"


# ---------------------------------------------------------------------------
# Fases da rotina (08/10/2026): carga inicial só com meses fechados, depois
# manutenção (dia a dia de todas primeiro, meses antigos depois)
# ---------------------------------------------------------------------------

CARGA = ["2026-07", "2026-08"]   # meses fechados para o HOJE dos testes (27/09/2026)


def _fase(tmp_path, cliente, fase, status=None, tentativas=3):
    return coleta.Coleta(cliente, ArmazenamentoLocal(tmp_path), CH, HOJE, 3, 10_000, tentativas, relogio=Relogio(),
                         dormir=lambda s: None, fase=fase, meses_carga=CARGA, status_carga=status)


def test_carga_baixa_so_os_meses_fechados_e_marca_a_empresa(tmp_path):
    cli = ClienteFalso()
    rel = _fase(tmp_path, cli, "carga").executar(["E1"], "p1")
    meses = sorted({c[1].isoformat()[:7] if c[0] == "compras" else c[2].isoformat()[:7] for c in cli.chamadas})
    assert meses == CARGA and not [c for c in cli.chamadas if c[0] == "estoque"]
    assert rel.unidades_ok == 2 + 2 * 2 and rel.fase == "carga" and rel.empresas_carga_completa == 1
    assert ArmazenamentoLocal(tmp_path).ler_json(CH.carga("E1")) == {
        "meses": CARGA, "completa": True, "tentativas": 0, "atualizado": "2026-09-27"}
    # Carga feita: rodar de novo não pede nada à API (mês fechado não é refeito).
    cli2 = ClienteFalso()
    _fase(tmp_path, cli2, "carga").executar(["E1"], "p1")
    assert cli2.chamadas == []


def test_falha_na_carga_soma_tentativa_e_parar_pelo_tempo_nao(tmp_path):
    marcador = lambda: ArmazenamentoLocal(tmp_path).ler_json(CH.carga("E1"))  # noqa: E731
    _fase(tmp_path, ClienteFalso(falhar_vendas_vezes=99), "carga", tentativas=2).executar(["E1"], "p1")
    assert marcador()["completa"] is False and marcador()["tentativas"] == 1
    _fase(tmp_path, ClienteFalso(falhar_vendas_vezes=99), "carga", status={"E1": marcador()},
          tentativas=2).executar(["E1"], "p1")
    assert marcador()["tentativas"] == 2
    # Parar pelo tempo no meio da empresa: não completa, mas também não conta tentativa.
    relogio = Relogio()
    rotina = coleta.Coleta(ClienteFalso(relogio=relogio, custo_por_chamada=60), ArmazenamentoLocal(tmp_path / "t"),
                           CH, HOJE, 3, 90, 3, relogio=relogio, dormir=lambda s: None, fase="carga",
                           meses_carga=CARGA)
    rotina.executar(["E1"], "p1")
    m = ArmazenamentoLocal(tmp_path / "t").ler_json(CH.carga("E1"))
    assert m["completa"] is False and m["tentativas"] == 0


def test_manutencao_faz_o_dia_a_dia_de_todas_antes_dos_meses_antigos(tmp_path):
    cli = ClienteFalso()
    _fase(tmp_path, cli, "manutencao").executar(["E1", "E2"], "p1")
    tipos = [(c[0], (c[1] if c[0] == "compras" else c[2] if c[0] == "vendas" else None)) for c in cli.chamadas]
    # Primeiro, de E1 e E2: mês corrente (compras e vendas) e estoque.
    primeiras = tipos[:2 * (1 + 2 + 1)]
    assert all(t == "estoque" or d.month == 9 for t, d in primeiras)
    assert sum(1 for t, _ in primeiras if t == "estoque") == 2
    # Depois, os meses fechados que faltavam (complemento).
    assert all(t != "estoque" and d.month < 9 for t, d in tipos[len(primeiras):])


def test_fase_da_execucao_e_ordem_das_empresas():
    from pedido import __main__ as rotina

    completa = {"meses": rotina.MESES_CARGA, "completa": True, "tentativas": 0}
    falhou_1 = {"meses": rotina.MESES_CARGA, "completa": False, "tentativas": 1}
    desistiu = {"meses": rotina.MESES_CARGA, "completa": False, "tentativas": 3}
    status = {"a": completa, "b": falhou_1, "d": desistiu}
    fase, empresas = rotina._fase_e_empresas(["a", "b", "c", "d"], status, 0, 1)
    assert fase == "carga" and empresas == ["c", "b"]          # nunca tentada primeiro; a e d não seguram
    fase, empresas = rotina._fase_e_empresas(["a", "d"], status, 0, 1)
    assert fase == "manutencao" and empresas == ["a", "d"]
    # Carga de outros meses (variável mudou): começa de novo.
    assert rotina._fase_e_empresas(["a"], {"a": {**completa, "meses": ["2025-01"]}}, 0, 1)[0] == "carga"


def test_codigo_da_loja_com_espacos_nao_gera_arquivo_vazio(tmp_path):
    """08/10/2026: "1" + 49 espaços na lista de lojas e "1" nos dados — a
    separação por loja não casava e os arquivos saíam vazios."""
    class ComEspacos(ClienteFalso):
        def listar_lojas(self, emp):
            return [{"CodigoLoja": "1" + " " * 49, "NomeLoja": "L1"}]

    _rotina(tmp_path, ComEspacos()).executar(["E1"], "p1")
    armaz = ArmazenamentoLocal(tmp_path)
    assert armaz.ler_df(CH.estoque("E1", "1"))["qtd_estoque"].tolist() == [3.0]
    assert armaz.ler_json(CH.lojas_gps("E1"))[0]["CodigoLoja"] == "1"


# ---------------------------------------------------------------------------
# Vínculo pelo CEP (08/10/2026, pedido/vinculo.py)
# ---------------------------------------------------------------------------

def _rmc_cep(cnpj, nome, numero, cep, cidade="NATAL", uf="RN"):
    return {"cnpj": cnpj, "razao_social": nome, "nome_fantasia": nome, "numero": numero, "bairro": None,
            "cidade": cidade, "uf": uf, "cep": cep}


def _gps_cep(nome, numero, cep, uf="RN"):
    return {"id_empresa": "E", "codigo_loja": "1", "nome_loja": nome, "cnpj": None, "numero": numero, "uf": uf, "cep": cep}


def test_cep_de_rua_e_numero_iguais_ligam_mesmo_com_nome_diferente():
    from pedido import vinculo

    rmc = [_rmc_cep("1", "ISABELLE DE SANTANA BORGES", "54", "59152-250"), _rmc_cep("2", "OUTRA", "54", "59000-100")]
    s = vinculo.sugerir([_gps_cep("MEGA FARMA 5 PIRANGI", "54", "59152250")], rmc)[0]
    assert (s.situacao, s.metodo, s.cnpj_rmc) == (vinculo.AUTOMATICO, vinculo.POR_CEP, "1")


def test_cep_de_cidade_nao_decide_sozinho():
    from pedido import vinculo

    rmc = [_rmc_cep("1", "ISABELLE DE SANTANA BORGES", "54", "59173-000")]
    s = vinculo.sugerir([_gps_cep("MEGA FARMA 5 PIRANGI", "54", "59173000")], rmc)[0]
    assert s.situacao == vinculo.CONFIRMAR and s.metodo == vinculo.POR_ENDERECO      # como antes: número sem nome


def test_cep_desempata_numero_repetido_na_uf():
    from pedido import vinculo

    rmc = [_rmc_cep("1", "DROGARIA FERRARI", "100", "59600-000", cidade="MOSSORO"),
           _rmc_cep("2", "DROGARIA FERRARI CENTRO", "100", "59010-000", cidade="NATAL")]
    s = vinculo.sugerir([_gps_cep("DROGARIA FERRARI", "100", "59010-000")], rmc)[0]
    assert (s.situacao, s.cnpj_rmc) == (vinculo.AUTOMATICO, "2")


def test_mesmo_cep_de_rua_sem_numero_vira_sugestao():
    from pedido import vinculo

    rmc = [_rmc_cep("1", "FARMACIA X", "10", "59152-250")]
    s = vinculo.sugerir([_gps_cep("LOJA Y", None, "59152250")], rmc)[0]
    assert (s.situacao, s.metodo, s.cnpj_rmc) == (vinculo.CONFIRMAR, vinculo.POR_CEP, "1")


def test_sessao_com_prazo_total_corta_resposta_que_nao_acaba(monkeypatch):
    """09/10/2026: uma consulta ficou 8 h pendurada — o timeout do requests é
    por leitura, não do total."""
    import requests
    from pedido import __main__ as rotina

    class RespostaLenta:
        def __init__(self):
            self.fechada = False

        def iter_content(self, chunk_size):
            for _ in range(10):
                tempo[0] += 100          # cada pedaço "leva" 100 s
                yield b"x"

        def close(self):
            self.fechada = True

    tempo = [0.0]
    resposta = RespostaLenta()
    monkeypatch.setattr(rotina.time, "monotonic", lambda: tempo[0])
    monkeypatch.setattr(requests.Session, "request", lambda self, m, u, **kw: resposta)
    with pytest.raises(requests.Timeout, match="prazo total"):
        rotina._SessaoComPrazo(480).request("GET", "http://x")
    assert resposta.fechada
