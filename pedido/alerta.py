"""Alerta por e-mail no fim da rotina do Pedido (28/10/2026).

    python -m pedido.alerta --partes 8            # job `alerta` do workflow, depois das partes e do `rede`

O aviso de falha do GitHub cobre a execução que QUEBRA. Este cobre a que
termina "verde" mas trouxe menos do que devia — foi o que aconteceu de 25 a
28/10/2026: as partes terminavam sem erro, presas na fase de carga, e nenhuma
loja ganhou foto de estoque (a mais recente ficou em 23/10). Também de 03 a
07/10 as execuções morriam em ~30 s e ninguém soube por dias.

Lê os relatórios do dia (o último de cada parte), compara com a noite
anterior (`controle/alerta/{data}.json`, gravado aqui) e só manda e-mail
quando há problema. No log público (repositório público) vai só "alerta
enviado" ou "nada a avisar": os números ficam no e-mail e no Spaces.
"""
from __future__ import annotations

import argparse
import datetime as dt
import smtplib
import sys
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from core.config import settings
from pedido.armazenamento import ArmazenamentoLocal, do_ambiente
from pedido.plano import Chaves

# Queda de lojas com foto do dia, contra a noite anterior, que vira alerta. 5%: numa rede de ~400 lojas da API,
# 20 lojas sem estoque novo já é o bastante pra alguém olhar.
QUEDA_MAXIMA = 0.05


def _prefixo_alerta(chaves: Chaves) -> str:
    return f"{chaves.prefixo}/controle/alerta/"


def resumo(armaz, chaves: Chaves, hoje: dt.date, partes: int) -> dict:
    """Os números da execução do dia, somando o ÚLTIMO relatório de cada parte (uma parte pode ter dois no dia:
    carga e depois manutenção, ou um disparo manual)."""
    prefixo = f"{chaves.prefixo}/controle/execucoes/{hoje.isoformat()}/"
    ultimo: dict[str, str] = {}
    for c in sorted(c for c in armaz.listar(prefixo) if c.endswith(".json")):
        ultimo[c.rsplit("/", 1)[1].rsplit("-", 1)[0]] = c   # nome: {parte}-{HHMMSS}.json — a última vence
    rels = {parte: armaz.ler_json(c) for parte, c in ultimo.items()}
    esperadas = [f"parte{i + 1}de{partes}" for i in range(partes)]
    soma = lambda campo: sum(int(r.get(campo) or 0) for r in rels.values())  # noqa: E731
    return {
        "data": hoje.isoformat(),
        "partes_esperadas": partes,
        "partes_sem_relatorio": [p for p in esperadas if p not in rels],
        "partes_com_erro": sorted(p for p, r in rels.items() if r.get("erro_geral")),
        "partes_paradas_pelo_tempo": sorted(p for p, r in rels.items() if r.get("parou_por_tempo")),
        "fases": sorted({r.get("fase") or "completa" for r in rels.values()}),
        "lojas": soma("lojas"),
        "lojas_com_foto": soma("historicos_gravados"),
        "consultas_ok": soma("unidades_ok"),
        "consultas_com_falha": soma("unidades_com_falha"),
        "empresas_sem_lista_de_lojas": soma("empresas_sem_lista_de_lojas"),
    }


def ultima_data(armaz, chaves: Chaves) -> dt.date | None:
    """O dia mais recente com relatório da rotina."""
    prefixo = f"{chaves.prefixo}/controle/execucoes/"
    dias = {c[len(prefixo):].split("/")[0] for c in armaz.listar(prefixo) if c.endswith(".json")}
    return dt.date.fromisoformat(max(dias)) if dias else None


def anterior(armaz, chaves: Chaves, hoje: dt.date) -> dict | None:
    """O resumo gravado mais recente ANTES de hoje (a noite anterior, ou a última que rodou)."""
    prefixo = _prefixo_alerta(chaves)
    antes = [c for c in armaz.listar(prefixo) if c.endswith(".json") and c[len(prefixo):-5] < hoje.isoformat()]
    return armaz.ler_json(max(antes)) if antes else None


def problemas(atual: dict, ant: dict | None, queda_maxima: float = QUEDA_MAXIMA) -> list[str]:
    """O que merece e-mail, em frases. Vazio = nada a avisar."""
    saida = []
    if atual["partes_sem_relatorio"]:
        saida.append(f"{len(atual['partes_sem_relatorio'])} de {atual['partes_esperadas']} partes não deixaram "
                     f"relatório: {', '.join(atual['partes_sem_relatorio'])}.")
    if atual["partes_com_erro"]:
        saida.append(f"A execução caiu com erro em: {', '.join(atual['partes_com_erro'])} (motivo em Dados → Rotinas).")
    if atual["lojas_com_foto"] == 0:
        saida.append("Nenhuma loja ganhou foto de estoque hoje: o Assistente e o histórico da Ruptura ficaram sem "
                     f"o dia (fase da rotina: {', '.join(atual['fases']) or 'nenhuma'}).")
    elif ant and ant.get("lojas_com_foto") and atual["lojas_com_foto"] < ant["lojas_com_foto"] * (1 - queda_maxima):
        saida.append(f"Lojas com foto de estoque caíram de {ant['lojas_com_foto']} ({ant['data']}) para "
                     f"{atual['lojas_com_foto']} hoje.")
    return saida


def mensagem(atual: dict, ant: dict | None, lista: list[str]) -> tuple[str, str]:
    assunto = f"Rotina do Pedido — {len(lista)} problema(s) em {dt.date.fromisoformat(atual['data']):%d/%m/%Y}"
    linhas = ["A rotina de dados do Pedido terminou com problema:", ""]
    linhas += [f"- {p}" for p in lista]
    linhas += ["", "Números de hoje:",
               f"Lojas processadas: {atual['lojas']}.",
               f"Lojas com foto de estoque: {atual['lojas_com_foto']}"
               + (f" (anterior: {ant['lojas_com_foto']} em {ant['data']})." if ant else "."),
               f"Consultas ok: {atual['consultas_ok']}; com falha: {atual['consultas_com_falha']}.",
               f"Empresas sem lista de lojas: {atual['empresas_sem_lista_de_lojas']}.",
               f"Partes que pararam pelo tempo: {', '.join(atual['partes_paradas_pelo_tempo']) or 'nenhuma'}.",
               "", "Detalhes em Dados → Rotinas e na aba Actions do GitHub."]
    return assunto, "\n".join(linhas)


def enviar(destinatarios: list[str], assunto: str, texto: str) -> None:
    """Gmail do sistema (mesmo remetente dos e-mails de senha). Aqui e não em integrations/usuarios.py: a rotina
    não importa integrations/ (puxaria o banco)."""
    cfg = settings.email
    if not cfg.configured or not destinatarios:
        raise RuntimeError("Alerta sem e-mail configurado (EMAIL_REMETENTE, EMAIL_SENHA_APP, ALERTA_DESTINATARIOS).")
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = f"{cfg.nome_remetente} <{cfg.remetente}>"
    msg["To"] = ", ".join(destinatarios)
    msg.set_content(texto)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_porta, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg.remetente, cfg.senha_app)
        smtp.send_message(msg)


def main(argv: list[str] | None = None, enviar_email=enviar) -> int:
    p = argparse.ArgumentParser(description="Alerta por e-mail da rotina do Pedido.")
    p.add_argument("--partes", type=int, default=8)
    p.add_argument("--destino", default="spaces", help="'spaces' ou 'local:<pasta>'")
    p.add_argument("--hoje", help="AAAA-MM-DD (padrão: hoje no fuso do Brasil)")
    a = p.parse_args(argv)
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date()
    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    chaves = Chaves(settings.pedido.prefixo)

    # A execução é a do dia mais recente com relatório, não necessariamente "hoje": um disparo manual às 23h50
    # termina no dia seguinte, e o alerta procuraria relatórios que estão no dia anterior.
    data = ultima_data(armaz, chaves)
    if data is None or data < hoje - dt.timedelta(days=1):
        atual = {**resumo(armaz, chaves, hoje, a.partes), "ultima_execucao": data and data.isoformat()}
        lista = [f"Nenhum relatório da rotina desde {data:%d/%m/%Y}." if data else "Nenhum relatório da rotina."]
        ant = None
    else:
        atual = resumo(armaz, chaves, data, a.partes)
        ant = anterior(armaz, chaves, data)
        lista = problemas(atual, ant)
    armaz.salvar_json(f"{_prefixo_alerta(chaves)}{atual['data']}.json", {**atual, "problemas": lista})
    if not lista:
        print("Alerta: nada a avisar.", flush=True)
        return 0
    try:
        enviar_email(settings.email.destinatarios_alerta, *mensagem(atual, ant, lista))
    except Exception as exc:  # noqa: BLE001 — falha o job: o GitHub avisa do mesmo jeito
        print(f"ERRO: alerta com {len(lista)} problema(s) não foi enviado ({type(exc).__name__}).", flush=True)
        return 1
    print(f"Alerta enviado: {len(lista)} problema(s).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
