/* AegisHiker hike website: overview, interactive topo map, elevation profile, photo gallery and
   "Play the Hike" (a browser port of the desktop app's 3D replay). Static, no dependencies.
   Data comes from hike.js (window.HIKE_DATA, or window.HIKE_LOCKED when password protected). */
(function () {
  "use strict";

  // ── helpers ───────────────────────────────────────────────────────────────
  function el(tag, attrs) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (v == null || v === false) continue;
        if (k === "class") e.className = v;
        else if (k === "text") e.textContent = v;
        else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v);
        else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
        else e.setAttribute(k, v === true ? "" : v);
      }
    }
    for (let i = 2; i < arguments.length; i++) append(e, arguments[i]);
    return e;
  }
  function append(e, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(k => append(e, k)); return; }
    e.append(kid.nodeType ? kid : String(kid));
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const smoothstep = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };

  // ── device: phone or desktop ──────────────────────────────────────────────
  // A phone is a touch screen that is small in both directions (so also in landscape), or any narrow
  // window. <html> gets .phone / .desktop and .touch, for the CSS and the views.
  const device = { phone: false, touch: false };
  function detectDevice() {
    const mq = q => !!(window.matchMedia && matchMedia(q).matches);
    const coarse = mq("(pointer: coarse)");
    const was = device.phone;
    device.touch = coarse || (navigator.maxTouchPoints > 0 && !mq("(pointer: fine)"));
    device.phone = (coarse && Math.min(screen.width, screen.height) < 600) || window.innerWidth < 600;
    const c = document.documentElement.classList;
    c.toggle("phone", device.phone); c.toggle("desktop", !device.phone); c.toggle("touch", device.touch);
    return was !== device.phone;
  }
  detectDevice();
  const pixelRatio = cap => Math.min(window.devicePixelRatio || 1, cap || 3);

  // ── units & formatting ────────────────────────────────────────────────────
  const FT = 3.28084, MI = 1609.344;
  let units = "us";
  const fmt = {
    dist(m, dec) {
      if (units === "us") {
        const mi = m / MI;
        if (mi < 0.1) return Math.round(m * FT).toLocaleString() + " ft";
        return mi.toFixed(dec != null ? dec : (mi < 10 ? 2 : 1)) + " mi";
      }
      if (m < 1000) return Math.round(m) + " m";
      return (m / 1000).toFixed(dec != null ? dec : (m < 10000 ? 2 : 1)) + " km";
    },
    ele(m) { return units === "us" ? Math.round(m * FT).toLocaleString() + " ft" : Math.round(m).toLocaleString() + " m"; },
    temp(c) { return c == null ? "" : units === "us" ? Math.round(c * 9 / 5 + 32) + "°F" : Math.round(c) + "°C"; },
    precip(mm) { return mm == null ? "" : units === "us" ? (mm / 25.4).toFixed(2) + " in" : mm.toFixed(1) + " mm"; },
    wind(k) { return k == null ? "" : units === "us" ? Math.round(k / 1.609) + " mph" : Math.round(k) + " km/h"; },
    dur(min) {
      if (!min) return "";
      const h = Math.floor(min / 60), m = Math.round(min - h * 60);
      return h ? h + " h " + String(m).padStart(2, "0") + " min" : m + " min";
    },
    time(iso) {
      if (!iso) return "";
      const d = new Date(iso);
      return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    },
    datetime(iso) {
      if (!iso) return "";
      const d = new Date(iso);
      return isNaN(d) ? "" : d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" }) +
        " · " + d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    },
    grade(p) { return p.toFixed(0) + "%"; },
  };

  // ── assets (plain files, or encrypted script-wrapped blobs) ───────────────
  let KEY = null;
  const pending = new Map();
  const assetUrls = new Map();
  const images = new Map();
  window.__hikeAsset = function (name, blob) {
    const cb = pending.get(name);
    if (cb) { pending.delete(name); cb(blob); }
  };
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = el("script", { src: src });
      s.onload = () => { s.remove(); resolve(); };
      s.onerror = () => { s.remove(); reject(new Error("Could not load " + src)); };
      document.head.append(s);
    });
  }
  const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  async function decrypt(blob) {
    return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(blob.iv) }, KEY, b64(blob.data)));
  }
  function assetUrl(path) {
    if (!KEY) return Promise.resolve(path);
    if (assetUrls.has(path)) return assetUrls.get(path);
    const p = new Promise((resolve, reject) => {
      pending.set(path, blob => {
        decrypt(blob).then(bytes => resolve(URL.createObjectURL(new Blob([bytes], { type: blob.type || "image/jpeg" }))), reject);
      });
      loadScript(path + ".js").catch(reject);
    });
    assetUrls.set(path, p);
    return p;
  }
  function loadImage(path) {
    if (!path) return Promise.resolve(null);
    if (images.has(path)) return images.get(path);
    const p = assetUrl(path).then(url => new Promise(resolve => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = url;
    })).catch(() => null);
    images.set(path, p);
    return p;
  }
  function setImg(imgEl, path) {
    loadImage(path).then(img => { if (img) imgEl.src = img.src; });
    return imgEl;
  }

  // ── data model ────────────────────────────────────────────────────────────
  let H = null; // hike data
  const G = {}; // derived geometry
  function prepare(data) {
    H = data;
    const S = H.summary;
    const route = H.route;
    let lat0 = 0, lon0 = 0;
    route.forEach(c => { lon0 += c[0]; lat0 += c[1]; });
    lat0 /= route.length; lon0 /= route.length;
    G.lat0 = lat0; G.lon0 = lon0;
    G.kx = 111320 * Math.cos(lat0 * Math.PI / 180);
    G.ky = 110540;
    G.toX = lon => (lon - lon0) * G.kx;
    G.toY = lat => (lat - lat0) * G.ky;
    G.rx = Float64Array.from(route, c => G.toX(c[0]));
    G.ry = Float64Array.from(route, c => G.toY(c[1]));
    G.re = Float64Array.from(route, c => c[2]);
    G.along = Float64Array.from(H.route_along);
    G.total = G.along[G.along.length - 1] || 1;
    G.legs = S.trails || [];
    G.nights = (S.days || []).slice(0, -1).map((d, i) => ({ n: i + 1, along: d.to_m, name: d.overnight || "", day: d.day }));
    const bm = H.basemap || {};
    const conv = c => { const a = new Float64Array(c.length * 2); c.forEach((p, i) => { a[2 * i] = G.toX(p[0]); a[2 * i + 1] = G.toY(p[1]); }); return a; };
    G.contours = (bm.contours || []).map(f => ({ e: f.e, i: f.i, xy: conv(f.c) }));
    G.lines = (bm.lines || []).map(f => ({ k: f.k, n: f.n, xy: conv(f.c) }));
    G.areas = (bm.areas || []).map(f => ({ k: f.k, n: f.n, xy: conv(f.c) }));
    G.points = (bm.points || []).map(f => ({ k: f.k, n: f.n, e: f.e, x: G.toX(f.c[0]), y: G.toY(f.c[1]) }));
    if (bm.bbox) G.bbox = [G.toX(bm.bbox[0]), G.toY(bm.bbox[1]), G.toX(bm.bbox[2]), G.toY(bm.bbox[3])];
    if (bm.relief) {
      const b = bm.relief.bbox;
      G.relief = { src: bm.relief.src, x0: G.toX(b[0]), y0: G.toY(b[1]), x1: G.toX(b[2]), y1: G.toY(b[3]), img: null };
      loadImage(bm.relief.src).then(img => { G.relief.img = img; redrawMaps(); });
    }
    H.photos.forEach((p, i) => { p.i = i; p.x = G.toX(p.lon); p.y = G.toY(p.lat); p.thumbImg = null; });
    // Route bbox (with photos) for fitting
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const grow = (x, y) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); };
    for (let i = 0; i < G.rx.length; i++) grow(G.rx[i], G.ry[i]);
    H.photos.forEach(p => grow(p.x, p.y));
    G.fitBox = [x0, y0, x1, y1];
  }
  function pointAt(along) {
    const a = G.along;
    along = clamp(along, 0, G.total);
    let lo = 0, hi = a.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (a[mid] < along) lo = mid; else hi = mid; }
    const span = a[hi] - a[lo], t = span > 0 ? (along - a[lo]) / span : 0;
    return {
      x: G.rx[lo] + (G.rx[hi] - G.rx[lo]) * t,
      y: G.ry[lo] + (G.ry[hi] - G.ry[lo]) * t,
      e: G.re[lo] + (G.re[hi] - G.re[lo]) * t,
      i: lo,
      grade: span > 0 ? (G.re[hi] - G.re[lo]) / span * 100 : 0,
    };
  }
  function legAt(along) {
    for (const l of G.legs) if (along >= l.from_m - 1 && along <= l.to_m + 1) return l;
    return null;
  }
  const LEG_COLORS = ["#2f6b4f", "#2d5fa8", "#a6600c", "#7b4fa0", "#1f8a8a", "#a83a5c", "#5f7a1f", "#9a6a3a"];
  function legColor(i) { return LEG_COLORS[i % LEG_COLORS.length]; }
  function coverPhoto() {
    return H.photos.find(p => p.starred) || H.photos[Math.floor(H.photos.length / 2)] || null;
  }

  // ── topographic map (canvas) ──────────────────────────────────────────────
  const MAPS = new Set();
  function redrawMaps() { MAPS.forEach(m => m.draw()); }
  const AREA_STYLE = {
    forest: ["rgba(86,140,88,0.20)", null],
    open: ["rgba(214,214,150,0.28)", null],
    wetland: ["rgba(110,160,150,0.22)", null],
    bare_rock: ["rgba(170,160,145,0.35)", null],
    scree: ["rgba(176,168,150,0.35)", null],
    glacier: ["rgba(235,245,250,0.8)", "rgba(160,190,210,0.9)"],
    water: ["rgba(150,196,230,0.95)", "rgba(80,140,190,0.9)"],
    reserve: [null, "rgba(130,90,170,0.5)"],
  };
  const LINE_STYLE = {
    river: ["#4f93cc", 2.4, null], stream: ["#5ca0d6", 1.1, null], water: ["#4f93cc", 1.0, null],
    road_major: ["#ffffff", 3.2, null, "#c7b796"], road_minor: ["#ffffff", 2.0, null, "#c9bea6"],
    track: ["#8b6b45", 1.3, [6, 4]], path: ["#6d4f33", 1.2, [4, 3]], path_hard: ["#b3472a", 1.3, [2, 3]],
    cliff: ["#6b6157", 1.4, null], aerialway: ["#555", 1.1, [8, 3, 2, 3]],
  };
  const POINT_ICON = {
    peak: "▲", saddle: "≍", parking: "P", trailhead: "◉", viewpoint: "◈", waterfall: "≋", shelter: "⌂", hut: "⌂",
    campsite: "⛺", spring: "●", drinking_water: "●", ranger_station: "★", toilets: "WC", picnic: "π",
  };

  class TopoMap {
    constructor(host, opts) {
      this.opts = Object.assign({ interactive: true, photos: true, focusOnly: false, embedded: false, onHover: null, onPhoto: null }, opts || {});
      this.box = el("div", { class: "mapbox" });
      this.canvas = el("canvas");
      // A map within a scrolling page on a touch screen: an up/down swipe scrolls the page, sideways
      // drags pan the map and two fingers zoom it
      if (this.opts.embedded && device.touch) this.canvas.style.touchAction = "pan-y";
      this.tip = el("div", { class: "tip" });
      this.box.append(this.canvas, this.tip);
      host.append(this.box);
      this.ctx = this.canvas.getContext("2d");
      this.cursor = null; // along (m)
      this.focus = null; // highlighted photo index
      this.showPhotos = this.opts.photos;
      this.view = { cx: 0, cy: 0, s: 0.1 };
      this.fitted = false;
      if (this.opts.interactive) this._controls();
      this._events();
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(this.box);
      MAPS.add(this);
      H.photos.forEach(p => { if (!p.thumbImg) loadImage(p.thumb).then(img => { p.thumbImg = img; this.draw(); }); });
    }
    destroy() { this.ro.disconnect(); MAPS.delete(this); }
    _controls() {
      const c = el("div", { class: "ctrl" },
        el("button", { title: "Zoom in", text: "+", onclick: () => this.zoomBy(1.6) }),
        el("button", { title: "Zoom out", text: "−", onclick: () => this.zoomBy(1 / 1.6) }),
        el("button", { title: "Show the whole hike", text: "⤢", onclick: () => this.fit() }));
      if (H.photos.length) {
        const pb = el("button", { title: "Show photos", text: "📷", class: this.showPhotos ? "on" : "" });
        pb.onclick = () => { this.showPhotos = !this.showPhotos; pb.classList.toggle("on", this.showPhotos); this.draw(); };
        c.append(pb);
      }
      this.box.append(c);
    }
    resize() {
      const r = this.box.getBoundingClientRect();
      const dpr = pixelRatio(device.phone ? 2 : 3);
      this.w = Math.max(10, r.width); this.h = Math.max(10, r.height);
      this.canvas.width = Math.round(this.w * dpr); this.canvas.height = Math.round(this.h * dpr);
      this.dpr = dpr;
      if (!this.fitted) { this.fitted = true; if (this.focus != null) this.focusPhoto(this.focus, true); else this.fit(); }
      else this.draw();
    }
    fit(box, pad) {
      const b = box || G.fitBox;
      pad = pad == null ? 34 : pad;
      const bw = Math.max(80, b[2] - b[0]), bh = Math.max(80, b[3] - b[1]);
      this.view.s = Math.min((this.w - 2 * pad) / bw, (this.h - 2 * pad) / bh);
      this.view.cx = (b[0] + b[2]) / 2; this.view.cy = (b[1] + b[3]) / 2;
      this.draw();
    }
    focusPhoto(i, keepZoom) {
      this.focus = i;
      const p = H.photos[i];
      if (!p || !this.w) return;
      if (!keepZoom || this.view.s < 0.25) this.view.s = Math.max(this.view.s, 0.35);
      this.view.cx = p.x; this.view.cy = p.y;
      this.draw();
    }
    zoomBy(f, sx, sy) {
      sx = sx == null ? this.w / 2 : sx; sy = sy == null ? this.h / 2 : sy;
      const before = this.unproject(sx, sy);
      this.view.s = clamp(this.view.s * f, 0.004, 8);
      const after = this.unproject(sx, sy);
      this.view.cx += before[0] - after[0]; this.view.cy += before[1] - after[1];
      this.draw();
    }
    sx(x) { return this.w / 2 + (x - this.view.cx) * this.view.s; }
    sy(y) { return this.h / 2 - (y - this.view.cy) * this.view.s; }
    unproject(px, py) { return [this.view.cx + (px - this.w / 2) / this.view.s, this.view.cy - (py - this.h / 2) / this.view.s]; }
    setCursor(along) { this.cursor = along; this.draw(); }
    _events() {
      const c = this.canvas;
      const pointers = new Map();
      let drag = null, pinch = null, moved = false;
      const local = e => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
      c.addEventListener("pointerdown", e => {
        if (!this.opts.interactive) return;
        c.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, local(e));
        moved = false;
        if (pointers.size === 1) drag = { p: local(e), cx: this.view.cx, cy: this.view.cy };
        else if (pointers.size === 2) {
          const [a, b] = [...pointers.values()];
          pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), s: this.view.s };
          drag = null;
        }
      });
      c.addEventListener("pointermove", e => {
        const p = local(e);
        if (pointers.has(e.pointerId)) {
          pointers.set(e.pointerId, p);
          if (pinch && pointers.size === 2) {
            const [a, b] = [...pointers.values()];
            const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
            const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
            this.zoomBy((pinch.s * d / pinch.d) / this.view.s, mx, my);
            moved = true;
            return;
          }
          if (drag) {
            const dx = p[0] - drag.p[0], dy = p[1] - drag.p[1];
            if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
            this.view.cx = drag.cx - dx / this.view.s; this.view.cy = drag.cy + dy / this.view.s;
            this.draw();
            return;
          }
        }
        this._hover(p);
      });
      const end = e => {
        const wasClick = pointers.size === 1 && !moved;
        pointers.delete(e.pointerId);
        if (pointers.size < 2) pinch = null;
        if (pointers.size === 0) drag = null;
        if (wasClick && e.type === "pointerup") this._click(local(e));
      };
      c.addEventListener("pointerup", end);
      c.addEventListener("pointercancel", end);
      c.addEventListener("pointerleave", () => { if (!pointers.size) this._hover(null); });
      c.addEventListener("click", e => { if (!this.opts.interactive) this._click(local(e)); });
      c.addEventListener("wheel", e => {
        if (!this.opts.interactive) return;
        e.preventDefault();
        const p = local(e);
        this.zoomBy(Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0018)), p[0], p[1]);
      }, { passive: false });
      c.addEventListener("dblclick", e => { if (this.opts.interactive) { const p = local(e); this.zoomBy(2, p[0], p[1]); } });
    }
    _photoAt(p) {
      if (!this.showPhotos || !p) return null;
      let best = null, bd = 1e9;
      const pad = device.touch ? 10 : 3; // fingers are less precise than a mouse
      for (const ph of H.photos) {
        const r = this._photoRect(ph);
        if (p[0] >= r.x - pad && p[0] <= r.x + r.w + pad && p[1] >= r.y - pad && p[1] <= r.y + r.h + pad) {
          const d = Math.hypot(p[0] - (r.x + r.w / 2), p[1] - (r.y + r.h / 2));
          if (d < bd) { bd = d; best = ph; }
        }
      }
      return best;
    }
    _nearestAlong(p) {
      let best = -1, bd = 18 * 18;
      for (let i = 0; i < G.rx.length; i++) {
        const dx = this.sx(G.rx[i]) - p[0], dy = this.sy(G.ry[i]) - p[1];
        const d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
      }
      return best < 0 ? null : G.along[best];
    }
    _hover(p) {
      const ph = this._photoAt(p);
      this.canvas.style.cursor = ph ? "pointer" : (this.opts.interactive ? "grab" : (this.opts.onPhoto ? "pointer" : "default"));
      const along = p && !ph ? this._nearestAlong(p) : null;
      if (ph) {
        this._tip(p, (ph.time_iso ? fmt.time(ph.time_iso) + " · " : "") + (ph.caption || ph.trail || "Photo " + (ph.i + 1)));
      } else if (along != null) {
        const pt = pointAt(along), leg = legAt(along);
        this._tip([this.sx(pt.x), this.sy(pt.y)], fmt.dist(along) + " · " + fmt.ele(pt.e) + (leg && leg.named ? " · " + leg.name : ""));
      } else this._tip(null);
      if (this.opts.onHover) this.opts.onHover(along);
    }
    _tip(p, text) {
      if (!p) { this.tip.style.display = "none"; return; }
      this.tip.textContent = text;
      this.tip.style.left = p[0] + "px"; this.tip.style.top = p[1] + "px";
      this.tip.style.display = "block";
    }
    _click(p) {
      const ph = this._photoAt(p);
      if (ph && this.opts.onPhoto) this.opts.onPhoto(ph.i);
      else if (device.touch) this._hover(p); // no hover on a touch screen: a tap on the route shows where it is
    }
    _photoRect(ph) {
      const big = this.view.s > 0.12 && ph.thumbImg && (!this.opts.focusOnly || ph.i === this.focus);
      const sz = big ? clamp(this.view.s * 120, 30, 62) : 12;
      const x = this.sx(ph.x), y = this.sy(ph.y);
      if (!big) return { x: x - sz / 2, y: y - sz / 2, w: sz, h: sz, big: false, ax: x, ay: y };
      const ar = ph.tw && ph.th ? ph.tw / ph.th : 1.33;
      const w = ar >= 1 ? sz : sz * ar, h = ar >= 1 ? sz / ar : sz;
      return { x: x - w / 2, y: y - h - 10, w: w, h: h, big: true, ax: x, ay: y };
    }
    draw() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = null; this._paint(); });
    }
    _path(ctx, xy, close) {
      ctx.beginPath();
      for (let i = 0; i < xy.length; i += 2) {
        const x = this.sx(xy[i]), y = this.sy(xy[i + 1]);
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      if (close) ctx.closePath();
    }
    _paint() {
      const ctx = this.ctx, w = this.w, h = this.h, s = this.view.s;
      if (!w) return;
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.fillStyle = "#e9eddf";
      ctx.fillRect(0, 0, w, h);
      // shaded relief
      const R = G.relief;
      if (R && R.img) {
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(R.img, this.sx(R.x0), this.sy(R.y1), (R.x1 - R.x0) * s, (R.y1 - R.y0) * s);
      }
      // land cover & water
      for (const kind of ["forest", "open", "wetland", "scree", "bare_rock", "glacier", "water", "reserve"]) {
        const st = AREA_STYLE[kind];
        for (const a of G.areas) {
          if (a.k !== kind) continue;
          this._path(ctx, a.xy, true);
          if (st[0]) { ctx.fillStyle = st[0]; ctx.fill(); }
          if (st[1]) { ctx.strokeStyle = st[1]; ctx.lineWidth = kind === "reserve" ? 1.4 : 1; ctx.setLineDash(kind === "reserve" ? [7, 4] : []); ctx.stroke(); ctx.setLineDash([]); }
        }
      }
      // contours
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      for (const c of G.contours) {
        if (!c.i && s < 0.03) continue;
        this._path(ctx, c.xy, false);
        ctx.strokeStyle = c.i ? "rgba(140,98,58,0.55)" : "rgba(150,110,70,0.30)";
        ctx.lineWidth = c.i ? 1.1 : 0.7;
        ctx.stroke();
      }
      if (s > 0.06) this._contourLabels(ctx);
      // lines: casings first, then fills
      const order = ["stream", "river", "water", "cliff", "aerialway", "road_major", "road_minor", "track", "path", "path_hard"];
      for (const pass of [0, 1]) {
        for (const kind of order) {
          const st = LINE_STYLE[kind];
          if (!st || (pass === 0 && !st[3])) continue;
          if ((kind === "path" || kind === "track" || kind === "path_hard") && s < 0.02) continue;
          for (const l of G.lines) {
            if (l.k !== kind) continue;
            this._path(ctx, l.xy, false);
            ctx.setLineDash(pass === 1 && st[2] ? st[2] : []);
            ctx.strokeStyle = pass === 0 ? st[3] : st[0];
            ctx.lineWidth = pass === 0 ? st[1] + 1.6 : st[1];
            ctx.stroke();
          }
        }
      }
      ctx.setLineDash([]);
      this._points(ctx);
      this._route(ctx);
      if (this.showPhotos) this._photos(ctx);
      this._nights(ctx);
      this._scale(ctx);
      ctx.fillStyle = "rgba(255,255,255,0.75)";
      ctx.fillRect(w - 214, h - 17, 214, 17);
      ctx.fillStyle = "#4b554f"; ctx.font = "10.5px " + getComputedStyle(document.body).fontFamily;
      ctx.textAlign = "right"; ctx.fillText("© OpenStreetMap contributors · USGS 3DEP", w - 6, h - 5);
      ctx.textAlign = "left";
    }
    _contourLabels(ctx) {
      const placed = [];
      ctx.font = "10px sans-serif";
      ctx.fillStyle = "rgba(120,82,46,0.85)";
      for (const c of G.contours) {
        if (!c.i || c.xy.length < 8) continue;
        const k = Math.floor(c.xy.length / 4) * 2;
        const x = this.sx(c.xy[k]), y = this.sy(c.xy[k + 1]);
        if (x < 20 || y < 20 || x > this.w - 20 || y > this.h - 20) continue;
        if (placed.some(p => Math.abs(p[0] - x) < 80 && Math.abs(p[1] - y) < 30)) continue;
        placed.push([x, y]);
        const label = fmt.ele(c.e);
        ctx.save();
        ctx.strokeStyle = "rgba(240,240,228,0.9)"; ctx.lineWidth = 3;
        ctx.strokeText(label, x, y); ctx.fillText(label, x, y);
        ctx.restore();
      }
    }
    _points(ctx) {
      const s = this.view.s;
      const placed = [];
      ctx.textBaseline = "middle";
      for (const p of G.points) {
        const big = p.k === "peak" || p.k === "parking" || p.k === "trailhead";
        if (!big && s < 0.08) continue;
        const x = this.sx(p.x), y = this.sy(p.y);
        if (x < -20 || y < -20 || x > this.w + 20 || y > this.h + 20) continue;
        const icon = POINT_ICON[p.k] || "•";
        ctx.font = (p.k === "parking" ? "bold 11px " : "12px ") + "sans-serif";
        ctx.textAlign = "center";
        if (p.k === "parking") {
          ctx.fillStyle = "#2d5fa8"; ctx.fillRect(x - 7, y - 7, 14, 14);
          ctx.fillStyle = "#fff"; ctx.fillText("P", x, y + 0.5);
        } else {
          ctx.fillStyle = p.k === "peak" ? "#5a4632" : (p.k === "waterfall" || p.k === "spring" || p.k === "drinking_water") ? "#2f7fbf" : "#3f5146";
          ctx.fillText(icon, x, y);
        }
        if (p.n && (big || s > 0.15)) {
          const label = p.n + (p.k === "peak" && p.e ? " " + fmt.ele(p.e) : "");
          ctx.font = (p.k === "peak" ? "600 11.5px " : "11px ") + "sans-serif";
          const tw = ctx.measureText(label).width;
          const box = [x + 9, y - 7, tw, 14];
          if (placed.some(b => box[0] < b[0] + b[2] && box[0] + box[2] > b[0] && box[1] < b[1] + b[3] && box[1] + box[3] > b[1])) continue;
          placed.push(box);
          ctx.textAlign = "left";
          ctx.strokeStyle = "rgba(250,250,244,0.92)"; ctx.lineWidth = 3;
          ctx.strokeText(label, x + 9, y); ctx.fillStyle = "#26302a"; ctx.fillText(label, x + 9, y);
        }
      }
      ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
    }
    _route(ctx) {
      const n = G.rx.length;
      const line = (from, to, color, width) => {
        ctx.beginPath();
        for (let i = from; i <= to; i++) {
          const x = this.sx(G.rx[i]), y = this.sy(G.ry[i]);
          if (i === from) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
      };
      line(0, n - 1, "rgba(255,255,255,0.95)", 7);
      line(0, n - 1, "#e0612b", 3.6);
      if (this.cursor != null) {
        const pt = pointAt(this.cursor);
        line(0, pt.i, "#8e2f12", 3.6);
      }
      // start & end
      const sx = this.sx(G.rx[0]), sy = this.sy(G.ry[0]);
      const ex = this.sx(G.rx[n - 1]), ey = this.sy(G.ry[n - 1]);
      const loop = Math.hypot(sx - ex, sy - ey) < 10;
      const dot = (x, y, fill, label) => {
        ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2);
        ctx.fillStyle = fill; ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = "#fff"; ctx.stroke();
        ctx.fillStyle = "#fff"; ctx.font = "bold 9px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(label, x, y + 0.5); ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      };
      if (!loop) dot(ex, ey, "#b94a3b", "■");
      dot(sx, sy, "#2f8a55", loop ? "⟲" : "▶");

      if (this.cursor != null) {
        const pt = pointAt(this.cursor);
        const x = this.sx(pt.x), y = this.sy(pt.y);
        ctx.beginPath(); ctx.arc(x, y, 14, 0, Math.PI * 2); ctx.fillStyle = "rgba(231,76,60,0.22)"; ctx.fill();
        ctx.beginPath(); ctx.arc(x, y, 6.5, 0, Math.PI * 2); ctx.fillStyle = "#e74c3c"; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke();
      }
    }
    _nights(ctx) {
      for (const night of G.nights) {
        const pt = pointAt(night.along), x = this.sx(pt.x), y = this.sy(pt.y);
        ctx.beginPath(); ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.fillStyle = "#3b4b8f"; ctx.fill(); ctx.lineWidth = 2.5; ctx.strokeStyle = "#fff"; ctx.stroke();
        ctx.font = "11px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillStyle = "#fff";
        ctx.fillText("☾", x, y + 0.5);
        const label = "Night " + night.n + (night.name ? ": " + night.name : "");
        ctx.font = "600 11.5px sans-serif"; ctx.textAlign = "left";
        ctx.strokeStyle = "rgba(250,250,244,0.95)"; ctx.lineWidth = 3; ctx.strokeText(label, x + 14, y);
        ctx.fillStyle = "#26302a"; ctx.fillText(label, x + 14, y);
        ctx.textBaseline = "alphabetic";
      }
    }
    _photos(ctx) {
      const list = H.photos.slice().sort((a, b) => (a.i === this.focus) - (b.i === this.focus) || b.y - a.y);
      for (const ph of list) {
        const r = this._photoRect(ph);
        const sel = ph.i === this.focus;
        if (r.big) {
          ctx.strokeStyle = sel ? "#f4d03f" : "#fff"; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.moveTo(r.ax, r.ay); ctx.lineTo(r.ax, r.y + r.h); ctx.stroke();
          ctx.fillStyle = "rgba(0,0,0,0.25)"; ctx.fillRect(r.x + 1.5, r.y + 2, r.w, r.h);
          ctx.drawImage(ph.thumbImg, r.x, r.y, r.w, r.h);
          ctx.lineWidth = sel ? 3 : 2; ctx.strokeStyle = sel ? "#f4d03f" : "#fff"; ctx.strokeRect(r.x, r.y, r.w, r.h);
          ctx.beginPath(); ctx.arc(r.ax, r.ay, 3, 0, Math.PI * 2); ctx.fillStyle = "#f39c12"; ctx.fill();
        } else {
          ctx.beginPath(); ctx.arc(r.ax, r.ay, sel ? 7.5 : 5.5, 0, Math.PI * 2);
          ctx.fillStyle = sel ? "#f4d03f" : "#f39c12"; ctx.fill();
          ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke();
        }
      }
    }
    _scale(ctx) {
      const targetPx = 110;
      const m = targetPx / this.view.s;
      const unitM = units === "us" ? (m > 1609 / 2 ? MI : 1 / FT) : (m > 1000 ? 1000 : 1);
      const raw = m / unitM;
      const nice = [1, 2, 5].map(f => f * Math.pow(10, Math.floor(Math.log10(raw)))).concat([10 * Math.pow(10, Math.floor(Math.log10(raw)))])
        .filter(v => v <= raw).pop() || raw;
      const px = nice * unitM * this.view.s;
      const label = nice + (units === "us" ? (unitM === MI ? " mi" : " ft") : (unitM === 1000 ? " km" : " m"));
      const x = 12, y = this.h - 26;
      ctx.fillStyle = "rgba(255,255,255,0.8)"; ctx.fillRect(x - 4, y - 14, px + 8, 22);
      ctx.strokeStyle = "#26302a"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x, y - 4); ctx.lineTo(x, y + 2); ctx.lineTo(x + px, y + 2); ctx.lineTo(x + px, y - 4); ctx.stroke();
      ctx.fillStyle = "#26302a"; ctx.font = "11px sans-serif"; ctx.fillText(label, x + 3, y - 3);
      // north arrow
      ctx.font = "bold 12px sans-serif"; ctx.textAlign = "center";
      ctx.fillText("N", 22, 22); ctx.beginPath(); ctx.moveTo(22, 26); ctx.lineTo(17, 38); ctx.lineTo(27, 38); ctx.closePath(); ctx.fill();
      ctx.textAlign = "left";
    }
  }

  // ── elevation profile ─────────────────────────────────────────────────────
  class Profile {
    constructor(host, opts) {
      this.opts = Object.assign({ onHover: null, onPhoto: null, height: 170 }, opts || {});
      this.box = el("div", { class: "chart", style: { height: this.opts.height + "px" } });
      this.canvas = el("canvas");
      this.box.append(this.canvas);
      host.append(this.box);
      this.ctx = this.canvas.getContext("2d");
      this.cursor = null;
      const lo = Math.min.apply(null, G.re), hi = Math.max.apply(null, G.re);
      const pad = Math.max(10, (hi - lo) * 0.12);
      this.lo = lo - pad; this.hi = hi + pad;
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(this.box);
      const local = e => { const r = this.canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
      const track = e => {
        const p = local(e);
        const along = clamp((p[0] - this.l) / (this.w - this.l - this.r) * G.total, 0, G.total);
        const ph = this._photoAt(p);
        this.canvas.style.cursor = ph ? "pointer" : "crosshair";
        this.setCursor(along);
        if (this.opts.onHover) this.opts.onHover(along);
      };
      this.canvas.addEventListener("pointermove", track);
      this.canvas.addEventListener("pointerdown", track); // a finger touches down without hovering first
      this.canvas.addEventListener("pointerleave", e => {
        if (e.pointerType !== "mouse") return; // a lifted finger leaves the readout where it was
        this.setCursor(null); if (this.opts.onHover) this.opts.onHover(null);
      });
      this.canvas.addEventListener("click", e => { const ph = this._photoAt(local(e)); if (ph && this.opts.onPhoto) this.opts.onPhoto(ph.i); });
    }
    destroy() { this.ro.disconnect(); }
    resize() {
      const r = this.box.getBoundingClientRect(), dpr = pixelRatio(device.phone ? 2 : 3);
      this.w = r.width; this.h = r.height; this.dpr = dpr;
      this.canvas.width = Math.round(r.width * dpr); this.canvas.height = Math.round(r.height * dpr);
      this.l = units === "us" ? 52 : 46; this.r = 12; this.t = G.legs.length > 1 ? 24 : 10; this.b = 24;
      this.draw();
    }
    X(a) { return this.l + a / G.total * (this.w - this.l - this.r); }
    Y(e) { return this.t + (1 - (e - this.lo) / (this.hi - this.lo)) * (this.h - this.t - this.b); }
    _photoAt(p) {
      for (const ph of H.photos) {
        const x = this.X(ph.along_m), y = this.Y(pointAt(ph.along_m).e);
        if (Math.hypot(p[0] - x, p[1] - y) < (device.touch ? 14 : 8)) return ph;
      }
      return null;
    }
    setCursor(a) { this.cursor = a; this.draw(); }
    draw() { if (!this._raf) this._raf = requestAnimationFrame(() => { this._raf = null; this._paint(); }); }
    _paint() {
      const ctx = this.ctx, w = this.w, h = this.h;
      if (!w) return;
      const dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
      const ink = dark ? "#c9d6cd" : "#46524b", grid = dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.07)";
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.font = "11px " + getComputedStyle(document.body).fontFamily;
      // trail bands
      G.legs.forEach((leg, i) => {
        const x0 = this.X(leg.from_m), x1 = this.X(leg.to_m);
        if (i % 2) { ctx.fillStyle = dark ? "rgba(255,255,255,0.03)" : "rgba(0,0,0,0.025)"; ctx.fillRect(x0, this.t, x1 - x0, h - this.t - this.b); }
        if (G.legs.length > 1) {
          ctx.fillStyle = leg.named ? legColor(i) : "#999";
          ctx.fillRect(x0, 4, Math.max(1, x1 - x0 - 1), 4);
          const name = leg.named ? leg.name : "";
          if (name && x1 - x0 > 50) {
            ctx.fillStyle = ink;
            let t = name;
            while (t.length > 3 && ctx.measureText(t).width > x1 - x0 - 6) t = t.slice(0, -2);
            if (t !== name) t = t.slice(0, -1) + "…";
            ctx.fillText(t, x0 + 3, 19);
          }
        }
      });
      // grid & axis labels
      const span = this.hi - this.lo;
      const stepM = units === "us" ? [25, 50, 100, 200, 250, 500, 1000].map(v => v / FT).find(v => span / v <= 5) || 1000 / FT
        : [10, 20, 25, 50, 100, 200, 500].find(v => span / v <= 5) || 500;
      ctx.strokeStyle = grid; ctx.fillStyle = ink; ctx.lineWidth = 1;
      ctx.textAlign = "right";
      for (let e = Math.ceil(this.lo / stepM) * stepM; e <= this.hi; e += stepM) {
        const y = this.Y(e);
        ctx.beginPath(); ctx.moveTo(this.l, y); ctx.lineTo(w - this.r, y); ctx.stroke();
        ctx.fillText(fmt.ele(e), this.l - 6, y + 4);
      }
      ctx.textAlign = "center";
      const unitM = units === "us" ? MI : 1000;
      const dStep = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10].find(v => G.total / unitM / v <= 8) || 10;
      for (let d = 0; d <= G.total / unitM + 1e-9; d += dStep) {
        const x = this.X(d * unitM);
        ctx.fillText((Math.round(d * 100) / 100) + (units === "us" ? " mi" : " km"), x, h - 6);
      }
      // area + line
      const n = G.re.length, step = Math.max(1, Math.floor(n / 1200));
      ctx.beginPath(); ctx.moveTo(this.X(0), this.Y(this.lo));
      for (let i = 0; i < n; i += step) ctx.lineTo(this.X(G.along[i]), this.Y(G.re[i]));
      ctx.lineTo(this.X(G.total), this.Y(G.re[n - 1])); ctx.lineTo(this.X(G.total), this.Y(this.lo)); ctx.closePath();
      const grad = ctx.createLinearGradient(0, this.t, 0, h - this.b);
      grad.addColorStop(0, "rgba(224,97,43,0.40)"); grad.addColorStop(1, "rgba(224,97,43,0.04)");
      ctx.fillStyle = grad; ctx.fill();
      ctx.beginPath();
      for (let i = 0; i < n; i += step) { const x = this.X(G.along[i]), y = this.Y(G.re[i]); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
      ctx.lineTo(this.X(G.total), this.Y(G.re[n - 1]));
      ctx.strokeStyle = "#e0612b"; ctx.lineWidth = 2; ctx.stroke();
      // nights of a multi-day hike
      for (const night of G.nights) {
        const x = this.X(night.along);
        ctx.strokeStyle = "rgba(59,75,143,0.8)"; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x, this.t); ctx.lineTo(x, h - this.b); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = "#3b4b8f"; ctx.font = "600 11px sans-serif"; ctx.textAlign = "left";
        ctx.fillText("☾ Night " + night.n, x + 4, h - this.b - 6);
      }
      // photos
      for (const ph of H.photos) {
        const x = this.X(ph.along_m), y = this.Y(pointAt(ph.along_m).e);
        ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = "#f39c12"; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = "#fff"; ctx.stroke();
      }
      // cursor
      if (this.cursor != null) {
        const pt = pointAt(this.cursor), x = this.X(this.cursor), y = this.Y(pt.e);
        ctx.strokeStyle = "rgba(142,47,18,0.7)"; ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(x, this.t); ctx.lineTo(x, h - this.b); ctx.stroke(); ctx.setLineDash([]);
        ctx.beginPath(); ctx.arc(x, y, 5.5, 0, Math.PI * 2); ctx.fillStyle = "#e74c3c"; ctx.fill();
        ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke();
        const label = fmt.dist(this.cursor) + " · " + fmt.ele(pt.e);
        ctx.font = "600 11.5px sans-serif";
        const tw = ctx.measureText(label).width + 12;
        const bx = clamp(x - tw / 2, this.l, w - this.r - tw);
        ctx.fillStyle = "rgba(20,28,23,0.88)"; ctx.fillRect(bx, this.t + 2, tw, 19);
        ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.fillText(label, bx + 6, this.t + 15.5);
      }
      ctx.textAlign = "left";
    }
  }

  // ── 3D replay (port of src/gui/replay3d.py) ───────────────────────────────
  const VIEW_RADIUS_M = 4200, FOG_START = 1800, FOG_END = 5200, BASE_DURATION_S = 75;
  const APPROACH_M = 300, DEPART_M = 250, EASE_SHARE = 0.06, MIN_SPEED = 0.12;
  // Photo stops: the same timing as the app (src/gui/replay3d.py)
  const BLEND_IN_S = 1.0, BLEND_OUT_S = 0.9, FIRST_HOLD_S = 3.0, NEXT_HOLD_S = 1.5, XFADE_S = 0.5;
  const SKY_TOP = [88, 140, 196], SKY_HORIZON = [196, 214, 226];
  // Map layers on the terrain (as in the app): kind -> [rgb, pen width, fades out beyond this depth]
  const LAYER_PENS = {
    water_edge: [[76, 155, 214], 1.6, FOG_END], river: [[76, 155, 214], 2.8, FOG_END],
    stream: [[110, 182, 232], 1.3, 3600], trail: [[214, 178, 112], 1.8, 4200], path: [[196, 168, 120], 1.0, 2400],
  };
  const WATER_RGB = [58, 116, 168];
  const lerpAngle = (a, b, t) => { const d = ((b - a + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI; return a + d * t; };

  function makeCamera(x, y, z, yaw, pitch, focal, cx, cy) { return { x, y, z, yaw, pitch, focal, cx, cy }; }
  function projector(c) {
    const cp = Math.cos(c.pitch), sp = Math.sin(c.pitch);
    const fx = Math.cos(c.yaw) * cp, fy = Math.sin(c.yaw) * cp, fz = -sp;
    const rx = Math.sin(c.yaw), ry = -Math.cos(c.yaw), rz = 0;
    const ux = ry * fz - rz * fy, uy = rz * fx - rx * fz, uz = rx * fy - ry * fx;
    return function (x, y, z) {
      const dx = x - c.x, dy = y - c.y, dz = z - c.z;
      const depth = dx * fx + dy * fy + dz * fz;
      if (depth < 5) return null;
      return [c.cx + c.focal * (dx * rx + dy * ry + dz * rz) / depth, c.cy - c.focal * (dx * ux + dy * uy + dz * uz) / depth, depth];
    };
  }
  function chaseCamera(t, yaw, w, h, groundAt) {
    const back = 1300, up = 600, ahead = 500, clearance = 150;
    const cx = t[0] - Math.cos(yaw) * back, cy = t[1] - Math.sin(yaw) * back;
    const cz = Math.max(t[2] + up, groundAt(cx, cy) + clearance);
    return makeCamera(cx, cy, cz, yaw, Math.atan2(cz - t[2], back + ahead), w * 0.85, w / 2, h * 0.42);
  }
  function lookCamera(hk, target, fallbackYaw, w, h, groundAt) {
    const back = 750, up = 380, clearance = 150;
    const dx = target[0] - hk[0], dy = target[1] - hk[1];
    const yaw = Math.hypot(dx, dy) > 40 ? Math.atan2(dy, dx) : fallbackYaw;
    const cx = hk[0] - Math.cos(yaw) * back, cy = hk[1] - Math.sin(yaw) * back;
    const cz = Math.max(hk[2] + up, groundAt(cx, cy) + clearance);
    const horiz = Math.max(1, Math.hypot(target[0] - cx, target[1] - cy));
    return makeCamera(cx, cy, cz, yaw, Math.atan2(cz - target[2], horiz), w * 0.85, w / 2, h * 0.42);
  }
  function blendCameras(a, b, t) {
    t = clamp(t, 0, 1);
    return makeCamera(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t,
      lerpAngle(a.yaw, b.yaw, t), a.pitch + (b.pitch - a.pitch) * t, a.focal + (b.focal - a.focal) * t, a.cx, a.cy);
  }

  // ── vegetation (ports of the plant scatter in src/hiking/vegetation.py and src/gui/vegetation_sprites.py) ──
  // Plants are drawn taller than life so they read from the chase camera (~1.5 km back)
  const VEG_SCALE = 1.6, VEG_NEAR = 1300, VEG_FAR = 2700, VEG_MIN_PX = 1.5, VEG_FULL_PX = 6;
  const VEG_BOOST = { krummholz: 2.0, shrub: 2.4, sage: 2.4, grass: 4.0, boulder: 1.6 };
  const VEG_GRID_BASE = 48, PLANTS_PER_SUBCELL = 6;
  const SPECIES_HEIGHT_M = {
    broadleaf: [15, 24], aspen: [9, 16], spruce: [10, 20], fir: [18, 32], pine: [16, 30], lodgepole: [15, 24],
    juniper: [3, 7], joshua: [4, 9], krummholz: [0.8, 2.2], shrub: [0.6, 2.0], sage: [0.5, 1.4], grass: [0.3, 0.7], boulder: [0.8, 2.6],
  };
  const TREES = new Set(["broadleaf", "aspen", "spruce", "fir", "pine", "lodgepole", "juniper", "joshua"]);
  const SPRITE_PX = 128, GROUND_FRAC = (SPRITE_PX - 5) / SPRITE_PX; // where a plant meets the ground
  const SPRITE_ASPECT = {
    broadleaf: 0.85, aspen: 0.5, spruce: 0.42, fir: 0.62, pine: 0.62, lodgepole: 0.38,
    juniper: 1.0, joshua: 0.95, krummholz: 2.4, shrub: 1.5, sage: 1.6, grass: 1.8, boulder: 1.5,
  };

  function seededRng(seed) { // mulberry32: the same plants on every frame and every visit
    let a = seed >>> 0;
    const next = () => {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), a | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    return { random: next, uniform: (lo, hi) => lo + (hi - lo) * next(), randint: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)) };
  }
  function strSeed(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return h >>> 0; }
  const rgbCss = (rgb, k = 1, a = 1) => `rgba(${Math.min(255, (rgb[0] * k) | 0)},${Math.min(255, (rgb[1] * k) | 0)},${Math.min(255, (rgb[2] * k) | 0)},${a})`;
  const jitter = (rgb, rng, amount = 10) => rgb.map(ch => clamp(ch + rng.randint(-amount, amount), 0, 255));

  function spriteBlob(g, x, y, rx, ry, rgb) { // leafy clump lit from the upper left
    const fx = x - rx * 0.35, fy = y - ry * 0.4, grad = g.createRadialGradient(fx, fy, 0, fx, fy, Math.max(rx, ry) * 1.25);
    grad.addColorStop(0, rgbCss(rgb, 1.35)); grad.addColorStop(0.55, rgbCss(rgb)); grad.addColorStop(1, rgbCss(rgb, 0.62));
    g.fillStyle = grad; g.beginPath(); g.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2); g.fill();
  }
  function spritePoly(g, pts) { g.beginPath(); pts.forEach((q, i) => i ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1])); g.closePath(); }
  function spriteLine(g, x0, y0, x1, y1, css, width) { g.strokeStyle = css; g.lineWidth = width; g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke(); }
  function spriteTrunk(g, x, y0, y1, width, rgb) {
    const grad = g.createLinearGradient(x - width / 2, 0, x + width / 2, 0);
    grad.addColorStop(0, rgbCss(rgb, 1.25)); grad.addColorStop(1, rgbCss(rgb, 0.6));
    g.fillStyle = grad;
    spritePoly(g, [[x - width / 2, y0], [x - width * 0.3, y1], [x + width * 0.3, y1], [x + width / 2, y0]]); g.fill();
  }
  function bareBranches(g, x, y, length, angle, width, depth, rgb, rng) {
    const x2 = x + Math.cos(angle) * length, y2 = y - Math.sin(angle) * length;
    spriteLine(g, x, y, x2, y2, rgbCss(rgb, 1, 0.9), Math.max(0.8, width));
    if (depth > 0) {
      for (const da of depth > 2 ? [-0.45, 0.4, 0] : [-0.45, 0.4]) {
        bareBranches(g, x2, y2, length * rng.uniform(0.6, 0.78), angle + da + rng.uniform(-0.15, 0.15), width * 0.62, depth - 1, rgb, rng);
      }
    }
  }
  function conifer(g, w, h, gy, rgb, tiers, spread, rng) { // tiered spire, narrowing to a point
    const cx = w / 2;
    spriteTrunk(g, cx, gy, gy - h * 0.16, w * 0.09, [86, 62, 44]);
    const top = 3, bottom = gy - h * 0.07, left = [[cx, top]], right = [[cx, top]];
    for (let k = 0; k < tiers; k++) {
      const yIn = top + (bottom - top) * (k + 0.55) / tiers, yOut = top + (bottom - top) * (k + 1) / tiers;
      const hw = w * spread * Math.pow((k + 1) / tiers, 0.9) * rng.uniform(0.88, 1.08);
      left.push([cx - hw * 0.5, yIn], [cx - hw, yOut]);
      right.push([cx + hw * 0.5, yIn + rng.uniform(-1, 1)], [cx + hw * rng.uniform(0.9, 1.05), yOut]);
    }
    const grad = g.createLinearGradient(cx - w * spread, 0, cx + w * spread, 0);
    grad.addColorStop(0, rgbCss(rgb, 1.3)); grad.addColorStop(0.5, rgbCss(rgb)); grad.addColorStop(1, rgbCss(rgb, 0.62));
    g.fillStyle = grad; g.strokeStyle = rgbCss(rgb, 0.55); g.lineWidth = 1;
    spritePoly(g, left.concat(right.reverse())); g.fill(); g.stroke();
  }
  function paintSprite(species, season, rock, variant) {
    const rng = seededRng(strSeed(species + "/" + variant));
    const h = SPRITE_PX, w = Math.max(8, Math.round(h * SPRITE_ASPECT[species]));
    const cv = document.createElement("canvas");
    cv.width = w; cv.height = h;
    const g = cv.getContext("2d");
    g.lineCap = "round";
    const gy = h * GROUND_FRAC, cx = w / 2;
    const shadow = g.createRadialGradient(cx, gy, 0, cx, gy, w * 0.42); // soft contact shadow
    shadow.addColorStop(0, "rgba(0,0,0,0.27)"); shadow.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = shadow; g.beginPath(); g.ellipse(cx, gy, w * 0.42, 5, 0, 0, Math.PI * 2); g.fill();

    if (species === "spruce") conifer(g, w, h, gy, jitter([32, 70, 46], rng, 6), 8, 0.47, rng);
    else if (species === "fir") conifer(g, w, h, gy, jitter([42, 84, 52], rng, 6), 7, 0.49, rng);
    else if (species === "pine" || species === "lodgepole") {
      const tall = species === "lodgepole";
      const bark = tall ? [112, 88, 66] : [140, 92, 62];
      const crownTop = h * (tall ? 0.03 : 0.05), crownBottom = h * (tall ? 0.58 : 0.5);
      spriteTrunk(g, cx, gy, crownTop + h * 0.08, w * (tall ? 0.1 : 0.08), bark);
      const rgb = jitter(tall ? [50, 86, 50] : [56, 92, 52], rng, 6), n = tall ? 9 : 8;
      for (let k = 0; k < n; k++) {
        const t = k / (n - 1), y = crownTop + (crownBottom - crownTop) * t + rng.uniform(-3, 3);
        const spread = tall ? 0.2 : 0.3 + 0.12 * Math.sin(t * Math.PI);
        const x = cx + (w * spread * rng.uniform(0.2, 1.0)) * (k % 2 ? 1 : -1);
        spriteLine(g, cx, y + 4, x, y, rgbCss(bark, 0.8), 1.4);
        spriteBlob(g, x, y, w * (tall ? 0.17 : 0.2) * rng.uniform(0.8, 1.15), h * 0.06 * rng.uniform(0.8, 1.2), rgb);
      }
    } else if (species === "broadleaf" || species === "aspen") {
      const aspen = species === "aspen", bark = aspen ? [222, 222, 210] : [92, 74, 58];
      if (season === "winter") {
        spriteTrunk(g, cx, gy, h * 0.45, w * (aspen ? 0.07 : 0.1), bark);
        const twig = aspen ? [196, 196, 186] : [96, 84, 72];
        for (const a of [Math.PI / 2 + 0.5, Math.PI / 2 - 0.5, Math.PI / 2 + 0.1]) {
          bareBranches(g, cx, h * 0.48, h * (aspen ? 0.2 : 0.24), a + rng.uniform(-0.1, 0.1), w * (aspen ? 0.035 : 0.05), 4, twig, rng);
        }
      } else {
        spriteTrunk(g, cx, gy, h * 0.42, w * (aspen ? 0.07 : 0.1), bark);
        if (aspen) { // dark knots on white bark
          g.fillStyle = "rgb(50,46,40)";
          for (let k = 0; k < 4; k++) { g.beginPath(); g.ellipse(cx + rng.uniform(-1.5, 1.5), gy - h * (0.12 + 0.08 * k), 1.6, 1.0, 0, 0, Math.PI * 2); g.fill(); }
        }
        let rgb;
        if (season === "autumn") {
          const palette = aspen ? [[236, 192, 46], [226, 178, 40], [240, 204, 70], [190, 176, 60]]
            : [[180, 60, 40], [206, 122, 42], [214, 176, 62], [150, 114, 54]];
          rgb = palette[variant % palette.length];
        } else rgb = aspen ? [112, 160, 72] : [[70, 118, 52], [82, 126, 58], [62, 108, 50], [76, 114, 46]][variant];
        const ccx = cx, ccy = h * (aspen ? 0.32 : 0.34), rx = w * (aspen ? 0.26 : 0.3), ry = h * (aspen ? 0.26 : 0.22);
        for (let k = 0; k < 8; k++) {
          const a = rng.uniform(0, Math.PI * 2), d = rng.uniform(0, 0.85);
          spriteBlob(g, ccx + Math.cos(a) * rx * d, ccy + Math.sin(a) * ry * d, w * rng.uniform(0.18, 0.25), h * rng.uniform(0.09, 0.13), jitter(rgb, rng, 12));
        }
      }
    } else if (species === "juniper") {
      const rgb = variant % 2 ? [78, 104, 80] : [66, 96, 58]; // juniper blue-green / pinyon green
      spriteLine(g, cx, gy, cx + rng.uniform(-6, 6), gy - h * 0.3, rgbCss([104, 90, 74]), w * 0.06);
      for (let k = 0; k < 7; k++) {
        spriteBlob(g, cx + rng.uniform(-0.3, 0.3) * w, h * rng.uniform(0.3, 0.68), w * rng.uniform(0.17, 0.26), h * rng.uniform(0.13, 0.19), jitter(rgb, rng, 8));
      }
    } else if (species === "joshua") {
      const bark = [122, 104, 84], fx = cx, fy = h * 0.52, ends = [];
      spriteLine(g, cx, gy, fx, fy, rgbCss(bark), w * 0.09);
      const arms = rng.randint(2, 4);
      for (let k = 0; k < arms; k++) {
        const a = Math.PI / 2 + rng.uniform(-0.9, 0.9);
        const mx = fx + Math.cos(a) * w * 0.2, my = fy - Math.sin(a) * h * 0.14;
        const ex = mx + Math.cos(a * 0.8 + 0.3) * w * 0.12, ey = my - h * rng.uniform(0.08, 0.16);
        spriteLine(g, fx, fy, mx, my, rgbCss(bark, 0.95), w * 0.065);
        spriteLine(g, mx, my, ex, ey, rgbCss(bark, 0.95), w * 0.065);
        ends.push([ex, ey]);
      }
      for (const e of ends) { // spiky leaf rosettes
        for (let k = 0; k < 14; k++) {
          const a = k / 14 * Math.PI * 2;
          spriteLine(g, e[0], e[1], e[0] + Math.cos(a) * w * 0.1, e[1] + Math.sin(a) * h * 0.06, rgbCss([98, 118, 58]), 1.6);
        }
        spriteBlob(g, e[0], e[1], w * 0.05, h * 0.03, [110, 128, 64]);
      }
    } else if (species === "krummholz") {
      const rgb = jitter([40, 74, 48], rng, 6);
      for (let k = 0; k < 14; k++) {
        const x = rng.uniform(0.1, 0.9) * w, top = 0.5 + 0.45 * (1 - Math.abs(x / w - 0.45) * 2); // flagged downwind
        spriteBlob(g, x, gy - h * rng.uniform(0.15, top * 0.6), w * rng.uniform(0.07, 0.12), h * rng.uniform(0.14, 0.24), rgb);
      }
    } else if (species === "shrub" || species === "sage") {
      let rgb;
      if (species === "sage") rgb = [138, 148, 118];
      else if (season === "autumn") rgb = [[150, 62, 44], [168, 98, 48], [134, 70, 48], [150, 116, 56]][variant]; // heath and blueberry
      else if (season === "winter") rgb = [112, 98, 82];
      else rgb = [72, 112, 56];
      const n = species === "shrub" ? 6 : 8;
      for (let k = 0; k < n; k++) {
        spriteBlob(g, cx + rng.uniform(-0.3, 0.3) * w, gy - h * rng.uniform(0.25, 0.55), w * rng.uniform(0.16, 0.24), h * rng.uniform(0.2, 0.32), jitter(rgb, rng, 10));
      }
    } else if (species === "grass") {
      const rgb = { autumn: [186, 162, 94], winter: [170, 150, 112] }[season] || [128, 148, 74];
      for (let k = 0; k < 26; k++) {
        const a = Math.PI / 2 + rng.uniform(-1.05, 1.05), length = h * rng.uniform(0.55, 0.95);
        const col = jitter(rgb, rng, 18);
        spriteLine(g, cx + rng.uniform(-w * 0.1, w * 0.1), gy, cx + Math.cos(a) * length * 0.9, gy - Math.sin(a) * length, rgbCss(col, rng.uniform(0.75, 1.2)), 2.6);
      }
    } else if (species === "boulder") {
      const rgb = jitter(rock, rng, 8), pts = [], n = 11;
      for (let k = 0; k < n; k++) {
        const a = Math.PI + k / (n - 1) * Math.PI; // upper half, left to right
        pts.push([cx + Math.cos(a) * w * 0.44 * rng.uniform(0.85, 1.0), gy + Math.sin(a) * h * 0.8 * rng.uniform(0.8, 1.0)]);
      }
      const grad = g.createLinearGradient(cx - w * 0.3, gy - h * 0.7, cx + w * 0.3, gy);
      grad.addColorStop(0, rgbCss(rgb, 1.3)); grad.addColorStop(1, rgbCss(rgb, 0.6));
      g.fillStyle = grad; g.strokeStyle = rgbCss(rgb, 0.5); g.lineWidth = 1;
      spritePoly(g, pts); g.fill(); g.stroke();
    }
    return cv;
  }
  function makeSprites(season, rock) {
    // species -> variants, each with mip levels (128 px tall halved down to 16) so small plants stay smooth
    const out = {};
    for (const sp in SPRITE_ASPECT) {
      out[sp] = [0, 1, 2, 3].map(v => {
        const levels = [paintSprite(sp, season, rock, v)];
        for (let src = levels[0]; src.height > 16;) {
          const dst = document.createElement("canvas");
          dst.width = Math.max(1, src.width >> 1); dst.height = src.height >> 1;
          dst.getContext("2d").drawImage(src, 0, 0, dst.width, dst.height);
          levels.push(src = dst);
        }
        return { aspect: levels[0].width / levels[0].height, levels };
      });
    }
    return out;
  }

  class Replay3D {
    constructor(host) {
      const R = H.replay;
      const m = R.mesh;
      this.mesh = { x0: m.x0, y0: m.y0, cell: m.cell, cols: m.cols, rows: m.rows, z: Float32Array.from(m.z), color: Uint8Array.from(m.color) };
      const xyz = R.path.xyz, n = xyz.length / 3;
      this.px = new Float64Array(n); this.py = new Float64Array(n); this.pz = new Float64Array(n);
      for (let i = 0; i < n; i++) { this.px[i] = xyz[3 * i]; this.py[i] = xyz[3 * i + 1]; this.pz[i] = xyz[3 * i + 2]; }
      // Life zones and land cover: each land-cover sample's habitat grows the same kind of plants as in the app
      this.zx = R.zx || 1.5;
      this.plantCells = new Map();
      this.veg = null;
      if (R.veg) {
        const V = R.veg, step = m.cell / V.sub;
        this.veg = { sub: V.sub, step, habitats: V.habitats, grid: V.grid, treeline: V.treeline, clear: V.clear,
          nx: (m.cols - 1) * V.sub, ny: (m.rows - 1) * V.sub };
        this.sprites = makeSprites(V.season, V.rock);
        this.avoid = new Map(); // route points by land-cover sample, to keep the trail itself clear
        for (let i = 0; i < n; i++) {
          const k = Math.floor((this.py[i] - m.y0) / step) * 100000 + Math.floor((this.px[i] - m.x0) / step);
          if (!this.avoid.has(k)) this.avoid.set(k, []);
          this.avoid.get(k).push(this.px[i], this.py[i]);
        }
      }
      this.pe = Float64Array.from(R.path.ele);
      this.cum = new Float64Array(n);
      for (let i = 1; i < n; i++) this.cum[i] = this.cum[i - 1] + Math.hypot(this.px[i] - this.px[i - 1], this.py[i] - this.py[i - 1]);
      this.length = this.cum[n - 1] || 1;
      this.scale = this.length / G.total; // replay path metres per route metre
      this.trail = [];
      this.trailCells = new Map();
      for (let i = 0; i < n; i++) this.trail.push([this.px[i], this.py[i], Math.max(this.pz[i], this.heightAt(this.px[i], this.py[i])) + 6]);
      for (let i = 0; i < n - 1; i++) {
        const k = this.cellKey((this.px[i] + this.px[i + 1]) / 2, (this.py[i] + this.py[i + 1]) / 2);
        if (!this.trailCells.has(k)) this.trailCells.set(k, []);
        this.trailCells.get(k).push(i);
      }
      // Map layers: segments grouped by the terrain cell they lie on; lake pieces per cell
      this.layerCells = new Map();
      this.waterCells = new Map();
      const L = R.layers || { names: [], kinds: [], segs: [], water: [] };
      this.layerSegs = L.segs;
      this.layerKinds = L.kinds.map(k => L.names[k]);
      for (let i = 0; i < this.layerKinds.length; i++) {
        const o = 6 * i;
        const key = this.cellKey((L.segs[o] + L.segs[o + 3]) / 2, (L.segs[o + 1] + L.segs[o + 4]) / 2);
        if (!this.layerCells.has(key)) this.layerCells.set(key, []);
        this.layerCells.get(key).push(i);
      }
      for (const w of L.water) {
        const key = w.cell[0] * 100000 + w.cell[1];
        if (!this.waterCells.has(key)) this.waterCells.set(key, []);
        this.waterCells.get(key).push(w.ring);
      }
      this.stops = R.stops.map(s => ({ photos: s.photos.map(i => H.photos[i]), hidden: s.hidden, along: s.along * this.scale, world: s.world }));
      this.stopCells = new Map();
      this.stops.forEach((s, i) => {
        const k = this.cellKey(s.world[0], s.world[1]);
        if (!this.stopCells.has(k)) this.stopCells.set(k, []);
        this.stopCells.get(k).push(i);
      });
      this.peaks = R.peaks;
      this.dist = 0; this.nextStop = 0; this.phase = "paused"; this.phaseT = 0; this.photoK = 0;
      this.speed = 1; this.yaw = this.heading(0); this.running = false; this.last = 0;
      this.full = new Map(); // photo index -> full Image
      H.photos.forEach(p => { if (!p.thumbImg) loadImage(p.thumb).then(img => { p.thumbImg = img; }); });
      this._build(host);
    }
    // geometry
    cellKey(x, y) { const r = Math.floor((y - this.mesh.y0) / this.mesh.cell), c = Math.floor((x - this.mesh.x0) / this.mesh.cell); return r * 100000 + c; }
    heightAt(x, y) {
      const M = this.mesh;
      const fx = clamp((x - M.x0) / M.cell, 0, M.cols - 1.001), fy = clamp((y - M.y0) / M.cell, 0, M.rows - 1.001);
      const c = Math.floor(fx), r = Math.floor(fy), tx = fx - c, ty = fy - r, z = M.z, C = M.cols;
      const top = z[r * C + c] + (z[r * C + c + 1] - z[r * C + c]) * tx;
      const bot = z[(r + 1) * C + c] + (z[(r + 1) * C + c + 1] - z[(r + 1) * C + c]) * tx;
      return top + (bot - top) * ty;
    }
    at(d) {
      d = clamp(d, 0, this.length);
      const cum = this.cum;
      let lo = 0, hi = cum.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (cum[mid] < d) lo = mid; else hi = mid; }
      const seg = cum[hi] - cum[lo], t = seg > 0 ? (d - cum[lo]) / seg : 0;
      return [this.px[lo] + (this.px[hi] - this.px[lo]) * t, this.py[lo] + (this.py[hi] - this.py[lo]) * t,
        this.pz[lo] + (this.pz[hi] - this.pz[lo]) * t, this.pe[lo] + (this.pe[hi] - this.pe[lo]) * t];
    }
    heading(d) {
      const a = this.at(d - 150), b = this.at(d + 300);
      if (Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1]) < 1e-6) return 0;
      return Math.atan2(b[1] - a[1], b[0] - a[0]);
    }
    // vegetation
    habitat(i, j) {
      const V = this.veg;
      return V.habitats[V.grid.charCodeAt(clamp(j, 0, V.ny - 1) * V.nx + clamp(i, 0, V.nx - 1)) - VEG_GRID_BASE];
    }
    zoneAt(x, y) { // Spruce–fir forest, Krummholz, Alpine tundra, ...
      if (!this.veg) return "";
      const s = this.veg.step;
      return this.habitat(Math.floor((x - this.mesh.x0) / s), Math.floor((y - this.mesh.y0) / s))[0];
    }
    _nearTrail(x, y) {
      const s = this.veg.step, i = Math.floor((x - this.mesh.x0) / s), j = Math.floor((y - this.mesh.y0) / s), r2 = this.veg.clear * this.veg.clear;
      for (let dj = -1; dj <= 1; dj++) {
        for (let di = -1; di <= 1; di++) {
          const pts = this.avoid.get((j + dj) * 100000 + i + di);
          if (!pts) continue;
          for (let k = 0; k < pts.length; k += 2) if ((pts[k] - x) ** 2 + (pts[k + 1] - y) ** 2 < r2) return true;
        }
      }
      return false;
    }
    _cellPlants(r, c) {
      // [x, y, z, drawn height in mesh units, sprite], made once per cell (as Vegetation.plants in the app)
      const key = r * 100000 + c;
      let out = this.plantCells.get(key);
      if (out) return out;
      out = [];
      const V = this.veg, M = this.mesh, sub = V.sub, step = V.step;
      const rng = seededRng((r * 1000003 + c) * 31);
      for (let j = 0; j < sub; j++) {
        for (let i = 0; i < sub; i++) {
          const hab = this.habitat(c * sub + i, r * sub + j), density = hab[1], flora = hab[2];
          if (!flora.length || density <= 0) continue;
          const sx = M.x0 + c * M.cell + i * step, sy = M.y0 + r * M.cell + j * step;
          const ele = this.heightAt(sx + step / 2, sy + step / 2) / this.zx;
          const want = density * PLANTS_PER_SUBCELL;
          const count = Math.floor(want) + (rng.random() < want - Math.floor(want) ? 1 : 0);
          const total = flora.reduce((t, f) => t + f[1], 0) || 1;
          for (let k = 0; k < count; k++) {
            const x = sx + rng.random() * step, y = sy + rng.random() * step;
            let pick = rng.random() * total, sp = flora[flora.length - 1][0];
            for (const f of flora) { if ((pick -= f[1]) < 0) { sp = f[0]; break; } }
            const variant = rng.randint(0, 3), [lo, hi] = SPECIES_HEIGHT_M[sp];
            let height = lo + (hi - lo) * rng.random();
            if (this._nearTrail(x, y)) continue;
            if (TREES.has(sp) && V.treeline != null && ele > V.treeline - 200) {
              height *= Math.max(0.35, 0.45 + 0.55 * (V.treeline - ele) / 200); // stunted near treeline
            }
            out.push([x, y, this.heightAt(x, y), this.zx * VEG_SCALE * height * (VEG_BOOST[sp] || 1), this.sprites[sp][variant]]);
          }
        }
      }
      out.tallest = out.reduce((t, p) => Math.max(t, p[3]), 0);
      this.plantCells.set(key, out);
      return out;
    }
    _plants(ctx, project, focal, r, c, depth, fog) {
      // This cell's trees, shrubs, grass and boulders, back to front. Always the same plants: they only
      // grow as the camera nears them, each fading in while still a few pixels tall, so none pop
      const plants = this._cellPlants(r, c);
      if (!plants.length || focal * plants.tallest / Math.max(1, depth - this.mesh.cell) < VEG_MIN_PX) return;
      const vis = [];
      for (const pl of plants) {
        const s = project(pl[0], pl[1], pl[2]);
        if (!s) continue;
        const hpx = focal * pl[3] / s[2];
        if (hpx >= VEG_MIN_PX) vis.push([s[2], s[0], s[1], hpx, pl[4]]);
      }
      if (!vis.length) return;
      if (depth < VEG_NEAR) vis.sort((a, b) => b[0] - a[0]); // farther away the order within a cell isn't noticeable
      const alpha = (1 - fog * 0.9) * Math.min(1, (VEG_FAR - depth) / 500);
      for (const v of vis) {
        const hpx = v[3], sprite = v[4];
        ctx.globalAlpha = hpx < VEG_FULL_PX ? alpha * (hpx - VEG_MIN_PX) / (VEG_FULL_PX - VEG_MIN_PX) : alpha;
        const img = sprite.levels[Math.min(sprite.levels.length - 1, hpx <= 16 ? 3 : hpx <= 32 ? 2 : hpx <= 64 ? 1 : 0)];
        const w = hpx * sprite.aspect;
        ctx.drawImage(img, v[1] - w * 0.5, v[2] - hpx * GROUND_FRAC, w, hpx); // ground point at (sx, sy)
      }
      ctx.globalAlpha = 1;
    }
    // UI
    _build(host) {
      this.root = el("div", { class: "replay" });
      this.stage = el("div", { class: "stage" });
      this.canvas = el("canvas");
      this.stage.append(this.canvas);
      this.playBtn = el("button", { class: "primary", text: "▶ Play", onclick: () => this.toggle() });
      this.skipBtn = el("button", { text: "Skip photo ⏭", disabled: true, onclick: () => this.skipPhoto() });
      this.slider = el("input", { type: "range", min: 0, max: 1000, value: 0, "aria-label": "Position along the hike" });
      this.slider.addEventListener("input", () => this.seek(this.slider.value / 1000));
      this.speedSel = el("select", { "aria-label": "Speed" },
        [["0.5×", 0.5], ["1×", 1], ["2×", 2], ["4×", 4]].map(o => el("option", { value: o[1], text: o[0], selected: o[1] === 1 })));
      this.speedSel.addEventListener("change", () => { this.speed = Number(this.speedSel.value); });
      // Full screen where the browser allows it (not on iPhones); a phone turns to landscape for it
      const fs = document.fullscreenEnabled ? el("button", { class: "fs", text: "⛶", title: "Full screen", "aria-label": "Full screen", onclick: () => {
        if (document.fullscreenElement) { document.exitFullscreen(); return; }
        const done = this.root.requestFullscreen();
        if (device.phone && done && done.then) done.then(() => screen.orientation && screen.orientation.lock && screen.orientation.lock("landscape")).catch(() => {});
      } }) : null;
      this.root.append(this.stage, el("div", { class: "bar" }, this.playBtn, this.skipBtn, this.slider, this.speedSel, fs,
        el("span", { class: "hint", text: "Space: play / pause · →: skip photo · click the view to pause" })));
      // A tap (or click) on the view: leave the photo being shown, else play / pause
      let down = null;
      this.canvas.addEventListener("pointerdown", e => { down = [e.clientX, e.clientY]; });
      this.canvas.addEventListener("pointerup", e => {
        if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 10) { down = null; return; }
        down = null;
        if (this.phase === "blend_in" || this.phase === "hold") this.skipPhoto(); else this.toggle();
      });
      host.append(this.root);
      this.ctx = this.canvas.getContext("2d");
      this.small = document.createElement("canvas");
      this.ro = new ResizeObserver(() => this.resize());
      this.ro.observe(this.stage);
      this.onKey = e => {
        if (!this.root.isConnected || e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
        if (e.code === "Space") { e.preventDefault(); this.toggle(); }
        else if ((e.key === "ArrowRight" || e.key === "Enter") && (this.phase === "blend_in" || this.phase === "hold")) this.skipPhoto();
      };
      document.addEventListener("keydown", this.onKey);
    }
    destroy() { this.running = false; this.ro.disconnect(); document.removeEventListener("keydown", this.onKey); }
    resize() {
      // Phones draw at a lower pixel ratio: thousands of plants a frame stay smooth on a small GPU
      const r = this.stage.getBoundingClientRect(), dpr = pixelRatio(device.phone ? 1.5 : 2);
      this.w = r.width; this.h = r.height; this.dpr = dpr;
      this.canvas.width = Math.round(r.width * dpr); this.canvas.height = Math.round(r.height * dpr);
      this.render();
    }
    // control
    get speedMps() { return Math.max(1, this.length / BASE_DURATION_S) * this.speed; }
    toggle() { this.running ? this.pause() : this.play(); }
    play() {
      if (this.phase === "done") this.seek(0);
      if (this.phase === "paused" || this.phase === "done") this.phase = "playing";
      this.running = true; this.last = performance.now();
      this._sync();
      if (this.looping) return;
      this.looping = true;
      const tick = now => {
        if (!this.running || !this.root.isConnected) { this.looping = false; return; }
        const dt = Math.min(0.2, (now - this.last) / 1000);
        this.last = now;
        this.advance(dt);
        this.render();
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
    pause() { this.running = false; if (this.phase === "playing") this.phase = "paused"; this._sync(); this.render(); }
    seek(f) {
      this.dist = clamp(f, 0, 1) * this.length;
      this.nextStop = this.stops.findIndex(s => s.along >= this.dist - 1);
      if (this.nextStop < 0) this.nextStop = this.stops.length;
      if (["blend_in", "hold", "blend_out", "done"].includes(this.phase)) this.phase = this.running ? "playing" : "paused";
      this.photoK = 0; this.yaw = this.heading(this.dist);
      this._sync(); this.render();
    }
    seekAlong(alongM) { this.seek(alongM * this.scale / this.length); }
    skipPhoto() {
      if (this.phase === "blend_in") { const done = Math.min(1, this.phaseT / BLEND_IN_S); this.phase = "blend_out"; this.phaseT = (1 - done) * BLEND_OUT_S; }
      else if (this.phase === "hold") { this.phase = "blend_out"; this.phaseT = 0; }
    }
    _sync() {
      const photo = this.phase === "blend_in" || this.phase === "hold" || this.phase === "blend_out";
      this.playBtn.textContent = this.running ? "⏸ Pause" : (this.phase === "done" ? "↺ Replay" : "▶ Play");
      this.skipBtn.disabled = !photo;
      this.slider.value = Math.round(this.dist / this.length * 1000);
    }
    easeM(m) { return Math.min(m, Math.max(40, this.length * EASE_SHARE)); }
    approach() {
      if (this.nextStop >= this.stops.length) return 0;
      const rem = this.stops[this.nextStop].along - this.dist;
      return rem >= 0 ? 1 - smoothstep(rem / this.easeM(APPROACH_M)) : 1;
    }
    departure() {
      if (this.nextStop === 0) return 0;
      const prev = this.stops[Math.min(this.nextStop, this.stops.length) - 1];
      const gone = this.dist - prev.along;
      return gone >= 0 ? 1 - smoothstep(gone / this.easeM(DEPART_M)) : 0;
    }
    speedFactor() { const slow = Math.max(this.approach(), this.departure()); return Math.max(MIN_SPEED, 1 - slow * (1 - MIN_SPEED)); }
    holdS(k) { return k === 0 ? FIRST_HOLD_S : NEXT_HOLD_S; }
    advance(dt) {
      this._prefetch();
      if (this.phase === "playing") {
        this.dist = Math.min(this.length, this.dist + this.speedMps * this.speedFactor() * dt);
        this.yaw = lerpAngle(this.yaw, this.heading(this.dist), Math.min(1, dt * 1.8));
        if (this.nextStop < this.stops.length && this.dist >= this.stops[this.nextStop].along) {
          this.phase = "blend_in"; this.phaseT = 0; this.photoK = 0; this._sync();
        } else if (this.dist >= this.length) {
          this.phase = "done"; this.running = false; this._sync();
        }
        this.slider.value = Math.round(this.dist / this.length * 1000);
      } else if (this.phase === "blend_in" || this.phase === "hold" || this.phase === "blend_out") {
        const stop = this.stops[this.nextStop];
        this.phaseT += dt;
        if (this.phase === "blend_in" && this.phaseT >= BLEND_IN_S) { this.phase = "hold"; this.phaseT = 0; }
        else if (this.phase === "hold" && this.phaseT >= this.holdS(this.photoK)) {
          if (this.photoK + 1 < stop.photos.length) { this.photoK++; this.phaseT = 0; }
          else { this.phase = "blend_out"; this.phaseT = 0; }
        } else if (this.phase === "blend_out" && this.phaseT >= BLEND_OUT_S) {
          this.nextStop++; this.photoK = 0; this.phase = "playing"; this._sync();
        }
      }
    }
    _prefetch() {
      const want = [];
      const s = this.stops[this.nextStop];
      if (s) want.push.apply(want, s.photos.slice(this.photoK, this.photoK + 2));
      const s2 = this.stops[this.nextStop + 1];
      if (s2) want.push(s2.photos[0]);
      for (const p of want) {
        if (!this.full.has(p.i)) { this.full.set(p.i, null); loadImage(p.src).then(img => this.full.set(p.i, img)); }
      }
    }
    camera(w, h) {
      const p = this.at(this.dist);
      const hk = [p[0], p[1], Math.max(p[2], this.heightAt(p[0], p[1]))];
      const ga = (x, y) => this.heightAt(x, y);
      const chase = chaseCamera(hk, this.yaw, w, h, ga);
      let weight, stop;
      if (this.phase === "blend_in" || this.phase === "hold" || this.phase === "blend_out") { weight = 1; stop = this.stops[this.nextStop]; }
      else {
        const a = this.approach(), d = this.departure();
        if (a >= d && this.nextStop < this.stops.length) { weight = a; stop = this.stops[this.nextStop]; }
        else if (d > 0) { weight = d; stop = this.stops[this.nextStop - 1]; }
        else return chase;
      }
      if (weight <= 0) return chase;
      return blendCameras(chase, lookCamera(hk, stop.world, this.yaw, w, h, ga), weight);
    }
    // rendering
    render() {
      const ctx = this.ctx, w = this.w, h = this.h;
      if (!w) return;
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      const p = this.at(this.dist);
      const cam = this.camera(w, h);
      const project = projector(cam);
      const sky = ctx.createLinearGradient(0, 0, 0, h * 0.6);
      sky.addColorStop(0, `rgb(${SKY_TOP})`); sky.addColorStop(1, `rgb(${SKY_HORIZON})`);
      ctx.fillStyle = sky; ctx.fillRect(0, 0, w, h);
      this._terrain(ctx, cam, project);
      this._peaks(ctx, project);
      this._hiker(ctx, project, [p[0], p[1], Math.max(p[2], this.heightAt(p[0], p[1])) + 6]);
      this._hud(ctx, w, h, p[3]);
      if ((this.phase === "blend_in" || this.phase === "hold" || this.phase === "blend_out") && this.nextStop < this.stops.length) {
        this._overlay(ctx, w, h, project, this.stops[this.nextStop]);
      }
    }
    _terrain(ctx, cam, project) {
      const M = this.mesh, C = M.cols;
      const rc = Math.floor(VIEW_RADIUS_M / M.cell);
      const hx = cam.x + Math.cos(cam.yaw) * VIEW_RADIUS_M * 0.45, hy = cam.y + Math.sin(cam.yaw) * VIEW_RADIUS_M * 0.45;
      const hr = Math.floor((hy - M.y0) / M.cell), hc = Math.floor((hx - M.x0) / M.cell);
      const r0 = Math.max(0, hr - rc), r1 = Math.min(M.rows - 2, hr + rc), c0 = Math.max(0, hc - rc), c1 = Math.min(M.cols - 2, hc + rc);
      const pw = c1 - c0 + 2;
      const proj = new Array((r1 - r0 + 2) * pw);
      for (let r = r0; r <= r1 + 1; r++) {
        for (let c = c0; c <= c1 + 1; c++) {
          proj[(r - r0) * pw + (c - c0)] = project(M.x0 + c * M.cell, M.y0 + r * M.cell, M.z[r * C + c]);
        }
      }
      const w = this.w, h = this.h, cells = [];
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          const a = proj[(r - r0) * pw + (c - c0)], b = proj[(r - r0) * pw + (c - c0 + 1)];
          const cc = proj[(r - r0 + 1) * pw + (c - c0 + 1)], d = proj[(r - r0 + 1) * pw + (c - c0)];
          if (!a || !b || !cc || !d) continue;
          const minx = Math.min(a[0], b[0], cc[0], d[0]), maxx = Math.max(a[0], b[0], cc[0], d[0]);
          const miny = Math.min(a[1], b[1], cc[1], d[1]), maxy = Math.max(a[1], b[1], cc[1], d[1]);
          if (maxx < 0 || minx > w || maxy < 0 || miny > h) continue;
          const depth = (a[2] + b[2] + cc[2] + d[2]) / 4;
          if (depth > FOG_END + 600) continue;
          cells.push([depth, r, c, a, b, cc, d]);
        }
      }
      cells.sort((u, v) => v[0] - u[0]);
      const doneI = clamp(Math.floor(this.dist / this.length * (this.trail.length - 1)), 0, this.trail.length - 2);
      const photoPhase = this.phase === "blend_in" || this.phase === "hold" || this.phase === "blend_out";
      ctx.lineJoin = "round";
      for (const cell of cells) {
        const depth = cell[0], r = cell[1], c = cell[2], a = cell[3], b = cell[4], cc = cell[5], d = cell[6];
        const ci = (r * (C - 1) + c) * 3;
        const f = depth <= FOG_START ? 0 : Math.min(1, (depth - FOG_START) / (FOG_END - FOG_START));
        const col = "rgb(" + Math.round(M.color[ci] + (SKY_HORIZON[0] - M.color[ci]) * f) + "," +
          Math.round(M.color[ci + 1] + (SKY_HORIZON[1] - M.color[ci + 1]) * f) + "," +
          Math.round(M.color[ci + 2] + (SKY_HORIZON[2] - M.color[ci + 2]) * f) + ")";
        ctx.fillStyle = col; ctx.strokeStyle = col; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.lineTo(cc[0], cc[1]); ctx.lineTo(d[0], d[1]); ctx.closePath();
        ctx.fill(); ctx.stroke();
        const key = r * 100000 + c;
        const water = this.waterCells.get(key);
        if (water) {
          const wc = "rgb(" + Math.round(WATER_RGB[0] + (SKY_HORIZON[0] - WATER_RGB[0]) * f) + "," +
            Math.round(WATER_RGB[1] + (SKY_HORIZON[1] - WATER_RGB[1]) * f) + "," + Math.round(WATER_RGB[2] + (SKY_HORIZON[2] - WATER_RGB[2]) * f) + ")";
          ctx.fillStyle = wc; ctx.strokeStyle = wc; ctx.lineWidth = 1;
          for (const ring of water) {
            ctx.beginPath();
            let ok = true;
            for (let k = 0; k < ring.length; k += 3) {
              const q = project(ring[k], ring[k + 1], ring[k + 2]);
              if (!q) { ok = false; break; }
              if (k) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]);
            }
            if (ok) { ctx.closePath(); ctx.fill(); ctx.stroke(); }
          }
        }
        const lsegs = this.layerCells.get(key);
        if (lsegs) this._layers(ctx, project, lsegs, depth);
        if (this.veg && depth < VEG_FAR) this._plants(ctx, project, cam.focal, r, c, depth, f);
        // Trail and photo markers on this cell are drawn now, so nearer terrain hides them
        const segs = this.trailCells.get(key);
        if (segs) {
          for (const i of segs) {
            const s0 = project(this.trail[i][0], this.trail[i][1], this.trail[i][2]);
            const s1 = project(this.trail[i + 1][0], this.trail[i + 1][1], this.trail[i + 1][2]);
            if (!s0 || !s1) continue;
            ctx.strokeStyle = i < doneI ? "#F39C12" : "rgba(236,245,238,0.86)";
            ctx.lineWidth = i < doneI ? 3.2 : 2.2;
            ctx.beginPath(); ctx.moveTo(s0[0], s0[1]); ctx.lineTo(s1[0], s1[1]); ctx.stroke();
          }
        }
        const st = this.stopCells.get(key);
        if (st) {
          for (const si of st) {
            if (si > this.nextStop || (si === this.nextStop && !photoPhase)) this._marker(ctx, project, this.stops[si]);
          }
        }
      }
    }
    _layers(ctx, project, ids, depth) {
      const byKind = {};
      for (const i of ids) {
        const kind = this.layerKinds[i];
        if (depth > LAYER_PENS[kind][2]) continue;
        (byKind[kind] = byKind[kind] || []).push(i);
      }
      const S = this.layerSegs;
      for (const kind in byKind) {
        const [rgb, width, reach] = LAYER_PENS[kind];
        const fade = depth <= FOG_START ? 1 : Math.max(0, 1 - (depth - FOG_START) / Math.max(1, reach - FOG_START));
        if (fade < 0.08) continue;
        ctx.strokeStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${(0.88 * fade).toFixed(3)})`;
        ctx.lineWidth = width;
        ctx.beginPath();
        for (const i of byKind[kind]) {
          const o = 6 * i;
          const a = project(S[o], S[o + 1], S[o + 2]), b = project(S[o + 3], S[o + 4], S[o + 5]);
          if (a && b) { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); }
        }
        ctx.stroke();
      }
    }
    _markerRect(project, stop) {
      const s = project(stop.world[0], stop.world[1], stop.world[2]);
      if (!s) return null;
      const size = Math.max(10, Math.min(46, 26000 / s[2]));
      return { gx: s[0], gy: s[1], x: s[0] - size / 2, y: s[1] - size * 0.9 - size * 0.75, w: size, h: size * 0.75 };
    }
    _marker(ctx, project, stop) {
      const r = this._markerRect(project, stop);
      if (!r) return;
      ctx.strokeStyle = "#F39C12"; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.moveTo(r.gx, r.gy); ctx.lineTo(r.gx, r.y + r.h); ctx.stroke();
      const img = stop.photos[0].thumbImg;
      if (img) ctx.drawImage(img, r.x, r.y, r.w, r.h); else { ctx.fillStyle = "#0b1610"; ctx.fillRect(r.x, r.y, r.w, r.h); }
      ctx.strokeRect(r.x, r.y, r.w, r.h);
      const n = stop.photos.length + stop.hidden;
      if (n > 1 && r.w >= 18) {
        ctx.fillStyle = "#F39C12";
        ctx.beginPath(); ctx.arc(r.x + r.w + 2, r.y - 1, 8, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "#0b1610"; ctx.font = "bold 9px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(n < 100 ? String(n) : "99+", r.x + r.w + 2, r.y - 0.5);
        ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      }
    }
    _peaks(ctx, project) {
      ctx.font = "bold 11px sans-serif";
      const placed = [];
      for (const pk of this.peaks) {
        const s = project(pk[1], pk[2], pk[3]);
        if (!s || s[2] > FOG_END || s[0] < 0 || s[0] > this.w || s[1] < 0 || s[1] > this.h) continue;
        const label = pk[0] + "  " + fmt.ele(pk[4]);
        const tw = ctx.measureText(label).width;
        const box = [s[0] - tw / 2, s[1] - 26, tw, 14];
        if (placed.some(b => box[0] < b[0] + b[2] && box[0] + box[2] > b[0] && box[1] < b[1] + b[3] && box[1] + box[3] > b[1])) continue;
        placed.push(box);
        ctx.fillStyle = "#fff";
        ctx.beginPath(); ctx.moveTo(s[0], s[1] - 6); ctx.lineTo(s[0] - 4, s[1]); ctx.lineTo(s[0] + 4, s[1]); ctx.closePath(); ctx.fill();
        ctx.fillStyle = "rgb(20,30,24)"; ctx.fillText(label, box[0] + 1, s[1] - 13);
        ctx.fillStyle = "#fff"; ctx.fillText(label, box[0], s[1] - 14);
      }
    }
    _hiker(ctx, project, pos) {
      // A hiker seen from behind (the chase camera's view): backpack, hat and trekking pole, legs and
      // arms swinging with the distance walked (still while paused)
      const s = project(pos[0], pos[1], pos[2]);
      if (!s) return;
      const top = project(pos[0], pos[1], pos[2] + 70); // sized like a 70 m figure, readable at any zoom
      const hg = clamp(top ? s[1] - top[1] : 36, 26, 64);
      const gx = s[0], gy = s[1], swing = Math.sin(this.dist / 0.9); // one stride every ~5.7 m
      const at = (dx, dy) => [gx + dx * hg, gy - dy * hg]; // offsets in figure heights from the ground point
      const ell = (q, rx, ry, fill) => { ctx.fillStyle = fill; ctx.beginPath(); ctx.ellipse(q[0], q[1], rx, ry, 0, 0, Math.PI * 2); ctx.fill(); };
      const seg = (a, b, css, width) => { ctx.strokeStyle = css; ctx.lineWidth = width; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
      const box = (x, y, bw, bh, rad, fill) => {
        ctx.fillStyle = fill; ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, y, bw, bh, rad); else ctx.rect(x, y, bw, bh);
        ctx.fill();
      };
      ctx.lineCap = "round";
      // Shadow and a soft halo so the figure stands out on any terrain
      ell([gx, gy], hg * 0.42, hg * 0.13, "rgba(231,76,60,0.235)");
      ell([gx, gy], hg * 0.22, hg * 0.07, "rgba(0,0,0,0.353)");
      // Legs: the stepping foot lifts and both shift with the stride; then the boots
      const feet = [[-1, swing], [1, -swing]].map(([side, ph]) => [side, at(side * 0.09, 0.02 + Math.max(0, ph) * 0.07)]);
      for (const [side, foot] of feet) seg(at(side * 0.07, 0.46), foot, "#2C3E50", hg * 0.12);
      for (const [, foot] of feet) ell(foot, hg * 0.065, hg * 0.065, "#5D4037");
      // Arms swing opposite to the legs; the right hand holds the pole
      const lHand = at(-0.21, 0.50 + swing * 0.04), rHand = at(0.21, 0.50 - swing * 0.04);
      seg(at(-0.15, 0.76), lHand, "#1F6FA8", hg * 0.09);
      seg(at(0.15, 0.76), rHand, "#1F6FA8", hg * 0.09);
      seg([rHand[0], rHand[1] - hg * 0.06], at(0.27 - swing * 0.05, 0), "rgb(220,220,220)", Math.max(1.5, hg * 0.035));
      // Jacket, then the backpack over it (seen from behind)
      box(gx - hg * 0.16, gy - hg * 0.82, hg * 0.32, hg * 0.40, hg * 0.08, "#2E86C1");
      box(gx - hg * 0.14, gy - hg * 0.86, hg * 0.28, hg * 0.36, hg * 0.07, "#E67E22");
      box(gx - hg * 0.10, gy - hg * 0.66, hg * 0.20, hg * 0.10, hg * 0.03, "#B85E12");
      ctx.lineCap = "butt";
      seg(at(-0.14, 0.62), at(0.14, 0.62), "#8E4A0E", Math.max(1, hg * 0.025)); // hip belt
      // Head and hat
      ell(at(0, 0.92), hg * 0.085, hg * 0.09, "#E8B990");
      ell(at(0, 0.955), hg * 0.14, hg * 0.035, "#6D4C41"); // brim
      box(gx - hg * 0.08, gy - hg * 1.06, hg * 0.16, hg * 0.10, hg * 0.04, "#6D4C41");
    }
    _hud(ctx, w, h, ele) {
      const round = (x, y, bw, bh, r) => { ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x, y, bw, bh, r) : ctx.rect(x, y, bw, bh); };
      // Small screens (phones, either way up) get a tighter HUD and no mini map
      const compact = w < 600 || h < 420, m = compact ? 10 : 14, showMini = w >= 520 && h >= 420;
      const bw = showMini ? Math.min(330, w - 200) : Math.min(330, w - 2 * m), bh = compact ? 76 : 92;
      const lx = m + 12, ys = compact ? [m + 19, m + 39, m + 64] : [m + 22, m + 46, m + 78];
      const maxW = bw - 24;
      ctx.fillStyle = "rgba(11,22,16,0.78)"; round(m, m, bw, bh, 8); ctx.fill();
      ctx.fillStyle = "#ECF5EE"; ctx.font = (compact ? "bold 12px" : "bold 13px") + " sans-serif";
      let name = H.summary.route_name || H.summary.title;
      while (name.length > 4 && ctx.measureText(name).width > maxW) name = name.slice(0, -2);
      if (name !== (H.summary.route_name || H.summary.title)) name += "…";
      ctx.fillText(name, lx, ys[0]);
      const along = this.dist / this.scale;
      const leg = legAt(along);
      ctx.fillStyle = "#98B2A1"; ctx.font = (compact ? "11px" : "12px") + " sans-serif";
      ctx.fillText(fmt.dist(along) + " of " + fmt.dist(G.total) + (leg && leg.named ? " · " + leg.name : ""), lx, ys[1], maxW);
      ctx.fillStyle = "#F4D03F"; ctx.font = (compact ? "bold 17px" : "bold 21px") + " sans-serif";
      const alt = "▲ " + fmt.ele(ele), altW = ctx.measureText(alt).width;
      ctx.fillText(alt, lx, ys[2]);
      // Life zone at the hiker (Spruce–fir forest, Krummholz, Alpine tundra, ...)
      const here = this.at(this.dist), zone = this.zoneAt(here[0], here[1]);
      const room = maxW - 12 - altW;
      if (zone && room > 30) {
        ctx.fillStyle = "#98B2A1"; ctx.font = (compact ? "11px" : "12px") + " sans-serif";
        let z = zone;
        while (z.length > 3 && ctx.measureText(z).width > room) z = z.slice(0, -2);
        ctx.fillText(z === zone ? z : z + "…", lx + altW + 12, ys[2] - 1);
      }
      // elevation profile
      const ph = compact ? 62 : 90, px = m, py = h - ph - m, pw = w - 2 * m;
      ctx.fillStyle = "rgba(11,22,16,0.75)"; round(px, py, pw, ph, 8); ctx.fill();
      const ix = px + 12, iy = py + 10, iw = pw - 24, ih = ph - 26;
      let lo = Infinity, hi = -Infinity;
      for (const e of this.pe) { lo = Math.min(lo, e); hi = Math.max(hi, e); }
      const span = Math.max(1, hi - lo), n = this.pe.length, step = Math.max(1, Math.floor(n / 400));
      ctx.beginPath(); ctx.moveTo(ix, iy + ih);
      for (let i = 0; i < n; i += step) ctx.lineTo(ix + iw * this.cum[i] / this.length, iy + ih - ih * (this.pe[i] - lo) / span);
      ctx.lineTo(ix + iw, iy + ih); ctx.closePath();
      ctx.fillStyle = "rgba(46,204,113,0.27)"; ctx.fill(); ctx.strokeStyle = "#2ECC71"; ctx.lineWidth = 1.6; ctx.stroke();
      ctx.fillStyle = "#F39C12";
      for (const s of this.stops) { ctx.beginPath(); ctx.arc(ix + iw * s.along / this.length, iy + ih + 7, 3, 0, Math.PI * 2); ctx.fill(); }
      const cx = ix + iw * this.dist / this.length, cy = iy + ih - ih * (ele - lo) / span;
      ctx.strokeStyle = "#F4D03F"; ctx.setLineDash([4, 3]); ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(cx, iy); ctx.lineTo(cx, iy + ih); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "#E74C3C"; ctx.beginPath(); ctx.arc(cx, cy, 5, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = "#98B2A1"; ctx.font = "10px sans-serif";
      ctx.fillText(fmt.ele(hi), ix, py + 9); ctx.fillText(fmt.ele(lo), ix, iy + ih + 14);
      // mini map
      if (showMini) {
        const size = 170, mx = w - size - 14, my = 14;
        ctx.fillStyle = "rgba(11,22,16,0.78)"; round(mx, my, size, size, 8); ctx.fill();
        ctx.strokeStyle = "rgba(236,245,238,0.45)"; ctx.lineWidth = 1; ctx.stroke();
        let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
        for (let i = 0; i < this.px.length; i++) { x0 = Math.min(x0, this.px[i]); x1 = Math.max(x1, this.px[i]); y0 = Math.min(y0, this.py[i]); y1 = Math.max(y1, this.py[i]); }
        const sp = Math.max(x1 - x0, y1 - y0, 1), sc = (size - 28) / sp;
        const ox = mx + size / 2 - (x0 + x1) / 2 * sc, oy = my + size / 2 + (y0 + y1) / 2 * sc;
        ctx.beginPath();
        const st2 = Math.max(1, Math.floor(this.px.length / 300));
        for (let i = 0; i < this.px.length; i += st2) { const X = ox + this.px[i] * sc, Y = oy - this.py[i] * sc; if (i) ctx.lineTo(X, Y); else ctx.moveTo(X, Y); }
        ctx.strokeStyle = "rgba(236,245,238,0.65)"; ctx.lineWidth = 2; ctx.stroke();
        const p = this.at(this.dist), hx = ox + p[0] * sc, hy = oy - p[1] * sc;
        ctx.fillStyle = "rgba(244,208,63,0.35)"; ctx.beginPath(); ctx.moveTo(hx, hy);
        for (const da of [-0.45, 0.45]) ctx.lineTo(hx + 26 * Math.cos(this.yaw + da), hy - 26 * Math.sin(this.yaw + da));
        ctx.closePath(); ctx.fill();
        ctx.fillStyle = "#E74C3C"; ctx.beginPath(); ctx.arc(hx, hy, 5, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.fillStyle = "#ECF5EE"; ctx.font = "bold 10px sans-serif"; ctx.fillText("N", mx + size - 18, my + 15);
      }
    }
    blend() {
      if (this.phase === "blend_in") return smoothstep(this.phaseT / BLEND_IN_S);
      if (this.phase === "blend_out") return 1 - smoothstep(this.phaseT / BLEND_OUT_S);
      return this.phase === "hold" ? 1 : 0;
    }
    _fit(img, w, h) {
      const ar = img && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 1.5;
      const fw = w / h < ar ? w : h * ar, fh = w / h < ar ? w / ar : h;
      return { x: (w - fw) / 2, y: (h - fh) / 2, w: fw, h: fh };
    }
    _paintPhoto(ctx, img, r, opacity) {
      ctx.globalAlpha = clamp(opacity, 0, 1);
      if (img) ctx.drawImage(img, r.x, r.y, r.w, r.h);
      else { ctx.fillStyle = "#0b1610"; ctx.fillRect(r.x, r.y, r.w, r.h); ctx.strokeStyle = "#F39C12"; ctx.lineWidth = 2; ctx.strokeRect(r.x, r.y, r.w, r.h); }
      ctx.globalAlpha = 1;
    }
    _overlay(ctx, w, h, project, stop) {
      const t = this.blend();
      if (t > 0.01) {
        // soft focus: the scene shrunk and stretched back over itself
        const sw = Math.max(8, Math.round(w / 14)), sh = Math.max(6, Math.round(h / 14));
        this.small.width = sw; this.small.height = sh;
        const sctx = this.small.getContext("2d");
        sctx.drawImage(this.canvas, 0, 0, sw, sh);
        ctx.globalAlpha = Math.min(1, t * 1.1);
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(this.small, 0, 0, w, h);
        ctx.globalAlpha = 1;
        ctx.fillStyle = `rgba(5,10,7,${(0.59 * t).toFixed(3)})`; ctx.fillRect(0, 0, w, h);
      }
      let k = Math.min(this.photoK, stop.photos.length - 1);
      if (this.phase === "blend_out") k = stop.photos.length - 1;
      const ph = stop.photos[k];
      const img = this.full.get(ph.i) || ph.thumbImg;
      const mr = this._markerRect(project, stop);
      const start = mr || { x: w / 2 - 23, y: h / 2 - 17, w: 46, h: 34 };
      const full = this._fit(img, w, h);
      const rect = { x: start.x + (full.x - start.x) * t, y: start.y + (full.y - start.y) * t, w: start.w + (full.w - start.w) * t, h: start.h + (full.h - start.h) * t };
      this._paintPhoto(ctx, img, rect, 0.45 + 0.55 * t);
      let capPh = ph, capA = 1;
      if (this.phase === "hold" && k + 1 < stop.photos.length) {
        const remaining = this.holdS(k) - this.phaseT;
        if (remaining < XFADE_S) {
          const f = smoothstep(1 - remaining / XFADE_S);
          const nx = stop.photos[k + 1];
          const nimg = this.full.get(nx.i) || nx.thumbImg;
          this._paintPhoto(ctx, nimg, this._fit(nimg, w, h), f);
          if (f > 0.5) { capPh = nx; capA = (f - 0.5) * 2; } else capA = 1 - f * 2;
        }
      }
      const cap = Math.max(0, (t - 0.7) / 0.3) * capA;
      if (cap > 0.01) {
        ctx.fillStyle = `rgba(0,0,0,${(0.6 * cap).toFixed(3)})`; ctx.fillRect(0, h - 86, w, 72);
        ctx.globalAlpha = cap;
        ctx.fillStyle = "rgb(244,208,63)"; ctx.font = "bold 16px sans-serif";
        let head = capPh.time_iso ? fmt.datetime(capPh.time_iso) : "";
        if (stop.photos.length > 1 || stop.hidden) head += "     " + (stop.photos.indexOf(capPh) + 1) + " / " + stop.photos.length + (stop.hidden ? "  (+" + stop.hidden + " more here)" : "");
        ctx.fillText(head, 24, h - 62);
        ctx.fillStyle = "rgb(236,245,238)"; ctx.font = "14px sans-serif";
        let caption = capPh.caption || capPh.ai || capPh.trail || "";
        if (capPh.people && capPh.people.length) caption += "   👥 " + capPh.people.join(", ");
        ctx.fillText(caption, 24, h - 34, w - 48);
        ctx.globalAlpha = 1;
      }
    }
  }

  // ── lightbox ──────────────────────────────────────────────────────────────
  let lightbox = null;
  function openPhoto(i, list) {
    const hadEntry = !!(lightbox && lightbox.pushed);
    list = list || H.photos;
    let pos = Math.max(0, list.findIndex(p => p.i === i));
    closePhoto(true);
    const img = el("img", { alt: "" });
    const side = el("div", { class: "side" });
    let mini = null;
    const show = () => {
      const p = list[pos];
      img.removeAttribute("src");
      loadImage(p.thumb).then(t => { if (t && list[pos] === p && !img.dataset.full) img.src = t.src; });
      img.dataset.full = "";
      loadImage(p.src).then(f => { if (f && list[pos] === p) { img.src = f.src; img.dataset.full = "1"; } });
      side.textContent = "";
      const title = p.caption || (p.trail ? "On the " + p.trail : "Photo " + (p.i + 1));
      side.append(el("div", { class: "when", text: p.time_iso ? fmt.datetime(p.time_iso) : "Time unknown" }), el("h3", { text: title }));
      if (p.ai && p.ai !== title) side.append(el("div", { class: "small", style: { color: "#c3d3c8" }, text: p.ai }));
      const kv = el("dl", { class: "kv" });
      const row = (k, v) => { if (v) kv.append(el("dt", { text: k }), el("dd", {}, v)); };
      row("Trail", p.trail || "");
      row("Along the hike", fmt.dist(p.along_m) + " of " + fmt.dist(G.total));
      row("Elevation", fmt.ele(p.ele_m));
      row("Location", el("a", { href: `https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=17/${p.lat}/${p.lon}`, target: "_blank", rel: "noopener", text: p.lat.toFixed(5) + ", " + p.lon.toFixed(5) }));
      if (p.off_m > 25) row("Off the trail", fmt.dist(p.off_m));
      row("People", p.people.join(", "));
      row("Camera", p.camera);
      row("Photo", (pos + 1) + " of " + list.length);
      side.append(kv);
      if (p.tags && p.tags.length) side.append(el("div", { class: "tags" }, p.tags.slice(0, 12).map(t => el("span", { class: "tag", text: t }))));
      const mapHost = el("div");
      side.append(mapHost);
      if (mini) mini.destroy();
      mini = new TopoMap(mapHost, { interactive: true, focusOnly: true, embedded: true, onPhoto: j => { pos = Math.max(0, list.findIndex(q => q.i === j)); show(); } });
      mini.focus = p.i;
      mini.view.s = 0.5;
      mini.box.style.height = "230px";
      side.append(el("div", { class: "btns" },
        el("a", { class: "btn ghost", href: "#map", onclick: e => { e.preventDefault(); pendingFocus = p.i; navigate("map"); }, text: "🗺 Show on map" }),
        el("a", { class: "btn ghost", href: "#play", onclick: e => { e.preventDefault(); pendingPlayAlong = p.along_m; navigate("play"); }, text: "▶ Play from here" })));
    };
    const go = d => { pos = (pos + d + list.length) % list.length; show(); };
    const box = el("div", { class: "lightbox", role: "dialog", "aria-label": "Photo" },
      el("div", { class: "pic" }, img,
        list.length > 1 ? el("button", { class: "nav prev", "aria-label": "Previous photo", text: "‹", onclick: () => go(-1) }) : null,
        list.length > 1 ? el("button", { class: "nav next", "aria-label": "Next photo", text: "›", onclick: () => go(1) }) : null),
      side,
      el("button", { class: "close", "aria-label": "Close", text: "✕", onclick: () => closePhoto() }));
    // Swipe sideways for the next / previous photo (up and down still scrolls to the details)
    let sx = null;
    img.addEventListener("pointerdown", e => { sx = e.clientX; });
    img.addEventListener("pointerup", e => { if (sx != null && Math.abs(e.clientX - sx) > 50) go(e.clientX < sx ? 1 : -1); sx = null; });
    img.addEventListener("pointercancel", () => { sx = null; });
    const onKey = e => {
      if (e.key === "Escape") closePhoto();
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
    };
    document.addEventListener("keydown", onKey);
    document.body.append(box);
    document.body.style.overflow = "hidden";
    // An open photo is a step in the browser history, so the phone's Back gesture closes it
    if (!hadEntry && history.pushState) history.pushState({ photo: true }, "");
    lightbox = { box, onKey, pushed: hadEntry || !!history.pushState, destroy: () => { if (mini) mini.destroy(); } };
    show();
  }
  function closePhoto(keepHistory) {
    if (!lightbox) return;
    const back = lightbox.pushed && !keepHistory;
    document.removeEventListener("keydown", lightbox.onKey);
    lightbox.destroy();
    lightbox.box.remove();
    document.body.style.overflow = "";
    lightbox = null;
    if (back) history.back();
  }
  window.addEventListener("popstate", () => closePhoto(true));

  // ── views ─────────────────────────────────────────────────────────────────
  let pendingFocus = null, pendingPlayAlong = null;
  let current = { destroy: () => {} };
  const tile = (p, list) => el("button", { class: "tile", title: p.caption || p.trail || "", onclick: () => openPhoto(p.i, list) },
    setImg(el("img", { alt: p.caption || "", loading: "lazy" }), p.thumb),
    p.starred ? el("span", { class: "star", text: "★" }) : null,
    el("span", { class: "cap" }, el("b", { text: fmt.time(p.time_iso) || " " }), p.trail || ""));

  function weatherIcon(text) {
    const t = (text || "").toLowerCase();
    if (/thunder/.test(t)) return "⛈";
    if (/snow/.test(t)) return "🌨";
    if (/rain|drizzle|shower/.test(t)) return "🌧";
    if (/fog/.test(t)) return "🌫";
    if (/overcast/.test(t)) return "☁️";
    if (/partly|mainly/.test(t)) return "⛅";
    if (/clear|sun/.test(t)) return "☀️";
    return "🌤";
  }
  function initials(name) { return name.split(/\s+/).filter(Boolean).slice(0, 2).map(s => s[0].toUpperCase()).join(""); }
  function hue(name) { let x = 0; for (const ch of name) x = (x * 31 + ch.charCodeAt(0)) % 360; return x; }

  function viewOverview(main) {
    const S = H.summary;
    const cover = coverPhoto();
    const shape = { loop: "Loop", out_and_back: "Out & back", one_way: "One way", segment: "One way" }[S.shape] || "";
    const hero = el("section", { class: "hero" });
    if (cover) hero.append(setImg(el("img", { class: "cover", alt: "" }), cover.src));
    const multiDay = S.days && S.days.length > 1;
    const span = !multiDay && S.start_iso && S.end_iso ? fmt.time(S.start_iso) + " – " + fmt.time(S.end_iso) : "";
    hero.append(el("div", { class: "inner" },
      el("div", { class: "eyebrow", text: [S.date_label ? S.date_label.split(" · ")[0] : "", span].filter(Boolean).join(" · ") }),
      el("h1", { text: S.title }),
      el("div", { class: "where", text: [S.area, (S.region || "").split(" (")[0]].filter(Boolean).join(" — ") }),
      el("div", { class: "chips" },
        el("span", { class: "chip", text: fmt.dist(S.distance_m) }),
        el("span", { class: "chip", text: "↗ " + fmt.ele(S.gain_m) }),
        S.days && S.days.length > 1 ? el("span", { class: "chip", text: "☾ " + S.days.length + " days" }) : null,
        S.elapsed_min ? el("span", { class: "chip", text: "⏱ " + fmt.dur(S.elapsed_min) }) : null,
        shape ? el("span", { class: "chip", text: shape }) : null,
        S.people.length ? el("span", { class: "chip", text: "👥 " + S.people.length }) : null),
      el("div", { class: "actions" },
        el("a", { class: "btn", href: "#play", text: "▶ Play the hike" }),
        el("a", { class: "btn light", href: "#map", text: "Explore the map" }),
        H.photos.length ? el("a", { class: "btn light", href: "#photos", text: H.photos.length + " photos" }) : null)));
    main.append(hero);

    const stat = (k, v, s) => el("div", { class: "stat" }, el("div", { class: "k", text: k }), el("div", { class: "v", text: v }), s ? el("div", { class: "s", text: s }) : null);
    main.append(el("section", { class: "stats" },
      stat("Distance", fmt.dist(S.distance_m), shape),
      stat("Elevation gain", "↗ " + fmt.ele(S.gain_m), "↘ " + fmt.ele(S.loss_m) + " descent"),
      stat("Highest point", fmt.ele(S.max_ele_m), S.high_place || ""),
      stat("Lowest point", fmt.ele(S.min_ele_m)),
      S.elapsed_min ? stat("Time on the trail", fmt.dur(S.elapsed_min),
        S.days && S.days.length > 1 ? "over " + S.days.length + " days, " + (S.days.length - 1) + " night" + (S.days.length > 2 ? "s" : "") + " out" : span) : null,
      S.estimated_min ? stat("Typical hiking time", fmt.dur(S.estimated_min), "Pace model estimate") : null,
      stat("Steepest grade", fmt.grade(S.max_grade_pct), "Average " + fmt.grade(S.avg_grade_pct)),
      stat("Photos", String(H.photos.length), S.first_photo_iso && !multiDay ? fmt.time(S.first_photo_iso) + " – " + fmt.time(S.last_photo_iso) : "")));

    const left = el("div", { class: "stack" }), right = el("div", { class: "stack" });
    main.append(el("section", { class: "grid cols-2" }, left, right));

    // the route: map + profile
    const routeCard = el("div", { class: "card" }, el("h2", { text: "The route" }));
    left.append(routeCard);
    const mapHost = el("div");
    routeCard.append(mapHost);
    const map = new TopoMap(mapHost, { interactive: true, embedded: true, onPhoto: i => openPhoto(i), onHover: a => prof.setCursor(a) });
    map.box.style.height = device.phone ? "280px" : "380px";
    const prof = new Profile(routeCard, { height: device.phone ? 120 : 150, onHover: a => map.setCursor(a), onPhoto: i => openPhoto(i) });
    const where = [S.start_place && (S.start_place === S.end_place || !S.end_place ? "Start & finish: " + S.start_place : "Start: " + S.start_place + " · Finish: " + (S.end_place || "—"))];
    if (where[0]) routeCard.append(el("p", { class: "muted small", style: { margin: "10px 0 0" }, text: where[0] }));

    // day by day (multi-day hikes)
    if (S.days && S.days.length > 1) {
      const list = el("ol", { class: "legs" });
      S.days.forEach((d, i) => {
        const when = new Date(d.day + "T12:00");
        const times = d.start_iso && d.end_iso ? fmt.time(d.start_iso) + " – " + fmt.time(d.end_iso) : "";
        list.append(el("li", { class: "leg" },
          el("span", { class: "dot", style: { background: legColor(i) } }),
          el("div", { class: "name" }, "Day " + (i + 1) + " · " + when.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })),
          el("div", { class: "meta", text: [fmt.dist(d.distance_m), "↗ " + fmt.ele(d.gain_m) + "  ↘ " + fmt.ele(d.loss_m), times,
            d.hiking_min ? fmt.dur(d.hiking_min) + " on the trail" : ""].filter(Boolean).join(" · ") }),
          el("div", { class: "desc", text: [d.from_place, i < S.days.length - 1 ? (d.overnight ? "☾ night at " + d.overnight : "☾ night out") : d.to_place]
            .filter(Boolean).join(" → ") })));
      });
      left.append(el("div", { class: "card" }, el("h2", { text: S.days.length + " days on the trail" }), list));
    }

    // trails taken
    if (S.trails.length) {
      const list = el("ol", { class: "legs" });
      S.trails.forEach((t, i) => {
        list.append(el("li", { class: "leg" },
          el("span", { class: "dot", style: { background: t.named ? legColor(i) : "#a7b0aa" } }),
          el("div", { class: "name" }, t.name, t.difficulty ? el("span", { class: "badge d-" + t.difficulty, text: t.difficulty }) : null),
          el("div", { class: "meta", text: [fmt.dist(t.distance_m), "↗ " + fmt.ele(t.gain_m) + "  ↘ " + fmt.ele(t.loss_m),
            t.from_place && t.to_place && t.from_place !== t.to_place ? t.from_place + " → " + t.to_place : (t.to_place || t.from_place || "")].filter(Boolean).join(" · ") }),
          t.description ? el("div", { class: "desc", text: t.description.replace(/^.*?: [\d.]+ mi one way, .*?\)\.\s*/, "").replace(/SAC T\d[^.]*\.\s*/g, "") }) : null));
      });
      left.append(el("div", { class: "card" }, el("h2", { text: "Trails taken" }), list));
    }

    // weather
    const W = S.weather || {};
    if (W.summary || W.temp_high_c != null) {
      const kv = el("dl", { class: "kv" });
      const row = (k, v) => { if (v) kv.append(el("dt", { text: k }), el("dd", { text: v })); };
      row("Temperature", W.temp_low_c != null && W.temp_high_c != null ? fmt.temp(W.temp_low_c) + " – " + fmt.temp(W.temp_high_c) : fmt.temp(W.temp_high_c));
      row("Precipitation", W.precip_mm != null ? fmt.precip(W.precip_mm) : "");
      row("Wind", W.wind_kmh != null ? "up to " + fmt.wind(W.wind_kmh) : "");
      right.append(el("div", { class: "card" }, el("h2", { text: "Weather" }),
        el("div", { class: "weather" }, el("div", { class: "icon", text: weatherIcon(W.summary) }),
          el("div", {}, el("div", { class: "main", text: W.summary || "" }),
            W.temp_high_c != null ? el("div", { class: "muted", text: fmt.temp(W.temp_high_c) + " high" }) : null)),
        kv, W.source ? el("div", { class: "muted small", style: { marginTop: "8px" }, text: "Source: " + W.source }) : null));
    }

    // people
    if (S.people.length) {
      const counts = {};
      H.photos.forEach(p => p.people.forEach(n => { counts[n] = (counts[n] || 0) + 1; }));
      right.append(el("div", { class: "card" }, el("h2", { text: "Hiking party" }),
        el("div", { class: "people" }, S.people.map(n => el("span", { class: "person" },
          el("span", { class: "av", style: { background: `hsl(${hue(n)} 45% 42%)` }, text: initials(n) }), n,
          counts[n] ? el("span", { class: "n", text: counts[n] + " photo" + (counts[n] > 1 ? "s" : "") }) : null)))));
    }

    // area & notes
    right.append(el("div", { class: "card" }, el("h2", { text: "The area" }),
      el("div", { style: { fontWeight: 650 }, text: S.area }),
      S.region ? el("div", { class: "muted small", text: S.region }) : null,
      S.area_summary ? el("p", { class: "small", style: { marginBottom: 0 }, text: S.area_summary }) : null));
    if (S.notes) right.append(el("div", { class: "card" }, el("h2", { text: "Notes" }), el("p", { style: { margin: 0, whiteSpace: "pre-line" }, text: S.notes })));

    if (H.photos.length) {
      const strip = el("div", { class: "strip" }, H.photos.slice(0, 12).map(p => tile(p)));
      right.append(el("div", { class: "card" }, el("h2", { text: "Photos" }), strip,
        el("p", { style: { margin: "12px 0 0" } }, el("a", { href: "#photos", text: "See all " + H.photos.length + " photos →" }))));
    }
    return { destroy: () => { map.destroy(); prof.destroy(); } };
  }

  function viewMap(main) {
    const S = H.summary;
    const wrap = el("section", { class: "mapview stack" });
    main.append(wrap);
    const card = el("div", { class: "card" });
    wrap.append(card);
    const mapHost = el("div");
    card.append(mapHost);
    const readout = el("div", { class: "readout" });
    const setReadout = a => {
      readout.textContent = "";
      if (a == null) {
        readout.append(el("span", {}, el("b", { text: fmt.dist(S.distance_m) }), " total"),
          el("span", {}, el("b", { text: "↗ " + fmt.ele(S.gain_m) }), " climb"),
          el("span", {}, el("b", { text: fmt.ele(S.max_ele_m) }), " high point"),
          el("span", { text: device.touch ? "Touch the route or the profile; tap a photo to open it." : "Hover the route or the profile; click a photo to open it." }));
        return;
      }
      const pt = pointAt(a), leg = legAt(a);
      readout.append(el("span", {}, el("b", { text: fmt.dist(a) }), " along"),
        el("span", {}, el("b", { text: fmt.ele(pt.e) }), " elevation"),
        el("span", {}, el("b", { text: (pt.grade >= 0 ? "+" : "") + pt.grade.toFixed(0) + "%" }), " grade"),
        leg && leg.named ? el("span", {}, "on ", el("b", { text: leg.name })) : null);
    };
    let prof = null;
    const map = new TopoMap(mapHost, { interactive: true, onPhoto: i => openPhoto(i), onHover: a => { prof && prof.setCursor(a); setReadout(a); } });
    prof = new Profile(card, { height: device.phone ? 110 : 180, onHover: a => { map.setCursor(a); setReadout(a); }, onPhoto: i => openPhoto(i) });
    card.append(readout);
    setReadout(null);
    if (pendingFocus != null) { map.focus = pendingFocus; pendingFocus = null; }
    return { destroy: () => { map.destroy(); prof.destroy(); } };
  }

  function viewPlay(main) {
    const S = H.summary;
    main.append(el("p", { class: "muted intro", style: { margin: "0 0 12px" },
      text: "Follow " + S.title + " in 3D over the real terrain. At each photo the view turns toward where it was taken and the photo grows to fill the screen." }));
    const r = new Replay3D(main);
    // A phone held sideways has room for the replay alone: bring it fully into view
    if (device.phone && window.innerWidth > window.innerHeight) setTimeout(() => r.root.scrollIntoView({ block: "start" }), 50);
    if (pendingPlayAlong != null) { r.seekAlong(Math.max(0, pendingPlayAlong - 80)); pendingPlayAlong = null; }
    setTimeout(() => { if (r.root.isConnected) r.play(); }, 500);
    return { destroy: () => r.destroy() };
  }

  function viewPhotos(main, focusId) {
    if (!H.photos.length) { main.append(el("div", { class: "empty", text: "No photos for this hike." })); return { destroy() {} }; }
    const trails = [...new Set(H.photos.map(p => p.trail).filter(Boolean))];
    const people = [...new Set(H.photos.flatMap(p => p.people))];
    const filters = el("div", { class: "filters" });
    const grid = el("div", { class: "gallery" });
    let filter = null;
    const render = () => {
      grid.textContent = "";
      const list = H.photos.filter(p => !filter || (filter.t === "trail" ? p.trail === filter.v : p.people.includes(filter.v)));
      list.forEach(p => grid.append(tile(p, list)));
      [...filters.children].forEach(b => b.classList.toggle("on", b._f === filter || (b._f && filter && b._f.v === filter.v)));
    };
    const fbtn = (label, f) => { const b = el("button", { text: label, onclick: () => { filter = f; render(); } }); b._f = f; return b; };
    filters.append(fbtn("All " + H.photos.length, null));
    if (trails.length > 1) trails.forEach(t => filters.append(fbtn(t, { t: "trail", v: t })));
    people.forEach(n => filters.append(fbtn("👤 " + n, { t: "person", v: n })));
    main.append(filters, grid);
    render();
    if (focusId) { const p = H.photos.find(q => q.id === focusId); if (p) openPhoto(p.i); }
    return { destroy() {} };
  }

  // ── shell & routing ───────────────────────────────────────────────────────
  let mainEl, tabs, barTabs;
  const ICONS = { // bottom tab bar (phones)
    overview: '<path d="M3 20l6.5-11 4 6.5 2.5-4L21 20z"/>',
    map: '<path d="M9 4l6 2 5-2v15l-5 2-6-2-5 2V6zM9 4v15M15 6v15"/>',
    play: '<circle cx="12" cy="12" r="9"/><path d="M10 8.5l5.5 3.5-5.5 3.5z"/>',
    photos: '<rect x="3" y="6" width="18" height="13" rx="2"/><circle cx="12" cy="12.5" r="3.5"/><path d="M8.5 6l1.5-2h4l1.5 2"/>',
  };
  function shell() {
    const app = document.getElementById("app");
    app.textContent = "";
    const S = H.summary;
    document.title = S.title + (S.date_label ? " · " + S.date_label.split(" · ")[0] : "");
    tabs = {
      overview: el("a", { href: "#overview", text: "Overview" }),
      map: el("a", { href: "#map", text: "Map" }),
      play: el("a", { href: "#play", text: "Play the Hike" }),
      photos: el("a", { href: "#photos" }, "Photos ", el("span", { class: "count", text: String(H.photos.length) })),
    };
    const unitBtns = [["us", "mi · ft"], ["metric", "km · m"]].map(u => {
      const b = el("button", { text: u[1], class: units === u[0] ? "on" : "", onclick: () => {
        units = u[0]; store.set("hike_units", units);
        unitBtns.forEach(x => x.classList.toggle("on", x === b));
        route();
      } });
      return b;
    });
    barTabs = {};
    for (const [k, label] of [["overview", "Overview"], ["map", "Map"], ["play", "Play"], ["photos", "Photos"]]) {
      const icon = el("span", { class: "ic", "aria-hidden": "true" });
      icon.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">' + ICONS[k] + "</svg>";
      barTabs[k] = el("a", { href: "#" + k }, icon, el("span", { text: label }));
    }
    mainEl = el("main");
    app.append(
      el("header", { class: "topbar" },
        el("a", { class: "brand", href: "#overview", style: { textDecoration: "none", color: "inherit" } },
          el("span", { class: "logo", text: "⛰" }), el("span", { class: "t", text: S.title })),
        el("nav", { class: "tabs" }, Object.values(tabs)),
        el("div", { class: "units" }, unitBtns)),
      mainEl,
      el("footer", {}, "Made with AegisHiker · Map data © OpenStreetMap contributors (ODbL) · Terrain: USGS 3DEP",
        el("br"), H.meta.generated ? "Published " + new Date(H.meta.generated).toLocaleDateString() : ""),
      el("nav", { class: "tabbar", "aria-label": "Sections" }, Object.values(barTabs)));
    window.addEventListener("hashchange", route);
    // Turning a tablet or resizing a window across the phone / desktop line redraws the page for it
    let resizing = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizing);
      resizing = setTimeout(() => { if (detectDevice() && !lightbox) route(); }, 200);
    });
    route();
  }
  function navigate(name) {
    if (lightbox && lightbox.pushed) { // the open photo's history step becomes the new view
      closePhoto(true);
      history.replaceState(null, "", "#" + name);
      route();
      return;
    }
    if (location.hash === "#" + name) route(); else location.hash = name;
  }
  function route() {
    const hash = (location.hash || "#overview").slice(1);
    const [name, arg] = hash.split("/");
    const view = ({ overview: viewOverview, map: viewMap, play: viewPlay, photos: viewPhotos })[name] || viewOverview;
    const key = ({ overview: 1, map: 1, play: 1, photos: 1 })[name] ? name : "overview";
    current.destroy();
    closePhoto(true);
    mainEl.textContent = "";
    for (const set of [tabs, barTabs]) Object.entries(set).forEach(([k, a]) => a.classList.toggle("on", k === key));
    document.documentElement.dataset.view = key;
    current = view(mainEl, arg) || { destroy() {} };
    if (key !== "overview") window.scrollTo(0, 0);
  }

  // ── start / unlock ────────────────────────────────────────────────────────
  function start(data) {
    prepare(data);
    units = store.get("hike_units") || H.meta.units || "us";
    shell();
  }
  function showLock(locked) {
    const app = document.getElementById("app");
    app.textContent = "";
    const input = el("input", { type: "password", placeholder: "Password", autocomplete: "current-password", autofocus: true, "aria-label": "Password" });
    const err = el("div", { class: "err" });
    const btn = el("button", { class: "btn", type: "submit", text: "Unlock" });
    const form = el("form", {},
      el("div", { class: "logo", text: "⛰" }),
      el("h1", { text: "A private hike" }),
      el("p", { text: "Enter the password you were given to see the hike, its photos and the replay." }),
      input, btn, err);
    app.append(el("div", { class: "lock" }, form));
    if (!(window.crypto && crypto.subtle)) {
      input.disabled = true; btn.disabled = true;
      err.textContent = "This protected page has to be opened over a secure connection (https://).";
      return;
    }
    const tryUnlock = async pw => {
      btn.disabled = true; err.textContent = "Unlocking…";
      try {
        const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
        KEY = await crypto.subtle.deriveKey({ name: "PBKDF2", salt: b64(locked.salt), iterations: locked.iter, hash: "SHA-256" },
          base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
        const json = new TextDecoder().decode(await decrypt(locked));
        try { window.sessionStorage.setItem("hike_pw", pw); } catch (e) { /* ignore */ }
        start(JSON.parse(json));
      } catch (e) {
        KEY = null;
        btn.disabled = false;
        err.textContent = "That password didn't work. Try again.";
        input.select();
      }
    };
    form.addEventListener("submit", e => { e.preventDefault(); if (input.value) tryUnlock(input.value); });
    let saved = null;
    try { saved = window.sessionStorage.getItem("hike_pw"); } catch (e) { /* ignore */ }
    if (saved) tryUnlock(saved);
  }

  if (window.HIKE_DATA) start(window.HIKE_DATA);
  else if (window.HIKE_LOCKED) showLock(window.HIKE_LOCKED);
  else document.getElementById("app").append(el("p", { class: "empty", text: "The hike data (hike.js) is missing from this folder." }));
})();
