// Mobile nav toggle
(function () {
  var btn = document.querySelector('.nav-toggle');
  var nav = document.querySelector('nav.main');
  if (btn && nav) {
    btn.addEventListener('click', function () { nav.classList.toggle('open'); });
  }
})();

// Lightbox
(function () {
  var box = document.getElementById('lightbox');
  if (!box) return;
  var img = box.querySelector('img');
  document.querySelectorAll('button.shot').forEach(function (b) {
    b.addEventListener('click', function () {
      img.src = b.getAttribute('data-full');
      img.alt = b.getAttribute('data-alt') || '';
      box.classList.add('open');
      document.body.style.overflow = 'hidden';
    });
  });
  function close() {
    box.classList.remove('open');
    document.body.style.overflow = '';
  }
  box.addEventListener('click', close);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
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
          status.textContent = 'Message sent. I’ll reply soon.';
          status.className = 'form-status ok';
        } else {
          return res.json().then(function (j) {
            var msg = (j.errors || []).map(function (x) { return x.message; }).join(', ');
            status.textContent = msg || 'Something went wrong. Email me directly.';
            status.className = 'form-status err';
          });
        }
      })
      .catch(function () {
        status.textContent = 'Network error. Email me directly.';
        status.className = 'form-status err';
      });
  });
});

// Scroll reveal. Classes are added here (not in the HTML) so the page stays
// fully visible if JS is off or fails. A scroll sweep backs up the observer:
// a fast or jumped scroll can skip an element entirely, and a skipped element
// would otherwise stay invisible forever.
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || !('IntersectionObserver' in window)) return;
  var targets = Array.prototype.slice.call(document.querySelectorAll(
    '.card, .feature, .hero-media, .about-grid > *, .service, .sec-head, form.contact'
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

