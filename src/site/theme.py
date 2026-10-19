"""Site theme: CSS and color palette.

Kept as a Python string constant (not a separate CSS file) so the CSS
ships inside the package and cannot drift from the templates that
reference it. The HTML templates themselves live as real ``.html`` files
under ``src/site/templates/`` — a one-line copy change there produces a
one-line diff, which a CSS-in-python-string would not.
"""

from __future__ import annotations

# Community palette. Muted, distinguishable, drawn from a paper-figure
# aesthetic rather than a tool default. Communities beyond the palette
# size fall back to COMMUNITY_FALLBACK (grey), which is honest: those are
# almost always singletons from isolated nodes.
COMMUNITY_COLORS: tuple[str, ...] = (
    "#1f4e79",  # deep blue
    "#a63d2f",  # brick red
    "#5a7d3a",  # forest green
    "#7a5e9b",  # muted purple
    "#b07c2f",  # amber
    "#3a7c7c",  # teal
    "#8b4a6a",  # mauve
    "#4a6f9c",  # mid blue
    "#7a6a3a",  # olive brown
    "#6a5a8b",  # muted violet
    "#b06060",  # muted coral
    "#4a7a5a",  # sage
    "#8a6a4a",  # taupe
    "#5a6a7a",  # slate
    "#a07090",  # dusty rose
)
COMMUNITY_FALLBACK = "#bbbbbb"

# Background for embedded charts (plots, pyvis containers).
CHART_BG = "#fafafa"

CSS = """
/* Site stylesheet — single-page-per-view GitHub Pages build.

   Base typography and table styling are inherited from the project's
   predecessor (ph-republic-acts-de) so the two sites read as one author's
   work. Additional rules support the multi-page graph site: a top nav,
   figure blocks, and the pyvis/graph container.
*/

body {
  max-width: 74ch;
  margin: 2em auto;
  padding: 0 1em;
  font-family: sans-serif;
  line-height: 1.5;
  color: #222;
}

h1, h2, h3 { line-height: 1.25; }

h2 {
  margin-top: 2em;
  padding-bottom: 0.2em;
  border-bottom: 1px solid #ccc;
  font-size: 1.3em;
}

p.tagline { color: #555; }
a { color: #1f4e79; }

code {
  font-family: monospace;
  font-size: 0.95em;
  background: #f4f4f4;
  padding: 0 0.2em;
}

small.source {
  display: block;
  margin-top: 0.3em;
  color: #666;
  font-size: 0.85em;
}

aside {
  border-left: 3px solid #bbb;
  margin: 1em 0;
  padding: 0 1em;
  color: #444;
}

nav.site-nav {
  border-bottom: 1px solid #ccc;
  margin-bottom: 1.5em;
  padding-bottom: 0.8em;
  font-size: 0.95em;
}
nav.site-nav a { margin-right: 1.2em; }
nav.site-nav a.active { color: #222; font-weight: bold; }

/* Graph and temporal pages use a wider content column — a 74ch body
   squeezes a 300-node graph into ~600px, which reads as a hairball
   regardless of node/edge styling. Prose elsewhere on the site stays
   at 74ch for readability. */
body.wide-view {
  max-width: 1200px;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
}
th, td {
  border: 1px solid #ccc;
  padding: 4px 8px;
  text-align: left;
  vertical-align: top;
}
th { background: #f4f4f4; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }

figure { margin: 1.5em 0; }
figure img {
  max-width: 100%;
  height: auto;
  border: 1px solid #ccc;
  background: #fafafa;
}
figcaption {
  font-size: 0.9em;
  color: #555;
  margin-top: 0.4em;
}

.graph-container {
  border: 1px solid #ccc;
  background: #fafafa;
  margin: 1em 0;
}
iframe.graph-iframe {
  width: 100%;
  border: 1px solid #ccc;
  display: block;
}

ul.findings { padding-left: 1.2em; }
ul.findings li { margin-bottom: 0.8em; }

footer {
  margin-top: 3em;
  border-top: 1px solid #ccc;
  padding-top: 1em;
  color: #555;
  font-size: 0.9em;
}
"""