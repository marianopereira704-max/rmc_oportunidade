"""A regra das dicas (core/dicas.py), em duas frentes: o verificador em si e uma varredura do código inteiro —
toda dica escrita no código (`dica=`, `help=`, "dica" de cards e do gráfico, constantes `_DICA_*`) tem que passar
pela regra, e `title=` de HTML (o balão do navegador) não pode mais existir."""
import ast
import pathlib

import pytest

from core import dicas

RAIZ = pathlib.Path(__file__).resolve().parent.parent
PASTAS = ("views", "core", "integrations")        # "pedido/" fica de fora: o help= de lá é do terminal (argparse)
CHAVES_DICA = {"dica", "dica_variacao"}
KWARGS_DICA = {"dica", "help"}


# --- o verificador ---------------------------------------------------------------------------------------

def test_dica_monta_paragrafos_com_ponto_final():
    assert dicas.dica("Uma  frase", "Outra frase.") == "Uma frase.\n\nOutra frase."
    assert dicas.dica("A: 1\nB: 2", None, "") == "A: 1.\nB: 2."


def test_texto_em_ordem_passa():
    assert dicas.problemas(dicas.dica("Unidades em estoque ÷ unidades vendidas por dia.", "Quem não vendeu fica sem cobertura.")) == []
    assert dicas.problemas("Editar") == []                    # rótulo de botão de ícone
    assert dicas.problemas("Tirar este filtro") == []


@pytest.mark.parametrize("texto", [
    "sem maiúscula.",
    "Esta frase não tem ponto final",
    "Frase um · frase dois.",
    "Frase — outra.",
    "Unidade(s) em estoque.",
    "Frase  com espaço duplo.",
    "Sai do GPS agora.",
    "Primeiro.\n\n\n\nSegundo.",
    "Uma frase muito longa " + "palavra " * 40 + "fim.",
    "A.\n\nB.\n\nC.\n\nD.\n\nE.",
    "",
])
def test_texto_fora_da_regra_e_acusado(texto):
    assert dicas.problemas(texto)


# --- a varredura do código -------------------------------------------------------------------------------

def _texto(no, chamadas=True):
    """O texto literal de uma expressão (f-string com {…} vira "1"), ou None se não for texto fixo."""
    if isinstance(no, ast.Constant) and isinstance(no.value, str):
        return no.value
    if isinstance(no, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "1" for v in no.values)
    if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Add):
        a, b = _texto(no.left), _texto(no.right)
        return None if a is None or b is None else a + b
    if chamadas and isinstance(no, ast.Call) and getattr(no.func, "attr", getattr(no.func, "id", "")) == "dica":
        partes = [_texto(a) for a in no.args]
        return dicas.dica(*[p for p in partes if p is not None]) if all(p is not None for p in partes) else None
    return None


def _candidatos(no):
    """Os textos de dica que a expressão pode ter (os dois ramos de um `x if c else y`)."""
    if isinstance(no, ast.IfExp):
        return _candidatos(no.body) + _candidatos(no.orelse)
    if isinstance(no, ast.Dict):                           # _DICA = {motivo: "texto", ...}
        return [t for v in no.values for t in _candidatos(v)]
    t = _texto(no)
    return [] if t is None else [t]


def _dicas_do_codigo():
    achados = []
    for pasta in PASTAS:
        for arquivo in sorted((RAIZ / pasta).glob("*.py")):
            arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
            for no in ast.walk(arvore):
                alvos = []
                if isinstance(no, ast.keyword) and no.arg in KWARGS_DICA:
                    alvos.append(no.value)
                elif isinstance(no, ast.Dict):
                    alvos += [v for k, v in zip(no.keys, no.values) if isinstance(k, ast.Constant) and k.value in CHAVES_DICA]
                elif isinstance(no, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith(("_DICA", "DICA_")) for t in no.targets):
                    alvos.append(no.value)
                elif isinstance(no, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "dica" for t in no.targets):
                    alvos.append(no.value)                        # dica = ... (variável que vai para um data-dica)
                elif isinstance(no, ast.Call) and getattr(no.func, "attr", getattr(no.func, "id", "")) in ("pedaco", "entrada")                         and len(no.args) >= 3:
                    alvos.append(no.args[2])                      # tb.pedaco(texto, classe, DICA) e tb.entrada(valor, classe, DICA)
                elif isinstance(no, ast.FunctionDef) and no.name.startswith("_dica"):
                    # montadores de dica (`_dica_compra`, `_dica_revisar`...): o que devolvem e o que juntam em listas
                    for sub in ast.walk(no):
                        if isinstance(sub, ast.Return) and sub.value is not None:
                            alvos.append(sub.value)
                        elif isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "append" and sub.args:
                            alvos.append(sub.args[0])
                        elif isinstance(sub, ast.Assign) and isinstance(sub.value, ast.List):
                            alvos += sub.value.elts
                for alvo in alvos:
                    for texto in _candidatos(alvo):
                        if texto == "":
                            continue                              # `else ""`: o elemento não tem dica
                        achados.append((f"{arquivo.relative_to(RAIZ)}:{alvo.lineno}", texto))
    return achados


def test_a_varredura_encontra_as_dicas():
    assert len(_dicas_do_codigo()) > 40            # se cair a zero, o teste deixou de enxergar o código


def test_toda_dica_do_codigo_segue_a_regra():
    erros = [f"{onde}: {texto[:60]!r} → {'; '.join(dicas.problemas(texto))}"
             for onde, texto in _dicas_do_codigo() if dicas.problemas(texto)]
    assert not erros, "Dicas fora da regra (core/dicas.py):\n" + "\n".join(erros)


def test_ninguem_usa_title_do_navegador_como_dica():
    """O `title="..."` do HTML abre um balão do navegador (tarde, sem parágrafos): a dica é `data-dica`."""
    usos = []
    for pasta in PASTAS:
        for arquivo in sorted((RAIZ / pasta).glob("*.py")):
            for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
                if isinstance(no, (ast.Constant, ast.JoinedStr)):
                    texto = _texto(no, chamadas=False) or ""
                    if ' title="' in texto and "<" in texto:
                        usos.append(f"{arquivo.relative_to(RAIZ)}:{no.lineno}")
    assert not usos, "title= em HTML (use data-dica, ver core/dicas.py): " + ", ".join(usos)


# --- as dicas montadas em tempo de execução (a varredura só vê o texto fixo) ----------------------------------

def _linha_pedido(**extra):
    base = {"revisar_motivo": None, "custo_cadastro": 3.53, "preco_nota": 40.29, "preco_venda": 9.9, "preco": 3.53,
            "compra_data": "2026-09-10", "compra_ignorada_data": "2026-08-20", "compra_ignorada_preco": 40.29,
            "custo_ajustado": None, "eans_negativos": "789123", "unidades_90d": 1, "curva_valor": "A"}
    return {**base, **extra}


def test_dicas_de_preco_do_assistente_seguem_a_regra():
    from pedido import calculo
    from views import pedido as v
    motivos = [calculo.MOTIVO_DIVERGENCIA, calculo.MOTIVO_ACIMA_VENDA, calculo.MOTIVO_CADASTRO, calculo.MOTIVO_COMPRA, None]
    textos = [v._dica_revisar(_linha_pedido(revisar_motivo=m)) for m in motivos]
    textos += [v._dica_revisar(_linha_pedido(revisar_motivo=None, compra_ignorada_preco=float("nan")))]
    textos += [v._dica_custo_ajustado(_linha_pedido(custo_ajustado=c)) for c in ("cadastro", "nota")]
    erros = [f"{t[:50]!r}: {dicas.problemas(t)}" for t in textos if dicas.problemas(t)]
    assert not erros, erros


def test_dicas_de_status_do_assistente_seguem_a_regra():
    from pedido import calculo
    from views import pedido as v
    p = calculo.Parametros()
    tags = [calculo.A_REVISAR, calculo.NEGATIVO, calculo.RUPTURA, calculo.RUPTURA_PROXIMA, calculo.CORRIGIDO,
            calculo.SEM_CLASSIFICACAO, calculo.GIRO_BAIXO, calculo.VENDA_PONTUAL]
    for unidades in (0, 1, 2):                               # singular e plural
        p = calculo.Parametros(ruptura_unidades=unidades, giro_baixo_max_unidades=max(unidades, 1))
        for tag in tags:
            texto = v._dica_status(tag, _linha_pedido(unidades_90d=unidades), p)
            assert texto is None or not dicas.problemas(texto), (tag, unidades, texto, dicas.problemas(texto or ""))


def test_dicas_da_analise_seguem_a_regra():
    from core import analise_api
    from views import analise_comum as c
    nota = {"nro_nf": "123", "data": "2026-09-10", "fornecedor": "DISTRIB", "quantidade": 1, "preco_nota": 5.0,
            "ajustado": True, "preco_pago": 3.0, "preco_venda": 4.0, "custo_cadastro": 3.0}
    textos = [c._dica_compra(nota), c._dica_compra({**nota, "quantidade": 12, "ajustado": False})]
    textos += [c._dica_revisar({**nota, "revisar_motivo": m}) for m in
               (analise_api.MOTIVO_DIVERGENCIA, analise_api.MOTIVO_ACIMA_VENDA, analise_api.MOTIVO_MEDIANA, None)]
    erros = [f"{t[:50]!r}: {dicas.problemas(t)}" for t in textos if dicas.problemas(t)]
    assert not erros, erros


def test_dicas_do_dashboard_estoque_seguem_a_regra():
    from core import dashboard_estoque as de
    from views import dashboard_estoque as v
    for motivo in (*de._MOTIVO_CUSTO, "codigo_novo", None):
        assert not dicas.problemas(v.dica_custo(motivo)), motivo
    assert "divergencia" not in v.dica_custo("divergencia")        # nunca o código cru


def test_qtd_e_atributo():
    assert dicas.qtd(1, "unidade") == "1 unidade" and dicas.qtd(1000, "unidade") == "1.000 unidades"
    assert dicas.qtd(2, "SKU", "SKUs") == "2 SKUs"
    atributo = dicas.atributo('Valor "R$ 5"\n\nSegundo & último.')
    assert "\n" not in atributo and "&#10;&#10;" in atributo and "&#36;" in atributo and "&quot;" in atributo
