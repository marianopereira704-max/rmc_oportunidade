"""Vínculo loja do GPS ↔ loja do RMC, do lado do app (banco).

A rotina do Pedido (pacote `pedido/`, fora do app) grava as lojas de cada
empresa do GPS no Spaces (`pedido/controle/lojas_gps/{empresa}.json`); aqui
elas são comparadas com as lojas do Postgres (regras em pedido/vinculo.py)
e o resultado vai para `vinculos_loja_gps`.

Decisão humana manda: CONFIRMADO e NAO_CLIENTE nunca são sobrescritos pelo
recálculo — do mesmo jeito que um EAN resolvido à mão não volta pra fila.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Loja, SituacaoVinculoGps, VinculoLojaGps
from pedido import vinculo
from pedido.armazenamento import Armazenamento
from pedido.plano import Chaves

_DECISAO_HUMANA = {SituacaoVinculoGps.CONFIRMADO, SituacaoVinculoGps.NAO_CLIENTE}
_SITUACAO = {
    vinculo.AUTOMATICO: SituacaoVinculoGps.AUTOMATICO,
    vinculo.CONFIRMAR: SituacaoVinculoGps.CONFIRMAR,
    vinculo.SEM_CANDIDATO: SituacaoVinculoGps.SEM_CANDIDATO,
}


def lojas_gps_salvas(armaz: Armazenamento, chaves: Chaves) -> list[dict]:
    """Todas as lojas do GPS que a rotina já gravou, no formato de
    pedido/vinculo.sugerir (+ cidade, pra mostrar na tela)."""
    saida = []
    prefixo = f"{chaves.prefixo}/controle/lojas_gps/"
    for chave in armaz.listar(prefixo):
        id_empresa = chave[len(prefixo):].removesuffix(".json")
        for l in armaz.ler_json(chave):
            saida.append({
                "id_empresa": id_empresa, "codigo_loja": str(l.get("CodigoLoja")),
                "nome_loja": l.get("NomeLoja") or l.get("NomeFantasia"), "cnpj": l.get("CNPJ"),
                "numero": l.get("Numero"), "uf": (l.get("Estado") or "").strip()[:2], "cidade": l.get("Cidade"),
            })
    return saida


def _lojas_rmc(session: Session) -> tuple[list[dict], dict[str, int]]:
    lojas = session.execute(select(
        Loja.id, Loja.cnpj, Loja.razao_social, Loja.nome_fantasia, Loja.endereco_numero, Loja.bairro, Loja.cidade, Loja.uf,
    )).all()
    entrada = [{"cnpj": l.cnpj, "razao_social": l.razao_social, "nome_fantasia": l.nome_fantasia,
                "numero": l.endereco_numero, "bairro": l.bairro, "cidade": l.cidade, "uf": l.uf} for l in lojas]
    return entrada, {vinculo.so_digitos(l.cnpj): l.id for l in lojas}


@dataclass
class ResultadoRecalculo:
    lojas_gps: int
    automaticos: int
    a_confirmar: int
    sem_candidato: int
    decisoes_preservadas: int


def recalcular(session: Session, lojas_gps: list[dict]) -> ResultadoRecalculo:
    """Upsert das sugestões; uma consulta pra ler o que existe e gravação em
    lote (o banco está em NY: consulta por loja custaria minutos)."""
    entrada_rmc, loja_por_cnpj = _lojas_rmc(session)
    sugestoes = vinculo.sugerir(lojas_gps, entrada_rmc)
    existentes = {(v.id_empresa_gps, v.codigo_loja_gps): v for v in session.scalars(select(VinculoLojaGps))}
    agora = dt.datetime.utcnow()
    contagem = {"automatico": 0, "confirmar": 0, "sem": 0, "preservadas": 0}
    for g, s in zip(lojas_gps, sugestoes):
        atual = existentes.get((s.id_empresa, s.codigo_loja))
        if atual is not None and atual.situacao in _DECISAO_HUMANA:
            contagem["preservadas"] += 1
            continue
        if atual is None:
            atual = VinculoLojaGps(id_empresa_gps=s.id_empresa, codigo_loja_gps=s.codigo_loja)
            session.add(atual)
        atual.nome_loja_gps = (g.get("nome_loja") or "")[:200] or None
        atual.cnpj_gps = vinculo.so_digitos(g.get("cnpj")) or None
        atual.numero_gps = (str(g.get("numero") or "").strip()[:20]) or None
        atual.cidade_gps = (g.get("cidade") or "")[:120] or None
        atual.uf_gps = g.get("uf") or None
        atual.situacao = _SITUACAO[s.situacao]
        atual.metodo = s.metodo
        atual.pontuacao = s.pontuacao
        atual.motivo = s.motivo[:200]
        atual.loja_id = loja_por_cnpj.get(s.cnpj_rmc) if s.cnpj_rmc else None
        atual.atualizado_em = agora
        contagem[{"automatico": "automatico", "confirmar": "confirmar"}.get(s.situacao, "sem")] += 1
    return ResultadoRecalculo(len(lojas_gps), contagem["automatico"], contagem["confirmar"], contagem["sem"],
                              contagem["preservadas"])


def confirmar(session: Session, vinculo_id: int, loja_id: int, usuario: str) -> None:
    v = session.get(VinculoLojaGps, vinculo_id)
    v.loja_id = loja_id
    v.situacao = SituacaoVinculoGps.CONFIRMADO
    v.decidido_por, v.decidido_em = usuario, dt.datetime.utcnow()


def marcar_nao_cliente(session: Session, vinculo_id: int, usuario: str) -> None:
    v = session.get(VinculoLojaGps, vinculo_id)
    v.loja_id = None
    v.situacao = SituacaoVinculoGps.NAO_CLIENTE
    v.decidido_por, v.decidido_em = usuario, dt.datetime.utcnow()


def loja_gps_da_loja(session: Session, loja_id: int) -> tuple[str, str] | None:
    """(idEmpresa, CodigoLoja) do GPS de uma loja do RMC — só vínculos
    válidos (automático ou confirmado). Usado pela tela Pedido."""
    v = session.scalars(select(VinculoLojaGps).where(
        VinculoLojaGps.loja_id == loja_id,
        VinculoLojaGps.situacao.in_([SituacaoVinculoGps.AUTOMATICO, SituacaoVinculoGps.CONFIRMADO]),
    )).first()
    return (v.id_empresa_gps, v.codigo_loja_gps) if v else None
