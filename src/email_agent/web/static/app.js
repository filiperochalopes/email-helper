const state = {
  page: 1,
  mode: "candidates",
  query: "",
  account: "",
  category: "",
  hasMore: false,
  loading: false,
  selected: new Set(),
  manuallyDeselected: new Set(),
  items: new Map(),
  pendingAction: null,
};
const MAX_SELECTION = 200;

const els = {
  list: document.querySelector("#message-list"),
  loading: document.querySelector("#loading-state"),
  empty: document.querySelector("#empty-state"),
  results: document.querySelector("#results-section"),
  loadMore: document.querySelector("#load-more"),
  sentinel: document.querySelector("#load-sentinel"),
  total: document.querySelector("#total-count"),
  heroSelected: document.querySelector("#hero-selected-count"),
  barSelected: document.querySelector("#bar-selected-count"),
  actionBar: document.querySelector("#action-bar"),
  search: document.querySelector("#search-input"),
  account: document.querySelector("#account-filter"),
  category: document.querySelector("#category-filter"),
  modal: document.querySelector("#confirm-modal"),
  modalTitle: document.querySelector("#modal-title"),
  modalCopy: document.querySelector("#modal-copy"),
  modalIcon: document.querySelector("#modal-icon"),
  modalConfirm: document.querySelector("#modal-confirm"),
  toast: document.querySelector("#toast"),
};

const categoryLabels = {
  marketing: "Marketing",
  promocao: "Promoção",
  spam_suspeito: "Spam suspeito",
  followup_sem_acao: "Follow-up sem ação",
  ignorar: "Ignorar",
  noticia: "Notícia",
  documento: "Documento",
  documento_fiscal: "Documento fiscal",
  aguardando_resposta: "Aguardando resposta",
  importante_p0: "Importante P0",
  importante_p1: "Importante P1",
  revisar: "Revisar",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "Data desconhecida";
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

function cardTemplate(item) {
  const checked = state.selected.has(item.id);
  const sender = item.from_name || item.from_email || "Remetente desconhecido";
  const category = categoryLabels[item.category] || item.category || "Sem classificação";
  const confidence = Number.isFinite(item.confidence)
    ? `${Math.round(item.confidence * 100)}% confiança`
    : "não classificado";
  return `
    <article class="message-card" data-card-id="${escapeHtml(item.id)}" data-selected="${checked}">
      <div class="flex gap-3 sm:gap-4">
        <label class="mt-0.5 grid size-6 shrink-0 cursor-pointer place-items-center">
          <span class="sr-only">Selecionar ${escapeHtml(item.subject)}</span>
          <input class="message-checkbox size-5 cursor-pointer rounded-md border-stone-300 text-mint-600 accent-mint-600 focus:ring-mint-500" type="checkbox" data-id="${escapeHtml(item.id)}" ${checked ? "checked" : ""} />
        </label>
        <div class="min-w-0 flex-1">
          <div class="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-ink-950">${escapeHtml(sender)}</p>
              <p class="truncate text-xs text-stone-500">${escapeHtml(item.from_email)}</p>
            </div>
            <span class="shrink-0 text-xs text-stone-500">${escapeHtml(formatDate(item.date))}</span>
          </div>
          <h2 class="mt-3 text-base font-semibold leading-6 tracking-tight text-ink-950">${escapeHtml(item.subject)}</h2>
          <p class="mt-1 line-clamp-2 text-sm leading-6 text-ink-700">${escapeHtml(item.snippet || "Sem prévia disponível.")}</p>
          <div class="mt-4 flex flex-wrap items-center gap-2 text-xs">
            <span class="rounded-full bg-stone-100 px-2.5 py-1 font-medium text-ink-700">${escapeHtml(category)}</span>
            <span class="rounded-full bg-stone-100 px-2.5 py-1 text-stone-600">${escapeHtml(item.account)}</span>
            <span class="text-stone-500">${escapeHtml(confidence)}</span>
            ${item.has_attachment ? '<span class="inline-flex items-center gap-1 text-stone-500">Anexo</span>' : ""}
          </div>
          ${item.cleanup_reason ? `<p class="mt-3 border-l-2 border-mint-500/50 pl-3 text-xs leading-5 text-mint-700"><strong class="font-semibold">Por que foi sugerido:</strong> ${escapeHtml(item.cleanup_reason)}</p>` : ""}
        </div>
      </div>
    </article>`;
}

function updateSelectionUI() {
  const count = state.selected.size;
  els.heroSelected.textContent = count;
  els.barSelected.textContent = count;
  els.actionBar.classList.toggle("translate-y-full", count === 0);
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
    const active = tab.dataset.mode === state.mode;
    tab.classList.toggle("bg-white", active);
    tab.classList.toggle("font-medium", active);
    tab.classList.toggle("text-ink-950", active);
    tab.classList.toggle("shadow-sm", active);
    tab.classList.toggle("text-ink-700", !active);
  });
}

function populateAccounts(accounts) {
  const current = els.account.value;
  const known = new Set([...els.account.options].map((option) => option.value));
  accounts.forEach((account) => {
    if (known.has(account)) return;
    const option = document.createElement("option");
    option.value = account;
    option.textContent = account;
    els.account.append(option);
  });
  els.account.value = current;
}

async function loadMessages({ reset = false } = {}) {
  if (state.loading || (!reset && !state.hasMore && state.page > 1)) return;
  if (reset) {
    state.page = 1;
    state.hasMore = false;
    state.items.clear();
    state.selected.clear();
    state.manuallyDeselected.clear();
    els.list.innerHTML = "";
    els.loading.classList.remove("hidden");
    els.empty.classList.add("hidden");
    updateSelectionUI();
  }

  state.loading = true;
  els.results.setAttribute("aria-busy", "true");
  const params = new URLSearchParams({
    page: String(state.page),
    page_size: "40",
    mode: state.mode,
  });
  if (state.query) params.set("query", state.query);
  if (state.account) params.set("account", state.account);
  if (state.category) params.set("category", state.category);

  try {
    const response = await fetch(`/api/cleanup/messages?${params}`);
    if (!response.ok) throw new Error(`Falha ao carregar (${response.status})`);
    const payload = await response.json();
    els.loading.classList.add("hidden");
    els.total.textContent = new Intl.NumberFormat("pt-BR").format(payload.total);
    populateAccounts(payload.accounts);

    payload.items.forEach((item) => {
      state.items.set(item.id, item);
      if (
        item.cleanup_candidate &&
        !state.manuallyDeselected.has(item.id) &&
        state.selected.size < MAX_SELECTION
      ) {
        state.selected.add(item.id);
      }
      els.list.insertAdjacentHTML("beforeend", cardTemplate(item));
    });

    state.hasMore = payload.has_more;
    state.page += 1;
    els.loadMore.classList.toggle("hidden", !state.hasMore);
    els.empty.classList.toggle("hidden", state.items.size !== 0);
    updateSelectionUI();
  } catch (error) {
    els.loading.classList.add("hidden");
    showToast(error.message, true);
  } finally {
    state.loading = false;
    els.results.setAttribute("aria-busy", "false");
  }
}

function resetAndLoad() {
  loadMessages({ reset: true });
}

let searchTimer;
els.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = els.search.value.trim();
    resetAndLoad();
  }, 280);
});

els.account.addEventListener("change", () => {
  state.account = els.account.value;
  resetAndLoad();
});

els.category.addEventListener("change", () => {
  state.category = els.category.value;
  resetAndLoad();
});

document.querySelector("#mode-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-mode]");
  if (!tab || tab.dataset.mode === state.mode) return;
  state.mode = tab.dataset.mode;
  updateModeTabs();
  resetAndLoad();
});

els.list.addEventListener("change", (event) => {
  const checkbox = event.target.closest(".message-checkbox");
  if (!checkbox) return;
  const id = checkbox.dataset.id;
  if (checkbox.checked) {
    if (state.selected.size >= MAX_SELECTION) {
      checkbox.checked = false;
      showToast(`Selecione no máximo ${MAX_SELECTION} mensagens por ação.`, true);
      return;
    }
    state.selected.add(id);
    state.manuallyDeselected.delete(id);
  } else {
    state.selected.delete(id);
    state.manuallyDeselected.add(id);
  }
  updateSelectionUI();
});

document.querySelector("#select-visible").addEventListener("click", () => {
  state.items.forEach((_, id) => {
    if (state.selected.size >= MAX_SELECTION) return;
    state.selected.add(id);
    state.manuallyDeselected.delete(id);
  });
  updateSelectionUI();
});

els.loadMore.addEventListener("click", () => loadMessages());

const observer = new IntersectionObserver(
  ([entry]) => {
    if (entry.isIntersecting && state.hasMore && !state.loading) loadMessages();
  },
  { rootMargin: "400px" },
);
observer.observe(els.sentinel);

document.querySelectorAll(".bulk-trigger").forEach((button) => {
  button.addEventListener("click", () => openConfirmation(button.dataset.action));
});

function openConfirmation(action) {
  state.pendingAction = action;
  const count = state.selected.size;
  const isTrash = action === "trash";
  els.modalIcon.className = isTrash
    ? "grid size-11 place-items-center rounded-2xl bg-red-100 text-red-600"
    : "grid size-11 place-items-center rounded-2xl bg-mint-100 text-mint-700";
  els.modalIcon.innerHTML = isTrash
    ? '<svg viewBox="0 0 24 24" class="size-5" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg>'
    : '<svg viewBox="0 0 24 24" class="size-5" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 7h16v13H4zM3 4h18v3H3zm6 8h6"/></svg>';
  els.modalTitle.textContent = isTrash ? "Mover para a Lixeira?" : "Arquivar mensagens?";
  els.modalCopy.textContent = isTrash
    ? `${count} mensagem(ns) serão movidas para a Lixeira recuperável do provedor. Nada será expurgado.`
    : `${count} mensagem(ns) sairão da Inbox e irão para o arquivo nativo de cada provedor.`;
  els.modalConfirm.textContent = isTrash ? "Mover à Lixeira" : "Arquivar";
  els.modalConfirm.classList.toggle("bg-red-500", isTrash);
  els.modalConfirm.classList.toggle("bg-ink-950", !isTrash);
  els.modal.classList.remove("hidden");
  els.modal.classList.add("flex");
  els.modalConfirm.focus();
}

function closeConfirmation() {
  state.pendingAction = null;
  els.modal.classList.add("hidden");
  els.modal.classList.remove("flex");
}

document.querySelector("#modal-cancel").addEventListener("click", closeConfirmation);
els.modal.addEventListener("click", (event) => {
  if (event.target === els.modal) closeConfirmation();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeConfirmation();
});

els.modalConfirm.addEventListener("click", async () => {
  if (!state.pendingAction || state.selected.size === 0) return;
  els.modalConfirm.disabled = true;
  els.modalConfirm.textContent = "Aplicando…";
  try {
    const response = await fetch("/api/cleanup/bulk-action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: state.pendingAction,
        ids: [...state.selected],
        confirmed: true,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "A ação falhou.");
    closeConfirmation();
    showToast(
      `${payload.succeeded} concluída(s)${payload.failed ? ` · ${payload.failed} falha(s)` : ""}`,
      payload.failed > 0,
    );
    await loadMessages({ reset: true });
  } catch (error) {
    showToast(error.message, true);
  } finally {
    els.modalConfirm.disabled = false;
  }
});

let toastTimer;
function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.classList.toggle("border-red-200", isError);
  els.toast.classList.toggle("text-red-700", isError);
  els.toast.classList.remove("hidden");
  toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 5000);
}

updateModeTabs();
loadMessages({ reset: true });
