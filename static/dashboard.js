(function () {
  "use strict";

  var D = window.__DASHBOARD__;
  var CFG = D.config;
  var BASE = CFG.template.layout;
  var SUM_FIELDS = [
    "total_tokens",
    "cost_usd",
    "calls",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
  ];
  var FILTER_ORDER = ["project", "model", "provider"];

  function resizePlots() {
    if (window.Plotly && Plotly.Plots) {
      Array.prototype.forEach.call(
        document.querySelectorAll(".plot-slot:not([hidden])"),
        function (el) {
          Plotly.Plots.resize(el);
        },
      );
    }
  }

  // Phase 5c filters mutate this. Until then it is simply every row.
  var state = {
    rows: D.rows,
    allRows: D.rows,
    excluded: { project: [], model: [], provider: [] },
    limit: {
      project: CFG.filterPage,
      model: CFG.filterPage,
      provider: CFG.filterPage,
    },
    search: { project: "", model: "", provider: "" },
    datePreset: CFG.defaultDatePreset,
    metric: "tokens",
  };

  var STORAGE_KEY = "opencode-dashboard-filters";
  var modelOwner = {};
  var groupTotals = {};

  function storageLoad() {
    try {
      return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function storageSave() {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          excluded: state.excluded,
          datePreset: state.datePreset,
          metric: state.metric,
          collapsed: document
            .getElementById("layout")
            .classList.contains("sidebar-collapsed"),
        }),
      );
    } catch (e) {
      /* private mode or file:// restrictions; filters still work */
    }
  }

  // Index the data once: which models belong to which gateway, and how many
  // tokens each filter item accounts for.
  function indexRows() {
    var i, r;
    for (i = 0; i < D.rows.length; i++) {
      r = D.rows[i];
      modelOwner[r.model] = r.provider;
      ["project", "model", "provider"].forEach(function (g) {
        var key = r[g];
        if (!groupTotals[g]) {
          groupTotals[g] = {};
        }
        if (!groupTotals[g][key]) {
          groupTotals[g][key] = 0;
        }
        groupTotals[g][key] += r.total_tokens;
      });
    }
  }

  function itemsFor(group) {
    return Object.keys(groupTotals[group]).sort(function (a, b) {
      return groupTotals[group][b] - groupTotals[group][a];
    });
  }

  function excludedSet(group) {
    var set = new Set(state.excluded[group] || []);
    // A model is also out of scope while its gateway is excluded. The stored
    // model choice is left alone so re-including the gateway restores it.
    if (group === "model") {
      state.excluded.provider.forEach(function (p) {
        itemsFor("model").forEach(function (m) {
          if (modelOwner[m] === p) {
            set.add(m);
          }
        });
      });
    }
    return set;
  }

  function visibleItems(group) {
    var out = [];
    var excluded = excludedSet(group);
    itemsFor(group).forEach(function (name) {
      if (
        state.search[group] &&
        name.toLowerCase().indexOf(state.search[group]) === -1
      ) {
        return;
      }
      out.push(name);
    });
    return out;
  }

  function buildFilterPanel(group) {
    var list = document.querySelector('[data-list-for="' + group + '"]');
    if (!list) {
      return;
    }
    var names = visibleItems(group);
    var excluded = excludedSet(group);
    var shown = names.slice(0, state.limit[group]);
    var blockedByProvider = group === "model";

    var html = shown
      .map(function (name) {
        var blocked =
          blockedByProvider &&
          state.excluded.provider.indexOf(modelOwner[name]) !== -1;
        var checked = !excluded.has(name);
        return (
          '<div class="filter-item' +
          (blocked ? " provider-excluded" : "") +
          '"' +
          ' data-value="' +
          esc(name) +
          '">' +
          '<label><input type="checkbox" data-check="' +
          group +
          '"' +
          ' value="' +
          esc(name) +
          '"' +
          (checked ? " checked" : "") +
          (blocked ? " disabled" : "") +
          ">" +
          '<span class="filter-name">' +
          esc(name) +
          "</span>" +
          '<span class="filter-count">' +
          fmtTokens(groupTotals[group][name]) +
          "</span></label>" +
          '<button type="button" class="filter-only" data-only="' +
          group +
          '" data-value="' +
          esc(name) +
          '">Only</button>' +
          (blocked
            ? '<div class="filter-blocked">Excluded by provider</div>'
            : "") +
          "</div>"
        );
      })
      .join("");

    list.innerHTML = html || '<div class="filter-empty">No matches</div>';

    var more = document.querySelector(
      '[data-action="more"][data-group="' + group + '"]',
    );
    if (more) {
      more.hidden = names.length <= state.limit[group];
    }
  }

  function applyFilters() {
    var projects = excludedSet("project");
    var models = excludedSet("model");
    var providers = excludedSet("provider");
    var cutoff = dateCutoff();
    state.rows = state.allRows.filter(function (r) {
      if (
        projects.has(r.project) ||
        models.has(r.model) ||
        providers.has(r.provider)
      ) {
        return false;
      }
      // Date presets are anchored to the latest date in the exports, not to
      // today, so a stale CSV still reads as a complete window.
      return cutoff === null || r.date >= cutoff;
    });
  }

  // The latest date present in the data, as a "YYYY-MM-DD" string. Used as the
  // anchor for date-range presets and for period-over-period deltas.
  function latestDate() {
    var best = "";
    for (var i = 0; i < D.rows.length; i++) {
      if (D.rows[i].date > best) {
        best = D.rows[i].date;
      }
    }
    return best;
  }

  // Inclusive "YYYY-MM-DD" cutoff for the selected preset, or null for all time.
  function dateCutoff() {
    var preset = state.datePreset;
    if (!preset || preset === "all") {
      return null;
    }
    var days = parseInt(preset, 10);
    if (!days || days <= 0) {
      return null;
    }
    var end = latestDate();
    if (!end) {
      return null;
    }
    var endMs = Date.parse(end + "T00:00:00Z");
    var startMs = endMs - (days - 1) * 86400000;
    return new Date(startMs).toISOString().slice(0, 10);
  }

  // The window immediately before the selected range, for period-over-period
  // deltas. Returns [start, end] inclusive, or null when there is no window of
  // the same length to compare against: all time, or a preset whose previous
  // window would reach past the earliest date in the data. A full window
  // compared against a shorter one inflates the percentage by the length
  // difference alone, so the badge is suppressed rather than shown with a
  // caveat the reader would have to remember when comparing cards.
  function previousWindow() {
    var start = dateCutoff();
    if (start === null) {
      return null;
    }
    var end = latestDate();
    var startMs = Date.parse(start + "T00:00:00Z");
    var endMs = Date.parse(end + "T00:00:00Z");
    var span = endMs - startMs + 86400000;
    var prevEndMs = startMs - 86400000;
    var prevStartMs = prevEndMs - span + 86400000;
    var first = earliestDate();
    if (first) {
      var firstMs = Date.parse(first + "T00:00:00Z");
      if (prevStartMs < firstMs) {
        return null;
      }
    }
    if (prevEndMs < prevStartMs) {
      return null;
    }
    return [
      new Date(prevStartMs).toISOString().slice(0, 10),
      new Date(prevEndMs).toISOString().slice(0, 10),
    ];
  }

  function earliestDate() {
    var best = "";
    for (var i = 0; i < D.rows.length; i++) {
      if (!best || D.rows[i].date < best) {
        best = D.rows[i].date;
      }
    }
    return best;
  }

  // ---- Data-freshness and payload-size signals -------------------------
  var SIGNALS_KEY = "opencode-dashboard-signals";

  function signalsLoad() {
    try {
      return JSON.parse(window.localStorage.getItem(SIGNALS_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function signalsSave(value) {
    try {
      window.localStorage.setItem(SIGNALS_KEY, JSON.stringify(value));
    } catch (e) {
      /* private mode or file:// restrictions */
    }
  }

  // Whole days between a "YYYY-MM-DD" string and now, or null when absent.
  function daysSince(dateString) {
    if (!dateString) {
      return null;
    }
    var then = Date.parse(dateString + "T00:00:00Z");
    if (isNaN(then)) {
      return null;
    }
    return Math.floor((Date.now() - then) / 86400000);
  }

  function formatBytes(bytes) {
    if (!bytes || bytes < 0) {
      return "0 KB";
    }
    if (bytes < 1024) {
      return bytes + " B";
    }
    if (bytes < 1048576) {
      return (bytes / 1024).toFixed(1) + " KB";
    }
    return (bytes / 1048576).toFixed(2) + " MB";
  }

  function rowCountLabel(d) {
    if (d.rows.length === CFG.rowCount) {
      return group(CFG.rowCount) + " rows";
    }
    return group(d.rows.length) + " of " + group(CFG.rowCount) + " rows";
  }

  function signalHtml(id, text) {
    return (
      '<div class="signal" data-signal="' +
      id +
      '"><span>' +
      text +
      "</span>" +
      '<button type="button" class="signal-dismiss" data-dismiss-signal="' +
      id +
      '" aria-label="Dismiss this notice">&times;</button></div>'
    );
  }

  function renderDataSignals() {
    var host = $("data-signals");
    if (!host) {
      return;
    }
    var saved = signalsLoad();
    var out = [];

    // Stale export: compare the latest date in the data with the viewer's clock.
    var through = latestDate();
    var age = daysSince(through);
    if (
      age !== null &&
      CFG.staleAfterDays > 0 &&
      age > CFG.staleAfterDays &&
      saved.staleThrough !== through
    ) {
      out.push(
        signalHtml(
          "stale",
          "This export is <b>" +
            age +
            " day" +
            (age === 1 ? "" : "s") +
            "</b> old (data through " +
            esc(through) +
            "). Re-run " +
            "<code>python extract_usage.py</code> to refresh it.",
        ),
      );
    }

    // Large payload: the client re-slices every row on each filter change, so
    // the cost shows up as slow loads and sluggish filters.
    if (CFG.rowCount >= CFG.rowWarnRows && saved.scaleRows !== CFG.rowCount) {
      var heavy = CFG.rowCount >= CFG.rowWarnRowsHigh;
      out.push(
        signalHtml(
          "scale",
          "<b>" +
            (heavy ? "Very large export" : "Large export") +
            ":</b> " +
            group(CFG.rowCount) +
            " rows (" +
            formatBytes(CFG.payloadBytes) +
            " embedded). " +
            (heavy
              ? "Expect slow loads and sluggish filters. "
              : "The page can get slow to load and filters sluggish. ") +
            "Export a shorter date range, or trim unused columns from the payload.",
        ),
      );
    }

    host.innerHTML = out.join("");
  }

  function wireSignals() {
    var host = $("data-signals");
    if (!host) {
      return;
    }
    host.addEventListener("click", function (event) {
      var button = event.target.closest("[data-dismiss-signal]");
      if (!button) {
        return;
      }
      var key = button.getAttribute("data-dismiss-signal");
      var saved = signalsLoad();
      if (key === "stale") {
        saved.staleThrough = latestDate();
      }
      if (key === "scale") {
        saved.scaleRows = CFG.rowCount;
      }
      signalsSave(saved);
      renderDataSignals();
    });
  }

  function setExcluded(group, names) {
    state.excluded[group] = names;
  }

  // Update a segmented control's pressed state to match the model.
  function syncSeg(attr, value) {
    document.querySelectorAll("[" + attr + "]").forEach(function (button) {
      button.setAttribute(
        "aria-pressed",
        button.getAttribute(attr) === value ? "true" : "false",
      );
    });
  }

  function setDatePreset(preset) {
    state.datePreset = preset || CFG.defaultDatePreset;
    syncSeg("data-date-preset", state.datePreset);
  }

  function setMetric(metric) {
    state.metric = metric === "cost" ? "cost" : "tokens";
    syncSeg("data-metric", state.metric);
  }

  function selectionHtml(d) {
    var parts = FILTER_ORDER.map(function (g) {
      var total = itemsFor(g).length;
      var excluded = excludedSet(g).size;
      return (
        "<b>" +
        (total - excluded) +
        "</b> of " +
        total +
        " " +
        g +
        (total === 1 ? "" : "s")
      );
    });
    var dates = d.daily.map(function (row) {
      return row.key;
    });
    var span = "";
    if (dates.length) {
      span =
        (dates.length > 1
          ? dates[0] + " to " + dates[dates.length - 1]
          : dates[0]) + " &middot; ";
    }
    return (
      span +
      parts.join(" &middot; ") +
      " &middot; " +
      state.rows.length.toLocaleString() +
      " of " +
      state.allRows.length.toLocaleString() +
      " rows"
    );
  }

  function refreshFilters(d) {
    FILTER_ORDER.forEach(buildFilterPanel);
    var selection = document.getElementById("selection");
    if (selection) {
      selection.innerHTML = selectionHtml(d);
    }
  }

  function wireFilters() {
    var layout = document.getElementById("layout");
    var toggle = document.getElementById("sidebar-toggle");

    if (storageLoad().collapsed) {
      layout.classList.add("sidebar-collapsed");
      toggle.setAttribute("aria-expanded", "false");
    }
    toggle.addEventListener("click", function () {
      var collapsed = layout.classList.toggle("sidebar-collapsed");
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      storageSave();
      resizePlots();
    });

    var reset = document.getElementById("reset-filters");
    if (reset) {
      reset.addEventListener("click", function () {
        FILTER_ORDER.forEach(function (g) {
          state.excluded[g] = [];
          state.search[g] = "";
          state.limit[g] = CFG.filterPage;
          var box = document.querySelector('[data-search-for="' + g + '"]');
          if (box) {
            box.value = "";
          }
        });
        setDatePreset(CFG.defaultDatePreset);
        setMetric("tokens");
        // The rows table keeps its own search/sort/page, which are not part of
        // the persisted filter state - so a reset that left them in place could
        // still show "No rows match this search" right after clearing every
        // other filter.
        rowsState.search = "";
        rowsState.sortKey = "date";
        rowsState.sortDir = "desc";
        rowsState.page = 0;
        var rowsBox = document.getElementById("rows-search");
        if (rowsBox) {
          rowsBox.value = "";
        }
        storageSave();
        refresh();
      });
    }

    // Date-range presets and the ranking metric are segmented button groups;
    // both re-render on click and persist across reloads.
    document.querySelectorAll("[data-date-preset]").forEach(function (button) {
      button.addEventListener("click", function () {
        setDatePreset(button.getAttribute("data-date-preset"));
        storageSave();
        refresh();
      });
    });
    document.querySelectorAll("[data-metric]").forEach(function (button) {
      button.addEventListener("click", function () {
        setMetric(button.getAttribute("data-metric"));
        storageSave();
        refresh();
      });
    });

    FILTER_ORDER.forEach(function (group) {
      var search = document.querySelector('[data-search-for="' + group + '"]');
      if (search) {
        search.addEventListener("input", function () {
          state.search[group] = search.value.trim().toLowerCase();
          state.limit[group] = CFG.filterPage;
          refresh();
        });
      }

      document
        .querySelectorAll('[data-action][data-group="' + group + '"]')
        .forEach(function (button) {
          button.addEventListener("click", function () {
            var action = button.getAttribute("data-action");
            if (action === "more") {
              state.limit[group] += CFG.filterPage;
              refresh();
            } else if (action === "all") {
              // Includes items hidden by search or pagination.
              setExcluded(group, []);
              refresh();
            } else if (action === "none") {
              setExcluded(group, itemsFor(group));
              refresh();
            }
          });
        });
    });

    // Delegated: the lists are rebuilt on every render.
    document
      .getElementById("sidebar")
      .addEventListener("click", function (event) {
        var only = event.target.closest("[data-only]");
        if (!only) {
          return;
        }
        var group = only.getAttribute("data-only");
        var name = only.getAttribute("data-value");
        setExcluded(
          group,
          itemsFor(group).filter(function (item) {
            return item !== name;
          }),
        );
        storageSave();
        refresh();
      });

    document
      .getElementById("sidebar")
      .addEventListener("change", function (event) {
        var box = event.target;
        if (!box.matches('input[type="checkbox"]')) {
          return;
        }
        var group = box.getAttribute("data-check");
        var name = box.value;
        var excluded = state.excluded[group].filter(function (item) {
          return item !== name;
        });
        if (!box.checked) {
          excluded.push(name);
        }
        setExcluded(group, excluded);
        storageSave();
        refresh();
      });
  }

  function $(id) {
    return document.getElementById(id);
  }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (c) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[c];
    });
  }

  function group(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function fmtTokens(value) {
    var v = Math.abs(value);
    if (v >= 1e9) {
      return (value / 1e9).toFixed(1) + "B";
    }
    if (v >= 1e6) {
      return (value / 1e6).toFixed(1) + "M";
    }
    if (v >= 1e3) {
      return (value / 1e3).toFixed(1) + "K";
    }
    return group(Math.round(value));
  }

  function fmtCost(value) {
    if (value === 0) {
      return "$0.00";
    }
    if (Math.abs(value) < 0.01) {
      return "$" + value.toFixed(4);
    }
    return "$" + value.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function fmtCostBar(value) {
    if (value === 0) {
      return "$0.00";
    }
    if (Math.abs(value) < 0.01) {
      return "$" + value.toFixed(4);
    }
    return "$" + value.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function bump(map, key, row) {
    var entry = map.get(key);
    if (!entry) {
      entry = { key: key };
      for (var i = 0; i < SUM_FIELDS.length; i++) {
        entry[SUM_FIELDS[i]] = 0;
      }
      map.set(key, entry);
    }
    for (var j = 0; j < SUM_FIELDS.length; j++) {
      entry[SUM_FIELDS[j]] += Number(row[SUM_FIELDS[j]]) || 0;
    }
    return entry;
  }

  function byTokensDesc(map) {
    return Array.from(map.values()).sort(function (a, b) {
      return b.total_tokens - a.total_tokens;
    });
  }

  function axis(base, extra) {
    var merged = {};
    var source = BASE[base] || {};
    for (var k in source) {
      if (Object.prototype.hasOwnProperty.call(source, k)) {
        merged[k] = source[k];
      }
    }
    for (var j in extra) {
      if (Object.prototype.hasOwnProperty.call(extra, j)) {
        merged[j] = extra[j];
      }
    }
    return merged;
  }

  function layout(extra, height) {
    var out = {};
    for (var k in BASE) {
      if (Object.prototype.hasOwnProperty.call(BASE, k)) {
        out[k] = BASE[k];
      }
    }
    out.height = height;
    return Object.assign(out, extra || {});
  }

  // Plotly does not reserve room for textposition="outside" when scaling an
  // axis, so the label on the longest bar would run past the card edge.
  function paddedRange(maximum) {
    if (!maximum || maximum <= 0) {
      return undefined;
    }
    return [0, maximum * (1 + CFG.labelHeadroom)];
  }

  function maxOf(entries, field) {
    var best = 0;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i][field] > best) {
        best = entries[i][field];
      }
    }
    return best;
  }

  function subtitleHtml(d) {
    var through = d.daily.length
      ? d.daily[d.daily.length - 1].key
      : "no dated usage";
    return (
      "Data through " +
      through +
      " &middot; generated " +
      CFG.generatedAt +
      " &middot; " +
      rowCountLabel(d) +
      " &middot; " +
      formatBytes(CFG.payloadBytes) +
      " embedded &middot; local session-store exports"
    );
  }

  function aggregate(rows) {
    var totals = { tokens: 0, cost: 0, calls: 0, cacheRead: 0 };
    var categories = {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      reasoning_tokens: 0,
    };
    var daily = new Map(),
      models = new Map(),
      projects = new Map(),
      providers = new Map(),
      efforts = new Map();
    var sessions = new Set();
    var costRows = 0;

    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      totals.tokens += r.total_tokens;
      totals.cost += r.cost_usd;
      totals.calls += r.calls;
      totals.cacheRead += r.cache_read_tokens;
      if (r.cost_usd > 0) {
        costRows += 1;
      }
      sessions.add(r.session_id);
      for (var k in categories) {
        categories[k] += r[k] || 0;
      }
      bump(daily, r.date, r);
      bump(models, r.model, r);
      bump(projects, r.project, r);
      bump(providers, r.provider, r);
      bump(efforts, r.variant || "n/a", r);
    }

    var allModels = byTokensDesc(models);
    var costed = modelsBy(costRows, models);

    return {
      rows: rows,
      totals: totals,
      categories: categories,
      sessions: sessions.size,
      costRows: costRows,
      daily: Array.from(daily.values()).sort(function (a, b) {
        return a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
      }),
      models: allModels,
      modelCount: allModels.length,
      projects: byTokensDesc(projects),
      projectCount: projects.size,
      providers: byTokensDesc(providers),
      costed: costed,
      freeModelCount: allModels.length - costed.length,
      efforts: byTokensDesc(efforts),
    };
  }

  function modelsBy(_costRows, models) {
    var out = [];
    models.forEach(function (entry) {
      if (entry.cost_usd > 0) {
        out.push(entry);
      }
    });
    return out.sort(function (a, b) {
      return b.cost_usd - a.cost_usd;
    });
  }

  function providerColors(providers) {
    var names = providers
      .map(function (p) {
        return p.key;
      })
      .sort();
    var colors = {};
    names.forEach(function (name, index) {
      colors[name] =
        CFG.categoricalColors[index % CFG.categoricalColors.length];
    });
    return colors;
  }

  // Aggregate only the rows falling in [start, end], using the same facet
  // exclusions already applied to state.rows, so a delta compares like with
  // like rather than against an unfiltered baseline.
  function totalsInWindow(start, end) {
    var projects = excludedSet("project");
    var models = excludedSet("model");
    var providers = excludedSet("provider");
    var out = { tokens: 0, cost: 0, calls: 0, cacheRead: 0, sessions: 0 };
    var seen = new Set();
    for (var i = 0; i < D.rows.length; i++) {
      var r = D.rows[i];
      if (
        projects.has(r.project) ||
        models.has(r.model) ||
        providers.has(r.provider)
      ) {
        continue;
      }
      if (r.date < start || r.date > end) {
        continue;
      }
      out.tokens += r.total_tokens;
      out.cost += r.cost_usd;
      out.calls += r.calls;
      out.cacheRead += r.cache_read_tokens;
      seen.add(r.session_id);
    }
    out.sessions = seen.size;
    return out;
  }

  // A period-over-period change, or null when there is nothing to compare (no
  // previous window, or a zero baseline, which has no defined percentage).
  function deltaOf(current, previous) {
    if (previous === null || previous === undefined || previous === 0) {
      return null;
    }
    return (current - previous) / previous;
  }

  function deltaHtml(delta) {
    if (delta === null) {
      return "";
    }
    var pct = Math.abs(delta * 100);
    if (pct < 0.05) {
      return (
        '<div class="delta flat">no change' +
        '<span class="delta-note"> vs previous period</span></div>'
      );
    }
    var up = delta > 0;
    var cls = up ? "up" : "down";
    var arrow = up ? "&#9650;" : "&#9660;";
    return (
      '<div class="delta ' +
      cls +
      '">' +
      arrow +
      " " +
      pct.toFixed(0) +
      '%<span class="delta-note"> vs previous period</span></div>'
    );
  }

  function kpiHtml(d) {
    var prev = null;
    var win = previousWindow();
    if (win) {
      prev = totalsInWindow(win[0], win[1]);
    }
    var deltas = prev
      ? {
          tokens: deltaOf(d.totals.tokens, prev.tokens),
          cost: deltaOf(d.totals.cost, prev.cost),
          calls: deltaOf(d.totals.calls, prev.calls),
          sessions: deltaOf(d.sessions, prev.sessions),
          cacheRead: deltaOf(d.totals.cacheRead, prev.cacheRead),
        }
      : null;
    var items = [
      ["Input + output tokens", group(d.totals.tokens), null, "tokens"],
      [
        "Recorded cost (USD)",
        fmtCost(d.totals.cost),
        "Reported by OpenCode, not an invoice",
        "cost",
      ],
      ["Assistant messages", group(d.totals.calls), null, "calls"],
      ["Sessions", group(d.sessions), null, "sessions"],
      [
        "Cache read tokens",
        group(d.totals.cacheRead),
        "Independent counter, not part of the total above",
        "cacheRead",
      ],
    ];
    return items
      .map(function (item) {
        var badge = deltas ? deltaHtml(deltas[item[3]]) : "";
        return (
          '<div class="kpi"><div class="kpi-label">' +
          esc(item[0]) +
          '</div><div class="kpi-value">' +
          esc(item[1]) +
          "</div>" +
          badge +
          (item[2] ? '<div class="kpi-note">' + esc(item[2]) + "</div>" : "") +
          "</div>"
        );
      })
      .join("");
  }

  function insightHtml(d) {
    var out = [];
    if (d.projects.length && d.totals.tokens) {
      var lead = d.projects[0];
      out.push([
        "info",
        "<b>" +
          esc(lead.key) +
          "</b> accounts for <b>" +
          ((lead.total_tokens / d.totals.tokens) * 100).toFixed(0) +
          "%</b> of recorded tokens.",
      ]);
    }
    if (d.models.length && d.totals.tokens) {
      var lm = d.models[0];
      out.push([
        "info",
        "Leading model: <b>" +
          esc(lm.key) +
          "</b> at <b>" +
          ((lm.total_tokens / d.totals.tokens) * 100).toFixed(0) +
          "%</b> of tokens.",
      ]);
    }
    if (d.totals.cacheRead && d.totals.tokens) {
      var ratio = d.totals.cacheRead / d.totals.tokens;
      if (ratio >= 1) {
        out.push([
          "info",
          "Cache reads are <b>" +
            ratio.toFixed(0) +
            "x</b> the input + output total. Most work is re-reading context, " +
            "not generating it.",
        ]);
      }
    }
    if (d.rows.length && d.costRows < d.rows.length) {
      out.push([
        d.costRows === 0 ? "warn" : "info",
        "Cost was recorded for <b>" +
          group(d.costRows) +
          " of " +
          group(d.rows.length) +
          "</b> exported rows (" +
          ((d.costRows / d.rows.length) * 100).toFixed(1) +
          "%). Providers only report cost for billable models; <b>" +
          d.freeModelCount +
          "</b> of " +
          d.modelCount +
          " models recorded none.",
      ]);
    }
    return out
      .map(function (item) {
        return (
          '<div class="insight ' +
          item[0] +
          '"><div>' +
          item[1] +
          "</div></div>"
        );
      })
      .join("");
  }

  function trendSpec(entries, field, unit) {
    if (!entries.length) {
      return null;
    }
    var maxEntry = entries[0];
    for (var i = 1; i < entries.length; i++) {
      if (entries[i][field] > maxEntry[field]) {
        maxEntry = entries[i];
      }
    }
    return {
      traces: [
        {
          type: "scatter",
          mode: "lines+markers",
          x: entries.map(function (e) {
            return e.key;
          }),
          y: entries.map(function (e) {
            return e[field];
          }),
          line: { color: CFG.aggregateColor, width: 2.5 },
          marker: { color: CFG.aggregateColor, size: 6 },
          fill: "tozeroy",
          fillcolor: "rgba(0, 114, 178, 0.10)",
          hovertemplate: "%{x}<br>%{y:,}<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: {
            text: unit === "cost" ? "Recorded cost by day" : "Tokens by day",
          },
          xaxis: axis("xaxis", { type: "date" }),
          yaxis: axis("yaxis", {
            title: {
              text:
                unit === "cost"
                  ? "Recorded cost (USD)"
                  : "Input + output tokens",
            },
          }),
          annotations: [
            {
              x: maxEntry.key,
              y: maxEntry[field],
              text:
                "<b>" +
                (unit === "cost"
                  ? fmtCost(maxEntry[field])
                  : fmtTokens(maxEntry[field])) +
                "</b>",
              showarrow: false,
              yshift: 16,
              font: { size: 11, color: "#59636e" },
            },
          ],
        },
        380,
      ),
    };
  }

  function compositionSpec(d) {
    var keep = [
      "input_tokens",
      "output_tokens",
      "cache_write_tokens",
      "reasoning_tokens",
    ];
    var labels = [],
      values = [],
      colors = [],
      best = 0;
    for (var i = 0; i < keep.length; i++) {
      labels.push(CFG.categoryLabels[keep[i]]);
      values.push(d.categories[keep[i]]);
      colors.push(CFG.categoryColors[keep[i]]);
      if (d.categories[keep[i]] > best) {
        best = d.categories[keep[i]];
      }
    }
    return {
      traces: [
        {
          type: "bar",
          x: labels,
          y: values,
          text: values.map(function (v) {
            return group(v);
          }),
          texttemplate: "%{text}",
          textposition: "outside",
          cliponaxis: false,
          textfont: { color: "#374151", size: 11 },
          marker: { color: colors },
          hovertemplate: "%{x}<br>%{y:,} tokens<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: { text: "Token categories (input, output, reasoning)" },
          xaxis: axis("xaxis", { title: { text: "Token category" } }),
          yaxis: axis("yaxis", {
            title: { text: "Tokens" },
            range: paddedRange(best),
          }),
        },
        340,
      ),
    };
  }

  function rankingSpec(entries, title, noun, colourByVendor, byCost) {
    var n = Math.min(entries.length, CFG.topN);
    // Sort on the effective metric. The caller resolves byCost so that a cost
    // view with no recorded cost falls back to tokens rather than drawing a
    // wall of empty bars under a "by tokens" title.
    var sorted = entries.slice().sort(function (a, b) {
      return (
        b[byCost ? "cost_usd" : "total_tokens"] -
        a[byCost ? "cost_usd" : "total_tokens"]
      );
    });
    var slice = sorted.slice(0, n);
    if (!slice.length) {
      return null;
    }
    var values = slice.map(function (e) {
      return e[byCost ? "cost_usd" : "total_tokens"];
    });
    var colours = colourByVendor
      ? slice.map(function (e) {
          return CFG.vendorColors[CFG.modelVendor[e.key]] || CFG.aggregateColor;
        })
      : CFG.aggregateColor;
    return {
      traces: [
        {
          type: "bar",
          orientation: "h",
          x: values,
          y: slice.map(function (e) {
            return e.key;
          }),
          text: values.map(function (v) {
            return byCost ? fmtCostBar(v) : fmtTokens(v);
          }),
          texttemplate: "%{text}",
          textposition: "outside",
          cliponaxis: false,
          textfont: { color: "#374151", size: 11 },
          marker: { color: colours },
          customdata: slice.map(function (e) {
            return [CFG.modelVendor[e.key] || ""];
          }),
          hovertemplate: byCost
            ? "%{y}<br>$%{x:,.4f}<extra></extra>"
            : "%{y}<br>%{x:,} tokens<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: { text: title },
          xaxis: axis("xaxis", {
            title: {
              text: byCost ? "Estimated cost (USD)" : "Input + output tokens",
            },
            range: paddedRange(Math.max.apply(null, values)),
          }),
          yaxis: axis("yaxis", {
            categoryorder: "total ascending",
            title: { text: noun },
          }),
        },
        Math.max(260, 34 * n + 120),
      ),
    };
  }

  // A ranking by cost is meaningless while every row records zero, and a single
  // priced model among many is a stray bar rather than a ranking - the same
  // standard the cost section applies. Below that, fall back to tokens rather
  // than drawing a wall of empty bars.
  function hasMetricData(d) {
    if (state.metric !== "cost") {
      return true;
    }
    return d.costed.length >= CFG.minCostModels;
  }

  function providerSpec(d) {
    if (!d.providers.length) {
      return null;
    }
    var colors = providerColors(d.providers);
    var total = d.providers.reduce(function (acc, p) {
      return acc + p.total_tokens;
    }, 0);
    var texts = d.providers.map(function (p) {
      var share = total ? p.total_tokens / total : 0;
      return share >= CFG.minLabelShare
        ? (share * 100).toFixed(1) + "%<br>" + fmtTokens(p.total_tokens)
        : "";
    });
    return {
      traces: [
        {
          type: "pie",
          hole: 0.45,
          labels: d.providers.map(function (p) {
            return p.key;
          }),
          values: d.providers.map(function (p) {
            return p.total_tokens;
          }),
          text: texts,
          texttemplate: "%{text}",
          textposition: "inside",
          textfont: { color: "#ffffff", size: 11 },
          marker: {
            colors: d.providers.map(function (p) {
              return colors[p.key];
            }),
            line: { width: 0 },
          },
          hovertemplate:
            "%{label}<br>%{value:,} tokens (%{percent})<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: { text: "Tokens by provider" },
          legend: Object.assign({}, BASE.legend || {}, {
            orientation: "h",
            yanchor: "top",
            y: -0.05,
            xanchor: "center",
            x: 0.5,
            title: { text: "" },
          }),
        },
        380,
      ),
    };
  }

  function costByModelSpec(d) {
    if (!d.costed.length) {
      return null;
    }
    var values = d.costed.map(function (m) {
      return m.cost_usd;
    });
    return {
      traces: [
        {
          type: "bar",
          orientation: "h",
          x: values,
          y: d.costed.map(function (m) {
            return m.key;
          }),
          text: values.map(function (v) {
            return fmtCostBar(v);
          }),
          texttemplate: "%{text}",
          textposition: "outside",
          cliponaxis: false,
          textfont: { color: "#374151", size: 11 },
          marker: { color: CFG.aggregateColor },
          hovertemplate: "%{y}<br>$%{x:,.4f}<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: { text: "Estimated cost by model" },
          xaxis: axis("xaxis", {
            title: { text: "Estimated cost (USD)" },
            range: paddedRange(Math.max.apply(null, values)),
          }),
          yaxis: axis("yaxis", {
            categoryorder: "total ascending",
            title: { text: "" },
          }),
        },
        Math.max(260, 34 * d.costed.length + 120),
      ),
    };
  }

  function efficiencySpec(d) {
    var rows = d.costed.filter(function (m) {
      return m.calls >= CFG.minCallsForEfficiency;
    });
    rows = rows
      .map(function (m) {
        return { key: m.key, value: m.total_tokens / m.cost_usd };
      })
      .sort(function (a, b) {
        return b.value - a.value;
      });
    if (!rows.length) {
      return null;
    }
    var values = rows.map(function (r) {
      return r.value;
    });
    return {
      traces: [
        {
          type: "bar",
          orientation: "h",
          x: values,
          y: rows.map(function (r) {
            return r.key;
          }),
          text: values.map(function (v) {
            return group(Math.round(v)) + " tok/$";
          }),
          texttemplate: "%{text}",
          textposition: "outside",
          cliponaxis: false,
          textfont: { color: "#374151", size: 11 },
          marker: { color: CFG.aggregateColor },
          hovertemplate: "%{y}<br>%{x:,.0f} tokens per $<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: {
            text:
              "Pricing efficiency by model (" +
              CFG.minCallsForEfficiency +
              "+ calls)",
          },
          xaxis: axis("xaxis", {
            title: { text: "Tokens per estimated USD (confirmed calls)" },
            range: paddedRange(Math.max.apply(null, values)),
          }),
          yaxis: axis("yaxis", {
            categoryorder: "total ascending",
            title: { text: "" },
          }),
        },
        Math.max(260, 34 * rows.length + 120),
      ),
    };
  }

  // Cache reuse: the share of a model's prompt tokens that were served from the
  // provider's cache rather than resent. Computed against input + cache read,
  // because those are the tokens that had to be present in the prompt; a model
  // with no cache reads shows 0% and reads as "always resend".
  function cacheReuseSpec(d) {
    var rows = d.models
      .filter(function (m) {
        return (
          m.calls >= CFG.minCallsForCacheReuse &&
          m.input_tokens + m.cache_read_tokens > 0
        );
      })
      .map(function (m) {
        return {
          key: m.key,
          share: m.cache_read_tokens / (m.input_tokens + m.cache_read_tokens),
          cache: m.cache_read_tokens,
          calls: m.calls,
        };
      })
      .sort(function (a, b) {
        return b.share - a.share;
      });
    if (!rows.length) {
      return null;
    }
    var values = rows.map(function (r) {
      return r.share;
    });
    return {
      traces: [
        {
          type: "bar",
          orientation: "h",
          x: values,
          y: rows.map(function (r) {
            return r.key;
          }),
          text: values.map(function (v) {
            return (v * 100).toFixed(0) + "%";
          }),
          texttemplate: "%{text}",
          textposition: "outside",
          cliponaxis: false,
          textfont: { color: "#374151", size: 11 },
          marker: {
            color: rows.map(function (r) {
              return (
                CFG.vendorColors[CFG.modelVendor[r.key]] || CFG.aggregateColor
              );
            }),
          },
          customdata: rows.map(function (r) {
            return [r.cache, r.calls];
          }),
          hovertemplate:
            "%{y}<br>%{x:.0%} from cache<br>" +
            "%{customdata[0]:,} cache-read tokens over " +
            "%{customdata[1]} calls<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: {
            text:
              "Cache reuse by model (" + CFG.minCallsForCacheReuse + "+ calls)",
          },
          xaxis: axis("xaxis", {
            title: { text: "Share of prompt tokens served from cache" },
            tickformat: ".0%",
            range: paddedRange(Math.max.apply(null, values)),
          }),
          yaxis: axis("yaxis", {
            categoryorder: "total ascending",
            title: { text: "" },
          }),
        },
        Math.max(260, 34 * rows.length + 120),
      ),
    };
  }

  function effortSpec(d) {
    if (d.efforts.length < 2) {
      return null;
    }
    var withTokens = d.efforts.filter(function (e) {
      return e.total_tokens > 0;
    });
    if (withTokens.length < 2) {
      return null;
    }
    var values = d.efforts.map(function (e) {
      return e.total_tokens;
    });
    return {
      traces: [
        {
          type: "bar",
          x: d.efforts.map(function (e) {
            return e.key;
          }),
          y: values,
          text: values.map(function (v) {
            return group(v);
          }),
          texttemplate: "%{text}",
          textposition: "outside",
          cliponaxis: false,
          textfont: { color: "#374151", size: 11 },
          marker: {
            color: d.efforts.map(function (e) {
              return CFG.effortColors[e.key] || CFG.unknownColor;
            }),
          },
          hovertemplate: "%{x}<br>%{y:,} tokens<extra></extra>",
        },
      ],
      layout: layout(
        {
          title: { text: "Tokens by reasoning effort" },
          xaxis: axis("xaxis", {
            title: { text: "Configured reasoning effort" },
          }),
          yaxis: axis("yaxis", {
            title: { text: "Input + output tokens" },
            range: paddedRange(Math.max.apply(null, values)),
          }),
        },
        Math.max(260, 34 * d.efforts.length + 120),
      ),
    };
  }

  function present(id, spec) {
    var plotEl = $("plot-" + id);
    var card = document.querySelector('[data-slot="' + id + '"]');
    if (!plotEl || !card) {
      return;
    }
    if (spec) {
      card.hidden = false;
      plotEl.hidden = false;
      Plotly.react(plotEl, spec.traces, spec.layout, CFG.plotlyConfig);
    } else {
      Plotly.purge(plotEl);
      plotEl.innerHTML = "";
      plotEl.hidden = true;
      card.hidden = true;
    }
  }

  function presentNotice(id, show, html) {
    var card = document.querySelector('[data-slot="' + id + '"]');
    var emptyEl = document.querySelector('[data-empty-for="' + id + '"]');
    if (!card || !emptyEl) {
      return;
    }
    card.hidden = !show;
    emptyEl.hidden = !show;
    emptyEl.innerHTML = show ? html || "" : "";
  }

  function costEmpty(d) {
    return (
      "<b>Not enough priced usage to rank yet.</b> Cost was recorded for " +
      "<b>" +
      d.costed.length +
      " of " +
      d.modelCount +
      "</b> models (" +
      group(d.costRows) +
      " of " +
      group(d.rows.length) +
      " rows, " +
      (d.rows.length
        ? ((d.costRows / d.rows.length) * 100).toFixed(1)
        : "0.0") +
      "% coverage). The remaining " +
      d.freeModelCount +
      " models recorded no cost, which usually means a free tier or an " +
      "unreported provider. Both charts appear here once at least " +
      CFG.minCostModels +
      " models carry cost."
    );
  }

  // A single priced model makes a stray bar rather than a ranking, so the gate
  // covers the whole section rather than each chart separately.
  function enoughPricedModels(d) {
    return d.costed.length >= CFG.minCostModels;
  }

  function effortEmpty() {
    return (
      "<b>No reasoning effort to compare.</b> Every message in this export " +
      "used the provider default, so there is nothing to rank. The chart " +
      "appears once two or more effort levels are recorded."
    );
  }

  function cacheReuseEmpty(d) {
    var few = d.models.filter(function (m) {
      return m.calls >= CFG.minCallsForCacheReuse;
    }).length;
    if (!few) {
      return (
        "<b>No model has enough calls to rank cache reuse.</b> A model " +
        "seen on a couple of calls says nothing stable about its cache " +
        "behaviour, so this chart appears once at least one model reaches " +
        CFG.minCallsForCacheReuse +
        " calls."
      );
    }
    return (
      "<b>No cache reuse recorded.</b> None of the models with " +
      CFG.minCallsForCacheReuse +
      "+ calls reported any cache-read tokens, so " +
      "every prompt was resent in full rather than served from the provider's " +
      "cache."
    );
  }

  function footerHtml(d) {
    var out = [
      "<p>Cache read is an independent counter reported separately from " +
        "input and output; it is not part of the input + output total " +
        "above.</p>",
    ];
    if (d.modelCount > CFG.topN) {
      var shown = d.models.slice(0, CFG.topN).reduce(function (a, m) {
        return a + m.total_tokens;
      }, 0);
      out.push(
        "<p>The model ranking shows the top " +
          CFG.topN +
          " of " +
          d.modelCount +
          " models, covering " +
          group(shown) +
          " of " +
          group(d.totals.tokens) +
          " tokens (" +
          (d.totals.tokens
            ? ((shown / d.totals.tokens) * 100).toFixed(1)
            : "0.0") +
          "%).</p>",
      );
    }
    return out.join("");
  }

  // ---- Rows table -------------------------------------------------------
  // A filterable, sortable view over the embedded export rows. It reuses the
  // page's existing filters (state.rows), so what it shows always agrees with
  // the charts above; its own search and sort are local to the table.

  var ROWS_PAGE_SIZE = 25;
  var rowsState = { sortKey: "date", sortDir: "desc", search: "", page: 0 };

  var ROW_COLUMNS = [
    { key: "date", label: "Date", type: "text" },
    { key: "project", label: "Project", type: "text" },
    { key: "model", label: "Model", type: "text" },
    { key: "provider", label: "Provider", type: "text" },
    { key: "variant", label: "Effort", type: "text" },
    { key: "calls", label: "Calls", type: "int" },
    { key: "input_tokens", label: "Input", type: "tokens" },
    { key: "output_tokens", label: "Output", type: "tokens" },
    { key: "cache_read_tokens", label: "Cache read", type: "tokens" },
    { key: "cache_write_tokens", label: "Cache write", type: "tokens" },
    { key: "reasoning_tokens", label: "Reasoning", type: "tokens" },
    { key: "total_tokens", label: "Total", type: "tokens" },
    { key: "cost_usd", label: "Cost", type: "cost" },
    { key: "session_id", label: "Session", type: "text" },
  ];

  function rowsCell(col, row) {
    var value = row[col.key];
    if (col.type === "tokens") {
      return fmtTokens(Number(value) || 0);
    }
    if (col.type === "cost") {
      return fmtCost(Number(value) || 0);
    }
    if (col.type === "int") {
      return group(Number(value) || 0);
    }
    return value === undefined || value === null ? "" : String(value);
  }

  // Rows narrowed by the table's own search box, across every visible column.
  // Both the raw value and the rendered one are matched: the numbers that reach
  // the CSV download are the raw ones, so a figure copied from the CSV should
  // find its row here, while a reader who types the grouped or abbreviated form
  // they see on screen should find it too.
  function searchedRows() {
    var needle = rowsState.search;
    if (!needle) {
      return state.rows;
    }
    return state.rows.filter(function (row) {
      for (var i = 0; i < ROW_COLUMNS.length; i++) {
        var col = ROW_COLUMNS[i];
        var shown = rowsCell(col, row);
        if (shown.toLowerCase().indexOf(needle) !== -1) {
          return true;
        }
        var raw = row[col.key];
        if (raw === undefined || raw === null) {
          continue;
        }
        if (String(raw).toLowerCase().indexOf(needle) !== -1) {
          return true;
        }
      }
      return false;
    });
  }

  function sortedRows(rows) {
    var key = rowsState.sortKey;
    var col = ROW_COLUMNS.filter(function (c) {
      return c.key === key;
    })[0];
    var dir = rowsState.sortDir === "asc" ? 1 : -1;
    var numeric =
      col &&
      (col.type === "int" || col.type === "tokens" || col.type === "cost");
    return rows.slice().sort(function (a, b) {
      if (numeric) {
        return ((Number(a[key]) || 0) - (Number(b[key]) || 0)) * dir;
      }
      var av = a[key] === undefined || a[key] === null ? "" : String(a[key]);
      var bv = b[key] === undefined || b[key] === null ? "" : String(b[key]);
      return av < bv ? -dir : av > bv ? dir : 0;
    });
  }

  function renderRows() {
    var table = $("rows-table");
    if (!table) {
      return;
    }
    var all = sortedRows(searchedRows());
    var pages = Math.max(1, Math.ceil(all.length / ROWS_PAGE_SIZE));
    if (rowsState.page >= pages) {
      rowsState.page = pages - 1;
    }
    if (rowsState.page < 0) {
      rowsState.page = 0;
    }
    var start = rowsState.page * ROWS_PAGE_SIZE;
    var slice = all.slice(start, start + ROWS_PAGE_SIZE);

    var head =
      "<tr>" +
      ROW_COLUMNS.map(function (col) {
        var active = rowsState.sortKey === col.key;
        var arrow = active
          ? rowsState.sortDir === "asc"
            ? " &#9650;"
            : " &#9660;"
          : "";
        var numeric = col.type !== "text";
        return (
          '<th data-sort="' +
          esc(col.key) +
          '"' +
          ' aria-sort="' +
          (active
            ? rowsState.sortDir === "asc"
              ? "ascending"
              : "descending"
            : "none") +
          '"' +
          ' tabindex="0" class="' +
          (numeric ? "num" : "") +
          '">' +
          esc(col.label) +
          arrow +
          "</th>"
        );
      }).join("") +
      "</tr>";
    table.querySelector("thead").innerHTML = head;

    var body;
    if (!slice.length) {
      body =
        '<tr><td class="rows-empty" colspan="' +
        ROW_COLUMNS.length +
        '">' +
        (state.rows.length
          ? "No rows match this search."
          : "No rows match the current dashboard filters.") +
        "</td></tr>";
    } else {
      body = slice
        .map(function (row) {
          return (
            "<tr>" +
            ROW_COLUMNS.map(function (col) {
              var numeric = col.type !== "text";
              return (
                '<td class="' +
                (numeric ? "num" : "") +
                '">' +
                esc(rowsCell(col, row)) +
                "</td>"
              );
            }).join("") +
            "</tr>"
          );
        })
        .join("");
    }
    table.querySelector("tbody").innerHTML = body;

    var meta = $("rows-meta");
    if (meta) {
      meta.textContent =
        all.length === state.rows.length
          ? group(all.length) + " rows"
          : group(all.length) + " of " + group(state.rows.length) + " rows";
    }
    var pageEl = $("rows-page");
    if (pageEl) {
      pageEl.textContent = all.length
        ? "Page " + (rowsState.page + 1) + " of " + pages
        : "No rows";
    }
    var prev = $("rows-prev"),
      next = $("rows-next");
    if (prev) {
      prev.disabled = rowsState.page <= 0;
    }
    if (next) {
      next.disabled = rowsState.page >= pages - 1;
    }
  }

  // Download exactly what the table is showing: current dashboard filters,
  // this table's search, and its sort order, as a CSV.
  function downloadRowsCsv() {
    var all = sortedRows(searchedRows());
    var lines = [
      ROW_COLUMNS.map(function (col) {
        return col.key;
      }).join(","),
    ];
    all.forEach(function (row) {
      lines.push(
        ROW_COLUMNS.map(function (col) {
          var raw = row[col.key];
          if (raw === undefined || raw === null) {
            return "";
          }
          var text = String(raw);
          // A leading =, +, - or @ makes a spreadsheet treat the cell as a
          // formula, and quoting does not stop that - the sheet still evaluates
          // it. Prefix those values with a single quote so the text survives a
          // round trip through Excel or Sheets. None of our numeric columns can
          // be negative (load_data rejects that), so a leading - here is always
          // text that happens to start with a dash.
          if (/^[=+\-@\t\r]/.test(text)) {
            text = "'" + text;
          }
          return /[",\n\r]/.test(text)
            ? '"' + text.replace(/"/g, '""') + '"'
            : text;
        }).join(","),
      );
    });
    var blob = new Blob([lines.join("\r\n")], {
      type: "text/csv;charset=utf-8",
    });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "opencode_usage_filtered.csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  function wireRows() {
    var search = $("rows-search");
    if (search) {
      search.addEventListener("input", function () {
        rowsState.search = search.value.trim().toLowerCase();
        rowsState.page = 0;
        renderRows();
      });
    }
    var table = $("rows-table");
    if (table) {
      table.querySelector("thead").addEventListener("click", function (event) {
        var th = event.target.closest("[data-sort]");
        if (!th) {
          return;
        }
        toggleRowSort(th.getAttribute("data-sort"));
      });
      table
        .querySelector("thead")
        .addEventListener("keydown", function (event) {
          if (event.key !== "Enter" && event.key !== " ") {
            return;
          }
          var th = event.target.closest("[data-sort]");
          if (!th) {
            return;
          }
          event.preventDefault();
          toggleRowSort(th.getAttribute("data-sort"));
        });
    }
    var prev = $("rows-prev"),
      next = $("rows-next");
    if (prev) {
      prev.addEventListener("click", function () {
        rowsState.page -= 1;
        renderRows();
      });
    }
    if (next) {
      next.addEventListener("click", function () {
        rowsState.page += 1;
        renderRows();
      });
    }
    var download = $("rows-download");
    if (download) {
      download.addEventListener("click", downloadRowsCsv);
    }
  }

  function toggleRowSort(key) {
    if (rowsState.sortKey === key) {
      rowsState.sortDir = rowsState.sortDir === "asc" ? "desc" : "asc";
    } else {
      rowsState.sortKey = key;
      rowsState.sortDir = key === "date" ? "desc" : "asc";
    }
    rowsState.page = 0;
    renderRows();
  }

  // The Cost button can stay pressed while the paid-model gate suppresses the
  // cost ranking (too few priced rows), so the button is not read as applied.
  // The notice sits on the toolbar next to the button, not down in the charts.
  function syncMetricNotice(d) {
    var notice = $("metric-notice");
    if (!notice) {
      return;
    }
    if (state.metric === "cost" && !hasMetricData(d)) {
      notice.textContent =
        "Not enough priced usage to rank by cost - " +
        "showing tokens instead.";
      notice.hidden = false;
    } else {
      notice.hidden = true;
      notice.textContent = "";
    }
  }

  function render() {
    var d = aggregate(state.rows);

    $("kpis").innerHTML = kpiHtml(d);
    $("subtitle").innerHTML = subtitleHtml(d);
    renderDataSignals();
    $("insights").innerHTML = insightHtml(d);
    $("footer-notes").innerHTML = footerHtml(d);
    syncMetricNotice(d);
    // The rows table renders on every path, empty or not: it carries its own
    // empty state rather than disappearing with the charts.
    renderRows();

    var charts = document.getElementById("charts");
    if (!d.rows.length) {
      present("trend-tokens", null);
      present("trend-cost", null);
      present("projects", null);
      present("models", null);
      present("composition", null);
      present("providers", null);
      present("cost-by-model", null);
      present("efficiency", null);
      present("effort", null);
      present("cache-reuse", null);
      presentNotice("cost-notice", false);
      presentNotice("effort-notice", false);
      presentNotice("cache-reuse-notice", false);
      if (charts && !charts.querySelector(".no-rows")) {
        var note = document.createElement("p");
        note.className = "no-rows";
        note.textContent =
          "No usage matches the current filters. " +
          "Re-include an item, or use Reset filters.";
        charts.insertBefore(note, charts.firstChild);
      }
      resizePlots();
      return;
    }
    if (charts) {
      var stale = charts.querySelector(".no-rows");
      if (stale) {
        stale.remove();
      }
    }

    present("trend-tokens", trendSpec(d.daily, "total_tokens", "tokens"));
    present("trend-cost", trendSpec(d.daily, "cost_usd", "cost"));

    var byCost = state.metric === "cost" && hasMetricData(d);
    var unit = byCost ? "cost" : "tokens";
    present(
      "projects",
      rankingSpec(
        d.projects,
        "Top " + Math.min(d.projects.length, CFG.topN) + " projects by " + unit,
        "",
        false,
        byCost,
      ),
    );
    // Models are coloured by inferred vendor so a single gateway's slice still
    // separates Google from Anthropic from OpenAI at a glance.
    present(
      "models",
      rankingSpec(
        d.models,
        "Top " + Math.min(d.models.length, CFG.topN) + " models by " + unit,
        "",
        true,
        byCost,
      ),
    );
    present("composition", compositionSpec(d));
    present("providers", providerSpec(d));

    var reuse = cacheReuseSpec(d);
    present("cache-reuse", reuse);
    presentNotice("cache-reuse-notice", !reuse, cacheReuseEmpty(d));

    var priced = enoughPricedModels(d);
    presentNotice("cost-notice", !priced, costEmpty(d));
    present("cost-by-model", priced ? costByModelSpec(d) : null);
    present("efficiency", priced ? efficiencySpec(d) : null);
    present("effort", effortSpec(d));
    presentNotice("effort-notice", !effortSpec(d), effortEmpty());

    resizePlots();
  }

  function refresh() {
    applyFilters();
    var d = aggregate(state.rows);
    refreshFilters(d);
    render();
    storageSave();
  }

  function init() {
    indexRows();
    var saved = storageLoad();
    FILTER_ORDER.forEach(function (g) {
      var stored = (saved.excluded || {})[g];
      // Drop names that no longer exist in this export.
      state.excluded[g] = Array.isArray(stored)
        ? stored.filter(function (name) {
            return name in groupTotals[g];
          })
        : [];
    });
    // Restore the date preset and ranking metric. A preset that is no longer
    // offered (an older export's stored value) falls back to the default.
    var validPreset = CFG.datePresets.some(function (p) {
      return p.id === saved.datePreset;
    });
    setDatePreset(validPreset ? saved.datePreset : CFG.defaultDatePreset);
    setMetric(saved.metric === "cost" ? "cost" : "tokens");
    wireFilters();
    wireRows();
    wireSignals();
    refresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Exposed so tests can drive the render path the way a filter would.
  window.__DASHBOARD_RENDER__ = render;
  window.__DASHBOARD_REFRESH__ = refresh;
})();
