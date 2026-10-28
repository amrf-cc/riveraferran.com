/* Work groups and skill chips, and the contact form's endpoint.

   Two lists in site-config.json drive things the owner sees all over the site, and neither could be
   edited before: categories[] (the Work page's filter pills, its group headings, each card's category
   line, the case-study category link and the sidebar groups) and capabilities[] (the home page's
   capability strip and the skill chips on every project and case study).

   A category `key` and a skill `tag` are join keys, not labels: the key is the Work URL fragment and
   the manager's group id, the tag is what a project's tags[] matches. Both are shown read-only here
   and the server refuses a change; renaming one would silently unlink a part of the site.

   This file only registers: a SITE pane with two sections and one section of the Contact pane. It is
   loaded after pages.js and never edits it. */
'use strict';

const TAX_MIME = 'application/x-site-taxrow';
const TAX_NOTE = {
  category: 'This key cannot change: it is the Work page anchor (work.html#…) and every project points at it.',
  skill: 'This tag cannot change: it is what links the skill to every project that shows it.',
};

const TAX = { categories: null, skills: null };   // the two editors, once their section has been built

// The same slug the manager uses for a project: "Café Colibrí — Menu" -> "cafe-colibri-menu".
function taxSlug(text) {
  return String(text || '').toLowerCase().normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

/* ------------------------------------------------------------------ one list */
// A whole list of rows, kept in the DOM and saved in one request. A stored row carries its old
// index in `from` and shows its key read-only; a new row gets an editable key derived from its
// label, shown before it is saved. Removing a row only marks it: a refused save puts it back.
function taxEditor(o) {
  const kind = o.kind;                              // 'category' | 'skill'
  const keyName = kind === 'category' ? 'key' : 'tag';
  const keyWord = kind === 'category' ? 'Key (cannot change)' : 'Tag (cannot change)';
  const wrap = el('<div class="taxsec"></div>');
  const notice = el('<p class="notice" data-tax-notice hidden></p>');
  const box = el('<div class="vids" data-tax="' + esc(o.id) + '"></div>');
  const note = el('<p class="vids-note" hidden>Unsaved changes to this list.</p>');
  wrap.appendChild(notice);
  wrap.appendChild(box);

  const touch = () => {
    dirtyLists.add('taxonomy');
    note.hidden = false;
    renderStatus();
  };
  // A list edit is both: the unsaved marker (dirtyLists) and the pane's own unsaved state, so Cmd+S
  // and the sticky Save button both fire.
  box.addEventListener('input', touch);

  function addRow(row, index) {
    const fresh = index == null;
    const node = el('<div class="vid rowx taxrow ' + (kind === 'category' ? 'cat' : 'skill') + '" data-from="' +
      (fresh ? '' : index) + '"><span class="grip" title="Drag to reorder">⠿</span>' +
      (fresh
        ? '<div class="cell"><input class="tkey" type="text" data-f="' + keyName + '" spellcheck="false" ' +
          'aria-label="' + esc(keyWord) + '" placeholder="' + (kind === 'category' ? 'new-group-key' : 'new-skill-tag') +
          '" title="Made from the name: lowercase words joined by hyphens."></div>'
        : '<span class="tkey" data-f="' + keyName + '" title="' + esc(TAX_NOTE[kind]) + '">' +
          esc(row[keyName]) + '</span>') +
      '<div class="cell"><input type="text" data-f="label" maxlength="40" aria-label="Name" placeholder="' +
      (kind === 'category' ? 'Group name' : 'Skill name') + '"></div>' +
      (kind === 'skill'
        ? '<div class="cell"><input type="text" data-f="line" maxlength="160" aria-label="The line under it" ' +
          'placeholder="The line under it"></div>'
        : '<label class="cell chk"><input type="checkbox" data-f="personal"><span>Personal work</span></label>') +
      '<button type="button" class="del" title="Remove (takes effect when you save)">✕</button></div>');
    $('[data-f="label"]', node).value = fresh ? '' : (row.label || '');
    if (kind === 'skill') $('[data-f="line"]', node).value = fresh ? '' : (row.line || '');
    else $('[data-f="personal"]', node).checked = fresh ? false : !!row.personal;

    // A new row's key follows its name until the owner types a key of their own.
    if (fresh) {
      const key = $('[data-f="' + keyName + '"]', node);
      const label = $('[data-f="label"]', node);
      let own = false;
      key.oninput = () => { own = !!key.value.trim(); };
      label.oninput = () => { if (!own) key.value = taxSlug(label.value); };
    }

    $('.del', node).onclick = () => {
      node.dataset.gone = '1';
      node.hidden = true;
      touch();
    };

    // Drag only from the handle, so selecting text in the inputs still works.
    $('.grip', node).onmousedown = () => { node.draggable = true; };
    node.ondragstart = e => {
      e.dataTransfer.setData(TAX_MIME, o.id);
      node.classList.add('dragging');
    };
    node.ondragend = () => {
      node.draggable = false;
      node.classList.remove('dragging');
      $$('.taxrow', box).forEach(r => r.classList.remove('drop-before', 'drop-after'));
    };
    node.ondragover = e => {
      if (!e.dataTransfer.types.includes(TAX_MIME)) return;
      e.preventDefault();
      const r = node.getBoundingClientRect();
      const after = e.clientY > r.top + r.height / 2;
      node.classList.toggle('drop-after', after);
      node.classList.toggle('drop-before', !after);
    };
    node.ondragleave = () => node.classList.remove('drop-before', 'drop-after');
    node.ondrop = e => {
      if (e.dataTransfer.getData(TAX_MIME) !== o.id) return;
      e.preventDefault();
      const moving = box.querySelector('.taxrow.dragging');
      const after = node.classList.contains('drop-after');
      node.classList.remove('drop-before', 'drop-after');
      if (!moving || moving === node) return;
      node.insertAdjacentElement(after ? 'afterend' : 'beforebegin', moving);
      touch();
    };
    box.appendChild(node);
    return node;
  }

  // The rows as the server takes them, in screen order. A removed row is left out until it is saved.
  const rows = () => {
    const out = [];
    $$('.taxrow', box).forEach(r => {
      if (r.dataset.gone) return;
      const row = { from: r.dataset.from === '' ? null : +r.dataset.from };
      const key = $('[data-f="' + keyName + '"]', r);
      row[keyName] = (key.value == null ? key.textContent : key.value).trim();
      row.label = $('[data-f="label"]', r).value.trim();
      if (kind === 'skill') row.line = $('[data-f="line"]', r).value.trim();
      else row.personal = $('[data-f="personal"]', r).checked;
      out.push(row);
    });
    return out;
  };

  // A refused save changes nothing, so every row it was going to remove comes back.
  const restore = () => {
    $$('.taxrow', box).forEach(r => {
      if (!r.dataset.gone) return;
      delete r.dataset.gone;
      r.hidden = false;
    });
    note.hidden = false;
  };

  const show = message => {
    notice.textContent = message;
    notice.hidden = false;
  };

  const actions = el('<div class="row"><button class="btn" type="button" data-tax-add="' + esc(o.id) + '">' +
    esc(o.addLabel) + '</button></div>');
  $('[data-tax-add]', actions).onclick = () => {
    const node = addRow(null, null);
    $('[data-f="label"]', node).focus();
    touch();
    node.scrollIntoView({ block: 'nearest' });
  };
  wrap.appendChild(note);
  wrap.appendChild(actions);

  o.rows().forEach((row, i) => addRow(row, i));
  TAX[o.id] = { rows, restore, show };
  return wrap;
}

/* ------------------------------------------------------------------- saving */
// What the server stores, in one shape, so "unchanged" is a comparison and not a guess.
function taxNorm(kind, rows) {
  return (rows || []).map(r => (kind === 'category'
    ? { key: r.key || '', label: r.label || '', personal: !!r.personal }
    : { tag: r.tag || '', label: r.label || '', line: r.line || '' }));
}

function taxChanged(kind, editor) {
  const was = kind === 'category' ? ((S.config || {}).categories || []) : ((S.config || {}).capabilities || []);
  return JSON.stringify(taxNorm(kind, editor.rows())) !== JSON.stringify(taxNorm(kind, was));
}

async function saveTaxonomy() {
  const body = {};
  if (TAX.categories && taxChanged('category', TAX.categories)) body.categories = TAX.categories.rows();
  if (TAX.skills && taxChanged('skill', TAX.skills)) body.capabilities = TAX.skills.rows();
  if (!Object.keys(body).length) { toast('Nothing changed'); return; }
  try {
    await api('PUT', 'api/taxonomy', body);
    await refresh();
    renderSidebar();          // the sidebar groups come from categories, in their new order
    renderCurrent();
    toast(body.categories && body.capabilities ? 'Categories and skills saved'
      : body.categories ? 'Categories saved' : 'Skills saved');
  } catch (e) {
    // The whole request is refused, so the rows it would have removed come back and the server's
    // own words stay on screen above the list: it names the projects that still use them.
    [['categories', body.categories], ['skills', body.capabilities]].forEach(pair => {
      if (!pair[1] || !TAX[pair[0]]) return;
      TAX[pair[0]].restore();
      TAX[pair[0]].show(e.message);
    });
    dirtyLists.add('taxonomy');
    renderStatus();
  }
}

/* ---------------------------------------------------------------------- pane */
const taxPane = Pages.addPane({ group: 'site', key: 'taxonomy', title: 'Categories & skills',
  sub: 'Work groups and the skill chips' });
taxPane.saveLabel = 'Save categories & skills';
taxPane.onSave = () => saveTaxonomy();

PageSections.add('taxonomy', { id: 'categories', title: 'Work groups', order: 10,
  sub: 'The Work page filters, headings and the sidebar groups',
  hint: 'Order here is the order of the Work page groups. A new group appears once a project uses it.',
  build(body) {
    body.appendChild(taxEditor({
      id: 'categories', kind: 'category', addLabel: '+ Add a group',
      rows: () => (S.config || {}).categories || [],
    }));
  } });

PageSections.add('taxonomy', { id: 'skills', title: 'Skills', order: 20,
  sub: 'The home page strip and the chips on every project',
  hint: 'The strip on the home page, and the chips on every project. A new skill has no page to link ' +
    'to, so it shows as plain text.',
  build(body) {
    body.appendChild(taxEditor({
      id: 'skills', kind: 'skill', addLabel: '+ Add a skill',
      rows: () => (S.config || {}).capabilities || [],
    }));
  } });

/* ------------------------------------------------------- contact form endpoint */
// The contact form's only wiring (site-config.formspree_endpoint). Empty is allowed: the page then
// opens the visitor's mail app instead. A wrong value loses inquiries silently, so the server only
// accepts a Formspree form URL and this field shows its refusal as it comes.
PageSections.add('contact', { id: 'endpoint', title: 'Form endpoint', order: 2.5,
  sub: 'Where the contact form sends messages',
  hint: 'Where the contact form sends messages. Empty = the form opens the visitor’s mail app instead.',
  build(body) {
    const current = (S.config || {}).formspree_endpoint || '';
    const g = el('<div class="grid"></div>');
    const d = el('<label class="field full"><span>Formspree URL</span>' +
      '<input type="text" data-endpoint spellcheck="false" placeholder="https://formspree.io/f/mgaoebdr">' +
      '<small class="fhint">The address of the form in the Formspree account. Empty and the form opens a ' +
      'message in the visitor’s mail app, addressed to the email in Identity &amp; sharing. A different ' +
      'provider also needs its address in the site’s security policy, which is a code change.</small></label>');
    const input = $('[data-endpoint]', d);
    input.value = current;
    input.dataset.was = current;
    g.appendChild(d);
    const row = el('<div class="row full"><button class="btn primary" type="button" data-endpoint-save>' +
      'Save form endpoint</button>' +
      '<a href="https://formspree.io/forms" target="_blank" rel="noopener">Open the Formspree dashboard ↗</a></div>');
    g.appendChild(row);
    body.appendChild(g);

    $('[data-endpoint-save]', row).onclick = async () => {
      const value = input.value.trim();
      if (value === String(input.dataset.was || '').trim()) { toast('Nothing changed'); return; }
      try {
        const r = await api('POST', 'api/config', { formspree_endpoint: value });
        await refresh();
        input.dataset.was = value;
        // The page keeps its own unsaved edits; only a pane with nothing else changed reads as saved.
        if (typeof pageBody === 'function' && !Object.keys(pageBody(pane)).length) markDirty(false);
        renderStatus();
        toast(r.changed ? 'Form endpoint saved' : 'Nothing changed');
      } catch (e) { toast(e.message, true); }
    };
  } });
