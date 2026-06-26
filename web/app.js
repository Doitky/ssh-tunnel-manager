
let TOKEN = localStorage.getItem("stm_token") || "";
let current = null;
let editingRules = [];
let evtSource = null;

const $ = (id) => document.getElementById(id);
const authHeaders = () => ({ "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json" });

function show(el) { el.style.display = "block"; }
function hide(el) { el.style.display = "none"; }

async function api(method, path, body) {
  const opt = { method, headers: authHeaders() };
  if (body !== undefined) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  if (r.status === 401) { logout(); throw new Error("unauthorized"); }
  if (!r.ok) { const t = await r.text(); throw new Error(t); }
  return r.json();
}

function logout() { localStorage.removeItem("stm_token"); location.reload(); }

$("loginBtn").onclick = async () => {
  const t = $("tokenInput").value.trim();
  if (!t) return;
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: t }) });
    if (!r.ok) { $("loginMsg").textContent = "口令错误"; return; }
    TOKEN = t; localStorage.setItem("stm_token", t);
    enterApp();
  } catch (e) { $("loginMsg").textContent = String(e); }
};

function enterApp() { hide($("login")); show($("app")); loadSessions(); openSSE(); }

async function loadSessions() {
  const list = await api("GET", "/api/sessions");
  const box = $("sessionList"); box.innerHTML = "";
  list.forEach((s) => {
    const div = document.createElement("div");
    div.className = "session-item" + (current === s.name ? " active" : "");
    div.innerHTML = `<span>${s.name}</span><span class="dot ${s.status === "active" ? "active" : ""}" title="${s.status}"></span>`;
    div.onclick = () => selectSession(s.name);
    box.appendChild(div);
  });
  if (current) { showView(); } else { hide($("view")); hide($("editor")); }
}

function selectSession(name) {
  current = name; loadSessions();
  show($("view"));
  loadLog();
}

async function loadLog() {
  if (!current) return;
  try { const r = await api("GET", `/api/sessions/${current}/logs`); $("log").textContent = r.lines || "(无日志)"; }
  catch (e) { $("log").textContent = String(e); }
}

function openSSE() {
  if (evtSource) evtSource.close();
  evtSource = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  evtSource.addEventListener("status", (e) => {
    const ev = JSON.parse(e.data);
    if (ev.name === current) loadLog();
    loadSessions();
  });
  evtSource.addEventListener("log", (e) => {
    const ev = JSON.parse(e.data);
    if (ev.name === current) loadLog();
  });
}

function showView() { show($("view")); }
function showEditor(session) {
  hide($("view")); show($("editor"));
  $("editorTitle").textContent = session ? "编辑会话" : "新建会话";
  $("f_name").value = session ? session.name : "";
  $("f_host").value = session ? session.host : "";
  $("f_port").value = session ? session.port : 22;
  $("f_username").value = session ? session.username : "";
  $("f_auth").value = session ? session.auth_method : "password";
  $("f_password").value = session ? session.password : "";
  $("f_keypath").value = session ? session.key_path : "";
  $("f_keepalive").value = session ? session.keepalive_interval : 30;
  $("f_keepalive_on").checked = session ? session.keepalive_enabled : true;
  toggleAuth();
  editingRules = session ? session.forward_rules.map((r) => ({ ...r })) : [];
  renderRules();
}
function toggleAuth() {
  const isPwd = $("f_auth").value === "password";
  $("pwdRow").style.display = isPwd ? "block" : "none";
  $("keyRow").style.display = isPwd ? "none" : "block";
}
$("f_auth").onchange = toggleAuth;

function renderRules() {
  const tbl = $("rulesTable");
  tbl.innerHTML = "<tr><th>方向</th><th>本地端口</th><th>远程主机</th><th>远程端口</th><th>操作</th></tr>";
  editingRules.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><select><option ${r.direction === "local" ? "selected" : ""}>local</option><option ${r.direction === "remote" ? "selected" : ""}>remote</option><option ${r.direction === "dynamic" ? "selected" : ""}>dynamic</option></select></td>
      <td><input type="number" value="${r.local_port || ""}"></td>
      <td><input value="${r.remote_host || "127.0.0.1"}"></td>
      <td><input type="number" value="${r.remote_port || ""}"></td>
      <td><button>删除</button></td>`;
    const sel = tr.querySelector("select"); sel.onchange = () => { r.direction = sel.value; };
    const inputs = tr.querySelectorAll("input");
    inputs[0].oninput = () => r.local_port = parseInt(inputs[0].value) || 0;
    inputs[1].oninput = () => r.remote_host = inputs[1].value;
    inputs[2].oninput = () => r.remote_port = parseInt(inputs[2].value) || 0;
    tr.querySelector("button").onclick = () => { editingRules.splice(i, 1); renderRules(); };
    tbl.appendChild(tr);
  });
}
$("addRuleBtn").onclick = () => { editingRules.push({ direction: "local", local_port: 0, remote_host: "127.0.0.1", remote_port: 0 }); renderRules(); };

$("saveBtn").onclick = async () => {
  const body = {
    name: $("f_name").value.trim(), host: $("f_host").value.trim(),
    port: parseInt($("f_port").value) || 22, username: $("f_username").value.trim(),
    auth_method: $("f_auth").value, password: $("f_password").value,
    key_path: $("f_keypath").value, keepalive_enabled: $("f_keepalive_on").checked,
    keepalive_interval: parseInt($("f_keepalive").value) || 30,
    forward_rules: editingRules, enabled: true,
  };
  try { await api("POST", "/api/sessions", body); current = body.name; hide($("editor")); show($("view")); loadSessions(); }
  catch (e) { alert("保存失败: " + e.message); }
};
$("cancelBtn").onclick = () => { hide($("editor")); if (current) show($("view")); };
$("newBtn").onclick = () => { showEditor(null); };
$("editBtn").onclick = async () => {
  const list = await api("GET", "/api/sessions");
  const s = list.find((x) => x.name === current);
  if (s) showEditor(s);
};
$("deleteBtn").onclick = async () => {
  if (!current || !confirm(`删除会话 ${current}?`)) return;
  await api("DELETE", `/api/sessions/${current}`); current = null; hide($("view")); loadSessions();
};
$("connectBtn").onclick = async () => { if (current) { await api("POST", `/api/sessions/${current}/connect`); loadLog(); } };
$("disconnectBtn").onclick = async () => { if (current) await api("POST", `/api/sessions/${current}/disconnect`); };
$("connectAllBtn").onclick = async () => { await api("POST", "/api/connect-all"); };
$("disconnectAllBtn").onclick = async () => { await api("POST", "/api/disconnect-all"); };

if (TOKEN) enterApp();
