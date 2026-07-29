const message = document.querySelector("#form-message");
const rows = document.querySelector("#task-rows");
const logDialog = document.querySelector("#log-dialog");
const sourceUrl = document.querySelector("#source-url");
const refererInput = document.querySelector("#url-form [name='referer']");
let inferredReferer = "";
let refererTimer;
let refererRequest = 0;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* response is not JSON */ }
    throw new Error(detail);
  }
  return response;
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((item) => {
    item.classList.toggle("active", item === tab);
    item.setAttribute("aria-selected", item === tab ? "true" : "false");
  });
  document.querySelector("#url-form").classList.toggle("hidden", tab.dataset.tab !== "url");
  document.querySelector("#upload-form").classList.toggle("hidden", tab.dataset.tab !== "upload");
}));

function requestHeaders(form) {
  const mapping = { user_agent: "User-Agent", referer: "Referer", cookie: "Cookie", authorization: "Authorization" };
  return Object.fromEntries(Object.entries(mapping)
    .map(([field, header]) => [header, form.elements[field]?.value.trim()])
    .filter(([, value]) => value));
}

sourceUrl.addEventListener("input", () => {
  clearTimeout(refererTimer);
  const url = sourceUrl.value.trim();
  if (!url) return;
  refererTimer = setTimeout(async () => {
    const request = ++refererRequest;
    try {
      const response = await api(`/api/v1/referer-preview?url=${encodeURIComponent(url)}`);
      const { referer } = await response.json();
      if (request === refererRequest && (!refererInput.value.trim() || refererInput.value === inferredReferer)) {
        refererInput.value = referer;
        inferredReferer = referer;
      }
    } catch (_) { /* Invalid or incomplete URLs are handled when the form is submitted. */ }
  }, 300);
});

document.querySelector("#url-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  message.textContent = "正在提交";
  try {
    await api("/api/v1/tasks/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: form.elements.url.value,
        output_name: form.elements.output_name.value,
        output_subdir: form.elements.output_subdir.value,
        headers: requestHeaders(form),
        ignore_certificate_errors: form.elements.ignore_certificate_errors.checked,
      }),
    });
    message.textContent = "任务已加入队列";
    form.elements.cookie.value = "";
    form.elements.authorization.value = "";
    loadTasks();
  } catch (error) { message.textContent = error.message; }
});

document.querySelector("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(event.currentTarget);
  data.set("headers_json", "{}");
  message.textContent = "正在上传";
  try {
    await api("/api/v1/tasks/upload", { method: "POST", body: data });
    message.textContent = "任务已加入队列";
    loadTasks();
  } catch (error) { message.textContent = error.message; }
});

function formatBytes(bytes) {
  if (!bytes) return "-";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function actionButton(label, action, id) {
  return `<button class="secondary" type="button" data-action="${action}" data-id="${id}">${label}</button>`;
}

function renderTasks(tasks) {
  document.querySelector("#task-count").textContent = `${tasks.length} 个任务`;
  if (!tasks.length) {
    rows.innerHTML = '<tr><td colspan="6" class="empty">暂无任务</td></tr>';
    return;
  }
  rows.innerHTML = tasks.map((task) => {
    const canCancel = ["queued", "preparing", "downloading", "postprocessing", "retry_wait"].includes(task.status);
    const canRetry = ["failed", "cancelled"].includes(task.status);
    const actions = [actionButton("日志", "logs", task.id)];
    if (canCancel) actions.push(actionButton("取消", "cancel", task.id));
    if (canRetry) actions.push(actionButton("重试", "retry", task.id));
    if (!canCancel) actions.push(actionButton("删除", "delete", task.id));
    if (task.status === "completed") actions.push(`<a href="/api/v1/tasks/${task.id}/file" data-download="${task.id}">下载</a>`);
    return `<tr>
      <td><strong>${escapeHtml(task.output_name)}</strong><br><small>${escapeHtml(task.output_path || task.output_subdir || "根目录")}</small></td>
      <td><span class="status ${task.status}">${task.status}</span></td>
      <td><div class="progress-track"><div class="progress-bar" style="width:${task.progress}%"></div></div>${task.progress.toFixed(1)}%</td>
      <td>${formatBytes(task.speed)}/s · ${task.eta == null ? "-" : `${task.eta}s`}</td>
      <td>${task.attempt}</td>
      <td><div class="row-actions">${actions.join("")}</div>${task.error_message ? `<small>${escapeHtml(task.error_message)}</small>` : ""}</td>
    </tr>`;
  }).join("");
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

async function loadTasks() {
  try {
    const response = await api("/api/v1/tasks?page_size=100");
    renderTasks((await response.json()).items);
  } catch (error) {
    rows.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(error.message)}</td></tr>`;
  }
}

rows.addEventListener("click", async (event) => {
  const download = event.target.closest("a[data-download]");
  if (download) {
    event.preventDefault();
    try {
      const response = await api(download.getAttribute("href"));
      const blobUrl = URL.createObjectURL(await response.blob());
      const temporary = document.createElement("a");
      temporary.href = blobUrl;
      temporary.download = download.closest("tr").querySelector("strong").textContent + ".mp4";
      temporary.click();
      URL.revokeObjectURL(blobUrl);
    } catch (error) { message.textContent = error.message; }
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, id } = button.dataset;
  try {
    if (action === "logs") {
      const response = await api(`/api/v1/tasks/${id}/logs`);
      document.querySelector("#log-output").textContent = await response.text() || "暂无日志";
      logDialog.showModal();
      return;
    }
    if (action === "delete") await api(`/api/v1/tasks/${id}`, { method: "DELETE" });
    else await api(`/api/v1/tasks/${id}/${action}`, { method: "POST" });
    loadTasks();
  } catch (error) { message.textContent = error.message; }
});

document.querySelector("#close-log").addEventListener("click", () => logDialog.close());
document.querySelector("#refresh-tasks").addEventListener("click", loadTasks);

fetch("/readyz").then((response) => {
  document.querySelector("#service-state").textContent = response.ok ? "服务可用" : "服务依赖尚未就绪";
}).catch(() => { document.querySelector("#service-state").textContent = "无法连接服务"; });

loadTasks();
setInterval(loadTasks, 2000);