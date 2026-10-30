"""Lista de destinatários dos e-mails para as lojas (29/10/2026) — `docs/specs/2026-10-29-emails-lojas-design.md`.

A rotina do GitHub manda os e-mails (`pedido/emails.py`) e não alcança o banco, onde estão os usuários e as
preferências: o app publica a lista em `pedido/insumos/<banco>/destinatarios.json`, junto com as fotos dos insumos do
cálculo de madrugada (`integrations/insumos.py`) e pelos mesmos momentos — ao gravar uma preferência ou o Cadastro, e
de 10 em 10 min por processo. Só grava quando algo mudou.

Entra quem recebe algo: usuário de LOJA (Comprador ou Proprietário), ativo, com senha criada (o "Aguardando" fica de
fora: o e-mail pode estar errado e o primeiro contato é o convite) e com pelo menos um dos dois e-mails ligado. A
equipe RMC não recebe. Lojas: a principal e as adicionais, só as da rede e com vínculo ao GPS.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

ARQUIVO = "destinatarios.json"


def montar(session: Session) -> list[dict]:
    from core import acesso
    from core.models import Usuario, UsuarioLoja
    from integrations import pedido_tela, usuarios

    lojas = {l["id"]: l for l in pedido_tela.lojas(session) if l.get("empresa_gps") and l.get("loja_gps")}
    adicionais: dict[int, list[int]] = {}
    for ul in session.scalars(select(UsuarioLoja)):
        adicionais.setdefault(ul.usuario_id, []).append(ul.loja_id)
    saida = []
    for u in session.scalars(select(Usuario).where(Usuario.ativo.is_(True), Usuario.login.is_not(None))
                             .order_by(Usuario.id)):
        nivel = acesso.nivel_do_usuario(u.papel.value if u.papel else None, u.nivel)
        if nivel not in (acesso.COMPRADOR, acesso.PROPRIETARIO) or not u.senha_hash:
            continue
        ruptura, relatorio = usuarios.preferencias(u)
        if not (ruptura or relatorio):
            continue
        ids = [u.loja_principal_id, *sorted(adicionais.get(u.id, []))]
        das_lojas = [lojas[i] for i in dict.fromkeys(ids) if i in lojas]
        saida.append({
            "id": u.id, "email": u.login, "nome": u.nome_exibicao, "nivel": nivel,
            "linhas": usuarios.codigos(u.linhas_compra) if nivel == acesso.COMPRADOR else None,
            "ruptura": ruptura, "relatorio": relatorio,
            "lojas": [{"id": l["id"], "legacy_id": l.get("legacy_id"), "razao_social": l.get("razao_social"),
                       "empresa_gps": l["empresa_gps"], "loja_gps": l["loja_gps"]} for l in das_lojas],
        })
    return saida


def chave(chaves, banco: str) -> str:
    from pedido import precalculo

    return f"{precalculo.pasta_insumos(chaves, banco)}{ARQUIVO}"


def publicar(session: Session, armaz=None) -> bool:
    """Grava a lista se mudou desde a última publicação. True = gravou."""
    from core import copia_spaces
    from core.config import settings
    from pedido.armazenamento import do_ambiente
    from pedido.plano import Chaves

    armaz = armaz or do_ambiente()
    lista = montar(session)
    k = chave(Chaves(settings.pedido.prefixo), copia_spaces.nome_do_banco())
    assinatura = hashlib.sha256(json.dumps(lista, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    if armaz.existe(k) and armaz.ler_json(k).get("assinatura") == assinatura:
        return False
    armaz.salvar_json(k, {"destinatarios": lista, "assinatura": assinatura,
                          "publicado_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    return True
