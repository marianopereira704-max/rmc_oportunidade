"""Integração com a base de lojas do sistema interno — a única com API real
disponível hoje.

Formato real confirmado em produção (bem diferente do que a doc original
sugeria — nada de campos flat tipo "uf"/"razao_social" na raiz):
  GET {base_url}  (sem concatenar path — "full-stores" já É o recurso)
  Header: X-API-KEY: <token>  (não Authorization: Bearer)
  -> [{"cnpj": "...", "businessName": "...", "active": true|false,
       "address": {"state": "...", "city": "...", ...},
       "team": [{"name": "...", "sector": "..."}, ...],
       "economic_group": [123, ...], "acronym_economic_group": "..." | null,
       ...}, ...]

Regras de negócio confirmadas (decisão de 2026-08-18, com base num panorama
de 2.188 registros reais):
- Só sincroniza lojas com active=true (as demais ~63% ficam de fora).
- razao_social vem de businessName (nome legal, não fantasyName).
- uf/cidade vêm de address.state/address.city (só objeto address, não achou
  nenhum registro sem os dois preenchidos nos 2.188 checados).
- team[] não tem campo direto pra atendente/consultor — precisa mapear por
  "sector": "Consultoria Interna" -> consultor_interno, "Consultoria Farma"
  -> consultor_farma (era chamado "consultor_externo" antes desta decisão),
  "Negócios" -> atendente_comercial (cobertura baixa, só ~5 ocorrências em
  2.188 — esperado ficar vazio na maioria das lojas). Outros setores (PBM,
  Supervisão, Assistente de Pedidos, Expansão, Qualidade, Produtos
  Exclusivos) são ignorados — não têm campo correspondente no model.
- grupo_economico continua sem mapear (None) — decisão pendente separada:
  só 7 de 2.188 registros têm acronym_economic_group preenchido, e nesses 7
  a relação com economic_group (lista de IDs) nem sempre bate 1:1, então não
  dá pra inferir sozinho qual vira o nome do grupo.
"""
from __future__ import annotations

import datetime as dt

import requests

from core.config import settings
from core.db import get_session
from core.models import Loja
from core.sql import insert_com_atualizacao
from integrations.base import IntegrationAdapter, ResultadoSincronizacao, StatusIntegracao

# setor do time (item["team"][i]["sector"]) -> campo de Loja que ele preenche.
# Setores fora dessa lista (PBM, Supervisão, Assistente de Pedidos, Expansão,
# Qualidade, Produtos Exclusivos) não têm campo correspondente e são ignorados.
_SETOR_PARA_CAMPO = {
    "Consultoria Interna": "consultor_interno",
    "Consultoria Farma": "consultor_farma",
    "Negócios": "atendente_comercial",
}


# Linhas por instrução. O teto de parâmetros do SQLite (o mais baixo dos dois
# bancos que o projeto roda) é o que manda aqui: 500 linhas x 9 colunas deixa
# ~4.500 parâmetros, folgado mesmo nas versões antigas.
_TAMANHO_BLOCO_LOJAS = 500

# Colunas sobrescritas quando o CNPJ já existe. `grupo_economico` fica de fora
# DE PROPÓSITO — ver a docstring do módulo e a de `insert_com_atualizacao`.
_COLUNAS_ATUALIZADAS = [
    "razao_social", "uf", "cidade",
    "atendente_comercial", "consultor_farma", "consultor_interno",
    "fonte", "atualizado_em",
]


def _campos_do_time(item: dict) -> tuple[dict[str, str | None], bool]:
    """Mapeia `team[]` -> campos de Loja. Primeira ocorrência de cada setor
    relevante vence; devolve também se houve repetição de setor, pra relatar.
    """
    campos: dict[str, str | None] = {}
    duplicou = False
    for pessoa in item.get("team") or []:
        campo = _SETOR_PARA_CAMPO.get(pessoa.get("sector"))
        if campo is None:
            continue
        if campo in campos:
            duplicou = True
            continue
        campos[campo] = pessoa.get("name")
    return campos, duplicou


def _gravar_lojas_em_bloco(session, linhas: list[dict]) -> None:
    """Uma instrução por bloco, em vez de uma consulta por loja.

    O que havia aqui antes era um SELECT por loja recebida da API ("existe?")
    seguido de insert ou update — com ~800 lojas ativas, ~800 idas ao banco,
    e foi isso que fez a sincronização levar minutos. Agora o próprio banco
    decide inserir ou atualizar, via ON CONFLICT (cnpj) DO UPDATE."""
    for inicio in range(0, len(linhas), _TAMANHO_BLOCO_LOJAS):
        bloco = linhas[inicio:inicio + _TAMANHO_BLOCO_LOJAS]
        if not bloco:
            continue
        stmt = insert_com_atualizacao(session, Loja.__table__, ["cnpj"], _COLUNAS_ATUALIZADAS)
        session.execute(stmt, bloco)


class SistemaInternoLojasAdapter(IntegrationAdapter):
    nome = "Base de Lojas (sistema interno)"

    def status(self) -> StatusIntegracao:
        return (
            StatusIntegracao.DISPONIVEL
            if settings.sistema_interno.configured
            else StatusIntegracao.INDISPONIVEL
        )

    def _buscar_lojas_api(self) -> list[dict]:
        resp = requests.get(
            settings.sistema_interno.base_url,
            headers={"X-API-KEY": settings.sistema_interno.token},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def sincronizar(self, **kwargs) -> ResultadoSincronizacao:
        if self.status() != StatusIntegracao.DISPONIVEL:
            return ResultadoSincronizacao(
                status=StatusIntegracao.INDISPONIVEL,
                registros_processados=0,
                mensagem=(
                    "API do sistema interno ainda não configurada "
                    "(SISTEMA_INTERNO_BASE_URL / SISTEMA_INTERNO_TOKEN)."
                ),
            )

        registros = self._buscar_lojas_api()

        ativos = [item for item in registros if item.get("active") is True]
        lojas_com_setor_duplicado = 0

        # Deduplicado por CNPJ, mantendo a ÚLTIMA ocorrência — mesmo resultado
        # final do laço antigo (cada repetição sobrescrevia a anterior), e
        # necessário por outro motivo: um lote de ON CONFLICT DO UPDATE não
        # pode conter a mesma chave duas vezes (ver core/sql.py).
        por_cnpj: dict[str, dict] = {}
        for item in ativos:
            campos_time, duplicou = _campos_do_time(item)
            if duplicou:
                lojas_com_setor_duplicado += 1

            endereco = item.get("address") or {}
            por_cnpj[item["cnpj"]] = {
                "cnpj": item["cnpj"],
                "razao_social": item["businessName"],
                "uf": endereco["state"],
                "cidade": endereco["city"],
                "atendente_comercial": campos_time.get("atendente_comercial"),
                "consultor_farma": campos_time.get("consultor_farma"),
                "consultor_interno": campos_time.get("consultor_interno"),
                # grupo_economico NÃO entra aqui nem na lista de colunas
                # atualizadas: é pendente de decisão separada (ver docstring
                # do módulo) e sobrescrevê-lo apagaria dado que não é desta
                # origem.
                "fonte": "sistema_interno",
                "atualizado_em": dt.datetime.utcnow(),
            }

        with get_session() as session:
            _gravar_lojas_em_bloco(session, list(por_cnpj.values()))

        processados = len(ativos)
        mensagem = (
            f"{processados} lojas ativas sincronizadas com sucesso "
            f"(de {len(registros)} recebidas da API, só active=true)."
        )
        if lojas_com_setor_duplicado:
            mensagem += (
                f" {lojas_com_setor_duplicado} loja(s) tinham mais de uma pessoa no mesmo "
                "setor relevante — usada a primeira ocorrência."
            )
        repetidos = len(ativos) - len(por_cnpj)
        if repetidos:
            mensagem += (
                f" {repetidos} registro(s) repetiam um CNPJ já recebido — "
                "valeu a última ocorrência."
            )

        return ResultadoSincronizacao(
            status=StatusIntegracao.DISPONIVEL,
            registros_processados=processados,
            mensagem=mensagem,
        )
