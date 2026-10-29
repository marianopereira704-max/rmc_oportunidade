"""Classificação de produtos — a seção das Configurações de Pedidos (25/10/2026, C6 do plano em
docs/specs/2026-10-25-classificacao-produtos-plano.md). Substitui a tabela "Linhas de compra".

Duas peças:
- a CASCATA, só leitura: as 5 categorias fixas, cada uma com uma setinha no fim da barra que abre os setores da
  IQVIA que a compõem; cada setor abre as categorias da FEBRAFAR; cada uma, os tipos da CMED. Só aparece o que
  existe. Em `<details>` aninhados do próprio HTML: abrir e fechar não roda o script (montar tudo a cada clique
  travava a tela de Dados — mesma razão das filas);
- o pop-up "Editar correspondência": setor da IQVIA e tipo da CMED → categoria. Muda a linha de TODOS os produtos
  e o que os Compradores veem, por isso pede confirmação e grava no histórico das Configurações (quem e quando).

As contas são de `pedido/classificacao.py`; aqui só a tela.
"""
from __future__ import annotations

import dataclasses
import html

import streamlit as st

from core import theme, ui
from core.db import get_session
from pedido import classificacao as cl
from pedido import linhas as lc
from pedido.configuracao import ConfigPedido
from views import popups

_KEY = "classificacao_produtos"
_CONFIRMAR = f"{_KEY}_confirmar"
_FONTE = {cl.NIVEL_IQVIA: "IQVIA", cl.NIVEL_FEBRAFAR: "FEBRAFAR", cl.NIVEL_CMED: "CMED"}


# ---------------------------------------------------------------------------
# A cascata (HTML puro: testado em tests/test_classificacao_tela.py)
# ---------------------------------------------------------------------------

def _qtd(n: int) -> str:
    return f"{ui.formatar_numero(n)} EAN" + ("" if n == 1 else "s")


def _linha(no: dict, total: int | None) -> str:
    """O conteúdo de uma barra: a fonte (a partir do nível 2), o nome, a quantidade e, no nível 1, o % do total."""
    fonte = _FONTE.get(no["nivel"])
    etiqueta = f'<span class="fonte {no["nivel"]}">{fonte}</span>' if fonte else ""
    pct = (f' <span class="pct">({no["eans"] / total * 100:.1f}%)</span>'.replace(".", ",")
           if total and no["nivel"] == cl.NIVEL_CATEGORIA else "")
    return (f'{etiqueta}<span class="nome">{html.escape(str(no["rotulo"]))}</span>'
            f'<span class="qtd">{_qtd(no["eans"])}{pct}</span>')


def _no_html(no: dict, total: int | None = None) -> str:
    """Nó com filhos: `<details>` (a setinha no fim da barra abre). Sem filhos: uma barra sem setinha."""
    classe = f"nivel-{no['nivel']}"
    if not no["filhos"]:
        return f'<div class="folha {classe}">{_linha(no, total)}</div>'
    filhos = "".join(_no_html(f) for f in no["filhos"])
    return (f'<details class="{classe}"><summary>{_linha(no, total)}</summary>'
            f'<div class="filhos">{filhos}</div></details>')


def html_cascata(arvore: list[dict]) -> str:
    """A cascata inteira, numa peça só (sem recuo nem linha em branco: o Markdown não vira código)."""
    total = sum(n["eans"] for n in arvore)
    return f'<div class="rmc-cascata">{"".join(_no_html(n, total) for n in arvore)}</div>'


@st.cache_data(max_entries=2, show_spinner="Montando a classificação dos produtos...")
def _cascata(versao: tuple, por_setor: tuple, por_cmed: tuple) -> list[dict]:
    """A cascata, por versão das fontes e por mapa. Só a árvore fica em cache (poucos KB): o universo (~270 mil
    EANs) é lido, classificado e jogado fora — o Assistente e o Dashboard têm o próprio cache, só dos classificados."""
    from integrations import classificacao as dados

    with get_session() as session:
        universo = dados.universo(session)
    return cl.arvore(cl.classificar(universo, dict(por_setor), dict(por_cmed)), dict(por_cmed))


def _tipos_cmed(config: ConfigPedido) -> list[str]:
    """Os tipos da CMED que o banco conhece mais os que a configuração cita, do mais comum para o menos."""
    from integrations import classificacao as dados

    with get_session() as session:
        contagem = dados.contagem_por_cmed(session)
    vistos = {cl.chave_tipo(t) for t in contagem}
    extras = [t for t in config.linhas_por_cmed if cl.chave_tipo(t) not in vistos]
    return [t for t, _ in sorted(contagem.items(), key=lambda kv: -kv[1])] + extras


def categoria_do_tipo(config: ConfigPedido, tipo: str) -> str:
    """A categoria que a configuração dá a um tipo da CMED (comparando sem acento e sem caixa)."""
    por_chave = {cl.chave_tipo(k): v for k, v in config.linhas_por_cmed.items()}
    return por_chave.get(cl.chave_tipo(tipo), lc.SEM_LINHA)


def mudancas(antes: ConfigPedido, depois: ConfigPedido, tipos: list[str]) -> list[str]:
    """As correspondências que mudaram, em frases ("Prescrição de trade (similar): Similar → Propagado")."""
    saida = []
    for setor, descricao in lc.SETORES.items():
        a = antes.linhas_por_setor.get(setor, lc.PADRAO_POR_SETOR[setor])
        d = depois.linhas_por_setor.get(setor, lc.PADRAO_POR_SETOR[setor])
        if a != d:
            saida.append(f"IQVIA, {descricao}: {lc.ROTULOS[a]} → {lc.ROTULOS[d]}")
    for tipo in tipos:
        a, d = categoria_do_tipo(antes, tipo), categoria_do_tipo(depois, tipo)
        if a != d:
            saida.append(f"CMED, {tipo}: {lc.ROTULOS[a]} → {lc.ROTULOS[d]}")
    return saida


# ---------------------------------------------------------------------------
# A seção
# ---------------------------------------------------------------------------

def secao(config: ConfigPedido) -> None:
    """A seção inteira, na página Padrão das Configurações. `config` = a vigente."""
    from views import config_pedido
    from views.pedido import dados_loja as dados_pedido

    theme.titulo_secao("Classificação de produtos")
    config_pedido._explicacao(
        "as 5 categorias em que todo produto cai — Propagado, Genérico, Similar, Perfumaria e Em classificação. "
        "É a linha de compra do Comprador no Assistente e a do Dashboard. Vale para todas as lojas.",
        "a categoria vem da classificação manual, senão do setor da IQVIA, senão do tipo da CMED (só Genérico e "
        "Similar); sem nenhuma, \"Em classificação\". A FEBRAFAR não decide: aparece só para identificar o produto. "
        "Clique na setinha de uma barra para ver o que a compõe.",
    )
    with st.container(key=f"{_KEY}-cascata"):
        arvore = _cascata(dados_pedido._versao_catalogo(), tuple(sorted(config.linhas_por_setor.items())),
                          tuple(sorted(config.linhas_por_cmed.items())))
        if not any(n["eans"] for n in arvore):
            st.info("Nenhuma fonte carregada ainda. Carregue o catálogo IQVIA e as fontes da classificação em "
                    "Dados → Importar → Categorias de produtos.")
        else:
            st.markdown(html_cascata(arvore), unsafe_allow_html=True)
    if st.button("Editar correspondência", icon=":material/edit:", key=f"{_KEY}_editar"):
        st.session_state.pop(_CONFIRMAR, None)
        _dialog_editar(config)


@popups.popup("Editar correspondência", "curto")
def _dialog_editar(config: ConfigPedido) -> None:
    from core import auth
    from views import config_pedido

    opcoes = list(cl.CATEGORIAS)
    st.caption("Escolha a categoria de cada setor da IQVIA e de cada tipo da CMED. \"Em classificação\" = não "
               "classifica: o produto segue para a próxima fonte.")
    popups.secao("Setores da IQVIA")
    por_setor = {}
    for setor, descricao in lc.SETORES.items():
        atual = config.linhas_por_setor.get(setor, lc.PADRAO_POR_SETOR[setor])
        por_setor[setor] = st.selectbox(descricao, opcoes, index=opcoes.index(atual), format_func=lc.ROTULOS.get,
                                        key=f"{_KEY}_setor_{setor}")
    popups.secao("Tipos da CMED")
    tipos = _tipos_cmed(config)
    por_cmed = {}
    for tipo in tipos:
        por_cmed[tipo] = st.selectbox(tipo, opcoes, index=opcoes.index(categoria_do_tipo(config, tipo)),
                                      format_func=lc.ROTULOS.get, key=f"{_KEY}_cmed_{cl.chave_tipo(tipo)}")

    nova = dataclasses.replace(config, linhas_por_setor=por_setor, linhas_por_cmed=por_cmed)
    lista = mudancas(config, nova, tipos)
    aviso = st.empty()
    confirmar = st.session_state.get(_CONFIRMAR) == lista and bool(lista)
    if confirmar:
        aviso.warning("Isto muda a linha de todos os produtos e o que os Compradores veem, em todas as lojas:\n\n"
                      + "\n".join(f"- {m}" for m in lista))
    cancelar, salvar = popups.botoes("Confirmar e salvar" if confirmar else "Salvar correspondência",
                                     chave=f"{_KEY}_rodape")
    if cancelar:
        st.session_state.pop(_CONFIRMAR, None)
        st.rerun()
    if salvar:
        if not lista:
            aviso.info("Nada mudou.")
            return
        erros = nova.erros()
        if erros:
            aviso.error("\n".join(erros))
            return
        if not confirmar:
            st.session_state[_CONFIRMAR] = lista       # a 1ª vez mostra o que muda; a 2ª grava
            st.rerun(scope="fragment")
        st.session_state.pop(_CONFIRMAR, None)
        config_pedido._salvar(nova, auth.usuario_atual()["nome"])
        st.rerun()
