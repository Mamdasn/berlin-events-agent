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

let threadId = null;
let streaming = false;
const surfacedEvents = new Map();
const surfacedCards = new Map();

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

function mdInline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\s][^*]*?)\*/g, "<em>$1</em>")
    .replace(/(^|[\s(])_([^_\s][^_]*?)_(?=[\s).,!?]|$)/g, "$1<em>$2</em>");
}

function renderMarkdown(text) {
  const lines = escapeHtml(text || "").split("\n");
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
  messages.appendChild(node);
  scrollDown();
  return {
    bubble: node.querySelector(".bubble"),
    events: node.querySelector(".surface-events"),
    tools: node.querySelector(".tools"),
    root: node,
  };
}

function setTools(view, names) {
  if (!names || !names.length) return;
  view.tools.hidden = false;
  view.tools.textContent = "Tools: " + names.join(", ");
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
  return [ev.date, ev.time, ev.location].filter(Boolean).join(" · ");
}

function updateSurfaceCardState(card, ev) {
  const add = card.querySelector(".event-add");
  const isStaged = stagedHas(ev.event_id);
  add.disabled = isStaged;
  add.textContent = isStaged ? "✓" : "+";
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
  send("ask/stream", { message: text, thread_id: threadId });
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
  if (!desired.size) {
    featuredList.textContent = "No featured events yet.";
    return;
  }
  desired.forEach((item) => {
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
    remove.textContent = "−";
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
    if (res.ok) await loadFeatured();
    else setDirty(true);
  } catch {
    setDirty(true);
  }
}

function showFeatured() {
  featuredPanel.hidden = false;
  renderFeatured();
}

featuredToggle.addEventListener("click", () => {
  if (featuredPanel.hidden) showFeatured();
  else featuredPanel.hidden = true;
});
featuredClose.addEventListener("click", () => {
  featuredPanel.hidden = true;
});
featuredApply.addEventListener("click", applyFeatured);
document.addEventListener("featured-changed", updateSurfaceCards);

loadFeatured();

logoutBtn.addEventListener("click", async () => {
  await fetch("logout", { method: "POST" }).catch(() => {});
  window.location.assign("login.html");
});
