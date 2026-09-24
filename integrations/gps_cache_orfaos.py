"""Atalho de releitura das linhas de CNPJ órfão de um upload GPS.

O problema que isto resolve. Quando uma linha da planilha tem um CNPJ que
ainda não está cadastrado como Loja, ela não é descartada — vai pra fila de
CNPJ órfão (`FilaCnpjOrfao`) e fica esperando alguém vincular aquele CNPJ a
uma loja. No momento em que isso acontece, `_reprocessar_cnpjs` precisa das
LINHAS daquele CNPJ de volta, e o único lugar onde elas existiam era o .xlsx
original. Reabrir o Excel custa ~7s por arquivo numa planilha real de 142 mil
linhas (medido) — pra, no fim, inserir talvez algumas centenas de linhas.

O que este módulo faz. No momento do upload, quando as linhas órfãs JÁ estão
identificadas e JÁ estão normalizadas, grava só elas num arquivo lateral, ao
lado do .xlsx no mesmo storage. O reprocesso lê esse arquivo (milissegundos)
em vez de reabrir o Excel. Medido no pior caso imaginável — um arquivo
inteiro de 142 mil linhas todas de um CNPJ não cadastrado — dá 0,49s contra
6,7s; no caso realista, de alguns milhares de linhas órfãs, dá milissegundos.

Por que JSON comprimido, e não Parquet ou CSV:

- Parquet exigiria `pyarrow`, que não está no requirements.txt. Não vale
  adicionar uma dependência de dezenas de MB ao deploy por causa disto.
- CSV traz de volta, na ida e volta, exatamente os bugs que o resto deste
  projeto já combateu: CNPJ "00000000000001" volta como o número 1, e não
  há como distinguir `None` (laboratório ausente -> NULL no banco) de `""`.
- JSON preserva str, float, None e a diferença entre "" e None sem precisar
  declarar tipo de coluna nenhum. Isso não é conveniência: é o que faz este
  módulo sobreviver às colunas NOVAS das próximas etapas (valor unitário,
  quantidade comprada, data da nota) sem precisar ser alterado.

Guardado em COLUNAS, não em linhas: é o formato que `_processar_linhas` já
consome (`_colunas_normalizadas` devolve dict de listas), então ler o atalho
não custa nenhuma conversão, e o JSON fica bem menor do que repetiria o nome
de cada campo em cada linha.

`descartar` NÃO é guardado, e é reconstruído como False na leitura. Não é
economia: é uma verdade estrutural do laço de `_processar_linhas` — a
checagem de linha-lixo acontece ANTES da de CNPJ órfão, então nenhuma linha
que chegou a ser órfã era lixo. Guardar o campo abriria a porta pra um
atalho corrompido reintroduzir linha-lixo num reprocesso; reconstruir fecha
essa porta. O teste `test_linha_orfa_nunca_e_lixo` trava essa premissa.

Falha ao gravar NUNCA derruba o upload: o atalho é otimização, e o .xlsx
continua lá como fonte da verdade. Um atalho ausente (upload antigo, ou
gravação que falhou) faz o reprocesso cair no caminho de sempre — ler o
Excel. É por isso que `UploadGPS.storage_key_orfaos` é nulo por padrão: NULL
quer dizer "não tem atalho, lê o Excel", e não "erro"."""
from __future__ import annotations

import gzip
import json
import logging

from sqlalchemy.orm import Session

from core.models import FSNode, UploadGPS
from storage import filesystem

logger = logging.getLogger(__name__)

# Sobe quando o conteúdo do atalho mudar de forma que a versão anterior não
# sirva mais. Atalho de versão desconhecida é IGNORADO (cai pro Excel), nunca
# interpretado na marra — ver `ler`.
VERSAO_FORMATO = 1

# Reconstruído na leitura em vez de guardado — ver a docstring do módulo.
_CAMPO_RECONSTRUIDO = "descartar"

_NIVEL_COMPRESSAO = 6  # ~10x de redução; nível 9 comprime ~3% mais e custa o dobro do tempo.


def _chave_do_atalho(node: FSNode) -> str:
    """Deriva do `storage_key` do próprio .xlsx (que já carrega um uuid), pra
    o atalho viver ao lado do arquivo que ele resume e herdar a unicidade
    dele — sem inventar um segundo espaço de nomes."""
    return f"{node.storage_key}.orfaos.json.gz"


def _serializar(colunas: dict[str, list], indices: list[int]) -> bytes:
    recorte = {
        campo: [valores[i] for i in indices]
        for campo, valores in colunas.items()
        if campo != _CAMPO_RECONSTRUIDO
    }
    bruto = json.dumps({"versao": VERSAO_FORMATO, "colunas": recorte}).encode("utf-8")
    return gzip.compress(bruto, _NIVEL_COMPRESSAO)


def gravar(session: Session, upload: UploadGPS, node: FSNode, saida_orfaos: dict) -> int:
    """Grava as linhas órfãs deste upload e devolve quantas foram gravadas.

    `saida_orfaos` é o que `_processar_linhas` devolve pelo parâmetro de
    mesmo nome: `{"colunas": <dict de listas>, "indices": <posições das
    linhas órfãs>}`. Nenhuma linha órfã -> nada é gravado e
    `storage_key_orfaos` fica NULL, que é o mesmo que "não precisa de
    atalho" (o reprocesso nem vai acontecer).

    Qualquer falha aqui é registrada e engolida: o upload do usuário não pode
    falhar por causa de uma otimização de leitura futura."""
    indices = saida_orfaos.get("indices") or []
    colunas = saida_orfaos.get("colunas")
    if not indices or not colunas:
        return 0

    try:
        conteudo = _serializar(colunas, indices)
        filesystem.backend().salvar(_chave_do_atalho(node), conteudo)
    except Exception:
        logger.exception(
            "Falha ao gravar o atalho de linhas órfãs do upload GPS %s — o upload segue "
            "normalmente e o reprocesso vai reler o Excel, como antes.", node.nome,
        )
        return 0

    upload.storage_key_orfaos = _chave_do_atalho(node)
    session.flush()
    logger.info(
        "Atalho de linhas órfãs gravado para o upload GPS %s: %d linha(s), %.1f KB.",
        node.nome, len(indices), len(conteudo) / 1024,
    )
    return len(indices)


def ler(upload: UploadGPS) -> dict[str, list] | None:
    """Colunas JÁ normalizadas das linhas órfãs deste upload, no mesmo
    formato de `_colunas_normalizadas`, ou `None` quando não há atalho
    utilizável — e aí o chamador lê o Excel, como sempre fez.

    `None` (e não exceção) também em atalho corrompido, ausente do storage ou
    de versão desconhecida: nenhum desses casos justifica derrubar a
    resolução de um CNPJ órfão, já que a fonte da verdade continua sendo o
    .xlsx."""
    if not upload.storage_key_orfaos:
        return None

    try:
        bruto = filesystem.backend().ler(upload.storage_key_orfaos)
        pacote = json.loads(gzip.decompress(bruto))
    except Exception:
        logger.exception(
            "Atalho de linhas órfãs ilegível (storage_key=%s) — reprocessando pelo Excel.",
            upload.storage_key_orfaos,
        )
        return None

    if not isinstance(pacote, dict) or pacote.get("versao") != VERSAO_FORMATO:
        logger.warning(
            "Atalho de linhas órfãs em versão não reconhecida (%r, esperada %d) — "
            "reprocessando pelo Excel.", (pacote or {}).get("versao"), VERSAO_FORMATO,
        )
        return None

    colunas = pacote.get("colunas")
    if not isinstance(colunas, dict) or not colunas:
        return None

    total = len(next(iter(colunas.values())))
    if any(len(valores) != total for valores in colunas.values()):
        logger.warning("Atalho de linhas órfãs com colunas de tamanhos diferentes — reprocessando pelo Excel.")
        return None

    # Ver a docstring do módulo: linha órfã, por construção do laço, nunca é
    # linha de lixo.
    colunas[_CAMPO_RECONSTRUIDO] = [False] * total
    return colunas


def chave_para_purgar(upload: UploadGPS | None) -> str | None:
    """A chave do atalho pra quem for apagar o byte físico DEPOIS do commit
    da exclusão — mesmo cuidado (e mesmo motivo) do .xlsx: apagar antes
    deixaria o arquivo destruído se a transação desse rollback."""
    return upload.storage_key_orfaos if upload is not None else None
