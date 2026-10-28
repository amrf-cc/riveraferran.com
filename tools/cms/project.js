/* Project pane: header with the visibility switch, fields, photographs, videos, delete. */
'use strict';

const PHOTO_MIME = 'application/x-site-photo';

function pageUrl(p) {
  const target = isStandalone(p) ? p.standalone : 'work/' + p.slug + '.html';
  return 'preview/' + target;
}

function nextFeatured() {
  return S.projects.reduce((m, p) => (Number.isInteger(p.featured) ? Math.max(m, p.featured) : m), 0) + 1;
}

function renderProject(p, isNew) {
  pane.innerHTML = '';
  const head = el('<div class="head"><div><h1>' + (isNew ? 'New project' : esc(p.client)) + '</h1>' +
    (isNew ? '<p class="hint">Save it once, then drop photographs in.</p>'
      : '<p class="slug">' + esc(p.slug) + '<span data-view-slug="' + esc(p.slug) + '"' + (p.hidden ? ' hidden' : '') +
        '> · <a href="' + esc(pageUrl(p)) + '" target="_blank" rel="noopener">View page ↗</a></span></p>') + '</div></div>');
  if (!isNew) {
    const sw = switchEl(p.hidden ? 'Hidden from the site' : 'Visible on the site', 'Takes effect now, no save needed',
      !p.hidden, 'data-vis-switch="' + esc(p.slug) + '"');
    $('input', sw).onchange = e => setHidden(p.slug, !e.target.checked);
    head.appendChild(sw);
  }
  pane.appendChild(head);
  if (!isNew) {
    pane.appendChild(el('<div class="notice" data-vis-note="' + esc(p.slug) + '"' + (p.hidden ? '' : ' hidden') + '>' +
      '<strong>Hidden from the site.</strong> No card, no page, no links, not in the previous/next pager. ' +
      'Photos and copy are kept, so showing it again restores everything.</div>'));
  }

  const g = el('<div class="grid" style="margin-top:22px"></div>');
  g.appendChild(field('Client', 'client', p.client));
  g.appendChild(field('Project title', 'title', p.title));
  g.appendChild(field('One-line summary', 'oneliner', p.oneliner, { full: true, hint: 'Shown on the cards. Say something the brief does not.' }));
  g.appendChild(field('Role', 'role', p.role, { full: true }));
  g.appendChild(field('Years', 'years', p.years));
  g.appendChild(field('Location', 'location', p.location));
  g.appendChild(categoryField(p));
  g.appendChild(featuredField(p));
  g.appendChild(tagsField(p));
  g.appendChild(field('The brief', 'brief', p.brief, { full: true, area: true, rows: 4, hint: 'What the client needed, in your words.' }));
  g.appendChild(field('What I did', 'did', lines(p.did), { full: true, area: true, rows: 4, hint: 'One line each.' }));
  g.appendChild(field('Scope', 'scope', lines(p.scope), { full: true, area: true, rows: 3, hint: 'One line each: counts, formats, where it ran.' }));
  g.appendChild(field('Result', 'result', p.result, { full: true, area: true, rows: 3, hint: 'Only a result you can back up. Leave it empty rather than guess.' }));
  g.appendChild(field('Behind the scenes', 'bts', p.bts, { full: true, area: true, rows: 2 }));
  pane.appendChild(g);

  pane.appendChild(el('<div class="row"><button class="btn primary js-save" data-scope="form" id="save-proj">Save project</button>' +
    (isNew ? '' : '<button class="btn danger" id="del-proj">Delete project</button>') + '</div>'));
  $('#save-proj').onclick = () => saveProject(p, isNew);
  if (isNew) return;

  $('#del-proj').onclick = () => deleteProject(p);
  const photos = el('<section id="photo-section"></section>');
  pane.appendChild(photos);
  drawPhotos(photos, p);
  pane.appendChild(videoEditor(p));
  if (typeof setsEditor === 'function') pane.appendChild(setsEditor(p));
}

function categoryField(p) {
  const known = S.categories.some(c => c.key === p.category);
  const opts = [];
  if (!known) opts.push([p.category || '', p.category ? p.category : 'No Work group (own page)']);
  S.categories.forEach(c => opts.push([c.key, c.label]));
  const d = el('<label class="field"><span>Category</span><select data-k="category">' +
    opts.map(([v, l]) => '<option value="' + esc(v) + '"' + (v === (p.category || '') ? ' selected' : '') + '>' + esc(l) + '</option>').join('') +
    '</select><small class="fhint">The Work page group.</small></label>');
  return d;
}

function tagsField(p) {
  const caps = (S.config.capabilities || []).map(c => [c.tag, c.label]);
  const extra = (p.tags || []).filter(t => !caps.some(c => c[0] === t)).map(t => [t, t]);
  const d = el('<div class="field full"><span>Skills shown on the page</span><div class="chips"></div></div>');
  caps.concat(extra).forEach(([tag, label]) => {
    const on = (p.tags || []).includes(tag);
    const c = el('<label class="chip' + (on ? ' on' : '') + '"><input type="checkbox" data-tag="' + esc(tag) + '"' + (on ? ' checked' : '') + '>' + esc(label) + '</label>');
    $('input', c).onchange = e => c.classList.toggle('on', e.target.checked);
    $('.chips', d).appendChild(c);
  });
  return d;
}

function featuredField(p) {
  const on = !!p.featured;
  const n = Number.isInteger(p.featured) && p.featured > 0 ? p.featured : (on ? 1 : '');
  const d = el('<div class="field"><span>Home page</span><div class="feat">' +
    '<label class="check"><input type="checkbox" data-k="feat-on"' + (on ? ' checked' : '') + '><span class="plain">Featured</span></label>' +
    '<input type="number" min="1" step="1" class="num" data-k="featured" value="' + esc(n) + '" placeholder="order"' + (on ? '' : ' disabled') + '></div>' +
    '<small class="fhint">1 leads the homepage, 2 to 4 follow it.</small></div>');
  $('[data-k="feat-on"]', d).onchange = e => {
    const num = $('[data-k="featured"]', d);
    num.disabled = !e.target.checked;
    if (e.target.checked && !num.value) num.value = nextFeatured();
  };
  return d;
}

function collectProject(p) {
  const out = {};
  $$('[data-k]', pane).forEach(inp => {
    const k = inp.dataset.k;
    if (k === 'feat-on') return;
    if (k === 'did' || k === 'scope') out[k] = toLines(inp.value);
    else if (k === 'featured') out[k] = $('[data-k="feat-on"]', pane).checked ? (String(inp.value || '').trim() || String(nextFeatured())) : '';
    else out[k] = inp.value;
  });
  out.tags = $$('input[data-tag]', pane).filter(i => i.checked).map(i => i.dataset.tag);
  if (p.slug) out.slug = p.slug;
  return out;
}

async function saveProject(p, isNew) {
  const body = collectProject(p);
  if (!body.client.trim() || !body.title.trim()) { toast('Client and project title are required.', true); return; }
  try {
    const r = await api('POST', 'api/projects', body);
    draft = null;
    sel = r.slug;
    await refresh();
    renderSidebar();
    renderCurrent();
    toast(isNew ? 'Project created' : (r.changed ? 'Project saved' : 'Nothing changed'));
  } catch (e) { toast(e.message, true); }
}

async function deleteProject(p) {
  const m = entryOf(p.slug);
  if (!confirm('Delete “' + p.client + '”?\n\nIts page and ' + byLabel(m.photos.length, 'photo') + ' leave the site and move to tools/removed-media. ' +
    'The project is archived in tools/removed-projects. Reels linked to it stay on the Reels page, unlinked.')) return;
  try {
    const r = await api('DELETE', 'api/projects/' + p.slug);
    sel = null;
    await refresh();
    renderSidebar();
    sel = firstSel();
    renderSidebar();
    renderCurrent();
    toast('Project deleted' + (r.unlinked_reels.length ? ' · ' + r.unlinked_reels.length + ' reel(s) unlinked' : ''));
  } catch (e) { toast(e.message, true); }
}

/* --------------------------------------------------------------- photographs */
async function redrawPhotos(p) {
  await refresh();
  const section = $('#photo-section');
  if (section) drawPhotos(section, p);
  renderSidebar();
}

function drawPhotos(section, p) {
  const m = entryOf(p.slug);
  section.innerHTML = '';
  section.appendChild(el('<h2>Photographs <span class="count">' + m.photos.length + '</span></h2>'));
  section.appendChild(el('<p class="hint">Drop images anywhere on the box below. They are resized on this Mac (2000px, plus a 1280px thumbnail), ' +
    'HEIC works, and the first photo is the cover. Nothing is deleted: a removed photo moves to tools/removed-media.</p>'));
  const drop = el('<div class="drop" style="margin-top:14px"><strong>Drop photographs here</strong>or click to choose files<br>' +
    '<span style="font-size:12px">JPEG, PNG, HEIC, WebP — any size</span><input type="file" accept="image/*,.heic,.heif" multiple hidden></div>');
  section.appendChild(drop);
  const shots = el('<div class="shots"></div>');
  section.appendChild(shots);

  m.photos.forEach((ph, i) => {
    const card = el('<div class="shot" draggable="true" data-i="' + i + '">' +
      (i === 0 ? '<span class="cover">COVER</span>' : '') +
      '<img src="' + esc(media(ph.thumb || ph.full, ph.v)) + '" alt="" loading="lazy" decoding="async" draggable="false">' +
      '<div class="bar"><span class="grip" title="Drag to reorder">⠿</span><span class="idx">' + String(i + 1).padStart(2, '0') + '</span>' +
      '<button class="del" title="Remove">✕</button></div>' +
      '<input class="alt" placeholder="alt text" value="' + esc(ph.alt || '') + '">' +
      '<div class="flags" title="Draws the AI-assisted badge on this photo wherever it appears. ' +
      'Composite adds a second badge.">' +
      '<label class="check"><input type="checkbox" data-flag="ai"' + (ph.ai ? ' checked' : '') + '>AI-assisted</label>' +
      '<label class="check"><input type="checkbox" data-flag="composite"' + (ph.composite ? ' checked' : '') + '>Composite</label>' +
      '</div></div>');
    $('.del', card).onclick = async () => {
      if (!confirm('Remove this photograph? It moves to tools/removed-media.')) return;
      try { await api('POST', 'api/media', { slug: p.slug, action: 'remove', index: i }); await redrawPhotos(p); toast('Photograph removed'); }
      catch (e) { toast(e.message, true); }
    };
    const alt = $('.alt', card);
    alt.onchange = async () => {
      try { await api('POST', 'api/media', { slug: p.slug, action: 'update', index: i, alt: alt.value }); ph.alt = alt.value; toast('Alt text saved'); }
      catch (e) { toast(e.message, true); }
    };
    // Two independent badges, saved on the spot: the cover, the masonry tile and the lightbox read them.
    const flag = name => $('[data-flag=' + name + ']', card);
    [flag('ai'), flag('composite')].forEach(box => {
      box.onchange = async () => {
        const on = { ai: flag('ai').checked, composite: flag('composite').checked };
        try {
          const r = await api('POST', 'api/media', { slug: p.slug, action: 'update', index: i,
            ai: on.ai, composite: on.composite });
          ph.ai = on.ai;
          ph.composite = on.composite;
          // Only a change in the number of photos needs the list rebuilt; otherwise the DOM stays put,
          // so an alt input being typed into does not lose its focus.
          if (r.photos !== m.photos.length) await redrawPhotos(p);
          toast('Badge updated');
        } catch (e) { toast(e.message, true); }
      };
    });
    // A private MIME type keeps photo drags apart from project drags and from files dropped on a card.
    card.ondragstart = e => { e.dataTransfer.setData(PHOTO_MIME, String(i)); card.classList.add('dragging'); };
    card.ondragend = () => card.classList.remove('dragging');
    card.ondragover = e => {
      if (!e.dataTransfer.types.includes(PHOTO_MIME)) return;
      e.preventDefault();
      card.classList.add('over');
    };
    card.ondragleave = () => card.classList.remove('over');
    card.ondrop = async e => {
      card.classList.remove('over');
      const raw = e.dataTransfer.getData(PHOTO_MIME);
      if (raw === '') return;
      e.preventDefault(); e.stopPropagation();
      const from = +raw;
      if (Number.isNaN(from) || from === i) return;
      const order = m.photos.map(x => x.full);
      order.splice(i, 0, order.splice(from, 1)[0]);
      try { await api('POST', 'api/media', { slug: p.slug, action: 'reorder', order }); await redrawPhotos(p); toast('Order saved'); }
      catch (err) { toast(err.message, true); }
    };
    shots.appendChild(card);
  });

  const input = $('input[type=file]', drop);
  drop.onclick = () => input.click();
  input.onchange = () => { const files = [...input.files]; input.value = ''; uploadFiles(p, files, drop); };
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('hot'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('hot'); }));
  drop.addEventListener('drop', e => uploadFiles(p, [...(e.dataTransfer.files || [])], drop));
}

async function uploadFiles(p, files, dropEl) {
  const imgs = files.filter(f => f.type.startsWith('image/') || /\.(heic|heif)$/i.test(f.name));
  if (!imgs.length) return;
  const prog = el('<div class="uploading"></div>');
  dropEl.after(prog);
  const items = [];
  for (const f of imgs) {
    prog.innerHTML = 'Resizing ' + (items.length + 1) + ' of ' + imgs.length + ' — ' + esc(f.name) +
      '<div class="bar-track"><div class="bar-fill" style="width:' + Math.round(items.length / imgs.length * 100) + '%"></div></div>';
    try {
      const alt = (p.client || '') + ' — ' + f.name.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ');
      const r = await rawSend('POST', 'api/upload?slug=' + encodeURIComponent(p.slug) + '&kind=photo&name=' +
        encodeURIComponent(f.name) + '&alt=' + encodeURIComponent(alt), f, false);
      items.push({ full: r.full, thumb: r.thumb, alt });
    } catch (e) {
      toast('Upload failed: ' + e.message, true);
      break;
    }
  }
  prog.remove();
  if (!items.length) return;
  try {
    await api('POST', 'api/media', { slug: p.slug, action: 'add', items });
    await redrawPhotos(p);
    toast(items.length + ' photograph' + (items.length === 1 ? '' : 's') + ' added');
  } catch (e) { toast(e.message, true); }
}

/* -------------------------------------------------------------------- videos */
function videoEditor(p) {
  return rowsEditor({
    id: 'videos-' + p.slug,
    title: 'Video',
    hint: 'Paste the link from the video\'s page on Livid. The watch link works, it\'s converted automatically. ' +
      'Drag ⠿ to change the order. Nothing changes until you click Save video list.',
    rowsOf: () => entryOf(p.slug).videos,
    columns: [
      { key: 'title', type: 'text', ph: 'Title', w: 190 },
      { key: 'livid', type: 'livid', ph: 'https://livid.com/watch/…', w: 300 },
      { key: 'ratio', type: 'select', options: SHAPES, w: 150, dflt: '9 / 16' },
    ],
    poster: { scope: 'project', slug: p.slug },
    save: rows => api('PUT', 'api/project/' + p.slug + '/videos', { videos: rows }),
    addLabel: '+ Add video',
    saveLabel: 'Save video list',
    savedMsg: 'Video list saved',
  });
}
