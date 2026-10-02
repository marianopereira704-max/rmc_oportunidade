"""Rotina de dados do Pedido — carga inicial e atualização diária são o
mesmo comando (o plano baixa "o que falta").

    python -m pedido                                  # tudo, no Spaces
    python -m pedido --parte 0 --partes 4             # 1/4 das empresas (GitHub Actions)
    python -m pedido --destino local:C:/tmp/pedido    # grava numa pasta (teste)
    python -m pedido --empresas ID1,ID2 --orcamento-minutos 30

Precisa de: GPS_API_BASE_URL, GPS_API_KEY; DO_SPACES_* (se o destino for o
Spaces); SISTEMA_INTERNO_BASE_URL e _TOKEN (lista de lojas ativas — sem
elas, processa todas as lojas do GPS, com aviso).
"""
from __future__ import annotations

import argparse
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


def _lojas_rmc_ativas() -> list[dict] | None:
    """Lojas ATIVAS do cadastro do RMC, direto da API do sistema interno (a
    rotina não alcança o Postgres — ver pedido/__init__.py). None = API não
    configurada."""
    cfg = settings.sistema_interno
    if not cfg.configured:
        return None
    resp = requests.get(cfg.base_url, headers={"X-API-KEY": cfg.token}, timeout=60)
    resp.raise_for_status()
    saida = []
    for item in resp.json():
        if item.get("active") is not True:
            continue
        end = item.get("address") or {}
        saida.append({
            "cnpj": item.get("cnpj"), "razao_social": item.get("businessName"),
            "nome_fantasia": item.get("fantasyName"), "numero": end.get("number"),
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
            "cnpj": l.get("CNPJ"), "numero": l.get("Numero"), "uf": l.get("Estado"),
        } for l in lojas_api]
        sugestoes = vinculo.sugerir(entrada, lojas_rmc)
        return [l for l, s in zip(lojas_api, sugestoes) if s.situacao != vinculo.SEM_CANDIDATO]
    return filtrar


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Rotina de dados do Pedido (API do GPS → Spaces).")
    p.add_argument("--parte", type=int, default=0)
    p.add_argument("--partes", type=int, default=1)
    p.add_argument("--destino", default="spaces", help="'spaces' ou 'local:<pasta>'")
    p.add_argument("--hoje", help="AAAA-MM-DD (padrão: hoje no fuso do Brasil)")
    p.add_argument("--empresas", help="ids separados por vírgula (teste)")
    p.add_argument("--orcamento-minutos", type=int, default=settings.pedido.orcamento_minutos)
    p.add_argument("--todas-as-lojas", action="store_true", help="não filtrar pelas lojas ativas do RMC")
    a = p.parse_args(argv)

    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date()
    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    chaves = Chaves(settings.pedido.prefixo)
    # Uma tentativa por chamada: quem repete é a fila da coleta, DEPOIS das
    # outras empresas — dá tempo ao GPS de terminar o cálculo que estourou.
    cliente = cliente_das_configuracoes(timeout_segundos=settings.pedido.timeout_consulta_segundos, tentativas=1)

    if a.empresas:
        empresas = [e.strip() for e in a.empresas.split(",") if e.strip()]
    else:
        empresas = [str(e["idEmpresa"]) for e in cliente.listar_empresas()]
    empresas = coleta.dividir(empresas, a.parte, a.partes)

    filtro = None
    if not a.todas_as_lojas:
        lojas_rmc = _lojas_rmc_ativas()
        if lojas_rmc is None:
            print("AVISO: API do sistema interno não configurada — processando todas as lojas do GPS.", flush=True)
        else:
            filtro = _filtro_lojas(lojas_rmc)

    parte = f"parte{a.parte + 1}de{a.partes}"
    print(f"Pedido — {hoje.isoformat()}, {parte}: {len(empresas)} empresa(s), orçamento {a.orcamento_minutos} min", flush=True)
    # Janela das Configurações de Pedidos (o app grava no Spaces ao salvar).
    meses = configuracao.meses_da_rotina(armaz, chaves, settings.pedido.meses_fechados)
    print(f"Janela: {meses} mes(es) fechado(s) + o mês corrente", flush=True)
    rotina = coleta.Coleta(
        cliente, armaz, chaves, hoje, meses, a.orcamento_minutos * 60,
        settings.pedido.tentativas_por_unidade, filtro_lojas=filtro,
    )
    rel = rotina.executar(empresas, parte)
    armaz.salvar_json(chaves.execucao(hoje, parte, time.strftime("%H%M%S")), coleta.relatorio_dict(rel))

    print(
        f"FIM — empresas processadas {rel.empresas_processadas}/{rel.empresas_na_parte} "
        f"(sem loja ativa: {rel.empresas_sem_loja_ativa}), lojas {rel.lojas}, consultas ok {rel.unidades_ok}, "
        f"falhas {rel.unidades_com_falha}, pendentes p/ próxima {len(rel.pendentes_para_proxima)}, "
        f"arquivos {rel.arquivos_gravados}, prontos {rel.prontos_montados}, {rel.segundos / 60:.1f} min"
        + (" — PAROU PELO TEMPO (a próxima execução continua)" if rel.parou_por_tempo else ""),
        flush=True,
    )
    # Falha "de verdade" só quando nada deu certo e algo falhou — o resto
    # (lojas com erro na API do GPS, como as compras da Reis) é esperado e
    # vai no relatório.
    return 1 if rel.unidades_ok == 0 and rel.unidades_com_falha > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
