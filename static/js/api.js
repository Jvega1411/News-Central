'use strict';
// All fetch wrappers for the News Central API.

var POST_HEADERS = { 'Content-Type': 'application/json', 'X-NewsCentral': '1' };

export function getJSON(path) {
  return fetch(path, { cache: 'no-store' }).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  });
}

export function post(path, body) {
  return fetch(path, {
    method: 'POST',
    headers: POST_HEADERS,
    body: JSON.stringify(body || {})
  }).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  });
}

export function fetchSources() {
  return getJSON('/api/sources');
}

export function fetchItems(since) {
  return getJSON('/api/items?since=' + since);
}

export function fetchSaved() {
  return getJSON('/api/saved');
}

export function postRefresh() {
  return post('/api/refresh');
}

export function postSave(id) {
  return post('/api/save', { id: id });
}

export function postUnsave(id) {
  return post('/api/unsave', { id: id });
}

export function postSavedUpdate(id, fields) {
  var body = { id: id };
  if (fields.note !== undefined) body.note = fields.note;
  if (fields.digested !== undefined) body.digested = fields.digested;
  return post('/api/saved/update', body);
}

export function fetchGeo(since) {
  return getJSON('/api/geo?since=' + (since || 0));
}

export function fetchExportMd(onlyUndigested) {
  var url = '/api/export.md' + (onlyUndigested ? '?only=undigested' : '');
  return fetch(url, { cache: 'no-store' }).then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.text();
  });
}
