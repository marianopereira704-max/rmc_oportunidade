"""Integração com as tabelas de preço da Gruppy — 100% manual (upload de
planilha pelo admin), sem API prevista.

Cada upload é uma tabela de UM laboratório, com cobertura em uma ou mais UFs
escolhidas pelo admin no momento do upload (não vem da planilha — é assim que
resolvemos o problema de tributação por UF: a Análise de Oportunidade só
compara uma loja contra tabelas cuja cobertura inclui a UF da própria loja).

A vigência é granular por UF (`TabelaGruppyCobertura`): subir uma tabela nova
do mesmo laboratório só inativa as UFs que se sobrepõem com a nova — UFs que a
tabela anterior cobria e a nova não cobre continuam vigentes na tabela antiga.

Cada linha da planilha tem seu EAN reconciliado contra a Base Genéricos via
`reconciliation.motor.resolver_ean` — isso é side-effect (registra o EAN como
resolvido ou o enfileira), o item da tabela em si guarda só o EAN bruto (a
junção com o genérico é sempre feita depois, via EanGenerico, nunca guardada
aqui — ver core/models.py).

Planilha esperada (nomes de coluna flexíveis, sem acento/maiúscula):
  ean | codigo | sku            -> EAN do produto
  descricao | produto            -> descrição de origem
  modo "pronto":
    custo | custoliquido | preco -> custo líquido já pronto
  modo "bruto x desconto":
    precobruto | preco | valorbruto -> preço de tabela bruto
    desconto | percentualdesconto   -> desconto em % (ex: 15 = 15%)
"""
from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import (
    FSNode,
    ItemTabelaGruppy,
    ModoCustoGruppy,
    OrigemFila,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
)
from integrations import mapeamento as mapeamento_integ
from integrations.base import IntegrationAdapter, ResultadoSincronizacao, StatusIntegracao
from reconciliation import motor as reconciliation_motor
from reconciliation.normalizador import normalizar_ean, normalizar_percentual
from storage import filesystem

# Lista de UFs (pro seletor de cobertura) e sinônimos de coluna aceitos na
# planilha não ficam mais fixos aqui — vêm de `settings.geografia.ufs_brasil`
# e `settings.colunas.gruppy` (core/config.py), configuráveis via
# secrets/env sem precisar editar código.


def _normalizar_coluna(col: str) -> str:
    """Só letras e dígitos sobrevivem — não só espaço/underscore. Uma
    planilha Gruppy real trouxe "R$ Unitário Bruto" (cifrão); qualquer
    pontuação no cabeçalho (cifrão, ponto, %, hífen, barra) precisa cair
    fora pra comparar de forma robusta contra os sinônimos de
    settings.colunas.gruppy, em vez de exigir um sinônimo novo toda vez
    que aparece uma variação de pontuação (mesmo ajuste já feito em
    integrations/gps.py::_normalizar_coluna)."""
    col = unicodedata.normalize("NFKD", str(col)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", col.strip().lower())


def campos_obrigatorios(modo_custo: ModoCustoGruppy) -> set[str]:
    obrigatorias = {"ean", "descricao"}
    obrigatorias |= {"custo"} if modo_custo == ModoCustoGruppy.PRONTO else {"preco_bruto", "desconto"}
    return obrigatorias


def detectar_colunas_automatico(df: pd.DataFrame, modo_custo: ModoCustoGruppy) -> dict[str, str]:
    """Sugestão por sinônimo, sem validar se todo campo obrigatório foi
    encontrado — usada tanto por `mapear_colunas` (que valida) quanto pelo
    popup de confirmação de mapeamento (que precisa da sugestão mesmo
    incompleta, pro usuário preencher manualmente o que faltar)."""
    colunas = settings.colunas.gruppy
    colunas_ean = set(colunas.get("ean", []))
    colunas_descricao = set(colunas.get("descricao", []))
    colunas_custo_pronto = set(colunas.get("custo_pronto", []))
    colunas_preco_bruto = set(colunas.get("preco_bruto", []))
    colunas_desconto = set(colunas.get("desconto", []))

    mapa: dict[str, str] = {}
    for col in df.columns:
        norm = _normalizar_coluna(col)
        if norm in colunas_ean and "ean" not in mapa:
            mapa["ean"] = col
        elif norm in colunas_descricao and "descricao" not in mapa:
            mapa["descricao"] = col
        elif modo_custo == ModoCustoGruppy.PRONTO and norm in colunas_custo_pronto and "custo" not in mapa:
            mapa["custo"] = col
        elif modo_custo == ModoCustoGruppy.BRUTO_DESCONTO:
            if norm in colunas_preco_bruto and "preco_bruto" not in mapa:
                mapa["preco_bruto"] = col
            elif norm in colunas_desconto and "desconto" not in mapa:
                mapa["desconto"] = col
    return mapa


def mapear_colunas(df: pd.DataFrame, modo_custo: ModoCustoGruppy) -> dict[str, str]:
    mapa = detectar_colunas_automatico(df, modo_custo)
    faltando = campos_obrigatorios(modo_custo) - mapa.keys()
    if faltando:
        raise ValueError(
            f"A planilha precisa ter colunas para: {', '.join(sorted(faltando))}. "
            f"Colunas encontradas: {', '.join(str(c) for c in df.columns)}"
        )
    return mapa


def _inativar_cobertura_sobreposta(session: Session, laboratorio: str, ufs_novas: list[str], inativada_por: str) -> None:
    """Upload novo do mesmo laboratório só inativa as UFs que se repetem —
    UFs cobertas por tabelas antigas e não incluídas na nova continuam
    vigentes normalmente."""
    coberturas_ativas = session.execute(
        select(TabelaGruppyCobertura)
        .join(TabelaGruppy, TabelaGruppyCobertura.tabela_gruppy_id == TabelaGruppy.id)
        .where(
            TabelaGruppy.laboratorio == laboratorio,
            TabelaGruppyCobertura.status == StatusCobertura.ATIVA,
            TabelaGruppyCobertura.uf.in_(ufs_novas),
        )
    ).scalars().all()
    agora = dt.datetime.utcnow()
    for cobertura in coberturas_ativas:
        cobertura.status = StatusCobertura.INATIVA
        cobertura.inativada_em = agora
        cobertura.inativada_por = inativada_por


def laboratorios_existentes(session: Session) -> list[str]:
    linhas = session.execute(select(TabelaGruppy.laboratorio).distinct().order_by(TabelaGruppy.laboratorio)).all()
    return [linha[0] for linha in linhas]


def processar_planilha_gruppy(
    session: Session,
    conteudo: bytes,
    nome_arquivo: str,
    criado_por: str,
    pasta_destino_id: int,
    laboratorio: str,
    ufs: list[str],
    modo_custo: ModoCustoGruppy,
    mapa_confirmado: dict[str, str] | None = None,
    df: pd.DataFrame | None = None,
) -> ResultadoSincronizacao:
    """`mapa_confirmado` vem do popup de confirmação de mapeamento (campo ->
    coluna escolhido pelo usuário) — quando informado, substitui a detecção
    automática por sinônimo pra ESTA planilha. Ainda validamos que nenhum
    campo obrigatório ficou de fora, como segurança contra um mapeamento
    incompleto vindo de um chamador que não seja o popup (que já bloqueia
    isso na própria UI).

    `df`, se informado, evita reler/reparsear o Excel — mesmo raciocínio de
    `integrations.gps.processar_planilha_gps` (o popup de mapeamento já leu
    a planilha inteira pra montar o preview)."""
    laboratorio = laboratorio.strip()
    if not laboratorio:
        raise ValueError("Informe o laboratório da tabela.")
    if not ufs:
        raise ValueError("Selecione ao menos uma UF de cobertura.")

    if df is None:
        df = pd.read_excel(io.BytesIO(conteudo))
    if mapa_confirmado is not None:
        faltando = campos_obrigatorios(modo_custo) - mapa_confirmado.keys()
        if faltando:
            raise ValueError(f"Mapeamento incompleto — faltam colunas para: {', '.join(sorted(faltando))}.")
        mapa = mapa_confirmado
    else:
        mapa = mapear_colunas(df, modo_custo)

    node = filesystem.salvar_arquivo(
        session, pasta_destino_id, nome_arquivo, conteudo, criado_por,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    tabela = TabelaGruppy(
        laboratorio=laboratorio,
        modo_custo=modo_custo,
        nome_arquivo_origem=nome_arquivo,
        upload_fs_node_id=node.id,
        mapa_colunas_json=mapeamento_integ.serializar_mapa(mapa),
        criado_por=criado_por,
    )
    session.add(tabela)
    session.flush()

    # vigência granular por UF: inativa só a sobreposição, depois abre cobertura ativa pra nova tabela
    _inativar_cobertura_sobreposta(session, laboratorio, ufs, criado_por)
    for uf in ufs:
        session.add(TabelaGruppyCobertura(tabela_gruppy_id=tabela.id, uf=uf, status=StatusCobertura.ATIVA))

    processados = 0
    ignorados_sem_ean = 0
    cache_candidatos: dict = {}  # ver reconciliation/motor.py::buscar_candidatos
    cache_eans_resolvidos = reconciliation_motor.carregar_cache_eans_resolvidos(session)
    cache_fila_pendente: dict = {}  # ver reconciliation/motor.py::upsert_fila_resolucao
    cache_descricao_pendente: dict = {}  # ver reconciliation/motor.py::resolver_ean

    # Mesma técnica de integrations/gps.py::_dataframe_normalizado — colunas
    # renomeadas por campo (não por nome de coluna da planilha) permitem
    # `itertuples` (atributo fixo, ex: `linha.ean`) em vez de `iterrows`
    # (Series inteira reconstruída por linha).
    df_norm = pd.DataFrame({campo: df[coluna] for campo, coluna in mapa.items()})
    novos_itens: list[ItemTabelaGruppy] = []

    with session.no_autoflush:  # ver justificativa detalhada em integrations/gps.py::_processar_linhas
        for linha in df_norm.itertuples(index=False):
            ean = normalizar_ean(linha.ean)
            if not ean or ean.lower() == "nan":
                ignorados_sem_ean += 1
                continue
            descricao = str(linha.descricao).strip()

            if modo_custo == ModoCustoGruppy.PRONTO:
                custo = float(linha.custo)
                preco_bruto = None
                percentual_desconto = None
            else:
                preco_bruto = float(linha.preco_bruto)
                # normalizar_percentual já devolve fração (0-1), detectando a
                # escala igual ao % de CMV do GPS — nunca dividir por 100 de
                # novo aqui, senão uma planilha que já vem em fração (0.84)
                # sai ~100x menor que o desconto real (vira 0,84%, não 84%).
                percentual_desconto = normalizar_percentual(linha.desconto)
                custo = preco_bruto * (1 - percentual_desconto)

            novos_itens.append(
                ItemTabelaGruppy(
                    tabela_gruppy_id=tabela.id,
                    ean=ean,
                    descricao_origem=descricao,
                    custo_liquido=custo,
                    preco_bruto=preco_bruto,
                    percentual_desconto=percentual_desconto,
                )
            )

            # Reconciliação de EAN é side-effect aqui: registra resolvido ou
            # enfileira. `valor` fica 0 de propósito — a Gruppy é catálogo de
            # preço unitário, não valor transacionado; a fila prioriza por
            # dinheiro real em jogo, que só o GPS carrega (ver reconciliation/motor.py).
            reconciliation_motor.resolver_ean(
                session, ean=ean, descricao_origem=descricao, origem=OrigemFila.GRUPPY,
                valor=0, aparece_em_estoque=False, criado_por="sistema",
                cache_candidatos=cache_candidatos, cache_eans_resolvidos=cache_eans_resolvidos,
                cache_fila_pendente=cache_fila_pendente, cache_descricao_pendente=cache_descricao_pendente,
            )
            processados += 1

    if novos_itens:
        session.add_all(novos_itens)

    msg = f"{processados} itens importados da tabela '{laboratorio}' ({', '.join(ufs)})."
    if ignorados_sem_ean:
        msg += f" {ignorados_sem_ean} linhas ignoradas por EAN vazio."

    return ResultadoSincronizacao(status=StatusIntegracao.MANUAL, registros_processados=processados, mensagem=msg)


def buscar_tabela_por_fs_node(session: Session, fs_node_id: int) -> TabelaGruppy | None:
    """Achado da investigação (Passo 1): `TabelaGruppy.upload_fs_node_id` já
    existe desde sempre e já é populado em `processar_planilha_gruppy` — não
    precisou de coluna nova. Usada pelo Explorador de Arquivos pra decidir se
    um arquivo tem "Excluir definitivamente" no menu (só existe pra Gruppy —
    GPS/Base Genéricos não têm esse elo de volta pro FSNode)."""
    return session.execute(
        select(TabelaGruppy).where(TabelaGruppy.upload_fs_node_id == fs_node_id)
    ).scalar_one_or_none()


@dataclass
class ResultadoExclusaoTabelaGruppy:
    tabela_id: int
    laboratorio: str
    itens_removidos: int
    coberturas_removidas: int


def excluir_tabela_gruppy_definitivamente(session: Session, fs_node_id: int) -> ResultadoExclusaoTabelaGruppy:
    """Remove DE VERDADE (não inativa) uma tabela de preço Gruppy — escopo
    travado só a Gruppy, de propósito: GPS e Base Genéricos não têm elo de
    volta pro FSNode (só `TabelaGruppy.upload_fs_node_id` existe), então essa
    função nem acha o que apagar pra eles.

    Ordem de exclusão (filhos antes do pai, depois o arquivo): ItemTabelaGruppy
    -> TabelaGruppyCobertura -> TabelaGruppy -> FSNode do arquivo. NUNCA toca
    em RegistroCompraGPS/UploadGPS/BaseGenerico/EanGenerico/FilaResolucaoEAN/
    FilaCnpjOrfao — mesmo que um EAN desta tabela já tenha sido reconciliado,
    EanGenerico é histórico de decisão (não pertence à tabela de preço em
    si); apagar a tabela não desfaz uma reconciliação já feita, nem mexe em
    nenhuma outra tabela Gruppy (cada uma só apaga a própria linhagem)."""
    tabela = session.execute(
        select(TabelaGruppy).where(TabelaGruppy.upload_fs_node_id == fs_node_id)
    ).scalar_one_or_none()
    if tabela is None:
        raise ValueError("Nenhuma tabela Gruppy vinculada a este arquivo.")

    tabela_id = tabela.id
    laboratorio = tabela.laboratorio

    itens_removidos = session.execute(
        delete(ItemTabelaGruppy).where(ItemTabelaGruppy.tabela_gruppy_id == tabela_id)
    ).rowcount
    coberturas_removidas = session.execute(
        delete(TabelaGruppyCobertura).where(TabelaGruppyCobertura.tabela_gruppy_id == tabela_id)
    ).rowcount

    session.delete(tabela)
    session.flush()

    node = session.get(FSNode, fs_node_id)
    if node is not None:
        session.delete(node)

    session.flush()

    return ResultadoExclusaoTabelaGruppy(
        tabela_id=tabela_id,
        laboratorio=laboratorio,
        itens_removidos=itens_removidos,
        coberturas_removidas=coberturas_removidas,
    )


class GruppyAdapter(IntegrationAdapter):
    nome = "Tabelas de Preço (Gruppy)"

    def status(self) -> StatusIntegracao:
        return StatusIntegracao.MANUAL

    def sincronizar(self, **kwargs) -> ResultadoSincronizacao:
        raise NotImplementedError(
            "Sem API da Gruppy prevista — use a aba Dados para subir a planilha de preços."
        )
