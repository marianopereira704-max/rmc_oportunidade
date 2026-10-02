"""Níveis de acesso (decisão de 27/09/2026) — estrutura pronta antes do
login individual:

- ADM: tudo.
- CONSULTOR: todas as lojas, tudo menos a Administração (Dados e
  Configurações de Pedidos).
- COMPRADOR / PROPRIETARIO: só o Pedido, e só das lojas ligadas a ele
  (`usuarios_lojas`): o dono de 4 lojas tem as 4 ligadas ao usuário dele; o
  comprador da loja 1, só a loja 1. "Gerente" virou "Comprador" em
  01/10/2026 (mesmos direitos; quem compra é quem usa o Pedido). As telas de Análise de Oportunidade são da
  consultoria (quanto a loja deixou de economizar com a RMC), não do dono
  da loja.

Hoje o login é único e a senha define o papel: admin → ADM, consultor →
CONSULTOR. Um usuário com `nivel` gravado usa esse nível.

Telas por nível (02/10/2026, loja escolhida na barra lateral):
- Por Loja: só ADM (a visão da rede inteira, loja a loja).
- Por Produto e Assistente de pedido: todos — Comprador e Proprietário
  só com as lojas liberadas pra eles (o corte é no dado, views/loja_barra.py
  e `analise.Filtros.lojas_permitidas`, não só na lista da barra).
- Dashboard: ADM e Consultor. Administração: só ADM.
"""
from __future__ import annotations

ADM = "ADM"
CONSULTOR = "CONSULTOR"
COMPRADOR = "COMPRADOR"
PROPRIETARIO = "PROPRIETARIO"
NIVEIS = (ADM, CONSULTOR, COMPRADOR, PROPRIETARIO)
# Nível gravado antes da troca de nome (01/10/2026): vale como Comprador.
_ANTIGOS = {"GERENTE": COMPRADOR}

ADMINISTRACAO = {"dados", "config_pedido"}
SO_ADM = ADMINISTRACAO | {"oportunidade_loja"}
DAS_LOJAS = {"oportunidade_produto", "pedido"}   # o que Comprador/Proprietário veem


def nivel_do_usuario(papel: str | None, nivel: str | None) -> str:
    nivel = _ANTIGOS.get(nivel, nivel)
    if nivel in NIVEIS:
        return nivel
    return ADM if papel == "admin" else CONSULTOR


def pode_ver(nivel: str, secao: str) -> bool:
    if nivel == ADM:
        return True
    if nivel == CONSULTOR:
        return secao not in SO_ADM
    return secao in DAS_LOJAS


def secao_inicial(nivel: str) -> str:
    if nivel == ADM:
        return "oportunidade_loja"
    return "oportunidade_produto" if nivel == CONSULTOR else "pedido"


def opcao_todas(nivel: str, n_lojas: int) -> str | None:
    """Rótulo da opção que junta as lojas na barra lateral, ou None quando não
    há: ADM e Consultor = "Todas as lojas" (a rede); Proprietário com mais de
    uma loja = "Todas" (só as dele); Comprador e quem tem 1 loja, nenhuma."""
    if ve_todas_as_lojas(nivel):
        return "Todas as lojas"
    if nivel == PROPRIETARIO and n_lojas > 1:
        return "Todas"
    return None


def ve_todas_as_lojas(nivel: str) -> bool:
    return nivel in (ADM, CONSULTOR)


def filtrar_lojas(lojas: list[dict], nivel: str, lojas_permitidas: list[int] | None) -> list[dict]:
    """Lojas que o usuário pode abrir. Comprador/Proprietário sem nenhuma loja
    ligada não vê nenhuma (nunca "todas" por engano)."""
    if ve_todas_as_lojas(nivel):
        return lojas
    permitidas = set(lojas_permitidas or [])
    return [l for l in lojas if l["id"] in permitidas]
