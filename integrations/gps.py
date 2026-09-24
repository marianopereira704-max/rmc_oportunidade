"""Integração com o GPS (compras das lojas) — upload manual da planilha
exportada do BI.

Regra de negócio (decidida em 09/2026):
- Uma linha vale como COMPRA quando tem `VlrUnitario > 0` e `Quantidade > 0`.
  O custo unitário é o `VlrUnitario` (preço de compra SEM ST — comparável à
  tabela Gruppy, que também não tem imposto) e a quantidade é a `Quantidade`
  comprada no mês. A exportação traz também linhas só de VENDA (sem esses
  dois campos): elas são ignoradas. Estoque não faz mais parte da análise.
- Cada envio cobre um mês (`ano_mes`, informado por quem envia) e SUBSTITUI,
  naquele mês, os dados de todos os CNPJs presentes no arquivo — e só deles.
  Um CNPJ que não veio no arquivo continua como estava. É o que permite
  dividir a exportação do BI em vários arquivos (por UF, por exemplo) e
  reenviar um deles depois pra atualizar os dados.
- A substituição é TUDO OU NADA: apaga e regrava numa única transação. Se
  qualquer coisa falhar no meio, o banco fica exatamente como antes do envio.
- CNPJ que não bate com nenhuma loja vai para `compras_gps_orfas` (mesma
  regra de substituição) e a fila de CNPJ órfão é recalculada a partir de lá.
  Vincular o CNPJ a uma loja MOVE essas compras para a loja — sem reabrir
  nenhum .xlsx — e o vínculo vale para os envios seguintes também.
- A fila de EAN é recalculada a partir das compras vigentes (nunca somada
  envio a envio — reenviar o mesmo mês não infla a prioridade).

A leitura do .xlsx não acontece aqui: o navegador lê a planilha e entrega um
DataFrame (ver integrations/planilha_navegador.py). Este módulo recebe esse
DataFrame, prepara as compras (`preparar_compras`, puro e rápido) e grava
(`aplicar_compras`, uma transação) — sem nenhuma consulta ao banco dentro de
laço: tudo em lote.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import (
    CompraGPSOrfa,
    FilaCnpjOrfao,
    FilaResolucaoEAN,
    FSNode,
    Loja,
    OrigemFila,
    RegistroCompraGPS,
    StatusFila,
    UploadGPS,
)
from core.sql import insert_com_atualizacao
from integrations import gps_cache_orfaos
from integrations.base import IntegrationAdapter, ResultadoSincronizacao, StatusIntegracao
from reconciliation import motor as reconciliation_motor
from reconciliation.normalizador import normalizar_ean

logger = logging.getLogger(__name__)

# Campo -> rótulo exibido no popup de mapeamento.
CAMPOS_OBRIGATORIOS = {"cnpj", "ean", "descricao", "quantidade", "custo_unitario"}
CAMPOS_OPCIONAIS = ("laboratorio", "razao_social", "fat_liquido", "pct_cmv", "qtd_vendida", "custo_medio")

# Campos de RECUO de preço guardados em cada compra (ver core/analise.py):
# usados só quando o VlrUnitario destoa do preço do laboratório escolhido.
CAMPOS_RECUO = ("fat_liquido", "pct_cmv", "qtd_vendida", "custo_cmv_unitario", "custo_medio_planilha")

_TAMANHO_BLOCO = 5000  # linhas por INSERT em lote
_TAMANHO_BLOCO_CHAVES = 1000  # itens por cláusula IN


def _normalizar_coluna(col: str) -> str:
    """Só letras e dígitos sobrevivem ("Fat. líquido" -> "fatliquido",
    "% CMV" -> "cmv"), pra comparar cabeçalhos contra os sinônimos de
    `settings.colunas.gps` sem depender de pontuação."""
    col = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", col.strip().lower())


def detectar_colunas_automatico(df: pd.DataFrame) -> dict[str, str]:
    """Sugestão por sinônimo, sem validar se todo campo obrigatório foi
    encontrado (o popup precisa da sugestão mesmo incompleta).

    A PRIORIDADE é a ordem dos sinônimos de cada campo, não a ordem das
    colunas na planilha. Isso importa de verdade: a exportação do BI traz
    `QTD` (quantidade VENDIDA) antes de `Quantidade` (quantidade COMPRADA) —
    varrendo pela ordem das colunas, a quantidade vendida ganhava. Uma coluna
    também nunca é sugerida para dois campos."""
    sinonimos = settings.colunas.gps
    colunas_por_nome: dict[str, str] = {}
    for col in df.columns:
        colunas_por_nome.setdefault(_normalizar_coluna(col), col)

    mapa: dict[str, str] = {}
    usadas: set[str] = set()
    for campo in (*sorted(CAMPOS_OBRIGATORIOS), *CAMPOS_OPCIONAIS):
        for sinonimo in sinonimos.get(campo, []):
            coluna = colunas_por_nome.get(sinonimo)
            if coluna is not None and coluna not in usadas:
                mapa[campo] = coluna
                usadas.add(coluna)
                break
    return mapa


def mapear_colunas(df: pd.DataFrame) -> dict[str, str]:
    mapa = detectar_colunas_automatico(df)
    faltando = CAMPOS_OBRIGATORIOS - mapa.keys()
    if faltando:
        raise ValueError(
            f"A planilha precisa ter colunas para: {', '.join(sorted(faltando))}. "
            f"Colunas encontradas: {', '.join(str(c) for c in df.columns)}"
        )
    return mapa


def listar_uploads_gps_no_mes(session: Session, ano_mes: str) -> list[UploadGPS]:
    return list(
        session.execute(
            select(UploadGPS).where(UploadGPS.ano_mes == ano_mes).order_by(UploadGPS.criado_em)
        ).scalars().all()
    )


# ---------------------------------------------------------------------------
# Normalização de valores
# ---------------------------------------------------------------------------

def _somente_digitos(valor) -> str:
    """CNPJ pode chegar formatado ("30.208.213/0001-74"), como texto puro de
    dígitos, ou como número que perdeu zeros à esquerda — normaliza tudo pra
    14 dígitos com zero-padding, nunca comparando string formatada com crua."""
    if valor is None:
        return ""
    if isinstance(valor, float):
        if pd.isna(valor):
            return ""
        valor = str(int(round(valor)))
    texto = "".join(ch for ch in str(valor) if ch.isdigit())
    return texto.zfill(14) if texto else ""


def _texto_ou_vazio(valor) -> str:
    return str(valor).strip() if pd.notna(valor) else ""


def _texto_ou_nulo(valor) -> str | None:
    """Ausência é None (NULL no banco), nunca "" nem o texto "nan"."""
    if not pd.notna(valor):
        return None
    texto = str(valor).strip()
    return texto if texto and texto.lower() != "nan" else None


def _mapear_por_valor_distinto(serie: pd.Series, funcao: Callable) -> pd.Series:
    """Aplica `funcao` uma vez por valor DISTINTO da coluna (centenas de CNPJs,
    alguns milhares de EANs), não uma vez por linha (150 mil), e expande de
    volta por indexação de array. Nulo vira `funcao(None)`, na última posição
    — onde cai o código -1 do `factorize`. `dtype=object` impede o pandas de
    converter os `None` devolvidos em NaN."""
    codigos, unicos = pd.factorize(serie, use_na_sentinel=True)
    saidas = np.empty(len(unicos) + 1, dtype=object)
    for posicao, valor in enumerate(unicos):
        saidas[posicao] = funcao(valor)
    saidas[-1] = funcao(None)
    return pd.Series(saidas[codigos], index=serie.index, dtype=object)


def _fracao_de_percentual(numeros: pd.Series) -> pd.Series:
    """% CMV pode vir em fração (0,61) ou em percentual (61). A escala é
    decidida pela COLUNA inteira, nunca valor a valor: CMV acima de 100%
    existe (1,12 = 112% numa coluna em fração) e dividir esse valor solto por
    100 sumiria com ele. Maioria dos valores não-zero em [0, 1] = fração."""
    validos = numeros[numeros.notna() & (numeros != 0)]
    if validos.empty or (validos.abs() <= 1).mean() >= 0.5:
        return numeros
    return numeros / 100


def _valor_ou_nulo(valor) -> float | None:
    return None if valor is None or pd.isna(valor) else float(valor)


def _numeros(serie: pd.Series) -> pd.Series:
    """Número em C pra coluna inteira; só o que sobrar como texto ("1.234,56",
    "R$ 12,50") paga a limpeza de string. Inconversível vira NaN."""
    numeros = pd.to_numeric(serie, errors="coerce").astype(float)
    resto = numeros.isna() & serie.notna()
    if resto.any():
        texto = (
            serie[resto].astype(str).str.strip()
            .str.replace(r"[R$\s]", "", regex=True)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        numeros.loc[resto] = pd.to_numeric(texto, errors="coerce")
    return numeros


# ---------------------------------------------------------------------------
# Preparação (pura, sem banco)
# ---------------------------------------------------------------------------

@dataclass
class ComprasPreparadas:
    # Uma linha por (cnpj, ean): cnpj, ean, descricao, laboratorio,
    # razao_social, quantidade, custo_unitario.
    compras: pd.DataFrame
    # TODO CNPJ presente no arquivo, inclusive os que só tinham linhas de
    # venda — é o conjunto que o envio substitui naquele mês.
    cnpjs_no_arquivo: set[str]
    linhas_total: int
    linhas_sem_chave: int  # sem CNPJ ou EAN: rodapé, linha de total, lixo
    linhas_sem_compra: int  # só venda: quantidade e valor de compra vazios
    linhas_invalidas: int  # compra com quantidade ou valor <= 0 / inconversível
    linhas_somadas: int = 0  # (cnpj, ean) repetido no arquivo, somado numa linha só


def preparar_compras(df: pd.DataFrame, mapa: dict[str, str]) -> ComprasPreparadas:
    faltando = CAMPOS_OBRIGATORIOS - mapa.keys()
    if faltando:
        raise ValueError(f"Mapeamento incompleto — faltam colunas para: {', '.join(sorted(faltando))}.")

    def _coluna(campo: str) -> pd.Series:
        if campo in mapa:
            return df[mapa[campo]]
        return pd.Series([None] * len(df), index=df.index, dtype=object)

    cnpj = _mapear_por_valor_distinto(_coluna("cnpj"), _somente_digitos)
    ean = _mapear_por_valor_distinto(_coluna("ean"), normalizar_ean)
    bruto_quantidade = _coluna("quantidade")
    bruto_custo = _coluna("custo_unitario")
    quantidade = _numeros(bruto_quantidade)
    custo = _numeros(bruto_custo)

    tem_chave = cnpj.ne("") & ean.ne("")
    sem_compra = tem_chave & bruto_quantidade.isna() & bruto_custo.isna()
    valida = tem_chave & (quantidade > 0) & (custo > 0)
    invalida = tem_chave & ~sem_compra & ~valida

    selecao = valida.to_numpy()
    compras = pd.DataFrame({
        "cnpj": cnpj[selecao].to_numpy(),
        "ean": ean[selecao].to_numpy(),
        "descricao": _mapear_por_valor_distinto(_coluna("descricao")[selecao], _texto_ou_vazio).to_numpy(),
        "laboratorio": _mapear_por_valor_distinto(_coluna("laboratorio")[selecao], _texto_ou_nulo).to_numpy(),
        "razao_social": _mapear_por_valor_distinto(_coluna("razao_social")[selecao], _texto_ou_nulo).to_numpy(),
        "quantidade": quantidade[selecao].to_numpy(),
        "custo_unitario": custo[selecao].to_numpy(),
    })
    # Recuo de preço (opcional): custo CMV por unidade vendida = Fat × %CMV
    # ÷ QTD, só quando os três existem e QTD > 0; e o "R$ Custo médio".
    fat = _numeros(_coluna("fat_liquido"))[selecao].to_numpy()
    pct = _fracao_de_percentual(_numeros(_coluna("pct_cmv")))[selecao].to_numpy()
    qtd_vendida = _numeros(_coluna("qtd_vendida"))[selecao].to_numpy()
    compras["fat_liquido"] = fat
    compras["pct_cmv"] = pct
    compras["qtd_vendida"] = qtd_vendida
    compras["custo_cmv_unitario"] = _custo_cmv(fat, pct, qtd_vendida)
    compras["custo_medio_planilha"] = _numeros(_coluna("custo_medio"))[selecao].to_numpy()
    antes = len(compras)
    compras = _somar_repetidas(compras, ["cnpj", "ean"])

    return ComprasPreparadas(
        compras=compras,
        cnpjs_no_arquivo=set(cnpj[cnpj.ne("")].unique()),
        linhas_total=len(df),
        linhas_sem_chave=int((~tem_chave).sum()),
        linhas_sem_compra=int(sem_compra.sum()),
        linhas_invalidas=int(invalida.sum()),
        linhas_somadas=antes - len(compras),
    )


def _custo_cmv(fat, pct, qtd_vendida):
    """Fat × %CMV ÷ QTD por linha; NaN quando falta algum dos três ou QTD ≤ 0."""
    fat, pct, qtd_vendida = (
        pd.to_numeric(pd.Series(x), errors="coerce").to_numpy(dtype=float) for x in (fat, pct, qtd_vendida)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(qtd_vendida > 0, fat * pct / qtd_vendida, np.nan)


def _somar_repetidas(compras: pd.DataFrame, chave: list[str]) -> pd.DataFrame:
    """A mesma chave repetida vira uma linha: quantidades somadas e custo
    unitário MÉDIO PONDERADO pela quantidade (valor total ÷ quantidade total),
    nunca a média simples. Textos, % CMV e Custo médio: fica o último. Fat e
    QTD vendida somam, e o custo CMV por unidade é recalculado a partir deles.
    Sem repetição (o caso normal), devolve o próprio DataFrame sem custo."""
    if not compras.duplicated(chave).any():
        return compras.reset_index(drop=True)
    trabalho = compras.assign(_valor=compras["quantidade"] * compras["custo_unitario"])
    ultimos = [
        c for c in ("descricao", "laboratorio", "razao_social", "pct_cmv", "custo_medio_planilha", "upload_fs_node_id")
        if c in compras.columns and c not in chave
    ]
    somados = [c for c in ("fat_liquido", "qtd_vendida") if c in compras.columns]
    agregado = trabalho.groupby(chave, sort=False, dropna=False).agg(
        quantidade=("quantidade", "sum"), _valor=("_valor", "sum"),
        **{c: (c, "last") for c in ultimos},
        **{c: (c, lambda serie: serie.sum(min_count=1)) for c in somados},
    ).reset_index()
    agregado["custo_unitario"] = agregado["_valor"] / agregado["quantidade"]
    if "custo_cmv_unitario" in compras.columns:
        agregado["custo_cmv_unitario"] = _custo_cmv(agregado["fat_liquido"], agregado["pct_cmv"], agregado["qtd_vendida"])
    return agregado.drop(columns="_valor")[list(compras.columns)]


# ---------------------------------------------------------------------------
# Gravação (uma transação)
# ---------------------------------------------------------------------------

@dataclass
class ResultadoAplicacao:
    compras_gravadas: int = 0
    compras_removidas: int = 0
    lojas_atualizadas: int = 0
    compras_orfas: int = 0
    cnpjs_orfaos: int = 0
    eans_novos_na_fila: int = 0
    eans_resolvidos_automaticamente: int = 0
    tempos: dict[str, float] = field(default_factory=dict)


ProgressoCallback = Callable[[str, int, int], None]


def lojas_por_cnpj(session: Session) -> dict[str, int]:
    """CNPJ (só dígitos) -> loja_id, incluindo os CNPJs órfãos que alguém já
    vinculou a uma loja — sem isso, o mesmo CNPJ voltaria a ser órfão no
    envio seguinte."""
    mapa = {_somente_digitos(cnpj): loja_id for loja_id, cnpj in session.execute(select(Loja.id, Loja.cnpj))}
    vinculados = session.execute(
        select(FilaCnpjOrfao.cnpj, FilaCnpjOrfao.resolvido_para_loja_id).where(
            FilaCnpjOrfao.status == StatusFila.RESOLVIDA, FilaCnpjOrfao.resolvido_para_loja_id.is_not(None),
        )
    )
    for cnpj, loja_id in vinculados:
        mapa.setdefault(_somente_digitos(cnpj), loja_id)
    return mapa


def _texto_limitado(valor, limite: int, vazio=None):
    """Texto pronto pra coluna do banco: NaN/None/"" viram `vazio`, e o resto
    é cortado no tamanho da coluna (o Postgres recusa texto maior)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return vazio
    texto = str(valor).strip()
    return texto[:limite] if texto else vazio


def _inserir_em_lote(session: Session, modelo, linhas: list[dict]) -> None:
    """INSERT em lote pela camada Core (a TABELA, não a entidade ORM).

    Pela entidade (`session.execute(insert(Modelo), linhas)`), o SQLAlchemy
    usa o "bulk insert" do ORM, que omite as colunas com None e AGRUPA as
    linhas pelo conjunto de colunas preenchidas — e ainda pede RETURNING id.
    Com os campos de recuo (Fat, %CMV, QTD, Custo médio) ora vazios, ora não,
    intercalados, os grupos viravam de uma linha só: medido em 24/09/2026,
    uma planilha de ~36 mil compras levava ~80 min (uma ida e volta de
    ~140 ms até o Postgres por linha). Pela tabela, o lote sai em instruções
    de muitas linhas, com os vazios gravados como NULL."""
    session.execute(insert(modelo.__table__), linhas)


def _em_blocos(itens: list, tamanho: int):
    for inicio in range(0, len(itens), tamanho):
        yield itens[inicio:inicio + tamanho]


def aplicar_compras(
    session: Session,
    preparadas: ComprasPreparadas,
    ano_mes: str,
    upload_fs_node_id: int | None,
    progresso: ProgressoCallback | None = None,
) -> ResultadoAplicacao:
    """Substitui, em `ano_mes`, as compras de todos os CNPJs presentes no
    arquivo pelas do arquivo, e recalcula as duas filas.

    NÃO faz commit: quem chama controla a transação (ver
    integrations/gps_processamento.py), e é isso que torna o envio tudo ou
    nada — nenhum commit intermediário, então uma falha em qualquer etapa
    desfaz tudo, inclusive o DELETE."""
    import time

    resultado = ResultadoAplicacao()
    marcar = time.perf_counter()

    def _tempo(etapa: str) -> None:
        nonlocal marcar
        agora = time.perf_counter()
        resultado.tempos[etapa] = round(agora - marcar, 2)
        marcar = agora

    def _avisar(fase: str, feito: int, total: int) -> None:
        if progresso is not None:
            progresso(fase, feito, total)

    compras = preparadas.compras
    mapa_lojas = lojas_por_cnpj(session)

    loja_ids = compras["cnpj"].map(mapa_lojas)
    casadas = compras[loja_ids.notna()].assign(loja_id=loja_ids[loja_ids.notna()].astype(int))
    orfas = compras[loja_ids.isna()]
    # Dois CNPJs do arquivo podem apontar pra mesma loja (um deles vinculado
    # manualmente na fila de órfãos): somar antes, senão (loja, EAN) repetido
    # violaria a chave única.
    casadas = _somar_repetidas(casadas.drop(columns="cnpj"), ["loja_id", "ean"])

    lojas_substituidas = sorted({mapa_lojas[c] for c in preparadas.cnpjs_no_arquivo if c in mapa_lojas})
    cnpjs_orfaos_substituidos = sorted(c for c in preparadas.cnpjs_no_arquivo if c not in mapa_lojas)
    resultado.lojas_atualizadas = len(lojas_substituidas)
    _tempo("preparo")

    # 1. Apaga o que o arquivo substitui.
    _avisar("Removendo os dados anteriores dessas lojas", 0, 1)
    for bloco in _em_blocos(lojas_substituidas, _TAMANHO_BLOCO_CHAVES):
        resultado.compras_removidas += session.execute(
            delete(RegistroCompraGPS).where(RegistroCompraGPS.ano_mes == ano_mes, RegistroCompraGPS.loja_id.in_(bloco))
        ).rowcount
    for bloco in _em_blocos(cnpjs_orfaos_substituidos, _TAMANHO_BLOCO_CHAVES):
        session.execute(delete(CompraGPSOrfa).where(CompraGPSOrfa.ano_mes == ano_mes, CompraGPSOrfa.cnpj.in_(bloco)))
    _tempo("remocao")

    # 2. Grava as compras novas.
    linhas = [
        {
            "loja_id": int(linha.loja_id), "ean": linha.ean,
            "descricao_origem": _texto_limitado(linha.descricao, 250, ""),
            "laboratorio_compra": _texto_limitado(linha.laboratorio, 150), "ano_mes": ano_mes,
            "quantidade": float(linha.quantidade), "custo_unitario": float(linha.custo_unitario),
            **{campo: _valor_ou_nulo(getattr(linha, campo)) for campo in CAMPOS_RECUO},
            "upload_fs_node_id": upload_fs_node_id,
        }
        for linha in casadas.itertuples(index=False)
    ]
    _avisar("Gravando compras", 0, len(linhas))
    for feito, bloco in enumerate(_em_blocos(linhas, _TAMANHO_BLOCO), start=1):
        _inserir_em_lote(session, RegistroCompraGPS, bloco)
        _avisar("Gravando compras", min(feito * _TAMANHO_BLOCO, len(linhas)), len(linhas))
    resultado.compras_gravadas = len(linhas)

    linhas_orfas = [
        {
            "cnpj": linha.cnpj, "razao_social": _texto_limitado(linha.razao_social, 200), "ean": linha.ean,
            "descricao_origem": _texto_limitado(linha.descricao, 250, ""),
            "laboratorio_compra": _texto_limitado(linha.laboratorio, 150),
            "ano_mes": ano_mes, "quantidade": float(linha.quantidade), "custo_unitario": float(linha.custo_unitario),
            **{campo: _valor_ou_nulo(getattr(linha, campo)) for campo in CAMPOS_RECUO},
            "upload_fs_node_id": upload_fs_node_id,
        }
        for linha in orfas.itertuples(index=False)
    ]
    for bloco in _em_blocos(linhas_orfas, _TAMANHO_BLOCO):
        _inserir_em_lote(session, CompraGPSOrfa, bloco)
    resultado.compras_orfas = len(linhas_orfas)
    resultado.cnpjs_orfaos = int(orfas["cnpj"].nunique())
    _tempo("gravacao")

    # 3. Filas.
    _avisar("Atualizando a fila de CNPJ órfão", 0, 1)
    recalcular_fila_cnpj_orfao(session, set(cnpjs_orfaos_substituidos))
    _tempo("fila_cnpj")

    _avisar("Reconciliando EANs com a Base Genéricos", 0, 1)
    novos, automaticos = _enfileirar_eans_novos(session, compras)
    resultado.eans_novos_na_fila = novos
    resultado.eans_resolvidos_automaticamente = automaticos
    reconciliation_motor.recalcular_valores_fila_ean(session)
    _tempo("fila_ean")
    return resultado


def _enfileirar_eans_novos(session: Session, compras: pd.DataFrame) -> tuple[int, int]:
    """EAN do arquivo que não está resolvido nem na fila ainda: passa pelo
    fuzzy-match (uma vez por EAN DISTINTO) e vai pra fila ou é aceito
    automaticamente. Valor/ocorrências NÃO são gravados aqui — quem grava é
    `recalcular_valores_fila_ean`, logo depois, a partir das compras vigentes.
    Devolve (enfileirados, resolvidos automaticamente)."""
    descricao_por_ean = dict(zip(compras["ean"], compras["descricao"]))
    resolvidos: dict[str, int] = {}
    reconciliation_motor.completar_cache_eans_resolvidos(session, set(descricao_por_ean), resolvidos)
    pendentes = sorted(set(descricao_por_ean) - resolvidos.keys())

    ja_na_fila: set[str] = set()
    for bloco in _em_blocos(pendentes, _TAMANHO_BLOCO_CHAVES):
        ja_na_fila.update(session.execute(select(FilaResolucaoEAN.ean).where(FilaResolucaoEAN.ean.in_(bloco))).scalars())

    buffer_fila: list[dict] = []
    cache_candidatos: dict = {}
    automaticos = 0
    with session.no_autoflush:
        for ean in pendentes:
            if ean in ja_na_fila:
                continue
            base_id = reconciliation_motor.resolver_ean(
                session, ean=ean, descricao_origem=descricao_por_ean[ean], origem=OrigemFila.GPS,
                valor=0, criado_por="sistema", cache_candidatos=cache_candidatos,
                cache_eans_resolvidos=resolvidos, cache_eans_completo=True, ocorrencias=0,
                buffer_fila=buffer_fila,
            )
            if base_id is not None:
                automaticos += 1
    reconciliation_motor.upsert_fila_resolucao_em_lote(session, buffer_fila)
    return len(buffer_fila), automaticos


def recalcular_fila_cnpj_orfao(session: Session, cnpjs: set[str] | None = None) -> None:
    """Valor e ocorrências da fila de CNPJ órfão a partir das compras órfãs
    VIGENTES (todos os meses) — nunca somados envio a envio. Cria o item da
    fila se o CNPJ é novo; mantém o status dos existentes (um CNPJ ignorado
    continua ignorado). `cnpjs=None` recalcula a fila inteira."""
    agregado = (
        select(
            CompraGPSOrfa.cnpj,
            func.max(CompraGPSOrfa.razao_social),
            func.sum(CompraGPSOrfa.quantidade * CompraGPSOrfa.custo_unitario),
            func.count(),
        )
        .group_by(CompraGPSOrfa.cnpj)
    )
    alvos = sorted(cnpjs) if cnpjs is not None else None
    linhas = []
    blocos = _em_blocos(alvos, _TAMANHO_BLOCO_CHAVES) if alvos is not None else [None]
    for bloco in blocos:
        stmt = agregado if bloco is None else agregado.where(CompraGPSOrfa.cnpj.in_(bloco))
        linhas.extend(session.execute(stmt).all())

    agora = dt.datetime.utcnow()
    valores = [
        {
            "cnpj": cnpj, "razao_social_observada": razao, "valor_total_acumulado": float(valor or 0),
            "qtd_ocorrencias": int(qtd), "status": StatusFila.PENDENTE, "criado_em": agora, "atualizado_em": agora,
        }
        for cnpj, razao, valor, qtd in linhas
    ]
    for bloco in _em_blocos(valores, _TAMANHO_BLOCO):
        stmt = insert_com_atualizacao(
            session, FilaCnpjOrfao.__table__, ["cnpj"],
            ["razao_social_observada", "valor_total_acumulado", "qtd_ocorrencias", "atualizado_em"],
        )
        session.execute(stmt, bloco)

    # CNPJ pendente que deixou de ter compra órfã (o envio novo não trouxe
    # mais nada dele): zera em vez de manter o valor antigo.
    com_compras = {cnpj for cnpj, *_ in linhas}
    zerar = (set(alvos) if alvos is not None else set(
        session.execute(select(FilaCnpjOrfao.cnpj).where(FilaCnpjOrfao.status == StatusFila.PENDENTE)).scalars()
    )) - com_compras
    for bloco in _em_blocos(sorted(zerar), _TAMANHO_BLOCO_CHAVES):
        session.execute(
            FilaCnpjOrfao.__table__.update()
            .where(FilaCnpjOrfao.cnpj.in_(bloco), FilaCnpjOrfao.status == StatusFila.PENDENTE)
            .values(valor_total_acumulado=0, qtd_ocorrencias=0, atualizado_em=agora)
        )


# ---------------------------------------------------------------------------
# Fila de CNPJ órfão
# ---------------------------------------------------------------------------

def listar_fila_cnpj_orfao_priorizada(session: Session, apenas_pendentes: bool = True, limite: int = 200) -> list[FilaCnpjOrfao]:
    stmt = select(FilaCnpjOrfao)
    if apenas_pendentes:
        stmt = stmt.where(FilaCnpjOrfao.status == StatusFila.PENDENTE)
    stmt = stmt.order_by(FilaCnpjOrfao.valor_total_acumulado.desc()).limit(limite)
    return list(session.execute(stmt).scalars().all())


def ignorar_cnpj_orfao(session: Session, fila_id: int) -> None:
    fila = session.get(FilaCnpjOrfao, fila_id)
    if fila is None:
        raise ValueError("Item da fila não encontrado.")
    fila.status = StatusFila.IGNORADA
    session.flush()


def _mover_compras_orfas(session: Session, loja_por_cnpj: dict[str, int]) -> int:
    """Move as compras órfãs destes CNPJs pra loja vinculada: grava em
    `registros_compra_gps` (substituindo, se a loja já tiver o mesmo EAN no
    mesmo mês) e apaga de `compras_gps_orfas`. Devolve quantas compras
    foram movidas."""
    cnpjs = sorted(loja_por_cnpj)
    if not cnpjs:
        return 0
    registros = []
    for bloco in _em_blocos(cnpjs, _TAMANHO_BLOCO_CHAVES):
        registros.extend(session.execute(select(CompraGPSOrfa).where(CompraGPSOrfa.cnpj.in_(bloco))).scalars())
    if registros:
        df = pd.DataFrame({
            "loja_id": [loja_por_cnpj[r.cnpj] for r in registros],
            "ean": [r.ean for r in registros],
            "ano_mes": [r.ano_mes for r in registros],
            "descricao": [r.descricao_origem for r in registros],
            "laboratorio": [r.laboratorio_compra for r in registros],
            "quantidade": [float(r.quantidade) for r in registros],
            "custo_unitario": [float(r.custo_unitario) for r in registros],
            **{campo: [_valor_ou_nulo(getattr(r, campo)) for r in registros] for campo in CAMPOS_RECUO},
            "upload_fs_node_id": [r.upload_fs_node_id for r in registros],
        })
        df = _somar_repetidas(df, ["loja_id", "ean", "ano_mes"])
        valores = [
            {
                "loja_id": int(linha.loja_id), "ean": linha.ean, "ano_mes": linha.ano_mes,
                "descricao_origem": _texto_limitado(linha.descricao, 250, ""),
                "laboratorio_compra": _texto_limitado(linha.laboratorio, 150),
                "quantidade": float(linha.quantidade), "custo_unitario": float(linha.custo_unitario),
                **{campo: _valor_ou_nulo(getattr(linha, campo)) for campo in CAMPOS_RECUO},
                "upload_fs_node_id": None if pd.isna(linha.upload_fs_node_id) else int(linha.upload_fs_node_id),
                "criado_em": dt.datetime.utcnow(),
            }
            for linha in df.itertuples(index=False)
        ]
        for bloco in _em_blocos(valores, _TAMANHO_BLOCO):
            stmt = insert_com_atualizacao(
                session, RegistroCompraGPS.__table__, ["loja_id", "ean", "ano_mes"],
                ["descricao_origem", "laboratorio_compra", "quantidade", "custo_unitario", *CAMPOS_RECUO,
                 "upload_fs_node_id"],
            )
            session.execute(stmt, bloco)
    for bloco in _em_blocos(cnpjs, _TAMANHO_BLOCO_CHAVES):
        session.execute(delete(CompraGPSOrfa).where(CompraGPSOrfa.cnpj.in_(bloco)))
    reconciliation_motor.recalcular_valores_fila_ean(session)
    return len(registros)


def resolver_cnpj_orfao(session: Session, fila_id: int, loja_id: int, resolvido_por: str) -> int:
    """Vincula um CNPJ órfão a uma loja: as compras dele passam pra loja na
    hora (sem reabrir planilha) e os próximos envios já o reconhecem."""
    fila = session.get(FilaCnpjOrfao, fila_id)
    if fila is None:
        raise ValueError("Item da fila não encontrado.")
    if session.get(Loja, loja_id) is None:
        raise ValueError("Loja não encontrada.")
    movidas = _mover_compras_orfas(session, {fila.cnpj: loja_id})
    fila.status = StatusFila.RESOLVIDA
    fila.resolvido_para_loja_id = loja_id
    session.flush()
    return movidas


def resolver_cnpjs_orfaos_identicos_em_lote(session: Session, resolvido_por: str) -> dict:
    """Resolve de uma vez todo CNPJ órfão pendente cujos dígitos batem com
    uma loja cadastrada (ex: a loja entrou na base depois do envio)."""
    fila_pendente = session.execute(
        select(FilaCnpjOrfao).where(FilaCnpjOrfao.status == StatusFila.PENDENTE)
    ).scalars().all()
    loja_id_por_cnpj = {_somente_digitos(cnpj): loja_id for loja_id, cnpj in session.execute(select(Loja.id, Loja.cnpj))}
    casados = {f.cnpj: loja_id_por_cnpj[_somente_digitos(f.cnpj)] for f in fila_pendente if _somente_digitos(f.cnpj) in loja_id_por_cnpj}
    if not casados:
        return {"resolvidos": 0, "linhas_inseridas": 0, "cnpjs": []}

    movidas = _mover_compras_orfas(session, casados)
    for fila in fila_pendente:
        if fila.cnpj in casados:
            fila.status = StatusFila.RESOLVIDA
            fila.resolvido_para_loja_id = casados[fila.cnpj]
    session.flush()
    return {"resolvidos": len(casados), "linhas_inseridas": movidas, "cnpjs": sorted(casados)}


# ---------------------------------------------------------------------------
# Explorador de Arquivos: exclusão definitiva de um envio
# ---------------------------------------------------------------------------

def buscar_upload_por_fs_node(session: Session, fs_node_id: int) -> UploadGPS | None:
    return session.execute(select(UploadGPS).where(UploadGPS.fs_node_id == fs_node_id)).scalar_one_or_none()


@dataclass
class ResultadoExclusaoUploadGPS:
    upload_id: int
    ano_mes: str
    registros_removidos: int
    storage_key: str | None
    # Atalho de linhas órfãs dos envios antigos (antes de compras_gps_orfas
    # existir) — purgado junto com o .xlsx, depois do commit.
    storage_key_orfaos: str | None = None


def excluir_upload_gps_definitivamente(session: Session, fs_node_id: int) -> ResultadoExclusaoUploadGPS:
    """Remove DE VERDADE um envio GPS: as compras que ainda pertencem a ele
    (normais e órfãs — o que um envio posterior já substituiu não é mais
    dele), o `UploadGPS` e o FSNode do arquivo. Recalcula as duas filas.
    NUNCA toca em Base Genéricos/EANs resolvidos.

    Devolve `storage_key` pra quem chamou apagar o byte físico só DEPOIS do
    commit — apagar antes deixaria o FSNode apontando pra um objeto
    inexistente se a transação desse rollback."""
    upload = buscar_upload_por_fs_node(session, fs_node_id)
    if upload is None:
        raise ValueError("Nenhum upload GPS vinculado a este arquivo.")

    upload_id, ano_mes = upload.id, upload.ano_mes
    storage_key_orfaos = gps_cache_orfaos.chave_para_purgar(upload)

    registros_removidos = session.execute(
        delete(RegistroCompraGPS).where(RegistroCompraGPS.upload_fs_node_id == fs_node_id)
    ).rowcount
    cnpjs_orfaos = set(
        session.execute(select(CompraGPSOrfa.cnpj).where(CompraGPSOrfa.upload_fs_node_id == fs_node_id).distinct()).scalars()
    )
    session.execute(delete(CompraGPSOrfa).where(CompraGPSOrfa.upload_fs_node_id == fs_node_id))

    session.delete(upload)
    session.flush()

    node = session.get(FSNode, fs_node_id)
    storage_key = node.storage_key if node is not None else None
    if node is not None:
        session.delete(node)
    session.flush()

    recalcular_fila_cnpj_orfao(session, cnpjs_orfaos)
    reconciliation_motor.recalcular_valores_fila_ean(session)

    return ResultadoExclusaoUploadGPS(
        upload_id=upload_id, ano_mes=ano_mes, registros_removidos=registros_removidos,
        storage_key=storage_key, storage_key_orfaos=storage_key_orfaos,
    )


def mensagem_resultado(nome_arquivo: str, ano_mes: str, preparadas: ComprasPreparadas, resultado: ResultadoAplicacao) -> str:
    partes = [
        f"{resultado.compras_gravadas:,} compras gravadas para {resultado.lojas_atualizadas} loja(s)".replace(",", "."),
    ]
    if resultado.compras_removidas:
        partes.append(f"{resultado.compras_removidas:,} compras anteriores dessas lojas substituídas".replace(",", "."))
    if resultado.compras_orfas:
        partes.append(
            f"{resultado.compras_orfas:,} compras de {resultado.cnpjs_orfaos} CNPJ(s) sem loja cadastrada "
            "(fila de CNPJ órfão)".replace(",", ".")
        )
    if resultado.eans_novos_na_fila:
        partes.append(f"{resultado.eans_novos_na_fila} EAN(s) novo(s) na fila de resolução")
    if resultado.eans_resolvidos_automaticamente:
        partes.append(f"{resultado.eans_resolvidos_automaticamente} EAN(s) resolvido(s) automaticamente")
    ignoradas = []
    if preparadas.linhas_sem_compra:
        ignoradas.append(f"{preparadas.linhas_sem_compra:,} só de venda".replace(",", "."))
    if preparadas.linhas_invalidas:
        ignoradas.append(f"{preparadas.linhas_invalidas} com quantidade ou valor ≤ 0")
    if preparadas.linhas_sem_chave:
        ignoradas.append(f"{preparadas.linhas_sem_chave} sem CNPJ/EAN (rodapé/total)")
    if ignoradas:
        partes.append("linhas ignoradas: " + ", ".join(ignoradas))
    return f"Planilha '{nome_arquivo}' ({ano_mes}): " + "; ".join(partes) + "."


class GpsAdapter(IntegrationAdapter):
    nome = "Compras das Lojas (GPS)"

    def status(self) -> StatusIntegracao:
        return StatusIntegracao.MANUAL

    def sincronizar(self, **kwargs) -> ResultadoSincronizacao:
        raise NotImplementedError(
            "Sem API do GPS em uso — use a aba Dados para subir a planilha de compras."
        )
