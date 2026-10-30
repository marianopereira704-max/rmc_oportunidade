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
  só se o painel foi atribuído a eles no Cadastro (07/10/2026) e só com as
  lojas liberadas pra eles (o corte é no dado, views/loja_barra.py
  e `analise.Filtros.lojas_permitidas`, não só na lista da barra).
- Dashboard (21/10/2026): ADM, Consultor e Proprietário — o Comprador NÃO
  vê (decisão do Mariano). Para o Proprietário vale como os outros painéis:
  só se atribuído no Cadastro. O Dashboard tem três telas (`dashboard_estoque`, `dashboard_ruptura`
  e `dashboard_compras`) que dependem de UM painel só, "dashboard".
  Administração: só ADM — menos o Cadastro (07/10/2026, Q4): o Consultor
  também cadastra os usuários das lojas e vê o grupo Administração só com ele.
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
CADASTRO = "cadastro"   # ADM e Consultor (fora de SO_ADM e de DAS_LOJAS)
# "Minhas notificações" (29/10/2026): só Comprador e Proprietário — os e-mails para as lojas são deles. Fora dos
# painéis: todo usuário de loja vê, tenha os painéis que tiver.
NOTIFICACOES = "notificacoes"
SO_ADM = ADMINISTRACAO | {"oportunidade_loja"}
DAS_LOJAS = {"oportunidade_produto", "pedido"}   # o que Comprador/Proprietário veem
# Painéis que se atribuem a um usuário de loja (Cadastro → Atribuir Painéis),
# na ordem em que a tela inicial é escolhida.
PAINEIS_DA_LOJA = ("pedido", "oportunidade_produto", "dashboard")
# Dashboard (21/10/2026): o Comprador não recebe esse painel. Fica aqui, e não
# só no Cadastro, porque um Comprador antigo pode ter "dashboard" gravado — o
# `pode_ver` ignora, em vez de depender de alguém limpar o cadastro.
PAINEIS_POR_NIVEL = {
    COMPRADOR: ("pedido", "oportunidade_produto"),
    PROPRIETARIO: PAINEIS_DA_LOJA,
}
# Um painel pode abrir mais de uma tela: o Dashboard tem Estoque, Ruptura e
# Compras (menu recolhível na barra lateral). Seção sem entrada aqui = o
# próprio nome é o painel.
SECOES_DO_PAINEL = {"dashboard": ("dashboard_estoque", "dashboard_ruptura", "dashboard_compras")}
PAINEL_DA_SECAO = {s: p for p, secoes in SECOES_DO_PAINEL.items() for s in secoes}


def paineis_do_nivel(nivel: str | None) -> tuple[str, ...]:
    """Painéis que o Cadastro pode oferecer a um usuário de loja desse nível.
    Nível ainda não escolhido (formulário aberto): todos os que existem —
    a gravação confere de novo contra o nível escolhido."""
    return PAINEIS_POR_NIVEL.get(nivel, PAINEIS_DA_LOJA)


def nivel_do_usuario(papel: str | None, nivel: str | None) -> str:
    nivel = _ANTIGOS.get(nivel, nivel)
    if nivel in NIVEIS:
        return nivel
    return ADM if papel == "admin" else CONSULTOR


def pode_ver(nivel: str, secao: str, paineis=None) -> bool:
    """Comprador/Proprietário: só os painéis ATRIBUÍDOS a ele no Cadastro
    (07/10/2026, etapa 4 — Q11/Q21: obrigatório e sem padrão; sem painel,
    nenhuma tela). ADM e Consultor não usam painéis. `secao` pode ser uma
    tela de um painel com várias (dashboard_estoque → painel "dashboard")."""
    if secao == NOTIFICACOES:   # e-mails para as lojas: só quem os recebe (29/10/2026)
        return nivel in (COMPRADOR, PROPRIETARIO)
    if nivel == ADM:
        return True
    if nivel == CONSULTOR:
        return secao not in SO_ADM
    painel = PAINEL_DA_SECAO.get(secao, secao)
    return painel in paineis_do_nivel(nivel) and painel in set(paineis or ())


def secao_inicial(nivel: str, paineis=None) -> str | None:
    """A primeira tela ao entrar. None = usuário de loja sem nenhum painel."""
    if nivel == ADM:
        return "oportunidade_loja"
    if nivel == CONSULTOR:
        return "oportunidade_produto"
    for painel in PAINEIS_DA_LOJA:
        if pode_ver(nivel, painel, paineis):
            return SECOES_DO_PAINEL.get(painel, (painel,))[0]
    return None


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
