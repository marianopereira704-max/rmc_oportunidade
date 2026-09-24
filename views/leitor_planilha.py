"""Seletor de planilha que LÊ O .XLSX NO NAVEGADOR (Custom Component v2).

O arquivo nunca é aberto no servidor: o navegador lê a primeira aba com a
SheetJS, converte em CSV, compacta com gzip e entrega só isso ao app
(poucos MB, contra os ~25s e centenas de MB que o parse do .xlsx custava no
servidor — ver integrations/planilha_navegador.py). O .xlsx ORIGINAL, que
fica guardado no Explorador de Arquivos, vai do navegador direto pro Spaces
por uma URL assinada, também sem passar pela memória do app. Se o envio
direto não for possível — Spaces não configurado (dev local) ou bucket sem
regra de CORS liberando PUT a partir do endereço do app — o original vem
junto no payload e o servidor grava (são só os bytes; o parse, que era o
peso de verdade, continua no navegador).

A planilha chega como GATILHO (`setTriggerValue`), não como estado: estado
de componente é reenviado ao servidor a cada rerun, e isso significaria
mandar os mesmos megabytes de novo a cada clique na tela.
"""
from __future__ import annotations

import logging
from typing import Callable

import streamlit as st

from integrations.planilha_navegador import PlanilhaRecebida, decodificar_payload
from storage import filesystem as fs

logger = logging.getLogger(__name__)

_HTML = """
<div class="rmc-leitor">
  <label class="rmc-leitor-botao">
    <input type="file" />
    <span class="rotulo"></span>
  </label>
  <div class="status"></div>
</div>
"""

_CSS = """
.rmc-leitor { font-family: var(--st-font); color: var(--st-text-color); }
.rmc-leitor-botao {
  display: inline-flex; align-items: center; gap: .5rem; cursor: pointer;
  padding: .55rem 1rem; border-radius: var(--st-base-radius, .5rem);
  border: 1px solid var(--st-border-color, rgba(128,128,128,.4));
  background: var(--st-secondary-background-color);
}
.rmc-leitor-botao:hover { border-color: var(--st-primary-color); }
.rmc-leitor-botao input { display: none; }
.rmc-leitor .status { margin-top: .4rem; font-size: .875rem; opacity: .8; min-height: 1.2em; }
"""

_JS = """
const SHEETJS_URL = "https://cdn.jsdelivr.net/npm/xlsx@0.18.5/+esm";
let sheetjs = null;
function carregarSheetJS() {
  if (!sheetjs) sheetjs = import(SHEETJS_URL);
  return sheetjs;
}

async function paraBase64(dados) {
  // FileReader em vez de btoa(String.fromCharCode(...)): não estoura a pilha
  // com arquivos de vários megabytes.
  const url = await new Promise((ok, falha) => {
    const leitor = new FileReader();
    leitor.onload = () => ok(leitor.result);
    leitor.onerror = () => falha(leitor.error);
    leitor.readAsDataURL(new Blob([dados]));
  });
  return url.slice(url.indexOf(",") + 1);
}

async function compactar(texto) {
  const fluxo = new Blob([texto]).stream().pipeThrough(new CompressionStream("gzip"));
  return await new Response(fluxo).arrayBuffer();
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const entrada = parentElement.querySelector("input[type=file]");
  const status = parentElement.querySelector(".status");
  parentElement.querySelector(".rotulo").textContent = (data && data.rotulo) || "Selecionar planilha";
  entrada.accept = (data && data.aceitar) || ".xlsx,.xls";

  entrada.onchange = async () => {
    const arquivo = entrada.files && entrada.files[0];
    if (!arquivo) return;
    entrada.disabled = true;
    const inicio = performance.now();
    try {
      status.textContent = "Lendo " + arquivo.name + " no seu computador...";
      const [XLSX, conteudo] = await Promise.all([carregarSheetJS(), arquivo.arrayBuffer()]);
      const pasta = XLSX.read(new Uint8Array(conteudo), { type: "array" });
      const aba = pasta.Sheets[pasta.SheetNames[0]];
      const csv = XLSX.utils.sheet_to_csv(aba, { rawNumbers: true, blankrows: false });
      status.textContent = "Compactando...";
      const csvGz = await paraBase64(await compactar(csv));

      let enviadoStorage = false;
      if (data && data.url_envio) {
        status.textContent = "Guardando o arquivo original...";
        try {
          const resposta = await fetch(data.url_envio, {
            method: "PUT", body: arquivo, headers: { "Content-Type": data.content_type },
          });
          enviadoStorage = resposta.ok;
        } catch (e) {
          // Bucket sem regra de CORS pra este endereço (ou rede): o navegador
          // bloqueia o PUT. Não é fatal — o original segue pelo app, abaixo.
          enviadoStorage = false;
        }
      }
      // Sem envio direto, o original vai junto (só os bytes: a leitura do
      // Excel, que era o peso de verdade, já aconteceu aqui no navegador).
      const originalB64 = enviadoStorage ? null : await paraBase64(conteudo);

      const segundos = (performance.now() - inicio) / 1000;
      status.textContent = "Enviando os dados ao sistema...";
      setTriggerValue("arquivo", {
        nome: arquivo.name, tamanho: arquivo.size, csv_gz_b64: csvGz, original_b64: originalB64,
        enviado_storage: enviadoStorage, storage_key: (data && data.storage_key) || null,
        segundos_leitura: segundos,
      });
      status.textContent = arquivo.name + " lido em " + segundos.toFixed(1) + "s.";
    } catch (e) {
      status.textContent = "";
      setTriggerValue("erro", String((e && e.message) || e));
    } finally {
      entrada.value = "";
      entrada.disabled = false;
    }
  };
}
"""

_COMPONENTE = st.components.v2.component("rmc_leitor_planilha", html=_HTML, css=_CSS, js=_JS)


def _chave_slot(key: str, sufixo: str) -> str:
    return f"_leitor_{key}_{sufixo}"


def _storage_key_do_slot(key: str) -> str:
    """Chave do Spaces reservada pra o PRÓXIMO arquivo deste seletor. Fica
    estável entre reruns (a URL assinada é refeita, a chave não) e é trocada
    assim que um arquivo chega — dois envios nunca caem na mesma chave."""
    slot = _chave_slot(key, "storage_key")
    if slot not in st.session_state:
        st.session_state[slot] = fs.nova_chave_armazenamento("planilha.xlsx")
    return st.session_state[slot]


def _descartar_nao_usada(anterior: PlanilhaRecebida | None) -> None:
    """Planilha selecionada e nunca processada: o original já foi pro Spaces
    e ficaria lá sem nenhum FSNode apontando pra ele."""
    if anterior is None or not anterior.storage_key:
        return
    try:
        fs.backend().excluir_fisicamente(anterior.storage_key)
    except Exception:  # limpeza de melhor esforço — nunca atrapalha o envio novo
        logger.warning("Não consegui apagar o original não usado %s", anterior.storage_key, exc_info=True)


def planilha_recebida(key: str) -> PlanilhaRecebida | None:
    return st.session_state.get(_chave_slot(key, "recebida"))


def erro_recebido(key: str) -> str | None:
    return st.session_state.get(_chave_slot(key, "erro"))


def consumir(key: str) -> None:
    """Chame quando a planilha virou um processamento: o original agora
    pertence a ele, então sai do slot sem ser apagado."""
    st.session_state.pop(_chave_slot(key, "recebida"), None)


def leitor_planilha(key: str, rotulo: str, ao_receber: Callable[[], None] | None = None) -> None:
    """Desenha o seletor. A planilha lida fica disponível em
    `planilha_recebida(key)` a partir do rerun seguinte à seleção."""
    storage_key = _storage_key_do_slot(key)
    url_envio = fs.backend().url_envio_direto(storage_key, fs.MIME_XLSX)

    def _ao_receber_arquivo() -> None:
        payload = st.session_state[key].arquivo
        if not payload:
            return
        st.session_state.pop(_chave_slot(key, "erro"), None)
        try:
            recebida = decodificar_payload(payload)
        except Exception as exc:  # payload corrompido/truncado vira erro na tela, não exceção
            st.session_state[_chave_slot(key, "erro")] = f"Não consegui ler os dados enviados pelo navegador: {exc}"
            return
        _descartar_nao_usada(st.session_state.get(_chave_slot(key, "recebida")))
        st.session_state[_chave_slot(key, "recebida")] = recebida
        st.session_state.pop(_chave_slot(key, "storage_key"), None)
        if ao_receber is not None:
            ao_receber()

    def _ao_receber_erro() -> None:
        erro = st.session_state[key].erro
        if erro:
            st.session_state[_chave_slot(key, "erro")] = f"Não consegui ler a planilha no navegador: {erro}"

    _COMPONENTE(
        key=key,
        data={
            "rotulo": rotulo,
            "aceitar": ".xlsx,.xls",
            "url_envio": url_envio,
            "storage_key": storage_key if url_envio else None,
            "content_type": fs.MIME_XLSX,
        },
        on_arquivo_change=_ao_receber_arquivo,
        on_erro_change=_ao_receber_erro,
    )
