"""Cadastro de usuários das lojas (aba Cadastro, 07/10/2026) — ADM e
Consultor. Regras: integrations/usuarios.py.

Desenho a partir dos prints do sistema de referência (grill de 06/10/2026):
- a lista segue a loja da barra lateral (Q24): com "Todas as lojas", nada
  aparece — pede uma loja. Mostra todo mundo que entra na loja, como
  principal ou adicional, e a coluna Loja traz TODAS as lojas de cada um
  (Q25: sem precisar abrir "Atribuir Empresas" pra saber);
- ações por linha: Editar, Atribuir Painéis, Atribuir Empresas, E-mail de
  ativação (só "Aguardando" — quem já criou a senha usa "Esqueci minha
  senha") e Excluir (desativa, com confirmação; "Mostrar excluídos" traz de
  volta pra reativar);
- Novo usuário: a loja é a da barra, só pra conferir (Q26). Ao cadastrar,
  o e-mail de ativação já sai, e o link aparece pra copiar (plano B, Q16).

A tabela é feita de colunas do Streamlit (não o componente de views/tabela.py):
são 5 botões por linha e poucos usuários por loja — a tabela das análises
tem um botão por linha e é feita pra milhares.
"""
from __future__ import annotations

import html

import streamlit as st

from core import acesso, auth, dicas, theme
from core.db import get_session
from views import loja_barra, popups

_KEY = "cadastro"
_MSG = f"{_KEY}_msg"
_RESULTADO = f"{_KEY}_resultado"     # resultado do envio do e-mail, mostrado no pop-up aberto
# Ações | Loja | Nome | Nível de acesso | E-mail | Status
_LARGURAS = [2.3, 2.3, 2.0, 1.8, 2.4, 1.3]   # Ações: 5 ícones de 28 px cabem em 1280 px
_STATUS = {"Aguardando": "sugestao", "Ativo": "sucesso", "Excluído": "neutro"}
_ICONE_MSG = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}


def render() -> None:
    with theme.tela("cadastro"):
        _render()


@st.cache_data(ttl=60, show_spinner=False)
def _usuarios_da_loja(loja_id: int, com_excluidos: bool) -> list[dict]:
    """A lista da loja, guardada por 60 s (07/10/2026): eram 3 consultas
    (~420 ms até NY) a cada letra digitada na busca e a cada clique. Toda
    gravação desta tela descarta o guardado (`_concluir`/`_limpar_resultado`);
    o que outra pessoa gravar aparece em até 60 s."""
    from integrations import usuarios

    with get_session() as session:
        return usuarios.da_loja(session, loja_id, com_excluidos)


def _concluir(tipo: str, texto: str) -> None:
    """Notificação no próximo redesenho (o pop-up fecha com o st.rerun) e a
    lista relida do banco."""
    st.session_state[_MSG] = (tipo, texto)
    _usuarios_da_loja.clear()
    # E-mails para as lojas (29/10/2026): quem recebe mudou — a lista vai para a rotina na hora.
    from integrations import insumos

    insumos.publicar_em_segundo_plano(forcar=True)


def _limpar_resultado() -> None:
    # Também ao fechar o pop-up do Novo pelo X: o usuário já foi gravado.
    st.session_state.pop(_RESULTADO, None)
    _usuarios_da_loja.clear()


def _abrir(*prefixos: str) -> None:
    """Antes de abrir um pop-up: os campos dele voltam ao valor do cadastro.
    Sem isso, o Streamlit guarda o que foi digitado e o pop-up cancelado
    reabria com a edição abandonada (ou o "Novo" com o usuário anterior)."""
    _limpar_resultado()
    for chave in [c for c in st.session_state if isinstance(c, str) and c.startswith(prefixos)]:
        del st.session_state[chave]


def _render() -> None:
    from integrations import usuarios

    # Sem a faixa de cabeçalho (pedido de 07/10/2026, como nas telas de
    # análise): a tela abre direto na busca e na lista.
    mensagem = st.session_state.pop(_MSG, None)
    if mensagem:
        st.toast(mensagem[1], icon=_ICONE_MSG.get(mensagem[0], "ℹ️"), duration=5)

    usuario = auth.usuario_atual()
    loja = loja_barra.escolha(usuario).loja
    if loja is None:
        st.info("Escolha uma loja na barra lateral para ver e cadastrar os usuários dela.")
        return

    with st.container(key=f"{_KEY}-topo", horizontal=True, vertical_alignment="center", gap="small"):
        busca = st.text_input("Pesquisar", placeholder="Pesquisar por nome ou e-mail", key=f"{_KEY}_busca",
                              label_visibility="collapsed", icon=":material/search:", width=320)
        com_excluidos = st.toggle("Mostrar excluídos", key=f"{_KEY}_excluidos")
        st.space("stretch")
        if st.button("Novo usuário", type="primary", icon=":material/add:", key=f"{_KEY}_novo"):
            _abrir(f"{_KEY}_novo_")
            _dialog_novo(loja)

    lista = _usuarios_da_loja(loja["id"], bool(com_excluidos))
    termo = (busca or "").strip().lower()
    if termo:
        lista = [r for r in lista if termo in r["nome"].lower() or termo in r["email"].lower()]

    st.caption(f"{len(lista)} usuário(s) com acesso a {loja_barra.rotulo(loja)}"
               + (" (incluindo os excluídos)" if com_excluidos else "") + ".")
    _tabela(lista, termo)


# ---------------------------------------------------------------------------
# Tabela
# ---------------------------------------------------------------------------

def _tabela(lista: list[dict], termo: str) -> None:
    with st.container(key=f"{_KEY}-tabela", gap=None):
        with st.container(key=f"{_KEY}-cab"):
            colunas = st.columns(_LARGURAS, vertical_alignment="center")
            for col, titulo in zip(colunas, ["Ações", "Loja", "Nome", "Nível de acesso", "E-mail", "Status"]):
                col.markdown(f'<div class="rmc-cad-cab">{titulo}</div>', unsafe_allow_html=True)
        if not lista:
            with st.container(key=f"{_KEY}-vazio"):
                st.caption("Nenhum usuário encontrado." if termo else
                           "Nenhum usuário nesta loja ainda. Use \"Novo usuário\" para cadastrar.")
            return
        for r in lista:
            _linha(r)


def _linha(r: dict) -> None:
    from integrations import usuarios

    excluido = r["status"] == "Excluído"
    with st.container(key=f"{_KEY}-linha-{r['id']}"):
        acoes, c_loja, c_nome, c_nivel, c_email, c_status = st.columns(_LARGURAS, vertical_alignment="center")
        with acoes.container(key=f"{_KEY}-acoes-{r['id']}", horizontal=True, gap=None):
            if st.button("", icon=":material/edit:", type="tertiary", key=f"{_KEY}_editar_{r['id']}",
                         help="Editar", disabled=excluido):
                _abrir(f"{_KEY}_{r['id']}_")
                _dialog_editar(r)
            if st.button("", icon=":material/bar_chart:", type="tertiary", key=f"{_KEY}_paineis_{r['id']}",
                         help="Atribuir Painéis", disabled=excluido):
                _abrir(f"{_KEY}_paineis_sel_{r['id']}")
                _dialog_paineis(r)
            if st.button("", icon=":material/apartment:", type="tertiary", key=f"{_KEY}_empresas_{r['id']}",
                         help="Atribuir Empresas", disabled=excluido):
                _abrir(f"{_KEY}_empresa_{r['id']}_")
                _dialog_empresas(r)
            pendente = r["status"] == "Aguardando"
            if st.button("", icon=":material/mail:", type="tertiary", key=f"{_KEY}_email_{r['id']}",
                         help="E-mail de ativação" if pendente else
                         dicas.dica("E-mail de ativação.", "Só vale para quem ainda não criou a senha. Os demais "
                                    "usam \"Esqueci minha senha\" no login."), disabled=not pendente):
                _abrir()
                _dialog_email(r)
            if excluido:
                if st.button("", icon=":material/restore_from_trash:", type="tertiary",
                             key=f"{_KEY}_reativar_{r['id']}", help="Reativar"):
                    try:
                        with get_session() as session:
                            usuarios.reativar(session, r["id"], auth.usuario_atual()["nome"])
                        _concluir("success", f"{r['nome']} reativado.")
                    except ValueError as exc:
                        _concluir("error", str(exc))
                    st.rerun()
            elif st.button("", icon=":material/delete:", type="tertiary", key=f"{_KEY}_excluir_{r['id']}",
                           help="Excluir"):
                _dialog_excluir(r)

        lojas = r["lojas"]
        principal = html.escape(lojas[0]) if lojas else "—"
        outras = "".join(f'<div class="rmc-cad-sub">{html.escape(l)}</div>' for l in lojas[1:])
        c_loja.markdown(f'<div class="rmc-cad-celula dica-abaixo" data-dica="{dicas.atributo(dicas.dica(*lojas))}">'
                        f'{principal}{outras}</div>',
                        unsafe_allow_html=True)
        cargo = f'<div class="rmc-cad-sub">{html.escape(r["cargo"])}</div>' if r["cargo"] else ""
        c_nome.markdown(f'<div class="rmc-cad-celula"><b>{html.escape(r["nome"])}</b>{cargo}</div>',
                        unsafe_allow_html=True)
        paineis = ", ".join(usuarios.PAINEIS[p] for p in usuarios.PAINEIS if p in r["paineis"])
        c_nivel.markdown(f'<div class="rmc-cad-celula dica-abaixo" data-dica="{dicas.atributo(dicas.dica("Painéis: " + (paineis or "nenhum")))}">'
                         f'{html.escape(usuarios.descrever_nivel(r["nivel"], r["linhas"]))}</div>',
                         unsafe_allow_html=True)
        telefone = f'<div class="rmc-cad-sub">{html.escape(r["telefone"])}</div>' if r["telefone"] else ""
        # O que ele recebe por e-mail (29/10/2026): discreto, embaixo do endereço; quem ainda não criou a senha não
        # recebe nada até criar.
        recebe = [n for n, sim in (("aviso de ruptura", r["email_ruptura"]), ("relatório", r["email_relatorio"])) if sim]
        recebe_texto = " e ".join(recebe) if recebe else "nenhum"
        if r["status"] == "Aguardando" and recebe:
            recebe_texto += " (depois de criar a senha)"
        notificacoes = f'<div class="rmc-cad-sub">E-mails: {recebe_texto}</div>' if not excluido else ""
        c_email.markdown(f'<div class="rmc-cad-celula rmc-cad-email">{html.escape(r["email"])}{telefone}'
                         f'{notificacoes}</div>', unsafe_allow_html=True)
        dica = (f' class="dica-abaixo" data-dica="{dicas.atributo(dicas.dica("Excluído por " + (r["excluido_por"] or "—") + (" em " + r["excluido_em"].strftime("%d/%m/%Y") if r["excluido_em"] else "")))}"') if excluido else ""
        c_status.markdown(f'<span{dica}>{theme.badge(r["status"], _STATUS[r["status"]])}</span>',
                          unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Novo / Editar
# ---------------------------------------------------------------------------

@popups.popup("Novo usuário", "longo")
def _dialog_novo(loja: dict) -> None:
    resultado = st.session_state.get(_RESULTADO)
    if resultado and resultado.get("novo"):
        _mostrar_envio(resultado)
        return
    _formulario(None, loja)


@popups.popup("Editar usuário", "longo")
def _dialog_editar(r: dict) -> None:
    _formulario(r, None)


def _formulario(r: dict | None, loja: dict | None) -> None:
    """Campos do print de referência: Identificação (nome, cargo), Contato
    (e-mail de acesso, telefone), Acesso (loja, nível, linhas, painéis).
    Sem `st.form`: as linhas de compra só aparecem pro Comprador."""
    from integrations import usuarios

    usuario = auth.usuario_atual()
    k = f"{_KEY}_{r['id'] if r else 'novo'}"

    popups.secao("Identificação")
    c1, c2 = st.columns(2)
    nome = c1.text_input("Nome completo *", value=r["nome"] if r else "", max_chars=120, key=f"{k}_nome")
    cargo = c2.text_input("Cargo *", value=r["cargo"] if r else "", max_chars=120, key=f"{k}_cargo",
                          placeholder="Ex.: Comprador, Gerente, Sócio")
    popups.secao("Contato")
    c1, c2 = st.columns([1.6, 1])
    email = c1.text_input("E-mail de acesso *", value=r["email"] if r else "", max_chars=120, key=f"{k}_email",
                          placeholder="nome@loja.com.br",
                          help=dicas.dica("É o login do usuário.",
                                       "Um e-mail por pessoa: quem trabalha em várias lojas tem um cadastro só, "
                                       "com as outras lojas em \"Atribuir Empresas\"."))
    telefone = c2.text_input("Telefone", value=r["telefone"] if r else "", max_chars=30, key=f"{k}_telefone")

    popups.secao("Acesso")
    c1, c2 = st.columns(2)
    if r is None:
        # Q26: a loja vem da barra lateral, só pra conferir.
        c1.text_input("Loja *", value=loja_barra.rotulo(loja), disabled=True, key=f"{k}_loja",
                      help=dicas.dica("A loja escolhida na barra lateral.",
                                      "Para cadastrar em outra loja, troque a loja na barra."))
        loja_id = loja["id"]
    else:
        # Q28: trocar a loja principal mantém o histórico do usuário.
        lojas = loja_barra.lojas_do_usuario(usuario)
        por_id = {l["id"]: l for l in lojas}
        ids = list(por_id)
        atual = r["loja_principal_id"]
        loja_id = c1.selectbox("Loja principal *", ids, index=ids.index(atual) if atual in ids else None,
                               format_func=lambda i: loja_barra.rotulo(por_id[i]), key=f"{k}_loja",
                               placeholder="Selecione a loja")
        if loja_id != atual and loja_id is not None:
            c1.caption("Trocar a loja principal tira o acesso à loja atual e às lojas adicionais que não forem do "
                       "grupo econômico da nova loja (fica registrado no histórico).")
    niveis = list(usuarios.NIVEIS_LOJA)
    nivel = c2.selectbox("Nível de acesso *", niveis,
                         index=niveis.index(r["nivel"]) if r and r["nivel"] in niveis else None,
                         format_func=usuarios.NIVEIS_LOJA.get, placeholder="Selecione o nível", key=f"{k}_nivel",
                         help=dicas.dica("Comprador: vê só as linhas de compra dele no Assistente de pedido.",
                                         "Proprietário: vê a loja inteira."))
    linhas: list[str] = []
    if nivel == acesso.COMPRADOR:
        linhas = st.multiselect("Linhas de compra *", list(usuarios.LINHAS_COMPRA),
                                default=r["linhas"] if r else [], format_func=usuarios.LINHAS_COMPRA.get,
                                placeholder="Escolha uma ou mais", key=f"{k}_linhas")
    # O Comprador não recebe o Dashboard (21/10/2026): a lista muda com o nível,
    # e o que já estava marcado e deixou de valer sai do padrão do campo.
    opcoes_paineis = [p for p in usuarios.PAINEIS if p in acesso.paineis_do_nivel(nivel)]
    paineis = st.multiselect("Painéis *", opcoes_paineis,
                             default=[p for p in (r["paineis"] if r else []) if p in opcoes_paineis],
                             format_func=usuarios.PAINEIS.get, placeholder="Escolha os painéis que ele vê",
                             key=f"{k}_paineis", help=dicas.dica("As telas que o usuário vê.",
                                                                 "Obrigatório: não há padrão. O Comprador não recebe o Dashboard."))

    if r is not None:
        # E-mails para as lojas (29/10/2026): o mesmo que o usuário escolhe em "Minhas notificações" — para quando
        # o lojista pede por telefone.
        popups.secao("E-mails")
        c1, c2 = st.columns(2)
        email_ruptura = c1.toggle("Aviso de ruptura", value=r["email_ruptura"], key=f"{k}_email_ruptura",
                                  help=dicas.dica("Quando um produto que mais fatura na loja zera.",
                                                  "Só chega nos dias em que isso acontece."))
        email_relatorio = c2.toggle("Relatório diário", value=r["email_relatorio"], key=f"{k}_email_relatorio",
                                    help=dicas.dica("Todo dia de manhã: orçamento, itens a comprar, ruptura e os 10 "
                                                    "mais críticos."))

    dados = usuarios.DadosUsuario(nome=nome or "", cargo=cargo or "", email=email or "", telefone=telefone or "",
                                  nivel=nivel or "", linhas=linhas, paineis=paineis, loja_principal_id=loja_id)
    # Erros acima do rodapé (o rodapé fica preso embaixo do pop-up).
    aviso = st.empty()
    rotulo = "Cadastrar e enviar convite" if r is None else "Salvar alterações"
    cancelar, salvar = popups.botoes(rotulo, chave=f"{k}_rodape")
    if cancelar:
        # Formulário longo só fecha por aqui (Q14 — sem X nem clique fora).
        _limpar_resultado()
        st.rerun()
    if salvar:
        with get_session() as session:
            erros = usuarios.validar(session, dados, exceto_id=r["id"] if r else None)
        if erros:
            aviso.error("\n".join(f"- {e}" for e in erros))
            return
        if r is not None:
            try:
                with get_session() as session:
                    usuarios.editar(session, r["id"], dados, usuario["nome"])
                    usuarios.gravar_preferencias(session, r["id"], email_ruptura, email_relatorio)
            except ValueError as exc:
                aviso.error(str(exc))
                return
            _concluir("success", f"Cadastro de {dados.nome.strip()} salvo.")
            st.rerun()
        with get_session() as session:
            novo = usuarios.criar(session, dados, usuario["nome"])
            novo_id = novo.id
        # O e-mail sai numa sessão à parte: se o envio falhar, o cadastro
        # continua gravado e a tela mostra o link pra copiar.
        st.session_state[_RESULTADO] = {"novo": True, **_enviar(novo_id, enviar=True)}
        st.rerun(scope="fragment")


# ---------------------------------------------------------------------------
# E-mail de ativação
# ---------------------------------------------------------------------------

def _enviar(usuario_id: int, enviar: bool) -> dict:
    """Envia (ou só gera) o link de ativação. Devolve o que mostrar."""
    from core.models import Usuario
    from integrations import usuarios

    with get_session() as session:
        u = session.get(Usuario, usuario_id)
        destino, nome = u.login, u.nome_exibicao
        if not enviar:
            return {"url": usuarios.gerar_link(session, u), "enviado": False, "erro": None, "destino": destino,
                    "nome": nome}
    try:
        with get_session() as session:
            url = usuarios.enviar_link(session, session.get(Usuario, usuario_id), usuarios.ATIVACAO)
        return {"url": url, "enviado": True, "erro": None, "destino": destino, "nome": nome}
    except Exception as exc:  # noqa: BLE001 — sem e-mail: o link continua sendo o caminho
        with get_session() as session:
            url = usuarios.gerar_link(session, session.get(Usuario, usuario_id))
        # RuntimeError = a frase do próprio sistema ("não configurado");
        # erro do Gmail/rede: só o tipo (a mensagem pode trazer endereço do servidor).
        erro = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        return {"url": url, "enviado": False, "erro": erro, "destino": destino, "nome": nome}


# Ícones do pop-up do convite (SVG: emoji tem cor fixa do sistema — regra da
# identidade visual).
_ICONE_ENVIADO = ('<path d="M3.5 6.5h17v9.5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/>'
                  '<path d="M3.5 6.5v11h10M3.5 6.5l8.5 6.5 8.5-6.5" fill="none" stroke="currentColor" '
                  'stroke-width="1.9" stroke-linejoin="round"/><path d="M15.5 18l2.2 2.2 4-4.4" fill="none" '
                  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
_ICONE_ALERTA = ('<path d="M3.5 6.5h17v6M3.5 6.5v11h11M3.5 6.5l8.5 6.5 8.5-6.5" fill="none" stroke="currentColor" '
                 'stroke-width="1.9" stroke-linejoin="round"/><path d="M19 15.5v3M19 21v.1" fill="none" '
                 'stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/>')
_ICONE_LINK = ('<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4l-1 1M14 10a4.5 4.5 0 0 0-6.4 0'
               'l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1-1" fill="none" stroke="currentColor" stroke-width="1.9" '
               'stroke-linecap="round"/>')


def _mostrar_envio(res: dict) -> None:
    """Resultado do convite (07/10/2026, modelo do print do Mariano): ícone
    num círculo colorido, título curto, uma frase, a pílula "Copiar link"
    (o link não aparece mais inteiro) e "Concluir"."""
    nome, destino = html.escape(res["nome"] or ""), html.escape(res["destino"] or "")
    if res["enviado"]:
        tom, icone, titulo = "ok", _ICONE_ENVIADO, "Convite enviado"
        texto = f"{nome} vai receber o acesso em<br><b>{destino}</b>"
    elif res["erro"]:
        tom, icone, titulo = "alerta", _ICONE_ALERTA, "E-mail não enviado"
        texto = (f"Copie o link e envie para {nome} por outro meio (ex.: WhatsApp)."
                 f'<span class="motivo">{html.escape(res["erro"])}</span>')
    else:
        tom, icone, titulo = "link", _ICONE_LINK, "Link gerado"
        texto = f"Copie o link e envie para {nome}. O e-mail de acesso é<br><b>{destino}</b>"
    # Largura máxima e centralizado: o pop-up do "Novo usuário" é largo, e o
    # "Concluir" esticava na largura toda.
    with st.container(key=f"convite-{_KEY}"):
        _convite(tom, icone, titulo, texto, res)


def _convite(tom: str, icone: str, titulo: str, texto: str, res: dict) -> None:
    from core.config import settings
    from views.copiar_link import copiar_link

    popups.resultado(tom, icone, titulo, texto)
    copiar_link(res["url"], key=f"{_KEY}_copiar_{abs(hash(res['url'])) % 10**8}")
    st.markdown(f'<div class="rmc-convite-validade">O link vale {settings.email.horas_ativacao} horas e uma vez só.'
                '</div>', unsafe_allow_html=True)
    _, concluir = popups.botoes("Concluir", None, chave=f"{_KEY}_concluir")
    if concluir:
        _limpar_resultado()
        if res.get("novo"):
            _concluir("success", f"{res['nome']} cadastrado.")
        st.rerun()


@popups.popup("E-mail de ativação", "curto", on_dismiss=_limpar_resultado)
def _dialog_email(r: dict) -> None:
    resultado = st.session_state.get(_RESULTADO)
    if resultado:
        _mostrar_envio(resultado)
        return
    st.markdown(f"**{html.escape(r['nome'])}** · {html.escape(r['email'])}")
    st.caption("O usuário recebe um link para criar a senha. Um link novo invalida o anterior.")
    so_link, enviar = popups.botoes("Enviar e-mail", "Só gerar o link", chave=f"{_KEY}_email_rodape")
    if so_link:
        st.session_state[_RESULTADO] = _enviar(r["id"], enviar=False)
        st.rerun(scope="fragment")
    if enviar:
        st.session_state[_RESULTADO] = _enviar(r["id"], enviar=True)
        st.rerun(scope="fragment")


# ---------------------------------------------------------------------------
# Painéis, Empresas, Excluir
# ---------------------------------------------------------------------------

@popups.popup("Atribuir Painéis", "curto")
def _dialog_paineis(r: dict) -> None:
    from integrations import usuarios

    st.markdown(f"Selecione os painéis do usuário **{html.escape(r['nome'])}**")
    opcoes_paineis = [p for p in usuarios.PAINEIS if p in acesso.paineis_do_nivel(r["nivel"])]
    paineis = st.multiselect("Painéis", opcoes_paineis, default=[p for p in r["paineis"] if p in opcoes_paineis],
                             format_func=usuarios.PAINEIS.get, placeholder="Escolha os painéis",
                             key=f"{_KEY}_paineis_sel_{r['id']}")
    if not paineis:
        st.caption("Escolha pelo menos um painel.")
    cancelar, salvar = popups.botoes("Salvar painéis", chave=f"{_KEY}_paineis_rodape", desabilitado=not paineis)
    if cancelar:
        st.rerun()
    if salvar:
        with get_session() as session:
            usuarios.atribuir_paineis(session, r["id"], paineis, auth.usuario_atual()["nome"])
        _concluir("success", f"Painéis de {r['nome']} atualizados.")
        st.rerun()


@popups.popup("Atribuir Empresas", "curto")
def _dialog_empresas(r: dict) -> None:
    """Só lojas do MESMO grupo econômico da loja principal (Q27); a
    principal vem marcada e travada (muda no Editar)."""
    from core import queries
    from integrations import usuarios

    with get_session() as session:
        lojas = usuarios.lojas_do_grupo(session, r["loja_principal_id"])
        grupo = queries.rotulos_grupos(session).get(r["grupo"], r["grupo"]) if r["grupo"] else None
    st.markdown(f"Lojas do usuário **{html.escape(r['nome'])}**")
    st.caption(f"Grupo econômico: {grupo}. Só lojas do mesmo grupo (informado pelo sistema da T.I.) podem ser "
               "atribuídas." if grupo else
               "Esta loja não tem grupo econômico no sistema da T.I.: não há outras lojas para atribuir.")
    escolhidas = []
    for l in lojas:
        principal = l["id"] == r["loja_principal_id"]
        marcado = st.checkbox(l["rotulo"] + (" (loja principal)" if principal else ""),
                              value=principal or l["id"] in r["adicionais"], disabled=principal,
                              key=f"{_KEY}_empresa_{r['id']}_{l['id']}")
        if marcado and not principal:
            escolhidas.append(l["id"])
    if len(lojas) <= 1:
        # Q31: sem outras lojas no grupo, as opções aparecem desabilitadas.
        st.checkbox("Outras lojas da rede (fora do grupo econômico)", value=False, disabled=True,
                    key=f"{_KEY}_empresa_{r['id']}_outras")
    aviso = st.empty()
    cancelar, salvar = popups.botoes("Salvar lojas", chave=f"{_KEY}_empresas_rodape", desabilitado=len(lojas) <= 1)
    if cancelar:
        st.rerun()
    if salvar:
        try:
            with get_session() as session:
                novas, retiradas = usuarios.atribuir_lojas(session, r["id"], escolhidas, auth.usuario_atual()["nome"])
        except ValueError as exc:
            aviso.error(str(exc))
            return
        partes = ([f"{novas} loja(s) acrescentada(s)"] if novas else []) + \
                 ([f"{retiradas} retirada(s)"] if retiradas else [])
        _concluir("success", f"Lojas de {r['nome']}: " + (", ".join(partes) if partes else "nada mudou") + ".")
        st.rerun()


@popups.popup("Excluir usuário", "aviso")
def _dialog_excluir(r: dict) -> None:
    from integrations import usuarios

    st.markdown(f"Tem certeza que deseja excluir **{html.escape(r['nome'])}** ({html.escape(r['email'])})?")
    st.caption("Ele não entra mais no sistema. Listas, exportações e o histórico continuam com o nome dele, e dá "
               "para reativar em \"Mostrar excluídos\".")
    cancelar, excluir = popups.botoes("Excluir usuário", chave=f"{_KEY}_excluir_rodape", destrutivo=True)
    if cancelar:
        st.rerun()
    if excluir:
        with get_session() as session:
            usuarios.excluir(session, r["id"], auth.usuario_atual()["nome"])
        _concluir("success", f"{r['nome']} excluído.")
        st.rerun()
