"""Draw a sheet figure from what the file states about it.

A drawing on a worksheet is not stored as a picture; it is stored as the
parts it is made of, and every part states where it sits (``a:off``), how
big it is (``a:ext``), what form it takes (a preset name, or an explicit
path), what fills it and what outlines it. That is enough to put the drawing
back together, so dokey does -- as SVG, which is the same statement in
another notation rather than a rendering of it.

This is deliberately not a call to an office suite. dokey ships no engine
and imports none; a renderer would be a third BYO seam, would need Excel or
LibreOffice present, and would return pixels where the file offered
geometry. What is transcribed here is exact: positions, sizes, custom paths,
colours, and the words. What is approximated is named -- a preset form this
module has no formula for is drawn as its own bounding box, and the figure
records how many parts that happened to, so an approximation is never
mistaken for the original.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

# EMU per pixel at 96 dpi: the unit every offset and extent is stated in.
EMU_PER_PX = 9525
# A figure is drawn at its own size, but a huge one is scaled to keep the
# file sensible for a viewer; the geometry is unchanged, only the canvas.
MAX_EDGE_PX = 2000


def _px(value: float) -> float:
    return round(value / EMU_PER_PX, 2)


def _round(value: float) -> float:
    return round(value, 2)


# The preset dash vocabularies, spelled as SVG dash arrays. An unknown name
# still draws dashed -- the file said "not solid", and that much is kept.
_DASHES = {
    "dash": "6 4",
    "lgDash": "10 4",
    "sysDash": "4 3",
    "dot": "1.5 3",
    "sysDot": "1.5 3",
    "dashDot": "6 4 1.5 4",
    "lgDashDot": "10 4 1.5 4",
    "lgDashDotDot": "10 4 1.5 4 1.5 4",
    "sysDashDot": "4 3 1.5 3",
    "sysDashDotDot": "4 3 1.5 3 1.5 3",
}


def _stroke(part: dict) -> str:
    """The outline only. Fill is stated separately, never twice."""
    colour = part.get("_line") or "444444"
    width = part.get("_line_w")
    width_px = max(0.5, _px(width)) if width else 1
    dash_name = part.get("_dash")
    dash = ""
    if dash_name and dash_name != "solid":
        dash = f' stroke-dasharray="{_DASHES.get(dash_name, "6 4")}"'
    return f'stroke="#{colour}" stroke-width="{width_px}"{dash}'


def _fill(part: dict) -> str:
    gradient = part.get("_gradient_id")
    if gradient:
        return f'fill="url(#{gradient})"'
    colour = part.get("_fill")
    return f'fill="#{colour}"' if colour else 'fill="none"'


def _gradient_def(part: dict, ident: str) -> str:
    """A shaded fill, transcribed stop by stop.

    Vertical, which is the axis a level is shaded along and the default when
    the file states no angle of its own.
    """
    stops = "".join(
        f'<stop offset="{_round(position * 100)}%" stop-color="#{colour}" '
        f'stop-opacity="{_round(opacity)}"/>'
        for position, colour, opacity in part["_grad"]
    )
    return (
        f'<linearGradient id="{ident}" x1="0" y1="0" x2="0" y2="1">{stops}'
        "</linearGradient>"
    )


def _custom_path(part: dict, x: float, y: float, w: float, h: float) -> str | None:
    """Transcribe an explicit path, scaled from its own space to the box."""
    path = part.get("_path")
    if not path:
        return None
    space_w = part.get("_path_w") or 0
    space_h = part.get("_path_h") or 0
    if not space_w or not space_h:
        return None
    scale_x, scale_y = w / space_w, h / space_h
    commands: list[str] = []
    for verb, points in path:
        mapped = " ".join(
            f"{_round(x + px * scale_x)},{_round(y + py * scale_y)}"
            for px, py in points
        )
        if verb == "moveTo":
            commands.append(f"M {mapped}")
        elif verb == "lnTo":
            commands.append(f"L {mapped}")
        elif verb == "cubicBezTo":
            commands.append(f"C {mapped}")
        elif verb == "quadBezTo":
            commands.append(f"Q {mapped}")
        elif verb == "close":
            commands.append("Z")
    if not commands:
        return None
    return " ".join(commands)


def _preset(part: dict, x: float, y: float, w: float, h: float) -> tuple[str, bool]:
    """The stated preset form, drawn. False when only its box could be."""
    name = (part.get("shape") or "").lower()
    fill, stroke = _fill(part), _stroke(part)
    if name.startswith(("line", "straightconnector")):
        return (
            f'<line x1="{x}" y1="{y}" x2="{_round(x + w)}" '
            f'y2="{_round(y + h)}" {stroke}/>',
            True,
        )
    if name.startswith(("bentconnector", "curvedconnector")):
        # The connector's own curvature is not stated as a path; its ends
        # are, and a smooth link between them is what it draws.
        return (
            f'<path d="M {x},{y} Q {_round(x + w)},{y} {_round(x + w)},'
            f'{_round(y + h)}" fill="none" {stroke}/>',
            True,
        )
    if name in ("ellipse", "flowchartconnector"):
        return (
            f'<ellipse cx="{_round(x + w / 2)}" cy="{_round(y + h / 2)}" '
            f'rx="{_round(w / 2)}" ry="{_round(h / 2)}" {fill} {stroke}/>',
            True,
        )
    if name in ("flowchartmagneticdisk", "can", "cylinder"):
        # A cylinder: the rim states the form, and it is the form a vessel
        # on an engineering sheet is drawn with.
        lid = _round(min(h / 6, w / 4))
        body = (
            f'<path d="M {x},{_round(y + lid)} L {x},{_round(y + h - lid)} '
            f"A {_round(w / 2)},{lid} 0 0 0 {_round(x + w)},{_round(y + h - lid)} "
            f'L {_round(x + w)},{_round(y + lid)}" {fill} {stroke}/>'
        )
        rim = (
            f'<ellipse cx="{_round(x + w / 2)}" cy="{_round(y + lid)}" '
            f'rx="{_round(w / 2)}" ry="{lid}" {fill} {stroke}/>'
        )
        return body + rim, True
    if name in ("rect", "roundrect", "textbox", "flowchartprocess", "custom"):
        radius = ' rx="4"' if name == "roundrect" else ""
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{radius} '
            f"{fill} {stroke}/>",
            name != "custom",
        )
    # An unknown form: its box is stated, its outline is not invented.
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" '
        f'stroke="#bbbbbb" stroke-dasharray="4 3"/>',
        False,
    )


def _text(part: dict, x: float, y: float, w: float, h: float) -> str:
    words = part.get("text")
    if not words:
        return ""
    size = part.get("_font") or 11
    return (
        f'<text x="{_round(x + w / 2)}" y="{_round(y + h / 2)}" text-anchor="middle" '
        f'dominant-baseline="middle" font-size="{size}" '
        f'font-family="sans-serif" fill="#111111">{escape(words)}</text>'
    )


def _cropped_image(
    part: dict,
    name: str,
    index: int,
    x: float,
    y: float,
    w: float,
    h: float,
) -> tuple[str, str]:
    """Draw the window of the source bitmap that the sheet displays."""
    if part.get("_crop_applied"):
        return "", ""
    crop = part.get("_crop") or {}
    try:
        left, top, right, bottom = (
            float(crop.get(side) or 0) for side in ("l", "t", "r", "b")
        )
    except (AttributeError, TypeError, ValueError):
        return "", ""
    visible_w = 1 - left - right
    visible_h = 1 - top - bottom
    if visible_w <= 0 or visible_h <= 0 or not any((left, top, right, bottom)):
        return "", ""

    clip_id = f"crop{index}"
    definition = (
        f'<clipPath id="{clip_id}" clipPathUnits="userSpaceOnUse">'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}"/>'
        "</clipPath>"
    )
    image_w = w / visible_w
    image_h = h / visible_h
    image_x = x - left * image_w
    image_y = y - top * image_h
    image = (
        f'<image href="{escape(name)}" x="{_round(image_x)}" '
        f'y="{_round(image_y)}" width="{_round(image_w)}" '
        f'height="{_round(image_h)}" preserveAspectRatio="none"/>'
    )
    clipped = f'<g clip-path="url(#{clip_id})">{image}</g>'
    return definition, clipped


def figure_svg(parts: list[dict]) -> tuple[str, int, int]:
    """The figure as SVG, with how many parts were drawn and how many boxed.

    Parts arrive with the geometry the file stated; anything this module has
    no formula for is drawn as its bounding box and counted, so the figure
    can say how much of itself is a transcription and how much a placeholder.
    """
    placed = [part for part in parts if part.get("_ext")]
    if not placed:
        return "", 0, 0
    min_x = min(part["_off"][0] for part in placed)
    min_y = min(part["_off"][1] for part in placed)
    max_x = max(part["_off"][0] + part["_ext"][0] for part in placed)
    max_y = max(part["_off"][1] + part["_ext"][1] for part in placed)
    width = _px(max_x - min_x) or 1
    height = _px(max_y - min_y) or 1
    scale = min(1.0, MAX_EDGE_PX / max(width, height))

    body: list[str] = []
    defs: list[str] = []
    drawn = boxed = 0
    for index, part in enumerate(placed):
        if part.get("_grad"):
            part["_gradient_id"] = f"g{index}"
            defs.append(_gradient_def(part, part["_gradient_id"]))
    for index, part in enumerate(placed):
        x = _px(part["_off"][0] - min_x)
        y = _px(part["_off"][1] - min_y)
        w = _px(part["_ext"][0])
        h = _px(part["_ext"][1])
        media = part.get("media")
        if media:
            name = media.rpartition("/")[2]
            rotation = part.get("_rot")
            transform = (
                f' transform="rotate({_round(rotation)} '
                f'{_round(x + w / 2)} {_round(y + h / 2)})"'
                if rotation
                else ""
            )
            crop_def, image = _cropped_image(part, name, index, x, y, w, h)
            if crop_def:
                defs.append(crop_def)
                body.append(f"<g{transform}>{image}</g>" if transform else image)
            else:
                aspect = (
                    ' preserveAspectRatio="none"'
                    if part.get("_crop_applied")
                    else ""
                )
                body.append(
                    f'<image href="{escape(name)}" x="{x}" y="{y}" '
                    f'width="{w}" height="{h}"{aspect}{transform}/>'
                )
            drawn += 1
            continue
        path = _custom_path(part, x, y, w, h)
        if path:
            body.append(f'<path d="{path}" {_fill(part)} {_stroke(part)}/>')
            drawn += 1
        else:
            shape, exact = _preset(part, x, y, w, h)
            body.append(shape)
            drawn += 1 if exact else 0
            boxed += 0 if exact else 1
        body.append(_text(part, x, y, w, h))

    canvas_w = round(width * scale, 2)
    canvas_h = round(height * scale, 2)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{canvas_w}" height="{canvas_h}" '
        f'viewBox="0 0 {width} {height}">'
        + (f"<defs>{''.join(defs)}</defs>" if defs else "")
        + f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
        + "".join(item for item in body if item)
        + "</svg>"
    )
    return svg, drawn, boxed
