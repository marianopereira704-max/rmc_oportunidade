"""
Modelos de dados (SQLAlchemy).

Pensados para rodar tanto em SQLite (desenvolvimento) quanto em Postgres
(produção) sem mudar nada de código — só a connection string em core/config.py.

Visão geral do domínio:
- Usuario: login único (CNPJ fixo da Rede), senha diferencia o papel
  (admin/consultor). Consultor enxerga a rede inteira — não há escopo por loja.
- Loja: vem do sistema interno (API real, credenciais ainda não configuradas
  nesta fase de desenvolvimento).
- BaseGenerico / EanGenerico: a "Base Genéricos" — nome canônico do produto
  genérico e o mapeamento (muitos-para-um) de EAN cru (vindo tanto da Gruppy
  quanto do GPS) para esse nome canônico. É a ÚNICA fonte de junção EAN→
  genérico: nenhuma outra tabela guarda base_generico_id diretamente, então
  resolver um EAN uma vez conserta retroativamente todo o histórico já
  importado, sem precisar de backfill.
- FilaResolucaoEAN / FilaCnpjOrfao: filas de pendência — nunca descartam
  silenciosamente um EAN ou CNPJ que o sistema não conseguiu casar sozinho.
- TabelaGruppy / TabelaGruppyCobertura / ItemTabelaGruppy: uma tabela de
  preço da Gruppy é upload = 1 registro, mas a vigência (qual tabela "vale"
  agora) é controlada por UF, não pela tabela inteira — por isso a cobertura
  geográfica é uma tabela própria, com status ativa/inativa por UF.
- RegistroCompraGPS: o que a loja efetivamente comprou (de qualquer
  fornecedor) num determinado mês, incluindo o estoque daquele mês (o arquivo
  GPS real traz estoque na mesma linha da compra — não é upload separado).
  "Economia" nunca é uma coluna gravada aqui: é sempre calculada cruzando isto
  com ItemTabelaGruppy em core/queries.py, pra nunca ficar desatualizada
  quando o preço da RMC mudar.
- FSNode: metadado do "diretório estilo Windows" sobre o DigitalOcean Spaces.
  Nada é excluído de verdade — "inativar" só move o nó para status inativo.

Pedido/Dashboard ficam fora do escopo desta construção (nunca foram
desenhados em detalhe) — por isso não há modelo de Pedido aqui ainda.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

class Papel(str, enum.Enum):
    ADMIN = "admin"
    CONSULTOR = "consultor"


class Usuario(Base):
    """Login único (mesmo CNPJ), senha diferente por papel. O consultor vê a
    rede inteira — decisão confirmada, não há escopo por loja/consultor."""
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnpj_login: Mapped[str] = mapped_column(String(18), index=True)
    senha_hash: Mapped[str] = mapped_column(String(128))
    papel: Mapped[Papel] = mapped_column(Enum(Papel))
    nome_exibicao: Mapped[str] = mapped_column(String(120))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("cnpj_login", "papel", name="uq_login_papel"),)


# ---------------------------------------------------------------------------
# Lojas (sistema interno)
# ---------------------------------------------------------------------------

class Loja(Base):
    __tablename__ = "lojas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnpj: Mapped[str] = mapped_column(String(18), unique=True, index=True)
    razao_social: Mapped[str] = mapped_column(String(200))
    uf: Mapped[str] = mapped_column(String(2), index=True)
    cidade: Mapped[str] = mapped_column(String(120))

    atendente_comercial: Mapped[str | None] = mapped_column(String(120), nullable=True)
    consultor_farma: Mapped[str | None] = mapped_column(String(120), nullable=True)
    consultor_interno: Mapped[str | None] = mapped_column(String(120), nullable=True)
    grupo_economico: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)

    fonte: Mapped[str] = mapped_column(String(30), default="sistema_interno")
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    compras: Mapped[list["RegistroCompraGPS"]] = relationship(back_populates="loja")


# ---------------------------------------------------------------------------
# Base Genéricos — mapeamento EAN -> nome canônico (auto-crescente)
# ---------------------------------------------------------------------------

class BaseGenerico(Base):
    """Nome canônico do genérico — o alvo de todo o pipeline de
    reconciliação. Cresce com o uso do sistema (nunca é carregada de uma vez
    só e depois congelada)."""
    __tablename__ = "base_genericos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome_canonico: Mapped[str] = mapped_column(String(250), unique=True, index=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class OrigemResolucao(str, enum.Enum):
    AUTOMATICA = "automatica"
    MANUAL = "manual"
    IMPORTADA = "importada"  # importação em massa de planilha curada (Base Genéricos), sem fuzzy-match


class EanGenerico(Base):
    """Muitos EAN -> um genérico canônico. Fonte ÚNICA de junção EAN→
    genérico (ver nota no topo do arquivo)."""
    __tablename__ = "ean_genericos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ean: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    base_generico_id: Mapped[int] = mapped_column(ForeignKey("base_genericos.id"), index=True)
    origem_resolucao: Mapped[OrigemResolucao] = mapped_column(Enum(OrigemResolucao))
    score_similaridade: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    descricao_origem_snapshot: Mapped[str] = mapped_column(Text)
    resolvido_por: Mapped[str] = mapped_column(String(120))  # "sistema" (auto) ou nome do admin
    resolvido_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    base_generico: Mapped["BaseGenerico"] = relationship()


# ---------------------------------------------------------------------------
# Filas de pendência (EAN não resolvido, CNPJ órfão)
# ---------------------------------------------------------------------------

class OrigemFila(str, enum.Enum):
    GPS = "gps"
    GRUPPY = "gruppy"


class StatusFila(str, enum.Enum):
    PENDENTE = "pendente"
    RESOLVIDA = "resolvida"
    IGNORADA = "ignorada"


class FilaResolucaoEAN(Base):
    """Uma linha por EAN não resolvido — chave de dedup é o EAN (nunca uma
    linha por ocorrência: um EAN repetido em várias lojas/meses só incrementa
    valor_total_acumulado/qtd_ocorrencias na mesma linha)."""
    __tablename__ = "fila_resolucao_ean"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ean: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    descricao_observada: Mapped[str] = mapped_column(String(250))
    origem: Mapped[OrigemFila] = mapped_column(Enum(OrigemFila))
    sugestao_base_generico_id: Mapped[int | None] = mapped_column(ForeignKey("base_genericos.id"), nullable=True)
    sugestao_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    valor_total_acumulado: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    aparece_em_estoque: Mapped[bool] = mapped_column(Boolean, default=False)
    qtd_ocorrencias: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[StatusFila] = mapped_column(Enum(StatusFila), default=StatusFila.PENDENTE)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    sugestao: Mapped["BaseGenerico | None"] = relationship()


class FilaCnpjOrfao(Base):
    """CNPJ que aparece no GPS mas não bate com nenhuma Loja cadastrada.
    Nunca é descartado silenciosamente."""
    __tablename__ = "fila_cnpj_orfao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnpj: Mapped[str] = mapped_column(String(18), unique=True, index=True)
    razao_social_observada: Mapped[str | None] = mapped_column(String(200), nullable=True)
    valor_total_acumulado: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    qtd_ocorrencias: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[StatusFila] = mapped_column(Enum(StatusFila), default=StatusFila.PENDENTE)
    resolvido_para_loja_id: Mapped[int | None] = mapped_column(ForeignKey("lojas.id"), nullable=True)
    # JSON (lista de int) dos FSNode.id dos uploads GPS em que este CNPJ
    # apareceu — só nesses arquivos vale a pena reler na hora de resolver o
    # órfão (ver integrations/gps.py::_reprocessar_cnpjs), em vez de reler
    # TODOS os uploads GPS já enviados. Nulo em fila criada antes deste campo
    # existir -> fallback pro comportamento antigo (relê tudo), pois não tem
    # como saber em quais arquivos aquele CNPJ apareceu.
    uploads_fs_node_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )


# ---------------------------------------------------------------------------
# Tabelas de preço RMC (Gruppy) — vigência granular por UF
# ---------------------------------------------------------------------------

class ModoCustoGruppy(str, enum.Enum):
    PRONTO = "pronto"
    BRUTO_DESCONTO = "bruto_desconto"


class TabelaGruppy(Base):
    """Um upload = uma tabela de um laboratório/distribuidor."""
    __tablename__ = "tabelas_gruppy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    laboratorio: Mapped[str] = mapped_column(String(150), index=True)
    modo_custo: Mapped[ModoCustoGruppy] = mapped_column(Enum(ModoCustoGruppy))
    nome_arquivo_origem: Mapped[str] = mapped_column(String(255))
    upload_fs_node_id: Mapped[int | None] = mapped_column(ForeignKey("fs_nodes.id"), nullable=True)
    # campo -> nome_coluna EXATO usado neste upload (JSON, via
    # integrations/mapeamento.py::serializar_mapa) — não é o "último
    # mapeamento global" de UltimoMapeamentoColuna (aquele é só sugestão pra
    # a PRÓXIMA planilha); nulo em tabelas de antes deste campo existir.
    mapa_colunas_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[str] = mapped_column(String(120))
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    itens: Mapped[list["ItemTabelaGruppy"]] = relationship(back_populates="tabela")
    cobertura: Mapped[list["TabelaGruppyCobertura"]] = relationship(back_populates="tabela")


class StatusCobertura(str, enum.Enum):
    ATIVA = "ativa"
    INATIVA = "inativa"


class TabelaGruppyCobertura(Base):
    """UF coberta por uma TabelaGruppy, com status próprio por linha — é isso
    que implementa a vigência granular por UF (upload novo do mesmo
    laboratório só inativa as UFs sobrepostas, não a tabela inteira)."""
    __tablename__ = "tabelas_gruppy_cobertura"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tabela_gruppy_id: Mapped[int] = mapped_column(ForeignKey("tabelas_gruppy.id"), index=True)
    uf: Mapped[str] = mapped_column(String(2), index=True)
    status: Mapped[StatusCobertura] = mapped_column(Enum(StatusCobertura), default=StatusCobertura.ATIVA)
    inativada_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    inativada_por: Mapped[str | None] = mapped_column(String(120), nullable=True)

    tabela: Mapped["TabelaGruppy"] = relationship(back_populates="cobertura")


class ItemTabelaGruppy(Base):
    __tablename__ = "itens_tabela_gruppy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tabela_gruppy_id: Mapped[int] = mapped_column(ForeignKey("tabelas_gruppy.id"), index=True)
    ean: Mapped[str] = mapped_column(String(40), index=True)  # bruto — resolve via EanGenerico
    descricao_origem: Mapped[str] = mapped_column(String(250))
    custo_liquido: Mapped[float] = mapped_column(Numeric(14, 4))
    preco_bruto: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    percentual_desconto: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)  # fração 0-1 (0.84 = 84%), não 0-100 — ver integrations/gruppy.py
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    tabela: Mapped["TabelaGruppy"] = relationship(back_populates="itens")


# ---------------------------------------------------------------------------
# Compras GPS (inclui estoque — mesma linha/arquivo, não é upload separado)
# ---------------------------------------------------------------------------

class RegistroCompraGPS(Base):
    __tablename__ = "registros_compra_gps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    ean: Mapped[str] = mapped_column(String(40), index=True)  # bruto — resolve via EanGenerico
    descricao_origem: Mapped[str] = mapped_column(String(250))
    laboratorio_compra: Mapped[str | None] = mapped_column(String(150), nullable=True)  # display-only, pode ser vazio

    ano_mes: Mapped[str] = mapped_column(String(7), index=True)  # "2026-07", vem do popup de upload

    quantidade: Mapped[float] = mapped_column(Numeric(14, 3))
    fat_liquido: Mapped[float] = mapped_column(Numeric(14, 4))
    pct_cmv: Mapped[float] = mapped_column(Numeric(7, 4))
    custo_unitario: Mapped[float] = mapped_column(Numeric(14, 4))  # (fat_liquido*pct_cmv)/quantidade, calculado 1x na importação
    estoque: Mapped[float] = mapped_column(Numeric(14, 3))  # QtdEstoque da mesma linha do arquivo GPS

    upload_fs_node_id: Mapped[int | None] = mapped_column(ForeignKey("fs_nodes.id"), nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    loja: Mapped["Loja"] = relationship(back_populates="compras")

    __table_args__ = (UniqueConstraint("loja_id", "ean", "ano_mes", name="uq_compra_loja_ean_mes"),)


class UltimoMapeamentoColuna(Base):
    """Último mapeamento campo -> coluna confirmado pelo admin no popup de
    upload, por fornecedor (GPS/Gruppy) e campo. Usado só como sugestão
    pré-preenchida na próxima planilha daquele fornecedor (prioridade sobre a
    heurística de sinônimo) — não é o mapeamento exato usado em cada upload
    já processado (isso fica para uma etapa futura, de guardar o mapeamento
    por upload para reaproveitar no reprocessamento de CNPJ órfão)."""
    __tablename__ = "ultimos_mapeamentos_coluna"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fornecedor: Mapped[OrigemFila] = mapped_column(Enum(OrigemFila), index=True)
    campo: Mapped[str] = mapped_column(String(40))
    nome_coluna: Mapped[str] = mapped_column(String(255))
    atualizado_por: Mapped[str] = mapped_column(String(120))
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    __table_args__ = (UniqueConstraint("fornecedor", "campo", name="uq_ultimo_mapeamento_fornecedor_campo"),)


class UploadGPS(Base):
    """Metadado de CADA upload de planilha GPS (1 linha por arquivo enviado),
    guardando o ano_mes escolhido pelo admin no popup — independente de
    quantas linhas daquele arquivo viraram RegistroCompraGPS.

    Existe especificamente pro reprocessamento automático de CNPJ órfão
    (ver integrations/gps.py::resolver_cnpj_orfao): sem isso, se um arquivo
    inteiro fosse só de um CNPJ ainda não cadastrado (nenhuma linha própria
    importada), não haveria como descobrir depois o ano_mes daquele arquivo
    pra reprocessar as linhas que ficaram de fora."""
    __tablename__ = "uploads_gps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fs_node_id: Mapped[int] = mapped_column(ForeignKey("fs_nodes.id"), unique=True, index=True)
    ano_mes: Mapped[str] = mapped_column(String(7))
    # campo -> nome_coluna EXATO usado neste upload (JSON, via
    # integrations/mapeamento.py::serializar_mapa) — não é o "último
    # mapeamento global" de UltimoMapeamentoColuna (aquele é só sugestão pra
    # a PRÓXIMA planilha). Usado pelo reprocessamento de CNPJ órfão pra reler
    # este arquivo com o MESMO mapeamento da vez que foi processado, em vez
    # de recalcular a heurística de novo (que pode já ter mudado); nulo em
    # uploads de antes deste campo existir -> ver _reprocessar_cnpjs.
    mapa_colunas_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    criado_por: Mapped[str] = mapped_column(String(120))
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# Monitoramento de infraestrutura
# ---------------------------------------------------------------------------

class VerificacaoIpsStreamlitCloud(Base):
    """Um registro por checagem feita (ver core/monitoramento.py) se a lista
    de IPs de saída do Streamlit Community Cloud publicada oficialmente
    ainda bate com `settings.streamlit_cloud.ips_conhecidos` (a lista usada
    pra liberar o firewall do servidor de persistência). Guardado no banco
    — não em st.session_state — pra sobreviver a reinícios do processo
    Streamlit e pra não precisar bater na rede a cada rerun da página."""
    __tablename__ = "verificacoes_ips_streamlit_cloud"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    verificado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    ips_publicados_snapshot: Mapped[str] = mapped_column(Text)  # JSON, só para auditoria
    divergente: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Sistema de arquivos virtual (sobre DigitalOcean Spaces)
# ---------------------------------------------------------------------------

class TipoNode(str, enum.Enum):
    PASTA = "pasta"
    ARQUIVO = "arquivo"


class StatusNode(str, enum.Enum):
    ATIVO = "ativo"
    INATIVO = "inativo"


class FSNode(Base):
    """Um nó do 'Explorador de Arquivos' (pasta ou arquivo). O conteúdo real
    do arquivo fica no DigitalOcean Spaces (ou em disco local, em modo dev);
    aqui só ficam os metadados que dão a experiência de pastas do Windows.

    Inativar NUNCA apaga: move o nó para dentro da pasta especial de inativos
    (ver storage/filesystem.py) e marca status=INATIVO. Reativar devolve para
    o local original."""
    __tablename__ = "fs_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("fs_nodes.id"), nullable=True, index=True)
    nome: Mapped[str] = mapped_column(String(255))
    tipo: Mapped[TipoNode] = mapped_column(Enum(TipoNode))
    status: Mapped[StatusNode] = mapped_column(Enum(StatusNode), default=StatusNode.ATIVO)

    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)  # só para arquivo
    tamanho_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)

    origem_parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # onde estava antes de inativar
    criado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    filhos: Mapped[list["FSNode"]] = relationship()
