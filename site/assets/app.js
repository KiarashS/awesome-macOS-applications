/* =====================================================================
   awesome-macOS-applications — the desktop's behaviour
   ---------------------------------------------------------------------
   Everything the page shows comes from data/apps.json, which the GitHub
   Action regenerates whenever the star list changes. No framework, no
   build step.
   ===================================================================== */

(() => {
  "use strict";

  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const el = (tag, props = {}, kids = []) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of [].concat(kids)) if (kid) node.append(kid);
    return node;
  };

  const svg = (id, size = 15, cls = "icon") => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", cls);
    s.setAttribute("width", size);
    s.setAttribute("height", size);
    s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", "#" + id);
    s.append(u);
    return s;
  };

  const fmtStars = (n) =>
    n >= 100000 ? Math.round(n / 1000) + "k"
    : n >= 10000 ? (n / 1000).toFixed(0) + "k"
    : n >= 1000  ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k"
    : String(n);

  const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

  /* ── state ─────────────────────────────────────────────────────── */

  const state = {
    scope: "all",          // "all" | "top" | "fresh" | a category id
    tags: new Set(),
    query: "",
    view: "grid",          // "grid" | "list"
    sort: "stars",         // "stars" | "name" | "updated"
  };

  let DATA = { apps: [], categories: [], tags: [], totals: {}, source: {} };
  let categoryById = new Map();
  let hasDates = false;

  /* ── appearance ────────────────────────────────────────────────── */

  const APPEARANCES = ["auto", "light", "dark"];

  function setAppearance(mode, remember = true) {
    if (!APPEARANCES.includes(mode)) mode = "auto";
    document.documentElement.dataset.appearance = mode;
    if (remember) {
      try { localStorage.setItem("appearance", mode); } catch { /* private mode */ }
    }
    const icon = { auto: "i-auto", light: "i-sun", dark: "i-moon" }[mode];
    $("#cc-btn .appearance-icon use").setAttribute("href", "#" + icon);
    $("#cc-btn").title = `Control Centre — appearance: ${mode[0].toUpperCase()}${mode.slice(1)}`;
    syncMenuChecks();
  }

  function initAppearance() {
    let saved = "auto";
    try { saved = localStorage.getItem("appearance") || "auto"; } catch { /* ignore */ }
    setAppearance(saved, false);
  }

  /* ── Liquid Glass ──────────────────────────────────────────────── */

  // Golden Gate's Appearance pane gained a slider from ultraclear to fully
  // tinted; --glass-level drives every glass surface in the stylesheet.
  function setGlass(level, remember = true) {
    const clamped = Math.min(1, Math.max(0, Number(level)));
    document.documentElement.style.setProperty("--glass-level", String(clamped));
    const slider = $("#glass-slider");
    slider.value = String(Math.round(clamped * 100));
    slider.parentElement.style.setProperty("--fill", `${Math.round(clamped * 100)}%`);
    slider.setAttribute("aria-valuetext",
      clamped < 0.2 ? "Ultraclear" : clamped < 0.45 ? "Mostly clear"
      : clamped < 0.7 ? "Balanced" : clamped < 0.9 ? "Mostly tinted" : "Fully tinted");
    if (remember) {
      try { localStorage.setItem("glass", String(clamped)); } catch { /* ignore */ }
    }
  }

  function initGlass() {
    let saved = 0.55;
    try {
      const stored = localStorage.getItem("glass");
      if (stored !== null && stored !== "") saved = Number(stored);
    } catch { /* ignore */ }
    if (!Number.isFinite(saved)) saved = 0.55;
    setGlass(saved, false);
    $("#glass-slider").addEventListener("input", (e) => setGlass(e.target.value / 100));
  }

  /* ── clock ─────────────────────────────────────────────────────── */

  function startClock() {
    const date = $("#clock-date");
    const time = $("#clock-time");
    const tick = () => {
      const now = new Date();
      date.textContent = now.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
      time.textContent = now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    };
    tick();
    setInterval(tick, 15000);
  }

  /* ── icon colours ──────────────────────────────────────────────── */

  // A stable hue per repository, so an app keeps the same tile colour
  // between visits and while its avatar is still loading.
  function tint(seed) {
    let h = 0;
    for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
    return {
      "--ic-a": `hsl(${h} 78% 62%)`,
      "--ic-b": `hsl(${(h + 34) % 360} 72% 44%)`,
    };
  }

  // Three tiers, best first: the project's own app icon, then the owner
  // avatar, then tinted initials. Each falls through if the image 404s.
  function standInTile(app) {
    const tile = el("span", {
      class: "tile",
      text: app.name.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?",
    });
    Object.entries(tint(app.full_name)).forEach(([k, v]) => tile.style.setProperty(k, v));
    if (app.avatar) {
      tile.append(el("img", {
        src: app.avatar, alt: "", loading: "lazy", decoding: "async",
        onerror: (e) => e.target.remove(),
      }));
    }
    return tile;
  }

  function appIcon(app, big = false) {
    const box = el("span", { class: "appicon" + (big ? " appicon-lg" : "") });
    if (app.icon) {
      // A real app icon already has its own shape, so it is never masked.
      box.append(el("img", {
        class: "real", src: app.icon, alt: "", loading: "lazy", decoding: "async",
        onerror: (e) => { e.target.remove(); box.append(standInTile(app)); },
      }));
    } else {
      box.append(standInTile(app));
    }
    return box;
  }

  /* ── filtering ─────────────────────────────────────────────────── */

  function visibleApps() {
    let apps = DATA.apps.slice();

    if (state.scope === "top") {
      apps.sort((a, b) => b.stars - a.stars);
      apps = apps.slice(0, 25);
    } else if (state.scope === "fresh") {
      apps.sort((a, b) => (b.pushed_at || "").localeCompare(a.pushed_at || ""));
      apps = apps.slice(0, 25);
    } else if (state.scope !== "all") {
      apps = apps.filter((a) => a.category === state.scope);
    }

    // Tags narrow the result: an app has to carry every selected tag.
    if (state.tags.size) {
      apps = apps.filter((a) => {
        const own = new Set(a.tags.map((t) => t.toLowerCase()));
        for (const t of state.tags) if (!own.has(t.toLowerCase())) return false;
        return true;
      });
    }

    const q = state.query.trim().toLowerCase();
    if (q) {
      const terms = q.split(/\s+/);
      apps = apps.filter((a) => {
        const hay = [a.name, a.owner, a.description, a.language, a.tags.join(" "), a.topics.join(" ")]
          .join(" ").toLowerCase();
        return terms.every((t) => hay.includes(t));
      });
    }

    if (state.sort === "name") apps.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
    else if (state.sort === "updated") apps.sort((a, b) => (b.pushed_at || "").localeCompare(a.pushed_at || ""));
    else apps.sort((a, b) => b.stars - a.stars || a.name.localeCompare(b.name));

    return apps;
  }

  function scopeLabel() {
    if (state.scope === "all") return "All Applications";
    if (state.scope === "top") return "Most Starred";
    if (state.scope === "fresh") return "Recently Updated";
    return categoryById.get(state.scope)?.label ?? "Applications";
  }

  function scopeBlurb() {
    if (state.scope === "top") return "The 25 most-starred repositories in the list.";
    if (state.scope === "fresh") return "The 25 repositories with the most recent commits.";
    const cat = categoryById.get(state.scope);
    return cat ? cat.blurb : `Every application in ${DATA.source.user}'s "${DATA.source.list}" star list.`;
  }

  /* ── rendering ─────────────────────────────────────────────────── */

  function renderSidebar() {
    $("#count-all").textContent = DATA.apps.length;

    const box = $("#side-categories");
    box.replaceChildren(
      ...DATA.categories.map((cat) =>
        el("button", {
          class: "side-row" + (state.scope === cat.id ? " is-on" : ""),
          "data-scope": cat.id,
          title: cat.blurb,
          style: `--row-color:${cat.color || "var(--accent)"}`,
          onclick: () => setScope(cat.id),
        }, [
          svg("i-" + cat.icon, 15, "icon side-glyph"),
          el("span", { class: "side-label", text: cat.label }),
          el("span", { class: "side-count", text: String(cat.count) }),
        ])
      )
    );

    $$("[data-scope]", $(".side-group")).forEach((b) =>
      b.classList.toggle("is-on", b.dataset.scope === state.scope)
    );

    const cloud = $("#side-tags");
    cloud.replaceChildren(
      ...DATA.tags.map((t) =>
        el("button", {
          class: "tagbtn" + (state.tags.has(t.name) ? " is-on" : ""),
          "aria-pressed": state.tags.has(t.name) ? "true" : "false",
          title: `${plural(t.count, "app", "apps")} tagged ${t.name}`,
          onclick: () => toggleTag(t.name),
        }, [
          document.createTextNode(t.name),
          el("span", { class: "tagbtn-count", text: String(t.count) }),
        ])
      )
    );

    $("#clear-tags").hidden = state.tags.size === 0;
  }

  function tagChip(name) {
    return el("button", {
      class: "tagbtn" + (state.tags.has(name) ? " is-on" : ""),
      "aria-pressed": state.tags.has(name) ? "true" : "false",
      title: `Filter by ${name}`,
      onclick: (e) => { e.stopPropagation(); toggleTag(name); },
    }, [document.createTextNode(name)]);
  }

  function appCard(app) {
    const card = el("article", {
      class: "app",
      role: "listitem",
      tabindex: "0",
      "data-id": app.full_name,
      "aria-label": `${app.name}. ${app.description}`,
      onclick: () => openSheet(app),
      onkeydown: (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openSheet(app); }
      },
    }, [
      appIcon(app),
      el("div", { class: "app-body" }, [
        el("div", { class: "app-top" }, [
          el("span", { class: "app-name", text: app.name }),
          el("span", { class: "app-stars", title: `${app.stars.toLocaleString()} stars` }, [
            svg("i-star", 10), document.createTextNode(fmtStars(app.stars)),
          ]),
        ]),
        el("p", { class: "app-desc", text: app.description }),
        el("div", { class: "app-tags" }, app.tags.map((t) => tagChip(t))),
      ]),
    ]);
    return card;
  }

  let listHead = null;

  function renderApps() {
    const apps = visibleApps();
    const grid = $("#apps");

    grid.classList.toggle("as-list", state.view === "list");

    if (!listHead) {
      listHead = el("div", { class: "list-head", "aria-hidden": "true" }, [
        el("span", { text: "" }),
        el("span", { text: "Name" }),
        el("span", { text: "Description" }),
        el("span", { text: "Tags" }),
        el("span", { text: "Stars" }),
      ]);
      grid.before(listHead);
    }
    listHead.hidden = state.view !== "list" || apps.length === 0;

    grid.replaceChildren(...apps.map(appCard));

    $("#empty").hidden = apps.length > 0;
    $("#loading").hidden = true;

    // window chrome
    $("#window-title").textContent = scopeLabel();
    $("#window-sub").textContent = scopeBlurb();
    document.title = state.scope === "all"
      ? "Applications — awesome macOS applications"
      : `${scopeLabel()} — awesome macOS applications`;

    const shown = apps.length;
    const total = DATA.apps.length;
    $("#status-left").textContent = shown === total
      ? `${plural(total, "application", "applications")}`
      : `${shown} of ${total} applications`;
    $("#status-right").textContent =
      `${apps.reduce((n, a) => n + a.stars, 0).toLocaleString()} stars · sorted by ` +
      { stars: "stars", name: "name", updated: "last updated" }[state.sort];

    renderFilterBar();
    renderSidebar();
    renderDock();
    syncMenuChecks();
  }

  function renderFilterBar() {
    const bar = $("#filterbar");
    const tokens = $("#tokens");
    const chips = [];

    if (state.scope !== "all") {
      chips.push(el("button", {
        class: "token",
        title: "Remove this filter",
        onclick: () => setScope("all"),
      }, [document.createTextNode(scopeLabel()), svg("i-xmark", 9)]));
    }
    for (const t of state.tags) {
      chips.push(el("button", {
        class: "token",
        title: "Remove this tag",
        onclick: () => toggleTag(t),
      }, [document.createTextNode(t), svg("i-xmark", 9)]));
    }
    if (state.query.trim()) {
      chips.push(el("button", {
        class: "token",
        title: "Clear the search",
        onclick: () => { $("#search").value = ""; state.query = ""; commit(); },
      }, [document.createTextNode(`“${state.query.trim()}”`), svg("i-xmark", 9)]));
    }

    tokens.replaceChildren(...chips);
    bar.hidden = chips.length === 0;
  }

  function renderDock() {
    const inner = $("#dock-inner");
    if (inner.dataset.built === "1") {
      $$(".dock-item", inner).forEach((b) =>
        b.classList.toggle("is-on", b.dataset.scope === state.scope)
      );
      return;
    }

    const item = (opts, kids) => {
      const node = el(opts.href ? "a" : "button", {
        class: "dock-item" + (opts.scope === state.scope ? " is-on" : ""),
        "data-scope": opts.scope,
        href: opts.href,
        target: opts.href ? "_blank" : null,
        rel: opts.href ? "noopener" : null,
        "aria-label": opts.label,
        onclick: opts.onclick,
      }, kids);
      if (opts.color) {
        node.style.setProperty("--ic-a", `color-mix(in srgb, ${opts.color} 72%, white)`);
        node.style.setProperty("--ic-b", opts.color);
      } else {
        Object.entries(tint(opts.seed || opts.label)).forEach(([k, v]) => node.style.setProperty(k, v));
      }
      node.append(el("span", { class: "dock-tip", text: opts.label }));
      return node;
    };

    const top = DATA.categories.slice().sort((a, b) => b.count - a.count).slice(0, 8);

    inner.replaceChildren(
      item({ label: "All Applications", scope: "all", color: "#0a84ff", onclick: () => setScope("all") },
           [svg("i-all", 22)]),
      item({ label: "Most Starred", scope: "top", color: "#ffb800", onclick: () => setScope("top") },
           [svg("i-star", 22)]),
      el("span", { class: "dock-sep" }),
      ...top.map((c) =>
        item({ label: `${c.label} (${c.count})`, scope: c.id, color: c.color, onclick: () => setScope(c.id) },
             [svg("i-" + c.icon, 22)])
      ),
      el("span", { class: "dock-sep" }),
      item({ label: "The star list on GitHub", color: "#5b5b60", href: DATA.source.url }, [svg("i-github", 24)]),
    );
    inner.dataset.built = "1";
  }

  /* ── the Get Info sheet ────────────────────────────────────────── */

  let lastFocus = null;

  function openSheet(app) {
    lastFocus = document.activeElement;

    $("#sheet-icon").replaceWith(Object.assign(appIcon(app, true), { id: "sheet-icon" }));
    $("#sheet-name").textContent = app.name;
    $("#sheet-owner").textContent = `${app.full_name}${app.archived ? " · archived" : ""}`;
    $("#sheet-desc").textContent = app.description;

    const cat = categoryById.get(app.category);
    const meta = [
      ["Category", cat ? cat.label : "—"],
      ["Stars", app.stars.toLocaleString()],
      ["Forks", app.forks ? app.forks.toLocaleString() : "—"],
      ["Language", app.language || "—"],
      ["License", app.license || "—"],
      ["Last commit", app.pushed_at ? new Date(app.pushed_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—"],
    ];
    $("#sheet-meta").replaceChildren(
      ...meta.map(([k, v]) => el("div", {}, [el("dt", { text: k }), el("dd", { text: v })]))
    );

    const chips = app.tags.map((t) =>
      el("button", { class: "tagbtn", title: `Show everything tagged ${t}`, onclick: () => { closeSheet(); setTagOnly(t); } },
         [document.createTextNode(t)])
    );
    if (cat) {
      const chip = el("button", {
        class: "tagbtn is-category", title: `Show the ${cat.label} category`,
        onclick: () => { closeSheet(); setScope(cat.id); },
      }, [svg("i-" + cat.icon, 11), document.createTextNode(cat.label)]);
      if (cat.color) chip.style.setProperty("--chip-color", cat.color);
      chips.unshift(chip);
    }
    $("#sheet-tags").replaceChildren(...chips);

    $("#sheet-repo").href = app.url;
    const home = $("#sheet-home");
    if (app.homepage && /^https?:\/\//.test(app.homepage)) {
      home.href = app.homepage;
      home.hidden = false;
    } else {
      home.hidden = true;
    }

    $("#window").classList.add("is-inactive");
    $("#scrim").hidden = false;
    $("#sheet-close").focus();
  }

  function closeSheet() {
    $("#scrim").hidden = true;
    if ($("#about-scrim").hidden) $("#window").classList.remove("is-inactive");
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }

  function openAbout(which = "about") {
    const body = $("#about-body");
    const src = DATA.source;
    if (which === "shortcuts") {
      body.replaceChildren(el("div", { html: `
        <h3 id="about-title">Keyboard Shortcuts</h3>
        <div class="about-grid">
          <kbd>/</kbd><span>Jump to the search field</span>
          <kbd>⌘F</kbd><span>Jump to the search field</span>
          <kbd>⌘1</kbd><span>Icon view</span>
          <kbd>⌘2</kbd><span>List view</span>
          <kbd>⌘0</kbd><span>Show all applications, clear every filter</span>
          <kbd>Esc</kbd><span>Close a panel, or clear the search</span>
          <kbd>↩</kbd><span>Open the highlighted application</span>
        </div>` }));
    } else {
      body.replaceChildren(el("div", { html: `
        <h3 id="about-title">About this list</h3>
        <p>A browser for the ${DATA.apps.length} macOS applications in
           <a href="${src.url}" target="_blank" rel="noopener">${src.user}'s “${src.list}” star list</a> on GitHub,
           sorted into ${DATA.categories.length} categories and ${DATA.tags.length} tags.</p>
        <p>Click a tag anywhere — on a card, in the sidebar, in an app's info panel — to filter by it.
           Tags stack, so picking two shows only the apps carrying both. The sidebar picks the category,
           the search field narrows by name, description, language or topic.</p>
        <p>Nothing here is written by hand. A GitHub Action re-reads the star list every night at
           03:00 Asia/Tehran, pulls fresh metadata for each repository from the GitHub API, and
           republishes this page. Last run: <strong>${new Date(DATA.generated_at).toLocaleString()}</strong>.</p>
        <p><a href="https://github.com/KiarashS/awesome-macOS-applications" target="_blank" rel="noopener">Source repository →</a></p>` }));
    }
    $("#window").classList.add("is-inactive");
    $("#about-scrim").hidden = false;
    $("#about-close").focus();
  }

  /* ── menus ─────────────────────────────────────────────────────── */

  function closeMenus() {
    $("#menus").hidden = true;
    $$(".menu-panel").forEach((p) => p.classList.remove("is-open"));
    $("#control-centre").classList.remove("is-open");
    $$(".menu-title, #cc-btn").forEach((b) => b.setAttribute("aria-expanded", "false"));
  }

  function openControlCentre() {
    const anchor = $("#cc-btn");
    const open = anchor.getAttribute("aria-expanded") === "true";
    closeMenus();
    if (open) return;
    const panel = $("#control-centre");
    $("#menus").hidden = false;
    panel.classList.add("is-open");
    anchor.setAttribute("aria-expanded", "true");
    const rect = anchor.getBoundingClientRect();
    // Right-aligned to the button, like every other menu-bar popover.
    panel.style.left =
      Math.max(6, Math.min(rect.right - panel.offsetWidth, window.innerWidth - panel.offsetWidth - 6)) + "px";
  }

  function openMenu(name, anchor) {
    closeMenus();
    const panel = $(`.menu-panel[data-panel="${name}"]`);
    if (!panel) return;
    $("#menus").hidden = false;
    panel.classList.add("is-open");
    anchor.setAttribute("aria-expanded", "true");
    const rect = anchor.getBoundingClientRect();
    panel.style.left = Math.max(6, Math.min(rect.left, window.innerWidth - panel.offsetWidth - 6)) + "px";
  }

  function wireControlCentre() {
    $$("#control-centre [data-act]").forEach((b) =>
      b.addEventListener("click", () => runAction(b.dataset.act))
    );
  }

  function syncMenuChecks() {
    const checks = {
      "view-grid": state.view === "grid",
      "view-list": state.view === "list",
      "sort-stars": state.sort === "stars",
      "sort-name": state.sort === "name",
      "sort-updated": state.sort === "updated",
    };
    for (const [act, on] of Object.entries(checks)) {
      const b = $(`[data-act="${act}"]`);
      if (b) b.setAttribute("aria-checked", on ? "true" : "false");
    }
    const mode = document.documentElement.dataset.appearance;
    for (const m of APPEARANCES) {
      const b = $(`[data-act="appearance-${m}"]`);
      if (b) b.setAttribute("aria-checked", mode === m ? "true" : "false");
    }
  }

  function buildGoMenu() {
    $("#menu-go").replaceChildren(
      el("button", { role: "menuitem", text: "All Applications", onclick: () => setScope("all") }),
      el("button", { role: "menuitem", text: "Most Starred", onclick: () => setScope("top") }),
      el("hr"),
      // Text rows, no glyphs: Golden Gate cut back on icons in menus.
      ...DATA.categories.map((c) =>
        el("button", { role: "menuitem", onclick: () => setScope(c.id) }, [
          document.createTextNode(c.label),
          el("span", { class: "menu-count", text: String(c.count) }),
        ])
      )
    );
  }

  function runAction(act) {
    const jump = { "view-grid": () => setView("grid"), "view-list": () => setView("list") };
    if (act in jump) return jump[act]();
    if (act.startsWith("sort-")) { state.sort = act.slice(5); return commit(); }
    if (act.startsWith("appearance-")) return setAppearance(act.slice(11));
    if (act === "reset") return resetAll();
    if (act === "focus-search") return $("#search").focus();
    if (act === "control-centre") return openControlCentre();
    if (act === "about") return openAbout("about");
    if (act === "shortcuts") return openAbout("shortcuts");
    if (act === "toggle-sidebar") return $("#window").classList.toggle("no-sidebar");
  }

  /* ── state transitions ─────────────────────────────────────────── */

  function setScope(scope) {
    state.scope = scope;
    $("#content").scrollTop = 0;
    if (window.innerWidth <= 900) {
      $("#window").classList.remove("show-sidebar");
      $("#sidebar-toggle").setAttribute("aria-expanded", "false");
    }
    commit();
  }

  function toggleTag(name) {
    state.tags.has(name) ? state.tags.delete(name) : state.tags.add(name);
    commit();
  }

  function setTagOnly(name) {
    state.tags = new Set([name]);
    state.scope = "all";
    commit();
  }

  function setView(view) {
    state.view = view;
    $$(".seg").forEach((b) => b.classList.toggle("is-on", b.dataset.view === view));
    try { localStorage.setItem("view", view); } catch { /* ignore */ }
    commit();
  }

  function resetAll() {
    state.scope = "all";
    state.tags.clear();
    state.query = "";
    $("#search").value = "";
    commit();
  }

  /* ── URL <-> state, so a filtered view is linkable ─────────────── */

  let applyingHash = false;

  function writeHash() {
    if (applyingHash) return;
    const p = new URLSearchParams();
    if (state.scope !== "all") p.set("in", state.scope);
    if (state.tags.size) p.set("tags", [...state.tags].join(","));
    if (state.query.trim()) p.set("q", state.query.trim());
    if (state.sort !== "stars") p.set("sort", state.sort);
    const hash = p.toString();
    const url = hash ? "#" + hash : location.pathname + location.search;
    history.replaceState(null, "", url);
  }

  function readHash() {
    const p = new URLSearchParams(location.hash.replace(/^#/, ""));
    const known = new Set(["all", "top", "fresh", ...DATA.categories.map((c) => c.id)]);
    const scope = p.get("in");
    state.scope = scope && known.has(scope) ? scope : "all";

    const valid = new Map(DATA.tags.map((t) => [t.name.toLowerCase(), t.name]));
    state.tags = new Set(
      (p.get("tags") || "").split(",").map((t) => valid.get(t.trim().toLowerCase())).filter(Boolean)
    );

    state.query = p.get("q") || "";
    $("#search").value = state.query;

    const sort = p.get("sort");
    if (["stars", "name", "updated"].includes(sort)) state.sort = sort;
  }

  function commit() {
    writeHash();
    renderApps();
  }

  /* ── wiring ────────────────────────────────────────────────────── */

  function wire() {
    // menu bar
    $$(".menu-title").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = btn.getAttribute("aria-expanded") === "true";
        open ? closeMenus() : openMenu(btn.dataset.menu, btn);
      });
      btn.addEventListener("mouseenter", () => {
        if ($$(".menu-title[aria-expanded='true']").length) openMenu(btn.dataset.menu, btn);
      });
    });
    $("#cc-btn").addEventListener("click", (e) => { e.stopPropagation(); openControlCentre(); });
    // Clicks inside the Control Centre must not dismiss it.
    $("#control-centre").addEventListener("click", (e) => e.stopPropagation());
    $("#control-centre").addEventListener("input", (e) => e.stopPropagation());

    $("#menus").addEventListener("click", (e) => {
      const menuitem = e.target.closest("[role='menuitem']");
      const item = e.target.closest("[data-act]");
      if (!menuitem && !item) return;
      // Dismiss the menu first, then act, so an action that opens a panel of
      // its own is not undone by its own dismissal. Then stop the click here:
      // the document-level handler that closes menus would otherwise fire on
      // the way up and shut the panel that just opened.
      if (menuitem) closeMenus();
      if (item) runAction(item.dataset.act);
      e.stopPropagation();
    });
    document.addEventListener("click", closeMenus);

    // toolbar
    $$(".seg").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));

    let debounce;
    $("#search").addEventListener("input", (e) => {
      clearTimeout(debounce);
      const value = e.target.value;
      debounce = setTimeout(() => { state.query = value; commit(); }, 110);
    });
    $("#menubar-search").addEventListener("click", (e) => {
      e.stopPropagation();
      $("#search").focus();
    });

    // sidebar
    const toggle = $("#sidebar-toggle");
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const on = $("#window").classList.toggle("show-sidebar");
      toggle.setAttribute("aria-expanded", on ? "true" : "false");
    });
    // Tapping the app list dismisses the drop-down sidebar on narrow screens.
    $("#content").addEventListener("click", () => {
      if ($("#window").classList.contains("show-sidebar")) {
        $("#window").classList.remove("show-sidebar");
        toggle.setAttribute("aria-expanded", "false");
      }
    }, true);

    $$("[data-scope]", $(".sidebar")).forEach((b) =>
      b.addEventListener("click", () => setScope(b.dataset.scope))
    );
    $("#clear-tags").addEventListener("click", () => { state.tags.clear(); commit(); });
    $("#reset-all").addEventListener("click", resetAll);
    $("#empty-reset").addEventListener("click", resetAll);

    // The lights are decoration, but the green one should still do something.
    // There are two sets — one in the sidebar, one in the toolbar for narrow
    // screens — and only ever one of them is visible.
    $$(".lights").forEach((set) =>
      set.addEventListener("click", (e) => {
        if (e.target.classList.contains("zoom")) $("#window").classList.toggle("no-sidebar");
      })
    );

    // sheets
    $("#sheet-close").addEventListener("click", closeSheet);
    $("#scrim").addEventListener("click", (e) => { if (e.target.id === "scrim") closeSheet(); });
    $("#about-close").addEventListener("click", () => { $("#about-scrim").hidden = true; });
    $("#about-scrim").addEventListener("click", (e) => {
      if (e.target.id === "about-scrim") $("#about-scrim").hidden = true;
    });

    // keyboard
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);

      if (e.key === "Escape") {
        if (!$("#scrim").hidden) return closeSheet();
        if (!$("#about-scrim").hidden) return ($("#about-scrim").hidden = true);
        if (!$("#menus").hidden) return closeMenus();
        if (typing) { $("#search").value = ""; state.query = ""; $("#search").blur(); commit(); }
        return;
      }
      if (!typing && e.key === "/" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        return $("#search").focus();
      }
      if (e.metaKey || e.ctrlKey) {
        if (e.key === "f") { e.preventDefault(); return $("#search").focus(); }
        if (e.key === "1") { e.preventDefault(); return setView("grid"); }
        if (e.key === "2") { e.preventDefault(); return setView("list"); }
        if (e.key === "0") { e.preventDefault(); return resetAll(); }
      }
    });

    window.addEventListener("hashchange", () => {
      applyingHash = true;
      readHash();
      renderApps();
      applyingHash = false;
    });
  }

  /* ── boot ──────────────────────────────────────────────────────── */

  async function boot() {
    initAppearance();
    initGlass();
    wireControlCentre();
    startClock();

    try { const v = localStorage.getItem("view"); if (v) state.view = v; } catch { /* ignore */ }
    $$(".seg").forEach((b) => b.classList.toggle("is-on", b.dataset.view === state.view));

    let payload;
    try {
      const res = await fetch("data/apps.json", { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      payload = await res.json();
    } catch (err) {
      $("#loading").replaceChildren(el("div", { html:
        `<p class="empty-title">Could not load the app list</p>
         <p class="empty-sub">${String(err.message || err)} — the nightly build may not have run yet.</p>` }));
      return;
    }

    DATA = payload;
    categoryById = new Map(DATA.categories.map((c) => [c.id, c]));
    hasDates = DATA.apps.some((a) => a.pushed_at);

    $("#menubar-count").textContent = `${DATA.apps.length} apps`;
    $("#generated-at").textContent = DATA.generated_at
      ? `Updated ${new Date(DATA.generated_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
      : "";
    $("#menu-source-link").href = DATA.source.url;

    // "Recently Updated" is meaningless until the API run fills in commit dates.
    if (!hasDates) $('[data-scope="fresh"]').hidden = true;

    buildGoMenu();
    readHash();
    wire();
    renderApps();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
