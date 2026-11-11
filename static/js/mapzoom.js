'use strict';
// Zoom and pan for the world map. Zoom works by changing the SVG viewBox so
// the vector stays sharp. The geometry is pure (plain {x, y, w, h} objects);
// initZoom wires it to the DOM. Zoom state is local, never in shared state.

export var MAX_ZOOM = 12;
var STEP = 2;
var DRAG_PX = 4;
var EPS = 1e-9;

// --- pure geometry ---

function copyView(v) {
  return { x: v.x, y: v.y, w: v.w, h: v.h };
}

// Clamp width to [home.w / MAX_ZOOM, home.w], keep the home aspect ratio, and
// keep the view inside the home extent.
export function clampView(view, home) {
  var w = Math.min(home.w, Math.max(home.w / MAX_ZOOM, view.w));
  if (w >= home.w - EPS) return copyView(home);
  var h = w * home.h / home.w;
  var x = Math.min(home.x + home.w - w, Math.max(home.x, view.x));
  var y = Math.min(home.y + home.h - h, Math.max(home.y, view.y));
  return { x: x, y: y, w: w, h: h };
}

// Zoom by `factor` (>1 zooms in) keeping the point at fractions (fx, fy) of
// the current view fixed on screen.
export function zoomAt(view, home, factor, fx, fy) {
  var w = Math.min(home.w, Math.max(home.w / MAX_ZOOM, view.w / factor));
  var h = w * home.h / home.w;
  var px = view.x + fx * view.w;
  var py = view.y + fy * view.h;
  return clampView({ x: px - fx * w, y: py - fy * h, w: w, h: h }, home);
}

// Pan by (dx, dy) in view units.
export function panBy(view, home, dx, dy) {
  return clampView({ x: view.x + dx, y: view.y + dy, w: view.w, h: view.h }, home);
}

export function zoomLevel(view, home) {
  return home.w / view.w;
}

// --- DOM wiring ---

function parseHome(svg) {
  var vb = (svg.getAttribute('viewBox') || '').split(/[\s,]+/);
  var n = [];
  for (var i = 0; i < vb.length; i++) {
    if (vb[i] !== '') n.push(parseFloat(vb[i]));
  }
  if (n.length !== 4 || isNaN(n[0] + n[1] + n[2] + n[3]) || n[2] <= 0 || n[3] <= 0) return null;
  return { x: n[0], y: n[1], w: n[2], h: n[3] };
}

function makeButton(label, text, extraClass) {
  var b = document.createElement('button');
  b.type = 'button';
  b.className = extraClass;
  b.setAttribute('aria-label', label);
  b.title = label;
  b.textContent = text;
  return b;
}

// Returns { isDragging, reset } or null when the viewBox is unusable.
export function initZoom(svg, container) {
  var home = parseHome(svg);
  if (!home) return null;
  var view = copyView(home);

  svg.style.touchAction = 'none';

  var bar = document.createElement('div');
  bar.className = 'map-zoom';
  var btnIn = makeButton('Zoom in', '+', 'map-zoom-btn');
  var btnOut = makeButton('Zoom out', '−', 'map-zoom-btn');
  var btnReset = makeButton('Reset zoom', 'Reset', 'map-zoom-btn');
  bar.appendChild(btnIn);
  bar.appendChild(btnOut);
  bar.appendChild(btnReset);
  container.appendChild(bar);

  var pointers = {};      // pointerId -> {x, y}
  var count = 0;
  var downX = 0, downY = 0;
  var dragging = false;   // moved past the threshold while pressed
  var movedSincePress = false;
  var suppressClick = false;
  var pinchDist = 0;

  function apply(v) {
    view = v;
    svg.setAttribute('viewBox', v.x + ' ' + v.y + ' ' + v.w + ' ' + v.h);
    var atMin = v.w >= home.w - EPS;
    var atMax = v.w <= home.w / MAX_ZOOM + EPS;
    btnIn.disabled = atMax;
    btnOut.disabled = atMin;
    btnReset.disabled = atMin;
    if (atMin) svg.classList.remove('zoomed'); else svg.classList.add('zoomed');
  }

  function fractions(cx, cy) {
    var r = svg.getBoundingClientRect();
    if (!r.width || !r.height) return { fx: 0.5, fy: 0.5, r: r };
    return {
      fx: Math.min(1, Math.max(0, (cx - r.left) / r.width)),
      fy: Math.min(1, Math.max(0, (cy - r.top) / r.height)),
      r: r
    };
  }

  function zoomFactorAt(factor, cx, cy) {
    var f = fractions(cx, cy);
    apply(zoomAt(view, home, factor, f.fx, f.fy));
  }

  function zoomCentre(factor) {
    apply(zoomAt(view, home, factor, 0.5, 0.5));
  }

  function endDrag() {
    if (dragging) {
      dragging = false;
      svg.classList.remove('dragging');
    }
  }

  function pairInfo() {
    var ids = Object.keys(pointers);
    var a = pointers[ids[0]], b = pointers[ids[1]];
    return {
      d: Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)),
      mx: (a.x + b.x) / 2,
      my: (a.y + b.y) / 2
    };
  }

  btnIn.addEventListener('click', function () { zoomCentre(STEP); });
  btnOut.addEventListener('click', function () { zoomCentre(1 / STEP); });
  btnReset.addEventListener('click', function () { apply(copyView(home)); });

  svg.addEventListener('wheel', function (e) {
    e.preventDefault();
    var dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 16;
    else if (e.deltaMode === 2) dy *= 100;
    zoomFactorAt(Math.exp(-dy * 0.0015), e.clientX, e.clientY);
  }, { passive: false });

  svg.addEventListener('dblclick', function (e) {
    e.preventDefault();
    zoomFactorAt(STEP, e.clientX, e.clientY);
  });

  svg.addEventListener('pointerdown', function (e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (count === 0) {
      movedSincePress = false;
      suppressClick = false;
      downX = e.clientX;
      downY = e.clientY;
    }
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    count++;
    if (count === 2) {
      // A second finger makes this a pinch: never a click.
      movedSincePress = true;
      pinchDist = pairInfo().d;
      if (!dragging) {
        dragging = true;
        svg.classList.add('dragging');
      }
    }
  });

  svg.addEventListener('pointermove', function (e) {
    var prev = pointers[e.pointerId];
    if (!prev) return;
    var r = svg.getBoundingClientRect();
    if (!r.width || !r.height) return;

    if (count >= 2) {
      var before = pairInfo();
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var after = pairInfo();
      var v = view;
      if (pinchDist > 0 && after.d > 0) {
        var f = fractions(after.mx, after.my);
        v = zoomAt(v, home, after.d / pinchDist, f.fx, f.fy);
      }
      v = panBy(v, home,
        -(after.mx - before.mx) * v.w / r.width,
        -(after.my - before.my) * v.h / r.height);
      pinchDist = after.d;
      apply(v);
      return;
    }

    if (!dragging) {
      var mdx = e.clientX - downX, mdy = e.clientY - downY;
      if (Math.sqrt(mdx * mdx + mdy * mdy) <= DRAG_PX) return;
      dragging = true;
      movedSincePress = true;
      svg.classList.add('dragging');
      // Capture only once it is a real drag, so a plain click keeps its
      // original target (the country path).
      try { svg.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    }
    pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    apply(panBy(view, home,
      -(e.clientX - prev.x) * view.w / r.width,
      -(e.clientY - prev.y) * view.h / r.height));
  });

  function release(e) {
    if (!pointers[e.pointerId]) return;
    delete pointers[e.pointerId];
    count = Object.keys(pointers).length;
    if (count === 1) {
      // Pinch ended with one finger left: resume as a pan from here.
      var id = Object.keys(pointers)[0];
      downX = pointers[id].x;
      downY = pointers[id].y;
    }
    if (count === 0) {
      if (movedSincePress) {
        suppressClick = true;
        setTimeout(function () { suppressClick = false; }, 60);
      }
      endDrag();
    }
  }
  svg.addEventListener('pointerup', release);
  svg.addEventListener('pointercancel', release);

  // Capture phase on the container so the click that follows a drag never
  // reaches the country click handler.
  container.addEventListener('click', function (e) {
    if (suppressClick) {
      suppressClick = false;
      e.stopPropagation();
      e.preventDefault();
    }
  }, true);

  apply(view);

  return {
    isDragging: function () { return dragging; },
    reset: function () { apply(copyView(home)); }
  };
}
