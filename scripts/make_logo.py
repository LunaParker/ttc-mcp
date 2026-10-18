#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["fonttools==4.67.0", "uharfbuzz==0.56.3", "resvg-py==0.5.0"]
# ///
"""Draw the ttc-mcp logo, its dark variant, the icon and the social preview card as SVG and PNG.

Text is Inter set as outlines, so no viewer needs the font. The variable font is fetched once from a pinned
google/fonts commit (Inter 4.001) into the cache, or read from --font.
Usage: uv run scripts/make_logo.py [--font 'Inter[opsz,wght].ttf'] [OUT_DIR]   (default docs/logo)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import urllib.request
from pathlib import Path

import resvg_py
import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

INTER_URL = "https://raw.githubusercontent.com/google/fonts/e1d6480102fed30739fead0faee463101f892c8f/ofl/inter/Inter%5Bopsz,wght%5D.ttf"
INTER_SHA256 = "29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031"
# TTC Red (PANTONE 485) and Black (Black C) from the TTC's Signage and Wayfinding Standards, section 3.3.1.
RED, BLACK, WHITE = "#DA251D", "#0A0B0C", "#FFFFFF"
TAGLINE = ("Live answers about Toronto’s transit network,", "for Claude and other MCP clients.")
FOOTER = ("github.com/LunaParker/ttc-mcp", "An independent project, not affiliated with the TTC")
ICON_PX, LOCKUP_PX = (1024, 512, 192, 32), 1600


def inter(path: str | None) -> bytes:
    cache = Path(os.environ.get("TTC_MCP_CACHE", "~/.cache/ttc-mcp")).expanduser() / "fonts" / "Inter-opsz-wght-4.001.ttf"
    src = Path(path) if path else cache
    if not src.exists():
        with urllib.request.urlopen(INTER_URL, timeout=60) as r:
            data = r.read()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
    data = src.read_bytes()
    if not path and hashlib.sha256(data).hexdigest() != INTER_SHA256:
        raise SystemExit(f"{src} is not the pinned Inter 4.001 (sha256 {INTER_SHA256}); delete it to fetch again")
    return data


class Face:
    """One static instance of the variable font: harfbuzz shapes with it and fontTools draws its outlines."""

    def __init__(self, vf: bytes, opsz: float, wght: float):
        buf = io.BytesIO()
        instantiateVariableFont(TTFont(io.BytesIO(vf)), {"opsz": opsz, "wght": wght}).save(buf)  # saving rounds coordinates as Google's static cuts do
        self.tt = TTFont(io.BytesIO(buf.getvalue()))
        self.gs, self.order, self.upm = self.tt.getGlyphSet(), self.tt.getGlyphOrder(), self.tt["head"].unitsPerEm
        self.hb = hb.Font(hb.Face(hb.Blob(buf.getvalue())))


def num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def shape(face: Face, text: str, size: float, x: float = 0.0, y: float = 0.0, tracking: float = 0.0) -> list[dict]:
    """Glyphs of `text` with the baseline at (x, y): each has its glyph name, SVG path data and ink box."""
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(face.hb, buf, {"kern": True})
    s, pen_x, out = size / face.upm, 0, []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        name, ox, oy = face.order[info.codepoint], x + (pen_x + pos.x_offset) * s, y - pos.y_offset * s
        sp, bp = SVGPathPen(face.gs, ntos=num), BoundsPen(face.gs)
        face.gs[name].draw(TransformPen(sp, (s, 0, 0, -s, ox, oy)))
        face.gs[name].draw(bp)
        b = bp.bounds
        out.append({"name": name, "d": sp.getCommands(), "bb": (ox + b[0] * s, oy - b[3] * s, ox + b[2] * s, oy - b[1] * s) if b else None})
        pen_x += pos.x_advance + tracking * face.upm
    return out


def ink(glyphs: list[dict]) -> tuple[float, float, float, float]:
    bbs = [g["bb"] for g in glyphs if g["bb"]]
    return min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs)


def text(glyphs: list[dict], fill: str) -> str:
    return f'<path fill="{fill}" d="{"".join(g["d"] for g in glyphs)}"/>'


def ring(x: float, y: float, R: float, w: float, colour: str) -> str:
    return f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(R - w / 2)}" fill="none" stroke="{colour}" stroke-width="{num(w)}"/>'


def route(points: list[tuple[float, float]], stops: list[tuple[float, float]], t: float, R: float, w: float, line: str, rings: str) -> str:
    """A line through `points` with hollow ring stops: segments end on the ring's stroke centreline, so the ring's centre stays transparent."""
    def seg(a, b):
        L = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        ta, tb = (R - w / 2 if a in stops else 0), (R - w / 2 if b in stops else 0)
        return f'<path d="M{num(a[0] + ux * ta)} {num(a[1] + uy * ta)}L{num(b[0] - ux * tb)} {num(b[1] - uy * tb)}" stroke="{line}" stroke-width="{num(t)}" fill="none"/>'
    return "".join(seg(a, b) for a, b in zip(points, points[1:], strict=False)) + "".join(ring(x, y, R, w, rings) for x, y in stops)


def lockup(face: Face, size: float, x: float = 0.0, y: float = 0.0) -> dict:
    """The word on its route line: the line as thick as 0.9 of Inter's stem, stops at the portfolio card's proportions,
    end stops flush with the ink and the middle stop under the hyphen."""
    word = shape(face, "ttc-mcp", size, x, y, tracking=-0.01)
    x0, top, x1, bot = ink(word)
    lx0, _, lx1, _ = ink(shape(face, "l", size))
    hy = next(g["bb"] for g in word if g["name"].startswith("hyphen"))
    t = 0.9 * (lx1 - lx0)
    R, w, yc = 1.1 * t, 0.42 * t, bot + 0.07 * size + t / 2
    return {"word": word, "ink": (x0, top, x1, bot), "t": t, "R": R, "w": w, "yc": yc, "stops": [(x0 + R, yc), ((hy[0] + hy[2]) / 2, yc), (x1 - R, yc)]}


def svg(vb: tuple[float, float, float, float], body: str, label: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{" ".join(num(v) for v in vb)}" role="img" aria-label="{label}">'
            f"<title>{label}</title>{body}</svg>\n")


def logo(face: Face, fg: str) -> str:
    L, pad = lockup(face, 200), 24
    x0, top, x1, _ = L["ink"]
    body = text(L["word"], fg) + route(L["stops"], L["stops"], L["t"], L["R"], L["w"], RED, fg)
    return svg((x0 - pad, top - pad, x1 - x0 + 2 * pad, L["yc"] + L["R"] - top + 2 * pad), body, "ttc-mcp")


def icon() -> str:
    """A black tile with a red line stepping up 45 degrees between two white stops."""
    S, d = 512, 72
    stops = [(S / 2 - d, S / 2 + d), (S / 2 + d, S / 2 - d)]
    body = f'<rect width="{S}" height="{S}" rx="{num(S * 0.2)}" fill="{BLACK}"/>' + route([(0, S / 2 + d), *stops, (S, S / 2 - d)], stops, 60, 84, 34, RED, WHITE)
    return svg((0, 0, S, S), body, "ttc-mcp")


def social(word: Face, tag: Face, foot: Face) -> str:
    """GitHub's 1280 x 640 social preview: the logo in white and black on TTC Red, its route line running edge to edge."""
    W, H, m, size, base = 1280, 640, 88, 244, 580
    L = lockup(word, size, m, 0)
    L = lockup(word, size, 2 * m - L["ink"][0], m - L["ink"][1])
    body = f'<rect width="{W}" height="{H}" fill="{RED}"/>' + text(L["word"], WHITE)
    body += route([(0, L["yc"]), *L["stops"], (W, L["yc"])], L["stops"], L["t"], L["R"], L["w"], BLACK, WHITE)
    y = L["yc"] + L["R"] + 76
    for line in TAGLINE:
        g = shape(tag, line, 38, m, y)
        body += text(shape(tag, line, 38, 2 * m - ink(g)[0], y), WHITE)
        y += 50
    right = shape(foot, FOOTER[1], 22, 0, base)
    body += text(shape(foot, FOOTER[0], 22, m, base), BLACK) + text(shape(foot, FOOTER[1], 22, W - m - ink(right)[2], base), BLACK)
    return svg((0, 0, W, H), body, f"ttc-mcp: {' '.join(TAGLINE)}")


def png(svg_text: str, out: Path, width: int) -> None:
    out.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=svg_text, width=width, skip_system_fonts=True)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Draw the ttc-mcp logo, icon and social preview card as SVG and PNG.")
    ap.add_argument("out", nargs="?", default=str(Path(__file__).resolve().parent.parent / "docs" / "logo"))
    ap.add_argument("--font", help="a local copy of Inter[opsz,wght].ttf instead of the pinned download")
    a = ap.parse_args()
    out, vf = Path(a.out), inter(a.font)
    out.mkdir(parents=True, exist_ok=True)
    word, tag, foot = Face(vf, 28, 800), Face(vf, 28, 500), Face(vf, 18, 500)
    files = {"ttc-mcp": (logo(word, BLACK), [("", LOCKUP_PX)]), "ttc-mcp-dark": (logo(word, WHITE), [("", LOCKUP_PX)]),
             "ttc-mcp-icon": (icon(), [(f"-{px}", px) for px in ICON_PX]), "ttc-mcp-social": (social(word, tag, foot), [("", 1280)])}
    for name, (doc, pngs) in files.items():
        (out / f"{name}.svg").write_text(doc)
        for suffix, px in pngs:
            png(doc, out / f"{name}{suffix}.png", px)
        print(f"{name}.svg and {len(pngs)} png")


if __name__ == "__main__":
    main()
