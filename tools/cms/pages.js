/* Page panes — the sidebar's SITE and PAGES tree, one pane per page, sections in the order they render.
   Every pane is an accordion per section: the copy fields come from templates/copy.json, the rest mount
   the builders site.js provides. Loaded after app.js and before site.js, which boots.

   Contract other workstreams build on (WS3-WS5):
     Pages.addPane({group, key, title, sub})                   group: 'site' | 'pages'; returns the pane
     PageSections.add(pageKey, {id, title, sub, order, hint, build})   build(body) appends the section
     accordion(title, sub, build)
     copyForm(pageKey, values, save)
     imageField({label, key, value, scope, slug, hint, onChange})
*/
'use strict';

const ACC_STORE = 'cms.acc.';          // sessionStorage prefix: an accordion remembers it is open
const PANE_GROUPS = [['site', 'Site'], ['pages', 'Pages']];

/* --------------------------------------------------------------------- Pages */
const Pages = {
  // The four pane keys the manager had before the page split still select a pane.
  ALIASES: { __site: 'identity', __vis: 'visibility', __reels: 'reels', __about: 'about' },
  panes: [],

  addPane(o) {
    const p = {
      group: o.group || 'pages', key: o.key, title: o.title, sub: o.sub || '',
      head: o.head || null,          // head(host) adds a line under the title
      onSave: o.onSave || null,      // onSave(pane) — the sticky "Save <page>" button
      render: o.render || null,      // a pane that keeps its own renderer (Visibility)
      sections: [],
    };
    this.panes.push(p);
    return p;
  },

  pane(key) { return this.panes.find(p => p.key === key) || null; },

  group(name) { return this.panes.filter(p => p.group === name); },

  keyOf(which) { return this.ALIASES[which] || which; },

  render(key) {
    const p = this.pane(key);
    if (!p) return false;
    pane.innerHTML = '';
    if (p.render) { p.render(pane); return true; }
    const head = el('<div class="pane-head"><h1>' + esc(p.title) + '</h1>' +
      (p.sub ? '<p class="hint">' + esc(p.sub) + '</p>' : '') + '</div>');
    pane.appendChild(head);
    if (p.head) p.head(head);
    p.sections.slice().sort(byOrder).forEach((s, i) => {
      const acc = accordion(s.title, s.sub, body => {
        if (s.hint) body.appendChild(el('<p class="hint">' + esc(s.hint) + '</p>'));
        s.build(body, p);
      }, { key: p.key + ':' + s.id, open: i === 0 });
      acc.dataset.sec = s.id;
      pane.appendChild(acc);
    });
    if (p.onSave) pane.appendChild(pageSaveBar(p));
    return true;
  },
};

const byOrder = (a, b) => (a.order - b.order) || (a.i - b.i);

const PageSections = {
  // One section of a page pane. `order` sorts it among the page's other sections (lower is higher);
  // two sections with the same order keep the order they were registered in.
  add(pageKey, spec) {
    const p = Pages.pane(pageKey);
    const s = {
      id: spec.id || String(spec.title || '').toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      title: spec.title, sub: spec.sub || '', hint: spec.hint || '',
      order: spec.order == null ? 99 : spec.order, build: spec.build,
      i: p ? p.sections.length : 0,
    };
    if (p) p.sections.push(s);
    else if (window.console) console.warn('PageSections.add: no pane "' + pageKey + '"');
    return s;
  },

  of(pageKey) { const p = Pages.pane(pageKey); return p ? p.sections.slice().sort(byOrder) : []; },
};

/* ----------------------------------------------------------------- accordion */
// A collapsible section: a header with the name, a one-line "what this controls", and the body.
// The first section of a pane is open; the rest start closed. Open state lives in sessionStorage.
function accordion(title, sub, build, opts) {
  opts = opts || {};
  const sec = el('<section class="acc">' +
    '<button class="acc-head" type="button" aria-expanded="false">' +
    '<span class="acc-caret" aria-hidden="true"></span>' +
    '<span class="acc-title">' + esc(title) + '</span>' +
    (sub ? '<span class="acc-sub">' + esc(sub) + '</span>' : '') +
    '</button><div class="acc-body"></div></section>');
  const head = $('.acc-head', sec);
  const body = $('.acc-body', sec);
  build(body);
  const n = body.querySelectorAll('[data-copy], [data-cfg], [data-seo], .rowx').length;
  if (n) head.appendChild(el('<span class="acc-count">' + n + '</span>'));
  const store = opts.key ? ACC_STORE + opts.key : '';
  let open = !!opts.open;
  if (store) {
    try { const saved = sessionStorage.getItem(store); if (saved != null) open = saved === '1'; } catch (e) { /* private mode */ }
  }
  const set = v => {
    head.setAttribute('aria-expanded', v ? 'true' : 'false');
    sec.classList.toggle('open', v);
    body.hidden = !v;
  };
  set(open);
  head.onclick = () => {
    const v = head.getAttribute('aria-expanded') !== 'true';
    set(v);
    try { if (store) sessionStorage.setItem(store, v ? '1' : '0'); } catch (e) { /* private mode */ }
  };
  return sec;
}

/* --------------------------------------------------------------- copy forms */
// The editable text comes from templates/copy.json, shipped in /api/state as `copy_registry`, so no
// label, default or limit is hard-coded here. An empty input means "use the built-in text": the
// placeholder shows what that is and ✕ clears an override back to it.
function copyRegistry() { return ((S && S.copy_registry) || {}).keys || []; }
function copyCurrent() { return ((S && S.config) || {}).copy || {}; }

function copyRow(row, values) {
  const v = values && typeof values[row.key] === 'string' ? values[row.key] : '';
  const max = Number(row.max) || 0;
  const long = max > 60;
  const d = el('<div class="field' + (long ? ' full' : '') + '"><span>' + esc(row.label) + '</span>' +
    '<div class="copy-line"><input type="text" data-copy="' + esc(row.key) + '"' +
    (max ? ' data-max="' + max + '" maxlength="' + max + '"' : '') +
    ' placeholder="' + esc(row.default) + '" value="">' +
    '<button type="button" class="reset-default" title="Use the built-in text" aria-label="Reset to the built-in text">✕</button>' +
    '</div>' + (long ? '<small class="fhint">Built-in: ' + esc(row.default) + '</small>' : '') + '</div>');
  const input = $('input', d);
  input.value = v;
  input.dataset.was = v;
  $('.reset-default', d).onclick = () => {
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    if (typeof markDirty === 'function') markDirty(true);
    input.focus();
  };
  return d;
}

// opts: {section: 'Hero'} for one registry section, or {keys: ['card.view']} for a named few (which may
// belong to another page — a shared line like "View project" is edited wherever it shows).
function copyRows(pageKey, values, opts) {
  opts = opts || {};
  const rows = copyRegistry().filter(r => (opts.keys
    ? opts.keys.indexOf(r.key) !== -1
    : r.page === pageKey && (!opts.section || r.section === opts.section)));
  const box = el('<div class="copygrid"></div>');
  if (!rows.length) box.appendChild(el('<p class="hint">No editable text in this section yet.</p>'));
  rows.forEach(r => box.appendChild(copyRow(r, values)));
  return box;
}

// Every copy field of a page, grouped by its registry section, with one "Save this page" button.
// Panes use copyRows() per accordion; this form is for anything that wants the whole page at once.
function copyForm(pageKey, values, save) {
  const form = el('<div class="copy-form"></div>');
  const names = [];
  copyRegistry().forEach(r => { if (r.page === pageKey && names.indexOf(r.section) === -1) names.push(r.section); });
  names.forEach(name => {
    form.appendChild(el('<h3 class="copy-sec">' + esc(name) + '</h3>'));
    form.appendChild(copyRows(pageKey, values, { section: name }));
  });
  if (save) {
    const row = el('<div class="row"><button class="btn primary" type="button">Save this page</button></div>');
    $('button', row).onclick = () => save(copyDelta(form, values));
    form.appendChild(row);
  }
  return form;
}

// The delta only: a key whose input still shows its stored value is not sent at all, so an untouched
// page writes nothing and an emptied field sends "" (which deletes the override server-side).
function copyDelta(root, values) {
  const out = {};
  $$('[data-copy]', root).forEach(input => {
    const key = input.dataset.copy;
    const was = values && typeof values[key] === 'string' ? values[key] : '';
    const now = input.value.trim();
    if (now === was.trim()) return;
    out[key] = now;
  });
  return out;
}

/* ------------------------------------------------------- config form fields */
// A field bound to a config path. `path` may be nested ('page_intros.work') or indexed
// ('masthead_labels.0'); `kind` says how the text becomes a value (text | lines | paragraphs).
function cfgField(label, path, value, opts) {
  opts = opts || {};
  const d = field(label, path, value, opts);
  const input = $('[data-k]', d);
  input.dataset.cfg = path;
  input.dataset.was = value == null ? '' : String(value);
  input.dataset.kind = opts.kind || 'text';
  if (opts.max) input.maxLength = opts.max;
  return d;
}

function cfgNorm(kind, v) {
  if (kind === 'lines') return toLines(v);
  if (kind === 'paragraphs') return String(v == null ? '' : v).split(/\n\s*\n/).map(s => s.trim()).filter(Boolean);
  return String(v == null ? '' : v).trim();
}

function cfgSet(body, segs, v) {
  let node = body;
  for (let i = 0; i < segs.length - 1; i++) {
    if (!node[segs[i]] || typeof node[segs[i]] !== 'object') node[segs[i]] = {};
    node = node[segs[i]];
  }
  node[segs[segs.length - 1]] = v;
}

// The config delta of a pane: only fields the owner actually changed. The three masthead labels are
// one array server-side, so they travel together when any of them moves.
function cfgDelta(root) {
  const body = {};
  const labels = [];
  let labelsMoved = false;
  $$('[data-cfg]', root).forEach(input => {
    const path = input.dataset.cfg;
    const kind = input.dataset.kind || 'text';
    const now = cfgNorm(kind, input.value);
    const was = cfgNorm(kind, input.dataset.was);
    const moved = JSON.stringify(now) !== JSON.stringify(was);
    if (path.indexOf('masthead_labels.') === 0) {
      labels[Number(path.split('.')[1])] = input.value.trim();
      if (moved) labelsMoved = true;
      return;
    }
    if (!moved) return;
    cfgSet(body, path.split('.'), kind === 'text' ? String(input.value).trim() : now);
  });
  if (labelsMoved) {
    const out = [];
    for (let i = 0; i < 3; i++) out.push(labels[i] == null ? '' : labels[i]);
    body.masthead_labels = out;
  }
  return body;
}

/* ---------------------------------------------------------------------- SEO */
// Per-page title and description (site-config -> seo.pages). targets: [{key, label, description}].
// The server refuses a description for the 404, so `description: false` hides that field.
function seoSection(targets) {
  return body => {
    body.appendChild(el('<p class="hint">The browser tab and search results. Leave a field empty to keep ' +
      'the wording the page already uses.</p>'));
    const g = el('<div class="grid"></div>');
    const pages = ((S.config || {}).seo || {}).pages || {};
    targets.forEach(t => {
      const cur = pages[t.key] || {};
      g.appendChild(seoField(t, 'title', 70, cur.title, 'Title'));
      if (t.description !== false) g.appendChild(seoField(t, 'description', 160, cur.description, 'Meta description'));
    });
    body.appendChild(g);
  };
}

function seoField(target, fieldName, max, value, label) {
  const d = el('<label class="field' + (fieldName === 'description' ? ' full' : '') + '">' +
    '<span>' + esc(target.label) + ' — ' + esc(label) + '</span>' +
    '<input type="text" data-seo="' + esc(target.key) + '|' + fieldName + '" data-max="' + max +
    '" maxlength="' + max + '" placeholder="The page\'s own wording">' +
    '<small class="fhint">' + max + ' characters at most.</small></label>');
  const input = $('input', d);
  input.value = value || '';
  input.dataset.was = input.value;
  return d;
}

function seoDelta(root) {
  const pages = {};
  $$('[data-seo]', root).forEach(input => {
    const parts = input.dataset.seo.split('|');
    const now = input.value.trim();
    if (now === String(input.dataset.was || '').trim()) return;
    if (!pages[parts[0]]) pages[parts[0]] = {};
    pages[parts[0]][parts[1]] = now;
  });
  return Object.keys(pages).length ? { pages } : null;
}

/* --------------------------------------------------------------- image field */
// A picture that lives in the repo, with a preview, a real upload and a Clear. `scope` is the upload
// scope server-side: 'hero' (JPEG, 2400px) or 'cutout' (PNG, alpha kept). WS3 reuses this for the
// share card; anything that is a single image on a page can.
function imageField(o) {
  const d = el('<div class="imgfield full" data-img="' + esc(o.key) + '">' +
    '<div class="img-head"><span>' + esc(o.label) + '</span><span class="img-path"></span></div>' +
    '<img alt="" hidden>' +
    '<span class="img-none">No image: the site shows nothing here.</span>' +
    '<div class="img-actions"><button type="button" class="btn small" data-pick>Replace image…</button>' +
    '<button type="button" class="btn small" data-clear>Clear</button>' +
    '<span class="lstat" data-stat></span></div>' +
    (o.hint ? '<small class="fhint">' + esc(o.hint) + '</small>' : '') +
    '<input type="hidden" data-cfg="' + esc(o.key) + '" data-kind="text"></div>');
  const file = el('<input type="file" accept="image/jpeg,image/png,image/webp,image/heic,image/heif" hidden>');
  d.appendChild(file);
  const input = $('[data-cfg]', d);
  let value = o.value || '';
  input.value = value;
  input.dataset.was = value;

  const paint = () => {
    const img = $('img', d);
    $('.img-path', d).textContent = value || 'none';
    $('.img-none', d).hidden = !!value;
    img.hidden = !value;
    if (value) img.src = media(value);
  };
  const stat = (text, cls) => { const s = $('[data-stat]', d); s.textContent = text; s.className = 'lstat ' + (cls || ''); };
  paint();

  $('[data-pick]', d).onclick = () => file.click();
  file.onchange = async () => {
    const f = file.files[0];
    file.value = '';
    if (!f) return;
    stat('Uploading…', '');
    try {
      const r = await rawSend('POST', 'api/upload?kind=poster&scope=' + encodeURIComponent(o.scope) +
        (o.slug ? '&slug=' + encodeURIComponent(o.slug) : '') + '&name=' + encodeURIComponent(f.name), f, false);
      value = r.full || '';
      input.value = value;
      paint();
      stat('Uploaded', 'ok');
      if (typeof markDirty === 'function') markDirty(true);
      if (o.onChange) o.onChange(value);
      toast('Image uploaded. Save the page to keep it.');
    } catch (e) {
      stat('Upload failed', 'bad');
      toast('Upload failed: ' + e.message, true);
    }
  };
  $('[data-clear]', d).onclick = () => {
    value = '';
    input.value = '';
    paint();
    stat('', '');
    if (typeof markDirty === 'function') markDirty(true);
    if (o.onChange) o.onChange('');
  };
  return d;
}

/* ------------------------------------------------------------- page savebar */
function firstTooLong(root) {
  let bad = null;
  $$('[data-max]', root).forEach(input => {
    const max = Number(input.dataset.max) || 0;
    if (!bad && max && input.value.trim().length > max) bad = input;
  });
  return bad;
}

// One request per page: that page's copy, its config fields and its SEO, in a single commit.
function pageBody(root) {
  const body = cfgDelta(root);
  const copy = copyDelta(root, copyCurrent());
  if (Object.keys(copy).length) body.copy = copy;
  const seo = seoDelta(root);
  if (seo) body.seo = seo;
  return body;
}

async function savePage(p, root) {
  const long = firstTooLong(root);
  if (long) {
    toast('That text is too long: ' + long.dataset.max + ' characters at most.', true);
    long.focus();
    return;
  }
  const body = pageBody(root);
  if (!Object.keys(body).length) { toast('Nothing changed on this page.'); return; }
  try {
    const r = await api('POST', 'api/config', body);
    await refresh();
    renderSidebar();
    renderCurrent();
    toast(r.changed ? (p.title + ' saved') : 'Nothing changed');
  } catch (e) { toast(e.message, true); }
}

function pageSaveBar(p) {
  const bar = el('<div class="savebar"><button class="btn primary js-save" type="button" data-scope="form" ' +
    'id="save-' + esc(p.key) + '">' + esc(p.saveLabel || ('Save ' + p.title)) + '</button>' +
    '<span class="hint">Cmd+S saves this page too.</span></div>');
  $('button', bar).onclick = () => p.onSave(p);
  return bar;
}

/* --------------------------------------------------- comparison sets, elsewhere */
// The set editor lives in the project pane (its data is per project), so the Case-study and AI panes
// point at it instead of repeating it.
function setsLinks(host) {
  const withSets = (S.projects || []).filter(p => (((entryOf(p.slug) || {}).sets) || []).length);
  const line = el('<p class="hint" data-sets-link></p>');
  if (!withSets.length) {
    line.textContent = 'Comparison sets are edited inside a project. No project has one yet.';
  } else {
    line.appendChild(document.createTextNode('Comparison sets are edited in the project: '));
    withSets.forEach((p, i) => {
      if (i) line.appendChild(document.createTextNode(', '));
      const a = el('<a href="#" data-open-sets="' + esc(p.slug) + '">' + esc(p.client) + '</a>');
      a.onclick = e => { e.preventDefault(); openProjectSets(p.slug); };
      line.appendChild(a);
    });
    line.appendChild(document.createTextNode(' →'));
  }
  host.appendChild(line);
}

function openProjectSets(slug) {
  if (anyDirty() && !confirm('You have unsaved changes. Leave without saving?')) return;
  sel = slug;
  renderSidebar();
  renderCurrent();
  const d = $('details.sets');
  if (d) {
    d.open = true;
    d.scrollIntoView({ block: 'start' });
  }
}

/* ------------------------------------------------------------- the page tree */
function definePanes() {
  const pages = (key, title, sub, extra) => Pages.addPane(Object.assign({ group: 'pages', key, title, sub }, extra || {}));
  const save = p => savePage(p, pane);
  const copying = (page, section) => b => b.appendChild(copyRows(page, copyCurrent(), { section }));

  /* ---------------------------------------------------------------- SITE */
  const identity = Pages.addPane({ group: 'site', key: 'identity', title: 'Identity & sharing',
    sub: 'Name, contact, résumé, share card' });
  identity.saveLabel = 'Save identity and sharing';
  identity.onSave = save;
  PageSections.add('identity', { id: 'identity', title: 'Identity', order: 1,
    sub: 'Who the site says you are', build: b => identityFields(b) });
  PageSections.add('identity', { id: 'resume', title: 'Résumé', order: 2,
    sub: 'The PDF behind the Résumé button', build: b => resumeSection(b) });
  // The Share card section is registered by share.js, which owns the upload and its own save.

  Pages.addPane({ group: 'site', key: 'visibility', title: 'Visibility',
    sub: 'What is on the site, and what is not', render: () => renderVisibility() });

  /* --------------------------------------------------------------- PAGES */
  const every = pages('everypage', 'Every page', 'Navigation, footer, photo viewer');
  every.onSave = save;
  PageSections.add('everypage', { id: 'nav', title: 'Navigation', order: 1,
    sub: 'The menu and the résumé button', hint: 'Every page carries this menu; the two labels are what a screen reader announces.',
    build: copying('everypage', 'Navigation') });
  PageSections.add('everypage', { id: 'footer', title: 'Footer', order: 2,
    sub: 'The closing line and its links', build: copying('everypage', 'Footer') });
  PageSections.add('everypage', { id: 'lightbox', title: 'Photo viewer', order: 3,
    sub: 'Labels on the full-screen photos', build: copying('everypage', 'Photo viewer') });

  const home = pages('home', 'Home', 'Hero, selected work, capabilities');
  home.onSave = save;
  PageSections.add('home', { id: 'hero', title: 'Hero', order: 1,
    sub: 'The first screen: words, buttons, the masthead images',
    build: b => { b.appendChild(copyRows('home', copyCurrent(), { section: 'Hero' })); mastheadFields(b); } });
  PageSections.add('home', { id: 'selected', title: 'Selected work', order: 2,
    sub: 'The featured projects block', build: copying('home', 'Selected work') });
  PageSections.add('home', { id: 'capabilities', title: 'Capabilities', order: 3,
    sub: 'The “What I do” strip', build: copying('home', 'Capabilities') });
  PageSections.add('home', { id: 'collab', title: 'Collaborations', order: 4,
    sub: 'The brand list under the bio', build: b => collaborationsFields(b) });
  PageSections.add('home', { id: 'about', title: 'About block', order: 5,
    sub: 'The bio and the two links under it', hint: 'The same bio also closes the About page.',
    build: b => { b.appendChild(copyRows('home', copyCurrent(), { section: 'About block' })); bioFields(b); } });

  const work = pages('work', 'Work', 'Headings, filters, groups');
  work.onSave = save;
  PageSections.add('work', { id: 'head', title: 'Head', order: 1,
    sub: 'Kicker, heading and intro', hint: 'The heading is also the browser tab and the search result for this page unless the SEO section says otherwise.',
    build: b => { b.appendChild(copyRows('work', copyCurrent(), { section: 'Head' })); introField(b, 'work', 'Page intro'); } });
  PageSections.add('work', { id: 'filters', title: 'Filters', order: 2,
    sub: 'The group filter above the cards', build: copying('work', 'Filters') });
  PageSections.add('work', { id: 'groups', title: 'Groups', order: 3,
    sub: 'Headings between the groups', hint: 'The group names themselves are edited under Site, in Categories & skills.',
    build: copying('work', 'Groups') });
  PageSections.add('work', { id: 'cards', title: 'Cards', order: 4,
    sub: 'The link on every project card', hint: 'Also on the Home page and on the AI page’s live cards.',
    build: b => b.appendChild(copyRows('work', copyCurrent(), { keys: ['card.view'] })) });
  PageSections.add('work', { id: 'seo', title: 'SEO', order: 5, sub: 'The browser tab and search results',
    build: seoSection([{ key: 'work', label: 'Work page' }]) });

  const cse = pages('case', 'Case study', 'The project pages', { head: host => setsLinks(host) });
  cse.onSave = save;
  PageSections.add('case', { id: 'header', title: 'Header', order: 1,
    sub: 'The back link above the title', build: copying('case', 'Header') });
  PageSections.add('case', { id: 'body', title: 'Body blocks', order: 2,
    sub: 'The brief, what I did, scope, result', build: copying('case', 'Body blocks') });
  PageSections.add('case', { id: 'media', title: 'Comparison and media', order: 3,
    sub: 'The comparison kicker, reels, photos, behind the scenes', hint: 'This heading is also the kicker on the AI page.',
    build: copying('case', 'Comparison and media') });
  PageSections.add('case', { id: 'pager', title: 'Pager', order: 4,
    sub: 'The “more projects” strip at the bottom', build: copying('case', 'Pager') });
  PageSections.add('case', { id: 'next', title: 'Next', order: 5,
    sub: 'The closing block that asks for the job', build: copying('case', 'Next') });
  PageSections.add('case', { id: 'seo', title: 'SEO', order: 6,
    sub: 'The browser tab and search results', hint: 'Every case study shares this one title and description.',
    build: seoSection([{ key: 'case', label: 'Case studies' }]) });

  const reels = pages('reels', 'Reels', 'Short-form video and animation');
  reels.onSave = save;
  PageSections.add('reels', { id: 'head', title: 'Head', order: 1, sub: 'Kicker, heading and intro',
    build: b => { b.appendChild(copyRows('reels', copyCurrent(), { section: 'Head' })); introField(b, 'reels', 'Page intro'); } });
  PageSections.add('reels', { id: 'filters', title: 'Filters', order: 2,
    sub: 'The format filter above the grid', build: copying('reels', 'Filters') });
  PageSections.add('reels', { id: 'reels', title: 'Reels', order: 3,
    sub: 'The video list, and the order on the page', build: b => b.appendChild(reelsList()) });
  PageSections.add('reels', { id: 'anatomy', title: 'Anatomy of a reel', order: 4,
    sub: 'The kicker over the breakdown', build: copying('reels', 'Anatomy of a reel') });
  PageSections.add('reels', { id: 'motion', title: 'Motion', order: 5,
    sub: 'Animation work under the reels',
    build: b => { b.appendChild(copyRows('reels', copyCurrent(), { section: 'Motion' })); b.appendChild(motionList()); } });
  PageSections.add('reels', { id: 'seo', title: 'SEO', order: 6, sub: 'The browser tab and search results',
    build: seoSection([{ key: 'reels', label: 'Reels page' }]) });

  const ai = pages('ai', 'AI & Workflow', 'Headings, the disclosure line, live cards', { head: host => setsLinks(host) });
  ai.onSave = save;
  PageSections.add('ai', { id: 'head', title: 'Head', order: 1, sub: 'Kicker, heading and intro',
    build: b => { b.appendChild(copyRows('ai', copyCurrent(), { section: 'Head' })); introField(b, 'ai', 'Page intro'); } });
  PageSections.add('ai', { id: 'feature', title: 'Feature', order: 2,
    sub: 'The featured project’s headings', build: copying('ai', 'Feature') });
  PageSections.add('ai', { id: 'process', title: 'Process', order: 3,
    sub: 'The heading over the process list', build: copying('ai', 'Process') });
  PageSections.add('ai', { id: 'live', title: 'Ran live', order: 4,
    sub: 'The heading over the live cards', hint: 'Which projects appear there is a switch in Visibility.',
    build: copying('ai', 'Ran live') });
  PageSections.add('ai', { id: 'tools', title: 'Tools: disclosure sentence', order: 5,
    sub: 'The sentence that says every AI piece is marked',
    build: b => b.appendChild(copyRows('ai', copyCurrent(), { section: 'Tools' })) });
  PageSections.add('ai', { id: 'seo', title: 'SEO', order: 6, sub: 'The browser tab and search results',
    build: seoSection([{ key: 'ai', label: 'AI & Workflow page' }]) });

  const paid = pages('paid', 'Paid Social', 'The ad page');
  paid.onSave = save;
  PageSections.add('paid', { id: 'head', title: 'Head', order: 1, sub: 'Kicker, heading and intro',
    build: b => { b.appendChild(copyRows('paid', copyCurrent(), { section: 'Head' })); introField(b, 'paid', 'Page intro'); } });
  PageSections.add('paid', { id: 'video', title: 'Video ads: heading and sizes', order: 2,
    sub: 'The heading and the sizes the page promises', build: copying('paid', 'Video ads') });
  PageSections.add('paid', { id: 'static', title: 'Static ads: heading', order: 3,
    sub: 'The heading over the stills', build: copying('paid', 'Static ads') });
  PageSections.add('paid', { id: 'seo', title: 'SEO', order: 4, sub: 'The browser tab and search results',
    build: seoSection([{ key: 'paid', label: 'Paid Social page' }]) });

  const about = pages('about', 'About', 'Bio, experience, credits, résumé');
  about.onSave = save;
  PageSections.add('about', { id: 'head', title: 'Head', order: 1, sub: 'Kicker, heading and intro',
    build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Head' })); introField(b, 'about', 'Page intro'); } });
  PageSections.add('about', { id: 'bio', title: 'Bio', order: 2,
    sub: 'The paragraph and the closing line', hint: 'The same bio opens the Home page’s About block.',
    build: b => bioFields(b) });
  PageSections.add('about', { id: 'experience', title: 'Experience', order: 3,
    sub: 'The jobs list', build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Experience' })); b.appendChild(experienceList()); } });
  PageSections.add('about', { id: 'credits', title: 'Credits', order: 4,
    sub: 'Production credits and the table headings',
    build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Credits' })); b.appendChild(creditsList()); } });
  PageSections.add('about', { id: 'tools', title: 'Tools', order: 5,
    sub: 'The heading and the list', build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Tools' })); b.appendChild(aboutValueField('tools')); } });
  PageSections.add('about', { id: 'education', title: 'Education', order: 6,
    sub: 'The heading and the line', build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Education' })); b.appendChild(aboutValueField('education')); } });
  PageSections.add('about', { id: 'certifications', title: 'Certifications', order: 7,
    sub: 'The heading and the list', build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Certifications' })); b.appendChild(aboutValueField('certifications')); } });
  PageSections.add('about', { id: 'languages', title: 'Languages', order: 8,
    sub: 'The heading and the line', build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Languages' })); b.appendChild(aboutValueField('languages')); } });
  PageSections.add('about', { id: 'side', title: 'Side projects', order: 9,
    sub: 'The heading and the list', build: b => { b.appendChild(copyRows('about', copyCurrent(), { section: 'Side projects' })); b.appendChild(sideProjectsList()); } });
  PageSections.add('about', { id: 'resume', title: 'Résumé', order: 10,
    sub: 'The heading and the download button', build: copying('about', 'Résumé') });
  PageSections.add('about', { id: 'seo', title: 'SEO', order: 11, sub: 'The browser tab and search results',
    build: seoSection([{ key: 'about', label: 'About page' }]) });

  const contact = pages('contact', 'Contact', 'Headings, the form, its messages');
  contact.onSave = save;
  PageSections.add('contact', { id: 'head', title: 'Head', order: 1, sub: 'Kicker, heading and the intro paragraph',
    hint: 'The intro can point at the email address with {email}.',
    build: copying('contact', 'Head') });
  PageSections.add('contact', { id: 'form', title: 'Form', order: 2,
    sub: 'Field labels, the note, the button, the subject', hint: 'The address the form posts to is switched in the form settings, not here.',
    build: copying('contact', 'Form') });
  PageSections.add('contact', { id: 'messages', title: 'Messages', order: 3,
    sub: 'What the form says while it sends',
    hint: 'Only a message you override travels with the page: an untouched site ships none of these. ' +
      '{email} becomes the address in Identity.',
    build: copying('contact', 'Messages') });
  PageSections.add('contact', { id: 'seo', title: 'SEO', order: 4, sub: 'The browser tab and search results',
    build: seoSection([{ key: 'contact', label: 'Contact page' }]) });

  const legal = pages('legal', 'Legal & 404', 'Privacy, terms, not found');
  legal.onSave = save;
  PageSections.add('legal', { id: 'privacy', title: 'Privacy', order: 1,
    sub: 'Title, description, effective date', hint: 'The prose itself is reviewed wording and stays in the code.',
    build: copying('legal', 'Privacy') });
  PageSections.add('legal', { id: 'terms', title: 'Terms', order: 2,
    sub: 'Title, description, effective date', build: copying('legal', 'Terms') });
  PageSections.add('legal', { id: 'notfound', title: 'Not found', order: 3,
    sub: 'The 404 page', build: notFoundSection });
  PageSections.add('legal', { id: 'seo', title: 'SEO', order: 4,
    sub: 'The browser tabs and search results',
    hint: 'These override the <title> and meta description; the text above is what the page shows.',
    build: seoSection([{ key: 'legal.privacy', label: 'Privacy Notice' },
      { key: 'legal.terms', label: 'Terms of Use' },
      { key: 'notfound', label: '404 page', description: false }]) });
}

function notFoundSection(body) {
  body.appendChild(copyRows('legal', copyCurrent(), { section: 'Not found' }));
  body.appendChild(el('<p class="hint" data-404-links><strong>Fixed links.</strong> The 404 page also shows ' +
    'three shortcuts written into its code — Work (<code>/work.html</code>), Reels (<code>/reels.html</code>) ' +
    'and Contact (<code>/contact.html</code>). They are root-absolute on purpose and are not editable here.</p>'));
}

window.Pages = Pages;
window.PageSections = PageSections;

definePanes();
