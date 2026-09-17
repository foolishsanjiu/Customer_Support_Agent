"use strict";

const state = {
  token: "",
  customerId: null,
  selectedTicketId: null,
  currentRunId: null,
  streamController: null,
};

const byId = (id) => document.getElementById(id);
const terminalStatuses = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const labels = {
  OPEN: "处理中",
  PROCESSING: "处理中",
  WAITING_APPROVAL: "等待审批",
  RESOLVED: "已解决",
  ESCALATED: "已转人工",
  FAILED: "失败",
  PENDING: "等待运行",
  RUNNING: "Agent 正在处理",
  CANCEL_REQUESTED: "正在取消",
  CANCELLED: "已取消",
  RESUME_PENDING: "等待恢复",
  RECOVERY_REQUIRED: "需要人工恢复",
  SUCCEEDED: "处理完成",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function decodeIdentity(token) {
  try {
    const segment = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const encoded = segment.padEnd(Math.ceil(segment.length / 4) * 4, "=");
    const payload = JSON.parse(atob(encoded));
    return {
      role: String(payload.role || "").toUpperCase(),
      customerId: Number(payload.customer_id),
    };
  } catch (_) {
    return { role: "", customerId: Number.NaN };
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      Authorization: `Bearer ${state.token}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      message = data.detail || data.error?.message || message;
    } catch (_) {
      // Use the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

function showNotice(message) {
  const notice = byId("notice");
  notice.textContent = message;
  notice.hidden = !message;
}

function setConnected(connected) {
  byId("connection-dot").classList.toggle("connected", connected);
  byId("connection-label").textContent = connected
    ? `客户 #${state.customerId}`
    : "未连接";
  byId("disconnect").hidden = !connected;
  byId("welcome").hidden = connected;
  byId("chat-shell").hidden = !connected;
}

function disconnect() {
  state.streamController?.abort();
  state.token = "";
  state.customerId = null;
  state.selectedTicketId = null;
  state.currentRunId = null;
  state.streamController = null;
  byId("token").value = "";
  setConnected(false);
  showNotice("");
}

function showTicketForm(show) {
  byId("ticket-form").hidden = !show;
  if (show) byId("subject").focus();
}

async function loadTickets(selectId = null) {
  const tickets = await api("/api/v1/chat/tickets?limit=50");
  const container = byId("ticket-list");
  if (!tickets.length) {
    container.replaceChildren(
      element("p", "empty-state", "还没有工单。新建一个问题开始对话。"),
    );
    clearConversation();
    return;
  }
  const fragment = document.createDocumentFragment();
  tickets.forEach((ticket) => {
    const button = element("button", "ticket-item");
    button.type = "button";
    button.dataset.ticketId = String(ticket.id);
    button.classList.toggle("active", ticket.id === state.selectedTicketId);
    button.append(
      element("strong", "", ticket.subject),
      element(
        "span",
        "",
        `#${ticket.id} · ${labels[ticket.status] || ticket.status} · ${formatDate(ticket.updated_at)}`,
      ),
    );
    button.addEventListener("click", () => selectTicket(ticket.id));
    fragment.append(button);
  });
  container.replaceChildren(fragment);
  const target = selectId || state.selectedTicketId || tickets[0].id;
  await selectTicket(target);
}

function clearConversation() {
  state.selectedTicketId = null;
  byId("ticket-meta").textContent = "NO TICKET SELECTED";
  byId("ticket-subject").textContent = "选择一个工单";
  byId("ticket-status").hidden = true;
  byId("message").disabled = true;
  byId("send-message").disabled = true;
  const empty = element("div", "conversation-empty");
  empty.append(
    element("span", "", "RX"),
    element("p", "", "选择已有工单，或新建一个问题开始对话。"),
  );
  byId("messages").replaceChildren(empty);
}

async function selectTicket(ticketId) {
  const ticket = await api(`/api/v1/chat/tickets/${ticketId}`);
  state.selectedTicketId = ticket.id;
  byId("ticket-meta").textContent = `TICKET #${ticket.id} · ${ticket.category}`;
  byId("ticket-subject").textContent = ticket.subject;
  const status = byId("ticket-status");
  status.textContent = labels[ticket.status] || ticket.status;
  status.hidden = false;
  renderMessages(ticket.messages);
  const disabled = state.currentRunId !== null;
  byId("message").disabled = disabled;
  byId("send-message").disabled = disabled;
  document.querySelectorAll(".ticket-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.ticketId === String(ticket.id));
  });
}

function renderMessages(messages) {
  const container = byId("messages");
  if (!messages.length) {
    const empty = element("div", "conversation-empty");
    empty.append(
      element("span", "", "RX"),
      element("p", "", "补充问题后，Agent 会在这里回复。"),
    );
    container.replaceChildren(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  messages.forEach((message) => {
    const role = message.sender_type.toLowerCase();
    const row = element("article", `message ${role}`);
    const bubble = element("div", "bubble");
    bubble.append(
      element("p", "", message.content),
      element("small", "", `${message.sender_type} · ${formatDate(message.created_at)}`),
    );
    row.append(bubble);
    fragment.append(row);
  });
  container.replaceChildren(fragment);
  container.scrollTop = container.scrollHeight;
}

function setRunStatus(status, node = "") {
  const banner = byId("run-banner");
  banner.hidden = false;
  byId("run-status").textContent = labels[status] || status;
  byId("run-node").textContent = node ? `当前节点：${node}` : "";
  byId("cancel-run").hidden = terminalStatuses.has(status);
}

async function handleRunEvent(event) {
  setRunStatus(event.status, event.current_node);
  if (event.status === "WAITING_APPROVAL") {
    showNotice("这项操作需要经理审批。审批完成后任务会自动继续。" );
  }
  if (!event.terminal) return false;
  state.currentRunId = null;
  state.streamController = null;
  byId("message").disabled = false;
  byId("send-message").disabled = false;
  byId("cancel-run").hidden = true;
  if (event.status === "SUCCEEDED") showNotice("");
  await selectTicket(state.selectedTicketId);
  await loadTickets(state.selectedTicketId);
  return true;
}

async function followRun(runId) {
  state.streamController?.abort();
  const controller = new AbortController();
  state.streamController = controller;
  let terminal = false;
  try {
    const response = await fetch(`/api/v1/agent-runs/${runId}/events`, {
      headers: {
        Authorization: `Bearer ${state.token}`,
        Accept: "text/event-stream",
      },
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error(`状态流连接失败（${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!terminal) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)[0].length;
        buffer = buffer.slice(boundary + separator);
        const data = block
          .split(/\r?\n/)
          .find((line) => line.startsWith("data:"));
        if (data) terminal = await handleRunEvent(JSON.parse(data.slice(5).trim()));
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      showNotice(`${error.message}，已切换为状态轮询。`);
    }
  }
  if (!terminal && state.currentRunId === runId) await pollRun(runId);
}

async function pollRun(runId) {
  while (state.currentRunId === runId) {
    const run = await api(`/api/v1/agent-runs/${runId}`);
    const terminal = terminalStatuses.has(run.status);
    await handleRunEvent({ ...run, terminal, current_node: "" });
    if (terminal) return;
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
  }
}

byId("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = byId("token").value.trim();
  const identity = decodeIdentity(token);
  if (identity.role !== "CUSTOMER" || !Number.isInteger(identity.customerId)) {
    showNotice("令牌不包含有效的客户身份。" );
    return;
  }
  state.token = token;
  state.customerId = identity.customerId;
  try {
    await loadTickets();
    setConnected(true);
    showNotice("");
  } catch (error) {
    disconnect();
    showNotice(error.message);
  }
});

byId("disconnect").addEventListener("click", disconnect);
byId("new-ticket").addEventListener("click", () => showTicketForm(true));
byId("cancel-ticket").addEventListener("click", () => showTicketForm(false));

byId("ticket-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const orderValue = byId("order-id").value.trim();
  const payload = {
    category: byId("category").value,
    subject: byId("subject").value.trim(),
    order_id: orderValue ? Number(orderValue) : null,
  };
  try {
    const ticket = await api("/api/v1/chat/tickets", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    event.currentTarget.reset();
    showTicketForm(false);
    await loadTickets(ticket.id);
    showNotice("");
  } catch (error) {
    showNotice(error.message);
  }
});

byId("message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedTicketId || state.currentRunId !== null) return;
  const content = byId("message").value.trim();
  if (!content) return;
  state.currentRunId = 0;
  byId("message").disabled = true;
  byId("send-message").disabled = true;
  try {
    await api(`/api/v1/chat/tickets/${state.selectedTicketId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    byId("message").value = "";
    await selectTicket(state.selectedTicketId);
    const run = await api("/api/v1/agent-runs", {
      method: "POST",
      body: JSON.stringify({ ticket_id: state.selectedTicketId }),
    });
    state.currentRunId = run.run_id;
    setRunStatus(run.status);
    void followRun(run.run_id);
  } catch (error) {
    state.currentRunId = null;
    byId("message").disabled = false;
    byId("send-message").disabled = false;
    showNotice(error.message);
  }
});

byId("cancel-run").addEventListener("click", async () => {
  if (state.currentRunId === null) return;
  try {
    await api(`/api/v1/agent-runs/${state.currentRunId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason: "Customer cancelled from chat" }),
    });
    setRunStatus("CANCEL_REQUESTED");
  } catch (error) {
    showNotice(error.message);
  }
});
