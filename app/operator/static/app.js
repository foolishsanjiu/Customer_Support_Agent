"use strict";

const state = { token: "", role: "", timer: null };
const byId = (id) => document.getElementById(id);

const labels = {
  PENDING: "等待运行",
  RUNNING: "运行中",
  WAITING_APPROVAL: "等待审批",
  RESUME_PENDING: "等待恢复",
  RECOVERY_REQUIRED: "需要恢复",
  SUCCEEDED: "成功",
  FAILED: "失败",
  OPEN: "待处理",
  REPLAYING: "重放中",
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function decodeRole(token) {
  try {
    const segment = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const encoded = segment.padEnd(Math.ceil(segment.length / 4) * 4, "=");
    const payload = JSON.parse(atob(encoded));
    return String(payload.role || "").toUpperCase();
  } catch (_) {
    return "";
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
      // Keep the status-based message when the response is not JSON.
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

function showEmpty(container, message) {
  container.replaceChildren(element("p", "empty-state", message));
}

function statusPill(status) {
  return element("span", `status ${status.toLowerCase()}`, labels[status] || status);
}

function field(label, value) {
  const wrapper = element("div");
  wrapper.append(element("span", "field-label", label), element("p", "", value || "—"));
  return wrapper;
}

async function loadRuns() {
  const container = byId("runs");
  const status = byId("run-status").value;
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const runs = await api(`/api/v1/operator/runs${query}`);
  if (!runs.length) return showEmpty(container, "当前筛选条件下没有运行记录。");
  const fragment = document.createDocumentFragment();
  runs.forEach((run) => {
    const summary = element("div");
    summary.append(
      element("h3", "", `Run #${run.id} · Ticket #${run.ticket_id}`),
      statusPill(run.status),
    );
    if (run.error_code) summary.append(element("p", "", `错误：${run.error_code}`));
    const row = element("div", "data-row");
    row.append(
      summary,
      field("意图", run.intent),
      field("当前节点", run.current_node),
      field("最后更新", formatDate(run.updated_at)),
    );
    fragment.append(row);
  });
  container.replaceChildren(fragment);
}

async function decideApproval(id, decision, reason, button) {
  if (!reason.trim()) {
    showNotice("审批决定必须填写原因。");
    return;
  }
  button.disabled = true;
  try {
    await api(`/api/v1/approvals/${id}/${decision}`, {
      method: "POST",
      body: JSON.stringify({ reason: reason.trim() }),
    });
    showNotice("");
    await Promise.all([loadApprovals(), loadRuns()]);
  } catch (error) {
    showNotice(error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadApprovals() {
  const container = byId("approvals");
  const approvals = await api("/api/v1/approvals?status=PENDING");
  if (!approvals.length) return showEmpty(container, "没有等待处理的审批。");
  const fragment = document.createDocumentFragment();
  approvals.forEach((approval) => {
    const card = element("section", "action-card");
    const head = element("div", "action-card-head");
    head.append(
      element("h3", "", `${approval.tool_name} · #${approval.id}`),
      statusPill(approval.status),
    );
    const input = element("input");
    input.type = "text";
    input.maxLength = 2000;
    input.placeholder = "填写审批原因";
    input.setAttribute("aria-label", `审批 #${approval.id} 的决定原因`);
    const approve = element("button", "button button-primary", "批准");
    const reject = element("button", "button button-danger", "拒绝");
    approve.type = reject.type = "button";
    approve.addEventListener("click", () => decideApproval(approval.id, "approve", input.value, approve));
    reject.addEventListener("click", () => decideApproval(approval.id, "reject", input.value, reject));
    const actions = element("div", "card-actions");
    actions.append(input, approve, reject);
    card.append(
      head,
      element("p", "", `Run #${approval.run_id} · Ticket #${approval.ticket_id} · 风险 ${approval.risk_level}`),
      element("p", "", approval.reason),
      element("p", "", `截止 ${formatDate(approval.expires_at)}`),
      actions,
    );
    fragment.append(card);
  });
  container.replaceChildren(fragment);
}

async function replayDeadLetter(id, button) {
  if (!window.confirm(`确认重放失败任务 #${id}？`)) return;
  button.disabled = true;
  try {
    await api(`/api/v1/dlq/${id}/replay`, { method: "POST" });
    showNotice("");
    await loadDlq();
  } catch (error) {
    showNotice(error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadDlq() {
  const container = byId("dlq");
  const records = await api("/api/v1/dlq?status=OPEN&limit=50");
  if (!records.length) return showEmpty(container, "没有待处理的失败任务。");
  const fragment = document.createDocumentFragment();
  records.forEach((record) => {
    const card = element("section", "action-card");
    const head = element("div", "action-card-head");
    head.append(
      element("h3", "", `${record.task_name} · #${record.id}`),
      statusPill(record.status),
    );
    const replay = element("button", "button button-secondary", "请求重放");
    replay.type = "button";
    replay.addEventListener("click", () => replayDeadLetter(record.id, replay));
    const actions = element("div", "card-actions");
    actions.append(replay);
    card.append(
      head,
      element("p", "", `Run #${record.run_id} · ${record.reason_code}`),
      element("p", "", `错误 ${record.error_type || "未知"} · 失败 ${record.failure_count} 次 · 已重放 ${record.replay_count} 次`),
      element("p", "", `更新于 ${formatDate(record.updated_at)}`),
      actions,
    );
    fragment.append(card);
  });
  container.replaceChildren(fragment);
}

async function refresh(target = "all") {
  if (!state.token) return;
  const tasks = [];
  if (target === "all" || target === "runs") tasks.push(loadRuns());
  if ((target === "all" || target === "approvals") && ["MANAGER", "ADMIN"].includes(state.role)) {
    tasks.push(loadApprovals());
  }
  if ((target === "all" || target === "dlq") && state.role === "ADMIN") tasks.push(loadDlq());
  try {
    await Promise.all(tasks);
    showNotice("");
    byId("last-updated").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN")}`;
  } catch (error) {
    showNotice(error.message);
  }
}

function setConnected(connected) {
  byId("connection-dot").classList.toggle("connected", connected);
  byId("connection-label").textContent = connected ? "已连接" : "未连接";
  byId("role-label").textContent = state.role || "UNKNOWN";
  byId("role-label").hidden = !connected;
  byId("disconnect").hidden = !connected;
  byId("approvals-panel").hidden = !connected || !["MANAGER", "ADMIN"].includes(state.role);
  byId("dlq-panel").hidden = !connected || state.role !== "ADMIN";
}

function disconnect() {
  state.token = "";
  state.role = "";
  window.clearInterval(state.timer);
  state.timer = null;
  byId("token").value = "";
  setConnected(false);
  showNotice("");
  showEmpty(byId("runs"), "连接后显示最近的代理运行。");
}

byId("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.token = byId("token").value.trim();
  state.role = decodeRole(state.token);
  if (!["SUPPORT_AGENT", "MANAGER", "ADMIN"].includes(state.role)) {
    disconnect();
    showNotice("令牌不包含有效的运营角色。");
    return;
  }
  setConnected(true);
  await refresh();
  window.clearInterval(state.timer);
  state.timer = window.setInterval(refresh, 15000);
});

byId("disconnect").addEventListener("click", disconnect);
byId("run-status").addEventListener("change", () => refresh("runs"));
document.querySelectorAll(".refresh").forEach((button) => {
  button.addEventListener("click", () => refresh(button.dataset.target));
});
