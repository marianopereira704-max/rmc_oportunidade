"""Pílula "Copiar link" (Custom Component v2, 07/10/2026) — usada no pop-up
do convite do Cadastro: o link de criar senha não aparece mais inteiro na
tela; um clique copia, e a pílula vira "Link copiado" em verde (modelo do
print do Mariano).

Tudo no navegador, sem volta ao servidor (nenhum rerun ao clicar). A cópia
usa a API de área de transferência; navegador que bloqueia (endereço sem
https, permissão negada) cai no `execCommand("copy")` antigo e, se nem ele
der, a pílula mostra o link selecionado pra copiar à mão.
"""
from __future__ import annotations

import streamlit as st

_HTML = """
<div class="rmc-copiar">
  <button type="button" class="pilula">
    <svg class="icone-link" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4l-1 1M14 10a4.5 4.5 0 0 0-6.4 0l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
    <svg class="icone-ok" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <span class="rotulo">Copiar link</span>
  </button>
  <input class="manual" readonly />
</div>
"""

# Cores da identidade (core/theme.py): contorno navy claro; copiado = par de
# status "sucesso" (#EAF3DE / #27500A) com borda verde-escura.
_CSS = """
.rmc-copiar { display: flex; flex-direction: column; align-items: center; gap: 8px; width: 100%;
  font-family: var(--st-font); }
.pilula { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 18px;
  border-radius: 999px; border: 1px solid #4A6E90; background: #FFFFFF; color: #17375E;
  font: 600 14px var(--st-font); cursor: pointer; transition: background .15s, border-color .15s; }
.pilula:hover { background: #F7F8FA; border-color: #17375E; }
.pilula svg { width: 16px; height: 16px; }
.pilula .icone-ok { display: none; }
.pilula.copiado { background: #EAF3DE; border-color: #639922; color: #27500A; cursor: default; }
.pilula.copiado .icone-ok { display: inline; }
.pilula.copiado .icone-link { display: none; }
.manual { display: none; width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 8px;
  border: 1px solid #E4E7EB; font: 13px var(--st-font); color: #3F4750; background: #F7F8FA; }
"""

_JS = """
async function copiar(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch (e) {
    const area = document.createElement("textarea");
    area.value = texto;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    area.remove();
    return ok;
  }
}

export default function (component) {
  const { data, parentElement } = component;
  const botao = parentElement.querySelector(".pilula");
  const rotulo = parentElement.querySelector(".rotulo");
  const manual = parentElement.querySelector(".manual");
  const url = (data && data.url) || "";
  botao.onclick = async () => {
    if (await copiar(url)) {
      botao.classList.add("copiado");
      rotulo.textContent = "Link copiado";
    } else {
      rotulo.textContent = "Copie o link abaixo";
      manual.value = url;
      manual.style.display = "block";
      manual.select();
    }
  };
}
"""

_COMPONENTE = st.components.v2.component("rmc_copiar_link", html=_HTML, css=_CSS, js=_JS)


def copiar_link(url: str, key: str) -> None:
    _COMPONENTE(key=key, data={"url": url})
