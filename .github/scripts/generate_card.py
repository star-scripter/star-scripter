"""Builds the profile cards in assets/ from config.json and GitHub stats.

Without GITHUB_TOKEN it reuses assets/stats.json.
"""
import base64
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

WIDTH, HEIGHT = 900, 450
MONO = "Consolas, 'SFMono-Regular', Menlo, 'DejaVu Sans Mono', monospace"
SANS = "'Segoe UI', -apple-system, Helvetica, Arial, sans-serif"

THEMES = {
    "dark": {"bg": "#0d1117", "border": "#30363d", "text": "#e6edf3", "muted": "#8b949e",
             "accent": "#58a6ff", "art": "#c9d1d9"},
    "light": {"bg": "#ffffff", "border": "#d0d7de", "text": "#1f2328", "muted": "#59636e",
              "accent": "#0969da", "art": "#24292f"},
}

# Octicons (MIT, github.com/primer/octicons), 16px grid.
ICON_REPO = ("M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 "
             "0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 "
             "0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 "
             ".25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z")
ICON_PEOPLE = ("M2 5.5a3.5 3.5 0 1 1 5.898 2.549 5.508 5.508 0 0 1 3.034 4.084.75.75 0 1 1-1.482.235 4 4 0 0 "
               "0-7.9 0 .75.75 0 0 1-1.482-.236A5.507 5.507 0 0 1 3.102 8.05 3.49 3.49 0 0 1 2 5.5ZM11 4a3.001 "
               "3.001 0 0 1 2.22 5.018 5.01 5.01 0 0 1 2.56 3.012.749.749 0 0 1-.885.954.752.752 0 0 1-.549-.514 "
               "3.507 3.507 0 0 0-2.522-2.372.75.75 0 0 1-.574-.73v-.352a.75.75 0 0 1 .416-.672A1.5 1.5 0 0 0 11 "
               "5.5.75.75 0 0 1 11 4Zm-5.5-.5a2 2 0 1 0-.001 3.999A2 2 0 0 0 5.5 3.5Z")


def graphql(query, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "profile-card"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]["user"]


def fetch_stats(login, token):
    user = graphql(f"""{{ user(login: "{login}") {{
        repositories(ownerAffiliations: OWNER, privacy: PUBLIC) {{ totalCount }}
        contributionsCollection {{ contributionYears
            contributionCalendar {{ weeks {{ contributionDays {{ date contributionCount }} }} }} }} }} }}""", token)

    # contributionsCollection is limited to one year per query
    years = "\n".join(
        f'y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{y}-12-31T23:59:59Z") '
        "{ contributionCalendar { totalContributions } }"
        for y in user["contributionsCollection"]["contributionYears"]
    )
    per_year = graphql(f'{{ user(login: "{login}") {{ {years} }} }}', token) if years else {}
    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    return {
        "repositories": user["repositories"]["totalCount"],
        "contributions": sum(v["contributionCalendar"]["totalContributions"] for v in per_year.values()),
        "days": [[d["date"], d["contributionCount"]] for w in weeks for d in w["contributionDays"]],
    }


def load_stats():
    path = ASSETS / "stats.json"
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        stats = fetch_stats(CONFIG["github"], token)
        stats["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path.write_text(json.dumps(stats) + "\n", encoding="utf-8")
        return stats
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"repositories": 0, "contributions": 0}


def name_font_face():
    # SVGs in a README can't load web fonts, so embed a subset with just the name's letters
    family, weight = CONFIG["name_font"]["family"], CONFIG["name_font"]["weight"]
    text = "".join(sorted(set(CONFIG["name"].replace(" ", ""))))
    key = hashlib.sha1(f"{family}|{weight}|{text}".encode()).hexdigest()[:10]
    path = ASSETS / "fonts" / f"name-{key}.woff2"
    if not path.exists():
        try:
            url = (f"https://fonts.googleapis.com/css2?family={urllib.parse.quote_plus(family)}:wght@{weight}"
                   f"&text={urllib.parse.quote(text)}")
            # needs a browser UA to get woff2
            ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"}
            with urllib.request.urlopen(urllib.request.Request(url, headers=ua), timeout=30) as resp:
                font_url = re.search(r"url\((\S+?)\)", resp.read().decode()).group(1)
            with urllib.request.urlopen(urllib.request.Request(font_url, headers=ua), timeout=30) as resp:
                data = resp.read()
        except (OSError, AttributeError) as err:
            print(f"warning: could not fetch {family} ({err}); falling back to system fonts")
            return ""
        path.parent.mkdir(exist_ok=True)
        for stale in path.parent.glob("name-*.woff2"):
            stale.unlink()
        path.write_bytes(data)
    data = base64.b64encode(path.read_bytes()).decode()
    return (f"@font-face{{font-family:'{family}';font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")


def bracket(x, y1, y2, side, color):
    # side 1 -> "[", side -1 -> "]"
    arm = 16 * side
    return (f'<path d="M{x + arm} {y1} H{x} V{y2} H{x + arm}" fill="none" stroke="{color}" '
            f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')


def render(theme, art, stats, font_face):
    t = THEMES[theme]
    name_lines = CONFIG["name"].split()
    name_font = CONFIG["name_font"]
    name_size = min(80, int(290 / (0.6 * max(map(len, name_lines)))))
    name_gap = name_size * 1.05
    box_top, box_h = 130, max(150, len(name_lines) * name_gap + 50)
    name_y = box_top + (box_h - (len(name_lines) - 1) * name_gap) / 2 + name_size * 0.35

    art_w, art_h = (float(v) for v in re.search(r'width="([\d.]+)" height="([\d.]+)"', art).groups())
    art_x, art_y = WIDTH - 40 - art_w, (HEIGHT - art_h) / 2

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
           f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(CONFIG["name"])} - GitHub profile card">',
           "<style>"
           f"{font_face}"
           f".mono{{font-family:{MONO}}} .sans{{font-family:{SANS}}} "
           f".name{{font-family:'{name_font['family']}',{MONO};font-weight:{name_font['weight']}}}"
           "</style>",
           f'<rect x="1.5" y="1.5" width="{WIDTH - 3}" height="{HEIGHT - 3}" rx="14" '
           f'fill="{t["bg"]}" stroke="{t["border"]}" stroke-width="2"/>']

    for i, (label, handle) in enumerate([("in", CONFIG["linkedin"]), ("gh", CONFIG["github"])]):
        out.append(f'<text x="48" y="{70 + i * 28}" class="mono" font-size="17" fill="{t["muted"]}">[@'
                   f'<tspan fill="{t["accent"]}">{escape(handle)}</tspan>]'
                   f'<tspan font-size="12" dx="8">{label}</tspan></text>')

    out.append(bracket(48, box_top, box_top + box_h, 1, t["text"]))
    out.append(bracket(380, box_top, box_top + box_h, -1, t["text"]))
    for i, line in enumerate(name_lines):
        out.append(f'<text x="214" y="{name_y + i * name_gap:.1f}" text-anchor="middle" class="name" '
                   f'font-size="{name_size}" fill="{t["text"]}">{escape(line)}</text>')

    stats_y = HEIGHT - 52
    for x, icon, value, label in [(50, ICON_REPO, stats["repositories"], "repositories"),
                                  (225, ICON_PEOPLE, stats["contributions"], "contributions")]:
        out.append(f'<g transform="translate({x} {stats_y - 15}) scale(1.15)"><path d="{icon}" fill="{t["muted"]}"/></g>')
        out.append(f'<text x="{x + 26}" y="{stats_y}" class="sans" font-size="16" fill="{t["muted"]}">'
                   f'<tspan font-weight="700" fill="{t["text"]}">{value:,}</tspan> {label}</text>')

    out.append(art.replace("<svg ", f'<svg x="{art_x:.1f}" y="{art_y:.1f}" ', 1))
    out.append("</svg>")
    return "\n".join(out) + "\n"


CITY_H = 280
CITY_LEVELS = {
    "dark": ["#1a2b27", "#1d6b52", "#26a37a", "#38d39f", "#7cf5c8"],
    "light": ["#e4eee9", "#a7e3c6", "#5cc79a", "#26a172", "#137a53"],
}
WEEK_AXIS, DAY_AXIS = (8.9, -1.6), (6.0, 4.0)


def shade(hex_color, f):
    return "#" + "".join(f"{min(255, int(int(hex_color[i:i + 2], 16) * f)):02x}" for i in (1, 3, 5))


def city_grid(days):
    # (week, weekday, count), weeks start on Sunday
    if not days:
        return [(w, d, 0) for w in range(53) for d in range(7)]
    offset = (datetime.fromisoformat(days[0][0]).weekday() + 1) % 7
    return [((offset + k) // 7, (offset + k) % 7, count) for k, (_, count) in enumerate(days)]


def render_city(theme, stats):
    t, levels = THEMES[theme], CITY_LEVELS[theme]
    cells = city_grid(stats.get("days"))
    counts = [c for _, _, c in cells]
    total, busiest = sum(counts), max(counts, default=0)
    active = sum(1 for c in counts if c)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{CITY_H}" '
           f'viewBox="0 0 {WIDTH} {CITY_H}" role="img" aria-label="{total} contributions in the last year">',
           f"<style>.mono{{font-family:{MONO}}}</style>",
           '<defs><filter id="glow" x="-5%" y="-5%" width="110%" height="110%">'
           '<feGaussianBlur stdDeviation="2.2" result="b"/>'
           '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>',
           f'<rect x="1.5" y="1.5" width="{WIDTH - 3}" height="{CITY_H - 3}" rx="14" '
           f'fill="{t["bg"]}" stroke="{t["border"]}" stroke-width="2"/>',
           f'<text x="48" y="62" class="mono" font-size="22" font-weight="700" fill="{t["text"]}">'
           "The city I built this year</text>",
           f'<text x="48" y="112" class="mono" font-size="13" letter-spacing="1.5" fill="{levels[3]}">CONTRIBUTIONS</text>',
           f'<text x="48" y="160" class="mono" font-size="44" font-weight="700" fill="{t["text"]}">{total:,}</text>',
           f'<text x="48" y="196" class="mono" font-size="15" fill="{t["muted"]}">{active} active days</text>',
           f'<text x="48" y="222" class="mono" font-size="15" fill="{t["muted"]}">Busiest day: {busiest}</text>',
           f'<path d="M868 22 l2.2 6.8 6.8 2.2 -6.8 2.2 -2.2 6.8 -2.2 -6.8 -6.8 -2.2 6.8 -2.2Z" '
           f'fill="{levels[4]}" opacity=".8"/>']

    ox, oy = 345, 235
    def point(u, v, h=0):
        return f"{ox + u * WEEK_AXIS[0] + v * DAY_AXIS[0]:.1f},{oy + u * WEEK_AXIS[1] + v * DAY_AXIS[1] - h:.1f}"

    # quartiles of active days, same idea as the contribution graph
    active_counts = sorted(c for c in counts if c)
    cuts = [active_counts[len(active_counts) * q // 4] for q in (1, 2, 3)] if active_counts else []

    # draw back to front
    gap = 0.12
    towers = []
    for w, d, count in sorted(cells, key=lambda c: (c[1], -c[0])):
        level = 0 if not count else 1 + sum(count >= cut for cut in cuts)
        h = 1.5 if not count else 4 + 92 * (count / busiest) ** 0.6
        top = levels[level]
        u0, u1, v0, v1 = w + gap, w + 1 - gap, d + gap, d + 1 - gap
        towers.append(
            f'<polygon points="{point(u0, v0)} {point(u0, v1)} {point(u0, v1, h)} {point(u0, v0, h)}" fill="{shade(top, 0.72)}"/>'
            f'<polygon points="{point(u0, v1)} {point(u1, v1)} {point(u1, v1, h)} {point(u0, v1, h)}" fill="{shade(top, 0.52)}"/>'
            f'<polygon points="{point(u0, v0, h)} {point(u1, v0, h)} {point(u1, v1, h)} {point(u0, v1, h)}" fill="{top}"/>')
    glow = ' filter="url(#glow)"' if theme == "dark" else ""
    out.append(f"<g{glow}>" + "".join(towers) + "</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def read_art(style, theme):
    return (ASSETS / "art" / f"{style}-{theme}.svg").read_text(encoding="utf-8")


def main():
    stats = load_stats()
    style = CONFIG["art"]["style"]
    font_face = name_font_face()
    for theme in THEMES:
        (ASSETS / f"card-{theme}.svg").write_text(
            render(theme, read_art(style, theme), stats, font_face), encoding="utf-8")
        (ASSETS / f"city-{theme}.svg").write_text(render_city(theme, stats), encoding="utf-8")
    print(f"rendered {style} cards: {stats['repositories']} repos, {stats['contributions']} contributions")


if __name__ == "__main__":
    main()
