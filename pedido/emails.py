"""E-mails para as lojas (29/10/2026) — `docs/specs/2026-10-29-emails-lojas-design.md`.

    python -m pedido.emails                         # job "E-mails" do workflow, depois do "Pré-cálculo"
    python -m pedido.emails --so-para eu@x.com      # conferência: TUDO para um endereço só, nada registrado
    python -m pedido.emails --previa <pasta>        # grava os e-mails em arquivos (.html e .txt), sem enviar

A rotina não alcança o banco: lê a lista de destinatários que o app publica (`integrations/destinatarios.py`) e os
resultados prontos do cálculo de madrugada (`pedido/precalculo.py`) — as mesmas contas e a mesma assinatura que o app
usaria. Um e-mail por pessoa, com todas as lojas dela; o aviso de ruptura no topo, o relatório embaixo (conteúdo:
`pedido/emails_conteudo.py`).

Estado no Spaces (`pedido/controle/emails/<banco>/`):
- `ruptura/<empresa>/<loja>.json` — a foto e os produtos de curva A/B em ruptura nela, e os da foto anterior: o
  "zerou" é contra a foto anterior; rodar de novo com a mesma foto usa a mesma anterior (nada se perde);
- `enviados/<dia>.json` — quem já recebeu no dia (só depois do envio dar certo): nunca duas vezes;
- `relatorios/<dia>.json` — enviados, pulados por motivo e falhas. No log público (repositório público), só contagens.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from pedido import emails_conteudo as ec
from pedido import precalculo

# O Gmail da conta comum aceita ~500 por dia, e a mesma conta manda os e-mails de senha e o alerta: folga de 100.
LIMITE_DIA = 400


def _pasta(chaves, banco: str) -> str:
    return f"{chaves.prefixo}/controle/emails/{banco}/"


def bancos_com_lista(armaz, chaves) -> list[str]:
    from integrations.destinatarios import ARQUIVO

    prefixo = f"{chaves.prefixo}/insumos/"
    return sorted({c[len(prefixo):].split("/")[0] for c in armaz.listar(prefixo) if c.endswith("/" + ARQUIVO)})


def _ler(armaz, k: str, padrao):
    return armaz.ler_json(k) if armaz.existe(k) else padrao


class Loja:
    """O que o e-mail precisa de uma loja: lido uma vez por execução, servido a todas as pessoas dela."""

    def __init__(self, armaz, chaves, ins, loja: dict, chave: str, limite: float, hoje: dt.date):
        from core import dashboard_ruptura as dr

        self.loja, self.foto, self.motivo = loja, None, None
        self.todas = self.d = self.resumo = None
        foto, assinaturas = precalculo.assinaturas_da_loja(armaz, chaves, ins, loja, limite)
        if foto is None:
            self.motivo = "sem foto de estoque"
            return
        # "Sem foto nova": a foto não é da noite (a de ontem ou de hoje). Um e-mail com a foto de dias atrás
        # repetiria o de antes.
        if dt.date.fromisoformat(foto.split("|")[0]) < hoje - dt.timedelta(days=1):
            self.motivo = "sem foto nova"
            return
        emp, lg = loja["empresa_gps"], loja["loja_gps"]
        assistente = precalculo.ler(armaz, chaves, ins.banco, emp, lg, "assistente", assinaturas["assistente"], chave)
        ruptura = precalculo.ler(armaz, chaves, ins.banco, emp, lg, "ruptura", assinaturas["ruptura"], chave)
        if assistente is None or ruptura is None:
            self.motivo = "sem resultado pronto"
            return
        from pedido import calculo

        self.foto = foto
        # A área de trabalho vazia: a sugestão do sistema, como o Assistente abre de manhã (é a área que cria a
        # quantidade e a marcação de cada produto).
        self.todas = calculo.aplicar_area(assistente[0], {})
        (self.d, self.resumo), _meta = ruptura
        self.regras = dr.Regras.da_config(ins.config(int(loja["id"])))
        self.tabela = ins.tabela_classificacao

    @property
    def rotulo(self) -> str:
        nome = (self.loja.get("razao_social") or "").strip()
        return f"{self.loja.get('legacy_id')} · {nome}" if self.loja.get("legacy_id") is not None else nome

    def cortes(self, linhas) -> tuple[pd.DataFrame, pd.DataFrame]:
        """As linhas de compra do Comprador, como no Assistente (Em classificação aparece para todos)."""
        from pedido import classificacao as cl
        from pedido import linhas as lc

        if not linhas:
            return self.todas, self.d
        mapa = cl.mapa_da_loja(self.tabela, cl.eans_da_tabela(self.todas))
        d = self.d[self.d["linha_compra"].isin(set(linhas) | {lc.SEM_LINHA})] if not self.d.empty else self.d
        return cl.filtrar(self.todas, linhas, mapa), d


def _relatorio(loja: Loja, todas: pd.DataFrame, d: pd.DataFrame) -> dict:
    from core import dashboard_ruptura as dr
    from pedido import calculo

    pedido = calculo.lista(todas, ocultar_giro_baixo=True)
    cards = dr.cards(d)
    if loja.resumo["modo"] == dr.MODO_MEDIDA:
        rotulo = f"Perda nos últimos {loja.resumo['periodo']} dias"
    else:
        rotulo = "Perda em 30 dias se não repor"
    return ec.relatorio(calculo.indicadores(pedido), cards, dr.criticos(d), rotulo)


def _anexo(loja: Loja, todas: pd.DataFrame, hoje: dt.date) -> tuple[str, bytes] | None:
    """O Excel do "Enviar pedido" do celular (pedido do Mariano, 29/10/2026): EAN, PRODUTO e QUANTIDADE dos
    marcados com quantidade — o pedido como o Assistente abre de manhã, já cortado nas linhas do Comprador."""
    from pedido import calculo, planilha

    pedido = calculo.lista(todas, ocultar_giro_baixo=True)
    tabela = calculo.exportacao_envio(pedido[pedido["selecionado"] & (pedido["quantidade"] > 0)])
    if tabela.empty:
        return None
    codigo = loja.loja.get("legacy_id") or loja.loja["id"]
    return f"pedido_{codigo}_{hoje:%Y%m%d}.xlsx", planilha.excel({"Pedido": tabela})


def rodar(armaz, chaves, chave: str | None, limite_bonificacao: float, hoje: dt.date, agora: str, enviar,
          app_url: str = "", so_para: str | None = None, previa: Path | None = None) -> list[dict]:
    """Uma execução, para cada banco com lista de destinatários. `enviar(destino, assunto, texto, html)`."""
    relatorios = []
    for banco in bancos_com_lista(armaz, chaves):
        from integrations.destinatarios import chave as chave_lista

        pasta = _pasta(chaves, banco)
        rel = {"banco": banco, "dia": hoje.isoformat(), "inicio": agora, "pessoas": 0, "enviados": 0, "falhas": 0,
               "pulados": {}, "lojas_puladas": {}, "limite_atingido": False, "fora_pelo_limite": 0,
               "so_para": bool(so_para), "previa": bool(previa)}
        pular = lambda motivo: rel["pulados"].__setitem__(motivo, rel["pulados"].get(motivo, 0) + 1)  # noqa: E731
        lista = armaz.ler_json(chave_lista(chaves, banco)).get("destinatarios", [])
        ins = precalculo.ler_insumos(armaz, chaves, banco)
        k_enviados = f"{pasta}enviados/{hoje.isoformat()}.json"
        enviados = _ler(armaz, k_enviados, {})
        registra = not (so_para or previa)
        lojas: dict[int, Loja] = {}
        estados: dict[int, dict] = {}

        def da_loja(l: dict) -> Loja:
            if l["id"] not in lojas:
                lojas[l["id"]] = Loja(armaz, chaves, ins, l, chave, limite_bonificacao, hoje)
            return lojas[l["id"]]

        def anteriores(lj: Loja) -> set[str] | None:
            """Os de curva A/B em ruptura na foto ANTERIOR; e o estado novo a gravar no fim."""
            if lj.loja["id"] not in estados:
                k = f"{pasta}ruptura/{lj.loja['empresa_gps']}/{lj.loja['loja_gps']}.json"
                atual = _ler(armaz, k, None)
                hoje_linhas = sorted(ec.em_ruptura_ab(lj.todas)["linha"].astype(str))
                if atual is None:
                    ant, novo = None, {"foto": lj.foto, "linhas": hoje_linhas, "anterior": None}
                elif atual.get("foto") == lj.foto:      # mesma foto (de novo no dia): a mesma anterior
                    ant, novo = atual.get("anterior"), atual
                else:
                    ant = {"foto": atual.get("foto"), "linhas": atual.get("linhas", [])}
                    novo = {"foto": lj.foto, "linhas": hoje_linhas, "anterior": ant}
                estados[lj.loja["id"]] = {"chave": k, "novo": novo, "anteriores": set(ant["linhas"]) if ant else None}
            return estados[lj.loja["id"]]["anteriores"]

        for pessoa in lista:
            rel["pessoas"] += 1
            if registra and str(pessoa["id"]) in enviados:
                pular("já recebeu hoje")
                continue
            secoes = []
            for l in pessoa["lojas"]:
                lj = da_loja(l)
                if lj.todas is None:
                    rel["lojas_puladas"][lj.motivo] = rel["lojas_puladas"].get(lj.motivo, 0) + 1
                    continue
                todas, d = lj.cortes(pessoa.get("linhas"))
                ant = anteriores(lj)
                z = ec.zeraram(todas, ant) if pessoa.get("ruptura") else todas.iloc[:0]
                r = _relatorio(lj, todas, d) if pessoa.get("relatorio") else None
                if z.empty and r is None:
                    continue
                secoes.append(ec.secao(lj.rotulo, lj.foto, z, r, _anexo(lj, todas, hoje)))
            if not secoes:
                pular("nada a avisar")
                continue
            if not previa and len(enviados) + rel["enviados"] >= LIMITE_DIA:
                rel["limite_atingido"] = True
                rel["fora_pelo_limite"] += 1
                continue
            assunto, texto, html, anexos = ec.email(pessoa.get("nome") or "", secoes, f"{hoje:%d/%m}", app_url)
            if previa:
                previa.mkdir(parents=True, exist_ok=True)
                base = previa / f"{banco}_{pessoa['id']}"
                base.with_suffix(".html").write_text(html, encoding="utf-8")
                base.with_suffix(".txt").write_text(f"{assunto}\n\n{texto}", encoding="utf-8")
                for nome_arquivo, conteudo in anexos:
                    (previa / f"{banco}_{pessoa['id']}_{nome_arquivo}").write_bytes(conteudo)
                rel["enviados"] += 1
                continue
            try:
                enviar(so_para or pessoa["email"], assunto, texto, html, anexos)
            except Exception as exc:  # noqa: BLE001 — um envio que falha não para os outros
                rel["falhas"] += 1
                rel.setdefault("erros", []).append({"usuario_id": pessoa["id"], "erro": type(exc).__name__})
                continue
            rel["enviados"] += 1
            if registra:
                enviados[str(pessoa["id"])] = agora
                armaz.salvar_json(k_enviados, enviados)    # a cada envio: uma queda no meio não reenvia ninguém
        if registra:
            for e in estados.values():
                armaz.salvar_json(e["chave"], e["novo"])
        if not previa:   # a prévia não deixa rastro no Spaces (nem o relatório que o alerta lê)
            armaz.salvar_json(f"{pasta}relatorios/{hoje.isoformat()}.json", rel)
        relatorios.append(rel)
    return relatorios


def _enviar_gmail(destino: str, assunto: str, texto: str, html: str, anexos=()) -> None:
    """Gmail do sistema (o mesmo dos e-mails de senha e do alerta). Aqui e não em integrations/usuarios.py: a
    rotina não importa integrations/ que puxem o banco."""
    import smtplib
    from email.message import EmailMessage

    from core.config import settings

    cfg = settings.email
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = f"{cfg.nome_remetente} <{cfg.remetente}>"
    msg["To"] = destino
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    for nome_arquivo, conteudo in anexos:   # o Excel do pedido de cada loja
        msg.add_attachment(conteudo, maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=nome_arquivo)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_porta, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg.remetente, cfg.senha_app)
        smtp.send_message(msg)


def main(argv: list[str] | None = None, enviar=None) -> int:
    from zoneinfo import ZoneInfo

    from core.config import settings
    from pedido.armazenamento import ArmazenamentoLocal, do_ambiente
    from pedido.plano import Chaves

    p = argparse.ArgumentParser(description="E-mails para as lojas: aviso de ruptura e relatório diário.")
    p.add_argument("--so-para", help="manda TUDO para este endereço (conferência); nada fica registrado")
    p.add_argument("--previa", help="grava os e-mails nesta pasta (.html e .txt) em vez de enviar")
    p.add_argument("--destino", default="spaces", help="'spaces' ou 'local:<pasta>'")
    p.add_argument("--hoje", help="AAAA-MM-DD (padrão: hoje no fuso do Brasil)")
    a = p.parse_args(argv)
    agora = dt.datetime.now(ZoneInfo(settings.pedido.fuso))
    hoje = dt.date.fromisoformat(a.hoje) if a.hoje else agora.date()
    chave = settings.pedido.precalculo_chave
    if not chave:
        print("ERRO: sem PRECALCULO_CHAVE: os resultados prontos não podem ser lidos; nenhum e-mail saiu.", flush=True)
        return 1
    if not a.previa and enviar is None and not settings.email.configured:
        print("ERRO: sem EMAIL_REMETENTE/EMAIL_SENHA_APP; nenhum e-mail saiu.", flush=True)
        return 1
    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    chaves = Chaves(settings.pedido.prefixo)
    if not bancos_com_lista(armaz, chaves):
        print("ERRO: nenhuma lista de destinatários publicada (abra o app uma vez).", flush=True)
        return 1
    rels = rodar(armaz, chaves, chave, settings.analise.limite_bonificacao, hoje, agora.isoformat(timespec="seconds"),
                 enviar or _enviar_gmail, settings.email.app_url or "", a.so_para,
                 Path(a.previa) if a.previa else None)
    for r in rels:
        # Log público: só contagens.
        print(f"{r['banco']}: {r['pessoas']} pessoa(s); enviados {r['enviados']}, falhas {r['falhas']}, "
              f"pulados {sum(r['pulados'].values())}, fora pelo limite {r['fora_pelo_limite']}", flush=True)
    falhas = sum(r["falhas"] for r in rels)
    tentados = falhas + sum(r["enviados"] for r in rels)
    return 1 if tentados and falhas > tentados * 0.10 else 0


if __name__ == "__main__":
    sys.exit(main())
