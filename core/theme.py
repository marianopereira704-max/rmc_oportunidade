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

def _construir_css() -> str:
    """Monta o CSS a cada chamada (não mais uma string fixa de módulo) —
    assim `settings.tema.sidebar_largura_px` (core/config.py, configurável
    via secrets/env) é sempre lido na hora, nunca "congelado" num valor
    fixo capturado no import."""
    sidebar_largura = settings.tema.sidebar_largura_px
    return f"""
<style>
:root {{
    --navy: {PALETA['navy']};
    --navy-claro: {PALETA['navy_claro']};
    --verde: {PALETA['verde']};
    --verde-escuro: {PALETA['verde_escuro']};
    --fundo-card: {PALETA['fundo_card']};
    --borda: {PALETA['borda']};
    --texto-muted: {PALETA['texto_muted']};
}}

/* Sidebar navy — navegação principal do sistema.
   Largura travada em `settings.tema.sidebar_largura_px` (default 272px,
   mesmo valor do mockup aprovado, mas ajustável via secrets/env
   SIDEBAR_LARGURA_PX sem editar código) — o Streamlit define a largura via
   style inline (sidebar redimensionável arrastando a borda), então só um
   CSS externo comum não venceria; precisa de !important tanto na largura
   quanto em min/max pra também desativar o arraste (min == max == width). */
section[data-testid="stSidebar"] {{
    background: linear-gradient(180deg, var(--navy) 0%, #1c3f68 100%);
    width: {sidebar_largura}px !important;
    min-width: {sidebar_largura}px !important;
    max-width: {sidebar_largura}px !important;
}}
section[data-testid="stSidebar"] * {{
    color: #FFFFFF;
}}
section[data-testid="stSidebar"] .stCaption, section[data-testid="stSidebar"] small {{
    color: #C9D4E0 !important;
}}

/* Botões de navegação da sidebar (marcador invisível + :has).
   Importante: o seletor usa ":has(> div[data-testid='stElementContainer'] ...)"
   — filho DIRETO stElementContainer (sempre presente, é assim que o
   Streamlit encapsula cada elemento) contendo o marcador em qualquer
   profundidade dentro dele. Isso escopa a regra exatamente ao container do
   item de navegação (marcador + botão), sem "vazar" para o stVerticalBlock
   externo que agrupa todos os itens da sidebar (esse teria o marcador como
   neto, não como filho direto de um stElementContainer seu). Seletor de
   ATRIBUTO (data-testid), não de classe (.stElementContainer) — validado
   via inspeção real do DOM (Playwright) no Streamlit 1.60; a classe
   "stElementContainer" também existe hoje, mas o atributo é o contrato
   estável que o Streamlit documenta, então é o que usamos aqui. */
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.nav-marker) div.stButton > button {{
    background: transparent;
    border: 1px solid rgba(255,255,255,0.18);
    color: #FFFFFF;
    text-align: left;
    justify-content: flex-start;
    align-items: center;
    gap: 10px;
    width: 100%;
    border-radius: 8px;
    font-weight: 500;
}}
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.nav-marker-ativo) div.stButton > button {{
    background: var(--verde);
    color: var(--navy);
    border: 1px solid var(--verde);
    text-align: left;
    justify-content: flex-start;
    align-items: center;
    gap: 10px;
    width: 100%;
    border-radius: 8px;
    font-weight: 700;
}}

/* Rótulo de grupo da navegação (ex.: "Análise de Oportunidade") — separa
   visualmente as seções da sidebar; ver theme.nav_grupo_label(). Precisa de
   !important porque "section[data-testid='stSidebar'] *" (acima) já fixa
   branco em tudo dentro da sidebar com especificidade maior que uma classe
   sozinha — a versão sem escopo/!important perde essa disputa de
   especificidade e o rótulo sai branco em vez do cinza discreto pretendido. */
section[data-testid="stSidebar"] .rmc-nav-grupo-label {{
    color: #C9D4E0 !important;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin: 18px 4px 6px;
}}

/* Botão primário (fora da sidebar): navy preenchido, texto branco */
div.stButton > button {{
    background: var(--navy);
    color: #FFFFFF;
    border: 1px solid var(--navy);
    border-radius: 8px;
    font-weight: 600;
}}
div.stButton > button:hover {{
    background: #123055;
    border-color: #123055;
    color: #FFFFFF;
}}
div.stDownloadButton > button {{
    background: #FFFFFF;
    color: var(--navy);
    border: 1px solid var(--navy);
    border-radius: 8px;
    font-weight: 600;
}}
div.stDownloadButton > button:hover {{
    background: var(--fundo-card);
}}

/* Botão de breadcrumb (Explorador de Arquivos): secundário/discreto.
   Reconstruído do zero com o seletor de ATRIBUTO validado (ver comentário
   do bloco de navegação da sidebar acima) — nem a versão da referência
   (seletor de classe .stElementContainer) nem a versão anterior deste
   arquivo (sem intermediário nenhum) tinham comprovação de que casam com o
   DOM real do Streamlit 1.60; confirmado por inspeção via Playwright na
   tela Dados. */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.bc-btn) div.stButton > button {{
    background: var(--fundo-card);
    color: var(--navy);
    border: 1px solid var(--borda);
    font-weight: 500;
}}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.bc-btn-ativo) div.stButton > button {{
    background: #FFFFFF;
    color: var(--navy);
    border: 1px solid var(--navy);
    font-weight: 700;
}}

/* Botão secundário/discreto reutilizável (ex: "Detalhes" nas tabelas de
   Análise de Oportunidade) — mesma cor de marca (navy), só sem o
   preenchimento sólido dos botões primários, pra não competir visualmente
   com ações principais numa tabela de muitas linhas. Mesmo seletor de
   atributo validado acima (reconstruído do zero pela mesma razão). */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.btn-secundario) div.stButton > button {{
    background: var(--fundo-card);
    color: var(--navy);
    border: 1px solid var(--borda);
    font-weight: 500;
}}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.btn-secundario) div.stButton > button:hover {{
    background: #FFFFFF;
    border-color: var(--navy);
}}
/* Centraliza o botão dentro da coluna (por padrão o Streamlit alinha o
   container do botão à esquerda da coluna). Importante: o botão em si
   (`<button>`) já vem com "flex-grow: 1" do próprio Streamlit, então forçar
   o container (stElementContainer) a 100% de largura faz o BOTÃO esticar
   junto — vira um botão largo, não um botão pequeno centralizado. A forma
   certa é manter o container do tamanho do conteúdo e só mudar sua POSIÇÃO
   no eixo cruzado do flex da coluna ("align-self: center" em vez do
   "align-items: stretch" herdado do bloco vertical pai). */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.btn-secundario) div[data-testid="stElementContainer"]:has(div.stButton) {{
    align-self: center;
}}

/* Botão de destaque com glow (usar no máximo 1 por tela — marcador
   .btn-destaque). Mesmo seletor de atributo validado acima (reconstruído
   do zero pela mesma razão; sem uso ativo em nenhuma tela hoje, então não
   deu pra confirmar via DOM real — só por analogia com o padrão validado). */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.btn-destaque) div.stButton > button {{
    background: var(--verde-escuro);
    border-color: var(--verde-escuro);
}}
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] div.btn-destaque) div.stButton > button:hover {{
    box-shadow: 0 0 0 4px rgba(141, 198, 63, 0.28);
}}

/* Cabeçalho em mesh gradient */
.rmc-header {{
    border-radius: 14px;
    padding: 28px 32px;
    margin-bottom: 20px;
    background:
        radial-gradient(circle at 15% 20%, rgba(141,198,63,0.55), transparent 45%),
        radial-gradient(circle at 85% 15%, rgba(74,110,144,0.55), transparent 45%),
        radial-gradient(circle at 50% 100%, rgba(99,153,34,0.45), transparent 55%),
        var(--navy);
    color: #FFFFFF;
}}
.rmc-header h1 {{
    font-size: 1.75rem;
    font-weight: 700;
    margin: 0 0 4px 0;
    color: #FFFFFF;
}}
.rmc-header p {{
    margin: 0;
    color: #E6ECF3;
}}

/* Cards de KPI */
.rmc-kpi {{
    background: #FFFFFF;
    border: 1px solid var(--borda);
    border-radius: 12px;
    padding: 18px 20px;
}}
.rmc-kpi .valor {{
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--navy);
}}
.rmc-kpi .label {{
    color: var(--texto-muted);
    font-size: 0.85rem;
}}

/* Card de destaque de oportunidade — preenchimento navy sólido */
.rmc-destaque {{
    background: var(--navy);
    color: #FFFFFF;
    border-radius: 12px;
    padding: 20px 24px;
}}
.rmc-destaque .valor {{
    font-size: 1.9rem;
    font-weight: 700;
    color: var(--verde);
}}
.rmc-destaque .label {{
    color: #C9D4E0;
    font-size: 0.9rem;
}}

/* Badge de status (pílula) */
.rmc-badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
}}
.rmc-badge-sugestao {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}
.rmc-badge-sucesso {{ background: {STATUS['sucesso']['bg']}; color: {STATUS['sucesso']['fg']}; }}
.rmc-badge-alterado {{ background: {STATUS['alterado']['bg']}; color: {STATUS['alterado']['fg']}; }}
.rmc-badge-multiplo {{ background: {STATUS['multiplo']['bg']}; color: {STATUS['multiplo']['fg']}; }}
.rmc-badge-neutro {{ background: {STATUS['neutro']['bg']}; color: {STATUS['neutro']['fg']}; }}
.rmc-badge-inativo {{ background: {STATUS['sugestao']['bg']}; color: {STATUS['sugestao']['fg']}; }}

/* Texto secundário/metadado */
.rmc-muted {{ color: var(--texto-muted); font-size: 0.85rem; }}

/* Loja + CNPJ embaixo do produto na visão Por Produto: 2px menor que o
   texto da tabela (pedido de 24/09/2026), no mesmo cinza dos metadados. */
.rmc-sub-loja {{ color: var(--texto-muted); font-size: calc(1em - 2px); }}

/* Valor negativo (ex: diferença por unidade quando a loja já paga menos) */
.rmc-negativo {{ color: #B3261E; font-weight: 600; }}

/* Faixa do filtro principal (Laboratório) nas telas de Análise de
   Oportunidade: destaca o filtro que "libera" a análise sem criar um
   componente novo — mesmo branco/borda dos cards, com o verde da marca só
   na borda esquerda, e compacta (uma linha só). Seletor por key do
   container, que o Streamlit expõe como classe "st-key-<key>". */
div[class*="st-key-faixa-laboratorio"] {{
    background: #FFFFFF;
    border: 1px solid var(--borda);
    border-left: 4px solid var(--verde);
    border-radius: 12px;
    /* Padding e altura mínima fixos: sem laboratório cadastrado a faixa só
       tem texto (sem o seletor), e ficava rasa, com o subtítulo colado na
       borda de baixo. Assim ela tem a mesma altura com ou sem seletor. */
    padding: 14px 18px 16px;
    min-height: 76px;
    justify-content: center;
    margin-bottom: 6px;
}}
.rmc-faixa-titulo {{ font-weight: 700; color: var(--navy); font-size: 0.95rem; line-height: 1.3; }}
.rmc-faixa-sub {{ color: var(--texto-muted); font-size: 0.78rem; line-height: 1.3; margin-top: 2px; }}

/* Cabeçalho ordenável das tabelas (clique ordena; seta mostra o sentido):
   botões sem cara de botão — texto navy em negrito, como os cabeçalhos
   fixos de antes; o sublinhado no hover é a pista de que é clicável. */
div[class*="st-key-cabecalho-"] div.stButton > button {{
    background: transparent;
    border: none;
    color: var(--navy);
    padding: 0;
    min-height: 0;
    justify-content: flex-start;
    text-align: left;
    box-shadow: none;
}}
div[class*="st-key-cabecalho-"] div.stButton > button:hover {{
    background: transparent;
    color: var(--verde-escuro);
    text-decoration: underline;
}}
div[class*="st-key-cabecalho-"] div.stButton > button p {{ font-weight: 700; }}

/* Nunca usar cor padrão vermelho/rosa do Streamlit em elementos de marca */
div.stButton > button:focus:not(:active) {{
    box-shadow: none;
}}

/* Abas (st.tabs): nunca o vermelho/rosa padrão do Streamlit */
[data-testid="stTab"] {{
    color: var(--texto-muted);
}}
[data-testid="stTab"][aria-selected="true"] {{
    color: var(--navy) !important;
    font-weight: 600;
}}
[data-testid="stTab"] .react-aria-SelectionIndicator {{
    background-color: var(--navy) !important;
}}

/* Selectbox (st.selectbox, Streamlit 1.62 = ComboBox do react-aria): a borda
   de foco vem vermelha por padrão — navy, como o resto da marca. Seletor
   validado por inspeção do DOM real (Playwright): o contorno é desenhado no
   `div[role=group]`, que ganha data-focus-within ao focar. */
[data-testid="stSelectbox"] div[role="group"][data-focus-within="true"] {{
    border-color: var(--navy) !important;
}}

/* Checkbox e toggle (st.checkbox / st.toggle): nunca o vermelho/rosa padrão
   do Streamlit. O input real fica visualmente escondido (clip) e o desenho
   do controle é feito pela div logo depois do <span> que embrulha o input —
   por isso o seletor estrutural (span + div), em vez de depender de classes
   com hash que mudam a cada build do Streamlit. */
div[data-testid="stCheckbox"] label > span + div {{
    border-color: var(--navy) !important;
}}
div[data-testid="stCheckbox"]:has(input:checked) label > span + div {{
    background: var(--navy) !important;
    border-color: var(--navy) !important;
}}
</style>
"""


def aplicar_tema() -> None:
    st.markdown(_construir_css(), unsafe_allow_html=True)


def cabecalho(titulo: str, subtitulo: str = "") -> None:
    st.markdown(
        f"""<div class="rmc-header"><h1>{titulo}</h1><p>{subtitulo}</p></div>""",
        unsafe_allow_html=True,
    )


def badge(texto: str, tipo: str = "sucesso") -> str:
    return f'<span class="rmc-badge rmc-badge-{tipo}">{texto}</span>'


def nav_grupo_label(texto: str) -> None:
    """Rótulo de grupo na sidebar (ex: 'Análise de Oportunidade',
    'Administração') — separa visualmente os itens de navegação por seção."""
    st.markdown(f'<div class="rmc-nav-grupo-label">{texto.upper()}</div>', unsafe_allow_html=True)


def kpi(label: str, valor: str) -> None:
    st.markdown(
        f"""<div class="rmc-kpi"><div class="valor">{valor}</div><div class="label">{label}</div></div>""",
        unsafe_allow_html=True,
    )


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
header[data-testid="stHeader"] {{ display: none !important; }}
section[data-testid="stSidebar"] {{ display: none !important; }}
div[data-testid="stAppViewContainer"] {{
    background: linear-gradient(135deg, var(--verde) 0%, var(--navy) 100%);
}}

iframe[data-testid="stIFrame"] {{
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
