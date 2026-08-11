const state = {
  page: 1, mode: "candidates", query: "", account: "", category: "",
  hasMore: false, loading: false, selected: new Set(), manuallyDeselected: new Set(),
  items: new Map(), activeId: null, contextId: null, pendingAction: null,
};
const MAX_SELECTION = 200;
const $ = (selector) => document.querySelector(selector);
const els = {
  list: $("#message-list"), loading: $("#loading-state"), empty: $("#empty-state"),
  results: $("#results-section"), loadMore: $("#load-more"), sentinel: $("#load-sentinel"),
  total: $("#total-count"), selectedCount: $("#selected-count"), barSelected: $("#bar-selected-count"),
  actionBar: $("#action-bar"), search: $("#search-input"), account: $("#account-filter"),
  category: $("#category-filter"), reader: $("#reader-pane"), readerEmpty: $("#reader-empty"),
  readerContent: $("#reader-content"), readerSubject: $("#reader-subject"),
  readerMeta: $("#reader-meta"), readerDate: $("#reader-date"), readerTags: $("#reader-tags"),
  readerBody: $("#reader-body"), contextMenu: $("#context-menu"), modal: $("#confirm-modal"),
  modalTitle: $("#modal-title"), modalCopy: $("#modal-copy"), modalConfirm: $("#modal-confirm"),
  toast: $("#toast"), theme: $("#theme-toggle"),
};

const categoryLabels = {
  marketing: "Marketing", promocao: "Promoção", spam_suspeito: "Spam suspeito",
  followup_sem_acao: "Follow-up sem ação", ignorar: "Ignorar", noticia: "Notícia",
  documento: "Documento", documento_fiscal: "Documento fiscal",
  aguardando_resposta: "Aguardando resposta", importante_p0: "Importante P0",
  importante_p1: "Importante P1", revisar: "Revisar",
};

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function formatDate(value, compact = false) {
  if (!value) return "Data desconhecida";
  return new Intl.DateTimeFormat("pt-BR", compact
    ? { day: "2-digit", month: "short" }
    : { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function cardTemplate(item) {
  const checked = state.selected.has(item.id);
  const sender = item.from_name || item.from_email || "Remetente desconhecido";
  const category = categoryLabels[item.category] || item.category || "Sem categoria";
  return `<article class="message-card" tabindex="0" data-card-id="${escapeHtml(item.id)}"
      data-selected="${checked}" data-active="${state.activeId === item.id}">
    <label class="check-wrap" title="Selecionar para ação em lote">
      <input class="message-checkbox" type="checkbox" data-id="${escapeHtml(item.id)}" ${checked ? "checked" : ""} />
      <span class="sr-only">Selecionar ${escapeHtml(item.subject)}</span>
    </label>
    <div class="message-summary">
      <div class="message-line"><strong>${escapeHtml(sender)}</strong><time>${escapeHtml(formatDate(item.date, true))}</time></div>
      <div class="subject-line">${escapeHtml(item.subject)}</div>
      <p>${escapeHtml(item.snippet || "Sem prévia disponível.")}</p>
      <div class="message-footer"><span>${escapeHtml(category)}</span><span>${escapeHtml(item.account)}</span>${item.has_attachment ? "<span>Anexo</span>" : ""}</div>
    </div>
  </article>`;
}

function updateSelectionUI() {
  const count = state.selected.size;
  els.selectedCount.textContent = `${count} selecionada${count === 1 ? "" : "s"}`;
  els.barSelected.textContent = count;
  els.actionBar.classList.toggle("is-hidden", count === 0);
  els.actionBar.setAttribute("aria-hidden", count === 0 ? "true" : "false");
  els.actionBar.inert = count === 0;
  document.querySelectorAll("[data-card-id]").forEach((card) => {
    const selected = state.selected.has(card.dataset.cardId);
    card.dataset.selected = String(selected);
    const checkbox = card.querySelector(".message-checkbox");
    if (checkbox) checkbox.checked = selected;
  });
}

function updateModeTabs() {
  document.querySelectorAll(".mode-tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.mode === state.mode);
  });
}

function populateAccounts(accounts) {
  const current = els.account.value;
  const known = new Set([...els.account.options].map((option) => option.value));
  accounts.forEach((account) => {
    if (known.has(account)) return;
    els.account.add(new Option(account, account));
  });
  els.account.value = current;
}

async function loadMessages({ reset = false } = {}) {
  if (state.loading || (!reset && !state.hasMore && state.page > 1)) return;
  if (reset) {
    state.page = 1; state.hasMore = false; state.items.clear(); state.selected.clear();
    state.manuallyDeselected.clear(); state.activeId = null; els.list.innerHTML = "";
    els.loading.classList.remove("hidden"); els.empty.classList.add("hidden"); clearReader();
  }
  state.loading = true;
  els.results.setAttribute("aria-busy", "true");
  const params = new URLSearchParams({ page: String(state.page), page_size: "40", mode: state.mode });
  if (state.query) params.set("query", state.query);
  if (state.account) params.set("account", state.account);
  if (state.category) params.set("category", state.category);
  try {
    const response = await fetch(`/api/cleanup/messages?${params}`);
    if (!response.ok) throw new Error(`Falha ao carregar (${response.status})`);
    const payload = await response.json();
    els.loading.classList.add("hidden");
    els.total.textContent = `${new Intl.NumberFormat("pt-BR").format(payload.total)} mensagens`;
    populateAccounts(payload.accounts);
    payload.items.forEach((item) => {
      state.items.set(item.id, item);
      if (item.cleanup_candidate && !state.manuallyDeselected.has(item.id) && state.selected.size < MAX_SELECTION) {
        state.selected.add(item.id);
      }
      els.list.insertAdjacentHTML("beforeend", cardTemplate(item));
    });
    state.hasMore = payload.has_more; state.page += 1;
    els.loadMore.classList.toggle("hidden", !state.hasMore);
    els.empty.classList.toggle("hidden", state.items.size !== 0);
    updateSelectionUI();
  } catch (error) { showToast(error.message, true); }
  finally { state.loading = false; els.results.setAttribute("aria-busy", "false"); }
}

function clearReader() {
  els.readerEmpty.classList.remove("hidden"); els.readerContent.classList.add("hidden");
  els.reader.classList.remove("mobile-open");
}

async function openMessage(id) {
  if (!id) return;
  state.activeId = id;
  document.querySelectorAll("[data-card-id]").forEach((card) => {
    card.dataset.active = String(card.dataset.cardId === id);
  });
  els.readerEmpty.classList.add("hidden"); els.readerContent.classList.remove("hidden");
  els.reader.classList.add("mobile-open");
  els.readerSubject.textContent = "Carregando…"; els.readerMeta.textContent = "";
  els.readerDate.textContent = ""; els.readerTags.innerHTML = ""; els.readerBody.textContent = "";
  try {
    const response = await fetch(`/api/cleanup/messages/${encodeURIComponent(id)}`);
    const item = await response.json();
    if (!response.ok) throw new Error(item.detail || "Não foi possível abrir a mensagem.");
    if (state.activeId !== id) return;
    const sender = item.from_name || item.from_email || "Remetente desconhecido";
    els.readerSubject.textContent = item.subject;
    els.readerMeta.textContent = `${sender} <${item.from_email}> · para ${item.account}`;
    els.readerDate.textContent = formatDate(item.date);
    const tags = [categoryLabels[item.category] || item.category, item.priority, item.has_attachment ? "Anexo" : null].filter(Boolean);
    els.readerTags.innerHTML = tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
    els.readerBody.textContent = item.body || "Mensagem sem corpo disponível.";
  } catch (error) { showToast(error.message, true); clearReader(); }
}

function hideContextMenu() { els.contextMenu.classList.add("hidden"); state.contextId = null; }

function showContextMenu(event, id) {
  event.preventDefault(); state.contextId = id; els.contextMenu.classList.remove("hidden");
  const menuWidth = 220, menuHeight = 112;
  els.contextMenu.style.left = `${Math.min(event.clientX, window.innerWidth - menuWidth - 8)}px`;
  els.contextMenu.style.top = `${Math.min(event.clientY, window.innerHeight - menuHeight - 8)}px`;
}

async function addToBlacklist(target) {
  const id = state.contextId;
  if (!id) return;
  hideContextMenu();
  try {
    const response = await fetch(`/api/cleanup/messages/${encodeURIComponent(id)}/blacklist`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Não foi possível criar a blacklist.");
    showToast(`${target === "sender" ? "Remetente" : "Domínio"} ${payload.value} adicionado à blacklist.`);
  } catch (error) { showToast(error.message, true); }
}

els.list.addEventListener("click", (event) => {
  if (event.target.closest(".message-checkbox")) return;
  const card = event.target.closest("[data-card-id]"); if (card) openMessage(card.dataset.cardId);
});
els.list.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest("[data-card-id]"); if (card) { event.preventDefault(); openMessage(card.dataset.cardId); }
});
els.list.addEventListener("contextmenu", (event) => {
  const card = event.target.closest("[data-card-id]"); if (card) showContextMenu(event, card.dataset.cardId);
});
els.list.addEventListener("change", (event) => {
  const checkbox = event.target.closest(".message-checkbox"); if (!checkbox) return;
  const id = checkbox.dataset.id;
  if (checkbox.checked) {
    if (state.selected.size >= MAX_SELECTION) { checkbox.checked = false; return showToast(`Máximo de ${MAX_SELECTION} mensagens.`, true); }
    state.selected.add(id); state.manuallyDeselected.delete(id);
  } else { state.selected.delete(id); state.manuallyDeselected.add(id); }
  updateSelectionUI();
});

els.contextMenu.addEventListener("click", (event) => {
  const button = event.target.closest("[data-blacklist]"); if (button) addToBlacklist(button.dataset.blacklist);
});
document.addEventListener("click", (event) => { if (!event.target.closest("#context-menu")) hideContextMenu(); });
window.addEventListener("resize", hideContextMenu);
$("#reader-back").addEventListener("click", () => els.reader.classList.remove("mobile-open"));

function resetAndLoad() { loadMessages({ reset: true }); }
let searchTimer;
els.search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = els.search.value.trim(); resetAndLoad(); }, 280); });
els.account.addEventListener("change", () => { state.account = els.account.value; resetAndLoad(); });
els.category.addEventListener("change", () => { state.category = els.category.value; resetAndLoad(); });
$("#mode-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-mode]"); if (!tab || tab.dataset.mode === state.mode) return;
  state.mode = tab.dataset.mode; updateModeTabs(); resetAndLoad();
});
$("#select-visible").addEventListener("click", () => {
  state.items.forEach((_, id) => { if (state.selected.size < MAX_SELECTION) { state.selected.add(id); state.manuallyDeselected.delete(id); } });
  updateSelectionUI();
});
els.loadMore.addEventListener("click", () => loadMessages());
new IntersectionObserver(([entry]) => { if (entry.isIntersecting && state.hasMore && !state.loading) loadMessages(); }, { rootMargin: "300px" }).observe(els.sentinel);

document.querySelectorAll(".bulk-trigger").forEach((button) => button.addEventListener("click", () => openConfirmation(button.dataset.action)));
function openConfirmation(action) {
  state.pendingAction = action; const count = state.selected.size; const trash = action === "trash";
  els.modalTitle.textContent = trash ? "Mover para a Lixeira?" : "Arquivar mensagens?";
  els.modalCopy.textContent = trash ? `${count} mensagem(ns) serão movidas para a Lixeira recuperável.` : `${count} mensagem(ns) sairão da Inbox e irão para o arquivo nativo.`;
  els.modalConfirm.textContent = trash ? "Mover à Lixeira" : "Arquivar";
  els.modalConfirm.classList.toggle("danger", trash); els.modal.classList.remove("hidden"); els.modalConfirm.focus();
}
function closeConfirmation() { state.pendingAction = null; els.modal.classList.add("hidden"); }
$("#modal-cancel").addEventListener("click", closeConfirmation);
els.modal.addEventListener("click", (event) => { if (event.target === els.modal) closeConfirmation(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeConfirmation(); hideContextMenu(); } });
els.modalConfirm.addEventListener("click", async () => {
  if (!state.pendingAction || !state.selected.size) return;
  els.modalConfirm.disabled = true;
  try {
    const response = await fetch("/api/cleanup/bulk-action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: state.pendingAction, ids: [...state.selected], confirmed: true }) });
    const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || "A ação falhou.");
    closeConfirmation(); showToast(`${payload.succeeded} concluída(s)${payload.failed ? ` · ${payload.failed} falha(s)` : ""}`, payload.failed > 0); await resetAndLoad();
  } catch (error) { showToast(error.message, true); } finally { els.modalConfirm.disabled = false; }
});

const themeOrder = ["system", "light", "dark"];
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  els.theme.textContent = `Tema: ${{ system: "sistema", light: "claro", dark: "escuro" }[theme]}`;
  localStorage.setItem("email-helper-theme", theme);
}
els.theme.addEventListener("click", () => {
  const current = localStorage.getItem("email-helper-theme") || "system";
  applyTheme(themeOrder[(themeOrder.indexOf(current) + 1) % themeOrder.length]);
});

let toastTimer;
function showToast(message, isError = false) {
  clearTimeout(toastTimer); els.toast.textContent = message; els.toast.classList.toggle("is-error", isError);
  els.toast.classList.remove("hidden"); toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 5000);
}

applyTheme(localStorage.getItem("email-helper-theme") || "system");
updateModeTabs(); loadMessages({ reset: true });
