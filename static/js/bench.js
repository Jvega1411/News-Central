'use strict';
// Review page for the translation benchmark: original, every engine's output, Claude's scores,
// and a "disagree" mark per score.
import { getJSON, post } from './api.js';

var B = { data: null, all: false };

function $(sel) { return document.querySelector(sel); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; });
}

function load(date) {
  getJSON('/api/bench/translate' + (date ? '?date=' + encodeURIComponent(date) : '')).then(function (d) {
    B.data = d;
    render();
  }).catch(function (e) { $('#bar').textContent = 'Could not load the benchmark: ' + e.message; });
}

function scoreText(s) {
  return s ? 'adequacy ' + s.adequacy + ' · fluency ' + s.fluency + (s.note ? ' · ' + s.note : '') : 'not scored';
}

function cellHTML(it, engine) {
  var tr = it.translations[engine] || { text: '', error: 'not run' };
  var s = it.scores[engine];
  var rv = it.review[engine] || {};
  var h = tr.error ? '<div class="err">' + esc(tr.error) + (tr.text ? ': ' + esc(tr.text) : '') + '</div>'
                   : '<div lang="es">' + esc(tr.text) + '</div>';
  h += '<span class="meta score">' + esc(scoreText(s)) + (tr.seconds ? ' · ' + esc(tr.seconds) + ' s' : '') + '</span>';
  if (s) {
    h += '<label class="meta"><input type="checkbox" data-disagree="' + esc(it.key) + '" data-engine="' + esc(engine) + '"' +
      (rv.disagree ? ' checked' : '') + '> I disagree with this score</label>' +
      '<input type="text" placeholder="Why (optional)" data-note="' + esc(it.key) + '" data-engine="' + esc(engine) +
      '" value="' + esc(rv.note || '') + '">';
  }
  return '<td>' + h + '</td>';
}

function render() {
  var d = B.data;
  if (!d.dates.length) {
    $('#bar').textContent = 'No benchmark yet. Run: py tools/bench_translate.py run';
    $('#bench').innerHTML = '';
    return;
  }
  $('#bar').innerHTML = '<label>Round <select id="date">' + d.dates.map(function (x) {
      return '<option' + (x === d.date ? ' selected' : '') + '>' + esc(x) + '</option>';
    }).join('') + '</select></label><span>' + esc(d.machine) + '</span><span>' +
    (d.has_scores ? d.sample.length + ' scored items to review' : 'not scored yet') + '</span>' +
    '<label><input type="checkbox" id="all"' + (B.all ? ' checked' : '') + '> Show every item (' + d.items.length + ')</label>';
  var inSample = {};
  d.sample.forEach(function (k) { inSample[k] = true; });
  var list = (B.all || !d.has_scores) ? d.items : d.items.filter(function (it) { return inSample[it.key]; });
  var h = '<table><thead><tr><th>Original</th>' + d.engines.map(function (e) { return '<th>' + esc(e) + '</th>'; }).join('') +
    '</tr></thead><tbody>';
  h += list.map(function (it) {
    return '<tr><td><div class="orig" dir="auto" lang="' + esc(it.lang) + '">' + esc(it.text) + '</div><span class="meta">' +
      esc(it.lang) + ' · ' + esc(it.field) + ' · ' + esc(it.source_id) + '</span></td>' +
      d.engines.map(function (e) { return cellHTML(it, e); }).join('') + '</tr>';
  }).join('');
  $('#bench').innerHTML = h + '</tbody></table>';
}

function saveReview(key, engine) {
  var sel = '[data-engine="' + CSS.escape(engine) + '"]';
  var box = document.querySelector('[data-disagree="' + CSS.escape(key) + '"]' + sel);
  var note = document.querySelector('[data-note="' + CSS.escape(key) + '"]' + sel);
  var body = { date: B.data.date, key: key, engine: engine, disagree: !!(box && box.checked), note: note ? note.value : '' };
  post('/api/bench/review', body).then(function () {
    B.data.items.forEach(function (it) { if (it.key === key) it.review[engine] = { disagree: body.disagree, note: body.note }; });
  }).catch(function (e) { $('#bar').insertAdjacentText('beforeend', ' · not saved: ' + e.message); });
}

document.addEventListener('change', function (e) {
  var t = e.target;
  if (t.id === 'date') { load(t.value); return; }
  if (t.id === 'all') { B.all = t.checked; render(); return; }
  if (t.dataset && t.dataset.disagree) saveReview(t.dataset.disagree, t.dataset.engine);
});

var timers = {};
document.addEventListener('input', function (e) {
  var t = e.target;
  if (!(t.dataset && t.dataset.note)) return;
  var id = t.dataset.note + '|' + t.dataset.engine;
  clearTimeout(timers[id]);
  timers[id] = setTimeout(function () { saveReview(t.dataset.note, t.dataset.engine); }, 600);
});

load('');
