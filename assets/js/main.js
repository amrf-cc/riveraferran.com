// Mobile nav toggle
(function () {
  var btn = document.querySelector('.nav-toggle');
  var nav = document.querySelector('nav.main');
  if (btn && nav) {
    btn.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }
})();

// Filters (Work by group, Reels by format): pills are plain #tag links, so
// without JS every item stays visible. With JS the hash (#food-beverage ...)
// selects the items to show, which also makes work.html#food-beverage links
// from other pages land pre-filtered. data-filter-scope names the container
// whose [data-tags] children are filtered (default: the Work grid). Group
// headings carry data-tags plus data-filter-heading: they follow their group
// but never count as a shown item.
(function () {
  var bar = document.querySelector('[data-filter-group]');
  if (!bar) return;
  var scope = document.querySelector(bar.getAttribute('data-filter-scope') || '.work-grid');
  if (!scope) return;
  var links = Array.prototype.slice.call(bar.querySelectorAll('a[data-filter]'));
  var items = Array.prototype.slice.call(scope.querySelectorAll('[data-tags]'));
  var empty = document.querySelector('.filter-empty');
  var known = links.map(function (a) { return a.getAttribute('data-filter'); });
  function apply() {
    var tag = decodeURIComponent((window.location.hash || '').replace(/^#/, ''));
    if (known.indexOf(tag) === -1) tag = 'all';
    var shown = 0;
    items.forEach(function (el) {
      var tags = (el.getAttribute('data-tags') || '').split(/\s+/);
      var on = tag === 'all' || tags.indexOf(tag) !== -1;
      el.hidden = !on;
      if (on && !el.hasAttribute('data-filter-heading')) shown++;
      // A reel filtered out of view stops playing.
      if (!on) Array.prototype.forEach.call(el.querySelectorAll('video'), function (v) { v.pause(); });
    });
    links.forEach(function (a) {
      var on = a.getAttribute('data-filter') === tag;
      a.classList.toggle('active', on);
      a.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    if (empty) empty.hidden = shown !== 0;
  }
  window.addEventListener('hashchange', apply);
  apply();
})();

// Reel cards: a poster + play button until clicked, then a real <video>.
// Only one reel plays at a time.
(function () {
  function pauseOthers(current) {
    Array.prototype.forEach.call(document.querySelectorAll('video'), function (v) {
      if (v !== current && !v.paused) v.pause();
    });
  }
  document.addEventListener('play', function (e) {
    if (e.target && e.target.tagName === 'VIDEO') pauseOthers(e.target);
  }, true);
  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest ? e.target.closest('button.reel-play') : null;
    if (!b) return;
    var v = document.createElement('video');
    v.setAttribute('controls', '');
    v.setAttribute('autoplay', '');
    v.setAttribute('playsinline', '');
    v.setAttribute('controlslist', 'nodownload');
    v.setAttribute('draggable', 'false');
    if (b.getAttribute('data-poster')) v.setAttribute('poster', b.getAttribute('data-poster'));
    v.src = b.getAttribute('data-src');
    b.parentNode.replaceChild(v, b);
    pauseOthers(v);
    var p = v.play();
    if (p && p.catch) p.catch(function () {});
  });
})();

// Lightbox: open a photo, then browse the whole project with the arrow buttons,
// the keyboard (Left / Right / Home / End / Esc) or a swipe.
(function () {
  var box = document.getElementById('lightbox');
  if (!box) return;
  var tiles = Array.prototype.slice.call(document.querySelectorAll('button.shot'));
  if (!tiles.length) return;
  var img = box.querySelector('.lb-stage img');
  var countEl = box.querySelector('.lb-count');
  var altEl = box.querySelector('.lb-alt');
  var btnClose = box.querySelector('.lb-close');
  var btnPrev = box.querySelector('.lb-prev');
  var btnNext = box.querySelector('.lb-next');
  var index = 0;
  var opener = null;
  var preloaded = {};

  function isOpen() { return box.classList.contains('open'); }
  function wrap(i) { return (i + tiles.length) % tiles.length; }
  function preload(i) {
    var src = tiles[wrap(i)].getAttribute('data-full');
    if (src && !preloaded[src]) { var im = new Image(); im.src = src; preloaded[src] = im; }
  }
  function show(i) {
    index = wrap(i);
    var t = tiles[index];
    img.src = t.getAttribute('data-full');
    img.alt = t.getAttribute('data-alt') || '';
    altEl.textContent = img.alt;
    countEl.textContent = (index + 1) + ' / ' + tiles.length;
    preload(index + 1);
    preload(index - 1);
  }
  function open(i, from) {
    opener = from || null;
    box.classList.toggle('single', tiles.length < 2);
    show(i);
    box.classList.add('open');
    box.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    btnClose.focus();
  }
  function close() {
    box.classList.remove('open');
    box.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    if (opener && opener.focus) opener.focus();
    opener = null;
  }

  tiles.forEach(function (b, i) {
    b.addEventListener('click', function () { open(i, b); });
  });
  btnClose.addEventListener('click', close);
  btnPrev.addEventListener('click', function (e) { e.stopPropagation(); show(index - 1); });
  btnNext.addEventListener('click', function (e) { e.stopPropagation(); show(index + 1); });
  box.addEventListener('click', function (e) {
    if (e.target === box || e.target.classList.contains('lb-stage')) close();
  });

  document.addEventListener('keydown', function (e) {
    if (!isOpen()) return;
    var k = e.key;
    if (k === 'Escape') { close(); }
    else if (k === 'ArrowRight') { show(index + 1); }
    else if (k === 'ArrowLeft') { show(index - 1); }
    else if (k === 'Home') { show(0); }
    else if (k === 'End') { show(tiles.length - 1); }
    else if (k === 'Tab') {
      var f = [btnClose, btnPrev, btnNext].filter(function (b) { return b.offsetParent !== null; });
      var at = f.indexOf(document.activeElement);
      var to = e.shiftKey ? (at <= 0 ? f.length - 1 : at - 1) : (at === f.length - 1 ? 0 : at + 1);
      f[to].focus();
    } else { return; }
    e.preventDefault();
  });

  var sx = 0, sy = 0;
  box.addEventListener('touchstart', function (e) {
    var t = e.changedTouches[0]; sx = t.clientX; sy = t.clientY;
  }, { passive: true });
  box.addEventListener('touchend', function (e) {
    var t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) show(index + (dx < 0 ? 1 : -1));
  }, { passive: true });
})();

// Contact form: Formspree AJAX when configured, mailto fallback when not.
document.querySelectorAll('form.contact').forEach(function (form) {
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var status = form.querySelector('.form-status');
    var endpoint = form.getAttribute('data-endpoint');
    var email = form.getAttribute('data-email');
    var fd = new FormData(form);
    if (!endpoint) {
      var body = 'Name: ' + fd.get('name') + '\nEmail: ' + fd.get('email') + '\n\n' + fd.get('message');
      window.location.href = 'mailto:' + email +
        '?subject=' + encodeURIComponent('Portfolio inquiry') +
        '&body=' + encodeURIComponent(body);
      status.textContent = 'Opening your mail app…';
      status.className = 'form-status ok';
      return;
    }
    status.textContent = 'Sending…';
    status.className = 'form-status';
    fetch(endpoint, { method: 'POST', body: fd, headers: { 'Accept': 'application/json' } })
      .then(function (res) {
        if (res.ok) {
          form.reset();
          status.textContent = "Thanks. Your message is in. I'll reply within 24 hours.";
          status.className = 'form-status ok';
        } else {
          return res.json().then(function (j) {
            var msg = (j.errors || []).map(function (x) { return x.message; }).join(', ');
            status.textContent = msg || 'Something went wrong. Email adrian@riveraferran.com directly.';
            status.className = 'form-status err';
          });
        }
      })
      .catch(function () {
        status.textContent = 'Something went wrong. Email adrian@riveraferran.com directly.';
        status.className = 'form-status err';
      });
  });
});

// Download deterrent: suppress the image/video context (long-press save) menu
// and native drag-out. Scoped to media so text, nav links and the form keep
// their normal menus; the lightbox and card links still open as usual.
(function () {
  document.addEventListener('contextmenu', function (e) {
    var t = e.target;
    if (t && t.closest && t.closest('img, video, picture, .shot, .lightbox, .masthead, .thumb, .frame')) {
      e.preventDefault();
    }
  });
  document.addEventListener('dragstart', function (e) {
    var t = e.target;
    if (t && t.closest && t.closest('img, video')) {
      e.preventDefault();
    }
  });
})();

// Scroll reveal. Classes are added here (not in the HTML) so the page stays
// fully visible if JS is off or fails. A scroll sweep backs up the observer:
// a fast or jumped scroll can skip an element entirely, and a skipped element
// would otherwise stay invisible forever.
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || !('IntersectionObserver' in window)) return;
  var targets = Array.prototype.slice.call(document.querySelectorAll(
    '.card, .feature, .about-grid > *, .service, .sec-head, form.contact'
  ));
  if (!targets.length) return;

  function show(el) {
    if (el.classList.contains('in')) return;
    el.classList.add('in');
    io.unobserve(el);
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) { if (en.isIntersecting) show(en.target); });
  }, { rootMargin: '0px 0px -5% 0px', threshold: 0 });

  targets.forEach(function (el, i) {
    el.classList.add('reveal');
    el.style.transitionDelay = (Math.min(i % 6, 5) * 45) + 'ms';
    io.observe(el);
  });

  // Safety net: anything at or above the fold line gets shown on scroll,
  // on load, and once more after a delay.
  function sweep() {
    var line = window.innerHeight * 0.98;
    targets.forEach(function (el) {
      if (el.classList.contains('in')) return;
      var r = el.getBoundingClientRect();
      if (r.top < line || r.bottom < 0) show(el);
    });
  }
  var ticking = false;
  window.addEventListener('scroll', function () {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(function () { sweep(); ticking = false; });
  }, { passive: true });
  window.addEventListener('resize', sweep);
  window.addEventListener('load', sweep);
  sweep();
  window.setTimeout(sweep, 1200);
  // Low-frequency backstop: keeps checking until everything has been shown, so
  // a layout shift or a jump-scroll can never strand content at opacity 0.
  var iv = window.setInterval(function () {
    sweep();
    if (document.querySelectorAll('.reveal:not(.in)').length === 0) {
      window.clearInterval(iv);
    }
  }, 900);
})();

// Masthead: the nav rides over the photo, then turns solid past it.
(function () {
  var header = document.querySelector('header.site');
  var mast = document.querySelector('.masthead');
  if (!header || !mast) return;
  function update() {
    var past = window.scrollY > (mast.offsetHeight - 88);
    header.classList.toggle('scrolled', past);
  }
  update();
  window.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update);
})();

// Parallax: the pinned masthead stays put while the page slides over it and the
// name/labels drift up behind the stack. Desktop + motion-OK only.
(function () {
  var mast = document.querySelector('.masthead');
  if (!mast || !window.matchMedia) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var mq = window.matchMedia('(min-width: 721px)');
  if (!mq.matches) return;

  var ticking = false;
  function paint() {
    ticking = false;
    var y = Math.max(0, Math.min(window.scrollY, mast.offsetHeight));
    mast.style.setProperty('--mp', (-y) + 'px');
    mast.style.visibility = (window.scrollY > mast.offsetHeight + 40) ? 'hidden' : '';
  }
  function schedule() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(paint);
  }
  function onMq() {
    if (mq.matches) {
      schedule();
    } else {
      mast.style.removeProperty('--mp');
      mast.style.visibility = '';
    }
  }

  paint();
  window.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', schedule);
  if (mq.addEventListener) { mq.addEventListener('change', onMq); }
  else if (mq.addListener) { mq.addListener(onMq); }
})();

