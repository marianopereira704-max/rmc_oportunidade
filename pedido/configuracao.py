"""Configurações de Pedidos — os números da sugestão que o admin muda na tela
(views/config_pedido.py). Sem banco aqui: validação e conversão; a gravação
fica em integrations/configuracao_pedido.py.

Padrões: resumo final da aba Pedido (27/09/2026) — medicamento 7 dias,
perfumaria 15, ruptura 3, giro baixo ≤ 1 un. em 90 dias, curva 50/40/10,
janela de 3 meses fechados, tolerância de preço ±50%. Vêm de
`settings.pedido` (dá pra mudar o padrão por variável de ambiente); o que o
admin gravar passa por cima.

A janela de meses é a única que vale também FORA do app: a rotina da
madrugada (GitHub Actions, sem acesso ao banco) precisa dela pra saber
quantos meses baixar. Por isso, ao gravar, ela também vai pro Spaces
(`pedido/controle/configuracao.json`). Aumentar a janela vale a partir da
coleta seguinte (os meses a mais precisam ser baixados); diminuir vale na
hora.

Personalização por loja (01/10/2026): o padrão vale para todas as lojas; uma
loja pode ter os SEUS valores em alguns campos (`diferencas`), e o resto
continua seguindo o padrão — inclusive quando o padrão muda depois. A janela
de meses fica de fora: é da rotina, que baixa o mesmo período de todas.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from pedido import calculo
from pedido import categorias as cat
from pedido import classificacao, linhas

MESES_MAXIMO = 6  # cada mês a mais é ~1 consulta por loja na API do GPS


@dataclass
class ConfigPedido:
    dias_medicamento: int = 7
    dias_perfumaria: int = 15
    # Categoria com dias próprios (ex.: {"INFANTIL": 20}); ausente = dias do grupo.
    dias_por_categoria: dict[str, int] = field(default_factory=dict)
    piso_maximo: int = 1
    dias_ruptura: int = 3
    giro_baixo_dias: int = 90
    giro_baixo_max_unidades: float = 1
    curva_a: float = 0.50
    curva_b: float = 0.40
    meses_fechados: int = 3
    # Novo layout do Pedido (29/09/2026):
    ruptura_unidades: float = 0             # Ruptura = estoque até N unidades (0 = zerado)
    dias_sem_classificacao: int = 7         # dias estimados pra produto sem categoria
    fator_preco_fora: float = 3.0           # "a revisar": > N× ou < 1/N da mediana das compras da loja
    # Cadastro × nota (01/10/2026): custo coerente = entre min e max × preço de venda.
    custo_faixa_min: float = 0.15
    custo_faixa_max: float = 1.0
    fator_cadastro_nota: float = 2.0        # divergem mais que isso → a faixa decide
    # Giro baixo de curva A/B vira "Venda pontual", desmarcado (05/10/2026).
    venda_pontual: bool = True
    # O que cada linha de compra do Comprador inclui: SETOR_NEC do catálogo
    # IQVIA → linha (07/10/2026, Q35; personalizável por loja, Q23 A).
    linhas_por_setor: dict[str, str] = field(default_factory=lambda: dict(linhas.PADRAO_POR_SETOR))
    # O mesmo para o TIPO da CMED (25/10/2026, cascata de classificação): só Genérico e Similar classificam; um
    # tipo que não está aqui, ou que o admin manda para "Em classificação", não classifica o produto. As duas
    # correspondências valem para TODAS as lojas: a classificação é do produto, não da loja (D8 do design).
    linhas_por_cmed: dict[str, str] = field(default_factory=lambda: dict(classificacao.PADRAO_CMED_TELA))
    # Dashboard (21/10/2026; personalizáveis por loja como os demais):
    # estoque em EXCESSO = as unidades que passam destes dias de venda. É uma
    # régua do Dashboard e NÃO tem relação com o "ideal" do Assistente (meta de
    # compra, 7 a 15 dias): a primeira versão usava o ideal e marcava 60% do
    # estoque da loja 60 como excesso.
    dias_cobertura_excesso: int = 60
    # "Comprou mais barato": a melhor compra da rede só vale se a quantidade
    # dela for ao menos esta fração da quantidade que a loja comprou (evita
    # comparar uma unidade avulsa com um lote), e só se ao menos N lojas
    # diferentes compraram o produto na UF (com menos, a loja que pagou menos
    # seria identificável por exclusão).
    quantidade_comparavel: float = 0.5
    minimo_lojas_comparacao: int = 2
    # Página Estoque (22/10/2026): SKU com estoque e NENHUMA venda nestes dias = parado. Também é a janela
    # da coluna "Vendidas em N dias" e do ritmo de venda da cobertura e do excesso. Sem teto de 90 no
    # código: a rotina guarda cerca de 3 meses de vendas hoje, e a página avisa quando o pedido passa
    # do que existe (a retenção pode crescer depois).
    dias_parado: int = 90
    # Página Ruptura (27/10/2026, design docs/specs/2026-10-26-dashboard-ruptura-design.md, R13): o período da perda
    # (R8), os limites do risco pela frequência de venda (R2: % dos últimos 90 dias com venda) e os do nível de
    # serviço (R6: vermelho abaixo de, verde a partir de). Personalizáveis por loja, como os demais do Dashboard.
    ruptura_perda_dias: int = 30
    # Risco (Q15 de 27/10/2026): alto = vende pelo menos 1 vez por semana (1/7 ≈ 14% dos dias), médio = pelo menos
    # 1 vez por mês (≈ 3%). Os 50%/20% da 1ª versão deixavam TODOS os 901 produtos em ruptura da loja 60 em
    # "baixo" — o produto típico de lá vende em 2% dos dias.
    ruptura_risco_alto: float = 0.14
    ruptura_risco_medio: float = 0.03
    ruptura_sla_vermelho: float = 0.65
    ruptura_sla_verde: float = 0.75
    # Página Compras (27/10/2026, design docs/specs/2026-10-27-dashboard-compras-design.md, R13): quando um dia fica
    # fora da conta da variação da dotação — a loja inteira vendeu menos que esta parte da mediana do mesmo dia da
    # semana nestas semanas anteriores (dado incompleto ou feriado; 28/10/2026, no lugar do "dia de início do
    # ajuste": a dotação passou a acompanhar a venda por semana completa) — e os dias sem venda antes da compra para
    # ela ser "sem demanda" (R6). Personalizáveis por loja.
    compras_dia_fora_pct: float = 0.40
    compras_dia_fora_semanas: int = 8
    compras_sem_demanda_dias: int = 90

    @classmethod
    def padrao(cls) -> "ConfigPedido":
        from core.config import settings

        c = settings.pedido
        return cls(
            dias_medicamento=c.dias_medicamento, dias_perfumaria=c.dias_perfumaria, dias_ruptura=c.dias_ruptura,
            giro_baixo_dias=c.giro_baixo_dias, giro_baixo_max_unidades=c.giro_baixo_max_unidades,
            curva_a=c.curva_a, curva_b=c.curva_b, meses_fechados=c.meses_fechados,
        )

    @classmethod
    def de_dict(cls, dados: dict | None, base: "ConfigPedido | None" = None) -> "ConfigPedido":
        """Campos que faltam (config gravada antes de um campo novo existir)
        ficam com o valor de `base` (o padrão); campos desconhecidos são
        ignorados."""
        base = base or cls.padrao()
        valores = asdict(base)
        nomes = {f.name for f in fields(cls)}
        valores.update({k: v for k, v in (dados or {}).items() if k in nomes})
        return cls(**valores)

    def para_dict(self) -> dict:
        return asdict(self)

    def erros(self) -> list[str]:
        """Tudo que impede gravar, em frase pra tela."""
        e = []
        for nome, valor, minimo, maximo in (
            ("Dias de estoque — medicamento", self.dias_medicamento, 1, 180),
            ("Dias de estoque — perfumaria", self.dias_perfumaria, 1, 180),
            ("Piso do estoque ideal", self.piso_maximo, 0, 1000),
            ("Dias da ruptura próxima", self.dias_ruptura, 0, 60),
            ("Ruptura (unidades)", self.ruptura_unidades, 0, 1000),
            ("Dias para Sem Classificação", self.dias_sem_classificacao, 1, 180),
            ("Dias do giro baixo", self.giro_baixo_dias, 7, 365),
            ("Unidades do giro baixo", self.giro_baixo_max_unidades, 0, 1000),
            ("Meses fechados da janela", self.meses_fechados, 1, MESES_MAXIMO),
            ("Cobertura do estoque em excesso (dias)", self.dias_cobertura_excesso, 7, 365),
            ("Quantidade comparável (fração)", self.quantidade_comparavel, 0.1, 1),
            # Mínimo 2: com 1 loja só, a comparação identificaria a loja.
            ("Lojas mínimas na comparação de preço", self.minimo_lojas_comparacao, 2, 20),
            ("Dias sem venda para ser parado", self.dias_parado, 7, 365),
            ("Período da perda por ruptura (dias)", self.ruptura_perda_dias, 7, 365),
            ("Dia fora da conta da dotação (% da mediana)", round(self.compras_dia_fora_pct * 100), 5, 90),
            ("Semanas de referência do dia fora da conta", self.compras_dia_fora_semanas, 3, 26),
            ("Dias sem venda para a compra ser sem demanda", self.compras_sem_demanda_dias, 7, 365),
        ):
            if not (minimo <= valor <= maximo):
                e.append(f"{nome}: entre {minimo} e {maximo} (está {valor}).")
        if not (0 < self.ruptura_risco_medio < self.ruptura_risco_alto <= 1):
            e.append("Risco da ruptura: o médio precisa ser maior que 0% e menor que o alto (até 100%).")
        if not (0 < self.ruptura_sla_vermelho < self.ruptura_sla_verde <= 1):
            e.append("Nível de serviço: o vermelho precisa ser maior que 0% e menor que o verde (até 100%).")
        for categoria, dias in self.dias_por_categoria.items():
            if categoria not in cat.CATEGORIAS:
                e.append(f"Categoria desconhecida: {categoria}.")
            elif not (1 <= dias <= 180):
                e.append(f"Dias de {categoria}: entre 1 e 180 (está {dias}).")
        for setor, linha in self.linhas_por_setor.items():
            if setor not in linhas.SETORES:
                e.append(f"Setor do catálogo desconhecido: {setor}.")
            elif linha not in linhas.ROTULOS:
                e.append(f"Linha de compra desconhecida para {linhas.SETORES[setor]}: {linha}.")
        for tipo, linha in self.linhas_por_cmed.items():
            if not str(tipo).strip():
                e.append("Tipo da CMED sem nome na correspondência de linhas.")
            elif linha not in linhas.ROTULOS:
                e.append(f"Linha de compra desconhecida para o tipo {tipo} da CMED: {linha}.")
        if not (0 < self.curva_a < 1 and 0 < self.curva_b < 1 and self.curva_a + self.curva_b < 1):
            e.append("Curva ABC: A e B maiores que 0 e somando menos de 100% (o resto é C).")
        if not (1.5 <= self.fator_preco_fora <= 20):
            e.append("Compra fora do padrão: entre 1,5 e 20 vezes a mediana.")
        if not (0 <= self.custo_faixa_min < self.custo_faixa_max <= 5):
            e.append("Custo coerente: o mínimo precisa ser menor que o máximo (em % do preço de venda, até 500%).")
        if not (1.2 <= self.fator_cadastro_nota <= 20):
            e.append("Cadastro × nota: entre 1,2 e 20 vezes.")
        if self.dias_ruptura >= min(self.dias_medicamento, self.dias_perfumaria):
            e.append("Os dias da ruptura próxima precisam ser menores que os dias de estoque dos dois grupos "
                     "(senão todo item abaixo do máximo vira ruptura próxima).")
        return e

    def parametros(self, limite_bonificacao: float) -> calculo.Parametros:
        return calculo.Parametros(
            dias_medicamento=self.dias_medicamento, dias_perfumaria=self.dias_perfumaria,
            dias_ruptura=self.dias_ruptura, giro_baixo_dias=self.giro_baixo_dias,
            giro_baixo_max_unidades=self.giro_baixo_max_unidades, curva_a=self.curva_a, curva_b=self.curva_b,
            limite_bonificacao=limite_bonificacao,
            dias_por_categoria=tuple(sorted(self.dias_por_categoria.items())), piso_maximo=self.piso_maximo,
            ruptura_unidades=self.ruptura_unidades, dias_sem_classificacao=self.dias_sem_classificacao,
            fator_preco_fora=self.fator_preco_fora, custo_faixa_min=self.custo_faixa_min,
            custo_faixa_max=self.custo_faixa_max, fator_cadastro_nota=self.fator_cadastro_nota,
            venda_pontual=bool(self.venda_pontual),
        )

    def dias_da_categoria(self, categoria: str) -> int:
        if categoria in self.dias_por_categoria:
            return self.dias_por_categoria[categoria]
        return self.dias_perfumaria if cat.grupo(categoria) == cat.PERFUMARIA else self.dias_medicamento


# Fora da personalização: a janela é da rotina (vale pra todas as lojas).
# "tolerancia_preco" (sem uso desde 29/09/2026, apagada em 02/10/2026) ainda
# pode estar no JSON de personalizações e configurações antigas: ignorada.
# As correspondências de linha (setor da IQVIA e tipo da CMED → categoria) também ficaram de fora em 25/10/2026: a
# classificação é do produto, igual em todas as lojas. Valor antigo gravado numa loja fica inerte (ignorado).
FORA_DA_PERSONALIZACAO = {"meses_fechados", "tolerancia_preco", "linhas_por_setor", "linhas_por_cmed"}
# Campos que são dicionários: a loja guarda (e herda) chave a chave.
_POR_CHAVE = ("dias_por_categoria",)


def diferencas(padrao: ConfigPedido, loja: ConfigPedido) -> dict:
    """O que a loja tem de diferente do padrão — é o que se grava. Dias por
    categoria e linhas por setor contam um a um: a loja guarda só as chaves
    que mudou."""
    a, b = padrao.para_dict(), loja.para_dict()
    saida = {k: b[k] for k in b if k not in FORA_DA_PERSONALIZACAO and k not in _POR_CHAVE and b[k] != a[k]}
    for campo in _POR_CHAVE:
        proprias = {c: d for c, d in b[campo].items() if a[campo].get(c) != d}
        if proprias:
            saida[campo] = proprias
    return saida


def aplicar(padrao: ConfigPedido, dif: dict | None) -> ConfigPedido:
    """O padrão com as diferenças da loja por cima (campo a campo; dias por
    categoria, categoria a categoria)."""
    valores = padrao.para_dict()
    for k, v in (dif or {}).items():
        if k in FORA_DA_PERSONALIZACAO or k not in valores:
            continue
        if k in _POR_CHAVE:
            valores[k] = {**valores[k], **v}
        else:
            valores[k] = v
    return ConfigPedido(**valores)


def quantos_campos(dif: dict | None) -> int:
    """"Reis F1 · 6 campos": cada categoria personalizada conta como um."""
    dif = dif or {}
    return (len([k for k in dif if k not in _POR_CHAVE and k not in FORA_DA_PERSONALIZACAO])
            + sum(len(dif.get(k, {})) for k in _POR_CHAVE))


def meses_da_rotina(armaz, chaves, padrao: int) -> int:
    """Janela que a rotina usa: a gravada pelo admin no Spaces
    (`chaves.configuracao()`), ou o padrão. Aqui e não em integrations/
    porque a rotina roda sem banco (e sem importar os modelos)."""
    try:
        if armaz.existe(chaves.configuracao()):
            meses = int(armaz.ler_json(chaves.configuracao()).get("meses_fechados", padrao))
            if 1 <= meses <= MESES_MAXIMO:
                return meses
    except Exception:  # noqa: BLE001 — arquivo ilegível: segue com o padrão
        pass
    return padrao
