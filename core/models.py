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
  fornecedor) num determinado mês — quantidade e custo unitário sem ST.
  CompraGPSOrfa guarda as mesmas compras enquanto o CNPJ não bate com loja.
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
    Index,
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

    # Estrutura dos níveis de acesso (27/09/2026), pronta antes do login
    # individual: `nivel` = ADM | CONSULTOR | COMPRADOR | PROPRIETARIO (texto,
    # não enum do Postgres — acrescentar valor a enum com o app antigo no
    # mesmo banco é arriscado). Vazio = deduzido do `papel` (core/acesso.py).
    # `login` será o usuário do login individual (e-mail ou CPF).
    nivel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    login: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lojas: Mapped[list["UsuarioLoja"]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("cnpj_login", "papel", name="uq_login_papel"),)


class UsuarioLoja(Base):
    """Lojas de um Comprador/Proprietário — ele só vê o Pedido delas."""
    __tablename__ = "usuarios_lojas"

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), primary_key=True)


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

    # Vindos do sistema interno desde 27/09/2026, para o vínculo com a loja do
    # GPS (pedido/vinculo.py): nas lojas que o GPS manda sem CNPJ, o número do
    # endereço é o que casa (MEGA FARMA: 6 de 6). `legacy_id` é o código que
    # a equipe usa no dia a dia (ex.: 787–790 = Drogarias Reis).
    legacy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    endereco_numero: Mapped[str | None] = mapped_column(String(20), nullable=True)
    bairro: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cep: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nome_fantasia: Mapped[str | None] = mapped_column(String(200), nullable=True)

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
    # Genérico que a loja vendeu (API do GPS, rotina do Pedido) e que não
    # está na Base Genéricos — entra pela fila, sempre pra confirmar
    # (integrations/fila_loja.py; decisão de 27/09/2026, Q23).
    LOJA_API = "loja_api"


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
# Compras GPS
# ---------------------------------------------------------------------------

class RegistroCompraGPS(Base):
    """Uma linha por (loja, EAN, mês). Regra de custo desde 09/2026: o
    `custo_unitario` é o `VlrUnitario` da planilha (preço de compra SEM ST,
    comparável à tabela Gruppy, que também não tem imposto) e `quantidade` é a
    `Quantidade` comprada no mês.

    Campos de RECUO de preço (opcionais, preenchidos quando a planilha traz):
    `fat_liquido`, `pct_cmv` e `qtd_vendida` (QTD, quantidade VENDIDA) geram
    `custo_cmv_unitario` = Fat × %CMV ÷ QTD, já calculado no upload; e
    `custo_medio_planilha` é o "R$ Custo médio". A análise (core/analise.py)
    só usa esses valores quando o VlrUnitario destoa mais de ±50% do preço do
    laboratório escolhido — a regra depende do laboratório, por isso fica na
    consulta, e não no upload.

    `estoque` é da regra antiga: fica no banco, opcional e sem uso. Não foi
    removido porque o app publicado (código antigo) lê o mesmo banco."""
    __tablename__ = "registros_compra_gps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    ean: Mapped[str] = mapped_column(String(40), index=True)  # bruto — resolve via EanGenerico
    descricao_origem: Mapped[str] = mapped_column(String(250))
    laboratorio_compra: Mapped[str | None] = mapped_column(String(150), nullable=True)  # display-only, pode ser vazio

    ano_mes: Mapped[str] = mapped_column(String(7), index=True)  # "2026-07", vem do popup de upload

    quantidade: Mapped[float] = mapped_column(Numeric(14, 3))
    custo_unitario: Mapped[float] = mapped_column(Numeric(14, 4))  # VlrUnitario da planilha (sem ST)
    fat_liquido: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)  # Fat. líquido (recuo)
    pct_cmv: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)  # % CMV em fração (recuo)
    qtd_vendida: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)  # QTD vendida (recuo)
    custo_cmv_unitario: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)  # Fat×%CMV÷QTD
    custo_medio_planilha: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)  # R$ Custo médio
    estoque: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)  # regra antiga, sem uso

    upload_fs_node_id: Mapped[int | None] = mapped_column(ForeignKey("fs_nodes.id"), nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    loja: Mapped["Loja"] = relationship(back_populates="compras")

    __table_args__ = (UniqueConstraint("loja_id", "ean", "ano_mes", name="uq_compra_loja_ean_mes"),)


class CompraGPSOrfa(Base):
    """Compra do GPS cujo CNPJ ainda não bate com nenhuma Loja cadastrada —
    guardada com o CNPJ (só dígitos) no lugar do `loja_id`, e com a MESMA
    regra de substituição de `RegistroCompraGPS`: reenviar o mesmo CNPJ no
    mesmo mês troca as linhas dele.

    É a fonte de verdade da fila de CNPJ órfão (`FilaCnpjOrfao` é recalculada
    a partir daqui, nunca somada envio a envio) e o que é movido para
    `RegistroCompraGPS` quando alguém vincula o CNPJ a uma loja — sem reabrir
    nenhum .xlsx."""
    __tablename__ = "compras_gps_orfas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cnpj: Mapped[str] = mapped_column(String(18))
    razao_social: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ean: Mapped[str] = mapped_column(String(40))
    descricao_origem: Mapped[str] = mapped_column(String(250))
    laboratorio_compra: Mapped[str | None] = mapped_column(String(150), nullable=True)
    ano_mes: Mapped[str] = mapped_column(String(7))
    quantidade: Mapped[float] = mapped_column(Numeric(14, 3))
    custo_unitario: Mapped[float] = mapped_column(Numeric(14, 4))
    # Mesmos campos de recuo de RegistroCompraGPS — viajam junto quando a
    # compra é movida pra loja vinculada.
    fat_liquido: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    pct_cmv: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)
    qtd_vendida: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)
    custo_cmv_unitario: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    custo_medio_planilha: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    upload_fs_node_id: Mapped[int | None] = mapped_column(ForeignKey("fs_nodes.id"), nullable=True, index=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("cnpj", "ean", "ano_mes", name="uq_compra_orfa_cnpj_ean_mes"),
        Index("ix_compras_gps_orfas_ano_mes_cnpj", "ano_mes", "cnpj"),
        Index("ix_compras_gps_orfas_ean", "ean"),
    )


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
    # Chave, no mesmo storage do .xlsx, do ATALHO com as linhas deste upload
    # que caíram em CNPJ órfão — ver integrations/gps_cache_orfaos.py. NULL
    # significa "não tem atalho, relê o Excel" (upload antigo, upload sem
    # nenhuma linha órfã, ou gravação do atalho que falhou), nunca "erro".
    storage_key_orfaos: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
# Controle de rotinas automáticas
# ---------------------------------------------------------------------------

class ControleRotina(Base):
    """Uma linha por rotina automática do sistema (sincronização de lojas,
    reprocessamento da fila de EAN, sincronização de compras do GPS...).

    Existe pra responder uma pergunta só, mas que nenhum outro lugar do
    sistema sabia responder: "isso já rodou hoje?". Sem esse registro não há
    como disparar nada por tempo — todo processo automático viraria de novo
    um botão que alguém precisa lembrar de clicar.

    Fica no BANCO, não em `st.session_state` nem em arquivo: o Streamlit
    reexecuta o script inteiro a cada interação e reinicia o processo de vez
    em quando, então qualquer estado em memória se perderia; e com mais de um
    usuário usando o app ao mesmo tempo, cada um teria a sua própria ideia de
    "já rodou", que é exatamente o que causa rodar duas vezes em paralelo.

    Três carimbos separados de propósito:
    - `ultima_tentativa_em` é o que IMPEDE duas execuções simultâneas (ver
      `core/rotinas.py::reivindicar`): quem consegue gravá-lo ganha o direito
      de rodar, e quem chega junto vê que já foi reivindicado há pouco.
    - `ultimo_sucesso_em` é o que decide se precisa rodar de novo hoje. Uma
      falha NUNCA o altera — senão um erro transitório da API viraria "já
      rodou hoje" e a rotina só voltaria a tentar no dia seguinte.
    - `ultimo_erro` guarda a última falha pra tela poder mostrar o que houve
      sem inventar texto; é limpo no próximo sucesso.
    """
    __tablename__ = "controle_rotinas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    ultima_tentativa_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ultimo_sucesso_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    ultima_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )


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


# ---------------------------------------------------------------------------
# Pedido: vínculo loja do GPS ↔ loja do RMC
# ---------------------------------------------------------------------------

class SituacaoVinculoGps(str, enum.Enum):
    AUTOMATICO = "automatico"      # CNPJ igual, ou número do endereço + nome concordando
    CONFIRMAR = "confirmar"        # há sugestão, falta uma pessoa confirmar
    CONFIRMADO = "confirmado"      # uma pessoa confirmou (ou trocou) a loja
    NAO_CLIENTE = "nao_cliente"    # uma pessoa disse que não é loja do RMC
    SEM_CANDIDATO = "sem_candidato"


class VinculoLojaGps(Base):
    """Qual loja do GPS (idEmpresa, CodigoLoja) é qual loja do RMC.

    Recalculado a partir das lojas que a rotina do Pedido grava no Spaces
    (`pedido/controle/lojas_gps/`), MAS decisão humana (CONFIRMADO,
    NAO_CLIENTE) nunca é sobrescrita pelo recálculo — igual EanGenerico com
    resolução manual. Regras da sugestão: pedido/vinculo.py."""
    __tablename__ = "vinculos_loja_gps"
    __table_args__ = (UniqueConstraint("id_empresa_gps", "codigo_loja_gps", name="uq_vinculo_loja_gps"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_empresa_gps: Mapped[str] = mapped_column(String(40))
    codigo_loja_gps: Mapped[str] = mapped_column(String(40))
    nome_loja_gps: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cnpj_gps: Mapped[str | None] = mapped_column(String(18), nullable=True)
    numero_gps: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cidade_gps: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uf_gps: Mapped[str | None] = mapped_column(String(2), nullable=True)

    loja_id: Mapped[int | None] = mapped_column(ForeignKey("lojas.id"), nullable=True, index=True)
    situacao: Mapped[SituacaoVinculoGps] = mapped_column(Enum(SituacaoVinculoGps))
    metodo: Mapped[str | None] = mapped_column(String(20), nullable=True)   # cnpj | endereco | nome
    pontuacao: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    motivo: Mapped[str | None] = mapped_column(String(200), nullable=True)

    decidido_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decidido_em: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

    loja: Mapped["Loja | None"] = relationship()


# ---------------------------------------------------------------------------
# Base de categorias (Pedido): EAN → categoria
# ---------------------------------------------------------------------------

class OrigemCategoria(str, enum.Enum):
    FEBRAFAR = "febrafar"
    CMED = "cmed"
    MANUAL = "manual"


class CategoriaEan(Base):
    """Categoria de cada EAN — decide quantos dias de estoque o Pedido sugere
    (medicamento 7, perfumaria 15) e se o produto entra na sugestão. Regras e
    nomes das categorias: pedido/categorias.py.

    Uma tabela só, que CRESCE com o uso: nasce com FEBRAFAR + CMED (carga
    inicial, arquivo no Spaces) e recebe as planilhas do admin para os
    produtos "Sem Classificação". A categoria MANUAL nunca é sobrescrita por
    uma nova carga FEBRAFAR/CMED — mesma ideia do EanGenerico resolvido à mão.

    `ean` é a CHAVE normalizada (só dígitos, sem zeros à esquerda): o mesmo
    produto chega como EAN-13 no GPS e como GTIN-14 com zero na frente em
    algumas listas (ver `categorias.chave_ean`)."""
    __tablename__ = "categorias_ean"

    ean: Mapped[str] = mapped_column(String(20), primary_key=True)
    categoria: Mapped[str] = mapped_column(String(60))
    origem: Mapped[OrigemCategoria] = mapped_column(Enum(OrigemCategoria))
    descricao: Mapped[str | None] = mapped_column(String(200), nullable=True)
    atualizado_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# Configurações de Pedidos
# ---------------------------------------------------------------------------

class ConfiguracaoPedido(Base):
    """Os parâmetros da sugestão de pedido (pedido/configuracao.py), em JSON.
    Cada gravação é uma LINHA NOVA — a vigente é a de maior id — pra ficar
    o histórico de quem mudou o quê e quando: um "dias de estoque" alterado
    muda o pedido de todas as lojas."""
    __tablename__ = "configuracoes_pedido"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    valores: Mapped[str] = mapped_column(Text)
    criado_por: Mapped[str] = mapped_column(String(120))
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)



# ---------------------------------------------------------------------------
# Pedido: rascunho, correção de estoque negativo, exportações
# ---------------------------------------------------------------------------
# `linha` = a linha do pedido (pedido/calculo.py): "G<id>" para um genérico
# da Base Genéricos, "P<código>" para um produto do cadastro da loja.

class RascunhoPedido(Base):
    """SEM USO desde 29/09/2026: o "rascunho da loja" da Fase 5 virou a área
    de trabalho por usuário + loja (`AreaPedido`). O modelo fica porque a
    tabela existe (migração 0009) e o teste de migrações compara as duas."""
    __tablename__ = "rascunhos_pedido"
    __table_args__ = (UniqueConstraint("loja_id", "linha", name="uq_rascunho_loja_linha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    linha: Mapped[str] = mapped_column(String(40))
    quantidade: Mapped[int] = mapped_column(Integer)
    sugestao_calculada: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alterado_por: Mapped[str] = mapped_column(String(120))
    alterado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class CorrecaoEstoque(Base):
    """Estoque correto de um item que o GPS mostra NEGATIVO. Vale só no
    nosso sistema (não volta pro GPS) e só enquanto a foto do GPS continuar
    negativa: quando uma foto nova vier não negativa, ela é ignorada."""
    __tablename__ = "correcoes_estoque"
    __table_args__ = (UniqueConstraint("loja_id", "linha", name="uq_correcao_loja_linha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    linha: Mapped[str] = mapped_column(String(40))
    estoque_corrigido: Mapped[float] = mapped_column(Numeric(12, 3))
    estoque_gps: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    data_foto: Mapped[str | None] = mapped_column(String(10), nullable=True)
    corrigido_por: Mapped[str] = mapped_column(String(120))
    corrigido_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class ExportacaoPedido(Base):
    """Registro de cada exportação (quem, quando, o quê) — não guarda o
    arquivo."""
    __tablename__ = "exportacoes_pedido"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    formato: Mapped[str] = mapped_column(String(10))
    itens: Mapped[int] = mapped_column(Integer)
    unidades: Mapped[int] = mapped_column(Integer)
    valor: Mapped[float] = mapped_column(Numeric(14, 2))
    exportado_por: Mapped[str] = mapped_column(String(120))
    exportado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


# ---------------------------------------------------------------------------
# Pedido: área de trabalho (usuário + loja) e listas salvas (29/09/2026)
# ---------------------------------------------------------------------------
# Substituem o "rascunho da loja" da Fase 5 (`rascunhos_pedido`, que fica no
# banco sem uso — nada se apaga enquanto o app antigo está publicado).

class AreaPedido(Base):
    """O que o usuário mudou no pedido de uma loja, gravado a cada clique —
    recarregar a página ou voltar outro dia traz tudo igual. Só guarda o que
    difere do padrão: `quantidade` None = segue a sugestão; `selecionado`
    None = marcado se a quantidade > 0."""
    __tablename__ = "pedido_area"
    __table_args__ = (UniqueConstraint("usuario_id", "loja_id", "linha", name="uq_pedido_area"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer, index=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    linha: Mapped[str] = mapped_column(String(40))
    quantidade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selecionado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class AreaPedidoEstado(Base):
    """Por usuário + loja: a última gravação (o "salvo às hh:mm") e a lista
    aberta — exportar apaga a lista que estava aberta."""
    __tablename__ = "pedido_area_estado"
    __table_args__ = (UniqueConstraint("usuario_id", "loja_id", name="uq_pedido_area_estado"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer, index=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"))
    lista_aberta_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class ListaPedido(Base):
    """Pedido salvo com nome ("Salvar como lista"), visível pra TODOS — é o
    que evita duas pessoas fazerem o mesmo pedido. Fica até alguém exportar
    a partir dela ou excluir."""
    __tablename__ = "pedido_listas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    nome: Mapped[str] = mapped_column(String(120))
    criado_por: Mapped[str] = mapped_column(String(120))
    criado_por_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    itens: Mapped[int] = mapped_column(Integer, default=0)
    unidades: Mapped[int] = mapped_column(Integer, default=0)
    valor: Mapped[float] = mapped_column(Numeric(14, 2), default=0)


class ListaPedidoItem(Base):
    __tablename__ = "pedido_listas_itens"

    lista_id: Mapped[int] = mapped_column(ForeignKey("pedido_listas.id"), primary_key=True)
    linha: Mapped[str] = mapped_column(String(40), primary_key=True)
    quantidade: Mapped[int] = mapped_column(Integer)
    selecionado: Mapped[bool] = mapped_column(Boolean, default=True)


class AvisoPedidoVisto(Base):
    """Os pop-ups de alerta ao abrir a loja no Assistente de pedido
    (01/10/2026): "estoque negativo" e "sem classificação" aparecem UMA vez
    por foto do GPS, por usuário + loja — perguntar a mesma coisa a cada
    abertura vira clique automático. `data_foto` = a foto respondida; foto
    nova, pergunta de novo. Tabela própria (e não `pedido_area_estado`) pra
    não mexer no "Salvo automaticamente · hh:mm"."""
    __tablename__ = "pedido_avisos_vistos"
    __table_args__ = (UniqueConstraint("usuario_id", "loja_id", "tipo", name="uq_pedido_avisos_vistos"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer, index=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"))
    tipo: Mapped[str] = mapped_column(String(30))
    data_foto: Mapped[str] = mapped_column(String(10))
    resposta: Mapped[str | None] = mapped_column(String(30), nullable=True)
    visto_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class PersonalizacaoPedidoLoja(Base):
    """Personalização dos parâmetros do Pedido para UMA loja (01/10/2026):
    só os campos que diferem do padrão (`valores`, JSON de
    pedido/configuracao.diferencas); o resto segue o padrão, inclusive quando
    o padrão muda depois. Como em `configuracoes_pedido`, cada gravação é uma
    LINHA NOVA (histórico de quem e quando); a vigente da loja é a de maior
    id, e `{}` = personalização removida (a loja volta ao padrão)."""
    __tablename__ = "personalizacoes_pedido_loja"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    valores: Mapped[str] = mapped_column(Text)
    criado_por: Mapped[str] = mapped_column(String(120))
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class FiltroSalvoPedido(Base):
    """"Meus filtros" do pop-up do Pedido: pessoais (usuário) e de cada
    loja, no máximo 8 por usuário + loja. SEM USO desde 01/10/2026: o pop-up
    de filtros saiu (Assistente de pedido) e o módulo foi apagado. O modelo
    fica porque a tabela existe (migração 0012) e o teste de migrações
    compara as duas.
    `filtros` = JSON de pedido/calculo.Filtros.para_dict()."""
    __tablename__ = "pedido_filtro_salvo"
    __table_args__ = (UniqueConstraint("usuario_id", "loja_id", "nome", name="uq_pedido_filtro_salvo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer, index=True)
    loja_id: Mapped[int] = mapped_column(ForeignKey("lojas.id"), index=True)
    nome: Mapped[str] = mapped_column(String(60))
    filtros: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    atualizado_em: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
