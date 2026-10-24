(function () {
  "use strict";

  const KEYS = { tenant: "agenttwin.tenantId", token: "agenttwin.token", base: "agenttwin.apiBase" };
  const NAV = [
    ["home", "Home"],
    ["tenants", "Tenants"],
    ["populations", "Populations"],
    ["personas", "Personas"],
    ["graph", "Org graph"],
    ["identity", "Identity"],
    ["audit", "Audit"],
    ["settings", "Settings"],
  ];

  const state = {
    page: "home",
    tenantId: localStorage.getItem(KEYS.tenant) || "",
    token: localStorage.getItem(KEYS.token) || "",
    base: localStorage.getItem(KEYS.base) || "",
    whoami: null,
    error: "",
  };

  function persist() {
    localStorage.setItem(KEYS.tenant, state.tenantId);
    localStorage.setItem(KEYS.token, state.token);
    localStorage.setItem(KEYS.base, state.base);
  }

  function apiBase() {
    const raw = (state.base || "").trim();
    if (!raw) return "";
    return raw.replace(/\/$/, "");
  }

  function headers() {
    const h = { "Content-Type": "application/json", Accept: "application/json" };
    if (state.tenantId) h["X-Tenant-Id"] = state.tenantId;
    if (state.token) h.Authorization = "Bearer " + state.token;
    return h;
  }

  function items(body) {
    if (Array.isArray(body)) return body;
    if (body && Array.isArray(body.items)) return body.items;
    return [];
  }

  async function api(path, opts) {
    const url = apiBase() + path;
    const res = await fetch(url, Object.assign({ headers: headers() }, opts || {}));
    const text = await res.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = text;
    }
    if (!res.ok) {
      const detail = body && (body.detail || (body.error && body.error.message));
      throw new Error(detail || res.status + " " + path);
    }
    return body;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showError(err) {
    const banner = document.getElementById("banner");
    banner.hidden = !err;
    banner.textContent = err ? String(err) : "";
  }

  function bindForm(id, handler) {
    const form = document.getElementById(id);
    if (!form) return;
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      showError("");
      try {
        await handler(ev);
      } catch (err) {
        showError(err.message || err);
      }
    };
  }

  function setPage(page) {
    state.page = page;
    if (location.hash !== "#/" + page) location.hash = "#/" + page;
    draw();
  }

  function renderNav() {
    document.getElementById("nav").innerHTML = NAV.map(([id, label]) => {
      const active = id === state.page ? " active" : "";
      return `<a class="${active}" href="#/${id}">${label}</a>`;
    }).join("");
  }

  function fillTenants(tenants) {
    const select = document.getElementById("tenant-select");
    const current = state.tenantId;
    const opts = ['<option value="">Select tenant</option>'].concat(
      tenants.map((t) => {
        const selected = t.id === current ? " selected" : "";
        return `<option value="${escapeHtml(t.id)}"${selected}>${escapeHtml(t.name || t.slug || t.id)}</option>`;
      })
    );
    select.innerHTML = opts.join("");
  }

  async function refreshWhoami() {
    try {
      state.whoami = await api("/api/v1/whoami");
      const el = document.getElementById("whoami");
      const roles = (state.whoami.roles || []).join(", ") || "none";
      el.textContent = (state.whoami.subject || "anonymous") + " · " + roles +
        (state.whoami.platform_admin ? " · platform admin" : "");
    } catch (err) {
      document.getElementById("whoami").textContent = String(err.message || err);
    }
  }

  async function loadTenants() {
    const body = await api("/api/v1/tenants");
    const tenants = items(body);
    fillTenants(tenants);
    if (!state.tenantId && tenants[0]) {
      state.tenantId = tenants[0].id;
      persist();
      fillTenants(tenants);
    }
    return tenants;
  }

  function table(headers, rows) {
    if (!rows.length) return '<p class="lede">Nothing here yet.</p>';
    return `<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
      <tbody>${rows.join("")}</tbody></table>`;
  }

  function homeView() {
    return `
      <h1>AgentTwin</h1>
      <p class="lede">Control plane for tenant-scoped synthetic workforces on MatrAIx.
        Harbor jobs and <code>matraix run</code> are unchanged. Simulated users are
        experimental instruments, not a substitute for people.</p>
      <div class="limit">Limitation: synthetic persona outputs are <strong>not human research</strong>.
        <code>synthetic_equivalent_to_human_research: false</code>. Validate with humans before decisions.</div>
      <div class="grid" id="home-stats">
        <div class="card"><h3>Tenants</h3><p class="stat" id="stat-tenants">—</p></div>
        <div class="card"><h3>Populations</h3><p class="stat" id="stat-pops">—</p></div>
        <div class="card"><h3>Personas</h3><p class="stat" id="stat-personas">—</p></div>
        <div class="card"><h3>Auth</h3><p class="stat" id="stat-auth">—</p></div>
      </div>
      <h2>Workspace path</h2>
      <div class="grid">
        <a class="card" href="#/tenants"><h3>1. Tenant</h3><p>Create an isolation boundary and default organization.</p></a>
        <a class="card" href="#/populations"><h3>2. Population</h3><p>Declare a workforce shape. Materialize a version when ready.</p></a>
        <a class="card" href="#/personas"><h3>3. Personas</h3><p>Wrap existing 1,290-dimension YAML records.</p></a>
        <a class="card" href="#/audit"><h3>4. Audit</h3><p>Append-only trail for every mutation.</p></a>
      </div>
      <p class="lede">Experiment launch and Harbor trial execution are not in this API yet.
        Use Playground or <code>matraix run</code> for simulation runs.</p>`;
  }

  function tenantsView() {
    return `
      <h1>Tenants</h1>
      <p class="lede">A tenant is the isolation boundary. Creating one also creates a default organization.</p>
      <form class="form" id="tenant-form">
        <label for="t-name">Name</label>
        <input id="t-name" name="name" required placeholder="Acme Research" />
        <label for="t-slug">Slug</label>
        <input id="t-slug" name="slug" required placeholder="acme" />
        <div class="row"><button type="submit">Create tenant</button></div>
      </form>
      <h2>Directory</h2>
      <div id="tenant-table"></div>`;
  }

  function populationsView() {
    return `
      <h1>Populations</h1>
      <p class="lede">Named persona sets. Declarations validate a shape (including 10k) without rewriting synthesis.</p>
      <form class="form" id="pop-form">
        <label for="p-name">Name</label>
        <input id="p-name" required value="Workforce" placeholder="Named workforce" />
        <label for="p-size">Target size</label>
        <input id="p-size" type="number" min="1" value="12" />
        <div class="row"><button type="submit">Create population</button></div>
      </form>
      <h2>Declare a shape</h2>
      <form class="form" id="decl-form">
        <label for="d-size">Target size</label>
        <input id="d-size" type="number" min="1" value="100" />
        <label for="d-backend">Backend</label>
        <select id="d-backend">
          <option value="coreset_1m">coreset_1m</option>
          <option value="treiver">treiver</option>
          <option value="full_dag">full_dag</option>
        </select>
        <label for="d-name">Population name</label>
        <input id="d-name" placeholder="100-shaped workforce" />
        <div class="row"><button type="submit">Declare population</button></div>
      </form>
      <h2>In this tenant</h2>
      <div id="pop-table"></div>
      <h2>Versions</h2>
      <form class="form" id="ver-form">
        <label for="v-pop">Population id</label>
        <input id="v-pop" placeholder="pop_…" />
        <label for="v-seed">Seed</label>
        <input id="v-seed" type="number" value="42" />
        <div class="row"><button type="submit">Materialize version</button></div>
      </form>
      <p class="lede">Materialization writes an immutable version from the declaration. It can fail if the evidence pool is missing — that is expected in a fresh checkout.</p>
      <div id="ver-out"></div>`;
  }

  function personasView() {
    return `
      <h1>Personas</h1>
      <p class="lede">Wrappers around existing MatrAIx YAML records. Not a claim of psychological equivalence.</p>
      <form class="form" id="per-form">
        <label for="per-json">Legacy record JSON</label>
        <textarea id="per-json">{
  "persona_id": "0042",
  "version": "1.0",
  "source": "synthetic",
  "display_name": "Casey Brooks",
  "dimensions": { "age_bracket": "25-34", "role_function": "Teaching" }
}</textarea>
        <label for="per-pop">Population id (optional)</label>
        <input id="per-pop" />
        <div class="row"><button type="submit">Wrap persona</button></div>
      </form>
      <h2>In this tenant</h2>
      <div id="per-table"></div>`;
  }

  function graphView() {
    return `
      <h1>Org graph</h1>
      <p class="lede">Directed edges between personas, teams, and organizations. Tenant-scoped.</p>
      <form class="form" id="edge-form">
        <label for="e-rel">Relation</label>
        <input id="e-rel" value="member_of" required />
        <label for="e-sk">Source kind</label>
        <input id="e-sk" value="persona" required />
        <label for="e-sid">Source id</label>
        <input id="e-sid" required />
        <label for="e-tk">Target kind</label>
        <input id="e-tk" value="organization" required />
        <label for="e-tid">Target id</label>
        <input id="e-tid" required />
        <div class="row"><button type="submit">Add edge</button></div>
      </form>
      <h2>Organizations in this tenant</h2>
      <div id="org-table"></div>
      <h2>Edges</h2>
      <div id="edge-table"></div>`;
  }

  function identityView() {
    return `
      <h1>Identity</h1>
      <pre id="who-pre">Loading…</pre>
      <h2>Users</h2>
      <form class="form" id="user-form">
        <label for="u-sub">Subject</label>
        <input id="u-sub" required placeholder="ada@example.test" />
        <label for="u-name">Display name</label>
        <input id="u-name" placeholder="Ada" />
        <label for="u-roles">Roles (comma)</label>
        <input id="u-roles" value="viewer" />
        <div class="row"><button type="submit">Create user</button></div>
      </form>
      <div id="user-table"></div>`;
  }

  function auditView() {
    return `
      <h1>Audit</h1>
      <p class="lede">Append-only security stream. Separate from Harbor job telemetry.</p>
      <div class="row">
        <button type="button" class="secondary" id="verify-chain">Verify hash chain</button>
      </div>
      <pre id="verify-out" hidden></pre>
      <div id="audit-table"></div>`;
  }

  function settingsView() {
    return `
      <h1>Settings</h1>
      <p class="lede">Same-origin by default when this UI is served by <code>matraix enterprise-api</code>.
        Tokens come from the environment — never commit them.</p>
      <form class="form" id="set-form">
        <label for="s-base">API base (blank = this origin)</label>
        <input id="s-base" value="${escapeHtml(state.base)}" placeholder="http://127.0.0.1:8090" />
        <label for="s-token">Bearer token (optional)</label>
        <input id="s-token" type="password" value="${escapeHtml(state.token)}" autocomplete="off" />
        <div class="row"><button type="submit">Save</button></div>
      </form>
      <p class="lede">Open-dev (no token configured) is a platform admin. Production should set
        <code>MATRIX_ENTERPRISE_AUTH_MODE=strict</code>.</p>`;
  }

  const views = {
    home: homeView,
    tenants: tenantsView,
    populations: populationsView,
    personas: personasView,
    graph: graphView,
    identity: identityView,
    audit: auditView,
    settings: settingsView,
  };

  async function hydrate() {
    showError("");
    try {
      if (state.page === "home") {
        const tenants = await loadTenants();
        document.getElementById("stat-tenants").textContent = String(tenants.length);
        document.getElementById("stat-auth").textContent = state.whoami && state.whoami.provider
          ? String(state.whoami.provider)
          : "—";
        if (state.tenantId) {
          const pops = items(await api("/api/v1/populations"));
          const pers = items(await api("/api/v1/personas"));
          document.getElementById("stat-pops").textContent = String(pops.length);
          document.getElementById("stat-personas").textContent = String(pers.length);
        } else {
          document.getElementById("stat-pops").textContent = "0";
          document.getElementById("stat-personas").textContent = "0";
        }
      } else if (state.page === "tenants") {
        const tenants = await loadTenants();
        document.getElementById("tenant-table").innerHTML = table(
          ["Name", "Slug", "Id", "Default org"],
          tenants.map((t) => `<tr><td>${escapeHtml(t.name)}</td><td>${escapeHtml(t.slug)}</td>
            <td class="mono">${escapeHtml(t.id)}</td>
            <td class="mono">${escapeHtml(t.default_organization_id || "")}</td></tr>`)
        );
        bindForm("tenant-form", async () => {
          const created = await api("/api/v1/tenants", {
            method: "POST",
            body: JSON.stringify({
              name: document.getElementById("t-name").value,
              slug: document.getElementById("t-slug").value,
            }),
          });
          state.tenantId = created.id;
          persist();
          await hydrate();
        });
      } else if (state.page === "populations") {
        await loadTenants();
        const pops = state.tenantId ? items(await api("/api/v1/populations")) : [];
        document.getElementById("pop-table").innerHTML = table(
          ["Name", "Size", "Id"],
          pops.map((p) => `<tr><td>${escapeHtml(p.name)}</td><td>${escapeHtml(p.target_size)}</td>
            <td class="mono">${escapeHtml(p.id)}</td></tr>`)
        );
        bindForm("pop-form", async () => {
          await api("/api/v1/populations", {
            method: "POST",
            body: JSON.stringify({
              name: document.getElementById("p-name").value,
              target_size: Number(document.getElementById("p-size").value),
            }),
          });
          await hydrate();
        });
        bindForm("decl-form", async () => {
          const size = Number(document.getElementById("d-size").value);
          await api("/api/v1/population-declarations", {
            method: "POST",
            body: JSON.stringify({
              target_size: size,
              backend: document.getElementById("d-backend").value,
              name: document.getElementById("d-name").value || undefined,
              segments: [{ name: "all", count: size }],
              privacy_mode: "aggregate_stats_then_synthetic",
            }),
          });
          await hydrate();
        });
        bindForm("ver-form", async () => {
          const popId = document.getElementById("v-pop").value.trim();
          const out = document.getElementById("ver-out");
          try {
            const version = await api("/api/v1/populations/" + encodeURIComponent(popId) + "/versions", {
              method: "POST",
              body: JSON.stringify({ seed: Number(document.getElementById("v-seed").value) }),
            });
            out.innerHTML = `<pre>${escapeHtml(JSON.stringify(version, null, 2))}</pre>`;
          } catch (err) {
            out.innerHTML = `<p class="err">${escapeHtml(err.message)}</p>`;
          }
        });
      } else if (state.page === "personas") {
        await loadTenants();
        const rows = state.tenantId ? items(await api("/api/v1/personas")) : [];
        document.getElementById("per-table").innerHTML = table(
          ["Id", "Display", "Population"],
          rows.map((p) => `<tr><td class="mono">${escapeHtml(p.id)}</td>
            <td>${escapeHtml(p.display_name || p.legacy_persona_id || "")}</td>
            <td class="mono">${escapeHtml(p.population_id || "")}</td></tr>`)
        );
        bindForm("per-form", async () => {
          const record = JSON.parse(document.getElementById("per-json").value);
          const population_id = document.getElementById("per-pop").value.trim() || undefined;
          await api("/api/v1/personas", {
            method: "POST",
            body: JSON.stringify({ record, population_id }),
          });
          await hydrate();
        });
      } else if (state.page === "graph") {
        await loadTenants();
        const orgs = state.tenantId ? items(await api("/api/v1/organizations")) : [];
        const edges = state.tenantId ? items(await api("/api/v1/org-edges")) : [];
        document.getElementById("org-table").innerHTML = table(
          ["Name", "Id"],
          orgs.map((o) => `<tr><td>${escapeHtml(o.name)}</td>
            <td class="mono">${escapeHtml(o.id)}</td></tr>`)
        );
        document.getElementById("edge-table").innerHTML = table(
          ["Relation", "Source", "Target", "Id"],
          edges.map((e) => `<tr><td>${escapeHtml(e.relation)}</td>
            <td class="mono">${escapeHtml(e.source_kind)} ${escapeHtml(e.source_id)}</td>
            <td class="mono">${escapeHtml(e.target_kind)} ${escapeHtml(e.target_id)}</td>
            <td class="mono">${escapeHtml(e.id)}</td></tr>`)
        );
        bindForm("edge-form", async () => {
          await api("/api/v1/org-edges", {
            method: "POST",
            body: JSON.stringify({
              relation: document.getElementById("e-rel").value,
              source_kind: document.getElementById("e-sk").value,
              source_id: document.getElementById("e-sid").value,
              target_kind: document.getElementById("e-tk").value,
              target_id: document.getElementById("e-tid").value,
            }),
          });
          await hydrate();
        });
      } else if (state.page === "identity") {
        document.getElementById("who-pre").textContent = JSON.stringify(state.whoami, null, 2);
        const users = state.tenantId ? items(await api("/api/v1/users")) : [];
        document.getElementById("user-table").innerHTML = table(
          ["Subject", "Roles", "Id"],
          users.map((u) => `<tr><td>${escapeHtml(u.subject)}</td>
            <td>${escapeHtml((u.roles || []).join(", "))}</td>
            <td class="mono">${escapeHtml(u.id)}</td></tr>`)
        );
        bindForm("user-form", async () => {
          await api("/api/v1/users", {
            method: "POST",
            body: JSON.stringify({
              subject: document.getElementById("u-sub").value,
              display_name: document.getElementById("u-name").value || undefined,
              roles: document.getElementById("u-roles").value.split(",").map((s) => s.trim()).filter(Boolean),
            }),
          });
          await hydrate();
        });
      } else if (state.page === "audit") {
        await loadTenants();
        const events = state.tenantId ? items(await api("/api/v1/audit-events")) : [];
        document.getElementById("audit-table").innerHTML = table(
          ["Action", "Resource", "Actor", "When"],
          events.map((e) => `<tr><td>${escapeHtml(e.action)}</td>
            <td class="mono">${escapeHtml(e.resource_type || "")} ${escapeHtml(e.resource_id || "")}</td>
            <td>${escapeHtml(e.actor || e.subject || "")}</td>
            <td class="mono">${escapeHtml(e.timestamp || e.created_at || "")}</td></tr>`)
        );
        document.getElementById("verify-chain").onclick = async () => {
          const box = document.getElementById("verify-out");
          box.hidden = false;
          showError("");
          try {
            box.textContent = JSON.stringify(await api("/api/v1/audit-events/verify"), null, 2);
          } catch (err) {
            box.textContent = String(err.message || err);
            showError(err.message || err);
          }
        };
      } else if (state.page === "settings") {
        bindForm("set-form", async () => {
          state.base = document.getElementById("s-base").value.trim();
          state.token = document.getElementById("s-token").value.trim();
          persist();
          await refreshWhoami();
          await hydrate();
        });
      }
    } catch (err) {
      showError(err.message || err);
    }
  }

  function draw() {
    renderNav();
    document.getElementById("view").innerHTML = (views[state.page] || homeView)();
    hydrate();
  }

  function routeFromHash() {
    const raw = (location.hash || "#/home").replace(/^#\/?/, "");
    const page = raw.split("?")[0] || "home";
    state.page = views[page] ? page : "home";
  }

  document.getElementById("tenant-select").onchange = () => {
    state.tenantId = document.getElementById("tenant-select").value;
    persist();
    hydrate();
  };

  window.addEventListener("hashchange", () => {
    routeFromHash();
    draw();
  });

  document.getElementById("menu-btn").onclick = () => {
    document.getElementById("sidebar").scrollIntoView({ behavior: "smooth" });
  };

  routeFromHash();
  renderNav();
  refreshWhoami()
    .then(loadTenants)
    .catch((err) => showError(err.message || err))
    .finally(draw);
})();
