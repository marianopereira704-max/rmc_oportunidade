"""Testes do cliente da API do GPS Farma (integrations/gps_api.py).

Nenhuma chamada real de rede: um transporte falso responde no lugar do
`requests.Session`, o que deixa os testes determinísticos e permite exercitar
exatamente os casos difíceis (timeout, 5xx, 401, paginação estranha).

Duas famílias de teste importam mais que as outras:

- As de FORMATO DESCONHECIDO. Não temos uma resposta real da API (a chave
  disponível era recusada com 401), e o Swagger declara o corpo do 200 como
  `"string"`. O cliente aceita as formas comuns e, diante de qualquer outra,
  precisa FALHAR ALTO — nunca devolver lista vazia, que pareceria uma
  sincronização bem-sucedida que não trouxe nada.
- As de PAGINAÇÃO. Um laço por offset que não para é uma forma fácil de
  martelar a API de um terceiro para sempre.
"""
from __future__ import annotations

import datetime as dt

import pytest

from integrations import gps_api
from integrations.base import StatusIntegracao


class _RespostaFalsa:
    def __init__(self, status_code: int, corpo=None, texto: str = ""):
        self.status_code = status_code
        self._corpo = corpo
        self.text = texto

    def json(self):
        if self._corpo is None:
            raise ValueError("corpo não é JSON")
        return self._corpo


class _HttpFalso:
    """Substitui o `requests.Session`. `manipulador` recebe (url, params) e
    devolve uma `_RespostaFalsa`."""

    def __init__(self, manipulador):
        self._manipulador = manipulador
        self.chamadas: list[tuple[str, dict, dict]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.chamadas.append((url, dict(params or {}), dict(headers or {})))
        return self._manipulador(url, dict(params or {}))


def _cliente(manipulador, **kwargs) -> gps_api.ClienteGpsApi:
    parametros = {
        "base_url": "http://api-de-teste",
        "api_key": "chave-de-teste",
        "limite_pagina": 2,
        "tentativas": 3,
        "sessao_http": _HttpFalso(manipulador),
        "pausa": lambda _segundos: None,  # nunca dorme de verdade nos testes
    }
    parametros.update(kwargs)
    return gps_api.ClienteGpsApi(**parametros)


# ---------------------------------------------------------------------------
# Janela de datas
# ---------------------------------------------------------------------------

def test_janela_sobreposta_cobre_os_dias_pedidos():
    inicio, fim = gps_api.janela_sobreposta(dt.date(2026, 9, 15), dias=45)

    assert fim == dt.date(2026, 9, 15)
    assert inicio == dt.date(2026, 8, 1)
    assert (fim - inicio).days == 45


def test_janela_sobreposta_recusa_dias_negativos():
    with pytest.raises(ValueError):
        gps_api.janela_sobreposta(dt.date(2026, 9, 15), dias=-1)


def test_compras_manda_as_datas_no_formato_iso():
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, []))

    list(cliente.listar_compras("42", data_inicio=dt.date(2026, 8, 1), data_fim=dt.date(2026, 9, 15)))

    _url, params, _headers = cliente._http.chamadas[0]
    assert params["dataInicio"] == "2026-08-01"
    assert params["dataFim"] == "2026-09-15"


def test_chave_vai_no_header_x_api_key():
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, []))

    list(cliente.listar_empresas())

    _url, _params, headers = cliente._http.chamadas[0]
    assert headers["X-API-Key"] == "chave-de-teste"


# ---------------------------------------------------------------------------
# Paginação
# ---------------------------------------------------------------------------

def test_paginacao_percorre_todas_as_paginas_ate_a_ultima_incompleta():
    paginas = {0: [{"i": 1}, {"i": 2}], 2: [{"i": 3}, {"i": 4}], 4: [{"i": 5}]}
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, paginas[params["offset"]]))

    itens = list(cliente.listar_empresas())

    assert [i["i"] for i in itens] == [1, 2, 3, 4, 5]
    assert [c[1]["offset"] for c in cliente._http.chamadas] == [0, 2, 4]


def test_paginacao_para_quando_a_primeira_pagina_ja_vem_incompleta():
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, [{"i": 1}]))

    itens = list(cliente.listar_empresas())

    assert len(itens) == 1
    assert len(cliente._http.chamadas) == 1, "não pode pedir uma segunda página depois de uma incompleta"


def test_paginacao_falha_alto_se_a_api_ignora_o_offset():
    """Página sempre cheia e sempre igual = laço infinito. O teto tem que
    interromper com erro explicativo em vez de martelar a API pra sempre."""
    cliente = _cliente(
        lambda url, params: _RespostaFalsa(200, [{"i": 1}, {"i": 2}]),
        max_paginas=5,
    )

    with pytest.raises(gps_api.GpsApiRespostaInesperada, match="offset"):
        list(cliente.listar_empresas())

    assert len(cliente._http.chamadas) == 5


# ---------------------------------------------------------------------------
# Envelope desconhecido — nunca silenciar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chave", ["items", "data", "results", "registros", "itens"])
def test_envelope_em_objeto_e_aceito_nas_chaves_conhecidas(chave):
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, {chave: [{"i": 1}]}))

    assert len(list(cliente.listar_empresas())) == 1


def test_envelope_desconhecido_falha_alto_em_vez_de_devolver_vazio():
    """O erro mais perigoso possível aqui seria devolver [] — a sincronização
    diria 'sucesso, 0 registros' e ninguém desconfiaria de nada."""
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, {"payload": {"linhas": []}, "total": 0}))

    with pytest.raises(gps_api.GpsApiRespostaInesperada) as excecao:
        list(cliente.listar_empresas())

    # A mensagem precisa dizer o que veio de verdade, senão não ajuda a corrigir.
    assert "payload" in str(excecao.value) and "total" in str(excecao.value)


def test_resposta_que_nao_e_json_falha_alto():
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, None, texto="<html>erro</html>"))

    with pytest.raises(gps_api.GpsApiRespostaInesperada):
        list(cliente.listar_empresas())


def test_id_de_empresa_desconhecido_falha_alto_dizendo_as_chaves_recebidas():
    with pytest.raises(gps_api.GpsApiRespostaInesperada) as excecao:
        gps_api._extrair_id_empresa({"nomeFantasia": "Farmácia X", "cnpj": "..."})

    assert "nomeFantasia" in str(excecao.value)


@pytest.mark.parametrize("chave", ["idEmpresa", "id_empresa", "id", "codigoEmpresa", "codigo"])
def test_id_de_empresa_aceito_nos_nomes_plausiveis(chave):
    assert gps_api._extrair_id_empresa({chave: 7}) == "7"


# ---------------------------------------------------------------------------
# Erros HTTP
# ---------------------------------------------------------------------------

def test_401_vira_excecao_propria_com_a_mensagem_da_api():
    """Formato de erro REAL, observado na API em 15/09/2026."""
    corpo = {
        "detail": {
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Chave de API ausente ou inválida. Informe via header 'X-API-Key'.",
            }
        }
    }
    cliente = _cliente(lambda url, params: _RespostaFalsa(401, corpo))

    with pytest.raises(gps_api.GpsApiNaoAutorizado, match="Chave de API ausente ou inválida"):
        list(cliente.listar_empresas())


def test_401_nao_e_repetido():
    """Repetir não faz a chave ficar válida — só gasta tempo e bate na API à toa."""
    cliente = _cliente(lambda url, params: _RespostaFalsa(401, {"detail": "nao autorizado"}), tentativas=3)

    with pytest.raises(gps_api.GpsApiNaoAutorizado):
        list(cliente.listar_empresas())

    assert len(cliente._http.chamadas) == 1


def test_422_vira_excecao_de_validacao():
    cliente = _cliente(lambda url, params: _RespostaFalsa(422, {"detail": [{"msg": "dataInicio inválida"}]}))

    with pytest.raises(gps_api.GpsApiValidacao):
        list(cliente.listar_compras("1", data_inicio=dt.date(2026, 1, 1)))


def test_erro_500_e_repetido_e_depois_falha_como_indisponivel():
    cliente = _cliente(lambda url, params: _RespostaFalsa(500, {"detail": "boom"}), tentativas=3)

    with pytest.raises(gps_api.GpsApiIndisponivel):
        list(cliente.listar_empresas())

    assert len(cliente._http.chamadas) == 3, "5xx é transitório: tem que tentar de novo"


def test_erro_500_que_passa_na_segunda_tentativa_nao_falha():
    estado = {"chamadas": 0}

    def _manipulador(url, params):
        estado["chamadas"] += 1
        if estado["chamadas"] == 1:
            return _RespostaFalsa(503, {"detail": "instável"})
        return _RespostaFalsa(200, [{"i": 1}])

    cliente = _cliente(_manipulador)

    assert len(list(cliente.listar_empresas())) == 1


def test_timeout_de_rede_e_repetido_e_depois_vira_indisponivel():
    def _manipulador(url, params):
        raise TimeoutError("demorou demais")

    cliente = _cliente(_manipulador, tentativas=2)

    with pytest.raises(gps_api.GpsApiIndisponivel, match="2 tentativa"):
        list(cliente.listar_empresas())


# ---------------------------------------------------------------------------
# Coleta completa
# ---------------------------------------------------------------------------

def _api_com_duas_empresas(url, params):
    if url.endswith("/api/v1/empresas"):
        return _RespostaFalsa(200, [{"id": "1"}, {"id": "2"}] if params["offset"] == 0 else [])
    if "/1/compras" in url:
        return _RespostaFalsa(200, [{"nota": "A"}] if params["offset"] == 0 else [])
    if "/2/compras" in url:
        return _RespostaFalsa(200, [{"nota": "B"}, {"nota": "C"}] if params["offset"] == 0 else [])
    raise AssertionError(f"rota inesperada: {url}")


def test_detalhe_empresa_aceita_data_como_objeto():
    """Envelope diferente das listagens, confirmado na API real: em rota de
    detalhe, `data` é um objeto, não uma lista."""
    corpo = {"data": {"idEmpresa": "1", "NomeFantasia": "MEGA FARMA", "lojas": [{"CodigoLoja": "1"}]}}
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, corpo))

    detalhe = cliente.detalhe_empresa("1")

    assert detalhe["NomeFantasia"] == "MEGA FARMA"
    assert len(detalhe["lojas"]) == 1


def test_detalhe_empresa_falha_alto_se_data_vier_como_lista():
    cliente = _cliente(lambda url, params: _RespostaFalsa(200, {"data": [{"idEmpresa": "1"}]}))

    with pytest.raises(gps_api.GpsApiRespostaInesperada, match="objeto"):
        cliente.detalhe_empresa("1")


def test_coletar_compras_itera_todas_as_empresas():
    """Decisão confirmada com o cliente: o RMC tem mais de uma empresa no GPS,
    então a coleta percorre a lista inteira, não um id fixo."""
    cliente = _cliente(_api_com_duas_empresas, limite_pagina=2)
    recebidos: list[tuple[str, dict]] = []

    coleta = gps_api.coletar_compras(
        cliente, hoje=dt.date(2026, 9, 15), dias_sobreposicao=45,
        consumidor=lambda id_empresa, registro: recebidos.append((id_empresa, registro)),
    )

    assert coleta.empresas == 2
    assert coleta.registros == 3
    assert [r[0] for r in recebidos] == ["1", "2", "2"]
    assert coleta.data_inicio == dt.date(2026, 8, 1) and coleta.data_fim == dt.date(2026, 9, 15)


def test_coletar_compras_usa_a_janela_em_todas_as_empresas():
    cliente = _cliente(_api_com_duas_empresas, limite_pagina=2)

    gps_api.coletar_compras(cliente, hoje=dt.date(2026, 9, 15), dias_sobreposicao=10)

    chamadas_compras = [c for c in cliente._http.chamadas if "compras" in c[0]]
    assert chamadas_compras, "nenhuma chamada de compras foi feita"
    for _url, params, _headers in chamadas_compras:
        assert params["dataInicio"] == "2026-09-05"
        assert params["dataFim"] == "2026-09-15"


# ---------------------------------------------------------------------------
# Adapter — o limite explícito do que ainda não existe
# ---------------------------------------------------------------------------

def test_adapter_reporta_indisponivel_sem_configuracao(monkeypatch):
    monkeypatch.setattr("integrations.gps_api.settings.gps_api.base_url", None)
    monkeypatch.setattr("integrations.gps_api.settings.gps_api.api_key", None)

    adaptador = gps_api.GpsApiComprasAdapter()

    assert adaptador.status() == StatusIntegracao.INDISPONIVEL
    resultado = adaptador.sincronizar()
    assert resultado.registros_processados == 0
    assert "não configurada" in resultado.mensagem


def test_adapter_coleta_mas_nao_grava_e_avisa_que_falta_o_mapeamento(monkeypatch):
    """Trava de contrato: enquanto o mapeamento de campos não existir, a
    sincronização NÃO pode dizer que processou registro nenhum. Se um dia
    alguém implementar a gravação, este teste falha e obriga a atualizar o
    contrato de propósito."""
    monkeypatch.setattr("integrations.gps_api.settings.gps_api.base_url", "http://api-de-teste")
    monkeypatch.setattr("integrations.gps_api.settings.gps_api.api_key", "chave-de-teste")
    cliente = _cliente(_api_com_duas_empresas, limite_pagina=2)

    resultado = gps_api.GpsApiComprasAdapter().sincronizar(cliente=cliente, hoje=dt.date(2026, 9, 15))

    assert resultado.status == StatusIntegracao.MANUAL
    assert resultado.registros_processados == 0
    assert "NADA foi gravado" in resultado.mensagem
    assert "3 registro(s)" in resultado.mensagem


def test_cliente_das_configuracoes_recusa_sem_chave(monkeypatch):
    monkeypatch.setattr("integrations.gps_api.settings.gps_api.base_url", "http://api-de-teste")
    monkeypatch.setattr("integrations.gps_api.settings.gps_api.api_key", None)

    with pytest.raises(gps_api.GpsApiError, match="não configurada"):
        gps_api.cliente_das_configuracoes()
