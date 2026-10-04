"""Self-contained CD+G (CDG) karaoke subcode encoder.

Produces a ``.cdg`` graphics stream synchronized to lyric timing so it can be
paired with an ``.mp3`` and played back by any standard CDG karaoke player
(Winamp CDG, VLC with the cdg demux, hardware karaoke machines, etc.).

The encoder is intentionally dependency-light: it only needs Pillow (already a
backend dependency) to rasterize text into the 6x12 CDG tile grid. It parses LRC
directly (both inline ``<mm:ss.xx>`` word tags and plain ``[mm:ss.xx]`` line
tags), greedily wraps words to the 288px visible width, paginates into pages,
and wipes each word to the highlight color at its start time.

CDG format reference (the parts used here):
    * 300 subcode packets per second, each packet is 24 bytes.
    * Screen is 300x216 px = 50x18 tiles of 6x12 px; a 1-tile border is left
      un-drawn so the "safe" area is 48x16 tiles (288x192 px).
    * 16-color CLUT, each color 12-bit (4 bits per channel) stored in two 6-bit
      bytes.
"""

from __future__ import annotations

from pathlib import Path
import re

# --- CDG protocol constants -------------------------------------------------
_CDG_COMMAND = 0x09
_INSTR_MEMORY_PRESET = 1
_INSTR_BORDER_PRESET = 2
_INSTR_TILE_BLOCK = 6
_INSTR_LOAD_CLUT_LO = 30
_INSTR_LOAD_CLUT_HI = 31

PACKETS_PER_SECOND = 300
_PACKET_SIZE = 24

TILE_W = 6
TILE_H = 12
TILES_ACROSS = 50          # full screen including border
TILES_DOWN = 18
SCREEN_W = TILES_ACROSS * TILE_W   # 300
SCREEN_H = TILES_DOWN * TILE_H     # 216

# Visible/safe drawing area (leave a one-tile border all around).
SAFE_COL_MIN = 1
SAFE_COL_MAX = 48          # inclusive -> 48 drawable columns (288px)
SAFE_ROW_MIN = 1
SAFE_ROW_MAX = 16          # inclusive -> 16 drawable rows (192px)
SAFE_W = (SAFE_COL_MAX - SAFE_COL_MIN + 1) * TILE_W   # 288

# Palette indices.
_COL_BG = 0
_COL_BASE = 1              # un-sung lyric text
_COL_HIGHLIGHT = 2         # already-sung lyric text
_COL_TITLE = 3


class CdgError(RuntimeError):
    """Raised when a CDG stream cannot be produced."""


# --- color helpers ----------------------------------------------------------
def _hex_to_rgb(value: str, fallback=(0, 0, 0)) -> tuple[int, int, int]:
    try:
        s = str(value or "").strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) != 6:
            return fallback
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return fallback


def _rgb_to_cdg_bytes(rgb: tuple[int, int, int]) -> tuple[int, int]:
    """Encode an 8-bit RGB triple into the two 6-bit CDG CLUT bytes."""
    r4, g4, b4 = (rgb[0] >> 4) & 0x0F, (rgb[1] >> 4) & 0x0F, (rgb[2] >> 4) & 0x0F
    hi = ((r4 & 0x0F) << 2) | ((g4 >> 2) & 0x03)
    lo = ((g4 & 0x03) << 4) | (b4 & 0x0F)
    return hi & 0x3F, lo & 0x3F


# --- low level packet builders ---------------------------------------------
def _packet(instruction: int, data: list[int]) -> bytes:
    pkt = bytearray(_PACKET_SIZE)
    pkt[0] = _CDG_COMMAND & 0x3F
    pkt[1] = instruction & 0x3F
    for i in range(16):
        pkt[4 + i] = (data[i] if i < len(data) else 0) & 0x3F
    return bytes(pkt)


def _memory_preset_packets(color_index: int) -> list[bytes]:
    # Repeat 0..15 so every player reliably clears the full screen.
    return [_packet(_INSTR_MEMORY_PRESET, [color_index & 0x0F, rpt & 0x0F])
            for rpt in range(16)]


def _border_preset_packet(color_index: int) -> bytes:
    return _packet(_INSTR_BORDER_PRESET, [color_index & 0x0F])


def _load_clut_packets(palette: list[tuple[int, int, int]]) -> list[bytes]:
    pal = list(palette) + [(0, 0, 0)] * (16 - len(palette))
    lo_data: list[int] = []
    hi_data: list[int] = []
    for idx in range(8):
        hi, lo = _rgb_to_cdg_bytes(pal[idx])
        lo_data += [hi, lo]
    for idx in range(8, 16):
        hi, lo = _rgb_to_cdg_bytes(pal[idx])
        hi_data += [hi, lo]
    return [
        _packet(_INSTR_LOAD_CLUT_LO, lo_data),
        _packet(_INSTR_LOAD_CLUT_HI, hi_data),
    ]


def _tile_packet(row: int, col: int, color0: int, color1: int,
                 rows12: list[int]) -> bytes:
    data = [color0 & 0x0F, color1 & 0x0F, row & 0x1F, col & 0x3F]
    for i in range(12):
        data.append((rows12[i] if i < len(rows12) else 0) & 0x3F)
    return _packet(_INSTR_TILE_BLOCK, data)


# --- LRC parsing ------------------------------------------------------------
_LINE_TAG = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_WORD_TAG = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>")


def _parse_lrc_words(lrc_text: str) -> list[dict]:
    """Return a flat list of ``{"word", "start", "end"}`` dicts in time order.

    Supports inline ``<mm:ss.xx>word`` enhanced tags as well as plain
    ``[mm:ss.xx]`` line-level tags (one word-group per line).
    """
    words: list[dict] = []
    for raw in (lrc_text or "").splitlines():
        line = raw.rstrip("\n")
        line_tags = list(_LINE_TAG.finditer(line))
        if not line_tags:
            continue
        line_start = int(line_tags[0].group(1)) * 60 + float(line_tags[0].group(2))
        body = line[line_tags[-1].end():]
        if not body.strip():
            continue

        inline = list(_WORD_TAG.finditer(body))
        if inline:
            first_of_line = True
            for i, m in enumerate(inline):
                t = int(m.group(1)) * 60 + float(m.group(2))
                seg_end = inline[i + 1].start() if i + 1 < len(inline) else len(body)
                chunk = body[m.end():seg_end]
                for tok in chunk.split():
                    words.append({"word": tok, "start": t, "end": None,
                                  "line_break": first_of_line})
                    first_of_line = False
        else:
            # Line-level timing: the whole visible line shares one start time.
            cleaned = _WORD_TAG.sub("", body).strip()
            toks = cleaned.split()
            if not toks:
                continue
            span = 1.6
            step = span / max(1, len(toks))
            for i, tok in enumerate(toks):
                words.append({
                    "word": tok,
                    "start": line_start + i * step,
                    "end": None,
                    "line_break": i == 0,
                })

    words = [w for w in words if w["word"].strip()]
    words.sort(key=lambda w: w["start"])
    # Fill in end times (next word's start, capped to a sensible hold).
    for i, w in enumerate(words):
        if i + 1 < len(words):
            w["end"] = max(w["start"] + 0.08, min(words[i + 1]["start"], w["start"] + 4.0))
        else:
            w["end"] = w["start"] + 1.6
    return words


# --- text rasterization -----------------------------------------------------
def _load_font(font_path, px: int):
    from PIL import ImageFont  # Pillow is a backend dependency.
    try:
        if font_path:
            return ImageFont.truetype(str(font_path), px)
    except Exception:
        pass
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", px)
    except Exception:
        return ImageFont.load_default()


def _measure(draw, font, text: str) -> int:
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return int(box[2] - box[0])
    except Exception:
        try:
            return int(draw.textlength(text, font=font))
        except Exception:
            return len(text) * 6


def _wrap_words(words: list[dict], font_path, line_px: int) -> list[list[dict]]:
    """Greedily group consecutive words into display lines that fit SAFE_W."""
    from PIL import Image, ImageDraw
    probe = ImageDraw.Draw(Image.new("L", (10, 10)))
    font = _load_font(font_path, line_px)
    space_w = max(2, _measure(probe, font, " "))

    lines: list[list[dict]] = []
    current: list[dict] = []
    width = 0
    for w in words:
        ww = _measure(probe, font, w["word"])
        forced_break = bool(w.get("line_break")) and current
        add = ww + (space_w if current else 0)
        if current and (forced_break or width + add > SAFE_W):
            lines.append(current)
            current = [w]
            width = ww
        else:
            current.append(w)
            width += add
    if current:
        lines.append(current)
    return lines


def _render_line_tiles(line_words: list[dict], font_path, line_px: int,
                       band_rows: int):
    """Rasterize one display line and return per-tile paint instructions.

    Returns ``(tiles, )`` where ``tiles`` is a list of dicts:
        ``{"col": int, "subrow": int, "rows12": [int*12], "word_index": int}``
    ``col``/``subrow`` are relative (col 0 = SAFE_COL_MIN, subrow 0 = line top).
    ``word_index`` is the index into ``line_words`` that owns the tile (for the
    progressive highlight wipe).
    """
    from PIL import Image, ImageDraw
    band_h = band_rows * TILE_H
    img = Image.new("L", (SAFE_W, band_h), 0)
    draw = ImageDraw.Draw(img)
    font = _load_font(font_path, line_px)
    space_w = max(2, _measure(draw, font, " "))

    text = " ".join(w["word"] for w in line_words)
    total_w = _measure(draw, font, text)
    x0 = max(0, (SAFE_W - total_w) // 2)
    try:
        box = draw.textbbox((0, 0), text or " ", font=font)
        text_h = box[3] - box[1]
        y0 = max(0, (band_h - text_h) // 2 - box[1])
    except Exception:
        y0 = max(0, (band_h - line_px) // 2)

    # Draw word by word so we can record each word's horizontal extent.
    cursor = x0
    word_ranges: list[tuple[int, int]] = []
    for i, w in enumerate(line_words):
        ww = _measure(draw, font, w["word"])
        draw.text((cursor, y0), w["word"], fill=255, font=font)
        word_ranges.append((cursor, cursor + ww))
        cursor += ww + space_w

    px = img.load()
    threshold = 110
    tiles = []
    cols = SAFE_COL_MAX - SAFE_COL_MIN + 1
    for subrow in range(band_rows):
        for col in range(cols):
            rows12 = []
            any_ink = False
            for ry in range(TILE_H):
                yy = subrow * TILE_H + ry
                bits = 0
                for cx in range(TILE_W):
                    xx = col * TILE_W + cx
                    if xx < SAFE_W and yy < band_h and px[xx, yy] >= threshold:
                        bits |= (0x20 >> cx)
                        any_ink = True
                rows12.append(bits)
            if not any_ink:
                continue
            center_x = col * TILE_W + TILE_W // 2
            widx = _owner_word(center_x, word_ranges)
            tiles.append({"col": col, "subrow": subrow, "rows12": rows12,
                          "word_index": widx})
    return tiles


def _owner_word(center_x: int, word_ranges: list[tuple[int, int]]) -> int:
    if not word_ranges:
        return 0
    for i, (a, b) in enumerate(word_ranges):
        if a <= center_x <= b:
            return i
    # In a gap: attach to the nearest preceding word (so wipes stay monotonic).
    best = 0
    for i, (a, _b) in enumerate(word_ranges):
        if a <= center_x:
            best = i
    return best


# --- stream assembly --------------------------------------------------------
def _seconds_to_packet(t: float) -> int:
    return max(0, int(round(float(t) * PACKETS_PER_SECOND)))


def render_cdg(
    lrc_text: str,
    out_cdg_path,
    *,
    font_path=None,
    audio_duration: float = 0.0,
    bg_hex: str = "#000820",
    base_hex: str = "#9fb4ff",
    highlight_hex: str = "#ffe14d",
    title: str | None = None,
    lines_per_page: int = 4,
    line_px: int = 20,
    band_rows: int = 2,
) -> dict:
    """Encode ``lrc_text`` into a CDG file at ``out_cdg_path``.

    Returns a small summary dict (``words``, ``lines``, ``pages``, ``packets``).
    Raises :class:`CdgError` on unrecoverable problems (e.g. no timed lyrics).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401 - availability check
    except Exception as exc:  # pragma: no cover - environment specific
        raise CdgError(
            "Pillow (PIL) is required for CDG export but is not installed."
        ) from exc

    words = _parse_lrc_words(lrc_text)
    if not words:
        raise CdgError("No timestamped lyrics found to build a CDG stream.")

    lines_per_page = max(1, min(6, int(lines_per_page or 4)))
    band_rows = max(1, min(3, int(band_rows or 2)))

    display_lines = _wrap_words(words, font_path, line_px)

    palette = [(0, 0, 0)] * 16
    palette[_COL_BG] = _hex_to_rgb(bg_hex, (0, 8, 32))
    palette[_COL_BASE] = _hex_to_rgb(base_hex, (159, 180, 255))
    palette[_COL_HIGHLIGHT] = _hex_to_rgb(highlight_hex, (255, 225, 77))
    palette[_COL_TITLE] = _hex_to_rgb(base_hex, (200, 210, 255))

    # Vertical placement of up to lines_per_page bands within the safe area.
    used_rows = lines_per_page * band_rows
    gaps = max(0, lines_per_page - 1)
    free = (SAFE_ROW_MAX - SAFE_ROW_MIN + 1) - used_rows
    gap_rows = min(1, free // gaps) if gaps else 0
    block_rows = used_rows + gap_rows * gaps
    top_row = SAFE_ROW_MIN + max(0, ((SAFE_ROW_MAX - SAFE_ROW_MIN + 1) - block_rows) // 2)

    # Group display lines into pages.
    pages: list[list[list[dict]]] = [
        display_lines[i:i + lines_per_page]
        for i in range(0, len(display_lines), lines_per_page)
    ]

    # Timed events: list of (packet_index, packet_bytes).
    events: list[tuple[int, bytes]] = []
    # Palette + clear at t=0.
    for pkt in _load_clut_packets(palette):
        events.append((0, pkt))
    events.append((0, _border_preset_packet(_COL_BG)))
    for pkt in _memory_preset_packets(_COL_BG):
        events.append((0, pkt))

    def _page_start(page: list[list[dict]]) -> float:
        first = page[0][0]
        return float(first["start"])

    for pi, page in enumerate(pages):
        appear = max(0.0, _page_start(page) - 0.6)
        appear_pkt = _seconds_to_packet(appear)
        if pi > 0:
            # Clear the screen before drawing the new page.
            for pkt in _memory_preset_packets(_COL_BG):
                events.append((appear_pkt, pkt))

        for li, line_words in enumerate(page):
            tile_row_base = top_row + li * (band_rows + gap_rows)
            tiles = _render_line_tiles(line_words, font_path, line_px, band_rows)
            # Paint the whole line in the base (un-sung) color when the page shows.
            for t in tiles:
                r = tile_row_base + t["subrow"]
                c = SAFE_COL_MIN + t["col"]
                events.append((appear_pkt,
                               _tile_packet(r, c, _COL_BG, _COL_BASE, t["rows12"])))
            # Wipe each word to the highlight color at its start time.
            # Group tiles by owning word so we can emit them together.
            for widx, w in enumerate(line_words):
                wpkt = _seconds_to_packet(w["start"])
                for t in tiles:
                    if t["word_index"] != widx:
                        continue
                    r = tile_row_base + t["subrow"]
                    c = SAFE_COL_MIN + t["col"]
                    events.append((wpkt,
                                   _tile_packet(r, c, _COL_BG, _COL_HIGHLIGHT,
                                                t["rows12"])))

    # Determine total length in packets.
    last_word_end = max((w["end"] or w["start"] for w in words), default=0.0)
    total_seconds = max(float(audio_duration or 0.0), last_word_end + 1.5)
    total_packets = _seconds_to_packet(total_seconds)

    # Flatten events into a gap-filled packet stream, in time order. When two
    # events target the same packet slot the later one slips forward by 1/300s,
    # which is imperceptible.
    events.sort(key=lambda e: e[0])
    stream = bytearray()
    written = 0
    filler = bytes(_PACKET_SIZE)
    for idx, pkt in events:
        target = max(idx, written)
        while written < target:
            stream += filler
            written += 1
        stream += pkt
        written += 1
    while written < total_packets:
        stream += filler
        written += 1

    out_path = Path(out_cdg_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(stream))

    return {
        "words": len(words),
        "lines": len(display_lines),
        "pages": len(pages),
        "packets": written,
        "duration": round(total_seconds, 2),
        "path": str(out_path),
    }
