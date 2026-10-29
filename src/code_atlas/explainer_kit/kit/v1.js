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
          cinema: "放映", cinemaHint: "连播并朗读每一帧", exitCinema: "退出放映", replay: "重播",
          resume: "继续", reading: "朗读中", paused: "已暂停", ended: "放映结束", voice: "音色",
          ttsFallback: "语音不可用，按文字节奏播放", ttsBlocked: "浏览器没有可用的语音，按文字节奏播放",
          missing: "这张图没有随卡片打包（重新运行 atlas-explainer pack）" },
    en: { real: "Real data", illustrative: "Illustrative data", undeclared: "Source not declared", from: "from", at: "read",
          play: "Play", pause: "Pause", frame: "Frame", next: "Next step", prev: "Previous step", sceneHint: "Click to continue",
          cinema: "Play", cinemaHint: "Play every frame and read it aloud", exitCinema: "Exit", replay: "Replay",
          resume: "Resume", reading: "Reading", paused: "Paused", ended: "The end", voice: "Voice",
          ttsFallback: "Speech is unavailable; using reading pace", ttsBlocked: "No voice is available; using reading pace",
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
    if (!d) return;
    if (d.type === "atlas:cinema" && d.on === false && cinema) { exitCinema(); return; }
    if (d.type !== "atlas:theme") return;
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
      var g = svgEl("g", { class: "ex-node k-" + n.kind + " t-" + n.tone + (n.dashed ? " is-dashed" : "") });
      var shape = null, fill = null, sub = null;
      var label = svgEl("text", { class: "ex-label", "dominant-baseline": "middle" });
      if (n.kind === "label") {
        label.setAttribute("text-anchor", "middle");
        g.appendChild(label);
      } else if (n.kind === "dot") {
        shape = svgEl("circle", { class: "ex-shape" });
        label.setAttribute("text-anchor", "middle");
        g.appendChild(shape);
        g.appendChild(label);
      } else if (n.kind === "bar") {
        shape = svgEl("rect", { rx: 3, class: "ex-track" });
        fill = svgEl("rect", { rx: 3, class: "ex-fill" });
        label.setAttribute("class", "ex-label ex-on-fill");
        g.appendChild(shape);
        g.appendChild(fill);
        g.appendChild(label);
      } else {
        shape = svgEl("rect", { rx: n.kind === "chip" ? 9 : 6, class: "ex-shape" });
        label.setAttribute("text-anchor", "middle");
        g.appendChild(shape);
        g.appendChild(label);
      }
      label.textContent = n.text || "";
      if (n.sub) {
        sub = svgEl("text", { class: "ex-sub", "text-anchor": "middle", "dominant-baseline": "middle" });
        sub.textContent = n.sub;
        g.appendChild(sub);
      }
      gNodes.appendChild(g);
      refs[n.id] = { node: n, g: g, shape: shape, fill: fill, label: label, sub: sub };
      layout(refs[n.id], n.size[0], n.size[1]);
    });

    /* Place a node for a given size. The group's transform carries `at`, so everything here is in its own frame. */
    function layout(r, w, h) {
      var at = r.node.at, cy = at[1] + h / 2;
      if (r.shape && r.node.kind === "dot") {
        r.shape.setAttribute("cx", at[0] + w / 2);
        r.shape.setAttribute("cy", cy);
        r.shape.setAttribute("r", Math.max(3, w / 2));
      } else if (r.shape) {
        r.shape.setAttribute("x", at[0]);
        r.shape.setAttribute("y", at[1]);
        r.shape.setAttribute("width", Math.max(2, w));
        r.shape.setAttribute("height", Math.max(2, h));
      }
      if (r.fill) {
        r.fill.setAttribute("x", at[0]);
        r.fill.setAttribute("y", at[1]);
        r.fill.setAttribute("height", Math.max(2, h));
      }
      if (r.node.kind === "label") {
        r.label.setAttribute("x", at[0]);
        r.label.setAttribute("y", cy);
      } else if (r.node.kind === "bar") {
        r.label.setAttribute("x", at[0] + 6);
        r.label.setAttribute("y", cy + 1);
      } else {
        r.label.setAttribute("x", at[0] + w / 2);
        r.label.setAttribute("y", r.sub ? cy - 5 : cy);
      }
      if (r.sub) {
        r.sub.setAttribute("x", at[0] + w / 2);
        r.sub.setAttribute("y", cy + 9);
      }
      r.w = w;
      r.h = h;
    }

    /* An edge end is either a node id or a point, so a bracket or a leader line can sit anywhere. */
    function endBox(ref, st) {
      if (ref && typeof ref === "object") return { at: [Number(ref[0]), Number(ref[1])], size: [0, 0] };
      return { at: st.at[ref], size: [st.size[ref][0], st.size[ref][1]] };
    }

    function borderPoint(box, toward) {
      var x = box.at[0], y = box.at[1], w = box.size[0], h = box.size[1], cy = y + h / 2;
      if (!w && !h) return [x, y];
      if (toward[0] >= x + w) return [x + w, cy];
      if (toward[0] <= x) return [x, cy];
      return [x + w / 2, toward[1] >= cy ? y + h : y];
    }

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
      var st = { visible: {}, at: {}, size: {}, text: {}, sub: {}, value: {}, tone: {}, focus: null };
      scene.nodes.forEach(function (n) {
        st.visible[n.id] = !mentioned[n.id];
        st.at[n.id] = n.at.slice();
        st.size[n.id] = n.size.slice();
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
        Object.keys(s.size || {}).forEach(function (id) { st.size[id] = s.size[id].slice(); });
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
        r.g.setAttribute("class", "ex-node k-" + n.kind + " t-" + st.tone[n.id] + (n.dashed ? " is-dashed" : "") +
                                  (visible ? "" : " is-hidden") + (dim ? " is-dim" : ""));
        r.g.style.transform = "translate(" + (at[0] - n.at[0]) + "px," + (at[1] - n.at[1]) + "px)";
        layout(r, st.size[n.id][0], st.size[n.id][1]);
        if (r.label.textContent !== st.text[n.id]) r.label.textContent = st.text[n.id];
        if (r.sub) {
          var sub = st.sub[n.id] == null ? "" : st.sub[n.id];
          if (r.sub.textContent !== sub) r.sub.textContent = sub;
          r.sub.style.display = sub ? "" : "none";
        }
        if (r.fill) {
          var value = st.value[n.id] == null ? 1 : st.value[n.id];
          r.fill.setAttribute("width", Math.max(0, st.size[n.id][0] * value));
        }
      });
      edges.forEach(function (e) {
        var a = endBox(e.edge.from, st), b = endBox(e.edge.to, st);
        var ca = [a.at[0] + a.size[0] / 2, a.at[1] + a.size[1] / 2];
        var cb = [b.at[0] + b.size[0] / 2, b.at[1] + b.size[1] / 2];
        var p1 = borderPoint(a, cb), p2 = borderPoint(b, ca);
        var visible = (typeof e.edge.from === "object" || st.visible[e.edge.from]) &&
                      (typeof e.edge.to === "object" || st.visible[e.edge.to]);
        e.path.setAttribute("d", "M " + p1[0] + " " + p1[1] + " L " + p2[0] + " " + p2[1]);
        e.path.setAttribute("class", "ex-edge" + (e.edge.dashed ? " ex-dashed" : "") + (visible ? "" : " is-hidden"));
        if (e.label) {
          e.label.setAttribute("x", (p1[0] + p2[0]) / 2);
          e.label.setAttribute("y", (p1[1] + p2[1]) / 2 - 5);
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
    node.__scene = {
      el: node,
      count: scene.steps.length,
      go: show,
      text: function (i) {
        var st = scene.steps[i] || {};
        return { title: st.title || "", detail: st.detail || "" };
      }
    };
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


  /* ---- screening: play the card frame by frame, reading each caption aloud ----
   * A stepper's radio frames and an <ex-scene>'s steps are both playable. The card speaks the caption with the
   * browser's speech synthesis, waits for the sentence to end, then moves to the next frame. When the browser has
   * no voice (or refuses to speak), a reading-pace timer keeps the screening running instead of stalling. */
  var cinema = null;
  var speech = typeof window.speechSynthesis !== "undefined" ? window.speechSynthesis : null;

  function clampIndex(i, n) { return Math.max(0, Math.min(n - 1, i)); }
  function clean(s) { return (s || "").replace(/\s+/g, " ").trim(); }

  function isNatural(v) { return /Online \(Natural\)|Natural/i.test(v.name || ""); }
  function voiceKey(v) { return (v.name || "") + "|" + (v.lang || ""); }
  function voiceLabel(v) {
    var n = (v.name || "").replace(/^Microsoft\s+/, "").replace(/\s+Online \(Natural\).*$/, "").replace(/\s+-\s+.*$/, "");
    return n + (isNatural(v) ? "（自然）" : "（本地）");
  }
  function langVoices(lang) {
    var all = speech ? speech.getVoices() || [] : [];
    var zh = (lang || "").toLowerCase().indexOf("zh") === 0;
    var prefix = zh ? "zh" : "en";
    var same = all.filter(function (v) { return (v.lang || "").toLowerCase().indexOf(prefix) === 0; });
    return same.length ? same : all;
  }
  function voiceFor(lang) {
    if (!speech) return null;
    var same = langVoices(lang);
    if (!same.length) return null;
    var zh = (lang || "").toLowerCase().indexOf("zh") === 0;
    var natural = same.filter(isNatural);
    var exact = natural.filter(function (v) { return (v.lang || "").toLowerCase() === (zh ? "zh-cn" : "en-us"); });
    var pool = exact.length ? exact : natural.filter(function (v) { return /^zh-cn|^en-us/i.test(v.lang || ""); });
    if (!pool.length) pool = natural;
    var prefer = zh
      ? ["晓晓", "云希", "云健", "晓伊", "云扬", "云夏", "Xiaoxiao", "Yunxi", "Yunjian", "Xiaoyi", "Yunyang", "Yunxia"]
      : ["Aria", "Jenny", "Guy", "Emma", "Brian", "Ava", "Andrew", "Natural", "Google"];
    for (var p = 0; p < prefer.length; p++) {
      for (var i = 0; i < pool.length; i++) if ((pool[i].name || "").indexOf(prefer[p]) >= 0) return pool[i];
    }
    if (pool.length) return pool[0];
    var local = same.filter(function (v) { return v.localService && (!zh || /^zh-cn/i.test(v.lang || "")); });
    return local[0] || same[0];
  }
  function voiceOptions(lang) {
    var all = langVoices(lang), seen = {}, out = [];
    all.forEach(function (v) {
      var k = voiceKey(v);
      if (!seen[k]) { seen[k] = 1; out.push(v); }
    });
    out.sort(function (a, b) {
      var an = isNatural(a) ? 0 : 1, bn = isNatural(b) ? 0 : 1;
      if (an !== bn) return an - bn;
      return (a.lang || "").localeCompare(b.lang || "");
    });
    return out;
  }
  function whenVoices(cb) {
    if (!speech) return cb();
    if ((speech.getVoices() || []).length) return cb();
    var done = false;
    function fire() {
      if (done) return;
      done = true;
      try { speech.removeEventListener("voiceschanged", fire); } catch (e) { /* older engine */ }
      cb();
    }
    try { speech.addEventListener("voiceschanged", fire); } catch (e) { /* older engine */ }
    window.setTimeout(fire, 1500);
  }
  var cinemaVoice = voiceFor(root.getAttribute("lang"));
  if (speech && typeof speech.addEventListener === "function") {
    speech.addEventListener("voiceschanged", function () { if (!cinemaVoice) cinemaVoice = voiceFor(root.getAttribute("lang")); });
  }

  function stepperFrames() {
    var figs = document.querySelectorAll("figure.stepper");
    for (var f = 0; f < figs.length; f++) {
      var radios = figs[f].querySelectorAll('input.st[type="radio"]');
      if (radios.length < 2) continue;
      var caps = figs[f].querySelectorAll(".caps .cap");
      if (!caps.length) caps = figs[f].querySelectorAll(".cap");
      var fig = figs[f];
      return {
        el: fig,
        count: radios.length,
        go: function (i) {
          var r = radios[clampIndex(i, radios.length)];
          if (r && !r.checked) { r.checked = true; r.dispatchEvent(new Event("change", { bubbles: true })); }
        },
        text: function (i) {
          var cap = caps[i];
          if (!cap) return { title: "", detail: "" };
          var b = cap.querySelector("b"), s = cap.querySelector("span");
          return { title: clean(b ? b.textContent : cap.textContent), detail: clean(s ? s.textContent : "") };
        }
      };
    }
    return null;
  }

  function sceneFrames() {
    var nodes = document.querySelectorAll("ex-scene");
    for (var i = 0; i < nodes.length; i++) if (nodes[i].__scene && nodes[i].__scene.count > 1) return nodes[i].__scene;
    return null;
  }

  /* A card may ship media/narration.json: one pre-recorded clip per frame. When it does, the screening plays
   * those clips instead of the device's voices; the browser's speechSynthesis remains the fallback. */
  var narration = null;
  function narrationFor() {
    if (narration !== null) return narration;
    narration = false;
    var box = document.getElementById("atlas-media-registry");
    if (!box) return narration;
    try {
      var reg = JSON.parse(box.textContent || "{}") || {};
      var n = reg.narration;
      if (n && n.kind === "narration" && n.frames && n.frames.length) narration = n;
    } catch (e) { /* no registry, no narration */ }
    return narration;
  }
  function voiceName(v) {
    return v ? v.replace(/^zh-CN-/, "").replace(/Neural$/, "") : "";
  }

  function cinemaUi(frames, title) {
    var rootEl = el("div", "ex-cinema");
    rootEl.setAttribute("role", "dialog");
    rootEl.setAttribute("aria-modal", "true");
    rootEl.setAttribute("aria-label", title);
    var shell = el("div", "ex-cinema-shell");
    var head = el("header", "ex-cinema-head");
    var h = el("h2", "ex-cinema-title", title);
    var count = el("div", "ex-cinema-count");
    count.setAttribute("aria-live", "off");
    var exit = el("button", "ex-cinema-exit", "×");
    exit.type = "button";
    exit.setAttribute("aria-label", t("exitCinema"));
    head.appendChild(el("div", "ex-cinema-kicker", t("cinema")));
    head.appendChild(h);
    head.appendChild(count);
    head.appendChild(exit);

    var main = el("div", "ex-cinema-main");
    var stage = el("div", "ex-cinema-stage");
    main.appendChild(stage);

    var cap = el("div", "ex-cinema-caption");
    cap.setAttribute("aria-live", "polite");
    var capB = el("b"), capS = el("span");
    cap.appendChild(capB);
    cap.appendChild(capS);

    var bar = el("footer", "ex-cinema-bar");
    var prev = el("button", "ex-cinema-prev", "←");
    prev.type = "button";
    prev.setAttribute("aria-label", t("prev"));
    var toggle = el("button", "ex-cinema-toggle");
    toggle.type = "button";
    var next = el("button", "ex-cinema-next", "→");
    next.type = "button";
    next.setAttribute("aria-label", t("next"));
    var voice = el("select", "ex-cinema-voice");
    voice.setAttribute("aria-label", t("voice"));
    voice.title = t("voice");
    var track = el("div", "ex-cinema-track");
    var fill = el("i");
    track.appendChild(fill);
    var status = el("div", "ex-cinema-status");
    status.setAttribute("aria-live", "polite");
    bar.appendChild(prev);
    bar.appendChild(toggle);
    bar.appendChild(next);
    bar.appendChild(voice);
    bar.appendChild(track);
    bar.appendChild(status);

    shell.appendChild(head);
    shell.appendChild(main);
    shell.appendChild(cap);
    shell.appendChild(bar);
    rootEl.appendChild(shell);
    return { root: rootEl, stage: stage, count: count, exit: exit, capB: capB, capS: capS,
             prev: prev, toggle: toggle, next: next, voice: voice, fill: fill, status: status };
  }

  function fillVoiceSelect(select, chosen) {
    if (!select) return;
    var opts = voiceOptions(root.getAttribute("lang"));
    var natural = opts.filter(isNatural);
    select.textContent = "";
    if (narrationFor() && !natural.length) { select.style.display = "none"; return; }
    if (opts.length < 2) { select.style.display = "none"; return; }
    select.style.display = "";
    opts.forEach(function (v) {
      var o = document.createElement("option");
      o.value = voiceKey(v);
      o.textContent = voiceLabel(v);
      if (chosen && voiceKey(chosen) === o.value) o.selected = true;
      select.appendChild(o);
    });
  }

  function enterCinema(frames) {
    if (cinema) return;
    var fig = frames.el;
    var placeholder = document.createComment("atlas-cinema");
    fig.parentNode.insertBefore(placeholder, fig);
    var titleEl = document.querySelector("h1");
    var title = clean(titleEl ? titleEl.textContent : document.title) || document.title || t("cinema");
    var ui = cinemaUi(frames, title);
    fig.classList.add("ex-cinema-figure");
    ui.stage.appendChild(fig);
    document.body.appendChild(ui.root);
    document.body.classList.add("ex-cinema-on");
    cinema = { frames: frames, fig: fig, placeholder: placeholder, ui: ui,
               index: 0, playing: false, finished: false, token: 0,
               timer: 0, watch: 0, broken: false, voice: cinemaVoice, live: false, audio: null };
    ui.exit.addEventListener("click", exitCinema);
    ui.toggle.addEventListener("click", function () { if (cinema) { if (cinema.playing) pauseCinema(); else playCinema(); } });
    ui.prev.addEventListener("click", function () { jumpCinema(-1); });
    ui.next.addEventListener("click", function () { jumpCinema(1); });
    ui.voice.addEventListener("change", function () {
      if (!cinema) return;
      var opts = voiceOptions(root.getAttribute("lang"));
      var picked = null;
      for (var i = 0; i < opts.length; i++) {
        if (voiceKey(opts[i]) === ui.voice.value) picked = opts[i];
      }
      if (!picked) return;
      cinema.voice = picked;
      cinema.live = true;
      if (cinema.playing) speakFrame(cinema.index);
    });
    ui.stage.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest("button")) return;
      if (cinema) { if (cinema.playing) pauseCinema(); else playCinema(); }
    });
    showFrame(0);
    updateToggle();
    ui.status.textContent = t("cinemaHint");
    fillVoiceSelect(ui.voice, cinema.voice);
    post({ type: "atlas:cinema", action: "enter" });
    whenVoices(function () {
      if (!cinema) return;
      if (!cinema.voice) cinema.voice = voiceFor(root.getAttribute("lang"));
      fillVoiceSelect(ui.voice, cinema.voice);
      window.setTimeout(function () { if (cinema && !cinema.playing) playCinema(); }, 120);
    });
  }

  function exitCinema() {
    if (!cinema) return;
    var c = cinema;
    stopSpeech();
    c.fig.classList.remove("ex-cinema-figure");
    if (c.placeholder.parentNode) c.placeholder.parentNode.replaceChild(c.fig, c.placeholder);
    if (c.ui.root.parentNode) c.ui.root.parentNode.removeChild(c.ui.root);
    document.body.classList.remove("ex-cinema-on");
    cinema = null;
    post({ type: "atlas:cinema", action: "exit" });
  }

  function updateToggle() {
    if (!cinema) return;
    var label = cinema.playing ? "❚❚ " + t("pause")
      : cinema.finished ? "↻ " + t("replay")
      : cinema.index > 0 ? "▶ " + t("resume") : "▶ " + t("play");
    cinema.ui.toggle.textContent = label;
    cinema.ui.toggle.setAttribute("aria-pressed", cinema.playing ? "true" : "false");
  }

  function showFrame(i) {
    if (!cinema) return;
    var f = cinema.frames, n = f.count;
    cinema.index = clampIndex(i, n);
    f.go(cinema.index);
    var text = f.text(cinema.index);
    cinema.ui.capB.textContent = text.title;
    cinema.ui.capS.textContent = text.detail;
    cinema.ui.capB.style.display = text.title ? "" : "none";
    cinema.ui.capS.style.display = text.detail ? "" : "none";
    cinema.ui.count.textContent = (cinema.index + 1) + " / " + n;
    cinema.ui.fill.style.width = ((cinema.index + 1) / n * 100).toFixed(2) + "%";
    cinema.ui.prev.disabled = cinema.index === 0;
    cinema.ui.next.disabled = cinema.index === n - 1;
    if (cinema.playing) speakFrame(cinema.index);
  }

  function playCinema() {
    if (!cinema) return;
    if (cinema.finished) { cinema.finished = false; cinema.index = 0; }
    cinema.playing = true;
    updateToggle();
    showFrame(cinema.index);
  }

  function pauseCinema() {
    if (!cinema) return;
    cinema.playing = false;
    stopSpeech();
    updateToggle();
    cinema.ui.status.textContent = t("paused");
  }

  function finishCinema() {
    if (!cinema) return;
    cinema.playing = false;
    cinema.finished = true;
    stopSpeech();
    updateToggle();
    cinema.ui.status.textContent = t("ended");
  }

  function jumpCinema(d) {
    if (!cinema) return;
    var i = cinema.index + d;
    if (i < 0 || i >= cinema.frames.count) return;
    cinema.finished = false;
    showFrame(i);
    if (!cinema.playing) updateToggle();
  }

  function stopSpeech() {
    if (!cinema) return;
    cinema.token++;
    window.clearTimeout(cinema.timer);
    window.clearTimeout(cinema.watch);
    cinema.timer = 0;
    cinema.watch = 0;
    if (cinema.audio) {
      var a = cinema.audio;
      cinema.audio = null;
      try {
        a.onended = null;
        a.onerror = null;
        a.pause();
        a.removeAttribute("src");
        a.load();
      } catch (e) { /* already gone */ }
    }
    if (speech) { try { speech.cancel(); } catch (e) { /* no synth after all */ } }
  }

  function scheduleNext(token) {
    if (!cinema || cinema.token !== token || !cinema.playing) return;
    window.clearTimeout(cinema.timer);
    cinema.timer = window.setTimeout(function () {
      if (!cinema || cinema.token !== token || !cinema.playing) return;
      if (cinema.index + 1 >= cinema.frames.count) finishCinema();
      else showFrame(cinema.index + 1);
    }, 420);
  }

  function fallbackSpeech(token, sentence) {
    if (!cinema || cinema.token !== token) return;
    window.clearTimeout(cinema.watch);
    var chars = sentence.replace(/\s+/g, "").length;
    var ms = Math.max(2400, Math.min(20000, chars * 210));
    cinema.ui.status.textContent = speech ? t("ttsFallback") : t("ttsBlocked");
    window.clearTimeout(cinema.timer);
    cinema.timer = window.setTimeout(function () {
      if (cinema && cinema.playing && cinema.token === token) scheduleNext(token);
    }, ms);
  }

  function speakFrame(i) {
    if (!cinema || !cinema.playing) return;
    stopSpeech();
    var token = ++cinema.token;
    var text = cinema.frames.text(i);
    var sentence = clean([text.title, text.detail].filter(Boolean).join("。"));
    cinema.ui.status.textContent = "";
    if (!sentence) {
      cinema.timer = window.setTimeout(function () { if (cinema && cinema.playing && cinema.token === token) scheduleNext(token); }, 1400);
      return;
    }
    var live = cinema.voice || voiceFor(root.getAttribute("lang"));
    if (live && (cinema.live || isNatural(live)) && !cinema.broken) { speakWithVoice(token, sentence); return; }
    var n = narrationFor();
    var track = n && n.frames ? n.frames[i] : null;
    if (track && track.audio) { playNarration(token, track, sentence); return; }
    if (!speech || cinema.broken) { fallbackSpeech(token, sentence); return; }
    speakWithVoice(token, sentence);
  }

  function playNarration(token, track, sentence) {
    if (!cinema || cinema.token !== token) return;
    var a = new Audio(track.audio);
    cinema.audio = a;
    a.preload = "auto";
    var failed = false;
    function fail() {
      if (failed || !cinema || cinema.token !== token) return;
      failed = true;
      if (cinema.audio === a) cinema.audio = null;
      try { a.onended = null; a.onerror = null; a.pause(); a.removeAttribute("src"); a.load(); } catch (e) { /* gone */ }
      if (speech && !cinema.broken) speakWithVoice(token, sentence);
      else fallbackSpeech(token, sentence);
    }
    var n = narrationFor();
    a.onplay = function () {
      if (!cinema || cinema.token !== token) return;
      var who = n && n.voice ? voiceName(n.voice) : "";
      cinema.ui.status.textContent = t("reading") + (who ? " · " + who : "");
    };
    a.onended = function () {
      if (!cinema || cinema.token !== token) return;
      window.clearTimeout(cinema.watch);
      cinema.ui.status.textContent = "";
      scheduleNext(token);
    };
    a.onerror = function () { fail(); };
    var p = a.play();
    if (p && typeof p.catch === "function") p.catch(function () { fail(); });
    window.clearTimeout(cinema.watch);
    cinema.watch = window.setTimeout(function () {
      if (cinema && cinema.playing && cinema.token === token) fail();
    }, Math.max(10000, sentence.length * 350 + 8000));
  }

  function afterSpeechFailure(token, sentence) {
    if (!cinema || cinema.token !== token) return;
    var n = narrationFor();
    var track = n && n.frames ? n.frames[cinema.index] : null;
    if (track && track.audio) { playNarration(token, track, sentence); return; }
    fallbackSpeech(token, sentence);
  }

  function speakWithVoice(token, sentence) {
    if (!cinema || cinema.token !== token) return;
    var u = new window.SpeechSynthesisUtterance(sentence);
    u.lang = root.getAttribute("lang") || "zh-CN";
    var v = cinema.voice || voiceFor(u.lang);
    if (v) { u.voice = v; cinema.voice = v; }
    u.rate = 1;
    u.pitch = 1;
    var started = false;
    u.onstart = function () {
      if (!cinema || cinema.token !== token) return;
      started = true;
      cinema.ui.status.textContent = t("reading") + (u.voice ? " · " + u.voice.name : "");
      window.clearTimeout(cinema.watch);
      cinema.watch = window.setTimeout(function () {
        if (cinema && cinema.playing && cinema.token === token) { cinema.broken = true; afterSpeechFailure(token, sentence); }
      }, 20000 + sentence.length * 200);
    };
    u.onend = function () {
      if (!cinema || cinema.token !== token) return;
      window.clearTimeout(cinema.watch);
      cinema.ui.status.textContent = "";
      scheduleNext(token);
    };
    u.onerror = function (ev) {
      if (!cinema || cinema.token !== token) return;
      window.clearTimeout(cinema.watch);
      var why = ev && ev.error ? ev.error : "error";
      if (why !== "interrupted" && why !== "canceled") {
        cinema.broken = true;
        afterSpeechFailure(token, sentence);
      }
    };
    cinema.watch = window.setTimeout(function () {
      if (!started && cinema && cinema.playing && cinema.token === token) {
        cinema.broken = true;
        afterSpeechFailure(token, sentence);
      }
    }, 4000);
    try { speech.speak(u); } catch (e) { cinema.broken = true; afterSpeechFailure(token, sentence); }
  }

  function installCinema() {
    var frames = stepperFrames() || sceneFrames();
    if (!frames || frames.count < 2) return;
    var bar = el("div", "ex-cinema-launchbar");
    var btn = el("button", "ex-cinema-launch");
    btn.type = "button";
    btn.title = t("cinemaHint");
    btn.setAttribute("aria-label", t("cinema") + " — " + t("cinemaHint"));
    btn.appendChild(el("span", "ex-cinema-launch-icon", "▶"));
    btn.appendChild(el("span", null, t("cinema")));
    btn.addEventListener("click", function () {
      var f = stepperFrames() || sceneFrames();
      if (f) enterCinema(f);
    });
    bar.appendChild(btn);
    document.body.insertBefore(bar, document.body.firstChild);
    post({ type: "atlas:playback", available: true, frames: frames.count });
  }

  document.addEventListener("keydown", function (e) {
    if (!cinema) return;
    var tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (e.key === "Escape") { e.preventDefault(); exitCinema(); }
    else if (e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      if (cinema.playing) pauseCinema(); else playCinema();
    } else if (e.key === "ArrowRight") { e.preventDefault(); jumpCinema(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); jumpCinema(-1); }
  });


  function init() {
    document.querySelectorAll("ex-table").forEach(renderTable);
    document.querySelectorAll("ex-stage").forEach(renderStage);
    document.querySelectorAll("ex-compare").forEach(renderCompare);
    document.querySelectorAll("ex-anim").forEach(renderAnim);
    document.querySelectorAll("ex-scene").forEach(renderScene);
    document.querySelectorAll("ex-chart").forEach(renderChart);
    installCinema();
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
