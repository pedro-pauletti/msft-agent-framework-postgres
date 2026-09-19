/* ==========================================================================
   Postgres Agent UI.
   No framework and no build step on purpose: the point of the sample is the
   agent, and a single readable file keeps the attention there.
   ========================================================================== */

const el = (id) => document.getElementById(id);

const ui = {
  transcript: el("transcript"),
  empty: el("empty"),
  suggestions: el("suggestions"),
  input: el("input"),
  send: el("send"),
  reset: el("reset"),
  banner: el("banner"),
  switch: el("switch"),
  subtitle: el("subtitle"),
  traceBody: el("trace-body"),
  traceSub: el("trace-sub"),
};

const SUGGESTIONS = [
  "Which critical alerts are open right now?",
  "Which open alert has the largest financial exposure? Join the alerts to the SLA sheet.",
  "Is any open alert explained by a planned maintenance window?",
  "Who is on call for the site with the most open alerts?",
];

const state = {
  implementation: "maf",
  config: {},
  turns: { maf: [], foundry: [] }, // one trace history per implementation
  selected: { maf: null, foundry: null },
  busy: false,
};

// ---------------------------------------------------------------- helpers --

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const label = (impl) => (impl === "maf" ? "Agent Framework" : "Foundry Agent Service");

const SQL_KEYWORDS =
  "SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP BY|ORDER BY|LIMIT|OFFSET|" +
  "INSERT INTO|VALUES|UPDATE|SET|DELETE|AND|OR|NOT|NULL|AS|COUNT|AVG|SUM|MIN|MAX|" +
  "DISTINCT|HAVING|CASE|WHEN|THEN|ELSE|END|ASC|DESC|IN|IS|LIKE|BETWEEN|WITH|EXPLAIN";

/**
 * Minimal SQL highlighting.
 *
 * Matching happens on the raw SQL and escaping happens per fragment afterwards.
 * Highlighting escaped text instead looks simpler but is wrong: the number rule
 * matches the "39" inside the &#39; entity of an escaped quote and breaks it.
 */
function highlightSql(sql) {
  const pattern = new RegExp(
    `('(?:[^']|'')*')|\\b(${SQL_KEYWORDS})\\b|\\b(\\d+(?:\\.\\d+)?)\\b`,
    "gi",
  );

  let out = "";
  let last = 0;
  let match;

  while ((match = pattern.exec(String(sql))) !== null) {
    out += esc(sql.slice(last, match.index));
    if (match[1]) out += `<span class="str">${esc(match[1])}</span>`;
    else if (match[2]) out += `<span class="kw">${esc(match[2])}</span>`;
    else out += `<span class="num">${esc(match[3])}</span>`;
    last = match.index + match[0].length;
  }

  return out + esc(sql.slice(last));
}

/**
 * Render the agent's answer. Models reply in markdown, and the two things they
 * reach for most here are tables and inline code - so those are supported and
 * everything else is left as plain text.
 */
function renderAnswer(text) {
  const blocks = [];
  const lines = String(text ?? "").split("\n");
  let table = null;

  const flush = () => {
    if (!table) return;
    const head = table.head
      .map((cell) => `<th>${inline(cell)}</th>`)
      .join("");
    const body = table.rows
      .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`)
      .join("");
    blocks.push(`<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`);
    table = null;
  };

  const cells = (line) =>
    line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((cell) => cell.trim());

  const isDivider = (line) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes("-");

  lines.forEach((line, index) => {
    const isRow = line.trim().startsWith("|") && line.includes("|");
    if (isRow && !table && isDivider(lines[index + 1] || "")) {
      table = { head: cells(line), rows: [] };
      return;
    }
    if (table && isDivider(line)) return;
    if (table && isRow) {
      table.rows.push(cells(line));
      return;
    }
    flush();
    blocks.push(inline(esc(line)));
  });
  flush();

  return blocks.join("\n").replace(/\n{3,}/g, "\n\n");
}

/** Inline markdown: `code` and **bold**. Input must already be escaped. */
function inline(text) {
  return String(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

// ------------------------------------------------------------- transcript --

function addMessage(role, html, className = "") {
  ui.empty?.remove();
  const node = document.createElement("div");
  node.className = `msg ${role} ${className}`.trim();
  node.innerHTML = html;
  ui.transcript.appendChild(node);
  scrollDown();
  return node;
}

function addChips(turn, index) {
  const chips = [];
  if (turn.usage?.total) chips.push(`${turn.usage.total} tokens`);
  if (turn.duration_ms) chips.push(`${(turn.duration_ms / 1000).toFixed(1)}s`);
  const toolCount = turn.tool_calls?.length || 0;
  chips.push(`${toolCount} tool call${toolCount === 1 ? "" : "s"}`);

  const node = document.createElement("div");
  node.className = "msg-meta";
  node.innerHTML = chips
    .map((chip) => `<span class="chip link">${esc(chip)}</span>`)
    .join("");
  node.onclick = () => selectTurn(index);
  ui.transcript.appendChild(node);
  scrollDown();
}

function scrollDown() {
  ui.transcript.scrollTop = ui.transcript.scrollHeight;
}

function showTyping() {
  const node = document.createElement("div");
  node.className = "typing";
  node.innerHTML = "<span></span><span></span><span></span>";
  ui.transcript.appendChild(node);
  scrollDown();
  return node;
}

// ------------------------------------------------------------ trace panel --

function renderTrace() {
  const turns = state.turns[state.implementation];
  const index = state.selected[state.implementation];
  const turn = index === null ? null : turns[index];

  ui.traceSub.textContent = `${label(state.implementation)} · what the agent actually did`;

  if (!turn) {
    ui.traceBody.innerHTML = `<div class="trace-empty">
      Run a turn and this panel fills with the tool calls, the SQL the model
      wrote, the token usage and the run metadata.
    </div>`;
    return;
  }

  ui.traceBody.innerHTML = [
    tokensHtml(turn),
    callsHtml(turn),
    metadataHtml(turn),
    turnsHtml(turns, index),
  ].join("");

  ui.traceBody.querySelectorAll(".call-head").forEach((head) => {
    head.onclick = () => head.parentElement.classList.toggle("open");
  });
  ui.traceBody.querySelectorAll(".turn-btn").forEach((button) => {
    button.onclick = () => selectTurn(Number(button.dataset.index));
  });
  ui.traceBody.scrollTop = 0;
}

function tokensHtml(turn) {
  const usage = turn.usage || {};
  const card = (value, text) => `
    <div class="token-card">
      <div class="value">${value === undefined ? "&mdash;" : esc(value)}</div>
      <div class="label">${text}</div>
    </div>`;

  const cached =
    usage.cached
      ? `<p class="token-note">${esc(usage.cached)} of the input tokens were served from cache.</p>`
      : "";

  return `<div class="tokens">
    ${card(usage.input, "Input")}
    ${card(usage.output, "Output")}
    ${card(usage.total, "Total")}
  </div>${cached}`;
}

function callsHtml(turn) {
  const calls = turn.tool_calls || [];
  if (!calls.length) {
    return `<div class="section">
      <h3>Tool calls <span class="count">0</span></h3>
      <div class="trace-empty" style="padding:8px 0">
        The model answered from the conversation alone, without touching the database.
      </div>
    </div>`;
  }

  const body = calls
    .map((call, index) => {
      let args;
      if (call.sql) {
        args = `<h4>SQL</h4><pre class="sql">${highlightSql(call.sql)}</pre>`;
      } else if (call.code) {
        args = `<h4>Python</h4><pre class="code">${esc(call.code)}</pre>`;
      } else {
        args = `<h4>Arguments</h4><pre>${esc(JSON.stringify(call.arguments, null, 2))}</pre>`;
      }
      const result = call.result
        ? `<h4>Result</h4><pre>${esc(call.result)}</pre>`
        : "";
      return `<div class="call ${index === 0 ? "open" : ""}">
        <div class="call-head">
          <span class="call-index">${index + 1}</span>
          <span class="call-name">${esc(call.name)}</span>
          <span class="caret">&#9656;</span>
        </div>
        <div class="call-body">${args}${result}</div>
      </div>`;
    })
    .join("");

  return `<div class="section">
    <h3>Tool calls <span class="count">${calls.length}</span></h3>
    ${body}
  </div>`;
}

function metadataHtml(turn) {
  const entries = Object.entries(turn.metadata || {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );
  entries.push(["Latency", `${turn.duration_ms} ms`]);

  const rows = entries
    .map(([key, value]) => `<div class="meta-row">
      <span class="k">${esc(key)}</span><span class="v">${esc(value)}</span>
    </div>`)
    .join("");

  return `<div class="section"><h3>Metadata</h3>${rows}</div>`;
}

function turnsHtml(turns, selected) {
  if (turns.length < 2) return "";
  const buttons = turns
    .map((turn, index) => `<button class="turn-btn ${index === selected ? "active" : ""}"
        data-index="${index}">
        <span class="q">${esc(turn.question)}</span>
        <span class="n">${turn.tool_calls?.length || 0}&nbsp;calls</span>
      </button>`)
    .reverse()
    .join("");

  return `<div class="section">
    <h3>Turns <span class="count">${turns.length}</span></h3>
    <div class="turns">${buttons}</div>
  </div>`;
}

function selectTurn(index) {
  state.selected[state.implementation] = index;
  renderTrace();
}

// ----------------------------------------------------------------- actions --

async function send() {
  const message = ui.input.value.trim();
  if (!message || state.busy) return;

  if (state.config[state.implementation]?.available === false) {
    showBanner(state.config[state.implementation].reason);
    return;
  }

  setBusy(true);
  ui.input.value = "";
  ui.input.style.height = "auto";
  addMessage("user", esc(message));

  const typing = showTyping();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ implementation: state.implementation, message }),
    });
    const data = await response.json();
    typing.remove();

    if (!response.ok) {
      addMessage("error", esc(data.detail || "Something went wrong."));
      return;
    }

    addMessage("agent", renderAnswer(data.text));

    const turns = state.turns[state.implementation];
    turns.push({ ...data, question: message });
    state.selected[state.implementation] = turns.length - 1;
    addChips(data, turns.length - 1);
    renderTrace();
  } catch (error) {
    typing.remove();
    addMessage("error", esc(`Could not reach the server: ${error.message}`));
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  state.busy = busy;
  ui.send.disabled = busy;
  ui.send.textContent = busy ? "Running…" : "Send";
  if (!busy) ui.input.focus();
}

async function resetConversation() {
  await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ implementation: state.implementation }),
  });
  state.turns[state.implementation] = [];
  state.selected[state.implementation] = null;
  renderImplementation();
}

function switchTo(implementation) {
  if (state.implementation === implementation || state.busy) return;
  state.implementation = implementation;
  [...ui.switch.children].forEach((button) =>
    button.classList.toggle("active", button.dataset.impl === implementation),
  );
  renderImplementation();
}

/** Each implementation keeps its own transcript, so switching is non-destructive. */
function renderImplementation() {
  const info = state.config[state.implementation] || {};
  ui.subtitle.textContent = info.available
    ? state.implementation === "maf"
      ? `${info.model} · MCP over stdio · ${info.database}`
      : `${info.model} · ${info.agent} · MCP over HTTPS`
    : "Not configured";

  ui.transcript.innerHTML = "";
  const turns = state.turns[state.implementation];

  if (!turns.length) {
    ui.transcript.innerHTML = `<div class="empty" id="empty">
      <h2>Ask the database something</h2>
      <p>The model writes the SQL itself. Every statement it runs shows up in the
         trace panel, along with token counts and the metadata for the turn.</p>
      <div class="suggestions"></div>
    </div>`;
    ui.empty = el("empty");
    mountSuggestions(ui.transcript.querySelector(".suggestions"));
  } else {
    ui.empty = null;
    turns.forEach((turn, index) => {
      addMessage("user", esc(turn.question));
      addMessage("agent", renderAnswer(turn.text));
      addChips(turn, index);
    });
  }

  showBanner(info.available === false ? info.reason : null);
  renderTrace();
}

function mountSuggestions(container) {
  if (!container) return;
  container.innerHTML = SUGGESTIONS.map((text) => `<button>${esc(text)}</button>`).join("");
  [...container.children].forEach((button) => {
    button.onclick = () => {
      ui.input.value = button.textContent;
      send();
    };
  });
}

function showBanner(message) {
  if (!message) {
    ui.banner.classList.remove("show");
    return;
  }
  ui.banner.textContent = message.split("\n")[0];
  ui.banner.classList.add("show");
}

// -------------------------------------------------------------- bootstrap --

async function init() {
  // Wire the controls up before the first await, so a fast typist cannot hit
  // Send during the config round trip and have nothing happen.
  ui.send.onclick = send;
  ui.reset.onclick = resetConversation;

  ui.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  ui.input.addEventListener("input", () => {
    ui.input.style.height = "auto";
    ui.input.style.height = `${Math.min(ui.input.scrollHeight, 180)}px`;
  });

  mountSuggestions(ui.suggestions);

  try {
    state.config = await (await fetch("/api/config")).json();
  } catch {
    state.config = {};
  }

  [...ui.switch.children].forEach((button) => {
    const impl = button.dataset.impl;
    const info = state.config[impl];
    if (info && info.available === false) {
      button.disabled = true;
      button.title = info.reason?.split("\n")[0] || "Not configured in .env";
    }
    button.onclick = () => switchTo(impl);
  });

  // Start on whichever implementation is actually usable.
  if (state.config.maf?.available === false && state.config.foundry?.available) {
    switchTo("foundry");
  } else {
    renderImplementation();
  }
}

init();
