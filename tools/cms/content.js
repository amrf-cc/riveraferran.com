/* The content that had no writer: the AI page's process steps and tools line, the Reels anatomy note,
   the About page's credits heading, and the whole Paid Social page (intro, video ads, static ads).

   These are not copy-registry values and they are not project fields: they live in media.json and
   paid_social. Each section owns its own Save button (`js-save` + `data-scope`), marks `dirtyLists` on
   input exactly like rowsEditor does, and sends only its own keys, so saving one section writes one
   key. Sections mount into the panes pages.js created (PageSections.add), and the two project fields
   are handed to project.js through its one hook line (contentProjectFields).

   Loaded after pages.js, before site.js (which boots). */
'use strict';

/* --------------------------------------------------------------- small pieces */
// A text block bound to one media.json key. No data-k/data-cfg: the pane's own Save button must not
// see these fields, and the page save bar must not think the page is dirty.
function contentText(label, value, o) {
  o = o || {};
  const attrs = (o.max ? ' maxlength="' + o.max + '"' : '') +
    (o.ph ? ' placeholder="' + esc(o.ph) + '"' : '');
  const input = o.one ? '<input type="text"' + attrs + '>'
    : '<textarea rows="' + (o.rows || 4) + '"' + attrs + '></textarea>';
  const d = el('<label class="field full"><span>' + esc(label) + '</span>' + input +
    (o.hint ? '<small class="fhint">' + esc(o.hint) + '</small>' : '') + '</label>');
  $('input, textarea', d).value = value == null ? '' : String(value);
  return d;
}

// The section wrapper: fields, an unsaved marker and one Save button that owns its own request.
// o: {scope, saveLabel, savedMsg, build(fields), save(fields) -> the request}
function contentForm(o) {
  const box = el('<div class="cform"></div>');
  const fields = el('<div class="cform-fields"></div>');
  const note = el('<p class="vids-note" hidden>Unsaved changes here.</p>');
  const row = el('<div class="row"><button class="btn primary js-save" type="button" data-scope="' +
    esc(o.scope) + '">' + esc(o.saveLabel || 'Save') + '</button></div>');
  box.appendChild(fields);
  box.appendChild(note);
  box.appendChild(row);
  const touch = () => {
    dirtyLists.add(o.scope);
    note.hidden = false;
    renderStatus();
  };
  // Typing here is this section's change, not the page form's: keep it away from the pane listener.
  box.addEventListener('input', e => { e.stopPropagation(); touch(); });
  box.addEventListener('change', e => { e.stopPropagation(); touch(); });
  $('button', row).onclick = async () => {
    try {
      const out = await o.save(fields);
      await refresh();
      renderSidebar();
      renderCurrent();          // redraws the pane, so every field shows what was stored
      toast(out && out.changed === false ? 'Nothing changed' : (o.savedMsg || 'Saved'));
    } catch (e) { toast(e.message, true); }
  };
  if (o.build) o.build(fields);
  return box;
}

/* ----------------------------------------------------------------- AI page */
// media.json -> ai_process: one step per line, numbered 01, 02, … on the AI page.
function processSection() {
  const steps = (S.media.ai_process || []).slice();
  let counter;
  const box = contentForm({
    scope: 'ai-process',
    saveLabel: 'Save steps',
    savedMsg: 'Process steps saved',
    build: fields => {
      const hint = el('<p class="hint">One step per line. <span class="count" data-steps></span>' +
        ' · Numbered 01, 02, … in this order.</p>');
      fields.appendChild(hint);
      counter = $('[data-steps]', hint);
      const d = contentText('Steps', steps.join('\n'), { rows: Math.max(4, steps.length + 1) });
      $('textarea', d).setAttribute('data-steps-input', '');
      fields.appendChild(d);
      const paint = () => {
        const n = toLines($('[data-steps-input]', fields).value).length;
        if (counter) counter.textContent = n + (n === 1 ? ' step' : ' steps');
      };
      $('[data-steps-input]', fields).addEventListener('input', paint);
      paint();
    },
    save: fields => api('POST', 'api/config', { ai_process: toLines($('[data-steps-input]', fields).value) }),
  });
  return box;
}

// media.json -> ai_tools_line: the disclosure paragraph under the process list.
function toolsSection() {
  return contentForm({
    scope: 'ai-tools',
    saveLabel: 'Save tools paragraph',
    savedMsg: 'Tools paragraph saved',
    build: fields => fields.appendChild(contentText('Tools paragraph', S.media.ai_tools_line, {
      rows: 4, max: 400,
      hint: 'Shown under the process list, above the AI label line. Empty hides it.',
    })),
    save: fields => api('POST', 'api/config',
      { ai_tools_line: $('textarea', fields).value.trim() }),
  });
}

/* --------------------------------------------------------------- Reels page */
// media.json -> reel_formats_note: the paragraph inside the “Anatomy of a reel” block.
function anatomySection() {
  return contentForm({
    scope: 'reels-anatomy',
    saveLabel: 'Save anatomy note',
    savedMsg: 'Anatomy note saved',
    build: fields => fields.appendChild(contentText('Anatomy of a reel', S.media.reel_formats_note, {
      rows: 4, max: 400,
      hint: 'Empty hides the whole block, kicker and all.',
    })),
    save: fields => api('POST', 'api/config',
      { reel_formats_note: $('textarea', fields).value.trim() }),
  });
}

/* --------------------------------------------------------------- About page */
// media.json -> credits_note: the heading over the production credits table.
function creditsHeadingSection() {
  return contentForm({
    scope: 'about-credits-note',
    saveLabel: 'Save heading',
    savedMsg: 'Credits heading saved',
    build: fields => fields.appendChild(contentText('Credits heading', S.media.credits_note, {
      one: true, max: 60, ph: 'Production credits',
      hint: 'The line over the table. Empty falls back to “Production credits”.',
    })),
    save: fields => api('POST', 'api/config',
      { credits_note: $('input[type=text]', fields).value.trim() }),
  });
}

/* ---------------------------------------------------------- Paid Social page */
function paidBlock() {
  const ps = S.media.paid_social;
  return ps && typeof ps === 'object' ? ps : {};
}

// media.json -> paid_social.video_ads: the ad videos, in the row editor the reels and credits use.
// The poster cell writes `poster`; the upload lands in assets/img/posters/paid/.
function videoAdsSection() {
  const wrap = el('<div></div>');
  wrap.appendChild(rowsEditor({
    id: 'paid-video',
    title: 'Video ads',
    hint: 'One row per video ad, in the order it shows on the page. Each plays in the shape it ran in. ' +
      'Nothing changes until you click Save video ads.',
    rowsOf: () => paidBlock().video_ads || [],
    columns: [
      { key: 'title', type: 'text', ph: 'Title', w: 190 },
      { key: 'campaign', type: 'text', ph: 'Campaign', w: 160 },
      { key: 'livid', type: 'livid', ph: 'https://livid.com/watch/…', w: 280 },
      { key: 'ratio', type: 'select', options: SHAPES, w: 150, dflt: '9 / 16' },
    ],
    poster: { scope: 'paid' },
    blank: () => ({ title: '', campaign: '', livid: '', ratio: '9 / 16' }),
    save: rows => api('PUT', 'api/paid', { video_ads: rows }),
    addLabel: '+ Add video ad', saveLabel: 'Save video ads', savedMsg: 'Video ads saved',
  }));
  return wrap;
}

function staticCell(key, value, o) {
  o = o || {};
  const d = el('<div class="cell" style="--w:' + (o.w || 160) + 'px"></div>');
  if (o.options) {
    d.appendChild(el('<select data-f="' + esc(key) + '">' +
      o.options.map(([v, l]) => '<option value="' + esc(v) + '">' + esc(l) + '</option>').join('') +
      '</select>'));
    $('select', d).value = value || o.dflt || '';
  } else {
    d.appendChild(el('<input type="text" data-f="' + esc(key) + '" placeholder="' + esc(o.ph || '') + '">'));
    $('input', d).value = value == null ? '' : String(value);
  }
  return d;
}

// media.json -> paid_social.static_ads: stills, so the image is the row (rowsEditor's poster cell
// writes `poster`, and these need `image`). Preview, upload, title, campaign, shape.
function staticAdsSection() {
  const scope = 'paid-static';
  const section = el('<section class="adrows-section"></section>');
  let box, note;
  const touch = () => {
    dirtyLists.add(scope);
    if (note) note.hidden = false;
    renderStatus();
  };
  section.addEventListener('input', e => { e.stopPropagation(); touch(); });
  section.addEventListener('change', e => { e.stopPropagation(); touch(); });

  function imageCell(row, value) {
    const d = el('<div class="acell-img" title="Click to set the still"><img alt="" hidden>' +
      '<span class="ph">＋ image</span>' +
      '<input type="file" accept="image/jpeg,image/png,image/webp,image/heic,image/heif" hidden></div>');
    const file = $('input[type=file]', d);
    const paint = () => {
      const img = $('img', d);
      const has = !!row.dataset.image;
      img.hidden = !has;
      $('.ph', d).hidden = has;
      if (has) img.src = media(row.dataset.image);
    };
    row.dataset.image = value || '';
    paint();
    file.onchange = async () => {
      const f = file.files[0];
      file.value = '';
      if (!f) return;
      try {
        const r = await rawSend('POST', 'api/upload?kind=poster&scope=paid&name=' +
          encodeURIComponent(f.name), f, false);
        row.dataset.image = r.full || '';
        paint();
        touch();
        toast('Image uploaded. Save the list to keep it.');
      } catch (e) { toast('Upload failed: ' + e.message, true); }
    };
    d.onclick = e => { if (e.target !== file) file.click(); };
    return d;
  }

  function addRow(v, from) {
    const row = el('<div class="adrow" data-from="' + (from == null ? '' : from) + '"></div>');
    row.appendChild(imageCell(row, v.image));
    row.appendChild(staticCell('title', v.title, { ph: 'Title', w: 190 }));
    row.appendChild(staticCell('campaign', v.campaign, { ph: 'Campaign', w: 160 }));
    row.appendChild(staticCell('ratio', v.ratio, { options: SHAPES, w: 150, dflt: '1 / 1' }));
    const del = el('<button type="button" class="del" title="Remove (takes effect when you save)">✕</button>');
    del.onclick = () => { row.remove(); touch(); };
    row.appendChild(del);
    box.appendChild(row);
    return row;
  }

  function collect() {
    return $$('.adrow', box).map(r => ({
      from: r.dataset.from === '' ? null : +r.dataset.from,
      image: r.dataset.image || '',
      title: $('[data-f=title]', r).value.trim(),
      campaign: $('[data-f=campaign]', r).value.trim(),
      ratio: $('[data-f=ratio]', r).value,
    })).filter(r => r.from !== null || r.title || r.image);
  }

  function draw() {
    dirtyLists.delete(scope);
    section.innerHTML = '';
    const rows = paidBlock().static_ads || [];
    section.appendChild(el('<h2>Static ads <span class="count">' + rows.length + '</span></h2>'));
    section.appendChild(el('<p class="hint">One row per still, in the order it shows on the page. ' +
      'Click the square to upload the image. Nothing changes until you click Save static ads.</p>'));
    box = el('<div class="adrows"></div>');
    section.appendChild(box);
    rows.forEach((r, i) => addRow(r, i));
    note = el('<p class="vids-note" hidden>Unsaved changes to this list.</p>');
    section.appendChild(note);
    const actions = el('<div class="row"><button class="btn" type="button" data-add>+ Add static ad</button>' +
      '<button class="btn primary js-save" type="button" data-scope="' + esc(scope) + '">Save static ads</button></div>');
    section.appendChild(actions);
    $('[data-add]', actions).onclick = () => {
      const r = addRow({ ratio: '1 / 1' }, null);
      const first = $('input[type=text]', r);
      if (first) first.focus();
      touch();
    };
    $('.js-save', actions).onclick = async () => {
      try {
        const out = await api('PUT', 'api/paid', { static_ads: collect() });
        await refresh();
        renderSidebar();
        draw();
        renderStatus();
        toast(out && out.changed === false ? 'Nothing changed' : 'Static ads saved');
      } catch (e) { toast(e.message, true); }
    };
  }

  draw();
  return section;
}

// media.json -> paid_social.intro: the line shown only while the page introduction is empty.
function paidIntroSection() {
  return contentForm({
    scope: 'paid-intro',
    saveLabel: 'Save intro',
    savedMsg: 'Paid Social intro saved',
    build: fields => fields.appendChild(contentText('Intro', paidBlock().intro, {
      rows: 3, max: 240,
      hint: 'Only used when the Paid Social page introduction above is empty.',
    })),
    save: fields => api('PUT', 'api/paid', { intro: $('textarea', fields).value.trim() }),
  });
}

/* ------------------------------------------------------- the project pane hook */
// project.js calls this once, inside the project field grid, after “Behind the scenes”. Both fields
// are plain [data-k] textareas, so collectProject() sends them with every other project field.
function contentProjectFields(grid, p) {
  const project = p || {};
  grid.appendChild(field('AI steps line (AI page “Ran live” card)', 'ai_steps', project.ai_steps, {
    full: true, area: true, rows: 2,
    hint: 'Leave empty if this project has comparison sets: the AI page shows those instead. ' +
      'An empty line with no sets blocks publishing.',
  }));
  grid.appendChild(field('Demo line (AI feature block)', 'demo', project.demo, {
    full: true, area: true, rows: 2,
    hint: 'Optional. Shown under the feature video.',
  }));
}

/* ------------------------------------------------------------------- mount them */
// Ids decide identity, orders decide layout: each section sits right after the copy section it belongs with
// (pages.js orders are whole numbers, these are the halves between them), so SEO stays last on every page.
function defineContentSections() {
  PageSections.add('ai', { id: 'process-steps', title: 'Process steps', order: 3.5,
    sub: 'The numbered list on the AI page', build: b => b.appendChild(processSection()) });
  PageSections.add('ai', { id: 'tools-line', title: 'Tools paragraph', order: 5.5,
    sub: 'The disclosure line under the process', build: b => b.appendChild(toolsSection()) });
  PageSections.add('reels', { id: 'anatomy-note', title: 'Reel formats note', order: 4.5,
    sub: 'The paragraph inside the breakdown block', build: b => b.appendChild(anatomySection()) });
  PageSections.add('about', { id: 'credits-heading', title: 'Credits heading', order: 4.5,
    sub: 'The line over the production credits', build: b => b.appendChild(creditsHeadingSection()) });
  PageSections.add('paid', { id: 'paid-intro', title: 'Paid Social intro', order: 1.5,
    sub: 'The line used while the page introduction is empty', build: b => b.appendChild(paidIntroSection()) });
  PageSections.add('paid', { id: 'video-ads', title: 'Video ads', order: 2.5,
    sub: 'The ad videos themselves', build: b => b.appendChild(videoAdsSection()) });
  PageSections.add('paid', { id: 'static-ads', title: 'Static ads', order: 3.5,
    sub: 'The stills themselves', build: b => b.appendChild(staticAdsSection()) });
}

defineContentSections();
