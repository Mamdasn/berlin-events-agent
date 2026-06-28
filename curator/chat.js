const messages = document.getElementById("messages");
const form = document.getElementById("ask-form");
const input = document.getElementById("ask-input");
const askBtn = document.getElementById("ask-btn");
const logoutBtn = document.getElementById("logout-btn");
const featuredToggle = document.getElementById("featured-toggle");
const featuredPanel = document.getElementById("featured-panel");
const featuredClose = document.getElementById("featured-close");
const featuredApply = document.getElementById("featured-apply");
const featuredList = document.getElementById("featured-list");
const featuredReset = document.getElementById("featured-reset");
const featuredCount = document.getElementById("featured-count");
const mobileFeaturedCount = document.getElementById("mobile-featured-count");
const featuredStatus = document.getElementById("featured-status");
const sidebarOverlay = document.getElementById("sidebar-overlay");
const successSnackbar = document.getElementById("success-snackbar");
const workDate = document.getElementById("work-date");

function todayISO() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now - offset).toISOString().slice(0, 10);
}

let threadId = null;
let streaming = false;
let snackbarTimer = null;
let selectedDate = todayISO();
const surfacedEvents = new Map();
const surfacedCards = new Map();

const ICON_ADD = '<span class="material-symbols-outlined">add</span>';
const ICON_CHECK = '<span class="material-symbols-outlined">check</span>';
const ICON_REMOVE = '<span class="material-symbols-outlined">remove</span>';

function tmpl(id) {
  return document.getElementById(id).content.firstElementChild.cloneNode(true);
}

function scrollDown() {
  messages.scrollTop = messages.scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function mentionSpan(id, label) {
  const staged = stagedHas(Number(id));
  const tip = staged ? "Already in Editor's Choice" : "Add to Editor's Choice";
  return (
    '<span class="event-mention' + (staged ? " is-added" : "") +
    '" data-event-id="' + id + '" tabindex="0">' +
    '<span class="wc wc-left" aria-hidden="true">✦</span>' +
    '<span class="em-text">' + label + "</span>" +
    '<span class="wc wc-right" aria-hidden="true">✦</span>' +
    '<button class="em-add" type="button" data-event-id="' + id + '"' +
    (staged ? " disabled" : "") +
    ' title="' + tip + '" aria-label="' + tip + '">' +
    (staged ? ICON_CHECK : ICON_ADD) +
    "</button>" +
    "</span>"
  );
}

function mdInline(s) {
  return s
    .replace(/\[\[(\d+)\|([^\]|]+)\]\]/g, (_, id, label) => mentionSpan(id, label))
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\s][^*]*?)\*/g, "<em>$1</em>")
    .replace(/(^|[\s(])_([^_\s][^_]*?)_(?=[\s).,!?]|$)/g, "$1<em>$2</em>");
}

function renderMarkdown(text) {
  text = text || "";
  // Hold back an incomplete trailing mention marker so a chunk-split [[id|...
  // never flashes as literal text while streaming.
  const open = text.lastIndexOf("[[");
  if (open !== -1 && text.indexOf("]]", open) === -1) {
    text = text.slice(0, open);
  }
  const lines = escapeHtml(text).split("\n");
  let html = "";
  let list = null;
  const closeList = () => {
    if (list) {
      html += "</" + list + ">";
      list = null;
    }
  };
  for (const line of lines) {
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      closeList();
      html += "<h" + Math.min(m[1].length + 2, 6) + ">" + mdInline(m[2]) +
        "</h" + Math.min(m[1].length + 2, 6) + ">";
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (list !== "ul") {
        closeList();
        html += "<ul>";
        list = "ul";
      }
      html += "<li>" + mdInline(m[1]) + "</li>";
    } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      if (list !== "ol") {
        closeList();
        html += "<ol>";
        list = "ol";
      }
      html += "<li>" + mdInline(m[1]) + "</li>";
    } else if (/^\s*---+\s*$/.test(line)) {
      closeList();
      html += "<hr>";
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      html += "<p>" + mdInline(line) + "</p>";
    }
  }
  closeList();
  return html;
}

function addUserMessage(text) {
  const node = tmpl("msg-user");
  node.querySelector(".bubble").textContent = text;
  messages.appendChild(node);
  scrollDown();
}

function addAgentMessage() {
  const node = tmpl("msg-agent");
  const bubble = node.querySelector(".bubble");
  bubble.textContent = "Analyzing event context...";
  bubble.classList.add("is-placeholder");
  messages.appendChild(node);
  scrollDown();
  return {
    bubble,
    events: node.querySelector(".surface-events"),
    tools: node.querySelector(".tools"),
    root: node,
  };
}

function setTools(view, names) {
  if (!names || !names.length) return;
  view.tools.hidden = false;
  view.tools.textContent = "ReAct tools: " + names.join(", ");
}

function normalizeEvent(ev) {
  const rawId = ev && (ev.event_id ?? ev.id);
  const eventId = Number(rawId);
  if (!Number.isFinite(eventId)) return null;
  return {
    ...ev,
    id: eventId,
    event_id: eventId,
    reason: ev.reason || null,
  };
}

function eventMeta(ev) {
  return [ev.date, ev.time, ev.location, "id " + ev.event_id]
    .filter(Boolean)
    .join(" · ");
}

function updateSurfaceCardState(card, ev) {
  const add = card.querySelector(".event-add");
  const isStaged = stagedHas(ev.event_id);
  add.disabled = isStaged;
  add.innerHTML = isStaged ? ICON_CHECK : ICON_ADD;
  add.title = isStaged ? "Already in Editor's Choice" : "Add to Editor's Choice";
  card.classList.toggle("is-staged", isStaged);
}

function updateSurfaceCard(card, ev) {
  card.dataset.eventId = String(ev.event_id);
  card.querySelector(".event-title").textContent = ev.title || "Untitled";
  card.querySelector(".event-meta").textContent = eventMeta(ev);
  const reason = card.querySelector(".event-reason");
  if (ev.reason) {
    reason.hidden = false;
    reason.textContent = ev.reason;
    card.classList.add("is-recommended");
  } else {
    reason.hidden = true;
    reason.textContent = "";
  }
  updateSurfaceCardState(card, ev);
}

function createSurfaceCard(ev) {
  const card = tmpl("event-card");
  card.querySelector(".event-add").addEventListener("click", () => {
    stageAdd(surfacedEvents.get(ev.event_id) || ev);
  });
  updateSurfaceCard(card, ev);
  return card;
}

function renderSurfaceEvents(view, events) {
  const target = view.events;
  if (!target) return;
  (events || []).forEach((raw) => {
    const ev = normalizeEvent(raw);
    if (!ev) return;
    const existing = surfacedEvents.get(ev.event_id) || {};
    const merged = { ...existing, ...ev, reason: ev.reason || existing.reason || null };
    surfacedEvents.set(ev.event_id, merged);

    let card = surfacedCards.get(ev.event_id);
    if (!card) {
      card = createSurfaceCard(merged);
      surfacedCards.set(ev.event_id, card);
      target.appendChild(card);
    } else {
      updateSurfaceCard(card, merged);
    }
  });
  target.hidden = !target.children.length;
  scrollDown();
}

function applyRecommendations(view, payload) {
  if (payload.events) renderSurfaceEvents(view, payload.events);
  (payload.picks || []).forEach((pick) => {
    const eventId = Number(pick.event_id);
    if (!Number.isFinite(eventId)) return;
    const existing = surfacedEvents.get(eventId) || {
      id: eventId,
      event_id: eventId,
      title: "Event " + eventId,
    };
    const ev = {
      ...existing,
      reason: (pick.reason || existing.reason || "").trim() || null,
    };
    surfacedEvents.set(eventId, ev);
    if (!surfacedCards.has(eventId)) renderSurfaceEvents(view, [ev]);
    const card = surfacedCards.get(eventId);
    if (card) updateSurfaceCard(card, ev);

    const staged = desired.get(eventId);
    if (featuredDirty && staged && !staged.note && ev.reason) {
      staged.note = ev.reason;
      renderFeatured();
    }
  });
}

function updateSurfaceCards() {
  surfacedCards.forEach((card, eventId) => {
    const ev = surfacedEvents.get(eventId);
    if (ev) updateSurfaceCardState(card, ev);
  });
}

function updateMentionStates() {
  messages.querySelectorAll(".event-mention").forEach((mention) => {
    const eventId = Number(mention.dataset.eventId);
    const staged = stagedHas(eventId);
    const add = mention.querySelector(".em-add");
    mention.classList.toggle("is-added", staged);
    if (!add) return;
    add.disabled = staged;
    add.innerHTML = staged ? ICON_CHECK : ICON_ADD;
    const tip = staged ? "Already in Editor's Choice" : "Add to Editor's Choice";
    add.title = tip;
    add.setAttribute("aria-label", tip);
  });
}

messages.addEventListener("click", (e) => {
  const add = e.target.closest(".em-add");
  if (!add) return;
  const mention = add.closest(".event-mention");
  const eventId = Number(add.dataset.eventId);
  if (!Number.isFinite(eventId)) return;
  const fallback = {
    event_id: eventId,
    title: mention ? mention.querySelector(".em-text").textContent : "Event " + eventId,
  };
  stageAdd(surfacedEvents.get(eventId) || fallback);
});

function linkMentionCard(eventId, on) {
  messages
    .querySelectorAll('.surface-event[data-event-id="' + eventId + '"]')
    .forEach((card) => card.classList.toggle("is-linked", on));
}

messages.addEventListener("mouseover", (e) => {
  const mention = e.target.closest(".event-mention");
  if (mention && !mention.contains(e.relatedTarget)) {
    linkMentionCard(mention.dataset.eventId, true);
  }
});

messages.addEventListener("mouseout", (e) => {
  const mention = e.target.closest(".event-mention");
  if (mention && !mention.contains(e.relatedTarget)) {
    linkMentionCard(mention.dataset.eventId, false);
  }
});

async function readStream(res, view) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      handleEvent(chunk, view);
    }
  }
}

function handleEvent(chunk, view) {
  let name = "message";
  const dataLines = [];
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return;

  let payload;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }

  if (name === "token") {
    view.bubble.classList.remove("is-placeholder");
    view.raw = (view.raw || "") + (payload.text || "");
    view.bubble.innerHTML = renderMarkdown(view.raw);
    scrollDown();
  } else if (name === "status") {
    setTools(view, payload.tools_used);
  } else if (name === "events") {
    renderSurfaceEvents(view, payload.events || []);
  } else if (name === "propose") {
    applyRecommendations(view, payload);
  } else if (name === "done") {
    if (payload.thread_id) threadId = payload.thread_id;
  } else if (name === "error") {
    view.bubble.classList.remove("is-placeholder");
    view.bubble.classList.add("bubble-error");
    view.bubble.textContent = payload.message || "Something went wrong.";
  }
}

async function send(url, body) {
  if (streaming) return;
  streaming = true;
  askBtn.disabled = true;

  const view = addAgentMessage();
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.status === 401) {
      window.location.assign("login.html");
      return;
    }
    if (!res.ok || !res.body) {
      view.bubble.classList.add("bubble-error");
      view.bubble.textContent = "The agent is unavailable.";
      return;
    }
    await readStream(res, view);
  } catch {
    view.bubble.classList.add("bubble-error");
    view.bubble.textContent = "Connection dropped.";
  } finally {
    streaming = false;
    askBtn.disabled = false;
  }
}

function ask(text) {
  send("ask/stream", { message: text, thread_id: threadId, date: selectedDate });
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || streaming) return;
  addUserMessage(text);
  input.value = "";
  input.style.height = "auto";
  ask(text);
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

const desired = new Map();
let featuredDirty = false;

function setDirty(v) {
  featuredDirty = v;
  featuredApply.disabled = !v;
  if (featuredStatus) featuredStatus.textContent = v ? "Pending apply" : "Synced";
}

function dayPicks() {
  return [...desired.values()].filter((item) => item.date === selectedDate);
}

function updateFeaturedCount() {
  const dayCount = dayPicks().length;
  const count = String(dayCount);
  if (featuredCount) featuredCount.textContent = count;
  if (mobileFeaturedCount) {
    mobileFeaturedCount.textContent = count;
    mobileFeaturedCount.hidden = dayCount === 0;
  }
}

function stagedHas(eventId) {
  return desired.has(eventId);
}

async function loadFeatured() {
  try {
    const res = await fetch("editors-choice");
    if (res.status === 401) {
      window.location.assign("login.html");
      return;
    }
    const data = await res.json();
    desired.clear();
    (data.items || []).forEach((it) => {
      desired.set(it.event_id, {
        event_id: it.event_id,
        title: it.title,
        date: it.date,
        time: it.time,
        location: it.location,
        note: it.note || null,
      });
    });
    setDirty(false);
    renderFeatured();
    document.dispatchEvent(new CustomEvent("featured-changed"));
  } catch {
    featuredList.textContent = "Could not load featured events.";
  }
}

function stageAdd(ev) {
  const event = normalizeEvent(ev);
  if (!event) return;
  if (desired.has(event.event_id)) {
    showFeatured();
    updateSurfaceCards();
    return;
  }
  desired.set(event.event_id, {
    event_id: event.event_id,
    title: event.title,
    date: event.date,
    time: event.time,
    location: event.location,
    note: event.reason || null,
  });
  setDirty(true);
  showFeatured();
  document.dispatchEvent(new CustomEvent("featured-changed"));
}

function stageRemove(eventId) {
  if (desired.delete(eventId)) {
    setDirty(true);
    renderFeatured();
    document.dispatchEvent(new CustomEvent("featured-changed"));
  }
}

function renderFeatured() {
  featuredList.textContent = "";
  updateFeaturedCount();
  const picks = dayPicks();
  if (!picks.length) {
    const empty = document.createElement("div");
    empty.className = "featured-empty";
    empty.innerHTML = "<strong>No picks for this day</strong><span>Use the plus bubble in the AI feed to pin candidate events for the selected day.</span>";
    featuredList.appendChild(empty);
    return;
  }
  picks.forEach((item) => {
    const row = document.createElement("div");
    row.className = "featured-row";

    const body = document.createElement("div");
    const title = document.createElement("div");
    title.className = "event-title";
    title.textContent = item.title || "Untitled";
    const meta = document.createElement("div");
    meta.className = "event-meta";
    meta.textContent = [item.date, item.time, item.location, item.note]
      .filter(Boolean)
      .join(" · ");
    body.appendChild(title);
    body.appendChild(meta);

    const remove = document.createElement("button");
    remove.className = "bubble-btn";
    remove.type = "button";
    remove.innerHTML = ICON_REMOVE;
    remove.title = "Remove from Editor's Choice";
    remove.addEventListener("click", () => stageRemove(item.event_id));

    row.appendChild(body);
    row.appendChild(remove);
    featuredList.appendChild(row);
  });
}

async function applyFeatured() {
  if (!featuredDirty) return;
  featuredApply.disabled = true;
  const appliedCount = desired.size;
  const items = [...desired.values()].map((it) => ({
    event_id: it.event_id,
    note: it.note || null,
  }));
  try {
    const res = await fetch("editors-choice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    if (res.status === 401) {
      window.location.assign("login.html");
      return;
    }
    if (res.ok) {
      await loadFeatured();
      showSnackbar(
        "Changes applied",
        appliedCount + " curated event" + (appliedCount === 1 ? " was" : "s were") + " saved."
      );
    }
    else setDirty(true);
  } catch {
    setDirty(true);
  }
}

function showFeatured() {
  featuredPanel.hidden = false;
  if (sidebarOverlay && !window.matchMedia("(min-width: 901px)").matches) {
    sidebarOverlay.hidden = false;
  }
  renderFeatured();
}

function hideFeatured() {
  featuredPanel.hidden = true;
  if (sidebarOverlay) sidebarOverlay.hidden = true;
}

function showSnackbar(title, message) {
  if (!successSnackbar) return;
  successSnackbar.querySelector("h3").textContent = title;
  successSnackbar.querySelector("p").textContent = message;
  successSnackbar.hidden = false;
  requestAnimationFrame(() => successSnackbar.classList.add("is-open"));
  clearTimeout(snackbarTimer);
  snackbarTimer = setTimeout(() => {
    successSnackbar.classList.remove("is-open");
    setTimeout(() => {
      if (!successSnackbar.classList.contains("is-open")) successSnackbar.hidden = true;
    }, 260);
  }, 3200);
}

function resetFeatured() {
  if (!desired.size) return;
  desired.clear();
  setDirty(true);
  renderFeatured();
  document.dispatchEvent(new CustomEvent("featured-changed"));
}

featuredToggle.addEventListener("click", () => {
  if (featuredPanel.hidden) showFeatured();
  else hideFeatured();
});
featuredClose.addEventListener("click", () => {
  hideFeatured();
});
featuredApply.addEventListener("click", applyFeatured);
featuredReset.addEventListener("click", resetFeatured);
sidebarOverlay.addEventListener("click", hideFeatured);
document.addEventListener("featured-changed", updateSurfaceCards);
document.addEventListener("featured-changed", updateMentionStates);

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    const text = button.dataset.prompt || "";
    if (!text || streaming) return;
    addUserMessage(text);
    input.value = "";
    input.style.height = "auto";
    ask(text);
  });
});

if (workDate) {
  workDate.value = selectedDate;
  workDate.min = todayISO();
  workDate.addEventListener("change", () => {
    selectedDate = workDate.value || todayISO();
    renderFeatured();
  });
}

loadFeatured().then(() => {
  if (window.matchMedia("(min-width: 901px)").matches) showFeatured();
});

logoutBtn.addEventListener("click", async () => {
  await fetch("logout", { method: "POST" }).catch(() => {});
  window.location.assign("login.html");
});
