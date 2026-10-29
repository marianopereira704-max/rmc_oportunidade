"""Assistente de pedido — sugestão de compra de uma loja, a partir das
vendas, compras e estoque que a rotina da madrugada traz da API do GPS
(pacote `pedido/`). Na tela o nome é "Assistente de pedido" (01/10/2026);
por dentro (código, tabelas, pastas do Spaces) continua "pedido".

Regras do cálculo: pedido/calculo.py. De onde vêm os dados: pedido/pronto.py
(Spaces) + base de categorias + Base Genéricos (banco).

Fluxo ao abrir a loja (grill-me de 01/10/2026), nesta ordem:
1. pop-up "Estoque negativo" — corrigir manualmente ou considerar todos zero;
2. pop-up "Produtos sem classificação" — informativo, "Entendi";
   os dois aparecem UMA vez por foto do GPS, por usuário + loja
   (`pedido_avisos_vistos`);
3. loja com personalização (Configurações de Pedidos): valem as regras dela,
   sempre; sem personalização, o padrão. Um texto discreto no topo diz qual
   ("Regras desta loja" / "Regras padrão"). Até 29/10/2026 eram pílulas
   Personalizado × Padrão, e a lista só aparecia depois da escolha.

Layout (02/10/2026): a loja vem da BARRA LATERAL (views/loja_barra.py),
que vale pra todas as telas; aqui, o título "Assistente de pedido" (a data
dos dados foi pro pé da barra), pílulas de
parâmetros, avisos contra pedido duplicado, 4 cards (do PEDIDO INTEIRO),
linha de filtros (busca, Categoria, Fabricante, Ocultar giro baixo — sem
pop-up desde 01/10/2026), tabela ("a revisar" no topo, em âmbar) e rodapé
fixo (Salvo automaticamente · hh:mm, Descartar, Listas (N), Exportar
selecionados (N)). O botão "Estoque negativo" saiu (o pop-up ao abrir a loja
já pergunta); "Listas" saiu do topo e virou o botão do rodapé, com as abas
"Salvar como lista" e "Listas salvas". Cada edição e marcação grava na hora
(integrations/pedido_area.py), por usuário + loja.

Desempenho: ler a loja do Spaces e calcular fica em cache por loja (5 min),
pela versão das categorias/genéricos e pelos parâmetros escolhidos.
Marcar, editar, filtrar e paginar só releem a área de trabalho (1 consulta
pequena) e aplicam em memória (~25 ms na Hudson, medido em 29/09/2026).

Arquivos (28/10/2026 — o pedido.py único tinha 1.130 linhas):
- `dados_loja.py`: configuração, versões, classificação e o cálculo da loja
  com cache — o que o Dashboard e as Configurações também usam;
- `estado.py`: o que a tela lê do banco sobre a loja (`_EstadoLoja`) e as
  notificações;
- `formato.py`: números, moeda, datas e a identificação da loja;
- `tabela_pedido.py`: colunas, células, dicas e os 4 cards;
- `avisos.py`: pop-ups ao abrir a loja e o rótulo de quais regras valem;
- `filtros.py`: a linha de filtros acima da tabela;
- `dialogos.py`: Estoque negativo, Listas, Descartar e Exportar;
- este arquivo: a tela (título, fluxo ao abrir a loja, rodapé).
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from core import acesso, analise, auth, dicas, theme, ui
from core.db import get_session
from pedido import calculo
from pedido import categorias as cat
from pedido import configuracao as cfg
from views import analise_comum as comum
from views import loja_barra
from views import tabela as tb
from views.pedido.avisos import _alerta_negativos, _alerta_sem_classificacao, _regras_usadas
from views.pedido.dados_loja import _calcular, _categorias_da_loja, _versoes, configuracao, parametros
from views.pedido.dialogos import _dialog_descartar, _dialog_exportar, _dialog_listas, _dialog_negativos
from views.pedido.estado import (
    _ABRIR_NEGATIVOS,
    _KEY,
    _ORDEM_PADRAO,
    _atualizar_area,
    _avisar_uma_vez,
    _estado_loja,
    _notificar,
)
from views.pedido.filtros import _linha_filtros
from views.pedido.formato import _hora_local
from views.pedido.tabela_pedido import _colunas, _indicadores, _linha

def render() -> None:
    with theme.tela("pedido"):
        _render()


def _titulo(linhas_comprador=None) -> None:
    """Só o título: a data dos dados foi pro pé da barra lateral (02/10/2026).
    Comprador: as linhas dele na MESMA linha do título, discretas (07/10/2026)
    — numa linha embaixo, empurravam a tela 34 px e o Assistente dele saía
    com outro respiro que o de todo mundo."""
    titulo = '<div class="rmc-pedido-titulo">Assistente de pedido</div>'
    if linhas_comprador:
        from integrations import usuarios

        nomes = ", ".join(usuarios.LINHAS_COMPRA[c] for c in usuarios.LINHAS_COMPRA if c in linhas_comprador)
        dica = dicas.dica("Você vê só os produtos das suas linhas.",
                          "Produto sem linha definida aparece para todos os compradores.")
        titulo = (f'<div class="rmc-pedido-cab">{titulo}<span class="rmc-pedido-linhas dica-abaixo" '
                  f'data-dica="{dicas.atributo(dica)}">'
                  f'Linhas de compra: {html.escape(nomes)}</span></div>')
    st.markdown(titulo, unsafe_allow_html=True)


def _render() -> None:
    from integrations import pedido_area

    usuario = auth.usuario_atual()
    # Resultado de uma ação (lista salva, exportação…): notificação, logo no
    # início — antes de qualquer saída antecipada da tela.
    mensagem = st.session_state.pop(f"{_KEY}_msg", None)
    if mensagem:
        _notificar(*mensagem)
    # A loja vem da barra lateral (02/10/2026). "Todas" não carrega: a
    # sugestão, o estoque e a exportação são de uma loja por vez.
    escolha = loja_barra.escolha(usuario)
    loja = escolha.loja
    if not loja_barra.lojas_do_usuario(usuario):
        st.info("Nenhuma loja ligada ao seu usuário. Peça ao administrador para liberar a sua loja.")
        return

    if loja is None or not loja.get("empresa_gps"):
        if loja is None:
            st.info("Escolha uma loja na barra lateral: o assistente trabalha com uma loja por vez.")
        else:
            st.info("Loja sem vínculo com o GPS: o sistema ainda não sabe qual loja do GPS é esta. "
                    "O administrador liga as duas em Dados → Pendências → Vínculo de lojas GPS.")
        return

    uid = usuario["id"] if usuario.get("id") is not None else -1
    estado = _estado_loja(uid, usuario["nome"], loja["id"])
    correcoes, area, listas = estado.correcoes, estado.area, estado.listas
    avisos, vistos, personalizacao = estado.avisos, estado.vistos, estado.personalizacao
    padrao = configuracao()
    # Loja com personalização: valem as regras dela, sempre (29/10/2026 — antes, pílulas Personalizado × Padrão).
    config = cfg.aplicar(padrao, personalizacao) if personalizacao else padrao
    p = parametros(config)
    marcador = loja_barra.marcador_estoque(loja["empresa_gps"]) or {}
    resultado = _calcular(loja["empresa_gps"], loja["loja_gps"], config.meses_fechados, _versoes(), p,
                          tuple(sorted((l, c.estoque) for l, c in correcoes.items())),
                          foto=f"{marcador.get('data')}|{marcador.get('gerado_em')}")
    if resultado is None:
        st.info("Os dados desta loja ainda não foram coletados. A rotina da madrugada traz as vendas, as "
                "compras e o estoque do GPS; a loja aparece aqui no dia seguinte à primeira coleta.")
        return
    todas, meta = resultado
    todas = calculo.aplicar_area(todas, area.itens)
    # Comprador: só as linhas de compra dele (etapa 4, 07/10/2026). Cortado
    # AQUI, antes de tudo: o resto não entra na lista, nos cards, no
    # orçamento, nos pop-ups nem na exportação (Q9 B). Proprietário: tudo.
    # A linha de cada produto é a categoria fixa da cascata de classificação
    # (25/10/2026, pedido/classificacao.py): manual, IQVIA, CMED, senão "Em
    # classificação" — que aparece para todos os compradores. Com o mapa do
    # PADRÃO: a classificação é do produto, não se personaliza por loja.
    linhas_comprador = usuario.get("linhas") if usuario["nivel"] == acesso.COMPRADOR else None
    if linhas_comprador:
        from pedido import classificacao as classif
        todas = classif.filtrar(todas, linhas_comprador, _categorias_da_loja(todas, padrao))
    ocultar = st.session_state.get(f"{_KEY}_giro", True)
    pedido = calculo.lista(todas, ocultar_giro_baixo=ocultar)
    # Só os do PEDIDO: a loja inteira tinha 60 produtos com EAN negativo na
    # Hudson, a maioria fora da lista (estoque acima do máximo).
    negativos = pedido[pedido["negativo"] | pedido["estoque_corrigido"]]
    n_negativos = int(negativos["negativo"].sum())
    n_sem = int((pedido["grupo"] == cat.SEM_CLASSIFICACAO).sum()) if not pedido.empty else 0

    with st.container(key="pedido-topo"):
        _titulo(linhas_comprador)
    # Um pop-up por vez (Streamlit): o que estiver pendente abre, na ordem
    # combinada — negativos, sem classificação. Sem botão "Estoque negativo"
    # (02/10/2026): corrigir é pelo pop-up, uma vez por foto do GPS.
    foto = meta["data_foto"]
    if st.session_state.pop(_ABRIR_NEGATIVOS, False):
        _dialog_negativos(loja, negativos, meta, usuario)
    elif n_negativos and vistos.get(pedido_area.AVISO_NEGATIVO) != foto:
        _alerta_negativos(uid, loja["id"], foto, n_negativos)
    elif n_sem and vistos.get(pedido_area.AVISO_SEM_CLASSIFICACAO) != foto:
        _alerta_sem_classificacao(uid, loja["id"], foto, n_sem, p.dias_sem_classificacao)

    _regras_usadas(config if personalizacao else None, padrao)

    for aviso in avisos:
        _avisar_uma_vez(loja["id"], "warning", aviso)
    sem_vendas, sem_compras = meta.get("dias_sem_vendas", []), meta.get("dias_sem_compras", [])
    if sem_vendas:
        _avisar_uma_vez(loja["id"], "warning",
                        f"{len(sem_vendas)} dia(s) sem vendas coletadas (a demanda usa só os dias com dado).")
    if sem_compras:
        _avisar_uma_vez(loja["id"], "info", f"Compras de {len(sem_compras)} dia(s) ainda não coletadas — nesses "
                                            "dias, o preço pode vir do custo de cadastro.")

    _indicadores(pedido, p)
    busca, categorias, fabricantes, status = _linha_filtros(loja["id"], todas)
    linhas = calculo.filtrar(todas, busca=busca, ocultar_giro_baixo=ocultar, categorias=categorias,
                             fabricantes=fabricantes, status=status)

    ui.resetar_pagina_se_filtro_mudou(
        _KEY, f"{loja['id']}|{busca}|{categorias}|{fabricantes}|{status}|{ocultar}")
    ordem = st.session_state.get(f"{_KEY}_ordem")
    if ordem is None:
        # Padrão (Q14 de 01/10/2026): os "a revisar" primeiro, depois o que
        # mais vende em valor; "Venda pontual" (desmarcados) no fim — Q14 de
        # 05/10/2026. Clicar num título troca pela ordem da coluna.
        ordem = _ORDEM_PADRAO
        ordenadas = linhas.sort_values(["venda_pontual", "a_revisar", "valor"], ascending=[True, False, False],
                                       kind="stable")
    else:
        ordenadas = analise.ordenar(linhas, *ordem)
    fatia, pagina = comum.pagina(ordenadas, _KEY)

    def _editar(linha: str, texto: str) -> None:
        texto = texto.strip()
        if not texto.isdigit():
            st.session_state[f"{_KEY}_msg"] = ("error", "Quantidade não gravada: use um número inteiro, 0 ou maior.")
            return
        # Só a linha digitada (antes: um dicionário das ~5 mil linhas a cada clique).
        achada = todas.loc[todas["linha"] == linha, "sugestao"]
        sugestao = int(achada.iloc[0]) if not achada.empty else 0
        with get_session() as s2:
            pedido_area.alterar_quantidade(s2, uid, loja["id"], linha, int(texto), sugestao)
        _atualizar_area(uid, loja["id"], lambda itens: pedido_area.com_quantidade(itens, linha, int(texto), sugestao))

    def _marcar(marcacoes: dict[str, bool]) -> None:
        with get_session() as s2:
            pedido_area.marcar(s2, uid, loja["id"], marcacoes)
        _atualizar_area(uid, loja["id"], lambda itens: pedido_area.com_marcacoes(itens, marcacoes))

    tb.tabela(
        f"{_KEY}_tabela", _colunas(p), [_linha(r, p) for r in fatia.to_dict("records")], ordem,
        ao_ordenar=lambda coluna: comum.alternar_ordem(_KEY, coluna.campo, coluna.numerica),
        vazio=("Nenhum produto para pedir", "O estoque desta loja cobre o ideal de todos os produtos com esses filtros"),
        densa=True, ao_editar=_editar,
        selecao=dict(zip(fatia["linha"], fatia["selecionado"])), ao_marcar=_marcar,
    )
    if not linhas.empty:
        ui.controles_paginacao(pagina, _KEY)

    _rodape(loja, usuario, pedido, todas, area, listas)


def _rodape(loja: dict, usuario: dict, pedido: pd.DataFrame, todas: pd.DataFrame, area, listas: list) -> None:
    """Rodapé do Pedido. Fica fixo no pé da tela (CSS `st-key-rodape-pedido`
    em core/theme.py) — os botões ficam DENTRO da faixa fixa, nada clicável
    embaixo dela (regra da identidade visual: sticky sobre elemento clicável
    já causou clique que não funcionava)."""
    marcados = pedido[pedido["selecionado"] & (pedido["quantidade"] > 0)]
    with st.container(key="rodape-pedido"):
        # Botões na largura natural, alinhados à direita: em colunas iguais,
        # "Descartar alterações" quebrava em 2 linhas em 1280 px (29/09/2026).
        esq, dir_ = st.columns([1, 3.4], vertical_alignment="center")
        if area.atualizado_em is not None:
            # ✓ do mesmo tamanho e cinza-esverdeado apagado: diz "está salvo"
            # sem chamar atenção (Q8 de 02/10/2026).
            esq.markdown(f'<span class="rmc-muted"><span class="rmc-salvo">✓</span> Salvo automaticamente · '
                         f'{_hora_local(area.atualizado_em)}</span>', unsafe_allow_html=True)
        else:
            esq.markdown('<span class="rmc-muted">Segue a sugestão</span>', unsafe_allow_html=True)
        # "Ocultar giro baixo" saiu daqui pra linha de filtros (01/10/2026).
        with dir_.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
            if st.button("Descartar alterações", key=f"{_KEY}_descartar", disabled=not area.itens,
                         icon=":material/delete:"):
                _dialog_descartar(loja, usuario, len(area.itens))
            # "Listas (N)": o nº avisa, sem abrir nada, que já há pedido montado
            # pra esta loja (era a função do botão do topo, que saiu).
            if st.button(f"Listas ({len(listas)})" if listas else "Listas", key=f"{_KEY}_listas",
                         icon=":material/list_alt:"):
                _dialog_listas(loja, usuario, pedido, listas, area.lista_aberta_id, bool(area.itens))
            # Habilitado mesmo sem marcados: "Só giro baixo" e "Tabela
            # completa" não dependem da seleção (01/10/2026).
            if st.button(f"Exportar selecionados ({ui.formatar_numero(len(marcados))})", key=f"{_KEY}_exportar",
                         icon=":material/download:", type="primary", disabled=todas.empty):
                _dialog_exportar(loja, pedido, todas, usuario)
