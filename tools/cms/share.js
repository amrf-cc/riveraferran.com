/* The share card — the one image behind every link preview (site-config -> og_image).

   The `share` upload scope crops server-side to exactly 1200x630, the size head.html.tmpl promises
   through og:image:width/height, so what this section previews is what a link wrapper gets. The image
   saves itself: the upload is already on disk, so there is no "save the page" step for it.

   Loaded after pages.js (the Identity pane must exist before a section can register on it) and before
   site.js, which boots. */
'use strict';

const SHARE_DEFAULT = 'assets/img/og-cover.jpg';      // the card the code ships with (build.py)

function shareCardSection(body) {
  const current = String((S.config || {}).og_image || '');
  body.appendChild(el('<p class="hint" data-share-caption>Shown when the site is shared on social ' +
    'media, iMessage and Slack. 1200×630.</p>'));
  const field = imageField({
    label: 'Share card', key: 'og_image', value: current, scope: 'share',
    hint: 'Centre-cropped to exactly 1200×630 and saved as soon as the upload finishes.',
    onChange: saveShareCard,
  });
  field.classList.add('share-card');
  field.setAttribute('data-share-card', current || SHARE_DEFAULT);
  // This image saves itself, so it stays out of the page's own "Save identity and sharing" delta.
  const input = $('[data-cfg]', field);
  if (input) input.removeAttribute('data-cfg');
  if (!current) {
    // Nothing stored: the site falls back to the card it ships with, so that is what the preview shows.
    const img = $('img', field);
    img.src = media(SHARE_DEFAULT);
    img.hidden = false;
    $('.img-none', field).hidden = true;
    $('.img-path', field).textContent = SHARE_DEFAULT + ' (default)';
  }
  const clear = $('[data-clear]', field);
  if (clear) {
    clear.textContent = 'Use the default';
    clear.title = 'Go back to the share card the site ships with';
    clear.hidden = !current;
    clear.style.display = current ? '' : 'none';      // .btn is inline-flex, so [hidden] alone is not enough
  }
  body.appendChild(field);
}

// Repaint this section only: the rest of the Identity pane may hold unsaved typing, and a new share
// card does not touch it.
function repaintShareCard() {
  const host = $('#pane .acc[data-sec="share"] .acc-body');
  if (!host) return;
  host.innerHTML = '';
  shareCardSection(host);
}

async function saveShareCard(path) {
  try {
    await api('POST', 'api/config', { og_image: path || '' });
    await refresh();
    repaintShareCard();
    // The card itself is saved; drop the "Unsaved changes" pill only when nothing else on the page moved.
    if (typeof markDirty === 'function' && !Object.keys(pageBody(pane)).length) markDirty(false);
    toast(path ? 'Share card replaced' : 'Back to the original share card');
  } catch (e) { toast(e.message, true); }
}

PageSections.add('identity', {
  id: 'share', title: 'Share card', order: 90, sub: 'The image on every link preview',
  build: b => shareCardSection(b),
});
