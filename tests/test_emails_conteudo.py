"""O conteúdo dos e-mails para as lojas (pedido/emails_conteudo.py, 29/10/2026)."""
import types

import pandas as pd

from pedido import emails_conteudo as ec


def _todas(*linhas):
    """(linha, nome, curva, em ruptura?, valor 90 dias)"""
    return pd.DataFrame([{"linha": l, "nome": n, "curva": c, "curva_valor": c[0], "ruptura": r, "valor_90d": v,
                          "venda_90d": 10, "quantidade": 3} for l, n, c, r, v in linhas])


TODAS = _todas(("1", "AAS", "AB", True, 500), ("2", "BEPANTOL", "BC", True, 900), ("3", "CAFE", "CA", True, 2000),
               ("4", "DIPIRONA", "AA", False, 3000), ("5", "ESPARADRAPO", "AC", True, 100))


def test_zeraram_pela_primeira_letra_e_so_os_novos():
    assert set(ec.em_ruptura_ab(TODAS)["nome"]) == {"AAS", "BEPANTOL", "ESPARADRAPO"}   # CA fica fora, AC entra
    z = ec.zeraram(TODAS, anteriores={"1"})
    assert list(z["nome"]) == ["BEPANTOL", "ESPARADRAPO"]                             # maior venda primeiro


def test_primeiro_dia_da_loja_nao_avisa():
    assert ec.zeraram(TODAS, anteriores=None).empty


def test_limite_de_20_com_mais_outros():
    muitos = _todas(*[(str(i), f"P{i:02d}", "AA", True, 100 - i) for i in range(25)])
    s = ec.secao("60 · LOJA", "2026-10-30|2026-10-30T03:10:00-03:00", ec.zeraram(muitos, set()), None)
    assert len(s.zerados) == 20 and s.zerados_outros == 5
    _, texto, html, _ = ec.email("Ana Lima", [s], "30/10", "https://app")
    assert "+ 5 outros" in texto and "+ 5 outros" in html


def _relatorio():
    ind = types.SimpleNamespace(orcamento=3254.04, itens=20)
    cards = types.SimpleNamespace(ruptura=5, perda=870.0)
    criticos = pd.DataFrame([{"nome": "X", "em_ruptura": True, "repor_un": 9}])
    return ec.relatorio(ind, cards, criticos, "Perda em 30 dias se não repor")


def test_assunto_de_cada_caso():
    com = ec.secao("60 · LOJA A", None, ec.zeraram(TODAS, {"1"}), None)
    sem = ec.secao("61 · LOJA B", None, ec.zeraram(TODAS, None), _relatorio())
    assert ec.assunto([com], "30/10") == "2 produtos importantes zeraram · 60 · LOJA A"
    assert ec.assunto([sem], "30/10") == "Resumo do dia 30/10 · 61 · LOJA B"
    assert ec.assunto([sem, sem, sem], "30/10") == "Resumo do dia 30/10 · 3 lojas"
    um = ec.secao("60 · LOJA A", None, ec.zeraram(TODAS, {"1", "5"}), None)
    assert ec.assunto([um, com], "30/10") == "3 produtos importantes zeraram em 2 lojas"
    assert ec.assunto([um], "30/10") == "1 produto importante zerou · 60 · LOJA A"


def test_email_com_aviso_no_topo_relatorio_links_e_texto_escapado():
    perigoso = _todas(("9", "<script>X</script>", "AA", True, 10))
    s = ec.secao("60 · LOJA & CIA", "2026-10-30|2026-10-30T03:10:00-03:00", ec.zeraram(perigoso, set()), _relatorio())
    assunto, texto, html, anexos = ec.email("Ana Lima", [s], "30/10", "https://app.exemplo/")
    assert "<script>" not in html and "&lt;script&gt;" in html and "LOJA &amp; CIA" in html
    assert html.index("Zeraram") < html.index("Orçamento sugerido")
    assert "https://app.exemplo/?pagina=notificacoes" in html and "R$ 3.254,04" in texto
    assert "30/10 às 03:10" in texto and texto.startswith("Olá, Ana!")


def test_anexo_citado_no_corpo_e_devolvido():
    s = ec.secao("60 · LOJA", None, ec.zeraram(TODAS, None), _relatorio(), ("pedido_60_20261030.xlsx", b"x"))
    _, texto, html, anexos = ec.email("Ana", [s], "30/10", "")
    assert anexos == [("pedido_60_20261030.xlsx", b"x")]
    assert "pedido_60_20261030.xlsx" in texto and "em anexo" in html
