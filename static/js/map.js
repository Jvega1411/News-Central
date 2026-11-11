'use strict';
// Light Equal Earth choropleth map.
// Fetches /world.svg + /api/geo, paints countries by item count or by RSF
// press-freedom score (toggle), wires hover tooltip and click-to-filter via
// shared state.

import { fetchGeo } from './api.js';
import { setState, onState, state } from './state.js';

var RAMP = ['#cfe1f2', '#93c0e0', '#5699cd', '#2b6cb0', '#13375e'];
var NO_DATA = '#d9dee4';
var PF_YEAR = 2026;

// RSF's five published categories, highest freedom first.
var PF_CATS = [
  { min: 85, label: 'good situation', range: '85–100', color: '#2f8f5b' },
  { min: 70, label: 'satisfactory', range: '70–85', color: '#6fae7a' },
  { min: 55, label: 'problematic', range: '55–70', color: '#e9d16b' },
  { min: 40, label: 'difficult', range: '40–55', color: '#d98f2b' },
  { min: -Infinity, label: 'very serious', range: '0–40', color: '#c0392b' }
];

// Layer mode is local to this module: 'coverage' or 'press'.
var _mode = 'coverage';
var _geo = null;
var _legendEl = null;
var _toggleEl = null;

// --- press-freedom helpers (pure) ---

function pfCategory(score) {
  if (typeof score !== 'number' || isNaN(score)) return null;
  for (var i = 0; i < PF_CATS.length; i++) {
    if (score >= PF_CATS[i].min) return PF_CATS[i];
  }
  return null;
}

function pfColor(score) {
  var cat = pfCategory(score);
  return cat ? cat.color : NO_DATA;
}

function hasPressData(geo) {
  var pf = geo && geo.press_freedom;
  return !!pf && typeof pf === 'object' && Object.keys(pf).length > 0;
}

function tipText(mode, name, d, score) {
  var counts;
  if (d && d.items > 0) {
    counts = d.items + ' items, ' + d.sources + ' sources';
  } else {
    counts = 'no sources';
  }
  if (mode !== 'press') return name + ' — ' + counts;
  var cat = pfCategory(score);
  var head = cat ? name + ' — ' + score.toFixed(2) + ' ' + cat.label : name + ' — no score';
  if (d && d.items > 0) return head + ' — ' + counts;
  return head;
}

// Country name for ISO codes the geo payload does not carry (countries with a
// score but no sources). Falls back to the code where Intl.DisplayNames is missing.
var _regionNames = null;

function regionName(iso) {
  try {
    if (!_regionNames) _regionNames = new Intl.DisplayNames(['en'], { type: 'region' });
    return _regionNames.of(iso) || iso;
  } catch (e) {
    return iso;
  }
}

// --- colour helpers ---

function pickColor(count, max) {
  if (!count || max < 1) return NO_DATA;
  var len = RAMP.length;
  var t = Math.log1p(count) / Math.log1p(max);
  var idx = Math.min(len - 1, Math.floor(t * len));
  return RAMP[idx];
}

// --- tooltip ---

var _tip = null;

function getTip() {
  if (!_tip) {
    _tip = document.createElement('div');
    _tip.className = 'map-tip';
    _tip.style.display = 'none';
    document.body.appendChild(_tip);
  }
  return _tip;
}

function showTip(text, cx, cy) {
  var tip = getTip();
  tip.textContent = text;
  tip.style.display = '';
  tip.style.left = (cx + 12) + 'px';
  tip.style.top = (cy + 12) + 'px';
}

function hideTip() {
  if (_tip) _tip.style.display = 'none';
}

// --- paint ---

function paintMap(svgRoot, geo) {
  var countries = geo.countries || {};
  var pf = geo.press_freedom || {};
  var press = _mode === 'press';
  var isos = Object.keys(countries);
  var max = 0;
  for (var i = 0; i < isos.length; i++) {
    var c = countries[isos[i]].items || 0;
    if (c > max) max = c;
  }

  var paths = svgRoot.querySelectorAll('path[id]');
  for (var pi = 0; pi < paths.length; pi++) {
    var p = paths[pi];
    var iso = p.id.toUpperCase();
    var d = countries[iso];
    if (press) {
      p.style.fill = pfColor(pf[iso]);
    } else if (d && d.items > 0) {
      p.style.fill = pickColor(d.items, max);
    } else {
      p.style.fill = NO_DATA;
    }
    // Clickability depends on items only, in both modes.
    if (d && d.items > 0) {
      p.classList.add('hit');
    } else {
      p.classList.remove('hit');
    }
  }
  return max;
}

// --- legend ---

function buildLegend(container) {
  _legendEl = document.createElement('div');
  _legendEl.className = 'map-legend';
  container.appendChild(_legendEl);
  renderLegend();
}

function renderLegend() {
  if (!_legendEl) return;
  while (_legendEl.firstChild) _legendEl.removeChild(_legendEl.firstChild);
  var i, swatch;

  if (_mode === 'press') {
    _legendEl.classList.add('pf');
    for (i = PF_CATS.length - 1; i >= 0; i--) {
      var cat = PF_CATS[i];
      var item = document.createElement('span');
      item.className = 'pf-item';
      swatch = document.createElement('i');
      swatch.style.background = cat.color;
      item.appendChild(swatch);
      var txt = document.createElement('span');
      txt.textContent = cat.label + ' ' + cat.range;
      item.appendChild(txt);
      _legendEl.appendChild(item);
    }
    var src = document.createElement('span');
    src.className = 'pf-src';
    src.textContent = 'Source: RSF World Press Freedom Index ' + PF_YEAR;
    _legendEl.appendChild(src);
    return;
  }

  _legendEl.classList.remove('pf');
  var fewer = document.createElement('span');
  fewer.textContent = 'fewer';
  _legendEl.appendChild(fewer);

  for (i = 0; i < RAMP.length; i++) {
    swatch = document.createElement('i');
    swatch.style.background = RAMP[i];
    _legendEl.appendChild(swatch);
  }

  var more = document.createElement('span');
  more.textContent = 'more';
  _legendEl.appendChild(more);
}

// --- layer toggle ---

var LAYERS = [
  { mode: 'coverage', label: 'Coverage' },
  { mode: 'press', label: 'Press freedom' }
];

function buildToggle(container) {
  _toggleEl = document.createElement('div');
  _toggleEl.className = 'map-layer';
  for (var i = 0; i < LAYERS.length; i++) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.setAttribute('data-layer', LAYERS[i].mode);
    b.textContent = LAYERS[i].label;
    _toggleEl.appendChild(b);
  }
  if (_legendEl && _legendEl.parentNode === container) {
    container.insertBefore(_toggleEl, _legendEl);
  } else {
    container.appendChild(_toggleEl);
  }
  updateToggle();
}

function updateToggle() {
  if (!_toggleEl) return;
  var btns = _toggleEl.querySelectorAll('button');
  for (var i = 0; i < btns.length; i++) {
    var on = btns[i].getAttribute('data-layer') === _mode;
    btns[i].setAttribute('aria-pressed', on ? 'true' : 'false');
    if (on) btns[i].classList.add('on'); else btns[i].classList.remove('on');
  }
}

function setMode(mode) {
  if (mode !== 'coverage' && mode !== 'press') return;
  if (mode === _mode) return;
  _mode = mode;
  updateToggle();
  renderLegend();
  if (_svgRoot && _geo) paintMap(_svgRoot, _geo);
}

// --- tray ---

var _trayEl = null;

function buildTray(container, buckets) {
  _trayEl = document.createElement('div');
  _trayEl.className = 'map-tray';

  var keys = Object.keys(buckets);
  for (var i = 0; i < keys.length; i++) {
    var scope = keys[i];
    var bd = buckets[scope];
    var chip = document.createElement('button');
    chip.className = 'chip';
    chip.setAttribute('data-scope', scope);
    chip.textContent = scope + ' ' + (bd.items || 0);
    _trayEl.appendChild(chip);
  }

  container.appendChild(_trayEl);
}

function updateTray(st) {
  if (!_trayEl) return;
  var chips = _trayEl.querySelectorAll('.chip');
  for (var i = 0; i < chips.length; i++) {
    var chip = chips[i];
    var scopeVal = chip.getAttribute('data-scope');
    if (scopeVal === st.scope) {
      chip.classList.add('on');
    } else {
      chip.classList.remove('on');
    }
  }
}

// --- active country highlight ---

var _svgRoot = null;

function updateActive(st) {
  if (!_svgRoot) return;
  var paths = _svgRoot.querySelectorAll('path[id]');
  for (var i = 0; i < paths.length; i++) {
    var p = paths[i];
    if (p.id === st.country) {
      p.classList.add('active');
    } else {
      p.classList.remove('active');
    }
  }
}

// --- init ---

// _ready is set to true only after SVG + geo are fully loaded and painted.
// The map element stays hidden until view === 'map' AND _ready is true.
var _ready = false;

export async function initMap(el) {
  if (!el) return;

  var svgText;
  try {
    var resp = await fetch('/world.svg', { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    svgText = await resp.text();
  } catch (e) {
    return;
  }

  // Parse and inject SVG
  var parser = new DOMParser();
  var svgDoc = parser.parseFromString(svgText, 'image/svg+xml');
  var svgNode = svgDoc.documentElement;
  if (!svgNode || svgNode.nodeName === 'parsererror') return;

  el.appendChild(svgNode);
  _svgRoot = el.querySelector('svg');
  if (!_svgRoot) return;

  buildLegend(el);

  var geo;
  try {
    geo = await fetchGeo(0);
  } catch (e) {
    return;
  }

  _geo = geo;
  paintMap(_svgRoot, geo);
  if (hasPressData(geo)) buildToggle(el);
  buildTray(el, geo.buckets || {});

  // Mark init complete, then apply current view to decide visibility.
  // The element was born with [hidden] in the HTML; show it only if
  // the current view is already 'map'.
  _ready = true;
  if (state.view === 'map') {
    el.removeAttribute('hidden');
  }

  // Layer toggle, tray and country clicks
  el.addEventListener('click', function (e) {
    var layerBtn = e.target.closest('[data-layer]');
    if (layerBtn) {
      setMode(layerBtn.getAttribute('data-layer'));
      return;
    }

    var chip = e.target.closest('[data-scope]');
    if (chip) {
      var scopeVal = chip.getAttribute('data-scope');
      setState({ scope: scopeVal, country: '', region: 'All' });
      return;
    }

    // Country click
    var path = e.target.closest('path[id]');
    if (path && path.classList.contains('hit')) {
      var iso = path.id.toUpperCase();
      var countries = geo.countries || {};
      var d = countries[iso];
      var regionVal = (d && d.region) ? d.region : 'All';
      setState({ country: iso, region: regionVal, scope: '' });
    }
  });

  // Hover tooltip
  _svgRoot.addEventListener('mousemove', function (e) {
    var path = e.target.closest('path[id]');
    if (!path) { hideTip(); return; }
    var iso = path.id.toUpperCase();
    var countries = geo.countries || {};
    var d = countries[iso];
    var name = (d && d.name) ? d.name : regionName(iso);
    var pfMap = geo.press_freedom || {};
    showTip(tipText(_mode, name, d, pfMap[iso]), e.clientX, e.clientY);
  });

  _svgRoot.addEventListener('mouseleave', function () {
    hideTip();
  });

  // State subscriber: sync active highlight, tray chips, and map visibility.
  // NOTE: this subscriber only reads state and toggles classes/hidden; it never
  // calls setState. No feedback loop is possible.
  onState(function (st) {
    updateActive(st);
    updateTray(st);
    // Show the map only when the user is on the Map tab and init succeeded.
    if (_ready) {
      if (st.view === 'map') {
        el.removeAttribute('hidden');
      } else {
        el.setAttribute('hidden', '');
      }
    }
  });

  // Apply current state immediately in case spine already set a country
  updateActive(state);
  updateTray(state);
}
