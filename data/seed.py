"""Popula o banco com um cenário completo e volumoso — pensado pra testar a
paginação SERVER-SIDE de verdade (Bloco 4), bem diferente das amostras
pequenas (poucas linhas, casos de borda propositais) usadas nos testes
automatizados de integração do Bloco 2.

O volume gerado é configurável via `settings.seed` (core/config.py,
variáveis SEED_QTD_LOJAS / SEED_QTD_GENERICOS / SEED_QTD_MESES /
SEED_FRACAO_COBERTURA_COMPRAS / SEED_SEMENTE_ALEATORIA) — os defaults
(70 lojas x 320 genéricos x 8 meses x 65% de cobertura) resultam em
100k+ linhas em `registros_compra_gps`.

Roda com: `python -m data.seed` (a partir da raiz do projeto, com o venv
ativado). É idempotente no sentido de que sempre limpa e recria o cenário de
exemplo antes de gerar — nunca mexe em `Usuario` (bootstrap de autenticação,
cuidado de `core/db.py::init_db`).

Todo o texto de exemplo aqui (nomes de loja, laboratórios, moléculas) é
conteúdo fictício pra dar volume realista à demonstração — não é
configuração de negócio (ao contrário dos sinônimos de coluna, UFs,
limiares etc. já centralizados em core/config.py), por isso fica só neste
script de geração de dados, que não roda em produção.
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from core.config import settings
from core.db import get_session, init_db
from core.models import (
    BaseGenerico,
    EanGenerico,
    ItemTabelaGruppy,
    Loja,
    ModoCustoGruppy,
    OrigemResolucao,
    RegistroCompraGPS,
    StatusCobertura,
    TabelaGruppy,
    TabelaGruppyCobertura,
)

_CIDADES_POR_UF = {
    "SP": "São Paulo", "RJ": "Rio de Janeiro", "MG": "Belo Horizonte", "BA": "Salvador",
    "PR": "Curitiba", "RS": "Porto Alegre", "PE": "Recife", "CE": "Fortaleza",
    "SC": "Florianópolis", "GO": "Goiânia", "ES": "Vitória", "DF": "Brasília",
    "PA": "Belém", "AM": "Manaus", "MA": "São Luís", "PB": "João Pessoa",
    "RN": "Natal", "AL": "Maceió", "SE": "Aracaju", "MT": "Cuiabá",
    "MS": "Campo Grande", "PI": "Teresina", "RO": "Porto Velho", "TO": "Palmas",
    "AC": "Rio Branco", "AP": "Macapá", "RR": "Boa Vista",
}
_ATENDENTES = ["Ana Souza", "Bruno Lima", "Carla Dias", "Diego Alves", "Elisa Rocha"]
_CONSULTORES_FARMA = ["Fábio Nunes", "Gabriela Reis", "Hugo Martins"]
_CONSULTORES_INTERNOS = ["Igor Castro", "Julia Prado"]
_PREFIXOS_GRUPO_ECONOMICO = ["Rede Vitalle", "Grupo Saúde+", "Rede Bem Estar", "Grupo Popular Farma"]
_LABORATORIOS = ["Medley", "EMS", "Germed", "Neo Química", "Prati-Donaduzzi", "Eurofarma"]
_MOLECULAS = [
    "DIPIRONA SODICA", "PARACETAMOL", "AMOXICILINA", "IBUPROFENO", "LOSARTANA POTASSICA",
    "OMEPRAZOL", "METFORMINA", "SINVASTATINA", "AZITROMICINA", "CLORIDRATO DE SERTRALINA",
    "ATENOLOL", "HIDROCLOROTIAZIDA", "LORATADINA", "PREDNISONA", "CEFALEXINA",
    "ENALAPRIL", "CAPTOPRIL", "FLUCONAZOL", "CIPROFLOXACINO", "NIMESULIDA",
]
_DOSAGENS = ["10MG", "20MG", "50MG", "100MG", "250MG", "500MG", "750MG", "1G"]
_FORMAS = ["COMPRIMIDO", "CAPSULA", "SUSPENSAO", "XAROPE", "SOLUCAO INJETAVEL"]


def _gerar_nomes_genericos(quantidade: int) -> list[str]:
    """Combina molécula x dosagem x forma pra gerar nomes plausíveis o
    suficiente pra não repetir até `quantidade` ser bem maior que o pool
    base — cicla combinações se precisar de mais do que existem."""
    combinacoes = [
        f"{molecula} {dosagem} {forma}"
        for molecula in _MOLECULAS
        for dosagem in _DOSAGENS
        for forma in _FORMAS
    ]
    random.shuffle(combinacoes)
    if quantidade <= len(combinacoes):
        return combinacoes[:quantidade]
    # precisa de mais nomes do que o pool de combinações -> cicla com sufixo numérico
    nomes = list(combinacoes)
    i = 1
    while len(nomes) < quantidade:
        base = combinacoes[(len(nomes)) % len(combinacoes)]
        nomes.append(f"{base} (LOTE {i})")
        i += 1
    return nomes[:quantidade]


def _limpar_dados_exemplo(session: Session) -> None:
    """Apaga só as tabelas de dados de negócio (nunca Usuario/FSNode) —
    permite rodar o seed várias vezes seguidas sem acumular duplicata."""
    for modelo in (
        RegistroCompraGPS,
        ItemTabelaGruppy,
        TabelaGruppyCobertura,
        TabelaGruppy,
        EanGenerico,
        BaseGenerico,
        Loja,
    ):
        session.execute(delete(modelo))


def _criar_lojas(session: Session, ufs: list[str], quantidade: int) -> list[Loja]:
    lojas = []
    for i in range(quantidade):
        uf = ufs[i % len(ufs)]
        grupo = None
        if i % 4 == 0:  # ~25% das lojas fazem parte de algum grupo econômico
            # "i // 4" (não "i % 4", que aqui dentro do bloco é sempre 0)
            # pra realmente ciclar entre os grupos em vez de travar no primeiro.
            grupo = _PREFIXOS_GRUPO_ECONOMICO[(i // 4) % len(_PREFIXOS_GRUPO_ECONOMICO)]
        loja = Loja(
            cnpj=f"{10_000_000 + i:08d}0001{i % 90:02d}",
            razao_social=f"Farmácia {_CIDADES_POR_UF.get(uf, uf)} {i + 1:03d}",
            uf=uf,
            cidade=_CIDADES_POR_UF.get(uf, uf),
            atendente_comercial=_ATENDENTES[i % len(_ATENDENTES)],
            consultor_farma=_CONSULTORES_FARMA[i % len(_CONSULTORES_FARMA)],
            consultor_interno=_CONSULTORES_INTERNOS[i % len(_CONSULTORES_INTERNOS)],
            grupo_economico=grupo,
        )
        session.add(loja)
        lojas.append(loja)
    session.flush()
    return lojas


def _criar_genericos_e_eans(session: Session, quantidade: int) -> list[dict]:
    """Devolve uma lista de dicts {base_generico_id, eans: [...]} — cada
    genérico tem 1 EAN na maioria dos casos e 2 EAN em ~20% (pra também
    exercitar, em volume, a consolidação de múltiplos EAN do mesmo
    genérico feita em core/queries.py)."""
    nomes = _gerar_nomes_genericos(quantidade)
    resultado = []
    contador_ean = 1_000_000

    for nome in nomes:
        bg = BaseGenerico(nome_canonico=nome, criado_por="seed")
        session.add(bg)
        session.flush()

        qtd_eans = 2 if random.random() < 0.2 else 1
        eans = []
        for _ in range(qtd_eans):
            ean = str(contador_ean)
            contador_ean += 1
            session.add(
                EanGenerico(
                    ean=ean,
                    base_generico_id=bg.id,
                    origem_resolucao=OrigemResolucao.MANUAL,
                    descricao_origem_snapshot=nome,
                    resolvido_por="seed",
                )
            )
            eans.append(ean)
        resultado.append({"base_generico_id": bg.id, "eans": eans})

    session.flush()
    return resultado


def _criar_tabelas_gruppy(session: Session, ufs: list[str], genericos: list[dict]) -> None:
    """3 laboratórios "nacionais" (cobertura em todas as UFs usadas) + 2
    "regionais" (cobertura só numa amostra de UFs) — dá textura real ao
    escopo por UF em vez de todo mundo cobrir tudo igual."""
    labs_nacionais = _LABORATORIOS[:3]
    labs_regionais = _LABORATORIOS[3:]

    for idx, lab in enumerate(labs_nacionais):
        _criar_uma_tabela_gruppy(session, lab, ufs, genericos, fator_preco=0.85 + idx * 0.05)

    for lab in labs_regionais:
        ufs_regionais = random.sample(ufs, k=max(1, len(ufs) // 3))
        _criar_uma_tabela_gruppy(session, lab, ufs_regionais, genericos, fator_preco=0.9 + random.random() * 0.2)


def _criar_uma_tabela_gruppy(
    session: Session, laboratorio: str, ufs: list[str], genericos: list[dict], fator_preco: float
) -> None:
    tabela = TabelaGruppy(
        laboratorio=laboratorio, modo_custo=ModoCustoGruppy.PRONTO,
        nome_arquivo_origem=f"seed_{laboratorio.lower()}.xlsx", criado_por="seed",
    )
    session.add(tabela)
    session.flush()

    session.execute(
        insert(TabelaGruppyCobertura),
        [{"tabela_gruppy_id": tabela.id, "uf": uf, "status": StatusCobertura.ATIVA} for uf in ufs],
    )

    itens = []
    for item in genericos:
        preco_base = 8 + (item["base_generico_id"] % 40)  # varia por genérico, determinístico
        custo = round(preco_base * fator_preco, 2)
        itens.append({
            "tabela_gruppy_id": tabela.id,
            "ean": item["eans"][0],
            "descricao_origem": f"{laboratorio} - item {item['base_generico_id']}",
            "custo_liquido": custo,
        })
    session.execute(insert(ItemTabelaGruppy), itens)


def _ultimos_ano_meses(quantidade: int) -> list[str]:
    hoje = dt.date.today().replace(day=1)
    meses = []
    ano_mes = hoje
    for _ in range(quantidade):
        meses.append(ano_mes.strftime("%Y-%m"))
        ano_mes = (ano_mes.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    return meses


def _criar_compras_gps(
    session: Session, lojas: list[Loja], genericos: list[dict], ano_meses: list[str], fracao_cobertura: float
) -> int:
    """Bulk insert (não ORM objeto-a-objeto) — com 100k+ linhas, criar um
    RegistroCompraGPS por vez via `session.add` seria ordens de magnitude
    mais lento. Devolve a quantidade total de linhas inseridas."""
    total = 0
    for loja in lojas:
        for ano_mes in ano_meses:
            selecionados = [g for g in genericos if random.random() < fracao_cobertura]
            if not selecionados:
                continue
            linhas = []
            for item in selecionados:
                ean = random.choice(item["eans"])
                quantidade = random.randint(2, 60)
                custo_unitario = round((6 + (item["base_generico_id"] % 40)) * (0.9 + random.random() * 0.4), 4)
                pct_cmv = round(0.35 + random.random() * 0.35, 4)
                fat_liquido = round((custo_unitario * quantidade) / pct_cmv, 4)
                linhas.append({
                    "loja_id": loja.id,
                    "ean": ean,
                    "descricao_origem": f"compra genérico {item['base_generico_id']}",
                    "laboratorio_compra": None,
                    "ano_mes": ano_mes,
                    "quantidade": quantidade,
                    "fat_liquido": fat_liquido,
                    "pct_cmv": pct_cmv,
                    "custo_unitario": custo_unitario,
                    "estoque": random.randint(0, 40),
                })
            session.execute(insert(RegistroCompraGPS), linhas)
            total += len(linhas)
    return total


def rodar() -> None:
    init_db()
    cfg = settings.seed
    random.seed(cfg.semente_aleatoria)

    ufs = settings.geografia.ufs_brasil

    with get_session() as session:
        print("Limpando dados de exemplo anteriores...")
        _limpar_dados_exemplo(session)

    with get_session() as session:
        print(f"Criando {cfg.qtd_lojas} lojas...")
        lojas = _criar_lojas(session, ufs, cfg.qtd_lojas)

        print(f"Criando {cfg.qtd_genericos} genéricos (Base Genéricos + EAN)...")
        genericos = _criar_genericos_e_eans(session, cfg.qtd_genericos)

        print("Criando tabelas de preço Gruppy (nacionais + regionais)...")
        _criar_tabelas_gruppy(session, ufs, genericos)

    ano_meses = _ultimos_ano_meses(cfg.qtd_meses)
    with get_session() as session:
        print(f"Gerando compras GPS pra {len(ano_meses)} meses ({ano_meses[-1]} a {ano_meses[0]})...")
        total_compras = _criar_compras_gps(session, lojas, genericos, ano_meses, cfg.fracao_cobertura_compras)

    print(
        f"\nPronto: {cfg.qtd_lojas} lojas, {cfg.qtd_genericos} genéricos, "
        f"{total_compras:,} linhas em registros_compra_gps.".replace(",", ".")
    )


if __name__ == "__main__":
    rodar()
