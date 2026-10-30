"""Cálculo de madrugada (29/10/2026) — `docs/specs/2026-10-29-calculo-madrugada-design.md`.

A rotina do GitHub monta, para cada loja, o resultado das 4 telas (Assistente de pedido e Dashboard: Estoque,
Ruptura, Compras) e grava no Spaces; o app lê o pronto em vez de calcular. Medido em 28/10/2026: 3–8 s por página
na 1ª abertura de uma loja grande e pico de 50–100 MB de memória ao montar.

    python -m pedido.precalculo --parte 0 --partes 8        # job `precalculo` do workflow
    python -m pedido.precalculo --lojas 60,64               # algumas lojas (teste)

A rotina não alcança o banco: calcula com as FOTOS dos insumos que o app publica (`integrations/insumos.py`, em
`pedido/insumos/<banco>/manifesto.json`) e com as MESMAS contas do app (`pedido/telas_loja.py`). Cada resultado leva
uma ASSINATURA de tudo o que entrou na conta (foto do estoque, versões das bases, regras da loja, correções de
estoque negativo, `VERSAO`); o app monta a mesma assinatura com o que tem na hora e só usa o pronto se as duas
baterem — mudou algo depois da madrugada (opção A, decisão do Mariano), a loja calcula na hora, como antes.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import pickle
import zlib

import pandas as pd

from pedido import selo
from pedido.configuracao import ConfigPedido

# Muda a cada mudança de REGRA de cálculo (pedido/calculo.py, core/dashboard_*.py, pedido/telas_loja.py): resultado
# gravado por código antigo não bate e o app calcula na hora até a próxima madrugada.
VERSAO = 1
TELAS = ("assistente", "estoque", "ruptura", "compras")


def _bibliotecas() -> str:
    """As versões das bibliotecas que fazem as contas também entram na assinatura (29/10/2026): o GitHub instalou o
    pandas 3.1 e os resultados feitos com ele diferiam do app (3.0.5) — valores de venda no Compras, tipo de coluna
    no Estoque — e ficavam "em dia" na execução seguinte. Versão diferente → o app calcula na hora."""
    import numpy
    import pyarrow

    return f"pandas {pd.__version__}; numpy {numpy.__version__}; pyarrow {pyarrow.__version__}"


BIBLIOTECAS = _bibliotecas()


# ---------------------------------------------------------------------------
# Assinatura — o app e a rotina chamam AS MESMAS funções
# ---------------------------------------------------------------------------

def _assinatura(tela: str, **campos) -> str:
    """JSON ordenado (tupla e lista viram a mesma coisa; as versões vêm do banco no app e do manifesto na rotina)."""
    texto = json.dumps({"tela": tela, "versao": VERSAO, "bibliotecas": BIBLIOTECAS, **campos}, sort_keys=True,
                       default=str, ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _params(params) -> dict:
    return dataclasses.asdict(params)


def assinatura_assistente(foto: str, versoes, meses: int, params, correcoes) -> str:
    return _assinatura("assistente", foto=foto, versoes=versoes, meses=meses, params=_params(params),
                       correcoes=correcoes)


def assinatura_estoque(foto: str, versao_categorias, versao_catalogo, config: ConfigPedido,
                       limite_bonificacao: float) -> str:
    return _assinatura("estoque", foto=foto, versao_categorias=versao_categorias, versao_catalogo=versao_catalogo,
                       config=dataclasses.asdict(config), limite=limite_bonificacao)


def assinatura_ruptura(foto: str, versoes, versao_catalogo, config: ConfigPedido, correcoes,
                       limite_bonificacao: float) -> str:
    return _assinatura("ruptura", foto=foto, versoes=versoes, versao_catalogo=versao_catalogo,
                       config=dataclasses.asdict(config), correcoes=correcoes, limite=limite_bonificacao)


def assinatura_compras(foto: str, versoes, versao_catalogo, config: ConfigPedido, limite_bonificacao: float) -> str:
    """Sem as correções de estoque negativo: o Compras não as usa — corrigir não derruba o pronto dele."""
    return _assinatura("compras", foto=foto, versoes=versoes, versao_catalogo=versao_catalogo,
                       config=dataclasses.asdict(config), limite=limite_bonificacao)


# ---------------------------------------------------------------------------
# Gravar e ler um resultado pronto
# ---------------------------------------------------------------------------

def _pasta_resultados(chaves, banco: str) -> str:
    return f"{chaves.prefixo}/resultados/{banco}/"


def _chave(chaves, banco: str, empresa: str, loja: str, tela: str, ext: str) -> str:
    return f"{_pasta_resultados(chaves, banco)}{empresa}/{loja}/{tela}.{ext}"


def assinatura_gravada(armaz, chaves, banco: str, empresa: str, loja: str, tela: str) -> str | None:
    k = _chave(chaves, banco, empresa, loja, tela, "json")
    return armaz.ler_json(k).get("assinatura") if armaz.existe(k) else None


def gravar(armaz, chaves, banco: str, empresa: str, loja: str, tela: str, assinatura: str, objeto, chave: str,
           segundos: float, agora: str) -> int:
    """O `.bin` (pickle comprimido e selado) primeiro e o `.json` (assinatura) por último: sem o `.json`, o app nem
    olha o `.bin` — um envio que caiu no meio nunca é lido pela metade. Devolve o tamanho gravado."""
    conteudo = selo.selar(zlib.compress(pickle.dumps(objeto, protocol=pickle.HIGHEST_PROTOCOL), 6), chave)
    armaz.salvar(_chave(chaves, banco, empresa, loja, tela, "bin"), conteudo)
    armaz.salvar_json(_chave(chaves, banco, empresa, loja, tela, "json"),
                      {"assinatura": assinatura, "gerado_em": agora, "segundos": round(segundos, 2),
                       "bytes": len(conteudo), "versao": VERSAO})
    return len(conteudo)


def ler(armaz, chaves, banco: str, empresa: str, loja: str, tela: str, assinatura: str, chave: str | None):
    """O resultado pronto, se existe, a assinatura bate e o selo confere; senão None (o app calcula na hora)."""
    if not chave or assinatura_gravada(armaz, chaves, banco, empresa, loja, tela) != assinatura:
        return None
    conteudo = selo.abrir(armaz.ler(_chave(chaves, banco, empresa, loja, tela, "bin")), chave)
    if conteudo is None:
        return None
    return pickle.loads(zlib.decompress(conteudo))


# ---------------------------------------------------------------------------
# Fotos dos insumos (lado da rotina — sem banco)
# ---------------------------------------------------------------------------

def pasta_insumos(chaves, banco: str) -> str:
    return f"{chaves.prefixo}/insumos/{banco}/"


def bancos_publicados(armaz, chaves) -> list[str]:
    prefixo = f"{chaves.prefixo}/insumos/"
    return sorted({c[len(prefixo):].split("/")[0] for c in armaz.listar(prefixo) if c.endswith("/manifesto.json")})


@dataclasses.dataclass
class Insumos:
    banco: str
    padrao: ConfigPedido
    personalizacoes: dict[int, dict]
    correcoes: dict[int, tuple]
    lojas: list[dict]
    versoes: list
    versao_catalogo: list
    base_categorias: pd.DataFrame
    genericos: pd.DataFrame
    tabela_classificacao: pd.DataFrame

    def config(self, loja_id: int) -> ConfigPedido:
        from pedido import configuracao as cfg

        return cfg.aplicar(self.padrao, self.personalizacoes.get(loja_id, {}))

    def correcoes_da_loja(self, loja_id: int) -> tuple:
        return self.correcoes.get(loja_id, ())


def ler_insumos(armaz, chaves, banco: str) -> Insumos:
    """O manifesto do banco e as 3 tabelas que ele aponta; a classificação é montada aqui (0,5 s), com o mapa do
    PADRÃO — a classificação é do produto, não se personaliza por loja (como no app)."""
    from pedido import classificacao as cl

    m = armaz.ler_json(f"{pasta_insumos(chaves, banco)}manifesto.json")
    padrao = ConfigPedido.de_dict(m["config_padrao"])
    universo = armaz.ler_df(m["tabelas"]["classificacao_universo"])
    por_setor, por_cmed = dict(sorted(padrao.linhas_por_setor.items())), dict(sorted(padrao.linhas_por_cmed.items()))
    tabela = cl.tabela_por_ean(cl.classificar(universo, por_setor, por_cmed), por_cmed)
    return Insumos(
        banco=banco, padrao=padrao,
        personalizacoes={int(k): v for k, v in m["personalizacoes"].items()},
        correcoes={int(k): tuple(tuple(par) for par in v) for k, v in m["correcoes"].items()},
        lojas=m["lojas"], versoes=m["versoes"], versao_catalogo=m["versao_catalogo"],
        base_categorias=armaz.ler_df(m["tabelas"]["base_categorias"]),
        genericos=armaz.ler_df(m["tabelas"]["genericos"]),
        tabela_classificacao=tabela,
    )


# ---------------------------------------------------------------------------
# A rotina
# ---------------------------------------------------------------------------

def foto_da_loja(armaz, chaves, empresa: str) -> str | None:
    """O mesmo texto que o app usa na chave do cache e na assinatura (`views/loja_barra.marcador_estoque`)."""
    k = chaves.marcador_estoque(empresa)
    if not armaz.existe(k):
        return None
    m = armaz.ler_json(k)
    return f"{m.get('data')}|{m.get('gerado_em')}"


def calcular_loja(armaz, chaves, ins: Insumos, loja: dict, chave: str, limite_bonificacao: float, agora: str,
                  relogio=None) -> dict[str, str]:
    """As 4 telas de uma loja: o que foi feito em cada uma ("calculada", "em dia" ou "sem dado")."""
    import time

    from pedido import telas_loja

    relogio = relogio or time.monotonic
    empresa, lg, loja_id = loja["empresa_gps"], loja["loja_gps"], int(loja["id"])
    foto = foto_da_loja(armaz, chaves, empresa)
    if foto is None:
        return {t: "sem dado" for t in TELAS}
    config = ins.config(loja_id)
    params = config.parametros(limite_bonificacao)
    meses = config.meses_fechados
    corr = ins.correcoes_da_loja(loja_id)
    assinaturas = {
        "assistente": assinatura_assistente(foto, ins.versoes, meses, params, corr),
        "estoque": assinatura_estoque(foto, ins.versoes[0], ins.versao_catalogo, config, limite_bonificacao),
        "ruptura": assinatura_ruptura(foto, ins.versoes, ins.versao_catalogo, config, corr, limite_bonificacao),
        "compras": assinatura_compras(foto, ins.versoes, ins.versao_catalogo, config, limite_bonificacao),
    }
    # O cálculo do Assistente serve a 3 telas: com as correções (Assistente e Ruptura) e sem (Compras) — o mesmo
    # que o app pede ao cache dele. Feito uma vez, só se alguma tela precisar.
    calcs: dict = {}

    def calc(com_correcoes: bool):
        k = bool(com_correcoes and corr)
        if k not in calcs:
            calcs[k] = telas_loja.assistente(armaz, chaves, empresa, lg, meses, params, dict(corr) if k else {},
                                             ins.base_categorias, ins.genericos)
        return calcs[k]

    contas = {
        "assistente": lambda: calc(True),
        "estoque": lambda: telas_loja.estoque(armaz, chaves, empresa, lg, meses, config, ins.base_categorias,
                                              ins.tabela_classificacao),
        "ruptura": lambda: telas_loja.ruptura(armaz, chaves, empresa, lg, meses, config, calc(True),
                                              ins.tabela_classificacao),
        "compras": lambda: telas_loja.compras(armaz, chaves, empresa, lg, meses, config, lambda: calc(False),
                                              ins.genericos, ins.tabela_classificacao, limite_bonificacao),
    }
    feito = {}
    for tela in TELAS:
        if assinatura_gravada(armaz, chaves, ins.banco, empresa, lg, tela) == assinaturas[tela]:
            feito[tela] = "em dia"
            continue
        inicio = relogio()
        objeto = contas[tela]()
        if objeto is None:              # sem pronto: o app também daria None, nada a guardar
            feito[tela] = "sem dado"
            continue
        gravar(armaz, chaves, ins.banco, empresa, lg, tela, assinaturas[tela], objeto, chave, relogio() - inicio, agora)
        feito[tela] = "calculada"
    return feito


def limpar(armaz, chaves, ins: Insumos) -> int:
    """Apaga, um arquivo por vez, os resultados de lojas que saíram do manifesto, só dentro de
    `pedido/resultados/<banco>/` (o bucket é compartilhado: nunca apagar por padrão de nome)."""
    pasta = _pasta_resultados(chaves, ins.banco)
    ativas = {(str(l["empresa_gps"]), str(l["loja_gps"])) for l in ins.lojas}
    apagados = 0
    for k in armaz.listar(pasta):
        partes = k[len(pasta):].split("/")
        if len(partes) == 3 and (partes[0], partes[1]) not in ativas and partes[2].split(".")[0] in TELAS:
            armaz.apagar(k)
            apagados += 1
    return apagados


def rodar(armaz, chaves, parte: int, partes: int, chave: str, limite_bonificacao: float, hoje: str, agora: str,
          lojas_filtro: set[str] | None = None) -> list[dict]:
    """Uma parte da rotina, para cada banco com fotos publicadas. Devolve o relatório de cada banco."""
    import time
    import traceback

    from pedido import coleta

    relatorios = []
    for banco in bancos_publicados(armaz, chaves):
        inicio = time.monotonic()
        ins = ler_insumos(armaz, chaves, banco)
        lojas = [l for l in ins.lojas if lojas_filtro is None or str(l.get("legacy_id")) in lojas_filtro]
        por_id = {str(l["id"]): l for l in lojas}
        minhas = coleta.dividir(sorted(por_id), parte, partes)
        rel = {"banco": banco, "parte": f"parte{parte + 1}de{partes}", "hoje": hoje, "inicio": agora,
               "lojas_na_parte": len(minhas), "lojas_prontas": 0, "calculadas": 0, "em_dia": 0, "sem_dado": 0,
               "lojas_com_falha": 0, "falhas": [], "apagados": 0}
        for loja_id in minhas:
            try:
                feito = calcular_loja(armaz, chaves, ins, por_id[loja_id], chave, limite_bonificacao, agora)
            except Exception as exc:  # noqa: BLE001 — uma loja não para as outras; o app calcula na hora
                rel["lojas_com_falha"] += 1
                rel["falhas"].append({"loja_id": loja_id, "erro": f"{type(exc).__name__}: {str(exc)[:200]}",
                                      "onde": traceback.extract_tb(exc.__traceback__)[-1].name})
                continue
            for situacao in feito.values():
                rel[{"calculada": "calculadas", "em dia": "em_dia", "sem dado": "sem_dado"}[situacao]] += 1
            rel["lojas_prontas"] += "sem dado" not in feito.values()
        if parte == 0 and lojas_filtro is None:
            rel["apagados"] = limpar(armaz, chaves, ins)
        rel["segundos"] = round(time.monotonic() - inicio, 1)
        armaz.salvar_json(f"{_pasta_relatorios(chaves)}{hoje}/{banco}/{rel['parte']}.json", rel)
        relatorios.append(rel)
    return relatorios


def _pasta_relatorios(chaves) -> str:
    return f"{chaves.prefixo}/controle/precalculo/"


def ultima_execucao(armaz, chaves) -> tuple[str | None, dict[str, list[dict]]]:
    """(dia, banco → relatórios das partes) da execução mais recente. Um relatório por parte e por dia: um disparo
    manual no mesmo dia regrava o da parte. Para o alerta e para Dados → Rotinas."""
    prefixo = _pasta_relatorios(chaves)
    chaves_rel = [c for c in armaz.listar(prefixo) if c.endswith(".json")]
    if not chaves_rel:
        return None, {}
    dia = max(c[len(prefixo):].split("/")[0] for c in chaves_rel)
    por_banco: dict[str, list[dict]] = {}
    for c in sorted(c for c in chaves_rel if c[len(prefixo):].startswith(dia + "/")):
        por_banco.setdefault(c[len(prefixo):].split("/")[1], []).append(armaz.ler_json(c))
    return dia, por_banco


def resumo(relatorios: list[dict]) -> dict:
    """As partes de um banco somadas: lojas, prontas (as 4 telas), com falha e quando começou."""
    soma = lambda campo: sum(int(r.get(campo) or 0) for r in relatorios)  # noqa: E731
    return {"partes": sorted(r.get("parte", "") for r in relatorios), "lojas": soma("lojas_na_parte"),
            "prontas": soma("lojas_prontas"), "com_falha": soma("lojas_com_falha"),
            "inicio": min((r["inicio"] for r in relatorios if r.get("inicio")), default=None)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import datetime as dt
    from zoneinfo import ZoneInfo

    from core.config import settings
    from pedido.armazenamento import ArmazenamentoLocal, do_ambiente
    from pedido.plano import Chaves

    p = argparse.ArgumentParser(description="Cálculo de madrugada: as 4 telas de cada loja, prontas no Spaces.")
    p.add_argument("--parte", type=int, default=0)
    p.add_argument("--partes", type=int, default=1)
    p.add_argument("--lojas", help="códigos das lojas (os da barra lateral), separados por vírgula, para teste")
    p.add_argument("--destino", default="spaces", help="'spaces' ou 'local:<pasta>'")
    a = p.parse_args(argv)
    chave = settings.pedido.precalculo_chave
    if not chave:
        print("ERRO: sem PRECALCULO_CHAVE nos Secrets; nada foi calculado (o app segue calculando na hora).",
              flush=True)
        return 1
    armaz = ArmazenamentoLocal(a.destino.split(":", 1)[1]) if a.destino.startswith("local:") else do_ambiente()
    agora = dt.datetime.now(ZoneInfo(settings.pedido.fuso))
    filtro = {c.strip() for c in a.lojas.split(",") if c.strip()} if a.lojas else None
    relatorios = rodar(armaz, Chaves(settings.pedido.prefixo), a.parte, a.partes, chave,
                       settings.analise.limite_bonificacao, agora.date().isoformat(),
                       agora.isoformat(timespec="seconds"), filtro)
    if not relatorios:
        print("Nenhum banco com fotos dos insumos publicadas: abra o app uma vez para publicar.", flush=True)
    for r in relatorios:
        # Log público (repositório público): só contagens, sem nome de loja.
        print(f"{r['banco']} {r['parte']}: {r['lojas_na_parte']} loja(s); telas calculadas {r['calculadas']}, em dia "
              f"{r['em_dia']}, sem dado {r['sem_dado']}; lojas com falha {r['lojas_com_falha']}; "
              f"{r['segundos'] / 60:.1f} min", flush=True)
    falhou = sum(r["lojas_com_falha"] for r in relatorios)
    total = sum(r["lojas_na_parte"] for r in relatorios)
    return 1 if total and falhou > total * 0.10 else 0


if __name__ == "__main__":
    raise SystemExit(main())
