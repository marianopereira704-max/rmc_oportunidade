"""
Configuração central do sistema.

Tudo que muda entre "meu ambiente de teste" e "produção real" (banco de dados,
credenciais do DigitalOcean Spaces, endpoint do sistema interno, limiares do
motor de reconciliação) vem daqui. Nada disso deve ser hardcoded no resto do
código: quando as credenciais reais chegarem, só se mexe neste arquivo (ou nas
variáveis de ambiente / secrets.toml que ele lê) e o sistema inteiro passa a
usar dados reais.

Ordem de prioridade de configuração:
1. st.secrets (usado quando roda dentro do Streamlit, inclusive Streamlit Cloud)
2. Variáveis de ambiente (usado em Docker / servidor próprio)
3. Valor padrão de desenvolvimento (SQLite local + storage local), só para permitir
   construir e testar o sistema sem depender de nenhuma credencial real ainda.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _get(key: str, default: str | None = None) -> str | None:
    """Busca uma configuração em st.secrets, depois em variável de ambiente,
    depois usa o default. Não falha se o Streamlit ainda não tiver secrets.toml.

    `RMC_IGNORAR_SECRETS=1` pula o secrets.toml e usa só variáveis de
    ambiente/defaults. Existe porque o secrets.toml da máquina de
    desenvolvimento aponta para o banco e o bucket de PRODUÇÃO, e fora do
    `streamlit run` não há outro jeito de trocar isso — é o que permite rodar
    `data.seed`, `alembic` ou um script contra um SQLite descartável."""
    if os.environ.get("RMC_IGNORAR_SECRETS") == "1":
        return os.environ.get(key, default)
    try:
        import streamlit as st

        if key in st.secrets:
            return str(st.secrets[key])
        # também aceita seções aninhadas, ex: [digitalocean] access_key = "..."
        for section in st.secrets.values():
            if hasattr(section, "get") and section.get(key) is not None:
                return str(section.get(key))
    except Exception:
        pass
    return os.environ.get(key, default)


def _get_list(key: str, default: list[str]) -> list[str]:
    """Mesma cadeia de prioridade de `_get`, mas para uma lista (formato
    "A,B,C" em secrets/env). Usado para valores que hoje seriam uma constante
    de código (ex: lista de UFs) — assim dá pra ajustar sem editar o código."""
    valor = _get(key)
    if not valor:
        return list(default)
    return [item.strip() for item in valor.split(",") if item.strip()]


def _get_int_list(key: str, default: list[int]) -> list[int]:
    """Variante inteira de `_get_list` — usada para opções numéricas
    configuráveis (ex: tamanhos de página disponíveis na paginação)."""
    valores = _get_list(key, [str(v) for v in default])
    try:
        return [int(v) for v in valores]
    except ValueError:
        return list(default)


def _get_dict(key: str, default: dict) -> dict:
    """Mesma ideia de `_get_list`, mas para estruturas mais ricas (dict de
    sinônimos, dict de abreviações) — formato JSON em secrets/env. Se o JSON
    não vier ou for inválido, cai no default (nunca quebra o app por causa de
    uma configuração opcional mal formatada)."""
    valor = _get(key)
    if not valor:
        return dict(default)
    try:
        carregado = json.loads(valor)
    except (TypeError, ValueError):
        return dict(default)
    return carregado if isinstance(carregado, dict) else dict(default)


@dataclass
class DatabaseConfig:
    # Em desenvolvimento usamos SQLite (arquivo local, zero setup).
    # Em produção, troque DATABASE_URL para algo como:
    #   postgresql+psycopg2://usuario:senha@host:5432/rmc_oportunidades
    url: str = field(default_factory=lambda: _get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    ))
    # Quanto tempo (ms) uma conexão SQLite espera por um lock antes de
    # desistir com "database is locked", em vez do default curto do driver.
    # 60s dá margem tanto pra uma importação de planilha grande (GPS chega a
    # ~150 mil linhas numa ÚNICA transação — ver integrations/gps.py) quanto
    # pra uma sincronização passageira de nuvem (OneDrive/Dropbox) ou outro
    # processo do próprio app segurando o arquivo por um instante — ver
    # core/db.py, só se aplica a SQLite. Subido de 30s pra 60s depois de um
    # "database is locked" real em produção local; WAL mode (também em
    # core/db.py) já limita a contenção a escritor-vs-escritor, mas o
    # timeout ainda precisa cobrir o pior caso de uma transação grande.
    sqlite_busy_timeout_ms: int = field(
        default_factory=lambda: int(_get("SQLITE_BUSY_TIMEOUT_MS", "60000"))
    )

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")


@dataclass
class SpacesConfig:
    """Credenciais do DigitalOcean Spaces (compatível com API S3, via boto3)."""
    endpoint_url: str | None = field(default_factory=lambda: _get("DO_SPACES_ENDPOINT"))
    region: str | None = field(default_factory=lambda: _get("DO_SPACES_REGION", "nyc3"))
    bucket: str | None = field(default_factory=lambda: _get("DO_SPACES_BUCKET"))
    access_key: str | None = field(default_factory=lambda: _get("DO_SPACES_ACCESS_KEY"))
    secret_key: str | None = field(default_factory=lambda: _get("DO_SPACES_SECRET_KEY"))

    @property
    def configured(self) -> bool:
        return bool(self.endpoint_url and self.bucket and self.access_key and self.secret_key)


@dataclass
class SistemaInternoConfig:
    """API do sistema interno (base de lojas). Já está pronta e rodando do
    lado da Rede — só falta receber as credenciais reais. Enquanto
    `configured` for False, o adapter reporta INDISPONIVEL/aguardando
    credenciais em vez de tentar chamar a API."""
    base_url: str | None = field(default_factory=lambda: _get("SISTEMA_INTERNO_BASE_URL"))
    token: str | None = field(default_factory=lambda: _get("SISTEMA_INTERNO_TOKEN"))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)


@dataclass
class GpsApiConfig:
    """API de consulta do GPS Farma (ERP) — a que deve substituir o upload
    manual da planilha GPS. Enquanto `configured` for False, o adapter reporta
    INDISPONIVEL em vez de tentar chamar.

    `dias_sobreposicao`: a sincronização automática NÃO pergunta "o que mudou
    desde a última vez" — ela sempre repuxa os últimos N dias. Motivo: no ERP
    uma nota pode ser lançada com data retroativa ou corrigida depois de já
    ter sido importada, e um corte por "desde a última sincronização" nunca
    traria essa correção de volta. Repuxar uma janela com sobreposição custa
    algumas chamadas a mais e cobre esse caso sem precisar detectar nada.

    `limite_pagina`: máximo aceito pela API é 1000 (visto no Swagger); usar o
    teto reduz o número de chamadas.
    """
    base_url: str | None = field(default_factory=lambda: _get("GPS_API_BASE_URL"))
    api_key: str | None = field(default_factory=lambda: _get("GPS_API_KEY"))
    dias_sobreposicao: int = field(default_factory=lambda: int(_get("GPS_API_DIAS_SOBREPOSICAO", "45")))
    limite_pagina: int = field(default_factory=lambda: int(_get("GPS_API_LIMITE_PAGINA", "1000")))
    timeout_segundos: int = field(default_factory=lambda: int(_get("GPS_API_TIMEOUT_SEGUNDOS", "60")))
    tentativas: int = field(default_factory=lambda: int(_get("GPS_API_TENTATIVAS", "3")))
    # Trava de segurança da paginação: se a API ignorasse o `offset` e
    # devolvesse sempre a mesma página, o laço nunca terminaria. Com o teto,
    # ele falha alto em vez de girar pra sempre consumindo a API.
    max_paginas: int = field(default_factory=lambda: int(_get("GPS_API_MAX_PAGINAS", "1000")))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)


@dataclass
class CookieConfig:
    """Segredo usado para assinar o cookie de sessão "lembrar-me". Nunca deve
    ficar hardcoded no código-fonte (bug conhecido do projeto anterior,
    corrigido aqui)."""
    segredo: str = field(default_factory=lambda: _get(
        "COOKIE_SECRET", "dev-apenas-nao-use-em-producao"
    ))


# --- Defaults abaixo: usados só quando não há override em secrets/env (ver
# _get_list/_get_dict). Nenhum código de negócio lê estes valores diretamente
# — sempre passam por `settings.*`, então trocar um comportamento é questão
# de configuração, não de editar código-fonte.

_ABREVIACOES_PADRAO: dict[str, str] = {
    "COMPR": "COMPRIMIDO", "COMP": "COMPRIMIDO", "CPR": "COMPRIMIDO",
    "CAPS": "CAPSULA", "CAP": "CAPSULA",
    "SUSP": "SUSPENSAO", "SOL": "SOLUCAO", "INJ": "INJETAVEL",
    "XPE": "XAROPE", "XAR": "XAROPE", "POM": "POMADA", "CREM": "CREME",
    "FR": "FRASCO", "CX": "CAIXA", "UN": "UNIDADE",
    "REV": "REVESTIDO", "LIB": "LIBERACAO", "PROL": "PROLONGADA",
    "GTS": "GOTAS", "GT": "GOTAS", "AMP": "AMPOLA",
    "PED": "PEDIATRICO", "ADT": "ADULTO",
    "GENER": "GENERICO", "GEN": "GENERICO", "BLIST": "BLISTER",
}

_UFS_BRASIL_PADRAO: list[str] = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
    "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]

# IPs de saída do Streamlit Community Cloud liberados no firewall do servidor
# de persistência (consultoria.rmc.tec.br) para a conexão com o Postgres —
# lista publicada em docs.streamlit.io/deploy/streamlit-community-cloud/status
# em 2026-09-09. O próprio Streamlit avisa que "essas IPs podem mudar a
# qualquer momento sem aviso"; por isso o sistema reconfere periodicamente
# (ver core/monitoramento.py) se a lista publicada ainda bate com esta, e
# avisa o admin se não bater mais — nunca ajusta o firewall sozinho.
_IPS_STREAMLIT_CLOUD_PADRAO: list[str] = [
    "35.230.127.150", "35.203.151.101", "34.19.100.134", "34.83.176.217",
    "35.230.58.211", "35.203.187.165", "35.185.209.55", "34.127.88.74",
    "34.127.0.121", "35.230.78.192", "35.247.110.67", "35.197.92.111",
    "34.168.247.159", "35.230.56.30", "34.127.33.101", "35.227.190.87",
    "35.199.156.97", "34.82.135.155",
]

_COLUNAS_GRUPPY_PADRAO: dict[str, list[str]] = {
    "ean": ["ean", "codigo", "sku", "codigoean", "codigobarras", "codigoproduto"],
    "descricao": ["descricao", "produto", "nomeproduto"],
    "custo_pronto": ["custo", "custoliquido", "precoliquido", "preco", "precotabela"],
    "preco_bruto": ["precobruto", "preco", "valorbruto", "precotabela"],
    "desconto": ["desconto", "percentualdesconto", "descontopercentual", "desc"],
}

_COLUNAS_GPS_PADRAO: dict[str, list[str]] = {
    "cnpj": ["cnpj", "cnpjloja"],
    "ean": ["ean", "codigo", "sku", "codigoean", "codigobarras", "codigoproduto"],
    "descricao": ["descricao", "produto", "nomeproduto"],
    # Ordem = prioridade (ver integrations/gps.py::detectar_colunas_automatico).
    # "qtd" ficou de fora de propósito: na exportação do BI, `QTD` é a
    # quantidade VENDIDA; a comprada é `Quantidade`.
    "quantidade": ["quantidade", "quantidadecomprada", "qtdcomprada"],
    # VlrUnitario: preço de compra unitário SEM ST (regra de custo desde 09/2026).
    "custo_unitario": ["vlrunitario", "valorunitario", "vlrunit", "custounitario", "precounitario"],
    # Opcionais, usados só no recuo de preço da análise quando o VlrUnitario
    # destoa do preço do laboratório (core/analise.py): custo CMV por unidade
    # vendida = Fat. líquido × %CMV ÷ QTD, e o "R$ Custo médio" da planilha.
    "fat_liquido": ["fatliquido", "faturamentoliquido"],
    "pct_cmv": ["cmv", "pctcmv", "percentualcmv"],
    "qtd_vendida": ["qtd", "qtdvendida", "quantidadevendida"],
    "custo_medio": ["rcustomedio", "customedio"],
    "laboratorio": ["laboratorio", "fornecedor", "laboratoriocompra", "fornecedorpago"],
    "razao_social": ["razaosocial", "nomeloja", "razao"],
}

_COLUNAS_BASE_GENERICOS_PADRAO: dict[str, list[str]] = {
    "ean": ["ean", "codigo", "sku", "codigoean", "codigobarras", "codigoproduto"],
    "descricao": ["descricaomarcos", "descricao", "produto", "nomeproduto", "genericocanonico"],
}


@dataclass
class AnaliseConfig:
    """Regras da Análise de Oportunidade (core/analise.py) decididas em
    09/2026 — ajustáveis sem editar código."""
    # Preço unitário abaixo disto é bonificação (produto recebido de graça):
    # fica fora da disputa de menor preço, da quantidade e da média.
    limite_bonificacao: float = field(default_factory=lambda: float(_get("ANALISE_LIMITE_BONIFICACAO", "0.10")))
    # Distância máxima (fração) entre o preço da loja e o do laboratório
    # escolhido pra o preço ser considerado coerente (0.5 = ±50%).
    tolerancia_preco: float = field(default_factory=lambda: float(_get("ANALISE_TOLERANCIA_PRECO", "0.5")))
    # Quantos meses carregados entram no "Preço médio".
    meses_preco_medio: int = field(default_factory=lambda: int(_get("ANALISE_MESES_PRECO_MEDIO", "3")))
    # Quantas combinações (laboratório, período) o app guarda calculadas em memória.
    cache_combinacoes: int = field(default_factory=lambda: int(_get("ANALISE_CACHE_COMBINACOES", "16")))


@dataclass
class ReconciliacaoConfig:
    """Limiares do motor de reconciliação de EAN (Base Genéricos). Começam
    conservadores — ajustar quando houver arquivo real grande o suficiente
    pra calibrar (ver plano de construção). `abreviacoes` também é
    configurável (RECON_ABREVIACOES em secrets/env, formato JSON
    {"SIGLA": "EXPANDIDA"}) — cresce sem precisar editar código conforme
    aparecem casos reais."""
    limiar_auto_aceite: float = field(default_factory=lambda: float(_get("RECON_LIMIAR_AUTO", "92")))
    limiar_fila_media: float = field(default_factory=lambda: float(_get("RECON_LIMIAR_MEDIA", "75")))
    abreviacoes: dict[str, str] = field(
        default_factory=lambda: _get_dict("RECON_ABREVIACOES", _ABREVIACOES_PADRAO)
    )


@dataclass
class ColunasMapeamentoConfig:
    """Sinônimos de nome de coluna aceitos ao importar planilhas (Gruppy/GPS)
    — cada grupo é sobrescrevível via secrets/env (COLUNAS_GRUPPY / COLUNAS_GPS,
    formato JSON {"campo": ["sinonimo1", "sinonimo2", ...]}), pra ajustar sem
    editar código se um fornecedor mudar o cabeçalho da planilha."""
    gruppy: dict[str, list[str]] = field(
        default_factory=lambda: _get_dict("COLUNAS_GRUPPY", _COLUNAS_GRUPPY_PADRAO)
    )
    gps: dict[str, list[str]] = field(
        default_factory=lambda: _get_dict("COLUNAS_GPS", _COLUNAS_GPS_PADRAO)
    )
    base_genericos: dict[str, list[str]] = field(
        default_factory=lambda: _get_dict("COLUNAS_BASE_GENERICOS", _COLUNAS_BASE_GENERICOS_PADRAO)
    )
    # Trechos de nome de coluna que uma Base Genéricos curada NUNCA tem (ela é
    # só EAN + nome canônico) — e que tabelas de preço (Gruppy) e compras
    # (GPS) sempre têm. Comparados por "contém", já normalizados (sem acento,
    # pontuação e maiúsculas): "R$ Unitário Bruto" → "runitariobruto" casa com
    # "unitario". Existe porque uma tabela Gruppy (EAN + "Produto") passava
    # na importação da Base e virava genérico canônico.
    proibidas_base_genericos: list[str] = field(default_factory=lambda: _get_list(
        "COLUNAS_PROIBIDAS_BASE_GENERICOS",
        ["preco", "custo", "valor", "desconto", "unitario", "bruto", "liquido", "quantidade", "cnpj", "pmc", "total"],
    ))


@dataclass
class GeografiaConfig:
    """Lista de UFs usada no seletor de cobertura da Gruppy — configurável
    (UFS_BRASIL em secrets/env, formato "SP,RJ,MG,...") em vez de fixa no
    código de integrations/gruppy.py."""
    ufs_brasil: list[str] = field(default_factory=lambda: _get_list("UFS_BRASIL", _UFS_BRASIL_PADRAO))


@dataclass
class StreamlitCloudConfig:
    """IPs de saída do Streamlit Community Cloud liberados no firewall do
    servidor de persistência — configurável (STREAMLIT_CLOUD_IPS_CONHECIDOS
    em secrets/env, formato "1.2.3.4,5.6.7.8,...") pra atualizar sem editar
    código quando a lista publicada pelo Streamlit mudar (ver
    core/monitoramento.py, que reconfere isso periodicamente e avisa o
    admin em vez de deixar a conexão quebrar em silêncio)."""
    ips_conhecidos: list[str] = field(
        default_factory=lambda: _get_list("STREAMLIT_CLOUD_IPS_CONHECIDOS", _IPS_STREAMLIT_CLOUD_PADRAO)
    )
    intervalo_verificacao_horas: int = field(
        default_factory=lambda: int(_get("STREAMLIT_CLOUD_IPS_INTERVALO_HORAS", "24"))
    )


@dataclass
class TemaConfig:
    """Parâmetros de layout do tema que fazem sentido variar por ambiente
    (ex: largura da sidebar) sem precisar editar core/theme.py."""
    sidebar_largura_px: int = field(default_factory=lambda: int(_get("SIDEBAR_LARGURA_PX", "272")))


@dataclass
class SeedConfig:
    """Volume/perfil de dados gerado por `data/seed.py` (Bloco 4) —
    configurável (SEED_* em secrets/env) pra rodar uma massa pequena em
    teste local ou grande o suficiente (100k+ linhas de compra) pra
    estressar a paginação server-side de verdade."""
    qtd_lojas: int = field(default_factory=lambda: int(_get("SEED_QTD_LOJAS", "70")))
    qtd_genericos: int = field(default_factory=lambda: int(_get("SEED_QTD_GENERICOS", "320")))
    qtd_meses: int = field(default_factory=lambda: int(_get("SEED_QTD_MESES", "8")))
    fracao_cobertura_compras: float = field(
        default_factory=lambda: float(_get("SEED_FRACAO_COBERTURA_COMPRAS", "0.65"))
    )
    semente_aleatoria: int = field(default_factory=lambda: int(_get("SEED_SEMENTE_ALEATORIA", "42")))


@dataclass
class AppConfig:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    spaces: SpacesConfig = field(default_factory=SpacesConfig)
    sistema_interno: SistemaInternoConfig = field(default_factory=SistemaInternoConfig)
    gps_api: GpsApiConfig = field(default_factory=GpsApiConfig)
    cookie: CookieConfig = field(default_factory=CookieConfig)
    reconciliacao: ReconciliacaoConfig = field(default_factory=ReconciliacaoConfig)
    analise: AnaliseConfig = field(default_factory=AnaliseConfig)
    colunas: ColunasMapeamentoConfig = field(default_factory=ColunasMapeamentoConfig)
    geografia: GeografiaConfig = field(default_factory=GeografiaConfig)
    streamlit_cloud: StreamlitCloudConfig = field(default_factory=StreamlitCloudConfig)
    tema: TemaConfig = field(default_factory=TemaConfig)
    seed: SeedConfig = field(default_factory=SeedConfig)
    local_storage_dir: Path = field(default_factory=lambda: Path(
        _get("LOCAL_STORAGE_DIR", str(BASE_DIR / "data" / "local_storage"))
    ))
    page_size_padrao: int = field(default_factory=lambda: int(_get("PAGE_SIZE_PADRAO", "50")))
    page_size_opcoes: list[int] = field(
        default_factory=lambda: _get_int_list("PAGE_SIZE_OPCOES", [25, 50, 100, 200])
    )
    periodos_meses_opcoes: list[int] = field(
        default_factory=lambda: _get_int_list("PERIODOS_MESES_OPCOES", [1, 2, 6])
    )
    nome_rede: str = field(default_factory=lambda: _get("NOME_REDE", "Rede"))
    nome_fornecedor: str = field(default_factory=lambda: _get("NOME_FORNECEDOR", "RMC"))
    logo_path: Path = field(default_factory=lambda: Path(
        _get("LOGO_PATH", str(BASE_DIR / "assets" / "logo_rmc.webp"))
    ))


settings = AppConfig()
