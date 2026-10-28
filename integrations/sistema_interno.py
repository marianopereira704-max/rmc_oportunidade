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
- Loja DA REDE (07/10/2026) = active=true E status_title "Ativo". O
  `active` sozinho trazia também os cadastros "Portal do PBM" (192) e
  "Institucional" (27): 813 "ativas", 594 da rede. Quem não está na lista
  fica com `na_rede` = False (nada se apaga) e sai das telas e da rotina.
- grupo_economico = `acronym_economic_group` ("GE787" = GE + o código da
  loja principal do grupo). Em 09/2026 só 7 lojas traziam a sigla; em
  07/10/2026, as 594 da rede (472 de 475 conferências lista × sigla batem).
  209 lojas estão em grupos de 2+ (multi-lojas), o resto é grupo de 1.
- Depois de gravar, os acessos dos usuários de loja são conferidos
  (integrations/usuarios.py::ajustar_acessos): loja fora da rede ou do grupo
  da loja principal sai do acesso, com registro em `usuarios_lojas_historico`.
"""
from __future__ import annotations

import datetime as dt

import requests
from sqlalchemy import update

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

# Status que define a loja da rede (ver a docstring do módulo).
STATUS_DA_REDE = "Ativo"

# Colunas sobrescritas quando o CNPJ já existe. `grupo_economico` entrou em
# 07/10/2026: desde então ele VEM desta origem (antes ficava de fora porque
# era preenchido por outra decisão).
_COLUNAS_ATUALIZADAS = [
    "grupo_economico", "na_rede",
    "razao_social", "uf", "cidade",
    "atendente_comercial", "consultor_farma", "consultor_interno",
    "fonte", "atualizado_em",
    # Desde 27/09/2026, para o vínculo com a loja do GPS (pedido/vinculo.py).
    "legacy_id", "endereco_numero", "bairro", "cep", "nome_fantasia",
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


def _texto_curto(valor, limite: int) -> str | None:
    """Texto limpo e cortado no tamanho da coluna — o número do endereço vem
    como inteiro em parte do cadastro e com sufixo ("170-A") em outra."""
    if valor is None:
        return None
    t = str(valor).strip()
    return t[:limite] or None


def da_rede(item: dict) -> bool:
    return item.get("active") is True and (item.get("status_title") or "").strip() == STATUS_DA_REDE


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

        ativos = [item for item in registros if da_rede(item)]
        if registros and not ativos:
            # Resposta sem nenhuma loja "Ativo" é defeito da API, não a rede
            # inteira saindo — marcar tudo como fora da rede tiraria todas as
            # lojas das telas. Nada é alterado.
            raise RuntimeError(f"A API do sistema interno devolveu {len(registros)} cadastro(s) e nenhum com status "
                               f"\"{STATUS_DA_REDE}\" — nada foi alterado.")
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
                "grupo_economico": _texto_curto(item.get("acronym_economic_group"), 150),
                "na_rede": True,
                "fonte": "sistema_interno",
                "atualizado_em": dt.datetime.utcnow(),
                "legacy_id": item.get("legacyId"),
                "endereco_numero": _texto_curto(endereco.get("number"), 20),
                "bairro": _texto_curto(endereco.get("neighborhood"), 120),
                "cep": _texto_curto(endereco.get("zip_code"), 10),
                "nome_fantasia": _texto_curto(item.get("fantasyName"), 200),
            }

        with get_session() as session:
            # Tudo numa transação: quem não veio na lista fica fora da rede,
            # quem veio volta (ou continua) nela.
            fora = 0
            if por_cnpj:
                fora = session.execute(
                    update(Loja).where(Loja.na_rede.is_not(False), Loja.cnpj.not_in(list(por_cnpj)))
                    .values(na_rede=False)
                ).rowcount
            _gravar_lojas_em_bloco(session, list(por_cnpj.values()))
            # Acesso dos usuários de loja (07/10/2026, Q32): loja que saiu da
            # rede ou do grupo econômico sai sozinha, com registro no histórico.
            from integrations import usuarios

            acessos = usuarios.ajustar_acessos(session)

        processados = len(ativos)
        mensagem = (
            f"{processados} lojas da rede sincronizadas com sucesso "
            f"(de {len(registros)} recebidas da API, só status \"{STATUS_DA_REDE}\")."
        )
        if fora:
            mensagem += f" {fora} loja(s) saíram da rede (ficam guardadas, fora das telas)."
        if acessos["excluidos"] or acessos["retiradas"]:
            mensagem += (
                f" Acessos: {acessos['excluidos']} usuário(s) excluído(s) (loja principal fora da rede) e "
                f"{acessos['retiradas']} loja(s) adicional(is) retirada(s) (fora da rede ou do grupo econômico)."
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
