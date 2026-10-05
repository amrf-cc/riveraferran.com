# riveraferran.com

Food & beverage portfolio of Adrian Rivera Ferran. Static site, generated in place.

- `python3 prepare_assets.py --check` — verify source media on the Selected Works volume
- `python3 prepare_assets.py` — compress selected photos/videos into `assets/`
- `python3 build.py` — regenerate all HTML pages (commit the output)
- `python3 -m unittest discover -s tests -v` — run tests

Deploy: GitHub Pages serves this repo's `main` branch root at https://riveraferran.com.
Videos: Livid embeds when configured in projects.json, else self-hosted MP4.
Form: Formspree endpoint in site-config.json, mailto fallback when empty.

## Design system

Light editorial theme: warm paper ground, ink type, one bold accent. Everything is
driven by CSS custom properties at the top of `assets/css/styles.css`.

- **Ground / type**: `--paper #F7F5F0`, `--ink #131210`, `--muted #6B6960`, hairlines `--line`.
- **Accent**: `--accent #E2401C` (tomato). To switch to cobalt, change the three
  accent values at the top of `styles.css` to `#1B4DFF` / `#0F35D6` / `#EBEFFF`.
- **Fonts**: Fraunces (display) + Inter (body), self-hosted in `assets/fonts/` and
  declared in `assets/css/fonts.css`. No third-party font requests. To refresh them,
  run `python3 fetch_fonts.py`.
- **Photos are shown at their native ratios** — cards and photo grids use CSS
  masonry (`columns`), not fixed aspect-ratio boxes, so portrait food shots are not
  cropped. The hero photograph is now also shown uncropped at its natural ratio;
  the `--hero-aspect` property no longer exists.
- **Social share card**: `assets/img/og-cover.jpg` (1200×630), referenced by the
  `og:image` tags on every page. Regenerate by rendering the card at 1200×630 and
  saving over it.
- **Favicon**: inline SVG tomato monogram in each page `<head>`.

