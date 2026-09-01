"""Integração com o GPS (compras das lojas) — 100% manual (upload de
planilha pelo admin), sem API prevista.

Cada upload cobre UM mês de compras (`ano_mes`, escolhido pelo admin no
popup — a planilha real não traz isso numa coluna própria). O arquivo já traz
o estoque atual junto (coluna `QtdEstoque` na mesma linha da compra — não é
upload separado, ver core/models.py).

Três coisas acontecem por linha:
1. Linha de lixo (CNPJ vazio, campo numérico que não parseia) é ignorada e
   contada — nunca processada como se fosse dado real.
2. CNPJ que não bate com nenhuma Loja cadastrada vai pra fila de CNPJ órfão
   (nunca é descartado). Vincular um item da fila a uma loja (manual, 1 por
   vez) reprocessa automaticamente via `resolver_cnpj_orfao` — relê os
   arquivos GPS já enviados e insere as linhas daquele CNPJ que tinham
   ficado de fora. `resolver_cnpjs_orfaos_identicos_em_lote` faz o mesmo pra
   todos os CNPJs cujo texto bate exato com uma Loja já cadastrada, de uma
   vez só — os dois reaproveitam `_reprocessar_cnpjs`, que lê cada arquivo
   uma única vez independente de quantos CNPJs estão sendo resolvidos.
3. EAN da linha é reconciliado contra a Base Genéricos via
   `reconciliation.motor.resolver_ean` (side-effect: resolve ou enfileira).

Planilha esperada (nomes de coluna flexíveis, sem acento/maiúscula):
  cnpj                         -> CNPJ da loja
  ean | codigo | sku           -> EAN do produto
  descricao | produto          -> descrição de origem
  quantidade | qtd             -> quantidade comprada no mês
  faturamentoliquido | fatliquido -> valor total pago no mês (Fat_liquido)
  pctcmv | percentualcmv       -> % de CMV (fração 0-1 ou percentual 0-100,
                                   normalizado automaticamente)
  qtdestoque | estoque         -> estoque atual (mesma linha/arquivo)
  laboratorio | fornecedor (opcional) -> pode ficar vazio, nunca "não informado"
  razaosocial (opcional)       -> só usada se o CNPJ cair na fila de órfãos
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import FilaCnpjOrfao, FSNode, Loja, OrigemFila, RegistroCompraGPS, StatusFila, StatusNode, UploadGPS
from integrations import mapeamento as mapeamento_integ
from integrations.base import IntegrationAdapter, ResultadoSincronizacao, StatusIntegracao
from reconciliation import motor as reconciliation_motor
from reconciliation.normalizador import normalizar_ean, normalizar_percentual
from storage import filesystem

logger = logging.getLogger(__name__)

# Sinônimos de coluna aceitos na planilha não ficam mais fixos aqui — vêm de
# `settings.colunas.gps` (core/config.py), configuráveis via secrets/env
# sem precisar editar código.


def _normalizar_coluna(col: str) -> str:
    """Só letras e dígitos sobrevivem — não só espaço/underscore. Planilha
    real trouxe "Fat. líquido" (ponto) e "% CMV" (o "%cmv" só batia com o
    sinônimo por coincidência); qualquer pontuação no cabeçalho (ponto, %,
    hífen, barra) precisa cair fora pra comparar de forma robusta contra os
    sinônimos de settings.colunas.gps, em vez de exigir um sinônimo novo
    toda vez que aparece uma variação de pontuação."""
    col = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", col.strip().lower())


CAMPOS_OBRIGATORIOS = {"cnpj", "ean", "descricao", "quantidade", "fat_liquido", "pct_cmv", "estoque"}


def detectar_colunas_automatico(df: pd.DataFrame) -> dict[str, str]:
    """Sugestão por sinônimo, sem validar se todo campo obrigatório foi
    encontrado — usada tanto por `mapear_colunas` (que valida) quanto pelo
    popup de confirmação de mapeamento (que precisa da sugestão mesmo
    incompleta, pro usuário preencher manualmente o que faltar)."""
    colunas = settings.colunas.gps
    mapa: dict[str, str] = {}
    for col in df.columns:
        norm = _normalizar_coluna(col)
        for chave in (
            "cnpj", "ean", "descricao", "quantidade", "fat_liquido",
            "pct_cmv", "estoque", "laboratorio", "razao_social",
        ):
            if norm in set(colunas.get(chave, [])) and chave not in mapa:
                mapa[chave] = col
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


def _somente_digitos(valor) -> str:
    """CNPJ pode chegar formatado ("30.208.213/0001-74"), como texto puro de
    dígitos, ou (perigo comum de Excel) como número que perdeu zeros à
    esquerda — normalizamos tudo pra 14 dígitos com zero-padding antes de
    comparar/gravar, nunca comparando string formatada contra string crua."""
    if valor is None:
        return ""
    if isinstance(valor, float):
        if pd.isna(valor):
            return ""
        valor = str(int(round(valor)))
    texto = "".join(ch for ch in str(valor) if ch.isdigit())
    return texto.zfill(14) if texto else ""


def _linha_e_lixo(cnpj_digitos: str, ean: str) -> bool:
    return not cnpj_digitos or not ean or ean.lower() == "nan"


def upsert_fila_cnpj_orfao(session: Session, cnpj_digitos: str, razao_social: str | None, valor: float) -> FilaCnpjOrfao:
    fila = session.execute(select(FilaCnpjOrfao).where(FilaCnpjOrfao.cnpj == cnpj_digitos)).scalar_one_or_none()
    if fila is None:
        fila = FilaCnpjOrfao(
            cnpj=cnpj_digitos,
            razao_social_observada=razao_social,
            valor_total_acumulado=valor or 0,
            qtd_ocorrencias=1,
            status=StatusFila.PENDENTE,
        )
        session.add(fila)
    else:
        fila.valor_total_acumulado = float(fila.valor_total_acumulado or 0) + float(valor or 0)
        fila.qtd_ocorrencias = (fila.qtd_ocorrencias or 0) + 1
        if razao_social and not fila.razao_social_observada:
            fila.razao_social_observada = razao_social
    session.flush()
    return fila


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


def _dataframe_normalizado(df: pd.DataFrame, mapa: dict[str, str]) -> pd.DataFrame:
    """Monta um DataFrame com uma coluna por CAMPO (não por nome de coluna da
    planilha original) — 'cnpj', 'ean', 'laboratorio' etc., sempre presentes
    (as opcionais viram None quando não mapeadas). Isso permite iterar com
    `itertuples` (atributo por nome fixo do domínio, ex: `linha.cnpj`) em vez
    de `iterrows` (que materializa uma Series inteira, com coerção de dtype,
    a cada linha — o gargalo medido em profiling real pra 150 mil linhas)."""
    colunas = {campo: df[coluna] for campo, coluna in mapa.items()}
    for campo_opcional in ("laboratorio", "razao_social"):
        colunas.setdefault(campo_opcional, None)
    return pd.DataFrame(colunas)


def _processar_linhas(
    session: Session,
    df: pd.DataFrame,
    mapa: dict[str, str],
    ano_mes: str,
    upload_fs_node_id: int,
    lojas_por_cnpj: dict[str, Loja],
    apenas_cnpjs: set[str] | None = None,
    cache_candidatos: dict | None = None,
    cache_eans_resolvidos: dict[str, int] | None = None,
    cache_fila_pendente: dict | None = None,
    cache_descricao_pendente: dict | None = None,
) -> dict[str, int]:
    """Núcleo compartilhado entre upload normal e reprocessamento de CNPJ
    órfão (manual — 1 item — ou em lote — N itens de uma vez, ver
    `_reprocessar_cnpjs`). `apenas_cnpjs` filtra pra só esses CNPJs; `None`
    processa a planilha inteira (fluxo de upload original).

    `cache_candidatos`/`cache_eans_resolvidos` (opcionais) repassam pra
    `reconciliation_motor.resolver_ean` (ver lá) — medido via profiling real
    (planilha de ~150 mil linhas): sem esses caches, resolver_ean sozinho
    dominava o tempo de processamento, muito à frente de leitura de arquivo
    ou inserts. Os chamadores passam o MESMO dict pra toda a duração de um
    upload/reprocesso — ver `processar_planilha_gps` e `_reprocessar_cnpjs`.

    `existentes_registro_por_chave` é pré-carregado abaixo (1 SELECT pra
    todo o `ano_mes`, não 1 por linha) — mesmo motivo: o profiling mostrou
    que reconsultar RegistroCompraGPS linha a linha (upsert manual) era a
    outra metade do tempo restante depois de cachear resolver_ean.

    `session.no_autoflush`: sem isso, toda SELECT feita dentro do loop (em
    `resolver_ean`/`upsert_fila_resolucao`, pra EAN ainda não resolvido)
    dispara um flush do SQLAlchemy de TODOS os objetos pendentes na sessão
    antes de rodar — com até ~150 mil `RegistroCompraGPS` acumulados via
    `session.add`, isso deixa o custo de flush crescente ao longo do loop
    (quanto mais linhas já processadas, mais caro o flush da próxima SELECT).
    Seguro desligar aqui porque todo ponto do código que grava e depois
    RELÊ o que gravou (`resolver_ean`, `upsert_fila_resolucao`,
    `upsert_fila_cnpj_orfao`) já dá `session.flush()` explícito logo após
    escrever — nenhuma SELECT deste loop depende de autoflush implícito pra
    enxergar uma escrita anterior; `RegistroCompraGPS` em si nunca é
    reconsultado dentro do loop (só via `existentes_registro_por_chave`, um
    dict em memória, não uma query)."""
    contadores = {"processados": 0, "atualizados": 0, "linhas_lixo": 0, "cnpj_orfao": 0}

    existentes_registro_por_chave = {
        (r.loja_id, r.ean): r
        for r in session.execute(
            select(RegistroCompraGPS).where(RegistroCompraGPS.ano_mes == ano_mes)
        ).scalars().all()
    }

    df_norm = _dataframe_normalizado(df, mapa)
    novos_registros: list[RegistroCompraGPS] = []

    with session.no_autoflush:
        for linha in df_norm.itertuples(index=False):
            cnpj_digitos = _somente_digitos(linha.cnpj)
            ean = normalizar_ean(linha.ean)

            if apenas_cnpjs is not None and cnpj_digitos not in apenas_cnpjs:
                continue
            if _linha_e_lixo(cnpj_digitos, ean):
                contadores["linhas_lixo"] += 1
                continue

            try:
                quantidade = float(linha.quantidade)
                fat_liquido = float(linha.fat_liquido)
                pct_cmv = normalizar_percentual(linha.pct_cmv)
                estoque = float(linha.estoque) if pd.notna(linha.estoque) else 0.0
            except (ValueError, TypeError):
                contadores["linhas_lixo"] += 1
                continue

            loja = lojas_por_cnpj.get(cnpj_digitos)
            if loja is None:
                if apenas_cnpjs is None:  # reprocesso já filtrou pelos CNPJs resolvidos — não deveria cair aqui
                    razao_social = (
                        str(linha.razao_social).strip() if pd.notna(linha.razao_social) else None
                    )
                    upsert_fila_cnpj_orfao(session, cnpj_digitos, razao_social, fat_liquido)
                    contadores["cnpj_orfao"] += 1
                continue

            descricao = str(linha.descricao).strip()
            laboratorio_compra = None
            if pd.notna(linha.laboratorio):
                texto = str(linha.laboratorio).strip()
                laboratorio_compra = texto if texto and texto.lower() != "nan" else None

            custo_unitario = (fat_liquido * pct_cmv) / quantidade if quantidade else 0.0

            chave = (loja.id, ean)
            existente = existentes_registro_por_chave.get(chave)

            if existente is not None:
                existente.descricao_origem = descricao
                existente.laboratorio_compra = laboratorio_compra
                existente.quantidade = quantidade
                existente.fat_liquido = fat_liquido
                existente.pct_cmv = pct_cmv
                existente.custo_unitario = custo_unitario
                existente.estoque = estoque
                existente.upload_fs_node_id = upload_fs_node_id
                contadores["atualizados"] += 1
            else:
                novo_registro = RegistroCompraGPS(
                    loja_id=loja.id,
                    ean=ean,
                    descricao_origem=descricao,
                    laboratorio_compra=laboratorio_compra,
                    ano_mes=ano_mes,
                    quantidade=quantidade,
                    fat_liquido=fat_liquido,
                    pct_cmv=pct_cmv,
                    custo_unitario=custo_unitario,
                    estoque=estoque,
                    upload_fs_node_id=upload_fs_node_id,
                )
                novos_registros.append(novo_registro)
                # mesma chave repetida MAIS ADIANTE no mesmo arquivo precisa achar
                # este registro como "existente" — sem isso, duplicaria a linha e
                # violaria a UniqueConstraint (loja_id, ean, ano_mes) na hora do flush.
                existentes_registro_por_chave[chave] = novo_registro
                contadores["processados"] += 1

            reconciliation_motor.resolver_ean(
                session, ean=ean, descricao_origem=descricao, origem=OrigemFila.GPS,
                valor=fat_liquido, aparece_em_estoque=estoque > 0, criado_por="sistema",
                cache_candidatos=cache_candidatos, cache_eans_resolvidos=cache_eans_resolvidos,
                cache_fila_pendente=cache_fila_pendente, cache_descricao_pendente=cache_descricao_pendente,
            )

    if novos_registros:
        # add_all em lote em vez de session.add() por linha — evita reinserir
        # no identity map da sessão 150 mil vezes espalhado pelo loop.
        session.add_all(novos_registros)

    return contadores


def processar_planilha_gps(
    session: Session, conteudo: bytes, nome_arquivo: str, criado_por: str, pasta_destino_id: int, ano_mes: str,
    mapa_confirmado: dict[str, str] | None = None, df: pd.DataFrame | None = None,
) -> ResultadoSincronizacao:
    """`mapa_confirmado` vem do popup de confirmação de mapeamento (campo ->
    coluna escolhido pelo usuário) — quando informado, substitui a detecção
    automática por sinônimo pra ESTA planilha. Ainda validamos que nenhum
    campo obrigatório ficou de fora, como segurança contra um mapeamento
    incompleto vindo de um chamador que não seja o popup (que já bloqueia
    isso na própria UI).

    `df`, se informado, evita reler/reparsear o Excel — o popup de
    mapeamento já leu a planilha inteira pra montar o preview de colunas
    (`pd.read_excel` sozinho leva ~19s numa planilha GPS real de 150 mil
    linhas); sem isso, o mesmo parse acontecia 2x: uma no preview, outra
    aqui. `conteudo` continua obrigatório (é o que vai pro storage do
    arquivo bruto, ver `filesystem.salvar_arquivo`)."""
    if df is None:
        df = pd.read_excel(io.BytesIO(conteudo))
    if mapa_confirmado is not None:
        faltando = CAMPOS_OBRIGATORIOS - mapa_confirmado.keys()
        if faltando:
            raise ValueError(f"Mapeamento incompleto — faltam colunas para: {', '.join(sorted(faltando))}.")
        mapa = mapa_confirmado
    else:
        mapa = mapear_colunas(df)

    node = filesystem.salvar_arquivo(
        session, pasta_destino_id, nome_arquivo, conteudo, criado_por,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    session.add(UploadGPS(
        fs_node_id=node.id, ano_mes=ano_mes, criado_por=criado_por,
        mapa_colunas_json=mapeamento_integ.serializar_mapa(mapa),
    ))

    lojas = session.execute(select(Loja)).scalars().all()
    lojas_por_cnpj = {_somente_digitos(loja.cnpj): loja for loja in lojas}

    contadores = _processar_linhas(
        session, df, mapa, ano_mes, node.id, lojas_por_cnpj,
        cache_candidatos={}, cache_eans_resolvidos=reconciliation_motor.carregar_cache_eans_resolvidos(session),
        cache_fila_pendente={}, cache_descricao_pendente={},
    )

    partes = [f"{contadores['processados']} compras importadas"]
    if contadores["atualizados"]:
        partes.append(f"{contadores['atualizados']} atualizadas (já existiam nesse mês)")
    if contadores["cnpj_orfao"]:
        partes.append(f"{contadores['cnpj_orfao']} linhas em CNPJ ainda não cadastrado (fila de CNPJ órfão)")
    if contadores["linhas_lixo"]:
        partes.append(f"{contadores['linhas_lixo']} linhas ignoradas (lixo/rodapé/dado inválido)")

    return ResultadoSincronizacao(
        status=StatusIntegracao.MANUAL,
        registros_processados=contadores["processados"],
        mensagem=f"Planilha '{nome_arquivo}' ({ano_mes}): " + "; ".join(partes) + ".",
    )


def _uploads_gps_ja_processados(session: Session) -> list[tuple[FSNode, UploadGPS]]:
    """(FSNode, UploadGPS) de cada upload GPS ainda ativo — via UploadGPS, não
    inferido de linhas já importadas, então funciona mesmo se um arquivo
    inteiro só tivesse linhas de CNPJ órfão."""
    linhas = session.execute(
        select(UploadGPS, FSNode)
        .join(FSNode, UploadGPS.fs_node_id == FSNode.id)
        .where(FSNode.status == StatusNode.ATIVO)
    ).all()
    return [(fs_node, upload) for upload, fs_node in linhas]


def _reprocessar_cnpjs(session: Session, lojas_por_cnpj: dict[str, Loja]) -> int:
    """Relê cada arquivo GPS já enviado UMA vez (não uma vez por CNPJ) e
    processa, numa única passada por arquivo, as linhas de todos os CNPJs em
    `lojas_por_cnpj`. Núcleo compartilhado entre o reprocesso manual de 1
    CNPJ órfão e a resolução em lote — só muda quantos CNPJs entram no
    dict; ler o arquivo 1x pra N CNPJs em vez de N vezes (1 por CNPJ) é o
    que torna a resolução em lote viável (segundos em vez de horas pra uma
    planilha de ~150 mil linhas).

    Usa o mapeamento EXATO gravado naquele upload (`UploadGPS.mapa_colunas_json`)
    pra reler o arquivo — nunca o "último mapeamento confirmado" (que é
    global por fornecedor e pode já ter mudado desde então), senão o
    reprocesso poderia aplicar um mapeamento diferente do que foi usado de
    verdade na hora do upload original. Upload de antes desse campo existir
    (mapa_colunas_json nulo) cai pra heurística automática, como sempre foi
    — com aviso no log, já que essa planilha nunca teve confirmação humana.

    Um único `cache_candidatos`/`cache_eans_resolvidos` (ver
    reconciliation/motor.py) é reaproveitado por TODOS os arquivos deste
    reprocesso — o catálogo de genéricos ativos e os EANs já resolvidos não
    mudam entre eles, então não faz sentido rebuscar do zero a cada
    arquivo, só a cada resolução de CNPJ órfão como um todo."""
    cnpjs = set(lojas_por_cnpj.keys())
    total_inseridas = 0
    cache_candidatos: dict = {}
    cache_eans_resolvidos = reconciliation_motor.carregar_cache_eans_resolvidos(session)
    cache_fila_pendente: dict = {}
    cache_descricao_pendente: dict = {}

    for node, upload in _uploads_gps_ja_processados(session):
        conteudo = filesystem.ler_arquivo(node)
        df = pd.read_excel(io.BytesIO(conteudo))

        mapa = mapeamento_integ.desserializar_mapa(upload.mapa_colunas_json)
        if mapa is None:
            logger.warning(
                "Upload GPS %s (ano_mes=%s) não tem mapeamento de colunas salvo — "
                "reprocessando CNPJ órfão com a heurística automática atual, que pode "
                "não ser idêntica à usada no upload original.",
                node.nome, upload.ano_mes,
            )
            mapa = mapear_colunas(df)

        contadores = _processar_linhas(
            session, df, mapa, upload.ano_mes, node.id, lojas_por_cnpj, apenas_cnpjs=cnpjs,
            cache_candidatos=cache_candidatos, cache_eans_resolvidos=cache_eans_resolvidos,
            cache_fila_pendente=cache_fila_pendente, cache_descricao_pendente=cache_descricao_pendente,
        )
        total_inseridas += contadores["processados"] + contadores["atualizados"]

    return total_inseridas


def resolver_cnpj_orfao(session: Session, fila_id: int, loja_id: int, resolvido_por: str) -> int:
    """CNPJ órfão vinculado a uma loja (fluxo manual, 1 item por vez, usado
    pela tela): reprocessa AUTOMATICAMENTE relendo os arquivos GPS já
    enviados e inserindo as linhas daquele CNPJ que tinham ficado de fora."""
    fila = session.get(FilaCnpjOrfao, fila_id)
    if fila is None:
        raise ValueError("Item da fila não encontrado.")
    loja = session.get(Loja, loja_id)
    if loja is None:
        raise ValueError("Loja não encontrada.")

    total_inseridas = _reprocessar_cnpjs(session, {fila.cnpj: loja})

    fila.status = StatusFila.RESOLVIDA
    fila.resolvido_para_loja_id = loja_id
    session.flush()
    return total_inseridas


def resolver_cnpjs_orfaos_identicos_em_lote(session: Session, resolvido_por: str) -> dict:
    """Resolve de uma vez todo item PENDENTE da fila de CNPJ órfão cujo CNPJ
    bate exatamente (string idêntica) com uma Loja já cadastrada — usa o
    mesmo `_reprocessar_cnpjs` do fluxo manual, só que com todos os CNPJs
    batidos de uma vez, então cada arquivo GPS é lido uma única vez no
    total (não uma vez por CNPJ). CNPJs sem bate exato ficam intocados na
    fila, pra revisão manual."""
    fila_pendente = session.execute(
        select(FilaCnpjOrfao).where(FilaCnpjOrfao.status == StatusFila.PENDENTE)
    ).scalars().all()
    lojas_por_cnpj_todas = {loja.cnpj: loja for loja in session.execute(select(Loja)).scalars().all()}

    fila_por_cnpj = {f.cnpj: f for f in fila_pendente}
    cnpjs_com_match = sorted(set(fila_por_cnpj) & set(lojas_por_cnpj_todas))

    if not cnpjs_com_match:
        return {"resolvidos": 0, "linhas_inseridas": 0, "cnpjs": []}

    lojas_dos_matches = {cnpj: lojas_por_cnpj_todas[cnpj] for cnpj in cnpjs_com_match}
    total_inseridas = _reprocessar_cnpjs(session, lojas_dos_matches)

    for cnpj in cnpjs_com_match:
        fila = fila_por_cnpj[cnpj]
        fila.status = StatusFila.RESOLVIDA
        fila.resolvido_para_loja_id = lojas_dos_matches[cnpj].id
    session.flush()

    return {
        "resolvidos": len(cnpjs_com_match),
        "linhas_inseridas": total_inseridas,
        "cnpjs": cnpjs_com_match,
    }


class GpsAdapter(IntegrationAdapter):
    nome = "Compras das Lojas (GPS)"

    def status(self) -> StatusIntegracao:
        return StatusIntegracao.MANUAL

    def sincronizar(self, **kwargs) -> ResultadoSincronizacao:
        raise NotImplementedError(
            "Sem API do GPS prevista — use a aba Dados para subir a planilha de compras."
        )
