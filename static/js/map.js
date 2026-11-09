'use strict';
// Dark Equal Earth choropleth map.
// Fetches /world.svg + /api/geo, paints countries by item count,
// wires hover tooltip and click-to-filter via shared state.

import { fetchGeo } from './api.js';
import { setState, onState, state } from './state.js';

var RAMP = ['#cfe1f2', '#93c0e0', '#5699cd', '#2b6cb0', '#13375e'];
var NO_DATA = '#d9dee4';

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
    if (d && d.items > 0) {
      p.style.fill = pickColor(d.items, max);
      p.classList.add('hit');
    } else {
      p.style.fill = NO_DATA;
      p.classList.remove('hit');
    }
  }
  return max;
}

// --- legend ---

function buildLegend(container) {
  var legend = document.createElement('div');
  legend.className = 'map-legend';

  var fewer = document.createElement('span');
  fewer.textContent = 'fewer';
  legend.appendChild(fewer);

  for (var i = 0; i < RAMP.length; i++) {
    var swatch = document.createElement('i');
    swatch.style.background = RAMP[i];
    legend.appendChild(swatch);
  }

  var more = document.createElement('span');
  more.textContent = 'more';
  legend.appendChild(more);

  container.appendChild(legend);
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

  paintMap(_svgRoot, geo);
  buildTray(el, geo.buckets || {});

  // Remove hidden AFTER everything is ready
  el.removeAttribute('hidden');

  // Tray click
  el.addEventListener('click', function (e) {
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
    var name = (d && d.name) ? d.name : iso;
    var label;
    if (d && d.items > 0) {
      label = name + ' — ' + d.items + ' items, ' + d.sources + ' sources';
    } else {
      label = name + ' — no sources';
    }
    showTip(label, e.clientX, e.clientY);
  });

  _svgRoot.addEventListener('mouseleave', function () {
    hideTip();
  });

  // State subscriber: sync active highlight + tray chips
  // NOTE: this subscriber only reads state and toggles classes; it never calls setState.
  // No feedback loop is possible.
  onState(function (st) {
    updateActive(st);
    updateTray(st);
  });

  // Apply current state immediately in case spine already set a country
  updateActive(state);
  updateTray(state);
}
