"""Tabela das telas de análise (Custom Component v2) — um grid só para o
cabeçalho e para as linhas.

Por que componente e não `st.columns` por linha: com colunas do Streamlit,
cada linha é um bloco horizontal independente e o cabeçalho é outro; a
largura de cada célula depende do conteúdo e da quebra de linha, e o botão
"Detalhes" quebrava em "Detalhe/s" a 1280 px (medido na auditoria de
24/09/2026). Aqui o quadro é UM grid e cada linha é um subgrid dele: as
colunas são as mesmas em todas as linhas, e a largura de cada uma vem do
conteúdo de TODAS as linhas juntas.

Espaço entre colunas (padronizado em 25/09/2026): cada coluna tem a largura
do próprio conteúdo e a sobra vira vãos IGUAIS entre todas elas
(`justify-content: space-between`). Antes as larguras eram pesos fixos
(`2.1fr`, `0.8fr`…): Responsável ficava bem mais largo que os nomes e,
com Produtos alinhado à direita, sobrava um buraco entre os dois títulos.
O teste visual (tests/visual/rodar.py) mede os vãos de toda tabela e falha
se diferirem mais de 2 px ou se um título quebrar linha.

O componente só desenha e avisa cliques — ordenar e paginar continuam em
Python sobre o resultado em memória (core/analise.py), como antes:
- clique no título → gatilho `ordenar` com o campo da coluna;
- clique no botão da linha → gatilho `acao` com o id da linha.

Os textos vêm do banco (razão social, nomes): o JS monta tudo com
`textContent`, nunca `innerHTML` — nome de loja com "<" não vira HTML.

Estilo: CSS próprio, isolado (shadow DOM), mas usando os tokens de
core/theme.py (`--navy`, `--fs-corpo`, `--esp-3`…): variável CSS atravessa o
isolamento por herança. Os valores depois da vírgula em `var(--x, valor)` só
valem se o tema não carregar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import streamlit as st

# ---------------------------------------------------------------------------
# Montagem das células (Python → dados do componente)
# ---------------------------------------------------------------------------
# Uma célula é uma lista de LINHAS; cada linha, uma lista de PEDAÇOS
# {"t": texto, "c": classe, "dica": texto da dica}. Classes aceitas pelo CSS
# abaixo: forte, aux, negativo, inteiro, selo-sucesso, selo-neutro, selo-sugestao,
# unica (uma linha só, "…" no fim, texto inteiro na dica).


def pedaco(texto: str, classe: str | None = None, dica: str | None = None) -> dict:
    p = {"t": "" if texto is None else str(texto)}
    if classe:
        p["c"] = classe
    if dica:
        p["dica"] = dica
    return p


def celula(*linhas) -> list[list[dict]]:
    """Cada argumento é uma linha: um pedaço, ou uma lista de pedaços."""
    return [linha if isinstance(linha, list) else [linha] for linha in linhas]


@dataclass
class ColunaTabela:
    campo: str | None       # campo do DataFrame usado na ordenação; None = não ordena
    rotulo: str
    # Trilha CSS da coluna. Padrão "auto" = largura do conteúdo (título e
    # todas as células). Texto que pode ser longo (nome de loja/produto,
    # cidade, laboratório): "fit-content(Npx)" — do tamanho do texto até N px,
    # e quebra linha acima disso. Nunca largura fixa ou mínima: sobra espaço
    # dentro da coluna e o vão visível fica maior que os outros.
    largura: str = "auto"
    direita: bool = False   # números e valores alinham à direita
    numerica: bool = False  # 1º clique: número do maior pro menor, texto de A a Z
    dica: str | None = None  # "?" ao lado do título, com esta explicação
    icone: str | None = None  # ícone à esquerda do conteúdo de cada célula: "local" | "pessoa"
    id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.campo or self.rotulo


# ---------------------------------------------------------------------------
# Componente
# ---------------------------------------------------------------------------

_HTML = '<div class="rmc-tabela"></div>'

_CSS = """
:host { display: block; }
.rmc-tabela {
  font-size: var(--fs-corpo, 14px);
  color: #1F2A37;
  line-height: 1.45;
}
.quadro {
  border: 1px solid var(--borda, #E4E7EB);
  border-radius: var(--raio-lg, 12px);
  background: #FFFFFF;
  display: grid;
  grid-template-columns: var(--colunas);
  /* Vão MÍNIMO entre colunas; a sobra de largura vira vãos iguais. */
  column-gap: var(--esp-6, 32px);
  justify-content: space-between;
}
.linha {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: subgrid;
  word-break: normal;
  align-items: center;
  padding: var(--esp-3, 12px) var(--esp-4, 16px);
  border-top: 1px solid var(--borda, #E4E7EB);
}
.vazio { grid-column: 1 / -1; }
.linha.corpo:hover { background: rgba(23, 55, 94, 0.03); }
/* Tabela de muitas colunas (Por Produto, Detalhes): vão menor entre colunas,
   pra caber em 1280 px sem quebrar os títulos. */
/* 12px mínimos: com 16, a Por Produto real passava 4px da borda em 1280. */
.rmc-tabela.densa .quadro { column-gap: var(--esp-3, 12px); }
.rmc-tabela.densa .linha { padding-left: var(--esp-3, 12px); padding-right: var(--esp-3, 12px); }
.linha.cabecalho {
  border-top: 0;
  background: var(--fundo-card, #F7F8FA);
  border-radius: var(--raio-lg, 12px) var(--raio-lg, 12px) 0 0;
  padding-top: var(--esp-2, 8px);
  padding-bottom: var(--esp-2, 8px);
}
.linha.corpo:last-child { border-radius: 0 0 var(--raio-lg, 12px) var(--raio-lg, 12px); }
/* break-word (não "anywhere"): "anywhere" reduz a largura mínima da coluna
   a uma letra, e o grid espremia nomes letra a letra antes de apertar os
   vãos. Números e títulos de coluna numérica nunca quebram. Sem
   "min-width: 0": a coluna nunca fica mais estreita que o próprio conteúdo
   (com ele, em 1280 px o selo de Economia invadia o vão até o botão). */
.cel { overflow-wrap: break-word; }
.cel.direita { text-align: right; white-space: nowrap; }
.cabecalho .titulo { white-space: nowrap; }
.cel > div + div { margin-top: 2px; }
/* Célula com ícone (Localização, Responsável): ícone à esquerda, centrado
   no bloco de texto. */
.cel.com-icone { display: flex; align-items: center; gap: var(--esp-2, 8px); }
.cel.com-icone > .icone-cel { flex: none; width: 16px; height: 16px; color: var(--navy, #17375E); }
.cel.com-icone > .texto { min-width: 0; flex: 1; }
.cel.com-icone > .texto > div + div { margin-top: 2px; }

/* Cabeçalho */
.titulo {
  position: relative;
  display: inline-flex; align-items: center; gap: var(--esp-1, 4px);
  font: inherit; font-size: var(--fs-aux, 13px); font-weight: 600;
  color: var(--navy, #17375E); background: none; border: 0; padding: 0;
  text-align: inherit; max-width: 100%;
}
button.titulo { cursor: pointer; }
button.titulo:hover .seta { opacity: .55; }
button.titulo:focus-visible { outline: 2px solid var(--navy, #17375E); outline-offset: 2px; border-radius: 4px; }
/* A seta flutua ao lado do título, fora do fluxo: não ocupa largura. Antes
   ela ocupava espaço mesmo invisível — empurrava o título das colunas à
   direita pra longe dos valores e fazia "Diferença un." quebrar em 1280 px
   com 8 colunas. Coluna à direita: seta do lado esquerdo do título. */
.seta {
  position: absolute; top: 50%; left: calc(100% + 3px); transform: translateY(-50%);
  width: 10px; height: 10px; opacity: 0;
}
.cel.direita .seta { left: auto; right: calc(100% + 3px); }
.seta.ativa { opacity: 1 !important; }
.ajuda {
  display: inline-flex; width: 14px; height: 14px; flex: none; cursor: help;
  color: var(--texto-muted, #7A8699);
}

/* Conteúdo */
.forte { font-weight: 600; color: var(--navy, #17375E); }
.aux { font-size: var(--fs-aux, 13px); color: var(--texto-muted, #7A8699); }
.negativo { color: var(--negativo, #B3261E); }
/* Não quebra por dentro (CNPJ): quebrava em "00.778.919/0001-" / "00". */
.inteiro { white-space: nowrap; }
.unica {
  display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
[class*="selo-"] {
  display: inline-block; padding: 2px var(--esp-2, 8px);
  border-radius: var(--raio-pill, 999px);
  font-size: var(--fs-min, 12px); font-weight: 600; line-height: 1.4;
  white-space: nowrap;
}
.selo-sucesso { background: #EAF3DE; color: #27500A; }
.selo-neutro { background: #E7E9EC; color: #3F4750; }
.selo-sugestao { background: #FAEEDA; color: #854F0B; }

/* Botão da linha (Detalhes) */
.acao {
  display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; padding: 0; cursor: pointer;
  background: #FFFFFF; color: var(--navy, #17375E);
  border: 1px solid var(--borda, #E4E7EB); border-radius: var(--raio-sm, 6px);
}
.acao:hover { border-color: var(--navy, #17375E); background: var(--fundo-card, #F7F8FA); }
.acao:focus-visible { outline: 2px solid var(--navy, #17375E); outline-offset: 2px; }
.acao svg { width: 16px; height: 16px; }
.cel.botao { justify-self: end; }

/* Dica no estilo do BI: caixa escura, aparece ao passar o mouse. Posições
   escolhidas pra nunca sair da área do componente: abaixo do "?" do
   cabeçalho, à esquerda do botão da última coluna. */
[data-dica] { position: relative; }
[data-dica]:hover::after, [data-dica]:focus-visible::after {
  content: attr(data-dica);
  position: absolute; z-index: 20; pointer-events: none;
  width: max-content; max-width: 280px; white-space: normal; text-align: left;
  background: #1F2A37; color: #FFFFFF;
  font-size: var(--fs-min, 12px); font-weight: 400; line-height: 1.4;
  padding: 6px 10px; border-radius: var(--raio-sm, 6px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, .18);
}
[data-dica].dica-abaixo:hover::after { top: calc(100% + 6px); left: 50%; transform: translateX(-50%); }
[data-dica].dica-esquerda:hover::after, [data-dica].dica-esquerda:focus-visible::after {
  right: calc(100% + 8px); top: 50%; transform: translateY(-50%);
}
[data-dica].dica-acima:hover::after { bottom: calc(100% + 4px); left: 0; }

/* Estado vazio */
.vazio { padding: var(--esp-6, 32px) var(--esp-4, 16px); text-align: center; }
.vazio .selo {
  width: 48px; height: 48px; margin: 0 auto var(--esp-3, 12px); border-radius: 50%;
  background:
    radial-gradient(circle at 30% 30%, #8DC63F 0%, transparent 60%),
    radial-gradient(circle at 70% 70%, #4A6E90 0%, transparent 65%),
    #17375E;
}
.vazio .t { font-size: var(--fs-destaque, 16px); font-weight: 600; color: var(--navy, #17375E); }
.vazio .s { margin-top: var(--esp-1, 4px); font-size: var(--fs-aux, 13px); color: var(--texto-muted, #7A8699); }
"""

_JS = """
const NS = "http://www.w3.org/2000/svg";

function svg(caminho, classe, vb) {
  const s = document.createElementNS(NS, "svg");
  s.setAttribute("viewBox", vb || "0 0 16 16");
  s.setAttribute("fill", "none");
  s.setAttribute("aria-hidden", "true");
  if (classe) s.setAttribute("class", classe);
  s.innerHTML = caminho;  // só constantes deste arquivo, nunca dado do banco
  return s;
}
const SETA_BAIXO = '<path d="M3 6l5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
const SETA_CIMA = '<path d="M3 10l5-5 5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
const SETA_DIREITA = '<path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';
const ICONES_CEL = {
  local: '<path d="M8 14.5s4.5-4.2 4.5-8a4.5 4.5 0 0 0-9 0c0 3.8 4.5 8 4.5 8z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>' +
         '<circle cx="8" cy="6.5" r="1.6" stroke="currentColor" stroke-width="1.3"/>',
  pessoa: '<circle cx="8" cy="5" r="2.6" stroke="currentColor" stroke-width="1.3"/>' +
          '<path d="M2.8 14.2c.6-2.6 2.7-4.2 5.2-4.2s4.6 1.6 5.2 4.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
};
const AJUDA = '<circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.3"/>' +
  '<path d="M6.2 6.2a1.9 1.9 0 0 1 3.6.7c0 1.3-1.8 1.5-1.8 2.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>' +
  '<circle cx="8" cy="11.6" r=".8" fill="currentColor"/>';

function el(tag, classe, texto) {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (texto !== undefined && texto !== null) e.textContent = texto;
  return e;
}

function montarCelula(linhas) {
  const frag = document.createDocumentFragment();
  for (const linha of (linhas || [])) {
    const div = el("div");
    linha.forEach((p, i) => {
      if (i) div.appendChild(document.createTextNode(" "));
      const span = el("span", p.c || "", p.t);
      if (p.dica) { span.dataset.dica = p.dica; span.classList.add("dica-acima"); }
      div.appendChild(span);
    });
    frag.appendChild(div);
  }
  return frag;
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const raiz = parentElement.querySelector(".rmc-tabela");
  const colunas = data.colunas || [];
  const linhas = data.linhas || [];
  const trilhas = colunas.map(c => c.largura || "auto");
  // "auto", não "32px": no subgrid o padding lateral da linha entra como
  // margem das colunas das pontas — com 32px fixos, o botão (32px) invadia o
  // vão de Economia em 16px (medido pela checagem de vãos em 25/09/2026).
  if (data.acao) trilhas.push("auto");
  raiz.style.setProperty("--colunas", trilhas.join(" "));
  raiz.classList.toggle("densa", !!data.densa);

  const quadro = el("div", "quadro");
  quadro.setAttribute("role", "table");

  if (!linhas.length) {
    const vazio = el("div", "vazio");
    vazio.appendChild(el("div", "selo"));
    vazio.appendChild(el("div", "t", data.vazio_titulo || "Nada encontrado"));
    if (data.vazio_texto) vazio.appendChild(el("div", "s", data.vazio_texto));
    quadro.appendChild(vazio);
    raiz.replaceChildren(quadro);
    return;
  }

  const cab = el("div", "linha cabecalho");
  cab.setAttribute("role", "row");
  for (const c of colunas) {
    const cel = el("div", "cel" + (c.direita ? " direita" : ""));
    cel.setAttribute("role", "columnheader");
    const ativa = data.ordem && data.ordem[0] === c.campo;
    const titulo = el(c.campo ? "button" : "span", "titulo");
    titulo.appendChild(el("span", "", c.rotulo));
    if (c.campo) {
      titulo.type = "button";
      titulo.appendChild(svg(ativa && data.ordem[1] ? SETA_CIMA : SETA_BAIXO, "seta" + (ativa ? " ativa" : "")));
      titulo.onclick = () => setTriggerValue("ordenar", c.campo);
      if (ativa) cel.setAttribute("aria-sort", data.ordem[1] ? "ascending" : "descending");
    }
    cel.appendChild(titulo);
    if (c.dica) {
      const ajuda = el("span", "ajuda dica-abaixo");
      ajuda.dataset.dica = c.dica;
      ajuda.setAttribute("aria-label", c.dica);
      ajuda.appendChild(svg(AJUDA));
      // Colado no texto (entre o título e a seta); passar o mouse mostra a
      // dica, clicar nele não ordena.
      ajuda.onclick = (ev) => ev.stopPropagation();
      titulo.firstChild.after(ajuda);
    }
    cab.appendChild(cel);
  }
  if (data.acao) cab.appendChild(el("div", "cel"));
  quadro.appendChild(cab);

  for (const l of linhas) {
    const linha = el("div", "linha corpo");
    linha.setAttribute("role", "row");
    for (const c of colunas) {
      const cel = el("div", "cel" + (c.direita ? " direita" : ""));
      cel.setAttribute("role", "cell");
      if (c.icone && ICONES_CEL[c.icone]) {
        cel.classList.add("com-icone");
        cel.appendChild(svg(ICONES_CEL[c.icone], "icone-cel"));
        const texto = el("div", "texto");
        texto.appendChild(montarCelula(l.celulas[c.id]));
        cel.appendChild(texto);
      } else {
        cel.appendChild(montarCelula(l.celulas[c.id]));
      }
      linha.appendChild(cel);
    }
    if (data.acao) {
      const cel = el("div", "cel botao");
      const botao = el("button", "acao dica-esquerda");
      botao.type = "button";
      botao.dataset.dica = data.acao;
      botao.setAttribute("aria-label", data.acao);
      botao.appendChild(svg(SETA_DIREITA));
      botao.onclick = () => setTriggerValue("acao", l.id);
      cel.appendChild(botao);
      linha.appendChild(cel);
    }
    quadro.appendChild(linha);
  }
  raiz.replaceChildren(quadro);
}
"""

# Registrado uma vez por processo: mudou o HTML/CSS/JS acima, reinicie o
# `streamlit run` — só salvar o arquivo deixa o navegador com o JS antigo
# (visto em 25/09/2026 ao ajustar o cabeçalho).
_COMPONENTE = st.components.v2.component("rmc_tabela", html=_HTML, css=_CSS, js=_JS)


def _chave_acao(key: str) -> str:
    return f"_tabela_{key}_acao"


def tabela(
    key: str,
    colunas: list[ColunaTabela],
    linhas: list[dict],
    ordem: tuple[str, bool],
    ao_ordenar: Callable[[ColunaTabela], None],
    acao: str | None = None,
    vazio: tuple[str, str] = ("Nenhuma oportunidade encontrada", "Tente alterar os filtros"),
    densa: bool = False,
) -> None:
    """Desenha a tabela.

    `linhas`: [{"id": str, "celulas": {coluna.id: celula(...)}}].
    `ordem`: (campo, crescente) atual — só pra desenhar a seta.
    `ao_ordenar(coluna)`: chamado no clique do título, antes do rerun.
    `acao`: texto da dica do botão de cada linha (None = sem botão). O id da
    linha clicada fica em `acao_clicada(key)` no rerun seguinte.
    `densa`: vão menor entre colunas (tabelas de 8+ colunas)."""
    por_campo = {c.campo: c for c in colunas if c.campo}

    def _ordenar() -> None:
        campo = st.session_state[key].ordenar
        if campo in por_campo:
            ao_ordenar(por_campo[campo])

    def _acao() -> None:
        linha_id = st.session_state[key].acao
        if linha_id is not None:
            st.session_state[_chave_acao(key)] = linha_id

    _COMPONENTE(
        key=key,
        data={
            "colunas": [
                {"id": c.id, "campo": c.campo, "rotulo": c.rotulo, "largura": c.largura,
                 "direita": c.direita, "dica": c.dica, "icone": c.icone}
                for c in colunas
            ],
            "linhas": linhas,
            "ordem": list(ordem),
            "acao": acao,
            "vazio_titulo": vazio[0],
            "vazio_texto": vazio[1],
            "densa": densa,
        },
        on_ordenar_change=_ordenar,
        on_acao_change=_acao,
    )


def acao_clicada(key: str) -> str | None:
    """Id da linha cujo botão foi clicado — lido UMA vez (sai do estado), pra
    o popup não reabrir sozinho no próximo clique em outra parte da tela."""
    return st.session_state.pop(_chave_acao(key), None)
