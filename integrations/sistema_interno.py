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
from sqlalchemy import select

from core.config import settings
from core.db import get_session
from core.models import Loja
from integrations.base import IntegrationAdapter, ResultadoSincronizacao, StatusIntegracao

# setor do time (item["team"][i]["sector"]) -> campo de Loja que ele preenche.
# Setores fora dessa lista (PBM, Supervisão, Assistente de Pedidos, Expansão,
# Qualidade, Produtos Exclusivos) não têm campo correspondente e são ignorados.
_SETOR_PARA_CAMPO = {
    "Consultoria Interna": "consultor_interno",
    "Consultoria Farma": "consultor_farma",
    "Negócios": "atendente_comercial",
}


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
        processados = 0
        lojas_com_setor_duplicado = 0

        with get_session() as session:
            for item in registros:
                if item.get("active") is not True:
                    continue

                # Primeira ocorrência de cada setor relevante vence; se
                # aparecer um segundo "Consultoria Interna" (etc.) na mesma
                # loja, ele é descartado e contado abaixo pra relatar.
                campos_time: dict[str, str | None] = {}
                duplicou = False
                for pessoa in item.get("team") or []:
                    campo = _SETOR_PARA_CAMPO.get(pessoa.get("sector"))
                    if campo is None:
                        continue
                    if campo in campos_time:
                        duplicou = True
                        continue
                    campos_time[campo] = pessoa.get("name")
                if duplicou:
                    lojas_com_setor_duplicado += 1

                loja = session.execute(
                    select(Loja).where(Loja.cnpj == item["cnpj"])
                ).scalar_one_or_none()
                if loja is None:
                    loja = Loja(cnpj=item["cnpj"])
                    session.add(loja)

                endereco = item.get("address") or {}
                loja.razao_social = item["businessName"]
                loja.uf = endereco["state"]
                loja.cidade = endereco["city"]
                loja.atendente_comercial = campos_time.get("atendente_comercial")
                loja.consultor_farma = campos_time.get("consultor_farma")
                loja.consultor_interno = campos_time.get("consultor_interno")
                # grupo_economico: pendente de decisão separada (ver
                # docstring do módulo) — nunca inventar um valor aqui.
                loja.fonte = "sistema_interno"
                loja.atualizado_em = dt.datetime.utcnow()
                processados += 1

        mensagem = (
            f"{processados} lojas ativas sincronizadas com sucesso "
            f"(de {len(registros)} recebidas da API, só active=true)."
        )
        if lojas_com_setor_duplicado:
            mensagem += (
                f" {lojas_com_setor_duplicado} loja(s) tinham mais de uma pessoa no mesmo "
                "setor relevante — usada a primeira ocorrência."
            )

        return ResultadoSincronizacao(
            status=StatusIntegracao.DISPONIVEL,
            registros_processados=processados,
            mensagem=mensagem,
        )
