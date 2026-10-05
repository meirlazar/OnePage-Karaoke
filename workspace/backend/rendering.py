"""Video rendering: LRC->ASS subtitle generation, bouncing-ball overlay,
FFmpeg burn pipeline, and CDG+MP3 export."""
from processing import *  # noqa: F401,F403



# ===============================================================
# SECTION: Video Rendering (FFmpeg Canvas to Video Export)
# Purpose: Render karaoke video with lyrics, effects, audio,
#          and support for Final (no vocal), Chorus, and Preview
# ===============================================================


def _resolve_font_file(font_name: str):
    """Locate the served .ttf/.otf whose stem matches ``font_name`` (or None)."""
    requested = str(font_name or "").strip()
    if not requested:
        return None
    try:
        for path in SERVED_FONTS_DIR.iterdir():
            if path.is_file() and path.stem.lower() == requested.lower():
                return path
    except Exception:
        pass
    return None


def _select_source_audio_existing(
    project_dir: Path, base_name: str, audio_path: Path, normalized_source: str
) -> Path:
    """Pick the best EXISTING audio file for a render source without triggering
    any heavy regeneration (used by the lightweight CDG export)."""
    minus_track = project_dir / f"{base_name}_minus.mp3"
    chorus_track = project_dir / f"{base_name}_minus_chorus.mp3"
    stem_dir = _find_project_stems(project_dir, base_name)
    demucs_no_vocals = stem_dir / "no_vocals.wav" if stem_dir else None
    if normalized_source == "preview":
        return audio_path
    if normalized_source == "chorus" and chorus_track.exists() and chorus_track.is_file():
        return chorus_track
    if minus_track.exists() and minus_track.is_file():
        return minus_track
    if demucs_no_vocals and demucs_no_vocals.exists() and demucs_no_vocals.is_file():
        return demucs_no_vocals
    return audio_path


def _run_cdg_job(
    job_id: str,
    audio_filename: str,
    font_name: str = "Arial",
    primary_color: str = "#ffe14d",
    secondary_color: str = "#9fb4ff",
    bg_color: str = "#000820",
    render_source: str = "final",
    lines_per_page: int = 4,
    output_filename: str = "",
    render_token: str = "",
    project_name: str = "",
    **_ignored,
) -> None:
    """Build a CDG+MP3 karaoke package (zipped) from a project's .lrc + audio."""
    _update_job(job_id, stage="CDG export", message="Preparing lyrics…", progress=5)
    audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    project_dir = audio_path.parent
    base_name = audio_path.stem
    lrc_path = _find_project_asset(project_dir, ".lrc", base_name)
    if not lrc_path:
        raise FileNotFoundError(
            f"No .lrc lyrics file found in project: {project_dir.name}"
        )

    normalized_source = str(render_source or "final").strip().lower()
    if normalized_source not in {"preview", "final", "chorus"}:
        normalized_source = "final"
    source_audio = _select_source_audio_existing(
        project_dir, base_name, audio_path, normalized_source
    )

    token = str(render_token or datetime.now().strftime("%Y%m%d_%H%M%S"))
    label = _safe_output_name(project_dir.name, fallback_stem=base_name)
    stem_out = f"{label}_CDG_{token}"
    cdg_path = project_dir / f"{stem_out}.cdg"
    mp3_path = project_dir / f"{stem_out}.mp3"

    # 1. Transcode the selected audio to a standard CDG-friendly MP3.
    _update_job(job_id, message="Encoding MP3 audio…", progress=25)
    res = subprocess.run(
        [
            FFMPEG_BIN, "-y", "-i", str(source_audio),
            "-map", "a:0", "-c:a", "libmp3lame", "-b:a", "256k",
            "-ar", "44100", "-ac", "2", str(mp3_path),
        ],
        check=False, capture_output=True, text=True,
    )
    if res.returncode != 0 or not mp3_path.exists():
        raise RuntimeError(
            "FFmpeg could not create the MP3 for CDG export: "
            + (res.stderr or "")[-300:]
        )

    # 2. Encode the CDG subcode stream from the lyric timing.
    _update_job(job_id, message="Rendering CDG graphics…", progress=55)
    duration = _probe_media_duration(mp3_path)
    font_file = _resolve_font_file(font_name)
    try:
        lines_pp = max(1, min(6, int(lines_per_page or 4)))
    except Exception:
        lines_pp = 4
    try:
        summary = cdg_karaoke.render_cdg(
            lrc_path.read_text(encoding="utf-8", errors="ignore"),
            cdg_path,
            font_path=font_file,
            audio_duration=duration,
            bg_hex=bg_color or "#000820",
            base_hex=secondary_color or "#9fb4ff",
            highlight_hex=primary_color or "#ffe14d",
            lines_per_page=lines_pp,
        )
    except cdg_karaoke.CdgError as exc:
        raise RuntimeError(str(exc)) from exc

    # 3. Zip the matched-name .cdg + .mp3 (the standard karaoke pairing).
    _update_job(job_id, message="Packaging .zip…", progress=85)
    zip_name = Path(output_filename).name if output_filename else f"{stem_out}.zip"
    zip_path = project_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(cdg_path, arcname=f"{stem_out}.cdg")
        zf.write(mp3_path, arcname=f"{stem_out}.mp3")

    logger.info(
        "[CDG END] job=%s zip=%s lines=%s pages=%s dur=%ss",
        job_id, zip_path.name, summary["lines"], summary["pages"], summary["duration"],
    )
    _update_job(
        job_id,
        status="completed",
        progress=100,
        output_filename=zip_path.name,
        message=(
            f"CDG+MP3 ready: {summary['lines']} lines / {summary['pages']} pages / "
            f"{summary['duration']}s"
        ),
    )



def _run_render_job(
    job_id: str,
    audio_filename: str,
    font_name: str,
    font_size: int,
    line_spacing: int,
    word_padding: int,
    primary_color: str,
    secondary_color: str,
    outline_color: str,
    bg_type: str,
    bg_color: str,
    transition_style: str,
    fx_scope: str,
    fx_speed: float,
    text_effect: str,
    reveal_mode: str,
    preview_line_count: int,
    pitch: float = 1.0,
    volume: float = 1.0,
    render_device: str = DEFAULT_RENDER_DEVICE,
    use_preview_audio: bool = False,
    render_source: str = "",
    output_filename: str = "",
    render_token: str = "",
    render_width: int = 1280,
    render_height: int = 720,
    render_resolution: str = "",
    project_name: str = "",
    ball_radius: int = 26,
    arc_height: int = 78,
    bounce_per_sec: float = 0.1,
    ball_icon: str = "",
    ball_rotation: float = 1.5,
    ball_color: str = "#ffffff",
    ball_outline_color: str = "#000000",
    ball_align: str = "default",
    outline_width: int = -1,
    male_primary_color: str = "",
    male_secondary_color: str = "",
    male_outline_color: str = "",
    female_primary_color: str = "",
    female_secondary_color: str = "",
    female_outline_color: str = "",
    both_primary_color: str = "",
    both_secondary_color: str = "",
    both_outline_color: str = "",
    fx_options: dict | None = None,
) -> None:
    # NOTE: "bouncing-ball" is rendered through the standard FFmpeg/ASS path
    # below (see _build_bouncing_ball_events), which correctly honors
    # render_source audio selection (final/chorus/preview), lyrics, and all FX.
    # It must NOT be special-cased here.

    # Standard FFmpeg rendering path
    preferred_device = _normalize_device(render_device, DEFAULT_RENDER_DEVICE)
    attempts = [preferred_device]
    if preferred_device != "cpu":
        attempts.append("cpu")

    last_error = None
    for idx, attempt_device in enumerate(dict.fromkeys(attempts)):
        if idx > 0:
            _update_job(
                job_id,
                stage="render fallback",
                message=f"GPU render failed, retrying on CPU for {audio_filename}",
            )
        logger.info(
            "[RENDER START] job=%s audio=%s font=%s size=%s device=%s pitch=%s volume=%s",
            job_id,
            audio_filename,
            font_name,
            font_size,
            attempt_device,
            pitch,
            volume,
        )
        try:
            actual_device = execute_ffmpeg_burn(
                audio_filename,
                font_name,
                font_size,
                line_spacing,
                word_padding,
                primary_color,
                secondary_color,
                outline_color,
                bg_type,
                bg_color,
                transition_style,
                fx_scope,
                fx_speed,
                text_effect,
                reveal_mode,
                preview_line_count,
                pitch=pitch,
                volume=volume,
                render_device=attempt_device,
                job_id=job_id,
                use_preview_audio=use_preview_audio,
                render_source=render_source,
                output_filename=output_filename,
                render_token=render_token,
                render_width=render_width,
                render_height=render_height,
                ball_radius=ball_radius,
                arc_height=arc_height,
                bounce_per_sec=bounce_per_sec,
                ball_icon=ball_icon,
                ball_rotation=ball_rotation,
                ball_color=ball_color,
                ball_outline_color=ball_outline_color,
                ball_align=ball_align,
                outline_width=outline_width,
                male_primary_color=male_primary_color,
                male_secondary_color=male_secondary_color,
                male_outline_color=male_outline_color,
                female_primary_color=female_primary_color,
                female_secondary_color=female_secondary_color,
                female_outline_color=female_outline_color,
                both_primary_color=both_primary_color,
                both_secondary_color=both_secondary_color,
                both_outline_color=both_outline_color,
                fx_options=fx_options,
            )
            _update_job(job_id, render_device=actual_device)
            logger.info(
                "[RENDER END] job=%s audio=%s device=%s",
                job_id,
                audio_filename,
                actual_device,
            )
            return
        except JobCancelledError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "[RENDER FAIL] job=%s device=%s error=%s", job_id, attempt_device, exc
            )
            if attempt_device == "cpu":
                raise

    if last_error:
        raise last_error


def _build_audio_filter(volume: float = 1.0, pitch: float = 1.0) -> str:
    filters = []
    safe_volume = max(0.0, float(volume or 1.0))
    safe_pitch = max(0.25, float(pitch or 1.0))
    if abs(safe_volume - 1.0) > 1e-3:
        filters.append(f"volume={safe_volume:.4f}")
    if abs(safe_pitch - 1.0) > 1e-3:
        filters.append(f"asetrate=44100*{safe_pitch:.5f}")
        filters.append(f"atempo={1.0 / safe_pitch:.5f}")
        filters.append("aresample=44100")
    return ",".join(filters)


_ENCODER_ARGS_CACHE: dict[str, list[str]] = {}


def _render_video_encoder_args(render_device: str) -> list[str]:
    # ffmpeg's encoder list and NVENC capability don't change during a process run,
    # so probe once per device and reuse the result instead of re-running two
    # subprocesses on every render.
    normalized = _normalize_device(render_device, DEFAULT_RENDER_DEVICE)
    cached = _ENCODER_ARGS_CACHE.get(normalized)
    if cached is not None:
        return list(cached)
    result = _probe_video_encoder_args(normalized)
    _ENCODER_ARGS_CACHE[normalized] = list(result)
    return list(result)


def _probe_video_encoder_args(normalized: str) -> list[str]:
    probe = subprocess.run(
        [FFMPEG_BIN, "-hide_banner", "-encoders"], capture_output=True, text=True,
    )
    encoders = f"{probe.stdout or ''}\n{probe.stderr or ''}"

    if normalized != "cpu":
        if "h264_nvenc" in encoders:
            runtime_probe = subprocess.run(
                [
                    FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=64x64:r=1", "-frames:v", "1",
                    "-c:v", "h264_nvenc", "-f", "null", "-",
                ],
                capture_output=True, text=True,
            )
            if runtime_probe.returncode == 0:
                logger.info("[GPU CHECK] FFmpeg NVENC initialization succeeded")
                # FIXED: Increased bitrate from 5M to 15M and preset to p6 for high quality
                return ["-c:v", "h264_nvenc", "-preset", "p6", "-b:v", "15M"]

            logger.warning(
                "[GPU CHECK] NVENC initialization failed; using CPU encoder: %s",
                (runtime_probe.stderr or "unknown error").strip()[-500:],
            )
        else:
            logger.warning("[GPU CHECK] FFmpeg does not expose h264_nvenc; using CPU encoder")

    if "libx264" in encoders:
        # FIXED: Lowered CRF from 26 to 18 (visually lossless)
        return ["-c:v", "libx264", "-crf", "18"]
    if "libopenh264" in encoders:
        # FIXED: Increased bitrate from 3M to 10M
        return ["-c:v", "libopenh264", "-b:v", "10M"]
    return ["-c:v", "mpeg4", "-q:v", "2"] # Lower q:v is better for mpeg4


def _render_static_background(
    kind: str,
    color_a: tuple[int, int, int],
    color_b: tuple[int, int, int],
    width: int,
    height: int,
    project_dir: Path,
) -> Path:
    """Rasterize a static gradient/spiral background to a PNG exactly once.

    Previously these were produced by ffmpeg's `geq` filter, which re-evaluates a
    per-pixel arithmetic expression on a single CPU thread for every frame. That
    bottlenecks the whole pipeline and leaves the NVENC encoder idle, so GPU
    renders performed no better than CPU ones. The output is identical because
    the background never changes over time.
    """
    from PIL import Image

    r1, g1, b1 = color_a
    r2, g2, b2 = color_b
    key = f"{kind}-{r1}_{g1}_{b1}-{r2}_{g2}_{b2}-{width}x{height}"
    cache_path = project_dir / f".bg_{_safe_output_name(key, 'bg')}.png"
    if cache_path.exists():
        return cache_path

    img = Image.new("RGB", (width, height))
    try:
        import numpy as np

        if kind == "gradient":
            t = (np.arange(height, dtype=np.float32) / max(1, height))[:, None]
            t = np.repeat(t, width, axis=1)
        else:
            xs = np.arange(width, dtype=np.float32) - width / 2.0
            ys = np.arange(height, dtype=np.float32) - height / 2.0
            t = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2) / (0.78 * max(1, height))
            t = np.minimum(1.0, t)
        arr = np.stack(
            [
                r1 + (r2 - r1) * t,
                g1 + (g2 - g1) * t,
                b1 + (b2 - b1) * t,
            ],
            axis=-1,
        )
        img = Image.fromarray(np.clip(np.round(arr), 0, 255).astype(np.uint8), "RGB")
    except Exception:
        px = img.load()
        if kind == "gradient":
            # Vertical ramp: matches geq r='r1+(r2-r1)*Y/H'
            for y in range(height):
                tv = y / height if height else 0.0
                row = (
                    round(r1 + (r2 - r1) * tv),
                    round(g1 + (g2 - g1) * tv),
                    round(b1 + (b2 - b1) * tv),
                )
                for x in range(width):
                    px[x, y] = row
        else:
            # Radial ramp: matches geq min(1, sqrt((X-W/2)^2+(Y-H/2)^2)/(0.78*H))
            cx, cy = width / 2.0, height / 2.0
            denom = 0.78 * height if height else 1.0
            for y in range(height):
                dy2 = (y - cy) ** 2
                for x in range(width):
                    tv = min(1.0, ((x - cx) ** 2 + dy2) ** 0.5 / denom)
                    px[x, y] = (
                        round(r1 + (r2 - r1) * tv),
                        round(g1 + (g2 - g1) * tv),
                        round(b1 + (b2 - b1) * tv),
                    )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(cache_path)
    logger.info(
        "[BACKGROUND] Rasterized static %s background -> %s", kind, cache_path.name
    )
    return cache_path


def _escape_filter_path(path: Path) -> str:
    # FFmpeg filter args need escaping for path separators and quotes.
    value = str(path)
    value = value.replace("\\", "\\\\")
    value = value.replace(":", "\\:")
    value = value.replace("'", "\\'")
    return value


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    value = str(hex_str or "#000000").strip().lstrip("#")
    if len(value) != 6:
        value = "000000"
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _estimate_word_centers(
    words: list[str],
    font_size: int,
    text_width=None,
    word_padding: float = 0.0,
) -> list[float]:
    """Per-word x offsets from line center.

    When a real font-metrics callable is supplied it is used so the ball lands
    exactly on each word; otherwise fall back to an average-character estimate.
    """
    if text_width is not None:
        measure = text_width
        space_w = float(measure(" ")) + float(word_padding or 0.0)
    else:
        avg_char_w = font_size * 0.56

        def measure(value: str) -> float:
            return max(len(value), 1) * avg_char_w

        space_w = font_size * 0.32
    widths = [max(float(measure(word)), 1.0) for word in words]
    total = sum(widths) + space_w * max(0, len(words) - 1)
    centers: list[float] = []
    cursor = -total / 2.0
    for width in widths:
        centers.append(cursor + (width / 2.0))
        cursor += width + space_w
    return centers


def _word_transform_ass(style: str, offset_ms: int, speed_ms: int) -> str:
    """Per-word override tags for word-scope FX. Position-based styles (slide/drop) fall back to a
    scale/alpha approximation since ASS cannot animate \\pos independently within one \\k text run.
    """
    end_ms = offset_ms + max(1, speed_ms)
    if style == "fade":
        return rf"\alpha&HFF&\t({offset_ms},{end_ms},\alpha&H00&)"
    if style in {"pop", "bouncing-ball"}:
        return rf"\fscX112\fscY112\t({offset_ms},{end_ms},\fscX100\fscY100)"
    if style in {"zoom", "slide", "drop"}:
        return rf"\fscX80\fscY80\alpha&HFF&\t({offset_ms},{end_ms},\fscX100\fscY100\alpha&H00&)"
    if style == "blur":
        return rf"\blur6\t({offset_ms},{end_ms},\blur0)"
    if style == "rotate-360":
        return rf"\frz360\t({offset_ms},{end_ms},\frz0)"
    if style == "glimmer":
        mid = offset_ms + max(1, speed_ms // 2)
        return rf"\alpha&H55&\t({offset_ms},{mid},\alpha&H00&)\t({mid},{end_ms},\alpha&H55&)"
    if style == "shake":
        third = max(1, speed_ms // 3)
        return (
            rf"\t({offset_ms},{offset_ms + third},\frx3\fry-3)"
            rf"\t({offset_ms + third},{offset_ms + (2 * third)},\frx-3\fry3)"
            rf"\t({offset_ms + (2 * third)},{end_ms},\frx0\fry0)"
        )
    if style == "flip":
        return rf"\fscx20\t({offset_ms},{end_ms},\fscx100)"
    if style == "pulse":
        mid = offset_ms + max(1, speed_ms // 2)
        return rf"\fscx82\fscy82\alpha&H55&\t({offset_ms},{mid},\fscx110\fscy110\alpha&H00&)\t({mid},{end_ms},\fscx100\fscy100)"
    if style == "sway":
        mid = offset_ms + max(1, speed_ms // 2)
        return rf"\frz-12\t({offset_ms},{mid},\frz10)\t({mid},{end_ms},\frz0)"
    if style == "skew":
        mid = offset_ms + max(1, speed_ms // 2)
        return rf"\fax-0.45\t({offset_ms},{mid},\fax0.35)\t({mid},{end_ms},\fax0)"
    if style == "stamp":
        return rf"\fscx138\fscy138\bord12\alpha&HFF&\t({offset_ms},{end_ms},\fscx100\fscy100\bord5\alpha&H00&)"
    if style == "focus":
        return rf"\blur9\alpha&HFF&\t({offset_ms},{end_ms},\blur0\alpha&H00&)"
    return ""


def _ass_override_color(style_color: str) -> str:
    """Normalize a color into an ASS \\1c override tag value (&HBBGGRR&).

    Accepts either an ASS style color (&HAABBGGRR&) or a web hex color
    (#RRGGBB). Web hex must be byte-reversed to BGR, otherwise libass fails to
    parse the tag and silently falls back to black.
    """
    raw = (style_color or "").strip()
    if not raw:
        return "&HFFFFFF&"
    if raw.startswith("#"):
        hex_digits = raw.lstrip("#")
        if len(hex_digits) == 3:
            hex_digits = "".join(ch * 2 for ch in hex_digits)
        if len(hex_digits) >= 6:
            r, g, b = hex_digits[0:2], hex_digits[2:4], hex_digits[4:6]
            return f"&H{b}{g}{r}&".upper()
        return "&HFFFFFF&"
    inner = raw.strip("&H")
    if len(inner) >= 8:
        inner = inner[2:]
    return f"&H{inner}&"


def _build_bouncing_ball_events(
    word_timings: list[tuple[str, float, float]],
    center_x: int,
    line_y: int,
    font_size: int,
    color_hex: str,
    fmt_time,
    ball_radius: int = 0,
    arc_height: int = 0,
    bounce_per_sec: float = 0.0,
    outline_hex: str = "",
    layout_scale: float = 1.0,
    text_width=None,
    word_padding: float = 0.0,
    ball_emoji: str = "none",
    ball_icon: str = "",
    ball_rotation: float = 1.5,
    icon_overlay: bool = False,
    ball_align: str = "default",
) -> tuple[list[str], list[tuple]]:
    """One ball follows the same word start/end timings used by karaoke color fills.

    Returns ``(events, segments)``:
      * ``events``  - ASS Dialogue lines that draw the vector/emoji ball.
      * ``segments`` - motion path as ``(t0, t1, x0, y0, x1, y1)`` tuples giving the
        ball CENTER position over time.

    When ``icon_overlay`` is True the ASS ball is NOT drawn (``events`` is empty) and
    only ``segments`` is populated, so an FFmpeg PNG overlay can move the user's icon
    along the exact same path in the burned video.
    """
    if not word_timings:
        return [], []

    words = [word for word, _, _ in word_timings]
    centers = _estimate_word_centers(
        words, font_size, text_width=text_width, word_padding=word_padding
    )
    # Vertical alignment multiplier (fraction of font size above the line center).
    # Driven by the UI (-15..+15) so tall fonts / high resolutions can keep the
    # ball above the words instead of letting it settle into the middle of the
    # glyphs. 0 is the default height; higher values lift the ball further.
    align_mult = _ball_align_multiplier(ball_align)
    ball_y = line_y - round(font_size * align_mult)

    scale = layout_scale if layout_scale > 0 else 1.0
    bounce_h = (
        max(4, round(arc_height * scale))
        if arc_height and arc_height > 0
        else max(10, round(font_size * 0.22))
    )
    radius = (
        max(2, round(ball_radius * scale))
        if ball_radius and ball_radius > 0
        else max(5, round(font_size * 0.12))
    )

    segments: list[tuple] = []
    ball_style = ""

    if not icon_overlay:
        # 1. TEXTURE UPGRADE: Image/Emoji/Vector Support
        if ball_icon and ball_icon.strip():
            # An icon was requested but the burned video can't use the raster PNG
            # through this ASS path (that happens via the FFmpeg overlay when the
            # file resolves). Draw the vector ball rather than literal text here.
            drawing = (
                f"m 0 {-radius} b {radius} {-radius} {radius} {radius} 0 {radius} "
                f"b {-radius} {radius} {-radius} {-radius} 0 {-radius} "
                f"m {-radius} 0 l {radius} 0 m 0 {-radius} l 0 {radius}"
            )
            if outline_hex:
                edge_tag = rf"\3c{_ass_override_color(outline_hex)}\bord{max(1, round(radius * 0.18))}"
            else:
                edge_tag = r"\bord0"
            ball_style = (
                rf"{{\p1\an5\1c{_ass_override_color(color_hex)}{edge_tag}\shad0}}"
                rf"{drawing}{{\p0}}"
            )
        elif ball_emoji and ball_emoji.strip() and ball_emoji.strip() != "none":
            # Text mode (\p0) for the emoji, scaled dynamically based on ball_radius
            emoji_size = max(10, round(radius * 2.5))
            ball_style = rf"{{\p0\an5\fs{emoji_size}\1c&HFFFFFF&\3c&H000000&\bord1\shad0}}{ball_emoji.strip()}"
        else:
            # Fallback to the standard spinning vector crosshair
            drawing = (
                f"m 0 {-radius} b {radius} {-radius} {radius} {radius} 0 {radius} "
                f"b {-radius} {radius} {-radius} {-radius} 0 {-radius} "
                f"m {-radius} 0 l {radius} 0 m 0 {-radius} l 0 {radius}"
            )

            if outline_hex:
                edge_tag = rf"\3c{_ass_override_color(outline_hex)}\bord{max(1, round(radius * 0.18))}"
            else:
                edge_tag = r"\bord0"

            ball_style = (
                rf"{{\p1\an5\1c{_ass_override_color(color_hex)}{edge_tag}\shad0}}"
                rf"{drawing}{{\p0}}"
            )

    try:
        bps = float(bounce_per_sec or 0.0)
    except (TypeError, ValueError):
        bps = 0.0

    cycle_len = min(2.0, max(0.08, 1.0 / bps)) if bps > 0 else 0.35
    events: list[str] = []
    max_hop = 0.55
    flight_dur = 0.32

    # 2. SMOOTHNESS & SPIN ENGINE
    def _arc_steps(x0, x1, t0, t1, height, steps=None, spin_rotations=ball_rotation):
        """Approximate a smooth sinusoidal arc, now with higher FPS and Z-axis rotation."""
        out = []
        dur = t1 - t0
        if dur <= 0:
            return out

        if steps is None:
            steps = max(15, min(60, int(dur * 60) + 1))

        prev_t = t0
        prev_px = x0
        prev_py = ball_y
        prev_angle = 0

        for i in range(1, steps + 1):
            p = i / steps
            cur_t = t0 + dur * p
            cur_px = x0 + (x1 - x0) * p
            cur_py = ball_y - math.sin(p * math.pi) * height

            # Record the exact centre keyframe so the PNG overlay can follow it.
            segments.append((prev_t, cur_t, prev_px, prev_py, cur_px, cur_py))

            if not icon_overlay:
                # Calculate total degrees to rotate during this hop using custom multiplier
                cur_angle = p * (360 * spin_rotations)

                segment_dur_ms = max(1, int((cur_t - prev_t) * 1000))

                rot_tag = f"\\frz{prev_angle:.1f}\\t(0,{segment_dur_ms},\\frz{cur_angle:.1f})"

                out.append(
                    f"Dialogue: 2,{fmt_time(prev_t)},{fmt_time(cur_t)},Default,,0,0,0,,"
                    f"{{\\move({prev_px:.0f},{prev_py:.0f},{cur_px:.0f},{cur_py:.0f},0,{segment_dur_ms}){rot_tag}}}"
                    f"{ball_style}"
                )
                prev_angle = cur_angle
            prev_t, prev_px, prev_py = cur_t, cur_px, cur_py
        return out

    def _hold(x, t0, t1):
        """Ball parked at a fixed x (recorded for the overlay, drawn for the vector ball)."""
        if t1 - t0 <= 0:
            return
        segments.append((t0, t1, x, ball_y, x, ball_y))
        if not icon_overlay:
            events.append(
                f"Dialogue: 2,{fmt_time(t0)},{fmt_time(t1)},Default,,0,0,0,,"
                f"{{\\pos({x:.0f},{ball_y})\\frz0}}{ball_style}"
            )

    for idx, (_, word_start, word_end) in enumerate(word_timings):
        word_end = max(word_start + 0.02, word_end)
        target_x = center_x + centers[idx]
        if idx == 0:
            prev_x = target_x
            idle_start = word_start
        else:
            prev_x = center_x + centers[idx - 1]
            prev_end = max(word_timings[idx - 1][1] + 0.02, word_timings[idx - 1][2])
            hop_start = min(word_start, max(prev_end, word_start - max_hop))
            hop_end = min(word_end, max(hop_start, word_start) + flight_dur)

            if hop_end - hop_start > 0.01:
                # Hop between words: Spin using user's customized ball_rotation
                events.extend(
                    _arc_steps(prev_x, target_x, hop_start, hop_end, bounce_h, spin_rotations=ball_rotation)
                )

            if hop_start > prev_end + 0.01:
                _hold(prev_x, prev_end, hop_start)
            idle_start = hop_end

        cycles_done = 0
        idle_h = max(2, round(bounce_h * 0.45))
        while idle_start < word_end - 0.02 and cycles_done < 6:
            cycle_end = min(word_end, idle_start + cycle_len)
            if cycle_end - idle_start > 0.01:
                # Bounce in place: Spin at 1/3rd the speed of the flight hop
                idle_rotation = ball_rotation / 3.0
                events.extend(
                    _arc_steps(target_x, target_x, idle_start, cycle_end, idle_h, spin_rotations=idle_rotation)
                )
            idle_start = cycle_end
            cycles_done += 1

        if idle_start < word_end - 0.01:
            _hold(target_x, idle_start, word_end)

    return events, segments


def _resolve_ball_icon_file(ball_icon: str) -> Path | None:
    """Map a UI ball-icon reference (e.g. ``/icons/foo.png``) to a real file in ICONS_DIR."""
    raw = str(ball_icon or "").strip()
    if not raw:
        return None
    name = raw.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if name.startswith("icons/"):
        name = name[len("icons/"):]
    icons_root = ICONS_DIR.resolve()
    try:
        candidate = (ICONS_DIR / Path(name).name).resolve()
        if candidate.is_file() and (
            candidate == icons_root or icons_root in candidate.parents
        ):
            return candidate
    except Exception:
        pass
    return None


def _write_ball_icon_sendcmd(
    segments: list[tuple],
    cmds_path: Path,
    icon_px: int,
    render_width: int,
    render_height: int,
    fps: int = 30,
) -> bool:
    """Sample the ball path and write an FFmpeg ``sendcmd`` script that moves the icon
    overlay (top-left x/y) frame-by-frame. Returns False if there's nothing to draw."""
    segs = sorted((s for s in segments if s[1] > s[0]), key=lambda s: s[0])
    if not segs:
        return False
    half = icon_px / 2.0
    off_x = int(render_width + icon_px + 20)  # parked fully off-screen when idle
    off_y = 0
    end_t = max(s[1] for s in segs)
    total_frames = int(math.ceil(end_t * fps)) + 1

    lines: list[str] = []
    last_cmd: tuple[int, int] | None = None

    def _emit(t: float, vx: int, vy: int) -> None:
        nonlocal last_cmd
        if last_cmd == (vx, vy):
            return
        lines.append(f"{t:.3f} overlay x {vx}, overlay y {vy};")
        last_cmd = (vx, vy)

    _emit(0.0, off_x, off_y)
    seg_start = 0
    for frame in range(total_frames):
        t = frame / fps
        while seg_start < len(segs) and segs[seg_start][1] < t:
            seg_start += 1
        cx = cy = None
        j = seg_start
        while j < len(segs) and segs[j][0] <= t:
            s = segs[j]
            if s[0] <= t <= s[1]:
                dur = s[1] - s[0]
                p = (t - s[0]) / dur if dur > 0 else 0.0
                cx = s[2] + (s[4] - s[2]) * p
                cy = s[3] + (s[5] - s[3]) * p
                break
            j += 1
        if cx is None:
            _emit(t, off_x, off_y)
        else:
            _emit(t, int(round(cx - half)), int(round(cy - half)))

    cmds_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True

# ===============================================================
# SECTION: LRC to ASS Conversion (Subtitle Generation for FFmpeg)
# Purpose: Convert LRC format lyrics to ASS subtitle format with
#          styling, color fills, effects, and word-level timing
# ===============================================================


def lrc_to_ass(
    lrc_path: Path,
    ass_path: Path,
    font_name: str,
    font_size: int,
    line_spacing: int,
    word_padding: int,
    primary_hex: str,
    secondary_hex: str,
    outline_hex: str,
    transition_style: str,
    fx_scope: str,
    fx_speed: float,
    text_effect: str,
    reveal_mode: str,
    preview_line_count: int,
    render_width: int = 1920,
    render_height: int = 1080,
    ball_radius: int = 0,
    arc_height: int = 0,
    bounce_per_sec: float = 0.0,
    ball_rotation: float = 1.5,
    ball_icon: str = "",
    ball_color: str = "",
    ball_outline_color: str = "",
    ball_align: str = "default",
    outline_width: int = -1,
    male_primary_hex: str = "",
    male_secondary_hex: str = "",
    male_outline_hex: str = "",
    female_primary_hex: str = "",
    female_secondary_hex: str = "",
    female_outline_hex: str = "",
    both_primary_hex: str = "",
    both_secondary_hex: str = "",
    both_outline_hex: str = "",
    ball_use_overlay: bool = False,
    fx_options: dict | None = None,
):
    """Converts standard LRC files into stylized ASS subtitles for FFmpeg rendering.

    Returns a list of ball-icon motion segments ``(t0, t1, x0, y0, x1, y1)`` when
    ``ball_use_overlay`` is True (the ASS ball is suppressed so the FFmpeg PNG overlay
    can follow this path without drawing a second ball); otherwise an empty list.
    """

    # Collected only when a resolvable PNG icon drives the bouncing ball overlay.
    ball_icon_segments: list[tuple] = []

    # Convert Web standard Hex (#RRGGBB) to ASS color format (&HBBGGRR&)
    def to_ass_color(hex_str):
        hex_str = hex_str.lstrip("#")
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        return f"&H00{b}{g}{r}&"

    requested_font_name = str(font_name or "Arial").strip()
    p_color = to_ass_color(primary_hex)
    s_color = to_ass_color(secondary_hex)
    o_color = to_ass_color(outline_hex)

    # Duet voice (Male/Female) color overrides. Empty values fall back to the
    # default scheme so ungendered lines are untouched.
    def _gender_scheme(prim: str, sec: str, out: str) -> dict | None:
        if not (prim or sec or out):
            return None
        return {
            "1c": _ass_override_color(prim or primary_hex),
            "2c": _ass_override_color(sec or secondary_hex),
            "3c": _ass_override_color(out or outline_hex),
        }

    gender_schemes = {
        "m": _gender_scheme(male_primary_hex, male_secondary_hex, male_outline_hex),
        "f": _gender_scheme(
            female_primary_hex, female_secondary_hex, female_outline_hex
        ),
        "b": _gender_scheme(both_primary_hex, both_secondary_hex, both_outline_hex),
    }

    # Recomputed after parsing once we know whether any line assigns a voice.
    any_gender = False
    default_fill_tag = (
        rf"\1c{_ass_override_color(primary_hex)}"
        rf"\2c{_ass_override_color(secondary_hex)}"
        rf"\3c{_ass_override_color(outline_hex)}"
    )
    default_preview_tag = (
        rf"\1c{_ass_override_color(secondary_hex)}\3c{_ass_override_color(outline_hex)}"
    )

    def _gender_fill_tag(gender: str | None) -> str:
        # Full override (fill + karaoke pre-fill + outline) for a sung word.
        if not any_gender:
            return ""
        scheme = gender_schemes.get(gender) if gender else None
        if not scheme:
            return default_fill_tag
        return rf"\1c{scheme['1c']}\2c{scheme['2c']}\3c{scheme['3c']}"

    def _gender_preview_tag(gender: str | None) -> str:
        # Upcoming (unsung) lines render in the secondary hue; tint per voice.
        if not any_gender:
            return ""
        scheme = gender_schemes.get(gender) if gender else None
        if not scheme:
            return default_preview_tag
        return rf"\1c{scheme['2c']}\3c{scheme['3c']}"

    main_margin_v = 0
    next_margin_v = 0
    font_name = _resolve_render_font_family(requested_font_name)
    spacing = 0

    render_width = max(320, int(render_width or 1920))
    render_height = max(180, int(render_height or 1080))
    layout_scale = min(render_width / 1920, render_height / 1080)
    font_size = max(12, round(int(font_size) * layout_scale))
    # Keep ASS stroke width in step with the canvas preview's scaled lineWidth.
    default_outline = max(2, round(font_size * 0.16))
    default_shadow = 0
    if text_effect == "shadow":
        default_shadow = max(2, round(font_size * 0.08))
    elif text_effect == "hard-shadow":
        default_shadow = max(4, round(font_size * 0.14))
    elif text_effect == "glow":
        default_shadow = max(4, round(font_size * 0.12))
    elif text_effect == "neon":
        default_shadow = max(6, round(font_size * 0.18))
        default_outline = max(2, round(font_size * 0.10))
    # An explicit outline width from the UI wins over the effect defaults.
    # 0 means "no outline"; the value is in preview pixels, so scale it to the
    # render resolution exactly like the font size.
    if outline_width is not None and int(outline_width) >= 0:
        default_outline = max(0, round(int(outline_width) * layout_scale))
    line_spacing = max(4, round(int(line_spacing) * layout_scale))
    line_height = max(20, round((font_size * 1.1) + line_spacing))
    center_x = round(render_width / 2)
    center_y = round(render_height / 2)

    # Base ASS Subtitle file formatting headers
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {render_width}
PlayResY: {render_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{p_color},{s_color},{o_color},&H00000000&,-1,0,0,0,100,100,{spacing},0,1,{default_outline},{default_shadow},2,10,10,120,1
Style: Upcoming,{font_name},{font_size},{s_color},{s_color},{o_color},&H00000000&,-1,0,0,0,100,100,{spacing},0,1,{default_outline},{default_shadow},5,10,10,{next_margin_v},1
"""
    ass_header = ass_header.replace(
        ",2,10,10,120,1\n", f",5,10,10,{main_margin_v},1\n", 1
    )
    ass_header = ass_header.replace(",2,10,10,", ",5,10,10,")

    # Parse LRC lines. Support [mm:ss], [mm:ss.xx], and [mm:ss.xxx].
    # Manual chorus markers ([00:12.34]*word) are stripped here: they select which
    # words are vocalized in the chorus stem and must never appear on screen.
    lines = [
        _CHORUS_MARK_RE.sub(r"\1", raw)
        for raw in lrc_path.read_text(encoding="utf-8", errors="ignore").split("\n")
    ]
    events = [
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    # Parse LRC into timed segments. Supports [mm:ss(.xx)] line tags plus an optional
    # trailing <mm:ss(.xx)> word-end tag that lets a word finish and hold during a pause.
    line_tag_re = re.compile(r"\[(\d+):(\d+)(?:\.(\d{1,3}))?\]")
    end_tag_re = re.compile(r"<(\d+):(\d+)(?:\.(\d{1,3}))?>")
    gender_mark_re = re.compile(r"\{([mfb])\}")

    def _tag_seconds(match) -> float:
        return (
            (int(match.group(1)) * 60)
            + int(match.group(2))
            + int((match.group(3) or "0").ljust(3, "0")[:3]) / 1000.0
        )

    segments = []
    break_before = False
    for line in lines:
        # Capture the duet voice for this line, then strip the {m}/{f} marker so it
        # never renders as lyric text.
        gender_match = gender_mark_re.search(line)
        line_gender = gender_match.group(1) if gender_match else None
        line = gender_mark_re.sub("", line)
        stripped = line.strip()
        if not stripped:
            break_before = bool(segments)
            continue
        line_tags = list(line_tag_re.finditer(stripped))
        if not line_tags:
            continue
        # Split into [tag -> following text] pieces so inline word-level timestamps
        # ([t1]word1 [t2]word2) label their own words instead of duplicating the whole line.
        pieces = []
        for idx, tag in enumerate(line_tags):
            seg_end = (
                line_tags[idx + 1].start()
                if idx + 1 < len(line_tags)
                else len(stripped)
            )
            raw_seg = stripped[tag.end() : seg_end]
            seg_ends = list(end_tag_re.finditer(raw_seg))
            pieces.append(
                {
                    "start": _tag_seconds(tag),
                    "text": end_tag_re.sub("", raw_seg).strip(),
                    "end": _tag_seconds(seg_ends[-1]) if seg_ends else None,
                }
            )
        non_empty = [p for p in pieces if p["text"]]
        if len(non_empty) <= 1:
            # A single line, or a repeated line with leading stacked timestamps.
            full_text = end_tag_re.sub("", line_tag_re.sub("", stripped)).strip()
            if not full_text:
                continue
            end_tags = list(end_tag_re.finditer(stripped))
            end_val = _tag_seconds(end_tags[-1]) if end_tags else None
            for piece in pieces:
                segments.append(
                    {
                        "start": piece["start"],
                        "end": end_val,
                        "text": full_text,
                        "break_before": break_before,
                        "gender": line_gender,
                    }
                )
                break_before = False
        else:
            # Inline word-level timing: each timestamp labels the word(s) after it.
            for i, piece in enumerate(non_empty):
                segments.append(
                    {
                        "start": piece["start"],
                        "end": piece["end"],
                        "text": piece["text"],
                        "break_before": break_before and i == 0,
                        "gender": line_gender,
                    }
                )
            break_before = False

    segments.sort(key=lambda item: item["start"])
    if not segments:
        # A plain-text revision intentionally has no playback timing. Do not invent
        # synthetic timestamps: timing mode must wait for the user's S/F input.
        ass_path.write_text(ass_header + "\n" + "\n".join(events), encoding="utf-8")
        return

    # Flatten and wrap tokens exactly like buildStableLines() in the preview.
    target_words = 8
    max_gap = 0.8
    horizontal_padding = max(90, round(render_width * 0.08))
    max_text_width = max(280, render_width - (horizontal_padding * 2))
    word_padding = max(0, round(int(word_padding) * layout_scale))
    font_path = next(
        (
            path
            for path in SERVED_FONTS_DIR.iterdir()
            if path.is_file() and path.stem.lower() == requested_font_name.lower()
        ),
        None,
    )
    try:
        from PIL import ImageFont

        metric_font = (
            ImageFont.truetype(str(font_path), font_size)
            if font_path
            else ImageFont.truetype(font_name, font_size)
        )
    except Exception:
        metric_font = None

    def _text_width(value: str) -> float:
        if metric_font is not None:
            try:
                return float(metric_font.getlength(value))
            except AttributeError:
                return float(metric_font.getbbox(value)[2])
        return len(value) * font_size * 0.56

    tokens = []
    for index, segment in enumerate(segments):
        words = segment["text"].split()
        if not words:
            continue
        next_start = (
            segments[index + 1]["start"]
            if index + 1 < len(segments)
            else segment["start"] + 3.0
        )
        segment_end = (
            segment["end"]
            if segment["end"] and segment["end"] > segment["start"]
            else next_start
        )
        per_word = max(0.02, (segment_end - segment["start"]) / len(words))
        for word_index, word in enumerate(words):
            start = segment["start"] + (word_index * per_word)
            end = (
                segment["end"]
                if len(words) == 1 and segment["end"]
                else start + per_word
            )
            tokens.append(
                {
                    "word": word,
                    "start": start,
                    "end": end,
                    "is_lrc_start": word_index == 0,
                    "break_before": bool(segment.get("break_before"))
                    and word_index == 0,
                    "gender": segment.get("gender"),
                }
            )

    # Only take the per-word color path when the lyrics actually assign a voice;
    # otherwise keep the default (byte-identical) output for non-duet songs.
    any_gender = any(t.get("gender") for t in tokens)

    display_lines = []
    current = []
    current_width = 0.0
    space_width = _text_width(" ")
    for index, token in enumerate(tokens):
        word_width = _text_width(token["word"])
        previous = tokens[index - 1] if index else None
        pause_break = (
            previous
            and previous["end"] is not None
            and token["start"] - previous["end"] > max_gap
        )
        line_break = current and (
            current_width + space_width + word_width + word_padding > max_text_width
            or len(current) >= target_words
            or pause_break
            or token.get("break_before")
        )
        if line_break:
            display_lines.append(
                {
                    "kind": "words",
                    "start": current[0]["start"],
                    "text": " ".join(item["word"] for item in current),
                    "words": current,
                }
            )
            current = []
            current_width = 0.0
        current.append(token)
        current_width += (
            word_width if len(current) == 1 else space_width + word_width + word_padding
        )
    if current:
        display_lines.append(
            {
                "kind": "words",
                "start": current[0]["start"],
                "text": " ".join(item["word"] for item in current),
                "words": current,
            }
        )

    logger.info(
        "[ASS] segments=%s -> display lines=%s from %s",
        len(segments),
        len(display_lines),
        lrc_path.name,
    )

    def _format_ass_time(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def _resolve_word_timings(entry, line_end):
        """Return [(word, start, end)] honoring per-word end (F) holds where present."""
        if entry["kind"] == "line":
            words = entry["words_text"]
            n = max(1, len(words))
            span = max(0.20, line_end - entry["start"])
            per = span / n
            return [
                (w, entry["start"] + k * per, entry["start"] + (k + 1) * per)
                for k, w in enumerate(words)
            ]
        raw = entry["words"]
        n = len(raw)
        timed = []
        for k, item in enumerate(raw):
            start = item["start"]
            nxt = raw[k + 1]["start"] if k + 1 < n else line_end
            end = item["end"]
            if end is None or end <= start:
                end = nxt
            if nxt > start:
                end = min(end, nxt)
            timed.append((item["word"], start, end))
        return timed

    word_padding = max(0, round(int(word_padding) * layout_scale))

    def _karaoke_for_display(
        entry,
        line_end,
        lead_in: float = 0.0,
        word_fx_style: str = "",
        fx_speed_ms: int = 400,
    ) -> str:
        # \kf sweeps a word secondary->primary over its sung duration; a following \k on the
        # inter-word space consumes the pause so the word stays fully filled (held) until the next word.
        timed = _resolve_word_timings(entry, line_end)
        if not timed:
            return entry.get("text", "")
        entry_words = entry.get("words") if entry.get("kind") == "words" else None

        def _word_color(idx: int) -> str:
            if entry_words and 0 <= idx < len(entry_words):
                return _gender_fill_tag(entry_words[idx].get("gender"))
            return _gender_fill_tag(None)

        # Reserve the reading lead-in before the first visible word so karaoke filling still
        # begins at the original lyric timestamp rather than when the line enters the screen.
        lead_tag = (
            r"{\k" + str(max(0, int(round(lead_in * 100)))) + "}" if lead_in > 0 else ""
        )
        line_start = timed[0][1]
        if len(timed) == 1:
            word, start, end = timed[0]
            fill_cs = max(1, int(round(max(0.02, end - start) * 100)))
            fx_tag = (
                _word_transform_ass(word_fx_style, 0, fx_speed_ms)
                if word_fx_style
                else ""
            )
            return (
                lead_tag
                + r"{"
                + _word_color(0)
                + r"\kf"
                + str(fill_cs)
                + fx_tag
                + "}"
                + word
            )
        parts = [lead_tag] if lead_tag else []
        prev_end = line_start
        for idx, (word, start, end) in enumerate(timed):
            if idx > 0:
                gap_cs = max(1, int(round(max(0.0, start - prev_end) * 100)))
                padding_tag = r"{\fsp" + str(word_padding) + "}" if word_padding else ""
                reset_tag = r"{\fsp0}" if word_padding else ""
                parts.append(r"{\k" + str(gap_cs) + "}" + padding_tag + " " + reset_tag)
            fill_cs = max(1, int(round(max(0.02, end - start) * 100)))
            fx_tag = (
                _word_transform_ass(
                    word_fx_style, int(round((start - line_start) * 1000)), fx_speed_ms
                )
                if word_fx_style
                else ""
            )
            parts.append(
                r"{" + _word_color(idx) + r"\kf" + str(fill_cs) + fx_tag + "}" + word
            )
            prev_end = end
        return "".join(parts)

    def _upcoming_payload(entry) -> str:
        # Upcoming preview lines are plain (unsung) text; tint each word by its
        # duet voice so the reader can tell who sings next.
        if not any_gender or entry.get("kind") != "words":
            return entry["text"]
        words = entry.get("words") or []
        parts = []
        for idx, item in enumerate(words):
            prefix = " " if idx > 0 else ""
            parts.append(
                "{"
                + _gender_preview_tag(item.get("gender"))
                + "}"
                + prefix
                + item["word"]
            )
        return "".join(parts) or entry["text"]

    # Generate ASS event blocks with transition/mode controls mapped from preview settings.
    speed_ms = max(80, min(1800, int(float(fx_speed or 0.6) * 1000)))
    visible_lines = max(1, min(4, int(preview_line_count or 3)))
    upcoming_count = visible_lines - 1
    line_height = max(30, int((font_size * 1.1) + max(0, line_spacing)))
    reading_lead_in = 1.5
    n = len(display_lines)

    def _line_end_for(gidx: int) -> float:
        return (
            display_lines[gidx + 1]["start"]
            if gidx + 1 < n
            else display_lines[gidx]["start"] + 5.0
        )

    def _line_last_word_end(gidx: int) -> float:
        words = display_lines[gidx].get("words") or []
        if words and words[-1].get("end") is not None:
            return float(words[-1]["end"])
        return _line_end_for(gidx)

    def _appear_time(gidx: int) -> float:
        # A line pre-rolls in during any real pause before it (up to reading_lead_in) so the
        # singer can read ahead, but never overlaps the previous line's sung words.
        start = display_lines[gidx]["start"]
        if gidx <= 0:
            return max(0.0, start - min(reading_lead_in, start))
        gap = start - _line_last_word_end(gidx - 1)
        return max(0.0, start - min(reading_lead_in, max(0.0, gap)))

    def _payload_for(entry: dict, line_end: float, lead: float) -> str:
        if fx_scope == "word" and transition_style not in {"none", ""}:
            return _karaoke_for_display(
                entry,
                line_end,
                lead,
                word_fx_style=transition_style,
                fx_speed_ms=speed_ms,
            )
        return _karaoke_for_display(entry, line_end, lead)

    def _effect_mod() -> str:
        # Quick entrance flourish only; text stays fully visible (in secondary) for reading.
        if fx_scope == "word" or transition_style == "bouncing-ball":
            return ""
        half_speed = max(1, speed_ms // 2)
        opts = fx_options or {}

        def _opt(key: str) -> str:
            val = opts.get(key)
            return str(val).strip().lower() if val is not None else ""

        # --- fade: intensity scales the fade in/out duration ---
        fade_mult = {"light": 0.6, "medium": 1.0, "heavy": 1.6}.get(
            _opt("fadeIntensity"), 1.0
        )
        fade_t = max(1, int(speed_ms * fade_mult))
        # --- zoom: how small the line starts before growing to 100% ---
        zoom_start = {"subtle": 92, "medium": 84, "dramatic": 68}.get(
            _opt("zoomScale"), 84
        )
        # --- blur: starting blur radius ---
        blur_start = {"5": 4, "10": 6, "20": 10}.get(_opt("blurAmount"), 6)
        # --- rotate-360: spin direction ---
        rot_deg = -360 if _opt("rotateDirection") == "ccw" else 360
        # --- glimmer: starting transparency of the shimmer ---
        glim_alpha = {"soft": "&H33&", "normal": "&H55&", "bright": "&H77&"}.get(
            _opt("glimmerIntensity"), "&H55&"
        )
        # --- shake: wobble magnitude in degrees ---
        shake_deg = {"small": 2, "medium": 3, "strong": 5}.get(
            _opt("shakeIntensity"), 3
        )
        s1, s2, s3 = speed_ms // 3, speed_ms * 2 // 3, speed_ms
        # --- flip: axis the text flips around ---
        flip_axis = "fscy" if _opt("flipDirection") == "vertical" else "fscx"
        # --- pulse: peak scale of the heartbeat ---
        pulse_peak = {"subtle": 104, "normal": 110, "strong": 122}.get(
            _opt("pulseIntensity"), 110
        )
        # --- sway: tilt amplitude in degrees ---
        sway_deg = {"slight": 6, "normal": 12, "extreme": 20}.get(
            _opt("swayAmount"), 12
        )
        # --- skew: which axis (or both) the shear animates on ---
        skew_dir = _opt("skewDirection")
        if skew_dir == "y":
            skew_tag = rf"\fay-0.45\t(0,{half_speed},\fay0.35)\t({half_speed},{speed_ms},\fay0)"
        elif skew_dir == "both":
            skew_tag = rf"\fax-0.45\fay-0.45\t(0,{half_speed},\fax0.35\fay0.35)\t({half_speed},{speed_ms},\fax0\fay0)"
        else:
            skew_tag = rf"\fax-0.45\t(0,{half_speed},\fax0.35)\t({half_speed},{speed_ms},\fax0)"
        # --- stamp: how the text slams into place ---
        stamp_style = _opt("stampStyle")
        if stamp_style == "fade":
            stamp_tag = rf"\fscx120\fscy120\alpha&HFF&\t(0,{speed_ms},\fscx100\fscy100\alpha&H00&)"
        elif stamp_style == "bounce":
            stamp_tag = rf"\fscx150\fscy150\alpha&HFF&\t(0,{half_speed},\fscx95\fscy95\alpha&H00&)\t({half_speed},{speed_ms},\fscx100\fscy100)"
        else:
            stamp_tag = rf"\fscx138\fscy138\bord12\alpha&HFF&\t(0,{speed_ms},\fscx100\fscy100\bord5\alpha&H00&)"
        # --- focus: optional zoom layered on the blur-in ---
        focus_zoom = _opt("focusZoom")
        if focus_zoom in {"1.2", "1.5", "2.0"}:
            fz = int(float(focus_zoom) * 100)
            focus_tag = rf"{{\blur9\fscx{fz}\fscy{fz}\alpha&HFF&\t(0,{speed_ms},\blur0\fscx100\fscy100\alpha&H00&)}}"
        else:
            focus_tag = rf"{{\blur9\alpha&HFF&\t(0,{speed_ms},\blur0\alpha&H00&)}}"

        table = {
            "fade": rf"{{\fad({fade_t},{fade_t})}}",
            "pop": rf"{{\fscX112\fscY112\t(0,{speed_ms},\fscX100\fscY100)}}",
            "slide": rf"{{\move({center_x},{round(render_height * 0.889)},{center_x},{center_y},0,{speed_ms})}}",
            "zoom": rf"{{\fscX{zoom_start}\fscY{zoom_start}\t(0,{speed_ms},\fscX100\fscY100)}}",
            "drop": rf"{{\move({center_x},{round(render_height * 0.278)},{center_x},{center_y},0,{speed_ms})}}",
            "blur": rf"{{\blur{blur_start}\t(0,{speed_ms},\blur0)}}",
            "rotate-360": rf"{{\frz{rot_deg}\t(0,{speed_ms},\frz0)}}",
            "glimmer": rf"{{\alpha{glim_alpha}\t(0,{half_speed},\alpha&H00&)\t({half_speed},{speed_ms},\alpha{glim_alpha})}}",
            "shake": rf"{{\t(0,{s1},\frx{shake_deg}\fry-{shake_deg})\t({s1},{s2},\frx-{shake_deg}\fry{shake_deg})\t({s2},{s3},\frx0\fry0)}}",
            "flip": rf"{{\{flip_axis}20\t(0,{speed_ms},\{flip_axis}100)}}",
            "pulse": rf"{{\fscx82\fscy82\alpha&H55&\t(0,{half_speed},\fscx{pulse_peak}\fscy{pulse_peak}\alpha&H00&)\t({half_speed},{speed_ms},\fscx100\fscy100)}}",
            "sway": rf"{{\frz-{sway_deg}\t(0,{half_speed},\frz{sway_deg})\t({half_speed},{speed_ms},\frz0)}}",
            "skew": rf"{{{skew_tag}}}",
            "stamp": rf"{{{stamp_tag}}}",
            "focus": focus_tag,
        }
        mod = table.get(transition_style, "")
        if reveal_mode == "continuous" and transition_style in {"slide", "drop"}:
            # \move drives scrolling, so use a compatible scale/fade entrance instead.
            mod = rf"{{\fscX84\fscY84\alpha&H88&\t(0,{speed_ms},\fscX100\fscY100\alpha&H00&)}}"
        return mod

    def _emit_line(entry, gidx, row_y, dialogue_start, start_str, end_str, effect_mod):
        line_end = _line_end_for(gidx)
        lead = max(0.0, entry["start"] - dialogue_start)
        payload = _payload_for(entry, line_end, lead)
        pos_tag = rf"\an5\pos({center_x},{row_y})"
        events.append(
            f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{{pos_tag}}}{effect_mod}{payload}"
        )
        if transition_style == "bouncing-ball" and fx_scope != "word":
            ball_events, ball_segs = _build_bouncing_ball_events(
                _resolve_word_timings(entry, line_end),
                center_x,
                row_y,
                font_size,
                ball_color or p_color,
                _format_ass_time,
                ball_radius=ball_radius,
                arc_height=arc_height,
                bounce_per_sec=bounce_per_sec,
                ball_rotation=ball_rotation,
                ball_icon=ball_icon,
                outline_hex=ball_outline_color,
                layout_scale=layout_scale,
                text_width=_text_width,
                word_padding=word_padding,
                icon_overlay=ball_use_overlay,
                ball_align=ball_align,
            )
            events.extend(ball_events)
            if ball_use_overlay:
                ball_icon_segments.extend(ball_segs)

    if reveal_mode == "block":
        # Paginate into fixed groups. Every line in a page is shown together (readable in the
        # secondary color) and each sweeps to primary at its own word time; the whole page holds
        # until the next page pre-rolls in.
        for page_start in range(0, n, visible_lines):
            page = list(range(page_start, min(page_start + visible_lines, n)))
            dialogue_start = _appear_time(page[0])
            next_page = page_start + visible_lines
            if next_page < n:
                dialogue_end = max(dialogue_start + 0.05, _appear_time(next_page))
            else:
                dialogue_end = _line_end_for(page[-1])
            start_str = _format_ass_time(dialogue_start)
            end_str = _format_ass_time(dialogue_end)
            top_y = center_y - int(((len(page) - 1) * line_height) / 2)
            effect_mod = _effect_mod()
            for row, gidx in enumerate(page):
                _emit_line(
                    display_lines[gidx],
                    gidx,
                    top_y + row * line_height,
                    dialogue_start,
                    start_str,
                    end_str,
                    effect_mod,
                )
    else:
        # Eager / continuous: the active line sweeps while upcoming lines are shown as an
        # unfilled (secondary) preview so the singer can read ahead without early highlighting.
        for i in range(n):
            entry = display_lines[i]
            dialogue_start = _appear_time(i)
            next_start = _appear_time(i + 1) if i + 1 < n else entry["start"] + 5.0
            dialogue_end = max(dialogue_start + 0.05, next_start)
            start_str = _format_ass_time(dialogue_start)
            end_str = _format_ass_time(dialogue_end)
            total_visible_now = 1 + min(upcoming_count, max(0, n - (i + 1)))
            top_y = center_y - int(((total_visible_now - 1) * line_height) / 2)
            _emit_line(
                entry, i, top_y, dialogue_start, start_str, end_str, _effect_mod()
            )
            for offset in range(1, upcoming_count + 1):
                if i + offset >= n:
                    break
                next_text = _upcoming_payload(display_lines[i + offset])
                next_y = top_y + (offset * line_height)
                events.append(
                    f"Dialogue: 1,{start_str},{end_str},Upcoming,,0,0,0,,{{\\an5\\pos({center_x},{next_y})}}{next_text}"
                )

    ass_path.write_text(ass_header + "\n" + "\n".join(events), encoding="utf-8")
    return ball_icon_segments


# ===============================================================
# SECTION: FFmpeg Video Burning (Canvas Rendering to MP4/WebM)
# Purpose: Execute FFmpeg command to render ASS subtitles and
#          audio onto canvas/video, generating final karaoke video
# ===============================================================


def execute_ffmpeg_burn(
    audio_filename: str,
    font_name: str,
    font_size: int,
    line_spacing: int,
    word_padding: int,
    primary_color: str,
    secondary_color: str,
    outline_color: str,
    bg_type: str,
    bg_color: str,
    transition_style: str,
    fx_scope: str,
    fx_speed: float,
    text_effect: str,
    reveal_mode: str,
    preview_line_count: int,
    pitch: float = 1.0,
    volume: float = 1.0,
    render_device: str = DEFAULT_RENDER_DEVICE,
    job_id: str | None = None,
    use_preview_audio: bool = False,
    render_source: str = "",
    output_filename: str = "",
    render_token: str = "",
    render_width: int = 1280,
    render_height: int = 720,
    ball_radius: int = 26,
    arc_height: int = 78,
    bounce_per_sec: float = 0.1,
    ball_rotation: float = 1.5,
    ball_icon: str = "",
    ball_color: str = "",
    ball_outline_color: str = "",
    ball_align: str = "default",
    outline_width: int = -1,
    male_primary_color: str = "",
    male_secondary_color: str = "",
    male_outline_color: str = "",
    female_primary_color: str = "",
    female_secondary_color: str = "",
    female_outline_color: str = "",
    both_primary_color: str = "",
    both_secondary_color: str = "",
    both_outline_color: str = "",
    fx_options: dict | None = None,
):
    """Render a karaoke video at the requested resolution using NVENC when available."""
    audio_path = _resolve_output_file(audio_filename)
    audio_path = _ensure_project_layout_for_audio(audio_path)
    project_dir = audio_path.parent
    base_name = audio_path.stem
    lrc_path = _find_project_asset(project_dir, ".lrc", base_name)
    if not lrc_path:
        raise FileNotFoundError(
            f"No .lrc lyrics file found in project: {project_dir.name}"
        )
    ass_path = (
        _find_project_asset(project_dir, ".ass", base_name)
        or project_dir / f"{base_name}.ass"
    )
    project_label = _safe_output_name(project_dir.name, fallback_stem=base_name)
    stamp = str(render_token or datetime.now().strftime("%Y%m%d_%H%M%S"))
    normalized_source = str(render_source or "").strip().lower()
    if normalized_source not in {"preview", "final", "chorus"}:
        normalized_source = "preview" if use_preview_audio else "final"
    mode_prefix_map = {
        "preview": f"{project_label}_Karaoke_N_Vocals_",
        "chorus": f"{project_label}_Karaoke_N_Chorus_",
        "final": f"{project_label}_Karaoke_",
    }
    mode_prefix = mode_prefix_map[normalized_source]
    if output_filename:
        output_video_path = project_dir / Path(str(output_filename)).name
    else:
        output_video_path = project_dir / f"{mode_prefix}{stamp}.mp4"
    if output_video_path.exists():
        suffix = 2
        while True:
            stem = Path(output_video_path).stem
            candidate = project_dir / f"{stem}_{suffix}.mp4"
            if not candidate.exists():
                output_video_path = candidate
                break
            suffix += 1

    # Preview uses the original vocal audio; final uses the instrumental; chorus keeps vocals only during chorus sections.
    minus_track = project_dir / f"{base_name}_minus.mp3"
    chorus_track = project_dir / f"{base_name}_minus_chorus.mp3"
    stem_dir = _find_project_stems(project_dir, base_name)
    demucs_no_vocals = stem_dir / "no_vocals.wav" if stem_dir else None
    # Projects separated before the residual instrumental change still hold a
    # thin Demucs no_vocals mix; upgrade it once so renders use the full one.
    if normalized_source != "preview":
        _ensure_residual_instrumental(project_dir, audio_path, base_name, job_id or "")
    render_audio_path = audio_path
    if normalized_source == "chorus":
        # Always rebuild the chorus stem at render time so it reflects the current
        # lyrics. _build_chorus_aware_track uses the user's manual +chorus/-chorus
        # words when present, and otherwise falls back to the repeated-block
        # auto-detection algorithm to pick default chorus sections.
        no_vocals_source = None
        if minus_track.exists() and minus_track.is_file():
            no_vocals_source = minus_track
        elif demucs_no_vocals and demucs_no_vocals.exists():
            no_vocals_source = demucs_no_vocals
        if no_vocals_source:
            try:
                _build_chorus_aware_track(
                    job_id or "", audio_path, no_vocals_source, lrc_path, chorus_track
                )
            except Exception as exc:
                logger.warning("[CHORUS STEM] rebuild failed, using existing: %s", exc)
    if (
        normalized_source == "chorus"
        and chorus_track.exists()
        and chorus_track.is_file()
    ):
        render_audio_path = chorus_track
    elif normalized_source != "preview":
        if minus_track.exists() and minus_track.is_file():
            render_audio_path = minus_track
        elif (
            demucs_no_vocals
            and demucs_no_vocals.exists()
            and demucs_no_vocals.is_file()
        ):
            render_audio_path = demucs_no_vocals
    logger.info(
        "[RENDER AUDIO] using %s for %s", render_audio_path.name, audio_path.name
    )

    # 1. Compile custom styled Subtitle asset mapping
    #    Resolve the bouncing-ball PNG icon (if any). When it maps to a real file we
    #    drive it through an FFmpeg overlay instead of the ASS ball, so libass never
    #    has to embed a raster image. lrc_to_ass then returns the ball's motion path.
    ball_icon_file = _resolve_ball_icon_file(ball_icon)
    use_ball_overlay = bool(ball_icon_file) and (
        (transition_style or "").strip().lower() == "bouncing-ball"
        and (fx_scope or "").strip().lower() != "word"
    )
    ball_segments = lrc_to_ass(
        lrc_path,
        ass_path,
        font_name,
        font_size,
        line_spacing,
        word_padding,
        primary_color,
        secondary_color,
        outline_color,
        transition_style,
        fx_scope,
        fx_speed,
        text_effect,
        reveal_mode,
        preview_line_count,
        render_width=render_width,
        render_height=render_height,
        ball_radius=ball_radius,
        arc_height=arc_height,
        bounce_per_sec=bounce_per_sec,
        ball_rotation=ball_rotation,
        ball_icon=ball_icon,
        ball_color=ball_color,
        ball_outline_color=ball_outline_color,
        ball_align=ball_align,
        outline_width=outline_width,
        male_primary_hex=male_primary_color,
        male_secondary_hex=male_secondary_color,
        male_outline_hex=male_outline_color,
        female_primary_hex=female_primary_color,
        female_secondary_hex=female_secondary_color,
        female_outline_hex=female_outline_color,
        both_primary_hex=both_primary_color,
        both_secondary_hex=both_secondary_color,
        both_outline_hex=both_outline_color,
        ball_use_overlay=use_ball_overlay,
        fx_options=fx_options,
    )
    ball_segments = ball_segments or []

    # 2. Build FFmpeg command stack targeting GTX 1070 NVENC cores
    # Default fallback video background container template mapping
    bg_kind = (bg_type or "color").strip().lower()
    render_width = max(320, int(render_width or 1280))
    render_height = max(180, int(render_height or 720))
    if bg_kind in {"color", "gradient", "spiral"}:
        video_source = [
            "-f",
            "lavfi",
            "-i",
            f"color=c={bg_color.lstrip('#')}:s={render_width}x{render_height}:r=30",
        ]
    else:
        bg_img = project_dir / "custom_bg.png"
        if not bg_img.exists():
            bg_img = OUTPUT_DIR / "custom_bg.png"
        video_source = ["-loop", "1", "-framerate", "30", "-i", str(bg_img)]

    vf_filters = []
    if bg_kind not in {"color", "gradient", "spiral"}:
        vf_filters.append(
            f"scale={render_width}:{render_height}:force_original_aspect_ratio=decrease"
        )
        vf_filters.append(f"pad={render_width}:{render_height}:(ow-iw)/2:(oh-ih)/2")
    if bg_kind in {"gradient", "spiral"}:
        # Gradient/spiral backgrounds are completely static, so rasterize them
        # ONCE to a PNG instead of using ffmpeg's `geq` filter. `geq` evaluates a
        # per-pixel expression on a single CPU thread for every frame, which
        # starves the NVENC encoder and makes GPU renders run at CPU speed.
        if bg_kind == "gradient":
            ramp = _hex_to_rgb(bg_color), _hex_to_rgb(outline_color)
        else:
            ramp = _hex_to_rgb(bg_color), _hex_to_rgb(primary_color)
        static_bg = _render_static_background(
            bg_kind, ramp[0], ramp[1], render_width, render_height, project_dir
        )
        video_source = ["-loop", "1", "-framerate", "30", "-i", str(static_bg)]

    vf_filters.append(
        f"ass='{_escape_filter_path(ass_path)}':fontsdir='{_escape_filter_path(SERVED_FONTS_DIR)}'"
    )

    # Bouncing-ball PNG overlay: composite the user's icon on top of the burned
    # subtitles and move it along the ball's motion path via a sendcmd script.
    overlay_ready = False
    if use_ball_overlay and ball_segments and ball_icon_file:
        icon_scale = min(render_width / 1920, render_height / 1080)
        if ball_radius and ball_radius > 0:
            radius_px = max(2, round(ball_radius * icon_scale))
        else:
            radius_px = max(5, round(font_size * 0.12))
        icon_px = max(10, round(radius_px * 2.5))
        cmds_file = project_dir / f"{base_name}_ball_icon_cmds.txt"
        try:
            overlay_ready = _write_ball_icon_sendcmd(
                ball_segments, cmds_file, icon_px, render_width, render_height, fps=30
            )
        except Exception as exc:
            logger.warning("[BALL ICON] failed to build overlay script: %s", exc)
            overlay_ready = False

    if overlay_ready:
        init_x = int(render_width + icon_px + 20)
        base_chain = ",".join(vf_filters)
        filter_complex = (
            f"[0:v]{base_chain}[base];"
            f"[2:v]format=rgba,scale={icon_px}:{icon_px}:force_original_aspect_ratio=decrease,"
            f"pad={icon_px}:{icon_px}:(ow-iw)/2:(oh-ih)/2:color=black@0.0,"
            f"rotate=a='2*PI*{ball_rotation:.4f}*t':c=none:ow=iw:oh=ih[ic];"
            f"[base]sendcmd=f='{_escape_filter_path(cmds_file)}'[basec];"
            f"[basec][ic]overlay=x={init_x}:y=0:eval=frame[outv]"
        )
        logger.info("[BALL ICON] overlaying '%s' (%dpx) in burned video", ball_icon_file.name, icon_px)
        cmd = [
            FFMPEG_BIN,
            "-y",
            *video_source,
            "-i",
            str(render_audio_path),
            "-loop",
            "1",
            "-i",
            str(ball_icon_file),
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "1:a:0",
        ]
    else:
        cmd = [
            FFMPEG_BIN,
            "-y",
            *video_source,
            "-i",
            str(render_audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            ",".join(vf_filters),
        ]
    audio_filter = _build_audio_filter(volume=volume, pitch=pitch)
    if audio_filter:
        cmd.extend(["-filter:a", audio_filter])
    encoder_args = _render_video_encoder_args(render_device)
    actual_device = "cuda" if "h264_nvenc" in encoder_args else "cpu"
    cmd.extend(
        [
            *encoder_args,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_video_path),
        ]
    )

    if job_id:
        _run_cancellable_command(
            job_id,
            cmd,
            f"Creating Karaoke video of {audio_filename}",
            30,
            95,
            total_duration=_probe_media_duration(render_audio_path),
        )
    else:
        subprocess.run(cmd, check=True)
    return actual_device


# ===============================================================
# SECTION: API Endpoints - Video & Audio Rendering
# Purpose: POST endpoints for burning lyrics/effects to video,
#          handling Final/Chorus/Preview render requests
# ===============================================================


# ---------------------------------------------------------------
# Effect input validation (whitelists + clamps)
# ---------------------------------------------------------------
_VALID_TRANSITION_STYLES = frozenset(
    {
        "none",
        "fade",
        "pop",
        "slide",
        "zoom",
        "drop",
        "blur",
        "bouncing-ball",
        "rotate-360",
        "glimmer",
        "shake",
        "flip",
        "pulse",
        "sway",
        "skew",
        "stamp",
        "focus",
    }
)
_VALID_TEXT_EFFECTS = frozenset({"none", "shadow", "hard-shadow", "glow", "neon"})
_VALID_REVEAL_MODES = frozenset({"continuous", "block", "eager"})
_VALID_FX_SCOPES = frozenset({"page", "line", "word"})
# Must match the frontend bgType <select> values and the branches in
# ``render_karaoke_video``. "color" = solid fill, "gradient"/"spiral" are
# rasterized procedurally, "image" loads custom_bg.png.
_VALID_BG_TYPES = frozenset({"color", "gradient", "spiral", "image"})

# Bouncing-ball vertical alignment: a signed integer in [-15, +15] describing how
# far above the line's vertical center the ball rides, expressed as a multiple of
# the (resolution-scaled) font size. 0 keeps the historical "default" height;
# positive raises the ball above the words, negative lowers it toward/below them.
_BALL_ALIGN_MIN = -15
_BALL_ALIGN_MAX = 15
_BALL_ALIGN_BASE = 0.85  # font-size multiplier at value 0 (the old "default")
_BALL_ALIGN_STEP = 0.12  # multiplier change per unit of the setting


def _ball_align_multiplier(value) -> float:
    """Map the signed ball-align setting (-15..+15) to a font-size multiplier."""
    v = _clamp_int(value, _BALL_ALIGN_MIN, _BALL_ALIGN_MAX, 0)
    return _BALL_ALIGN_BASE + v * _BALL_ALIGN_STEP

# Per-effect sub-option whitelist. Keys are the frontend element IDs and values
# are the only accepted choices; anything else is dropped so nothing arbitrary
# can flow into the ASS override tags.
_VALID_FX_OPTIONS: dict[str, frozenset] = {
    "fadeIntensity": frozenset({"light", "medium", "heavy"}),
    "zoomScale": frozenset({"subtle", "medium", "dramatic"}),
    "blurAmount": frozenset({"5", "10", "20"}),
    "rotateDirection": frozenset({"cw", "ccw"}),
    "glimmerIntensity": frozenset({"soft", "normal", "bright"}),
    "shakeIntensity": frozenset({"small", "medium", "strong"}),
    "flipDirection": frozenset({"horizontal", "vertical"}),
    "pulseIntensity": frozenset({"subtle", "normal", "strong"}),
    "swayAmount": frozenset({"slight", "normal", "extreme"}),
    "skewDirection": frozenset({"x", "y", "both"}),
    "stampStyle": frozenset({"punch", "fade", "bounce"}),
    "focusZoom": frozenset({"1.2", "1.5", "2.0"}),
}


def _validate_choice(value: str, allowed: frozenset, default: str) -> str:
    """Return ``value`` if it is in ``allowed`` (case-insensitive), else ``default``."""
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _sanitize_fx_options(raw: str) -> dict:
    """Parse the fx_options JSON string and keep only whitelisted key/value pairs."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean: dict[str, str] = {}
    for key, allowed in _VALID_FX_OPTIONS.items():
        val = data.get(key)
        if val is None:
            continue
        sval = str(val).strip().lower()
        if sval in allowed:
            clean[key] = sval
    return clean


# Register job runners with the core worker-loop dispatch table.
JOB_RUNNERS["render"] = _run_render_job
JOB_RUNNERS["cdg"] = _run_cdg_job


__all__ = [n for n in dir() if not n.startswith("__")]
