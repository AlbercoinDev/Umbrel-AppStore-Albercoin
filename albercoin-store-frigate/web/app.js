const $ = (selector) => document.querySelector(selector);
const form = $("#config-form");

function setForm(config) {
  for (const [key, value] of Object.entries(config || {})) {
    const field = form.elements[key];
    if (field) field.value = value ?? "";
  }
  $("#initial-start-height").value = config?.startHeight || 709632;
}

function getForm() {
  return Object.fromEntries(new FormData(form).entries());
}

function statusText(status) {
  if (!status.configured) return "Pendiente de configurar";
  if (status.ready) return "Listo";
  if (status.running) return "Indexando";
  return "Parado";
}

async function refreshStatus() {
  const status = await fetch("/api/status").then((r) => r.json());
  $("#status-pill").textContent = statusText(status);
  $("#setup").hidden = status.configured;
  $("#progress-fill").style.width = `${status.progress || 0}%`;
  $("#progress-text").textContent = `${status.progress || 0}%`;
  $("#start-height").textContent = status.startHeight || "-";
  $("#current-height").textContent = status.currentHeight || "-";
  $("#tip-height").textContent = status.tipHeight || "-";
  $("#local-connection").textContent = `${status.localHost}:${status.tcpPort}`;
  $("#tor-connection").textContent = `${status.hiddenService}:${status.tcpPort}`;
  $("#txindex-warning").hidden = status.txindex !== false;
  setForm(status.config);
}

async function refreshLogs(keepScroll = false) {
  const box = $("#logs");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  const data = await fetch("/api/logs").then((r) => r.json());
  box.textContent = data.logs || "Sin logs todavia.";
  if (!keepScroll || nearBottom) box.scrollTop = box.scrollHeight;
}

async function post(url, data = {}) {
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  await refreshStatus();
  await refreshLogs();
}

$("#setup-save").addEventListener("click", () => {
  const config = getForm();
  config.startHeight = $("#initial-start-height").value || 709632;
  post("/api/config", config);
});

$("#config-save").addEventListener("click", (event) => {
  event.preventDefault();
  post("/api/config", getForm());
});

$("#start-btn").addEventListener("click", () => post("/api/start"));
$("#stop-btn").addEventListener("click", () => post("/api/stop"));
$("#logs-refresh").addEventListener("click", () => refreshLogs());

refreshStatus();
refreshLogs();
setInterval(refreshStatus, 5000);
setInterval(() => refreshLogs(true), 5000);
