const state = {
  page: 1, mode: "candidates", query: "", account: "", category: "", priority: "",
  datePreset: "", dateFrom: "", dateTo: "", sort: "newest", hasMore: false, loading: false,
  selected: new Set(), items: new Map(), activeId: null, contextId: null,
  pendingAction: null, lastSelectedId: null, lastRenderedGroup: null,
};
const MAX_SELECTION = 200;
const $ = (selector) => document.querySelector(selector);
const els = {
  list: $("#message-list"), loading: $("#loading-state"), empty: $("#empty-state"),
  results: $("#results-section"), loadMore: $("#load-more"), sentinel: $("#load-sentinel"),
  total: $("#total-count"), selectedCount: $("#selected-count"), barSelected: $("#bar-selected-count"),
  actionBar: $("#action-bar"), search: $("#search-input"), account: $("#account-filter"),
  category: $("#category-filter"), priority: $("#priority-filter"), datePreset: $("#date-preset"),
  dateFrom: $("#date-from"), dateTo: $("#date-to"), dateRange: $("#custom-date-range"),
  filterToggle: $("#filter-toggle"), filterPanel: $("#filter-panel"), filterCount: $("#filter-count"),
  sortToggle: $("#sort-toggle"), sortPanel: $("#sort-panel"), sortLabel: $("#sort-label"),
  selectAll: $("#select-all"), reader: $("#reader-pane"), readerEmpty: $("#reader-empty"),
  readerContent: $("#reader-content"), readerSubject: $("#reader-subject"), readerAvatar: $("#reader-avatar"),
  readerMeta: $("#reader-meta"), readerDate: $("#reader-date"), readerTags: $("#reader-tags"),
  readerCleanup: $("#reader-cleanup"), readerBody: $("#reader-body"), contextMenu: $("#context-menu"),
  modal: $("#confirm-modal"), modalTitle: $("#modal-title"), modalCopy: $("#modal-copy"),
  modalConfirm: $("#modal-confirm"), toast: $("#toast"), theme: $("#theme-toggle"),
};

const categoryLabels = {
  marketing: "Marketing", promocao: "Promoção", spam_suspeito: "Spam suspeito",
  followup_sem_acao: "Follow-up sem ação", ignorar: "Ignorar", noticia: "Notícia",
  documento: "Documento", documento_fiscal: "Documento fiscal",
  aguardando_resposta: "Aguardando resposta", importante_p0: "Importante P0",
  importante_p1: "Importante P1", revisar: "Revisar",
};
const sortLabels = { newest: "Mais recentes", oldest: "Mais antigos", priority: "Prioridade" };
const priorityLabels = { P0: "P0", P1: "P1", P2: "P2", ignore: "Ignorar" };

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function formatDate(value, compact = false) {
  if (!value) return "Data desconhecida";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Data desconhecida";
  return new Intl.DateTimeFormat("pt-BR", compact
    ? { day: "2-digit", month: "short" }
    : { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function cardTemplate(item) {
  const checked = state.selected.has(item.id);
  const sender = item.from_name || item.from_email || "Remetente desconhecido";
  const category = categoryLabels[item.category] || item.category || "Sem categoria";
  const suggestion = item.cleanup_candidate
    ? `<span class="suggestion-chip" title="${escapeHtml(item.cleanup_reason || "Sugestão de limpeza da IA")}">Limpeza sugerida</span>` : "";
  const priority = item.priority ? `<span>${escapeHtml(priorityLabels[item.priority] || item.priority)}</span>` : "";
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
      <div class="message-footer">${priority}<span>${escapeHtml(category)}</span>${suggestion}<span>${escapeHtml(item.account)}</span>${item.has_attachment ? "<span>Anexo</span>" : ""}</div>
    </div>
  </article>`;
}

function itemGroup(item) {
  if (state.sort === "priority") {
    const priority = item.priority || "none";
    return {
      key: `priority-${priority}`,
      label: priority === "none" ? "Sem prioridade" : priorityLabels[priority] || priority,
    };
  }
  if (!item.date) return { key: "date-unknown", label: "Data desconhecida" };
  const messageDate = new Date(item.date);
  if (Number.isNaN(messageDate.getTime())) return { key: "date-unknown", label: "Data desconhecida" };
  messageDate.setHours(0, 0, 0, 0);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const ageDays = Math.floor((today.getTime() - messageDate.getTime()) / 86400000);
  if (ageDays <= 0) return { key: "date-today", label: "Hoje" };
  if (ageDays === 1) return { key: "date-yesterday", label: "Ontem" };
  if (ageDays <= 7) return { key: "date-last-week", label: "Última semana" };
  if (ageDays <= 30) return { key: "date-last-month", label: "Último mês" };
  return { key: "date-older", label: "Mais de um mês" };
}

function groupHeaderTemplate(group) {
  return `<div class="message-group-header" role="heading" aria-level="2" data-group="${escapeHtml(group.key)}">${escapeHtml(group.label)}</div>`;
}

function updateSelectionUI() {
  const count = state.selected.size;
  els.selectedCount.textContent = `${count} selecionada${count === 1 ? "" : "s"}`;
  els.barSelected.textContent = count;
  const loadedIds = [...els.list.querySelectorAll("[data-card-id]")].map((card) => card.dataset.cardId);
  const selectedLoaded = loadedIds.filter((id) => state.selected.has(id)).length;
  els.selectAll.checked = loadedIds.length > 0 && selectedLoaded === loadedIds.length;
  els.selectAll.indeterminate = selectedLoaded > 0 && selectedLoaded < loadedIds.length;
  els.selectAll.disabled = loadedIds.length === 0;
  els.selectAll.setAttribute("aria-label", els.selectAll.checked
    ? "Desselecionar todas as mensagens carregadas"
    : "Selecionar todas as mensagens carregadas");
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

function setSelected(id, selected) {
  if (!selected) { state.selected.delete(id); return true; }
  if (state.selected.has(id)) return true;
  if (state.selected.size >= MAX_SELECTION) return false;
  state.selected.add(id);
  return true;
}

function selectRange(id, selected) {
  const cards = [...els.list.querySelectorAll("[data-card-id]")];
  const currentIndex = cards.findIndex((card) => card.dataset.cardId === id);
  const anchorIndex = cards.findIndex((card) => card.dataset.cardId === state.lastSelectedId);
  if (currentIndex < 0 || anchorIndex < 0) return setSelected(id, selected);
  const [start, end] = [currentIndex, anchorIndex].sort((a, b) => a - b);
  let complete = true;
  cards.slice(start, end + 1).forEach((card) => {
    if (!setSelected(card.dataset.cardId, selected)) complete = false;
  });
  return complete;
}

function updateModeTabs() {
  document.querySelectorAll(".mode-tab").forEach((tab) => {
    const active = tab.dataset.mode === state.mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-pressed", String(active));
  });
}

function populateAccounts(accounts) {
  const current = els.account.value;
  const known = new Set([...els.account.options].map((option) => option.value));
  accounts.forEach((account) => {
    if (!known.has(account)) els.account.add(new Option(account, account));
  });
  els.account.value = current;
}

function listParams({ includeMode = true } = {}) {
  const params = new URLSearchParams();
  if (includeMode) params.set("mode", state.mode);
  if (state.query) params.set("query", state.query);
  if (state.account) params.set("account", state.account);
  if (state.category) params.set("category", state.category);
  if (state.priority) params.set("priority", state.priority);
  if (state.dateFrom) params.set("date_from", state.dateFrom);
  if (state.dateTo) params.set("date_to", state.dateTo);
  params.set("sort", state.sort);
  return params;
}

async function loadMessages({ reset = false } = {}) {
  if (state.loading || (!reset && !state.hasMore && state.page > 1)) return;
  if (reset) {
    state.page = 1; state.hasMore = false; state.items.clear(); state.selected.clear();
    state.lastSelectedId = null; state.lastRenderedGroup = null; state.activeId = null; els.list.innerHTML = "";
    els.loading.classList.remove("hidden"); els.empty.classList.add("hidden"); clearReader(); updateSelectionUI();
  }
  state.loading = true;
  els.results.setAttribute("aria-busy", "true");
  const params = listParams();
  params.set("page", String(state.page)); params.set("page_size", "40");
  try {
    const response = await fetch(`/api/cleanup/messages?${params}`);
    if (!response.ok) throw new Error(`Falha ao carregar (${response.status})`);
    const payload = await response.json();
    els.loading.classList.add("hidden");
    els.total.textContent = `${new Intl.NumberFormat("pt-BR").format(payload.total)} mensagens`;
    populateAccounts(payload.accounts);
    payload.items.forEach((item) => {
      state.items.set(item.id, item);
      const group = itemGroup(item);
      if (group.key !== state.lastRenderedGroup) {
        els.list.insertAdjacentHTML("beforeend", groupHeaderTemplate(group));
        state.lastRenderedGroup = group.key;
      }
      els.list.insertAdjacentHTML("beforeend", cardTemplate(item));
    });
    state.hasMore = payload.has_more; state.page += 1;
    els.loadMore.classList.toggle("hidden", !state.hasMore);
    els.empty.classList.toggle("hidden", state.items.size !== 0);
    updateSelectionUI();
  } catch (error) { showToast(error.message, "error"); }
  finally { state.loading = false; els.results.setAttribute("aria-busy", "false"); }
}

function clearReader() {
  els.readerEmpty.classList.remove("hidden"); els.readerContent.classList.add("hidden");
  els.reader.classList.remove("mobile-open");
}

function appendLinkedText(element, value) {
  const urlPattern = /https?:\/\/[^\s<>]+/g;
  let cursor = 0;
  for (const match of value.matchAll(urlPattern)) {
    element.append(document.createTextNode(value.slice(cursor, match.index)));
    const url = match[0].replace(/[),.;!?]+$/, "");
    const link = document.createElement("a");
    link.href = url; link.textContent = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    element.append(link, document.createTextNode(match[0].slice(url.length)));
    cursor = match.index + match[0].length;
  }
  element.append(document.createTextNode(value.slice(cursor)));
}

function readableBlocks(body) {
  const clean = String(body || "").replaceAll("\r\n", "\n").replaceAll("\r", "\n").trim();
  if (!clean) return [];
  let blocks = clean.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  if (blocks.length === 1 && clean.length > 520 && !clean.includes("\n")) {
    blocks = clean.match(/.{1,420}(?:[.!?](?=\s)|$)/g)?.map((part) => part.trim()) || blocks;
  }
  return blocks;
}

function renderEmailBody(body) {
  els.readerBody.replaceChildren();
  const blocks = readableBlocks(body);
  if (!blocks.length) {
    const empty = document.createElement("p"); empty.className = "reader-body-empty";
    empty.textContent = "Mensagem sem corpo disponível."; els.readerBody.append(empty); return;
  }
  blocks.forEach((block) => {
    const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
    if (lines.length && lines.every((line) => /^[-*•]\s+/.test(line))) {
      const list = document.createElement("ul");
      lines.forEach((line) => { const item = document.createElement("li"); appendLinkedText(item, line.replace(/^[-*•]\s+/, "")); list.append(item); });
      els.readerBody.append(list); return;
    }
    const quoted = lines.length && lines.every((line) => line.startsWith(">"));
    const element = document.createElement(quoted ? "blockquote" : "p");
    appendLinkedText(element, quoted ? lines.map((line) => line.replace(/^>\s?/, "")).join("\n") : block);
    els.readerBody.append(element);
  });
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
  els.readerDate.textContent = ""; els.readerTags.innerHTML = ""; els.readerCleanup.classList.add("hidden");
  renderEmailBody("");
  try {
    const response = await fetch(`/api/cleanup/messages/${encodeURIComponent(id)}`);
    const item = await response.json();
    if (!response.ok) throw new Error(item.detail || "Não foi possível abrir a mensagem.");
    if (state.activeId !== id) return;
    const sender = item.from_name || item.from_email || "Remetente desconhecido";
    els.readerSubject.textContent = item.subject;
    els.readerAvatar.textContent = sender.trim().charAt(0).toUpperCase() || "?";
    els.readerMeta.textContent = item.from_name && item.from_email
      ? `${item.from_name} <${item.from_email}> · para ${item.account}` : `${sender} · para ${item.account}`;
    els.readerDate.textContent = formatDate(item.date);
    const tags = [categoryLabels[item.category] || item.category, priorityLabels[item.priority] || item.priority, item.has_attachment ? "Anexo" : null].filter(Boolean);
    els.readerTags.innerHTML = tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
    if (item.cleanup_candidate) {
      els.readerCleanup.textContent = `Sugestão de limpeza: ${item.cleanup_reason || "mensagem sem valor futuro identificada pela triagem."}`;
      els.readerCleanup.classList.remove("hidden");
    }
    renderEmailBody(item.body);
  } catch (error) { showToast(error.message, "error"); clearReader(); }
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
    const item = state.items.get(id);
    if (item) { item.cleanup_candidate = true; item.cleanup_reason = `Blacklist explícita de ${target}: ${payload.value}.`; }
    showToast(`${target === "sender" ? "Remetente" : "Domínio"} ${payload.value} adicionado à blacklist e às sugestões de limpeza.`, "success");
  } catch (error) { showToast(error.message, "error"); }
}

els.list.addEventListener("click", (event) => {
  const checkbox = event.target.closest(".message-checkbox");
  if (checkbox) {
    const complete = event.shiftKey
      ? selectRange(checkbox.dataset.id, checkbox.checked) : setSelected(checkbox.dataset.id, checkbox.checked);
    state.lastSelectedId = checkbox.dataset.id; updateSelectionUI();
    if (!complete) showToast(`Máximo de ${MAX_SELECTION} mensagens.`, "warning");
    return;
  }
  const card = event.target.closest("[data-card-id]"); if (card) openMessage(card.dataset.cardId);
});
els.list.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest("[data-card-id]"); if (card) { event.preventDefault(); openMessage(card.dataset.cardId); }
});
els.list.addEventListener("contextmenu", (event) => {
  const card = event.target.closest("[data-card-id]"); if (card) showContextMenu(event, card.dataset.cardId);
});

els.contextMenu.addEventListener("click", (event) => {
  const button = event.target.closest("[data-blacklist]"); if (button) addToBlacklist(button.dataset.blacklist);
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("#context-menu")) hideContextMenu();
  if (!event.target.closest("#filter-panel") && !event.target.closest("#filter-toggle")) closePopover("filter");
  if (!event.target.closest("#sort-panel") && !event.target.closest("#sort-toggle")) closePopover("sort");
});
window.addEventListener("resize", hideContextMenu);
$("#reader-back").addEventListener("click", () => els.reader.classList.remove("mobile-open"));

function resetAndLoad() { loadMessages({ reset: true }); }
let searchTimer;
els.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = els.search.value.trim(); resetAndLoad(); }, 280);
});

function toLocalISO(value) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function updateDates() {
  state.datePreset = els.datePreset.value;
  els.dateRange.classList.toggle("hidden", state.datePreset !== "custom");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const start = new Date(today);
  if (state.datePreset === "today") state.dateFrom = state.dateTo = toLocalISO(today);
  else if (state.datePreset === "yesterday") { start.setDate(start.getDate() - 1); state.dateFrom = state.dateTo = toLocalISO(start); }
  else if (state.datePreset === "last7") { start.setDate(start.getDate() - 6); state.dateFrom = toLocalISO(start); state.dateTo = toLocalISO(today); }
  else if (state.datePreset === "last30") { start.setDate(start.getDate() - 29); state.dateFrom = toLocalISO(start); state.dateTo = toLocalISO(today); }
  else if (state.datePreset === "custom") { state.dateFrom = els.dateFrom.value; state.dateTo = els.dateTo.value; }
  else state.dateFrom = state.dateTo = "";
}

function updateFilterCount() {
  const count = [state.account, state.category, state.priority, state.datePreset].filter(Boolean).length;
  els.filterCount.textContent = count; els.filterCount.classList.toggle("hidden", count === 0);
  els.filterToggle.classList.toggle("has-active-filters", count > 0);
}

function applyFilters() {
  state.account = els.account.value; state.category = els.category.value; state.priority = els.priority.value;
  updateDates(); updateFilterCount(); resetAndLoad();
}
[els.account, els.category, els.priority, els.datePreset].forEach((control) => control.addEventListener("change", applyFilters));
[els.dateFrom, els.dateTo].forEach((control) => control.addEventListener("change", () => { if (els.datePreset.value === "custom") applyFilters(); }));
$("#clear-filters").addEventListener("click", () => {
  [els.account, els.category, els.priority, els.datePreset, els.dateFrom, els.dateTo].forEach((control) => { control.value = ""; });
  applyFilters();
});

function closePopover(which) {
  const panel = which === "filter" ? els.filterPanel : els.sortPanel;
  const toggle = which === "filter" ? els.filterToggle : els.sortToggle;
  panel.classList.add("hidden"); toggle.setAttribute("aria-expanded", "false");
}

function togglePopover(which) {
  const panel = which === "filter" ? els.filterPanel : els.sortPanel;
  const toggle = which === "filter" ? els.filterToggle : els.sortToggle;
  const open = panel.classList.contains("hidden");
  closePopover(which === "filter" ? "sort" : "filter");
  panel.classList.toggle("hidden", !open); toggle.setAttribute("aria-expanded", String(open));
}
els.filterToggle.addEventListener("click", () => togglePopover("filter"));
els.sortToggle.addEventListener("click", () => togglePopover("sort"));
els.sortPanel.addEventListener("click", (event) => {
  const button = event.target.closest("[data-sort]"); if (!button) return;
  state.sort = button.dataset.sort; els.sortLabel.textContent = sortLabels[state.sort];
  els.sortPanel.querySelectorAll("[data-sort]").forEach((item) => item.classList.toggle("is-active", item === button));
  closePopover("sort"); resetAndLoad();
});

$("#mode-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-mode]"); if (!tab || tab.dataset.mode === state.mode) return;
  state.mode = tab.dataset.mode; updateModeTabs(); resetAndLoad();
});
els.selectAll.addEventListener("change", () => {
  const cards = [...els.list.querySelectorAll("[data-card-id]")];
  const select = els.selectAll.checked;
  let complete = true;
  cards.forEach((card) => { if (!setSelected(card.dataset.cardId, select)) complete = false; });
  updateSelectionUI();
  if (!complete) showToast(`Máximo de ${MAX_SELECTION} mensagens.`, "warning");
  else showToast(select
    ? `${cards.length} mensagem(ns) carregada(s) selecionada(s).`
    : "Mensagens carregadas desselecionadas.", "info");
});

els.loadMore.addEventListener("click", () => loadMessages());
new IntersectionObserver(([entry]) => {
  if (entry.isIntersecting && state.hasMore && !state.loading) loadMessages();
}, { root: els.results, rootMargin: "300px" }).observe(els.sentinel);

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
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { closeConfirmation(); hideContextMenu(); closePopover("filter"); closePopover("sort"); }
});
els.modalConfirm.addEventListener("click", async () => {
  if (!state.pendingAction || !state.selected.size) return;
  els.modalConfirm.disabled = true;
  try {
    const response = await fetch("/api/cleanup/bulk-action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: state.pendingAction, ids: [...state.selected], confirmed: true }) });
    const payload = await response.json(); if (!response.ok) throw new Error(payload.detail || "A ação falhou.");
    closeConfirmation();
    showToast(
      `${payload.succeeded} concluída(s)${payload.failed ? ` · ${payload.failed} falha(s)` : ""}`,
      payload.failed ? "warning" : "success",
    );
    await resetAndLoad();
  } catch (error) { showToast(error.message, "error"); } finally { els.modalConfirm.disabled = false; }
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
function showToast(message, type = "info") {
  const toastTypes = ["info", "success", "warning", "error"];
  const resolvedType = toastTypes.includes(type) ? type : "info";
  clearTimeout(toastTimer); els.toast.textContent = message;
  toastTypes.forEach((item) => els.toast.classList.toggle(`is-${item}`, item === resolvedType));
  els.toast.setAttribute("role", resolvedType === "error" ? "alert" : "status");
  els.toast.setAttribute("aria-live", resolvedType === "error" ? "assertive" : "polite");
  els.toast.classList.remove("hidden"); toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 5000);
}

applyTheme(localStorage.getItem("email-helper-theme") || "system");
updateModeTabs(); updateFilterCount(); loadMessages({ reset: true });
