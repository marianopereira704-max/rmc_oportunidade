"""Rotina de dados do Pedido — carga inicial e atualização diária são o
mesmo comando (o plano baixa "o que falta").

    python -m pedido                                  # tudo, no Spaces
    python -m pedido --parte 0 --partes 4             # 1/4 das empresas (GitHub Actions)
    python -m pedido --destino local:C:/tmp/pedido    # grava numa pasta (teste)
    python -m pedido --empresas ID1,ID2 --orcamento-minutos 30

Precisa de: GPS_API_BASE_URL, GPS_API_KEY; DO_SPACES_* (se o destino for o
Spaces); SISTEMA_INTERNO_BASE_URL e _TOKEN (lista de lojas ativas — sem
elas, processa todas as lojas do GPS, com aviso).

Robustez (07/10/2026): as execuções agendadas de 03 a 07/10 morreram em
~30 s, antes de gravar qualquer relatório — erro numa das duas primeiras
chamadas (empresas do GPS ou lojas da T.I.) derrubava tudo, e o motivo só
aparecia no log do GitHub (que pede login). Agora:
- as duas primeiras chamadas têm tentativas com espera (`_com_tentativas`);
- qualquer erro que derrube a execução vira um relatório no Spaces com o
  campo `erro_geral` (aparece em Dados → Rotinas). No log público vai só o
  TIPO do erro: a mensagem traz o endereço da API.
"""
from __future__ import annotations

import argparse
import os
import datetime as dt
import sys
import time
from zoneinfo import ZoneInfo

import requests

from core.config import settings
from integrations.gps_api import cliente_das_configuracoes
from pedido import coleta, configuracao, vinculo
from pedido.armazenamento import ArmazenamentoLocal, do_ambiente
from pedido.plano import Chaves


# Mesma regra de integrations/sistema_interno.py::STATUS_DA_REDE (a rotina
# não importa integrations/ — puxaria o banco).
_STATUS_DA_REDE = "Ativo"


def _lojas_rmc_ativas() -> list[dict] | None:
    """Lojas DA REDE do cadastro do RMC, direto da API do sistema interno (a
    rotina não alcança o Postgres — ver pedido/__init__.py): active=true E
    status "Ativo" — o resto é Portal do PBM/Institucional (07/10/2026).
    None = API não configurada."""
    cfg = settings.sistema_interno
    if not cfg.configured:
        return None
    resp = requests.get(cfg.base_url, headers={"X-API-KEY": cfg.token}, timeout=60)
    resp.raise_for_status()
    saida = []
    for item in resp.json():
        if item.get("active") is not True or (item.get("status_title") or "").strip() != _STATUS_DA_REDE:
            continue
        end = item.get("address") or {}
        saida.append({
            "cnpj": item.get("cnpj"), "razao_social": item.get("businessName"),
            "nome_fantasia": item.get("fantasyName"), "numero": end.get("number"), "cep": end.get("zip_code"),
            "bairro": end.get("neighborhood"), "cidade": end.get("city"), "uf": end.get("state"),
        })
    return saida


def _filtro_lojas(lojas_rmc: list[dict]):
    """Só as lojas do GPS que têm correspondente ATIVO no RMC (automático ou
    a confirmar — na dúvida, baixa: o vínculo final é decidido no app)."""
    def filtrar(id_empresa: str, lojas_api: list[dict]) -> list[dict]:
        entrada = [{
            "id_empresa": id_empresa, "codigo_loja": l.get("CodigoLoja"),
            "nome_loja": l.get("NomeLoja") or l.get("NomeFantasia"),
            "cnpj": l.get("CNPJ"), "numero": l.get("Numero"), "uf": l.get("Estado"), "cep": l.get("CEP"),
        } for l in lojas_api]
        sugestoes = vinculo.sugerir(entrada, lojas_rmc)
        return [l for l, s in zip(lojas_api, sugestoes) if s.situacao != vinculo.SEM_CANDIDATO]
    return filtrar


# Esperas entre as tentativas das chamadas iniciais (segundos): ~16 min no
# total — o bastante pra um 502 passageiro do GPS, pouco perto das 5h30 de
# orçamento.
ESPERAS_INICIAIS = (60, 300, 600)

# Fase 1 da rotina — carga inicial (08/10/2026, pedido/plano.py::
# separar_por_fase): os meses FECHADOS e definitivos que toda empresa recebe
# antes de qualquer manutenção. Por variável de ambiente pra não depender de
# publicar código se um dia precisar de outro par de meses.
MESES_CARGA = [m.strip() for m in os.environ.get("PEDIDO_CARGA_MESES", "2026-08,2026-09").split(",") if m.strip()]
TENTATIVAS_CARGA = 3   # empresa que falha 3 vezes na carga deixa de segurar a manutenção (Q2)


def _carga_concluida(marcador: dict | None) -> bool:
    return bool(marcador) and marcador.get("meses") == MESES_CARGA and (
        marcador.get("completa") or int(marcador.get("tentativas", 0)) >= TENTATIVAS_CARGA)


def _status_carga(armaz, chaves: Chaves) -> dict[str, dict]:
    """Marcador da carga de cada empresa (uma listagem + leituras em paralelo)."""
    prefixo = chaves.prefixo_carga()
    arquivos = [c for c in armaz.listar(prefixo) if c.endswith(".json")]
    marcadores = armaz.ler_varios(arquivos, armaz.ler_json)
    return {c[len(prefixo):].removesuffix(".json"): m for c, m in zip(arquivos, marcadores)}


def _fase_e_empresas(todas: list[str], status: dict[str, dict], parte: int, partes: int) -> tuple[str, list[str]]:
    """Carga enquanto houver empresa pendente; depois, manutenção. Na carga,
    só as pendentes são divididas entre as partes — todas leem o mesmo
    status ao começar (o workflow não deixa duas execuções ao mesmo tempo) —
    e cada parte começa pelas que NUNCA foram tentadas."""
    pendentes = [e for e in todas if not _carga_concluida(status.get(e))]
    if not pendentes:
        return "manutencao", coleta.dividir(todas, parte, partes)
    minhas = coleta.dividir(pendentes, parte, partes)
    return "carga", sorted(minhas, key=lambda e: (e in status, int((status.get(e) or {}).get("tentativas", 0)), e))


class _SessaoComPrazo(requests.Session):
    """Sessão HTTP com PRAZO TOTAL por consulta (09/10/2026). O `timeout` do
    requests é por leitura (tempo entre um pedaço da resposta e o próximo),
    não do total: na carga de 08/10 uma consulta ficou 8 h pendurada depois
    que a rede caiu. Aqui a resposta é lida em pedaços e cortada quando o
    total passa do prazo — a unidade volta pra fila como qualquer falha."""

    def __init__(self, prazo_segundos: float) -> None:
        super().__init__()
        self.prazo = prazo_segundos

    def request(self, method, url, **kwargs):
        inicio = time.monotonic()
        kwargs["stream"] = True
        resposta = super().request(method, url, **kwargs)
        partes = []
        try:
            for pedaco in resposta.iter_content(chunk_size=64 * 1024):
                partes.append(pedaco)
                if time.monotonic() - inicio > self.prazo:
                    raise requests.Timeout(f"passou do prazo total de {self.prazo:.0f}s")
        finally:
            resposta.close()
        resposta._content = b"".join(partes)
        resposta._content_consumed = True
        return resposta


def _manter_acordado() -> None:
    """No Windows, pede ao sistema pra não entrar em suspensão enquanto a
    rotina roda (09/10/2026: a carga local parou quando o computador dormiu
    e a rede caiu). Some sozinho quando o processo termina. No GitHub
    (Linux), não faz nada."""
    if os.name != "nt":
        return
    try:
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
    except Exception:  # noqa: BLE001 — sem isso a rotina roda igual, só pode dormir
        pass


def _com_tentativas(funcao, descricao: str, esperas=ESPERAS_INICIAIS, dormir=time.sleep):
    """Chama `funcao()`; se falhar, espera e tenta de novo (uma vez por
    espera). Na última falha, a exceção sobe."""
    for n, espera in enumerate([*esperas, None], 1):
        try:
            return funcao()
        except Exception as exc:  # noqa: BLE001
            if espera is None:
                raise
            print(f"AVISO: {descricao} falhou ({type(exc).__name__}), tentativa {n}; "
                  f"nova tentativa em {espera // 60 or 1} min.", flush=True)
            dormir(espera)


def _relatorio_de_erro(armaz, chaves: Chaves, hoje: dt.date, parte: str, exc: Exception, comeco: float) -> None:
    """O relatório da execução que caiu (nada baixado, só o motivo)."""
    rel = coleta.Relatorio(hoje=hoje.isoformat(), parte=parte, erro_geral=coleta._resumo(exc),
                           segundos=round(time.monotonic() - comeco, 1))
    try:
        armaz.salvar_json(chaves.execucao(hoje, parte, time.strftime("%H%M%S")), coleta.relatorio_dict(rel))
    except Exception as exc2:  # noqa: BLE001 — sem Spaces nem pra isso: fica só o log
        print(f"ERRO: nem o relatório do erro foi gravado ({type(exc2).__name__}).", flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rotina de dados do Pedido (API do GPS → Spaces).")
    p.add_argument("--parte", type=int, default=0)
    p.add_argument("--partes", type=int, default=1)
    p.add_argument("--destino", default="spaces", help="'spaces' ou 'local:<pasta>'")
    p.add_argument("--hoje", help="AAAA-MM-DD (padrão: hoje no fuso do Brasil)")
    p.add_argument("--empresas", help="ids separados por vírgula (teste)")
    p.add_argument("--orcamento-minutos", type=int, default=settings.pedido.orcamento_minutos)
    p.add_argument("--todas-as-lojas", action="store_true", help="não filtrar pelas lojas ativas do RMC")
    p.add_argument("--so-estoque", action="store_true",
                   help="só a foto de estoque (e o histórico) de todas as empresas, sem vendas, compras nem fases")
    a = p.parse_args(argv)

    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date()
    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    chaves = Chaves(settings.pedido.prefixo)
    parte = f"parte{a.parte + 1}de{a.partes}"
    comeco = time.monotonic()
    _manter_acordado()
    try:
        return _executar(a, hoje, armaz, chaves, parte)
    except Exception as exc:  # noqa: BLE001 — vira relatório (e falha no GitHub)
        print(f"ERRO: a execução caiu ({type(exc).__name__}) — detalhes no relatório do Spaces "
              "(Dados → Rotinas).", flush=True)
        _relatorio_de_erro(armaz, chaves, hoje, parte, exc, comeco)
        return 1


def _executar(a, hoje: dt.date, armaz, chaves: Chaves, parte: str) -> int:
    # Uma tentativa por chamada: quem repete é a fila da coleta, DEPOIS das
    # outras empresas — dá tempo ao GPS de terminar o cálculo que estourou.
    cliente = cliente_das_configuracoes(timeout_segundos=settings.pedido.timeout_consulta_segundos, tentativas=1,
                                        sessao_http=_SessaoComPrazo(settings.pedido.timeout_consulta_segundos))

    if a.empresas:
        empresas = [e.strip() for e in a.empresas.split(",") if e.strip()]
    else:
        empresas = _com_tentativas(lambda: [str(e["idEmpresa"]) for e in cliente.listar_empresas()],
                                   "lista de empresas do GPS")
    status = _status_carga(armaz, chaves)
    if a.so_estoque:
        # Todas as empresas, divididas entre as partes, sem olhar a fase: a foto do estoque não depende da carga.
        fase, empresas = "completa", coleta.dividir(empresas, a.parte, a.partes)
        print(f"Só estoque: esta parte pega {len(empresas)} empresa(s)", flush=True)
    else:
        fase, empresas = _fase_e_empresas(empresas, status, a.parte, a.partes)
    if a.so_estoque:
        pass
    elif fase == "carga":
        concluidas = sum(1 for m in status.values() if _carga_concluida(m))
        print(f"Fase 1 (carga {', '.join(MESES_CARGA)}): {concluidas} empresa(s) concluída(s); "
              f"esta parte pega {len(empresas)} pendente(s)", flush=True)
    else:
        print("Fase 2 (manutenção): todas as empresas com a carga concluída", flush=True)

    filtro = None
    if not a.todas_as_lojas:
        lojas_rmc = _com_tentativas(_lojas_rmc_ativas, "lista de lojas do sistema da T.I.")
        if lojas_rmc is None:
            print("AVISO: API do sistema interno não configurada — processando todas as lojas do GPS.", flush=True)
        else:
            filtro = _filtro_lojas(lojas_rmc)

    print(f"Pedido — {hoje.isoformat()}, {parte}: {len(empresas)} empresa(s), orçamento {a.orcamento_minutos} min", flush=True)
    # Janela das Configurações de Pedidos (o app grava no Spaces ao salvar).
    meses = configuracao.meses_da_rotina(armaz, chaves, settings.pedido.meses_fechados)
    print(f"Janela: {meses} mes(es) fechado(s) + o mês corrente", flush=True)
    rotina = coleta.Coleta(
        cliente, armaz, chaves, hoje, meses, a.orcamento_minutos * 60,
        settings.pedido.tentativas_por_unidade, filtro_lojas=filtro,
        fase=fase, meses_carga=MESES_CARGA, status_carga=status, so_estoque=a.so_estoque,
    )
    rel = rotina.executar(empresas, parte)
    armaz.salvar_json(chaves.execucao(hoje, parte, time.strftime("%H%M%S")), coleta.relatorio_dict(rel))

    print(
        f"FIM — empresas processadas {rel.empresas_processadas}/{rel.empresas_na_parte} "
        f"(sem loja ativa: {rel.empresas_sem_loja_ativa}), lojas {rel.lojas}, consultas ok {rel.unidades_ok}, "
        f"falhas {rel.unidades_com_falha}, pendentes p/ próxima {len(rel.pendentes_para_proxima)}, "
        f"arquivos {rel.arquivos_gravados}, prontos {rel.prontos_montados}, modelos {rel.modelos_montados}, "
        f"históricos {rel.historicos_gravados}, "
        f"empresas sem lista de lojas {rel.empresas_sem_lista_de_lojas}, {rel.segundos / 60:.1f} min"
        + (" — PAROU PELO TEMPO (a próxima execução continua)" if rel.parou_por_tempo else ""),
        flush=True,
    )
    # Falha "de verdade" só quando nada deu certo e algo falhou — o resto
    # (lojas com erro na API do GPS, como as compras da Reis) é esperado e
    # vai no relatório.
    return 1 if rel.unidades_ok == 0 and rel.unidades_com_falha > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
