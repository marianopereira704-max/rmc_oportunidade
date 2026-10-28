"""A regra das dicas (balões que aparecem ao passar o mouse) — 22/10/2026.

Antes, cada tela escrevia a sua: umas com parágrafos, outras numa linha só com "·" e "—", umas sem ponto
final, balões de três cores e tamanhos (o do navegador, o da tabela, o do gráfico). Esta é a regra única; o
teste `tests/test_dicas.py` varre o código e quebra se alguma dica nova fugir dela.

TEXTO
1. Parágrafos separados por uma linha em branco (`dica("...", "...")` monta isso). Até 4, de preferência 2: o
   1º diz o que é; o 2º, como é calculado ou o que fazer.
2. Frases completas: começam em maiúscula (ou número) e terminam em ponto, "!" ou "?". Nada de fragmento
   solto nem de "..." no fim.
3. No máximo 220 caracteres por parágrafo. Passou disso, quebre em dois.
4. Sem "·" nem "—" como separador (use ponto ou dois-pontos), sem "(s)" para plural (escreva o singular ou o
   plural certo) e sem a sigla "GPS": para quem usa o sistema é "sistema de vendas".
5. Lista curta de "Nome: valor." — uma por linha, cada uma já com ponto final (o balão do gráfico).
6. Dica de botão só com ícone é o nome da ação, curto (até 4 palavras, sem ponto): "Editar", "Tirar este filtro".

VISUAL — um balão só, `theme.BALAO_DICA` (fundo #1F2A37, 12 px, até 300 px de largura, cantos e sombra
iguais): `[data-dica]` na tela e dentro da tabela e o balão do gráfico. O `title=` do navegador não se usa
(aparece tarde, sem parágrafos e com outra cara). A exceção é o `help=` do Streamlit, que não dá para
trocar: o texto segue a regra e o CSS do balão dele (`dica_help`) imita o mesmo visual.
"""
from __future__ import annotations

import re

MAX_PARAGRAFOS = 4
MAX_CARACTERES = 220
MAX_PALAVRAS_ROTULO = 4

_FINAIS = (".", "!", "?")
_PROIBIDOS = (
    (" · ", 'o separador "·": use ponto ou dois-pontos'),
    (" — ", 'o travessão como separador: use ponto ou dois-pontos'),
    ("...", 'reticências no fim: escreva a frase inteira'),
    ("(s)", 'plural com "(s)": escreva o singular ou o plural certo'),
    ("  ", "espaço duplo"),
)


def _paragrafo(texto: str) -> str:
    """Um parágrafo limpo: espaços colapsados e ponto final garantido."""
    limpo = re.sub(r"[ \t]+", " ", str(texto)).strip()
    if limpo and not limpo.endswith(_FINAIS):
        limpo += "."
    return limpo


def dica(*paragrafos: str) -> str:
    """Monta a dica no padrão: cada argumento é um parágrafo (ou uma lista de linhas "Nome: valor." separadas
    por "\\n"); sai com espaços limpos, ponto final em cada linha e uma linha em branco entre parágrafos."""
    saida = []
    for p in paragrafos:
        if p is None:
            continue
        linhas = [_paragrafo(l) for l in str(p).split("\n")]
        linhas = [l for l in linhas if l]
        if linhas:
            saida.append("\n".join(linhas))
    return "\n\n".join(saida)


def rotulo(texto: str) -> bool:
    """Dica de botão de ícone: o nome da ação, curto e sem ponto."""
    t = texto.strip()
    return 0 < len(t.split()) <= MAX_PALAVRAS_ROTULO and "\n" not in t and not t.endswith(_FINAIS)


def problemas(texto: str) -> list[str]:
    """Tudo que a dica faz de errado pela regra acima; vazio = em ordem."""
    if texto is None or not str(texto).strip():
        return ["dica vazia"]
    if rotulo(texto):
        return []
    achados: list[str] = []
    paragrafos = str(texto).split("\n\n")
    if len(paragrafos) > MAX_PARAGRAFOS:
        achados.append(f"{len(paragrafos)} parágrafos (o máximo é {MAX_PARAGRAFOS})")
    for p in paragrafos:
        if not p.strip():
            achados.append("parágrafo vazio (linha em branco a mais)")
            continue
        if len(p) > MAX_CARACTERES:
            achados.append(f"parágrafo com {len(p)} caracteres (o máximo é {MAX_CARACTERES}): {p[:40]}...")
        for linha in p.split("\n"):
            if linha != linha.strip():
                achados.append(f"espaço sobrando no começo ou no fim: {linha!r}")
            l = linha.strip()
            if l and not (l[0].isupper() or l[0].isdigit() or l[0] in '"“('):
                achados.append(f"não começa em maiúscula: {l[:40]!r}")
            if l and not l.endswith(_FINAIS):
                achados.append(f"não termina em ponto: {l[-40:]!r}")
        for proibido, motivo in _PROIBIDOS:
            if proibido in p:
                achados.append(f"tem {motivo}")
        if re.search(r"\bGPS\b", p):
            achados.append('tem a sigla "GPS": escreva "sistema de vendas"')
    return achados


def qtd(n, um: str, varios: str | None = None) -> str:
    """"1 unidade" / "2 unidades" / "1.000 unidades": o número já formatado e o plural certo (nunca "unidade(s)")."""
    from core import ui
    valor = float(n)
    return f"{ui.formatar_numero(valor)} {um if valor == 1 else (varios or um + 's')}"


def atributo(texto) -> str:
    """O texto da dica pronto para `data-dica="..."` dentro de HTML do st.markdown: aspas escapadas, "\n" como
    entidade (uma linha em branco no atributo cortaria o bloco HTML do Markdown) e "$" como entidade (o Markdown
    leria "R$ ... R$" como fórmula)."""
    import html
    return html.escape(str(texto), quote=True).replace("\n", "&#10;").replace("$", "&#36;")
