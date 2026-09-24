"""Mede o quão bem as lojas da API do GPS casam com as lojas cadastradas no RMC.

POR QUE ESTE SCRIPT EXISTE
--------------------------
A API do GPS não expõe CNPJ de loja (vem `null`), e a razão social vem vazia
em parte da base. O único vínculo possível entre uma compra da API e uma loja
do RMC é, hoje, o TEXTO do nome mais cidade/UF. Antes de construir uma fila de
reconciliação inteira apostando nisso, este script mede se a aposta se paga:
quantas lojas casariam com alta confiança, quantas ficariam ambíguas e quantas
não casariam de jeito nenhum.

O resultado responde, de quebra, uma segunda pergunta importante: das 384
empresas da API, quantas têm alguma loja que é cliente do RMC. Só essas
precisam ser sincronizadas — o que muda a viabilidade da sincronização diária.

GARANTIAS
---------
- Não escreve NADA no banco: abre a conexão em modo read-only e só faz SELECT.
- Não escreve NADA na API: só chamadas GET.
- Não grava nada no sistema de arquivos além do cache e do relatório, ambos
  em arquivos próprios, com nome explícito.

COMO RODAR (na pasta do projeto, com o venv ativado)
----------------------------------------------------
    python medir_casamento_lojas.py

Precisa de, em `.streamlit/secrets.toml`:
    DATABASE_URL  = "..."        (já existe)
    GPS_API_BASE_URL = "http://143.244.153.213"
    GPS_API_KEY      = "<a chave>"

É demorado: são ~384 chamadas à API (uma por empresa). Por isso o script é
RETOMÁVEL — cada empresa lida é gravada em `cache_empresas_gps.json`, e rodar
de novo continua de onde parou em vez de recomeçar. Se precisar refazer do
zero, apague esse arquivo.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz, process
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).parent
CACHE = BASE_DIR / "cache_empresas_gps.json"
RELATORIO = BASE_DIR / "relatorio_casamento_lojas.csv"

# Acima disto, o casamento é considerado confiável o bastante para virar
# sugestão forte. Abaixo de MINIMO, nem vira sugestão.
LIMIAR_ALTA_CONFIANCA = 90
LIMIAR_MINIMO = 75
# Se o segundo melhor candidato está a menos disto do primeiro, o caso é
# ambíguo: dois nomes parecidos disputando a mesma loja é exatamente o
# cenário em que casar sozinho atribuiria compra à farmácia errada.
MARGEM_MINIMA = 5


# --- normalização ----------------------------------------------------------
# De propósito NÃO reaproveita `reconciliation.normalizador.normalizar_texto`:
# aquele expande abreviações farmacêuticas (CX -> CAIXA, COMPR -> COMPRIMIDO),
# o que faz sentido para nome de medicamento e atrapalha em nome de empresa.

_SUFIXOS_SOCIETARIOS = (
    "LTDA", "ME", "EPP", "EIRELI", "SA", "S A", "CIA", "MEI", "EI",
)


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def normalizar_nome(texto: str | None) -> str:
    if not texto:
        return ""
    t = _sem_acento(str(texto)).upper()
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for sufixo in _SUFIXOS_SOCIETARIOS:
        t = re.sub(rf"\b{sufixo}\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def limpar(valor) -> str:
    """Corta espaço em volta. A API devolve UF preenchida à direita em parte
    dos registros ('RN                '), e comparar sem isso erraria calado."""
    return str(valor).strip() if valor is not None else ""


# --- configuração ----------------------------------------------------------

def _secrets() -> dict:
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        import tomli as tomllib  # type: ignore

    caminho = BASE_DIR / ".streamlit" / "secrets.toml"
    if not caminho.exists():
        print("Não encontrei .streamlit/secrets.toml")
        sys.exit(1)
    return tomllib.loads(caminho.read_text(encoding="utf-8"))


def _config(dados: dict, chave: str) -> str | None:
    if chave in dados:
        return str(dados[chave])
    for secao in dados.values():
        if isinstance(secao, dict) and chave in secao:
            return str(secao[chave])
    return None


# --- coleta na API ---------------------------------------------------------

def coletar_empresas_da_api(base_url: str, api_key: str) -> dict[str, dict]:
    """Devolve {idEmpresa: detalhe_com_lojas}, usando cache em disco."""
    from integrations.gps_api import ClienteGpsApi

    cache: dict[str, dict] = {}
    if CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        print(f"Cache encontrado: {len(cache)} empresa(s) já lidas.")

    cliente = ClienteGpsApi(base_url=base_url, api_key=api_key, limite_pagina=1000, timeout_segundos=120)

    print("Listando empresas...")
    empresas = list(cliente.listar_empresas())
    print(f"{len(empresas)} empresa(s) na API.")

    pendentes = [e for e in empresas if str(e.get("idEmpresa")) not in cache]
    print(f"{len(pendentes)} empresa(s) a buscar (o resto veio do cache).")

    for i, empresa in enumerate(pendentes, start=1):
        id_empresa = str(empresa.get("idEmpresa"))
        try:
            cache[id_empresa] = cliente.detalhe_empresa(id_empresa)
        except Exception as exc:  # noqa: BLE001 — uma empresa problemática não pode parar a medição
            print(f"  [{i}/{len(pendentes)}] {id_empresa}: FALHOU ({type(exc).__name__}: {exc})")
            cache[id_empresa] = {"_erro": f"{type(exc).__name__}: {exc}", **empresa}
        else:
            nome = cache[id_empresa].get("NomeFantasia") or cache[id_empresa].get("RazaoSocial") or id_empresa
            qtd = len(cache[id_empresa].get("lojas") or [])
            print(f"  [{i}/{len(pendentes)}] {nome[:45]:45s} {qtd} loja(s)")
        # Grava a cada empresa: se cair no meio, nada do que já foi lido se perde.
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    return cache


# --- lojas do RMC ----------------------------------------------------------

def carregar_lojas_rmc(database_url: str) -> list[dict]:
    engine = create_engine(database_url)
    with engine.connect() as conexao:
        conexao.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        linhas = conexao.execute(text(
            "SELECT id, cnpj, razao_social, uf, cidade FROM lojas ORDER BY id"
        )).mappings().all()
    return [dict(l) for l in linhas]


# --- casamento -------------------------------------------------------------

def texto_identificador(loja_gps: dict) -> str:
    """O melhor texto disponível para identificar a loja: razão social quando
    existe, senão nome da loja / nome fantasia. A API preenche um OU outro
    dependendo da empresa."""
    for chave in ("RazaoSocial", "NomeLoja", "NomeFantasia"):
        valor = limpar(loja_gps.get(chave))
        if valor:
            return valor
    return ""


def medir(cache: dict[str, dict], lojas_rmc: list[dict]) -> list[dict]:
    # Índice das lojas do RMC por UF — casar só dentro da mesma UF corta
    # falso positivo entre farmácias de nome parecido em estados diferentes.
    por_uf: dict[str, list[dict]] = {}
    for loja in lojas_rmc:
        por_uf.setdefault(limpar(loja["uf"]).upper(), []).append(loja)

    resultados: list[dict] = []
    for id_empresa, empresa in cache.items():
        if empresa.get("_erro"):
            resultados.append({
                "idEmpresa": id_empresa, "CodigoLoja": "", "nome_gps": "",
                "uf_gps": "", "cidade_gps": "", "classificacao": "ERRO NA API",
                "melhor_rmc": "", "score": "", "segundo_rmc": "", "score_segundo": "",
            })
            continue

        for loja_gps in (empresa.get("lojas") or []):
            nome_gps = texto_identificador(loja_gps)
            uf = limpar(loja_gps.get("Estado")).upper()
            cidade = limpar(loja_gps.get("Cidade")).upper()
            candidatas = por_uf.get(uf, [])

            registro = {
                "idEmpresa": id_empresa,
                "CodigoLoja": limpar(loja_gps.get("CodigoLoja")),
                "nome_gps": nome_gps,
                "uf_gps": uf,
                "cidade_gps": cidade,
                "melhor_rmc": "", "score": "", "segundo_rmc": "", "score_segundo": "",
            }

            if not nome_gps:
                registro["classificacao"] = "SEM NOME NA API"
            elif not candidatas:
                registro["classificacao"] = "UF SEM LOJA RMC"
            else:
                alvo = normalizar_nome(nome_gps)
                escolhas = {i: normalizar_nome(c["razao_social"]) for i, c in enumerate(candidatas)}
                achados = process.extract(alvo, escolhas, scorer=fuzz.WRatio, limit=2)
                melhor = achados[0] if achados else None
                segundo = achados[1] if len(achados) > 1 else None

                if melhor:
                    registro["melhor_rmc"] = candidatas[melhor[2]]["razao_social"]
                    registro["score"] = round(melhor[1], 1)
                if segundo:
                    registro["segundo_rmc"] = candidatas[segundo[2]]["razao_social"]
                    registro["score_segundo"] = round(segundo[1], 1)

                score = melhor[1] if melhor else 0
                margem = (melhor[1] - segundo[1]) if (melhor and segundo) else 100
                if score >= LIMIAR_ALTA_CONFIANCA and margem >= MARGEM_MINIMA:
                    registro["classificacao"] = "ALTA CONFIANCA"
                elif score >= LIMIAR_MINIMO:
                    registro["classificacao"] = "AMBIGUO" if margem < MARGEM_MINIMA else "SUGESTAO FRACA"
                else:
                    registro["classificacao"] = "SEM CORRESPONDENCIA"

            resultados.append(registro)
    return resultados


def main() -> None:
    dados = _secrets()
    database_url = _config(dados, "DATABASE_URL")
    base_url = _config(dados, "GPS_API_BASE_URL")
    api_key = _config(dados, "GPS_API_KEY")
    if not database_url:
        print("Falta DATABASE_URL em secrets.toml"); sys.exit(1)
    if not base_url or not api_key:
        print("Faltam GPS_API_BASE_URL e/ou GPS_API_KEY em secrets.toml"); sys.exit(1)

    cache = coletar_empresas_da_api(base_url, api_key)
    print("\nLendo as lojas do RMC (somente leitura)...")
    lojas_rmc = carregar_lojas_rmc(database_url)
    print(f"{len(lojas_rmc)} loja(s) no RMC.")

    resultados = medir(cache, lojas_rmc)

    # --- relatório ---
    contagem: dict[str, int] = {}
    for r in resultados:
        contagem[r["classificacao"]] = contagem.get(r["classificacao"], 0) + 1

    total = len(resultados)
    print("\n" + "=" * 62)
    print(f"RESULTADO — {total} loja(s) da API do GPS avaliadas")
    print("=" * 62)
    for classificacao, qtd in sorted(contagem.items(), key=lambda x: -x[1]):
        print(f"  {classificacao:22s} {qtd:5d}  ({qtd/total*100:5.1f}%)")

    empresas_com_match = {
        r["idEmpresa"] for r in resultados if r["classificacao"] == "ALTA CONFIANCA"
    }
    print(f"\nEmpresas da API com ao menos uma loja casada com alta confiança: "
          f"{len(empresas_com_match)} de {len(cache)}")
    print("(só essas precisariam entrar na sincronização diária de compras)")

    cabecalho = ["idEmpresa", "CodigoLoja", "nome_gps", "uf_gps", "cidade_gps",
                 "classificacao", "melhor_rmc", "score", "segundo_rmc", "score_segundo"]
    import csv

    with RELATORIO.open("w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=cabecalho)
        escritor.writeheader()
        escritor.writerows(resultados)
    print(f"\nDetalhe linha a linha: {RELATORIO.name}")
    print("Nada foi gravado no banco nem na API.")


if __name__ == "__main__":
    main()
