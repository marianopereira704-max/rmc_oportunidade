"""O que falta coletar — carga inicial e atualização diária são a MESMA
conta: "o que da janela ainda não está salvo". Na primeira execução falta
tudo (a carga inicial); nas seguintes, só o dia anterior e o estoque.

Janela (decisão de 25/09/2026): os N meses FECHADOS anteriores + os dias já
decorridos do mês corrente, até ontem. Ex.: rodando em 27/09 com N=3 →
junho, julho e agosto inteiros + 1 a 26 de setembro.

Chaves no armazenamento (tudo sob `settings.pedido.prefixo`):

    bruto/{empresa}/{loja}/vendas/2026-06.parquet            mês fechado
    bruto/{empresa}/{loja}/vendas/2026-09/2026-09-26.parquet dia do mês corrente
    bruto/{empresa}/{loja}/compras/…                         idem
    bruto/{empresa}/{loja}/estoque.parquet                   foto mais recente
    bruto/{empresa}/_estoque.json                            data da foto
    catalogo/{empresa}.parquet                               produtos das lojas (com o estoque)
    categorias/base_inicial.parquet (+ .json)                FEBRAFAR + CMED unidas
    controle/lojas_gps/{empresa}.json                        lojas da empresa
    controle/execucoes/{data}/{parte}-{hora}.json            relatório

Por que mês fechado num arquivo e mês corrente por dia: o mês corrente cresce
um dia por noite — com um arquivo por dia a rotina diária só baixa ONTEM (1
página por loja). Quando o mês fecha, a rotina baixa o mês inteiro de novo
num arquivo só (um mês é o maior intervalo que a API aguenta nas redes
grandes: 3 meses deram erro 500 ou passaram de 8 min na MEGA FARMA).

Janela de uma consulta: vendas por LOJA e no máximo 1 mês; compras e
estoque pela rota da EMPRESA (a de compras por loja deu erro 500 na MEGA
FARMA e na Reis; a de estoque da empresa é 3× mais rápida por linha).
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

VENDAS = "vendas"
COMPRAS = "compras"
ESTOQUE = "estoque"


def ano_mes(data: dt.date) -> str:
    return f"{data.year:04d}-{data.month:02d}"


def _primeiro_dia(data: dt.date) -> dt.date:
    return data.replace(day=1)


def _ultimo_dia(ano: int, mes: int) -> dt.date:
    return dt.date(ano, mes, calendar.monthrange(ano, mes)[1])


def _mes_menos(data: dt.date, meses: int) -> dt.date:
    total = data.year * 12 + (data.month - 1) - meses
    return dt.date(total // 12, total % 12 + 1, 1)


def janela(hoje: dt.date, meses_fechados: int) -> tuple[dt.date, dt.date]:
    """(primeiro dia, ontem)."""
    return _mes_menos(_primeiro_dia(hoje), meses_fechados), hoje - dt.timedelta(days=1)


@dataclass(frozen=True)
class Unidade:
    """Uma consulta à API (ou um conjunto de páginas dela) que, quando
    termina, grava arquivos que a marcam como feita."""
    tipo: str                 # vendas | compras | estoque
    empresa: str
    loja: str | None          # vendas: a loja; compras/estoque: None (empresa inteira)
    inicio: dt.date | None = None
    fim: dt.date | None = None
    mes_fechado: bool = False

    @property
    def descricao(self) -> str:
        onde = f"{self.empresa}/{self.loja}" if self.loja else self.empresa
        periodo = f" {self.inicio}..{self.fim}" if self.inicio else ""
        return f"{self.tipo} {onde}{periodo}"


# ---------------------------------------------------------------------------
# Chaves
# ---------------------------------------------------------------------------

@dataclass
class Chaves:
    prefixo: str

    def empresa(self, empresa: str) -> str:
        return f"{self.prefixo}/bruto/{empresa}/"

    def mes(self, empresa: str, loja: str, tipo: str, mes: str) -> str:
        return f"{self.prefixo}/bruto/{empresa}/{loja}/{tipo}/{mes}.parquet"

    def dia(self, empresa: str, loja: str, tipo: str, data: dt.date) -> str:
        return f"{self.prefixo}/bruto/{empresa}/{loja}/{tipo}/{ano_mes(data)}/{data.isoformat()}.parquet"

    def estoque(self, empresa: str, loja: str) -> str:
        return f"{self.prefixo}/bruto/{empresa}/{loja}/estoque.parquet"

    def marcador_estoque(self, empresa: str) -> str:
        return f"{self.prefixo}/bruto/{empresa}/_estoque.json"

    def catalogo(self, empresa: str) -> str:
        """Um produto por EAN das lojas vinculadas (pedido/categorias.py)."""
        return f"{self.prefixo}/catalogo/{empresa}.parquet"

    def prefixo_catalogo(self) -> str:
        return f"{self.prefixo}/catalogo/"

    def base_categorias(self) -> str:
        """FEBRAFAR + CMED já unidas (python -m pedido.carga_categorias)."""
        return f"{self.prefixo}/categorias/base_inicial.parquet"

    def base_categorias_info(self) -> str:
        return f"{self.prefixo}/categorias/base_inicial.json"

    def configuracao(self) -> str:
        """O que a rotina precisa das Configurações de Pedidos (a janela)."""
        return f"{self.prefixo}/controle/configuracao.json"

    def lojas_gps(self, empresa: str) -> str:
        return f"{self.prefixo}/controle/lojas_gps/{empresa}.json"

    def execucao(self, data: dt.date, parte: str, hora: str) -> str:
        return f"{self.prefixo}/controle/execucoes/{data.isoformat()}/{parte}-{hora}.json"


# ---------------------------------------------------------------------------
# O que falta
# ---------------------------------------------------------------------------

@dataclass
class _Periodo:
    """Um mês da janela: fechado (1 arquivo) ou corrente (1 arquivo por dia)."""
    mes: str
    inicio: dt.date
    fim: dt.date
    fechado: bool
    dias: list[dt.date] = field(default_factory=list)


def periodos(hoje: dt.date, meses_fechados: int) -> list[_Periodo]:
    inicio, ontem = janela(hoje, meses_fechados)
    saida: list[_Periodo] = []
    cursor = inicio
    while cursor <= ontem:
        fim_mes = _ultimo_dia(cursor.year, cursor.month)
        fechado = fim_mes <= ontem
        fim = fim_mes if fechado else ontem
        dias = [] if fechado else [cursor + dt.timedelta(days=i) for i in range((fim - cursor).days + 1)]
        saida.append(_Periodo(ano_mes(cursor), cursor, fim, fechado, dias))
        cursor = fim_mes + dt.timedelta(days=1)
    return saida


def _faltantes_de_uma_loja(chaves: Chaves, existentes: set[str], empresa: str, loja: str, tipo: str,
                           per: list[_Periodo]) -> list[tuple[dt.date, dt.date, bool]]:
    """Intervalos (início, fim, mês fechado?) que ainda não estão salvos."""
    faltas = []
    for p in per:
        if p.fechado:
            if chaves.mes(empresa, loja, tipo, p.mes) not in existentes:
                faltas.append((p.inicio, p.fim, True))
            continue
        sem = [d for d in p.dias if chaves.dia(empresa, loja, tipo, d) not in existentes]
        if sem:
            # Um intervalo só, do primeiro ao último dia que falta: uma
            # consulta em vez de várias (os dias no meio que já existiam são
            # regravados iguais — inofensivo).
            faltas.append((min(sem), max(sem), False))
    return faltas


def unidades_da_empresa(chaves: Chaves, existentes: set[str], empresa: str, lojas: list[str],
                        hoje: dt.date, meses_fechados: int, estoque_do_dia: str | None) -> list[Unidade]:
    """Tudo que falta de uma empresa, na ordem de execução: compras (rota da
    empresa), vendas (por loja) e, por último, o estoque do dia.

    `existentes`: as chaves já salvas sob `chaves.empresa(empresa)` (uma
    listagem por empresa — conferir arquivo a arquivo custaria uma ida ao
    Spaces por chave). `estoque_do_dia`: a data gravada no marcador de
    estoque, ou None."""
    per = periodos(hoje, meses_fechados)
    unidades: list[Unidade] = []

    # Compras: a unidade é da empresa, e só conta como feita quando TODAS as
    # lojas têm o arquivo daquele período.
    intervalos: set[tuple[dt.date, dt.date, bool]] = set()
    for loja in lojas:
        intervalos.update(_faltantes_de_uma_loja(chaves, existentes, empresa, loja, COMPRAS, per))
    for inicio, fim, fechado in sorted(_unir(intervalos)):
        unidades.append(Unidade(COMPRAS, empresa, None, inicio, fim, fechado))

    for loja in lojas:
        for inicio, fim, fechado in _faltantes_de_uma_loja(chaves, existentes, empresa, loja, VENDAS, per):
            unidades.append(Unidade(VENDAS, empresa, loja, inicio, fim, fechado))

    if estoque_do_dia != hoje.isoformat() and lojas:
        unidades.append(Unidade(ESTOQUE, empresa, None))
    return unidades


def _unir(intervalos: set[tuple[dt.date, dt.date, bool]]) -> set[tuple[dt.date, dt.date, bool]]:
    """Lojas diferentes podem faltar pedaços diferentes do mês corrente; a
    consulta é da empresa, então vale o intervalo que cobre todos."""
    por_mes: dict[tuple[str, bool], tuple[dt.date, dt.date]] = {}
    for inicio, fim, fechado in intervalos:
        k = (ano_mes(inicio), fechado)
        atual = por_mes.get(k)
        por_mes[k] = (min(inicio, atual[0]), max(fim, atual[1])) if atual else (inicio, fim)
    return {(i, f, fechado) for (m, fechado), (i, f) in por_mes.items()}
