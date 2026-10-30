"""Botão "Enviar pedido" do celular (29/10/2026, etapa 6 — decisão do Mariano): abre a janela de compartilhar do
próprio aparelho (WhatsApp, e-mail…) com o Excel do pedido. Quem envia é a pessoa, do aparelho dela: sem custo e sem
API do WhatsApp. Sem a janela de compartilhar (navegador antigo, ou fora de https), baixa o arquivo, como o
Exportar. Conta como exportação (`ao_enviar`).

Também é daqui que o rodapé do Assistente some ao rolar para baixo e volta ao rolar para cima (opção B do Mariano):
a classe `rmc-rolando-baixo` vai no <body> (que o Streamlit não redesenha) e o CSS em core/theme.py esconde o
rodapé só em tela de celular.

Custom Component v2, como views/tabela.py: mudou o JS/CSS, reinicie o `streamlit run`."""
from __future__ import annotations

import base64
from typing import Callable

import streamlit as st

_HTML = '<button type="button" class="enviar"></button>'

_CSS = """
:host { display: block; }
.enviar {
  width: 100%; height: 44px; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  border: 0; border-radius: var(--raio, 8px); background: var(--navy, #17375E); color: #FFFFFF;
  font: inherit; font-size: 15px; font-weight: 600; cursor: pointer;
}
.enviar:disabled { background: var(--borda, #E4E7EB); color: var(--texto-muted, #7A8699); cursor: default; }
.enviar:focus-visible { outline: 2px solid var(--navy, #17375E); outline-offset: 2px; }
.enviar svg { width: 18px; height: 18px; }
"""

_JS = """
const ICONE = '<path d="M16 5l3 3-3 3M19 8H10a5 5 0 0 0-5 5v1M5 19h14" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" fill="none"/>';

function baixar(arquivo) {
  const url = URL.createObjectURL(arquivo);
  const a = document.createElement("a");
  a.href = url; a.download = arquivo.name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

function vigiarRolagem() {
  // Uma vez por página. O Streamlit rola um contêiner por dentro, não a página: o "scroll" é ouvido em todos.
  if (window.__rmcRolagem) return;
  window.__rmcRolagem = true;
  const ultimo = new WeakMap();
  document.addEventListener("scroll", (ev) => {
    const alvo = ev.target === document ? document.scrollingElement : ev.target;
    if (!alvo || typeof alvo.scrollTop !== "number") return;
    const antes = ultimo.has(alvo) ? ultimo.get(alvo) : 0;
    const agora = alvo.scrollTop;
    if (Math.abs(agora - antes) < 8) return;
    ultimo.set(alvo, agora);
    document.body.classList.toggle("rmc-rolando-baixo", agora > antes && agora > 80);
  }, { capture: true, passive: true });
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const botao = parentElement.querySelector(".enviar");
  botao.replaceChildren();
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONE;  // constante deste arquivo, nunca dado do banco
  botao.append(s, document.createTextNode(data.rotulo));
  botao.disabled = !data.arquivo;
  vigiarRolagem();
  botao.onclick = async () => {
    if (!data.arquivo) return;
    const bytes = Uint8Array.from(atob(data.arquivo), (c) => c.charCodeAt(0));
    const arquivo = new File([bytes], data.nome, { type: data.mime });
    try {
      if (navigator.canShare && navigator.canShare({ files: [arquivo] })) {
        await navigator.share({ files: [arquivo], title: data.titulo });
      } else {
        baixar(arquivo);
      }
      setTriggerValue("enviado", Date.now());
    } catch (erro) {
      // Fechou a janela de compartilhar sem escolher: nada foi enviado, nada a registrar.
      if (erro && erro.name === "AbortError") return;
      baixar(arquivo);
      setTriggerValue("enviado", Date.now());
    }
  };
}
"""

_COMPONENTE = st.components.v2.component("rmc_enviar_pedido", html=_HTML, css=_CSS, js=_JS)
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def botao(key: str, rotulo: str, arquivo: bytes | None, nome: str, titulo: str, ao_enviar: Callable[[], None]) -> None:
    """`arquivo` None = desabilitado (nada marcado). `ao_enviar` roda depois que a pessoa escolheu para onde mandar
    (ou baixou), antes do rerun."""
    def _enviado() -> None:
        if st.session_state[key].enviado:
            ao_enviar()

    _COMPONENTE(
        key=key,
        data={"rotulo": rotulo, "arquivo": base64.b64encode(arquivo).decode("ascii") if arquivo else None,
              "nome": nome, "titulo": titulo, "mime": _XLSX},
        on_enviado_change=_enviado,
    )
