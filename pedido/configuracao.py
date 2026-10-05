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
        ):
            if not (minimo <= valor <= maximo):
                e.append(f"{nome}: entre {minimo} e {maximo} (está {valor}).")
        for categoria, dias in self.dias_por_categoria.items():
            if categoria not in cat.CATEGORIAS:
                e.append(f"Categoria desconhecida: {categoria}.")
            elif not (1 <= dias <= 180):
                e.append(f"Dias de {categoria}: entre 1 e 180 (está {dias}).")
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
FORA_DA_PERSONALIZACAO = {"meses_fechados", "tolerancia_preco"}


def diferencas(padrao: ConfigPedido, loja: ConfigPedido) -> dict:
    """O que a loja tem de diferente do padrão — é o que se grava. Dias por
    categoria contam um a um: a loja guarda só as categorias que mudou."""
    a, b = padrao.para_dict(), loja.para_dict()
    saida = {k: b[k] for k in b if k not in FORA_DA_PERSONALIZACAO and k != "dias_por_categoria" and b[k] != a[k]}
    categorias = {c: d for c, d in b["dias_por_categoria"].items() if a["dias_por_categoria"].get(c) != d}
    if categorias:
        saida["dias_por_categoria"] = categorias
    return saida


def aplicar(padrao: ConfigPedido, dif: dict | None) -> ConfigPedido:
    """O padrão com as diferenças da loja por cima (campo a campo; dias por
    categoria, categoria a categoria)."""
    valores = padrao.para_dict()
    for k, v in (dif or {}).items():
        if k in FORA_DA_PERSONALIZACAO or k not in valores:
            continue
        if k == "dias_por_categoria":
            valores[k] = {**valores[k], **v}
        else:
            valores[k] = v
    return ConfigPedido(**valores)


def quantos_campos(dif: dict | None) -> int:
    """"Reis F1 · 6 campos": cada categoria personalizada conta como um."""
    dif = dif or {}
    return len([k for k in dif if k != "dias_por_categoria"]) + len(dif.get("dias_por_categoria", {}))


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
