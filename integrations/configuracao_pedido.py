"""Configurações de Pedidos no banco (`configuracoes_pedido`), as
personalizações por loja (`personalizacoes_pedido_loja`, 01/10/2026) e o
pedaço que a rotina da madrugada precisa (a janela de meses) no Spaces.
Regras e validação: pedido/configuracao.py."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import ConfiguracaoPedido, Loja, PersonalizacaoPedidoLoja
from pedido import configuracao as cfg
from pedido.armazenamento import Armazenamento
from pedido.configuracao import ConfigPedido
from pedido.plano import Chaves


@dataclass
class Vigente:
    config: ConfigPedido
    alterado_por: str | None      # None = nunca gravada (valendo o padrão)
    alterado_em: dt.datetime | None


def vigente(session: Session) -> Vigente:
    linha = session.scalars(select(ConfiguracaoPedido).order_by(ConfiguracaoPedido.id.desc()).limit(1)).first()
    if linha is None:
        return Vigente(ConfigPedido.padrao(), None, None)
    return Vigente(ConfigPedido.de_dict(json.loads(linha.valores)), linha.criado_por, linha.criado_em)


def versao(session: Session) -> int:
    """Muda a cada gravação — chave do cache da tela Pedido."""
    return int(session.scalar(select(func.max(ConfiguracaoPedido.id))) or 0)


def salvar(session: Session, config: ConfigPedido, usuario: str) -> None:
    erros = config.erros()
    if erros:
        raise ValueError(" ".join(erros))
    session.add(ConfiguracaoPedido(valores=json.dumps(config.para_dict(), ensure_ascii=False, sort_keys=True),
                                   criado_por=usuario, criado_em=dt.datetime.utcnow()))
    session.flush()


def historico(session: Session, limite: int = 10) -> list[dict]:
    """As últimas gravações, com o que mudou em relação à anterior."""
    linhas = session.scalars(
        select(ConfiguracaoPedido).order_by(ConfiguracaoPedido.id.desc()).limit(limite + 1)
    ).all()
    saida = []
    for atual, anterior in zip(linhas, linhas[1:] + [None]):
        novo = json.loads(atual.valores)
        velho = json.loads(anterior.valores) if anterior else ConfigPedido.padrao().para_dict()
        mudou = sorted(k for k in novo if novo.get(k) != velho.get(k))
        saida.append({"por": atual.criado_por, "em": atual.criado_em, "campos": mudou})
    return saida[:limite]


def publicar_para_rotina(armaz: Armazenamento, chaves: Chaves, config: ConfigPedido, usuario: str) -> None:
    """A rotina (GitHub Actions) não alcança o banco: lê a janela daqui."""
    armaz.salvar_json(chaves.configuracao(), {
        "meses_fechados": config.meses_fechados,
        "alterado_por": usuario,
        "alterado_em": dt.datetime.now().isoformat(timespec="seconds"),
    })



# ---------------------------------------------------------------------------
# Personalização por loja
# ---------------------------------------------------------------------------

def _ultimas_por_loja(session: Session) -> dict[int, PersonalizacaoPedidoLoja]:
    """A gravação mais recente de cada loja (inclusive as `{}` = removida)."""
    ultimas = (select(PersonalizacaoPedidoLoja.loja_id, func.max(PersonalizacaoPedidoLoja.id).label("id"))
               .group_by(PersonalizacaoPedidoLoja.loja_id).subquery())
    linhas = session.scalars(select(PersonalizacaoPedidoLoja).join(ultimas, PersonalizacaoPedidoLoja.id == ultimas.c.id))
    return {l.loja_id: l for l in linhas}


def da_loja(session: Session, loja_id: int) -> dict:
    """As diferenças vigentes da loja; `{}` = sem personalização (padrão)."""
    linha = session.scalars(select(PersonalizacaoPedidoLoja).where(PersonalizacaoPedidoLoja.loja_id == loja_id)
                            .order_by(PersonalizacaoPedidoLoja.id.desc()).limit(1)).first()
    return json.loads(linha.valores) if linha else {}


def config_da_loja(session: Session, loja_id: int) -> tuple[ConfigPedido, dict]:
    """(config efetiva da loja = padrão + diferenças, diferenças)."""
    dif = da_loja(session, loja_id)
    return cfg.aplicar(vigente(session).config, dif), dif


def lojas_personalizadas(session: Session) -> list[dict]:
    """As lojas com personalização vigente, pra lista da tela de
    configurações: id, nome, nº de campos, quem e quando."""
    ultimas = {k: v for k, v in _ultimas_por_loja(session).items() if json.loads(v.valores)}
    if not ultimas:
        return []
    nomes = dict(session.execute(select(Loja.id, Loja.razao_social).where(Loja.id.in_(list(ultimas)))).all())
    return sorted(({"loja_id": k, "nome": nomes.get(k, f"Loja {k}"), "campos": cfg.quantos_campos(json.loads(v.valores)),
                    "por": v.criado_por, "em": v.criado_em} for k, v in ultimas.items()), key=lambda d: d["nome"])


def salvar_loja(session: Session, loja_id: int, config_loja: ConfigPedido, usuario: str) -> dict:
    """Grava o que a loja tem de diferente do padrão VIGENTE. Devolve as
    diferenças gravadas (`{}` = ficou igual ao padrão: a loja volta a ele)."""
    erros = config_loja.erros()
    if erros:
        raise ValueError(" ".join(erros))
    dif = cfg.diferencas(vigente(session).config, config_loja)
    if dif == da_loja(session, loja_id):
        return dif
    session.add(PersonalizacaoPedidoLoja(loja_id=loja_id, valores=json.dumps(dif, ensure_ascii=False, sort_keys=True),
                                         criado_por=usuario, criado_em=dt.datetime.utcnow()))
    session.flush()
    return dif


def remover_loja(session: Session, loja_id: int, usuario: str) -> None:
    """A loja volta ao padrão. Grava `{}` em vez de apagar: o histórico fica."""
    if da_loja(session, loja_id):
        session.add(PersonalizacaoPedidoLoja(loja_id=loja_id, valores="{}", criado_por=usuario,
                                             criado_em=dt.datetime.utcnow()))
        session.flush()


def historico_loja(session: Session, loja_id: int, limite: int = 10) -> list[dict]:
    linhas = session.scalars(select(PersonalizacaoPedidoLoja).where(PersonalizacaoPedidoLoja.loja_id == loja_id)
                             .order_by(PersonalizacaoPedidoLoja.id.desc()).limit(limite)).all()
    return [{"por": l.criado_por, "em": l.criado_em, "valores": json.loads(l.valores)} for l in linhas]


def versao_personalizacoes(session: Session) -> int:
    """Muda a cada gravação de qualquer loja — chave do cache da tela Pedido."""
    return int(session.scalar(select(func.max(PersonalizacaoPedidoLoja.id))) or 0)
