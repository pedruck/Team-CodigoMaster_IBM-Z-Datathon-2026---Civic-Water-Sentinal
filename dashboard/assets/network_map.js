// Interactive L-Town network map. Reads the JSON built by dashboard.network_map.map_payload
// and draws it as live SVG. Every value from the payload enters the page as text (textContent,
// attributes), never as HTML. Rendering only: nothing here decides or infers an alert.
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('map-data').textContent);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const $ = (id) => document.getElementById(id);
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const [bx0, by0, bx1, by1] = data.bbox;
  const worldW = bx1 - bx0;
  const worldH = by1 - by0;
  const wx = (x) => x - bx0;
  const wy = (y) => by1 - y; // network y points up, screen y points down

  const links = data.links.map(([id, type, x1, y1, x2, y2, diameter, region, sensor], i) => ({
    i, id, type, diameter, region, sensor,
    x1: wx(x1), y1: wy(y1), x2: wx(x2), y2: wy(y2),
  }));
  const linkById = new Map(links.map((l) => [l.id, l]));
  const sensors = data.sensors.map(([id, x, y, region]) => ({ id, x: wx(x), y: wy(y), region }));
  const sensorById = new Map(sensors.map((s) => [s.id, s]));
  const landmarks = data.landmarks.map(([id, kind, x, y]) => ({ id, kind, x: wx(x), y: wy(y) }));
  const regionInfo = new Map(data.regions.map((r) => [r.id, r]));
  const alerts = data.alerts;
  const alertById = new Map(alerts.map((a) => [a.id, a]));
  const selected = data.selected ? alertById.get(data.selected) || null : null;

  const stage = $('stage');
  const svg = $('svg');
  const camera = $('camera');
  const labelsLayer = $('labels');

  const state = {
    k: 1, tx: 0, ty: 0, W: 0, H: 0,
    mode: 'alert',
    isolate: null,
    shown: selected,
    replay: { active: false, playing: false, step: 0, lit: -1, timer: 0, hold: 0 },
    moved: false,
    lastTarget: null,
    flight: 0,
    tags: [],
    hover: null,
    opener: null,
  };

  // ---------------------------------------------------------------- formatting
  const fmtM = (v) => `${v < 0 ? '−' : '+'}${Math.abs(v).toFixed(2)} m`;
  const fmtNum = (v) => (Number.isInteger(v) ? v.toFixed(1) : String(Math.round(v * 1000) / 1000));
  const el = (tag, attrs = {}, text) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svgEl = (tag, attrs = {}) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const facts = (dl, rows) => {
    dl.replaceChildren();
    for (const [term, value] of rows) {
      dl.append(el('dt', {}, term), el('dd', {}, value));
    }
  };

  // ---------------------------------------------------------------- static drawing
  const pipeEls = [];
  const pipesG = $('pipes');
  const hitsG = $('hits');
  for (const l of links) {
    const width = l.diameter == null ? 1.1 : Math.min(0.7 + l.diameter / 260, 2.6);
    const line = svgEl('line', { x1: l.x1, y1: l.y1, x2: l.x2, y2: l.y2, class: `pipe ${l.type}` });
    line.style.strokeWidth = `${width}px`;
    l.width = width;
    pipesG.append(line);
    pipeEls.push(line);
    hitsG.append(svgEl('line', { x1: l.x1, y1: l.y1, x2: l.x2, y2: l.y2, class: 'hit', 'data-link': l.i }));
  }
  const landmarkEls = landmarks.map((m) => {
    const rect = svgEl('rect', { class: 'landmark', 'data-landmark': m.id });
    $('landmarks').append(rect);
    return rect;
  });
  const sensorsG = $('sensors');
  const ringsG = svgEl('g');
  sensorsG.append(ringsG);
  const sensorEls = new Map();
  for (const s of sensors) {
    const c = svgEl('circle', { cx: s.x, cy: s.y, class: 'sensor', 'data-sensor': s.id });
    sensorsG.append(c);
    sensorEls.set(s.id, c);
  }

  // ---------------------------------------------------------------- camera
  const kFit = () => Math.min(state.W / worldW, state.H / worldH) * 0.94;
  const clampK = (k) => Math.max(kFit() * 0.6, Math.min(kFit() * 16, k));

  function applyCamera() {
    camera.setAttribute('transform', `matrix(${state.k} 0 0 ${state.k} ${state.tx} ${state.ty})`);
    const px = 1 / state.k;
    for (const s of sensors) {
      const deviating = sensorEls.get(s.id).classList.contains('deviating');
      sensorEls.get(s.id).setAttribute('r', (deviating ? 7 : 4.5) * px);
    }
    for (const ring of ringsG.children) ring.setAttribute('r', 7 * px);
    landmarks.forEach((m, i) => {
      const half = 5.5 * px;
      landmarkEls[i].setAttribute('x', m.x - half);
      landmarkEls[i].setAttribute('y', m.y - half);
      landmarkEls[i].setAttribute('width', 2 * half);
      landmarkEls[i].setAttribute('height', 2 * half);
    });
    layoutTags();
  }

  // Labels are placed in priority order (the leak callout first) and each tries right, left,
  // above and below its anchor until it covers neither an earlier label nor the candidate pipe.
  // A landmark or region name that finds no free place is hidden rather than piled on top.
  const OFFSETS = {
    right: (w, h) => [10, -h / 2],
    left: (w, h) => [-w - 10, -h / 2],
    above: (w, h) => [-w / 2, -h - 10],
    below: (w, h) => [-w / 2, 10],
  };
  const overlaps = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
  function layoutTags() {
    const taken = [];
    if (state.candidateRect) taken.push(state.candidateRect());
    for (const tag of state.tags) {
      const sx = tag.x * state.k + state.tx;
      const sy = tag.y * state.k + state.ty;
      const onStage = sx > -40 && sy > -20 && sx < state.W + 40 && sy < state.H + 20;
      tag.node.hidden = !onStage;
      if (!onStage) continue;
      if (!tag.w) { tag.w = tag.node.offsetWidth; tag.h = tag.node.offsetHeight; }
      let chosen = null;
      for (const name of tag.places) {
        const [dx, dy] = OFFSETS[name](tag.w, tag.h);
        const rect = { x0: sx + dx - 2, y0: sy + dy - 2, x1: sx + dx + tag.w + 2, y1: sy + dy + tag.h + 2 };
        if (!taken.some((r) => overlaps(r, rect))) { chosen = { dx, dy, rect }; break; }
      }
      if (!chosen && !tag.must) { tag.node.hidden = true; continue; }
      if (!chosen) {
        const [dx, dy] = OFFSETS[tag.places[0]](tag.w, tag.h);
        chosen = { dx, dy, rect: { x0: sx + dx, y0: sy + dy, x1: sx + dx + tag.w, y1: sy + dy + tag.h } };
      }
      taken.push(chosen.rect);
      tag.node.style.transform = 'none';
      tag.node.style.left = `${sx + chosen.dx}px`;
      tag.node.style.top = `${sy + chosen.dy}px`;
    }
  }

  // The part of the stage not covered by the replay card or the legend: a focused alert is
  // framed inside it, so no overlay hides what the camera came to show.
  function freeArea() {
    // Top: room for the leak callout above its pipe. Right: the zoom stack. Bottom: the legend.
    const area = { x0: 0, y0: 44, x1: state.W - 56, y1: state.H };
    const legend = $('legend');
    if (legend && legend.childElementCount) area.y1 -= legend.offsetHeight + 14;
    const cardEl = $('card');
    if (cardEl && !cardEl.hidden && state.W > 560) area.x0 += cardEl.offsetWidth + 16;
    else if (cardEl && !cardEl.hidden) area.y1 = Math.min(area.y1, cardEl.offsetTop - 8);
    if (area.x1 - area.x0 < 160 || area.y1 - area.y0 < 120) return { x0: 0, y0: 0, x1: state.W, y1: state.H };
    return area;
  }

  function cameraFor(bounds) {
    if (!bounds) {
      const k = kFit();
      return { k, tx: (state.W - worldW * k) / 2, ty: (state.H - worldH * k) / 2 };
    }
    const area = freeArea();
    const aw = area.x1 - area.x0;
    const ah = area.y1 - area.y0;
    const minSpan = 260; // metres: never zoom onto a single pipe so far that context vanishes
    const w = Math.max(bounds.x1 - bounds.x0, minSpan);
    const h = Math.max(bounds.y1 - bounds.y0, minSpan * 0.6);
    const k = clampK(Math.min(aw / (w * 1.35), ah / (h * 1.35)));
    const cx = (bounds.x0 + bounds.x1) / 2;
    const cy = (bounds.y0 + bounds.y1) / 2;
    return { k, tx: (area.x0 + area.x1) / 2 - cx * k, ty: (area.y0 + area.y1) / 2 - cy * k };
  }

  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

  function flyTo(target, duration) {
    cancelAnimationFrame(state.flight);
    if (reduceMotion.matches || duration <= 0) {
      Object.assign(state, target);
      applyCamera();
      return;
    }
    // Interpolate the centre in world space and the scale in log space: a camera move,
    // not a zoom towards a corner.
    const from = { k: state.k, cx: (state.W / 2 - state.tx) / state.k, cy: (state.H / 2 - state.ty) / state.k };
    const to = { k: target.k, cx: (state.W / 2 - target.tx) / target.k, cy: (state.H / 2 - target.ty) / target.k };
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const e = easeInOut(t);
      const k = Math.exp(Math.log(from.k) + (Math.log(to.k) - Math.log(from.k)) * e);
      const cx = from.cx + (to.cx - from.cx) * e;
      const cy = from.cy + (to.cy - from.cy) * e;
      state.k = k;
      state.tx = state.W / 2 - cx * k;
      state.ty = state.H / 2 - cy * k;
      applyCamera();
      if (t < 1) state.flight = requestAnimationFrame(step);
    };
    state.flight = requestAnimationFrame(step);
  }

  function zoomAt(factor, sx, sy) {
    cancelAnimationFrame(state.flight);
    const k = clampK(state.k * factor);
    const f = k / state.k;
    state.tx = sx - (sx - state.tx) * f;
    state.ty = sy - (sy - state.ty) * f;
    state.k = k;
    state.moved = true;
    applyCamera();
  }
  function panBy(dx, dy) {
    cancelAnimationFrame(state.flight);
    state.tx += dx;
    state.ty += dy;
    state.moved = true;
    applyCamera();
  }

  const growBounds = (b, x, y) => {
    b.x0 = Math.min(b.x0, x); b.y0 = Math.min(b.y0, y);
    b.x1 = Math.max(b.x1, x); b.y1 = Math.max(b.y1, y);
  };
  function alertBounds(alert) {
    if (!alert) return null;
    const b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    // With a candidate pipe the camera frames it and the deviating sensors (closer, so the pipe
    // reads as a pipe); without one, the whole region.
    if (alert.region_known && !alert.candidate_known) {
      for (const l of links) if (l.region === alert.region) { growBounds(b, l.x1, l.y1); growBounds(b, l.x2, l.y2); }
    }
    const pipe = alert.candidate_known ? linkById.get(alert.candidate_pipe) : null;
    if (pipe) { growBounds(b, pipe.x1, pipe.y1); growBounds(b, pipe.x2, pipe.y2); }
    for (const d of alert.sensors) {
      const s = sensorById.get(d.id);
      if (s) growBounds(b, s.x, s.y);
    }
    return Number.isFinite(b.x0) ? b : null;
  }

  // ---------------------------------------------------------------- alert & mode drawing
  // Where the region's name sits: on the region's pipe nearest the top of the alert's frame,
  // so it stays on screen when the camera is close and clear of the alert's own labels.
  function regionAnchor(regionId, alert) {
    const frame = alertBounds(alert);
    const tx = frame ? (frame.x0 + frame.x1) / 2 : worldW / 2;
    const ty = frame ? frame.y0 : 0;
    let best = null;
    for (const l of links) {
      if (l.region !== regionId) continue;
      const x = (l.x1 + l.x2) / 2; const y = (l.y1 + l.y2) / 2;
      const d = (x - tx) ** 2 + (y - ty) ** 2;
      if (!best || d < best.d) best = { x, y, d };
    }
    return best;
  }

  function addTag(text, x, y, cls, { small, places = ['right', 'left', 'above', 'below'], must = false } = {}) {
    const node = el('span', { class: `tag ${cls}` }, text);
    if (small) node.append(el('small', {}, small));
    labelsLayer.append(node);
    state.tags.push({ node, x, y, places, must });
  }

  function draw() {
    const alert = state.shown;
    const alertMode = state.mode === 'alert';
    const region = alert && alert.region_known ? alert.region : null;
    const isolate = state.isolate;

    links.forEach((l, i) => {
      const line = pipeEls[i];
      line.classList.remove('faded', 'in-region');
      line.style.stroke = '';
      if (alertMode) {
        if (region) line.classList.add(l.region === region ? 'in-region' : 'faded');
        line.style.strokeWidth = `${l.width + (region && l.region === region ? 0.9 : 0)}px`;
      } else {
        line.style.strokeWidth = `${l.width + 0.4}px`;
        const info = l.region ? regionInfo.get(l.region) : null;
        if (isolate && l.region !== isolate) line.classList.add('faded');
        else if (info) line.style.stroke = info.color;
      }
    });

    // Candidate pipe: always a best guess, and only when the ID exists in the map.
    const candLayer = $('candidate-layer');
    candLayer.replaceChildren();
    labelsLayer.replaceChildren();
    state.tags = [];
    state.candidateRect = null;
    ringsG.replaceChildren();
    const pipe = alertMode && alert && alert.candidate_known ? linkById.get(alert.candidate_pipe) : null;
    if (pipe) {
      const pos = { x1: pipe.x1, y1: pipe.y1, x2: pipe.x2, y2: pipe.y2 };
      candLayer.append(svgEl('line', { ...pos, class: 'candidate-glow' }));
      const solid = svgEl('line', {
        ...pos, class: 'candidate', tabindex: '0', role: 'button', 'data-link': pipe.i,
        'aria-label': `Candidate pipe ${pipe.id}: suspected leak, best guess. Press Enter for details.`,
      });
      candLayer.append(solid);
      addTag('Suspected leak (best guess)', (pipe.x1 + pipe.x2) / 2, (pipe.y1 + pipe.y2) / 2, 'leak',
        { small: pipe.id, places: ['above', 'below', 'right', 'left'], must: true });
      // The pipe itself stays uncovered: labels avoid its on-screen box.
      state.candidateRect = () => {
        const xs = [pipe.x1, pipe.x2].map((v) => v * state.k + state.tx);
        const ys = [pipe.y1, pipe.y2].map((v) => v * state.k + state.ty);
        return { x0: Math.min(...xs) - 8, y0: Math.min(...ys) - 8, x1: Math.max(...xs) + 8, y1: Math.max(...ys) + 8 };
      };
    }

    const deviating = new Map();
    if (alertMode && alert) for (const d of alert.sensors) if (d.known) deviating.set(d.id, d);
    const maxDev = Math.max(0, ...[...deviating.values()].map((d) => Math.abs(d.deviation_m))) || 1;
    for (const s of sensors) {
      const c = sensorEls.get(s.id);
      const d = deviating.get(s.id);
      c.classList.toggle('deviating', Boolean(d));
      if (d) {
        c.setAttribute('tabindex', '0');
        c.setAttribute('role', 'button');
        c.setAttribute('aria-label', `Sensor ${s.id}, ${fmtM(d.deviation_m)} against expected pressure. Press Enter for details.`);
        const ring = svgEl('circle', { cx: s.x, cy: s.y, class: 'ring' });
        ring.style.setProperty('--amp', (Math.abs(d.deviation_m) / maxDev).toFixed(3));
        ringsG.append(ring);
        addTag(`${s.id} ${fmtM(d.deviation_m)}`, s.x, s.y, 'alert', { must: true });
      } else {
        c.removeAttribute('tabindex');
        c.removeAttribute('role');
        c.removeAttribute('aria-label');
      }
    }
    if (alertMode && region) {
      const anchor = regionAnchor(region, alert);
      if (anchor) addTag(region, anchor.x, anchor.y, 'region', { places: ['above', 'right', 'left', 'below'] });
    }
    for (const m of landmarks) addTag(m.id, m.x, m.y, 'landmark-tag');

    // Warnings: unknown IDs are said, never drawn somewhere plausible.
    const warnings = $('warnings');
    warnings.replaceChildren(...(alert ? alert.warnings : []).map((w) => el('li', {}, w)));
    warnings.hidden = !(alert && alert.warnings.length);
    $('mock-badge').hidden = !(alert && alert.is_mock);

    $('focus').disabled = !alertBounds(alert);
    drawLegend();
    summarise();
    applyCamera();
  }

  function drawLegend() {
    const legend = $('legend');
    legend.replaceChildren();
    if (state.mode === 'regions') {
      legend.setAttribute('aria-label', 'Regions. Select one to isolate it.');
      for (const r of data.regions) {
        const b = el('button', { type: 'button', 'aria-pressed': String(state.isolate === r.id) });
        const sw = el('span', { class: 'swatch' });
        sw.style.background = r.color;
        b.append(sw, document.createTextNode(r.id));
        b.title = `${r.id}: ${r.links} pipes, sensors ${r.sensors.join(', ')}`;
        b.addEventListener('click', () => {
          state.isolate = state.isolate === r.id ? null : r.id;
          draw();
          legend.querySelector(`button[aria-pressed="true"]`)?.focus();
          if (state.isolate === null) b.focus();
        });
        legend.append(b);
      }
      return;
    }
    legend.setAttribute('aria-label', 'Legend');
    const key = (cls, text, color) => {
      const span = el('span', { class: 'key' });
      const mark = el('span', { class: cls });
      if (color) mark.style.background = color;
      span.append(mark, document.createTextNode(text));
      legend.append(span);
    };
    if (state.shown && state.shown.region_known) key('swatch', 'Alert region', 'var(--accent)');
    if (state.shown && state.shown.sensors.some((d) => d.known)) key('dot', 'Deviating sensor');
    if (state.shown && state.shown.candidate_known) key('swatch', 'Candidate pipe (best guess)', 'var(--alert)');
    key('sq', 'Tank / reservoir');
  }

  function summarise() {
    const a = state.shown;
    const text = !a
      ? `Whole network: ${data.counts.pipes ?? links.length} pipes and ${sensors.length} pressure sensors. No alert selected.`
      : `Alert ${a.id} at ${a.time_label}, region ${a.region ?? 'not available'}, `
        + `${a.sensors.length} deviating sensor${a.sensors.length === 1 ? '' : 's'}, `
        + `candidate pipe ${a.candidate_pipe ?? 'not provided'} (best guess).`;
    $('map-summary').textContent = text;
  }

  // ---------------------------------------------------------------- tooltip & details
  const tooltip = $('tooltip');
  function linkFacts(l) {
    return [
      ['Type', l.type],
      ['Diameter', l.diameter == null ? 'not provided' : `${fmtNum(l.diameter)} mm`],
      ['Region', l.region ?? 'not assigned'],
      ['Nearest pressure sensor', l.sensor ?? 'not assigned'],
    ];
  }
  function sensorFacts(s) {
    const rows = [['Region', s.region ?? 'not assigned']];
    const d = state.shown && state.mode === 'alert' ? state.shown.sensors.find((x) => x.id === s.id) : null;
    if (d) {
      rows.push(['Deviation', fmtM(d.deviation_m)]);
      rows.push(['z', d.z == null ? 'not provided' : d.z.toFixed(2)]);
    }
    return rows;
  }
  function targetOf(node) {
    if (!node || !node.closest) return null;
    const linkNode = node.closest('[data-link]');
    if (linkNode) return { kind: 'link', item: links[Number(linkNode.getAttribute('data-link'))] };
    const sensorNode = node.closest('[data-sensor]');
    if (sensorNode) return { kind: 'sensor', item: sensorById.get(sensorNode.getAttribute('data-sensor')) };
    const lm = node.closest('[data-landmark]');
    if (lm) return { kind: 'landmark', item: landmarks.find((m) => m.id === lm.getAttribute('data-landmark')) };
    return null;
  }
  function setHover(target) {
    if (state.hover && state.hover.kind === 'link') pipeEls[state.hover.item.i].classList.remove('hover');
    state.hover = target;
    if (target && target.kind === 'link') pipeEls[target.item.i].classList.add('hover');
  }
  function showTooltip(target, sx, sy) {
    if (!target) { tooltip.hidden = true; return; }
    tooltip.replaceChildren();
    const title = target.kind === 'link' ? target.item.id : target.kind === 'sensor' ? `Sensor ${target.item.id}` : target.item.id;
    tooltip.append(el('strong', {}, title));
    const dl = el('dl');
    const rows = target.kind === 'link' ? linkFacts(target.item)
      : target.kind === 'sensor' ? sensorFacts(target.item) : [['Type', target.item.kind]];
    if (target.kind === 'link' && isCandidate(target.item)) rows.push(['Alert', 'candidate pipe (best guess)']);
    facts(dl, rows);
    tooltip.append(dl);
    tooltip.hidden = false;
    const w = tooltip.offsetWidth; const h = tooltip.offsetHeight;
    const left = sx + 14 + w > state.W ? sx - w - 14 : sx + 14;
    const top = sy + 14 + h > state.H ? sy - h - 14 : sy + 14;
    tooltip.style.transform = 'none';
    tooltip.style.left = `${Math.max(4, left)}px`;
    tooltip.style.top = `${Math.max(4, top)}px`;
  }
  const isCandidate = (l) => Boolean(state.shown && state.mode === 'alert' && state.shown.candidate_known && state.shown.candidate_pipe === l.id);

  const detail = $('detail');
  function openDetail(target, opener) {
    if (!target) return;
    tooltip.hidden = true;
    const { kind, item } = target;
    $('detail-title').textContent = kind === 'link' ? `Pipe ${item.id}` : kind === 'sensor' ? `Sensor ${item.id}` : item.id;
    $('detail-kind').textContent = kind === 'link' ? `Network ${item.type}` : kind === 'sensor' ? 'Pressure sensor' : `Network ${item.kind}`;
    facts($('detail-facts'), kind === 'link' ? linkFacts(item) : kind === 'sensor' ? sensorFacts(item) : [['Type', item.kind]]);
    const note = $('detail-note');
    note.hidden = true;
    if (kind === 'link' && isCandidate(item)) {
      note.hidden = false;
      note.textContent = `Candidate pipe of alert ${state.shown.id}: a best guess, not a confirmed leak location.`;
    } else if (kind === 'sensor' && state.shown && state.mode === 'alert' && state.shown.sensors.some((d) => d.id === item.id)) {
      note.hidden = false;
      const d = state.shown.sensors.find((x) => x.id === item.id);
      note.textContent = `${d.deviation_m < 0 ? 'Below' : 'Above'} expected pressure in alert ${state.shown.id}.`;
    }
    state.opener = opener || stage;
    detail.hidden = false;
    $('detail-title').focus({ preventScroll: true });
  }
  function closeDetail() {
    if (detail.hidden) return;
    detail.hidden = true;
    (state.opener && state.opener.isConnected ? state.opener : stage).focus({ preventScroll: true });
  }
  $('detail-close').addEventListener('click', closeDetail);

  // ---------------------------------------------------------------- pointer input
  // Controls drawn over the map keep their own clicks and keys; the stage handles the rest.
  const OVERLAYS = '.panel, .legend, .warnings, .zoom-tools, .attrib';
  const pointers = new Map();
  let drag = null;
  const local = (e) => {
    const r = stage.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  stage.addEventListener('pointerdown', (e) => {
    if (e.target.closest(OVERLAYS)) return;
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    pointers.set(e.pointerId, local(e));
    stage.setPointerCapture(e.pointerId);
    const p = local(e);
    drag = { x: p.x, y: p.y, startX: p.x, startY: p.y, moved: false, target: targetOf(e.target), pinch: null };
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      drag.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
    }
  });
  stage.addEventListener('pointermove', (e) => {
    const p = local(e);
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p);
    if (drag && drag.pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      const mx = (a.x + b.x) / 2; const my = (a.y + b.y) / 2;
      zoomAt(d / drag.pinch.d, mx, my);
      panBy(mx - drag.pinch.mx, my - drag.pinch.my);
      drag.pinch = { d, mx, my };
      drag.moved = true;
      return;
    }
    if (drag && pointers.has(e.pointerId)) {
      const dx = p.x - drag.x; const dy = p.y - drag.y;
      if (!drag.moved && Math.hypot(p.x - drag.startX, p.y - drag.startY) > 4) {
        drag.moved = true;
        stage.classList.add('dragging');
        tooltip.hidden = true;
      }
      if (drag.moved) panBy(dx, dy);
      drag.x = p.x; drag.y = p.y;
      return;
    }
    const target = targetOf(document.elementFromPoint(e.clientX, e.clientY));
    setHover(target);
    showTooltip(target, p.x, p.y);
  });
  const endPointer = (e) => {
    pointers.delete(e.pointerId);
    if (!drag) return;
    if (pointers.size === 0) {
      if (!drag.moved && e.type === 'pointerup' && drag.target) openDetail(drag.target, stage);
      stage.classList.remove('dragging');
      drag = null;
    } else if (pointers.size === 1) {
      const [only] = pointers.values();
      drag.x = only.x; drag.y = only.y; drag.pinch = null;
    }
  };
  stage.addEventListener('pointerup', endPointer);
  stage.addEventListener('pointercancel', endPointer);
  stage.addEventListener('pointerleave', () => { setHover(null); tooltip.hidden = true; });
  stage.addEventListener('wheel', (e) => {
    if (e.target.closest(OVERLAYS)) return;
    e.preventDefault();
    const p = local(e);
    const delta = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    zoomAt(Math.exp(-delta * 0.0018), p.x, p.y);
  }, { passive: false });
  stage.addEventListener('dblclick', (e) => {
    if (e.target.closest(OVERLAYS)) return;
    const p = local(e);
    const target = cameraFor(null);
    const k = clampK(state.k * 2);
    const f = k / state.k;
    target.k = k;
    target.tx = p.x - (p.x - state.tx) * f;
    target.ty = p.y - (p.y - state.ty) * f;
    state.moved = true;
    flyTo(target, 320);
  });

  // ---------------------------------------------------------------- keyboard
  stage.addEventListener('keydown', (e) => {
    if (e.target.closest(OVERLAYS) && e.key !== 'Escape') return;
    const step = 80;
    const handled = {
      ArrowLeft: () => panBy(step, 0), ArrowRight: () => panBy(-step, 0),
      ArrowUp: () => panBy(0, step), ArrowDown: () => panBy(0, -step),
      '+': () => zoomAt(1.4, state.W / 2, state.H / 2), '=': () => zoomAt(1.4, state.W / 2, state.H / 2),
      '-': () => zoomAt(1 / 1.4, state.W / 2, state.H / 2), _: () => zoomAt(1 / 1.4, state.W / 2, state.H / 2),
      0: () => fit(), f: () => focusAlert(), F: () => focusAlert(),
      Escape: () => closeDetail(),
      Enter: () => { const t = targetOf(e.target); if (t) openDetail(t, e.target); },
      ' ': () => { const t = targetOf(e.target); if (t) openDetail(t, e.target); },
    }[e.key];
    if (handled) { e.preventDefault(); handled(); }
  });
  detail.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); closeDetail(); } });

  // ---------------------------------------------------------------- toolbar
  function fit(duration = 450) {
    state.moved = false;
    state.lastTarget = null;
    flyTo(cameraFor(null), duration);
  }
  function focusAlert(duration = 1100) {
    const bounds = alertBounds(state.shown);
    state.lastTarget = bounds;
    state.moved = false;
    flyTo(cameraFor(bounds), duration);
  }
  $('zoom-in').addEventListener('click', () => zoomAt(1.5, state.W / 2, state.H / 2));
  $('zoom-out').addEventListener('click', () => zoomAt(1 / 1.5, state.W / 2, state.H / 2));
  $('fit').addEventListener('click', () => fit());
  $('focus').addEventListener('click', () => focusAlert());

  const tabs = [$('tab-alert'), $('tab-regions')];
  function setMode(mode, focusTab) {
    state.mode = mode;
    state.isolate = null;
    tabs.forEach((t) => {
      const on = t.id === `tab-${mode}`;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      if (on && focusTab) t.focus();
    });
    closeDetail();
    // Regions mode draws no alert, so the replay card steps aside until the alert view returns.
    if (state.replay.active) {
      if (mode === 'regions') { setPlaying(false); card.hidden = true; } else showCard(state.shown);
    }
    draw();
  }
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => setMode(i === 0 ? 'alert' : 'regions'));
    t.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        setMode(i === 0 ? 'regions' : 'alert', true);
      }
    });
  });

  // ---------------------------------------------------------------- replay
  const STEP_MS = 5 * 60 * 1000; // one 5-minute window
  const times = alerts.map((a) => Date.parse(a.time));
  const t0 = times.length ? Math.floor((times[0] - 30 * 60 * 1000) / STEP_MS) * STEP_MS : 0;
  const t1 = times.length ? times[times.length - 1] + STEP_MS : 0;
  const steps = times.length ? Math.round((t1 - t0) / STEP_MS) : 0;
  const scrub = $('scrub');
  const ticks = $('ticks');
  const card = $('card');
  const clock = $('clock');
  const playBtn = $('play');
  const many = alerts.length > 12;
  const fmtClock = (ms) => `${new Date(ms).toISOString().slice(0, 16).replace('T', ' ')} UTC`;

  function litAt(step) {
    const t = t0 + step * STEP_MS;
    let lit = -1;
    for (let i = 0; i < times.length; i += 1) if (times[i] <= t) lit = i;
    return lit;
  }
  function showCard(alert) {
    card.hidden = false;
    if (!alert) {
      $('card-title').textContent = 'No alert yet';
      $('card-when').textContent = fmtClock(t0 + state.replay.step * STEP_MS);
      facts($('card-facts'), []);
      $('card-pipe').textContent = '';
      $('card-end').hidden = true;
      $('card-badge').hidden = true;
      return;
    }
    $('card-title').textContent = alert.region ?? 'Region not available';
    $('card-badge').hidden = !alert.is_mock;
    $('card-when').textContent = alert.time_label;
    const rows = [];
    if (alert.strongest) rows.push(['Largest deviation', `${fmtM(alert.strongest.deviation_m)} at ${alert.strongest.sensor}`]);
    else rows.push(['Largest deviation', 'no deviating sensor']);
    rows.push(['Sensors', String(alert.sensors.length)]);
    if (alert.onset_label) rows.push(['Estimated onset', alert.onset_label.slice(11)]);
    if (alert.cusum) rows.push(['CUSUM', `${fmtNum(alert.cusum.statistic)} vs ${fmtNum(alert.cusum.threshold)}`]);
    facts($('card-facts'), rows);
    $('card-pipe').classList.toggle('none', !alert.candidate_pipe);
    $('card-pipe').textContent = alert.candidate_pipe
      ? `Suspected leak (best guess): ${alert.candidate_pipe}${alert.candidate_known ? '' : ' (not in the map)'}`
      : 'Candidate pipe: not provided';
    const end = state.replay.step >= steps;
    $('card-end').hidden = !end;
    if (end) {
      const regions = new Set(alerts.map((a) => a.region).filter(Boolean));
      $('card-end').textContent = `End of the recorded alerts: ${alerts.length} alert${alerts.length === 1 ? '' : 's'} in ${regions.size} region${regions.size === 1 ? '' : 's'}.`;
    }
  }
  // While the replay lights another alert, say so: the side panels keep the selected one.
  function renderReplayNote() {
    const note = $('replay-note');
    const r = state.replay;
    const other = r.active && selected && state.shown && state.shown.id !== selected.id;
    note.hidden = !r.active || !selected;
    if (!note.hidden) {
      note.textContent = other || !state.shown
        ? `Replay view. The panels beside the map still show the selected alert: ${selected.region ?? 'region not available'}, ${selected.time_label.slice(11, 16)} UTC.`
        : 'Replay view. This is also the selected alert.';
    }
  }
  function renderReplay() {
    const r = state.replay;
    clock.textContent = fmtClock(t0 + r.step * STEP_MS);
    scrub.value = String(r.step);
    const lit = litAt(r.step);
    ticks.querySelectorAll('span').forEach((s, i) => s.classList.toggle('lit', i === lit));
    scrub.setAttribute('aria-valuetext', `${fmtClock(t0 + r.step * STEP_MS)}${lit >= 0 ? `, alert in ${alerts[lit].region ?? 'unknown region'}` : ', no alert yet'}`);
    $('count').textContent = `${lit + 1} of ${alerts.length} alert${alerts.length === 1 ? '' : 's'}`;
    renderReplayNote();
  }
  function light(lit, flight) {
    const r = state.replay;
    if (lit === r.lit && r.active) return false;
    r.lit = lit;
    state.shown = lit >= 0 ? alerts[lit] : null;
    closeDetail();
    draw();
    showCard(state.shown);
    if (state.shown) {
      const bounds = alertBounds(state.shown);
      state.lastTarget = bounds;
      flyTo(cameraFor(bounds), flight);
    } else fit(flight);
    return true;
  }
  function setPlaying(on) {
    const r = state.replay;
    r.playing = on;
    // SVG elements have no ``hidden`` property: toggle the attribute itself.
    $('play-icon').toggleAttribute('hidden', on);
    $('pause-icon').toggleAttribute('hidden', !on);
    $('play-label').textContent = on ? 'Pause' : 'Replay';
    playBtn.setAttribute('aria-label', on ? 'Pause replay' : 'Play replay');
    clearTimeout(r.timer);
    if (on) tick();
  }
  function tick() {
    const r = state.replay;
    if (!r.playing) return;
    if (r.step >= steps) { setPlaying(false); return; }
    r.step += 1;
    const changed = light(litAt(r.step), many ? 0 : 1000);
    renderReplay();
    const span = Math.max(1, steps);
    const travel = Math.min(26000, Math.max(9000, alerts.length * 2200));
    const delay = changed && !many ? 1900 : Math.max(16, travel / span);
    r.timer = setTimeout(tick, delay);
  }
  function enterReplay() {
    const r = state.replay;
    if (!r.active) {
      r.active = true;
      r.lit = -2;
    }
  }
  function exitReplay() {
    const r = state.replay;
    setPlaying(false);
    r.active = false;
    r.lit = -1;
    card.hidden = true;
    state.shown = selected;
    renderReplayNote();
    draw();
    focusAlert(800);
    r.step = selected ? Math.round((Date.parse(selected.time) - t0) / STEP_MS) : steps;
    renderReplay();
    playBtn.focus({ preventScroll: true });
  }
  $('card-exit').addEventListener('click', exitReplay);
  if (!alerts.length) {
    playBtn.disabled = true;
    scrub.disabled = true;
    $('count').textContent = 'No alerts to replay';
    clock.textContent = '';
  } else {
    scrub.max = String(steps);
    for (const t of times) {
      const s = el('span');
      s.style.left = `${((t - t0) / (t1 - t0)) * 100}%`;
      ticks.append(s);
    }
    state.replay.step = selected ? Math.round((Date.parse(selected.time) - t0) / STEP_MS) : steps;
    renderReplay();
    playBtn.addEventListener('click', () => {
      const r = state.replay;
      if (r.playing) { setPlaying(false); return; }
      // A fresh replay, or one that reached the end, starts at the beginning of the day.
      if (!r.active || r.step >= steps) r.step = 0;
      enterReplay();
      light(litAt(r.step), 600);
      renderReplay();
      setPlaying(true);
    });
    scrub.addEventListener('input', () => {
      const r = state.replay;
      setPlaying(false);
      enterReplay();
      r.step = Number(scrub.value);
      light(litAt(r.step), 500);
      renderReplay();
    });
  }
  $('card-exit').hidden = !selected;

  // ---------------------------------------------------------------- start
  // Short credit on the map; the full attribution is its tooltip and is in the page footer.
  $('attrib').textContent = 'BattLeDIM 2020 · CC BY 4.0';
  $('attrib').title = `${data.network} · ${data.attribution}`;
  const resize = new ResizeObserver(() => {
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const first = state.W === 0;
    state.W = r.width;
    state.H = r.height;
    if (first) {
      Object.assign(state, cameraFor(null));
      draw();
      // Open on the whole network, then let the camera come down to the alert.
      if (state.shown && alertBounds(state.shown)) {
        setTimeout(() => focusAlert(reduceMotion.matches ? 0 : 1400), reduceMotion.matches ? 0 : 500);
      }
    } else if (!state.moved) {
      Object.assign(state, cameraFor(state.lastTarget));
      applyCamera();
    } else applyCamera();
  });
  resize.observe(stage);
  draw();

  // Read-only handle for browser checks; it exposes the camera and the shown alert only.
  window.civicWaterMap = Object.freeze({
    get state() {
      return { k: state.k, tx: state.tx, ty: state.ty, mode: state.mode, shown: state.shown && state.shown.id,
        replay: { active: state.replay.active, playing: state.replay.playing, step: state.replay.step, lit: state.replay.lit } };
    },
  });
})();
