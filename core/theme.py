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

from core import dicas
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
    # Texto de apoio (legendas que explicam a tela, 07/10/2026): o cinza
    # esmaecido do Streamlit (~60% do texto) ficava apagado demais — pedido
    # do Mariano, "mais puxado para o preto do que para o cinza".
    "texto_suporte": "#3F4750",
}

STATUS = {
    "sugestao": {"bg": "#FAEEDA", "fg": "#854F0B"},
    "sucesso": {"bg": "#EAF3DE", "fg": "#27500A"},
    "alterado": {"bg": "#DCE9F7", "fg": "#123A63"},
    "multiplo": {"bg": "#EEE8F7", "fg": "#4A2E7A"},
    "neutro": {"bg": "#E7E9EC", "fg": "#3F4750"},  # NOVO — "já otimizada"
}

# Gráficos do Dashboard (21/10/2026): a mesma cor para a mesma situação em
# toda a tela. Validadas com o script do skill de dataviz contra o fundo do
# cartão (#F7F8FA) — o verde da marca (#639922) e o âmbar ficavam quase iguais
# para quem tem deuteranopia (ΔE 1,5; o piso é 8), por isso o verde escureceu e
# o âmbar clareou. Resultado nos dois gráficos de barras empilhadas (verde →
# âmbar → azul / vermelho): separação de daltonismo 9,5 a 18, normal 20,9.
# O âmbar tem contraste 2,77:1 com o fundo (< 3:1): por isso toda barra mostra
# o valor escrito e a dica, nunca só a cor. Verde e vermelho se confundem entre
# si na deuteranopia (ΔE 1,8) e por isso nunca ficam lado a lado numa pilha.
GRAFICO = {
    "eficiente": "#3F7D20",
    "excesso": "#C98A12",
    "parado": "#3B73B9",
    "ruptura": "#B3261E",
    "aproveitada": "#3F7D20",      # Compras: o que foi comprado e é aproveitado
    "sem_demanda": "#B3261E",
    # Curva ABC: uma cor só, do forte ao claro (a mais clara precisa de 2:1).
    "curva_a": "#17375E",
    "curva_b": "#4A6E90",
    "curva_c": "#86A0BF",
    # Linhas e textos do gráfico
    "grade": "#E4E7EB",            # = borda
    "zero": "#7A8699",             # linha do zero: mais forte que a grade
    "texto": "#17375E",
    "texto_suporte": "#3F4750",
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

# O balão de dica (22/10/2026, regra em core/dicas.py): um visual só para a tabela, o gráfico, os cards e o
# `help=` do Streamlit. Antes eram três: #1F2A37 na tabela, #17375E no gráfico e o do navegador no resto.
BALAO_VISUAL = ("background: #1F2A37; color: #FFFFFF; font-size: 12px; font-weight: 400; line-height: 1.4; "
                "padding: 8px 12px; border-radius: 6px; box-shadow: 0 4px 12px rgba(0, 0, 0, .18); "
                "text-align: left;")
BALAO_DICA = BALAO_VISUAL + " width: max-content; max-width: 300px; white-space: pre-line;"
# `pre-line`: a linha em branco entre parágrafos e a quebra de linha das listas aparecem como estão no texto. O
# balão de dentro vence o de fora (um card com dica e, nele, um selo com outra dica mostra só a do selo).
CSS_DICA = (
    "[data-dica] { position: relative; }\n"
    "[data-dica]:hover::after, [data-dica]:focus-visible::after {\n"
    "  content: attr(data-dica); position: absolute; z-index: 20; pointer-events: none; " + BALAO_DICA + "\n}\n"
    "[data-dica]:has([data-dica]:hover)::after { display: none; }\n"
    "[data-dica].dica-abaixo:hover::after { top: calc(100% + 6px); left: 50%; transform: translateX(-50%); }\n"
    "[data-dica].dica-esquerda:hover::after, [data-dica].dica-esquerda:focus-visible::after {\n"
    "  right: calc(100% + 8px); top: 50%; transform: translateY(-50%);\n}\n"
    "[data-dica].dica-acima:hover::after { bottom: calc(100% + 4px); left: 0; }\n"
)

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
    # O balão do `help=` (só aparece com o mouse em cima: sem tela garantida no roteiro do teste visual).
    "dica_help": ('[data-testid="stTooltipContent"]', "dica_help_sem_tela"),
    "rotulo_campo": ('[data-testid="stWidgetLabel"]', "por_loja"),
    "titulo_expander": ('[data-testid="stExpander"] summary', "por_loja"),
    "aba": ('[data-testid="stTab"]', "dados_explorador"),
    "cabecalho_app": ('[data-testid="stHeader"]', "login"),
    "fundo_app": ('[data-testid="stAppViewContainer"]', "login"),
    "iframe": ('[data-testid="stIFrame"]', "login"),
    # Camada que o 1.62 põe em volta de cada st.container, da MESMA altura
    # dele: `position: sticky` no container não gruda (o pai não tem folga).
    # Grudar esta camada, cujo pai é a página inteira (medido 29/09/2026).
    "envoltorio_layout": ('div[data-testid="stLayoutWrapper"]', "por_loja"),
    "bloco_horizontal": ('div[data-testid="stHorizontalBlock"]', "por_loja"),
    # Assistente de pedido (01/10/2026) — nome conferido no DOM do 1.62: a
    # pílula não tem data-testid, só data-variant (marcada = aria-checked na
    # escolha única, aria-pressed na múltipla). Os seletores do pop-up de
    # filtros (diálogo, campo de texto, caixa do multiselect) saíram com ele.
    # Desde 29/10/2026 conferido no Dashboard · Compras (as pílulas do Assistente saíram).
    "pilula": ('button[data-variant="pills"]', "dashboard_compras"),
    # Texto digitado/de exemplo dos campos (01/10/2026): o Streamlit usa
    # 0,875 × a base = 12,25px, fora da escala e MENOR que o rótulo.
    "campo_entrada": ('[data-testid="stTextInputField"], [data-testid="stNumberInputField"], '
                      '[data-testid="stSelectbox"] input, [data-testid="stMultiSelect"] input', "por_loja"),
    # Etiqueta de valor escolhido no multiselect: sem data-testid; é o span
    # dentro do grupo (role="group") do container de etiquetas (DOM do 1.62).
    # Caixa da seleção (selectbox) — no 1.62, o fundo é o div[role="group"]
    # dentro do ComboBox do react-aria (conferido no DOM em 02/10/2026).
    "caixa_selecao": ('[data-testid="stSelectbox"] .react-aria-ComboBox div[role="group"]', "por_loja"),
    "etiqueta_multiselect": ('[data-testid="stMultiSelectTagsContainer"] span[role="group"] > span', "pedido_filtrado"),
    # Botão terciário e o ícone Material dele (Cadastro, 07/10/2026): com
    # `help`, o 1.62 põe o <button> dentro das camadas da dica — o
    # "div.stButton > button" deixa de casar (medido no DOM).
    "botao_terciario": ('button[data-testid="stBaseButton-tertiary"]', "cadastro"),
    # Botão comum (type="secondary") — os chips de filtro e a paginação do
    # Dashboard (21/10/2026) pegam por ele, para não tocar no botão terciário
    # ("Limpar") que fica na mesma linha.
    "botao_secundario": ('button[data-testid="stBaseButton-secondary"]', "dashboard_ruptura_filtrado"),
    # Pop-ups (07/10/2026, views/popups.py) — estrutura do st.dialog no 1.62,
    # medida no DOM: camada fixa da tela inteira (fundo escurecido, alinha
    # pelo topo) > moldura (margin-top 14px) > painel section[role=dialog] >
    # botão X, título h2 e corpo.
    "dialogo_fundo": ('div[data-testid="stDialog"]', "cadastro_novo"),
    "dialogo_moldura": ('div[data-testid="stDialog"] > div', "cadastro_novo"),
    "dialogo": ('div[data-testid="stDialog"] section[role="dialog"]', "cadastro_novo"),
    "dialogo_titulo": ('div[data-testid="stDialog"] section[role="dialog"] > h2', "cadastro_novo"),
    "dialogo_corpo": ('div[data-testid="stDialog"] section[role="dialog"] > div', "cadastro_novo"),
    "barra_lateral_aberta": ('section[data-testid="stSidebar"][aria-expanded="true"]', "cadastro_novo"),
    # Legenda (st.caption) — cor do texto de apoio (07/10/2026).
    "legenda": ('[data-testid="stCaptionContainer"]', "cadastro"),
    "icone_material": ('[data-testid="stIconMaterial"]', "cadastro"),
}


# Larguras dos tipos de pop-up (views/popups.py::TIPOS — a mesma tabela).
POPUP_LARGURAS = {"aviso": 440, "curto": 560, "longo": 760, "detalhe": 1000}


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
    # Menu recolhível: o marcador do pai leva classes extras (aberto, ativo).
    pai, pai_aberto, pai_ativo = _com_marcador("nav-pai"), _com_marcador("nav-pai.aberto"), _com_marcador("nav-pai.ativo")
    filho, filho_ativo = _com_marcador("nav-filho"), _com_marcador("nav-filho-ativo")
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
    --texto-suporte: {PALETA['texto_suporte']};
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
/* Texto digitado e de exemplo dos campos: 12,25px → 14px, o texto do corpo —
   o que se lê dentro do campo não pode ser menor que o rótulo dele (Q3 de
   01/10/2026, vale no app inteiro). */
{_sel("campo_entrada")} {{
    font-size: var(--fs-corpo);
}}
/* Etiquetas do multiselect com a cara dos chips (pílula azul-clara, texto
   azul-escuro de 13px): é a mesma informação (Q12 de 01/10/2026). Antes:
   azul-marinho cheio, cantos de 6px. */
{_sel("etiqueta_multiselect")} {{
    background: {STATUS['alterado']['bg']};
    color: {STATUS['alterado']['fg']};
    border: 1px solid #C9DAF0;
    border-radius: var(--raio-pill);
    font-size: var(--fs-aux);
}}
{_sel("etiqueta_multiselect")} span, {_sel("etiqueta_multiselect")} svg {{
    color: {STATUS['alterado']['fg']};
}}

/* Sidebar navy, largura travada (min == max == width desativa o arraste
   de redimensionar, que o Streamlit faz via style inline — por isso
   !important). Só ABERTA: travada sempre, ela continuava ocupando os 272px
   depois de recolhida e o conteúdo não se ajustava (01/10/2026). */
{sidebar} {{
    background: linear-gradient(180deg, var(--navy) 0%, #1c3f68 100%);
}}
{sidebar}[aria-expanded="true"] {{
    width: {sidebar_largura}px !important;
    min-width: {sidebar_largura}px !important;
    max-width: {sidebar_largura}px !important;
}}
{sidebar} * {{
    color: #FFFFFF;
}}
/* Loja na barra (02/10/2026): caixa translúcida no navy, como os botões da
   barra — branca, o texto (branco pela regra acima) sumia. */
{sidebar} {_sel("caixa_selecao")} {{
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: var(--raio);
}}
{sidebar} {_sel("caixa_selecao")} input::placeholder {{ color: var(--sidebar-texto-2); }}
/* "Ver todas as lojas" embaixo da caixa: link discreto, não botão. */
{sidebar} div[class*="st-key-loja_barra_todas"] button {{
    background: none; border: 0; min-height: 0; padding: 0; color: var(--sidebar-texto-2);
}}
{sidebar} div[class*="st-key-loja_barra_todas"] button p {{ font-size: var(--fs-min); }}
{sidebar} div[class*="st-key-loja_barra_todas"] button:hover {{ color: #FFFFFF; text-decoration: underline; }}
{sidebar} .rmc-loja-fixa {{
    padding: var(--esp-2) var(--esp-3); border: 1px solid rgba(255, 255, 255, 0.35); border-radius: var(--raio);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: var(--fs-corpo);
}}
/* Atualização dos dados, no pé da barra: discreto, sem destaque (Q5). */
{sidebar} .rmc-atualizacao {{
    font-size: var(--fs-min); line-height: 1.5; color: var(--sidebar-texto-2) !important; opacity: .8;
    margin-top: var(--esp-1);
}}
{sidebar} .stCaption, {sidebar} small {{
    color: var(--sidebar-texto-2) !important;
}}
/* Legendas (st.caption) no app inteiro — telas, pop-ups e login: o texto
   que explica a tela, mais escuro (07/10/2026). Na barra lateral (fundo
   navy) continua valendo a regra acima, que tem !important. O 1.62 apaga a
   legenda com `opacity: 0.6` no contêiner, não pela cor: só trocar a cor
   deixava o texto igual de claro (medido: cor #3F4750 desenhada como
   #8B9098). Por isso a opacidade volta a 1 aqui. */
{_sel("legenda")} {{ opacity: 1; }}
{sidebar} {_sel("legenda")} {{ opacity: 0.6; }}
{_sel("legenda")}, {_sel("legenda")} p {{ color: var(--texto-suporte); }}

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
{sidebar} {_sel("elemento")}:has(div.nav-marker-ativo),
{sidebar} {_sel("elemento")}:has(div.nav-pai),
{sidebar} {_sel("elemento")}:has(div.nav-filho),
{sidebar} {_sel("elemento")}:has(div.nav-filho-ativo) {{ display: none; }}
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

/* === BARRA LATERAL SEM CONTORNO NOS INATIVOS (02/10/2026) — bloco isolado:
   pra voltar ao visual anterior (todos com contorno), apagar só daqui até
   "FIM DA BARRA SEM CONTORNO". O ativo continua verde cheio; os inativos
   ficam só ícone e texto, com um fundo leve ao passar o mouse. === */
{sidebar} {nav} {botao} {{
    border-color: transparent;
}}
{sidebar} {nav} {botao}:hover {{
    background: rgba(255, 255, 255, 0.08);
    border-color: transparent;
}}
/* === FIM DA BARRA SEM CONTORNO === */

/* === MENU RECOLHÍVEL (21/10/2026) — bloco isolado. Item pai (Dashboard) que
   abre e fecha, e telas filhas recuadas (views/menu_lateral.py). O pai fica com
   fundo claro e negrito enquanto uma filha está ativa; a filha ativa tem o
   verde cheio dos demais itens. A seta é desenhada aqui: o botão só aceita o
   ícone da esquerda. Apagar o bloco: o pai vira um botão comum e as filhas
   ficam sem recuo. === */
{sidebar} {pai} {botao}, {sidebar} {filho} {botao}, {sidebar} {filho_ativo} {botao} {{
    background: transparent;
    border: 1px solid transparent;
    color: #FFFFFF;
    text-align: left;
    justify-content: flex-start;
    align-items: center;
    gap: 10px;
    width: 100%;
    border-radius: var(--raio);
    font-weight: 500;
}}
{sidebar} {pai} {_sel("botao_conteudo")}, {sidebar} {filho} {_sel("botao_conteudo")},
{sidebar} {filho_ativo} {_sel("botao_conteudo")} {{ justify-content: flex-start; }}
{sidebar} {pai} {botao}:hover, {sidebar} {filho} {botao}:hover {{ background: rgba(255, 255, 255, 0.08); }}
{sidebar} {pai} {botao} {{ position: relative; padding-right: 40px; }}
{sidebar} {pai} {botao}::after {{
    content: "";
    position: absolute;
    right: var(--esp-4);
    top: 50%;
    width: 7px;
    height: 7px;
    border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor;
    transform: translateY(-70%) rotate(45deg);
    transition: transform .15s;
}}
{sidebar} {pai_aberto} {botao}::after {{ transform: translateY(-30%) rotate(225deg); }}
{sidebar} {pai_ativo} {botao} {{ background: rgba(255, 255, 255, 0.14); font-weight: 700; }}
{sidebar} {filho} {botao}, {sidebar} {filho_ativo} {botao} {{
    width: calc(100% - var(--esp-5));
    margin-left: var(--esp-5);
    font-size: var(--fs-corpo);
}}
{sidebar} {filho} {botao} {{ color: var(--sidebar-texto-2); }}
{sidebar} {filho_ativo} {botao} {{
    background: var(--verde);
    color: var(--navy);
    border-color: var(--verde);
    font-weight: 700;
}}
@media (prefers-reduced-motion: reduce) {{ {sidebar} {pai} {botao}::after {{ transition: none; }} }}
/* === FIM DO MENU RECOLHÍVEL === */

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
/* Download como ação principal (type="primary" — ex.: "Baixar" na
   exportação do Assistente de pedido): azul-marinho cheio, como os outros
   botões primários (01/10/2026). */
{_sel("botao_download")}[kind="primary"] {{
    background: var(--navy); color: #FFFFFF;
}}
{_sel("botao_download")}[kind="primary"]:hover {{
    background: #123055; border-color: #123055; color: #FFFFFF;
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
.rmc-kpis .rmc-kpi.ambar {{ background: linear-gradient(135deg, #FDF6EA 0%, #FFFFFF 100%); border-color: #F2E0C0; }}
.rmc-kpis .ambar .icone {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}
/* Vermelho (Dashboard, 21/10/2026): ruptura e venda em risco. O par do selo "perigo" da tabela. */
.rmc-kpis .rmc-kpi.vermelho {{ background: linear-gradient(135deg, #FDF1F0 0%, #FFFFFF 100%); border-color: #F5D5D2; }}
.rmc-kpis .vermelho .icone {{ background: #FDE2E1; color: #9B1C1C; }}
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
.rmc-kpis.unico {{ grid-template-columns: minmax(0, 1fr); margin-bottom: 0; height: 100%; }}
.rmc-kpis.unico .rmc-kpi {{ height: 100%; }}
.rmc-kpis .rmc-kpi-dentro {{ display: flex; align-items: flex-start; gap: var(--esp-3); width: 100%; min-width: 0; }}
/* Barra de progresso do card (Compras, 27/10/2026: quanto da dotação já foi consumido). Vermelha quando passa. */
.rmc-kpis .rmc-kpi .corpo:has(.progresso) {{ min-width: 0; flex: 1; }}
.rmc-kpis .rmc-kpi .progresso {{ height: 6px; border-radius: var(--raio-pill); background: var(--borda);
    margin-top: var(--esp-2); overflow: hidden; }}
.rmc-kpis .rmc-kpi .progresso i {{ display: block; height: 100%; background: var(--navy); border-radius: var(--raio-pill); }}
.rmc-kpis .rmc-kpi .progresso.passou i {{ background: var(--negativo); }}
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
/* Vermelho de "em ruptura" e "alto risco" (Ruptura, 27/10/2026): o par do selo-perigo das tabelas. */
.rmc-badge-perigo {{ background: #FDE2E1; color: #9B1C1C; }}
.rmc-badge-inativo {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}

/* Textos auxiliares */
.rmc-muted {{ color: var(--texto-muted); font-size: var(--fs-aux); }}
.rmc-negativo {{ color: var(--negativo); font-weight: 600; }}

/* Faixa do filtro principal (Laboratório): mesmo branco/borda dos cards,
   verde só na borda esquerda. Altura mínima fixa: com ou sem o seletor
   (sem laboratório cadastrado), a faixa tem a mesma altura. */
/* A faixa da Loja (tela Pedido) é a mesma peça, com o mesmo papel. */
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

/* Pop-up do convite (Cadastro, 07/10/2026 — modelo do print do Mariano, nas
   cores da identidade): ícone num círculo com o par de status (verde =
   enviado, âmbar = e-mail não saiu, azul = só o link), título, uma frase. */
.rmc-convite {{ display: flex; flex-direction: column; align-items: center; text-align: center;
    gap: var(--esp-2); padding-top: var(--esp-2); }}
.rmc-convite .icone {{ width: 72px; height: 72px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; margin-bottom: var(--esp-2); }}
.rmc-convite .icone svg {{ width: 40px; height: 40px; }}
div[class*="st-key-convite-"] {{ max-width: 440px; margin: 0 auto; }}
.rmc-convite .icone.ok {{ background: {STATUS['sucesso']['bg']}; color: var(--verde-escuro); }}
.rmc-convite .icone.alerta {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}
.rmc-convite .icone.link {{ background: {STATUS['alterado']['bg']}; color: var(--navy); }}
.rmc-convite .titulo {{ font-size: var(--fs-secao); font-weight: 700; color: var(--navy); line-height: 1.2; }}
.rmc-convite .texto {{ font-size: var(--fs-corpo); color: var(--texto-suporte); line-height: 1.5;
    margin-bottom: var(--esp-2); }}
.rmc-convite .texto b {{ color: var(--navy); }}
.rmc-convite .motivo {{ display: block; font-size: var(--fs-min); color: var(--texto-muted); margin-top: var(--esp-1); }}
.rmc-convite-validade {{ text-align: center; font-size: var(--fs-min); color: var(--texto-muted);
    margin-bottom: var(--esp-2); }}

/* === DICAS (22/10/2026, regra em core/dicas.py): o mesmo balão da tabela, em qualquer elemento === */
{CSS_DICA}
/* O balão do `help=` do Streamlit, com o mesmo visual. O Markdown de dentro tem margem negativa embaixo
   (como em todo o app) e cores próprias: aqui herda o balão e separa os parágrafos em 8 px. */
{_sel("dica_help")} {{ {BALAO_VISUAL} max-width: 300px; }}
{_sel("dica_help")} {_sel("markdown")} {{ margin-bottom: 0; }}
{_sel("dica_help")} * {{ color: inherit; font-size: inherit; line-height: inherit; }}
{_sel("dica_help")} p {{ margin: 0 0 8px; }}
{_sel("dica_help")} p:last-child {{ margin-bottom: 0; }}
/* === POP-UPS (07/10/2026, grill dos pop-ups — views/popups.py) ========
   Bloco único. Q2: centralizado na vertical (margin auto na moldura — com
   align-items:center, um pop-up mais alto que a tela perderia o topo); Q3:
   na área do conteúdo — com a barra lateral aberta, o fundo ganha um recuo
   à esquerda da largura dela; Q4: largura do tipo ou a área menos 32 px, a
   menor; Q5: no máximo 85% da altura, o corpo rola e o rodapé de botões
   fica preso embaixo; Q8: no celular, a tela menos 32 px; Q11: 24 px nas
   bordas, 16 px entre os elementos. Só pega pop-up feito pela fábrica
   (marcador st-key-popup-<tipo>). */
{_sel("dialogo_fundo")} {{ --popup-desconto: 0px; --popup-w: 440px; }}
@media (min-width: 640px) {{
    body:has({_sel("barra_lateral_aberta")}) {_sel("dialogo_fundo")} {{
        --popup-desconto: {sidebar_largura}px; padding-left: {sidebar_largura}px;
    }}
}}
{_sel("dialogo_fundo")}:has(div[class*="st-key-popup-aviso"]) {{ --popup-w: {POPUP_LARGURAS['aviso']}px; }}
{_sel("dialogo_fundo")}:has(div[class*="st-key-popup-curto"]) {{ --popup-w: {POPUP_LARGURAS['curto']}px; }}
{_sel("dialogo_fundo")}:has(div[class*="st-key-popup-longo"]) {{ --popup-w: {POPUP_LARGURAS['longo']}px; }}
{_sel("dialogo_fundo")}:has(div[class*="st-key-popup-detalhe"]) {{ --popup-w: {POPUP_LARGURAS['detalhe']}px; }}
{_sel("dialogo_moldura")}:has(div[class*="st-key-popup-"]) {{
    margin-top: auto; margin-bottom: auto; max-width: none;
    width: min(var(--popup-w), calc(100vw - var(--popup-desconto) - 32px));
}}
{_sel("dialogo")}:has(div[class*="st-key-popup-"]) {{ width: 100%; max-height: 85vh; }}
{_sel("dialogo")}:has(div[class*="st-key-popup-"]) > h2 {{
    padding: var(--esp-5) 56px var(--esp-2) var(--esp-5);
    font-size: var(--fs-secao); font-weight: 700; color: var(--navy); line-height: 1.25;
}}
{_sel("dialogo")}:has(div[class*="st-key-popup-"]) > div {{
    padding: var(--esp-2) var(--esp-5) var(--esp-5); overflow-y: auto; flex: 1 1 auto; min-height: 0;
}}
div[class*="st-key-popup-aviso"], div[class*="st-key-popup-curto"], div[class*="st-key-popup-longo"],
div[class*="st-key-popup-detalhe"] {{ gap: var(--esp-4); }}
{_sel("dialogo")}:has(div[class*="st-key-popup-"]) {_sel("markdown")} {{ margin-bottom: 0; }}
/* Rodapé de botões: preso embaixo quando o corpo rola (na camada que o
   1.62 põe em volta do container — no próprio container o sticky não gruda,
   ver "envoltorio_layout"). Fundo branco pra cobrir o que passa por baixo. */
{_sel("envoltorio_layout")}:has(> div[class*="st-key-popup-rodape"]) {{
    position: sticky; bottom: calc(-1 * var(--esp-5)); z-index: 2; background: #FFFFFF;
    padding: var(--esp-3) 0 var(--esp-5); margin-bottom: calc(-1 * var(--esp-5));
}}
/* Q6: o secundário do rodapé com contorno (o padrão do app é cheio) —
   mesmo estilo do rodapé do Assistente; só o principal é cheio. */
div[class*="st-key-popup-rodape"] {botao}[kind="secondary"] {{
    background: #FFFFFF; color: var(--navy); border-color: var(--navy);
}}
div[class*="st-key-popup-rodape"] {botao}[kind="secondary"]:hover {{ background: var(--fundo-card); color: var(--navy); }}
/* O último parágrafo de cada texto sem a margem do Streamlit: com ela, o
   vão entre dois textos virava ~50 px em vez dos 16 da escala. */
{_sel("dialogo")}:has(div[class*="st-key-popup-"]) {_sel("markdown")} p:last-child {{ margin-bottom: 0; }}
div[class*="st-key-popup-rodape-destrutivo"] {botao}[kind="primary"] {{
    background: var(--negativo); border-color: var(--negativo);
}}
div[class*="st-key-popup-rodape-destrutivo"] {botao}[kind="primary"]:hover {{
    background: #8F1E17; border-color: #8F1E17;
}}
/* Q12: rótulo de seção dentro do pop-up — "IDENTIFICAÇÃO ———". */
.rmc-popup-secao {{ display: flex; align-items: center; gap: var(--esp-2); font-size: var(--fs-min);
    font-weight: 700; color: var(--navy); text-transform: uppercase; letter-spacing: .04em; }}
.rmc-popup-secao::after {{ content: ""; flex: 1; height: 1px; background: var(--borda); }}
/* === FIM DOS POP-UPS === */

/* === DETALHE DO SKU (22/10/2026, Dashboard · Estoque) =====================
   Cabeçalho (nome, categoria | EAN, pílulas), grade de 6 cards, Diagnóstico e
   Sugestão de ação. Modelo: print do Mariano. Vermelho só no número que passou
   do limite (`.alerta`). */
.rmc-sku {{ display: flex; flex-direction: column; gap: var(--esp-4); }}
.rmc-sku-cab .nome {{ font-size: var(--fs-secao); font-weight: 700; color: var(--navy); line-height: 1.25; }}
.rmc-sku-cab .sub {{ color: var(--texto-suporte); font-size: var(--fs-corpo); margin-top: var(--esp-1); }}
.rmc-sku-cab .pilulas {{ display: flex; gap: var(--esp-2); margin-top: var(--esp-2); flex-wrap: wrap; }}
.rmc-sku-grade {{ display: grid; grid-template-columns: 1fr 1fr; gap: var(--esp-3); }}
.rmc-sku-card {{ background: var(--fundo-card); border-radius: var(--raio-lg); padding: var(--esp-3) var(--esp-4);
    min-width: 0; }}
.rmc-sku-card .rotulo {{ color: var(--texto-suporte); font-size: var(--fs-aux); }}
.rmc-sku-card .valor {{ color: var(--navy); font-size: var(--fs-destaque); font-weight: 700; margin-top: var(--esp-1); }}
.rmc-sku-card .valor.alerta {{ color: var(--negativo); }}
.rmc-sku-card .aux {{ color: var(--texto-suporte); font-size: var(--fs-aux); }}
.rmc-sku-card .aux.alerta {{ color: var(--negativo); }}
.rmc-sku-bloco {{ display: flex; gap: var(--esp-3); align-items: flex-start; }}
.rmc-sku-bloco .icone {{ flex: none; width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center;
    justify-content: center; font-weight: 700; }}
.rmc-sku-bloco.diagnostico .icone {{ background: #FDE2E1; color: #9B1C1C; }}
.rmc-sku-bloco.sugestao .icone {{ background: #DCE9F7; color: #123A63; }}
.rmc-sku-bloco .titulo {{ font-weight: 700; color: var(--navy); }}
.rmc-sku-bloco .texto, .rmc-sku-bloco li {{ color: var(--texto-suporte); font-size: var(--fs-corpo); line-height: 1.4; }}
.rmc-sku-bloco ul {{ margin: var(--esp-1) 0 0; padding-left: var(--esp-4); }}
.rmc-sku-bloco li strong {{ color: var(--navy); }}
/* Notas fiscais no detalhe do produto da página Compras (27/10/2026). */
.rmc-sku-notas {{ overflow-x: auto; border: 1px solid var(--borda); border-radius: var(--raio-lg); }}
.rmc-sku-notas table {{ width: 100%; border-collapse: collapse; font-size: var(--fs-aux); font-variant-numeric: tabular-nums; }}
.rmc-sku-notas th {{ text-align: left; color: var(--navy); font-weight: 600; background: var(--fundo-card);
    padding: var(--esp-2) var(--esp-3); white-space: nowrap; }}
.rmc-sku-notas td {{ padding: var(--esp-2) var(--esp-3); border-top: 1px solid var(--borda-linha, #EEF0F3); color: var(--texto-suporte); }}
.rmc-sku-notas .num {{ text-align: right; white-space: nowrap; }}
@media (max-width: 480px) {{ .rmc-sku-grade {{ grid-template-columns: 1fr; }} }}
/* === FIM DO DETALHE DO SKU === */

/* === CLASSIFICAÇÃO DE PRODUTOS (25/10/2026, views/classificacao_produtos.py) ===============
   A cascata em <details> aninhados: 5 barras (as categorias), cada uma com a setinha no FIM da barra
   (pedido do Mariano); dentro, IQVIA → FEBRAFAR → CMED, recuados e com a fonte numa etiqueta. A barra sem
   filhos não tem setinha, mas reserva o espaço dela para as quantidades alinharem. */
.rmc-cascata {{ display: flex; flex-direction: column; gap: var(--esp-2); }}
.rmc-cascata summary, .rmc-cascata .folha {{
    display: flex; align-items: center; gap: var(--esp-2); padding: 6px var(--esp-3); border-radius: var(--raio);
}}
.rmc-cascata summary {{ list-style: none; cursor: pointer; }}
.rmc-cascata summary::-webkit-details-marker {{ display: none; }}
.rmc-cascata summary:hover {{ background: var(--fundo-card); }}
.rmc-cascata summary::after {{
    content: ""; flex: none; width: 7px; height: 7px; margin: 0 4px 3px var(--esp-2);
    border-right: 2px solid var(--navy); border-bottom: 2px solid var(--navy); transform: rotate(45deg);
    transition: transform .15s ease;
}}
.rmc-cascata details[open] > summary::after {{ transform: rotate(-135deg); margin-bottom: -3px; }}
.rmc-cascata .folha::after {{ content: ""; flex: none; width: 19px; }}
.rmc-cascata .nome {{ flex: 1; min-width: 0; color: var(--navy); overflow-wrap: anywhere; }}
.rmc-cascata .qtd {{ color: var(--texto-suporte); font-size: var(--fs-aux); white-space: nowrap;
    font-variant-numeric: tabular-nums; }}
.rmc-cascata .pct {{ color: var(--texto-muted); }}
.rmc-cascata .fonte {{ flex: none; font-size: var(--fs-min); font-weight: 600; padding: 1px 8px;
    border-radius: var(--raio-pill); background: {STATUS['neutro']['bg']}; color: {STATUS['neutro']['fg']}; }}
.rmc-cascata .fonte.iqvia {{ background: {STATUS['alterado']['bg']}; color: {STATUS['alterado']['fg']}; }}
.rmc-cascata .fonte.cmed {{ background: {STATUS['multiplo']['bg']}; color: {STATUS['multiplo']['fg']}; }}
.rmc-cascata .filhos {{ display: flex; flex-direction: column; gap: 2px; margin: 2px 0 var(--esp-2) var(--esp-4);
    padding-left: var(--esp-3); border-left: 2px solid var(--borda); }}
.rmc-cascata > details > summary, .rmc-cascata > .folha {{
    background: #FFFFFF; border: 1px solid var(--borda); border-radius: var(--raio-lg);
    padding: var(--esp-3) var(--esp-4); font-weight: 700;
}}
.rmc-cascata > details > summary:hover {{ background: var(--fundo-card); }}
.rmc-cascata > details > summary .qtd, .rmc-cascata > .folha .qtd {{ font-weight: 400; }}
/* === FIM DA CLASSIFICAÇÃO DE PRODUTOS === */

/* ===== 4. Telas ======================================================
   Ajustes que valem só numa tela entram aqui, sempre dentro do container
   dela: div[class*="st-key-tela-<nome>"] ... (ver `tela()`). */

/* Análise (Por Loja / Por Produto): 16px entre as seções (cabeçalho,
   faixa, cards, filtros, lojas específicas, tabela, paginação). O padrão
   do Streamlit é 1rem = 14px, fora da escala (medido em 25/09/2026). */
div[class*="st-key-tela-por-loja"], div[class*="st-key-tela-por-produto"], div[class*="st-key-tela-pedido"] {{ gap: var(--esp-4); }}
/* Mesmo -1rem do Markdown que foi zerado na sidebar: aqui ele "comia" 14px
   de baixo do cabeçalho e dos cards — o vão visível entre os cards e os
   rótulos dos filtros era de ~2px (medido em 25/09/2026). */
div[class*="st-key-tela-por-loja"] {_sel("markdown")}, div[class*="st-key-tela-por-produto"] {_sel("markdown")},
div[class*="st-key-tela-pedido"] {_sel("markdown")} {{
    margin-bottom: 0;
}}
/* === DASHBOARD (21/10/2026) — bloco isolado: apagar daqui até "FIM DO
   DASHBOARD" tira só o acabamento das telas do Dashboard. Mesmos vãos das
   telas de análise (16px entre blocos), mesmo fundo/borda/raio dos cards;
   o que cada elemento mede está na seção 7 da spec (docs/specs). === */
div[class*="st-key-tela-dashboard"] {{ gap: var(--esp-4); }}
div[class*="st-key-tela-dashboard"] {_sel("markdown")} {{ margin-bottom: 0; }}
/* Cartão de gráfico e de tabela: 24px por dentro, 12px entre os itens dele. */
div[class*="st-key-dash-bloco"] {{
    background: var(--fundo-card); border: 1px solid var(--borda); border-radius: var(--raio-lg);
    padding: var(--esp-5); gap: var(--esp-3);
}}
.rmc-dash-titulo {{ font-size: var(--fs-secao); font-weight: 700; color: var(--navy); line-height: 1.2; }}
.rmc-dash-sub {{ font-size: var(--fs-aux); color: var(--texto-suporte); line-height: 1.35; }}
.rmc-dash-faixa {{ font-size: var(--fs-aux); color: var(--texto-suporte); }}
.rmc-dash-faixa b {{ color: var(--navy); font-variant-numeric: tabular-nums; }}
/* Filtros ativos: um chip por filtro (clicar tira) e "Limpar". Altura fixa: a
   linha ocupa o mesmo espaço com ou sem filtro, e a tela não "pula". */
div[class*="st-key-dash-chips"] {{ align-items: center; gap: var(--esp-2); min-height: 36px; flex-wrap: wrap; }}
div[class*="st-key-dash-chips"] {_sel("botao_secundario")} {{
    background: {STATUS['alterado']['bg']}; color: var(--navy); border: 0; border-radius: var(--raio-pill);
    padding: 5px var(--esp-3); min-height: 0; font-size: var(--fs-aux); font-weight: 600;
}}
div[class*="st-key-dash-chips"] {_sel("botao_secundario")}:hover {{ background: #D6E1EE; color: var(--navy); }}
div[class*="st-key-dash-chips"] {_sel("botao_terciario")} {{
    background: transparent; color: var(--navy-claro); text-decoration: underline; font-size: var(--fs-aux);
    font-weight: 400; border: 0; min-height: 0; padding: 5px var(--esp-2);
}}
/* Paginação da tabela: botões de contorno, pequenos. */
div[class*="st-key-dash-pag"] {{ align-items: center; justify-content: space-between; gap: var(--esp-3); }}
div[class*="st-key-dash-pag"] {_sel("botao_secundario")} {{
    background: #FFFFFF; color: var(--navy); border: 1px solid var(--borda); font-size: var(--fs-aux);
    font-weight: 400; min-height: 0; padding: 6px var(--esp-3);
}}
div[class*="st-key-dash-pag"] {_sel("botao_secundario")}:hover:not(:disabled) {{ border-color: var(--navy); background: #FFFFFF; }}
div[class*="st-key-dash-pag"] {_sel("botao_secundario")}:disabled {{ opacity: .45; }}
/* === FIM DO DASHBOARD === */
/* Assistente de pedido — topo, pílulas de parâmetros, cards e linha de
   filtros (01/10/2026). */
.rmc-pedido-titulo {{ font-size: 22px; font-weight: 700; color: var(--navy); line-height: 1.2; }}
/* Comprador (07/10/2026): as linhas de compra ao lado do título, na mesma
   linha de base — mesma altura de topo que a de todo mundo. */
.rmc-pedido-cab {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--esp-3); }}
.rmc-pedido-linhas {{ font-size: var(--fs-aux); line-height: 1; color: var(--texto-muted); cursor: help; }}
.rmc-pedido-rotulo {{ font-size: var(--fs-min); font-weight: 700; color: var(--navy); text-transform: uppercase;
    letter-spacing: .03em; display: inline-block; line-height: 28px; }}
/* "Regras desta loja" / "Regras padrão" (29/10/2026, no lugar das pílulas Personalizado × Padrão): o <p> do
   Markdown tem margem embaixo. Cursor de ajuda: a dica diz quais regras são e quem muda. */
div[class*="st-key-pedido-parametros"] {_sel("markdown")} p {{ margin: 0; }}
div[class*="st-key-pedido-parametros"] .rmc-pedido-rotulo {{ cursor: help; }}
/* Cards em colunas (um por coluna): mesmo vão de 12px do grid de cards. */
div[class*="st-key-pedido-cards"] {_sel("bloco_horizontal")} {{ gap: var(--esp-3); }}
/* 4º card ("Sem classificação"): o botão "Saiba mais" cobre o card inteiro,
   transparente — o card fica clicável sem mudar o desenho. Nada clicável
   embaixo dele (o card é só texto). */
div[class*="st-key-pedido-card-sem"] {{ position: relative; height: 100%; }}
div[class*="st-key-pedido-card-sem"] .rmc-kpi {{ cursor: pointer; transition: border-color .15s, box-shadow .15s; }}
div[class*="st-key-pedido-card-sem"]:hover .rmc-kpi {{ border-color: var(--navy); box-shadow: 0 2px 8px rgba(23, 55, 94, .08); }}
div[class*="st-key-pedido-card-sem"] .sub {{ color: var(--navy); font-weight: 600; }}
div[class*="st-key-pedido_saiba_mais"] {{ position: absolute; inset: 0; z-index: 2; }}
/* O botão fica dentro de 2 divs do Streamlit: sem 100% em todos, ele
   cobria só os 51px de cima do card (medido em 01/10/2026). */
div[class*="st-key-pedido_saiba_mais"] div, div[class*="st-key-pedido_saiba_mais"] button {{ width: 100%; height: 100%; }}
div[class*="st-key-pedido_saiba_mais"] button {{ opacity: 0; cursor: pointer; }}
/* Botões do rodapé: 40px (Q11 de 01/10/2026). Só "Exportar" é azul-marinho
   cheio; Descartar e Listas com contorno — três botões cheios lado a lado
   anulavam a hierarquia, e "Descartar" como botão principal convidava ao
   clique errado. (O topo ficou sem botões em 02/10/2026.) */
div[class*="st-key-rodape-pedido"] {botao} {{ min-height: 40px; }}
div[class*="st-key-rodape-pedido"] {botao}[kind="secondary"] {{
    background: #FFFFFF; color: var(--navy); border-color: var(--navy);
}}
div[class*="st-key-rodape-pedido"] {botao}[kind="secondary"]:hover {{ background: var(--fundo-card); color: var(--navy); }}
div[class*="st-key-rodape-pedido"] {botao}[kind="secondary"]:disabled {{
    background: #FFFFFF; color: var(--texto-muted); border-color: var(--borda);
}}
/* Tabela do Assistente: divisória entre as linhas mais clara (02/10/2026) —
   a variável atravessa o componente da tabela (views/tabela.py). */
div[class*="st-key-tela-pedido"] {{ --borda-linha: #EEF0F3; }}
/* ✓ do "Salvo automaticamente": verde apagado, nada que chame atenção. */
.rmc-salvo {{ color: #8FB27A; font-weight: 600; }}
/* Linha de filtros: a busca leva a maior parte; o toggle, o que precisa. */
div[class*="st-key-pedido-filtros-linha"] > div {{ min-width: 0; }}
/* Larguras (05/10/2026, pedido do Mariano): proporção 187 · 187 · 157 · 187
   (busca, Categoria, Status, Fabricante) sobre o que sobra depois do
   interruptor (150 px) e dos vãos — a mesma proporção em 1280 e 1920 px. */
div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_busca"],
div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_cat_"],
div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_fab_"] {{ flex: 187 1 0; }}
div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_status_"] {{ flex: 157 1 0; }}
div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_giro"] {{ flex: none; }}

/* Cadastro de usuários (07/10/2026, views/cadastro.py): tabela feita de
   colunas do Streamlit, no desenho do print de referência — quadro com
   borda, cabeçalho em maiúsculas sobre fundo claro, divisória fina. */
div[class*="st-key-tela-cadastro"] {{ gap: var(--esp-4); }}
div[class*="st-key-tela-cadastro"] {_sel("markdown")} {{ margin-bottom: 0; }}
div[class*="st-key-cadastro-tabela"] {{
    border: 1px solid var(--borda); border-radius: 12px; background: #FFFFFF; overflow: hidden; gap: 0;
}}
div[class*="st-key-cadastro-cab"] {{ background: var(--fundo-card); border-bottom: 1px solid var(--borda);
    padding: var(--esp-3) var(--esp-4); }}
div[class*="st-key-cadastro-linha-"], div[class*="st-key-cadastro-vazio"] {{
    border-bottom: 1px solid #EEF0F3; padding: var(--esp-2) var(--esp-4); }}
div[class*="st-key-cadastro-linha-"]:hover {{ background: #FAFBFC; }}
.rmc-cad-cab {{ font-size: var(--fs-min); font-weight: 700; color: var(--navy); text-transform: uppercase;
    letter-spacing: .03em; }}
.rmc-cad-celula {{ font-size: var(--fs-aux); line-height: 1.35; overflow-wrap: anywhere; }}
.rmc-cad-sub {{ font-size: var(--fs-min); color: var(--texto-muted); }}
/* Botões de ação só com o ícone (5 por linha), como no print: navy claro,
   e-mail pendente em verde, lixeira em âmbar escuro (vermelho é permitido,
   mas o âmbar já diz "cuidado" sem gritar numa lista inteira). */
div[class*="st-key-cadastro-acoes-"] {{ gap: 0; flex-wrap: nowrap; }}
div[class*="st-key-cadastro-acoes-"] {_sel("botao_terciario")} {{
    min-height: 28px; width: 28px; padding: 0; justify-content: center; color: var(--navy-claro);
    border-radius: var(--raio-sm); }}
div[class*="st-key-cadastro-acoes-"] {_sel("botao_terciario")}:hover {{ color: var(--navy); background: var(--fundo-card); }}
div[class*="st-key-cadastro-acoes-"] {_sel("botao_terciario")}:disabled {{ color: #C9CED6; background: none; }}
div[class*="st-key-cadastro-acoes-"] {_sel("icone_material")} {{ font-size: 18px; }}
div[class*="st-key-cadastro_email_"] {_sel("botao_terciario")}:not(:disabled) {{ color: var(--verde-escuro); }}
div[class*="st-key-cadastro_excluir_"] {_sel("botao_terciario")} {{ color: #B4532A; }}
div[class*="st-key-tela-cadastro"] .rmc-badge {{ white-space: nowrap; }}

/* Minhas notificações (29/10/2026, views/notificacoes.py): uma coluna de leitura, não a largura toda. */
div[class*="st-key-tela-notificacoes"] {{ max-width: 640px; }}
.rmc-notif-para {{ color: var(--texto-suporte); margin: var(--esp-2) 0 0; }}

/* === RODAPÉ FIXO DO PEDIDO (29/09/2026) — bloco isolado: se precisar
   desfazer, apagar só daqui até "FIM DO RODAPÉ FIXO". Os botões ficam
   DENTRO da faixa fixa (nunca por cima de outro elemento clicável). === */
{_sel("envoltorio_layout")}:has(> div[class*="st-key-rodape-pedido"]) {{
    position: sticky; bottom: 0; z-index: 20;
}}
div[class*="st-key-rodape-pedido"] {{
    background: #FFFFFF; border-top: 1px solid var(--borda);
    padding: var(--esp-2) 0; margin-top: var(--esp-2);
    box-shadow: 0 -6px 16px rgba(23, 55, 94, 0.06);
}}
/* === FIM DO RODAPÉ FIXO === */

/* === CELULAR (29/10/2026, etapa 6 — decidido pelo Mariano; medido em 390 × 844) — bloco isolado. Abaixo de 640 px
   (a largura em que o próprio Streamlit empilha as colunas). As tabelas viram cartão dentro do componente
   (views/tabela.py); aqui, o que é do Streamlit. Fora do celular, o que é só do celular fica escondido. === */
/* A versão escondida do rodapé some com a caixa que o Streamlit põe em volta de todo contêiner com chave
   (`envoltorio_layout`): só o contêiner escondido, a caixa vazia ainda contava o vão — o rodapé do PC subiu 14 px
   (teste visual de 29/10/2026). */
div[class*="st-key-pedido_filtros_celular"], {_sel("elemento")}:has(.rmc-filtros-abertos),
div[class*="st-key-rodape-celular"], {_sel("envoltorio_layout")}:has(> div[class*="st-key-rodape-celular"]) {{ display: none; }}
@media (max-width: 640px) {{
    /* Cards: dois por linha (quatro espremiam o "R$ 70,85" contra a borda); "so-pc" some. */
    .rmc-kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .rmc-kpi.so-pc {{ display: none; }}
    /* Assistente: Orçamento na linha inteira, Produtos e Ruptura embaixo; "Sem categoria" sai (o pop-up ao abrir a
       loja já avisa). */
    div[class*="st-key-pedido-cards"] {_sel("bloco_horizontal")} {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    div[class*="st-key-pedido-cards"] {_sel("bloco_horizontal")} > div:first-child {{ grid-column: 1 / -1; }}
    div[class*="st-key-pedido-cards"] {_sel("bloco_horizontal")} > div:has(div[class*="st-key-pedido-card-sem"]) {{ display: none; }}
    /* Assistente: a busca e o botão "Filtros"; os outros campos só com ele aberto, dois por linha. */
    div[class*="st-key-pedido-filtros-linha"] {{ flex-wrap: wrap; }}
    /* A busca e o botão sozinhos na 1ª linha: com "1 1 0", a Categoria subia para a linha deles e a busca
       encolhia a um ícone (teste visual de 29/10/2026). */
    div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_busca"] {{ flex: 1 1 calc(100% - 132px); min-width: 0; }}
    div[class*="st-key-pedido_filtros_celular"] {{ display: block; flex: none; }}
    div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_cat_"],
    div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_status_"],
    div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_fab_"],
    div[class*="st-key-pedido-filtros-linha"] div[class*="st-key-pedido_giro"] {{ display: none; }}
    div[class*="st-key-pedido-filtros-linha"]:has(.rmc-filtros-abertos) div[class*="st-key-pedido_cat_"],
    div[class*="st-key-pedido-filtros-linha"]:has(.rmc-filtros-abertos) div[class*="st-key-pedido_status_"],
    div[class*="st-key-pedido-filtros-linha"]:has(.rmc-filtros-abertos) div[class*="st-key-pedido_fab_"],
    div[class*="st-key-pedido-filtros-linha"]:has(.rmc-filtros-abertos) div[class*="st-key-pedido_giro"] {{
        display: block; flex: 1 1 calc(50% - 4px); min-width: 0;
    }}
    /* Rodapé: só "Enviar pedido" e o "⋯"; some ao rolar para baixo e volta ao rolar para cima (opção B — a
       classe no <body> vem de views/enviar_pedido.py). */
    div[class*="st-key-rodape-pc"], {_sel("envoltorio_layout")}:has(> div[class*="st-key-rodape-pc"]) {{ display: none; }}
    /* Lado a lado: no celular o Streamlit empilha os contêineres horizontais (o "⋯" ia para baixo do Enviar). */
    div[class*="st-key-rodape-celular"] {{ display: flex; flex-direction: row; flex-wrap: nowrap; align-items: center; }}
    {_sel("envoltorio_layout")}:has(> div[class*="st-key-rodape-celular"]) {{ display: block; }}
    div[class*="st-key-rodape-celular"] > * {{ width: auto; }}
    div[class*="st-key-pedido_enviar"] {{ flex: 1 1 0; min-width: 0; }}
    div[class*="st-key-pedido_mais"] {{ flex: 0 0 auto; min-width: 104px; }}
    div[class*="st-key-pedido_mais"] button {{ width: 100%; min-height: 44px; }}
    {_sel("envoltorio_layout")}:has(> div[class*="st-key-rodape-pedido"]) {{ transition: transform .2s ease; }}
    body.rmc-rolando-baixo {_sel("envoltorio_layout")}:has(> div[class*="st-key-rodape-pedido"]) {{
        transform: translateY(110%);
    }}
}}
@media (prefers-reduced-motion: reduce) {{
    {_sel("envoltorio_layout")}:has(> div[class*="st-key-rodape-pedido"]) {{ transition: none; }}
}}
/* Nome do produto no detalhe do cartão do Assistente (views/pedido/tabela_pedido._dialog_detalhe). */
.rmc-detalhe-nome {{ font-size: var(--fs-destaque); font-weight: 700; color: var(--navy); line-height: 1.3; }}
/* === FIM DO CELULAR === */

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
    "duvida": '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" fill="none"/>'
              '<path d="M9.6 9.3a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.6M12 16.8v.2" stroke="currentColor" '
              'stroke-width="1.9" stroke-linecap="round" fill="none"/>',
    "alerta": '<path d="M12 4 2.8 19.5h18.4L12 4z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" '
              'fill="none"/><path d="M12 10v4.2M12 16.8v.2" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
    "carrinho": '<path d="M3 4h2.2l2.3 11h10.8l2-7.5H6.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
                'stroke-linejoin="round" fill="none"/><circle cx="9.5" cy="19" r="1.4" fill="currentColor"/>'
                '<circle cx="16.5" cy="19" r="1.4" fill="currentColor"/>',
    # Compras (27/10/2026): a dotação (carteira), compra × venda (setas opostas) e a eficiência (visto).
    "carteira": '<rect x="3" y="6" width="18" height="13" rx="2" stroke="currentColor" stroke-width="1.8" fill="none"/>'
                '<path d="M3 10h18M7 15h4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none"/>',
    "trocas": '<path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3" stroke="currentColor" stroke-width="1.8" '
              'stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    "visto": '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" fill="none"/>'
             '<path d="M8 12.5l2.5 2.5L16 9.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
             'stroke-linejoin="round" fill="none"/>',
    # Dashboard (21/10/2026): estoque parado.
    "pausa": '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" fill="none"/>'
             '<path d="M10 9v6M14 9v6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
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
    return (f'<span class="rmc-tendencia {classe} dica-abaixo" data-dica="{dicas.atributo(dica)}">'
            f'{seta}{percentual}</span>')


def kpis(cards: list[dict], unico: bool = False) -> None:
    """Linha de cards de indicador. Cada card: {"icone": loja|moeda|produto|
    media|alerta|carrinho|pausa, "tom": azul|verde|roxo|ambar|vermelho, "valor", "label", "sub"
    (opcional), "variacao" (float|None), "dica_variacao", "dica" (opcional: a conta do número, ao
    passar o mouse no card — Dashboard, 21/10/2026), "classe" (opcional: "so-pc" some no celular, 29/10/2026)}.

    O "$" vira entidade HTML: o Markdown do Streamlit leria "R$ … R$" como
    fórmula (mesmo motivo de views/analise_comum._sem_matematica)."""
    partes = []
    for c in cards:
        sub = f'<div class="sub">{html.escape(c["sub"])}</div>' if c.get("sub") else ""
        if c.get("progresso") is not None:
            # "progresso": % (0 a 100+) — a barra enche até 100 e fica vermelha acima disso.
            p = float(c["progresso"])
            sub += (f'<div class="progresso{" passou" if p > 100 else ""}" role="img" aria-label="{p:.0f}%">'
                    f'<i style="width:{min(max(p, 0), 100):.1f}%"></i></div>')
        dica = f' data-dica="{dicas.atributo(c["dica"])}"' if c.get("dica") else ""
        # O container-query mede o .rmc-kpi; quem muda de direção é o
        # .rmc-kpi-dentro (um elemento não consulta o próprio tamanho).
        partes.append(
            f'<div class="rmc-kpi {c.get("tom", "azul")}{" " + c["classe"] if c.get("classe") else ""}'
            f'{" dica-abaixo" if dica else ""}"{dica}><div class="rmc-kpi-dentro">'
            f'<span class="icone"><svg viewBox="0 0 24 24" aria-hidden="true">{_ICONES_KPI[c["icone"]]}</svg></span>'
            f'<div class="corpo"><div class="label">{html.escape(c["label"])}</div>'
            f'<div class="linha-valor"><span class="valor">{html.escape(c["valor"])}</span>'
            f'{_tendencia(c.get("variacao"), c.get("dica_variacao", ""))}</div>{sub}</div></div></div>'
        )
    # `unico`: um card por chamada, dentro de uma coluna do Streamlit — o
    # Assistente de pedido precisa de um botão no 4º card (01/10/2026).
    classe = "rmc-kpis unico" if unico else "rmc-kpis"
    st.markdown(f'<div class="{classe}">{"".join(partes)}</div>'.replace("$", "&#36;"), unsafe_allow_html=True)


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
/* "Esqueci minha senha" / "Voltar ao login" (07/10/2026): link discreto, não
   botão — o estilo geral de botão do app pintava os dois de azul cheio. */
div[class*="st-key-login-card"] div[data-testid="stButton"]:has(button[kind="tertiary"]) {{
    display: flex; justify-content: center;
}}
div[class*="st-key-login-card"] button[kind="tertiary"] {{
    background: transparent !important; border: none !important; box-shadow: none !important;
    color: var(--navy) !important; font-weight: 600 !important; padding: 2px 4px !important;
    min-height: 0 !important; height: auto !important;
}}
div[class*="st-key-login-card"] button[kind="tertiary"]:hover {{
    text-decoration: underline !important; background: transparent !important;
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
