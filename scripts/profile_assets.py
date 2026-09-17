#!/usr/bin/env python3
"""Self-contained, theme-aware SVGs adapted from the Superdesign identity board.

No external fonts, stylesheets, scripts, or raster assets. Run this file to
regenerate the headers; the activity updater renders the metric panels.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

PALETTES = {
    "dark": {
        "background": "#0b1720", "text": "#f1f4ef", "muted": "#8eaaa9",
        "line": "#427c80", "teal": "#89cec3", "amber": "#eab66b",
    },
    "light": {
        "background": "#f5f4ed", "text": "#162e35", "muted": "#526f71",
        "line": "#83a4a0", "teal": "#246b6b", "amber": "#a55a19",
    },
}
SANS = "Arial, Helvetica, sans-serif"
MONO = "'Courier New', Courier, monospace"


def descent_points() -> list[tuple[float, float]]:
    # f(u, v) = (u² + 4v²) / 2. η=0.42 < 2/λ_max = 0.5.
    # The oscillation across the narrow valley is real, not an arbitrary zigzag.
    u, v = -4.6, -1.7
    points = [(u, v)]
    for _ in range(24):
        u -= 0.42 * u
        v -= 0.42 * 4 * v
        points.append((u, v))
    return points


def render_hero(theme: str, static: bool = False) -> str:
    colors = PALETTES[theme]
    points = descent_points()
    path = " ".join(f"{'M' if i == 0 else 'L'}{52*u:.3f},{52*v:.3f}" for i, (u, v) in enumerate(points))
    ellipses = "\n".join(
        f'<ellipse rx="{radius}" ry="{radius/2:g}" stroke="{colors["teal"]}" stroke-width="{1.6 if i < 2 else 1.1}" opacity="{0.85-i*0.11:.2f}"/>'
        for i, radius in enumerate((40, 85, 135, 190, 250, 310))
    )
    dots = "\n".join(
        f'<circle cx="{52*u:.3f}" cy="{52*v:.3f}" r="{max(2, 4-i*0.3):g}" fill="{colors["amber"]}"/>'
        for i, (u, v) in enumerate(points[:7])
    )
    animation = '''<style>
    .trajectory { stroke-dasharray:1; stroke-dashoffset:0; }
    @media (prefers-reduced-motion: no-preference) {
      .trajectory { animation: converge 12s linear infinite; }
    }
    @keyframes converge {
      0% { stroke-dashoffset:1; opacity:0; }
      5% { opacity:1; }
      72%, 90% { stroke-dashoffset:0; opacity:1; }
      100% { stroke-dashoffset:0; opacity:0; }
    }
  </style>''' if not static else ''
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="460" viewBox="0 0 1200 460" role="img" aria-labelledby="title desc">
  <title id="title">Masaki Ono — Software Engineer</title>
  <desc id="desc">Mathematical contours with a gradient-descent trajectory converging to a minimum. Mathematics, GPU computing, and optimization.</desc>
  <defs>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M40 0H0V40" fill="none" stroke="{colors['line']}" stroke-opacity=".09"/>
    </pattern>
    <clipPath id="plot"><rect x="505" y="0" width="695" height="460"/></clipPath>
  </defs>
{animation}
  <rect width="1200" height="460" fill="{colors['background']}"/>
  <rect width="1200" height="460" fill="url(#grid)"/>
  <g stroke="{colors['line']}" stroke-opacity=".4" fill="none">
    <path d="M504 0V460M0 459.5H1200"/>
    <path d="M504 230H1200M852 0V460" stroke-opacity=".25"/>
  </g>
  <g font-family="{MONO}" fill="{colors['teal']}">
    <path d="M48 60H65" stroke="{colors['teal']}"/>
    <text x="78" y="65" font-size="18" letter-spacing="2">SOFTWARE ENGINEER</text>
  </g>
  <g font-family="{SANS}" font-weight="700" font-size="128" letter-spacing="-7" fill="{colors['text']}">
    <text x="44" y="214">Masaki</text>
    <text x="44" y="322">Ono</text>
  </g>
  <g font-family="{MONO}" font-size="15" fill="{colors['muted']}" letter-spacing="1">
    <text x="48" y="385">MATHEMATICS / GPU / OPTIMIZATION</text>
    <text x="48" y="413">MSK-ONO</text>
    <path d="M128 408H183" stroke="{colors['line']}"/>
  </g>
  <g clip-path="url(#plot)">
    <g transform="translate(852 230) rotate(-15)" fill="none">
      {ellipses}
      <path d="{path}" stroke="{colors['amber']}" stroke-width="2.8" opacity=".22"/>
      <path class="trajectory" d="{path}" pathLength="1" stroke="{colors['amber']}" stroke-width="3.2" stroke-linejoin="round"/>
      {dots}
      <circle r="4.5" fill="{colors['text']}"/>
    </g>
  </g>
  <g font-family="{MONO}" font-size="15" fill="{colors['muted']}">
    <circle cx="969" cy="42" r="4" fill="{colors['amber']}"/>
    <text x="984" y="47">GRADIENT DESCENT</text>
    <text x="1152" y="419" text-anchor="end">x[k+1] = x[k] − η ∇f(x[k])</text>
  </g>
</svg>
'''


def render_stats(snapshot: dict, theme: str, mobile: bool = False) -> str:
    colors = PALETTES[theme]
    counts = snapshot["counts"]
    title = f"Public activity in the last 30 days: {counts['commits']} commits, {counts['prs']} pull requests, {counts['reviews']} reviews"
    if mobile:
        cells = []
        for index, (label, key) in enumerate((("COMMITS", "commits"), ("PRs", "prs"), ("REVIEWS", "reviews"))):
            value = str(counts[key])
            value_size = 66 if len(value) <= 4 else 44
            cells.append(f'''<g transform="translate({index * 200 + 22} 0)">
      <text y="88" font-family="{MONO}" font-size="23" font-weight="700" fill="{colors['teal']}">{label}</text>
      <text y="161" font-family="{SANS}" font-size="{value_size}" font-weight="700" letter-spacing="-2" fill="{colors['text']}">{escape(value)}</text>
    </g>''')
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="192" viewBox="0 0 600 192" role="img" aria-labelledby="title desc">
  <title id="title">{escape(title)}</title>
  <desc id="desc">Public GitHub contributions only. PRs means pull requests.</desc>
  <rect width="600" height="192" fill="{colors['background']}"/>
  <path d="M0 .5H600M0 46H600M0 191.5H600M200 46V192M400 46V192" fill="none" stroke="{colors['line']}" stroke-opacity=".4"/>
  <text x="22" y="30" font-family="{MONO}" font-size="20" letter-spacing="1" fill="{colors['muted']}">PUBLIC ACTIVITY / LAST 30 DAYS</text>
  {''.join(cells)}
</svg>
'''
    cells = []
    icons = (
        '<path d="M0 12H8M24 12H32"/><circle cx="16" cy="12" r="8"/>',
        '<circle cx="6" cy="3" r="4"/><circle cx="26" cy="23" r="4"/><path d="M6 7V28M26 19V9Q26 3 20 3H15M19 -1L15 3L19 7"/>',
        '<path d="M3 0H29V21H15L7 28V21H3Z"/><path d="M10 10L14 14L22 6"/>',
    )
    for index, (label, key) in enumerate((("COMMITS", "commits"), ("PULL REQUESTS", "prs"), ("REVIEWS", "reviews"))):
        x = index * 400 + 38
        value = str(counts[key])
        value_size = 88 if len(value) <= 5 else 68
        cells.append(f'''<g transform="translate({x} 91)">
      <g fill="none" stroke="{colors['teal']}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">{icons[index]}</g>
      <text x="46" y="22" font-family="{MONO}" font-size="25" font-weight="700" fill="{colors['teal']}">{label}</text>
      <text x="0" y="122" font-family="{SANS}" font-size="{value_size}" font-weight="700" letter-spacing="-3" fill="{colors['text']}">{escape(value)}</text>
    </g>''')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="250" viewBox="0 0 1200 250" role="img" aria-labelledby="title desc">
  <title id="title">{escape(title)}</title>
  <desc id="desc">Public GitHub contributions only. Counts follow GitHub contribution rules.</desc>
  <rect width="1200" height="250" fill="{colors['background']}"/>
  <path d="M0 .5H1200M0 53H1200M0 249.5H1200M400 53V250M800 53V250" fill="none" stroke="{colors['line']}" stroke-opacity=".4"/>
  <text x="38" y="34" font-family="{MONO}" font-size="22" letter-spacing="2" fill="{colors['muted']}">PUBLIC ACTIVITY / LAST 30 DAYS</text>
  {''.join(cells)}
</svg>
'''


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "assets"
    root.mkdir(exist_ok=True)
    for name in PALETTES:
        (root / f"hero-{name}.svg").write_text(render_hero(name), encoding="utf-8")
        (root / f"hero-static-{name}.svg").write_text(render_hero(name, static=True), encoding="utf-8")
