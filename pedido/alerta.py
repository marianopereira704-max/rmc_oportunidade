"""Alerta por e-mail no fim da rotina do Pedido (28/10/2026).

    python -m pedido.alerta --partes 8            # job `alerta` do workflow, depois das partes e do `rede`
    python -m pedido.alerta --partes 8 --precalculo     # + o cálculo de madrugada, quando o job dele rodou

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
# Backup dos bancos (scripts/backup_banco.sh, cron do droplet às 02:00, 29/10/2026): mesma pasta e mesmos bancos do
# script. O backup roda fora do GitHub; sem este aviso, um cron parado só seria notado no dia de precisar dele.
PASTA_BACKUP = "backups/rmc_oportunidades/"
BANCOS_BACKUP = ("rmc_oportunidades", "rmc_oportunidades_dev")


def problemas_backup(armaz, hoje: dt.date) -> list[str]:
    """Falta o backup de hoje ou de ontem de algum banco (de ontem também vale: o alerta de um disparo manual de
    madrugada pode rodar antes das 02:00)."""
    chaves = [c for c in armaz.listar(PASTA_BACKUP) if c.endswith(".sql.gz")]
    recentes = {(hoje - dt.timedelta(days=d)).isoformat() for d in (0, 1)}
    faltam = [b for b in BANCOS_BACKUP
              if not any(c == f"{PASTA_BACKUP}{dia}/{b}.sql.gz" for dia in recentes for c in chaves)]
    if not faltam:
        return []
    datas = sorted({c[len(PASTA_BACKUP):].split("/")[0] for c in chaves})
    desde = f" O mais recente é de {dt.date.fromisoformat(datas[-1]):%d/%m/%Y}." if datas else " Nenhum backup encontrado."
    return [f"Sem backup de hoje nem de ontem do banco {', '.join(faltam)}.{desde} Confira o cron do droplet "
            "(docs/backup.md)."]


def problemas_precalculo(armaz, chaves: Chaves, hoje: dt.date, partes: int) -> list[str]:
    """Cálculo de madrugada (pedido/precalculo.py, 29/10/2026): parte sem relatório (inclusive a que parou por falta
    da PRECALCULO_CHAVE) ou mais de 10% das lojas com falha. Nada disso quebra o app — a loja calcula na hora —, mas
    ela volta a demorar a abrir e ninguém saberia por quê."""
    from pedido import precalculo

    bancos = precalculo.bancos_publicados(armaz, chaves)
    if not bancos:
        return ["O cálculo de madrugada não achou as fotos dos insumos de nenhum banco: abra o app uma vez para "
                "publicar."]
    dia, por_banco = precalculo.ultima_execucao(armaz, chaves)
    if dia is None or dt.date.fromisoformat(dia) < hoje - dt.timedelta(days=1):
        return [f"Nenhum relatório do cálculo de madrugada desde {dt.date.fromisoformat(dia):%d/%m/%Y}." if dia
                else "Nenhum relatório do cálculo de madrugada."]
    saida = []
    esperadas = [f"parte{i + 1}de{partes}" for i in range(partes)]
    for banco in bancos:
        r = precalculo.resumo(por_banco.get(banco, []))
        faltam = [p for p in esperadas if p not in r["partes"]]
        if faltam:
            saida.append(f"Cálculo de madrugada do banco {banco}: {len(faltam)} de {partes} partes sem relatório "
                         f"({', '.join(faltam)}). Sem a PRECALCULO_CHAVE nos Secrets do GitHub nenhuma parte grava.")
        if r["lojas"] and r["com_falha"] > r["lojas"] * 0.10:
            saida.append(f"Cálculo de madrugada do banco {banco}: {r['com_falha']} de {r['lojas']} lojas com falha "
                         "(o erro de cada uma está no relatório no Spaces).")
    return saida


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
    p.add_argument("--coleta", default="",
                   help="resultado do job da coleta no GitHub (success, failure, cancelled, skipped)")
    p.add_argument("--precalculo", action="store_true", help="confere também o cálculo de madrugada")
    p.add_argument("--so-precalculo", action="store_true",
                   help="confere SÓ o cálculo de madrugada (disparo manual 'só o pré-cálculo': a coleta da noite já "
                        "foi conferida, e conferir de novo reenviaria o mesmo e-mail)")
    a = p.parse_args(argv)
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else dt.datetime.now(ZoneInfo(settings.pedido.fuso)).date()
    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    chaves = Chaves(settings.pedido.prefixo)

    if a.so_precalculo:
        lista = problemas_precalculo(armaz, chaves, hoje, a.partes)
        if not lista:
            print("Alerta: nada a avisar.", flush=True)
            return 0
        assunto = f"Cálculo de madrugada — {len(lista)} problema(s) em {hoje:%d/%m/%Y}"
        texto = "\n".join(["O cálculo de madrugada terminou com problema:", ""] + [f"- {p}" for p in lista]
                          + ["", "Enquanto isso as lojas calculam na hora, como antes. Detalhes em Dados → Rotinas."])
        return _enviar(enviar_email, lista, assunto, texto)

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
    # Parte cortada pelo GitHub (29/10/2026): morre sem relatório, e o último do dia dela pode ser um anterior (o da
    # carga, ou o de outro disparo) — pelos relatórios, a noite parecia completa. O GitHub diz.
    if a.coleta in ("failure", "cancelled"):
        lista.append(f"Alguma parte da coleta terminou como '{a.coleta}' no GitHub (cortada pelo limite de tempo ou "
                     "com erro): os números acima podem ser de um relatório anterior dela. Veja a aba Actions.")
    try:
        lista += problemas_backup(armaz, hoje)
    except Exception as exc:  # noqa: BLE001 — não conseguir conferir também é problema
        lista.append(f"Não deu para conferir o backup dos bancos ({type(exc).__name__}).")
    if a.precalculo:
        try:
            lista += problemas_precalculo(armaz, chaves, hoje, a.partes)
        except Exception as exc:  # noqa: BLE001
            lista.append(f"Não deu para conferir o cálculo de madrugada ({type(exc).__name__}).")
    armaz.salvar_json(f"{_prefixo_alerta(chaves)}{atual['data']}.json", {**atual, "problemas": lista})
    if not lista:
        print("Alerta: nada a avisar.", flush=True)
        return 0
    return _enviar(enviar_email, lista, *mensagem(atual, ant, lista))


def _enviar(enviar_email, lista: list[str], assunto: str, texto: str) -> int:
    try:
        enviar_email(settings.email.destinatarios_alerta, assunto, texto)
    except Exception as exc:  # noqa: BLE001 — falha o job: o GitHub avisa do mesmo jeito
        print(f"ERRO: alerta com {len(lista)} problema(s) não foi enviado ({type(exc).__name__}).", flush=True)
        return 1
    print(f"Alerta enviado: {len(lista)} problema(s).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
