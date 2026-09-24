// MCP manager UI — slide-over drawer.
// Reads /api/mcp fresh on every open. Mutations hit the API and the backend
// refreshes the runtime tool registry, so changes apply to the models
// immediately (no restart).
(() => {
  "use strict";

  const MASK = "\u2022\u2022\u2022\u2022\u2022\u2022";

  // ── DOM helpers ──────────────────────────────────────────────────────────
  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "checked") node.checked = !!value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    node.append(...children.flat());
    return node;
  };

  // ── API client ───────────────────────────────────────────────────────────
  async function api(path, options = {}) {
    const res = await fetch(`/api/mcp${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
    return data;
  }

  // ── State ────────────────────────────────────────────────────────────────
  let editingId = null; // null ⇒ create mode

  // ── Drawer shell ─────────────────────────────────────────────────────────
  const statusEl = el("div", { class: "mcp-status", id: "mcp-status" });

  const overlay = el("div", { class: "mcp-overlay", id: "mcp-overlay" });
  const drawer = el("div", { class: "mcp-drawer", id: "mcp-drawer" });
  overlay.appendChild(drawer);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeDrawer(); });
  document.body.appendChild(overlay);

  const openBtn = $("#mcp-open");
  if (openBtn) openBtn.addEventListener("click", (e) => { e.preventDefault(); openDrawer(); });

  function openDrawer() { editingId = null; overlay.classList.add("open"); renderList(); }
  function closeDrawer() { overlay.classList.remove("open"); }

  function showStatus(text, isError = false) {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", isError);
  }

  function summarize(refresh) {
    if (typeof refresh === "undefined") return "Saved.";
    const failed = (refresh || []).filter((r) => r.status === "error");
    if (failed.length === 0) return "Saved — all servers OK.";
    return `Saved — ${failed.length} server(s) failed: ${failed.map((f) => f.name).join(", ")}`;
  }

  // ── List view ────────────────────────────────────────────────────────────
  async function renderList() {
    const header = el("div", { class: "mcp-header" },
      el("h2", { text: "MCP servers" }),
      el("button", { class: "mcp-close", text: "\u2715", onclick: closeDrawer }),
    );
    const addBtn = el("button", { class: "mcp-btn primary", text: "+ Add server", onclick: () => renderForm() });
    const list = el("ul", { class: "mcp-list" });
    drawer.replaceChildren(header, addBtn, list, statusEl);
    showStatus("Loading\u2026");
    let servers;
    try {
      servers = await api("/");
    } catch (err) {
      showStatus(err.message, true);
      return;
    }
    if (servers.length === 0) list.appendChild(el("li", { class: "mcp-empty", text: "No servers configured." }));
    else for (const s of servers) list.appendChild(serverRow(s));
    showStatus("");
  }

  function serverRow(s) {
    const toggle = el("input", {
      type: "checkbox",
      class: "mcp-toggle",
      checked: s.enabled,
      title: "Include in tool discovery",
    });
    toggle.addEventListener("change", () => updateEnabled(s.id, toggle.checked));

    const head = el("div", { class: "mcp-item-top" },
      toggle,
      el("span", { class: "mcp-name", text: s.name }),
      el("span", { class: "mcp-badge", text: s.server.transport }),
      s.enabled ? null : el("span", { class: "mcp-disabled", text: "disabled" }),
    );
    const meta = el("div", { class: "mcp-meta", text: transportDescription(s) });
    const actions = el("div", { class: "mcp-actions" },
      el("button", { class: "mcp-btn", text: "Test", onclick: () => testServer(s.id) }),
      el("button", { class: "mcp-btn", text: "Edit", onclick: () => renderForm(s) }),
      el("button", { class: "mcp-btn danger", text: "Delete", onclick: () => deleteServer(s.id, s.name) }),
    );
    return el("li", { class: "mcp-item" }, head, meta, actions);
  }

  function transportDescription(s) {
    return s.server.transport === "stdio"
      ? `${s.server.command} ${s.server.args.join(" ")}`.trim()
      : s.server.url;
  }

  async function updateEnabled(id, enabled) {
    try {
      const saved = await api(`/${id}`, { method: "PUT", body: JSON.stringify({ enabled }) });
      renderList().then(() => showStatus(summarize(saved.refresh)));
    } catch (err) {
      renderList().then(() => showStatus(err.message, true));
    }
  }

  async function testServer(id) {
    showStatus("Testing\u2026");
    try {
      const result = await api(`/${id}/test`, { method: "POST" });
      if (result.ok) showStatus(`OK \u2014 ${result.tools.join(", ") || "no tools exposed"}`);
      else showStatus(`FAIL \u2014 ${result.error}`, true);
    } catch (err) {
      showStatus(err.message, true);
    }
  }

  async function deleteServer(id, name) {
    if (!confirm(`Delete MCP server "${name}"?`)) return;
    try {
      const result = await api(`/${id}`, { method: "DELETE" });
      renderList().then(() => showStatus(summarize(result.refresh)));
    } catch (err) {
      showStatus(err.message, true);
    }
  }

  // ── Form view ────────────────────────────────────────────────────────────
  function renderForm(server = null) {
    editingId = server ? server.id : null;
    const transport = server ? server.server.transport : "stdio";

    const header = el("div", { class: "mcp-header" },
      el("h2", { text: server ? `Edit ${server.name}` : "Add server" }),
      el("button", { class: "mcp-close", text: "\u2715", onclick: renderList }),
    );
    const nameInput = el("input", {
      type: "text",
      id: "mcp-form-name",
      class: "mcp-input",
      value: server ? server.name : "",
      placeholder: "name \u2014 no spaces or underscores",
    });
    const radioRow = el("div", { class: "mcp-transports" },
      transportRadio("stdio", transport),
      transportRadio("http", transport),
    );
    const configBox = el("div", { id: "mcp-config" });
    const enabledRow = el("label", { class: "mcp-enabled" },
      el("input", { type: "checkbox", id: "mcp-form-enabled", checked: server ? server.enabled : true }),
      " enabled",
    );
    const actions = el("div", { class: "mcp-actions" },
      el("button", { class: "mcp-btn", text: "Cancel", onclick: renderList }),
      el("button", { class: "mcp-btn primary", text: "Save", onclick: saveServer }),
    );

    drawer.replaceChildren(header, nameInput, radioRow, configBox, enabledRow, actions, statusEl);
    renderTransportFields(transport, server ? server.server : null);
  }

  function transportRadio(value, current) {
    const input = el("input", {
      type: "radio",
      name: "mcp-transport",
      value,
      checked: value === current,
    });
    input.addEventListener("change", () => renderTransportFields(value));
    return el("label", { class: "mcp-radio" }, input, ` ${value}`);
  }

  // ── Key/value pair editor (headers, env) ─────────────────────────────────
  function pairEditor(containerId, initial, opts) {
    const box = el("div", { class: "mcp-pairs", id: containerId });
    const add = (k = "", v = "") => {
      const row = el("div", { class: "mcp-pair" },
        el("input", { class: "mcp-pair-key", type: "text", value: k, placeholder: opts.keyPlaceholder }),
        el("input", { class: "mcp-pair-value", type: opts.valueType, value: v, placeholder: opts.valuePlaceholder }),
        el("button", {
          class: "mcp-btn",
          text: "\u2212",
          onclick: () => { row.remove(); },
        }),
      );
      box.appendChild(row);
    };
    Object.entries(initial).forEach(([k, v]) => add(k, v));
    if (Object.keys(initial).length === 0) add();
    box.appendChild(el("button", { class: "mcp-btn", text: "+ pair", onclick: () => add() }));
    return box;
  }

  function collectPairs(containerId) {
    const pairs = {};
    $(`#${containerId}`).querySelectorAll(".mcp-pair").forEach((row) => {
      const key = row.querySelector(".mcp-pair-key").value.trim();
      const value = row.querySelector(".mcp-pair-value").value;
      if (key) pairs[key] = value;
    });
    return pairs;
  }

  function fieldInput(attrs) { return el("input", { class: "mcp-input", ...attrs }); }

  function renderTransportFields(transport, config = null) {
    const box = $("#mcp-config");
    if (transport === "stdio") {
      const command = fieldInput({ type: "text", id: "mcp-stdio-command", value: config?.command || "", placeholder: "command" });
      const args = fieldInput({ type: "text", id: "mcp-stdio-args", value: (config?.args || []).join(" "), placeholder: "args \u2014 space separated" });
      const env = pairEditor("mcp-stdio-env", config?.env || {}, { valueType: "text", keyPlaceholder: "env var", valuePlaceholder: "value" });
      box.replaceChildren(
        el("label", { class: "mcp-label", text: "Command" }), command,
        el("label", { class: "mcp-label", text: "Args" }), args,
        el("label", { class: "mcp-label", text: "Env" }), env,
      );
    } else {
      const url = fieldInput({ type: "text", id: "mcp-http-url", value: config?.url || "", placeholder: "https://\u2026/mcp" });
      const headers = pairEditor("mcp-http-headers", config?.headers || {}, { valueType: "password", keyPlaceholder: "header", valuePlaceholder: "value" });
      const auth = el("select", { id: "mcp-http-auth", class: "mcp-input" },
        el("option", { value: "none", text: "none" }),
        el("option", { value: "header", text: "header" }),
        el("option", { value: "oauth", text: "oauth" }),
      );
      auth.value = config?.auth_type || "none";
      box.replaceChildren(
        el("label", { class: "mcp-label", text: "URL" }), url,
        el("label", { class: "mcp-label", text: "Headers" }), headers,
        el("label", { class: "mcp-label", text: "Auth type" }), auth,
      );
    }
  }

  // ── Save ─────────────────────────────────────────────────────────────────
  async function saveServer() {
    const name = $("#mcp-form-name").value.trim();
    if (!/^[^\s_]+$/.test(name)) { showStatus("Name cannot contain spaces or underscores.", true); return; }
    const transport = $('input[name="mcp-transport"]:checked').value;
    let server;
    if (transport === "stdio") {
      server = {
        transport,
        command: $("#mcp-stdio-command").value.trim(),
        args: $("#mcp-stdio-args").value.trim().split(/\s+/).filter(Boolean),
        env: collectPairs("mcp-stdio-env"),
      };
      if (!server.command) { showStatus("Command is required.", true); return; }
    } else {
      server = {
        transport,
        url: $("#mcp-http-url").value.trim(),
        headers: collectPairs("mcp-http-headers"),
        auth_type: $("#mcp-http-auth").value,
      };
      if (!server.url) { showStatus("URL is required.", true); return; }
    }
    const body = { name, enabled: $("#mcp-form-enabled").checked, server };
    try {
      const saved = editingId === null
        ? await api("/", { method: "POST", body: JSON.stringify(body) })
        : await api(`/${editingId}`, { method: "PUT", body: JSON.stringify(body) });
      renderList().then(() => showStatus(summarize(saved.refresh)));
    } catch (err) {
      showStatus(err.message, true);
    }
  }
})();