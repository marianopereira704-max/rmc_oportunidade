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
    categorias/classificacao_ean.parquet (+ .json)           FEBRAFAR e CMED por EAN, sem decidir
    historico_estoque/{empresa}/{loja}/2026-10.parquet       o que mudou no estoque, mês a mês (13 meses)
    historico_estoque/{empresa}/{loja}/_fotos.json           os dias em que houve foto de estoque
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

Compras para a Análise de Oportunidade (decisões de 05/10/2026):
- 6 meses fechados (`COMPRAS_MESES_FECHADOS`), não 3: a Análise mostra até
  3 meses e a seta de tendência compara com os 3 anteriores. Vendas
  continuam na janela das Configurações de Pedidos.
- A API filtra as compras pela data de EMISSÃO da nota e não tem data de
  entrada (conferido em 05/10/2026). Nota emitida em setembro e lançada no
  sistema em 3 de outubro só aparece se setembro for baixado de novo: o
  último mês fechado é baixado outra vez, uma vez por dia, até o dia 6 do
  mês seguinte (`DIA_MES_DEFINITIVO`) — a baixa do dia 6 pega o que foi
  lançado até o dia 5 e deixa o mês definitivo. A data de cada baixa fica
  em `bruto/{empresa}/_compras.json` (`marcador_compras`).
- No mês corrente, as compras dos últimos `DIAS_COMPRAS_REBAIXADOS` dias
  são baixadas de novo a cada execução, pelo mesmo motivo (vale também
  pra "última compra" do Assistente).
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

VENDAS = "vendas"
COMPRAS = "compras"
ESTOQUE = "estoque"

COMPRAS_MESES_FECHADOS = 6
DIA_MES_DEFINITIVO = 6
DIAS_COMPRAS_REBAIXADOS = 5


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


def dia_definitivo(mes: str) -> dt.date:
    """A partir de que dia uma baixa do mês `mes` ("2026-09") é definitiva:
    o dia 6 do mês seguinte (regra no topo do módulo)."""
    ano, m = (int(x) for x in mes.split("-"))
    return _mes_menos(dt.date(ano, m, 1), -1).replace(day=DIA_MES_DEFINITIVO)


def mes_definitivo(mes: str, baixado_em: str | None) -> bool:
    return baixado_em is not None and dt.date.fromisoformat(baixado_em) >= dia_definitivo(mes)


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

    def historico_estoque(self, empresa: str, loja: str, mes: str) -> str:
        """O que mudou no estoque da loja no mês "AAAA-MM" (pedido/historico_estoque.py, 27/10/2026)."""
        return f"{self.prefixo}/historico_estoque/{empresa}/{loja}/{mes}.parquet"

    def historico_estoque_fotos(self, empresa: str, loja: str) -> str:
        """{"dias": ["2026-10-27", ...]}: os dias em que houve foto (para saber os dias sem foto)."""
        return f"{self.prefixo}/historico_estoque/{empresa}/{loja}/_fotos.json"

    def prefixo_historico_estoque(self, empresa: str, loja: str) -> str:
        return f"{self.prefixo}/historico_estoque/{empresa}/{loja}/"

    def marcador_compras(self, empresa: str) -> str:
        """{"meses": {"2026-09": "2026-10-03"}} — dia em que cada mês fechado
        de compras foi baixado pela última vez (regra no topo do módulo)."""
        return f"{self.prefixo}/bruto/{empresa}/_compras.json"

    def carga(self, empresa: str) -> str:
        """Andamento da CARGA INICIAL da empresa (fase 1, 08/10/2026):
        {"meses": [...], "completa": bool, "tentativas": n, "atualizado": data}.
        Em `controle/` (e não em `bruto/`): a rotina lê o de todas as
        empresas a cada execução, numa listagem só."""
        return f"{self.prefixo}/controle/carga/{empresa}.json"

    def prefixo_carga(self) -> str:
        return f"{self.prefixo}/controle/carga/"

    def compras_rede(self, mes: str) -> str:
        """Compras de TODAS as lojas num mês fechado (pedido/rede.py)."""
        return f"{self.prefixo}/analise/compras/{mes}.parquet"

    def compras_rede_meta(self, mes: str) -> str:
        return f"{self.prefixo}/analise/compras/{mes}.json"

    def compras_rede_resumo(self, mes: str) -> str:
        """Uma linha por EAN do mês (valor comprado, compras, lojas) — a fila
        de EAN lê isto em vez das compras inteiras (pedido/rede.py)."""
        return f"{self.prefixo}/analise/compras/{mes}_eans.parquet"

    def prefixo_compras_rede(self) -> str:
        return f"{self.prefixo}/analise/compras/"

    def estoque_modelo(self, empresa: str, loja: str, nome: str) -> str:
        """Modelo de estoque do Dashboard (pedido/estoque_modelo.py): `skus.parquet`, `vendas_dia.parquet` e
        `meta.json` por loja. Fora de `bruto/` e de `pronto/` de propósito: é derivado dos dois."""
        return f"{self.prefixo}/estoque/{empresa}/{loja}/{nome}"

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

    def classificacao_ean(self) -> str:
        """O que a FEBRAFAR e a CMED dizem de cada EAN (python -m pedido.carga_categorias, 25/10/2026)."""
        return f"{self.prefixo}/categorias/classificacao_ean.parquet"

    def classificacao_ean_info(self) -> str:
        return f"{self.prefixo}/categorias/classificacao_ean.json"

    def catalogo_iqvia(self) -> str:
        """EAN → SETOR_NEC + GMRS (python -m pedido.carga_catalogo, 07/10/2026)."""
        return f"{self.prefixo}/categorias/catalogo_iqvia.parquet"

    def catalogo_iqvia_info(self) -> str:
        return f"{self.prefixo}/categorias/catalogo_iqvia.json"

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
                           per: list[_Periodo], rebaixar_meses: set[str] = frozenset(),
                           rebaixar_dias: set[dt.date] = frozenset()) -> list[tuple[dt.date, dt.date, bool]]:
    """Intervalos (início, fim, mês fechado?) que ainda não estão salvos — ou
    que precisam ser baixados de novo (`rebaixar_*`, só compras)."""
    faltas = []
    for p in per:
        if p.fechado:
            if chaves.mes(empresa, loja, tipo, p.mes) not in existentes or p.mes in rebaixar_meses:
                faltas.append((p.inicio, p.fim, True))
            continue
        sem = [d for d in p.dias if chaves.dia(empresa, loja, tipo, d) not in existentes or d in rebaixar_dias]
        if sem:
            # Um intervalo só, do primeiro ao último dia que falta: uma
            # consulta em vez de várias (os dias no meio que já existiam são
            # regravados iguais — inofensivo).
            faltas.append((min(sem), max(sem), False))
    return faltas


def compras_a_rebaixar(hoje: dt.date, meses_compras: int,
                       baixados: dict[str, str] | None) -> tuple[set[str], set[dt.date]]:
    """(meses fechados, dias do mês corrente) de compras a baixar de novo
    (regra no topo do módulo). `baixados`: mês → dia da última baixa (o
    `marcador_compras`). Último mês fechado sem registro: baixado de novo —
    pode ter sido baixado antes do dia 6, quando o marcador ainda não
    existia; os mais antigos contam como definitivos."""
    baixados = baixados or {}
    fechados = [p for p in periodos(hoje, meses_compras) if p.fechado]
    meses = set()
    if fechados:
        ultimo = fechados[-1].mes
        quando = baixados.get(ultimo)
        if not mes_definitivo(ultimo, quando) and (quando is None or quando < hoje.isoformat()):
            meses.add(ultimo)
    dias = set()
    for i in range(1, DIAS_COMPRAS_REBAIXADOS + 1):
        dia = hoje - dt.timedelta(days=i)
        if (dia.year, dia.month) == (hoje.year, hoje.month):
            dias.add(dia)
    return meses, dias


def unidades_da_empresa(chaves: Chaves, existentes: set[str], empresa: str, lojas: list[str],
                        hoje: dt.date, meses_fechados: int, estoque_do_dia: str | None,
                        meses_compras: int | None = None, compras_baixadas: dict[str, str] | None = None) -> list[Unidade]:
    """Tudo que falta de uma empresa, na ordem de execução: compras (rota da
    empresa), vendas (por loja) e, por último, o estoque do dia.

    `existentes`: as chaves já salvas sob `chaves.empresa(empresa)` (uma
    listagem por empresa — conferir arquivo a arquivo custaria uma ida ao
    Spaces por chave). `estoque_do_dia`: a data gravada no marcador de
    estoque, ou None. `meses_compras` e `compras_baixadas` (o
    `marcador_compras`): regras de compras no topo do módulo — com
    `meses_compras` None, as compras seguem a janela das vendas e nada é
    baixado de novo."""
    per = periodos(hoje, meses_fechados)
    unidades: list[Unidade] = []

    # Compras: a unidade é da empresa, e só conta como feita quando TODAS as
    # lojas têm o arquivo daquele período.
    if meses_compras is None:
        per_compras, rebaixar_meses, rebaixar_dias = per, set(), set()
    else:
        n = max(meses_compras, meses_fechados)
        per_compras = periodos(hoje, n)
        rebaixar_meses, rebaixar_dias = compras_a_rebaixar(hoje, n, compras_baixadas)
    intervalos: set[tuple[dt.date, dt.date, bool]] = set()
    for loja in lojas:
        intervalos.update(_faltantes_de_uma_loja(chaves, existentes, empresa, loja, COMPRAS, per_compras,
                                                 rebaixar_meses, rebaixar_dias))
    for inicio, fim, fechado in sorted(_unir(intervalos)):
        unidades.append(Unidade(COMPRAS, empresa, None, inicio, fim, fechado))

    for loja in lojas:
        for inicio, fim, fechado in _faltantes_de_uma_loja(chaves, existentes, empresa, loja, VENDAS, per):
            unidades.append(Unidade(VENDAS, empresa, loja, inicio, fim, fechado))

    if estoque_do_dia != hoje.isoformat() and lojas:
        unidades.append(Unidade(ESTOQUE, empresa, None))
    return unidades


# ---------------------------------------------------------------------------
# Fases da rotina (08/10/2026)
# ---------------------------------------------------------------------------
# A carga do jeito antigo (3 meses de vendas + 6 de compras + o mês corrente
# + estoque, de uma vez, e toda noite revisitando quem já tinha dados)
# estacionava em ~60 empresas: na noturna de 08/10, 1,7 h passou só
# atualizando 20 empresas antigas, nenhuma nova (cada visita paga o
# "aquecimento" do GPS, de 30 s a 8 min). Decisão do Mariano:
# - fase 1 (CARGA): todas as empresas só com vendas e compras de meses
#   FECHADOS e definitivos (agosto e setembro) — 5 consultas por empresa de
#   1 loja em vez de 12, e mês fechado baixado nunca é refeito;
# - fase 2 (MANUTENÇÃO), quando todas terminarem: o dia a dia (mês
#   corrente, foto do estoque, compras recentes, o mês que fecha) de todas
#   primeiro; os meses antigos que faltarem (3 de vendas, 6 de compras)
#   depois, com o tempo que sobrar.

def separar_por_fase(unidades: list[Unidade], meses_carga: list[str]) -> tuple[list, list, list]:
    """(carga, manutenção, complemento) — as unidades de uma empresa pela
    fase em que entram. carga = meses de `meses_carga` (sem estoque);
    manutenção = estoque, mês corrente e meses fechados DEPOIS da carga;
    complemento = meses fechados antes da carga (e os da carga que faltarem)."""
    ultimo = max(meses_carga)
    carga, manutencao, complemento = [], [], []
    for u in unidades:
        mes = ano_mes(u.inicio) if u.inicio else None
        if u.tipo != ESTOQUE and u.mes_fechado and mes in meses_carga:
            carga.append(u)
        elif u.tipo == ESTOQUE or not u.mes_fechado or (mes is not None and mes > ultimo):
            manutencao.append(u)
        else:
            complemento.append(u)
    return carga, manutencao, complemento


def _unir(intervalos: set[tuple[dt.date, dt.date, bool]]) -> set[tuple[dt.date, dt.date, bool]]:
    """Lojas diferentes podem faltar pedaços diferentes do mês corrente; a
    consulta é da empresa, então vale o intervalo que cobre todos."""
    por_mes: dict[tuple[str, bool], tuple[dt.date, dt.date]] = {}
    for inicio, fim, fechado in intervalos:
        k = (ano_mes(inicio), fechado)
        atual = por_mes.get(k)
        por_mes[k] = (min(inicio, atual[0]), max(fim, atual[1])) if atual else (inicio, fim)
    return {(i, f, fechado) for (m, fechado), (i, f) in por_mes.items()}
