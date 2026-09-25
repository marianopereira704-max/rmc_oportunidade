"""Linha da tabela Por Loja (views/oportunidade_loja.py → views/tabela.py):
o que cada célula mostra. O desenho em si é testado por captura
(tests/visual/rodar.py)."""
from views import oportunidade_loja as tela
from views import tabela as tb


def _loja(**campos) -> dict:
    base = {
        "loja_id": 7, "razao_social": "DROGARIA TESTE LTDA", "cnpj": "12345678000190",
        "uf": "MG", "cidade": "Ipatinga", "qtd_produtos": 12, "economia": 150.5,
        "consultor_interno": "ANA", "consultor_farma": "BRUNO", "atendente_comercial": "CARLA",
    }
    base.update(campos)
    base["localizacao"] = tela._localizacao(base)
    return base


def _textos(celula) -> list[str]:
    return [" ".join(p["t"] for p in linha) for linha in celula]


def test_responsaveis_em_ordem_fixa_um_por_linha_sem_prefixo():
    celulas = tela._linha(_loja())["celulas"]
    assert _textos(celulas["consultor_interno"]) == ["ANA", "BRUNO", "CARLA"]


def test_responsavel_ausente_vira_traco_e_mantem_a_posicao():
    celulas = tela._linha(_loja(consultor_interno=None, consultor_farma="  ", atendente_comercial="CARLA"))["celulas"]
    assert _textos(celulas["consultor_interno"]) == ["—", "—", "CARLA"]


def test_responsavel_mostra_nome_e_sobrenome_com_o_nome_inteiro_na_dica():
    celula = tela._linha(_loja(consultor_interno="AMANDA EMANUELI DE SOUZA LIMA"))["celulas"]["consultor_interno"]
    assert celula[0][0]["t"] == "AMANDA EMANUELI"
    assert celula[0][0]["dica"] == "AMANDA EMANUELI DE SOUZA LIMA"


def test_nome_curto_pula_conectivo_e_aceita_nome_unico():
    assert tela.nome_curto("ANA DE SOUZA LIMA") == "ANA SOUZA"
    assert tela.nome_curto("ana  dos   santos") == "ana santos"
    assert tela.nome_curto("RAFAEL") == "RAFAEL"


def test_nome_que_ja_e_curto_nao_repete_na_dica():
    celula = tela._linha(_loja(consultor_farma="RANGEL MATOS"))["celulas"]["consultor_interno"]
    assert celula[1][0] == {"t": "RANGEL MATOS", "c": "aux unica"}


def test_localizacao_cidade_barra_uf():
    assert tela._localizacao({"cidade": "Ipatinga", "uf": "MG"}) == "Ipatinga/MG"
    assert tela._localizacao({"cidade": None, "uf": "MG"}) == "MG"
    assert tela._localizacao({"cidade": None, "uf": None}) is None
    assert _textos(tela._linha(_loja(cidade=None, uf=None))["celulas"]["localizacao"]) == ["—"]


def test_loja_nome_em_destaque_e_cnpj_formatado_embaixo():
    celula = tela._linha(_loja())["celulas"]["razao_social"]
    assert celula[0][0] == {"t": "DROGARIA TESTE LTDA", "c": "forte"}
    assert celula[1][0]["t"] == "12.345.678/0001-90"


def test_economia_positiva_selo_verde_e_zero_ja_otimizada():
    assert tela._linha(_loja(economia=150.5))["celulas"]["economia"][0][0]["c"] == "selo-sucesso"
    zero = tela._linha(_loja(economia=0))["celulas"]["economia"][0][0]
    assert (zero["t"], zero["c"]) == ("Já otimizada", "selo-neutro")


def test_id_da_linha_e_texto_para_o_gatilho_do_componente():
    assert tela._linha(_loja(loja_id=42))["id"] == "42"


def test_texto_do_banco_vai_como_texto_nunca_como_html():
    # O JS usa textContent; aqui só garante que nada é escapado/alterado no
    # caminho (escapar duas vezes mostraria "&lt;" na tela).
    celula = tb.celula(tb.pedaco("<b>LOJA</b> & CIA"))
    assert celula[0][0]["t"] == "<b>LOJA</b> & CIA"
    assert "innerHTML = caminho" in tb._JS and "textContent" in tb._JS
