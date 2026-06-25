const messages = document.getElementById("messages");
const form = document.getElementById("ask-form");
const input = document.getElementById("ask-input");
const askBtn = document.getElementById("ask-btn");
const logoutBtn = document.getElementById("logout-btn");
const featuredToggle = document.getElementById("featured-toggle");
const featuredPanel = document.getElementById("featured-panel");
const featuredClose = document.getElementById("featured-close");
const featuredList = document.getElementById("featured-list");

let threadId = null;
let streaming = false;

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
    tools: node.querySelector(".tools"),
    root: node,
  };
}

function setTools(view, names) {
  if (!names || !names.length) return;
  view.tools.hidden = false;
  view.tools.textContent = "Tools: " + names.join(", ");
}

function renderEventRow(ev) {
  const row = tmpl("event-row");
  row.querySelector(".event-title").textContent = ev.title || "Untitled";
  const meta = [ev.date, ev.time, ev.location].filter(Boolean).join(" · ");
  row.querySelector(".event-meta").textContent = meta;
  return row;
}

function addProposal(proposalId, events) {
  const card = tmpl("proposal-card");
  const list = card.querySelector(".proposal-events");
  events.forEach((ev) => list.appendChild(renderEventRow(ev)));

  const note = card.querySelector(".note-input");
  const approve = card.querySelector(".approve-btn");
  const reject = card.querySelector(".reject-btn");

  function settle(label) {
    approve.disabled = true;
    reject.disabled = true;
    note.disabled = true;
    card.classList.add("settled");
    const tag = document.createElement("div");
    tag.className = "proposal-result";
    tag.textContent = label;
    card.appendChild(tag);
  }

  approve.addEventListener("click", () => {
    settle("Approved");
    resume(proposalId, "approve", note.value.trim());
  });
  reject.addEventListener("click", () => {
    settle("Rejected");
    resume(proposalId, "reject", note.value.trim());
  });

  messages.appendChild(card);
  scrollDown();
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
  } else if (name === "proposal") {
    addProposal(payload.proposal_id, payload.events || []);
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
    if (!featuredPanel.hidden) loadFeatured();
  }
}

function ask(text) {
  send("ask/stream", { message: text, thread_id: threadId });
}

function resume(proposalId, decision, note) {
  send("resume", {
    thread_id: threadId,
    proposal_id: proposalId,
    decision,
    note: note || null,
  });
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

async function loadFeatured() {
  featuredList.textContent = "Loading…";
  try {
    const res = await fetch("editors-choice");
    if (res.status === 401) {
      window.location.assign("login.html");
      return;
    }
    const data = await res.json();
    renderFeatured(data.items || []);
  } catch {
    featuredList.textContent = "Could not load featured events.";
  }
}

function renderFeatured(items) {
  featuredList.textContent = "";
  if (!items.length) {
    featuredList.textContent = "No featured events yet.";
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "featured-row";

    const body = document.createElement("div");
    const title = document.createElement("div");
    title.className = "event-title";
    title.textContent = item.title || "Untitled";
    const meta = document.createElement("div");
    meta.className = "event-meta";
    meta.textContent = [item.note, item.selected_at].filter(Boolean).join(" · ");
    body.appendChild(title);
    body.appendChild(meta);

    const remove = document.createElement("button");
    remove.className = "ghost-btn";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeFeatured(item.event_id, row));

    row.appendChild(body);
    row.appendChild(remove);
    featuredList.appendChild(row);
  });
}

async function removeFeatured(eventId, row) {
  try {
    const res = await fetch("editors-choice/" + encodeURIComponent(eventId), {
      method: "DELETE",
    });
    if (res.ok) row.remove();
  } catch {
    /* leave the row in place on failure */
  }
}

function openFeatured() {
  featuredPanel.hidden = false;
  loadFeatured();
}

featuredToggle.addEventListener("click", () => {
  if (featuredPanel.hidden) openFeatured();
  else featuredPanel.hidden = true;
});
featuredClose.addEventListener("click", () => {
  featuredPanel.hidden = true;
});

logoutBtn.addEventListener("click", async () => {
  await fetch("logout", { method: "POST" }).catch(() => {});
  window.location.assign("login.html");
});
