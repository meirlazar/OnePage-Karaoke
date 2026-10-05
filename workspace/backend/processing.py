"""Audio processing: Whisper transcription, Demucs stem separation, URL
ingest, and multi-source lyrics fetching / timing correction."""
from core import *  # noqa: F401,F403



# ===============================================================
# SECTION: Whisper Transcription (AI Speech-to-Text)
# Purpose: Use faster-whisper model to transcribe audio to text
#          with word-level timing and multiple language support
# ===============================================================


def _transcribe_with_faster_whisper(
    audio_path: Path,
    *,
    model_name: str,
    device: str,
    compute_type: str,
    language: str = "auto",
    initial_prompt: str = "",
) -> dict:
    from faster_whisper import WhisperModel

    model = None
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        kwargs = {
            "word_timestamps": True,
            "vad_filter": True,
            "condition_on_previous_text": False,
        }
        normalized_language = _normalize_language(language)
        if normalized_language != "auto":
            kwargs["language"] = normalized_language
        if initial_prompt.strip():
            kwargs["initial_prompt"] = initial_prompt.strip()[:1500]

        segments, info = model.transcribe(str(audio_path), **kwargs)
        return _serialize_faster_whisper_payload(segments, info)
    finally:
        # Drop model references immediately so memory returns before next attempt.
        model = None
        _release_runtime_resources(f"post transcription cleanup ({audio_path.name})")


def _run_faster_whisper_with_fallback(
    job_id: str,
    audio_path: Path,
    *,
    whisper_model: str,
    whisper_device: str,
    language: str = "auto",
    start_progress: int,
    end_progress: int,
    stage: str,
    fallback_stage: str,
    action_label: str,
    initial_prompt: str = "",
) -> tuple[dict, str, str]:
    preferred_device = _effective_ai_device(whisper_device)
    project_name = audio_path.parent.name
    attempts = [preferred_device]
    if preferred_device == "cuda":
        attempts.append("cpu")

    last_error: Exception | None = None
    for idx, device in enumerate(dict.fromkeys(attempts)):
        current_stage = fallback_stage if idx > 0 else stage
        compute_candidates = _whisper_compute_type_candidates(device)
        for candidate_idx, compute_type in enumerate(compute_candidates):
            _ensure_not_cancelled(job_id)
            stage_msg = (
                action_label
                if idx == 0
                else f"{action_label} fallback on {device.upper()}"
            )
            _update_job(
                job_id,
                stage=current_stage,
                message=f"{stage_msg} using {device.upper()} ({compute_type}) for {project_name}",
                progress=start_progress,
            )
            try:
                payload = _transcribe_with_faster_whisper(
                    audio_path,
                    model_name=whisper_model,
                    device=device,
                    compute_type=compute_type,
                    language=language,
                    initial_prompt=initial_prompt,
                )
                _update_job(job_id, progress=end_progress)
                return payload, device, compute_type
            except JobCancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if (
                    device == "cuda"
                    and _is_whisper_compute_type_error(exc)
                    and candidate_idx < len(compute_candidates) - 1
                ):
                    logger.warning(
                        "[FASTER WHISPER CUDA RETRY] job=%s compute_type=%s failed, trying next CUDA compute type: %s",
                        job_id,
                        compute_type,
                        exc,
                    )
                    continue
                logger.warning(
                    "[FASTER WHISPER FAIL] job=%s device=%s compute_type=%s error=%s",
                    job_id,
                    device,
                    compute_type,
                    exc,
                )
                break

    if last_error:
        raise last_error
    raise RuntimeError(f"Faster-Whisper failed for {project_name}")


def _build_lrc_from_transcript_payload(payload: dict) -> str:
    lines = []
    for seg in payload.get("segments") or []:
        text = str(seg.get("text") or "").strip()
        start = seg.get("start")
        if not text or not isinstance(start, (int, float)):
            continue
        lines.append(f"[{_format_lrc_timestamp(float(start))}]{text}")
    return "\n".join(lines)


def _build_residual_instrumental(
    original: Path, vocals: Path, dest: Path, quality: str = "2"
) -> bool:
    """Build the instrumental as (original - vocals) instead of using Demucs'
    own ``no_vocals`` stem.

    Demucs synthesises each stem independently, so ``vocals + no_vocals`` does
    not add back up to the input: roughly 10% of the mix energy is simply
    dropped, which is why the accompaniment sounds thin and quiet (measured
    -5 dB overall, with bass down 3.7 dB and mids down 11.8 dB).

    Subtracting only what the model is confident is vocal keeps every part of
    the original that was not identified as a voice, so the backing track keeps
    its full weight (measured -0.8 dB, bass fully intact) while removing the
    same amount of vocal.

    Phase-inverting one input can push peaks above full scale, so the sum is
    computed in float and passed through a limiter before encoding.
    """
    if not (original.exists() and vocals.exists()):
        return False
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(original),
        "-i",
        str(vocals),
        "-filter_complex",
        # volume=-1 phase-inverts the vocal stem; amix with normalize=0 keeps
        # unity gain so the result is a true sample-accurate subtraction.
        "[1:a]volume=-1[vi];"
        "[0:a][vi]amix=inputs=2:normalize=0:dropout_transition=0[sum];"
        "[sum]alimiter=limit=0.891:level=disabled[out]",
        "-map",
        "[out]",
        "-q:a",
        quality,
        str(dest),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.warning(
            "[STEMS] residual instrumental failed for %s: %s",
            original.name,
            (res.stderr or "")[-300:],
        )
        return False
    return dest.exists() and dest.stat().st_size > 0


def _ensure_residual_instrumental(
    project_dir: Path, audio_path: Path, base_name: str, job_id: str = ""
) -> None:
    """Upgrade a project's instrumental to the residual mix if it predates it.

    Projects separated before the residual change carry a thin ``_minus.mp3``
    built straight from Demucs' ``no_vocals`` stem. A marker file records the
    upgrade so the rebuild only happens once per project.
    """
    minus_track = project_dir / f"{base_name}_minus.mp3"
    marker = project_dir / f".{base_name}_minus.residual"
    if marker.exists():
        return
    stem_dir = _find_project_stems(project_dir, base_name)
    vocals_wav = stem_dir / "vocals.wav" if stem_dir else None
    if not (vocals_wav and vocals_wav.exists() and audio_path.exists()):
        return
    rebuilt = project_dir / f"{base_name}_minus.residual.mp3"
    if job_id:
        _update_job(
            job_id,
            stage="audio prep",
            message=f"Rebuilding instrumental audio track first for {project_dir.name}",
        )
    try:
        if _build_residual_instrumental(audio_path, vocals_wav, rebuilt):
            rebuilt.replace(minus_track)
            marker.write_text("residual", encoding="utf-8")
            # The chorus mix is derived from the instrumental, so drop the stale
            # one; the render path rebuilds it on demand.
            chorus_track = project_dir / f"{base_name}_minus_chorus.mp3"
            if chorus_track.exists():
                chorus_track.unlink()
            logger.info(
                "[STEMS] upgraded instrumental to residual mix for %s",
                project_dir.name,
            )
        elif rebuilt.exists():
            rebuilt.unlink()
    except Exception as exc:
        logger.warning(
            "[STEMS] residual upgrade failed for %s: %s", project_dir.name, exc
        )
        if rebuilt.exists():
            try:
                rebuilt.unlink()
            except Exception:
                pass


def _run_demucs_with_progress(
    job_id: str,
    audio_path: Path,
    stems_out: Path,
    requested_device: str,
    start_progress: int,
    end_progress: int,
) -> str:
    primary_device = _effective_ai_device(requested_device)
    attempts = [primary_device]
    if primary_device == "cuda":
        attempts.append("cpu")

    last_error = ""
    for idx, device in enumerate(dict.fromkeys(attempts)):
        prefix = "Separating stems"
        if idx > 0:
            prefix = f"CUDA failed, retrying separation on {device.upper()}"
        _update_job(
            job_id,
            status="running",
            stage="stem separation",
            message=f"{prefix} for {audio_path.name}",
            progress=start_progress,
        )
        _ensure_not_cancelled(job_id)

        cmd = [
            sys.executable,
            "-m",
            "demucs",
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs",
            "-d",
            device,
            "--out",
            str(stems_out),
            str(audio_path),
        ]
        logger.info(
            "[DEMUCS START] job=%s device=%s cmd=%s", job_id, device, _cmd_text(cmd)
        )
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        with JOB_LOCK:
            RUNNING_PROCESSES[job_id] = proc

        try:
            output_buf = ""
            all_output = []
            while True:
                if _check_cancel_requested(job_id):
                    _terminate_running_process(job_id)
                    raise JobCancelledError("Job cancelled")
                chunk = proc.stdout.read(256) if proc.stdout else ""
                if not chunk and proc.poll() is not None:
                    break
                if not chunk:
                    continue
                all_output.append(chunk)
                output_buf += chunk
                parts = re.split(r"[\r\n]", output_buf)
                output_buf = parts[-1]
                for part in parts[:-1]:
                    match = re.search(r"(\d{1,3})%\|", part)
                    if match:
                        pct = max(0, min(100, int(match.group(1))))
                        progress = start_progress + int(
                            (end_progress - start_progress) * (pct / 100.0)
                        )
                        _update_job(
                            job_id,
                            message=f"Separating stems for {audio_path.name} ({pct}%)",
                            progress=progress,
                        )
            returncode = proc.wait()
            if returncode == 0:
                logger.info("[DEMUCS OK] job=%s device=%s", job_id, device)
                _update_job(job_id, progress=end_progress)
                return device
            collected = "".join(all_output)
            last_error = (collected or f"demucs failed with code {returncode}")[-2000:]
            logger.warning(
                "[DEMUCS FAIL] job=%s device=%s code=%s", job_id, device, returncode
            )
        finally:
            with JOB_LOCK:
                RUNNING_PROCESSES.pop(job_id, None)

    raise RuntimeError(last_error or "Demucs separation failed")


def _enhanced_lrc_to_word_lines(text: str) -> str:
    """Convert A2 enhanced LRC (<mm:ss.xx> word tags) to one-word-per-line [mm:ss.xx] LRC.

    Returns "" when the input has no word-level tags, so callers can fall back to
    standard line-level fetching.
    """
    word_re = re.compile(r"<(\d{1,3}):(\d{1,2}(?:\.\d{1,3})?)>\s*([^<\[\n]+)")
    out: list[str] = []
    for raw in str(text or "").splitlines():
        for match in word_re.finditer(raw):
            stamp = (int(match.group(1)) * 60) + float(match.group(2))
            word = match.group(3).strip()
            if word:
                out.append(f"[{_format_lrc_timestamp(stamp)}]{word}")
    return "\n".join(out)


def _fetch_synced_lyrics_enhanced(
    term: str, out_path: Path, timeout: int | None = None
) -> bool:
    """Try to fetch word/syllable-level (enhanced) synced lyrics and store them as word-per-line LRC."""
    tmp_path = out_path.with_name(out_path.name + ".enh")
    run_kwargs: dict = {"capture_output": True, "text": True}
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    try:
        res = subprocess.run(
            ["syncedlyrics", term, "-o", str(tmp_path), "--enhanced"], **run_kwargs
        )
        if res.returncode == 0 and tmp_path.exists():
            raw = tmp_path.read_text(encoding="utf-8", errors="ignore")
            converted = _enhanced_lrc_to_word_lines(raw)
            if converted.strip():
                out_path.write_text(converted + "\n", encoding="utf-8")
                logger.info(
                    "[LYRICS SYNCED] enhanced word-level lyrics captured for %r", term
                )
                return True
    except Exception as exc:
        logger.warning("[LYRICS SYNCED] enhanced fetch failed for %r: %s", term, exc)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return False


def _run_syncedlyrics(term: str, out_path: Path, timeout: int | None = None) -> bool:
    """Fetch synced lyrics, preferring word/syllable-level timing, falling back to line-level."""
    if _fetch_synced_lyrics_enhanced(term, out_path, timeout=timeout):
        return True
    run_kwargs: dict = {"capture_output": True, "text": True}
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    res = subprocess.run(["syncedlyrics", term, "-o", str(out_path)], **run_kwargs)
    return res.returncode == 0 and _has_timed_lyrics(out_path)


# ===============================================================
# SECTION: Audio Processing Pipeline (Demucs, Transcription, Lyrics)
# Purpose: Split audio into stems (vocals/instrumental), transcribe
#          to lyrics, fetch synced lyrics, and correct word timing
# ===============================================================


def _run_audio_pipeline_job(
    job_id: str,
    audio_filename: str,
    *,
    start_progress: int = 5,
    end_progress: int = 95,
    stem_device: str = DEFAULT_STEM_DEVICE,
    whisper_device: str = DEFAULT_WHISPER_DEVICE,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE,
    lyrics_query: str = "",
    display_title: str = "",
    project_name: str = "",
) -> None:
    audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    rel_audio = _relative_to_output(audio_path)
    _update_job(job_id, audio_filename=rel_audio, project_name=audio_path.parent.name)
    identity = _derive_media_identity(
        path=audio_path, raw_name=display_title or audio_path.stem
    )
    lyrics_search = lyrics_query or identity["display"] or audio_path.stem
    logger.info("[PIPELINE START] job=%s audio=%s", job_id, audio_path.name)
    ACTIVE_PROCESSING_CACHE.add(rel_audio)
    try:
        project_dir = audio_path.parent
        stems_out = project_dir / "stems"
        stem_ai_device = _effective_ai_device(stem_device)
        whisper_ai_device = _effective_ai_device(whisper_device)
        _update_job(
            job_id, stem_device=stem_ai_device, whisper_device=whisper_ai_device
        )
        demucs_end = start_progress + int((end_progress - start_progress) * 0.45)
        stem_ai_device = _run_demucs_with_progress(
            job_id,
            audio_path,
            stems_out,
            stem_ai_device,
            start_progress,
            demucs_end,
        )
        _update_job(job_id, stem_device=stem_ai_device)
        _update_job(
            job_id,
            details=f"Stem device: {stem_ai_device}; Whisper device: {whisper_ai_device}; Model: {whisper_model}; Language: {transcription_language}",
        )

        stem_dir = _find_project_stems(project_dir, audio_path.stem)
        no_vocals_track = stem_dir / "no_vocals.wav" if stem_dir else None
        vocals_wav_track = stem_dir / "vocals.wav" if stem_dir else None
        minus_track = project_dir / f"{audio_path.stem}_minus.mp3"
        vocals_track_mp3 = project_dir / f"{audio_path.stem}_vocals.mp3"
        chorus_track = project_dir / f"{audio_path.stem}_minus_chorus.mp3"
        if no_vocals_track and no_vocals_track.exists():
            _update_job(
                job_id,
                stage="package stems",
                message=f"Packaging vocal/instrumental stems for {project_name}",
                progress=demucs_end,
            )
            logger.info(
                "[PIPELINE] job=%s packaging accompaniment=%s", job_id, no_vocals_track
            )
            # Prefer the residual (original - vocals) instrumental: it keeps the
            # full weight of the backing track. Fall back to Demucs' no_vocals
            # stem if the subtraction could not be performed.
            packaged = False
            if vocals_wav_track and vocals_wav_track.exists():
                packaged = _build_residual_instrumental(
                    audio_path, vocals_wav_track, minus_track
                )
                if packaged:
                    logger.info(
                        "[PIPELINE] job=%s instrumental built by vocal subtraction",
                        job_id,
                    )
            if packaged:
                ffmpeg_res = None
            else:
                ffmpeg_res = subprocess.run(
                    [
                        FFMPEG_BIN,
                        "-y",
                        "-i",
                        str(no_vocals_track),
                        "-q:a",
                        "2",
                        str(minus_track),
                    ],
                    capture_output=True,
                    text=True,
                )
            if ffmpeg_res is not None and ffmpeg_res.returncode != 0:
                logger.warning(
                    "[PIPELINE] job=%s accompaniment packaging failed: %s",
                    job_id,
                    (ffmpeg_res.stderr or "")[-300:],
                )
                _update_job(
                    job_id,
                    message=f"Separation complete, accompaniment packaging failed for {project_name}",
                )
        if vocals_wav_track and vocals_wav_track.exists():
            vocals_res = subprocess.run(
                [
                    FFMPEG_BIN,
                    "-y",
                    "-i",
                    str(vocals_wav_track),
                    "-q:a",
                    "2",
                    str(vocals_track_mp3),
                ],
                capture_output=True,
                text=True,
            )
            if vocals_res.returncode != 0:
                logger.warning(
                    "[PIPELINE] job=%s vocals-only packaging failed: %s",
                    job_id,
                    (vocals_res.stderr or "")[-300:],
                )

        vocals_track = (
            stem_dir / "vocals.wav" if stem_dir else project_dir / "vocals.wav"
        )
        lyrics_end = start_progress + int((end_progress - start_progress) * 0.65)
        lrc_file = (
            _find_project_asset(project_dir, ".lrc", audio_path.stem)
            or project_dir / f"{audio_path.stem}.lrc"
        )

        _update_job(
            job_id,
            stage="lyrics fetch",
            message=f"Fetching synced lyrics for {project_name}",
            progress=demucs_end,
        )
        _ensure_not_cancelled(job_id)
        synced_ok = _run_syncedlyrics(lyrics_search, lrc_file)
        if synced_ok and lrc_file.exists() and lrc_file.stat().st_size >= 10:
            logger.info(
                "[PIPELINE] job=%s syncedlyrics hit for %s", job_id, audio_path.name
            )
            if no_vocals_track and no_vocals_track.exists():
                _update_job(
                    job_id,
                    stage="package chorus stem",
                    message=f"Building chorus-aware stem for {project_name}",
                    progress=max(0, end_progress - 2),
                )
                _build_chorus_aware_track(
                    job_id, audio_path, no_vocals_track, lrc_file, chorus_track
                )
            _update_job(
                job_id,
                progress=end_progress,
                message=f"Timed lyrics ready for {project_name}",
            )
            return

        whisper_end = end_progress
        language = _normalize_language(transcription_language)
        transcript_payload, whisper_ai_device, _ = _run_faster_whisper_with_fallback(
            job_id,
            vocals_track,
            whisper_model=whisper_model,
            whisper_device=whisper_ai_device,
            language=language,
            start_progress=lyrics_end,
            end_progress=whisper_end,
            stage="transcription",
            fallback_stage="transcription fallback",
            action_label="Running Faster-Whisper transcription",
            initial_prompt=lyrics_search,
        )
        lrc_content = _build_lrc_from_transcript_payload(transcript_payload)
        if not lrc_content.strip():
            raise RuntimeError(
                f"Faster-Whisper produced no timed transcription for {project_name}"
            )
        (project_dir / "vocals.json").write_text(
            json.dumps(transcript_payload, ensure_ascii=False), encoding="utf-8"
        )
        lrc_file.write_text(lrc_content, encoding="utf-8")
        if no_vocals_track and no_vocals_track.exists():
            _update_job(
                job_id,
                stage="package chorus stem",
                message=f"Building chorus-aware stem for {project_name}",
                progress=max(0, end_progress - 2),
            )
            _build_chorus_aware_track(
                job_id, audio_path, no_vocals_track, lrc_file, chorus_track
            )
        _update_job(
            job_id,
            details=(
                f"Stem device: {stem_ai_device}; Whisper device: {whisper_ai_device}; "
                f"Model: {whisper_model}; Language: {transcription_language}"
            ),
        )

        logger.info("[PIPELINE END] job=%s audio=%s", job_id, audio_path.name)
    finally:
        ACTIVE_PROCESSING_CACHE.discard(rel_audio)


def _download_with_ytdlp(job_id: str, url: str) -> Path:
    temp_stem = f"download_{job_id}"
    out_template = str(OUTPUT_DIR / f"{temp_stem}_%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=android",
        "--js-runtimes",
        "deno,node",
        "--no-mtime",
        "--prefer-free-formats",
        "-x",
        "--audio-format",
        "mp3",
        "-o",
        out_template,
        url,
    ]
    _run_cancellable_command(job_id, cmd, "Downloading source from URL", 5, 35)
    produced = sorted(
        OUTPUT_DIR.glob(f"{temp_stem}_*.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not produced:
        raise RuntimeError("yt-dlp completed but no output file was found")
    return produced[0]


def _download_with_metube(
    job_id: str,
    url: str,
    *,
    start_progress: int = 20,
    end_progress: int = 35,
    profile: str = "",
) -> tuple[Path, dict]:
    known_files = {path.name for path in OUTPUT_DIR.iterdir() if _is_source_media(path)}
    expected = _probe_url_identity(url)
    _update_job(
        job_id,
        status="running",
        progress=start_progress,
        message="Forwarding URL to MeTube",
    )
    _ensure_not_cancelled(job_id)
    target = f"{METUBE_URL.rstrip('/')}/add"
    resp = requests.post(target, json={"url": url}, timeout=8)
    if resp.status_code >= 400:
        raise RuntimeError(f"MeTube request failed with status {resp.status_code}")
    downloaded = _wait_for_new_media_file(job_id, known_files)
    identity = _choose_best_identity(downloaded, expected)
    downloaded, identity = _apply_canonical_media_name(
        downloaded,
        raw_name=identity["display"],
        artist=identity["artist"],
        title=identity["title"],
    )
    downloaded = _move_audio_into_project(
        downloaded, identity["safe_stem"], profile
    )
    rel_audio = _relative_to_output(downloaded)
    _update_job(
        job_id,
        audio_filename=rel_audio,
        project_name=downloaded.parent.name,
        progress=end_progress,
        message=f"MeTube downloaded {downloaded.name}",
    )
    return downloaded, identity


def _run_url_job(
    job_id: str,
    url: str,
    engine: str,
    stem_device: str = DEFAULT_STEM_DEVICE,
    whisper_device: str = DEFAULT_WHISPER_DEVICE,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE,
    profile: str = "",
) -> None:
    engine_norm = (engine or "ytdl").strip().lower()
    logger.info("[URL START] job=%s engine=%s url=%s", job_id, engine_norm, url)
    if engine_norm == "metube":
        downloaded, identity = _download_with_metube(
            job_id, url, start_progress=20, end_progress=35, profile=profile
        )
        rel_audio = _relative_to_output(downloaded)
        _run_audio_pipeline_job(
            job_id,
            rel_audio,
            start_progress=35,
            end_progress=95,
            stem_device=stem_device,
            whisper_device=whisper_device,
            whisper_model=whisper_model,
            transcription_language=transcription_language,
            lyrics_query=identity["display"],
            display_title=identity["display"],
        )
        logger.info("[URL END] job=%s metube_downloaded=%s", job_id, downloaded.name)
        return

    expected = _probe_url_identity(url)
    try:
        downloaded = _download_with_ytdlp(job_id, url)
        identity = _choose_best_identity(downloaded, expected)
        downloaded, identity = _apply_canonical_media_name(
            downloaded,
            raw_name=identity["display"],
            artist=identity["artist"],
            title=identity["title"],
        )
        downloaded = _move_audio_into_project(
            downloaded, identity["safe_stem"], profile
        )
        rel_audio = _relative_to_output(downloaded)
        _update_job(
            job_id,
            audio_filename=rel_audio,
            project_name=downloaded.parent.name,
            message=f"Downloaded {downloaded.name}",
            progress=35,
        )
    except Exception as exc:
        err_text = str(exc)
        needs_fallback = (
            "No supported JavaScript runtime" in err_text
            or "HTTP Error 403" in err_text
            or "unable to download video data" in err_text
        )
        if not needs_fallback:
            raise
        logger.warning(
            "[URL FALLBACK] job=%s ytdlp failed, retrying via MeTube: %s",
            job_id,
            err_text,
        )
        _update_job(
            job_id,
            message="yt-dlp failed (YouTube anti-bot/JS). Retrying via MeTube...",
            progress=15,
        )
        downloaded, identity = _download_with_metube(
            job_id, url, start_progress=20, end_progress=35, profile=profile
        )
        rel_audio = _relative_to_output(downloaded)

    _run_audio_pipeline_job(
        job_id,
        rel_audio,
        start_progress=35,
        end_progress=95,
        stem_device=stem_device,
        whisper_device=whisper_device,
        whisper_model=whisper_model,
        transcription_language=transcription_language,
        lyrics_query=identity["display"],
        display_title=identity["display"],
    )
    logger.info("[URL END] job=%s downloaded=%s", job_id, downloaded.name)


def _run_lyrics_fetch_job(
    job_id: str,
    audio_filename: str,
    *,
    lyrics_query: str = "",
    display_title: str = "",
    provider: str = "syncedlyrics",
    project_name: str = "",
    force: bool = False,
    suno_url: str = "",
) -> None:
    audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    rel_audio = _relative_to_output(audio_path)
    _update_job(job_id, audio_filename=rel_audio, project_name=audio_path.parent.name)

    project_dir = audio_path.parent
    lrc_file = (
        _find_project_asset(project_dir, ".lrc", audio_path.stem)
        or project_dir / f"{audio_path.stem}.lrc"
    )
    # Only skip when NOT an explicit user pull; a manual "Pull Lyrics" always re-fetches.
    if not force and _has_timed_lyrics(lrc_file):
        _update_job(
            job_id,
            progress=100,
            message=f"Lyrics already exist for {project_name}",
            status="completed",
        )
        return

    selected_provider = str(provider or "syncedlyrics").strip().lower()
    if selected_provider not in {"auto", "syncedlyrics", "lrclib", "genius", "suno"}:
        selected_provider = "auto"

    _update_job(
        job_id,
        status="running",
        stage="lyrics fetch",
        message=f"Fetching lyrics via {selected_provider} for {project_name}",
        progress=20,
    )

    _ensure_not_cancelled(job_id)
    if selected_provider == "auto":
        identity = _derive_media_identity(
            path=audio_path, raw_name=display_title or audio_path.stem
        )
        content, winning_provider = _fetch_best_lyrics(identity)
        lrc_file.write_text(content.strip() + "\n", encoding="utf-8")
        timing_level, timestamp_count = _detect_timing_level(content)
        timing_names = {
            3: "syllable-level",
            2: "word-level",
            1: "line-level",
            0: "no-timing",
        }
        timing_desc = timing_names.get(timing_level, "unknown")
        selected_provider = (
            f"auto ({winning_provider}) - {timing_desc} ({timestamp_count} timestamps)"
        )
    elif selected_provider == "suno":
        content = _fetch_lyrics_from_suno(suno_url)
        if not str(content or "").strip():
            raise RuntimeError(f"Suno returned no lyrics for {project_name}")
        lrc_file.write_text(content.strip() + "\n", encoding="utf-8")
        timing_level, timestamp_count = _detect_timing_level(content)
        timing_names = {
            3: "syllable-level",
            2: "word-level",
            1: "line-level",
            0: "no-timing",
        }
        timing_desc = timing_names.get(timing_level, "unknown")
        selected_provider = f"suno - {timing_desc} ({timestamp_count} timestamps)"
    elif selected_provider == "syncedlyrics":
        identity = _derive_media_identity(
            path=audio_path, raw_name=display_title or audio_path.stem
        )
        lookup = lyrics_query or identity["display"] or audio_path.stem
        if not _run_syncedlyrics(lookup, lrc_file) or not _has_timed_lyrics(lrc_file):
            raise RuntimeError(f"Lyrics fetch failed for {project_name}")
        content = lrc_file.read_text(encoding="utf-8")
        timing_level, timestamp_count = _detect_timing_level(content)
        timing_names = {
            3: "syllable-level",
            2: "word-level",
            1: "line-level",
            0: "no-timing",
        }
        timing_desc = timing_names.get(timing_level, "unknown")
        selected_provider = (
            f"syncedlyrics - {timing_desc} ({timestamp_count} timestamps)"
        )
    else:
        _fetch_lyrics_for_media_with_provider(audio_path, selected_provider)
        if not _has_timed_lyrics(lrc_file):
            raise RuntimeError(
                f"{selected_provider} did not produce timed lyrics for {project_name}"
            )
        content = lrc_file.read_text(encoding="utf-8")
        timing_level, timestamp_count = _detect_timing_level(content)
        timing_names = {
            3: "syllable-level",
            2: "word-level",
            1: "line-level",
            0: "no-timing",
        }
        timing_desc = timing_names.get(timing_level, "unknown")
        selected_provider = (
            f"{selected_provider} - {timing_desc} ({timestamp_count} timestamps)"
        )

    _ensure_not_cancelled(job_id)
    # Write metadata file to track which provider was used
    meta_file = lrc_file.with_suffix(".lrc.meta")
    try:
        winning_provider_name = (
            selected_provider.split(" ")[0]
            if " " in selected_provider
            else selected_provider
        )
        meta_file.write_text(
            json.dumps({"provider": winning_provider_name, "fetched_at": time.time()}),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Could not write metadata file: {e}")

    _update_job(
        job_id,
        progress=100,
        message=f"Lyrics ready via {selected_provider} for {project_name}",
        status="completed",
    )


def _run_word_timing_correction_job(
    job_id: str,
    audio_filename: str,
    *,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    whisper_device: str = DEFAULT_WHISPER_DEVICE,
    timing_mode: str = "major",
    max_offset_seconds: float = 2.0,
    project_name: str = "",
) -> None:
    audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    rel_audio = _relative_to_output(audio_path)
    _update_job(job_id, audio_filename=rel_audio, project_name=audio_path.parent.name)

    project_dir = audio_path.parent
    lrc_file = _find_project_asset(project_dir, ".lrc", audio_path.stem)
    stem_dir = _find_project_stems(project_dir, audio_path.stem)
    vocals_track = stem_dir / "vocals.wav" if stem_dir else None

    if not lrc_file:
        raise RuntimeError("Missing lyrics file for correction")
    if not vocals_track or not vocals_track.exists():
        raise RuntimeError("Missing vocals stem. Run stem separation first.")

    json_path = project_dir / "vocals.json"
    language = "auto"
    aligned_payload, _, _ = _run_faster_whisper_with_fallback(
        job_id,
        vocals_track,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
        language=language,
        start_progress=20,
        end_progress=75,
        stage="ai word align",
        fallback_stage="ai word align fallback",
        action_label="Aligning words with Faster-Whisper",
        initial_prompt=lrc_file.read_text(encoding="utf-8", errors="ignore"),
    )
    json_path.write_text(
        json.dumps(aligned_payload, ensure_ascii=False), encoding="utf-8"
    )

    _update_job(
        job_id,
        stage="apply word timing",
        message=f"Applying AI word timing to {lrc_file.name}",
        progress=80,
    )
    source_lrc = lrc_file.read_text(encoding="utf-8", errors="ignore")
    if timing_mode == "custom":
        corrected_lrc = _minor_adjust_lrc_timing(
            source_lrc, aligned_payload, max_shift=max_offset_seconds
        )
    elif timing_mode == "minor":
        corrected_lrc = _minor_adjust_lrc_timing(source_lrc, aligned_payload)
    else:
        corrected_lrc = _rebuild_word_timed_lrc_from_alignment(
            source_lrc, aligned_payload
        )
    lrc_file.write_text(corrected_lrc, encoding="utf-8")

    mode_label = {
        "minor": "Minor",
        "custom": f"Custom (\u00b1{max_offset_seconds:g}s)",
        "safe-word": "Safe word-level",
    }.get(timing_mode, "Major")
    _update_job(
        job_id,
        progress=100,
        message=f"{mode_label} AI timing correction complete for {project_name}",
    )


# ===============================================================
# SECTION: Lyrics Fetching & Processing (Multiple Sources)
# Purpose: Fetch lyrics from LRCLib, Genius, SyncedLyrics APIs
#          with ranking, normalization, and LRC format conversion
# ===============================================================


def _fetch_lyrics_for_media(audio_path: Path) -> str:
    audio_path = _ensure_project_layout_for_audio(audio_path)
    project_dir = audio_path.parent
    lrc_file = (
        _find_project_asset(project_dir, ".lrc", audio_path.stem)
        or project_dir / f"{audio_path.stem}.lrc"
    )
    identity = _derive_media_identity(path=audio_path, raw_name=audio_path.stem)
    if (
        not _run_syncedlyrics(identity["display"], lrc_file)
        or not lrc_file.exists()
        or lrc_file.stat().st_size < 10
    ):
        raise RuntimeError("Lyrics lookup failed")
    return lrc_file.read_text(encoding="utf-8")


def _plain_text_to_lrc(text: str) -> str:
    """
    Convert plain text lyrics to LRC format with line-level timing.
    Used as fallback when providers return unsynced lyrics.
    """
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    out = []
    cursor = 0.0
    for line in lines:
        out.append(f"[{_format_lrc_timestamp(cursor)}]{line}")
        cursor += 3.5  # 3.5 seconds per line default
    return "\n".join(out)


def _lyric_tokens(value: str) -> set[str]:
    return set(re.findall(r"\w+", str(value or "").lower()))


def _lyrics_result_relevance(identity: dict, entry: dict) -> float:
    wanted_title = _lyric_tokens(identity.get("title") or identity.get("display"))
    wanted_artist = _lyric_tokens(identity.get("artist"))
    result_title = _lyric_tokens(entry.get("trackName"))
    result_artist = _lyric_tokens(entry.get("artistName"))
    if not wanted_title or not result_title:
        return 0.0
    title_score = len(wanted_title & result_title) / len(wanted_title)
    artist_score = 0.0
    if wanted_artist and result_artist:
        artist_score = len(wanted_artist & result_artist) / len(wanted_artist)
    return (title_score * 0.7) + (artist_score * 0.3)


def _fetch_lyrics_from_lrclib(identity: dict) -> str:
    artist = str(identity.get("artist") or "").strip()
    title = str(identity.get("title") or "").strip()
    if not title:
        title = str(identity.get("display") or "").strip()

    candidates = []
    if artist and title:
        candidates.append({"track_name": title, "artist_name": artist})
    if title:
        candidates.append({"q": title})
    if identity.get("display"):
        candidates.append({"q": str(identity.get("display"))})

    headers = {"User-Agent": "OnePageKaraoke/1.0"}
    for params in candidates:
        try:
            res = requests.get(
                "https://lrclib.net/api/search",
                params=params,
                headers=headers,
                timeout=12,
            )
            if res.status_code >= 400:
                continue
            payload = res.json()
            if not isinstance(payload, list) or not payload:
                continue
            entries = [entry for entry in payload if isinstance(entry, dict)]
            ranked = sorted(
                entries,
                key=lambda entry: (
                    _lyrics_result_relevance(identity, entry),
                    bool(str(entry.get("syncedLyrics") or "").strip()),
                ),
                reverse=True,
            )
            for entry in ranked:
                relevance = _lyrics_result_relevance(identity, entry)
                if relevance < 0.6:
                    continue
                synced = str(entry.get("syncedLyrics") or "").strip()
                plain = str(entry.get("plainLyrics") or "").strip()
                if synced:
                    return synced
                if plain:
                    return _plain_text_to_lrc(plain)
        except Exception:
            continue
    raise RuntimeError("lrclib did not return lyrics for this track")


def _extract_genius_path(search_payload: dict) -> str:
    response = (
        search_payload.get("response") if isinstance(search_payload, dict) else {}
    )
    sections = response.get("sections") if isinstance(response, dict) else []
    if not isinstance(sections, list):
        return ""
    for section in sections:
        if not isinstance(section, dict):
            continue
        if str(section.get("type") or "").lower() != "song":
            continue
        hits = section.get("hits") or []
        if not isinstance(hits, list):
            continue
        for hit in hits:
            result = hit.get("result") if isinstance(hit, dict) else {}
            path = str((result or {}).get("path") or "").strip()
            if path:
                return path
    return ""


def _strip_html_to_text(raw_html: str) -> str:
    text = raw_html
    text = re.sub(r"<br\\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _fetch_lyrics_from_genius(identity: dict) -> str:
    query = str(identity.get("display") or identity.get("title") or "").strip()
    if not query:
        raise RuntimeError("Missing title for Genius lookup")

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) OnePageKaraoke/1.0"}
    search_res = requests.get(
        "https://genius.com/api/search/multi",
        params={"q": query},
        headers=headers,
        timeout=12,
    )
    if search_res.status_code >= 400:
        raise RuntimeError(f"Genius search failed with status {search_res.status_code}")
    path = _extract_genius_path(search_res.json())
    if not path:
        raise RuntimeError("Genius returned no matching song path")

    page_res = requests.get(f"https://genius.com{path}", headers=headers, timeout=12)
    if page_res.status_code >= 400:
        raise RuntimeError(
            f"Genius lyrics page failed with status {page_res.status_code}"
        )
    page = page_res.text or ""

    blocks = re.findall(
        r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocks:
        raise RuntimeError("Could not extract lyrics from Genius page")
    merged = "\n".join(_strip_html_to_text(block) for block in blocks if block.strip())
    merged = merged.strip()
    if not merged:
        raise RuntimeError("Genius returned an empty lyrics payload")
    return _plain_text_to_lrc(merged)


def _fetch_lyrics_from_syncedlyrics(identity: dict) -> str:
    lookup = str(identity.get("display") or identity.get("title") or "").strip()
    if not lookup:
        raise RuntimeError("Missing title for syncedlyrics lookup")

    output_file = tempfile.NamedTemporaryFile(
        prefix="onepage-lyrics-", suffix=".lrc", delete=False
    )
    output_path = Path(output_file.name)
    output_file.close()
    try:
        ok = _run_syncedlyrics(lookup, output_path, timeout=20)
        content = (
            output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        )
        if not ok or not content.strip():
            raise RuntimeError("syncedlyrics returned no lyrics")
        return content
    finally:
        output_path.unlink(missing_ok=True)


# Suno structural markers such as [Verse], [Chorus], [Intro], [Bridge] are not
# sung words, so they are dropped before the lyrics become karaoke lines.
_SUNO_SECTION_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _extract_suno_song_id(raw: str) -> str:
    """Pull a Suno clip UUID out of a full URL, share link, or bare id."""
    match = _UUID_RE.search(str(raw or ""))
    return match.group(0) if match else ""


def _clean_suno_lyrics(text: str) -> str:
    """Strip Suno section tags while keeping stanza breaks and sung lines."""
    out: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            out.append("")
            continue
        if _SUNO_SECTION_RE.match(line):
            continue
        out.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return cleaned


def _decode_json_string_body(body: str) -> str:
    """Decode a JSON-escaped string body captured from embedded page JSON."""
    try:
        return json.loads('"' + body + '"')
    except Exception:
        return (
            body.replace("\\r", "")
            .replace("\\n", "\n")
            .replace('\\"', '"')
            .replace("\\/", "/")
            .replace("\\\\", "\\")
        )


def _fetch_lyrics_from_suno(url_or_id: str) -> str:
    """
    Pull the exact lyrics a Suno song was generated from.

    Accepts a full song URL (https://suno.com/song/<id>), a share link
    (https://suno.com/s/<code>), or a bare clip id. Suno lyrics are unsynced,
    so they are returned as line-level LRC (placeholder timing) that the user
    can refine with AI word-timing correction.
    """
    raw = str(url_or_id or "").strip()
    if not raw:
        raise RuntimeError("Provide a Suno song link or ID to pull lyrics from Suno.")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OnePageKaraoke/1.0"
        ),
        "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    }

    song_id = _extract_suno_song_id(raw)
    page_html = ""

    # Share links (/s/<code>) redirect to the canonical /song/<uuid> page.
    if not song_id and raw.lower().startswith("http"):
        try:
            resolved = requests.get(
                raw, headers=headers, timeout=15, allow_redirects=True
            )
            song_id = _extract_suno_song_id(resolved.url)
            page_html = resolved.text or ""
        except Exception as exc:
            logger.warning("[SUNO] link resolve failed: %s", exc)

    lyrics = ""

    # Strategy 1: public clip/feed API endpoints return metadata.prompt (lyrics).
    if song_id:
        api_urls = [
            f"https://studio-api.prod.suno.com/api/clip/{song_id}",
            f"https://studio-api.suno.ai/api/clip/{song_id}",
            f"https://studio-api.prod.suno.com/api/feed/v2?ids={song_id}",
            f"https://studio-api.suno.ai/api/external/clips/?ids={song_id}",
        ]
        for api_url in api_urls:
            try:
                res = requests.get(api_url, headers=headers, timeout=15)
                if res.status_code >= 400:
                    continue
                data = res.json()
                clip = data
                if isinstance(data, dict) and isinstance(data.get("clips"), list):
                    clip = data["clips"][0] if data["clips"] else {}
                elif isinstance(data, list):
                    clip = data[0] if data else {}
                if isinstance(clip, dict):
                    meta = clip.get("metadata")
                    meta = meta if isinstance(meta, dict) else {}
                    lyrics = str(
                        meta.get("prompt")
                        or clip.get("prompt")
                        or meta.get("lyrics")
                        or clip.get("lyrics")
                        or ""
                    ).strip()
                if lyrics:
                    break
            except Exception as exc:
                logger.warning("[SUNO] api %s failed: %s", api_url, exc)
                continue

    # Strategy 2: scrape the public song page for the embedded lyrics JSON.
    if not lyrics:
        if not page_html and song_id:
            try:
                page_res = requests.get(
                    f"https://suno.com/song/{song_id}", headers=headers, timeout=15
                )
                page_html = page_res.text or ""
            except Exception as exc:
                logger.warning("[SUNO] page fetch failed: %s", exc)
        if page_html:
            for pattern in (
                r'\\"prompt\\":\\"(.*?)\\"',
                r'"prompt":"(.*?)"',
                r'\\"lyrics\\":\\"(.*?)\\"',
                r'"lyrics":"(.*?)"',
            ):
                match = re.search(pattern, page_html, flags=re.DOTALL)
                if not match:
                    continue
                candidate = _decode_json_string_body(match.group(1))
                # Double-escaped payloads (Next.js stream) need a second pass.
                if "\\n" in candidate or '\\"' in candidate:
                    candidate = _decode_json_string_body(candidate)
                if candidate.strip():
                    lyrics = candidate.strip()
                    break

    cleaned = _clean_suno_lyrics(lyrics)
    if not cleaned:
        raise RuntimeError(
            "Could not read lyrics from that Suno link. Make sure the song is "
            "public/shared and the link or ID is correct."
        )
    return _plain_text_to_lrc(cleaned)


def _detect_timing_level(content: str) -> tuple[int, int]:

    """
    Detect the timing level granularity of lyrics.
    Returns: (timing_level, timestamp_count)
    Levels:
    - 3: Syllable-level (4+ timestamps per line on average, 20+ total)
    - 2: Word-level (2-3 timestamps per line on average, 10+ total)
    - 1: Line-level (1 timestamp per line on average)
    - 0: No timing
    """
    lines = str(content or "").splitlines()
    if not lines:
        return 0, 0

    # Count timestamps per line
    timestamps_per_line = []
    total_timestamps = 0
    single_word_timed_lines = 0

    for line in lines:
        line_timestamps = len(re.findall(r"\[\d+:\d+(?:\.\d+)?\]", line))
        if line_timestamps > 0:
            timestamps_per_line.append(line_timestamps)
            total_timestamps += line_timestamps
            # A timed line carrying a single word is word/syllable-level timing.
            text_only = _WORD_END_TAG_RE.sub(
                "", re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", line)
            ).strip()
            if text_only and len(text_only.split()) == 1:
                single_word_timed_lines += 1

    if total_timestamps == 0:
        return 0, 0  # No timing

    avg_timestamps_per_line = (
        total_timestamps / len(timestamps_per_line) if timestamps_per_line else 0
    )

    # One-word-per-line LRC (enhanced or manual word timing) is genuinely word-level even
    # though each line only carries a single [mm:ss] tag.
    if (
        timestamps_per_line
        and (single_word_timed_lines / len(timestamps_per_line)) >= 0.7
    ):
        if total_timestamps >= 20:
            return 3, total_timestamps  # dense word/syllable-level
        if total_timestamps >= 8:
            return 2, total_timestamps  # word-level

    # Check for synthetic timing (uniform 1.5s deltas)
    all_timestamps = []
    for minutes, seconds in re.findall(
        r"\[(\d+):(\d+(?:\.\d+)?)\]", str(content or "")
    ):
        all_timestamps.append((int(minutes) * 60) + float(seconds))

    deltas = [
        later - earlier
        for earlier, later in zip(all_timestamps, all_timestamps[1:])
        if later > earlier
    ]
    is_synthetic = bool(deltas) and all(abs(delta - 1.5) < 0.03 for delta in deltas)

    # Detect timing level
    if avg_timestamps_per_line >= 4 and total_timestamps >= 20 and not is_synthetic:
        return 3, total_timestamps  # Syllable-level
    elif avg_timestamps_per_line >= 1.5 and total_timestamps >= 10:
        return 2, total_timestamps  # Word-level
    elif avg_timestamps_per_line >= 0.8 and not is_synthetic:
        return 1, total_timestamps  # Line-level
    else:
        return 0, total_timestamps  # Unknown or synthetic


def _lyrics_timing_quality(content: str) -> tuple[int, int, int]:
    """
    Rate lyrics quality based on timing level, timestamp count, and content length.
    Returns: (timing_level, timestamp_count, content_length)
    Used for sorting - higher values are better.
    """
    timing_level, total_timestamps = _detect_timing_level(content)
    content_length = len(str(content or ""))
    return timing_level, total_timestamps, content_length


def _fetch_best_lyrics(identity: dict) -> tuple[str, str]:
    """
    Fetch lyrics from multiple providers and select the best result.
    Priority: Syllable-level timing > Word-level > Line-level > No timing
    Falls back through providers if timing is not available.
    """
    fetchers = {
        "lrclib": lambda: _fetch_lyrics_from_lrclib(identity),
        "syncedlyrics": lambda: _fetch_lyrics_from_syncedlyrics(identity),
        "genius": lambda: _fetch_lyrics_from_genius(identity),
    }
    provider_weight = {"lrclib": 3, "syncedlyrics": 2, "genius": 1}
    results = []
    errors = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(fetchers))
    futures = {
        executor.submit(fetcher): provider for provider, fetcher in fetchers.items()
    }
    try:
        for future in concurrent.futures.as_completed(futures, timeout=25):
            provider = futures[future]
            try:
                content = str(future.result() or "").strip()
                if content:
                    results.append((content, provider))
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
    except concurrent.futures.TimeoutError:
        errors.append("provider search timed out")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    if not results:
        raise RuntimeError("No lyrics provider returned a result. " + "; ".join(errors))

    # Select best result prioritizing timing level, then provider weight
    def score_result(item):
        content, provider = item
        timing_level, timestamp_count, content_length = _lyrics_timing_quality(content)
        # Score: (timing_level, provider_weight, timestamp_count, content_length)
        # This ensures syllable > word > line > no-timing, with provider weight as tiebreaker
        return (
            timing_level,
            provider_weight.get(provider, 0),
            timestamp_count,
            content_length,
        )

    content, provider = max(results, key=score_result)
    timing_level, timestamp_count, _ = _lyrics_timing_quality(content)
    timing_names = {
        3: "syllable-level",
        2: "word-level",
        1: "line-level",
        0: "no-timing",
    }
    timing_desc = timing_names.get(timing_level, "unknown")
    logger.info(
        "[LYRICS AUTO] selected provider=%s timing=%s count=%d",
        provider,
        timing_desc,
        timestamp_count,
    )
    return content, provider


def _fetch_lyrics_for_media_with_provider(audio_path: Path, provider: str) -> str:
    """
    Fetch lyrics from a specific provider with fallback to auto-detect best timing level.
    If the provider doesn't return timing data, falls back to other providers.
    """
    audio_path = _ensure_project_layout_for_audio(audio_path)
    project_dir = audio_path.parent
    lrc_file = (
        _find_project_asset(project_dir, ".lrc", audio_path.stem)
        or project_dir / f"{audio_path.stem}.lrc"
    )
    identity = _derive_media_identity(path=audio_path, raw_name=audio_path.stem)

    selected = str(provider or "").strip().lower()
    if selected not in {"genius", "lrclib", "syncedlyrics"}:
        selected = "lrclib"

    # Try the selected provider first
    try:
        if selected == "syncedlyrics":
            content = _fetch_lyrics_for_media(audio_path)
        elif selected == "lrclib":
            content = _fetch_lyrics_from_lrclib(identity)
        else:
            content = _fetch_lyrics_from_genius(identity)

        if str(content or "").strip():
            timing_level, timestamp_count = _detect_timing_level(content)
            if timing_level > 0:  # Has timing data
                lrc_file.write_text(content.strip() + "\n", encoding="utf-8")
                return lrc_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[LYRICS PROVIDER] %s failed: %s, trying fallback", selected, e)

    # If selected provider failed or returned no timing, use auto-fetch to find best timing
    logger.info("[LYRICS PROVIDER] Falling back to auto-detect for better timing level")
    content, _ = _fetch_best_lyrics(identity)

    if not str(content or "").strip():
        raise RuntimeError("No lyrics with timing available from any provider")
    lrc_file.write_text(content.strip() + "\n", encoding="utf-8")
    return lrc_file.read_text(encoding="utf-8")


def _normalize_word_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _format_lrc_timestamp(seconds: float) -> str:
    safe = max(0.0, float(seconds or 0.0))
    mins = int(safe // 60)
    secs = safe - (mins * 60)
    return f"{mins:02d}:{secs:05.2f}"


def _extract_lrc_words(lrc_text: str) -> list[str]:
    words: list[str] = []
    tag_regex = re.compile(r"\[(\d+):(\d+)(?:\.(\d{1,3}))?\]")
    for raw_line in (lrc_text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        text = _WORD_END_TAG_RE.sub(" ", tag_regex.sub(" ", stripped)).strip()
        if not text:
            continue
        words.extend([part for part in re.split(r"\s+", text) if part])
    return words


CHORUS_MARK = "*"
# A manually-marked chorus word looks like: [00:12.34]*word<00:12.90>
# The marker sits between the start tag and the word so existing timestamp
# regexes (which only match the bracketed tags) are unaffected.
_CHORUS_MARK_RE = re.compile(r"(\[\d+:\d+(?:\.\d{1,3})?\])\s*\*")


def _parse_manual_chorus_ranges(lrc_path: Path | None) -> list[tuple[float, float]]:
    """Read user-marked chorus words from the LRC and return their time ranges.

    Each marked word contributes the span from its own start tag to either its
    explicit end tag (<mm:ss.xx>) or the next word's start. Adjacent marked
    words merge into continuous ranges so the vocal doesn't stutter on and off.
    """
    if not lrc_path or not lrc_path.exists():
        return []
    content = lrc_path.read_text(encoding="utf-8", errors="ignore")
    tag_regex = re.compile(r"\[(\d+):(\d+)(?:\.(\d{1,3}))?\]")
    end_regex = re.compile(r"<(\d+):(\d+)(?:\.(\d{1,3}))?>")

    def _to_sec(mm: str, ss: str, frac: str | None) -> float:
        frac_str = frac or "0"
        # 3 digits means milliseconds; fewer means hundredths.
        denom = 1000.0 if len(frac_str) == 3 else 100.0
        value = float(frac_str) if len(frac_str) == 3 else float(frac_str.ljust(2, "0"))
        return int(mm) * 60 + int(ss) + value / denom

    entries: list[tuple[float, float | None, bool]] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        start_match = tag_regex.search(stripped)
        if not start_match:
            continue
        start = _to_sec(*start_match.groups())
        marked = bool(_CHORUS_MARK_RE.search(stripped))
        end_match = end_regex.search(stripped)
        end = _to_sec(*end_match.groups()) if end_match else None
        entries.append((start, end, marked))

    if not any(marked for _, _, marked in entries):
        return []
    entries.sort(key=lambda item: item[0])

    ranges: list[tuple[float, float]] = []
    for idx, (start, end, marked) in enumerate(entries):
        if not marked:
            continue
        stop = end
        if stop is None or stop <= start:
            stop = entries[idx + 1][0] if idx + 1 < len(entries) else start + 4.0
        ranges.append((start, max(stop, start + 0.05)))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        # Bridge tiny gaps so consecutive marked words play as one phrase.
        if merged and start <= merged[-1][1] + 0.35:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _detect_lrc_chorus_ranges(
    lrc_path: Path | None, min_repeats: int = 2, min_block: int = 2
) -> list[tuple[float, float]]:
    """Find chorus sections by detecting repeated timed-lyric line blocks (no separate AI model needed)."""
    if not lrc_path or not lrc_path.exists():
        return []
    content = lrc_path.read_text(encoding="utf-8", errors="ignore")
    tag_regex = re.compile(r"\[(\d+):(\d+)(?:\.(\d{1,3}))?\]")
    parsed: list[tuple[float, str]] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = tag_regex.search(stripped)
        if not match:
            continue
        time_sec = (
            int(match.group(1)) * 60
            + int(match.group(2))
            + int((match.group(3) or "0").ljust(3, "0")[:3]) / 1000
        )
        text = _WORD_END_TAG_RE.sub("", tag_regex.sub("", stripped))
        # Drop duet-voice markers ({m}/{f}/{b}) so they don't pollute the
        # repeated-line comparison used to auto-detect chorus sections.
        text = re.sub(r"\{[mfb]\}", "", text).strip()
        if text:
            parsed.append((time_sec, text))

    if len(parsed) < min_block * min_repeats:
        return []
    parsed.sort(key=lambda item: item[0])
    normalized = [re.sub(r"[^\w\s]", "", text.lower()).strip() for _, text in parsed]

    chorus_line_idx: set[int] = set()
    used: set[int] = set()
    max_block = min(8, len(parsed) // 2)
    for block_size in range(max_block, min_block - 1, -1):
        for i in range(len(normalized) - block_size + 1):
            if any(j in used for j in range(i, i + block_size)):
                continue
            block = tuple(normalized[i : i + block_size])
            if not any(block):
                continue
            occurrences = [
                j
                for j in range(len(normalized) - block_size + 1)
                if tuple(normalized[j : j + block_size]) == block
            ]
            if len(occurrences) >= min_repeats:
                for occ in occurrences:
                    occ_range = range(occ, occ + block_size)
                    if any(j in used for j in occ_range):
                        continue
                    used.update(occ_range)
                    chorus_line_idx.update(occ_range)

    if not chorus_line_idx:
        return []

    ranges: list[tuple[float, float]] = []
    for idx in sorted(chorus_line_idx):
        start = parsed[idx][0]
        end = parsed[idx + 1][0] if idx + 1 < len(parsed) else start + 6.0
        ranges.append((start, end))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 0.5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _build_chorus_aware_track(
    job_id: str,
    audio_path: Path,
    no_vocals_path: Path,
    lrc_path: Path | None,
    output_path: Path,
) -> None:
    """Instrumental everywhere, but the original vocals play during chorus sections.

    User-marked words (see _parse_manual_chorus_ranges) take priority; when the
    user hasn't marked anything we fall back to repeated-block auto-detection.
    """
    if job_id:
        _update_job(
            job_id,
            stage="audio prep",
            message=f"Rendering chorus audio track first for {audio_path.parent.name}",
        )
    chorus_ranges = _parse_manual_chorus_ranges(lrc_path)
    if chorus_ranges:
        logger.info(
            "[CHORUS STEM] job=%s using %d manually marked range(s)",
            job_id,
            len(chorus_ranges),
        )
    else:
        chorus_ranges = _detect_lrc_chorus_ranges(lrc_path)
    if not chorus_ranges:
        res = subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                str(no_vocals_path),
                "-q:a",
                "2",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            logger.warning(
                "[CHORUS STEM] job=%s no-chorus fallback failed: %s",
                job_id,
                (res.stderr or "")[-300:],
            )
        return

    enable_expr = "+".join(
        f"between(t,{start:.3f},{end:.3f})" for start, end in chorus_ranges
    )
    filter_complex = (
        f"[0:a]volume=0:enable='not({enable_expr})'[full];"
        f"[1:a]volume=0:enable='{enable_expr}'[instr];"
        "[full][instr]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[out]"
    )
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i",
        str(audio_path),
        "-i",
        str(no_vocals_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-q:a",
        "2",
        str(output_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.warning(
            "[CHORUS STEM] job=%s build failed: %s", job_id, (res.stderr or "")[-500:]
        )


def _extract_alignment_words(json_payload: dict) -> list[dict]:
    out: list[dict] = []
    for seg in json_payload.get("segments") or []:
        for item in seg.get("words") or []:
            token = str(item.get("word") or "").strip()
            if not token:
                continue
            start = item.get("start")
            if not isinstance(start, (int, float)):
                continue
            out.append(
                {
                    "word": token,
                    "norm": _normalize_word_token(token),
                    "start": float(start),
                    "end": (
                        float(item["end"])
                        if isinstance(item.get("end"), (int, float))
                        else None
                    ),
                }
            )
    return _normalize_alignment_word_boundaries(out)


def _normalize_alignment_word_boundaries(words: list[dict]) -> list[dict]:
    """Keep generated word timings monotonic when the recognizer leaves gaps."""
    normalized: list[dict] = []
    previous_end = 0.0
    for item in words:
        word = dict(item)
        start = max(0.0, float(word["start"]))
        if normalized and start < previous_end:
            start = previous_end
        end = word.get("end")
        if not isinstance(end, (int, float)) or float(end) <= start:
            end = start + 0.08
        word["start"] = start
        word["end"] = float(end)
        previous_end = float(end)
        normalized.append(word)
    return normalized


def _rebuild_word_timed_lrc_from_alignment(
    lrc_content: str, alignment_payload: dict
) -> str:
    lyric_words = _extract_lrc_words(lrc_content)
    aligned_words = _extract_alignment_words(alignment_payload)

    if not lyric_words:
        raise RuntimeError("No lyric words found in existing LRC for correction.")
    if not aligned_words:
        raise RuntimeError("No aligned words were produced by Faster-Whisper.")

    aligned_idx = 0
    last_time = 0.0
    resolved: list[dict] = []

    for lyric_word in lyric_words:
        target_norm = _normalize_word_token(lyric_word)
        matched = None
        search_limit = min(len(aligned_words), aligned_idx + 30)

        for idx in range(aligned_idx, search_limit):
            candidate = aligned_words[idx]
            if target_norm and candidate["norm"] == target_norm:
                matched = candidate
                aligned_idx = idx + 1
                break

        if matched is None:
            if aligned_idx < len(aligned_words):
                matched = aligned_words[aligned_idx]
                aligned_idx += 1
            else:
                last_time += 0.25
                resolved.append({"word": lyric_word, "start": last_time, "end": None})
                continue

        start = max(last_time, float(matched["start"]))
        end = matched.get("end")
        end = (
            float(end) if isinstance(end, (int, float)) and float(end) > start else None
        )
        last_time = end if end is not None else start
        resolved.append({"word": lyric_word, "start": start, "end": end})

    # Emit a start tag for every word plus an <end> hold marker whenever the aligned word
    # finishes before the next word begins, so preview and render fill-and-hold identically
    # to a hand-timed session.
    corrected_lines = []
    for i, cur in enumerate(resolved):
        next_start = resolved[i + 1]["start"] if i + 1 < len(resolved) else None
        line = f"[{_format_lrc_timestamp(cur['start'])}]{cur['word']}"
        end = cur.get("end")
        if (
            end is not None
            and end > cur["start"]
            and (next_start is None or end < next_start - 0.03)
        ):
            line += f"<{_format_lrc_timestamp(end)}>"
        corrected_lines.append(line)

    return "\n".join(corrected_lines)


def _minor_adjust_lrc_timing(
    lrc_content: str, alignment_payload: dict, max_shift: float = 2.0
) -> str:
    """Adjust existing line timestamps without changing lyric text or structure."""
    aligned_words = _extract_alignment_words(alignment_payload)
    if not aligned_words:
        raise RuntimeError("No aligned words were produced by Faster-Whisper.")

    tag_regex = re.compile(r"\[(\d+):(\d+)(?:\.(\d{1,3}))?\]")
    aligned_idx = 0
    last_time = 0.0
    output = []
    adjusted_count = 0

    def _shift_line_timestamps(line: str, delta: float) -> str:
        # Move every [mm:ss.xx] and <mm:ss.xx> tag on the line by the same delta
        # so inline word-level timing and finish-and-hold tags are preserved
        # (not flattened) while the line is nudged.
        if not delta:
            return line

        def _shift_open(m: "re.Match[str]") -> str:
            base = (
                int(m.group(1)) * 60
                + int(m.group(2))
                + int((m.group(3) or "0").ljust(3, "0")[:3]) / 1000
            )
            return f"[{_format_lrc_timestamp(base + delta)}]"

        def _shift_end(m: "re.Match[str]") -> str:
            inner = m.group(0)[1:-1]
            mm, _, rest = inner.partition(":")
            ss, _, frac = rest.partition(".")
            base = int(mm) * 60 + int(ss) + int((frac or "0").ljust(3, "0")[:3]) / 1000
            return f"<{_format_lrc_timestamp(base + delta)}>"

        line = tag_regex.sub(_shift_open, line)
        return _WORD_END_TAG_RE.sub(_shift_end, line)

    for raw_line in (lrc_content or "").splitlines():
        match = tag_regex.search(raw_line)
        if not match:
            output.append(raw_line)
            continue

        original_time = (
            int(match.group(1)) * 60
            + int(match.group(2))
            + int((match.group(3) or "0").ljust(3, "0")[:3]) / 1000
        )
        text = _WORD_END_TAG_RE.sub("", tag_regex.sub("", raw_line)).strip()
        lyric_words = [word for word in re.split(r"\s+", text) if word]
        target_norm = _normalize_word_token(lyric_words[0]) if lyric_words else ""
        matched = None
        for idx in range(aligned_idx, min(len(aligned_words), aligned_idx + 30)):
            if target_norm and aligned_words[idx]["norm"] == target_norm:
                matched = aligned_words[idx]
                aligned_idx = idx + 1
                break

        adjusted_time = original_time
        if matched:
            proposed = float(matched["start"])
            adjusted_time = max(
                original_time - max_shift, min(original_time + max_shift, proposed)
            )
            adjusted_count += 1
        adjusted_time = max(last_time, adjusted_time)
        last_time = adjusted_time
        output.append(_shift_line_timestamps(raw_line, adjusted_time - original_time))

    if adjusted_count == 0:
        raise RuntimeError("Minor timing could not match the existing lyric words.")
    return "\n".join(output)


# Register job runners with the core worker-loop dispatch table.
JOB_RUNNERS["pipeline"] = _run_audio_pipeline_job
JOB_RUNNERS["url"] = _run_url_job
JOB_RUNNERS["lyrics"] = _run_lyrics_fetch_job
JOB_RUNNERS["word_timing"] = _run_word_timing_correction_job


__all__ = [n for n in dir() if not n.startswith("__")]
