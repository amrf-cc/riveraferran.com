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
- **Accent**: `--accent #1B4DFF` (cobalt). To switch to tomato, change the three
  accent values at the top of `styles.css` to `#E2401C` / `#C2300F` / `#FDECE7`.
- **Fonts**: Fraunces (display) + Inter (body), self-hosted in `assets/fonts/` and
  declared in `assets/css/fonts.css`. No third-party font requests. To refresh them,
  run `python3 fetch_fonts.py`.
- **Photos are shown at their native ratios** — cards and photo grids use CSS
  masonry (`columns`), not fixed aspect-ratio boxes, so portrait food shots are not
  cropped. If you add a landscape hero image, set `--hero-aspect: 3 / 2`.
- **Social share card**: `assets/img/og-cover.jpg` (1200×630), referenced by the
  `og:image` tags on every page. Regenerate by rendering the card at 1200×630 and
  saving over it.
- **Favicon**: inline SVG cobalt monogram in each page `<head>`.

