import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { toast, confirmDialog, promptDialog } from "./ui.js";

const CAPABILITIES = [
  { id: "scry", label: "Scry", hint: "multi-model fan-out + fused answer" },
  { id: "plan", label: "Plan", hint: "interactive clarifying interview → plan" },
  { id: "research", label: "Research", hint: "web-on deep research report" },
];

const state = {
  status: null,
  locations: [],
  activeLocationId: "contextless",
  conv: null, // { conversation, location, messages, runs, attachments }
  convList: [], // sibling conversations in the active location (for the picker)
  pendingAttachments: [],
  options: {
    capability: "scry",
    mode: "fusion",
    web_tools: null,
    effort: "",
    max_tool_calls: "",
    max_output_tokens: "",
    timeout: "",
    max_rounds: "",
  },
  polling: {},
};

const $ = (sel, root = document) => root.querySelector(sel);
const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
const esc = (s) =>
  (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

// --------------------------------------------------------------------------- //
// Boot
// --------------------------------------------------------------------------- //
async function init() {
  wireComposer();
  wireMobileNav();
  await refreshStatus();
  await refreshLocations();
  await openScratchOrNew("contextless");
}

// --------------------------------------------------------------------------- //
// Mobile navigation (sidebar becomes a slide-in drawer below 760px)
// --------------------------------------------------------------------------- //
function wireMobileNav() {
  const toggle = document.getElementById("nav-toggle");
  const backdrop = document.getElementById("nav-backdrop");
  if (toggle) toggle.addEventListener("click", toggleMobileNav);
  if (backdrop) backdrop.addEventListener("click", closeMobileNav);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("nav-open")) closeMobileNav();
  });
  // Crossing back to desktop must clear any open-drawer state, otherwise it
  // lingers (stale aria-expanded) and slams open if the window shrinks again.
  window.matchMedia("(min-width: 761px)").addEventListener("change", (e) => {
    if (e.matches) closeMobileNav();
  });
}
function openMobileNav() {
  document.body.classList.add("nav-open");
  const t = document.getElementById("nav-toggle");
  const b = document.getElementById("nav-backdrop");
  if (t) {
    t.setAttribute("aria-expanded", "true");
    t.setAttribute("aria-label", "Close navigation");
    t.textContent = "✕";
  }
  if (b) b.hidden = false;
}
function closeMobileNav() {
  document.body.classList.remove("nav-open");
  const t = document.getElementById("nav-toggle");
  const b = document.getElementById("nav-backdrop");
  if (t) {
    t.setAttribute("aria-expanded", "false");
    t.setAttribute("aria-label", "Open navigation");
    t.textContent = "☰";
  }
  if (b) b.hidden = true;
}
function toggleMobileNav() {
  if (document.body.classList.contains("nav-open")) closeMobileNav();
  else openMobileNav();
}

// Open an existing empty conversation in this location if there is one, else
// create a fresh one. Stops blank "Untitled" conversations piling up on every
// page load / "New chat" click.
async function openScratchOrNew(locationId) {
  let convs = [];
  try {
    const r = await api.locationConversations(locationId);
    convs = r.conversations || [];
  } catch (_e) {
    /* fall through and create one */
  }
  const empty = convs.find((c) => (c.message_count || 0) === 0);
  if (empty) {
    state.activeLocationId = locationId;
    await loadConversation(empty.id);
  } else {
    await newConversation(locationId);
  }
}

async function refreshStatus() {
  try {
    state.status = await api.status();
  } catch (e) {
    state.status = { ready: false, error: String(e), providers: [], panel: [] };
  }
  renderStatusBanner();
}

async function refreshLocations() {
  const r = await api.listLocations();
  state.locations = r.locations;
  renderSidebar();
}

// --------------------------------------------------------------------------- //
// Conversations
// --------------------------------------------------------------------------- //
async function newConversation(locationId) {
  const r = await api.createConversation(locationId, "Untitled");
  state.activeLocationId = locationId;
  state.pendingAttachments = [];
  await loadConversation(r.conversation.id);
  await refreshLocations();
}

async function loadConversation(cid) {
  // Stop polls from the conversation we're leaving — their interval callbacks
  // read/mutate state.conv and would otherwise corrupt the one we're loading.
  for (const id of Object.keys(state.polling)) stopPolling(id);
  state.conv = await api.getConversation(cid);
  state.activeLocationId = state.conv.location.id;
  state.pendingAttachments = [];
  // Load sibling conversations so the picker is available immediately (not only
  // after clicking a location row).
  try {
    const r = await api.locationConversations(state.activeLocationId);
    state.convList = r.conversations || [];
  } catch (_e) {
    state.convList = [];
  }
  renderSidebar();
  renderThread();
  // resume polling any in-flight runs. A "questions" run is already rendered by
  // renderThread() above and is waiting on the user — polling it would only
  // re-render and wipe in-progress answers, so we don't resume it here.
  for (const run of state.conv.runs || []) {
    if (["running", "ready"].includes(run.status)) startPolling(run.id);
  }
}

// --------------------------------------------------------------------------- //
// Sidebar
// --------------------------------------------------------------------------- //
function renderSidebar() {
  const side = $("#sidebar");
  const byType = { contextless: [], workspace: [], project: [] };
  for (const loc of state.locations) (byType[loc.type] || []).push(loc);

  const section = (title, locs, actions) => `
    <div class="side-section">
      <div class="side-head"><span>${title}</span>${actions || ""}</div>
      ${locs
        .map(
          (loc) => `
        <div class="loc ${loc.id === state.activeLocationId ? "active" : ""}" data-loc="${loc.id}"
             role="button" tabindex="0" aria-label="${esc(loc.name)}, ${loc.conversation_count || 0} conversations">
          <span class="loc-name">${esc(loc.name)}</span>
          <span class="loc-count">${loc.conversation_count || 0}</span>
        </div>`
        )
        .join("")}
    </div>`;

  side.innerHTML = `
    <div class="brand">scry<span>web</span></div>
    <button class="btn primary block" data-action="new-chat">+ New chat</button>
    ${section("Scratchpad", byType.contextless, "")}
    ${section(
      "Workspaces",
      byType.workspace,
      `<button class="mini" data-action="new-workspace" title="New workspace">+</button>`
    )}
    ${section(
      "Projects",
      byType.project,
      `<button class="mini" data-action="open-project" title="Open project">+</button>`
    )}
    <div class="side-foot">
      ${
        state.status
          ? `<span class="dot ${state.status.ready ? "ok" : "bad"}"></span>${
              state.status.ready ? "panel ready" : "panel not ready"
            }`
          : ""
      }
    </div>`;

  side.querySelectorAll(".loc").forEach((node) => {
    const go = () => onLocationClick(node.dataset.loc);
    node.addEventListener("click", go);
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        go();
      }
    });
  });
  side.querySelector('[data-action="new-chat"]').addEventListener("click", () => {
    closeMobileNav();
    openScratchOrNew(state.activeLocationId || "contextless");
  });
  side.querySelector('[data-action="new-workspace"]').addEventListener("click", onNewWorkspace);
  side.querySelector('[data-action="open-project"]').addEventListener("click", onOpenProject);
}

async function onLocationClick(locId) {
  closeMobileNav();
  state.activeLocationId = locId;
  const r = await api.locationConversations(locId);
  if (r.conversations.length) {
    await loadConversation(r.conversations[0].id);
  } else {
    await newConversation(locId);
  }
}

// Render the active location's conversations as a switcher in the thread header.
// Shown whenever there's more than one conversation to switch between.
function renderConvPicker() {
  const host = $("#conv-picker");
  if (!host) return;
  const convs = state.convList || [];
  if (convs.length <= 1) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = convs
    .map(
      (c) =>
        `<button class="chip ${state.conv && c.id === state.conv.conversation.id ? "active" : ""}" data-conv="${c.id}">${esc(c.title || "Untitled")}</button>`
    )
    .join("");
  host.querySelectorAll("[data-conv]").forEach((b) =>
    b.addEventListener("click", () => loadConversation(b.dataset.conv))
  );
}

async function onNewWorkspace() {
  const name = await promptDialog("Name your new workspace.", {
    title: "New workspace",
    placeholder: "e.g. payments-refactor",
    okText: "Create",
  });
  if (!name) return;
  try {
    const r = await api.createWorkspace(name);
    await refreshLocations();
    await onLocationClick(r.location.id);
  } catch (e) {
    toast("Could not create workspace: " + e.message, "error");
  }
}

async function onOpenProject() {
  const path = await promptDialog("Absolute path to a directory to open as a project.", {
    title: "Open project",
    placeholder: "/Users/you/code/my-project",
    okText: "Open",
  });
  if (!path) return;
  try {
    const r = await api.openProject(path);
    await refreshLocations();
    await onLocationClick(r.location.id);
  } catch (e) {
    toast("Could not open project: " + e.message, "error");
  }
}

// --------------------------------------------------------------------------- //
// Thread
// --------------------------------------------------------------------------- //
function renderThread() {
  const thread = $("#thread");
  if (!state.conv) {
    thread.innerHTML = "";
    return;
  }
  const { conversation, location, messages, runs } = state.conv;
  const runsById = {};
  for (const r of runs || []) runsById[r.id] = r;

  // Map assistant messages to their run for rich rendering.
  const blocks = [];
  for (const m of messages || []) {
    if (m.role === "user") {
      blocks.push(userBlock(m));
    } else {
      const run = m.run_id ? runsById[m.run_id] : null;
      blocks.push(assistantBlock(m, run));
      if (run) delete runsById[run.id];
    }
  }
  // Any non-terminal / message-less runs (in-flight) render at the end.
  for (const r of runs || []) {
    if (runsById[r.id] && r.status !== "done") blocks.push(runBlock(r));
  }

  thread.innerHTML = `
    <div class="thread-head">
      <div class="thread-title">${esc(conversation.title || "Untitled")}
        <span class="loc-tag">${esc(location.name)}${
          location.type === "contextless" ? "" : " · " + location.type
        }</span>
      </div>
      <div class="thread-actions">
        ${
          location.type === "contextless" && (runs || []).some((r) => r.status === "done")
            ? `<button class="btn ghost" data-action="upgrade">⬆ Promote to project</button>`
            : ""
        }
        <button class="btn ghost" data-action="export">⬇ Export</button>
      </div>
    </div>
    <div id="conv-picker" class="conv-picker"></div>
    <div class="messages">${blocks.join("") || emptyState()}</div>`;

  const up = thread.querySelector('[data-action="upgrade"]');
  if (up) up.addEventListener("click", onUpgrade);
  thread.querySelector('[data-action="export"]').addEventListener("click", onExport);
  renderConvPicker();
  wireRunControls(thread);
  thread.querySelector(".messages").scrollTop = thread.querySelector(".messages").scrollHeight;
  thread.scrollTop = thread.scrollHeight;
}

function emptyState() {
  return `<div class="empty">
    <h2>Ask the whole panel.</h2>
    <p>Pick a mode below — <b>Scry</b> for a fused multi-model answer,
    <b>Plan</b> for an interactive planning interview, <b>Research</b> for a web-on report.</p>
  </div>`;
}

function userBlock(m) {
  const atts = (m.attachments || [])
    .map((a) => `<span class="att">📎 ${esc(a.filename)}</span>`)
    .join("");
  return `<div class="msg user">
    <div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div>
    ${atts ? `<div class="atts">${atts}</div>` : ""}
  </div>`;
}

function assistantBlock(m, run) {
  if (run && run.status !== "done") return runBlock(run);
  return `<div class="msg assistant">
    <div class="role">${run ? capLabel(run.capability) : "Assistant"}</div>
    <div class="md">${renderMarkdown(m.content)}</div>
    ${run ? runDetails(run) : ""}
  </div>`;
}

function capLabel(cap) {
  return { scry: "Scry", plan: "Plan", research: "Research" }[cap] || "Assistant";
}

// --------------------------------------------------------------------------- //
// Run rendering (in-flight + completed details)
// --------------------------------------------------------------------------- //
function fmtElapsed(s) {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

function runBlock(run) {
  let body = "";
  if (run.status === "running") {
    // Elapsed in the *current* phase (updated_at is set when it entered running),
    // so a slow panel draft visibly ticks instead of looking frozen.
    const base = run.updated_at || run.created_at;
    const secs = base ? Math.max(0, Math.round(Date.now() / 1000 - base)) : null;
    const elapsed = secs != null ? ` <span class="elapsed">${fmtElapsed(secs)}</span>` : "";
    body = `<div class="working"><span class="spinner"></span> the panel is deliberating…${elapsed}</div>`;
  } else if (run.status === "questions") {
    body = questionCards(run);
  } else if (run.status === "ready") {
    body = `<div class="working"><span class="spinner"></span> panel is confident — drafting the plan…</div>`;
  } else if (run.status === "error") {
    body = `<div class="error">⚠ ${esc(run.error || "run failed")}</div>`;
  } else if (run.status === "done") {
    body = `<div class="md">${renderMarkdown(run.final || "")}</div>${runDetails(run)}`;
  }
  return `<div class="msg assistant" data-run="${run.id}">
    <div class="role">${capLabel(run.capability)}</div>${body}</div>`;
}

function questionCards(run) {
  const qs = run.questions || [];
  const cards = qs
    .map((q, idx) => {
      const opts = (q.options || [])
        .map(
          (o, oi) =>
            `<button type="button" class="opt" data-q="${idx}" data-opt="${oi}" aria-pressed="false">${esc(o)}</button>`
        )
        .join("");
      return `<div class="qcard" data-qidx="${idx}">
        <div class="q">${esc(q.q)}</div>
        ${q.why ? `<div class="why">${esc(q.why)}</div>` : ""}
        ${opts ? `<div class="opts">${opts}</div>` : ""}
        <input class="qinput" data-q="${idx}" placeholder="Your answer…" value="">
      </div>`;
    })
    .join("");
  return `<div class="questions" data-run="${run.id}">
    <div class="q-head">Round ${run.round || 1} · ${qs.length} question${
      qs.length === 1 ? "" : "s"
    }</div>
    ${cards}
    <div class="q-actions">
      <button class="btn primary" data-action="answer" data-run="${run.id}">Submit answers</button>
      <button class="btn ghost" data-action="answer-done" data-run="${run.id}">Skip — draft now</button>
    </div>
  </div>`;
}

function runDetails(run) {
  const parts = [];
  if (run.analysis) parts.push(consensusMap(run.analysis));
  if (run.responses && run.responses.length) parts.push(proposers(run.responses));
  const bars = [];
  if (run.cost) bars.push(costBar(run.cost));
  const arts = (run.artifact_paths || []).filter(Boolean);
  if (arts.length) bars.push(artifactBar(run, arts));
  return `<details class="run-details"><summary>Details</summary>
    ${parts.join("")}</details>${bars.join("")}`;
}

function consensusMap(a) {
  const sec = (title, items, cls) =>
    items && items.length
      ? `<div class="cm-row ${cls}"><span class="cm-label">${title}</span><ul>${items
          .map((i) => `<li>${esc(i)}</li>`)
          .join("")}</ul></div>`
      : "";
  const body =
    sec("Consensus", a.consensus, "ok") +
    sec("Contradictions", a.contradictions, "bad") +
    sec("Partial coverage", a.partial_coverage, "warn") +
    sec("Unique insights", a.unique_insights, "uniq") +
    sec("Blind spots", a.blind_spots, "warn");
  return body ? `<div class="consensus"><div class="cm-title">Consensus map</div>${body}</div>` : "";
}

function proposers(responses) {
  return `<div class="proposers"><div class="cm-title">Panel (${responses.length})</div>${responses
    .map(
      (r) => `<details class="proposer ${r.ok ? "" : "failed"}">
        <summary><span class="badge ${r.ok ? "ok" : "bad"}">${r.ok ? "ok" : "fail"}</span>
          ${esc(r.label)} ${r.seconds != null ? `<span class="secs">${r.seconds}s</span>` : ""}</summary>
        <div class="md">${renderMarkdown(r.content || r.error || "")}</div>
      </details>`
    )
    .join("")}</div>`;
}

function costBar(cost) {
  const usd =
    typeof cost.total_usd === "number" ? `$${cost.total_usd.toFixed(2)}` : "—";
  return `<div class="cost">
    <span>${cost.calls ?? "—"} calls</span>
    <span>${usd}</span>
    <span>${cost.seconds != null ? Math.round(cost.seconds) + "s" : "—"}</span>
  </div>`;
}

function artifactBar(run, arts) {
  return `<div class="artifacts">${arts
    .map(
      (p, i) => `<span class="artifact">
        <span class="afile" title="${esc(p)}">${esc(p.split("/").pop())}</span>
        <a class="mini" href="${api.downloadUrl(run.id, i)}" download>download</a>
        <button class="mini" data-action="reveal" data-run="${esc(run.id)}" data-index="${i}">reveal</button>
      </span>`
    )
    .join("")}</div>`;
}

function wireRunControls(root) {
  root.querySelectorAll('[data-action="answer"]').forEach((b) =>
    b.addEventListener("click", () => submitAnswers(b.dataset.run, false))
  );
  root.querySelectorAll('[data-action="answer-done"]').forEach((b) =>
    b.addEventListener("click", () => submitAnswers(b.dataset.run, true))
  );
  root.querySelectorAll(".opt").forEach((b) =>
    b.addEventListener("click", () => {
      const card = b.closest(".qcard");
      card.querySelectorAll(".opt").forEach((o) => {
        o.classList.remove("sel");
        o.setAttribute("aria-pressed", "false");
      });
      b.classList.add("sel");
      b.setAttribute("aria-pressed", "true");
      const input = card.querySelector(".qinput");
      input.value = b.textContent;
    })
  );
  root.querySelectorAll('[data-action="reveal"]').forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await api.reveal(b.dataset.run, b.dataset.index);
      } catch (e) {
        toast("Reveal failed: " + e.message, "error");
      }
    })
  );
}

async function submitAnswers(runId, done) {
  const node = $(`.questions[data-run="${runId}"]`);
  const payload = {};
  if (done) {
    payload.done = true;
  } else {
    if (!node) return; // thread re-rendered out from under us — nothing to submit
    const run = (state.conv.runs || []).find((r) => r.id === runId);
    const qs = (run && run.questions) || [];
    const answers = [];
    node.querySelectorAll(".qcard").forEach((card) => {
      const idx = +card.dataset.qidx;
      const val = card.querySelector(".qinput").value.trim();
      if (val && qs[idx]) answers.push({ q: qs[idx].q, a: val });
    });
    payload.answers = answers;
  }
  if (node) node.innerHTML = `<div class="working"><span class="spinner"></span> thinking…</div>`;
  await api.answerRun(runId, payload);
  startPolling(runId);
}

// --------------------------------------------------------------------------- //
// Polling
// --------------------------------------------------------------------------- //
function startPolling(runId) {
  if (state.polling[runId]) return;
  const ownerCid = state.conv && state.conv.conversation.id;
  state.polling[runId] = setInterval(async () => {
    // If the user navigated to a different conversation, this poll is stale.
    if (!state.conv || state.conv.conversation.id !== ownerCid) {
      stopPolling(runId);
      return;
    }
    let run;
    try {
      run = (await api.getRun(runId)).run;
    } catch (_e) {
      return;
    }
    // The conversation may have changed during the await — bail before touching
    // state.conv so a stale poll can't write a run into the wrong conversation.
    if (!state.conv || state.conv.conversation.id !== ownerCid) return;
    patchRun(run);
    if (run.status === "ready") {
      // auto-advance: the panel is confident → draft the plan
      stopPolling(runId);
      await api.answerRun(runId, { done: true });
      if (state.conv && state.conv.conversation.id === ownerCid) startPolling(runId);
      return;
    }
    if (["done", "error"].includes(run.status)) {
      stopPolling(runId);
      await loadConversation(state.conv.conversation.id);
    } else if (run.status === "questions") {
      // Render the questions once, then stop polling. The run won't change
      // server-side until the user answers, and re-rendering on every tick
      // would wipe answers they're typing into the cards.
      renderThread();
      stopPolling(runId);
    } else {
      renderThread();
    }
  }, 900);
}

function stopPolling(runId) {
  if (state.polling[runId]) {
    clearInterval(state.polling[runId]);
    delete state.polling[runId];
  }
}

function patchRun(run) {
  const runs = state.conv.runs || [];
  const idx = runs.findIndex((r) => r.id === run.id);
  if (idx >= 0) runs[idx] = run;
  else runs.push(run);
  state.conv.runs = runs;
}

// --------------------------------------------------------------------------- //
// Composer
// --------------------------------------------------------------------------- //
function wireComposer() {
  const cap = $("#cap-picker");
  cap.innerHTML = CAPABILITIES.map(
    (c) =>
      `<button class="cap ${c.id === state.options.capability ? "active" : ""}" data-cap="${c.id}" aria-pressed="${
        c.id === state.options.capability
      }" title="${c.hint}">${c.label}</button>`
  ).join("");
  cap.querySelectorAll(".cap").forEach((b) =>
    b.addEventListener("click", () => {
      state.options.capability = b.dataset.cap;
      cap.querySelectorAll(".cap").forEach((x) => {
        x.classList.remove("active");
        x.setAttribute("aria-pressed", "false");
      });
      b.classList.add("active");
      b.setAttribute("aria-pressed", "true");
      applyCapabilityUI(b.dataset.cap);
    })
  );

  const ta = $("#composer-input");
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("#send-btn").addEventListener("click", sendMessage);
  $("#adv-toggle").addEventListener("click", () => {
    $("#adv-panel").classList.toggle("open");
  });
  $("#attach-btn").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", onAttach);

  // advanced fields
  bindAdv("opt-mode", "mode");
  bindAdv("opt-web", "web_tools", true);
  bindAdv("opt-effort", "effort");
  bindAdv("opt-tool-calls", "max_tool_calls");
  bindAdv("opt-out-tokens", "max_output_tokens");
  bindAdv("opt-timeout", "timeout");
  bindAdv("opt-max-rounds", "max_rounds");

  applyCapabilityUI(state.options.capability);
}

// Keep the composer's options + placeholder honest about what each mode uses:
// Mode (fusion/synthesize) only affects Scry; Research always runs web-on
// (the engine forces it); Max plan rounds only applies to the Plan interview.
function applyCapabilityUI(cap) {
  const ta = $("#composer-input");
  const placeholders = {
    scry: "Ask the panel…  (⌘/Ctrl + Enter to send)",
    plan: "Describe what you want to plan…  (⌘/Ctrl + Enter to send)",
    research: "What should the panel research?  (⌘/Ctrl + Enter to send)",
  };
  if (placeholders[cap]) ta.placeholder = placeholders[cap];

  setOptEnabled("opt-mode", cap === "scry", cap === "scry" ? "" : "scry only");

  const web = document.getElementById("opt-web");
  if (web) {
    if (cap === "research") {
      web.value = "on";
      setOptEnabled("opt-web", false, "forced on for research");
    } else {
      web.value =
        state.options.web_tools === true
          ? "on"
          : state.options.web_tools === false
          ? "off"
          : "";
      setOptEnabled("opt-web", true, "");
    }
  }

  const roundsLabel = document.getElementById("opt-max-rounds")?.closest("label");
  if (roundsLabel) roundsLabel.style.display = cap === "plan" ? "" : "none";
}

function setOptEnabled(id, enabled, note = "") {
  const ctl = document.getElementById(id);
  if (!ctl) return;
  ctl.disabled = !enabled;
  const label = ctl.closest("label");
  if (!label) return;
  label.classList.toggle("disabled", !enabled);
  let hint = label.querySelector(".opt-note");
  if (note) {
    if (!hint) {
      hint = document.createElement("span");
      hint.className = "opt-note";
      label.appendChild(hint);
    }
    hint.textContent = note;
  } else if (hint) {
    hint.remove();
  }
}

function bindAdv(id, key, tri) {
  const node = document.getElementById(id);
  if (!node) return;
  node.addEventListener("change", () => {
    let v = node.value;
    if (tri) v = node.value === "" ? null : node.value === "on";
    state.options[key] = v;
  });
}

async function onAttach(e) {
  const file = e.target.files[0];
  if (!file || !state.conv) return;
  try {
    const r = await api.uploadAttachment(state.conv.conversation.id, file);
    state.pendingAttachments.push(r.attachment);
    renderPending();
  } catch (err) {
    toast("Upload failed: " + err.message, "error");
  }
  e.target.value = "";
}

function renderPending() {
  const host = $("#pending-atts");
  host.innerHTML = state.pendingAttachments
    .map((a) => `<span class="att">📎 ${esc(a.filename)}</span>`)
    .join("");
}

function collectOptions() {
  const o = { mode: state.options.mode };
  if (state.options.web_tools !== null) o.web_tools = state.options.web_tools;
  for (const k of ["effort", "max_tool_calls", "max_output_tokens", "timeout", "max_rounds"]) {
    const v = state.options[k];
    if (v !== "" && v != null) {
      o[k] = ["effort"].includes(k) ? v : Number(v);
    }
  }
  return o;
}

async function sendMessage() {
  const ta = $("#composer-input");
  const content = ta.value.trim();
  if (!content || !state.conv) return;
  if (state.status && !state.status.ready && !state.status.fake_engine) {
    const go = await confirmDialog("The panel isn't reporting ready. Send anyway?", {
      okText: "Send anyway",
    });
    if (!go) return;
  }
  const payload = {
    capability: state.options.capability,
    content,
    options: collectOptions(),
    attachment_ids: state.pendingAttachments.map((a) => a.id),
  };
  try {
    const r = await api.postMessage(state.conv.conversation.id, payload);
    // Clear the composer only once the message is safely accepted, so a failed
    // send never discards what the user typed (or their pending attachments).
    ta.value = "";
    state.pendingAttachments = [];
    renderPending();
    await loadConversation(state.conv.conversation.id);
    startPolling(r.run.id);
  } catch (e) {
    toast("Send failed — your message was kept. " + e.message, "error");
  }
}

// --------------------------------------------------------------------------- //
// Misc actions
// --------------------------------------------------------------------------- //
async function onUpgrade() {
  const name = await promptDialog("Promote this session into a new scry project.", {
    title: "Promote to project",
    placeholder: "project name",
    okText: "Promote",
  });
  if (!name) return;
  try {
    const r = await api.upgradeConversation(state.conv.conversation.id, name);
    await refreshLocations();
    await loadConversation(r.conversation_id);
    toast("Promoted to project at " + r.location.root_path, "success", 7000);
  } catch (e) {
    toast("Promote failed: " + e.message, "error");
  }
}

async function onExport() {
  const r = await api.exportConversation(state.conv.conversation.id);
  const blob = new Blob([r.markdown], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = r.filename;
  a.click();
  URL.revokeObjectURL(url);
}

function renderStatusBanner() {
  const banner = $("#banner");
  if (!state.status || state.status.ready) {
    banner.style.display = "none";
    return;
  }
  banner.style.display = "block";
  const missing = (state.status.providers || [])
    .filter((p) => !p.ok)
    .map((p) => `${p.name} (${p.detail})`)
    .join(", ");
  banner.innerHTML = state.status.has_config
    ? `⚠ Panel not ready: ${esc(missing || "providers unavailable")}. Run <code>scry --check</code>.`
    : `⚠ No scry config found. Run <code>scry init</code> in your terminal first, then reload.`;
}

window.addEventListener("DOMContentLoaded", init);
