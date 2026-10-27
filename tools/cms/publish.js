/* Publish — the top-bar button, its status pill, the confirm dialog, the progress strip and the result.
   Publishing commits everything and pushes it to GitHub, which puts the site live. All the rules (what is
   refused, what is never done) live in tools/cms.py; this file only shows them. Loaded after app.js. */
'use strict';

let pub = null;                  // the last GET api/publish
let pubPolling = null;           // a status request in flight
let pubAgain = false;            // another was asked for meanwhile
let pubWatching = false;         // a job is being followed
let pubOpen = false;             // the dialog is showing
let pubUnavailable = false;      // the last status request failed

const PUB_DEFAULT_MESSAGE = 'Site update (edited in the site manager)';

function pubDomain() {
  const fromUrl = pub && pub.live_url ? pub.live_url.replace(/^https?:\/\//, '').replace(/\/$/, '') : '';
  return fromUrl || (S.config && S.config.domain) || 'the site';
}

function pubChanges() { return pub ? pub.ahead + (pub.dirty ? 1 : 0) : 0; }

// What the pill says, and its colour. Order matters: the most blocking fact wins.
function pubPill() {
  if (!pub) return { text: pubUnavailable ? 'Publish status unavailable' : 'Checking GitHub…', cls: pubUnavailable ? 'warn' : '' };
  if (pub.running) return { text: 'Publishing…', cls: 'warn' };
  if (!pub.remote) return { text: 'No GitHub remote', cls: 'warn' };
  if (!pub.reachable) return { text: 'Cannot reach GitHub', cls: 'warn' };
  if (pub.behind > 0) return { text: 'GitHub has newer commits', cls: 'warn' };
  const n = pubChanges();
  if (n > 0) return { text: n + ' change' + (n === 1 ? '' : 's') + ' to publish', cls: 'dirty' };
  return { text: 'All published', cls: 'clean' };
}

function pubDetail() {
  if (!pub) return '';
  const bits = [];
  if (pub.remote_url) bits.push('GitHub: ' + pub.remote_url);
  if (pub.dirty) bits.push(pub.changed_files + ' changed file' + (pub.changed_files === 1 ? '' : 's') + ' not committed');
  if (pub.ahead) bits.push(pub.ahead + ' commit' + (pub.ahead === 1 ? '' : 's') + ' not on GitHub yet');
  if (pub.last_commit) bits.push('Last commit: ' + pub.last_commit);
  return bits.join(' · ');
}

function renderPublish() {
  const pill = $('#pub-pill'), btn = $('#btn-publish');
  if (!pill || !btn) return;
  const p = pubPill();
  pill.textContent = p.text;
  pill.className = 'pubpill' + (p.cls ? ' ' + p.cls : '');
  pill.title = (pub && !pub.can_publish && pub.reason ? pub.reason + '. ' : '') + pubDetail();
  btn.disabled = !(pub && pub.can_publish);
  btn.title = !pub ? (pubUnavailable ? 'The Site Manager did not answer' : 'Checking GitHub…') : pub.can_publish ? 'Publish to ' + pubDomain() : pub.reason;
}

// One request at a time. A call that lands while one is in flight asks for another round afterwards,
// because the answer being waited for may have been computed before whatever just changed.
async function pubPoll() {
  if (pubPolling) { pubAgain = true; return pubPolling; }
  pubPolling = (async () => {
    do {
      pubAgain = false;
      try { pub = await api('GET', 'api/publish'); pubUnavailable = false; } catch (e) { pub = null; pubUnavailable = true; }
      renderPublish();
      if (pub && pub.running && !pubWatching) watchPublish();
    } while (pubAgain);
  })().finally(() => { pubPolling = null; });
  return pubPolling;
}

function pubPollSoon() {
  clearTimeout(pubPollSoon.t);
  pubPollSoon.t = setTimeout(pubPoll, 300);
}

/* ------------------------------------------------------------- the dialog */
function pubShow(title, node) {
  $('#pub-title').textContent = title;
  const body = $('#pub-body');
  body.innerHTML = '';
  body.appendChild(node);
  $('#pub-dialog').hidden = false;
  pubOpen = true;
}

function pubHide() {
  $('#pub-dialog').hidden = true;
  pubOpen = false;
}

function openPublish() {
  if (!pub || !pub.can_publish || pubWatching) return;
  const n = pubChanges();
  const parts = [];
  if (pub.dirty) parts.push(pub.changed_files + ' changed file' + (pub.changed_files === 1 ? '' : 's'));
  if (pub.ahead) parts.push(pub.ahead + ' commit' + (pub.ahead === 1 ? '' : 's') + ' already waiting');
  const box = el('<div class="pub-confirm"></div>');
  box.appendChild(el('<p class="pub-lead"><strong>Publish to ' + esc(pubDomain()) + '?</strong> ' +
    'This commits everything and pushes to GitHub, which puts it live.</p>'));
  box.appendChild(el('<p class="pub-sub">' + (n ? esc(parts.join(' · ')) : 'Nothing new is waiting; this will say so.') + '</p>'));
  if (anyDirty()) {
    box.appendChild(el('<p class="warn-note pub-unsaved">You have unsaved edits in the form. They are not on disk, so they will not be published. ' +
      'Save them first if you want them live.</p>'));
  }
  box.appendChild(el('<label class="field full"><span>Message (optional)</span>' +
    '<input type="text" id="pub-msg" maxlength="500" placeholder="' + esc(PUB_DEFAULT_MESSAGE) + '" autocomplete="off"></label>'));
  const row = el('<div class="pub-actions"><button type="button" class="btn ghost" id="pub-cancel">Cancel</button>' +
    '<button type="button" class="btn primary" id="pub-go">Publish</button></div>');
  box.appendChild(row);
  pubShow('Publish', box);
  $('#pub-cancel').onclick = pubHide;
  $('#pub-msg').addEventListener('keydown', e => { if (e.key === 'Enter') $('#pub-go').click(); });
  $('#pub-go').onclick = startPublish;
  $('#pub-msg').focus();
}

async function startPublish() {
  const go = $('#pub-go');
  go.disabled = true;
  const message = ($('#pub-msg').value || '').trim();
  try {
    await api('POST', 'api/publish', message ? { message } : {});
  } catch (e) {
    pubFailure({ error: e.message, log: '' });
    pubPoll();
    return;
  }
  pubMarkRunning();
  watchPublish();
}

// Show "Publishing…" at once, before the next status poll answers.
function pubMarkRunning() {
  if (pub) Object.assign(pub, { running: true, can_publish: false, reason: 'A publish is already running.' });
  renderPublish();
}

function pubProgress(job) {
  const steps = job.steps || [];
  const at = job.state === 'done' ? steps.length : Math.max(0, steps.indexOf(job.step));
  const box = el('<div class="pub-progress"></div>');
  box.appendChild(el('<p class="pub-lead"><strong>Publishing to ' + esc(pubDomain()) + '…</strong> ' +
    'Please keep this page open.</p>'));
  const ol = el('<ol class="pub-steps" id="pub-steps"></ol>');
  steps.forEach((name, i) => {
    const cls = i < at ? 'done' : i === at ? 'active' : '';
    ol.appendChild(el('<li' + (cls ? ' class="' + cls + '"' : '') + (cls === 'active' ? ' aria-current="step"' : '') + '>' + esc(name) + '</li>'));
  });
  box.appendChild(ol);
  return box;
}

function pubFailure(job) {
  const box = el('<div class="pub-failure"></div>');
  box.appendChild(el('<p class="pub-fail"><strong>Not published.</strong></p>'));
  const why = el('<p class="pub-reason" id="pub-reason"></p>');
  why.textContent = job.error || 'Something went wrong.';
  box.appendChild(why);
  if (job.log) {
    const pre = el('<pre class="pub-log" id="pub-log"></pre>');
    pre.textContent = job.log;
    box.appendChild(el('<p class="pub-sub">Log</p>'));
    box.appendChild(pre);
  }
  box.appendChild(el('<div class="pub-actions"><button type="button" class="btn" id="pub-done">Close</button></div>'));
  pubShow('Publish failed', box);
  $('#pub-done').onclick = pubHide;
}

function pubToast(job) {
  const t = $('#toast');
  t.className = 'toast';
  t.textContent = '';
  if (job.message === 'Nothing to publish') {
    t.textContent = 'Nothing to publish. The site on GitHub is already up to date.';
  } else {
    t.appendChild(document.createTextNode('Published. '));
    if (job.url) {
      const a = document.createElement('a');
      a.href = job.url; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = 'View commit ' + job.commit;
      t.appendChild(a);
    } else if (job.commit) {
      t.appendChild(document.createTextNode('Commit ' + job.commit + '.'));
    }
    t.appendChild(document.createElement('br'));
    t.appendChild(document.createTextNode('Live in about a minute.'));
  }
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 20000);
}

async function watchPublish() {
  if (pubWatching) return;
  pubWatching = true;
  try {
    for (;;) {
      let job;
      try { job = await api('GET', 'api/publish/job'); } catch (e) {
        pubFailure({ error: 'Lost contact with the Site Manager (' + e.message + '). Reload the page: the pill shows whether anything is still waiting to publish.', log: '' });
        return;
      }
      if (job.state === 'running') {
        pubShow('Publishing', pubProgress(job));
        await new Promise(r => setTimeout(r, 500));
        continue;
      }
      if (job.state === 'done') {
        pubHide();
        pubToast(job);
      } else if (job.state === 'failed') {
        pubFailure(job);
      } else {
        pubHide();
      }
      return;
    }
  } finally {
    pubWatching = false;
    pubPoll();
  }
}

$('#btn-publish').onclick = openPublish;
$('#pub-close').onclick = () => { if (!pubWatching) pubHide(); };
$('#pub-dialog').onclick = e => { if (e.target.id === 'pub-dialog' && !pubWatching) pubHide(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape' && pubOpen && !pubWatching) pubHide(); });
window.addEventListener('focus', pubPoll);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pubPoll(); });
pubPoll();
