"""Usuários de loja: login por e-mail, links de criar/recuperar senha e os
e-mails do sistema (decisões de 06–07/10/2026).

- Equipe RMC (ADM, Consultor) continua no CNPJ + senha (core/auth.py).
- Lojas (Comprador, Proprietário) entram por e-mail + senha. A senha nunca
  é definida por quem cadastra: o usuário recebe um link (ativação, 48 h) e
  cria a dele. "Esqueci minha senha" manda um link de 1 h. Quem cadastra
  também pode COPIAR o link (pra mandar por WhatsApp se o e-mail não chegar).
- O link leva um token aleatório; o banco guarda só o hash (sha256) — quem
  lê o banco não consegue usar um link. Cada link vale uma vez; pedir um
  novo invalida os anteriores do mesmo tipo.
- E-mail pelo Gmail (SMTP + senha de app — core/config.py::EmailConfig).
  Custo: zero no volume do sistema; ~1–2 s por envio, só pra quem clicou.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import secrets
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from core import acesso
from core.config import settings
from core.models import Loja, Papel, TokenSenha, Usuario, UsuarioLoja, UsuarioLojaHistorico, loja_da_rede
from core.security import hash_senha, verificar_senha

ATIVACAO = "ativacao"
RECUPERACAO = "recuperacao"
SENHA_MINIMA = 8

# Linhas de compra do Comprador (o que cada uma inclui: catálogo IQVIA — etapa 5).
LINHAS_COMPRA = {
    "genericos": "Genérico",
    "similares": "Similar",
    "propagados": "Propagado",
    "perfumaria": "Perfumaria",
}
# Painéis (telas) que um usuário de loja pode receber.
PAINEIS = {
    "oportunidade_produto": "Análise de Oportunidade · Por Produto",
    "pedido": "Assistente de pedido",
    "dashboard": "Dashboard",
}


def normalizar_email(email: str | None) -> str:
    return (email or "").strip().lower()


def parece_email(texto: str | None) -> bool:
    return "@" in (texto or "")


def codigos(texto: str | None) -> list[str]:
    """"genericos,similares" → ["genericos", "similares"]."""
    return [c for c in (texto or "").split(",") if c]


def juntar(lista) -> str | None:
    return ",".join(sorted(set(lista))) or None


def status(usuario: Usuario) -> str:
    """Aguardando (ainda não criou a senha) · Ativo · Excluído."""
    if not usuario.ativo:
        return "Excluído"
    return "Ativo" if usuario.senha_hash else "Aguardando"


# ---------------------------------------------------------------------------
# Login por e-mail
# ---------------------------------------------------------------------------

def autenticar_email(session: Session, email: str, senha: str) -> Usuario | None:
    u = session.scalar(select(Usuario).where(Usuario.login == normalizar_email(email), Usuario.ativo.is_(True)))
    if u is None or not u.senha_hash or not verificar_senha(senha, u.senha_hash):
        return None
    return u


# ---------------------------------------------------------------------------
# Links de senha
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def criar_token(session: Session, usuario_id: int, tipo: str, agora: dt.datetime | None = None) -> str:
    """Novo link (devolve o token em claro — só existe neste momento).
    Invalida os links anteriores do mesmo tipo ainda não usados."""
    agora = agora or dt.datetime.utcnow()
    horas = settings.email.horas_ativacao if tipo == ATIVACAO else settings.email.horas_recuperacao
    session.execute(
        update(TokenSenha)
        .where(TokenSenha.usuario_id == usuario_id, TokenSenha.tipo == tipo, TokenSenha.usado_em.is_(None))
        .values(usado_em=agora)
    )
    token = secrets.token_urlsafe(32)
    session.add(TokenSenha(usuario_id=usuario_id, token_hash=_hash_token(token), tipo=tipo, criado_em=agora,
                           expira_em=agora + dt.timedelta(hours=horas)))
    session.flush()
    return token


def link(token: str) -> str:
    return f"{settings.email.app_url}/?token={token}"


@dataclass
class TokenValido:
    usuario: Usuario
    tipo: str
    registro: TokenSenha


def validar_token(session: Session, token: str | None, agora: dt.datetime | None = None) -> TokenValido | None:
    """O link ainda vale? (existe, não usado, não vencido, usuário ativo)."""
    if not token:
        return None
    agora = agora or dt.datetime.utcnow()
    t = session.scalar(select(TokenSenha).where(TokenSenha.token_hash == _hash_token(token)))
    if t is None or t.usado_em is not None or t.expira_em < agora:
        return None
    u = session.get(Usuario, t.usuario_id)
    if u is None or not u.ativo:
        return None
    return TokenValido(u, t.tipo, t)


def definir_senha(session: Session, token: str, senha: str, agora: dt.datetime | None = None) -> Usuario:
    """Cria (ou troca) a senha pelo link. Levanta ValueError com a frase pra
    tela quando não dá."""
    if len(senha or "") < SENHA_MINIMA:
        raise ValueError(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.")
    agora = agora or dt.datetime.utcnow()
    valido = validar_token(session, token, agora)
    if valido is None:
        raise ValueError("Este link não vale mais (já foi usado ou venceu). Peça um novo.")
    valido.usuario.senha_hash = hash_senha(senha)
    valido.registro.usado_em = agora
    session.flush()
    return valido.usuario


# ---------------------------------------------------------------------------
# E-mails
# ---------------------------------------------------------------------------

def enviar_email(destino: str, assunto: str, texto: str, html_corpo: str) -> None:
    """Envia pelo Gmail. Levanta exceção se não der (a tela mostra o link pra copiar)."""
    cfg = settings.email
    if not cfg.configured:
        raise RuntimeError("E-mail do sistema não configurado (EMAIL_REMETENTE / EMAIL_SENHA_APP).")
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = f"{cfg.nome_remetente} <{cfg.remetente}>"
    msg["To"] = destino
    msg.set_content(texto)
    msg.add_alternative(html_corpo, subtype="html")
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_porta, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg.remetente, cfg.senha_app)
        smtp.send_message(msg)


def _mensagem(usuario: Usuario, tipo: str, url: str) -> tuple[str, str, str]:
    nome = (usuario.nome_exibicao or "").split(" ")[0] or "Olá"
    if tipo == ATIVACAO:
        assunto = "Seu acesso ao Assistente de Pedidos RMC"
        frase = "Seu acesso ao sistema da RMC foi criado. Clique no botão para criar a sua senha."
        botao, validade = "Criar minha senha", f"O link vale por {settings.email.horas_ativacao} horas."
    else:
        assunto = "Redefinir sua senha — Assistente de Pedidos RMC"
        frase = "Recebemos um pedido para redefinir a sua senha. Clique no botão para criar uma nova."
        botao, validade = "Criar nova senha", (f"O link vale por {settings.email.horas_recuperacao} hora. "
                                               "Se você não pediu, ignore este e-mail.")
    texto = f"{nome},\n\n{frase}\n\n{url}\n\n{validade}\n\nSeu login é o e-mail: {usuario.login}"
    corpo = (
        f'<div style="font-family:Arial,sans-serif;color:#17375E;max-width:520px">'
        f"<p>{html.escape(nome)},</p><p>{html.escape(frase)}</p>"
        f'<p><a href="{html.escape(url)}" style="background:#17375E;color:#fff;padding:12px 20px;'
        f'border-radius:8px;text-decoration:none;display:inline-block">{botao}</a></p>'
        f'<p style="color:#7A8699;font-size:13px">{html.escape(validade)}<br>Seu login é o e-mail: '
        f"{html.escape(usuario.login or '')}</p></div>"
    )
    return assunto, texto, corpo


def enviar_link(session: Session, usuario: Usuario, tipo: str, enviar=enviar_email) -> str:
    """Cria o link e manda por e-mail. Devolve o link (pra copiar). Se o
    envio falhar, o link continua valendo e a exceção sobe — a tela mostra
    o link pra mandar por outro meio."""
    url = link(criar_token(session, usuario.id, tipo))
    assunto, texto, corpo = _mensagem(usuario, tipo, url)
    enviar(usuario.login, assunto, texto, corpo)
    return url


def pedir_recuperacao(session: Session, email: str, enviar=enviar_email) -> bool:
    """"Esqueci minha senha": manda o link se o e-mail for de um usuário
    ativo (quem ainda não criou a senha recebe o de ativação). A tela mostra
    sempre a mesma mensagem, exista o e-mail ou não — não dá pra descobrir
    quem tem cadastro. Devolve se enviou (só pra teste)."""
    u = session.scalar(select(Usuario).where(Usuario.login == normalizar_email(email), Usuario.ativo.is_(True)))
    if u is None:
        return False
    enviar_link(session, u, RECUPERACAO if u.senha_hash else ATIVACAO, enviar)
    return True


def email_em_uso(session: Session, email: str, exceto_id: int | None = None) -> bool:
    stmt = select(func.count()).select_from(Usuario).where(Usuario.login == normalizar_email(email))
    if exceto_id is not None:
        stmt = stmt.where(Usuario.id != exceto_id)
    return bool(session.scalar(stmt))


def gerar_link(session: Session, usuario: Usuario, tipo: str = ATIVACAO) -> str:
    """Só o link, sem e-mail ("Copiar link" do cadastro). Invalida os
    anteriores do mesmo tipo, como o envio."""
    return link(criar_token(session, usuario.id, tipo))


# ---------------------------------------------------------------------------
# Cadastro (aba Cadastro, 07/10/2026)
# ---------------------------------------------------------------------------
# Só usuários de LOJA (Comprador, Proprietário): a equipe RMC entra pelo CNPJ
# da rede (Q1) e não é cadastrada aqui — então o Consultor, que cadastra
# "só usuários das lojas" (Q5), e o ADM usam a mesma tela.

NIVEIS_LOJA = {acesso.COMPRADOR: "Comprador", acesso.PROPRIETARIO: "Proprietário"}
SINCRONIZACAO = "Sincronização com o sistema da T.I."


@dataclass
class DadosUsuario:
    nome: str
    cargo: str
    email: str
    telefone: str
    nivel: str
    linhas: list[str]
    paineis: list[str]
    loja_principal_id: int


def _paineis_validos(paineis, nivel: str | None) -> list[str]:
    """Só os painéis que existem E que o nível pode receber (o Comprador não
    recebe o Dashboard — 21/10/2026): a tela já só oferece esses, mas a
    gravação não confia nela."""
    permitidos = acesso.paineis_do_nivel(nivel)
    return [p for p in paineis if p in PAINEIS and p in permitidos]


def validar(session: Session, d: DadosUsuario, exceto_id: int | None = None) -> list[str]:
    """Frases pra tela; vazio = pode gravar. Painel é obrigatório pros dois
    níveis e linha de compra pro Comprador (Q11/Q21: sem padrão — quem
    cadastra escolhe)."""
    erros = []
    if not d.nome.strip():
        erros.append("Informe o nome completo.")
    if not d.cargo.strip():
        erros.append("Informe o cargo.")
    email = normalizar_email(d.email)
    if not email:
        erros.append("Informe o e-mail de acesso.")
    elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        erros.append("E-mail inválido.")
    elif email_em_uso(session, email, exceto_id):
        erros.append("Este e-mail já tem cadastro (um e-mail por pessoa — a mesma pessoa em várias lojas usa "
                     "\"Atribuir Empresas\").")
    if d.nivel not in NIVEIS_LOJA:
        erros.append("Escolha o nível de acesso.")
    elif d.nivel == acesso.COMPRADOR and not [c for c in d.linhas if c in LINHAS_COMPRA]:
        erros.append("Escolha pelo menos uma linha de compra.")
    if not _paineis_validos(d.paineis, d.nivel):
        erros.append("Escolha pelo menos um painel.")
    loja = session.get(Loja, d.loja_principal_id) if d.loja_principal_id else None
    if loja is None or loja.na_rede is False:
        erros.append("Escolha uma loja da rede.")
    return erros


def _do_cadastro(session: Session, usuario_id: int) -> Usuario:
    """Só usuário de loja passa pelo cadastro — nunca ADM/Consultor (a
    equipe RMC), nem por chave forjada."""
    u = session.get(Usuario, usuario_id)
    if u is None or acesso.nivel_do_usuario(u.papel.value, u.nivel) not in NIVEIS_LOJA:
        raise ValueError("Usuário não encontrado no cadastro das lojas.")
    return u


def _aplicar(u: Usuario, d: DadosUsuario) -> None:
    u.nome_exibicao = d.nome.strip()
    u.cargo = d.cargo.strip()
    u.login = normalizar_email(d.email)
    u.telefone = d.telefone.strip() or None
    u.nivel = d.nivel
    # Proprietário vê todas as linhas (Q22): não guarda linha nenhuma.
    u.linhas_compra = juntar(c for c in d.linhas if c in LINHAS_COMPRA) if d.nivel == acesso.COMPRADOR else None
    u.paineis = juntar(_paineis_validos(d.paineis, d.nivel))


def _historico(session: Session, usuario_id: int, loja_id: int, motivo: str, por: str,
               agora: dt.datetime | None = None) -> None:
    session.add(UsuarioLojaHistorico(usuario_id=usuario_id, loja_id=loja_id, motivo=motivo[:200],
                                     registrado_em=agora or dt.datetime.utcnow(), registrado_por=por[:120]))


def criar(session: Session, d: DadosUsuario, por: str) -> Usuario:
    """Novo usuário de loja, "Aguardando" até criar a senha pelo link. Sem
    lojas adicionais: só a loja do cadastro, até alguém atribuir (Q12)."""
    erros = validar(session, d)
    if erros:
        raise ValueError(" ".join(erros))
    # `papel` = CONSULTOR só porque a coluna é obrigatória (ver core/models.py::Usuario).
    u = Usuario(senha_hash="", papel=Papel.CONSULTOR, ativo=True, loja_principal_id=d.loja_principal_id,
                criado_em=dt.datetime.utcnow(), criado_por=por[:120])
    _aplicar(u, d)
    session.add(u)
    session.flush()
    return u


def editar(session: Session, usuario_id: int, d: DadosUsuario, por: str) -> Usuario:
    """Grava a edição. Trocar a loja principal (Q28 — quem muda de loja não
    perde o histórico) leva junto as regras do grupo: sai a loja antiga e as
    adicionais que não são do grupo da loja nova (com registro no histórico)."""
    u = _do_cadastro(session, usuario_id)
    erros = validar(session, d, exceto_id=u.id)
    if erros:
        raise ValueError(" ".join(erros))
    _aplicar(u, d)
    if d.loja_principal_id != u.loja_principal_id:
        antiga = u.loja_principal_id
        u.loja_principal_id = d.loja_principal_id
        if antiga is not None:
            _historico(session, u.id, antiga, "Troca de loja principal", por)
        grupo = session.get(Loja, d.loja_principal_id).grupo_economico
        for ul in list(u.lojas):
            loja = session.get(Loja, ul.loja_id)
            if ul.loja_id == d.loja_principal_id:
                u.lojas.remove(ul)          # a adicional virou a principal
            elif grupo is None or loja is None or loja.grupo_economico != grupo:
                _historico(session, u.id, ul.loja_id, "Troca de loja principal (loja fora do grupo econômico)", por)
                u.lojas.remove(ul)
    session.flush()
    return u


def atribuir_paineis(session: Session, usuario_id: int, paineis: list[str], por: str) -> None:
    u = _do_cadastro(session, usuario_id)
    validos = _paineis_validos(paineis, acesso.nivel_do_usuario(u.papel.value, u.nivel))
    if not validos:
        raise ValueError("Escolha pelo menos um painel.")
    u.paineis = juntar(validos)
    session.flush()


def _rotulo_loja(l: Loja) -> str:
    nome = (l.razao_social or "").strip()
    return f"{l.legacy_id} · {nome}" if l.legacy_id else nome


def lojas_do_grupo(session: Session, loja_id: int) -> list[dict]:
    """Lojas que podem ser atribuídas a quem tem `loja_id` como principal:
    as da rede do MESMO grupo econômico informado pela T.I. (Q27). Loja sem
    grupo: só ela. A própria loja vem primeiro."""
    loja = session.get(Loja, loja_id)
    if loja is None:
        return []
    if loja.grupo_economico:
        lojas = session.scalars(select(Loja).where(Loja.grupo_economico == loja.grupo_economico, loja_da_rede())
                                .order_by(Loja.legacy_id, Loja.razao_social)).all()
    else:
        lojas = [loja]
    lojas = sorted(lojas, key=lambda l: l.id != loja_id)
    return [{"id": l.id, "rotulo": _rotulo_loja(l), "cidade": l.cidade, "uf": l.uf} for l in lojas]


def atribuir_lojas(session: Session, usuario_id: int, adicionais: list[int], por: str) -> tuple[int, int]:
    """Grava as lojas adicionais (só do grupo da principal). As retiradas
    ficam no histórico. Devolve (acrescentadas, retiradas)."""
    u = _do_cadastro(session, usuario_id)
    permitidas = {l["id"] for l in lojas_do_grupo(session, u.loja_principal_id)} - {u.loja_principal_id}
    fora = set(adicionais) - permitidas
    if fora:
        raise ValueError("Só lojas do mesmo grupo econômico da loja principal podem ser atribuídas.")
    atuais = {ul.loja_id for ul in u.lojas}
    novas = set(adicionais)
    for ul in list(u.lojas):
        if ul.loja_id not in novas:
            _historico(session, u.id, ul.loja_id, "Retirada em \"Atribuir Empresas\"", por)
            u.lojas.remove(ul)
    for loja_id in sorted(novas - atuais):
        u.lojas.append(UsuarioLoja(usuario_id=u.id, loja_id=loja_id))
    session.flush()
    return len(novas - atuais), len(atuais - novas)


def excluir(session: Session, usuario_id: int, por: str) -> None:
    """Desativa (Q13): não entra mais e some da lista; listas, exportações e
    o histórico continuam com o nome. Os links abertos param de valer."""
    u = _do_cadastro(session, usuario_id)
    agora = dt.datetime.utcnow()
    u.ativo, u.excluido_em, u.excluido_por = False, agora, por[:120]
    session.execute(update(TokenSenha).where(TokenSenha.usuario_id == u.id, TokenSenha.usado_em.is_(None))
                    .values(usado_em=agora))
    session.flush()


def reativar(session: Session, usuario_id: int, por: str) -> None:
    u = _do_cadastro(session, usuario_id)
    loja = session.get(Loja, u.loja_principal_id) if u.loja_principal_id else None
    if loja is None or loja.na_rede is False:
        raise ValueError("A loja principal deste usuário não é mais da rede: não dá para reativar.")
    if email_em_uso(session, u.login or "", exceto_id=u.id):
        raise ValueError("O e-mail deste usuário foi usado em outro cadastro.")
    u.ativo, u.excluido_em, u.excluido_por = True, None, None
    session.flush()


def da_loja(session: Session, loja_id: int, com_excluidos: bool = False) -> list[dict]:
    """Usuários com acesso à loja (principal ou adicional — quem olha a
    loja vê todo mundo que entra nela, Q25), com TODAS as lojas de cada um.
    Três consultas, qualquer que seja o número de usuários."""
    adicionais_aqui = select(UsuarioLoja.usuario_id).where(UsuarioLoja.loja_id == loja_id)
    stmt = select(Usuario).where(
        Usuario.nivel.in_(list(NIVEIS_LOJA)),
        (Usuario.loja_principal_id == loja_id) | Usuario.id.in_(adicionais_aqui),
    )
    if not com_excluidos:
        stmt = stmt.where(Usuario.ativo.is_(True))
    usuarios = session.scalars(stmt).all()
    ids = [u.id for u in usuarios]
    extras: dict[int, list[int]] = {}
    for usuario_id, l_id in session.execute(
            select(UsuarioLoja.usuario_id, UsuarioLoja.loja_id).where(UsuarioLoja.usuario_id.in_(ids))):
        extras.setdefault(usuario_id, []).append(l_id)
    lojas_ids = {u.loja_principal_id for u in usuarios} | {l for ls in extras.values() for l in ls}
    lojas = {l.id: l for l in session.scalars(select(Loja).where(Loja.id.in_(lojas_ids)))}
    saida = []
    for u in usuarios:
        principal = lojas.get(u.loja_principal_id)
        outras = sorted((lojas[i] for i in extras.get(u.id, []) if i in lojas), key=lambda l: (l.legacy_id or 0))
        saida.append({
            "id": u.id, "nome": u.nome_exibicao, "cargo": u.cargo or "", "email": u.login or "",
            "telefone": u.telefone or "", "nivel": u.nivel, "linhas": codigos(u.linhas_compra),
            "paineis": codigos(u.paineis), "status": status(u), "loja_principal_id": u.loja_principal_id,
            "adicionais": [l.id for l in outras],
            "lojas": ([_rotulo_loja(principal)] if principal else []) + [_rotulo_loja(l) for l in outras],
            "grupo": principal.grupo_economico if principal else None,
            "excluido_por": u.excluido_por, "excluido_em": u.excluido_em,
        })
    ordem = {"Aguardando": 0, "Ativo": 1, "Excluído": 2}
    return sorted(saida, key=lambda r: (ordem[r["status"]], r["nome"].lower()))


def descrever_nivel(nivel: str | None, linhas: list[str]) -> str:
    """"Comprador · Genéricos, Similares" / "Proprietário"."""
    rotulo = NIVEIS_LOJA.get(nivel, nivel or "")
    if nivel == acesso.COMPRADOR and linhas:
        return f"{rotulo} · " + ", ".join(LINHAS_COMPRA[c] for c in LINHAS_COMPRA if c in linhas)
    return rotulo


def ajustar_acessos(session: Session, por: str = SINCRONIZACAO) -> dict[str, int]:
    """Depois da sincronização de lojas (Q32 de 06/10/2026: a loja que a T.I.
    tira saiu da rede e perde o acesso sozinha, com registro no histórico):
    - loja principal fora da rede → o usuário é excluído (desativado);
    - loja adicional fora da rede ou fora do grupo econômico da principal →
      sai das lojas dele.
    Custo: três consultas e só grava o que mudou — nada perto dos ~600
    upserts da própria sincronização."""
    # selectinload: as lojas de todos numa consulta só (uma por usuário, a
    # ~140 ms do banco, viraria minutos com a rede inteira cadastrada).
    usuarios = session.scalars(select(Usuario).options(selectinload(Usuario.lojas)).where(
        Usuario.ativo.is_(True), Usuario.nivel.in_(list(NIVEIS_LOJA)), Usuario.loja_principal_id.is_not(None))).all()
    if not usuarios:
        return {"excluidos": 0, "retiradas": 0}
    ids = {u.loja_principal_id for u in usuarios} | {ul.loja_id for u in usuarios for ul in u.lojas}
    lojas = {l.id: l for l in session.scalars(select(Loja).where(Loja.id.in_(ids)))}
    agora = dt.datetime.utcnow()
    excluidos = retiradas = 0
    for u in usuarios:
        principal = lojas.get(u.loja_principal_id)
        if principal is None or principal.na_rede is False:
            _historico(session, u.id, u.loja_principal_id, "Loja principal saiu da rede (usuário excluído)", por, agora)
            u.ativo, u.excluido_em, u.excluido_por = False, agora, por
            excluidos += 1
            continue
        for ul in list(u.lojas):
            loja = lojas.get(ul.loja_id)
            if loja is None or loja.na_rede is False:
                motivo = "Loja saiu da rede"
            elif not principal.grupo_economico or loja.grupo_economico != principal.grupo_economico:
                motivo = "Loja fora do grupo econômico da loja principal"
            else:
                continue
            _historico(session, u.id, ul.loja_id, motivo, por, agora)
            u.lojas.remove(ul)
            retiradas += 1
    session.flush()
    return {"excluidos": excluidos, "retiradas": retiradas}
