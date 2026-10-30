"""O conteúdo dos e-mails para as lojas (29/10/2026) — `docs/specs/2026-10-29-emails-lojas-design.md`, partes 1 e 2.

Puro (sem banco nem Streamlit): a rotina (`pedido/emails.py`) entrega os resultados prontos de cada loja e recebe o
assunto, o texto e o HTML de cada pessoa. Decisões do Mariano: o aviso de ruptura leva os produtos de curva A e B
pela 1ª letra (o valor vendido — a mesma regra do "Ruptura por curva" do Dashboard) que zeraram desde a foto
anterior; um e-mail por pessoa, com todas as lojas dela; com o aviso e o relatório ligados, um e-mail só, o aviso no
topo. Todo texto que vem do banco (nome de produto, de loja) passa por `html.escape`.
"""
from __future__ import annotations

import dataclasses
import html

import pandas as pd

LIMITE_AVISO = 20      # produtos zerados por loja no e-mail; o resto vira "+ N outros"
LIMITE_CRITICOS = 10   # os mais críticos do relatório

NAVY, BORDA, SUPORTE, VERMELHO = "#17375E", "#E4E7EB", "#3F4750", "#9B1C1C"


# ---------------------------------------------------------------------------
# Formatos (os de core/ui.py, que importa o Streamlit)
# ---------------------------------------------------------------------------

def _num(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", ".")


def _moeda(v) -> str:
    inteiro, centavos = f"{float(v or 0):,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def _foto(foto: str | None) -> str:
    """"2026-10-30|2026-10-30T03:10:00-03:00" → "30/10 às 03:10"."""
    data, _, gerado = (foto or "").partition("|")
    if len(data) < 10:
        return "data desconhecida"
    hora = f" às {gerado[11:16]}" if len(gerado) >= 16 else ""
    return f"{data[8:10]}/{data[5:7]}{hora}"


# ---------------------------------------------------------------------------
# Quem zerou
# ---------------------------------------------------------------------------

def em_ruptura_ab(todas: pd.DataFrame) -> pd.DataFrame:
    """Em ruptura hoje (a regra da loja já está em `ruptura`) e com a 1ª letra da curva A ou B."""
    if todas.empty:
        return todas
    return todas[todas["ruptura"].astype(bool) & todas["curva_valor"].isin(["A", "B"])]


def zeraram(todas: pd.DataFrame, anteriores: set[str] | None) -> pd.DataFrame:
    """Os de `em_ruptura_ab` que NÃO estavam na lista da foto anterior, da maior venda em valor para a menor. Sem lista
    anterior (primeiro dia da loja), nada: não dá para saber o que zerou."""
    atuais = em_ruptura_ab(todas)
    if anteriores is None or atuais.empty:
        return atuais.iloc[:0]
    novos = atuais[~atuais["linha"].astype(str).isin(anteriores)]
    return novos.sort_values(["valor_90d", "nome"], ascending=[False, True], kind="stable")


# ---------------------------------------------------------------------------
# A seção de uma loja
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Secao:
    loja: str                           # "60 · RAZÃO SOCIAL"
    foto: str | None
    zerados: list[dict]                 # nome, curva, venda_90d, quantidade (até LIMITE_AVISO)
    zerados_outros: int
    relatorio: dict | None              # None = o relatório está desligado para esta pessoa
    # O Excel do pedido sugerido (nome do arquivo, conteúdo) — o mesmo do "Enviar pedido" do celular: EAN, PRODUTO e
    # QUANTIDADE (pedido do Mariano, 29/10/2026). None = a loja não tem nada a pedir.
    anexo: tuple[str, bytes] | None = None

    @property
    def tem_aviso(self) -> bool:
        return bool(self.zerados)


def secao(rotulo_loja: str, foto: str | None, zerados: pd.DataFrame, relatorio: dict | None,
          anexo: tuple[str, bytes] | None = None) -> Secao:
    lista = [{"nome": r["nome"], "curva": r["curva"], "venda_90d": r["venda_90d"], "quantidade": r["quantidade"]}
             for r in zerados.head(LIMITE_AVISO).to_dict("records")]
    return Secao(rotulo_loja, foto, lista, max(0, len(zerados) - LIMITE_AVISO), relatorio, anexo)


def relatorio(indicadores, cards, criticos: pd.DataFrame, rotulo_perda: str) -> dict:
    """Os números do relatório do dia: os cards do Assistente (`calculo.indicadores` do pedido) e os da Ruptura
    (`dashboard_ruptura.cards`), e os mais críticos (`dashboard_ruptura.criticos`, já na ordem da página)."""
    linhas = []
    for r in criticos.head(LIMITE_CRITICOS).to_dict("records"):
        un = int(r.get("repor_un") or 0)
        linhas.append({"nome": r["nome"], "situacao": "Em ruptura" if r.get("em_ruptura") else "Ruptura próxima",
                       "repor": f"repor {_num(un)} un." if un else "nada a repor"})
    return {"orcamento": float(indicadores.orcamento), "itens": int(indicadores.itens),
            "marcados": int(getattr(indicadores, "marcados", indicadores.itens)),
            "ruptura": int(cards.ruptura), "perda": float(cards.perda), "rotulo_perda": rotulo_perda,
            "criticos": linhas}


# ---------------------------------------------------------------------------
# O e-mail de uma pessoa
# ---------------------------------------------------------------------------

def assunto(secoes: list[Secao], hoje: str) -> str:
    """Com produto zerado: "3 produtos importantes zeraram · Loja" ("… em 2 lojas"); só o relatório: "Resumo do dia
    30/10 · Loja" ("… · 3 lojas")."""
    com_aviso = [s for s in secoes if s.tem_aviso]
    if com_aviso:
        n = sum(len(s.zerados) + s.zerados_outros for s in com_aviso)
        onde = com_aviso[0].loja if len(com_aviso) == 1 else f"em {len(com_aviso)} lojas"
        sep = " · " if len(com_aviso) == 1 else " "
        return f"{n} produto{'s' if n != 1 else ''} importante{'s' if n != 1 else ''} zer{'aram' if n != 1 else 'ou'}{sep}{onde}"
    onde = secoes[0].loja if len(secoes) == 1 else f"{len(secoes)} lojas"
    return f"Resumo do dia {hoje} · {onde}"


def _bloco_aviso_html(s: Secao) -> str:
    itens = "".join(
        f'<tr><td style="padding:6px 0;border-top:1px solid {BORDA}"><strong>{html.escape(str(z["nome"]))}</strong><br>'
        f'<span style="color:{SUPORTE};font-size:13px">Curva {html.escape(str(z["curva"]))} · vendeu '
        f'{_num(z["venda_90d"])} em 90 dias · pedir {_num(z["quantidade"])}</span></td></tr>' for z in s.zerados)
    outros = (f'<p style="margin:6px 0 0;color:{SUPORTE};font-size:13px">+ {_num(s.zerados_outros)} outros: veja no '
              f'Assistente de pedido.</p>' if s.zerados_outros else "")
    return (f'<div style="border:1px solid #F5D5D2;background:#FDF1F0;border-radius:10px;padding:12px 14px;margin:0 0 14px">'
            f'<div style="color:{VERMELHO};font-weight:700">Zeraram desde a foto anterior</div>'
            f'<div style="color:{SUPORTE};font-size:13px;margin-bottom:4px">Produtos que mais faturam na loja.</div>'
            f'<table role="presentation" style="width:100%;border-collapse:collapse">{itens}</table>{outros}</div>')


def _bloco_relatorio_html(r: dict) -> str:
    def numero(rotulo, valor, sub=""):
        sub = f'<div style="font-size:12px;color:{SUPORTE}">{sub}</div>' if sub else ""
        return (f'<td style="padding:8px;border:1px solid {BORDA};border-radius:8px;width:50%;vertical-align:top">'
                f'<div style="font-size:11px;font-weight:700;color:{SUPORTE};text-transform:uppercase">{rotulo}</div>'
                f'<div style="font-size:18px;font-weight:700;color:{NAVY}">{valor}</div>{sub}</td>')
    cards = (f'<table role="presentation" style="width:100%;border-collapse:separate;border-spacing:6px">'
             f'<tr>{numero("Orçamento sugerido", _moeda(r["orcamento"]))}{numero("Produtos para comprar", _num(r["itens"]), f"{_num(r['marcados'])} marcados no pedido")}</tr>'
             f'<tr>{numero("Em ruptura", _num(r["ruptura"]))}{numero(html.escape(r["rotulo_perda"]), _moeda(r["perda"]))}</tr>'
             f'</table>')
    criticos = "".join(
        f'<tr><td style="padding:6px 0;border-top:1px solid {BORDA}">{html.escape(str(c["nome"]))}<br>'
        f'<span style="color:{SUPORTE};font-size:13px">{c["situacao"]} · {c["repor"]}</span></td></tr>'
        for c in r["criticos"])
    titulo = (f'<div style="font-weight:700;color:{NAVY};margin:12px 0 2px">Os {len(r["criticos"])} mais críticos</div>'
              if r["criticos"] else "")
    return f'{cards}{titulo}<table role="presentation" style="width:100%;border-collapse:collapse">{criticos}</table>'


def _botao(rotulo: str, url: str) -> str:
    return (f'<a href="{html.escape(url)}" style="display:inline-block;background:{NAVY};color:#FFFFFF;'
            f'text-decoration:none;padding:10px 16px;border-radius:8px;font-weight:600;margin:4px 6px 4px 0">{rotulo}</a>')


def email(nome: str, secoes: list[Secao], hoje: str, app_url: str) -> tuple[str, str, str, list[tuple[str, bytes]]]:
    """(assunto, texto puro, HTML, anexos). `hoje` = "30/10"."""
    base = (app_url or "").rstrip("/")
    partes_html, partes_texto = [], []
    for s in secoes:
        corpo = _bloco_aviso_html(s) if s.tem_aviso else ""
        if s.relatorio is not None:
            corpo += _bloco_relatorio_html(s.relatorio)
        anexo = (f' O pedido sugerido vai em anexo, em Excel: <strong>{html.escape(s.anexo[0])}</strong> (EAN, produto '
                 f'e quantidade).' if s.anexo else "")
        partes_html.append(
            f'<div style="margin:18px 0"><div style="font-size:16px;font-weight:700;color:{NAVY};margin-bottom:8px">'
            f'{html.escape(s.loja)}</div>{corpo}<div style="color:{SUPORTE};font-size:12px;margin-top:8px">Dados do '
            f'estoque de {_foto(s.foto)}.{anexo}</div></div>')
        texto = [s.loja, ""]
        if s.tem_aviso:
            texto.append("Zeraram desde a foto anterior (produtos que mais faturam na loja):")
            texto += [f"- {z['nome']} · curva {z['curva']} · vendeu {_num(z['venda_90d'])} em 90 dias · pedir "
                      f"{_num(z['quantidade'])}" for z in s.zerados]
            if s.zerados_outros:
                texto.append(f"+ {_num(s.zerados_outros)} outros: veja no Assistente de pedido.")
            texto.append("")
        if s.relatorio is not None:
            r = s.relatorio
            texto += [f"Orçamento sugerido: {_moeda(r['orcamento'])}", f"Produtos para comprar: {_num(r['itens'])} ({_num(r['marcados'])} marcados no pedido)",
                      f"Em ruptura: {_num(r['ruptura'])}", f"{r['rotulo_perda']}: {_moeda(r['perda'])}"]
            if r["criticos"]:
                texto.append(f"Os {len(r['criticos'])} mais críticos:")
                texto += [f"- {c['nome']} · {c['situacao']} · {c['repor']}" for c in r["criticos"]]
            texto.append("")
        texto.append(f"Dados do estoque de {_foto(s.foto)}."
                     + (f" O pedido sugerido vai em anexo, em Excel: {s.anexo[0]} (EAN, produto e quantidade)."
                        if s.anexo else ""))
        partes_texto.append("\n".join(texto))

    lojas = ", ".join(s.loja for s in secoes)
    botoes = _botao("Abrir o Assistente de pedido", base or "#")
    if any(s.relatorio is not None for s in secoes):
        botoes += _botao("Ver a Ruptura no Dashboard", base or "#")
    link_preferencias = f"{base}/?pagina=notificacoes" if base else ""
    rodape = (f'<p style="color:{SUPORTE};font-size:12px;margin-top:20px;border-top:1px solid {BORDA};padding-top:10px">'
              f'Você recebe por estar cadastrado em {html.escape(lojas)}. Para mudar o que você recebe: '
              + (f'<a href="{html.escape(link_preferencias)}" style="color:{NAVY}">Minhas notificações</a>.'
                 if link_preferencias else "Minhas notificações, no sistema.") + "</p>")
    corpo_html = (f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1F2A37;max-width:560px">'
                  f'<p>Olá, {html.escape(nome.split()[0] if nome else "")}!</p>{"".join(partes_html)}'
                  f'<div style="margin-top:8px">{botoes}</div>{rodape}</div>')
    corpo_texto = (f"Olá, {nome.split()[0] if nome else ''}!\n\n" + "\n\n".join(partes_texto)
                   + f"\n\nAbrir o sistema: {base or '(endereço do sistema)'}"
                   + f"\n\nVocê recebe por estar cadastrado em {lojas}. Para mudar o que você recebe: Minhas notificações"
                   + (f" ({link_preferencias})." if link_preferencias else ", no sistema."))
    return assunto(secoes, hoje), corpo_texto, corpo_html, [s.anexo for s in secoes if s.anexo]
