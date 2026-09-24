"""Processamento de uma planilha GPS em SEGUNDO PLANO.

O que isto resolve: antes, o processamento rodava dentro da execução da
tela do Streamlit — se a aba fechasse, o websocket caísse ou a pessoa
clicasse em outra coisa, o trabalho morria no meio. Agora a tela só dispara
(`iniciar`) e acompanha (`estado_atual`); o trabalho roda numa thread do
próprio processo, que não depende da aba aberta.

Por que thread e não subprocesso ou worker: um worker permanente não cabe no
orçamento de memória do servidor, e um subprocesso reimportaria o app inteiro
(~130 MB a mais) só pra isso. A thread nasce no clique e termina com o
trabalho — nada fica rodando à toa.

Garantias:
- UM processamento por vez, em qualquer aba/usuário: trava atômica no banco
  (`rotinas.reivindicar_trava`), liberada sempre ao terminar.
- TUDO OU NADA: arquivo no Explorador, registro do upload, substituição das
  compras e recálculo das filas acontecem numa transação só. Se algo falhar,
  nada muda no banco e o original enviado ao Spaces é apagado.
- O resultado (sucesso ou erro) fica em `controle_rotinas`, então sobrevive a
  um reinício do app; o progresso fino (fase, linhas) fica só em memória.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
import traceback
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from core import rotinas
from core.db import get_session
from core.models import OrigemFila, UploadGPS
from integrations import gps, mapeamento as mapeamento_integ
from integrations.planilha_navegador import PlanilhaRecebida
from storage import filesystem

logger = logging.getLogger(__name__)

# A trava expira se ninguém a renovar por este tempo — é o que destrava o
# upload quando o app é parado/cai no meio (em 24/09/2026, com 30 min fixos,
# um envio interrompido deixou a tela recusando novos envios por meia hora).
# Enquanto o envio está vivo, ele renova a trava (ver `_RENOVAR_A_CADA_S`).
JANELA_TRAVA_MINUTOS = 5
_RENOVAR_A_CADA_S = 60


@dataclass
class EstadoProcessamento:
    nome_arquivo: str
    ano_mes: str
    usuario: str
    iniciado_em: dt.datetime
    fase: str = "Iniciando"
    feito: int = 0
    total: int = 0
    concluido: bool = False
    sucesso: bool = False
    mensagem: str | None = None
    erro: str | None = None
    tempos: dict[str, float] = field(default_factory=dict)
    lido_na_tela: bool = False


_estado: EstadoProcessamento | None = None
_trava_estado = threading.Lock()


def estado_atual() -> EstadoProcessamento | None:
    with _trava_estado:
        return _estado


def marcar_resultado_visto() -> None:
    with _trava_estado:
        if _estado is not None and _estado.concluido:
            _estado.lido_na_tela = True


def _atualizar(**campos) -> None:
    with _trava_estado:
        if _estado is not None:
            for nome, valor in campos.items():
                setattr(_estado, nome, valor)


FabricaSessao = Callable[[], AbstractContextManager[Session]]


def iniciar(
    planilha: PlanilhaRecebida,
    preparadas: gps.ComprasPreparadas,
    mapa: dict[str, str],
    ano_mes: str,
    pasta_destino_id: int,
    usuario: str,
    fabrica_sessao: FabricaSessao = get_session,
    em_segundo_plano: bool = True,
) -> tuple[bool, str | None]:
    """Dispara o processamento. Devolve `(True, None)` se começou, ou
    `(False, motivo)` se a planilha não tem nenhuma compra ou se já existe
    outro processamento em andamento.

    `em_segundo_plano=False` roda na própria chamada — usado pelos testes e
    por scripts, com o mesmo código do caminho da tela."""
    global _estado
    if preparadas.compras.empty:
        # Planilha sem nenhuma linha de compra (VlrUnitario e Quantidade) —
        # ex: tabela Gruppy ou Base Genéricos enviada na seção do GPS. Sem
        # esta trava, o envio "substituiria" os CNPJs do arquivo por nada.
        return False, (
            "Nenhuma compra encontrada nesta planilha (linhas com CNPJ, EAN, VlrUnitario e Quantidade). "
            "Confira se é a exportação de compras do GPS e o mapeamento das colunas. Nada foi importado."
        )
    with fabrica_sessao() as sessao:
        ganhou = rotinas.reivindicar_trava(
            sessao, rotinas.ROTINA_UPLOAD_GPS, janela_tentativa_minutos=JANELA_TRAVA_MINUTOS,
        )
    if not ganhou:
        with fabrica_sessao() as sessao:
            controle = rotinas.obter(sessao, rotinas.ROTINA_UPLOAD_GPS)
            desde = controle.ultima_tentativa_em if controle else None
        quando = f" desde {desde:%H:%M} (UTC)" if desde else ""
        return False, f"Já existe um processamento de planilha GPS em andamento{quando}. Aguarde ele terminar."

    with _trava_estado:
        _estado = EstadoProcessamento(
            nome_arquivo=planilha.nome, ano_mes=ano_mes, usuario=usuario, iniciado_em=dt.datetime.utcnow(),
        )

    argumentos = (planilha, preparadas, mapa, ano_mes, pasta_destino_id, usuario, fabrica_sessao)
    if em_segundo_plano:
        threading.Thread(target=_executar, args=argumentos, name="processamento-gps", daemon=True).start()
    else:
        _executar(*argumentos)
    return True, None


def _executar(
    planilha: PlanilhaRecebida,
    preparadas: gps.ComprasPreparadas,
    mapa: dict[str, str],
    ano_mes: str,
    pasta_destino_id: int,
    usuario: str,
    fabrica_sessao: FabricaSessao,
) -> None:
    inicio = time.perf_counter()
    ultima_renovacao = [time.monotonic()]

    def _progresso(fase: str, feito: int, total: int) -> None:
        _atualizar(fase=fase, feito=feito, total=total)
        if time.monotonic() - ultima_renovacao[0] >= _RENOVAR_A_CADA_S:
            ultima_renovacao[0] = time.monotonic()
            try:
                with fabrica_sessao() as sessao_trava:
                    rotinas.renovar_trava(sessao_trava, rotinas.ROTINA_UPLOAD_GPS)
            except Exception:  # renovar é melhor esforço — nunca derruba o envio
                logger.warning("Não consegui renovar a trava do upload GPS", exc_info=True)

    try:
        with fabrica_sessao() as sessao:
            _progresso("Registrando o arquivo", 0, 1)
            node = filesystem.guardar_planilha(
                sessao, pasta_destino_id, planilha.nome, usuario,
                conteudo=planilha.conteudo, storage_key=planilha.storage_key, tamanho_bytes=planilha.tamanho_bytes,
            )
            sessao.add(UploadGPS(
                fs_node_id=node.id, ano_mes=ano_mes, criado_por=usuario,
                mapa_colunas_json=mapeamento_integ.serializar_mapa(mapa),
            ))
            sessao.flush()
            resultado = gps.aplicar_compras(sessao, preparadas, ano_mes, node.id, progresso=_progresso)
            mapeamento_integ.confirmar_mapeamento(sessao, OrigemFila.GPS, mapa, usuario)
            _progresso("Confirmando a gravação", 1, 1)
        # Commit feito ao sair do `with` acima — só daqui pra baixo o envio existe.

        resultado.tempos["total"] = round(time.perf_counter() - inicio, 2)
        mensagem = gps.mensagem_resultado(planilha.nome, ano_mes, preparadas, resultado)
        logger.info("Upload GPS concluído em %.1fs: %s | tempos %s", resultado.tempos["total"], mensagem, resultado.tempos)
        with fabrica_sessao() as sessao:
            rotinas.registrar_sucesso(sessao, rotinas.ROTINA_UPLOAD_GPS, mensagem)
        _atualizar(concluido=True, sucesso=True, mensagem=mensagem, tempos=resultado.tempos, fase="Concluído")
    except Exception as exc:
        logger.error("Upload GPS falhou: %s\n%s", exc, traceback.format_exc())
        # A transação já voltou atrás (get_session faz rollback). O original
        # que o navegador mandou ao Spaces não tem mais FSNode apontando pra
        # ele — apaga, senão fica esquecido no bucket.
        if planilha.storage_key:
            try:
                filesystem.backend().excluir_fisicamente(planilha.storage_key)
            except Exception:
                logger.warning("Não consegui apagar o original %s após a falha", planilha.storage_key, exc_info=True)
        erro = f"{type(exc).__name__}: {exc}"
        try:
            with fabrica_sessao() as sessao:
                rotinas.registrar_falha(sessao, rotinas.ROTINA_UPLOAD_GPS, erro)
        except Exception:
            logger.warning("Não consegui registrar a falha do upload GPS", exc_info=True)
        _atualizar(concluido=True, sucesso=False, erro=erro, fase="Falhou")
    finally:
        try:
            with fabrica_sessao() as sessao:
                rotinas.liberar_trava(sessao, rotinas.ROTINA_UPLOAD_GPS)
        except Exception:
            logger.error("Não consegui liberar a trava do upload GPS — ela expira em %d min", JANELA_TRAVA_MINUTOS, exc_info=True)
