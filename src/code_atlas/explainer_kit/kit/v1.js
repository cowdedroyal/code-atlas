/* atlas-explainer kit v1 — behaviour. No dependencies, no network.
 *
 * Host protocol (postMessage; the card runs in a sandboxed iframe):
 *   host → card  {type:"atlas:theme", theme:"light"|"dark", lang:"zh", scale:1.0}
 *   card → host  {type:"atlas:ready", kit:1}
 *                {type:"atlas:height", height:<px>}          whenever the content size changes
 *                {type:"atlas:open", target:"<path>#<symbol>"} when an atlas://… link is clicked
 */
(function () {
  "use strict";
  var KIT = 1;
  var root = document.documentElement;
  var inFrame = window.parent !== window;
  function post(msg) { if (inFrame) window.parent.postMessage(msg, "*"); }

  var I18N = {
    zh: { real: "真实数据", illustrative: "示意数据", undeclared: "未声明数据来源", from: "来自", at: "读取于" },
    en: { real: "Real data", illustrative: "Illustrative data", undeclared: "Source not declared", from: "from", at: "read" }
  };
  function t(key) {
    var lang = (root.getAttribute("lang") || "en").toLowerCase().indexOf("zh") === 0 ? "zh" : "en";
    return I18N[lang][key];
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ---- host messages ---------------------------------------------------- */
  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || d.type !== "atlas:theme") return;
    if (d.theme === "light" || d.theme === "dark") root.setAttribute("data-theme", d.theme);
    if (typeof d.lang === "string" && /^[a-z]{2,3}(-[A-Za-z0-9]+)?$/.test(d.lang)) {
      root.setAttribute("lang", d.lang);
      relabel();
    }
    if (typeof d.scale === "number" && d.scale >= 0.5 && d.scale <= 3) root.style.setProperty("--ex-scale", String(d.scale));
  });

  /* ---- <ex-table> : data sample with a mandatory provenance badge -------------- */
  function renderTable(node) {
    if (node.__ex) return;
    node.__ex = true;
    var table = node.querySelector(":scope > table");
    if (table) {
      var wrap = el("div", "ex-scroll");
      node.replaceChild(wrap, table);
      wrap.appendChild(table);
    }
    var badge = el("div", "ex-badge");
    node.insertBefore(badge, node.firstChild);
    var cap = node.getAttribute("caption");
    if (cap) node.appendChild(el("div", "ex-cap", cap));
    labelTable(node);
  }
  function labelTable(node) {
    var badge = node.querySelector(":scope > .ex-badge");
    if (!badge) return;
    var src = node.getAttribute("source");
    badge.textContent = "";
    badge.appendChild(el("b", null, src === "real" || src === "illustrative" ? t(src) : t("undeclared")));
    var origin = node.getAttribute("origin"), at = node.getAttribute("at");
    if (origin) badge.appendChild(el("span", null, t("from") + " " + origin));
    if (at) badge.appendChild(el("span", null, t("at") + " " + at));
  }

  /* ---- <ex-stage> : collapsible stage; numbering and the rail are CSS --------- */
  function renderStage(node) {
    if (node.__ex) return;
    node.__ex = true;
    var body = el("div", "ex-body");
    var io = el("div", "ex-io");
    var ins = [], outs = [], rest = [];
    Array.prototype.slice.call(node.childNodes).forEach(function (c) {
      var name = c.nodeType === 1 ? c.tagName.toLowerCase() : "";
      (name === "ex-in" ? ins : name === "ex-out" ? outs : rest).push(c);
    });
    ins.concat(outs).forEach(function (c) { io.appendChild(c); });
    if (io.childNodes.length) body.appendChild(io);
    rest.forEach(function (c) { body.appendChild(c); });

    var head = el("button", "ex-head");
    head.type = "button";
    head.appendChild(el("span", "ex-title", node.getAttribute("title") || ""));
    if (node.getAttribute("tag")) head.appendChild(el("span", "ex-tag", node.getAttribute("tag")));
    head.appendChild(el("span", "ex-caret", "▾"));
    node.removeAttribute("title");     // otherwise the browser shows it as a hover tooltip
    node.appendChild(head);
    node.appendChild(body);

    function set(open) { node.setAttribute("data-open", String(open)); head.setAttribute("aria-expanded", String(open)); }
    set(true);
    head.addEventListener("click", function () { set(node.getAttribute("data-open") === "false"); });
  }

  /* ---- <ex-compare> : highlight what changed between the two sides ----------- */
  var TOKEN = /\s+|[A-Za-z0-9_]+|[^\sA-Za-z0-9_]/g;
  function tokens(s) { return s.match(TOKEN) || []; }
  function lcsKeep(a, b) {            // which tokens of a and b are NOT part of the common subsequence
    var n = a.length, m = b.length, keepA = new Array(n).fill(false), keepB = new Array(m).fill(false);
    if (n * m > 250000) return null;  // too large to diff cheaply: show plain text
    var dp = [], i, j;
    for (i = 0; i <= n; i++) dp.push(new Uint16Array(m + 1));
    for (i = n - 1; i >= 0; i--) for (j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    i = 0; j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { keepA[i] = keepB[j] = true; i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
      else j++;
    }
    return { a: keepA, b: keepB };
  }
  function paint(side, toks, keep, mark) {
    var pre = el("pre");
    var run = "", changed = null;
    function flush() {
      if (!run) return;
      if (changed) { var m = document.createElement(mark); m.textContent = run; pre.appendChild(m); }
      else pre.appendChild(document.createTextNode(run));
      run = "";
    }
    toks.forEach(function (tok, i) {
      var isChange = keep ? !keep[i] && !/^\s+$/.test(tok) : false;
      if (changed === null) changed = isChange;
      if (isChange !== changed && !/^\s+$/.test(tok)) { flush(); changed = isChange; }
      run += tok;
    });
    flush();
    side.textContent = "";
    side.appendChild(pre);
  }
  function renderCompare(node) {
    if (node.__ex) return;
    node.__ex = true;
    var before = node.querySelector(":scope > ex-before"), after = node.querySelector(":scope > ex-after");
    if (!before || !after) return;
    var a = tokens(before.textContent.replace(/^\n+|\s+$/g, "")), b = tokens(after.textContent.replace(/^\n+|\s+$/g, ""));
    var keep = lcsKeep(a, b);
    paint(before, a, keep && keep.a, "del");
    paint(after, b, keep && keep.b, "ins");
  }

  function relabel() { document.querySelectorAll("ex-table").forEach(labelTable); }

  function init() {
    document.querySelectorAll("ex-table").forEach(renderTable);
    document.querySelectorAll("ex-stage").forEach(renderStage);
    document.querySelectorAll("ex-compare").forEach(renderCompare);
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a[href]");
      if (!a) return;
      var href = a.getAttribute("href");
      if (href.indexOf("atlas://") === 0) {
        e.preventDefault();
        post({ type: "atlas:open", target: decodeURIComponent(href.slice("atlas://".length)) });
      } else if (href.charAt(0) !== "#") {
        e.preventDefault();             // nothing else may navigate inside the sandbox
      }
    });
    if (typeof ResizeObserver === "function") {
      var last = 0;
      new ResizeObserver(function () {
        var h = Math.ceil(document.documentElement.getBoundingClientRect().height);
        if (h !== last) { last = h; post({ type: "atlas:height", height: h }); }
      }).observe(document.documentElement);
    }
    post({ type: "atlas:ready", kit: KIT });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
