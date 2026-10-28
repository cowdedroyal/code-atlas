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
    zh: { real: "真实数据", illustrative: "示意数据", undeclared: "未声明数据来源", from: "来自", at: "读取于",
          play: "播放", pause: "暂停", frame: "帧", next: "下一步", prev: "上一步", sceneHint: "点一下，看下一步",
          missing: "这张图没有随卡片打包（重新运行 atlas-explainer pack）" },
    en: { real: "Real data", illustrative: "Illustrative data", undeclared: "Source not declared", from: "from", at: "read",
          play: "Play", pause: "Pause", frame: "Frame", next: "Next step", prev: "Previous step", sceneHint: "Click to continue",
          missing: "This picture was not packed into the card (re-run atlas-explainer pack)" }
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
    var was = root.getAttribute("data-theme");
    if (d.theme === "light" || d.theme === "dark") root.setAttribute("data-theme", d.theme);
    if (was !== d.theme) refreshCharts();
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
    labelBadge(node);
  }
  function labelBadge(node) {
    var badge = node.querySelector(":scope > .ex-badge");
    if (!badge) return;
    var src = node.getAttribute("source");
    badge.textContent = "";
    badge.appendChild(el("b", null, src === "real" || src === "illustrative" ? t(src) : t("undeclared")));
    var origin = node.getAttribute("origin"), at = node.getAttribute("at");
    if (origin) badge.appendChild(el("span", null, t("from") + " " + origin));
    if (at) badge.appendChild(el("span", null, t("at") + " " + at));
  }
  function renderBadge(node) {
    node.insertBefore(el("div", "ex-badge"), node.firstChild);
    labelBadge(node);
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

  function relabel() {
    document.querySelectorAll("ex-table, ex-chart, ex-anim").forEach(labelBadge);
  }

  /* ---- baked media: <ex-anim> and <ex-chart> -------------------------------
   * The reader never fetches anything: pack inlines every asset into a JSON registry under
   * #atlas-media-registry, and the elements name an entry with src="atlas-media:<name>". */
  var MEDIA = "atlas-media:";
  var registry = null;

  function mediaFor(node) {
    var ref = (node.getAttribute("src") || "").trim();
    if (ref.indexOf(MEDIA) !== 0) return null;
    if (!registry) {
      registry = {};
      var box = document.getElementById("atlas-media-registry");
      if (box) {
        try { registry = JSON.parse(box.textContent || "{}") || {}; } catch (e) { registry = {}; }
      }
    }
    return registry[ref.slice(MEDIA.length)] || null;
  }
  function mediaMissing(node) {
    node.appendChild(el("p", "ex-missing", t("missing")));
  }
  function prefersReduced() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }
  function currentTheme() {
    var attr = root.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  /* ---- <ex-anim> : a baked frame animation, played here ------------------- */
  function renderAnim(node) {
    if (node.__ex) return;
    node.__ex = true;
    var m = mediaFor(node);
    if (!m || m.kind !== "anim") { mediaMissing(node); renderBadge(node); return; }
    var fig = el("figure", "ex-anim");
    var view = el("div", "ex-anim-view");
    view.style.aspectRatio = m.width + " / " + m.height;
    var canvas = document.createElement("canvas");
    canvas.setAttribute("role", "img");
    view.appendChild(canvas);
    var bar = el("div", "ex-anim-bar");
    var play = el("button", "ex-play");
    play.type = "button";
    var scrub = document.createElement("input");
    scrub.type = "range";
    scrub.className = "ex-scrub";
    scrub.min = "0";
    scrub.max = String(Math.max(0, m.frames - 1));
    scrub.step = "1";
    scrub.value = "0";
    var count = el("span", "ex-count");
    bar.appendChild(play);
    bar.appendChild(scrub);
    bar.appendChild(count);
    fig.appendChild(view);
    fig.appendChild(bar);
    var cap = el("figcaption", "ex-cap");
    fig.appendChild(cap);
    node.appendChild(fig);
    renderBadge(node);

    var img = new Image(), ready = false, frame = 0, playing = false, raf = 0, last = 0, acc = 0;
    var captions = m.captions || [], fallback = node.getAttribute("caption") || "", loop = node.hasAttribute("loop");
    var step = m.ms || Math.max(16, Math.round(1000 / (m.fps || 12)));

    function caption() { return captions[frame] || fallback; }
    function draw() {
      var dpr = window.devicePixelRatio || 1;
      var w = view.clientWidth || m.width;
      var h = Math.round(w * m.height / Math.max(1, m.width));
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = w + "px";
        canvas.style.height = h + "px";
      }
      var ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (ready) {
        var sx = (frame % m.columns) * m.width, sy = Math.floor(frame / m.columns) * m.height;
        ctx.drawImage(img, sx, sy, m.width, m.height, 0, 0, canvas.width, canvas.height);
      }
      canvas.setAttribute("aria-label", caption());
      cap.textContent = caption();
      scrub.value = String(frame);
      count.textContent = (frame + 1) + " / " + m.frames;
      play.textContent = playing ? "❚❚" : "▶";
      play.setAttribute("aria-label", (playing ? t("pause") : t("play")) + " · " + t("frame") + " " + (frame + 1));
    }
    function setFrame(i) { frame = (i + m.frames) % m.frames; draw(); }
    function stop() { playing = false; cancelAnimationFrame(raf); raf = 0; draw(); }
    function tick(now) {
      if (!playing) return;
      if (!last) last = now;
      acc += now - last;
      last = now;
      while (acc >= (m.durations ? m.durations[frame] : step)) {
        acc -= m.durations ? m.durations[frame] : step;
        if (frame + 1 >= m.frames && !loop) { frame = m.frames - 1; acc = 0; stop(); return; }
        frame = (frame + 1) % m.frames;
      }
      draw();
      raf = requestAnimationFrame(tick);
    }
    function start() {
      if (playing) return;
      if (!loop && frame + 1 >= m.frames) frame = 0;
      playing = true;
      last = 0;
      acc = 0;
      raf = requestAnimationFrame(tick);
      draw();
    }
    play.addEventListener("click", function () { if (playing) stop(); else start(); });
    scrub.setAttribute("aria-label", t("frame"));
    scrub.addEventListener("input", function () { var next = Number(scrub.value); stop(); setFrame(next); });
    view.addEventListener("click", function () { if (playing) stop(); else start(); });
    img.onload = function () { ready = true; draw(); };
    img.src = m.sheet;
    node.__anim = { el: node, draw: draw, stop: stop };

    if (typeof ResizeObserver === "function") new ResizeObserver(function () { draw(); }).observe(view);
    if (typeof IntersectionObserver === "function") {
      new IntersectionObserver(function (entries) {
        if (!entries[0].isIntersecting && playing) stop();
      }).observe(view);
    }
    document.addEventListener("visibilitychange", function () { if (document.hidden && playing) stop(); });
    draw();
    if (node.hasAttribute("autoplay") && !prefersReduced()) start();
  }

  /* ---- <ex-scene> : a click-through diagram, drawn here -------------------
   * Everything in a scene is live SVG built from numbers: the reader clicks a step, the diagram changes, and the line
   * under it says what changed. No pixels of text, so it follows the theme, the language and the text size. */
  var SVGNS = "http://www.w3.org/2000/svg";
  var sceneSeq = 0;

  function svgEl(name, attrs) {
    var n = document.createElementNS(SVGNS, name);
    if (attrs) Object.keys(attrs).forEach(function (k) { if (attrs[k] != null) n.setAttribute(k, String(attrs[k])); });
    return n;
  }

  function anchorOf(a, b) {
    var ax = a.at[0], ay = a.at[1], aw = a.size[0], ah = a.size[1];
    var bx = b.at[0], by = b.at[1], bw = b.size[0], bh = b.size[1];
    if (bx >= ax + aw) return [ax + aw, ay + ah / 2, bx, by + bh / 2];
    if (bx + bw <= ax) return [ax, ay + ah / 2, bx + bw, by + bh / 2];
    return [ax + aw / 2, ay + ah, bx + bw / 2, by];
  }

  function renderScene(node) {
    if (node.__ex) return;
    node.__ex = true;
    var m = mediaFor(node);
    if (!m || m.kind !== "scene" || !m.scene) { mediaMissing(node); renderBadge(node); return; }
    var scene = m.scene, W = scene.size[0], H = scene.size[1];
    var uid = "ex-arrow-" + (++sceneSeq);

    var fig = el("figure", "ex-scene");
    fig.tabIndex = 0;
    fig.setAttribute("role", "group");
    fig.setAttribute("aria-label", node.getAttribute("caption") || "");
    var stage = el("div", "ex-scene-stage");
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, role: "group", "aria-label": node.getAttribute("caption") || "" });
    var defs = svgEl("defs");
    var marker = svgEl("marker", { id: uid, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6,
                                  orient: "auto-start-reverse" });
    marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "ex-arrow-head" }));
    defs.appendChild(marker);
    svg.appendChild(defs);
    var gEdges = svgEl("g", { class: "ex-edges" });
    var gNodes = svgEl("g", { class: "ex-nodes" });
    svg.appendChild(gEdges);
    svg.appendChild(gNodes);
    stage.appendChild(svg);
    fig.appendChild(stage);

    var refs = {};
    scene.nodes.forEach(function (n) {
      var g = svgEl("g", { class: "ex-node k-" + n.kind + " t-" + n.tone });
      var cx = n.at[0] + n.size[0] / 2, cy = n.at[1] + n.size[1] / 2;
      var shape = null, fill = null, label, sub = null;
      if (n.kind === "label") {
        label = svgEl("text", { x: n.at[0], y: cy, class: "ex-label", "text-anchor": "middle", "dominant-baseline": "middle" });
      } else if (n.kind === "dot") {
        shape = svgEl("circle", { cx: cx, cy: cy, r: Math.max(3, n.size[0] / 2), class: "ex-shape" });
        label = svgEl("text", { x: cx, y: cy, class: "ex-label", "text-anchor": "middle", "dominant-baseline": "middle" });
      } else if (n.kind === "bar") {
        shape = svgEl("rect", { x: n.at[0], y: n.at[1], width: n.size[0], height: n.size[1], rx: 3, class: "ex-track" });
        fill = svgEl("rect", { x: n.at[0], y: n.at[1], width: n.size[0] * (n.value == null ? 1 : n.value),
                               height: n.size[1], rx: 3, class: "ex-fill" });
        label = svgEl("text", { x: n.at[0] + 6, y: cy + 1, class: "ex-label ex-on-fill", "dominant-baseline": "middle" });
      } else {
        shape = svgEl("rect", { x: n.at[0], y: n.at[1], width: Math.max(2, n.size[0]), height: Math.max(2, n.size[1]),
                                rx: n.kind === "chip" ? 9 : 6, class: "ex-shape" });
        label = svgEl("text", { x: cx, y: n.sub ? cy - 5 : cy, class: "ex-label", "text-anchor": "middle",
                                "dominant-baseline": "middle" });
      }
      if (fill) g.appendChild(shape), g.appendChild(fill);
      else if (shape) g.appendChild(shape);
      label.textContent = n.text || "";
      g.appendChild(label);
      if (n.sub) {
        sub = svgEl("text", { x: cx, y: cy + 9, class: "ex-sub", "text-anchor": "middle", "dominant-baseline": "middle" });
        sub.textContent = n.sub;
        g.appendChild(sub);
      }
      gNodes.appendChild(g);
      refs[n.id] = { node: n, g: g, shape: shape, fill: fill, label: label, sub: sub };
    });

    var edges = scene.edges.map(function (e) {
      var path = svgEl("path", { class: "ex-edge" + (e.dashed ? " ex-dashed" : ""), "marker-end": "url(#" + uid + ")" });
      gEdges.appendChild(path);
      var label = null;
      if (e.label) {
        label = svgEl("text", { class: "ex-edge-label", "text-anchor": "middle", "dominant-baseline": "middle" });
        label.textContent = e.label;
        gEdges.appendChild(label);
      }
      return { edge: e, path: path, label: label };
    });

    // A node nobody shows or hides is scaffolding: visible from the first step.
    var mentioned = {};
    scene.steps.forEach(function (s) {
      (s.show || []).forEach(function (id) { mentioned[id] = 1; });
      (s.hide || []).forEach(function (id) { mentioned[id] = 1; });
    });
    function stateAt(k) {
      var st = { visible: {}, at: {}, text: {}, sub: {}, value: {}, tone: {}, focus: null };
      scene.nodes.forEach(function (n) {
        st.visible[n.id] = !mentioned[n.id];
        st.at[n.id] = n.at.slice();
        st.text[n.id] = n.text || "";
        st.sub[n.id] = n.sub;
        st.value[n.id] = n.value;
        st.tone[n.id] = n.tone;
      });
      for (var i = 0; i <= k; i++) {
        var s = scene.steps[i];
        (s.show || []).forEach(function (id) { st.visible[id] = true; });
        (s.hide || []).forEach(function (id) { st.visible[id] = false; });
        Object.keys(s.move || {}).forEach(function (id) { st.at[id] = s.move[id].slice(); });
        Object.keys(s.text || {}).forEach(function (id) { st.text[id] = s.text[id]; });
        Object.keys(s.sub || {}).forEach(function (id) { st.sub[id] = s.sub[id]; });
        Object.keys(s.value || {}).forEach(function (id) { st.value[id] = s.value[id]; });
        Object.keys(s.tone || {}).forEach(function (id) { st.tone[id] = s.tone[id]; });
        if (s.focus !== null && s.focus !== undefined) st.focus = s.focus.length ? s.focus : null;
      }
      return st;
    }

    var capTitle = el("b"), capDetail = el("span");
    var cap = el("figcaption", "ex-scene-cap");
    cap.setAttribute("aria-live", "polite");
    cap.appendChild(capTitle);
    cap.appendChild(capDetail);
    var prev = el("button", "ex-prev", "‹");
    prev.type = "button";
    var next = el("button", "ex-next", t("next"));
    next.type = "button";
    var count = el("span", "ex-count");
    var dots = el("div", "ex-scene-dots");
    var dotEls = scene.steps.map(function (s, i) {
      var d = el("button", "ex-dot");
      d.type = "button";
      d.setAttribute("aria-label", (i + 1) + ". " + s.title);
      d.addEventListener("click", function (e) { e.stopPropagation(); show(i); });
      dots.appendChild(d);
      return d;
    });
    var bar = el("div", "ex-scene-bar");
    bar.appendChild(prev);
    bar.appendChild(dots);
    bar.appendChild(next);
    bar.appendChild(count);
    fig.appendChild(cap);
    fig.appendChild(bar);
    node.appendChild(fig);
    renderBadge(node);

    var current = 0;
    function show(k) {
      current = Math.max(0, Math.min(scene.steps.length - 1, k));
      var st = stateAt(current);
      scene.nodes.forEach(function (n) {
        var r = refs[n.id], at = st.at[n.id], visible = st.visible[n.id];
        var dim = visible && st.focus && st.focus.indexOf(n.id) < 0;
        r.g.setAttribute("class", "ex-node k-" + n.kind + " t-" + st.tone[n.id] + (visible ? "" : " is-hidden") +
                                  (dim ? " is-dim" : ""));
        r.g.style.transform = "translate(" + (at[0] - n.at[0]) + "px," + (at[1] - n.at[1]) + "px)";
        if (r.label.textContent !== st.text[n.id]) r.label.textContent = st.text[n.id];
        if (r.sub) {
          var sub = st.sub[n.id] == null ? "" : st.sub[n.id];
          if (r.sub.textContent !== sub) r.sub.textContent = sub;
          r.sub.style.display = sub ? "" : "none";
        }
        if (r.fill) {
          var value = st.value[n.id] == null ? 1 : st.value[n.id];
          r.fill.setAttribute("width", Math.max(0, n.size[0] * value));
        }
      });
      edges.forEach(function (e) {
        var a = refs[e.edge.from], b = refs[e.edge.to];
        var visible = st.visible[e.edge.from] && st.visible[e.edge.to];
        var p = anchorOf({ at: st.at[e.edge.from], size: a.node.size }, { at: st.at[e.edge.to], size: b.node.size });
        e.path.setAttribute("d", "M " + p[0] + " " + p[1] + " L " + p[2] + " " + p[3]);
        e.path.setAttribute("class", "ex-edge" + (e.edge.dashed ? " ex-dashed" : "") + (visible ? "" : " is-hidden"));        if (e.label) {
          e.label.setAttribute("x", (p[0] + p[2]) / 2);
          e.label.setAttribute("y", (p[1] + p[3]) / 2 - 5);
          e.label.setAttribute("class", "ex-edge-label" + (visible ? "" : " is-hidden"));
        }
      });
      var step = scene.steps[current];
      capTitle.textContent = step.title;
      capDetail.textContent = step.detail || (current === 0 ? t("sceneHint") : "");
      count.textContent = (current + 1) + " / " + scene.steps.length;
      prev.disabled = current === 0;
      next.disabled = current === scene.steps.length - 1;
      dotEls.forEach(function (d, i) {
        d.setAttribute("aria-current", i === current ? "true" : "false");
        d.className = "ex-dot" + (i === current ? " is-on" : i < current ? " is-done" : "");
      });
      fig.setAttribute("data-step", String(current + 1));
    }
    prev.addEventListener("click", function (e) { e.stopPropagation(); show(current - 1); });
    next.addEventListener("click", function (e) { e.stopPropagation(); show(current + 1); });
    stage.addEventListener("click", function () { show(current + 1); });
    fig.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight" || e.key === "Enter" || e.key === " ") { e.preventDefault(); show(current + 1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); show(current - 1); }
    });
    show(0);
  }


  /* ---- <ex-chart> : a baked Vega-Lite chart ---------------------------------- */
  function renderChart(node) {
    if (node.__ex) return;
    node.__ex = true;
    var m = mediaFor(node);
    if (!m || m.kind !== "chart") { mediaMissing(node); renderBadge(node); return; }
    var fig = el("figure", "ex-chart");
    var body = el("div", "ex-chart-body");
    fig.appendChild(body);
    var caption = node.getAttribute("caption");
    if (caption) fig.appendChild(el("figcaption", "ex-cap", caption));
    node.appendChild(fig);
    renderBadge(node);
    node.__chart = { body: body, manifest: m, view: null, host: null, tip: null };
    paintChart(node);
  }

  function paintChart(node, forceStatic) {
    var st = node.__chart, m = st.manifest;
    st.body.textContent = "";
    ["light", "dark"].forEach(function (theme) {
      var box = el("div", "ex-chart-svg ex-chart-" + theme);
      var picture = document.createElement("img");
      picture.alt = node.getAttribute("caption") || "";
      picture.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent((m.svg && m.svg[theme]) || "");
      box.appendChild(picture);       // SVG stays in an inert image document, never parsed into the card DOM
      st.body.appendChild(box);
    });
    if (!forceStatic && node.hasAttribute("interactive") && m.vega && window.vega && window.vegaInterpreter) mountChart(node);
  }

  function mountChart(node) {
    var st = node.__chart, m = st.manifest, vega = window.vega;
    var theme = currentTheme();
    var spec = m.vega[theme] || m.vega.light || m.vega.dark;
    if (!spec) return;
    spec = Object.assign({}, spec, {
      background: "transparent",
      autosize: { type: "fit", contains: "padding", resize: true }
    });
    st.body.textContent = "";
    var host = el("div", "ex-chart-canvas");
    st.body.appendChild(host);
    var tip = el("div", "ex-tip");
    tip.hidden = true;
    st.body.appendChild(tip);            // not inside the Vega container: a render replaces its children
    try {
      var runtime = vega.parse(spec, null, { ast: true });
      st.view = new vega.View(runtime, {
        renderer: "svg", container: host, hover: true, expr: window.vegaInterpreter
      });
    } catch (e) {
      st.body.textContent = "";
      paintChart(node, true);
      return;
    }
    st.host = host;
    st.tip = tip;
    var fields = (m.tooltip || []).slice();
    // Vega calls this as (handler, event, item, value); `value` is what the spec's tooltip encoding asked for.
    st.view.tooltip(function (handler, event, item, value) {
      if (!item || item.exit || !item.datum) { tip.hidden = true; return; }
      var datum = value && typeof value === "object" ? value : item.datum;
      var keys = fields.filter(function (k) { return datum[k] !== undefined; });
      if (!keys.length) {
        keys = Object.keys(datum).filter(function (k) {
          return k.charAt(0) !== "_" && datum[k] !== null && datum[k] !== undefined;
        }).slice(0, 6);
      }
      tip.textContent = "";
      keys.forEach(function (k) {
        var row = el("div", "ex-tip-row");
        row.appendChild(el("b", null, k));
        row.appendChild(el("span", null, String(datum[k])));
        tip.appendChild(row);
      });
      if (!tip.childNodes.length) { tip.hidden = true; return; }
      var box = st.body.getBoundingClientRect();
      tip.hidden = false;
      var x = (event.clientX || 0) - box.left, y = (event.clientY || 0) - box.top;
      tip.style.left = Math.max(0, Math.min(box.width - tip.offsetWidth - 4, x + 12)) + "px";
      tip.style.top = Math.max(0, Math.min(box.height - tip.offsetHeight - 4, y + 12)) + "px";
    });
    host.addEventListener("mouseleave", function () { tip.hidden = true; });
    st.view.runAsync().catch(function () {
      try { st.view.finalize(); } catch (e) { /* already gone */ }
      st.view = null;
      st.tip = null;
      paintChart(node, true);                // a spec Vega cannot draw still has its baked SVG
    });
    if (typeof ResizeObserver === "function") {
      new ResizeObserver(function () {
        if (st.view) st.view.resize().runAsync();
      }).observe(host);
    }
  }

  function refreshCharts() {
    document.querySelectorAll("ex-chart").forEach(function (node) {
      var st = node.__chart;
      if (!st) return;
      if (st.view) {
        try { st.view.finalize(); } catch (e) { /* already gone */ }
        st.view = null;
        st.tip = null;
      }
      paintChart(node);
    });
  }


  function init() {
    document.querySelectorAll("ex-table").forEach(renderTable);
    document.querySelectorAll("ex-stage").forEach(renderStage);
    document.querySelectorAll("ex-compare").forEach(renderCompare);
    document.querySelectorAll("ex-anim").forEach(renderAnim);
    document.querySelectorAll("ex-scene").forEach(renderScene);
    document.querySelectorAll("ex-chart").forEach(renderChart);
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
