"""Área de trabalho do Pedido (usuário + loja) e listas salvas com nome —
novo layout de 29/09/2026 (substitui o "rascunho da loja" da Fase 5).

- Área de trabalho: cada edição de quantidade e cada marcação grava na hora
  (`pedido_area`), por usuário + loja. Recarregar a página ou voltar outro
  dia traz tudo como estava — `st.session_state` sozinho se perde no reload.
  Só guarda o que difere do padrão (quantidade digitada, marcação feita à
  mão): a leitura é 1 consulta pequena por abertura de loja.
- Listas: "Salvar como lista" grava uma cópia com nome, visível pra todos
  (evita pedido duplicado e mostra quem está fazendo). "Abrir" traz a lista
  pra área de trabalho; exportar a partir dela apaga a lista.

Cada ação = 1 ida ao banco (~140 ms daqui até NY), fora do cálculo da loja,
que fica em cache (views/pedido.py).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.models import AreaPedido, AreaPedidoEstado, AvisoPedidoVisto, ExportacaoPedido, ListaPedido, ListaPedidoItem
from core.sql import insert_com_atualizacao


@dataclass
class ItemArea:
    quantidade: int | None
    selecionado: bool | None


@dataclass
class Area:
    itens: dict[str, ItemArea]
    atualizado_em: dt.datetime | None
    lista_aberta_id: int | None


def _tocar_estado(session: Session, usuario_id: int, loja_id: int, **valores) -> None:
    agora = dt.datetime.utcnow()
    colunas = ["atualizado_em"] + list(valores)
    session.execute(
        insert_com_atualizacao(session, AreaPedidoEstado.__table__, ["usuario_id", "loja_id"], colunas),
        [{"usuario_id": usuario_id, "loja_id": loja_id, "atualizado_em": agora, **valores}],
    )


def area(session: Session, usuario_id: int, loja_id: int) -> Area:
    itens = {
        a.linha: ItemArea(a.quantidade, a.selecionado)
        for a in session.scalars(select(AreaPedido).where(AreaPedido.usuario_id == usuario_id,
                                                          AreaPedido.loja_id == loja_id))
    }
    estado = session.scalars(select(AreaPedidoEstado).where(AreaPedidoEstado.usuario_id == usuario_id,
                                                            AreaPedidoEstado.loja_id == loja_id)).first()
    return Area(itens, estado.atualizado_em if estado else None, estado.lista_aberta_id if estado else None)


def _gravar(session: Session, usuario_id: int, loja_id: int, registros: list[dict], colunas: list[str]) -> None:
    agora = dt.datetime.utcnow()
    for r in registros:
        r.update({"usuario_id": usuario_id, "loja_id": loja_id, "atualizado_em": agora})
    session.execute(
        insert_com_atualizacao(session, AreaPedido.__table__, ["usuario_id", "loja_id", "linha"], colunas + ["atualizado_em"]),
        registros,
    )
    # Linha que voltou ao padrão (segue a sugestão e a marcação automática) sai.
    session.execute(delete(AreaPedido).where(
        AreaPedido.usuario_id == usuario_id, AreaPedido.loja_id == loja_id,
        AreaPedido.quantidade.is_(None), AreaPedido.selecionado.is_(None)))
    _tocar_estado(session, usuario_id, loja_id)


def alterar_quantidade(session: Session, usuario_id: int, loja_id: int, linha: str, quantidade: int,
                       sugestao: int) -> None:
    """Digitar o número sugerido volta a seguir a sugestão. A marcação volta
    a ser automática (quantidade > 0 marca, 0 desmarca — Q2 de 29/09/2026)."""
    if quantidade < 0:
        raise ValueError("A quantidade não pode ser negativa.")
    _gravar(session, usuario_id, loja_id,
            [{"linha": linha, "quantidade": None if quantidade == sugestao else int(quantidade), "selecionado": None}],
            ["quantidade", "selecionado"])


def marcar(session: Session, usuario_id: int, loja_id: int, marcacoes: dict[str, bool]) -> None:
    """Marca/desmarca uma ou várias linhas (o "marcar todos" da página)."""
    if marcacoes:
        _gravar(session, usuario_id, loja_id,
                [{"linha": l, "selecionado": bool(v)} for l, v in marcacoes.items()], ["selecionado"])


def descartar(session: Session, usuario_id: int, loja_id: int) -> int:
    """Volta tudo à sugestão (e fecha a lista aberta, sem apagá-la)."""
    n = session.execute(delete(AreaPedido).where(AreaPedido.usuario_id == usuario_id,
                                                 AreaPedido.loja_id == loja_id)).rowcount or 0
    _tocar_estado(session, usuario_id, loja_id, lista_aberta_id=None)
    return n


# ---------------------------------------------------------------------------
# Listas
# ---------------------------------------------------------------------------

@dataclass
class ResumoLista:
    id: int
    nome: str
    criado_por: str
    criado_por_id: int | None
    criado_em: dt.datetime
    itens: int
    unidades: int
    valor: float


def listas(session: Session, loja_id: int) -> list[ResumoLista]:
    return [
        ResumoLista(l.id, l.nome, l.criado_por, l.criado_por_id, l.criado_em, l.itens, l.unidades, float(l.valor))
        for l in session.scalars(select(ListaPedido).where(ListaPedido.loja_id == loja_id)
                                 .order_by(ListaPedido.criado_em.desc()))
    ]


def salvar_lista(session: Session, usuario_id: int, usuario_nome: str, loja_id: int, nome: str,
                 itens: list[tuple[str, int, bool]], unidades: int, valor: float) -> int:
    """`itens`: (linha, quantidade, selecionado) de todas as linhas do pedido.
    A lista nova fica aberta na área de quem salvou."""
    nome = nome.strip()
    if not nome:
        raise ValueError("Dê um nome à lista.")
    lista = ListaPedido(loja_id=loja_id, nome=nome[:120], criado_por=usuario_nome, criado_por_id=usuario_id,
                        criado_em=dt.datetime.utcnow(), itens=sum(1 for _, q, s in itens if s and q > 0),
                        unidades=int(unidades), valor=round(float(valor), 2))
    session.add(lista)
    session.flush()
    session.add_all([ListaPedidoItem(lista_id=lista.id, linha=l, quantidade=int(q), selecionado=bool(s))
                     for l, q, s in itens])
    _tocar_estado(session, usuario_id, loja_id, lista_aberta_id=lista.id)
    session.flush()
    return lista.id


def abrir_lista(session: Session, usuario_id: int, loja_id: int, lista_id: int) -> int:
    """Substitui a área de trabalho pela lista: quantidade e marcação de cada
    linha ficam fixas (mesmo que a sugestão de hoje seja outra)."""
    itens = session.scalars(select(ListaPedidoItem).where(ListaPedidoItem.lista_id == lista_id)).all()
    session.execute(delete(AreaPedido).where(AreaPedido.usuario_id == usuario_id, AreaPedido.loja_id == loja_id))
    if itens:
        agora = dt.datetime.utcnow()
        session.add_all([AreaPedido(usuario_id=usuario_id, loja_id=loja_id, linha=i.linha, quantidade=i.quantidade,
                                    selecionado=i.selecionado, atualizado_em=agora) for i in itens])
    _tocar_estado(session, usuario_id, loja_id, lista_aberta_id=lista_id)
    session.flush()
    return len(itens)


def excluir_lista(session: Session, lista_id: int) -> None:
    session.execute(delete(ListaPedidoItem).where(ListaPedidoItem.lista_id == lista_id))
    session.execute(delete(ListaPedido).where(ListaPedido.id == lista_id))
    # Quem estava com ela aberta continua com a área, só sem a referência.
    from sqlalchemy import update
    session.execute(update(AreaPedidoEstado).where(AreaPedidoEstado.lista_aberta_id == lista_id)
                    .values(lista_aberta_id=None))


def ao_exportar(session: Session, usuario_id: int, loja_id: int) -> str | None:
    """Exportou: a lista aberta (se houver) cumpriu o papel e sai. Devolve o
    nome dela, pra mensagem."""
    estado = session.scalars(select(AreaPedidoEstado).where(AreaPedidoEstado.usuario_id == usuario_id,
                                                            AreaPedidoEstado.loja_id == loja_id)).first()
    if estado is None or estado.lista_aberta_id is None:
        return None
    lista = session.get(ListaPedido, estado.lista_aberta_id)
    nome = lista.nome if lista else None
    excluir_lista(session, estado.lista_aberta_id)
    return nome


def avisos(session: Session, usuario_id: int, usuario_nome: str, loja_id: int, hoje: dt.date) -> list[str]:
    """Contra pedido duplicado: listas de OUTRAS pessoas desta loja e
    exportações de hoje feitas por outras pessoas."""
    saida = []
    for l in listas(session, loja_id):
        if l.criado_por_id != usuario_id:
            saida.append(f"{l.criado_por} tem a lista \"{l.nome}\" desta loja ({l.criado_em:%d/%m %H:%M} UTC).")
    inicio = dt.datetime.combine(hoje, dt.time())
    for e in session.scalars(select(ExportacaoPedido).where(ExportacaoPedido.loja_id == loja_id,
                                                           ExportacaoPedido.exportado_em >= inicio)):
        if e.exportado_por != usuario_nome:
            saida.append(f"{e.exportado_por} exportou um pedido desta loja hoje às {e.exportado_em:%H:%M} (UTC).")
    return saida


# ---------------------------------------------------------------------------
# Pop-ups de alerta ao abrir a loja (Assistente de pedido, 01/10/2026)
# ---------------------------------------------------------------------------

AVISO_NEGATIVO = "negativo"
AVISO_SEM_CLASSIFICACAO = "sem_classificacao"


def avisos_vistos(session: Session, usuario_id: int, loja_id: int) -> dict[str, str]:
    """tipo → data da foto do GPS em que o usuário já respondeu o pop-up."""
    return {a.tipo: a.data_foto for a in session.scalars(select(AvisoPedidoVisto).where(
        AvisoPedidoVisto.usuario_id == usuario_id, AvisoPedidoVisto.loja_id == loja_id))}


def marcar_aviso_visto(session: Session, usuario_id: int, loja_id: int, tipo: str, data_foto: str,
                       resposta: str | None = None) -> None:
    session.execute(
        insert_com_atualizacao(session, AvisoPedidoVisto.__table__, ["usuario_id", "loja_id", "tipo"],
                               ["data_foto", "resposta", "visto_em"]),
        [{"usuario_id": usuario_id, "loja_id": loja_id, "tipo": tipo, "data_foto": data_foto,
          "resposta": resposta, "visto_em": dt.datetime.utcnow()}],
    )
