'use strict';
// Region -> Country navigation spine.
// Fetches /api/geo and /api/sources, builds a collapsible two-level list
// (regions -> countries) plus a Transnational group, and drives the feed
// filter via setState({region, country, scope}).

import { fetchGeo, fetchSources } from './api.js';
import { setState, onState, state } from './state.js';

// --- helpers (no ?. or ??) ---

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
  });
}

function el(tag, attrs, children) {
  var e = document.createElement(tag);
  var k;
  for (k in attrs) {
    if (Object.prototype.hasOwnProperty.call(attrs, k)) {
      if (k === 'className') { e.className = attrs[k]; }
      else if (k === 'textContent') { e.textContent = attrs[k]; }
      else { e.setAttribute(k, attrs[k]); }
    }
  }
  if (children) {
    for (var i = 0; i < children.length; i++) {
      if (children[i]) e.appendChild(children[i]);
    }
  }
  return e;
}

function btn(text, dataset, title) {
  var b = document.createElement('button');
  b.className = 'spine-btn';
  b.textContent = text;
  if (title) b.title = title;
  var k;
  for (k in dataset) {
    if (Object.prototype.hasOwnProperty.call(dataset, k)) {
      b.dataset[k] = dataset[k];
    }
  }
  return b;
}

function countBadge(n) {
  var s = document.createElement('span');
  s.className = 'spine-n';
  s.textContent = String(n);
  return s;
}

// --- build data model from geo + sources ---

function buildModel(geo, sourceList) {
  // regions: {regionName: {items, countries: {iso: {items, sources, name, region}}}}
  var regions = {};
  var isos = Object.keys(geo.countries);
  for (var i = 0; i < isos.length; i++) {
    var iso = isos[i];
    var d = geo.countries[iso];
    var r = d.region || 'Other';
    if (!regions[r]) regions[r] = { items: 0, countries: {} };
    regions[r].items += d.items;
    regions[r].countries[iso] = d;
  }
  // buckets: transnational scopes
  var buckets = geo.buckets || {};
  return { regions: regions, buckets: buckets };
}

// --- render ---

var _container = null;
var _model = null;
var _collapsed = {};   // regionName -> bool (true = collapsed)

function activeClass(condition) {
  return condition ? ' on' : '';
}

function renderSpine() {
  if (!_container || !_model) return;

  var regions = _model.regions;
  var buckets = _model.buckets;
  var currentRegion = state.region || 'All';
  var currentCountry = state.country || '';
  var currentScope = state.scope || '';

  var frag = document.createDocumentFragment();

  // --- Reset / All button ---
  var allBtn = btn('All regions', { spineRegion: 'All' });
  allBtn.className = 'spine-btn spine-all' + activeClass(currentRegion === 'All' && !currentCountry && !currentScope);
  frag.appendChild(allBtn);

  // --- Region list ---
  var regionNames = Object.keys(regions);
  for (var ri = 0; ri < regionNames.length; ri++) {
    var regionName = regionNames[ri];
    var regionData = regions[regionName];
    var collapsed = _collapsed[regionName] !== false; // default collapsed
    var regionActive = currentRegion === regionName && !currentCountry && !currentScope;

    // Region row: toggle + filter button
    var toggler = document.createElement('button');
    toggler.className = 'spine-toggle';
    toggler.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    toggler.setAttribute('aria-label', (collapsed ? 'Expand ' : 'Collapse ') + regionName);
    toggler.dataset.spineToggle = regionName;
    toggler.textContent = collapsed ? '+' : '−';

    var regionBtn = document.createElement('button');
    regionBtn.className = 'spine-btn spine-region' + activeClass(regionActive);
    regionBtn.dataset.spineRegion = regionName;
    var regionLabel = document.createTextNode(regionName);
    regionBtn.appendChild(regionLabel);
    regionBtn.appendChild(countBadge(regionData.items));

    var regionRow = el('div', { className: 'spine-row' }, [toggler, regionBtn]);
    frag.appendChild(regionRow);

    // Country rows (shown if not collapsed)
    if (!collapsed) {
      var countryCodes = Object.keys(regionData.countries);
      // sort alphabetically by name
      countryCodes.sort(function (a, b) {
        var na = regionData.countries[a].name || a;
        var nb = regionData.countries[b].name || b;
        return na.localeCompare(nb);
      });
      for (var ci = 0; ci < countryCodes.length; ci++) {
        var iso = countryCodes[ci];
        var cd = regionData.countries[iso];
        var countryActive = currentCountry === iso;
        var cBtn = document.createElement('button');
        cBtn.className = 'spine-btn spine-country' + activeClass(countryActive);
        cBtn.dataset.spineCountry = iso;
        cBtn.dataset.spineCountryRegion = regionName;
        var cLabel = document.createTextNode(cd.name || iso);
        cBtn.appendChild(cLabel);
        cBtn.appendChild(countBadge(cd.items));
        var cRow = el('div', { className: 'spine-row spine-country-row' }, [cBtn]);
        frag.appendChild(cRow);
      }
    }
  }

  // --- Transnational group ---
  var bucketKeys = Object.keys(buckets);
  if (bucketKeys.length > 0) {
    var transHead = el('div', { className: 'spine-section-head', textContent: 'Transnational' });
    frag.appendChild(transHead);
    for (var bi = 0; bi < bucketKeys.length; bi++) {
      var scope = bucketKeys[bi];
      var bd = buckets[scope];
      var scopeActive = currentScope === scope;
      var sBtn = document.createElement('button');
      sBtn.className = 'spine-btn spine-scope' + activeClass(scopeActive);
      sBtn.dataset.spineScope = scope;
      var sLabel = document.createTextNode(scope);
      sBtn.appendChild(sLabel);
      sBtn.appendChild(countBadge(bd.items));
      var sRow = el('div', { className: 'spine-row' }, [sBtn]);
      frag.appendChild(sRow);
    }
  }

  _container.innerHTML = '';
  _container.appendChild(frag);
}

// --- event handling ---

function wireSpineEvents(container) {
  container.addEventListener('click', function (e) {
    var t = e.target;

    // toggle expand/collapse
    var toggleBtn = t.closest('[data-spine-toggle]');
    if (toggleBtn) {
      var regionName = toggleBtn.dataset.spineToggle;
      // flip: default is collapsed (undefined treated as true)
      _collapsed[regionName] = (_collapsed[regionName] === false); // undefined or true -> expand
      renderSpine();
      return;
    }

    // region filter
    var regionBtn = t.closest('[data-spine-region]');
    if (regionBtn) {
      var r = regionBtn.dataset.spineRegion;
      setState({ region: r, country: '', scope: '' });
      return;
    }

    // country filter
    var countryBtn = t.closest('[data-spine-country]');
    if (countryBtn) {
      var iso = countryBtn.dataset.spineCountry;
      var cRegion = countryBtn.dataset.spineCountryRegion || 'All';
      setState({ region: cRegion, country: iso, scope: '' });
      return;
    }

    // scope / transnational filter
    var scopeBtn = t.closest('[data-spine-scope]');
    if (scopeBtn) {
      var scope = scopeBtn.dataset.spineScope;
      setState({ region: 'All', country: '', scope: scope });
      return;
    }
  });
}

// --- init ---

export async function initSpine(container) {
  if (!container) return;
  _container = container;

  // Show loading placeholder
  container.innerHTML = '<div class="spine-loading">Loading…</div>';

  try {
    var geoData = await fetchGeo(0);
    // fetchSources already called by reader; reuse if available, else fetch again
    var srcData = await fetchSources();
    _model = buildModel(geoData, srcData.sources || []);
  } catch (e) {
    container.innerHTML = '<div class="spine-loading">Geography unavailable</div>';
    return;
  }

  wireSpineEvents(container);

  // Re-render when shared state changes (region/country/scope set by reader or spine itself)
  onState(function () { renderSpine(); });

  renderSpine();
}
