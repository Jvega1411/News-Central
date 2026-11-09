'use strict';
import { fetchSources, fetchItems, fetchSaved, postRefresh, postSave, postUnsave, postSavedUpdate, fetchExportMd } from './api.js';
import { setState, onState } from './state.js';

const REGION_ORDER = ['Spain', 'Europe & EU', 'Eurasia', 'Middle East & N. Africa', 'Africa', 'Latin America',
  'North America', 'South Asia', 'East Asia', 'Southeast Asia', 'Oceania', 'Global'];
const PAGE = 150;
// Section names in article URLs that mark sports and celebrity coverage (culture, music and film stay in).
const SPORT_RE = /\/(?:sports?|deportes?|esportes?|futbol|football|soccer|nfl|nba|mlb|tenis|tennis|golf|f1|formula-?1|cricket|rugby|boxeo|boxing|ufc|motorsport|celebrit(?:y|ies)|famosos|farandula|espectaculos|teleshow|showbiz|gente|horoscopos?|horoscopes?|loterias?|lottery|k-?pop)(?:\/|-|\.|$)/;
function isSportOrCeleb(link) {
  try { return SPORT_RE.test(new URL(link).pathname.toLowerCase()); } catch (e) { return false; }
}
function $(sel) { return document.querySelector(sel); }

const pref = {
  get: function (k, d) { try { var v = localStorage.getItem('nc.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set: function (k, v) { try { localStorage.setItem('nc.' + k, JSON.stringify(v)); } catch (e) {} }
};

const S = {
  items: new Map(), sources: {}, sourceList: [], saved: new Map(),
  region: pref.get('region', 'All'), win: pref.get('win', 24), kind: pref.get('kind', 'all'),
  hideState: pref.get('hideState', false), hideSports: pref.get('hideSports', true), showDigested: pref.get('showDigested', false),
  q: '', src: '', country: '', scope: '', shown: PAGE, cutoff: 0, maxSeen: 0, view: 'feed',
  running: false, updated: 0, sourcesError: '', booting: true, applyAfterRun: false, timer: null,
  _ignoreState: false
};

// Mirror initial prefs to shared state.
setState({
  region: S.region, country: S.country, scope: S.scope,
  win: S.win, kind: S.kind,
  hideState: S.hideState, hideSports: S.hideSports, showDigested: S.showDigested,
  view: S.view, q: S.q
});

// Subscribe to state changes driven by external modules (e.g. spine.js).
// Guard: skip if the change originated from reader itself (_ignoreState flag).
onState(function (st) {
  if (S._ignoreState) return;
  var changed = false;
  if (st.region !== undefined && st.region !== S.region) { S.region = st.region; changed = true; }
  if (st.country !== undefined && st.country !== S.country) { S.country = st.country; changed = true; }
  if (st.scope !== undefined && st.scope !== S.scope) { S.scope = st.scope; changed = true; }
  if (!changed) return;
  S.shown = PAGE;
  if (S.view === 'feed') { renderFeed(); window.scrollTo(0, 0); }
});

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; });
}
function safeHref(u) { return /^https?:\/\//i.test(u || '') ? esc(u) : '#'; }
function norm(s) { return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
function ago(ts) {
  var s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'now';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
}
function toast(msg) {
  var t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(function () { t.classList.remove('show'); }, 2400);
}

/* ------------------------------------------------------------ data */

async function loadSources() {
  var d = await fetchSources();
  S.sources = {};
  d.sources.forEach(function (s) { S.sources[s.id] = s; });
  S.sourceList = d.sources;
  S.running = d.running; S.updated = d.updated; S.sourcesError = d.sources_error;
}

async function loadItems() {
  var d = await fetchItems(S.maxSeen);
  var added = 0;
  for (var i = 0; i < d.items.length; i++) {
    var it = d.items[i];
    if (S.items.has(it.id)) continue;
    var src = S.sources[it.s];
    it._sport = isSportOrCeleb(it.link);
    it._text = norm(it.title + ' ' + (it.summary || '') + ' ' + (src ? src.name + ' ' + (src.country || '') : ''));
    S.items.set(it.id, it);
    if (it.fs > S.maxSeen) S.maxSeen = it.fs;
    added++;
  }
  S.running = d.running; S.updated = d.updated;
  var minT = Date.now() / 1000 - 96 * 3600;
  for (var entry of S.items) {
    var id = entry[0], item = entry[1];
    if (item.t < minT && item.fs < minT) S.items.delete(id);
  }
  return added;
}

async function loadSaved() {
  var d = await fetchSaved();
  S.saved = new Map(d.saved.map(function (x) { return [x.id, x]; }));
  updateSavedCount();
}
function updateSavedCount() { $('#saved-n').textContent = S.saved.size ? '(' + S.saved.size + ')' : ''; }

function regionsPresent() {
  var set = new Set(S.sourceList.map(function (s) { return s.region; }));
  var out = REGION_ORDER.filter(function (r) { return set.has(r); });
  set.forEach(function (r) { if (out.indexOf(r) === -1) out.push(r); });
  return out;
}

function effectiveIso(src) {
  // Returns the ISO or scope string that represents this source's geographic focus.
  return (src.exile && src.covers) ? src.covers : (src.country_iso || src.scope || '');
}

function compute() {
  var minT = Date.now() / 1000 - S.win * 3600;
  var terms = norm(S.q).split(/\s+/).filter(Boolean);
  var shown = [], counts = {};
  var total = 0, pending = 0;
  for (var it of S.items.values()) {
    var src = S.sources[it.s];
    if (!src || it.t < minT) continue;
    if (S.kind !== 'all' && (src.kind || 'news') !== S.kind) continue;
    if (S.hideState && src.state_media) continue;
    if (S.hideSports && it._sport) continue;
    if (S.src && it.s !== S.src) continue;
    var ok = true;
    for (var i = 0; i < terms.length; i++) if (it._text.indexOf(terms[i]) === -1) { ok = false; break; }
    if (!ok) continue;
    // country filter: match effective ISO against selected country
    if (S.country && effectiveIso(src) !== S.country) continue;
    // scope filter: match source scope against selected bucket
    if (S.scope && (src.scope || '') !== S.scope) continue;
    var inRegion = S.region === 'All' || src.region === S.region;
    if (it.fs > S.cutoff) { if (inRegion) pending++; continue; }
    counts[src.region] = (counts[src.region] || 0) + 1;
    total++;
    if (inRegion) shown.push(it);
  }
  shown.sort(function (a, b) { return b.t - a.t; });
  return { shown: shown, counts: counts, total: total, pending: pending };
}

/* ------------------------------------------------------------ rendering */

function renderStatus() {
  if (S.sourcesError) { $('#status').textContent = S.sourcesError; return; }
  var L = S.sourceList;
  var ok = L.filter(function (s) { return s.status === 'ok' || s.status === 'discovered'; }).length;
  var txt = ok + '/' + L.length + ' sources';
  if (S.updated) { var a = ago(S.updated); txt += ' · updated ' + (a === 'now' ? 'just now' : a + ' ago'); }
  if (S.running) txt += ' · fetching…';
  $('#status').textContent = txt;
}

function itemHTML(it, now) {
  var src = S.sources[it.s];
  var d = new Date(it.t * 1000);
  var sameDay = d.toDateString() === new Date().toDateString();
  var top = sameDay ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                    : d.toLocaleDateString([], { day: 'numeric', month: 'short' });
  var tags = [];
  if (src.lang && src.lang !== 'en') tags.push('<span class="tag">' + esc(src.lang) + '</span>');
  if (src.kind === 'analysis') tags.push('<span class="tag an">analysis</span>');
  if (src.state_media) tags.push('<span class="tag state">state-controlled</span>');
  if (src.paywall) tags.push('<span class="tag">paywall</span>');
  if (src.lang && src.lang !== 'en' && src.lang !== 'es') {
    tags.push('<a href="https://translate.google.com/translate?sl=auto&tl=en&u=' + encodeURIComponent(it.link) +
              '" target="_blank" rel="noopener">translate</a>');
  }
  var saved = S.saved.has(it.id);
  return '<article class="item' + (now - it.t < 3600 ? ' fresh' : '') + '" data-id="' + esc(it.id) + '">' +
    '<div class="time">' + esc(top) + '<span class="ago">' + ago(it.t) + '</span></div>' +
    '<div><div class="meta"><span class="src" data-src="' + esc(it.s) + '" title="Show only this outlet">' + esc(src.name) +
    '</span><span>' + esc(src.country || '') + '</span>' + tags.join('') + '</div>' +
    '<a class="title" href="' + safeHref(it.link) + '" target="_blank" rel="noopener">' + esc(it.title) + '</a>' +
    (it.summary ? '<div class="sum">' + esc(it.summary) + '</div>' : '') + '</div>' +
    '<button class="star' + (saved ? ' on' : '') + '" data-star="' + esc(it.id) + '" title="Save for later">' +
    (saved ? '★' : '☆') + '</button></article>';
}

function renderFeed(listToo) {
  var r = compute();
  var regs = ['All'].concat(regionsPresent());
  var chips = '';
  if (S.src && S.sources[S.src]) {
    chips += '<button class="chip on" data-clearsrc="1">Only ' + esc(S.sources[S.src].name) + ' ✕</button>';
  }
  chips += regs.map(function (g) {
    return '<button class="chip' + (S.region === g ? ' on' : '') + '" data-region="' + esc(g) + '">' +
      esc(g) + '<span class="n">' + (g === 'All' ? r.total : (r.counts[g] || 0)) + '</span></button>';
  }).join('');
  $('#regions').innerHTML = chips;

  var b = $('#banner');
  if (r.pending > 0 && S.view === 'feed') {
    b.textContent = r.pending + ' new item' + (r.pending > 1 ? 's' : '') + ' · show';
    b.style.display = 'block';
  } else {
    b.style.display = 'none';
  }
  document.title = (r.pending ? '(' + r.pending + ') ' : '') + 'News Central';
  if (listToo === false) return;

  var el = $('#feed');
  if (!r.shown.length) {
    var msg = 'Nothing matches these filters.';
    if (!S.items.size) msg = S.running ? 'Fetching every source for the first time. This takes a minute or two.'
                                       : 'No items yet. Press Refresh.';
    el.innerHTML = '<div class="empty">' + msg + '</div>';
    return;
  }
  var now = Date.now() / 1000;
  var h = r.shown.slice(0, S.shown).map(function (it) { return itemHTML(it, now); }).join('');
  if (r.shown.length > S.shown) h += '<button class="btn more" id="more">Show more (' + (r.shown.length - S.shown) + ' left)</button>';
  el.innerHTML = h;
}

function updateTimes() {
  var now = Date.now() / 1000;
  document.querySelectorAll('#feed .item').forEach(function (el) {
    var it = S.items.get(el.dataset.id);
    if (!it) return;
    el.querySelector('.ago').textContent = ago(it.t);
    el.classList.toggle('fresh', now - it.t < 3600);
  });
}

function renderSaved() {
  var all = Array.from(S.saved.values()).sort(function (a, b) { return b.saved_at - a.saved_at; });
  var list = S.showDigested ? all : all.filter(function (x) { return !x.digested; });
  var open = all.filter(function (x) { return !x.digested; }).length;
  var h = '<div class="toolbar">' +
    '<button class="btn" id="copy-md">Copy for Claude (' + open + ' not digested)</button>' +
    '<a class="btn" href="/api/export.md?download=1">Download .md (all)</a>' +
    '<label class="btn"><input type="checkbox" id="show-dig"' + (S.showDigested ? ' checked' : '') + '> Show digested</label></div>';
  if (!list.length) {
    h += '<div class="empty">' + (all.length ? 'Everything saved is marked as digested.' : 'Nothing saved yet. Use ☆ on any item.') + '</div>';
  }
  h += list.map(function (x) {
    return '<div class="saved-item' + (x.digested ? ' done' : '') + '">' +
      '<div class="meta"><span class="src">' + esc(x.source_name) + '</span><span>' + esc(x.country || '') + '</span><span>' +
      esc(x.region || '') + '</span><span>' + (x.published ? esc(new Date(x.published * 1000).toLocaleString()) : '') + '</span></div>' +
      '<a class="title" href="' + safeHref(x.link) + '" target="_blank" rel="noopener">' + esc(x.title) + '</a>' +
      (x.summary ? '<div class="sum open">' + esc(x.summary) + '</div>' : '') +
      '<textarea placeholder="Your note: why it matters, which motion or argument it feeds…" data-note="' + esc(x.id) + '">' +
      esc(x.note || '') + '</textarea>' +
      '<div class="toolbar"><label class="btn"><input type="checkbox" data-dig="' + esc(x.id) + '"' + (x.digested ? ' checked' : '') +
      '> Digested</label><button class="btn" data-unsave="' + esc(x.id) + '">Remove</button></div></div>';
  }).join('');
  $('#saved').innerHTML = h;
}

function renderSources() {
  var rank = { error: 0, pending: 1, discovered: 2, ok: 3 };
  var L = S.sourceList.slice().sort(function (a, b) {
    return (rank[a.status] - rank[b.status]) || a.region.localeCompare(b.region) || a.name.localeCompare(b.name);
  });
  var c = { ok: 0, discovered: 0, error: 0, pending: 0 };
  L.forEach(function (s) { c[s.status] = (c[s.status] || 0) + 1; });
  var label = { ok: 'ok', discovered: 'auto-found', error: 'failing', pending: 'not fetched yet' };
  var h = '<div class="toolbar">' + c.ok + ' ok · ' + c.discovered + ' auto-found · ' + c.error + ' failing' +
    (c.pending ? ' · ' + c.pending + ' not fetched yet' : '') + '</div>';
  h += '<table><thead><tr><th>Source</th><th>Region</th><th>Status</th><th>Items</th><th>Last OK</th><th>Detail</th></tr></thead><tbody>';
  h += L.map(function (s) {
    return '<tr><td>' + esc(s.name) + '<div class="meta">' + esc(s.country || '') + ' · ' + esc(s.lang || '') + '</div></td>' +
      '<td>' + esc(s.region) + '</td><td class="st-' + s.status + '">' + label[s.status] + '</td><td>' + (s.count || 0) + '</td>' +
      '<td>' + (s.last_ok ? (ago(s.last_ok) === 'now' ? 'just now' : ago(s.last_ok) + ' ago') : 'never') + '</td>' +
      '<td class="detail">' + esc(s.status === 'error' ? s.error : s.feed) + '</td></tr>';
  }).join('');
  $('#sources').innerHTML = h + '</tbody></table>';
}

function setView(v) {
  S.view = v;
  setState({ view: v });
  ['feed', 'saved', 'sources'].forEach(function (x) {
    $('#' + x).hidden = x !== v;
    $('#v-' + x).classList.toggle('on', x === v);
  });
  $('#filters').hidden = v !== 'feed';
  $('#banner').style.display = 'none';
  if (v === 'feed') renderFeed();
  if (v === 'saved') loadSaved().then(renderSaved).catch(function () { toast('Server not reachable'); });
  if (v === 'sources') renderSources();
  window.scrollTo(0, 0);
}

/* ------------------------------------------------------------ polling */

async function tick() {
  clearTimeout(S.timer);
  var wasRunning = S.running;
  try {
    await loadSources();
    var added = await loadItems();
    var applied = false;
    if (S.booting || (S.applyAfterRun && wasRunning && !S.running)) {
      S.cutoff = S.maxSeen;
      applied = true;
      if (!S.running) { S.booting = false; S.applyAfterRun = false; }
    }
    renderStatus();
    if (S.view === 'feed') {
      if (applied) renderFeed();
      else if (added) renderFeed(false);
      updateTimes();
    } else if (S.view === 'sources') {
      renderSources();
    }
  } catch (e) {
    $('#status').textContent = 'Cannot reach the News Central server. Is its Terminal window still open?';
  }
  S.timer = setTimeout(tick, S.running ? 4000 : 60000);
}

async function copyMd() {
  try {
    var txt = await fetchExportMd(true);
    try {
      await navigator.clipboard.writeText(txt);
    } catch (e) {
      var ta = document.createElement('textarea');
      ta.value = txt;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast('Copied. Paste it into a chat with Claude.');
  } catch (e) {
    toast('Could not copy: ' + e.message);
  }
}

async function toggleSave(id, btn) {
  var on = S.saved.has(id);
  try {
    if (on) {
      await postUnsave(id);
    } else {
      await postSave(id);
    }
    if (on) { S.saved.delete(id); } else { S.saved.set(id, { id: id }); }
    btn.classList.toggle('on', !on);
    btn.textContent = on ? '☆' : '★';
    updateSavedCount();
    if (!on) toast('Saved for later');
  } catch (e) {
    toast('Could not save: ' + e.message);
  }
}

/* ------------------------------------------------------------ events */

function wireEvents() {
  document.addEventListener('click', async function (e) {
    var t = e.target;
    var view = t.closest('[data-view]');
    if (view) { setView(view.dataset.view); return; }
    if (t.closest('#refresh')) {
      try {
        var r = await postRefresh();
        toast(r.started ? 'Fetching every source now…' : 'A fetch is already running…');
        S.running = true; S.applyAfterRun = true;
        renderStatus();
        clearTimeout(S.timer);
        S.timer = setTimeout(tick, 2000);
      } catch (err) { toast('Server not reachable'); }
      return;
    }
    var chip = t.closest('[data-region]');
    if (chip) {
      S.region = chip.dataset.region; S.country = ''; S.scope = '';
      S._ignoreState = true;
      setState({ region: S.region, country: '', scope: '' });
      S._ignoreState = false;
      pref.set('region', S.region); S.shown = PAGE; renderFeed(); window.scrollTo(0, 0); return;
    }
    if (t.closest('[data-clearsrc]')) { S.src = ''; S.shown = PAGE; renderFeed(); return; }
    var src = t.closest('#feed [data-src]');
    if (src) { S.src = src.dataset.src; S.shown = PAGE; renderFeed(); window.scrollTo(0, 0); return; }
    if (t.closest('#more')) { S.shown += PAGE; renderFeed(); return; }
    if (t.closest('#banner')) { S.cutoff = S.maxSeen; S.shown = PAGE; renderFeed(); window.scrollTo(0, 0); return; }
    var star = t.closest('[data-star]');
    if (star) { toggleSave(star.dataset.star, star); return; }
    var sum = t.closest('#feed .sum');
    if (sum) { sum.classList.toggle('open'); return; }
    if (t.closest('#copy-md')) { copyMd(); return; }
    var un = t.closest('[data-unsave]');
    if (un) {
      try { await postUnsave(un.dataset.unsave); await loadSaved(); renderSaved(); }
      catch (err) { toast('Server not reachable'); }
    }
  });

  document.addEventListener('change', async function (e) {
    var t = e.target;
    if (t.id === 'win') { S.win = +t.value; setState({ win: S.win }); pref.set('win', S.win); S.shown = PAGE; renderFeed(); }
    else if (t.id === 'kind') { S.kind = t.value; setState({ kind: S.kind }); pref.set('kind', S.kind); S.shown = PAGE; renderFeed(); }
    else if (t.id === 'hidesports') { S.hideSports = t.checked; setState({ hideSports: S.hideSports }); pref.set('hideSports', S.hideSports); S.shown = PAGE; renderFeed(); }
    else if (t.id === 'hidestate') { S.hideState = t.checked; setState({ hideState: S.hideState }); pref.set('hideState', S.hideState); S.shown = PAGE; renderFeed(); }
    else if (t.id === 'show-dig') { S.showDigested = t.checked; setState({ showDigested: S.showDigested }); pref.set('showDigested', S.showDigested); renderSaved(); }
    else if (t.dataset && t.dataset.dig) {
      var id = t.dataset.dig;
      try {
        await postSavedUpdate(id, { digested: t.checked });
        var x = S.saved.get(id);
        if (x) x.digested = t.checked ? 1 : 0;
        renderSaved();
      } catch (err) { toast('Server not reachable'); }
    }
  });

  var qTimer = null;
  var noteTimers = {};
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (t.id === 'q') {
      clearTimeout(qTimer);
      qTimer = setTimeout(function () { S.q = t.value; setState({ q: S.q }); S.shown = PAGE; renderFeed(); }, 150);
    } else if (t.dataset && t.dataset.note) {
      var id = t.dataset.note;
      clearTimeout(noteTimers[id]);
      noteTimers[id] = setTimeout(async function () {
        try {
          await postSavedUpdate(id, { note: t.value });
          var x = S.saved.get(id);
          if (x) x.note = t.value;
        } catch (err) { toast('Note not saved: server not reachable'); }
      }, 600);
    }
  });

  document.addEventListener('keydown', function (e) {
    var tag = (e.target.tagName || '').toLowerCase();
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      if (e.key === 'Escape' && e.target.id === 'q') { e.target.value = ''; S.q = ''; setState({ q: '' }); renderFeed(); e.target.blur(); }
      return;
    }
    if (e.key === '/') { e.preventDefault(); if (S.view !== 'feed') setView('feed'); $('#q').focus(); }
    else if (e.key === 'r') { $('#refresh').click(); }
  });
}

/* ------------------------------------------------------------ start */

export function initReader() {
  $('#win').value = String(S.win);
  $('#kind').value = S.kind;
  $('#hidestate').checked = S.hideState;
  $('#hidesports').checked = S.hideSports;
  wireEvents();
  loadSaved().catch(function () {}).then(tick);
}
