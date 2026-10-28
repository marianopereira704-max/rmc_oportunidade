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

Empresa cuja LISTA DE LOJAS falha também volta (07/10/2026): na execução
de 05/10 o servidor do GPS respondeu 502/500 ao listar as lojas de 366 das
388 empresas, e elas eram simplesmente puladas até a noite seguinte. Agora
ficam numa fila própria e são tentadas de novo depois das outras, com uma
pausa antes (erro 502 é o servidor do GPS sobrecarregado — tentar de novo
no mesmo segundo não adianta).

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

from pedido import categorias, estoque_modelo, historico_estoque, plano, pronto, transformar
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
    modelos_montados: int = 0   # modelo de estoque do Dashboard (pedido/estoque_modelo.py), 22/10/2026
    historicos_gravados: int = 0   # lojas com a foto do dia no histórico do estoque (27/10/2026)
    parou_por_tempo: bool = False
    segundos: float = 0.0
    empresas_sem_lista_de_lojas: int = 0   # a lista de lojas falhou em todas as voltas
    # Fases (08/10/2026, pedido/plano.py::separar_por_fase): "carga",
    # "manutencao" ou "completa" (tudo de uma vez — o jeito antigo/testes).
    fase: str = "completa"
    empresas_carga_completa: int = 0
    # Erro que derrubou a execução antes/fora da coleta (pedido/__main__.py):
    # com ele, o relatório existe mesmo quando nada foi baixado.
    erro_geral: str | None = None
    falhas: list[dict] = field(default_factory=list)
    pendentes_para_proxima: list[str] = field(default_factory=list)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Coleta:
    def __init__(self, cliente, armazenamento: Armazenamento, chaves: Chaves, hoje: dt.date,
                 meses_fechados: int, orcamento_segundos: float, tentativas_por_unidade: int,
                 filtro_lojas: Callable[[str, list[dict]], list[dict]] | None = None,
                 relogio: Callable[[], float] = time.monotonic,
                 meses_compras: int = plano.COMPRAS_MESES_FECHADOS,
                 dormir: Callable[[float], None] = time.sleep,
                 pausa_antes_de_repetir_lojas: float = 120.0,
                 fase: str = "completa", meses_carga: list[str] | None = None,
                 status_carga: dict[str, dict] | None = None, so_estoque: bool = False) -> None:
        """`filtro_lojas(id_empresa, lojas_da_api)` → só as lojas que interessam
        (lojas ativas no RMC — decisão de 27/09/2026). None = todas.
        `meses_compras`: meses fechados de compras (Análise de Oportunidade —
        regras no topo de pedido/plano.py)."""
        self.cliente = cliente
        self.armaz = armazenamento
        self.chaves = chaves
        self.hoje = hoje
        self.meses_fechados = meses_fechados
        self.orcamento = orcamento_segundos
        self.tentativas = max(1, tentativas_por_unidade)
        self.filtro_lojas = filtro_lojas
        self.relogio = relogio
        self.meses_compras = meses_compras
        self.dormir = dormir
        self.pausa_lojas = pausa_antes_de_repetir_lojas
        # `so_estoque` (27/10/2026): só a foto de estoque de cada empresa (e o histórico), sem vendas, compras nem
        # os "prontos". Para começar o histórico do estoque num dia sem rotina completa — a foto de todas as
        # empresas leva minutos; a rotina inteira, horas.
        self.so_estoque = so_estoque
        # Histórico do estoque (27/10/2026): gravado junto com a foto, mas a falha dele não derruba a foto — vai
        # para o relatório pelo `_tentar`.
        self._historico_ok = 0
        self._historico_falhas: list[dict] = []
        # `fase`: "carga" (só os `meses_carga`), "manutencao" (o dia a dia de
        # todas primeiro; o complemento depois) ou "completa".
        self.fase = fase
        self.meses_carga = list(meses_carga or [])
        self.status_carga = dict(status_carga or {})
        self._depois: list[tuple[Unidade, list[dict]]] = []   # complemento (manutenção), depois de todas
        self._com_lojas: set[str] = set()                     # empresas cuja lista de lojas saiu
        self._inicio = relogio()
        self._compras_baixadas: dict[str, dict[str, str]] = {}  # empresa → mês → dia da última baixa

    # -- tempo ----------------------------------------------------------------

    def _acabou_o_tempo(self) -> bool:
        return self.relogio() - self._inicio >= self.orcamento

    # -- execução -------------------------------------------------------------

    def executar(self, empresas: list[str], parte: str) -> Relatorio:
        rel = Relatorio(hoje=self.hoje.isoformat(), parte=parte, empresas_na_parte=len(empresas), fase=self.fase)
        fila: list[tuple[Unidade, list[str], int]] = []  # (unidade, lojas da empresa, tentativas feitas)
        processadas: list[tuple[str, list[dict]]] = []

        sem_lojas: dict[int, str] = {}      # posição → empresa cuja lista de lojas falhou
        erro_lojas: dict[str, str] = {}     # empresa → último erro (vai pro relatório só se não sair)

        for i, empresa in enumerate(empresas, 1):
            if self._acabou_o_tempo():
                rel.parou_por_tempo = True
                break
            if not self._processar_empresa(i, len(empresas), empresa, rel, fila, processadas, erro_lojas):
                sem_lojas[i] = empresa

        # Empresas cuja lista de lojas falhou: de novo, depois de todas as
        # outras e de uma pausa (502/500 do GPS costuma passar em minutos).
        volta = 1
        while sem_lojas and not self._acabou_o_tempo() and volta < self.tentativas:
            volta += 1
            _log(f"nova tentativa: listar lojas de {len(sem_lojas)} empresa(s) (volta {volta})")
            self._pausar(self.pausa_lojas)
            for i, empresa in list(sem_lojas.items()):
                if self._acabou_o_tempo():
                    rel.parou_por_tempo = True
                    break
                if self._processar_empresa(i, len(empresas), empresa, rel, fila, processadas, erro_lojas):
                    del sem_lojas[i]
        for empresa in sem_lojas.values():
            rel.falhas.append({"unidade": f"lojas {empresa}", "erro": erro_lojas.get(empresa, "")})
        rel.empresas_sem_lista_de_lojas = len(sem_lojas)

        # Manutenção: os meses antigos que faltam, só depois do dia a dia de
        # TODAS as empresas.
        if self._depois and not self._acabou_o_tempo():
            _log(f"complemento: {len(self._depois)} consulta(s) de meses antigos")
        for u, lojas in self._depois:
            if self._acabou_o_tempo():
                rel.parou_por_tempo = True
                fila.append((u, lojas, 0))
            elif not self._tentar(u, lojas, rel):
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
        if self.so_estoque:
            pass                                   # sem vendas e compras novas, nem carga a marcar nem pronto a remontar
        elif self.fase == "carga":
            self._marcar_carga(fila, sem_lojas.values(), rel)
        else:
            # Na carga não há estoque: o "pronto" da loja só faz sentido com ele.
            self._montar_prontos(processadas, rel)
        rel.segundos = round(self.relogio() - self._inicio, 1)
        return rel

    def _marcar_carga(self, fila: list, sem_lojas, rel: Relatorio) -> None:
        """Marcador da carga de cada empresa que esta execução alcançou:
        completa = nada da carga ficou pendente. Falha (consulta que falhou
        em todas as voltas, ou a lista de lojas) soma uma tentativa — com 3,
        a empresa deixa de segurar a passagem pra manutenção (Q2 de
        08/10/2026). Parar pelo TEMPO no meio da empresa não conta como
        tentativa: ela só continua na próxima."""
        falhou = {u.empresa for u, _, feitas in fila if feitas >= 1} | set(sem_lojas)
        cortada = {u.empresa for u, _, feitas in fila if feitas == 0}
        for empresa in sorted(self._com_lojas | set(sem_lojas)):
            tentativas = self._tentativas_anteriores(empresa)
            completa = empresa not in falhou and empresa not in cortada
            if empresa in falhou:
                tentativas += 1
            self._gravar_marcador(empresa, completa, tentativas, rel)
            rel.empresas_carga_completa += int(completa)

    def _tentativas_anteriores(self, empresa: str) -> int:
        anterior = self.status_carga.get(empresa) or {}
        return int(anterior.get("tentativas", 0)) if anterior.get("meses") == self.meses_carga else 0

    def _gravar_marcador(self, empresa: str, completa: bool, tentativas: int, rel: Relatorio) -> None:
        marcador = {"meses": self.meses_carga, "completa": completa, "tentativas": tentativas,
                    "atualizado": self.hoje.isoformat()}
        try:
            self.armaz.salvar_json(self.chaves.carga(empresa), marcador)
        except Exception as exc:  # noqa: BLE001 — a próxima execução refaz a conta
            rel.falhas.append({"unidade": f"marcador da carga {empresa}", "erro": _resumo(exc)})

    def _pausar(self, segundos: float) -> None:
        """Espera, sem passar do orçamento de tempo da execução."""
        resta = self.orcamento - (self.relogio() - self._inicio)
        if segundos > 0 and resta > segundos:
            self.dormir(segundos)

    def _processar_empresa(self, i: int, total: int, empresa: str, rel: Relatorio, fila: list,
                           processadas: list, erro_lojas: dict[str, str]) -> bool:
        """Lista as lojas e tenta as consultas da empresa (as que falham vão
        pra `fila`). False = a lista de lojas falhou (a empresa volta depois)."""
        try:
            lojas = self._lojas(empresa)
        except Exception as exc:  # noqa: BLE001 — volta na fila de empresas
            erro_lojas[empresa] = _resumo(exc)
            _log(f"empresa {i}/{total}: falha ao listar lojas ({type(exc).__name__})")
            return False
        self._com_lojas.add(empresa)
        if not lojas:
            rel.empresas_sem_loja_ativa += 1
            return True
        rel.empresas_processadas += 1
        rel.lojas += len(lojas)
        processadas.append((empresa, lojas))
        unidades = self._plano(empresa, lojas)
        if self.fase != "completa":
            carga, manutencao, complemento = plano.separar_por_fase(unidades, self.meses_carga)
            if self.fase == "carga":
                unidades = carga
            else:
                unidades = manutencao
                self._depois.extend((u, lojas) for u in carga + complemento)
        _log(f"empresa {i}/{total}: {len(lojas)} loja(s), {len(unidades)} consulta(s) a fazer")
        na_fila_antes = len(fila)
        for u in unidades:
            if self._acabou_o_tempo():
                rel.parou_por_tempo = True
                fila.append((u, lojas, 0))
                continue
            if not self._tentar(u, lojas, rel):
                fila.append((u, lojas, 1))
        # Carga: empresa que terminou sem pendência ganha o marcador NA HORA
        # (09/10/2026) — antes só no fim da execução, e os 7 processos que
        # caíram com a rede às 17:56 de 08/10 não deixaram marcador nenhum.
        if self.fase == "carga" and len(fila) == na_fila_antes:
            self._gravar_marcador(empresa, completa=True, tentativas=self._tentativas_anteriores(empresa), rel=rel)
        return True

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
                    continue
                # Modelo de estoque do Dashboard (22/10/2026): na mesma passada, com o pronto que acabou de
                # ficar na memória e SEM banco (a rotina não tem). Em dia (a assinatura do pronto não mudou):
                # pula sozinho. Falha aqui não derruba o pronto: o app monta o que faltar, como faz com ele.
                if p is not None:
                    try:
                        if estoque_modelo.atualizar(self.armaz, self.chaves, p, empresa, str(loja["CodigoLoja"])):
                            rel.modelos_montados += 1
                    except Exception as exc:  # noqa: BLE001
                        rel.falhas.append({"unidade": f"modelo de estoque {empresa}/{loja.get('CodigoLoja')}",
                                           "erro": _resumo(exc)})
            # O que cada loja vendeu vai pro catálogo da empresa (fila de EAN
            # "Loja (API)" no app — decisão de 27/09/2026).
            chave = self.chaves.catalogo(empresa)
            try:
                if vendas_das_lojas and self.armaz.existe(chave):
                    self.armaz.salvar_df(chave, categorias.completar_com_vendas(self.armaz.ler_df(chave), vendas_das_lojas))
            except Exception as exc:  # noqa: BLE001
                rel.falhas.append({"unidade": f"catálogo {empresa}", "erro": _resumo(exc)})

    def _lojas(self, empresa: str) -> list[dict]:
        # Código da loja SEM espaços (08/10/2026): uma empresa do GPS manda
        # "1" + 49 espaços na lista de lojas, mas "1" nos dados — a separação
        # por loja não casava e os 40 arquivos dela saíram VAZIOS, sem erro
        # nenhum. Os dados já vêm limpos (transformar._texto); a lista, agora também.
        lojas = [{**l, "CodigoLoja": str(l.get("CodigoLoja") or "").strip()} for l in self.cliente.listar_lojas(empresa)]
        self.armaz.salvar_json(self.chaves.lojas_gps(empresa), lojas)
        if self.filtro_lojas is not None:
            lojas = self.filtro_lojas(empresa, lojas)
        return lojas

    def _plano(self, empresa: str, lojas: list[dict]) -> list[Unidade]:
        existentes = set(self.armaz.listar(self.chaves.empresa(empresa)))
        marcador = self.chaves.marcador_estoque(empresa)
        estoque_do_dia = self.armaz.ler_json(marcador).get("data") if marcador in existentes else None
        k_compras = self.chaves.marcador_compras(empresa)
        baixados = self.armaz.ler_json(k_compras).get("meses", {}) if k_compras in existentes else {}
        self._compras_baixadas[empresa] = dict(baixados)
        codigos = [str(l["CodigoLoja"]) for l in lojas]
        unidades = plano.unidades_da_empresa(self.chaves, existentes, empresa, codigos, self.hoje,
                                             self.meses_fechados, estoque_do_dia,
                                             meses_compras=self.meses_compras, compras_baixadas=baixados)
        return [u for u in unidades if u.tipo == ESTOQUE] if self.so_estoque else unidades

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
        rel.historicos_gravados += self._historico_ok
        rel.falhas.extend(self._historico_falhas)
        self._historico_ok, self._historico_falhas = 0, []
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
            gravados = self._gravar_periodo(u, COMPRAS, transformar.por_loja(df, lojas))
            if u.mes_fechado:
                # Depois dos arquivos: se a gravação parar no meio, o mês é
                # baixado de novo amanhã (o marcador ainda diz a data antiga).
                baixados = self._compras_baixadas.setdefault(u.empresa, {})
                baixados[plano.ano_mes(u.inicio)] = self.hoje.isoformat()
                self.armaz.salvar_json(self.chaves.marcador_compras(u.empresa), {"meses": baixados})
                gravados += 1
            return gravados
        if u.tipo == ESTOQUE:
            df = transformar.estoque(list(self.cliente.listar_estoque_empresa(u.empresa)))
            gravados = 0
            fatias = transformar.por_loja(df, lojas)
            for loja, fatia in fatias.items():
                self.armaz.salvar_df(self.chaves.estoque(u.empresa, loja), fatia)
                gravados += 1
            # O histórico do estoque (perda por ruptura MEDIDA, 27/10/2026): a mesma foto, só o que mudou. Depois de
            # gravar a foto e com a falha isolada: sem histórico, a perda do dia fica sem medição; sem a foto, o
            # Assistente inteiro ficaria sem estoque.
            for loja, fatia in fatias.items():
                try:
                    historico_estoque.registrar(self.armaz, self.chaves, u.empresa, loja, fatia, self.hoje)
                    self._historico_ok += 1
                except Exception as exc:  # noqa: BLE001
                    self._historico_falhas.append({"unidade": f"histórico do estoque {u.empresa}/{loja}",
                                                   "erro": _resumo(exc)})
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
