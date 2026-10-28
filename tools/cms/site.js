/* The field groups the page panes mount (identity, hero, bio, introductions, collaborations, reels,
   motion, experience, credits, side projects, education and the résumé card), the rows editor they share
   with the video list, the Visibility pane, and boot. pages.js owns the panes; this file owns the fields. */
'use strict';

const ROW_MIME = 'application/x-site-row';

/* ------------------------------------------------------------- rows editor */
// A list of rows the user edits locally and saves in one go (the manager's video list, generalised).
// o: { id, title, hint, rowsOf(), columns[], poster?, blank?, save(rows), addLabel, saveLabel, savedMsg, checkOnOpen }
// column: { key, type: text | select | project | bool | livid, ph, w, options, dflt, label, list }
function rowsEditor(o) {
  const section = el('<section class="rows-section"></section>');
  let box, note;

  const touch = () => {
    dirtyLists.add(o.id);
    if (note) note.hidden = false;
    renderStatus();
  };
  // Typing here is a list change, not a form change: keep it away from the pane-level listener.
  section.addEventListener('input', e => { e.stopPropagation(); touch(); });

  function posterCell(row, v) {
    const d = el('<div class="pcell" title="Click to set the poster image"><img alt="" hidden><span class="ph">＋ poster</span></div>');
    row.dataset.poster = v.poster || '';
    const paint = tok => {
      const img = $('img', d);
      const has = !!row.dataset.poster;
      img.hidden = !has;
      $('.ph', d).hidden = has;
      if (has) img.src = media(row.dataset.poster, tok);
    };
    paint(v.v);
    d.onclick = () => {
      const f = document.createElement('input');
      f.type = 'file';
      f.accept = 'image/*,.heic,.heif';
      f.onchange = async () => {
        if (!f.files[0]) return;
        try {
          const r = await rawSend('POST', 'api/upload?kind=poster&scope=' + encodeURIComponent(o.poster.scope) +
            (o.poster.slug ? '&slug=' + encodeURIComponent(o.poster.slug) : '') + '&name=' + encodeURIComponent(f.files[0].name), f.files[0], false);
          row.dataset.poster = r.full;
          paint('');
          touch();
        } catch (e) { toast('Upload failed: ' + e.message, true); }
      };
      f.click();
    };
    return d;
  }

  function cell(row, c, v) {
    const w = c.w || 160;
    const val = v[c.key] == null ? (c.dflt == null ? '' : c.dflt) : v[c.key];
    let d;
    if (c.type === 'select' || c.type === 'project') {
      const opts = c.type === 'project'
        ? [['', '— no project —']].concat(S.projects.map(p => [p.slug, p.client]))
        : c.options;
      d = el('<div class="cell" style="--w:' + w + 'px"><select data-f="' + esc(c.key) + '">' +
        opts.map(([ov, ol]) => '<option value="' + esc(ov) + '">' + esc(ol) + '</option>').join('') + '</select></div>');
      $('select', d).value = val;
    } else if (c.type === 'bool') {
      d = el('<label class="cell chk" style="--w:' + w + 'px"><input type="checkbox" data-f="' + esc(c.key) + '"><span>' + esc(c.label || c.key) + '</span></label>');
      $('input', d).checked = !!v[c.key];
    } else if (c.type === 'livid') {
      d = el('<div class="cell link" style="--w:' + w + 'px"><input type="text" data-f="' + esc(c.key) + '" placeholder="' + esc(c.ph || '') + '"><span class="lstat"></span></div>');
      const input = $('input', d);
      input.value = val;
      const run = quiet => checkLivid(input, $('.lstat', d), $('.lhelp', row), $('[data-f=title]', row), quiet);
      input.onchange = () => run(false);
      // Pasting the same link again (after fixing it on Livid) changes nothing, so `change` never fires.
      input.addEventListener('paste', () => setTimeout(() => run(false), 0));
      d._run = run;
    } else {
      d = el('<div class="cell" style="--w:' + w + 'px"><input type="text" data-f="' + esc(c.key) + '" placeholder="' + esc(c.ph || '') + '"' +
        (c.list ? ' list="' + esc(c.list) + '"' : '') + '></div>');
      $('input', d).value = val;
    }
    return d;
  }

  function addRow(v, from) {
    const row = el('<div class="vid rowx" data-from="' + (from == null ? '' : from) + '"><span class="grip" title="Drag to reorder">⠿</span></div>');
    if (o.poster) row.appendChild(posterCell(row, v));
    const cells = o.columns.map(c => cell(row, c, v));
    cells.forEach(c => row.appendChild(c));
    const del = el('<button type="button" class="del" title="Remove (takes effect when you save)">✕</button>');
    del.onclick = () => { row.remove(); touch(); };
    row.appendChild(del);
    row.appendChild(el('<p class="lhelp" hidden></p>'));

    // Drag only from the handle, so selecting text in the inputs still works.
    $('.grip', row).onmousedown = () => { row.draggable = true; };
    row.ondragstart = e => { e.dataTransfer.setData(ROW_MIME, o.id); row.classList.add('dragging'); };
    row.ondragend = () => {
      row.draggable = false;
      row.classList.remove('dragging');
      $$('.rowx', box).forEach(r => r.classList.remove('drop-before', 'drop-after'));
    };
    row.ondragover = e => {
      if (!e.dataTransfer.types.includes(ROW_MIME)) return;
      e.preventDefault();
      const r = row.getBoundingClientRect();
      const after = e.clientY > r.top + r.height / 2;
      row.classList.toggle('drop-after', after);
      row.classList.toggle('drop-before', !after);
    };
    row.ondragleave = () => row.classList.remove('drop-before', 'drop-after');
    row.ondrop = e => {
      if (e.dataTransfer.getData(ROW_MIME) !== o.id) return;
      e.preventDefault();
      const moving = box.querySelector('.rowx.dragging');
      const after = row.classList.contains('drop-after');
      row.classList.remove('drop-before', 'drop-after');
      if (!moving || moving === row) return;
      row.insertAdjacentElement(after ? 'afterend' : 'beforebegin', moving);
      touch();
    };
    box.appendChild(row);
    if (o.checkOnOpen && from != null) cells.forEach(c => { if (c._run && $('input', c).value.trim()) c._run(true); });
    return row;
  }

  function collect() {
    const textual = o.columns.filter(c => c.type === 'text' || c.type === 'livid');
    return $$('.rowx', box).map(r => {
      const out = { from: r.dataset.from === '' ? null : +r.dataset.from };
      o.columns.forEach(c => {
        const inp = $('[data-f="' + c.key + '"]', r);
        out[c.key] = c.type === 'bool' ? inp.checked : inp.value;
      });
      if (o.poster) out.poster = r.dataset.poster || '';
      return out;
    }).filter(r => r.from !== null || textual.some(c => String(r[c.key] || '').trim()));
  }

  async function save() {
    try {
      await o.save(collect());
      await refresh();
      renderSidebar();
      draw();
      renderStatus();
      toast(o.savedMsg || 'Saved');
    } catch (e) { toast(e.message, true); }
  }

  function draw() {
    dirtyLists.delete(o.id);
    section.innerHTML = '';
    const rows = o.rowsOf();
    section.appendChild(el('<h2>' + esc(o.title) + ' <span class="count">' + rows.length + '</span></h2>'));
    if (o.hint) section.appendChild(el('<p class="hint">' + esc(o.hint) + '</p>'));
    box = el('<div class="vids"></div>');
    section.appendChild(box);
    note = el('<p class="vids-note" hidden>Unsaved changes to this list.</p>');
    rows.forEach((r, i) => addRow(r, i));
    const actions = el('<div class="row"><button class="btn" type="button" data-add>' + esc(o.addLabel || '+ Add row') + '</button>' +
      '<button class="btn primary js-save" type="button" data-scope="' + esc(o.id) + '">' + esc(o.saveLabel || 'Save list') + '</button></div>');
    section.appendChild(note);
    section.appendChild(actions);
    $('[data-add]', actions).onclick = () => {
      const r = addRow(o.blank ? o.blank() : {}, null);
      const first = $('input[type=text]', r);
      if (first) first.focus();
      touch();
    };
    $('.js-save', actions).onclick = save;
  }

  draw();
  return section;
}

/* ----------------------------------------------------------- Livid field */
function lividField(label, key, value) {
  const d = el('<div class="field"><span>' + esc(label) + '</span><div class="link"><input type="text" data-k="' + esc(key) +
    '" placeholder="https://livid.com/watch/…"><span class="lstat"></span></div><p class="lhelp" hidden></p></div>');
  const input = $('input', d);
  input.value = value || '';
  const run = quiet => checkLivid(input, $('.lstat', d), $('.lhelp', d), null, quiet);
  input.onchange = () => run(false);
  input.addEventListener('paste', () => setTimeout(() => run(false), 0));
  return d;
}

/* ----------------------------------------------------------- site settings */
function sectionGrid(parent, title, hint) {
  parent.appendChild(el('<h2>' + esc(title) + '</h2>'));
  if (hint) parent.appendChild(el('<p class="hint">' + esc(hint) + '</p>'));
  const g = el('<div class="grid" style="margin-top:12px"></div>');
  parent.appendChild(g);
  return g;
}

// Every builder below appends its group to a page pane's section body and tags each input with the
// config path it writes (cfgField in pages.js), so a page's Save button sends only that page's changes.

// SITE -> Identity & sharing
function identityFields(parent) {
  const c = S.config || {};
  const g = el('<div class="grid"></div>');
  g.appendChild(cfgField('Name', 'name', c.name));
  g.appendChild(cfgField('Role / title', 'title', c.title));
  g.appendChild(cfgField('Based', 'based', c.based));
  g.appendChild(cfgField('Email', 'email', c.email));
  g.appendChild(cfgField('Domain', 'domain', c.domain, { hint: 'No https://. Setting it writes the CNAME on the next build.' }));
  g.appendChild(cfgField('Instagram', 'instagram', c.instagram, { hint: 'A full link, or empty.' }));
  g.appendChild(cfgField('LinkedIn', 'linkedin', c.linkedin, { hint: 'A full link, or empty.' }));
  g.appendChild(cfgField('AI label', 'ai_label', c.ai_label, { hint: 'Shown on every AI-assisted piece.' }));
  parent.appendChild(g);
}

// Home -> Hero: the headline, the two lines, the three masthead images and their labels.
function mastheadFields(parent) {
  const c = S.config || {};
  const g = el('<div class="grid"></div>');
  g.appendChild(cfgField('Headline', 'headline', c.headline, { full: true, area: true, rows: 2 }));
  g.appendChild(cfgField('Subline', 'subline', c.subline, { full: true, area: true, rows: 3 }));
  g.appendChild(imageField({
    label: 'Hero photograph', key: 'hero_image', value: c.hero_image, scope: 'hero',
    hint: 'The wide masthead image. Anything larger than 2400 px on the long edge is shrunk, never stretched. ' +
      'It is also the cover of any project that uses it as photo 01.',
  }));
  g.appendChild(imageField({
    label: 'Hero photograph, phone', key: 'hero_image_phone', value: c.hero_image_phone, scope: 'hero',
    hint: 'Shown on narrow screens. A portrait crop works best.',
  }));
  g.appendChild(imageField({
    label: 'Hero cut-out', key: 'hero_cutout', value: c.hero_cutout, scope: 'cutout',
    hint: 'PNG with transparency: the subject standing in front of the photo. A JPEG cut-out would show a black box.',
  }));
  g.appendChild(cfgField('Hero caption', 'hero_caption', c.hero_caption, { full: true, hint: 'Read aloud by screen readers.' }));
  const labels = Array.isArray(c.masthead_labels) ? c.masthead_labels.slice(0, 3) : [];
  for (let i = 0; i < 3; i++) {
    g.appendChild(cfgField('Masthead label ' + (i + 1), 'masthead_labels.' + i, labels[i] || '',
      { hint: i === 0 ? 'One word under each masthead image.' : '' }));
  }
  parent.appendChild(g);
}

// Home -> About block and About -> Bio: the same two values close both pages.
function bioFields(parent) {
  const c = S.config || {};
  const g = el('<div class="grid"></div>');
  g.appendChild(cfgField('Bio (one paragraph per blank line)', 'bio', (c.bio || []).join('\n\n'),
    { full: true, area: true, rows: 8 }));
  g.appendChild(cfgField('Closing line', 'cta', c.cta, { full: true, area: true, rows: 2 }));
  parent.appendChild(g);
}

// Each page's intro: the line under the page title.
function introField(parent, pageKey, label) {
  const intros = (S.config || {}).page_intros || {};
  const g = el('<div class="grid"></div>');
  g.appendChild(cfgField(label || 'Page intro', 'page_intros.' + pageKey, intros[pageKey],
    { full: true, area: true, rows: 2, hint: 'The line under the page title.' }));
  parent.appendChild(g);
}

// Home -> Collaborations
function collaborationsFields(parent) {
  const collab = (S.config || {}).collaborations || {};
  const g = el('<div class="grid"></div>');
  g.appendChild(cfgField('Heading', 'collaborations.label', collab.label, { full: true }));
  g.appendChild(cfgField('Brands (one per line)', 'collaborations.names', lines(collab.names),
    { full: true, area: true, rows: 6, kind: 'lines' }));
  parent.appendChild(g);
}


function resumeCard() {
  const r = S.resume || {};
  const kb = r.size ? Math.round(r.size / 1024) + ' KB' : '';
  const d = el('<div class="card"><h2 style="margin-top:0">Résumé</h2>' +
    '<p class="hint">The PDF behind the Résumé button and the About page download. A new file replaces it; the old one is kept in tools/backups. ' +
    'Pages exports lose their fi/fl/ff letters in job boards, so every upload gets its text layer repaired.</p>' +
    '<div class="res-row"><div class="res-file">' +
    (r.exists ? '<a href="' + esc(media(r.path, r.v)) + '" target="_blank" rel="noopener">' + esc(r.name) + ' ↗</a><span class="muted">' + esc(kb) + '</span>'
      : '<span class="muted">' + (r.path ? esc(r.path) + ' is missing' : 'No résumé on the site yet') + '</span>') +
    '</div><button class="btn" type="button" id="res-pick">Replace PDF…</button><input type="file" accept="application/pdf,.pdf" hidden id="res-file"></div></div>');
  $('#res-pick', d).onclick = () => $('#res-file', d).click();
  $('#res-file', d).onchange = async e => {
    const f = e.target.files[0];
    e.target.value = '';
    if (!f) return;
    if (!/\.pdf$/i.test(f.name)) { toast('That is not a PDF.', true); return; }
    try {
      const out = await rawSend('PUT', 'api/resume?name=' + encodeURIComponent(f.name), f, true);
      await refresh();
      toast('Résumé replaced · ' + out.note);
      const fresh = resumeCard();
      d.replaceWith(fresh);
    } catch (err) { toast(err.message, true); }
  };
  return d;
}

function resumeSection(parent) {
  parent.appendChild(resumeCard());
}

/* -------------------------------------------------------------- visibility */
async function postConfig(body, okMsg) {
  try {
    await api('POST', 'api/config', body);
    await refresh();
    renderSidebar();
    renderStatus();
    toast(okMsg);
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

function renderVisibility() {
  pane.innerHTML = '';
  pane.appendChild(el('<div><h1>Visibility</h1><p class="hint">Everything that can be switched on or off, in one place. Each switch takes effect now: the site is rebuilt, and nothing is ever deleted.</p></div>'));

  pane.appendChild(el('<h2>Pages and sections</h2>'));
  const cards = el('<div class="vcards"></div>');
  pane.appendChild(cards);

  // Showreel
  const sr = S.config.showreel || {};
  const reel = el('<div class="vcard"></div>');
  const reelSwitch = switchEl('Showreel on the home page', 'The “Watch reel” button and the reel section under the intro.', !sr.hidden);
  reel.appendChild(reelSwitch);
  const links = el('<div class="grid vgrid"></div>');
  links.appendChild(lividField('Vertical reel (9:16)', 'sr-v', (sr.vertical || {}).livid));
  links.appendChild(lividField('Horizontal reel (16:9)', 'sr-h', (sr.horizontal || {}).livid));
  reel.appendChild(links);
  const saveLinks = el('<div class="row"><button class="btn small" type="button">Save reel links</button></div>');
  reel.appendChild(saveLinks);
  cards.appendChild(reel);
  $('input', reelSwitch).onchange = async e => {
    const on = e.target.checked;
    const empty = !((S.config.showreel || {}).vertical || {}).livid || !((S.config.showreel || {}).horizontal || {}).livid;
    const ok = await postConfig({ showreel: { hidden: !on } },
      on ? (empty ? 'Showreel is on. Add both Livid links below: until then the release check lists the empty slots.' : 'Showreel is on.') : 'Showreel hidden. The button and section are gone.');
    if (!ok) e.target.checked = !on;
  };
  $('button', saveLinks).onclick = async () => {
    const v = $('[data-k="sr-v"]', reel).value.trim();
    const h = $('[data-k="sr-h"]', reel).value.trim();
    await postConfig({ showreel: { vertical: { livid: v }, horizontal: { livid: h } } }, 'Reel links saved');
  };

  // Paid Social page
  const hiddenPages = S.config.hidden_pages || [];
  const paid = el('<div class="vcard"></div>');
  const paidSwitch = switchEl('Paid Social page', 'A page in the navigation. It needs its ads added in media.json before the site can be released.', !hiddenPages.includes('paid-social'));
  paid.appendChild(paidSwitch);
  cards.appendChild(paid);
  $('input', paidSwitch).onchange = async e => {
    const on = e.target.checked;
    const others = (S.config.hidden_pages || []).filter(x => x !== 'paid-social');
    const ok = await postConfig({ hidden_pages: on ? others : others.concat(['paid-social']) },
      on ? 'Paid Social page is on. Its ad lists are empty, so the release check will list them.' : 'Paid Social page hidden. The page, its nav link and the links to it are gone.');
    if (!ok) e.target.checked = !on;
  };

  // AI page "Ran live" cards
  const liveNow = S.media.ai_live || [];
  const eligible = (S.constants.ai_live_default || []).filter(s => S.projects.some(p => p.slug === s));
  if (eligible.length) {
    const ai = el('<div class="vcard"><p class="vcard-title">AI & Workflow page, “Ran live” cards</p></div>');
    eligible.forEach(slug => {
      const p = S.projects.find(x => x.slug === slug);
      const sw = switchEl(p.client, 'Shown as an AI-assisted example on the AI page.', liveNow.includes(slug));
      ai.appendChild(sw);
      $('input', sw).onchange = async e => {
        const on = e.target.checked;
        const cur = (S.media.ai_live || []).filter(s => s !== slug);
        const next = eligible.filter(s => s === slug ? on : cur.includes(s));
        const note = on && !p.ai_steps ? ' It has no AI steps text yet, so the release check will list it.' : '';
        const ok = await postConfig({ ai_live: next }, (on ? p.client + ' is on the AI page.' : p.client + ' is off the AI page.') + note);
        if (!ok) e.target.checked = !on;
      };
    });
    cards.appendChild(ai);
  }

  // Every project
  const live = S.projects.filter(p => !p.hidden).length;
  pane.appendChild(el('<h2>Projects <span class="count">' + live + ' live · ' + (S.projects.length - live) + ' hidden</span></h2>'));
  pane.appendChild(el('<p class="hint">A hidden project has no card, no page and no links. Its photos and copy are kept, so showing it again restores everything.</p>'));
  const table = el('<div class="vlist"></div>');
  pane.appendChild(table);
  sidebarGroups(true).forEach(g => {
    table.appendChild(el('<div class="vgroup' + (g.hidden ? ' is-hid' : '') + '">' + esc(g.label) + ' <span class="gc">' + g.items.length + '</span></div>'));
    g.items.forEach(p => {
      const t = thumbFor(p.slug);
      const row = el('<div class="vrow' + (p.hidden ? ' is-hidden' : '') + '" data-vis-row="' + esc(p.slug) + '">' +
        (t ? '<img src="' + esc(t) + '" alt="" loading="lazy">' : '<img alt="">') +
        '<div class="meta"><div class="name">' + esc(p.client) + '</div><div class="sub">' + esc(p.title) + '</div></div></div>');
      const sw = switchEl(p.hidden ? 'Hidden' : 'Visible', '', !p.hidden, 'data-vis-switch="' + esc(p.slug) + '"');
      sw.classList.add('compact');
      $('input', sw).onchange = e => setHidden(p.slug, !e.target.checked);
      row.appendChild(sw);
      row.onclick = e => { if (!e.target.closest('.switch')) select(p.slug); };
      table.appendChild(row);
    });
  });
}

/* ----------------------------------------------------------- reels, motion */
// The two lists the Reels page is built from. Each keeps its own Save button and unsaved marker; the
// copy around them saves with the page.
function reelsList() {
  const wrap = el('<div></div>');
  wrap.appendChild(el('<datalist id="dl-formats">' +
    (S.formats || []).map(f => '<option value="' + esc(f) + '">').join('') + '</datalist>'));
  wrap.appendChild(rowsEditor({
    id: 'reels', title: 'Reels',
    hint: 'Format groups the filter pills on the Reels page. Link a reel to a project to show it on that project\'s page. Nothing changes until you click Save reels.',
    rowsOf: () => S.media.reels || [],
    columns: [
      { key: 'title', type: 'text', ph: 'Title', w: 190 },
      { key: 'livid', type: 'livid', ph: 'https://livid.com/watch/…', w: 280 },
      { key: 'ratio', type: 'select', options: SHAPES, w: 150, dflt: '9 / 16' },
      { key: 'format', type: 'text', ph: 'Format (Brand reel…)', w: 150, list: 'dl-formats' },
      { key: 'brand', type: 'text', ph: 'Brand', w: 140 },
      { key: 'project', type: 'project', w: 150 },
      { key: 'year', type: 'text', ph: 'Year', w: 70 },
      { key: 'ai', type: 'bool', label: 'AI-assisted', w: 120 },
    ],
    poster: { scope: 'reel' },
    save: rows => api('PUT', 'api/lists/reels', { rows }),
    addLabel: '+ Add reel', saveLabel: 'Save reels', savedMsg: 'Reels saved',
  }));
  return wrap;
}

function motionList() {
  return rowsEditor({
    id: 'motion', title: 'Motion',
    hint: 'Animation work, shown under the reels. Nothing changes until you click Save motion.',
    rowsOf: () => S.media.motion || [],
    columns: [
      { key: 'title', type: 'text', ph: 'Title', w: 190 },
      { key: 'livid', type: 'livid', ph: 'https://livid.com/watch/…', w: 280 },
      { key: 'ratio', type: 'select', options: SHAPES, w: 150, dflt: '16 / 9' },
      { key: 'client', type: 'text', ph: 'Client', w: 150 },
      { key: 'role', type: 'text', ph: 'Role', w: 130 },
      { key: 'year', type: 'text', ph: 'Year', w: 70 },
    ],
    poster: { scope: 'motion' },
    save: rows => api('PUT', 'api/lists/motion', { rows }),
    addLabel: '+ Add motion piece', saveLabel: 'Save motion', savedMsg: 'Motion saved',
  });
}

/* --------------------------------------------------------- about & credits */
// Experience, credits and side projects keep their own Save buttons; the headings over them are copy.
function experienceList() {
  return rowsEditor({
    id: 'experience', title: 'Experience',
    hint: 'Newest first. Nothing changes until you click Save experience.',
    rowsOf: () => S.config.experience || [],
    columns: [
      { key: 'role', type: 'text', ph: 'Role', w: 230 },
      { key: 'org', type: 'text', ph: 'Organisation', w: 190 },
      { key: 'years', type: 'text', ph: 'Years', w: 110 },
      { key: 'place', type: 'text', ph: 'Place', w: 150 },
    ],
    save: rows => api('PUT', 'api/lists/experience', { rows }),
    addLabel: '+ Add job', saveLabel: 'Save experience', savedMsg: 'Experience saved',
  });
}

function creditsList() {
  return rowsEditor({
    id: 'credits', title: 'Production credits',
    hint: 'One row per credit, as it should read on the About page. Nothing changes until you click Save credits.',
    rowsOf: () => S.media.credits || [],
    columns: [
      { key: 'artist', type: 'text', ph: 'Artist', w: 170 },
      { key: 'work', type: 'text', ph: 'Work', w: 200 },
      { key: 'role', type: 'text', ph: 'Role', w: 330 },
      { key: 'year', type: 'text', ph: 'Year', w: 64 },
    ],
    save: rows => api('PUT', 'api/lists/credits', { rows }),
    addLabel: '+ Add credit', saveLabel: 'Save credits', savedMsg: 'Credits saved',
  });
}

function sideProjectsList() {
  return rowsEditor({
    id: 'side_projects', title: 'Side projects',
    hint: 'Nothing changes until you click Save side projects.',
    rowsOf: () => S.config.side_projects || [],
    columns: [
      { key: 'label', type: 'text', ph: 'Name', w: 180 },
      { key: 'line', type: 'text', ph: 'One line about it', w: 320 },
      { key: 'href', type: 'text', ph: 'https://…', w: 220 },
    ],
    save: rows => api('PUT', 'api/lists/side_projects', { rows }),
    addLabel: '+ Add side project', saveLabel: 'Save side projects', savedMsg: 'Side projects saved',
  });
}

// Tools, education, certifications, languages: one plain config value each, under its copy heading.
const ABOUT_VALUES = {
  tools: ['Tools (one per line)', 8, 'lines'],
  education: ['Education', 2, 'text'],
  certifications: ['Certifications (one per line)', 8, 'lines'],
  languages: ['Languages', 2, 'text'],
};

function aboutValueField(kind) {
  const c = S.config || {};
  const spec = ABOUT_VALUES[kind];
  const linesMode = spec[2] === 'lines';
  return cfgField(spec[0], kind, linesMode ? lines(c[kind]) : c[kind],
    { full: true, area: true, rows: spec[1], kind: linesMode ? 'lines' : 'text' });
}

/* --------------------------------------------------------------------- boot */
load().catch(e => { $('#status').textContent = 'Could not load: ' + e.message; });
