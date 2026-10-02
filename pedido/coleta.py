"""A execução da rotina: percorre as empresas do GPS (uma parte delas, quando
dividida em execuções paralelas), baixa o que falta e grava no armazenamento.

Por que empresa por empresa: o custo alto da API é o "aquecimento" da
EMPRESA — a primeira consulta leva de 30 s a mais de 8 min; depois, qualquer
loja dela responde em segundos (medido em 25–26/09/2026). Fazer tudo de uma
empresa em sequência paga esse custo uma vez só.

Falhas não param a execução: a unidade (uma consulta) que estourar o tempo
ou der erro volta pra uma fila e é tentada de novo DEPOIS das outras
empresas. Isso é de propósito: quando uma consulta pesada estoura, o
servidor do GPS costuma terminar o cálculo mesmo assim, e a nova tentativa
responde rápido. O que não sair nesta execução fica pra próxima — o plano
recalcula "o que falta" a partir do que já está salvo.

Nada de nome de loja, produto ou valor no log (o repositório e os logs do
GitHub Actions são públicos): só índices, tipos, períodos e tempos. O
relatório detalhado vai pro Spaces (privado).
"""
from __future__ import annotations

import datetime as dt
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Callable

from pedido import categorias, plano, pronto, transformar
from pedido.armazenamento import Armazenamento
from pedido.plano import COMPRAS, ESTOQUE, VENDAS, Chaves, Unidade


@dataclass
class Relatorio:
    hoje: str
    parte: str
    empresas_na_parte: int = 0
    empresas_processadas: int = 0
    empresas_sem_loja_ativa: int = 0
    lojas: int = 0
    unidades_ok: int = 0
    unidades_com_falha: int = 0
    arquivos_gravados: int = 0
    prontos_montados: int = 0
    parou_por_tempo: bool = False
    segundos: float = 0.0
    falhas: list[dict] = field(default_factory=list)
    pendentes_para_proxima: list[str] = field(default_factory=list)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Coleta:
    def __init__(self, cliente, armazenamento: Armazenamento, chaves: Chaves, hoje: dt.date,
                 meses_fechados: int, orcamento_segundos: float, tentativas_por_unidade: int,
                 filtro_lojas: Callable[[str, list[dict]], list[dict]] | None = None,
                 relogio: Callable[[], float] = time.monotonic) -> None:
        """`filtro_lojas(id_empresa, lojas_da_api)` → só as lojas que interessam
        (lojas ativas no RMC — decisão de 27/09/2026). None = todas."""
        self.cliente = cliente
        self.armaz = armazenamento
        self.chaves = chaves
        self.hoje = hoje
        self.meses_fechados = meses_fechados
        self.orcamento = orcamento_segundos
        self.tentativas = max(1, tentativas_por_unidade)
        self.filtro_lojas = filtro_lojas
        self.relogio = relogio
        self._inicio = relogio()

    # -- tempo ----------------------------------------------------------------

    def _acabou_o_tempo(self) -> bool:
        return self.relogio() - self._inicio >= self.orcamento

    # -- execução -------------------------------------------------------------

    def executar(self, empresas: list[str], parte: str) -> Relatorio:
        rel = Relatorio(hoje=self.hoje.isoformat(), parte=parte, empresas_na_parte=len(empresas))
        fila: list[tuple[Unidade, list[str], int]] = []  # (unidade, lojas da empresa, tentativas feitas)
        processadas: list[tuple[str, list[dict]]] = []

        for i, empresa in enumerate(empresas, 1):
            if self._acabou_o_tempo():
                rel.parou_por_tempo = True
                break
            try:
                lojas = self._lojas(empresa)
            except Exception as exc:  # noqa: BLE001 — a empresa fica pra próxima execução
                rel.falhas.append({"unidade": f"lojas {empresa}", "erro": _resumo(exc)})
                _log(f"empresa {i}/{len(empresas)}: falha ao listar lojas ({type(exc).__name__})")
                continue
            if not lojas:
                rel.empresas_sem_loja_ativa += 1
                continue
            rel.empresas_processadas += 1
            rel.lojas += len(lojas)
            processadas.append((empresa, lojas))
            unidades = self._plano(empresa, lojas)
            _log(f"empresa {i}/{len(empresas)}: {len(lojas)} loja(s), {len(unidades)} consulta(s) a fazer")
            for u in unidades:
                if self._acabou_o_tempo():
                    rel.parou_por_tempo = True
                    fila.append((u, lojas, 0))
                    continue
                if not self._tentar(u, lojas, rel):
                    fila.append((u, lojas, 1))

        # Segunda (e terceira) volta nas que falharam, depois de todo o resto.
        rodada = 1
        while fila and not self._acabou_o_tempo() and rodada < self.tentativas:
            rodada += 1
            _log(f"nova tentativa: {len(fila)} consulta(s) na fila (volta {rodada})")
            restante = []
            for u, lojas, feitas in fila:
                if self._acabou_o_tempo():
                    rel.parou_por_tempo = True
                    restante.append((u, lojas, feitas))
                elif not self._tentar(u, lojas, rel):
                    restante.append((u, lojas, feitas + 1))
            fila = restante

        rel.pendentes_para_proxima = [u.descricao for u, _, _ in fila]
        self._montar_prontos(processadas, rel)
        rel.segundos = round(self.relogio() - self._inicio, 1)
        return rel

    def _montar_prontos(self, processadas: list[tuple[str, list[dict]]], rel: Relatorio) -> None:
        """Junta os brutos de cada loja no "pronto" (pedido/pronto.py) já de
        madrugada, pra o primeiro a abrir o Pedido de manhã não pagar os
        ~2,6 s de ler ~60 arquivos do Spaces (medido em 28/09/2026). Loja cujo
        pronto já está em dia é pulada pela própria assinatura. Falha aqui
        não é grave: a tela monta o que faltar."""
        for empresa, lojas in processadas:
            vendas_das_lojas = []
            for loja in lojas:
                if self._acabou_o_tempo():
                    rel.parou_por_tempo = True
                    return
                try:
                    p = pronto.atualizar(self.armaz, self.chaves, empresa, str(loja["CodigoLoja"]), self.meses_fechados)
                    if p is not None:
                        rel.prontos_montados += 1
                        vendas_das_lojas.append(pronto.vendas_por_ean(p))
                except Exception as exc:  # noqa: BLE001
                    rel.falhas.append({"unidade": f"pronto {empresa}/{loja.get('CodigoLoja')}", "erro": _resumo(exc)})
            # O que cada loja vendeu vai pro catálogo da empresa (fila de EAN
            # "Loja (API)" no app — decisão de 27/09/2026).
            chave = self.chaves.catalogo(empresa)
            try:
                if vendas_das_lojas and self.armaz.existe(chave):
                    self.armaz.salvar_df(chave, categorias.completar_com_vendas(self.armaz.ler_df(chave), vendas_das_lojas))
            except Exception as exc:  # noqa: BLE001
                rel.falhas.append({"unidade": f"catálogo {empresa}", "erro": _resumo(exc)})

    def _lojas(self, empresa: str) -> list[dict]:
        lojas = list(self.cliente.listar_lojas(empresa))
        self.armaz.salvar_json(self.chaves.lojas_gps(empresa), lojas)
        if self.filtro_lojas is not None:
            lojas = self.filtro_lojas(empresa, lojas)
        return lojas

    def _plano(self, empresa: str, lojas: list[dict]) -> list[Unidade]:
        existentes = set(self.armaz.listar(self.chaves.empresa(empresa)))
        marcador = self.chaves.marcador_estoque(empresa)
        estoque_do_dia = self.armaz.ler_json(marcador).get("data") if marcador in existentes else None
        codigos = [str(l["CodigoLoja"]) for l in lojas]
        return plano.unidades_da_empresa(self.chaves, existentes, empresa, codigos, self.hoje,
                                         self.meses_fechados, estoque_do_dia)

    def _tentar(self, u: Unidade, lojas: list[dict], rel: Relatorio) -> bool:
        comeco = self.relogio()
        try:
            gravados = self._executar_unidade(u, [str(l["CodigoLoja"]) for l in lojas])
        except Exception as exc:  # noqa: BLE001 — fica na fila; a execução segue
            rel.unidades_com_falha += 1
            rel.falhas.append({"unidade": u.descricao, "erro": _resumo(exc)})
            _log(f"  falha: {u.tipo} {u.inicio or ''}..{u.fim or ''} ({type(exc).__name__}, {self.relogio() - comeco:.0f}s)")
            return False
        rel.unidades_ok += 1
        rel.arquivos_gravados += gravados
        _log(f"  ok: {u.tipo} {u.inicio or ''}..{u.fim or ''} ({self.relogio() - comeco:.0f}s)")
        return True

    def _executar_unidade(self, u: Unidade, lojas: list[str]) -> int:
        """Baixa tudo da unidade e SÓ ENTÃO grava — uma consulta interrompida
        no meio não deixa arquivo parcial que a retomada tomaria por pronto."""
        if u.tipo == VENDAS:
            df = transformar.vendas(list(self.cliente.listar_vendas_loja(u.empresa, u.loja, u.inicio, u.fim)))
            return self._gravar_periodo(u, VENDAS, {u.loja: df})
        if u.tipo == COMPRAS:
            df = transformar.compras(list(self.cliente.listar_compras(u.empresa, data_inicio=u.inicio, data_fim=u.fim)))
            return self._gravar_periodo(u, COMPRAS, transformar.por_loja(df, lojas))
        if u.tipo == ESTOQUE:
            df = transformar.estoque(list(self.cliente.listar_estoque_empresa(u.empresa)))
            gravados = 0
            for loja, fatia in transformar.por_loja(df, lojas).items():
                self.armaz.salvar_df(self.chaves.estoque(u.empresa, loja), fatia)
                gravados += 1
            self.armaz.salvar_df(self.chaves.catalogo(u.empresa), categorias.catalogo(df, lojas))
            gravados += 1
            # O marcador vai por último: se algo falhar antes, amanhã (ou na
            # próxima volta) o estoque da empresa é baixado de novo inteiro.
            self.armaz.salvar_json(self.chaves.marcador_estoque(u.empresa),
                                   {"data": self.hoje.isoformat(), "gerado_em": dt.datetime.now().isoformat(timespec="seconds")})
            return gravados + 1
        raise ValueError(f"tipo de unidade desconhecido: {u.tipo}")

    def _gravar_periodo(self, u: Unidade, tipo: str, por_loja: dict) -> int:
        gravados = 0
        for loja, df in por_loja.items():
            if u.mes_fechado:
                self.armaz.salvar_df(self.chaves.mes(u.empresa, loja, tipo, plano.ano_mes(u.inicio)), df)
                gravados += 1
            else:
                for dia, fatia in transformar.por_dia(df, u.inicio, u.fim).items():
                    self.armaz.salvar_df(self.chaves.dia(u.empresa, loja, tipo, dia), fatia)
                    gravados += 1
        return gravados


def _resumo(exc: Exception) -> str:
    """Tipo + começo da mensagem + última linha do traceback — o suficiente
    pra saber o que houve sem despejar resposta da API no relatório."""
    ultima = traceback.format_exception(exc)[-1].strip()
    return f"{type(exc).__name__}: {str(exc)[:300]} | {ultima[:200]}"


def dividir(empresas: list[str], parte: int, partes: int) -> list[str]:
    """Divisão estável entre execuções paralelas: pela posição na lista
    ORDENADA de ids — a mesma empresa cai sempre na mesma parte."""
    return [e for i, e in enumerate(sorted(empresas)) if i % partes == parte]


def relatorio_dict(rel: Relatorio) -> dict:
    return asdict(rel)


def ultimas_execucoes(armaz: Armazenamento, chaves: Chaves) -> tuple[str | None, list[dict]]:
    """(data, relatórios) da execução mais recente da rotina: o último
    relatório de cada parte naquele dia (uma parte pode ter rodado mais de
    uma vez — disparo manual + agendado). Pra tela Dados → Rotinas."""
    prefixo = f"{chaves.prefixo}/controle/execucoes/"
    chaves_rel = [c for c in armaz.listar(prefixo) if c.endswith(".json")]
    if not chaves_rel:
        return None, []
    data = max(c[len(prefixo):].split("/")[0] for c in chaves_rel)
    do_dia = sorted(c for c in chaves_rel if c[len(prefixo):].startswith(data + "/"))
    ultima_por_parte: dict[str, str] = {}
    for c in do_dia:  # nome: {parte}-{HHMMSS}.json — em ordem, a última vence
        parte = c.rsplit("/", 1)[1].rsplit("-", 1)[0]
        ultima_por_parte[parte] = c
    return data, [armaz.ler_json(c) for _, c in sorted(ultima_por_parte.items())]
