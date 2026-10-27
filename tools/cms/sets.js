/* Comparison sets: the "Real shot → AI step → final" rows of a project page, edited in place.
   The data is assets-manifest.json → projects[<slug>].sets[]; build.py draws it on the case-study
   page and, for a project on the AI page's "Ran live" list, on ai.html as well. */
'use strict';

const SET_MIME = 'application/x-site-set';          // a whole set, dragged by its grip
const STEP_MIME = 'application/x-site-step';        // one step, dragged inside its own set
const SET_TITLE_PH = 'Studio shot → AI step → final';
const MAX_SETS = 6;                                 // the server allows the same
const MAX_STEPS = 6;                                // the server allows 8; the row gets unreadable

function setsEditor(p) {
  const section = el('<details class="sets" data-sets="' + esc(p.slug) + '"></details>');
  let note = null;
  let model = [];

  function entry() { return entryOf(p.slug) || {}; }

  function fromState() {
    return (entry().sets || []).map((s, i) => ({
      from: i,
      title: s.title || '',
      steps: (s.steps || []).map((st, j) => ({
        from: j, label: st.label || '', image: st.image || '',
        ai: !!st.ai, composite: !!st.composite, v: st.v || '',
      })),
    }));
  }

  const newStep = () => ({ from: null, label: '', image: '', ai: false, composite: false, v: '' });
  const newSet = () => ({ from: null, title: '', steps: [newStep()] });

  // What the page will look like: the DOM is the draft, the same idea as the manager's rows editors.
  function collect() {
    return $$('.set-card', section).map(card => ({
      from: card.dataset.from === '' ? null : Number(card.dataset.from),
      title: $('[data-f="title"]', card).value,
      steps: $$('.set-step', card).map(st => ({
        from: st.dataset.from === '' ? null : Number(st.dataset.from),
        label: $('[data-f="label"]', st).value,
        image: st.dataset.image || '',
        ai: $('[data-f="ai"]', st).checked,
        composite: $('[data-f="composite"]', st).checked,
      })),
    }));
  }

  const touch = () => {
    dirtyLists.add('sets');
    if (note) note.hidden = false;
    renderStatus();
  };

  // Every structural change goes through here: read the draft, apply the edit, redraw.
  function changed(edit) {
    model = collect();
    if (edit) edit(model);
    paint();
    touch();
  }

  /* --------------------------------------------------------------- one step */
  function stepCard(i, j, s) {
    const st = s.steps[j];
    const step = el('<div class="set-step" data-from="' + (st.from == null ? '' : st.from) + '" data-image="' + esc(st.image) + '">' +
      '<div class="bar"><span class="grip" title="Drag to reorder">⠿</span>' +
      '<button type="button" data-move="-" title="Move left">←</button>' +
      '<button type="button" data-move="+" title="Move right">→</button>' +
      '<button type="button" class="last" data-drop title="Remove this step">✕</button></div>' +
      '<img alt=""' + (st.image ? ' src="' + esc(media(st.image, st.v)) + '"' : ' hidden') + '>' +
      '<span class="ph"' + (st.image ? ' hidden' : '') + '>No image</span>' +
      '<button type="button" class="btn small" data-pick>' + (st.image ? 'Replace image…' : 'Choose image…') + '</button>' +
      '<input type="file" accept="image/*,.heic,.heif" hidden data-file>' +
      '<input type="text" data-f="label" maxlength="60" placeholder="Label" value="' + esc(st.label) + '">' +
      '<label class="check"><input type="checkbox" data-f="ai"' + (st.ai ? ' checked' : '') + '>AI-assisted</label>' +
      '<label class="check"><input type="checkbox" data-f="composite"' + (st.composite ? ' checked' : '') + '>Composite</label>' +
      (st.image ? '' : '<p class="hint" data-no-image>No image yet — publishing is blocked until you add one</p>') +
      '</div>');

    const file = $('[data-file]', step);
    file.onchange = () => uploadStep(file, i, j);
    $('[data-pick]', step).onclick = () => file.click();
    $('[data-move="-"]', step).disabled = j === 0;
    $('[data-move="+"]', step).disabled = j === s.steps.length - 1;
    $$('[data-move]', step).forEach(b => {
      b.onclick = () => changed(m => {
        const list = m[i].steps;
        const to = j + (b.dataset.move === '+' ? 1 : -1);
        if (to < 0 || to >= list.length) return;
        list.splice(to, 0, list.splice(j, 1)[0]);
      });
    });
    $('[data-drop]', step).onclick = () => {
      if (s.steps.length < 2) { toast('A set needs at least one step.', true); return; }
      changed(m => { m[i].steps.splice(j, 1); });
    };
    return dragStep(step);
  }

  function dragStep(step) {
    $('.grip', step).onmousedown = () => { step.draggable = true; };
    step.ondragstart = e => {
      e.stopPropagation();                       // the card must not claim a step's drag
      if (!e.dataTransfer) return;
      e.dataTransfer.setData(STEP_MIME, '1');
      step.classList.add('dragging');
    };
    step.ondragend = e => {
      e.stopPropagation();
      step.draggable = false;
      step.classList.remove('dragging');
      clearMarks();
    };
    step.ondragover = e => {
      if (!e.dataTransfer || ![...e.dataTransfer.types].includes(STEP_MIME)) return;
      e.preventDefault();
      mark(step, e, true);
    };
    step.ondragleave = () => step.classList.remove('drop-before', 'drop-after');
    step.ondrop = e => {
      if (!e.dataTransfer || e.dataTransfer.getData(STEP_MIME) !== '1') return;
      e.preventDefault();
      e.stopPropagation();
      const moving = [...step.parentElement.children].find(c => c.classList.contains('dragging'));
      const after = step.classList.contains('drop-after');
      step.classList.remove('drop-before', 'drop-after');
      // A step only moves inside its own set: `from` names a position in that set's list, so a step
      // carried into another set would copy the wrong stored object.
      if (!moving || moving === step || moving.closest('.set-card') !== step.closest('.set-card')) return;
      step.insertAdjacentElement(after ? 'afterend' : 'beforebegin', moving);
      changed();
    };
    return step;
  }

  async function uploadStep(input, i, j) {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    try {
      const r = await rawSend('POST', 'api/upload?kind=poster&scope=set&slug=' + encodeURIComponent(p.slug) +
        '&name=' + encodeURIComponent(file.name), file, false);
      model = collect();
      model[i].steps[j].image = r.full;
      model[i].steps[j].v = '';
      paint();
      touch();
      toast('Image uploaded — save the set to keep it');
    } catch (e) { toast('Upload failed: ' + e.message, true); }
  }

  /* ---------------------------------------------------------------- one set */
  function setCard(s, i) {
    const card = el('<div class="set-card" data-from="' + (s.from == null ? '' : s.from) + '">' +
      '<span class="grip" title="Drag to reorder">⠿</span>' +
      '<label class="field"><span>Title</span><input type="text" data-f="title" maxlength="120" placeholder="' +
      esc(SET_TITLE_PH) + '" value="' + esc(s.title) + '">' +
      '<small class="fhint">The caption above the row. Empty hides it.</small></label>' +
      '<button type="button" class="btn danger small" data-drop-set title="Remove this set">✕</button>' +
      '<div class="set-steps"></div>' +
      '<div><button type="button" class="btn small" data-add-step>+ Add step</button></div>' +
      '</div>');
    const steps = $('.set-steps', card);
    s.steps.forEach((_st, j) => steps.appendChild(stepCard(i, j, s)));
    const add = $('[data-add-step]', card);
    add.disabled = s.steps.length >= MAX_STEPS;
    add.title = add.disabled ? 'Six steps is the most a row shows' : '';
    add.onclick = () => changed(m => { m[i].steps.push(newStep()); });
    $('[data-drop-set]', card).onclick = () => {
      if (!confirm('Remove this comparison set? Its images move to tools/removed-media when nothing else uses them.')) return;
      changed(m => { m.splice(i, 1); });
    };

    $('.grip', card).onmousedown = () => { card.draggable = true; };
    card.ondragstart = e => {
      if (e.target !== card || !e.dataTransfer) return;   // a step's own drag stops here
      e.dataTransfer.setData(SET_MIME, '1');
      card.classList.add('dragging');
    };
    card.ondragend = e => {
      if (e.target !== card) return;
      card.draggable = false;
      card.classList.remove('dragging');
      clearMarks();
    };
    card.ondragover = e => {
      if (!e.dataTransfer || ![...e.dataTransfer.types].includes(SET_MIME)) return;
      e.preventDefault();
      mark(card, e, false);
    };
    card.ondragleave = () => card.classList.remove('drop-before', 'drop-after');
    card.ondrop = e => {
      if (!e.dataTransfer || e.dataTransfer.getData(SET_MIME) !== '1') return;
      e.preventDefault();
      const moving = [...card.parentElement.children].find(c => c.classList.contains('dragging'));
      const after = card.classList.contains('drop-after');
      card.classList.remove('drop-before', 'drop-after');
      if (!moving || moving === card) return;
      card.insertAdjacentElement(after ? 'afterend' : 'beforebegin', moving);
      changed();
    };
    return card;
  }

  // Sets stack vertically, steps run left to right, so the midpoint differs by axis. A synthetic drag
  // (what the tests dispatch) carries no clientX, and then the vertical half answers instead.
  function mark(node, e, horizontal) {
    const r = node.getBoundingClientRect();
    const after = horizontal && e.clientX ? e.clientX > r.left + r.width / 2
      : e.clientY > r.top + r.height / 2;
    node.classList.toggle('drop-after', after);
    node.classList.toggle('drop-before', !after);
  }

  function clearMarks() {
    $$('.drop-before, .drop-after', section).forEach(n => n.classList.remove('drop-before', 'drop-after'));
  }

  /* --------------------------------------------------------------- the pane */
  function shownOn() {
    const pages = [isStandalone(p) ? p.standalone.split('#')[0] : 'work/' + p.slug + '.html'];
    const live = (S.media && S.media.ai_live) || [];
    if (live.indexOf(p.slug) >= 0 && pages.indexOf('ai.html') < 0) pages.push('ai.html');
    return pages;
  }

  function paint() {
    dirtyLists.delete('sets');
    section.innerHTML = '';
    const missing = model.reduce((n, s) => n + s.steps.filter(st => !st.image).length, 0);
    section.appendChild(el('<summary><span class="sets-head">Real shot → AI step → final</span> ' +
      '<span class="count">' + model.length + '</span></summary>'));
    section.appendChild(el('<p class="hint">Appears on this project\'s page and, when the project is on the AI ' +
      'page\'s "Ran live" list, on AI &amp; Workflow. The label is also the image\'s alt text.</p>'));
    section.appendChild(el('<p class="hint" data-shown-on>Shown on: ' + esc(shownOn().join(', ')) + '</p>'));
    if (missing) {
      section.appendChild(el('<p class="warn-note" data-sets-warn>' + missing + ' step' + (missing === 1 ? '' : 's') +
        ' need' + (missing === 1 ? 's' : '') + ' an image</p>'));
    }
    if (!model.length) {
      section.appendChild(el('<p class="hint" data-sets-empty>No comparison sets. Add one to show a real shot, ' +
        'the AI step and the final side by side.</p>'));
    }
    model.forEach((s, i) => section.appendChild(setCard(s, i)));

    note = el('<p class="vids-note" hidden>Unsaved changes to this list.</p>');
    section.appendChild(note);

    const actions = el('<div class="row"><button class="btn" type="button" data-add-set>+ Add a set</button>' +
      '<button class="btn primary js-save" type="button" data-scope="sets">Save comparison sets</button></div>');
    section.appendChild(actions);
    const add = $('[data-add-set]', actions);
    add.disabled = model.length >= MAX_SETS;
    add.title = add.disabled ? 'Six sets is the most a page shows' : '';
    add.onclick = () => changed(m => { m.push(newSet()); });
    $('.js-save', actions).onclick = save;
  }

  async function save() {
    const rows = collect();
    if (rows.some(r => !r.steps.length)) { toast('A set needs at least one step.', true); return; }
    try {
      const r = await api('PUT', 'api/project/' + p.slug + '/sets', { sets: rows });
      await refresh();
      model = fromState();
      paint();
      renderStatus();
      toast('Comparison sets saved' + (r.files_moved
        ? ' · ' + byLabel(r.files_moved, 'replaced file') + ' moved to tools/removed-media' : ''));
    } catch (e) { toast(e.message, true); }
  }

  // Typing here is a list change, not a form change: keep it away from the pane-level listener.
  section.addEventListener('input', e => { e.stopPropagation(); touch(); });
  section.addEventListener('change', e => { e.stopPropagation(); touch(); });

  model = fromState();
  paint();
  return section;
}
