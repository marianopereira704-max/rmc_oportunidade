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
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import EmpresaGps, Loja, SituacaoVinculoGps, VinculoLojaGps, loja_da_rede
from core.sql import insert_com_atualizacao
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
    arquivos = armaz.listar(prefixo)
    # Em paralelo: um arquivo por empresa (ver Armazenamento.ler_varios).
    for chave, lojas in zip(arquivos, armaz.ler_varios(arquivos, armaz.ler_json)):
        id_empresa = chave[len(prefixo):].removesuffix(".json")
        for l in lojas:
            saida.append({
                # Sem espaços: lojas gravadas antes de 08/10/2026 trazem o código
                # do GPS com espaços no fim (ver pedido/coleta.py::_lojas).
                "id_empresa": id_empresa, "codigo_loja": str(l.get("CodigoLoja") or "").strip(),
                "nome_loja": l.get("NomeLoja") or l.get("NomeFantasia"), "cnpj": l.get("CNPJ"),
                "numero": l.get("Numero"), "uf": (l.get("Estado") or "").strip()[:2], "cidade": l.get("Cidade"),
                "cep": l.get("CEP"),
            })
    return saida


def _lojas_rmc(session: Session) -> tuple[list[dict], dict[str, int]]:
    lojas = session.execute(select(
        Loja.id, Loja.cnpj, Loja.razao_social, Loja.nome_fantasia, Loja.endereco_numero, Loja.bairro, Loja.cidade, Loja.uf,
        Loja.cep,
    ).where(loja_da_rede())).all()   # loja fora da rede não é candidata a vínculo (07/10/2026)
    entrada = [{"cnpj": l.cnpj, "razao_social": l.razao_social, "nome_fantasia": l.nome_fantasia,
                "numero": l.endereco_numero, "bairro": l.bairro, "cidade": l.cidade, "uf": l.uf, "cep": l.cep}
               for l in lojas]
    return entrada, {vinculo.so_digitos(l.cnpj): l.id for l in lojas}


@dataclass
class ResultadoRecalculo:
    lojas_gps: int
    automaticos: int
    a_confirmar: int
    sem_candidato: int
    decisoes_preservadas: int


def recalcular_se_houver_novas(session: Session, armaz: Armazenamento, chaves: Chaves) -> ResultadoRecalculo | None:
    """Vínculo automático (Q4 de 08/10/2026): só recalcula se a rotina
    gravou lojas de alguma empresa que ainda não está no banco — uma
    listagem do Spaces e uma consulta quando não há nada novo. Decisão
    humana continua preservada (é o mesmo `recalcular` do botão).
    None = nada novo."""
    prefixo = f"{chaves.prefixo}/controle/lojas_gps/"
    no_arquivo = {c[len(prefixo):].removesuffix(".json") for c in armaz.listar(prefixo) if c.endswith(".json")}
    no_banco = set(session.scalars(select(VinculoLojaGps.id_empresa_gps).distinct()))
    if not (no_arquivo - no_banco):
        return None
    return recalcular(session, lojas_gps_salvas(armaz, chaves))


def recalcular(session: Session, lojas_gps: list[dict]) -> ResultadoRecalculo:
    """Upsert das sugestões; uma consulta pra ler o que existe e gravação em
    lote (o banco está em NY: consulta por loja custaria minutos)."""
    entrada_rmc, loja_por_cnpj = _lojas_rmc(session)
    sugestoes = vinculo.sugerir(lojas_gps, entrada_rmc)
    # Comparado pelo código SEM espaços: um vínculo gravado com o código com
    # espaços (antes de 08/10/2026) era tomado por loja nova, e o insert batia
    # na chave única — o recálculo inteiro (e o botão) caía.
    existentes = {(v.id_empresa_gps, v.codigo_loja_gps.strip()): v for v in session.scalars(select(VinculoLojaGps))}
    agora = dt.datetime.utcnow()
    contagem = {"automatico": 0, "confirmar": 0, "sem": 0, "preservadas": 0}
    for g, s in zip(lojas_gps, sugestoes):
        atual = existentes.get((s.id_empresa, s.codigo_loja))
        if atual is not None and atual.codigo_loja_gps != s.codigo_loja:
            atual.codigo_loja_gps = s.codigo_loja   # o antigo, com espaços, fica limpo (vale também pra decisão humana)
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
    return (v.id_empresa_gps, v.codigo_loja_gps.strip()) if v else None


# ---------------------------------------------------------------------------
# Código da empresa no GPS (08/10/2026)
# ---------------------------------------------------------------------------
# A API só identifica a empresa pelo `idEmpresa` longo; o número que a equipe
# usa (Mega Farma = 2089, Costa e Verli = 2246) vem de uma planilha exportada
# do próprio GPS: Empresa ("2089 - Mega Farma", às vezes "2246 - 1124 -
# NOME" ou "2316 - [GPS] 2316 - NOME - 2154"), Cnpj (uma linha por loja) e
# idEmp (= o idEmpresa da API). Conferido em 08/10/2026: 524 linhas, um
# código por idEmp sem exceção, 97% dos CNPJs no cadastro do RMC, 342 das
# 388 empresas da API presentes.

_COLUNAS_CODIGOS = {"empresa": ("empresa", "nome"), "cnpj": ("cnpj",), "id": ("idemp", "idempresa", "id_empresa")}


@dataclass
class PlanilhaCodigos:
    empresas: list[dict]                 # id_empresa_gps, codigo_gps, nome, cnpjs
    ignoradas: list[tuple[int, str]]     # (linha da planilha, motivo)


def _coluna(df, nomes: tuple[str, ...]) -> str | None:
    por_chave = {re.sub(r"[^a-z]", "", str(c).lower()): c for c in df.columns}
    return next((por_chave[n] for n in nomes if n in por_chave), None)


def ler_planilha_codigos(df) -> PlanilhaCodigos:
    """Planilha do GPS → uma entrada por empresa. Levanta ValueError com a
    frase pra tela se faltar coluna."""
    colunas = {k: _coluna(df, v) for k, v in _COLUNAS_CODIGOS.items()}
    if colunas["empresa"] is None or colunas["id"] is None:
        raise ValueError("A planilha precisa das colunas Empresa (\"2089 - Mega Farma\") e idEmp (o código da "
                         "empresa na API do GPS). Cnpj é opcional.")
    por_id: dict[str, dict] = {}
    ignoradas = []
    for n, r in enumerate(df.to_dict("records"), start=2):   # linha 1 = cabeçalho
        id_emp = str(r.get(colunas["id"]) or "").strip().lower()
        texto = str(r.get(colunas["empresa"]) or "").strip()
        m = re.match(r"^\s*(\d+)\s*-?\s*(.*)$", texto)
        if not re.fullmatch(r"[0-9a-f]{24}", id_emp):
            ignoradas.append((n, "idEmp vazio ou fora do formato da API"))
            continue
        if not m:
            ignoradas.append((n, f"Empresa sem o código no começo: \"{texto[:40]}\""))
            continue
        nome = re.sub(r"^\[GPS\]\s*\d+\s*-\s*", "", m.group(2)).strip(" -") or None
        e = por_id.setdefault(id_emp, {"id_empresa_gps": id_emp, "codigo_gps": m.group(1), "nome": nome, "cnpjs": set()})
        cnpj = vinculo.so_digitos(r.get(colunas["cnpj"])) if colunas["cnpj"] else ""
        if cnpj:
            e["cnpjs"].add(cnpj)
    return PlanilhaCodigos(list(por_id.values()), ignoradas)


def importar_codigos(session: Session, lida: PlanilhaCodigos, usuario: str) -> int:
    """Grava (insere ou atualiza por idEmp). Nada se apaga: empresa que saiu
    da planilha nova continua com o código que tinha."""
    agora = dt.datetime.utcnow()
    linhas = [{**e, "nome": (e["nome"] or "")[:200] or None, "cnpjs": ",".join(sorted(e["cnpjs"])) or None,
               "atualizado_em": agora, "atualizado_por": usuario[:120]} for e in lida.empresas]
    for i in range(0, len(linhas), 500):
        stmt = insert_com_atualizacao(session, EmpresaGps.__table__, ["id_empresa_gps"],
                                      ["codigo_gps", "nome", "cnpjs", "atualizado_em", "atualizado_por"])
        session.execute(stmt, linhas[i:i + 500])
    session.flush()
    return len(linhas)


@dataclass
class EmpresaInfo:
    codigo: str
    nome: str | None
    cnpjs: frozenset[str]


def empresas(session: Session) -> dict[str, EmpresaInfo]:
    """idEmpresa → código do GPS, nome e CNPJs — uma consulta."""
    return {e.id_empresa_gps: EmpresaInfo(e.codigo_gps, e.nome, frozenset((e.cnpjs or "").split(",")) - {""})
            for e in session.scalars(select(EmpresaGps))}
