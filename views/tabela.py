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
- clique no botão da linha → gatilho `acao` com o id da linha;
- linhas selecionáveis (opcional, 22/10/2026 — Dashboard · Estoque): clique na linha → gatilho `linha`
  com o id e se o Ctrl/⌘ estava apertado. A tela decide o que é a seleção (substitui, soma, tira) e
  devolve a lista; o componente deixa as não selecionadas apagadas, como no BI.

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

from core import theme

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


def entrada(valor: int, classe: str | None = None, dica: str | None = None) -> dict:
    """Campo numérico (inteiro ≥ 0) dentro da célula — a quantidade do
    Pedido. Mudou o número (Enter ou sair do campo): `ao_editar(id da linha,
    texto digitado)` no Python. Classe "alterada": o valor não é o sugerido."""
    p = {"t": "", "entrada": int(valor)}
    if classe:
        p["c"] = classe
    if dica:
        p["dica"] = dica
    return p


def pilha(pedacos: list[dict]) -> list[dict]:
    """Linha de tags empilhadas (uma embaixo da outra) — a coluna Status do
    Pedido, quando há mais de 2 tags."""
    if pedacos:
        pedacos[0] = {**pedacos[0], "pilha": True}
    return pedacos


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
    centro: bool = False    # quantidades centralizadas (Assistente de pedido, 02/10/2026)
    numerica: bool = False  # 1º clique: número do maior pro menor, texto de A a Z
    dica: str | None = None  # "?" ao lado do título, com esta explicação
    icone: str | None = None  # ícone à esquerda do conteúdo de cada célula: "local" | "pessoa"
    # Celular (29/10/2026, etapa 6): onde a coluna aparece quando a tabela fica estreita e a tela pediu
    # `celular=` em `tabela()`. "titulo" (nome no topo do cartão), "etiquetas", "dado" (rótulo + valor, meia
    # largura), "dado_largo", "acao" (a quantidade, embaixo à esquerda), "destaque" (o número principal, embaixo à
    # direita) ou None (só no PC). No modo "tabela" (poucas colunas), qualquer papel = a coluna fica.
    celular: str | None = "dado"
    rotulo_celular: str | None = None  # o rótulo no cartão, quando difere do título da coluna
    ordem_celular: int = 0             # a ordem dos dados no cartão (menor primeiro); 0 = a da tabela
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
  /* --borda-linha: a tela pode deixar a divisória mais clara (o Assistente
     de pedido usa #EEF0F3, 02/10/2026); sem ela, a borda padrão. */
  border-top: 1px solid var(--borda-linha, var(--borda, #E4E7EB));
}
.vazio { grid-column: 1 / -1; }
.linha.corpo:hover { background: rgba(23, 55, 94, 0.03); }
/* Linha em destaque (`"classe": "revisar"` na linha): "a revisar" do
   Assistente de pedido — âmbar, não vermelho (vermelho é erro; 01/10/2026).
   Barra por box-shadow: não ocupa largura, o grid não se mexe. */
/* Desde 02/10/2026 só a barra na borda esquerda, sem o fundo bege: com
   muitas linhas "a revisar" o fundo virava uma massa de cor (a tag "A
   revisar" no Status já diz o motivo). */
.linha.corpo.revisar { box-shadow: inset 3px 0 0 #D29B3A; }
/* `"classe": "destaque"`: a loja escolhida na barra lateral, na Por Loja
   (visão da rede, 02/10/2026) — azul claro, a cor de "selecionado". */
.linha.corpo.destaque { background: #EEF4FB; box-shadow: inset 3px 0 0 var(--navy, #17375E); }
.linha.corpo.destaque:hover { background: #E4EDF8; }
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
.cel.centro { text-align: center; white-space: nowrap; }
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
/* Situação do estoque no Pedido (29/09/2026), em escala de gravidade:
   negativo (vermelho) > ruptura (laranja-avermelhado) > ruptura próxima
   (âmbar, o par .selo-sugestao). Azul = alterado à mão (par "alterado
   manualmente" da identidade visual). */
.selo-perigo { background: #FDE2E1; color: #9B1C1C; }
.selo-ruptura { background: #FEE4D2; color: #9A3412; }
.selo-alterado { background: #DCE9F7; color: #123A63; }
/* "A revisar" (02/10/2026): âmbar de atenção só no CONTORNO, fundo branco —
   não se confunde com "Ruptura próxima" (âmbar cheio). Contorno por sombra:
   borda de verdade mudaria a altura da tag. */
.selo-revisar { background: #FFFFFF; color: #854F0B; box-shadow: inset 0 0 0 1px #D29B3A; }
.txt-vermelho { color: #B3261E; font-weight: 600; }
.txt-laranja { color: #C2410C; font-weight: 600; }
.txt-ambar { color: #B45309; font-weight: 600; }
/* Verde do "bom" (nível de serviço da Ruptura, 27/10/2026): o mesmo do selo de sucesso. */
.txt-verde { color: #27500A; font-weight: 600; }
/* Tags empilhadas: com mais de 2, uma embaixo da outra (pedido de 29/09). */
.pilha > span { display: table; }
.pilha > span + span { margin-top: 3px; }

/* Nome de produto com "palavra" longa (SERING.DESC.DESCARPACK): pode quebrar
   dentro da palavra, senão a coluna não respeita o fit-content e a tabela
   do Pedido passava da borda em 1280 px (29/09/2026). Só nessa peça — em
   toda célula, o grid espremia os nomes letra a letra (ver .cel). */
.quebra { overflow-wrap: anywhere; }

/* Coluna de seleção */
.cel.sel { justify-self: start; }
.marca { width: 16px; height: 16px; margin: 0; cursor: pointer; accent-color: var(--navy, #17375E); }

/* Campo de quantidade (Pedido). Azul = alterado à mão (par "alterado
   manualmente" da identidade visual). Sem as setas do campo numérico. */
.entrada {
  width: 64px; box-sizing: border-box; padding: 4px 8px; text-align: right;
  font: inherit; font-weight: 600; color: var(--navy, #17375E);
  background: #FFFFFF; border: 1px solid var(--borda, #E4E7EB); border-radius: var(--raio-sm, 6px);
  -moz-appearance: textfield;
}
.entrada::-webkit-outer-spin-button, .entrada::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.entrada:hover { border-color: var(--navy-claro, #4A6E90); }
.entrada:focus { outline: 2px solid var(--navy, #17375E); outline-offset: 1px; }
.entrada.alterada { background: #DCE9F7; border-color: #B7CDE6; color: #123A63; }
/* Destaque pequeno da coluna Sugestão (pedido de 29/09/2026): fundo azul bem
   leve no campo; o alterado à mão continua no azul mais forte acima. */
.entrada.destaque:not(.alterada) { background: #F3F7FC; border-color: #D5E2F1; }

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

/* Dica: o balão único do app (theme.CSS_DICA, regra em core/dicas.py). Posições escolhidas pra nunca sair da
   área do componente: abaixo do "?" do cabeçalho, à esquerda do botão da última coluna. */
""" + theme.CSS_DICA + """

/* Linhas selecionáveis (Dashboard · Estoque, 22/10/2026): clicar filtra a página. A linha escolhida ganha o
   azul claro de "selecionado" e, havendo seleção, as outras ficam apagadas (como no BI). A linha de total
   (classe "total", e "selecionavel": false) nunca é selecionável nem apaga. */
.linha.corpo.clicavel { cursor: pointer; user-select: none; }
.linha.corpo.clicavel:focus-visible { outline: 2px solid var(--navy-claro, #4A6E90); outline-offset: -2px; }
.linha.corpo.clicavel > .cel { transition: opacity .15s; }
.linha.corpo.apagada > .cel { opacity: .35; }
.linha.corpo.selecionada { background: #EEF4FB; box-shadow: inset 3px 0 0 var(--navy, #17375E); }
.linha.corpo.selecionada:hover { background: #E4EDF8; }
.linha.corpo.total { font-weight: 700; background: var(--fundo-card, #F7F8FA); border-top: 2px solid var(--borda, #E4E7EB); }

/* Linhas que abrem (26/10/2026, Dashboard · Estoque): a setinha no FIM da linha abre as de dentro (`pai`), que
   ficam recuadas pelo `nivel` e em texto auxiliar. Abrir e fechar é só do navegador: não roda o script. */
.linha.corpo.oculta { display: none; }
.linha.corpo.sub > .cel { font-size: var(--fs-aux, 13px); color: var(--texto-suporte, #3F4750); }
.linha.corpo.sub { background: #FBFCFD; }
.abrir {
  display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0;
  border: 0; border-radius: var(--raio-sm, 6px); background: transparent; color: var(--navy, #17375E); cursor: pointer;
}
.abrir:hover { background: var(--fundo-card, #F7F8FA); }
.abrir:focus-visible { outline: 2px solid var(--navy, #17375E); outline-offset: 1px; }
.abrir svg { width: 16px; height: 16px; transition: transform .15s ease; }
.abrir[aria-expanded="true"] svg { transform: rotate(180deg); }
@media (prefers-reduced-motion: reduce) { .linha.corpo.clicavel > .cel { transition: none; } }

/* === CELULAR (29/10/2026, etapa 6) — só com `celular=` na tabela e a tabela estreita (classe "estreita", posta
   pelo JS abaixo de 600 px de largura). No PC nada daqui vale: o que é só do celular nasce com display: none, e
   elemento escondido não ocupa coluna do grid. === */
.so-celular, .passo { display: none; }
.estreita .so-pc { display: none; }
.estreita .cel > div.so-celular { display: block; }
.estreita span.so-celular { display: inline; }

/* Modo "tabela": só as colunas com papel, em vãos menores. */
.estreita.modo-tabela .quadro { grid-template-columns: var(--colunas-celular); column-gap: var(--esp-3, 12px); }
.estreita.modo-tabela .linha { padding-left: var(--esp-3, 12px); padding-right: var(--esp-3, 12px); }
.estreita.modo-tabela .cel[data-papel="fora"] { display: none; }

/* Modo "cartão": um cartão por linha. O cartão é o resumo; o botão do canto abre o detalhe. */
.estreita.modo-cartao .quadro { display: flex; flex-direction: column; gap: var(--esp-2, 8px); border: 0;
  background: transparent; }
.estreita.modo-cartao .linha.cabecalho { display: none; }
.estreita.modo-cartao .linha.corpo {
  /* Sem vão entre as duas colunas: a linha que separa a quantidade e o subtotal sai inteira (com vão, picotava). */
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--esp-2, 8px) 0;
  position: relative; padding: var(--esp-3, 12px); border: 1px solid var(--borda, #E4E7EB);
  border-radius: var(--raio-lg, 12px); background: #FFFFFF;
}
.estreita.modo-cartao .linha.corpo:hover { background: #FFFFFF; }
.estreita.modo-cartao .linha.corpo.com-marca { padding-left: 48px; }
.estreita.modo-cartao .linha.corpo.com-botao > .cel[data-papel="titulo"] { padding-right: 40px; }
.estreita.modo-cartao .cel { order: var(--ordem, 30); text-align: left; white-space: normal; justify-self: stretch; }
.estreita.modo-cartao .cel[data-papel="fora"] { display: none; }
.estreita.modo-cartao .cel[data-papel="titulo"] { grid-column: 1 / -1; order: 1; font-size: 15px; }
.estreita.modo-cartao .cel[data-papel="etiquetas"] { grid-column: 1 / -1; order: 2; }
.estreita.modo-cartao .cel[data-papel="dado_largo"] { grid-column: 1 / -1; }
.estreita.modo-cartao .cel[data-papel="dado"]::before,
.estreita.modo-cartao .cel[data-papel="dado_largo"]::before,
.estreita.modo-cartao .cel[data-papel="destaque"]::before {
  content: attr(data-rotulo); display: block; font-size: var(--fs-min, 12px); color: var(--texto-suporte, #3F4750);
}
.estreita.modo-cartao .cel[data-papel="acao"] { order: 90; align-self: end; }
.estreita.modo-cartao .cel[data-papel="destaque"] { order: 91; text-align: right; align-self: end; grid-column: 2; }
.estreita.modo-cartao .cel[data-papel="destaque"] > div:first-of-type { font-size: 16px; font-weight: 700; color: var(--navy, #17375E); }
.estreita.modo-cartao .cel[data-papel="dado"] { padding-right: var(--esp-2, 8px); }
/* Uma linha só, acima da quantidade e do subtotal (a linha em cima de cada dado ficava picotada). */
.estreita.modo-cartao .cel[data-papel="acao"],
.estreita.modo-cartao .cel[data-papel="destaque"] { padding-top: var(--esp-2, 8px); border-top: 1px solid #EEF0F3; }
/* Cartão sem quantidade (Por Produto): o destaque na linha inteira, alinhado à direita, com a linha inteira em cima. */
.estreita.modo-cartao .linha.corpo:not(:has(.cel[data-papel="acao"])) > .cel[data-papel="destaque"] { grid-column: 1 / -1; }
.estreita.modo-cartao .pilha > span { display: inline-block; margin: 0 4px 4px 0; }
.estreita.modo-cartao .pilha > span + span { margin-top: 0; }
.estreita.modo-cartao .cel.sel { position: absolute; left: 12px; top: 14px; }
.estreita.modo-cartao .marca { width: 22px; height: 22px; }
.estreita.modo-cartao .cel.botao.so-celular { display: block; position: absolute; right: 8px; top: 8px; }
.estreita.modo-cartao .cel.botao:not(.so-celular) { position: absolute; right: 8px; top: 8px; }
.estreita.modo-cartao .acao { width: 36px; height: 36px; }
/* Quantidade com − e + (dedo): botões de 44 px; o campo continua digitável. */
.estreita.modo-cartao .passo { display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px;
  padding: 0; border: 1px solid var(--borda, #E4E7EB); background: var(--fundo-card, #F7F8FA);
  color: var(--navy, #17375E); font: inherit; font-size: 20px; cursor: pointer; }
.estreita.modo-cartao .passo.menos { border-radius: 10px 0 0 10px; }
.estreita.modo-cartao .passo.mais { border-radius: 0 10px 10px 0; }
.estreita.modo-cartao .entrada { height: 44px; width: 56px; border-radius: 0; border-left: 0; border-right: 0;
  text-align: center; font-size: 16px; }
.estreita.modo-cartao .com-passos { display: inline-flex; align-items: center; }
/* === FIM DO CELULAR === */

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

function campoEntrada(p, aoEditar, passos) {
  const campo = el("input", "entrada " + (p.c || ""));
  campo.type = "number"; campo.min = "0"; campo.step = "1"; campo.inputMode = "numeric";
  campo.value = String(p.entrada);
  campo.setAttribute("aria-label", "Quantidade");
  const original = String(p.entrada);
  campo.onkeydown = (ev) => { if (ev.key === "Enter") campo.blur(); };
  campo.onchange = () => { if (campo.value !== original) aoEditar(campo.value); };
  if (p.dica) campo.title = p.dica;
  if (!passos) return campo;
  // Celular: − e + em volta do campo (escondidos no PC). Toques seguidos somam na tela e gravam UMA vez, 0,7 s
  // depois do último — cada gravação roda a página de novo.
  const grupo = el("span", "com-passos");
  let espera = null;
  const passo = (classe, texto, rotulo, delta) => {
    const b = el("button", "passo " + classe, texto);
    b.type = "button";
    b.setAttribute("aria-label", rotulo);
    b.onclick = () => {
      campo.value = String(Math.max(0, (parseInt(campo.value, 10) || 0) + delta));
      clearTimeout(espera);
      espera = setTimeout(() => { if (campo.value !== original) aoEditar(campo.value); }, 700);
    };
    return b;
  };
  grupo.append(passo("menos", "−", "Diminuir a quantidade", -1), campo, passo("mais", "+", "Aumentar a quantidade", 1));
  return grupo;
}

const so = (linha, classe) => linha.length && linha.every(p => (" " + (p.c || "") + " ").includes(" " + classe + " "));

function montarCelula(linhas, aoEditar, passos) {
  const frag = document.createDocumentFragment();
  for (const linha of (linhas || [])) {
    const div = el("div");
    if (linha.length && linha[0].pilha) div.className = "pilha";
    // Linha só do celular (ou só do PC): a linha inteira some, não só o texto — senão sobrava o vão entre linhas.
    if (so(linha, "so-celular")) div.classList.add("so-celular");
    if (so(linha, "so-pc")) div.classList.add("so-pc");
    linha.forEach((p, i) => {
      if (i && !div.classList.contains("pilha")) div.appendChild(document.createTextNode(" "));
      if (p.entrada !== undefined && aoEditar) { div.appendChild(campoEntrada(p, aoEditar, passos)); return; }
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
  const selecao = data.selecao || null;   // {id: true|false} — coluna de checkbox
  if (selecao) trilhas.unshift("auto");
  // "auto", não "32px": no subgrid o padding lateral da linha entra como
  // margem das colunas das pontas — com 32px fixos, o botão (32px) invadia o
  // vão de Economia em 16px (medido pela checagem de vãos em 25/09/2026).
  if (data.acao) trilhas.push("auto");
  // Linhas que abrem: uma coluna a mais, no fim, para a setinha. O que está aberto fica guardado no próprio
  // elemento (sobrevive ao redesenho de um clique em outra parte da tela).
  const comArvore = linhas.some(l => l.filhos);
  if (comArvore) trilhas.push("auto");
  const abertos = raiz.__abertos || (raiz.__abertos = new Set());
  const paiDe = new Map(linhas.map(l => [String(l.id), l.pai != null ? String(l.pai) : null]));
  const visivel = (id) => {
    for (let p = paiDe.get(id); p; p = paiDe.get(p)) if (!abertos.has(p)) return false;
    return true;
  };
  raiz.style.setProperty("--colunas", trilhas.join(" "));
  raiz.classList.toggle("densa", !!data.densa);
  // Celular (29/10/2026): o modo que a tela pediu e, abaixo de 600 px de largura, a classe "estreita" — pela
  // largura da PRÓPRIA tabela (o pop-up de detalhe no celular também é estreito). Sem `celular`, nada muda.
  const modo = data.celular || null;
  raiz.classList.toggle("modo-cartao", modo === "cartao");
  raiz.classList.toggle("modo-tabela", modo === "tabela");
  const doCelular = colunas.filter(c => c.celular).map(c => c.largura || "auto");
  if (selecao) doCelular.unshift("auto");
  if (data.acao) doCelular.push("auto");
  if (linhas.some(l => l.filhos)) doCelular.push("auto");
  raiz.style.setProperty("--colunas-celular", doCelular.join(" "));
  const medir = () => raiz.classList.toggle("estreita", !!modo && raiz.getBoundingClientRect().width < 600);
  if (!raiz.__observador) {
    raiz.__observador = new ResizeObserver(() => raiz.__medir && raiz.__medir());
    raiz.__observador.observe(raiz);
  }
  raiz.__medir = medir;
  medir();
  const papel = (cel, c) => {
    cel.dataset.papel = c.celular || "fora";
    cel.dataset.rotulo = c.rotulo_celular || c.rotulo;
    if (c.ordem_celular) cel.style.setProperty("--ordem", String(c.ordem_celular));
  };
  const detalheCelular = !data.acao && data.detalhe_celular ? data.detalhe_celular : null;

  const quadro = el("div", "quadro");
  quadro.setAttribute("role", "table");
  // Linhas selecionáveis: a tela inteira é redesenhada a cada clique (o Python devolve a seleção nova); quem
  // navega por teclado perderia o foco. Guarda qual linha o tinha e devolve depois.
  const selecionaveis = !!data.linhas_selecionaveis;
  const marcadas = new Set((data.linhas_selecionadas || []).map(String));
  const comFoco = raiz.__foco || null;
  raiz.__reconstruindo = true;

  if (!linhas.length) {
    const vazio = el("div", "vazio");
    vazio.appendChild(el("div", "selo"));
    vazio.appendChild(el("div", "t", data.vazio_titulo || "Nada encontrado"));
    if (data.vazio_texto) vazio.appendChild(el("div", "s", data.vazio_texto));
    quadro.appendChild(vazio);
    raiz.replaceChildren(quadro);
    raiz.__reconstruindo = false;
    return;
  }

  const cab = el("div", "linha cabecalho");
  cab.setAttribute("role", "row");
  if (selecao) {
    // Marca/desmarca a PÁGINA (as linhas desta tabela) — Q3b de 29/09/2026.
    const ids = linhas.map(l => l.id);
    const marcadas = ids.filter(i => selecao[i]).length;
    const cel = el("div", "cel sel");
    const caixa = el("input", "marca");
    caixa.type = "checkbox";
    caixa.checked = ids.length > 0 && marcadas === ids.length;
    caixa.indeterminate = marcadas > 0 && marcadas < ids.length;
    caixa.setAttribute("aria-label", "Marcar todos desta página");
    caixa.title = "Marcar/desmarcar todos desta página";
    caixa.onchange = () => setTriggerValue("marcar", { ids, valor: caixa.checked });
    cel.appendChild(caixa);
    cab.appendChild(cel);
  }
  for (const c of colunas) {
    const cel = el("div", "cel" + (c.direita ? " direita" : "") + (c.centro ? " centro" : ""));
    cel.setAttribute("role", "columnheader");
    papel(cel, c);
    const ativa = data.ordem && data.ordem[0] === c.campo;
    const titulo = el(c.campo ? "button" : "span", "titulo");
    titulo.appendChild(el("span", c.rotulo_celular ? "so-pc" : "", c.rotulo));
    // Título curto no celular (modo "tabela"): "Estoque a preço de compra" não cabe em 390 px.
    if (c.rotulo_celular) titulo.appendChild(el("span", "so-celular", c.rotulo_celular));
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
      (c.rotulo_celular ? titulo.children[1] : titulo.firstChild).after(ajuda);
    }
    cab.appendChild(cel);
  }
  if (data.acao) cab.appendChild(el("div", "cel"));
  if (comArvore) cab.appendChild(el("div", "cel"));
  quadro.appendChild(cab);
  const elementos = new Map();

  for (const l of linhas) {
    const linha = el("div", "linha corpo" + (l.classe ? " " + l.classe : ""));
    linha.setAttribute("role", "row");
    if (selecao) linha.classList.add("com-marca");
    if (data.acao || detalheCelular) linha.classList.add("com-botao");
    elementos.set(String(l.id), linha);
    if (l.pai != null) {
      linha.classList.add("sub");
      if (!visivel(String(l.id))) linha.classList.add("oculta");
      // As de dentro apagam junto com a categoria de cima quando ela não está entre as escolhidas.
      if (marcadas.size && l.grupo != null && !marcadas.has(String(l.grupo))) linha.classList.add("apagada");
    }
    if (selecionaveis && l.selecionavel !== false) {
      const escolhida = marcadas.has(String(l.id));
      linha.classList.add("clicavel");
      if (escolhida) linha.classList.add("selecionada");
      else if (marcadas.size) linha.classList.add("apagada");
      linha.dataset.id = String(l.id);
      linha.tabIndex = 0;
      linha.setAttribute("aria-selected", escolhida ? "true" : "false");
      const clicar = (soma) => setTriggerValue("linha", { id: l.id, soma, t: Date.now() });
      linha.onclick = (ev) => { if (!ev.target.closest("button, input, a")) clicar(!!(ev.ctrlKey || ev.metaKey)); };
      // Linha não é botão: Enter/Espaço (com Ctrl/⌘ soma) precisam do tratamento do teclado.
      linha.onkeydown = (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); clicar(!!(ev.ctrlKey || ev.metaKey)); }
      };
      linha.onfocus = () => { raiz.__foco = String(l.id); };
      linha.onblur = () => { if (!raiz.__reconstruindo) raiz.__foco = null; };
    }
    if (selecao) {
      const cel = el("div", "cel sel");
      const caixa = el("input", "marca");
      caixa.type = "checkbox";
      caixa.checked = !!selecao[l.id];
      caixa.setAttribute("aria-label", "Incluir no pedido");
      caixa.onchange = () => setTriggerValue("marcar", { ids: [l.id], valor: caixa.checked });
      cel.appendChild(caixa);
      linha.appendChild(cel);
    }
    colunas.forEach((c, ic) => {
      const cel = el("div", "cel" + (c.direita ? " direita" : "") + (c.centro ? " centro" : ""));
      cel.setAttribute("role", "cell");
      papel(cel, c);
      if (ic === 0 && l.nivel) cel.style.paddingLeft = (l.nivel * 20) + "px";
      if (c.icone && ICONES_CEL[c.icone]) {
        cel.classList.add("com-icone");
        cel.appendChild(svg(ICONES_CEL[c.icone], "icone-cel"));
        const texto = el("div", "texto");
        texto.appendChild(montarCelula(l.celulas[c.id]));
        cel.appendChild(texto);
      } else {
        cel.appendChild(montarCelula(l.celulas[c.id], (valor) => setTriggerValue("editar", { id: l.id, valor }),
                                     modo === "cartao"));
      }
      linha.appendChild(cel);
    });
    if (detalheCelular) {
      // Detalhe só do celular (o Assistente no PC mostra tudo na linha e nas dicas; no celular não há "passar o
      // mouse"). display: none no PC: não ocupa coluna do grid.
      const cel = el("div", "cel botao so-celular");
      const botao = el("button", "acao");
      botao.type = "button";
      botao.setAttribute("aria-label", detalheCelular);
      botao.appendChild(svg(SETA_DIREITA));
      botao.onclick = () => setTriggerValue("acao", l.id);
      cel.appendChild(botao);
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
    if (comArvore) {
      const cel = el("div", "cel botao");
      if (l.filhos) {
        const id = String(l.id);
        const botao = el("button", "abrir");
        botao.type = "button";
        botao.setAttribute("aria-expanded", abertos.has(id) ? "true" : "false");
        botao.setAttribute("aria-label", abertos.has(id) ? "Fechar detalhamento" : "Abrir detalhamento");
        botao.appendChild(svg(SETA_BAIXO));
        botao.onclick = (ev) => {
          ev.stopPropagation();   // abrir não seleciona a linha
          if (abertos.has(id)) abertos.delete(id); else abertos.add(id);
          botao.setAttribute("aria-expanded", abertos.has(id) ? "true" : "false");
          botao.setAttribute("aria-label", abertos.has(id) ? "Fechar detalhamento" : "Abrir detalhamento");
          for (const [outro, elem] of elementos) if (paiDe.get(outro)) elem.classList.toggle("oculta", !visivel(outro));
        };
        cel.appendChild(botao);
      }
      linha.appendChild(cel);
    }
    quadro.appendChild(linha);
  }
  raiz.replaceChildren(quadro);
  raiz.__reconstruindo = false;
  if (comFoco) {
    const alvo = Array.from(raiz.querySelectorAll(".linha.clicavel")).find((x) => x.dataset.id === comFoco);
    if (alvo) alvo.focus({ preventScroll: true });
  }
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
    ao_editar: Callable[[str, str], None] | None = None,
    selecao: dict[str, bool] | None = None,
    ao_marcar: Callable[[dict[str, bool]], None] | None = None,
    selecao_linhas: list[str] | None = None,
    ao_clicar_linha: Callable[[str, bool], None] | None = None,
    celular: str | None = None,
    detalhe_celular: str | None = None,
) -> None:
    """Desenha a tabela.

    `linhas`: [{"id": str, "celulas": {coluna.id: celula(...)}}].
    `ordem`: (campo, crescente) atual — só pra desenhar a seta.
    `ao_ordenar(coluna)`: chamado no clique do título, antes do rerun.
    `acao`: texto da dica do botão de cada linha (None = sem botão). O id da
    linha clicada fica em `acao_clicada(key)` no rerun seguinte.
    `densa`: vão menor entre colunas (tabelas de 8+ colunas).
    `ao_editar(id da linha, texto)`: chamado quando um campo `entrada()`
    muda, antes do rerun.
    `selecao`: {id da linha: marcada?} — com ele, a 1ª coluna é um checkbox
    por linha e o do cabeçalho marca/desmarca a PÁGINA. `ao_marcar({id:
    valor})`: chamado no clique, antes do rerun.
    `selecao_linhas`: LIGA as linhas selecionáveis (22/10/2026): a lista de ids selecionados (vazia = nenhuma,
    mas as linhas já são clicáveis; None = recurso desligado, a tabela é a de sempre). `ao_clicar_linha(id,
    soma)`: chamado no clique numa linha, antes do rerun; `soma` é o Ctrl/⌘. Linha com `"selecionavel":
    False` (o total) não clica nem apaga; `"classe": "total"` a deixa em negrito.
    Linhas que abrem (26/10/2026): `"filhos": True` põe a setinha no fim da linha; a linha com `"pai": id` fica
    escondida até o pai (e os avós) estarem abertos, recuada `"nivel"` × 20 px; com `"grupo": id`, ela apaga junto
    quando há seleção e o grupo não está nela. Abrir e fechar não roda o script.
    Celular (29/10/2026, etapa 6): `celular="cartao"` (um cartão por linha, com os papéis de `ColunaTabela.celular`)
    ou `"tabela"` (só as colunas com papel), quando a tabela fica abaixo de 600 px; None = como sempre.
    `detalhe_celular`: dica do botão de detalhe que só aparece no cartão (a tabela sem `acao`); o clique chega por
    `acao_clicada`, como o botão de sempre. Pedaço com a classe "so-celular" só aparece no cartão; "so-pc", só fora
    dele."""
    por_campo = {c.campo: c for c in colunas if c.campo}

    def _ordenar() -> None:
        campo = st.session_state[key].ordenar
        if campo in por_campo:
            ao_ordenar(por_campo[campo])

    def _editar() -> None:
        valor = st.session_state[key].editar
        if ao_editar and isinstance(valor, dict) and valor.get("id") is not None:
            ao_editar(str(valor["id"]), str(valor.get("valor", "")))

    def _marcar() -> None:
        valor = st.session_state[key].marcar
        if ao_marcar and isinstance(valor, dict) and valor.get("ids"):
            ao_marcar({str(i): bool(valor.get("valor")) for i in valor["ids"]})

    def _acao() -> None:
        linha_id = st.session_state[key].acao
        if linha_id is not None:
            st.session_state[_chave_acao(key)] = linha_id

    def _linha() -> None:
        valor = st.session_state[key].linha
        if ao_clicar_linha and isinstance(valor, dict) and valor.get("id") is not None:
            ao_clicar_linha(str(valor["id"]), bool(valor.get("soma")))

    _COMPONENTE(
        key=key,
        data={
            "colunas": [
                {"id": c.id, "campo": c.campo, "rotulo": c.rotulo, "largura": c.largura,
                 "direita": c.direita, "centro": c.centro, "dica": c.dica, "icone": c.icone,
                 "celular": c.celular, "rotulo_celular": c.rotulo_celular, "ordem_celular": c.ordem_celular}
                for c in colunas
            ],
            "celular": celular,
            "detalhe_celular": detalhe_celular,
            "linhas": linhas,
            "ordem": list(ordem),
            "acao": acao,
            "vazio_titulo": vazio[0],
            "vazio_texto": vazio[1],
            "densa": densa,
            "selecao": selecao,
            "linhas_selecionaveis": selecao_linhas is not None,
            "linhas_selecionadas": list(selecao_linhas or []),
        },
        on_ordenar_change=_ordenar,
        on_acao_change=_acao,
        on_editar_change=_editar,
        on_marcar_change=_marcar,
        on_linha_change=_linha,
    )


def acao_clicada(key: str) -> str | None:
    """Id da linha cujo botão foi clicado — lido UMA vez (sai do estado), pra
    o popup não reabrir sozinho no próximo clique em outra parte da tela."""
    return st.session_state.pop(_chave_acao(key), None)
