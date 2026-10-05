"""HTTP API endpoints for the OnePage Karaoke suite."""
from rendering import *  # noqa: F401,F403


@app.post("/api/upload-file")
async def upload_file(
    file: UploadFile = File(...),
    stem_device: str = Form(DEFAULT_STEM_DEVICE),
    whisper_device: str = Form(DEFAULT_WHISPER_DEVICE),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    transcription_language: str = Form(DEFAULT_TRANSCRIPTION_LANGUAGE),
    profile: str = Form(""),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in SUPPORTED_AUDIO_EXTS:
        return JSONResponse(
            status_code=400, content={"message": f"Unsupported file type: {suffix}"}
        )

    target = OUTPUT_DIR / f"upload_{uuid.uuid4().hex[:8]}{suffix or '.mp3'}"

    try:
        with target.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        await file.close()

    target, identity = _apply_canonical_media_name(
        target, raw_name=file.filename or "upload"
    )
    target = _move_audio_into_project(target, identity["safe_stem"], profile)

    job = _start_pipeline_if_idle(
        target,
        identity["display"],
        stem_device=stem_device,
        whisper_device=whisper_device,
        whisper_model=whisper_model,
        transcription_language=transcription_language,
        lyrics_query=identity["display"],
        display_title=identity["display"],
    )
    if job:
        return {
            "status": "queued",
            "message": f"Uploaded {target.name} and queued pipeline.",
            "job": job,
        }
    return {
        "status": "busy",
        "message": f"Uploaded {target.name}. A pipeline job already exists for it.",
    }


@app.post("/api/process-url")
def process_url(
    url: str = Form(...),
    engine: str = Form("ytdl"),
    stem_device: str = Form(DEFAULT_STEM_DEVICE),
    whisper_device: str = Form(DEFAULT_WHISPER_DEVICE),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    transcription_language: str = Form(DEFAULT_TRANSCRIPTION_LANGUAGE),
    profile: str = Form(""),
):
    url = (url or "").strip()
    if not url:
        return JSONResponse(status_code=400, content={"message": "URL is required."})

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return JSONResponse(
            status_code=400, content={"message": "Only http/https URLs are supported."}
        )

    engine_norm = (engine or "ytdl").strip().lower()
    if engine_norm not in {"ytdl", "metube"}:
        return JSONResponse(
            status_code=400, content={"message": f"Unsupported engine: {engine}"}
        )

    job = _enqueue_job(
        "url",
        f"Download from URL ({engine_norm})",
        "url",
        target_key=f"url:{url}:{engine_norm}",
        url=url,
        engine=engine_norm,
        stem_device=stem_device,
        whisper_device=whisper_device,
        whisper_model=whisper_model,
        transcription_language=_normalize_language(transcription_language),
        profile=_safe_profile_name(profile) if profile else _active_profile(),
        section="ingest",
        stage="download",
        details=f"Stem device: {stem_device}; Whisper device: {whisper_device}; Model: {whisper_model}; Language: {transcription_language}",
    )
    if engine_norm == "metube":
        return {
            "status": "queued",
            "message": "Queued MeTube download and pipeline processing.",
            "job": job,
        }
    return {
        "status": "queued",
        "message": "Queued URL download and pipeline processing.",
        "job": job,
    }


# ===============================================================
# SECTION: API Endpoints - Media Vault Profiles
# Purpose: Create/list/select profiles and copy or move projects
#          between profiles. Projects live under output/<profile>/.
# ===============================================================


@app.get("/api/profiles")
def get_profiles():
    reg = _load_profiles_registry()
    return {"profiles": reg["profiles"], "active": reg["active"]}


@app.post("/api/profiles")
def create_profile(name: str = Form(...)):
    safe = _safe_profile_name(name)
    if not safe:
        return JSONResponse(
            status_code=400, content={"message": "Invalid profile name."}
        )
    if safe in RESERVED_OUTPUT_NAMES:
        return JSONResponse(
            status_code=400,
            content={"message": f"'{safe}' is a reserved name."},
        )
    _ensure_profile(safe)
    reg = _load_profiles_registry()
    return {
        "status": "success",
        "message": f"Profile '{safe}' ready.",
        "created": safe,
        "profiles": reg["profiles"],
        "active": reg["active"],
    }


@app.post("/api/profiles/active")
def set_active_profile_endpoint(name: str = Form(...)):
    safe = _safe_profile_name(name)
    if not safe:
        return JSONResponse(
            status_code=400, content={"message": "Invalid profile name."}
        )
    active = _set_active_profile(safe)
    reg = _load_profiles_registry()
    return {"status": "success", "active": active, "profiles": reg["profiles"]}


@app.post("/api/profiles/delete")
def delete_profile(name: str = Form(...)):
    safe = _safe_profile_name(name)
    if safe == DEFAULT_PROFILE:
        return JSONResponse(
            status_code=400,
            content={"message": "The Default profile cannot be deleted."},
        )
    reg = _load_profiles_registry()
    if safe not in reg["profiles"]:
        return JSONResponse(
            status_code=404, content={"message": f"Unknown profile: {safe}"}
        )

    # Relocate any projects into Default rather than destroying them.
    source_root = OUTPUT_DIR / safe
    default_root = OUTPUT_DIR / DEFAULT_PROFILE
    default_root.mkdir(parents=True, exist_ok=True)
    moved = []
    if source_root.exists():
        for path in list(source_root.iterdir()):
            if path.is_dir() and path.name != "stems":
                dest = _unique_project_dir(default_root, path.name)
                try:
                    shutil.move(str(path), str(dest))
                    moved.append(dest.name)
                except Exception:
                    logger.warning(
                        "[PROFILES] could not relocate %s during delete", path.name
                    )
        try:
            shutil.rmtree(source_root, ignore_errors=True)
        except Exception:
            pass

    reg["profiles"] = [p for p in reg["profiles"] if p != safe]
    if reg["active"] == safe:
        reg["active"] = DEFAULT_PROFILE
    _save_profiles_registry(reg)
    return {
        "status": "success",
        "message": (
            f"Deleted profile '{safe}'."
            + (f" Moved {len(moved)} project(s) to Default." if moved else "")
        ),
        "profiles": reg["profiles"],
        "active": reg["active"],
        "relocated": moved,
    }


@app.post("/api/transfer-project")
def transfer_project(
    audio_filename: str = Form(...),
    target_profile: str = Form(...),
    mode: str = Form("copy"),
):
    mode_norm = (mode or "copy").strip().lower()
    if mode_norm not in {"copy", "move"}:
        return JSONResponse(
            status_code=400, content={"message": f"Unsupported mode: {mode}"}
        )
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    project_dir = audio_path.parent
    source_profile = project_dir.parent.name
    dest_profile = _ensure_profile(target_profile)
    if dest_profile == source_profile:
        return JSONResponse(
            status_code=400,
            content={"message": "Source and destination profiles are the same."},
        )

    dest_root = OUTPUT_DIR / dest_profile
    old_rel_project = _relative_to_output(project_dir)

    if mode_norm == "move" and _project_has_active_jobs(old_rel_project):
        return JSONResponse(
            status_code=409,
            content={
                "message": "Project is busy with an active job. Try again once it finishes."
            },
        )

    dest_dir = _unique_project_dir(dest_root, project_dir.name)
    try:
        if mode_norm == "move":
            shutil.move(str(project_dir), str(dest_dir))
        else:
            shutil.copytree(str(project_dir), str(dest_dir))
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"message": f"Transfer failed: {exc}"},
        )

    new_audio = next(
        (p for p in dest_dir.iterdir() if _is_source_media(p)), None
    )
    new_rel_audio = _relative_to_output(new_audio) if new_audio else ""

    if mode_norm == "move" and new_rel_audio:
        # Keep in-flight/known job references pointing at the new location.
        _rewrite_project_references(
            project_dir.name, dest_dir.name, _relative_to_output(audio_path), new_rel_audio
        )

    verb = "Moved" if mode_norm == "move" else "Copied"
    return {
        "status": "success",
        "message": f"{verb} '{project_dir.name}' to profile '{dest_profile}'.",
        "mode": mode_norm,
        "target_profile": dest_profile,
        "project_name": dest_dir.name,
        "audio_filename": new_rel_audio,
    }


# ===============================================================
# SECTION: API Endpoints - Lyrics & Audio Processing
# Purpose: POST endpoints for fetching, transcribing, and timing
#          lyrics automatically using various AI services
# ===============================================================


@app.post("/api/timing-mode")
def set_timing_mode(active: str = Form("0")):
    # Frontend heartbeat: pause automatic ingest/lyrics jobs while hand-timing lyrics.
    is_active = str(active).strip().lower() in {"1", "true", "yes", "on"}
    _TIMING_STATE["until"] = (time.time() + 120.0) if is_active else 0.0
    return {"status": "ok", "active": is_active, "paused_until": _TIMING_STATE["until"]}


@app.post("/api/auto-grab-lyrics")
def auto_grab_lyrics(
    audio_filename: str = Form(...),
    provider: str = Form("lrclib"),
    suno_url: str = Form(""),
):
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
        rel_audio = _relative_to_output(audio_path)
        project_name = audio_path.parent.name
        provider_norm = str(provider or "lrclib").strip().lower()
        if provider_norm not in {"auto", "lrclib", "genius", "syncedlyrics", "suno"}:
            provider_norm = "auto"

        if provider_norm == "suno" and not str(suno_url or "").strip():
            return JSONResponse(
                status_code=400,
                content={"message": "Paste a Suno song link or ID to pull from Suno."},
            )

        job = _enqueue_job(
            "lyrics",
            f"Grab lyrics ({provider_norm}) for {project_name}",
            "lyrics",
            section="ingest",
            stage="lyrics fetch",
            target_key=f"lyrics:{rel_audio}:{provider_norm}",
            audio_filename=rel_audio,
            project_name=audio_path.parent.name,
            lyrics_query=audio_path.stem,
            display_title=audio_path.stem,
            provider=provider_norm,
            suno_url=str(suno_url or "").strip(),
            force=True,
            details=f"Provider: {provider_norm}",
        )
        lrc_rel = _relative_to_output(audio_path.parent / f"{audio_path.stem}.lrc")
        return {
            "status": "queued",
            "message": f"Queued lyrics grab via {provider_norm} for {project_name}.",
            "job": job,
            "filename": lrc_rel,
        }
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"message": f"Auto-grab lyrics failed: {exc}"}
        )


@app.post("/api/auto-transcribe")
def auto_transcribe(
    audio_filename: str = Form(...),
    stem_device: str = Form(DEFAULT_STEM_DEVICE),
    whisper_device: str = Form(DEFAULT_WHISPER_DEVICE),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    transcription_language: str = Form(DEFAULT_TRANSCRIPTION_LANGUAGE),
):
    try:
        audio_path = _resolve_output_file(audio_filename)
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    job = _start_pipeline_if_idle(
        audio_path,
        audio_path.stem,
        stem_device=stem_device,
        whisper_device=whisper_device,
        whisper_model=whisper_model,
        transcription_language=transcription_language,
    )
    if not job:
        return {
            "status": "busy",
            "message": f"{audio_path.stem} is already queued or being processed.",
        }
    return {
        "status": "queued",
        "message": f"Queued auto-transcribe/sync for {audio_path.stem}.",
        "job": job,
    }


@app.post("/api/auto-correct-word-timing")
def auto_correct_word_timing(
    audio_filename: str = Form(...),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    whisper_device: str = Form(DEFAULT_WHISPER_DEVICE),
    timing_mode: str = Form("major"),
    max_offset_seconds: float = Form(5.0),
):
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    rel_audio = _relative_to_output(audio_path)
    project_name=audio_path.parent.name

    selected_mode = str(timing_mode or "major").strip().lower()
    if selected_mode not in {"minor", "major", "safe-word", "custom"}:
        selected_mode = "safe-word"
    # Clamp the user-selected max offset so a bad value can't blow out timing.
    clamped_offset = max(0.5, min(30.0, float(max_offset_seconds or 5.0)))
    mode_label = {
        "minor": "Minor",
        "custom": f"Custom (\u00b1{clamped_offset:g}s)",
        "safe-word": "Safe word-level",
    }.get(selected_mode, "Major")
    job_kwargs = dict(
        audio_filename=rel_audio,
        project_name=audio_path.parent.name,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
        timing_mode=selected_mode,
        details=f"Mode: {mode_label}; Model: {whisper_model}; Preferred device: {whisper_device}",
    )
    if selected_mode == "custom":
        job_kwargs["max_offset_seconds"] = clamped_offset
    job = _enqueue_job(
        "word_timing",
        f"{mode_label} AI timing for {audio_filename}",
        "word_timing",
        section="editor",
        stage="ai word align",
        target_key=f"word_timing:{rel_audio}",
        **job_kwargs,
    )
    return {
        "status": "queued",
        "message": f"Queued {mode_label.lower()} AI timing correction for {project_name}.",
        "job": job,
    }


@app.post("/api/delete-media")
def delete_media(audio_filename: str = Form(...)):
    try:
        audio_path = _resolve_output_file(audio_filename)
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    audio_path = _ensure_project_layout_for_audio(audio_path)
    rel_audio = _relative_to_output(audio_path)
    project_dir = audio_path.parent

    with JOB_LOCK:
        related_ids = [
            job_id
            for job_id, job in JOBS.items()
            if (
                job.get("audio_filename") == rel_audio
                or job.get("project_name") == project_dir.name
            )
            and job["status"] in {"queued", "running"}
        ]
    for job_id in related_ids:
        _cancel_job(job_id)

    deleted = []
    if project_dir != OUTPUT_DIR and project_dir.exists():
        deleted.append(project_dir.name)
        shutil.rmtree(project_dir, ignore_errors=True)
        return {
            "status": "success",
            "message": f"Deleted project {project_dir.name}.",
            "deleted": deleted,
        }

    # Legacy fallback if media is still at output root.
    stem = audio_path.stem
    candidates = [audio_path, *_project_sidecar_paths(OUTPUT_DIR, stem)]
    stems_dir = OUTPUT_DIR / "stems" / "htdemucs" / stem
    if stems_dir.exists():
        shutil.rmtree(stems_dir, ignore_errors=True)
        deleted.append(str(stems_dir.name))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            candidate.unlink(missing_ok=True)
            deleted.append(candidate.name)
    return {
        "status": "success",
        "message": f"Deleted {stem} and related assets.",
        "deleted": deleted,
    }


@app.post("/api/rename-media")
def rename_media(audio_filename: str = Form(...), new_name: str = Form(...)):
    try:
        audio_path = _resolve_output_file(audio_filename)
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    audio_path = _ensure_project_layout_for_audio(audio_path)
    old_project = audio_path.parent
    profile_dir = old_project.parent
    old_rel_project = _relative_to_output(old_project)
    old_rel_audio = _relative_to_output(audio_path)

    proposed_project = _safe_project_name(new_name, fallback=old_project.name)
    if not proposed_project:
        return JSONResponse(
            status_code=400, content={"message": "Invalid project name."}
        )

    target_rel_project = f"{_relative_to_output(profile_dir)}/{proposed_project}"
    target_project = OUTPUT_DIR / target_rel_project
    with RENAME_LOCK:
        reserved_names = set(PENDING_PROJECT_RENAMES.values()) - {
            PENDING_PROJECT_RENAMES.get(old_rel_project)
        }
        if target_rel_project in reserved_names or (
            target_project.exists()
            and target_project.resolve() != old_project.resolve()
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "message": f"Project already exists or is being renamed to: {proposed_project}"
                },
            )
        PENDING_PROJECT_RENAMES[old_rel_project] = target_rel_project
        completed = _finalize_pending_project_renames()

    finalized = next(
        (item for item in completed if item["old_project"] == old_rel_project), None
    )
    if finalized:
        target_audio = _resolve_output_file(finalized["audio_filename"])
        lrc_path = _find_project_asset(target_audio.parent, ".lrc", target_audio.stem)
        return {
            "status": "success",
            "message": f"Renamed project to {finalized['project_name']}.",
            "project_name": finalized["project_name"],
            "audio_filename": finalized["audio_filename"],
            "lrc_filename": _relative_to_output(lrc_path) if lrc_path else "",
            "audio_url": f"/files/{quote(finalized['audio_filename'], safe='/')}",
            "rename_pending": False,
        }
    return {
        "status": "success",
        "message": f"Rename to {proposed_project} is queued and will finish after active work releases {old_project.name}.",
        "project_name": old_project.name,
        "audio_filename": old_rel_audio,
        "rename_pending": True,
        "requested_project_name": proposed_project,
    }


@app.post("/api/burn-video")
def burn_video(
    audio_filename: str = Form(...),
    font_name: str = Form(...),
    font_size: int = Form(...),
    line_spacing: int = Form(30),
    word_padding: int = Form(0),
    primary_color: str = Form(...),
    secondary_color: str = Form("#00ffff"),
    outline_color: str = Form(...),
    bg_type: str = Form(...),
    bg_color: str = Form(...),
    transition_style: str = Form(...),
    fx_scope: str = Form("page"),
    fx_speed: float = Form(0.6),
    text_effect: str = Form("none"),
    reveal_mode: str = Form("continuous"),
    preview_line_count: int = Form(3),
    pitch: float = Form(1.0),
    volume: float = Form(1.0),
    render_device: str = Form(DEFAULT_RENDER_DEVICE),
    use_preview_audio: bool = Form(False),
    render_source: str = Form(""),
    render_width: int = Form(1280),
    render_height: int = Form(720),
    ball_radius: int = Form(26),
    arc_height: int = Form(78),
    bounce_per_sec: float = Form(0.1),
    ball_rotation: float = Form(1.5),
    ball_icon: str = Form(""),
    ball_color: str = Form("#ffffff"),
    ball_outline_color: str = Form("#000000"),
    ball_align: str = Form("0"),
    outline_width: int = Form(-1),
    male_primary_color: str = Form("#59b0ff"),
    male_secondary_color: str = Form("#2b6cb0"),
    male_outline_color: str = Form("#08213a"),
    female_primary_color: str = Form("#ff7ec8"),
    female_secondary_color: str = Form("#b83d86"),
    female_outline_color: str = Form("#3a0824"),
    both_primary_color: str = Form("#ff4d4d"),
    both_secondary_color: str = Form("#8b1a1a"),
    both_outline_color: str = Form("#2a0606"),
    fx_options: str = Form(""),
):
    rel_audio = audio_filename
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
        project_name = audio_path.parent.name
        rel_audio = _relative_to_output(audio_path)
    except Exception:
        parts = Path(audio_filename).parts
        project_name = parts[-2] if len(parts) >= 2 else ""

    safe_project = _safe_output_name(
        project_name, fallback_stem=Path(rel_audio).stem or "project"
    )
    # Whitelist/clamp all effect-related inputs so malformed or hostile form
    # values can never reach the ASS generator; unknown choices fall back to a
    # safe default rather than raising.
    transition_style = _validate_choice(
        transition_style, _VALID_TRANSITION_STYLES, "none"
    )
    text_effect = _validate_choice(text_effect, _VALID_TEXT_EFFECTS, "none")
    reveal_mode = _validate_choice(reveal_mode, _VALID_REVEAL_MODES, "continuous")
    fx_scope = _validate_choice(fx_scope, _VALID_FX_SCOPES, "page")
    bg_type = _validate_choice(bg_type, _VALID_BG_TYPES, "color")
    ball_align = _clamp_int(ball_align, -15, 15, 0)
    fx_speed = _clamp_float(fx_speed, 0.08, 1.8, 0.6)
    preview_line_count = _clamp_int(preview_line_count, 1, 4, 3)
    font_size = _clamp_int(font_size, 8, 400, 72)
    line_spacing = _clamp_int(line_spacing, 0, 400, 30)
    word_padding = _clamp_int(word_padding, 0, 200, 0)
    pitch = _clamp_float(pitch, 0.5, 2.0, 1.0)
    volume = _clamp_float(volume, 0.0, 4.0, 1.0)
    fx_options_dict = _sanitize_fx_options(fx_options)
    render_width = max(320, int(render_width or 1280))
    render_height = max(180, int(render_height or 720))
    render_resolution = f"{render_width}x{render_height}"
    render_token = datetime.now().strftime("%Y%m%d_%H%M%S")
    normalized_source = str(render_source or "").strip().lower()
    if normalized_source not in {"preview", "final", "chorus"}:
        normalized_source = "preview" if use_preview_audio else "final"
    # Karaoke + Vocals -> _Karaoke_N_Vocals, Karaoke + Chorus -> _Karaoke_N_Chorus,
    # Std. Karaoke -> _Karaoke
    mode_suffix_map = {
        "preview": "_Karaoke_N_Vocals",
        "chorus": "_Karaoke_N_Chorus",
        "final": "_Karaoke",
    }
    mode_suffix = mode_suffix_map[normalized_source]
    output_filename = (
        f"{safe_project}{mode_suffix}_{render_resolution}_{render_token}.mp4"
    )

    job = _enqueue_job(
        "render",
        f"Render {rel_audio}",
        "render",
        section="render",
        stage="Creating Karaoke Video",
        target_key=f"render:{rel_audio}:{normalized_source}:{render_token}",
        audio_filename=rel_audio,
        project_name=project_name,
        font_name=font_name,
        font_size=font_size,
        line_spacing=line_spacing,
        word_padding=word_padding,
        primary_color=primary_color,
        secondary_color=secondary_color,
        outline_color=outline_color,
        bg_type=bg_type,
        bg_color=bg_color,
        transition_style=transition_style,
        fx_scope=fx_scope,
        fx_speed=fx_speed,
        text_effect=text_effect,
        reveal_mode=reveal_mode,
        preview_line_count=preview_line_count,
        pitch=pitch,
        volume=volume,
        fx_options=fx_options_dict,
        render_device=render_device,
        use_preview_audio=use_preview_audio,
        render_source=normalized_source,
        output_filename=output_filename,
        render_token=render_token,
        render_width=render_width,
        render_height=render_height,
        render_resolution=render_resolution,
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
        details=(
            f"Resolution: {render_resolution}; Source: {rel_audio}; Output: {output_filename}; "
            f"Render device: {render_device}; Pitch: {pitch}; Volume: {volume}; "
            f"Font: {font_name}; Size: {font_size}; BG: {bg_type}; FX: {transition_style}/{text_effect}; "
            f"Mode: {reveal_mode}; Speed: {fx_speed}; Scope: {fx_scope}; "
            f"Audio Source: {normalized_source.capitalize()}"
        ),
    )
    return {"status": "queued", "message": "Queued FFmpeg render job.", "job": job}


@app.post("/api/burn-cdg")
def burn_cdg(
    audio_filename: str = Form(...),
    font_name: str = Form("Arial"),
    primary_color: str = Form("#ffe14d"),
    secondary_color: str = Form("#9fb4ff"),
    bg_color: str = Form("#000820"),
    render_source: str = Form("final"),
    lines_per_page: int = Form(4),
):
    """Queue a CDG+MP3 karaoke export (zipped) built from the project's lyrics."""
    rel_audio = audio_filename
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
        project_name = audio_path.parent.name
        rel_audio = _relative_to_output(audio_path)
    except Exception:
        parts = Path(audio_filename).parts
        project_name = parts[-2] if len(parts) >= 2 else ""

    safe_project = _safe_output_name(
        project_name, fallback_stem=Path(rel_audio).stem or "project"
    )
    normalized_source = str(render_source or "final").strip().lower()
    if normalized_source not in {"preview", "final", "chorus"}:
        normalized_source = "final"
    try:
        lines_pp = max(1, min(6, int(lines_per_page or 4)))
    except Exception:
        lines_pp = 4
    render_token = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{safe_project}_CDG_{normalized_source}_{render_token}.zip"

    job = _enqueue_job(
        "cdg",
        f"CDG export {rel_audio}",
        "cdg",
        section="render",
        stage="Creating CDG+MP3",
        target_key=f"cdg:{rel_audio}:{normalized_source}:{render_token}",
        audio_filename=rel_audio,
        project_name=project_name,
        font_name=font_name,
        primary_color=primary_color,
        secondary_color=secondary_color,
        bg_color=bg_color,
        render_source=normalized_source,
        lines_per_page=lines_pp,
        output_filename=output_filename,
        render_token=render_token,
        render_source_label=normalized_source,
        details=(
            f"CDG+MP3 export; Source: {rel_audio}; Output: {output_filename}; "
            f"Font: {font_name}; Lines/page: {lines_pp}; "
            f"Audio Source: {normalized_source.capitalize()}"
        ),
    )
    return {"status": "queued", "message": "Queued CDG+MP3 export job.", "job": job}


# ===============================================================
# SECTION: API Endpoints - Job Management & Utilities
# Purpose: List/cancel jobs, manage fonts/themes, export projects,
#          save/load project state, and health checks
# ===============================================================


@app.get("/api/jobs")
def list_jobs(project_name: str = ""):
    with JOB_LOCK:
        jobs = [
            _job_view(job)
            for job in sorted(
                JOBS.values(), key=lambda item: item["created_at"], reverse=True
            )
        ]
    if project_name:
        # Keep project-less ingest/download jobs visible in every view: a fresh URL
        # download has no resolved project name until it finishes downloading.
        jobs = [
            job
            for job in jobs
            if job.get("project_name") == project_name or not job.get("project_name")
        ]
    return {"jobs": jobs}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = _cancel_job(job_id)
    if not job:
        return JSONResponse(
            status_code=404, content={"message": f"Job not found: {job_id}"}
        )
    return {
        "status": "success",
        "message": f"Cancellation requested for {job['label']}",
        "job": job,
    }


@app.post("/api/jobs/clear")
def clear_inactive_jobs():
    with JOB_LOCK:
        # Identify jobs that are no longer actively queued or running
        inactive_ids = [
            job_id for job_id, job in JOBS.items()
            if job.get("status") in {"completed", "failed", "cancelled"}
        ]

        # Remove them from the global state
        for jid in inactive_ids:
            JOBS.pop(jid, None)
            if jid in JOB_QUEUE:
                JOB_QUEUE.remove(jid)

    return {"status": "success", "cleared_count": len(inactive_ids)}


@app.get("/api/get-fonts")
def get_fonts():
    return {"fonts": _refresh_font_cache()}


@app.get("/api/themes")
def get_themes():
    try:
        return {"themes": _load_theme_catalog()}
    except Exception as exc:
        # Never 500 the theme picker; the frontend falls back to built-in themes.
        logger.warning("[THEMES] Failed to build theme catalog: %s", exc)
        return {"themes": [], "message": f"Theme catalog error: {exc}"}


@app.get("/api/debug-report")
def debug_report():
    syntax_issues = _collect_python_syntax_issues()
    runtime_issues = _collect_runtime_issues()
    return {
        "generated_at": int(time.time()),
        "syntax_issues": syntax_issues,
        "runtime_issues": runtime_issues,
        "summary": {
            "syntax_error_count": len(syntax_issues),
            "runtime_error_count": len(runtime_issues),
        },
    }


@app.post("/api/save-lyrics")
def save_lyrics(filename: str = Form(...), content: str = Form(...)):
    target_path = _resolve_output_path(filename, require_exists=False)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_path.write_text(content, encoding="utf-8")
        return {"status": "success", "message": "Lyrics data updated successfully."}
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"message": f"Write access failure: {exc}"}
        )


@app.get("/api/load-lyrics")
def load_lyrics(filename: str):
    p = _resolve_output_path(filename, require_exists=False)
    content = p.read_text(encoding="utf-8") if p.exists() else ""
    # Try to extract provider info from metadata file next to LRC
    metadata_file = (
        p.with_suffix(".lrc.meta")
        if p.suffix == ".lrc"
        else p.parent / f"{p.stem}.lrc.meta"
    )
    provider = None
    if metadata_file.exists():
        try:
            meta = json.loads(metadata_file.read_text(encoding="utf-8"))
            provider = meta.get("provider")
        except Exception:
            pass
    return {"content": content, "provider": provider}


@app.get("/api/list-files")
def list_files(sources_only: bool = False, profile: str = ""):
    projects = _list_project_manifests(profile)
    if sources_only:
        projects = [item for item in projects if item.get("audio_filename")]
    return {"files": projects, "profile": _safe_profile_name(profile) if profile else _active_profile()}


@app.get("/api/export-project")
def export_project(audio_filename: str):
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    project_dir = audio_path.parent
    archive_name = f"{project_dir.name}.zip"
    archive_file = tempfile.NamedTemporaryFile(
        prefix="onepage-karaoke-", suffix=".zip", delete=False
    )
    archive_path = Path(archive_file.name)
    archive_file.close()

    try:
        with zipfile.ZipFile(
            archive_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            for source_path in sorted(project_dir.rglob("*")):
                if source_path.is_file() and not source_path.is_symlink():
                    archive.write(
                        source_path,
                        Path(project_dir.name) / source_path.relative_to(project_dir),
                    )
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        return JSONResponse(
            status_code=500, content={"message": f"Failed to export project: {exc}"}
        )

    def stream_archive():
        try:
            with archive_path.open("rb") as archive_stream:
                while chunk := archive_stream.read(1024 * 1024):
                    yield chunk
        finally:
            archive_path.unlink(missing_ok=True)

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(archive_name)}"
    }
    return StreamingResponse(
        stream_archive(), media_type="application/zip", headers=headers
    )


@app.get("/api/vocal-waveform")
def vocal_waveform(audio_filename: str):
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    stem_dir = _find_project_stems(audio_path.parent, audio_path.stem)
    if not stem_dir:
        return JSONResponse(
            status_code=404,
            content={"message": "No vocals stem found for this project."},
        )
    vocal_path = next(
        (
            stem_dir / name
            for name in ("vocals.wav", "vocals.mp3")
            if (stem_dir / name).is_file()
        ),
        None,
    )
    if not vocal_path:
        return JSONResponse(
            status_code=404,
            content={"message": "No vocals audio file found for this project."},
        )
    relative = _relative_to_output(vocal_path)
    return {"filename": relative, "url": f"/files/{quote(relative, safe='/')}"}


@app.get("/api/load-project-state")
def load_project_state(audio_filename: str):
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    project_dir = audio_path.parent
    proj_path = _find_project_state(project_dir)
    if not proj_path:
        return {"status": "missing", "project_name": project_dir.name, "state": {}}

    try:
        state = json.loads(proj_path.read_text(encoding="utf-8"))
        return {"status": "success", "project_name": project_dir.name, "state": state}
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"message": f"Failed to load project state: {exc}"}
        )


@app.post("/api/save-project-state")
def save_project_state(audio_filename: str = Form(...), state_json: str = Form(...)):
    try:
        audio_path = _ensure_project_layout_for_audio(
            _resolve_output_file(audio_filename)
        )
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    project_dir = audio_path.parent
    proj_path = (
        _find_project_state(project_dir)
        or project_dir / f"{project_dir.name}.proj.json"
    )
    try:
        parsed = json.loads(state_json)
    except Exception as exc:
        return JSONResponse(
            status_code=400, content={"message": f"Invalid state JSON: {exc}"}
        )

    parsed["project_name"] = project_dir.name
    parsed["audio_filename"] = _relative_to_output(audio_path)
    parsed["saved_at"] = int(time.time())
    proj_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    global_bg = OUTPUT_DIR / "custom_bg.png"
    if parsed.get("preview", {}).get("bgType") == "image" and global_bg.exists():
        try:
            shutil.copy2(global_bg, project_dir / "custom_bg.png")
        except Exception:
            pass

    return {
        "status": "success",
        "message": f"Saved project state to {proj_path.name}.",
        "project_name": project_dir.name,
    }


@app.post("/api/upload-bg")
def upload_bg(file: UploadFile = File(...), audio_filename: str = Form("")):
    target = OUTPUT_DIR / "custom_bg.png"
    if audio_filename:
        try:
            audio_path = _ensure_project_layout_for_audio(
                _resolve_output_file(audio_filename)
            )
            target = audio_path.parent / "custom_bg.png"
        except FileNotFoundError:
            pass
    with target.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "success"}


# Image formats a browser can render for the bouncing-ball icon preview.
_ICON_UPLOAD_EXTS = {".png", ".gif", ".webp", ".jpg", ".jpeg", ".bmp", ".ico", ".svg"}


@app.post("/api/upload-icon")
def upload_icon(file: UploadFile = File(...)):
    """Store a user-picked ball icon (PNG, etc.) under ICONS_DIR and return its
    browser-reachable path (served by the /icons static mount)."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ICON_UPLOAD_EXTS:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Unsupported icon type: {suffix or '(none)'}. "
                f"Allowed: {', '.join(sorted(_ICON_UPLOAD_EXTS))}",
            },
        )
    safe_stem = _safe_output_name(file.filename or "icon", fallback_stem="icon")
    target = ICONS_DIR / f"{safe_stem}{suffix}"
    counter = 2
    while target.exists():
        target = ICONS_DIR / f"{safe_stem}_{counter}{suffix}"
        counter += 1
    try:
        with target.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    finally:
        file.file.close()
    icon_path = f"/icons/{target.name}"
    logger.info("[BALL ICON] uploaded %s -> %s", file.filename, icon_path)
    return {"status": "success", "path": icon_path, "filename": target.name}


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index_page():
    return (FRONTEND_DIR / "index.html").read_text()
