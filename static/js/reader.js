'use strict';
import { fetchSources, fetchItems, fetchSaved, postRefresh, postSave, postUnsave, postSavedUpdate, fetchExportMd, fetchStats, fetchSearch } from './api.js';
import { setState, onState } from './state.js';

const REGION_ORDER = ['Spain', 'Europe & EU', 'Eurasia', 'Middle East & N. Africa', 'Africa', 'Latin America',
  'North America', 'South Asia', 'East Asia', 'Southeast Asia', 'Oceania', 'Global'];
const PAGE = 150;
const LIVE = 72;   // hours kept in memory; matches LIVE_WINDOW_HOURS on the server
var ARCHIVE_PAGE = 50;
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
  items: new Map(), stats: null, sources: {}, sourceList: [], saved: new Map(),
  region: pref.get('region', 'All'), win: pref.get('win', 24), kind: pref.get('kind', 'all'),
  hideState: pref.get('hideState', false), hideSports: pref.get('hideSports', true), showDigested: pref.get('showDigested', false),
  q: '', src: '', country: '', scope: '', shown: PAGE, cutoff: 0, maxSeen: 0, loadedFrom: null, view: 'feed',
  running: false, updated: 0, sourcesError: '', booting: true, applyAfterRun: false, timer: null,
  archFrom: '', archTo: '',
  readLangs: ['es', 'en'], showLangs: pref.get('showLangs', false), truncated: false,
  worker: [], workerEnabled: true, trSeen: 0, loaded: false,
  archive: { key: null, items: [], total: 0, loading: false, error: '' },
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
  var regionChanged = false;
  if (st.region !== undefined && st.region !== S.region) { S.region = st.region; regionChanged = true; changed = true; }
  if (st.country !== undefined && st.country !== S.country) { S.country = st.country; changed = true; }
  if (st.scope !== undefined && st.scope !== S.scope) { S.scope = st.scope; changed = true; }
  if (!changed) return;
  S.src = '';  // a new place selection drops the single-outlet filter
  if (regionChanged) pref.set('region', S.region);
  S.shown = PAGE;
  if (S.view === 'feed' || S.view === 'map') { renderFeed(); window.scrollTo(0, 0); }
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
  if (d.read_langs && d.read_langs.length) S.readLangs = d.read_langs;
  S.worker = d.worker || [];
  S.workerEnabled = d.worker_enabled !== false;
}

// Text the live-feed search matches: original, translation, outlet and country.
function itemText(it, src) {
  var tr = it.tr || {};
  return norm(it.title + ' ' + (it.summary || '') + ' ' + (tr.title || '') + ' ' + (tr.summary || '') + ' ' +
              (src ? src.name + ' ' + (src.country || '') : ''));
}

// Translations that finished after their item reached the page: update the item in place.
function applyTranslated(translated) {
  var changed = 0;
  translated = translated || {};
  for (var tid in translated) {
    if (!Object.prototype.hasOwnProperty.call(translated, tid)) continue;
    var known = S.items.get(tid);
    if (!known) continue;
    known.tr = translated[tid];
    known._text = itemText(known, S.sources[known.s]);
    changed++;
  }
  return changed;
}

function mergeItems(d) {
  var added = 0;
  for (var i = 0; i < d.items.length; i++) {
    var it = d.items[i];
    if (S.items.has(it.id)) continue;
    var src = S.sources[it.s];
    it._sport = isSportOrCeleb(it.link);
    it._text = itemText(it, src);
    S.items.set(it.id, it);
    if (it.fs > S.maxSeen) S.maxSeen = it.fs;
    added++;
  }
  return added;
}

function pruneItems() {
  var minT = Date.now() / 1000 - LIVE * 3600;
  for (var entry of S.items) {
    var id = entry[0], item = entry[1];
    if (item.t < minT && item.fs < minT) S.items.delete(id);
  }
}

async function loadItems() {
  var since;
  if (S.loadedFrom === null) {
    // First load: only the selected window (Archive searches server-side; an hour is enough for the poller).
    since = Date.now() / 1000 - (S.win === 'archive' ? 1 : S.win) * 3600;
    S.loadedFrom = since;
  } else {
    since = S.maxSeen || S.loadedFrom;
  }
  // tr_since is sent only on polls (not on the first load), and S.trSeen only advances here, so a
  // translation is never skipped; ensureWindow() below neither sends nor moves it.
  var d = await fetchItems(since, S.loaded ? S.trSeen : 0);
  S.loaded = true;
  var added = mergeItems(d);
  added += applyTranslated(d.translated);
  if (d.tr_seen) S.trSeen = d.tr_seen;
  S.running = d.running; S.updated = d.updated;
  S.truncated = !!d.truncated;
  pruneItems();
  return added;
}

// The user widened the window past what was loaded: fetch the older part once.
async function ensureWindow() {
  if (S.win === 'archive' || S.loadedFrom === null) return;
  var need = Date.now() / 1000 - S.win * 3600;
  if (need >= S.loadedFrom) return;
  var d = await fetchItems(need);
  mergeItems(d);
  S.loadedFrom = need;
  if (d.truncated) S.truncated = true;
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
  var total = 0, pending = 0, hidden = 0;
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
    // A country or scope selection already passed its own filter above; the source's
    // editorial region (e.g. BBC World is "Global") must not exclude it again.
    var inRegion = !!S.country || !!S.scope || S.region === 'All' || src.region === S.region;
    // Languages the user does not read stay out of the feed (and out of its counts) unless asked for.
    // An item's own language wins over its source's.
    // Hidden: unread languages with no translated title yet.
    if (!S.showLangs && S.readLangs.indexOf(it.lang || src.lang) === -1 && !(it.tr && it.tr.title)) {
      if (inRegion && it.fs <= S.cutoff) hidden++;
      continue;
    }
    if (it.fs > S.cutoff) { if (inRegion) pending++; continue; }
    counts[src.region] = (counts[src.region] || 0) + 1;
    total++;
    if (inRegion) shown.push(it);
  }
  shown.sort(function (a, b) { return b.t - a.t; });
  return { shown: shown, counts: counts, total: total, pending: pending, hidden: hidden };
}

/* ------------------------------------------------------------ rendering */

function renderStatus() {
  if (S.sourcesError) { $('#status').textContent = S.sourcesError; return; }
  var L = S.sourceList;
  var ok = L.filter(function (s) { return s.status === 'ok' || s.status === 'discovered'; }).length;
  var txt = ok + '/' + L.length + ' sources';
  if (S.updated) { var a = ago(S.updated); txt += ' · updated ' + (a === 'now' ? 'just now' : a + ' ago'); }
  if (S.running) txt += ' · fetching…';
  if (S.truncated) txt += ' · too many items in the window, showing the newest';
  $('#status').textContent = txt;
}

// Title block: the Spanish title as the link, the original underneath in a smaller grey line.
function titleHTML(it) {
  var href = safeHref(it.link);
  if (it.tr && it.tr.title) {
    return '<a class="title" lang="es" href="' + href + '" target="_blank" rel="noopener">' + esc(it.tr.title) + '</a>' +
      '<div class="orig" dir="auto" lang="' + esc(it.lang || '') + '">' + esc(it.title) + '</div>';
  }
  return '<a class="title" dir="auto" href="' + href + '" target="_blank" rel="noopener">' + esc(it.title) + '</a>';
}

// Summary block: the translation, with the original summary behind a click.
function summaryHTML(it, open) {
  var cls = 'sum' + (open ? ' open' : '');
  if (it.tr && it.tr.summary) {
    return '<div class="' + cls + '" lang="es">' + esc(it.tr.summary) + '</div>' +
      (it.summary ? '<details class="orig-sum"><summary>Resumen original</summary><div dir="auto" lang="' +
                    esc(it.lang || '') + '">' + esc(it.summary) + '</div></details>' : '');
  }
  return it.summary ? '<div class="' + cls + '" dir="auto">' + esc(it.summary) + '</div>' : '';
}

function langTag(it, lang) {
  if (it.tr && (it.tr.title || it.tr.summary)) {
    return '<span class="tag" title="Machine translation (' + esc(it.tr.engine || '') + ')">traducido de ' +
      esc(lang) + '</span>';
  }
  return lang && lang !== 'en' ? '<span class="tag">' + esc(lang) + '</span>' : '';
}

function itemHTML(it, now, src) {
  src = src || S.sources[it.s];
  var d = new Date(it.t * 1000);
  var sameDay = d.toDateString() === new Date().toDateString();
  var top = sameDay ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                    : d.toLocaleDateString([], { day: 'numeric', month: 'short' });
  var tags = [];
  var lang = it.lang || src.lang;
  var tagLang = langTag(it, lang);
  if (tagLang) tags.push(tagLang);
  if (src.kind === 'analysis') tags.push('<span class="tag an">analysis</span>');
  if (src.kind === 'official') tags.push('<span class="tag an">official</span>');
  if (src.state_media) tags.push('<span class="tag state">state-controlled</span>');
  if (src.paywall) tags.push('<span class="tag">paywall</span>');
  if (lang && lang !== 'en' && lang !== 'es' && !(it.tr && it.tr.title)) {
    tags.push('<a href="https://translate.google.com/translate?sl=auto&tl=en&u=' + encodeURIComponent(it.link) +
              '" target="_blank" rel="noopener">translate</a>');
  }
  var saved = S.saved.has(it.id);
  return '<article class="item' + (now - it.t < 3600 ? ' fresh' : '') + '" data-id="' + esc(it.id) + '">' +
    '<div class="time">' + esc(top) + '<span class="ago">' + ago(it.t) + '</span></div>' +
    '<div><div class="meta"><span class="src" data-src="' + esc(it.s) + '" title="Show only this outlet">' + esc(src.name) +
    '</span><span>' + esc(src.country || '') + '</span>' + tags.join('') + '</div>' +
    titleHTML(it) + summaryHTML(it, false) + '</div>' +
    '<button class="star' + (saved ? ' on' : '') + '" data-star="' + esc(it.id) + '" title="Save for later">' +
    (saved ? '★' : '☆') + '</button></article>';
}

/* ------------------------------------------------------------ archive mode */

function dayStart(v) { return v ? new Date(v + 'T00:00:00').getTime() / 1000 : ''; }

function archiveParams() {
  var p = { q: S.q.trim(), from: dayStart(S.archFrom), to: S.archTo ? dayStart(S.archTo) + 86400 : '' };
  if (S.src) p.source = S.src;
  if (S.country) p.country = S.country;
  else if (S.scope) p.scope = S.scope;
  else if (S.region !== 'All') p.region = S.region;
  if (S.kind !== 'all') p.kind = S.kind;
  return p;
}

// The server needs a text query, a start date or an outlet. Without one there is nothing to ask.
function archiveHasCriteria(p) { return !!(p.q || p.from !== '' || p.source); }

function archiveSource(it) {
  var live = S.sources[it.s];
  if (live) return live;
  // An outlet that is no longer in sources.json: describe it from the archived row.
  return { name: it.source_name, country: it.country_iso || it.scope || '', lang: it.lang, kind: it.kind,
           state_media: false, paywall: false };
}

function loadArchive(more) {
  var A = S.archive, p = archiveParams(), key = JSON.stringify(p);
  if (more && A.loading) return;   // a second click while a page is loading would fetch the same offset
  if (!more) { A.key = key; A.items = []; A.total = 0; A.error = ''; }
  if (!archiveHasCriteria(p)) { A.loading = false; return; }
  p.limit = ARCHIVE_PAGE;
  p.offset = A.items.length;
  A.loading = true;
  fetchSearch(p).then(function (d) {
    if (A.key !== key) return;                 // the filters changed while this request was out
    A.items = A.items.concat(d.items);
    A.total = d.total;
    A.loading = false;
    renderArchive();
  }).catch(function () {
    if (A.key !== key) return;
    A.loading = false;
    A.error = 'Search failed. Is the News Central server running?';
    renderArchive();
  });
}

function renderArchive() {
  var A = S.archive, p = archiveParams();
  // loadArchive sets A.key synchronously, so a second render with the same filters does not search again.
  if (A.key !== JSON.stringify(p)) loadArchive(false);

  var chips = '';
  if (S.src) {
    var only = S.sources[S.src] ? S.sources[S.src].name : S.src;
    chips += '<button class="chip on" data-clearsrc="1">Only ' + esc(only) + ' ✕</button>';
  }
  chips += ['All'].concat(regionsPresent()).map(function (g) {
    return '<button class="chip' + (S.region === g ? ' on' : '') + '" data-region="' + esc(g) + '">' + esc(g) + '</button>';
  }).join('');
  $('#regions').innerHTML = chips;
  $('#banner').style.display = 'none';
  $('#showlangs-n').textContent = '';
  document.title = 'News Central';

  var el = $('#feed');
  if (!archiveHasCriteria(p)) {
    el.innerHTML = '<div class="empty">Type a search, pick a start date or click an outlet name to search the whole archive.</div>';
    return;
  }
  if (A.error) { el.innerHTML = '<div class="empty">' + esc(A.error) + '</div>'; return; }
  if (!A.items.length) {
    el.innerHTML = '<div class="empty">' + (A.loading ? 'Searching the archive…' : 'Nothing in the archive matches.') + '</div>';
    return;
  }
  var now = Date.now() / 1000;
  var h = '<div class="toolbar">' + A.total.toLocaleString() + ' result' + (A.total === 1 ? '' : 's') + ' in the archive</div>';
  h += A.items.map(function (it) { return itemHTML(it, now, archiveSource(it)); }).join('');
  if (A.items.length < A.total) {
    h += '<button class="btn more" id="more">' + (A.loading ? 'Loading…' : 'Show more (' + (A.total - A.items.length) + ' left)') + '</button>';
  }
  el.innerHTML = h;
}

function syncArchiveInputs() {
  var on = S.win === 'archive';
  $('#arch-from').hidden = !on;
  $('#arch-to').hidden = !on;
  $('#q').placeholder = on ? 'Search the whole archive  ( / )' : 'Search titles, summaries, outlets  ( / )';
}

function renderFeed(listToo) {
  if (S.win === 'archive') {
    // The poller calls renderFeed(false) when new live items arrive; the archive list does not depend on them.
    if (listToo !== false) renderArchive();
    return;
  }
  var r = compute();
  $('#showlangs-n').textContent = r.hidden ? '(' + r.hidden + ')' : '';
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
  if (r.pending > 0 && (S.view === 'feed' || S.view === 'map')) {
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
      esc(x.region || '') + '</span><span>' + (x.published ? esc(new Date(x.published * 1000).toLocaleString()) : '') + '</span>' +
      (x.tr ? langTag(x, x.lang) : '') + '</div>' +
      titleHTML(x) + summaryHTML(x, true) +
      '<textarea placeholder="Your note: why it matters, which motion or argument it feeds…" data-note="' + esc(x.id) + '">' +
      esc(x.note || '') + '</textarea>' +
      '<div class="toolbar"><label class="btn"><input type="checkbox" data-dig="' + esc(x.id) + '"' + (x.digested ? ' checked' : '') +
      '> Digested</label><button class="btn" data-unsave="' + esc(x.id) + '">Remove</button></div></div>';
  }).join('');
  $('#saved').innerHTML = h;
}

function pct(x) { return x === null || x === undefined ? 'no data' : (Math.round(x * 1000) / 10) + '%'; }

function duration(seconds) {
  var m = Math.round(seconds / 60);
  if (m < 60) return m + ' min';
  var h = Math.floor(m / 60);
  return h < 48 ? h + ' h ' + (m % 60) + ' min' : Math.round(h / 24) + ' days';
}

function agoText(ts) { var a = ago(ts); return a === 'now' ? 'just now' : a + ' ago'; }

// Worker states map to the existing status colours: working (ok), not working (pending), broken (error).
var WORKER_CLASS = { running: 'st-ok', idle: 'st-ok', unavailable: 'st-pending', stopped: 'st-pending', error: 'st-error' };

function workerHTML() {
  if (!S.workerEnabled) {
    return '<div class="toolbar">Background worker: off (--no-worker or "worker": false in config.json)</div>';
  }
  if (!S.worker.length) return '<div class="toolbar">Background worker: no report yet</div>';
  return S.worker.map(function (w) {
    var parts = ['Worker job ' + esc(w.job) + ': <span class="' + (WORKER_CLASS[w.state] || 'st-pending') + '">' +
                 esc(w.state) + '</span>'];
    if (w.last_batch) parts.push('last batch ' + agoText(w.last_batch));
    parts.push((w.done_last_hour || 0).toLocaleString() + ' done in the last hour');
    if (w.updated) parts.push('reported ' + agoText(w.updated));
    var h = '<div class="toolbar">' + parts.join(' · ') + '</div>';
    if (w.last_error) h += '<div class="toolbar detail">' + esc(w.last_error) + '</div>';
    return h;
  }).join('');
}

function translationHTML(tr) {
  if (!tr) return '';
  return '<div class="toolbar">Translation into Spanish: ' + tr.pending_titles.toLocaleString() + ' titles and ' +
    tr.pending_summaries.toLocaleString() + ' summaries waiting · ' + tr.done_24h.toLocaleString() +
    ' done in the last 24 h' + (tr.failed ? ' · ' + tr.failed.toLocaleString() + ' given up after 3 failures' : '') +
    '</div>';
}

function collectionHTML() {
  var st = S.stats;
  if (!st) return '';
  var read = st.read_langs || [];
  var days = {}, order = [];
  st.volume.forEach(function (v) {
    if (!days[v.day]) { days[v.day] = { read: 0, other: 0, chars: 0, langs: [] }; order.push(v.day); }
    var d = days[v.day];
    if (read.indexOf(v.lang) !== -1) {
      d.read += v.items;
    } else {
      d.other += v.items; d.chars += v.chars;
      d.langs.push((v.lang || 'unknown') + ' ' + v.items);
    }
  });
  var h = '<h2>Collection</h2><div class="toolbar">Uptime last 7 days: ' + esc(pct(st.uptime_7d)) +
    ' · last 30 days: ' + esc(pct(st.uptime_30d)) + ' · ' + st.gaps.length + ' gap' + (st.gaps.length === 1 ? '' : 's') +
    (st.longest_gap ? ' · longest ' + esc(duration(st.longest_gap)) : '') + '</div>';
  h += workerHTML() + translationHTML(st.translation);
  h += '<table class="volume"><thead><tr><th>Day (UTC)</th><th>Read (' + esc(read.join(', ')) +
    ')</th><th>Other languages</th><th>Characters to translate</th><th>Breakdown</th></tr></thead><tbody>';
  h += order.slice(0, 14).map(function (day) {
    var d = days[day];
    return '<tr><td>' + esc(day) + '</td><td class="count">' + d.read + '</td><td class="count">' + d.other +
      '</td><td class="count">' + d.chars.toLocaleString() + '</td><td class="detail">' + esc(d.langs.join(' · ')) + '</td></tr>';
  }).join('');
  return h + '</tbody></table><h2>Sources</h2>';
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
  $('#sources').innerHTML = collectionHTML() + h + '</tbody></table>';
}

function setView(v) {
  S.view = v;
  setState({ view: v });
  // The feed section is visible in both 'feed' and 'map' views.
  // map.js drives #map visibility via its own onState subscriber.
  var feedVisible = (v === 'feed' || v === 'map');
  $('#feed').hidden = !feedVisible;
  $('#saved').hidden = v !== 'saved';
  $('#sources').hidden = v !== 'sources';
  // Toggle active class on all four tab buttons.
  ['feed', 'map', 'saved', 'sources'].forEach(function (x) {
    var btn = $('#v-' + x);
    if (btn) btn.classList.toggle('on', x === v);
  });
  $('#filters').hidden = !feedVisible;
  $('#banner').style.display = 'none';
  if (feedVisible) renderFeed();
  if (v === 'saved') loadSaved().then(renderSaved).catch(function () { toast('Server not reachable'); });
  if (v === 'sources') {
    renderSources();
    fetchStats(30).then(function (d) { S.stats = d; if (S.view === 'sources') renderSources(); })
      .catch(function () { toast('Server not reachable'); });
  }
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
    if (S.view === 'feed' || S.view === 'map') {
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
    if (t.closest('#more')) {
      if (S.win === 'archive') { loadArchive(true); renderArchive(); return; }
      S.shown += PAGE; renderFeed(); return;
    }
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
    if (t.id === 'win') {
      S.win = t.value === 'archive' ? 'archive' : +t.value;
      setState({ win: S.win }); pref.set('win', S.win); S.shown = PAGE;
      syncArchiveInputs(); renderFeed();
      ensureWindow().then(function () { if (S.win !== 'archive') renderFeed(false); }).catch(function () { toast('Server not reachable'); });
    }
    else if (t.id === 'arch-from') { S.archFrom = t.value; renderFeed(); }
    else if (t.id === 'arch-to') { S.archTo = t.value; renderFeed(); }
    else if (t.id === 'kind') { S.kind = t.value; setState({ kind: S.kind }); pref.set('kind', S.kind); S.shown = PAGE; renderFeed(); }
    else if (t.id === 'hidesports') { S.hideSports = t.checked; setState({ hideSports: S.hideSports }); pref.set('hideSports', S.hideSports); S.shown = PAGE; renderFeed(); }
    else if (t.id === 'showlangs') { S.showLangs = t.checked; pref.set('showLangs', S.showLangs); S.shown = PAGE; renderFeed(); }
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
      qTimer = setTimeout(function () { S.q = t.value; setState({ q: S.q }); S.shown = PAGE; renderFeed(); }, S.win === 'archive' ? 400 : 150);
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
  syncArchiveInputs();
  $('#kind').value = S.kind;
  $('#hidestate').checked = S.hideState;
  $('#hidesports').checked = S.hideSports;
  $('#showlangs').checked = S.showLangs;
  wireEvents();
  loadSaved().catch(function () {}).then(tick);
}
