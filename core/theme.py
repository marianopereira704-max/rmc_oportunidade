"""Identidade visual (skill mariano-identidade-visual) aplicada ao sistema.

Nota sobre navegação: a skill define dois padrões — abas dentro da página
(padrão oficial atual) e navbar fixa navy (padrão alternativo, só pra quando o
projeto cresce para múltiplas seções reais). Este sistema tem seções de
verdade (Análise de Oportunidade, Dados, e os placeholders de Pedido/
Dashboard) navegadas por sidebar — é o caso que justifica o padrão
alternativo, aplicado verticalmente porque a navegação pedida é em sidebar.

Nota sobre o par cinza "já otimizada": a paleta oficial define 4 pares de
status (sugestão/sucesso/alterado/múltiplo), nenhum deles "neutro discreto".
Quando uma loja/produto já compra no menor preço disponível (nada a vender
ali), o valor não pode competir visualmente com a economia real em R$ (que
usa o par verde de sucesso) — por isso este par cinza foi adicionado como
extensão pontual da paleta (a própria skill prevê avaliar novos pares caso a
caso). Fundo #E7E9EC / texto #3F4750 — contraste ≈ 7,75:1 (acima do mínimo
AA de 4,5:1), deliberadamente mais apagado que o verde de sucesso.
"""
from __future__ import annotations

import base64
import html
import io
import logging

import streamlit as st
from PIL import Image

from core.config import settings

logger = logging.getLogger(__name__)

PALETA = {
    "navy": "#17375E",
    "navy_claro": "#4A6E90",
    "verde": "#8DC63F",
    "verde_escuro": "#639922",
    "fundo_card": "#F7F8FA",
    "borda": "#E4E7EB",
    "texto_muted": "#7A8699",
}

STATUS = {
    "sugestao": {"bg": "#FAEEDA", "fg": "#854F0B"},
    "sucesso": {"bg": "#EAF3DE", "fg": "#27500A"},
    "alterado": {"bg": "#DCE9F7", "fg": "#123A63"},
    "multiplo": {"bg": "#EEE8F7", "fg": "#4A2E7A"},
    "neutro": {"bg": "#E7E9EC", "fg": "#3F4750"},  # NOVO — "já otimizada"
}

# ---------------------------------------------------------------------------
# Camada 1 — tokens: a fonte ÚNICA dos valores de tamanho do sistema.
#
# Escala curta de propósito (aprovada em 24/09/2026): antes eram 9 tamanhos
# de fonte (alguns em px, outros em rem) e espaçamentos/raios escolhidos um a
# um em cada componente. Componente novo usa só estes valores; se precisar de
# outro, o valor entra AQUI primeiro. Fonte do corpo e títulos nativos do
# Streamlit vêm de .streamlit/config.toml (baseFontSize/headingFontSizes),
# com os mesmos números.
# ---------------------------------------------------------------------------
TOKENS = {
    # texto
    "fs-titulo": "28px",    # título da página (cabeçalho de cada tela)
    "fs-secao": "20px",     # título de seção
    "fs-destaque": "16px",  # destaque: nome em evidência, subtítulo do cabeçalho
    "fs-corpo": "14px",     # corpo: tabelas, filtros, textos
    "fs-aux": "13px",       # auxiliar: CNPJ, responsáveis, textos em cinza
    "fs-min": "12px",       # mínimo: selos, rótulos de grupo
    # espaçamento
    "esp-1": "4px",
    "esp-2": "8px",
    "esp-3": "12px",
    "esp-4": "16px",
    "esp-5": "24px",
    "esp-6": "32px",
    # raios
    "raio-sm": "6px",     # selo pequeno, botão compacto
    "raio": "8px",        # botão, campo
    "raio-lg": "12px",    # card, faixa, tabela, cabeçalho
    "raio-pill": "999px",
}

# ---------------------------------------------------------------------------
# Camada 2 — contrato com a estrutura INTERNA do Streamlit.
#
# Estes são os únicos pontos em que o CSS depende de como o Streamlit monta o
# HTML (atributos `data-testid`, classes de widget). Isso pode mudar de uma
# versão pra outra sem aviso — já aconteceu (o alinhamento dos botões da
# sidebar parou de valer entre 1.60 e 1.62). Por isso: (1) a versão do
# Streamlit é fixada no requirements.txt; (2) toda regra que depende dessa
# estrutura monta o seletor a partir DESTA lista; (3) o teste de contrato
# (tests/visual/) abre as telas e confere se cada seletor ainda encontra o
# elemento — rodar antes de atualizar o Streamlit.
#
# nome -> (seletor, tela em que o teste confere que ele existe)
# ---------------------------------------------------------------------------
SELETORES_STREAMLIT: dict[str, tuple[str, str]] = {
    "sidebar": ('section[data-testid="stSidebar"]', "por_loja"),
    "conteudo": ('[data-testid="stMainBlockContainer"]', "por_loja"),
    "bloco_vertical": ('div[data-testid="stVerticalBlock"]', "por_loja"),
    "elemento": ('div[data-testid="stElementContainer"]', "por_loja"),
    "botao": ("div.stButton > button", "por_loja"),
    "botao_download": ("div.stDownloadButton > button", "dados_explorador_baixar"),
    # <div> de dentro do <button>, que centraliza ícone+texto (1.62) — alinhar
    # o <button> à esquerda não basta, é este que manda (medido 25/09/2026).
    "botao_conteudo": ("div.stButton > button > div", "por_loja"),
    "markdown": ('[data-testid="stMarkdownContainer"]', "por_loja"),
    "rotulo_campo": ('[data-testid="stWidgetLabel"]', "por_loja"),
    "titulo_expander": ('[data-testid="stExpander"] summary', "por_loja"),
    "aba": ('[data-testid="stTab"]', "dados_explorador"),
    "cabecalho_app": ('[data-testid="stHeader"]', "login"),
    "fundo_app": ('[data-testid="stAppViewContainer"]', "login"),
    "iframe": ('[data-testid="stIFrame"]', "login"),
}


def _sel(nome: str) -> str:
    return SELETORES_STREAMLIT[nome][0]


def _com_marcador(marcador: str) -> str:
    """Bloco vertical que tem, como filho direto, o elemento com o marcador
    invisível `div.<marcador>` — é assim que um botão nativo recebe estilo
    próprio sem perder a função (padrão da skill de identidade visual)."""
    return f'{_sel("bloco_vertical")}:has(> {_sel("elemento")} div.{marcador})'


def _construir_css() -> str:
    """Monta o CSS a cada chamada — `settings.tema.*` (configurável via
    secrets/env) é lido na hora, nunca congelado no import.

    Organizado em quatro camadas, cada uma com seu alcance:
    1. tokens (`:root`) — valores globais, de propósito;
    2. adaptação do Streamlit — as únicas regras sobre a estrutura interna
       dele, todas a partir de `SELETORES_STREAMLIT`;
    3. componentes — classes próprias (`.rmc-*`) e containers com `key`
       própria (`st-key-*`), que só pegam onde o componente é usado;
    4. telas — ajustes que valem só numa tela, dentro do container dela
       (`theme.tela("nome")` → classe `st-key-tela-nome`)."""
    sidebar_largura = settings.tema.sidebar_largura_px
    largura_conteudo = settings.tema.largura_maxima_px
    tokens = "\n".join(f"    --{nome}: {valor};" for nome, valor in TOKENS.items())
    sidebar = _sel("sidebar")
    nav = _com_marcador("nav-marker")
    nav_ativo = _com_marcador("nav-marker-ativo")
    bc = _com_marcador("bc-btn")
    bc_ativo = _com_marcador("bc-btn-ativo")
    destaque = _com_marcador("btn-destaque")
    botao = _sel("botao")
    return f"""
<style>
/* ===== 1. Tokens ===================================================== */
:root {{
    --navy: {PALETA['navy']};
    --navy-claro: {PALETA['navy_claro']};
    --verde: {PALETA['verde']};
    --verde-escuro: {PALETA['verde_escuro']};
    --fundo-card: {PALETA['fundo_card']};
    --borda: {PALETA['borda']};
    --texto-muted: {PALETA['texto_muted']};
    --negativo: #B3261E;
    --sidebar-texto-2: #C9D4E0;
    --largura-conteudo: {largura_conteudo}px;
{tokens}
}}

/* ===== 2. Adaptação do Streamlit (estrutura interna — ver
   SELETORES_STREAMLIT e o teste de contrato em tests/visual/) ========== */

/* Área útil com largura máxima, centralizada. O respiro lateral do
   Streamlit (padding) fica de fora da conta: a largura limitada é a do
   conteúdo em si. */
{_sel("conteudo")} {{
    max-width: calc(var(--largura-conteudo) + 10rem);
}}

/* Texto "pequeno" do Streamlit (rótulo de campo, título de expander, texto
   de botão, aba) é 0,875 × a fonte base: com baseFontSize 14 dava 12,25px, fora
   da escala (medido em 24/09/2026 — antes, com base 16, era 14px). Vai para
   o tamanho auxiliar da escala. */
{_sel("rotulo_campo")} p,
{_sel("titulo_expander")} p,
{_sel("aba")} p,
{botao} p,
{_sel("botao_download")} p {{
    font-size: var(--fs-aux);
}}

/* Sidebar navy, largura travada (min == max == width desativa o arraste
   de redimensionar, que o Streamlit faz via style inline — por isso
   !important). */
{sidebar} {{
    background: linear-gradient(180deg, var(--navy) 0%, #1c3f68 100%);
    width: {sidebar_largura}px !important;
    min-width: {sidebar_largura}px !important;
    max-width: {sidebar_largura}px !important;
}}
{sidebar} * {{
    color: #FFFFFF;
}}
{sidebar} .stCaption, {sidebar} small {{
    color: var(--sidebar-texto-2) !important;
}}

/* Espaço regular na sidebar: 8px entre itens. O elemento do marcador
   invisível (nav-marker) ocupava uma vaga no "gap" do Streamlit — dava 28px
   entre botões do mesmo grupo. Escondido com display:none ele continua no
   DOM (o seletor :has() do marcador segue valendo), mas sai do espaçamento. */
{sidebar} {_sel("bloco_vertical")} {{ gap: var(--esp-2); }}
/* O Streamlit põe margin-bottom: -1rem (-14px) em todo bloco de Markdown:
   o rótulo do grupo "afundava" no botão de baixo e o primeiro rótulo
   encostava no logo (medido em 25/09/2026). Na sidebar, sem margem. */
{sidebar} {_sel("markdown")} {{ margin-bottom: 0; }}
{sidebar} {_sel("elemento")}:has(div.nav-marker),
{sidebar} {_sel("elemento")}:has(div.nav-marker-ativo) {{ display: none; }}
{sidebar} {nav} {_sel("botao_conteudo")},
{sidebar} {nav_ativo} {_sel("botao_conteudo")} {{ justify-content: flex-start; }}

/* Botões de navegação da sidebar (marcador nav-marker / nav-marker-ativo). */
{sidebar} {nav} {botao} {{
    background: transparent;
    border: 1px solid rgba(255,255,255,0.18);
    color: #FFFFFF;
    text-align: left;
    justify-content: flex-start;
    align-items: center;
    gap: 10px;
    width: 100%;
    border-radius: var(--raio);
    font-weight: 500;
}}
{sidebar} {nav_ativo} {botao} {{
    background: var(--verde);
    color: var(--navy);
    border: 1px solid var(--verde);
    text-align: left;
    justify-content: flex-start;
    align-items: center;
    gap: 10px;
    width: 100%;
    border-radius: var(--raio);
    font-weight: 700;
}}

/* Botão padrão (fora da sidebar): navy preenchido, texto branco. */
{botao} {{
    background: var(--navy);
    color: #FFFFFF;
    border: 1px solid var(--navy);
    border-radius: var(--raio);
    font-weight: 600;
}}
{botao}:hover {{
    background: #123055;
    border-color: #123055;
    color: #FFFFFF;
}}
{botao}:focus:not(:active) {{
    box-shadow: none;
}}
{_sel("botao_download")} {{
    background: #FFFFFF;
    color: var(--navy);
    border: 1px solid var(--navy);
    border-radius: var(--raio);
    font-weight: 600;
}}
{_sel("botao_download")}:hover {{
    background: var(--fundo-card);
}}

/* Variações de botão por marcador invisível. */
{bc} {botao} {{
    background: var(--fundo-card);
    color: var(--navy);
    border: 1px solid var(--borda);
    font-weight: 500;
}}
{bc_ativo} {botao} {{
    background: #FFFFFF;
    color: var(--navy);
    border: 1px solid var(--navy);
    font-weight: 700;
}}
/* Destaque com glow no hover (no máximo 1 por tela, regra da skill). */
{destaque} {botao} {{
    background: var(--verde-escuro);
    border-color: var(--verde-escuro);
}}
{destaque} {botao}:hover {{
    box-shadow: 0 0 0 4px rgba(141, 198, 63, 0.28);
}}

/* ===== 3. Componentes ================================================ */

/* Marca no topo da sidebar */
.rmc-marca {{
    display: flex; align-items: center; gap: var(--esp-3);
    padding: var(--esp-2) var(--esp-1) var(--esp-3) var(--esp-1);
}}
.rmc-marca-logo {{
    width: 36px; height: 36px; border-radius: 50%; flex: none; object-fit: cover;
    background: linear-gradient(135deg, var(--verde) 0%, var(--navy-claro) 100%);
    box-shadow: 0 0 0 2px rgba(255,255,255,0.18);
}}
.rmc-marca-nome {{ font-weight: 700; font-size: var(--fs-destaque); line-height: 1.25; }}
.rmc-marca-usuario {{ font-size: var(--fs-aux); line-height: 1.3; color: var(--sidebar-texto-2) !important; }}
.rmc-marca-usuario .sep {{ opacity: .5; margin: 0 var(--esp-1); }}
/* Divisória entre grupos da navegação e antes do "Sair". */
.rmc-nav-divisor {{ border-top: 1px solid rgba(255,255,255,0.14); margin: var(--esp-2) 0; }}

/* Rótulo de grupo da navegação (ex.: "Análise de Oportunidade"). O
   !important vence o "{sidebar} *" que fixa branco em tudo. */
{sidebar} .rmc-nav-grupo-label {{
    color: var(--sidebar-texto-2) !important;
    font-size: var(--fs-min);
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin: var(--esp-1) var(--esp-1) 0;
}}

/* Cabeçalho da página em mesh gradient */
.rmc-header {{
    border-radius: var(--raio-lg);
    padding: var(--esp-5) var(--esp-6);
    margin-bottom: var(--esp-5);
    background:
        radial-gradient(circle at 15% 20%, rgba(141,198,63,0.55), transparent 45%),
        radial-gradient(circle at 85% 15%, rgba(74,110,144,0.55), transparent 45%),
        radial-gradient(circle at 50% 100%, rgba(99,153,34,0.45), transparent 55%),
        var(--navy);
    color: #FFFFFF;
}}
.rmc-header h1 {{
    font-size: var(--fs-titulo);
    font-weight: 700;
    margin: 0 0 var(--esp-1) 0;
    padding: 0;
    color: #FFFFFF;
}}
.rmc-header p {{
    margin: 0;
    font-size: var(--fs-destaque);
    color: #E6ECF3;
}}

/* Card de KPI */
/* Cards de indicadores (KPI), no modelo aprovado em 25/09/2026: ícone num
   círculo à esquerda e, ao lado, TÍTULO em maiúsculas em cima, número e
   subtítulo; fundo levemente tingido na cor do card. Grid de 4 colunas
   iguais: as bordas batem com as da tabela embaixo. */
.rmc-kpis {{
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: var(--esp-3);
    /* 16px do vão da tela + 12px = 28px até os rótulos dos filtros — com
       16px a borda dos cards parecia "colada" nos rótulos. */
    margin-bottom: var(--esp-3);
}}
.rmc-kpi {{
    background: #FFFFFF;
    border: 1px solid var(--borda);
    border-radius: var(--raio-lg);
    padding: var(--esp-4) var(--esp-5);
}}
.rmc-kpi .valor {{ font-size: var(--fs-secao); font-weight: 700; color: var(--navy); }}
.rmc-kpi .label {{ color: var(--texto-muted); font-size: var(--fs-aux); }}
/* Daqui pra baixo, só os cards de indicador (dentro de .rmc-kpis). O
   `.rmc-kpi` sozinho também é usado nos cards de status da aba Dados — em
   25/09/2026 mudar o `.rmc-kpi` direto alterou aquela tela sem querer
   (o teste visual pegou). */
.rmc-kpis .rmc-kpi {{
    display: flex; align-items: flex-start; gap: var(--esp-3);
    padding: var(--esp-4); min-width: 0;
    container-type: inline-size;
}}
.rmc-kpis .rmc-kpi.azul {{ background: linear-gradient(135deg, #F1F6FC 0%, #FFFFFF 100%); border-color: #D9E6F5; }}
.rmc-kpis .rmc-kpi.verde {{ background: linear-gradient(135deg, #F3F8EC 0%, #FFFFFF 100%); border-color: #DCEBC9; }}
.rmc-kpis .rmc-kpi.roxo {{ background: linear-gradient(135deg, #F5F1FB 0%, #FFFFFF 100%); border-color: #E3DAF2; }}
.rmc-kpis .icone {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 40px; height: 40px; border-radius: 50%; flex: none;
}}
.rmc-kpis .icone svg {{ width: 20px; height: 20px; }}
.rmc-kpis .azul .icone {{ background: {STATUS['alterado']['bg']}; color: var(--navy); }}
.rmc-kpis .verde .icone {{ background: {STATUS['sucesso']['bg']}; color: var(--verde-escuro); }}
.rmc-kpis .roxo .icone {{ background: {STATUS['multiplo']['bg']}; color: {STATUS['multiplo']['fg']}; }}
.rmc-kpis .corpo {{ min-width: 0; flex: 1; }}
.rmc-kpis .label {{
    color: var(--navy); font-size: var(--fs-min); font-weight: 700;
    text-transform: uppercase; letter-spacing: .03em; line-height: 1.3;
}}
.rmc-kpis .linha-valor {{
    display: flex; align-items: center; flex-wrap: wrap; gap: 2px var(--esp-2); margin-top: var(--esp-1);
}}
.rmc-kpis .valor {{ line-height: 1.2; white-space: nowrap; }}
.rmc-kpis .sub {{ margin-top: 2px; color: var(--texto-muted); font-size: var(--fs-aux); line-height: 1.3; }}
/* Card estreito (1280px: ~205px por card): o ícone ao lado não deixa caber
   "R$ 31.503,20" — o ícone sobe e o título continua em cima do número. */
@container (max-width: 250px) {{
    .rmc-kpis .rmc-kpi-dentro {{ flex-direction: column; gap: var(--esp-2); }}
    /* Título de 1 ou 2 linhas conforme o card: reservar 2 deixa os números
       dos quatro cards na mesma altura. */
    .rmc-kpis .label {{ min-height: 2.6em; }}
}}
.rmc-kpis .rmc-kpi-dentro {{ display: flex; align-items: flex-start; gap: var(--esp-3); width: 100%; min-width: 0; }}
/* Selo de tendência: pílula com seta + %. Verde = subiu, âmbar = caiu
   (nunca vermelho — regra da identidade). */
.rmc-tendencia {{
    display: inline-flex; align-items: center; gap: 2px;
    padding: 2px var(--esp-2); border-radius: var(--raio-pill);
    font-size: var(--fs-min); font-weight: 600; line-height: 1.4; white-space: nowrap; cursor: default;
}}
.rmc-tendencia svg {{ width: 10px; height: 10px; }}
.rmc-tendencia.sobe {{ background: {STATUS['sucesso']['bg']}; color: {STATUS['sucesso']['fg']}; }}
.rmc-tendencia.desce {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}
.rmc-tendencia.igual {{ background: {STATUS['neutro']['bg']}; color: {STATUS['neutro']['fg']}; }}

/* Card de destaque — preenchimento navy sólido (máx. 1 por seção) */
.rmc-destaque {{
    background: var(--navy);
    color: #FFFFFF;
    border-radius: var(--raio-lg);
    padding: var(--esp-5);
}}
.rmc-destaque .valor {{
    font-size: var(--fs-titulo);
    font-weight: 700;
    color: var(--verde);
}}
.rmc-destaque .label {{
    color: var(--sidebar-texto-2);
    font-size: var(--fs-corpo);
}}

/* Selo de status (pílula) */
.rmc-badge {{
    display: inline-block;
    padding: var(--esp-1) var(--esp-2);
    border-radius: var(--raio-pill);
    font-size: var(--fs-min);
    font-weight: 600;
    line-height: 1.2;
}}
.rmc-badge-sugestao {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}
.rmc-badge-sucesso {{ background: {STATUS['sucesso']['bg']}; color: {STATUS['sucesso']['fg']}; }}
.rmc-badge-alterado {{ background: {STATUS['alterado']['bg']}; color: {STATUS['alterado']['fg']}; }}
.rmc-badge-multiplo {{ background: {STATUS['multiplo']['bg']}; color: {STATUS['multiplo']['fg']}; }}
.rmc-badge-neutro {{ background: {STATUS['neutro']['bg']}; color: {STATUS['neutro']['fg']}; }}
.rmc-badge-inativo {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}

/* Textos auxiliares */
.rmc-muted {{ color: var(--texto-muted); font-size: var(--fs-aux); }}
.rmc-negativo {{ color: var(--negativo); font-weight: 600; }}

/* Faixa do filtro principal (Laboratório): mesmo branco/borda dos cards,
   verde só na borda esquerda. Altura mínima fixa: com ou sem o seletor
   (sem laboratório cadastrado), a faixa tem a mesma altura. */
div[class*="st-key-faixa-laboratorio"] {{
    background: #FFFFFF;
    border: 1px solid var(--borda);
    border-left: 4px solid var(--verde);
    border-radius: var(--raio-lg);
    padding: var(--esp-4);
    min-height: 76px;  /* 61px ficou baixa demais (pedido de 25/09/2026) */
    justify-content: center;
}}
.rmc-faixa-cab {{ display: flex; align-items: center; gap: var(--esp-3); }}
.rmc-faixa-icone {{
    display: inline-flex; align-items: center; justify-content: center; flex: none;
    width: 32px; height: 32px; border-radius: var(--raio);
    background: {STATUS['sucesso']['bg']}; color: var(--verde-escuro);
}}
.rmc-faixa-icone svg {{ width: 18px; height: 18px; }}
.rmc-faixa-titulo {{ font-weight: 700; color: var(--navy); font-size: var(--fs-destaque); line-height: 1.25; }}
.rmc-faixa-sub {{ color: var(--texto-muted); font-size: var(--fs-aux); line-height: 1.3; }}

/* Título de seção dentro de popup/tela. O "####" do Markdown saía com
   ~12px dentro do st.dialog (medido em 24/09/2026), menor que o texto. */
.rmc-titulo-secao {{
    font-size: var(--fs-destaque); font-weight: 600; color: var(--navy);
    margin: var(--esp-4) 0 var(--esp-2) 0;
}}

/* ===== 4. Telas ======================================================
   Ajustes que valem só numa tela entram aqui, sempre dentro do container
   dela: div[class*="st-key-tela-<nome>"] ... (ver `tela()`). */

/* Análise (Por Loja / Por Produto): 16px entre as seções (cabeçalho,
   faixa, cards, filtros, lojas específicas, tabela, paginação). O padrão
   do Streamlit é 1rem = 14px, fora da escala (medido em 25/09/2026). */
div[class*="st-key-tela-por-loja"], div[class*="st-key-tela-por-produto"] {{ gap: var(--esp-4); }}
/* Mesmo -1rem do Markdown que foi zerado na sidebar: aqui ele "comia" 14px
   de baixo do cabeçalho e dos cards — o vão visível entre os cards e os
   rótulos dos filtros era de ~2px (medido em 25/09/2026). */
div[class*="st-key-tela-por-loja"] {_sel("markdown")}, div[class*="st-key-tela-por-produto"] {_sel("markdown")} {{
    margin-bottom: 0;
}}
</style>
"""


def aplicar_tema() -> None:
    st.markdown(_construir_css(), unsafe_allow_html=True)


def tela(nome: str):
    """Container de uma tela inteira (`with theme.tela("por-loja"):`). Dá à
    tela a classe `st-key-tela-<nome>`, onde entram os ajustes que valem SÓ
    nela (camada 4 do CSS) — sem isso, qualquer ajuste pra uma tela era uma
    regra global que podia mudar as outras sem ninguém perceber."""
    return st.container(key=f"tela-{nome}")


def cabecalho(titulo: str, subtitulo: str = "") -> None:
    st.markdown(
        f"""<div class="rmc-header"><h1>{titulo}</h1><p>{subtitulo}</p></div>""",
        unsafe_allow_html=True,
    )


def badge(texto: str, tipo: str = "sucesso") -> str:
    return f'<span class="rmc-badge rmc-badge-{tipo}">{texto}</span>'


def nav_grupo_label(texto: str, divisoria: bool = True) -> None:
    """Rótulo de grupo na sidebar (ex: 'Análise de Oportunidade',
    'Administração'), com linha fina acima — menos no primeiro grupo."""
    linha = '<div class="rmc-nav-divisor"></div>' if divisoria else ""
    st.markdown(f'{linha}<div class="rmc-nav-grupo-label">{html.escape(texto.upper())}</div>', unsafe_allow_html=True)


def nav_divisoria() -> None:
    st.markdown('<div class="rmc-nav-divisor"></div>', unsafe_allow_html=True)


def marca_sidebar(nome: str, usuario: str, papel: str) -> None:
    """Logo + nome do sistema + "usuário | papel" no topo da sidebar. O logo
    é o mesmo do login (já carregado em base64 no import); sem ele, fica o
    círculo em gradiente do próprio CSS."""
    if _LOGO_LOGIN is not None:
        b64, mime = _LOGO_LOGIN
        logo = f'<img class="rmc-marca-logo" src="data:{mime};base64,{b64}" alt="" />'
    else:
        logo = '<span class="rmc-marca-logo"></span>'
    st.markdown(
        f'<div class="rmc-marca">{logo}<div>'
        f'<div class="rmc-marca-nome">{html.escape(nome)}</div>'
        f'<div class="rmc-marca-usuario">{html.escape(usuario)}<span class="sep">|</span>{html.escape(papel)}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


_ICONES_KPI = {
    "loja": '<path d="M3 9l1.5-5h15L21 9M3 9h18M3 9v11h18V9M9 20v-6h6v6" stroke="currentColor" '
            'stroke-width="1.8" stroke-linejoin="round" fill="none"/>',
    "moeda": '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" fill="none"/>'
             '<path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .9-3 2.1 0 2.8 6 1.4 6 4.3 0 1.2-1.3 2.1-3 2.1-1.4 0-2.6-.6-3-1.6'
             'M12 6.5v11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/>',
    "produto": '<rect x="4" y="7" width="16" height="13" rx="2" stroke="currentColor" stroke-width="1.8" fill="none"/>'
               '<path d="M8 7V5a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M12 10.5v6M9 13.5h6" stroke="currentColor" '
               'stroke-width="1.8" stroke-linecap="round" fill="none"/>',
    "media": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2" stroke="currentColor" stroke-width="1.8" '
             'stroke-linecap="round" fill="none"/>',
}
_SETA_SOBE = '<svg viewBox="0 0 10 10"><path d="M5 1.5L9 7H1z" fill="currentColor"/></svg>'
_SETA_DESCE = '<svg viewBox="0 0 10 10"><path d="M5 8.5L1 3h8z" fill="currentColor"/></svg>'


def _tendencia(variacao: float | None, dica: str) -> str:
    """Selo de tendência, ou nada (sem período anterior comparável, a regra
    é não mostrar seta — decidido em 24/09/2026)."""
    if variacao is None:
        return ""
    percentual = f"{abs(variacao) * 100:.0f}%"
    if percentual == "0%":
        classe, seta, percentual = "igual", "", "0%"
    elif variacao > 0:
        classe, seta = "sobe", _SETA_SOBE
    else:
        classe, seta = "desce", _SETA_DESCE
    return f'<span class="rmc-tendencia {classe}" title="{html.escape(dica)}">{seta}{percentual}</span>'


def kpis(cards: list[dict]) -> None:
    """Linha de cards de indicador. Cada card: {"icone": loja|moeda|produto|
    media, "tom": azul|verde|roxo, "valor", "label", "sub" (opcional),
    "variacao" (float|None), "dica_variacao"}.

    O "$" vira entidade HTML: o Markdown do Streamlit leria "R$ … R$" como
    fórmula (mesmo motivo de views/analise_comum._sem_matematica)."""
    partes = []
    for c in cards:
        sub = f'<div class="sub">{html.escape(c["sub"])}</div>' if c.get("sub") else ""
        # O container-query mede o .rmc-kpi; quem muda de direção é o
        # .rmc-kpi-dentro (um elemento não consulta o próprio tamanho).
        partes.append(
            f'<div class="rmc-kpi {c.get("tom", "azul")}"><div class="rmc-kpi-dentro">'
            f'<span class="icone"><svg viewBox="0 0 24 24" aria-hidden="true">{_ICONES_KPI[c["icone"]]}</svg></span>'
            f'<div class="corpo"><div class="label">{html.escape(c["label"])}</div>'
            f'<div class="linha-valor"><span class="valor">{html.escape(c["valor"])}</span>'
            f'{_tendencia(c.get("variacao"), c.get("dica_variacao", ""))}</div>{sub}</div></div></div>'
        )
    st.markdown(f'<div class="rmc-kpis">{"".join(partes)}</div>'.replace("$", "&#36;"), unsafe_allow_html=True)


def titulo_secao(texto: str) -> None:
    st.markdown(f'<div class="rmc-titulo-secao">{html.escape(texto)}</div>', unsafe_allow_html=True)


def card_destaque(label: str, valor: str) -> None:
    st.markdown(
        f"""<div class="rmc-destaque"><div class="valor">{valor}</div><div class="label">{label}</div></div>""",
        unsafe_allow_html=True,
    )


# --- Tela de login -----------------------------------------------------
#
# Visual isolado do resto do sistema: fundo em gradiente diagonal, canvas de
# partículas animadas e um card branco flutuante. Só é ativado pela própria
# tela de login (aplicar_estilo_login), então os seletores abaixo nunca
# vazam pras telas internas — eles dependem dos containers com
# key="login-center-wrap"/"login-card" que só existem em views/login.py, e
# o CSS some do DOM assim que a view deixa de ser renderizada.

_LOGO_LADO_EXIBICAO_PX = 72  # tamanho da div circular no card (ver .login-logo)
_LOGO_LADO_ARQUIVO_PX = 144  # 2x o tamanho de exibição, pra telas de alta densidade


def _carregar_logo_login() -> tuple[str, str] | None:
    """Lê o logo real (settings.logo_path) e devolve (base64, mime) prontos
    pro <img src="data:..."> do card de login. Roda uma única vez no import
    deste módulo (ver _LOGO_LOGIN abaixo) — o arquivo não muda em runtime,
    então não faz sentido reabrir/recodificar a cada rerun.

    Qualquer falha aqui (arquivo ausente, corrompido, formato não suportado)
    cai no fallback do círculo gradiente que já existia na tela de login;
    como essa é a porta de entrada do sistema, um problema na logo não pode
    travar o login — mas o erro vai pro log pra não passar despercebido."""
    try:
        with Image.open(settings.logo_path) as img:
            formato = img.format or "PNG"
            largura, altura = img.size
            if largura != altura:
                lado = min(largura, altura)
                esquerda = (largura - lado) // 2
                topo = (altura - lado) // 2
                img = img.crop((esquerda, topo, esquerda + lado, topo + lado))
                largura = altura = lado
            if largura > _LOGO_LADO_ARQUIVO_PX:
                img = img.resize((_LOGO_LADO_ARQUIVO_PX, _LOGO_LADO_ARQUIVO_PX), Image.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format=formato)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
            mime = Image.MIME.get(formato, "image/png")
            return b64, mime
    except Exception:
        logger.warning(
            "Não foi possível carregar o logo de login em %s; usando o "
            "círculo gradiente de fallback.",
            settings.logo_path,
            exc_info=True,
        )
        return None


_LOGO_LOGIN = _carregar_logo_login()


def logo_login_markup() -> str:
    """HTML do círculo de logo do card de login — logo real em base64 quando
    disponível, ou o mesmo fallback em gradiente navy/verde de sempre."""
    if _LOGO_LOGIN is not None:
        b64, mime = _LOGO_LOGIN
        return (
            f'<div class="login-logo">'
            f'<img src="data:{mime};base64,{b64}" alt="{settings.nome_fornecedor}" />'
            f"</div>"
        )
    return '<div class="login-logo login-logo-fallback"></div>'


_CSS_LOGIN = f"""
<style>
header{_sel("cabecalho_app")} {{ display: none !important; }}
section[data-testid="stSidebar"] {{ display: none !important; }}
div{_sel("fundo_app")} {{
    background: linear-gradient(135deg, var(--verde) 0%, var(--navy) 100%);
}}

iframe{_sel("iframe")} {{
    position: fixed !important;
    inset: 0 !important;
    width: 100vw !important;
    height: 100vh !important;
    border: none !important;
    z-index: 0;
}}

div[class*="st-key-login-center-wrap"] {{
    position: fixed !important;
    inset: 0 !important;
    z-index: 1;
    display: flex !important;
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    overflow-y: auto !important;
}}

.login-logo {{
    width: {_LOGO_LADO_EXIBICAO_PX}px;
    height: {_LOGO_LADO_EXIBICAO_PX}px;
    border-radius: 50%;
    overflow: hidden;
    display: block;
    margin: 0 auto 16px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.25);
}}
.login-logo img {{ width: 100%; height: 100%; display: block; object-fit: cover; }}
.login-logo-fallback {{
    background: radial-gradient(circle at 30% 30%, var(--verde), var(--navy) 70%);
}}

/* Cabeçalho (logo + título + subtítulo) fica fora do card branco, direto
   sobre o fundo em gradiente — por isso texto branco em vez de var(--navy). */
.login-headline {{
    font-size: 22px;
    font-weight: 700;
    color: #FFFFFF;
    text-align: center;
    line-height: 1.3;
    margin: 0 0 4px;
}}
.login-headline span {{ font-weight: 800; }}
.login-subheadline {{
    font-size: 13px;
    color: rgba(255,255,255,0.82);
    text-align: center;
    margin: 0 0 24px;
}}

div[class*="st-key-login-card"] {{
    background-color: #FFFFFF !important;
    border-radius: 14px !important;
    padding: 28px !important;
    width: 320px;
    margin: 0 auto;
    box-sizing: border-box;
    box-shadow: 0 8px 24px rgba(0,0,0,0.18);
}}
div[class*="st-key-login-card"] label p {{
    font-size: 12px !important;
    color: var(--texto-muted) !important;
}}
div[class*="st-key-login-card"] input {{
    border: 1.5px solid #C7CDD6 !important;
    border-radius: 7px !important;
    font-size: 13px !important;
    color: var(--navy) !important;
    background-color: #FFFFFF !important;
}}
div[class*="st-key-login-card"] input:focus {{ border-color: var(--navy) !important; }}

div[class*="st-key-login-card"] div[data-testid="stButton"]:has(button[kind="primary"]) {{
    position: relative;
    overflow: hidden;
    border-radius: 8px;
    margin-top: 6px;
}}
div[class*="st-key-login-card"] button[kind="primary"] {{
    background-color: var(--navy) !important;
    border: none !important;
    color: #FFFFFF !important;
    padding: 12px !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    box-shadow: 0 2px 8px rgba(23,55,94,0.28) !important;
    transition: transform .15s ease, box-shadow .15s ease !important;
}}
div[class*="st-key-login-card"] button[kind="primary"]:hover {{
    transform: translateY(-1px);
    box-shadow: 0 4px 16px rgba(23,55,94,0.4) !important;
}}
div[class*="st-key-login-card"] div[data-testid="stButton"]:has(button[kind="primary"])::after {{
    content: "";
    position: absolute;
    top: 0;
    left: -60%;
    width: 40%;
    height: 100%;
    background: linear-gradient(120deg, transparent, rgba(141,198,63,0.55), transparent);
    transform: skewX(-20deg);
    transition: left .5s ease;
    pointer-events: none;
}}
div[class*="st-key-login-card"] div[data-testid="stButton"]:has(button[kind="primary"]):hover::after {{
    left: 130%;
}}
</style>
"""

# st.iframe (não st.html) porque o canvas precisa da própria janela/viewport
# pra calcular largura/altura e posição do mouse — dentro de um iframe real,
# window.innerWidth/innerHeight e os mousemove são os do próprio iframe, daí
# o CSS acima força o iframe (data-testid="stIFrame") a cobrir a tela toda
# (position:fixed; inset:0), assim o canvas "por trás" do card branco fica
# do tamanho exato da viewport. O guard em window.__rmcLoginParticlesInit
# evita duplicar o loop de animação caso o Streamlit reaproveite o iframe
# entre reruns.
_CANVAS_HTML_LOGIN = """
<html><head><style>
html, body { margin: 0; padding: 0; overflow: hidden; background: transparent; }
canvas { display: block; width: 100%; height: 100%; }
</style></head>
<body>
<canvas id="login-particle-canvas"></canvas>
<script>
(function () {
    if (window.__rmcLoginParticlesInit) { return; }
    window.__rmcLoginParticlesInit = true;

    const canvas = document.getElementById('login-particle-canvas');
    const ctx = canvas.getContext('2d');
    let w, h, particles = [];
    const mouse = { x: -999, y: -999 };

    function resize() {
        w = canvas.width = window.innerWidth;
        h = canvas.height = window.innerHeight;
    }
    function seed() {
        particles = [];
        const n = Math.round((w * h) / 9000);
        for (let i = 0; i < n; i++) {
            particles.push({
                x: Math.random() * w, y: Math.random() * h,
                vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3,
            });
        }
    }
    resize();
    seed();
    window.addEventListener('resize', () => { resize(); seed(); });
    window.addEventListener('mousemove', (e) => { mouse.x = e.clientX; mouse.y = e.clientY; });
    window.addEventListener('mouseleave', () => { mouse.x = -999; mouse.y = -999; });

    function tick() {
        ctx.clearRect(0, 0, w, h);
        for (const p of particles) {
            p.x += p.vx; p.y += p.vy;
            if (p.x < 0 || p.x > w) p.vx *= -1;
            if (p.y < 0 || p.y > h) p.vy *= -1;
            const dx = mouse.x - p.x, dy = mouse.y - p.y, dist = Math.hypot(dx, dy);
            if (dist < 80) { p.x -= dx * 0.02; p.y -= dy * 0.02; }
        }
        ctx.fillStyle = 'rgba(255,255,255,0.55)';
        for (const p of particles) {
            ctx.beginPath();
            ctx.arc(p.x, p.y, 1.4, 0, 7);
            ctx.fill();
        }
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx = particles[i].x - particles[j].x;
                const dy = particles[i].y - particles[j].y;
                const d = Math.hypot(dx, dy);
                if (d < 95) {
                    ctx.strokeStyle = 'rgba(255,255,255,' + (0.18 * (1 - d / 95)) + ')';
                    ctx.lineWidth = 0.6;
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.stroke();
                }
            }
        }
        window.requestAnimationFrame(tick);
    }
    tick();
})();
</script>
</body></html>
"""


def aplicar_estilo_login() -> None:
    """CSS + canvas de partículas da tela de login. Chame no início de
    views/login.py, antes de montar os containers "login-center-wrap" e
    "login-card" (os seletores CSS acima dependem desses keys)."""
    st.markdown(_CSS_LOGIN, unsafe_allow_html=True)
    st.iframe(_CANVAS_HTML_LOGIN, height=1)
