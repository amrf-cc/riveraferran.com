/* Site Manager — content tool for the brand portfolio (adrian-portfolio).
   Vanilla JS, no build step. Same layout and behaviour as the riveraferran.com Site Manager, plus what
   this site needs: show/hide switches, reels, credits, résumé and a release check. Talks to tools/cms.py.
   Files: app.js (core + sidebar), project.js (project pane), site.js (site panes + boot). */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const pane = $('#pane');

const SHAPES = [['9 / 16', 'Vertical 9:16'], ['16 / 9', 'Horizontal 16:9'], ['4 / 5', 'Portrait 4:5'], ['1 / 1', 'Square 1:1']];
const SITE_PANES = [
  ['__site', 'Site settings', 'Name, hero, bio, contact'],
  ['__vis', 'Visibility', 'What is on the site, and what is not'],
  ['__reels', 'Reels & motion', 'Short-form video and animation'],
  ['__about', 'About & credits', 'Experience, credits, tools'],
];

const EYE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.9 10.9 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.2M6.6 6.6A17.4 17.4 0 0 0 2 12s3.6 7 10 7a9.8 9.8 0 0 0 4-.8"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';

let S = {                        // server state
  version: '', config: {}, categories: [], projects: [], manifest: { projects: {} },
  media: {}, formats: [], constants: {}, resume: {}, build: {},
};
let sel = null;                  // project slug | '__site' | '__vis' | '__reels' | '__about' | '__draft'
let draft = null;                // an unsaved new project
let dirty = false;               // unsaved edits in the open form
const dirtyLists = new Set();    // rows editors with unsaved edits (video list, reels, ...)
let filterText = '';
let filterSeg = 'all';
let pending = 0;

const anyDirty = () => dirty || dirtyLists.size > 0;

/* ---------------------------------------------------------------- helpers */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, bad ? 6000 : 3600);
}

function sheet(title, body) {
  $('#sheet-title').textContent = title;
  const box = $('#sheet-body');
  box.innerHTML = '';
  if (typeof body === 'string') {
    const pre = document.createElement('pre');
    pre.textContent = body;
    box.appendChild(pre);
  } else box.appendChild(body);
  $('#sheet').hidden = false;
}

function busy(d) {
  pending += d;
  document.body.classList.toggle('busy', pending > 0);
}

function onStale(msg) {
  if (confirm(msg + '\n\nReload this page now? Unsaved edits on it will be lost.')) location.reload();
}

// Writes run one at a time, so each request carries the version the previous one produced and quick
// clicks (two eyes in a row) never trip the stale-page guard.
let chain = Promise.resolve();
function serial(fn) {
  const run = chain.then(fn, fn);
  chain = run.catch(() => {});
  return run;
}

async function api(method, path, body) {
  const run = async () => {
    const opts = { method };
    let url = path;
    if (method === 'DELETE') url += (path.includes('?') ? '&' : '?') + 'version=' + encodeURIComponent(S.version);
    else if (method !== 'GET') {
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(Object.assign({}, body || {}, { version: S.version }));
    }
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (res.status === 409 && data.stale) { onStale(data.error); throw new Error(data.error); }
    if (!res.ok || data.ok === false) throw new Error(data.error || (method + ' ' + path + ' → HTTP ' + res.status));
    if (data.version) S.version = data.version;
    if (data.build) { S.build = data.build; renderStatus(); }
    return data;
  };
  busy(1);
  try { return method === 'GET' ? await run() : await serial(run); } finally { busy(-1); }
}

// A raw body (an image, a PDF): uploads are not versioned, a résumé swap is.
function rawSend(method, path, file, versioned) {
  return serial(async () => {
    const url = path + (versioned ? (path.includes('?') ? '&' : '?') + 'version=' + encodeURIComponent(S.version) : '');
    const res = await fetch(url, { method, body: file });
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (res.status === 409 && data.stale) { onStale(data.error); throw new Error(data.error); }
    if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
    if (data.version) S.version = data.version;
    if (data.build) { S.build = data.build; renderStatus(); }
    return data;
  });
}

// Preview URL for a site file. `v` changes whenever other bytes land at the same path, so the browser
// can never show a cached copy of an old photo.
function media(path, v) {
  return 'preview/' + String(path).split('/').map(encodeURIComponent).join('/') + (v ? '?v=' + encodeURIComponent(v) : '');
}

function entryOf(slug) { return S.manifest.projects[slug] || { photos: [], videos: [] }; }

function thumbFor(slug) {
  const m = entryOf(slug);
  if (m.photos && m.photos.length) return media(m.photos[0].thumb || m.photos[0].full, m.photos[0].v);
  if (m.videos && m.videos.length && m.videos[0].poster) return media(m.videos[0].poster, m.videos[0].v);
  return '';
}

const isStandalone = p => !!(p.standalone && !/^\[/.test(p.standalone));
const byLabel = (n, word) => n ? n + ' ' + word + (n === 1 ? '' : 's') : '';

async function refresh() {       // re-read the server without touching the pane
  const d = await api('GET', 'api/state');
  S = Object.assign(S, {
    version: d.version, config: d.config, categories: d.categories, projects: d.projects,
    manifest: d.manifest, media: d.media, formats: d.formats, constants: d.constants,
    resume: d.resume, build: d.build,
  });
}

const firstSel = () => (S.projects.length ? S.projects[0].slug : '__site');

async function load() {
  await refresh();
  if (!sel) sel = firstSel();
  renderSidebar();
  renderStatus();
  renderCurrent();
}

/* ----------------------------------------------------------------- status */
function renderStatus() {
  const b = S.build || {};
  const bits = [];
  const unsaved = anyDirty();
  bits.push('<span class="pill ' + (unsaved ? 'dirty' : 'clean') + '">' + (unsaved ? 'Unsaved changes' : 'Saved') + '</span>');
  if (b.ready) bits.push('<button type="button" class="pill clean btnlike" id="pill-release" title="Nothing left to resolve">Release-ready</button>');
  else if (b.ready === false) {
    bits.push('<button type="button" class="pill warn btnlike" id="pill-release" title="Placeholders or switches still block a release">Draft · ' +
      esc(b.blockers == null ? '?' : b.blockers) + ' to resolve</button>');
  }
  const hidden = S.projects.filter(p => p.hidden).length;
  if (hidden) bits.push('<span class="pill">' + hidden + ' hidden</span>');
  if (b.last_run) bits.push('<span class="pill">' + (b.ok === false ? 'Build failed' : 'Built') + ' ' + esc(b.last_run) + '</span>');
  $('#status').innerHTML = bits.join(' ');
  const pr = $('#pill-release');
  if (pr) pr.onclick = openRelease;
}

function markDirty(on) {
  dirty = on;
  renderStatus();
}

/* ---------------------------------------------------------------- sidebar */
function leadProject() {
  const live = S.projects.filter(p => !p.hidden && Number.isInteger(p.featured) && p.featured > 0);
  return live.sort((a, b) => a.featured - b.featured)[0] || null;
}

function sidebarGroups(ignoreFilter) {
  const needle = ignoreFilter ? '' : filterText.trim().toLowerCase();
  const match = p => !needle || (p.client + ' ' + p.title + ' ' + p.slug).toLowerCase().includes(needle);
  const rows = S.projects.filter(p => match(p) &&
    (ignoreFilter || filterSeg === 'all' || (filterSeg === 'hidden') === !!p.hidden));
  const live = rows.filter(p => !p.hidden);
  const hid = rows.filter(p => p.hidden);
  const keys = S.categories.map(c => c.key);
  const out = [];
  S.categories.forEach(c => {
    const items = live.filter(p => !isStandalone(p) && p.category === c.key);
    if (items.length) out.push({ id: 'cat:' + c.key, label: c.label, items });
  });
  const solo = live.filter(isStandalone);
  if (solo.length) out.push({ id: 'solo', label: 'Own page, not in Work', items: solo });
  const rest = live.filter(p => !isStandalone(p) && !keys.includes(p.category));
  if (rest.length) out.push({ id: 'rest', label: 'Other work', items: rest });
  if (hid.length) out.push({ id: 'hidden', label: 'Hidden from the site', items: hid, hidden: true });
  return out;
}

function renderSidebar() {
  $('#proj-count').textContent = S.projects.length;
  const site = $('#site-list');
  site.innerHTML = '';
  SITE_PANES.forEach(([key, name, sub]) => {
    const li = el('<li' + (sel === key ? ' class="sel"' : '') + '><div class="meta"><div class="name">' + esc(name) +
      '</div><div class="sub">' + esc(sub) + '</div></div></li>');
    li.onclick = () => select(key);
    site.appendChild(li);
  });

  const list = $('#proj-list');
  list.innerHTML = '';
  if (draft) {
    const d = el('<li class="sel"><div class="meta"><div class="name">' + esc(draft.client || 'New project') +
      '</div><div class="sub">unsaved</div></div></li>');
    d.onclick = () => select('__draft');
    list.appendChild(d);
  }
  const filtering = !!filterText.trim() || filterSeg !== 'all';
  const lead = leadProject();
  const groups = sidebarGroups();
  if (!groups.length) list.appendChild(el('<li class="empty-note">No project matches.</li>'));
  groups.forEach(g => {
    list.appendChild(el('<li class="group-head' + (g.hidden ? ' is-hid' : '') + '">' + esc(g.label) +
      ' <span class="gc">' + g.items.length + '</span></li>'));
    g.items.forEach(p => list.appendChild(projectRow(p, g, lead, filtering)));
  });
}

function projectRow(p, group, lead, filtering) {
  const t = thumbFor(p.slug);
  const m = entryOf(p.slug);
  const sub = [byLabel(m.photos.length, 'photo'), byLabel(m.videos.length, 'video')].filter(Boolean).join(' · ') || 'empty';
  const flag = p.hidden ? '' : p === lead ? 'LEAD' : p.featured ? 'FEATURED' : '';
  const li = el(
    '<li ' + (filtering ? '' : 'draggable="true" ') + 'class="proj' + (p.hidden ? ' is-hidden' : '') + (sel === p.slug ? ' sel' : '') + '">' +
    '<span class="grip" title="' + (filtering ? 'Clear the filter to reorder' : 'Drag to reorder') + '">' + (filtering ? '' : '⠿') + '</span>' +
    (t ? '<img src="' + esc(t) + '" alt="" loading="lazy" draggable="false">' : '<img alt="" draggable="false">') +
    '<div class="meta"><div class="name">' + esc(p.client) + '</div><div class="sub">' + esc(sub) + '</div></div>' +
    '<span class="tail">' +
    (flag ? '<span class="flag"' + (flag === 'LEAD' ? ' title="Leads the homepage"' : '') + '>' + flag + '</span>' : '') +
    '<button type="button" class="eye" data-vis-slug="' + esc(p.slug) + '" aria-pressed="' + (p.hidden ? 'true' : 'false') + '" ' +
    'title="' + (p.hidden ? 'Show on the site' : 'Hide from the site') + '" aria-label="' + (p.hidden ? 'Show ' : 'Hide ') + esc(p.client) + '">' +
    (p.hidden ? EYE_OFF : EYE) + '</button></span></li>');
  li.onclick = () => select(p.slug);
  $('.eye', li).onclick = e => { e.stopPropagation(); setHidden(p.slug, !p.hidden); };
  if (!filtering) wireProjectDrag(li, p.slug, group);
  return li;
}

/* ------------------------------------------------------ visibility toggle */
// One function for every switch (sidebar eye, project header, Visibility pane).
async function setHidden(slug, hidden) {
  const p = S.projects.find(x => x.slug === slug);
  if (!p) return;
  try {
    const r = await api('POST', 'api/projects/' + slug + '/visibility', { hidden });
    if (hidden) p.hidden = true; else delete p.hidden;
    // Sidebar only: re-rendering the pane would throw away unsaved edits.
    renderSidebar();
    renderStatus();
    syncVisibility(slug);
    const where = r.affects && r.affects.length ? (hidden ? ' Gone from ' : ' Back on ') + r.affects.join(', ') + '.' : '';
    toast((hidden ? 'Hidden: ' : 'Visible again: ') + p.client + '.' + where);
  } catch (e) {
    toast(e.message, true);
    syncVisibility(slug);
  }
}

function syncVisibility(slug) {
  const p = S.projects.find(x => x.slug === slug);
  if (!p) return;
  $$('[data-vis-switch="' + slug + '"]').forEach(box => {
    box.checked = !p.hidden;
    const sw = box.closest('.switch');
    const txt = sw.querySelector('.txt b');
    if (txt) txt.textContent = sw.classList.contains('compact') ? (p.hidden ? 'Hidden' : 'Visible') : (p.hidden ? 'Hidden from the site' : 'Visible on the site');
  });
  $$('[data-vis-note="' + slug + '"]').forEach(n => { n.hidden = !p.hidden; });
  $$('[data-view-slug="' + slug + '"]').forEach(a => { a.hidden = !!p.hidden; });
  $$('[data-vis-row="' + slug + '"]').forEach(r => r.classList.toggle('is-hidden', !!p.hidden));
}

/* --------------------------------------------------------- project reorder */
const PROJ_MIME = 'application/x-site-project';
let dragSlug = null;

function wireProjectDrag(li, slug, group) {
  li.ondragstart = e => {
    dragSlug = slug;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData(PROJ_MIME, slug);
    li.classList.add('dragging');
  };
  li.ondragend = () => {
    dragSlug = null;
    li.classList.remove('dragging');
    $$('#proj-list li').forEach(x => x.classList.remove('drop-before', 'drop-after'));
  };
  li.ondragover = e => {
    // Only a project from the same group; files and photos are ignored.
    if (!dragSlug || dragSlug === slug || !group.items.some(p => p.slug === dragSlug)) return;
    e.preventDefault();
    const r = li.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    li.classList.toggle('drop-after', after);
    li.classList.toggle('drop-before', !after);
  };
  li.ondragleave = () => li.classList.remove('drop-before', 'drop-after');
  li.ondrop = async e => {
    const from = e.dataTransfer.getData(PROJ_MIME);
    const after = li.classList.contains('drop-after');
    li.classList.remove('drop-before', 'drop-after');
    if (!from || from === slug || !group.items.some(p => p.slug === from)) return;
    e.preventDefault();
    const slugs = orderAfterMove(group.items, from, slug, after);
    try {
      await api('POST', 'api/projects/order', { slugs });
      const byslug = Object.fromEntries(S.projects.map(p => [p.slug, p]));
      S.projects = slugs.map(s => byslug[s]);
      renderSidebar();
      renderStatus();
      toast('Project order saved');
    } catch (err) { toast(err.message, true); }
  };
}

// The group's members keep the positions they already hold in the full list; only their order changes,
// so projects in other groups do not move.
function orderAfterMove(items, from, target, after) {
  const inGroup = items.map(p => p.slug);
  const next = inGroup.filter(s => s !== from);
  next.splice(next.indexOf(target) + (after ? 1 : 0), 0, from);
  const members = new Set(inGroup);
  let k = 0;
  return S.projects.map(p => (members.has(p.slug) ? next[k++] : p.slug));
}

/* ----------------------------------------------------------------- routing */
function paneFor(which) {
  if (which === '__site') return renderSite;
  if (which === '__vis') return renderVisibility;
  if (which === '__reels') return renderReels;
  if (which === '__about') return renderAbout;
  return null;
}

function renderCurrent() {
  dirty = false;
  dirtyLists.clear();
  renderStatus();
  const fn = paneFor(sel);
  if (fn) return fn();
  if (sel === '__draft') return renderProject(draft, true);
  const p = S.projects.find(x => x.slug === sel);
  if (p) return renderProject(p, false);
  sel = firstSel();
  return renderCurrent();
}

function select(which) {
  if (anyDirty() && !confirm('You have unsaved changes. Leave without saving?')) return;
  sel = which;
  renderSidebar();
  renderCurrent();
}

/* ------------------------------------------------------- shared form bits */
// A labelled input or textarea. `opts`: full, area, rows, hint, ph.
function field(label, key, value, opts = {}) {
  const d = el('<label class="field' + (opts.full ? ' full' : '') + '"><span>' + esc(label) + '</span>' +
    (opts.area ? '<textarea data-k="' + esc(key) + '"' + (opts.rows ? ' rows="' + opts.rows + '"' : '') + '></textarea>'
      : '<input type="text" data-k="' + esc(key) + '"' + (opts.list ? ' list="' + esc(opts.list) + '"' : '') + '>') +
    (opts.hint ? '<small class="fhint">' + esc(opts.hint) + '</small>' : '') + '</label>');
  const input = $('[data-k]', d);
  input.value = value == null ? '' : value;
  if (opts.ph) input.placeholder = opts.ph;
  return d;
}

function lines(v) { return (v || []).join('\n'); }
function toLines(s) { return String(s || '').split('\n').map(x => x.trim()).filter(Boolean); }

function switchEl(label, sub, checked, attrs) {
  return el('<label class="switch"><input type="checkbox" role="switch" ' + (attrs || '') + (checked ? ' checked' : '') + '>' +
    '<span class="track"></span><span class="txt"><b>' + esc(label) + '</b>' + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</span></label>');
}

// A Livid link with the manager's check: normalise the link, say whether it plays for a logged-out visitor.
async function checkLivid(input, stat, help, titleInput, quiet) {
  const value = input.value.trim();
  stat.className = 'lstat'; stat.textContent = ''; stat.title = '';
  help.hidden = true; help.textContent = '';
  if (!value) return;
  const explain = msg => { help.textContent = msg; help.hidden = false; };
  stat.textContent = 'Checking…';
  try {
    const r = await api('GET', 'api/livid?url=' + encodeURIComponent(value));
    // Keep a deliberate embed (title=0) as typed; only a different link is rewritten.
    if (!/livid\.com\/embed\//.test(value) || value.split('?')[0].split('/').pop() !== r.embed.split('?')[0].split('/').pop()) {
      input.value = r.embed;
      if (!quiet) input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (r.playable === true) {
      stat.className = 'lstat ok'; stat.textContent = '✓ Plays'; stat.title = 'Livid: ' + r.title;
      if (titleInput && !titleInput.value.trim() && r.title) {
        titleInput.value = r.title;
        titleInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
    } else if (r.playable === false) {
      stat.className = 'lstat bad'; stat.textContent = '⚠ Won\'t play';
      explain('Livid isn\'t letting this video play on other sites yet. On livid.com, open the video, click Share & Embed, ' +
        'set it to Unlisted, then on the Embed tab turn on Enable Embedding. Paste the link again to re-check.');
    } else {
      stat.className = 'lstat'; stat.textContent = 'Couldn\'t check';
      stat.title = 'Livid did not answer. The link was kept as typed.';
    }
  } catch (e) {
    stat.className = 'lstat bad'; stat.textContent = '⚠ Not a Livid link';
    explain(e.message);
  }
}

/* -------------------------------------------------------------- top buttons */
async function rebuild() {
  const b = $('#btn-build');
  b.disabled = true; b.textContent = 'Building…';
  try {
    const r = await api('POST', 'api/build', {});
    sheet(r.ok ? 'Build succeeded' : 'Build failed', r.log);
    toast(r.ok ? (r.ready ? 'Site rebuilt: release-ready' : 'Site rebuilt as a draft') : 'Build failed. See the log.', !r.ok);
    await refresh();
    renderSidebar();
  } catch (e) { sheet('Build failed', e.message); toast(e.message, true); }
  b.disabled = false; b.textContent = 'Rebuild';
}

async function openRelease() {
  try {
    const r = await api('GET', 'api/release');
    const box = el('<div class="release"></div>');
    if (r.ready) {
      box.appendChild(el('<p class="ok-note"><strong>Release-ready.</strong> Nothing is left to resolve. The site on disk is a release build: no draft banner, no placeholders.</p>'));
    } else {
      box.appendChild(el('<p class="warn-note"><strong>' + r.count + ' item' + (r.count === 1 ? '' : 's') +
        ' left to resolve</strong> before this site can be released. The site on disk is a draft build until they are.</p>'));
      const byFile = {};
      r.items.forEach(i => { (byFile[i.file] = byFile[i.file] || []).push(i); });
      Object.keys(byFile).forEach(f => {
        box.appendChild(el('<h3 class="rel-file">' + esc(f) + ' <span class="gc">' + byFile[f].length + '</span></h3>'));
        const ul = el('<ul class="rel-list"></ul>');
        byFile[f].forEach(i => ul.appendChild(el('<li><code>' + esc(i.path) + '</code><span>' + esc(i.value) + '</span></li>')));
        box.appendChild(ul);
      });
    }
    sheet('Release check', box);
  } catch (e) { toast(e.message, true); }
}

$('#btn-new').onclick = () => {
  if (anyDirty() && !confirm('Leave your unsaved changes?')) return;
  draft = {
    client: '', title: '', oneliner: '', location: 'New York City', years: '', role: '', featured: false,
    category: (S.categories[0] || {}).key || '', tags: [], brief: '', did: [], scope: [], result: '', bts: '',
  };
  select('__draft');
};
$('#btn-preview').onclick = () => window.open('preview/', '_blank');
$('#btn-build').onclick = rebuild;
$('#btn-release').onclick = openRelease;
$('#sheet-close').onclick = () => { $('#sheet').hidden = true; };
$('#sheet').onclick = e => { if (e.target.id === 'sheet') $('#sheet').hidden = true; };
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') $('#sheet').hidden = true;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    const b = $$('.js-save').find(x => (x.dataset.scope === 'form' ? dirty : dirtyLists.has(x.dataset.scope)));
    if (b) b.click();
  }
});

$('#q').addEventListener('input', e => { filterText = e.target.value; renderSidebar(); });
$$('#seg button').forEach(b => {
  b.onclick = () => {
    filterSeg = b.dataset.seg;
    $$('#seg button').forEach(x => x.classList.toggle('on', x === b));
    renderSidebar();
  };
});

// Attached once, at boot. Rows editors stop their own input events, so only form fields land here.
pane.addEventListener('input', () => { if (!dirty) markDirty(true); });

// A file dropped outside the upload box must not make the browser navigate away and lose edits.
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());
window.addEventListener('beforeunload', e => { if (anyDirty()) { e.preventDefault(); e.returnValue = ''; } });
