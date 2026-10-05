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
