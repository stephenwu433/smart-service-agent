const state = {
  conversationId: null,
  last: null,
  caseData: null,
  attempts: [],
  events: [],
  selectedEvent: null,
  selectedConversationId: null,
  agentView: null,
  transcript: [],
};

const panels = ["start", "ask", "guide", "risk", "agent"];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
  });
}

function $(id) {
  return document.getElementById(id);
}

function showError(message) {
  const banner = $("error-banner");
  if (!message) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  banner.hidden = false;
  banner.textContent = message;
}

function formatDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item.msg || item.detail || JSON.stringify(item))
      .join("; ");
  }
  return JSON.stringify(detail);
}

function showPanel(name) {
  document.querySelectorAll(".panel").forEach((node) => {
    node.classList.toggle("active", node.id === `panel-${name}`);
  });
  document.querySelectorAll(".panel-nav button").forEach((node) => {
    node.classList.toggle("active", node.dataset.panel === name);
  });
  if (window.matchMedia("(min-width: 1100px)").matches) {
    document.querySelectorAll(".panel").forEach((node) => node.classList.add("active"));
  }
  history.replaceState(null, "", `#${name}`);
}

function panelForState(apiState) {
  if (apiState === "ASK" || apiState === "RESOLVE") return "ask";
  if (apiState === "GUIDE") return "guide";
  if (apiState === "BLOCK" || apiState === "HANDOFF") return "risk";
  return "start";
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let body = {};
  if (text) {
    try {
      body = JSON.parse(text);
    } catch (_error) {
      body = { detail: text };
    }
  }
  if (!response.ok) {
    throw new Error(formatDetail(body.detail) || text || response.statusText);
  }
  return body;
}

function renderList(node, items, emptyText) {
  if (!items || items.length === 0) {
    node.innerHTML = `<li>${escapeHtml(emptyText)}</li>`;
    return;
  }
  node.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function currentFacts() {
  const revisions = state.caseData?.revisions || [];
  const latest = revisions[revisions.length - 1];
  if (!latest) return [];
  return Object.entries(latest.facts || {}).map(([key, value]) => `${key}：${value}`);
}

function currentAttempt() {
  const fromResponse = state.last?.next_attempt;
  if (fromResponse) return fromResponse;
  const id = state.last?.next_attempt_id;
  return state.attempts.find((item) => item.attempt_id === id && item.status !== "withdrawn");
}

function renderTranscript() {
  $("transcript").innerHTML = state.transcript
    .map((item) => `<li><b>${escapeHtml(item.role)}</b> ${escapeHtml(item.text)}</li>`)
    .join("");
}

function renderSession() {
  const last = state.last;
  const apiState = last?.state || "未开始";
  $("session-meta").textContent = state.conversationId
    ? `会话 ${state.conversationId} · ${apiState}`
    : "尚未创建会话";
  $("live-state").textContent = `状态：${apiState}${last?.risk_lock ? " · 风险锁定" : ""}`;
  $("ask-state").textContent = last
    ? `${apiState}${last.confirmation_required ? " · 需要确认" : ""}`
    : "等待开始";
  renderList(
    $("missing-list"),
    last?.reasons?.length ? last.reasons : last ? [] : null,
    "暂无待确认项",
  );
  if (last?.confirmation_required) {
    $("missing-list").innerHTML = `<li>${escapeHtml(last.message)}</li>`;
  }
  renderList($("fact-list"), currentFacts(), "还没有已确认事实");
  const attempt = currentAttempt();
  if (attempt && last?.state === "GUIDE") {
    $("current-step").innerHTML = `
      <h3>当前一步</h3>
      <p>${escapeHtml(attempt.instructions || attempt.recommendation)}</p>
      <p><b>观察：</b>${escapeHtml(attempt.observation_target || "")}</p>
      <p><b>停止条件：</b>${escapeHtml(attempt.exit_condition || "")}</p>
    `;
  } else if (last?.message) {
    $("current-step").innerHTML = `<h3>当前一步</h3><p>${escapeHtml(last.message)}</p>`;
  }
  renderList(
    $("evidence-list"),
    (last?.evidence || []).map((item) => `${item.knowledge_id}：${item.excerpt}`),
    "本轮没有可展示的知识依据",
  );
  $("attempt-list").innerHTML = state.attempts.length
    ? state.attempts
        .map((item) => {
          const mark = item.status === "withdrawn" ? "已撤回" : item.execution_status;
          return `<li>${escapeHtml(mark)} · ${escapeHtml(item.recommendation)}${
            item.observation ? ` · ${escapeHtml(item.observation)}` : ""
          }</li>`;
        })
        .join("")
    : "<li>还没有历史尝试</li>";
  $("risk-message").innerHTML = last?.message
    ? `<p>${escapeHtml(last.message)}</p>`
    : "<p>出现鼓包、冒烟、异味、进液或异常发热后，这里会停止排障。</p>";
  $("resolve-actions").classList.toggle(
    "visible",
    Boolean(last?.confirmation_required && last?.state === "GUIDE"),
  );
  renderTranscript();
}

async function refreshSide() {
  if (!state.conversationId) {
    renderSession();
    return;
  }
  try {
    state.caseData = await api(`/v1/conversations/${state.conversationId}/case`);
  } catch (_error) {
    state.caseData = null;
  }
  try {
    state.attempts = await api(`/v1/conversations/${state.conversationId}/attempts`);
  } catch (_error) {
    state.attempts = [];
  }
  renderSession();
}

function applyResponse(body, userText) {
  if (body.conversation_id) state.conversationId = body.conversation_id;
  if (body.state) {
    state.last = body;
    if (userText) state.transcript.push({ role: "用户", text: userText });
    if (body.message) state.transcript.push({ role: "系统", text: body.message });
    showPanel(panelForState(body.state));
  }
}

function resetSession() {
  state.conversationId = null;
  state.last = null;
  state.caseData = null;
  state.attempts = [];
  state.selectedEvent = null;
  state.selectedConversationId = null;
  state.agentView = null;
  state.transcript = [];
  showError("");
  $("start-message").value = "";
  $("ask-message").value = "";
  $("observe-message").value = "";
  renderSession();
  showPanel("start");
}
  const message = text.trim();
  if (!message) return;
  showError("");
  const path = state.conversationId
    ? `/v1/conversations/${state.conversationId}/messages`
    : "/v1/conversations";
  try {
    const body = await api(path, {
      method: "POST",
      body: JSON.stringify({ message, product: "Anker 737 Power Bank A1289" }),
    });
    applyResponse(body, message);
    if (switchTo) showPanel(switchTo);
    await refreshSide();
  } catch (error) {
    showError(error.message);
    state.transcript.push({ role: "系统", text: error.message });
    renderTranscript();
  }
}

async function reportAttempt(kind) {
  const observation = $("observe-message").value.trim();
  const attempt = currentAttempt();
  if (attempt && state.conversationId) {
    const payload = {
      execution_status: kind === "improved" || kind === "executed" ? "executed" : kind,
      observation:
        kind === "executed" || kind === "improved" ? observation || "已执行并观察" : observation || null,
      skip_reason: kind === "executed" || kind === "improved" ? null : observation || kind,
      outcome: kind === "improved" ? "improved" : kind === "executed" ? "unchanged" : "unknown",
    };
    try {
      await api(`/v1/conversations/${state.conversationId}/attempts/${attempt.attempt_id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
    } catch (_error) {
      /* 主路径仍用消息推进编排器 */
    }
  }
  const defaults = {
    executed: "已按当前一步执行并观察",
    improved: "已经恢复充电",
    skipped: "先跳过这一步",
    skipped_unavailable: "没有其他充电器或线材",
  };
  await sendMessage(observation || defaults[kind] || "", { switchTo: "guide" });
}

async function decideHandoff(accepted) {
  if (!state.conversationId) return;
  showError("");
  try {
    const body = await api(`/v1/conversations/${state.conversationId}/handoff`, {
      method: "POST",
      body: JSON.stringify({
        accepted,
        idempotency_key: `ui-${state.conversationId}`,
      }),
    });
    if (body.state || body.event_id) {
      if (body.event_id) {
        state.transcript.push({
          role: "系统",
          text: `已创建模拟人工事件 ${body.event_id}，不是真实安克工单。`,
        });
        showPanel("agent");
        await loadQueue();
      } else {
        applyResponse(body, accepted ? "同意转人工" : "暂不转人工");
      }
    }
    await refreshSide();
  } catch (error) {
    showError(error.message);
    state.transcript.push({ role: "系统", text: error.message });
    renderTranscript();
  }
}

function renderQueue() {
  $("event-queue").innerHTML = state.events.length
    ? state.events
        .map(
          (item) =>
            `<li><button data-event="${escapeHtml(item.event_id)}" data-conversation="${escapeHtml(
              item.conversation_id,
            )}">${escapeHtml(item.status)} · ${escapeHtml(item.reason)}</button></li>`,
        )
        .join("")
    : "<li>队列为空</li>";
}

function renderAgentDetail() {
  const view = state.agentView;
  if (!view) return;
  const pack = view.handoff_package || {};
  const facts = (pack.confirmed_facts || []).map(escapeHtml).join("</li><li>");
  const attempts = (pack.attempts || pack.executed_attempts || [])
    .map((item) => escapeHtml(item.recommendation || JSON.stringify(item)))
    .join("</li><li>");
  const knowledge = (pack.knowledge_refs || [])
    .map((item) => escapeHtml(`${item.knowledge_id}：${item.excerpt || item.source || ""}`))
    .join("</li><li>");
  $("agent-detail").innerHTML = `
    <div class="card">
      <h3>原话</h3>
      <p>${escapeHtml((pack.original_messages || []).join(" / "))}</p>
    </div>
    <div class="card">
      <h3>事实与更正</h3>
      <ul><li>${facts || "无"}</li></ul>
    </div>
    <div class="card">
      <h3>尝试及结果</h3>
      <ul><li>${attempts || "无"}</li></ul>
    </div>
    <div class="card">
      <h3>知识依据</h3>
      <ul><li>${knowledge || "无"}</li></ul>
    </div>
    <div class="card">
      <h3>升级原因 / 待处理问题</h3>
      <p>${escapeHtml(pack.suggested_next_step || pack.summary || "")}</p>
      <p>未排除：${escapeHtml((pack.unresolved_items || pack.untested_items || []).join("，") || "无")}</p>
    </div>
  `;
}

async function loadQueue() {
  state.events = await api("/v1/agent/events");
  renderQueue();
}

async function openEvent(eventId, conversationId) {
  state.selectedEvent = eventId;
  state.selectedConversationId = conversationId;
  state.agentView = await api(`/v1/agent/conversations/${conversationId}`);
  renderAgentDetail();
}

function bind() {
  document.querySelectorAll(".panel-nav button").forEach((button) => {
    button.addEventListener("click", () => {
      showPanel(button.dataset.panel);
      const target = document.getElementById(`panel-${button.dataset.panel}`);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  document.querySelectorAll(".stories button").forEach((button) => {
    button.addEventListener("click", () => {
      $("start-message").value = button.dataset.seed;
      showPanel("start");
    });
  });
  $("start-send").addEventListener("click", () => sendMessage($("start-message").value));
  $("new-session").addEventListener("click", () => resetSession());
  $("ask-send").addEventListener("click", () => sendMessage($("ask-message").value));
  $("ask-unknown").addEventListener("click", () => sendMessage("不确定"));
  $("ask-safe").addEventListener("click", () => sendMessage("没有"));
  document.querySelectorAll("[data-outcome]").forEach((button) => {
    button.addEventListener("click", () => reportAttempt(button.dataset.outcome));
  });
  $("confirm-stable").addEventListener("click", () => sendMessage("持续有输入，没有中断"));
  $("confirm-unstable").addEventListener("click", () => sendMessage("又变成 0W 了"));
  $("handoff-yes").addEventListener("click", () => decideHandoff(true));
  $("handoff-no").addEventListener("click", () => decideHandoff(false));
  $("refresh-queue").addEventListener("click", () => loadQueue());
  $("event-queue").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-event]");
    if (!button) return;
    openEvent(button.dataset.event, button.dataset.conversation);
  });
  $("agent-action").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.selectedEvent) return;
    showError("");
    try {
      await api(`/v1/agent/events/${state.selectedEvent}/actions`, {
        method: "POST",
        body: JSON.stringify({
          action: $("agent-action-type").value,
          parameters: { note: $("agent-note").value },
        }),
      });
      await loadQueue();
      if (state.selectedConversationId) {
        await openEvent(state.selectedEvent, state.selectedConversationId);
      }
    } catch (error) {
      showError(error.message);
    }
  });

  const hash = (location.hash || "").replace("#", "");
  if (location.pathname.includes("/workspace/agent") || hash === "agent") {
    showPanel("agent");
    loadQueue();
  } else if (panels.includes(hash)) {
    showPanel(hash);
  } else {
    showPanel("start");
  }
}

bind();
renderSession();
