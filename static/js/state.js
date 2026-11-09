'use strict';
// Shared state for cross-module communication (reader <-> map, spine, etc.).
// Only the fields that future modules need are kept here.
// The reader keeps its own full S object and mirrors these fields here.

export var state = {
  region: 'All',
  country: '',
  scope: '',
  win: 24,
  kind: 'all',
  hideState: false,
  hideSports: true,
  showDigested: false,
  view: 'feed',
  q: ''
};

var listeners = [];

export function setState(patch) {
  var key;
  for (key in patch) {
    if (Object.prototype.hasOwnProperty.call(patch, key)) {
      state[key] = patch[key];
    }
  }
  for (var i = 0; i < listeners.length; i++) {
    listeners[i](state);
  }
}

export function onState(fn) {
  listeners.push(fn);
}
