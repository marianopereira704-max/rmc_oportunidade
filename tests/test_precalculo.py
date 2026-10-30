"""Cálculo de madrugada (pedido/precalculo.py, pedido/selo.py, 29/10/2026). As contas das telas (pedido/telas_loja.py)
ficam trocadas por objetos pequenos: aqui se testa a assinatura, o selo e a rotina; que o pronto é IDÊNTICO ao
cálculo na hora foi conferido com a loja fictícia (tarefa 7 do plano) e o teste visual passa pelos dois caminhos."""
import dataclasses

import pytest

from pedido import precalculo, selo, telas_loja
from pedido.armazenamento import ArmazenamentoLocal
from pedido.configuracao import ConfigPedido
from pedido.plano import Chaves

CH = Chaves("pedido")
CHAVE = "chave-de-teste"
LIMITE = 0.10


# ---------------------------------------------------------------------------
# Selo
# ---------------------------------------------------------------------------

def test_selo_volta_igual_e_recusa_alterado_outra_chave_e_sem_chave():
    selado = selo.selar(b"conteudo", CHAVE)
    assert selo.abrir(selado, CHAVE) == b"conteudo"
    assert selo.abrir(selado[:-1] + b"X", CHAVE) is None            # conteúdo alterado
    assert selo.abrir(b"Y" + selado[1:], CHAVE) is None             # selo alterado
    assert selo.abrir(selado, "outra") is None and selo.abrir(selado, None) is None
    with pytest.raises(ValueError):
        selo.selar(b"x", "")


# ---------------------------------------------------------------------------
# Assinatura
# ---------------------------------------------------------------------------

def _base():
    config = ConfigPedido.padrao()
    return dict(foto="2026-10-29|2026-10-29T03:10:00", versoes=((10, "a"), (5, "b")),
                catalogo=((1, "x"), (2, "y"), (10, "a")), config=config, params=config.parametros(LIMITE),
                correcoes=(("L1", 3.0),))


def _todas(b):
    return {
        "assistente": precalculo.assinatura_assistente(b["foto"], b["versoes"], b["config"].meses_fechados,
                                                       b["params"], b["correcoes"]),
        "estoque": precalculo.assinatura_estoque(b["foto"], b["versoes"][0], b["catalogo"], b["config"], LIMITE),
        "ruptura": precalculo.assinatura_ruptura(b["foto"], b["versoes"], b["catalogo"], b["config"], b["correcoes"],
                                                 LIMITE),
        "compras": precalculo.assinatura_compras(b["foto"], b["versoes"], b["catalogo"], b["config"], LIMITE),
    }


def test_assinatura_igual_para_a_mesma_entrada_e_tupla_igual_a_lista():
    b = _base()
    assert _todas(b) == _todas(_base())
    # O app tem tuplas; a rotina lê o manifesto (JSON): listas.
    lista = {**b, "versoes": [list(v) for v in b["versoes"]], "catalogo": [list(v) for v in b["catalogo"]],
             "correcoes": [list(c) for c in b["correcoes"]]}
    assert _todas(lista) == _todas(b)


@pytest.mark.parametrize("mudanca, telas", [
    ({"foto": "2026-10-30|2026-10-30T03:10:00"}, {"assistente", "estoque", "ruptura", "compras"}),
    ({"versoes": ((11, "a"), (5, "b"))}, {"assistente", "estoque", "ruptura", "compras"}),
    ({"versoes": ((10, "a"), (6, "b"))}, {"assistente", "ruptura", "compras"}),      # genéricos: o Estoque não usa
    ({"catalogo": ((1, "x"), (3, "y"), (10, "a"))}, {"estoque", "ruptura", "compras"}),
    ({"correcoes": (("L1", 4.0),)}, {"assistente", "ruptura"}),                      # correção: Compras e Estoque não
])
def test_assinatura_muda_so_onde_deve(mudanca, telas):
    antes, depois = _todas(_base()), _todas({**_base(), **mudanca})
    assert {t for t in antes if antes[t] != depois[t]} == telas


def test_assinatura_muda_com_a_configuracao_da_loja_e_com_a_versao_do_codigo(monkeypatch):
    b = _base()
    outra = dataclasses.replace(b["config"], dias_medicamento=b["config"].dias_medicamento + 1)
    mudou = _todas({**b, "config": outra, "params": outra.parametros(LIMITE)})
    assert all(mudou[t] != v for t, v in _todas(b).items())
    antes = _todas(b)
    monkeypatch.setattr(precalculo, "VERSAO", precalculo.VERSAO + 1)
    assert all(_todas(b)[t] != v for t, v in antes.items())
    # Outra versão do pandas (29/10/2026: o GitHub instalou a 3.1 e os resultados diferiam do app).
    antes = _todas(b)
    monkeypatch.setattr(precalculo, "BIBLIOTECAS", "pandas 3.1.0; numpy 2.5.4; pyarrow 25.0.1")
    assert all(_todas(b)[t] != v for t, v in antes.items())


# ---------------------------------------------------------------------------
# Gravar e ler
# ---------------------------------------------------------------------------

def test_ler_so_com_assinatura_igual_e_selo_certo(tmp_path):
    armaz = ArmazenamentoLocal(tmp_path)
    precalculo.gravar(armaz, CH, "banco", "E1", "1", "estoque", "assin", {"x": [1, 2]}, CHAVE, 0.1, "agora")
    assert precalculo.ler(armaz, CH, "banco", "E1", "1", "estoque", "assin", CHAVE) == {"x": [1, 2]}
    assert precalculo.ler(armaz, CH, "banco", "E1", "1", "estoque", "outra", CHAVE) is None
    assert precalculo.ler(armaz, CH, "banco", "E1", "1", "estoque", "assin", "outra-chave") is None
    assert precalculo.ler(armaz, CH, "banco", "E1", "1", "estoque", "assin", None) is None
    assert precalculo.ler(armaz, CH, "banco", "E1", "2", "estoque", "assin", CHAVE) is None    # não existe
    k = "pedido/resultados/banco/E1/1/estoque.bin"
    armaz.salvar(k, armaz.ler(k)[:-3] + b"xyz")                                                # adulterado
    assert precalculo.ler(armaz, CH, "banco", "E1", "1", "estoque", "assin", CHAVE) is None


# ---------------------------------------------------------------------------
# A rotina
# ---------------------------------------------------------------------------

@pytest.fixture
def rotina(tmp_path, monkeypatch):
    """Banco "teste" publicado com 3 lojas, 2 com foto de estoque; as contas trocadas por objetos pequenos."""
    import pandas as pd

    armaz = ArmazenamentoLocal(tmp_path)
    for nome in ("base", "gen", "uni"):
        armaz.salvar_df(f"pedido/cache_app/teste/{nome}.parquet", pd.DataFrame({"ean": ["1"], "categoria": ["X"]}))
    armaz.salvar_json("pedido/insumos/teste/manifesto.json", {
        "config_padrao": dataclasses.asdict(ConfigPedido.padrao()), "personalizacoes": {"2": {"dias_medicamento": 9}},
        "correcoes": {"1": [["L1", 2.0]]}, "versoes": [[1, "a"], [1, "b"]], "versao_catalogo": [[1, "x"], [1, "y"], [1, "a"]],
        "lojas": [{"id": i, "legacy_id": 60 + i, "empresa_gps": f"E{i}", "loja_gps": "1"} for i in (1, 2, 3)],
        "tabelas": {"base_categorias": "pedido/cache_app/teste/base.parquet", "genericos": "pedido/cache_app/teste/gen.parquet",
                    "classificacao_universo": "pedido/cache_app/teste/uni.parquet"},
    })
    for e in ("E1", "E2"):
        armaz.salvar_json(CH.marcador_estoque(e), {"data": "2026-10-29", "gerado_em": "03:00"})
    monkeypatch.setattr("pedido.classificacao.classificar", lambda u, s, c: u)
    monkeypatch.setattr("pedido.classificacao.tabela_por_ean", lambda t, c: t)
    chamadas = []

    def conta(nome):
        def f(armaz_, chaves_, empresa, *a, **k):
            chamadas.append((nome, empresa))
            if empresa == "E2" and nome == "estoque" and getattr(f, "quebrar", False):
                raise RuntimeError("falhou")
            if nome == "compras" and callable(a[3]):       # como a de verdade: pede o cálculo SEM correções
                a[3]()
            return {"tela": nome, "empresa": empresa}
        return f
    for nome in precalculo.TELAS:
        monkeypatch.setattr(telas_loja, nome, conta(nome))
    return armaz, chamadas


def _rodar(armaz, parte=0, partes=1, filtro=None):
    return precalculo.rodar(armaz, CH, parte, partes, CHAVE, LIMITE, "2026-10-29", "agora", filtro)


def test_rotina_calcula_pula_em_dia_e_ignora_loja_sem_foto(rotina):
    armaz, chamadas = rotina
    rel = _rodar(armaz)[0]
    assert (rel["lojas_na_parte"], rel["calculadas"], rel["sem_dado"], rel["em_dia"]) == (3, 8, 4, 0)
    assert armaz.ler_json("pedido/controle/precalculo/2026-10-29/teste/parte1de1.json")["calculadas"] == 8
    chamadas.clear()
    rel = _rodar(armaz)[0]
    assert (rel["calculadas"], rel["em_dia"]) == (0, 8) and chamadas == []
    # Foto nova de uma empresa: só ela recalcula.
    armaz.salvar_json(CH.marcador_estoque("E1"), {"data": "2026-10-30", "gerado_em": "03:00"})
    assert _rodar(armaz)[0]["calculadas"] == 4


def test_correcao_usa_o_calculo_com_e_sem_correcao(rotina):
    armaz, chamadas = rotina
    _rodar(armaz, filtro={"61"})                                    # loja 1: tem correção
    assert [c for c in chamadas if c == ("assistente", "E1")] == [("assistente", "E1")] * 2   # com e sem


def test_partes_cobrem_todas_as_lojas_sem_repetir(rotina):
    armaz, _ = rotina
    lojas = [_rodar(armaz, p, 2)[0]["lojas_na_parte"] for p in (0, 1)]
    assert sum(lojas) == 3


def test_falha_numa_loja_nao_para_as_outras(rotina, monkeypatch):
    armaz, _ = rotina
    telas_loja.estoque.quebrar = True
    rel = _rodar(armaz)[0]
    assert rel["lojas_com_falha"] == 1 and rel["falhas"][0]["loja_id"] == "2" and rel["calculadas"] >= 4


def test_limpeza_so_apaga_lojas_que_sairam_e_so_na_pasta(rotina):
    armaz, _ = rotina
    _rodar(armaz)
    armaz.salvar("pedido/resultados/teste/E9/1/estoque.bin", b"velho")
    armaz.salvar_json("pedido/resultados/teste/E9/1/estoque.json", {"assinatura": "x"})
    armaz.salvar("pedido/resultados/outro/E9/1/estoque.bin", b"de outro banco")
    armaz.salvar("pedido/bruto/E9/1/estoque.parquet", b"dado bruto")
    assert _rodar(armaz)[0]["apagados"] == 2
    assert not armaz.existe("pedido/resultados/teste/E9/1/estoque.bin")
    assert armaz.existe("pedido/resultados/outro/E9/1/estoque.bin") and armaz.existe("pedido/bruto/E9/1/estoque.parquet")
    assert armaz.existe("pedido/resultados/teste/E1/1/estoque.bin")


def test_sem_chave_a_rotina_nao_grava(monkeypatch, tmp_path):
    from core.config import settings

    monkeypatch.setattr(settings.pedido, "precalculo_chave", None)
    assert precalculo.main(["--destino", f"local:{tmp_path}"]) == 1
    assert not list(tmp_path.rglob("*.bin"))


def test_relatorio_conta_lojas_prontas_e_resumo_soma_as_partes(rotina):
    armaz, _ = rotina
    for p in (0, 1):
        _rodar(armaz, p, 2)
    dia, por_banco = precalculo.ultima_execucao(armaz, CH)
    r = precalculo.resumo(por_banco["teste"])
    assert dia == "2026-10-29" and r["partes"] == ["parte1de2", "parte2de2"]
    assert (r["lojas"], r["prontas"], r["com_falha"], r["inicio"]) == (3, 2, 0, "agora")   # a loja 3 não tem foto
    assert precalculo.ultima_execucao(ArmazenamentoLocal(armaz.raiz / "vazio"), CH) == (None, {})


def test_linha_de_dados_rotinas():
    from views.dados.rotinas import _texto_precalculo

    r = {"lojas": 1200, "prontas": 1180, "com_falha": 3, "inicio": "2026-10-29T03:12:40-03:00"}
    assert _texto_precalculo("2026-10-29", r, True) == ("Cálculo de madrugada: 1.180 de 1.200 lojas prontas, 3 com "
                                                         "falha · última execução 29/10 às 03:12.")
    assert "ainda não rodou" in _texto_precalculo(None, None, True)
    assert "PRECALCULO_CHAVE" in _texto_precalculo("2026-10-29", {**r, "com_falha": 0}, False)
