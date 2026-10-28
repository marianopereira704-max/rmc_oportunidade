"""Barras clicáveis do Dashboard (Custom Component v2, 21/10/2026).

Clique numa barra filtra a tela; Ctrl/⌘ + clique soma ou tira uma barra da
seleção; clique de novo numa barra que já era a única selecionada limpa. Os
gráficos nativos do Streamlit não têm Ctrl+clique — por isso um componente
próprio (spike de 21/10/2026: 40 a 150 ms do clique até a tela inteira
refletir, nos navegadores Chromium).

Divisão do trabalho: o Python decide TUDO que é número (o eixo vem de
`core.dashboard.eixo`, os textos já vêm formatados) e guarda a seleção; o
JavaScript só desenha e avisa do clique. Assim o que o gráfico mostra é
testável sem navegador (`montar_dados`, `nova_selecao`).

Medidas fixas (as mesmas em todos os gráficos, para a tela não "pular"):
área do gráfico de 200 px, 28 px acima dela para o valor escrito, 5 marcas no
eixo Y, barras com no máximo 88 px.

Achados do spike que ficaram aqui:
- O Chrome NÃO gera `click` quando Enter/Espaço é pressionado com Ctrl/⌘ (só o
  `keydown`): o componente trata o teclado à parte.
- Cada clique roda o script inteiro, e a tela é redesenhada: o foco do teclado
  volta para a barra que o tinha, senão quem navega por teclado perde o lugar.
"""
from __future__ import annotations

import streamlit as st

from core import dashboard as dash
from core.theme import BALAO_DICA, GRAFICO

ALTURA_PLOT = 200
ESPACO_VALOR = 28        # acima da área, para o valor escrito em cima da barra mais alta
LARGURA_EIXO = 72        # cabe "R$ 400 mil" em uma linha (com 56 px quebrava em duas — visto em 21/10/2026)
ALTURA_ROTULO = 38       # embaixo: 8 px de respiro + 2 linhas de 12 px (o rótulo cabe em 2 linhas)
LARGURA_MAX_BARRA = 88


# ---------------------------------------------------------------------------
# Regras puras (testadas em tests/test_barras_filtro.py)
# ---------------------------------------------------------------------------

def nova_selecao(atual: list[str], chave: str, soma: bool) -> list[str]:
    """A seleção depois de um clique.
    - com Ctrl/⌘: soma a barra, ou tira se já estava;
    - sem: a barra vira a única selecionada; se ela já era a única, limpa."""
    atual = list(atual)
    if soma:
        return [c for c in atual if c != chave] if chave in atual else atual + [chave]
    return [] if atual == [chave] else [chave]


def _casas(x: float) -> str:
    texto = f"{x:.0f}" if x >= 100 else f"{x:.1f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def abreviar(valor: float) -> str:
    """372835 → "373 mil"; 1500000 → "1,5 mi"; 7 → "7". Para os rótulos do eixo
    e o valor em cima da barra (tabelas e cards nunca abreviam)."""
    v = float(valor)
    sinal, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e6:
        return f"{sinal}{_casas(a / 1e6)} mi"
    if a >= 1e3:
        return f"{sinal}{_casas(a / 1e3)} mil"
    return f"{sinal}{_casas(a)}"


def moeda_curta(valor: float) -> str:
    return f"R$ {abreviar(valor)}"


def _extremos(partes: list[dict]) -> tuple[float, float]:
    """(soma dos positivos, soma dos negativos) de uma barra: o que ela sobe e o
    que ela desce a partir do zero."""
    positivos = sum(p["valor"] for p in partes if p["valor"] > 0)
    negativos = sum(p["valor"] for p in partes if p["valor"] < 0)
    return positivos, negativos


def montar_dados(itens: list[dict], selecionadas, formato=abreviar, legenda=(), inteiro: bool = False,
                 barra_max: int = LARGURA_MAX_BARRA) -> dict:
    """Tudo que o JavaScript precisa, já calculado.

    `itens`: [{"chave", "rotulo", "partes": [{"valor", "cor", "nome"}], "texto" (o valor escrito em
    cima), "dica"}]. As partes de uma barra empilham do zero para cima (positivas) e do zero para
    baixo (negativas). `legenda`: [(cor, nome)]. `inteiro`: eixo de contagem (passo mínimo 1)."""
    extremos = [_extremos(i["partes"]) for i in itens]
    valores = [v for par in extremos for v in par]
    minimo, maximo, marcas = dash.eixo(valores, inteiro=inteiro)
    return {
        "itens": itens,
        "selecionadas": list(selecionadas or []),
        "eixo": {"min": minimo, "max": maximo, "marcas": [{"valor": m, "texto": formato(m)} for m in marcas]},
        "legenda": [{"cor": c, "nome": n} for c, n in legenda],
        "layout": {"plot": ALTURA_PLOT, "topo": ESPACO_VALOR, "eixo": LARGURA_EIXO, "rotulo": ALTURA_ROTULO,
                   "barra_max": barra_max},
        "cores": {"grade": GRAFICO["grade"], "zero": GRAFICO["zero"], "texto": GRAFICO["texto"],
                  "suporte": GRAFICO["texto_suporte"]},
    }


# ---------------------------------------------------------------------------
# Componente
# ---------------------------------------------------------------------------

_HTML = '<div class="grafico" role="group"></div><div class="dica" role="tooltip"></div>'

_CSS = """
.grafico { font: 12px/1.2 var(--st-font); color: var(--c-suporte); display: flex; flex-direction: column; gap: 8px; }
.legenda { display: flex; flex-wrap: wrap; gap: 16px; min-height: 20px; align-items: center; }
.legenda span { display: inline-flex; align-items: center; gap: 6px; }
.legenda i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.area { position: relative; height: calc(var(--topo) + var(--plot) + var(--rotulo)); }
.marca-y { position: absolute; left: 0; width: calc(var(--eixo) - 8px); text-align: right; transform: translateY(-50%);
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.grade { position: absolute; left: var(--eixo); right: 0; height: 1px; background: var(--c-grade); }
.grade.zero { background: var(--c-zero); }
.barras { position: absolute; left: var(--eixo); right: 0; top: 0; bottom: 0; display: flex;
  justify-content: space-around; gap: 12px; padding: 0 8px; }
.barra { position: relative; flex: 1; max-width: var(--barra-max); height: 100%; background: transparent; border: 0;
  padding: 0; cursor: pointer; font: inherit; color: inherit; }
.barra:focus-visible { outline: 2px solid #4A6E90; outline-offset: 2px; border-radius: 4px; }
.barra .seg { position: absolute; left: 0; right: 0; transition: opacity .15s; }
.barra .valor { position: absolute; left: -12px; right: -12px; text-align: center; font-weight: 600;
  color: var(--c-texto); white-space: nowrap; font-variant-numeric: tabular-nums; transition: opacity .15s; }
.barra .rot { position: absolute; left: -6px; right: -6px; text-align: center; overflow-wrap: anywhere; }
.barra:hover .seg { filter: brightness(1.08); }
.barra.apagada .seg, .barra.apagada .valor { opacity: .35; }
.barra[aria-pressed="true"] .rot { color: var(--c-texto); font-weight: 700; }
.vazio { position: absolute; left: var(--eixo); right: 0; top: calc(var(--topo) + var(--plot) / 2 - 8px); text-align: center; }
.dica { position: fixed; z-index: 1000; display: none; pointer-events: none; font-family: var(--st-font); """ + BALAO_DICA + """ }
@media (prefers-reduced-motion: reduce) { .barra .seg, .barra .valor { transition: none; } }
"""

_JS = """
const GAP = 2;   // vão de 2 px entre as partes empilhadas

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const raiz = parentElement.querySelector(".grafico");
  const dica = parentElement.querySelector(".dica");
  const L = data.layout, E = data.eixo, sel = new Set(data.selecionadas || []);
  for (const [k, v] of Object.entries(data.cores)) raiz.style.setProperty("--c-" + k, v);
  raiz.style.setProperty("--plot", L.plot + "px"); raiz.style.setProperty("--topo", L.topo + "px");
  raiz.style.setProperty("--eixo", L.eixo + "px"); raiz.style.setProperty("--rotulo", L.rotulo + "px");
  raiz.style.setProperty("--barra-max", L.barra_max + "px");

  // O gráfico é redesenhado a cada clique (o Python devolve a seleção nova): quem navega por
  // teclado perderia o foco. Guarda qual barra tinha, e devolve depois.
  const comFoco = raiz.__foco || null;
  raiz.__reconstruindo = true;
  raiz.innerHTML = "";
  const y = (v) => L.topo + (E.max - v) / (E.max - E.min) * L.plot;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const leg = document.createElement("div");
  leg.className = "legenda";
  leg.innerHTML = (data.legenda || []).map((l) => `<span><i style="background:${esc(l.cor)}"></i>${esc(l.nome)}</span>`).join("");
  raiz.appendChild(leg);

  const area = document.createElement("div");
  area.className = "area";
  E.marcas.forEach((m) => {
    const g = document.createElement("div");
    g.className = "grade" + (m.valor === 0 ? " zero" : "");
    g.style.top = y(m.valor) + "px";
    area.appendChild(g);
    const t = document.createElement("div");
    t.className = "marca-y";
    t.style.top = y(m.valor) + "px";
    t.textContent = m.texto;
    area.appendChild(t);
  });

  const barras = document.createElement("div");
  barras.className = "barras";
  const algum = sel.size > 0;
  data.itens.forEach((it) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "barra" + (algum && !sel.has(it.chave) ? " apagada" : "");
    b.dataset.chave = it.chave;
    b.setAttribute("aria-pressed", sel.has(it.chave) ? "true" : "false");
    b.setAttribute("aria-label", it.rotulo + ": " + it.texto);

    // Positivas sobem do zero, negativas descem; cada parte fica entre dois valores acumulados.
    let sobe = 0, desce = 0;
    const desenhar = [];
    it.partes.forEach((p) => {
      if (p.valor > 0) { desenhar.push({ p, de: sobe, ate: sobe + p.valor, dir: 1, primeira: sobe === 0 }); sobe += p.valor; }
      else if (p.valor < 0) { desenhar.push({ p, de: desce, ate: desce + p.valor, dir: -1, primeira: desce === 0 }); desce += p.valor; }
    });
    const ultimaPos = desenhar.filter((d) => d.dir === 1).pop(), ultimaNeg = desenhar.filter((d) => d.dir === -1).pop();
    desenhar.forEach((d) => {
      const topo = y(Math.max(d.de, d.ate)), base = y(Math.min(d.de, d.ate));
      // vão só entre partes: a que encosta no zero não perde nada
      const top = d.dir === 1 ? topo : topo + (d.primeira ? 0 : GAP);
      const alt = (base - topo) - (d.primeira ? 0 : GAP);
      if (alt < 0.5) return;
      const s = document.createElement("div");
      s.className = "seg";
      s.style.cssText = `top:${top}px;height:${alt}px;background:${d.p.cor};` +
        (d === ultimaPos ? "border-radius:4px 4px 0 0;" : d === ultimaNeg ? "border-radius:0 0 4px 4px;" : "");
      b.appendChild(s);
    });
    // o valor escrito: em cima da barra (ou embaixo, se só desce)
    const v = document.createElement("div");
    v.className = "valor";
    v.textContent = it.texto;
    v.style.top = (sobe > 0 || desce === 0 ? y(sobe) - 18 : y(desce) + 4) + "px";
    b.appendChild(v);
    const r = document.createElement("div");
    r.className = "rot";
    r.style.top = (L.topo + L.plot + 8) + "px";
    r.textContent = it.rotulo;
    b.appendChild(r);

    b.onclick = (e) => setTriggerValue("clique", { chave: it.chave, soma: !!(e.ctrlKey || e.metaKey), t: Date.now() });
    // O Chrome não gera "click" com Ctrl/⌘ + Enter/Espaço (só o keydown).
    b.onkeydown = (e) => {
      if ((e.key === "Enter" || e.key === " ") && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setTriggerValue("clique", { chave: it.chave, soma: true, t: Date.now() });
      }
    };
    const mostrar = () => {
      if (!it.dica) return;
      dica.textContent = it.dica;
      dica.style.display = "block";
      const r0 = b.getBoundingClientRect(), tw = dica.offsetWidth, th = dica.offsetHeight;
      const topoBarra = Math.min(y(sobe), y(0)) - 18;
      dica.style.left = Math.max(4, Math.min(window.innerWidth - tw - 4, r0.left + r0.width / 2 - tw / 2)) + "px";
      dica.style.top = Math.max(4, r0.top + topoBarra - th - 6) + "px";
    };
    b.onmouseenter = mostrar; b.onfocus = () => { raiz.__foco = it.chave; mostrar(); };
    b.onmouseleave = () => { dica.style.display = "none"; };
    b.onblur = () => { dica.style.display = "none"; if (!raiz.__reconstruindo) raiz.__foco = null; };
    barras.appendChild(b);
  });
  area.appendChild(barras);
  if (!data.itens.length) {
    const vz = document.createElement("div");
    vz.className = "vazio";
    vz.textContent = "Sem dados para esta seleção";
    area.appendChild(vz);
  }
  raiz.appendChild(area);
  raiz.__reconstruindo = false;
  if (comFoco) {
    const alvo = Array.from(raiz.querySelectorAll(".barra")).find((x) => x.dataset.chave === comFoco);
    if (alvo) alvo.focus({ preventScroll: true });
  }
}
"""

_COMPONENTE = st.components.v2.component("rmc_barras_filtro", html=_HTML, css=_CSS, js=_JS)


def barras(key: str, estado: str, itens: list[dict], formato=abreviar, legenda=(), inteiro: bool = False,
           barra_max: int = LARGURA_MAX_BARRA) -> None:
    """Desenha o gráfico. `estado` = a chave do `st.session_state` que guarda a seleção (uma lista de
    chaves de barra): o componente a atualiza no clique, e dois gráficos (ou duas telas) que usam a
    mesma chave compartilham o filtro. `inteiro`: gráfico de contagem (eixo sem casas decimais). `barra_max`: largura
    máxima da barra em px (padrão 88; um gráfico de largura inteira com poucas barras usa mais)."""
    def _ao_clicar() -> None:
        evento = st.session_state[key].clique
        st.session_state[estado] = nova_selecao(st.session_state.get(estado, []), evento["chave"], evento["soma"])

    _COMPONENTE(key=key, data=montar_dados(itens, st.session_state.get(estado, []), formato, legenda, inteiro, barra_max),
                on_clique_change=_ao_clicar)
