"""Cliente da API de consulta do GPS Farma — a integração que deve substituir
o upload manual da planilha GPS.

O QUE ESTE MÓDULO FAZ HOJE: fala com a API (autenticação, paginação, janela
de datas, erros, novas tentativas) e ENTREGA OS REGISTROS BRUTOS.

O QUE ELE DELIBERADAMENTE NÃO FAZ: transformar esses registros em
`RegistroCompraGPS`. O mapeamento campo a campo (qual chave do JSON é o EAN,
qual é o custo, qual é a quantidade, como sai o mês de referência) NÃO está
implementado porque nenhuma chamada autenticada foi feita até agora — a chave
de API disponível em 15/09/2026 era recusada com 401. Escrever esse
mapeamento sem ver uma resposta real seria adivinhar em cima de dado
financeiro, que é exatamente o erro que não se pode cometer aqui. O ponto
onde ele entra está marcado em `GpsApiComprasAdapter.sincronizar`.

=== O que foi confirmado (lido do Swagger em 15/09/2026) ===

- Base: http://143.244.153.213  (sem TLS — ver aviso de segurança abaixo)
- Autenticação: header `X-API-Key: <chave>`
- Rotas usadas aqui:
    GET /api/v1/empresas
    GET /api/v1/empresas/{idEmpresa}/compras
- Paginação: query `limit` (1..1000) e `offset` (>=0), em todas as rotas de
  listagem.
- Filtro de data em /compras: `dataInicio` e `dataFim`, formato YYYY-MM-DD.
- Outros filtros de /compras: codigoLoja, codigoFornecedor, codigoProduto,
  codigoCompra, busca, tipoCompra (0=compra, 1=transferência), quantidadeMin,
  quantidadeMax, apenasComFornecedor.
- Formato de erro (visto de verdade, num 401):
    {"detail": {"error": {"code": "UNAUTHORIZED", "message": "..."}}}

=== O que NÃO foi confirmado (e por isso falha alto em vez de chutar) ===

1. O ENVELOPE da resposta de listagem. O Swagger declara o corpo do 200 como
   um `"string"` genérico, o que não diz nada. Pode ser uma lista pura, pode
   ser um objeto com os itens dentro de alguma chave. `_extrair_itens` aceita
   as formas mais comuns e, se vier qualquer outra, levanta
   `GpsApiRespostaInesperada` dizendo o que realmente veio — nunca devolve
   lista vazia fingindo que deu certo.
2. O NOME DO CAMPO de id da empresa em /api/v1/empresas, necessário pra
   montar as rotas seguintes. Mesmo tratamento: `_extrair_id_empresa` tenta
   os nomes plausíveis e falha com a lista de chaves recebidas se nenhum
   servir.

Confirmar esses dois pontos é a PRIMEIRA coisa a fazer quando a chave
funcionar — os dois estão isolados em funções de uma linha de propósito.

=== Aviso de segurança ===

A API responde em HTTP puro, sem TLS, num endereço IP. A chave viaja em texto
claro em toda requisição, e qualquer um no caminho de rede consegue lê-la.
Isso não é problema deste código (ele manda no header que a API exige), mas é
um ponto a levar pro TI junto com a questão da chave.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import requests

from core.config import settings
from integrations.base import IntegrationAdapter, ResultadoSincronizacao, StatusIntegracao


class GpsApiError(RuntimeError):
    """Base de todos os erros desta integração."""


class GpsApiNaoAutorizado(GpsApiError):
    """401 — chave ausente, malformada ou inválida."""


class GpsApiValidacao(GpsApiError):
    """422 — a API recusou os parâmetros enviados."""


class GpsApiRespostaInesperada(GpsApiError):
    """A resposta veio num formato que este cliente não sabe interpretar.

    Erro de propósito: é melhor parar e mostrar o formato real do que seguir
    em frente com uma lista vazia e fazer parecer que a sincronização rodou
    sem trazer nada."""


class GpsApiIndisponivel(GpsApiError):
    """Falha de rede, timeout ou erro 5xx que persistiu após as tentativas."""


# Chaves em que os itens de uma listagem podem estar, se a resposta for um
# objeto em vez de uma lista pura. Ver "o que NÃO foi confirmado" no topo.
_CHAVES_ITENS = ("items", "data", "results", "registros", "itens")

# Nomes possíveis do id da empresa no registro de /api/v1/empresas.
_CHAVES_ID_EMPRESA = ("idEmpresa", "id_empresa", "id", "codigoEmpresa", "codigo")


def _extrair_itens(payload: Any, caminho: str) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for chave in _CHAVES_ITENS:
            valor = payload.get(chave)
            if isinstance(valor, list):
                return valor
        raise GpsApiRespostaInesperada(
            f"A resposta de {caminho} veio como objeto, mas nenhuma das chaves conhecidas "
            f"({', '.join(_CHAVES_ITENS)}) continha uma lista. Chaves recebidas: "
            f"{', '.join(sorted(payload.keys())) or '(nenhuma)'}."
        )
    raise GpsApiRespostaInesperada(
        f"A resposta de {caminho} não é lista nem objeto — veio como {type(payload).__name__}."
    )


def _extrair_id_empresa(registro: dict) -> str:
    for chave in _CHAVES_ID_EMPRESA:
        valor = registro.get(chave)
        if valor not in (None, ""):
            return str(valor)
    raise GpsApiRespostaInesperada(
        "Não consegui identificar o id da empresa no registro de /api/v1/empresas. "
        f"Procurei por {', '.join(_CHAVES_ID_EMPRESA)} e recebi as chaves: "
        f"{', '.join(sorted(registro.keys())) or '(nenhuma)'}."
    )


def janela_sobreposta(hoje: dt.date, dias: int) -> tuple[dt.date, dt.date]:
    """Intervalo que a sincronização automática repuxa a cada rodada.

    Sempre termina hoje e começa `dias` atrás — independente de quando foi a
    última sincronização. Ver a justificativa em `core/config.py::GpsApiConfig`:
    é o que garante que uma nota corrigida retroativamente no ERP volte pra
    cá, coisa que um corte "desde a última vez" nunca traria."""
    if dias < 0:
        raise ValueError("dias de sobreposição não pode ser negativo.")
    return (hoje - dt.timedelta(days=dias), hoje)


@dataclass
class PaginaRequisitada:
    """Registro do que foi pedido — usado só pra relatório/diagnóstico."""
    caminho: str
    offset: int
    recebidos: int


class ClienteGpsApi:
    """Chamadas HTTP à API do GPS. Sem nenhum conhecimento do domínio do RMC:
    devolve exatamente o que a API mandou, em dicts."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_segundos: int = 60,
        tentativas: int = 3,
        limite_pagina: int = 1000,
        max_paginas: int = 1000,
        sessao_http: Any | None = None,
        pausa: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_segundos = timeout_segundos
        self.tentativas = max(1, tentativas)
        self.limite_pagina = max(1, min(1000, limite_pagina))
        self.max_paginas = max(1, max_paginas)
        self._http = sessao_http if sessao_http is not None else requests.Session()
        self._pausa = pausa
        self.paginas_requisitadas: list[PaginaRequisitada] = []

    # -- infraestrutura -----------------------------------------------------

    def _mensagem_de_erro(self, resposta: Any) -> str:
        """Extrai a mensagem do corpo de erro no formato que a API usa de
        verdade — e cai pro texto cru se vier em qualquer outro formato."""
        try:
            corpo = resposta.json()
        except Exception:  # noqa: BLE001 — corpo não-JSON é possível em erro de proxy
            return (getattr(resposta, "text", "") or "").strip()[:500]
        detalhe = corpo.get("detail") if isinstance(corpo, dict) else None
        if isinstance(detalhe, dict):
            erro = detalhe.get("error")
            if isinstance(erro, dict) and erro.get("message"):
                return str(erro["message"])
        return str(detalhe or corpo)[:500]

    def _get(self, caminho: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{caminho}"
        ultimo_erro: Exception | None = None

        for tentativa in range(1, self.tentativas + 1):
            try:
                resposta = self._http.get(
                    url,
                    params=params or {},
                    headers={"X-API-Key": self.api_key, "accept": "application/json"},
                    timeout=self.timeout_segundos,
                )
            except Exception as exc:  # noqa: BLE001 — timeout/DNS/conexão
                ultimo_erro = exc
                if tentativa < self.tentativas:
                    self._pausa(2 ** (tentativa - 1))
                    continue
                raise GpsApiIndisponivel(
                    f"Não consegui falar com a API do GPS em {url} após {self.tentativas} tentativa(s): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            status = resposta.status_code
            if status == 401:
                # Não adianta repetir: a chave não vai ficar válida sozinha.
                raise GpsApiNaoAutorizado(
                    f"A API do GPS recusou a chave (401) em {caminho}: {self._mensagem_de_erro(resposta)}"
                )
            if status == 422:
                raise GpsApiValidacao(
                    f"A API do GPS recusou os parâmetros (422) em {caminho}: {self._mensagem_de_erro(resposta)}"
                )
            if status >= 500:
                ultimo_erro = GpsApiIndisponivel(
                    f"A API do GPS respondeu {status} em {caminho}: {self._mensagem_de_erro(resposta)}"
                )
                if tentativa < self.tentativas:
                    self._pausa(2 ** (tentativa - 1))
                    continue
                raise ultimo_erro
            if status >= 400:
                raise GpsApiError(
                    f"A API do GPS respondeu {status} em {caminho}: {self._mensagem_de_erro(resposta)}"
                )

            try:
                return resposta.json()
            except Exception as exc:  # noqa: BLE001
                raise GpsApiRespostaInesperada(
                    f"A resposta de {caminho} veio com status {status} mas não é JSON válido."
                ) from exc

        raise GpsApiIndisponivel(str(ultimo_erro))  # pragma: no cover — inalcançável

    def _paginar(self, caminho: str, params: dict | None = None) -> Iterator[dict]:
        """Percorre todas as páginas de uma rota de listagem.

        Para quando uma página vem com menos itens que o `limit` pedido — o
        sinal padrão de "acabou" em paginação por offset. O teto de páginas
        existe pro caso de a API ignorar o `offset`: sem ele, o laço pediria
        a mesma página pra sempre."""
        offset = 0
        for pagina in range(self.max_paginas):
            consulta = dict(params or {})
            consulta.update({"limit": self.limite_pagina, "offset": offset})
            payload = self._get(caminho, consulta)
            itens = _extrair_itens(payload, caminho)
            self.paginas_requisitadas.append(
                PaginaRequisitada(caminho=caminho, offset=offset, recebidos=len(itens))
            )

            for item in itens:
                yield item

            if len(itens) < self.limite_pagina:
                return
            offset += self.limite_pagina

        raise GpsApiRespostaInesperada(
            f"A paginação de {caminho} passou de {self.max_paginas} páginas sem terminar. "
            "Isso normalmente significa que a API está ignorando o parâmetro `offset` e "
            "devolvendo sempre a mesma página — interrompido de propósito pra não repetir "
            "chamada pra sempre."
        )

    # -- rotas --------------------------------------------------------------

    def listar_empresas(self) -> Iterator[dict]:
        yield from self._paginar("/api/v1/empresas")

    def detalhe_empresa(self, id_empresa: str) -> dict:
        """Detalhe de uma empresa, COM as lojas embutidas (lista `lojas`).

        Envelope diferente das listagens, confirmado na API real em
        16/09/2026: aqui `data` é um OBJETO, não uma lista — por isso esta
        função não passa por `_extrair_itens`. Uma chamada resolve empresa +
        lojas, em vez de duas."""
        payload = self._get(f"/api/v1/empresas/{id_empresa}")
        dado = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(dado, dict):
            raise GpsApiRespostaInesperada(
                f"Esperava um objeto em `data` no detalhe da empresa {id_empresa}, "
                f"veio {type(dado).__name__}."
            )
        return dado

    def listar_lojas(self, id_empresa: str) -> Iterator[dict]:
        yield from self._paginar(f"/api/v1/empresas/{id_empresa}/lojas")

    def listar_compras(
        self,
        id_empresa: str,
        data_inicio: dt.date | None = None,
        data_fim: dt.date | None = None,
        **filtros: Any,
    ) -> Iterator[dict]:
        params: dict[str, Any] = {k: v for k, v in filtros.items() if v is not None}
        if data_inicio is not None:
            params["dataInicio"] = data_inicio.isoformat()
        if data_fim is not None:
            params["dataFim"] = data_fim.isoformat()
        yield from self._paginar(f"/api/v1/empresas/{id_empresa}/compras", params)


@dataclass
class ColetaCompras:
    """Resultado de uma coleta — números pra relatório, sem nenhum dado
    persistido (persistir depende do mapeamento que ainda não existe)."""
    data_inicio: dt.date
    data_fim: dt.date
    empresas: int
    registros: int
    chamadas: int


def coletar_compras(
    cliente: ClienteGpsApi,
    hoje: dt.date | None = None,
    dias_sobreposicao: int | None = None,
    consumidor: Callable[[str, dict], None] | None = None,
) -> ColetaCompras:
    """Percorre TODAS as empresas e traz as compras da janela com
    sobreposição, entregando cada registro bruto ao `consumidor`.

    Iterar todas as empresas (em vez de um id fixo) é decisão confirmada com
    o cliente: a base do RMC tem mais de uma empresa no GPS.

    Nada é gravado aqui. Quando o mapeamento de campos existir, o `consumidor`
    é exatamente o lugar onde ele entra."""
    hoje = hoje or dt.date.today()
    dias = settings.gps_api.dias_sobreposicao if dias_sobreposicao is None else dias_sobreposicao
    data_inicio, data_fim = janela_sobreposta(hoje, dias)

    empresas = 0
    registros = 0
    for empresa in cliente.listar_empresas():
        id_empresa = _extrair_id_empresa(empresa)
        empresas += 1
        for compra in cliente.listar_compras(id_empresa, data_inicio=data_inicio, data_fim=data_fim):
            registros += 1
            if consumidor is not None:
                consumidor(id_empresa, compra)

    return ColetaCompras(
        data_inicio=data_inicio,
        data_fim=data_fim,
        empresas=empresas,
        registros=registros,
        chamadas=len(cliente.paginas_requisitadas),
    )


def cliente_das_configuracoes(**substituicoes: Any) -> ClienteGpsApi:
    """Monta o cliente a partir de `settings.gps_api`. Levanta se não estiver
    configurado — quem chama deve checar `status()` antes."""
    cfg = settings.gps_api
    if not cfg.configured:
        raise GpsApiError(
            "API do GPS não configurada — faltam GPS_API_BASE_URL e/ou GPS_API_KEY em secrets.toml."
        )
    parametros: dict[str, Any] = {
        "base_url": cfg.base_url,
        "api_key": cfg.api_key,
        "timeout_segundos": cfg.timeout_segundos,
        "tentativas": cfg.tentativas,
        "limite_pagina": cfg.limite_pagina,
        "max_paginas": cfg.max_paginas,
    }
    parametros.update(substituicoes)
    return ClienteGpsApi(**parametros)


class GpsApiComprasAdapter(IntegrationAdapter):
    """Adapter do GPS pela API, no lugar do upload de planilha.

    ATENÇÃO: `sincronizar` ainda NÃO grava nada. Ele faz a coleta completa —
    o que já prova ponta a ponta que autenticação, paginação e janela de datas
    funcionam — e devolve a contagem, com `registros_processados=0` e status
    MANUAL pra deixar explícito que a planilha continua sendo a fonte real
    enquanto o mapeamento de campos não existir.

    É de propósito que ele não escreva "quase certo" no banco: sem uma
    resposta real da API à vista, qualquer mapeamento seria chute em cima de
    valor financeiro."""

    nome = "Compras (API GPS Farma)"

    def status(self) -> StatusIntegracao:
        return (
            StatusIntegracao.DISPONIVEL
            if settings.gps_api.configured
            else StatusIntegracao.INDISPONIVEL
        )

    def sincronizar(self, **kwargs) -> ResultadoSincronizacao:
        if self.status() != StatusIntegracao.DISPONIVEL:
            return ResultadoSincronizacao(
                status=StatusIntegracao.INDISPONIVEL,
                registros_processados=0,
                mensagem="API do GPS ainda não configurada (GPS_API_BASE_URL / GPS_API_KEY).",
            )

        cliente = kwargs.get("cliente") or cliente_das_configuracoes()
        coleta = coletar_compras(
            cliente,
            hoje=kwargs.get("hoje"),
            dias_sobreposicao=kwargs.get("dias_sobreposicao"),
            # >>> É AQUI que o mapeamento campo a campo entra, quando existir:
            # trocar `consumidor=None` por uma função que converta o registro
            # bruto em RegistroCompraGPS e grave em bloco.
            consumidor=kwargs.get("consumidor"),
        )
        return ResultadoSincronizacao(
            status=StatusIntegracao.MANUAL,
            registros_processados=0,
            mensagem=(
                f"Coleta de {coleta.data_inicio:%d/%m/%Y} a {coleta.data_fim:%d/%m/%Y}: "
                f"{coleta.registros} registro(s) de compra em {coleta.empresas} empresa(s), "
                f"em {coleta.chamadas} chamada(s) à API. NADA foi gravado — o mapeamento dos "
                "campos da API para RegistroCompraGPS ainda não está implementado."
            ),
        )
