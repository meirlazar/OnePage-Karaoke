import os
import gc
import re
import shutil
import subprocess
import sys
import ctypes
import threading
import time
import uuid
import logging
import json
import html
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse
import requests
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="AI Audio Video Production Suite")
WORKSPACE = Path("/workspace")
OUTPUT_DIR = WORKSPACE / "output"
FONTS_DIR = WORKSPACE / "fonts"
SERVED_FONTS_DIR = WORKSPACE / ".served-fonts"
THEMES_DIR = WORKSPACE / "themes"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)
SERVED_FONTS_DIR.mkdir(parents=True, exist_ok=True)
THEMES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")
app.mount("/fonts", StaticFiles(directory=str(SERVED_FONTS_DIR)), name="fonts")
app.mount("/themes", StaticFiles(directory=str(THEMES_DIR)), name="themes")

logger = logging.getLogger("karaoke-miniupgrade")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

ACTIVE_PROCESSING_CACHE = set()
JOB_LOCK = threading.Lock()
JOB_QUEUE = []
JOBS = {}
RUNNING_PROCESSES = {}
SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".webm", ".mp4"}
METUBE_URL = "http://metube:8081"
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "/usr/bin/ffmpeg")
DERIVED_NAME_MARKERS = (
    "_karaoke",
    "_minus",
    "_vocals",
    "_accompaniment",
    "vocals",
    "accompaniment",
)
DEFAULT_WHISPER_MODEL = os.environ.get("WHISPER_MODEL_DEFAULT", "medium")
DEFAULT_TRANSCRIPTION_LANGUAGE = "auto"
DEFAULT_STEM_DEVICE = "auto"
DEFAULT_WHISPER_DEVICE = "auto"
DEFAULT_RENDER_DEVICE = "auto"
DEFAULT_FONT_SOURCE_DIRS = [FONTS_DIR, Path("/usr/local/share/fonts/extrafonts")]
LANGUAGE_ALIASES = {
    "auto": "auto",
    "en": "en",
    "english": "en",
    "ru": "ru",
    "russian": "ru",
    "he": "he",
    "hebrew": "he",
    "es": "es",
    "spanish": "es",
    "fr": "fr",
    "french": "fr",
    "de": "de",
    "german": "de",
    "it": "it",
    "italian": "it",
    "pt": "pt",
    "portuguese": "pt",
    "pl": "pl",
    "polish": "pl",
}
_TITLE_JUNK = re.compile(
    r"\s*[\(\[](official|music|video|starsetonline|audio|lyrics|hd|hq|mv|clip|live|feat\.? .*|"
    r"official\s+\w+\s+video|4k|full)[\)\]]",
    re.IGNORECASE,
)


class JobCancelledError(RuntimeError):
    pass


def _trim_process_heap() -> None:
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except Exception:
        # Not all libc implementations expose malloc_trim; ignore silently.
        pass


def _release_runtime_resources(reason: str = "") -> None:
    # Force Python to release unreachable objects quickly.
    gc.collect()

    # If CUDA is active, return cached pages to the driver.
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except Exception:
        pass

    _trim_process_heap()
    if reason:
        logger.info("[RESOURCE CLEANUP] %s", reason)


def _job_view(job: dict) -> dict:
    return {
        "id": job["id"],
        "type": job["type"],
        "section": job.get("section", job["type"]),
        "stage": job.get("stage", ""),
        "label": job["label"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "audio_filename": job.get("audio_filename", ""),
        "project_name": job.get("project_name", ""),
        "cancel_requested": job.get("cancel_requested", False),
        "device": job.get("device", ""),
        "stem_device": job.get("stem_device", ""),
        "whisper_device": job.get("whisper_device", ""),
        "render_device": job.get("render_device", ""),
        "whisper_model": job.get("whisper_model", ""),
        "transcription_language": job.get("transcription_language", ""),
        "pitch": job.get("pitch", 1),
        "volume": job.get("volume", 1),
        "details": job.get("details", ""),
        "output_filename": job.get("output_filename", ""),
        "error_file": job.get("error_file", ""),
        "error_line": job.get("error_line", 0),
        "error_column": job.get("error_column", 0),
    }


def _normalize_language(language: str | None) -> str:
    raw = str(language or DEFAULT_TRANSCRIPTION_LANGUAGE).strip().lower()
    return LANGUAGE_ALIASES.get(raw, raw or DEFAULT_TRANSCRIPTION_LANGUAGE)


def _normalize_device(device: str | None, fallback: str = "auto") -> str:
    raw = str(device or fallback).strip().lower()
    if raw in {"cuda", "gpu"}:
        return "cuda"
    if raw == "cpu":
        return "cpu"
    return "auto"


def _effective_ai_device(requested: str | None = None) -> str:
    pref = _normalize_device(requested or os.environ.get("AI_DEVICE", "auto"))
    if pref == "cpu":
        return "cpu"
    try:
        import torch

        if not torch.cuda.is_available():
            return "cpu"
        if not _cuda_runtime_ready():
            logger.warning("[CUDA CHECK] CUDA visible but cuDNN runtime libs are missing. Falling back to CPU.")
            return "cpu"
        return "cuda"
    except Exception:
        return "cpu"


def _cuda_runtime_ready() -> bool:
    required = [
        "libcudnn_ops_infer.so.8",
        "libcudnn_cnn_infer.so.8",
    ]
    for libname in required:
        try:
            ctypes.CDLL(libname)
        except OSError:
            return False
    return True


def _whisper_compute_type(device: str) -> str:
    normalized = _normalize_device(device, "auto")
    if normalized == "cuda":
        raw = str(os.environ.get("WHISPER_COMPUTE_TYPE_CUDA", "int8_float16")).strip().lower() or "int8_float16"
        allowed = {"float16", "int8_float16", "float32"}
        if raw not in allowed:
            logger.warning(
                "[FASTER WHISPER] Unsupported CUDA compute_type '%s', falling back to int8_float16.",
                raw,
            )
            return "int8_float16"
        return raw

    raw = str(os.environ.get("WHISPER_COMPUTE_TYPE_CPU", "int8")).strip().lower() or "int8"
    allowed = {"int8", "float32"}
    if raw not in allowed:
        logger.warning(
            "[FASTER WHISPER] Unsupported CPU compute_type '%s', falling back to int8.",
            raw,
        )
        return "int8"
    return raw


def _whisper_compute_type_candidates(device: str) -> list[str]:
    normalized = _normalize_device(device, "auto")
    if normalized == "cuda":
        preferred = _whisper_compute_type("cuda")
        ordered = []
        for candidate in (preferred, "int8_float16", "float16", "float32"):
            if candidate not in ordered:
                ordered.append(candidate)
        return ordered

    preferred = _whisper_compute_type("cpu")
    ordered = []
    for candidate in (preferred, "int8", "float32"):
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _is_whisper_compute_type_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "compute type" in text and ("support" in text or "requested" in text)


def _serialize_faster_whisper_payload(segments, info) -> dict:
    payload_segments = []
    for idx, seg in enumerate(list(segments)):
        words = []
        for word in (getattr(seg, "words", None) or []):
            start = getattr(word, "start", None)
            end = getattr(word, "end", None)
            words.append({
                "word": str(getattr(word, "word", "") or "").strip(),
                "start": float(start) if isinstance(start, (int, float)) else None,
                "end": float(end) if isinstance(end, (int, float)) else None,
                "probability": float(getattr(word, "probability", 0.0) or 0.0),
            })
        payload_segments.append({
            "id": idx,
            "start": float(getattr(seg, "start", 0.0) or 0.0),
            "end": float(getattr(seg, "end", 0.0) or 0.0),
            "text": str(getattr(seg, "text", "") or "").strip(),
            "words": [item for item in words if item["word"]],
        })
    return {
        "language": str(getattr(info, "language", "") or ""),
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "segments": payload_segments,
    }


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
    attempts = [preferred_device]
    if preferred_device == "cuda":
        attempts.append("cpu")

    last_error: Exception | None = None
    for idx, device in enumerate(dict.fromkeys(attempts)):
        current_stage = fallback_stage if idx > 0 else stage
        compute_candidates = _whisper_compute_type_candidates(device)
        for candidate_idx, compute_type in enumerate(compute_candidates):
            _ensure_not_cancelled(job_id)
            stage_msg = action_label if idx == 0 else f"{action_label} fallback on {device.upper()}"
            _update_job(
                job_id,
                stage=current_stage,
                message=f"{stage_msg} using {device.upper()} ({compute_type}) for {audio_path.name}",
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
                if device == "cuda" and _is_whisper_compute_type_error(exc) and candidate_idx < len(compute_candidates) - 1:
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
    raise RuntimeError(f"Faster-Whisper failed for {audio_path.name}")


def _build_lrc_from_transcript_payload(payload: dict) -> str:
    lines = []
    for seg in (payload.get("segments") or []):
        text = str(seg.get("text") or "").strip()
        start = seg.get("start")
        if not text or not isinstance(start, (int, float)):
            continue
        lines.append(f"[{_format_lrc_timestamp(float(start))}]{text}")
    return "\n".join(lines)


def _clean_title(title: str) -> str:
    return _TITLE_JUNK.sub("", str(title or "")).strip(" -")


def _split_artist_title(raw_title: str) -> tuple[str, str]:
    for sep in (" - ", " – ", " — "):
        if sep in raw_title:
            artist, title = raw_title.split(sep, 1)
            return artist.strip(), title.strip()
    return "", raw_title.strip()


def _probe_media_tags(path: Path) -> tuple[str, str]:
    ffprobe_bin = os.environ.get("FFPROBE_BIN", "/usr/bin/ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format_tags=artist,title",
        "-of",
        "json",
        str(path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return "", ""
        payload = json.loads(res.stdout or "{}")
        tags = payload.get("format", {}).get("tags", {}) or {}
        lowered = {str(key).lower(): str(value).strip() for key, value in tags.items()}
        return lowered.get("artist", ""), lowered.get("title", "")
    except Exception:
        return "", ""


def _derive_media_identity(
    *,
    path: Path | None = None,
    raw_name: str = "",
    artist: str = "",
    title: str = "",
    fallback_stem: str = "track",
) -> dict:
    fallback = _clean_title(raw_name or (path.stem if path else fallback_stem)) or fallback_stem
    detected_artist = artist.strip()
    detected_title = title.strip()

    if path and (not detected_artist or not detected_title):
        tag_artist, tag_title = _probe_media_tags(path)
        if not detected_artist:
            detected_artist = tag_artist.strip()
        if not detected_title:
            detected_title = tag_title.strip()

    parsed_artist, parsed_title = _split_artist_title(_clean_title(fallback) or fallback)
    if not detected_artist:
        detected_artist = parsed_artist
    if not detected_title:
        detected_title = parsed_title or fallback

    detected_artist = _clean_title(detected_artist)
    detected_title = _clean_title(detected_title) or fallback_stem
    display = f"{detected_artist} - {detected_title}" if detected_artist and detected_title else detected_title
    return {
        "artist": detected_artist,
        "title": detected_title,
        "display": display,
        "safe_stem": _safe_output_name(display, fallback_stem=fallback_stem),
    }


def _probe_url_identity(url: str) -> dict:
    fallback = _safe_output_name(urlparse(url).path.split("/")[-1] or "download")
    try:
        res = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--no-playlist", url],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and (res.stdout or "").strip():
            meta = json.loads(res.stdout)
            raw_title = str(meta.get("track") or meta.get("title") or fallback)
            artist = str(meta.get("artist") or meta.get("album_artist") or meta.get("creator") or meta.get("uploader") or "")
            return _derive_media_identity(raw_name=raw_title, artist=artist, title=raw_title, fallback_stem=fallback)
    except Exception:
        pass
    return _derive_media_identity(raw_name=fallback, fallback_stem=fallback)


def _unique_output_path(stem: str, suffix: str, *, current_name: str = "") -> Path:
    ext = suffix or ".mp3"
    candidate = OUTPUT_DIR / f"{stem}{ext}"
    if candidate.name == current_name or not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = OUTPUT_DIR / f"{stem}_{counter}{ext}"
        if candidate.name == current_name or not candidate.exists():
            return candidate
        counter += 1


def _apply_canonical_media_name(
    path: Path,
    *,
    raw_name: str = "",
    artist: str = "",
    title: str = "",
) -> tuple[Path, dict]:
    identity = _derive_media_identity(path=path, raw_name=raw_name or path.stem, artist=artist, title=title)
    target = _unique_output_path(identity["safe_stem"], path.suffix or ".mp3", current_name=path.name)
    if target != path:
        path.rename(target)
        logger.info("[MEDIA RENAME] %s -> %s", path.name, target.name)
        path = target
    return path, identity


def _choose_best_identity(downloaded: Path, expected: dict) -> dict:
    actual = _derive_media_identity(path=downloaded, raw_name=downloaded.stem)
    expected_safe = str(expected.get("safe_stem", "") or "").strip().lower()
    weak_expected = expected_safe.startswith("download_") or expected_safe in {"watch", "download", "video"}
    if weak_expected:
        return actual
    if expected.get("artist") or expected.get("title"):
        return _derive_media_identity(
            path=downloaded,
            raw_name=expected.get("display") or downloaded.stem,
            artist=expected.get("artist", ""),
            title=expected.get("title", ""),
            fallback_stem=actual["safe_stem"],
        )
    return actual


def _move_audio_into_project(audio_path: Path, project_name: str) -> Path:
    safe_project = _safe_project_name(project_name, fallback=audio_path.stem)
    project_dir = OUTPUT_DIR / safe_project
    if not project_dir.exists():
        project_dir.mkdir(parents=True, exist_ok=True)
    elif project_dir.is_file():
        raise RuntimeError(f"Cannot create project directory: {project_dir}")

    target_name = f"{safe_project}{audio_path.suffix or '.mp3'}"
    target = project_dir / target_name
    if target.exists() and target.resolve() != audio_path.resolve():
        counter = 2
        while (project_dir / f"{safe_project}_{counter}{audio_path.suffix or '.mp3'}").exists():
            counter += 1
        target = project_dir / f"{safe_project}_{counter}{audio_path.suffix or '.mp3'}"

    if target.resolve() != audio_path.resolve():
        audio_path.rename(target)
    return target


def _wait_for_new_media_file(job_id: str, known_files: set[str], timeout_seconds: int = 1800) -> Path:
    seen_sizes: dict[str, int] = {}
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        _ensure_not_cancelled(job_id)
        for file_path in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not _is_source_media(file_path) or file_path.name in known_files:
                continue
            size = file_path.stat().st_size
            if size > 0 and seen_sizes.get(file_path.name) == size:
                return file_path
            seen_sizes[file_path.name] = size
        _update_job(job_id, progress=30, message="Waiting for MeTube download to finish")
        time.sleep(2)
    raise RuntimeError("Timed out waiting for MeTube to finish downloading")


def _font_source_dirs() -> list[Path]:
    raw = os.environ.get("FONT_SOURCE_DIRS", "")
    dirs = []
    if raw:
        dirs.extend(Path(part) for part in raw.split(":") if part.strip())
    dirs.extend(DEFAULT_FONT_SOURCE_DIRS)
    seen = set()
    unique = []
    for path in dirs:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _refresh_font_cache() -> list[dict]:
    fonts = []
    for source_dir in _font_source_dirs():
        if not source_dir.exists() or not source_dir.is_dir():
            continue
        for pattern in ("*.ttf", "*.TTF", "*.otf", "*.OTF"):
            for font_path in source_dir.rglob(pattern):
                target_path = SERVED_FONTS_DIR / font_path.name
                if not target_path.exists():
                    try:
                        shutil.copy2(font_path, target_path)
                    except Exception:
                        continue
                fonts.append({"name": font_path.stem, "filename": target_path.name})

    deduped = []
    seen_names = set()
    for item in fonts:
        key = (item["name"], item["filename"])
        if key in seen_names:
            continue
        seen_names.add(key)
        deduped.append(item)
    return sorted(deduped, key=lambda item: item["name"].lower())


def _update_job(job_id: str, **updates) -> dict | None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        job.update(updates)
        job["updated_at"] = time.time()
        return dict(job)


def _workspace_relative_path(path_like: str | Path) -> str:
    try:
        path = Path(path_like).resolve()
        return path.relative_to(WORKSPACE.resolve()).as_posix()
    except Exception:
        return str(path_like)


def _extract_exception_location(exc: Exception) -> dict:
    frames = traceback.extract_tb(exc.__traceback__)
    chosen = frames[-1] if frames else None
    workspace_root = str(WORKSPACE.resolve())
    for frame in reversed(frames):
        filename = str(Path(frame.filename).resolve())
        if filename.startswith(workspace_root):
            chosen = frame
            break
    if not chosen:
        return {}
    return {
        "error_file": _workspace_relative_path(chosen.filename),
        "error_line": int(getattr(chosen, "lineno", 0) or 0),
        "error_column": 0,
        "error_function": getattr(chosen, "name", "") or "",
        "error_code": getattr(chosen, "line", "") or "",
    }


def _collect_python_syntax_issues() -> list[dict]:
    issues: list[dict] = []
    for path in sorted(WORKSPACE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            compile(source, str(path), "exec")
        except SyntaxError as err:
            line_text = (err.text or "").rstrip("\n")
            column = int(err.offset or 0)
            issues.append({
                "kind": "syntax",
                "severity": "error",
                "message": str(err.msg or err),
                "file": _workspace_relative_path(path),
                "line": int(err.lineno or 0),
                "column": column,
                "code": line_text,
                "pointer": (" " * max(0, column - 1) + "^") if column else "",
            })
        except Exception as exc:
            issues.append({
                "kind": "syntax",
                "severity": "error",
                "message": str(exc),
                "file": _workspace_relative_path(path),
                "line": 0,
                "column": 0,
                "code": "",
                "pointer": "",
            })
    return issues


def _collect_runtime_issues() -> list[dict]:
    issues = []
    with JOB_LOCK:
        failed_jobs = [job for job in JOBS.values() if job.get("status") == "failed"]
    for job in sorted(failed_jobs, key=lambda item: item.get("updated_at", 0), reverse=True)[:12]:
        issues.append({
            "kind": "runtime",
            "severity": "error",
            "job_id": job.get("id", ""),
            "job_label": job.get("label", ""),
            "message": job.get("message", "Unknown runtime failure"),
            "file": job.get("error_file", ""),
            "line": int(job.get("error_line", 0) or 0),
            "column": int(job.get("error_column", 0) or 0),
            "code": job.get("error_code", ""),
            "trace": job.get("error_trace", ""),
        })
    return issues


def _load_theme_catalog() -> list[dict]:
    themes = []
    for path in sorted(THEMES_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        theme_id = str(payload.get("id") or path.stem)
        vars_payload = payload.get("vars") or {}
        if not isinstance(vars_payload, dict) or not vars_payload:
            continue
        themes.append({
            "id": theme_id,
            "name": str(payload.get("name") or theme_id),
            "description": str(payload.get("description") or ""),
            "source_name": str(payload.get("source_name") or ""),
            "source_url": str(payload.get("source_url") or ""),
            "license": str(payload.get("license") or ""),
            "vars": vars_payload,
        })
    return themes


def _cmd_text(cmd: list[str]) -> str:
    return " ".join(str(part) for part in cmd)


def _find_active_job_by_key(target_key: str) -> dict | None:
    for job in JOBS.values():
        if job.get("target_key") == target_key and job["status"] in {"queued", "running"}:
            return job
    return None


def _has_active_job_for_audio(rel_audio: str, project_name: str = "") -> bool:
    for job in JOBS.values():
        if job.get("status") not in {"queued", "running"}:
            continue
        if rel_audio and job.get("audio_filename") == rel_audio:
            return True
        if project_name and job.get("project_name") == project_name:
            return True
    return False


def _enqueue_job(kind: str, label: str, runner: str, *, section: str = "general", stage: str = "", target_key: str = "", details: str = "", **kwargs) -> dict:
    with JOB_LOCK:
        if target_key:
            existing = _find_active_job_by_key(target_key)
            if existing:
                return _job_view(existing)

        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "type": kind,
            "label": label,
            "section": section,
            "stage": stage,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "cancel_requested": False,
            "runner": runner,
            "runner_kwargs": kwargs,
            "target_key": target_key,
            "audio_filename": kwargs.get("audio_filename", ""),
            "project_name": kwargs.get("project_name", ""),
            "device": kwargs.get("device", ""),
            "stem_device": kwargs.get("stem_device", ""),
            "whisper_device": kwargs.get("whisper_device", ""),
            "render_device": kwargs.get("render_device", ""),
            "whisper_model": kwargs.get("whisper_model", ""),
            "transcription_language": kwargs.get("transcription_language", ""),
            "pitch": kwargs.get("pitch", 1),
            "volume": kwargs.get("volume", 1),
            "details": details,
            "output_filename": kwargs.get("output_filename", ""),
        }
        JOBS[job_id] = job
        JOB_QUEUE.append(job_id)
        logger.info("[QUEUE] queued job=%s type=%s runner=%s section=%s stage=%s target=%s", job_id, kind, runner, section, stage, target_key or "-")
        return _job_view(job)


def _check_cancel_requested(job_id: str) -> bool:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))


def _ensure_not_cancelled(job_id: str) -> None:
    if _check_cancel_requested(job_id):
        raise JobCancelledError("Job cancelled")


def _terminate_running_process(job_id: str) -> None:
    with JOB_LOCK:
        proc = RUNNING_PROCESSES.get(job_id)
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _cancel_job(job_id: str) -> dict | None:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        if job["status"] in {"completed", "failed", "cancelled"}:
            return _job_view(job)
        job["cancel_requested"] = True
        if job["status"] == "queued":
            job["status"] = "cancelled"
            job["message"] = "Cancelled before start"
            job["updated_at"] = time.time()
            return _job_view(job)
        job["message"] = "Cancelling..."
        job["updated_at"] = time.time()
    logger.info("[QUEUE] cancellation requested job=%s label=%s status=%s", job_id, job.get("label", "-"), job.get("status", "-"))
    _terminate_running_process(job_id)
    return _job_view(job)


def _run_cancellable_command(job_id: str, cmd: list[str], message: str, start_progress: int, end_progress: int) -> None:
    _ensure_not_cancelled(job_id)
    logger.info("[PROC START] job=%s %s | %s", job_id, message, _cmd_text(cmd))
    _update_job(job_id, status="running", message=message, progress=start_progress)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    with JOB_LOCK:
        RUNNING_PROCESSES[job_id] = proc
    try:
        pulse_progress = int(start_progress)
        pulse_cap = max(int(start_progress), int(end_progress) - 2)
        last_pulse_at = time.time()
        while proc.poll() is None:
            if _check_cancel_requested(job_id):
                _terminate_running_process(job_id)
                raise JobCancelledError("Job cancelled")
            now = time.time()
            # Keep UI responsive for commands that do not emit parseable progress.
            if pulse_progress < pulse_cap and (now - last_pulse_at) >= 1.5:
                pulse_progress += 1
                _update_job(job_id, progress=pulse_progress)
                last_pulse_at = now
            time.sleep(0.5)
        stderr_output = proc.stderr.read() if proc.stderr else ""
        if proc.returncode != 0:
            logger.error("[PROC FAIL] job=%s code=%s cmd=%s\n%s", job_id, proc.returncode, _cmd_text(cmd), (stderr_output or "")[-2000:])
            raise RuntimeError((stderr_output or f"Command failed: {' '.join(cmd)}").strip()[-2000:])
        logger.info("[PROC OK] job=%s code=0 cmd=%s", job_id, _cmd_text(cmd))
        _update_job(job_id, progress=end_progress)
    finally:
        with JOB_LOCK:
            RUNNING_PROCESSES.pop(job_id, None)


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
        _update_job(job_id, status="running", stage="stem separation", message=f"{prefix} for {audio_path.name}", progress=start_progress)
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
        logger.info("[DEMUCS START] job=%s device=%s cmd=%s", job_id, device, _cmd_text(cmd))
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
                        progress = start_progress + int((end_progress - start_progress) * (pct / 100.0))
                        _update_job(job_id, message=f"Separating stems for {audio_path.name} ({pct}%)", progress=progress)
            returncode = proc.wait()
            if returncode == 0:
                logger.info("[DEMUCS OK] job=%s device=%s", job_id, device)
                _update_job(job_id, progress=end_progress)
                return device
            collected = "".join(all_output)
            last_error = (collected or f"demucs failed with code {returncode}")[-2000:]
            logger.warning("[DEMUCS FAIL] job=%s device=%s code=%s", job_id, device, returncode)
        finally:
            with JOB_LOCK:
                RUNNING_PROCESSES.pop(job_id, None)

    raise RuntimeError(last_error or "Demucs separation failed")

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
    identity = _derive_media_identity(path=audio_path, raw_name=display_title or audio_path.stem)
    lyrics_search = lyrics_query or identity["display"] or audio_path.stem
    logger.info("[PIPELINE START] job=%s audio=%s", job_id, audio_path.name)
    ACTIVE_PROCESSING_CACHE.add(rel_audio)
    try:
        project_dir = audio_path.parent
        stems_out = project_dir / "stems"
        whisper_ai_device = _effective_ai_device(whisper_device)
        demucs_end = start_progress + int((end_progress - start_progress) * 0.45)
        stem_ai_device = _run_demucs_with_progress(
            job_id,
            audio_path,
            stems_out,
            stem_device,
            start_progress,
            demucs_end,
        )
        _update_job(job_id, details=f"Stem device: {stem_ai_device}; Whisper device: {whisper_ai_device}; Model: {whisper_model}; Language: {transcription_language}")

        no_vocals_track = stems_out / "htdemucs" / audio_path.stem / "no_vocals.wav"
        minus_track = project_dir / f"{audio_path.stem}_minus.mp3"
        if no_vocals_track.exists():
            _update_job(job_id, stage="package accompaniment", message=f"Packaging accompaniment for {audio_path.name}", progress=demucs_end)
            logger.info("[PIPELINE] job=%s packaging accompaniment=%s", job_id, no_vocals_track)
            ffmpeg_res = subprocess.run(
                [FFMPEG_BIN, "-y", "-i", str(no_vocals_track), "-q:a", "2", str(minus_track)],
                capture_output=True,
                text=True,
            )
            if ffmpeg_res.returncode != 0:
                logger.warning("[PIPELINE] job=%s accompaniment packaging failed: %s", job_id, (ffmpeg_res.stderr or "")[-300:])
                _update_job(job_id, message=f"Separation complete, accompaniment packaging failed for {audio_path.name}")

        vocals_track = stems_out / "htdemucs" / audio_path.stem / "vocals.wav"
        lyrics_end = start_progress + int((end_progress - start_progress) * 0.65)
        lrc_file = project_dir / f"{audio_path.stem}.lrc"

        _update_job(job_id, stage="lyrics fetch", message=f"Fetching synced lyrics for {audio_path.name}", progress=demucs_end)
        _ensure_not_cancelled(job_id)
        res = subprocess.run(["syncedlyrics", lyrics_search, "-o", str(lrc_file)], capture_output=True, text=True)
        if res.returncode == 0 and lrc_file.exists() and lrc_file.stat().st_size >= 10:
            logger.info("[PIPELINE] job=%s syncedlyrics hit for %s", job_id, audio_path.name)
            _update_job(job_id, progress=end_progress, message=f"Timed lyrics ready for {audio_path.name}")
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
            raise RuntimeError(f"Faster-Whisper produced no timed transcription for {audio_path.name}")
        (project_dir / "vocals.json").write_text(json.dumps(transcript_payload, ensure_ascii=False), encoding="utf-8")
        lrc_file.write_text(lrc_content, encoding="utf-8")
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
    _run_cancellable_command(job_id, cmd, f"Downloading source from URL", 5, 35)
    produced = sorted(OUTPUT_DIR.glob(f"{temp_stem}_*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not produced:
        raise RuntimeError("yt-dlp completed but no output file was found")
    return produced[0]


def _download_with_metube(job_id: str, url: str, *, start_progress: int = 20, end_progress: int = 35) -> tuple[Path, dict]:
    known_files = {path.name for path in OUTPUT_DIR.iterdir() if _is_source_media(path)}
    expected = _probe_url_identity(url)
    _update_job(job_id, status="running", progress=start_progress, message="Forwarding URL to MeTube")
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
    downloaded = _move_audio_into_project(downloaded, identity["safe_stem"])
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
) -> None:
    engine_norm = (engine or "ytdl").strip().lower()
    logger.info("[URL START] job=%s engine=%s url=%s", job_id, engine_norm, url)
    if engine_norm == "metube":
        downloaded, identity = _download_with_metube(job_id, url, start_progress=20, end_progress=35)
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
        downloaded = _move_audio_into_project(downloaded, identity["safe_stem"])
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
        logger.warning("[URL FALLBACK] job=%s ytdlp failed, retrying via MeTube: %s", job_id, err_text)
        _update_job(job_id, message="yt-dlp failed (YouTube anti-bot/JS). Retrying via MeTube...", progress=15)
        downloaded, identity = _download_with_metube(job_id, url, start_progress=20, end_progress=35)
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
    output_filename: str = "",
    render_token: str = "",
    project_name: str = "",
) -> None:
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
            execute_ffmpeg_burn(
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
                output_filename=output_filename,
                render_token=render_token,
            )
            _update_job(job_id, render_device=attempt_device)
            logger.info("[RENDER END] job=%s audio=%s device=%s", job_id, audio_filename, attempt_device)
            return
        except JobCancelledError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning("[RENDER FAIL] job=%s device=%s error=%s", job_id, attempt_device, exc)
            if attempt_device == "cpu":
                raise

    if last_error:
        raise last_error


def _run_lyrics_fetch_job(
    job_id: str,
    audio_filename: str,
    *,
    lyrics_query: str = "",
    display_title: str = "",
    provider: str = "syncedlyrics",
    project_name: str = "",
) -> None:
    audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    rel_audio = _relative_to_output(audio_path)
    _update_job(job_id, audio_filename=rel_audio, project_name=audio_path.parent.name)

    project_dir = audio_path.parent
    lrc_file = project_dir / f"{audio_path.stem}.lrc"
    if _has_timed_lyrics(lrc_file):
        _update_job(job_id, progress=100, message=f"Lyrics already exist for {audio_path.name}")
        return

    selected_provider = str(provider or "syncedlyrics").strip().lower()
    if selected_provider not in {"syncedlyrics", "lrclib", "genius"}:
        selected_provider = "lrclib"

    _update_job(
        job_id,
        status="running",
        stage="lyrics fetch",
        message=f"Fetching lyrics via {selected_provider} for {audio_path.name}",
        progress=20,
    )

    _ensure_not_cancelled(job_id)
    if selected_provider == "syncedlyrics":
        identity = _derive_media_identity(path=audio_path, raw_name=display_title or audio_path.stem)
        lookup = lyrics_query or identity["display"] or audio_path.stem
        res = subprocess.run(["syncedlyrics", lookup, "-o", str(lrc_file)], capture_output=True, text=True)
        if res.returncode != 0 or not _has_timed_lyrics(lrc_file):
            raise RuntimeError((res.stderr or res.stdout or f"Lyrics fetch failed for {audio_path.name}").strip()[-2000:])
    else:
        _fetch_lyrics_for_media_with_provider(audio_path, selected_provider)
        if not _has_timed_lyrics(lrc_file):
            raise RuntimeError(f"{selected_provider} did not produce timed lyrics for {audio_path.name}")

    _ensure_not_cancelled(job_id)
    _update_job(job_id, progress=100, message=f"Lyrics ready via {selected_provider} for {audio_path.name}")


def _run_word_timing_correction_job(
    job_id: str,
    audio_filename: str,
    *,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    whisper_device: str = DEFAULT_WHISPER_DEVICE,
    project_name: str = "",
) -> None:
    audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    rel_audio = _relative_to_output(audio_path)
    _update_job(job_id, audio_filename=rel_audio, project_name=audio_path.parent.name)

    project_dir = audio_path.parent
    lrc_file = project_dir / f"{audio_path.stem}.lrc"
    vocals_track = project_dir / "stems" / "htdemucs" / audio_path.stem / "vocals.wav"

    if not lrc_file.exists():
        raise RuntimeError(f"Missing lyrics file for correction: {lrc_file.name}")
    if not vocals_track.exists():
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
    json_path.write_text(json.dumps(aligned_payload, ensure_ascii=False), encoding="utf-8")

    _update_job(job_id, stage="apply word timing", message=f"Applying AI word timing to {lrc_file.name}", progress=80)
    corrected_lrc = _rebuild_word_timed_lrc_from_alignment(lrc_file.read_text(encoding="utf-8", errors="ignore"), aligned_payload)
    lrc_file.write_text(corrected_lrc, encoding="utf-8")

    _update_job(job_id, progress=100, message=f"AI word timing correction complete for {audio_path.name}")


def _sync_project_jobs() -> None:
    manifests = _list_project_manifests()
    for manifest in manifests:
        rel_audio = manifest["audio_filename"]
        project_name = manifest["project_name"]
        audio_path = _resolve_output_file(rel_audio)
        if _has_active_job_for_audio(rel_audio, project_name):
            continue
        if rel_audio in ACTIVE_PROCESSING_CACHE:
            continue

        if not manifest["has_stems"]:
            _start_pipeline_if_idle(audio_path, project_name, display_title=project_name, lyrics_query=project_name)
            continue

        if manifest["has_stems"] and not manifest["has_timed_lyrics"]:
            _enqueue_job(
                "lyrics",
                f"Fetch lyrics for {project_name}",
                "lyrics",
                section="ingest",
                stage="lyrics fetch",
                target_key=f"lyrics:{rel_audio}",
                audio_filename=rel_audio,
                project_name=project_name,
                lyrics_query=project_name,
                display_title=project_name,
            )


def _job_worker_loop() -> None:
    while True:
        job = None
        with JOB_LOCK:
            while JOB_QUEUE:
                next_job = JOBS.get(JOB_QUEUE.pop(0))
                if not next_job:
                    continue
                if next_job["status"] == "cancelled":
                    continue
                job = next_job
                job["status"] = "running"
                job["message"] = "Started"
                job["updated_at"] = time.time()
                break
        if not job:
            time.sleep(0.3)
            continue

        try:
            if job["runner"] == "pipeline":
                logger.info("[WORKER] start job=%s runner=pipeline", job["id"])
                _run_audio_pipeline_job(job["id"], **job["runner_kwargs"])
            elif job["runner"] == "url":
                logger.info("[WORKER] start job=%s runner=url", job["id"])
                _run_url_job(job["id"], **job["runner_kwargs"])
            elif job["runner"] == "render":
                logger.info("[WORKER] start job=%s runner=render", job["id"])
                _run_render_job(job["id"], **job["runner_kwargs"])
            elif job["runner"] == "lyrics":
                logger.info("[WORKER] start job=%s runner=lyrics", job["id"])
                _run_lyrics_fetch_job(job["id"], **job["runner_kwargs"])
            elif job["runner"] == "word_timing":
                logger.info("[WORKER] start job=%s runner=word_timing", job["id"])
                _run_word_timing_correction_job(job["id"], **job["runner_kwargs"])
            else:
                raise RuntimeError(f"Unknown job runner: {job['runner']}")

            current = JOBS.get(job["id"], {})
            if current.get("status") == "running":
                _update_job(job["id"], status="completed", progress=100, message="Completed")
                logger.info("[WORKER] completed job=%s", job["id"])
        except JobCancelledError:
            _update_job(job["id"], status="cancelled", message="Cancelled", progress=0)
            logger.info("[WORKER] cancelled job=%s", job["id"])
        except Exception as exc:
            trace_text = traceback.format_exc()
            location = _extract_exception_location(exc)
            _update_job(
                job["id"],
                status="failed",
                message=str(exc),
                progress=0,
                error_trace=trace_text,
                **location,
            )
            logger.error("[WORKER] failed job=%s: %s\n%s", job["id"], exc, trace_text)
        finally:
            _release_runtime_resources(f"post job cleanup ({job.get('id', '-')})")


def _safe_output_name(name: str, fallback_stem: str = "track") -> str:
    stem = Path(name or fallback_stem).stem.strip() or fallback_stem
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    return cleaned or fallback_stem


def _safe_project_name(name: str, fallback: str = "project") -> str:
    return _safe_output_name(name, fallback_stem=fallback)


def _relative_to_output(path: Path) -> str:
    return path.resolve().relative_to(OUTPUT_DIR.resolve()).as_posix()


def _resolve_output_path(path_like: str, *, require_exists: bool = True) -> Path:
    rel = Path(str(path_like or "").strip().lstrip("/"))
    candidate = (OUTPUT_DIR / rel).resolve()
    output_root = OUTPUT_DIR.resolve()
    if candidate != output_root and output_root not in candidate.parents:
        raise FileNotFoundError(f"Path not allowed: {path_like}")
    if require_exists and not candidate.exists():
        raise FileNotFoundError(f"Path not found: {path_like}")
    return candidate


def _resolve_project_dir(project_name: str) -> Path:
    project = _safe_project_name(project_name, fallback="project")
    project_dir = (OUTPUT_DIR / project).resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root not in project_dir.parents:
        raise FileNotFoundError("Invalid project name")
    return project_dir


def _project_sidecar_paths(project_dir: Path, stem: str) -> list[Path]:
    return [
        project_dir / f"{stem}.lrc",
        project_dir / f"{stem}.ass",
        project_dir / f"{stem}.json",
        project_dir / f"{stem}.srt",
        project_dir / f"{stem}.txt",
        project_dir / f"{stem}_karaoke.mp4",
        project_dir / f"{stem}_minus.mp3",
    ]


def _ensure_project_layout_for_audio(audio_path: Path) -> Path:
    if audio_path.parent != OUTPUT_DIR:
        return audio_path

    base_project = _safe_project_name(audio_path.stem, fallback="project")
    project_dir = OUTPUT_DIR / base_project
    if project_dir.exists() and project_dir.is_dir():
        if not (project_dir / audio_path.name).exists():
            counter = 2
            while (OUTPUT_DIR / f"{base_project}_{counter}").exists():
                counter += 1
            project_dir = OUTPUT_DIR / f"{base_project}_{counter}"
    project_dir.mkdir(parents=True, exist_ok=True)

    target_audio = project_dir / audio_path.name
    if target_audio.exists() and target_audio.resolve() != audio_path.resolve():
        counter = 2
        while (project_dir / f"{audio_path.stem}_{counter}{audio_path.suffix or '.mp3'}").exists():
            counter += 1
        target_audio = project_dir / f"{audio_path.stem}_{counter}{audio_path.suffix or '.mp3'}"
    if target_audio != audio_path:
        audio_path.rename(target_audio)
    old_stem = audio_path.stem
    for candidate in _project_sidecar_paths(OUTPUT_DIR, old_stem):
        if candidate.exists() and candidate.is_file():
            candidate.rename(project_dir / candidate.name)

    old_stems = OUTPUT_DIR / "stems" / "htdemucs" / old_stem
    new_stems = project_dir / "stems" / "htdemucs" / old_stem
    if old_stems.exists() and not new_stems.exists():
        new_stems.parent.mkdir(parents=True, exist_ok=True)
        old_stems.rename(new_stems)

    return target_audio


def _project_manifest(project_dir: Path) -> dict | None:
    if not project_dir.exists() or not project_dir.is_dir():
        return None
    audio_files = sorted(
        [path for path in project_dir.iterdir() if _is_source_media(path)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not audio_files:
        return None

    audio_path = audio_files[0]
    audio_stem = audio_path.stem
    rel_audio = _relative_to_output(audio_path)
    lrc_path = project_dir / f"{audio_stem}.lrc"
    stems_dir = project_dir / "stems" / "htdemucs" / audio_stem
    has_stems = (stems_dir / "vocals.wav").exists() or (stems_dir / "no_vocals.wav").exists()
    has_timed_lyrics = _has_timed_lyrics(lrc_path)
    proj_path = project_dir / f"{project_dir.name}.proj.json"
    has_proj = proj_path.exists() and proj_path.is_file()

    state = "media_only"
    if has_stems and not has_timed_lyrics:
        state = "stems_ready_lyrics_pending"
    elif has_timed_lyrics and not has_proj:
        state = "lyrics_ready_unsaved"
    elif has_timed_lyrics and has_proj:
        state = "project_saved"

    return {
        "name": project_dir.name,
        "project_name": project_dir.name,
        "audio_filename": rel_audio,
        "size": f"{audio_path.stat().st_size / (1024*1024):.2f} MB",
        "url": f"/files/{quote(rel_audio, safe='/')}",
        "type": audio_path.suffix.lower(),
        "audio_name": audio_path.name,
        "has_stems": has_stems,
        "has_timed_lyrics": has_timed_lyrics,
        "has_proj": has_proj,
        "state": state,
    }


def _list_project_manifests() -> list[dict]:
    manifests = []
    for path in OUTPUT_DIR.iterdir():
        if path.is_dir() and path.name != "stems":
            manifest = _project_manifest(path)
            if manifest:
                manifests.append(manifest)

    # Compatibility migration path: convert loose top-level media files into project folders.
    for path in sorted(OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not _is_source_media(path):
            continue
        moved = _ensure_project_layout_for_audio(path)
        manifest = _project_manifest(moved.parent)
        if manifest:
            manifests.append(manifest)

    dedup = {}
    for item in manifests:
        dedup[item["project_name"]] = item
    return sorted(dedup.values(), key=lambda item: item["project_name"].lower())

def _start_pipeline_if_idle(
    audio_path: Path,
    song_title: str,
    stem_device: str = DEFAULT_STEM_DEVICE,
    whisper_device: str = DEFAULT_WHISPER_DEVICE,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE,
    lyrics_query: str = "",
    display_title: str = "",
) -> dict | None:
    audio_path = _ensure_project_layout_for_audio(audio_path)
    rel_audio = _relative_to_output(audio_path)
    if rel_audio in ACTIVE_PROCESSING_CACHE:
        return None
    return _enqueue_job(
        "pipeline",
        f"Process {display_title or song_title}",
        "pipeline",
        section="ingest",
        stage="download and separate",
        target_key=f"pipeline:{rel_audio}",
        audio_filename=rel_audio,
        project_name=audio_path.parent.name,
        stem_device=stem_device,
        whisper_device=whisper_device,
        whisper_model=whisper_model,
        transcription_language=_normalize_language(transcription_language),
        lyrics_query=lyrics_query,
        display_title=display_title,
        details=f"Stem device: {stem_device}; Whisper device: {whisper_device}; Model: {whisper_model}; Language: {transcription_language}",
    )


def _is_source_media(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
        return False
    lowered = path.name.lower()
    if lowered.startswith("download_"):
        return False
    if any(marker in lowered for marker in DERIVED_NAME_MARKERS):
        return False
    # Ignore ffmpeg/subtitle byproducts even if extension happens to match supported media.
    if lowered.endswith(".ass") or lowered.endswith(".lrc"):
        return False
    return True

def _convert_srt_to_lrc(srt_path: Path, lrc_path: Path) -> None:
    """Converts standard SRT output into LRC format for the frontend."""
    if not srt_path.exists():
        logger.error("[LRC CONVERSION] Missing SRT file: %s", srt_path)
        return

    content = srt_path.read_text(encoding="utf-8")
    lrc_lines = []
    for block in content.strip().split('\n\n'):
        lines = block.split('\n')
        if len(lines) >= 3:
            times = lines[1].split(' --> ')
            if len(times) == 2:
                start_time = times[0].replace(',', '.')
                try:
                    h, m, s = start_time.split(':')
                    total_m = int(h) * 60 + int(m)
                    s_sec, s_ms = s.split('.')
                    lrc_time = f"[{total_m:02d}:{s_sec}.{s_ms[:2]}]"
                    text = " ".join(lines[2:])
                    lrc_lines.append(f"{lrc_time}{text}")
                except Exception as e:
                    logger.warning("[LRC CONVERSION] Malformed timestamp %s: %s", start_time, e)
                    continue

    lrc_path.write_text("\n".join(lrc_lines), encoding="utf-8")
    logger.info("[LRC CONVERSION] Successfully built %s", lrc_path.name)

def _resolve_output_file(filename: str) -> Path:
    candidate = _resolve_output_path(filename, require_exists=True)
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"File not found: {filename}")
    return candidate


def _fetch_lyrics_for_media(audio_path: Path) -> str:
    audio_path = _ensure_project_layout_for_audio(audio_path)
    project_dir = audio_path.parent
    lrc_file = project_dir / f"{audio_path.stem}.lrc"
    identity = _derive_media_identity(path=audio_path, raw_name=audio_path.stem)
    res = subprocess.run(["syncedlyrics", identity["display"], "-o", str(lrc_file)], capture_output=True, text=True)
    if res.returncode != 0 or not lrc_file.exists() or lrc_file.stat().st_size < 10:
        raise RuntimeError((res.stderr or res.stdout or "Lyrics lookup failed").strip())
    return lrc_file.read_text(encoding="utf-8")


def _plain_text_to_lrc(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    out = []
    cursor = 0.0
    for line in lines:
        out.append(f"[{_format_lrc_timestamp(cursor)}]{line}")
        cursor += 3.5
    return "\n".join(out)


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
            res = requests.get("https://lrclib.net/api/search", params=params, headers=headers, timeout=12)
            if res.status_code >= 400:
                continue
            payload = res.json()
            if not isinstance(payload, list) or not payload:
                continue
            entry = payload[0] if isinstance(payload[0], dict) else {}
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
    response = search_payload.get("response") if isinstance(search_payload, dict) else {}
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
    search_res = requests.get("https://genius.com/api/search/multi", params={"q": query}, headers=headers, timeout=12)
    if search_res.status_code >= 400:
        raise RuntimeError(f"Genius search failed with status {search_res.status_code}")
    path = _extract_genius_path(search_res.json())
    if not path:
        raise RuntimeError("Genius returned no matching song path")

    page_res = requests.get(f"https://genius.com{path}", headers=headers, timeout=12)
    if page_res.status_code >= 400:
        raise RuntimeError(f"Genius lyrics page failed with status {page_res.status_code}")
    page = page_res.text or ""

    blocks = re.findall(r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>', page, flags=re.IGNORECASE | re.DOTALL)
    if not blocks:
        raise RuntimeError("Could not extract lyrics from Genius page")
    merged = "\n".join(_strip_html_to_text(block) for block in blocks if block.strip())
    merged = merged.strip()
    if not merged:
        raise RuntimeError("Genius returned an empty lyrics payload")
    return _plain_text_to_lrc(merged)


def _fetch_lyrics_for_media_with_provider(audio_path: Path, provider: str) -> str:
    audio_path = _ensure_project_layout_for_audio(audio_path)
    project_dir = audio_path.parent
    lrc_file = project_dir / f"{audio_path.stem}.lrc"
    identity = _derive_media_identity(path=audio_path, raw_name=audio_path.stem)

    selected = str(provider or "").strip().lower()
    if selected not in {"genius", "lrclib", "syncedlyrics"}:
        selected = "lrclib"

    if selected == "syncedlyrics":
        content = _fetch_lyrics_for_media(audio_path)
    elif selected == "lrclib":
        content = _fetch_lyrics_from_lrclib(identity)
    else:
        content = _fetch_lyrics_from_genius(identity)

    if not str(content or "").strip():
        raise RuntimeError(f"{selected} returned no lyrics")
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
        text = tag_regex.sub(" ", stripped).strip()
        if not text:
            continue
        words.extend([part for part in re.split(r"\s+", text) if part])
    return words


def _extract_alignment_words(json_payload: dict) -> list[dict]:
    out: list[dict] = []
    for seg in (json_payload.get("segments") or []):
        for item in (seg.get("words") or []):
            token = str(item.get("word") or "").strip()
            if not token:
                continue
            start = item.get("start")
            if not isinstance(start, (int, float)):
                continue
            out.append({
                "word": token,
                "norm": _normalize_word_token(token),
                "start": float(start),
            })
    return out


def _rebuild_word_timed_lrc_from_alignment(lrc_content: str, alignment_payload: dict) -> str:
    lyric_words = _extract_lrc_words(lrc_content)
    aligned_words = _extract_alignment_words(alignment_payload)

    if not lyric_words:
        raise RuntimeError("No lyric words found in existing LRC for correction.")
    if not aligned_words:
        raise RuntimeError("No aligned words were produced by Faster-Whisper.")

    aligned_idx = 0
    last_time = 0.0
    corrected_lines = []

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
                corrected_lines.append(f"[{_format_lrc_timestamp(last_time)}]{lyric_word}")
                continue

        stamp = max(last_time, float(matched["start"]))
        last_time = stamp
        corrected_lines.append(f"[{_format_lrc_timestamp(stamp)}]{lyric_word}")

    return "\n".join(corrected_lines)


def _has_timed_lyrics(lrc_path: Path) -> bool:
    if not lrc_path.exists() or not lrc_path.is_file():
        return False
    try:
        content = lrc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return re.search(r"\[\d{1,2}:\d{1,2}(?:\.\d{1,3})?\]", content) is not None


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


def _render_video_encoder_args(render_device: str) -> list[str]:
    probe = subprocess.run([FFMPEG_BIN, "-hide_banner", "-encoders"], capture_output=True, text=True)
    encoders = probe.stdout or ""
    normalized = _normalize_device(render_device, DEFAULT_RENDER_DEVICE)
    if normalized != "cpu" and "h264_nvenc" in encoders:
        return ["-c:v", "h264_nvenc", "-b:v", "5M"]
    if "libx264" in encoders:
        return ["-c:v", "libx264", "-crf", "26"]
    if "libopenh264" in encoders:
        return ["-c:v", "libopenh264", "-b:v", "3M"]
    return ["-c:v", "mpeg4", "-q:v", "5"]


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


def directory_watcher_loop() -> None:
    while True:
        try:
            _sync_project_jobs()
        except Exception:
            pass
        time.sleep(5)


threading.Thread(target=_job_worker_loop, daemon=True).start()
threading.Thread(target=directory_watcher_loop, daemon=True).start()


@app.post("/api/upload-file")
async def upload_file(
    file: UploadFile = File(...),
    stem_device: str = Form(DEFAULT_STEM_DEVICE),
    whisper_device: str = Form(DEFAULT_WHISPER_DEVICE),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    transcription_language: str = Form(DEFAULT_TRANSCRIPTION_LANGUAGE),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix and suffix not in SUPPORTED_AUDIO_EXTS:
        return JSONResponse(status_code=400, content={"message": f"Unsupported file type: {suffix}"})

    target = OUTPUT_DIR / f"upload_{uuid.uuid4().hex[:8]}{suffix or '.mp3'}"

    try:
        with target.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        await file.close()

    target, identity = _apply_canonical_media_name(target, raw_name=file.filename or "upload")
    target = _move_audio_into_project(target, identity["safe_stem"])

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
        return {"status": "queued", "message": f"Uploaded {target.name} and queued pipeline.", "job": job}
    return {"status": "busy", "message": f"Uploaded {target.name}. A pipeline job already exists for it."}


@app.post("/api/process-url")
def process_url(
    url: str = Form(...),
    engine: str = Form("ytdl"),
    stem_device: str = Form(DEFAULT_STEM_DEVICE),
    whisper_device: str = Form(DEFAULT_WHISPER_DEVICE),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    transcription_language: str = Form(DEFAULT_TRANSCRIPTION_LANGUAGE),
):
    url = (url or "").strip()
    if not url:
        return JSONResponse(status_code=400, content={"message": "URL is required."})

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return JSONResponse(status_code=400, content={"message": "Only http/https URLs are supported."})

    engine_norm = (engine or "ytdl").strip().lower()
    if engine_norm not in {"ytdl", "metube"}:
        return JSONResponse(status_code=400, content={"message": f"Unsupported engine: {engine}"})

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
        section="ingest",
        stage="download",
        details=f"Stem device: {stem_device}; Whisper device: {whisper_device}; Model: {whisper_model}; Language: {transcription_language}",
    )
    if engine_norm == "metube":
        return {"status": "queued", "message": "Queued MeTube download and pipeline processing.", "job": job}
    return {"status": "queued", "message": "Queued URL download and pipeline processing.", "job": job}


@app.post("/api/auto-grab-lyrics")
def auto_grab_lyrics(audio_filename: str = Form(...), provider: str = Form("lrclib")):
    try:
        audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
        rel_audio = _relative_to_output(audio_path)
        provider_norm = str(provider or "lrclib").strip().lower()
        if provider_norm not in {"lrclib", "genius", "syncedlyrics"}:
            provider_norm = "lrclib"

        job = _enqueue_job(
            "lyrics",
            f"Grab lyrics ({provider_norm}) for {audio_path.name}",
            "lyrics",
            section="ingest",
            stage="lyrics fetch",
            target_key=f"lyrics:{rel_audio}:{provider_norm}",
            audio_filename=rel_audio,
            project_name=audio_path.parent.name,
            lyrics_query=audio_path.stem,
            display_title=audio_path.stem,
            provider=provider_norm,
            details=f"Provider: {provider_norm}",
        )
        lrc_rel = _relative_to_output(audio_path.parent / f"{audio_path.stem}.lrc")
        return {
            "status": "queued",
            "message": f"Queued lyrics grab via {provider_norm} for {audio_path.name}.",
            "job": job,
            "filename": lrc_rel,
        }
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"message": f"Auto-grab lyrics failed: {exc}"})


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
        return {"status": "busy", "message": f"{audio_path.name} is already queued or being processed."}
    return {"status": "queued", "message": f"Queued auto-transcribe/sync for {audio_path.name}.", "job": job}


@app.post("/api/auto-correct-word-timing")
def auto_correct_word_timing(
    audio_filename: str = Form(...),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    whisper_device: str = Form(DEFAULT_WHISPER_DEVICE),
):
    try:
        audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    rel_audio = _relative_to_output(audio_path)
    job = _enqueue_job(
        "word_timing",
        f"AI word timing for {audio_path.name}",
        "word_timing",
        section="editor",
        stage="ai word align",
        target_key=f"word_timing:{rel_audio}",
        audio_filename=rel_audio,
        project_name=audio_path.parent.name,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
        details=f"Model: {whisper_model}; Preferred device: {whisper_device}",
    )
    return {"status": "queued", "message": f"Queued AI word timing correction for {audio_path.name}.", "job": job}


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
            job_id for job_id, job in JOBS.items()
            if (job.get("audio_filename") == rel_audio or job.get("project_name") == project_dir.name)
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
    return {"status": "success", "message": f"Deleted {audio_path.name} and related assets.", "deleted": deleted}


@app.post("/api/rename-media")
def rename_media(audio_filename: str = Form(...), new_name: str = Form(...)):
    try:
        audio_path = _resolve_output_file(audio_filename)
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    audio_path = _ensure_project_layout_for_audio(audio_path)
    old_project = audio_path.parent
    old_rel_audio = _relative_to_output(audio_path)

    proposed_project = _safe_project_name(new_name, fallback=old_project.name)
    if not proposed_project:
        return JSONResponse(status_code=400, content={"message": "Invalid project name."})

    target_project = OUTPUT_DIR / proposed_project
    if target_project.exists() and target_project.resolve() != old_project.resolve():
        return JSONResponse(status_code=409, content={"message": f"Project already exists: {proposed_project}"})

    with JOB_LOCK:
        related_ids = [
            job_id for job_id, job in JOBS.items()
            if (job.get("audio_filename") == old_rel_audio or job.get("project_name") == old_project.name)
            and job["status"] in {"queued", "running"}
        ]
    for job_id in related_ids:
        _cancel_job(job_id)

    if target_project.resolve() != old_project.resolve():
        old_project.rename(target_project)
    else:
        target_project = old_project

    legacy_proj = target_project / f"{old_project.name}.proj.json"
    renamed_proj = target_project / f"{target_project.name}.proj.json"
    if legacy_proj.exists() and legacy_proj != renamed_proj and not renamed_proj.exists():
        legacy_proj.rename(renamed_proj)

    audio_candidates = sorted(
        [path for path in target_project.iterdir() if _is_source_media(path)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not audio_candidates:
        return JSONResponse(status_code=500, content={"message": "Project rename succeeded but no media file was found."})
    target_audio = audio_candidates[0]
    new_rel_audio = _relative_to_output(target_audio)

    # Keep persisted project snapshot metadata aligned with the renamed project.
    if renamed_proj.exists() and renamed_proj.is_file():
        try:
            payload = json.loads(renamed_proj.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload["project_name"] = target_project.name
                payload["audio_filename"] = new_rel_audio
                renamed_proj.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    with JOB_LOCK:
        for job in JOBS.values():
            if job.get("audio_filename") == old_rel_audio:
                job["audio_filename"] = new_rel_audio
            if job.get("project_name") == old_project.name:
                job["project_name"] = target_project.name

            runner_kwargs = job.get("runner_kwargs")
            if isinstance(runner_kwargs, dict):
                if runner_kwargs.get("audio_filename") == old_rel_audio:
                    runner_kwargs["audio_filename"] = new_rel_audio
                if runner_kwargs.get("project_name") == old_project.name:
                    runner_kwargs["project_name"] = target_project.name

            target_key = job.get("target_key")
            if isinstance(target_key, str) and target_key:
                patched_key = target_key.replace(old_rel_audio, new_rel_audio).replace(old_project.name, target_project.name)
                job["target_key"] = patched_key

            for field in ("label", "message", "details"):
                value = job.get(field)
                if isinstance(value, str) and value:
                    job[field] = value.replace(old_project.name, target_project.name)

    return {
        "status": "success",
        "message": f"Renamed project to {target_project.name}.",
        "project_name": target_project.name,
        "audio_filename": new_rel_audio,
        "lrc_filename": _relative_to_output(target_project / f"{target_audio.stem}.lrc"),
        "audio_url": f"/files/{quote(new_rel_audio, safe='/')}",
    }

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
):
    """Converts standard LRC files into stylized ASS subtitles for FFmpeg rendering."""
    # Convert Web standard Hex (#RRGGBB) to ASS color format (&HBBGGRR&)
    def to_ass_color(hex_str):
        hex_str = hex_str.lstrip('#')
        r, g, b = hex_str[0:2], hex_str[2:4], hex_str[4:6]
        return f"&H00{b}{g}{r}&"

    p_color = to_ass_color(primary_hex)
    s_color = to_ass_color(secondary_hex)
    o_color = to_ass_color(outline_hex)
    main_margin_v = 0
    next_margin_v = 0
    spacing = max(0, int(word_padding // 2))

    default_shadow = 0
    default_outline = 5
    if text_effect == "shadow":
        default_shadow = 5
    elif text_effect == "hard-shadow":
        default_shadow = 9
    elif text_effect == "glow":
        default_shadow = 8
    elif text_effect == "neon":
        default_shadow = 12
        default_outline = 3

    # Base ASS Subtitle file formatting headers
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{p_color},{s_color},{o_color},&H00000000&,-1,0,0,0,100,100,{spacing},0,1,{default_outline},{default_shadow},2,10,10,120,1
Style: Upcoming,{font_name},{int(font_size*0.7)},{s_color},{s_color},{o_color},&H00000000&,0,-1,0,0,100,100,{spacing},0,1,3,2,2,10,10,{next_margin_v},1
"""
    ass_header = ass_header.replace(",2,10,10,120,1\n", f",5,10,10,{main_margin_v},1\n", 1)
    ass_header = ass_header.replace(",2,10,10,", ",5,10,10,")

    # Parse LRC lines. Support [mm:ss], [mm:ss.xx], and [mm:ss.xxx].
    lines = lrc_path.read_text(encoding="utf-8", errors="ignore").split('\n')
    events = ["[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    parsed_lines = []
    tag_regex = r"\[(\d+):(\d+)(?:\.(\d{1,3}))?\]"
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        tags = list(re.finditer(tag_regex, stripped))
        if not tags:
            continue
        text = re.sub(tag_regex, "", stripped).strip()
        if not text:
            continue
        for tag in tags:
            mins = int(tag.group(1))
            secs = int(tag.group(2))
            frac = tag.group(3) or "0"
            frac_norm = int((frac + "00")[:3])
            total_secs = (mins * 60) + secs + (frac_norm / 1000.0)
            parsed_lines.append((total_secs, text))

    parsed_lines.sort(key=lambda x: x[0])
    if not parsed_lines:
        # Fallback for plain (untimed) lyrics: create sequential synthetic timing
        plain_lines = [line.strip() for line in lines if line.strip()]
        cursor = 0.0
        for entry in plain_lines:
            parsed_lines.append((cursor, entry))
            cursor += 3.5

    # AI correction can generate one word per LRC line; regroup adjacent word-timed entries
    # into phrase lines for readable multi-word subtitles in final render.
    if len(parsed_lines) >= 8:
        single_word_lines = sum(1 for _, text in parsed_lines if len((text or "").split()) <= 1)
        single_ratio = single_word_lines / float(len(parsed_lines))
        if single_ratio >= 0.70:
            target_words = max(3, min(10, int(os.environ.get("ASS_WORD_GROUP_SIZE", "6"))))
            max_gap = max(0.15, float(os.environ.get("ASS_WORD_GROUP_MAX_GAP_SEC", "0.9")))
            regrouped: list[tuple[float, str]] = []
            i = 0
            while i < len(parsed_lines):
                start_t = parsed_lines[i][0]
                words: list[str] = []
                j = i
                while j < len(parsed_lines) and len(words) < target_words:
                    token_time, token_text = parsed_lines[j]
                    token = (token_text or "").strip()
                    if not token:
                        j += 1
                        continue
                    if words and (token_time - parsed_lines[j - 1][0]) > max_gap:
                        break
                    token_words = token.split()
                    if words and len(token_words) > 1:
                        break
                    words.extend(token_words)
                    j += 1
                if words:
                    regrouped.append((start_t, " ".join(words)))
                i = max(j, i + 1)

            if regrouped and len(regrouped) < len(parsed_lines):
                logger.info(
                    "[ASS] regrouped one-word lines %s -> %s for %s",
                    len(parsed_lines),
                    len(regrouped),
                    lrc_path.name,
                )
                parsed_lines = regrouped
    logger.info("[ASS] parsed timed lyric lines=%s from %s", len(parsed_lines), lrc_path.name)

    def _format_ass_time(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def _karaoke_text_for_line(text: str, start_t: float, end_t: float) -> str:
        # Use ASS \k timing so render output visibly transitions Secondary->Primary per word.
        words = [w for w in (text or "").split() if w]
        if len(words) <= 1:
            return text or ""

        total_cs = max(20, int(round(max(0.20, end_t - start_t) * 100.0)))
        base_cs = max(1, total_cs // len(words))
        rem = max(0, total_cs - (base_cs * len(words)))
        chunks = []
        for idx, word in enumerate(words):
            word_cs = base_cs + (1 if idx < rem else 0)
            chunks.append(r"{\k" + str(word_cs) + "}" + word)
        return " ".join(chunks)

    # Generate ASS event blocks with transition/mode controls mapped from preview settings.
    speed_ms = max(80, min(1800, int(float(fx_speed or 0.6) * 1000)))
    visible_lines = max(1, min(6, int(preview_line_count or 3)))
    show_upcoming = reveal_mode in {"block", "eager"}
    upcoming_count = (visible_lines - 1) if show_upcoming else 0
    line_height = max(30, int((font_size * 1.1) + max(0, line_spacing)))

    for i in range(len(parsed_lines)):
        start_time = parsed_lines[i][0]
        # End event when the next line kicks in, or default to 5 seconds later
        end_time = parsed_lines[i+1][0] if i+1 < len(parsed_lines) else start_time + 5.0

        start_str = _format_ass_time(start_time)
        end_str = _format_ass_time(end_time)
        text = parsed_lines[i][1]
        text_payload = _karaoke_text_for_line(text, start_time, end_time)

        # Inject selected render effect directives
        effect_mod = ""
        if transition_style == "fade":
            effect_mod = rf"{{\fad({speed_ms},{speed_ms})}}"
        elif transition_style == "pop":
            effect_mod = rf"{{\fscX112\fscY112\t(0,{speed_ms},\fscX100\fscY100)}}"
        elif transition_style == "slide":
            effect_mod = rf"{{\move(960,960,960,540,0,{speed_ms})}}"
        elif transition_style == "zoom":
            effect_mod = rf"{{\fscX84\fscY84\t(0,{speed_ms},\fscX100\fscY100)}}"
        elif transition_style == "drop":
            effect_mod = rf"{{\move(960,300,960,540,0,{speed_ms})}}"
        elif transition_style == "blur":
            effect_mod = rf"{{\blur6\t(0,{speed_ms},\blur0)}}"

        if reveal_mode == "continuous" and transition_style in {"slide", "drop"}:
            # Continuous mode already uses \move for vertical scrolling.
            effect_mod = ""

        if fx_scope == "line":
            effect_mod = effect_mod
        elif fx_scope == "page" and effect_mod:
            effect_mod = effect_mod

        total_visible_now = 1 + min(upcoming_count, max(0, len(parsed_lines) - (i + 1)))
        top_y = 540 - int(((total_visible_now - 1) * line_height) / 2)
        current_y = top_y
        pos_tag = rf"\an5\pos(960,{current_y})"
        if reveal_mode == "continuous":
            scroll_span = max(line_height * 2, int(line_height * (visible_lines + 0.5)))
            scroll_start_y = 540 + (scroll_span // 2)
            scroll_end_y = 540 - (scroll_span // 2)
            scroll_ms = max(1, int((end_time - start_time) * 1000))
            pos_tag = rf"\an5\move(960,{scroll_start_y},960,{scroll_end_y},0,{scroll_ms})"

        events.append(
            f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{{pos_tag}}}{effect_mod}{text_payload}"
        )

        # Display upcoming lines based on reveal mode (block/eager only).
        if show_upcoming and transition_style != "scroll":
            for offset in range(1, upcoming_count + 1):
                if i + offset >= len(parsed_lines):
                    break
                next_text = parsed_lines[i + offset][1]
                next_y = top_y + (offset * line_height)
                events.append(
                    f"Dialogue: 1,{start_str},{end_str},Upcoming,,0,0,0,,{{\\an5\\pos(960,{next_y})}}{next_text}"
                )

    ass_path.write_text(ass_header + "\n" + "\n".join(events), encoding="utf-8")

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
    output_filename: str = "",
    render_token: str = "",
):
    """Executes hardware-accelerated 1080p video render using NVIDIA NVENC."""
    audio_path = _resolve_output_file(audio_filename)
    audio_path = _ensure_project_layout_for_audio(audio_path)
    project_dir = audio_path.parent
    base_name = audio_path.stem
    lrc_path = project_dir / f"{base_name}.lrc"
    ass_path = project_dir / f"{base_name}.ass"
    project_label = _safe_output_name(project_dir.name, fallback_stem=base_name)
    stamp = str(render_token or datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f"))
    mode_prefix = f"{project_label}_preview_karaoke_" if use_preview_audio else f"{project_label}_karaoke_"
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

    # Preview renders should use original vocal audio; final export prefers accompaniment.
    minus_track = project_dir / f"{base_name}_minus.mp3"
    demucs_no_vocals = project_dir / "stems" / "htdemucs" / base_name / "no_vocals.wav"
    render_audio_path = audio_path
    if not use_preview_audio:
        if minus_track.exists() and minus_track.is_file():
            render_audio_path = minus_track
        elif demucs_no_vocals.exists() and demucs_no_vocals.is_file():
            render_audio_path = demucs_no_vocals
    logger.info("[RENDER AUDIO] using %s for %s", render_audio_path.name, audio_path.name)

    # 1. Compile custom styled Subtitle asset mapping
    lrc_to_ass(
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
    )

    # 2. Build FFmpeg command stack targeting GTX 1070 NVENC cores
    # Default fallback video background container template mapping
    bg_kind = (bg_type or "color").strip().lower()
    if bg_kind in {"color", "gradient", "spiral"}:
        video_source = ["-f", "lavfi", "-i", f"color=c={bg_color.lstrip('#')}:s=1920x1080:r=30"]
    else:
        bg_img = project_dir / "custom_bg.jpg"
        if not bg_img.exists():
            bg_img = OUTPUT_DIR / "custom_bg.jpg"
        video_source = ["-loop", "1", "-i", str(bg_img)]

    vf_filters = []
    if bg_kind == "gradient":
        r1, g1, b1 = _hex_to_rgb(bg_color)
        r2, g2, b2 = _hex_to_rgb(outline_color)
        vf_filters.append(
            "geq="
            f"r='{r1}+({r2}-{r1})*Y/H':"
            f"g='{g1}+({g2}-{g1})*Y/H':"
            f"b='{b1}+({b2}-{b1})*Y/H'"
        )
    elif bg_kind == "spiral":
        r1, g1, b1 = _hex_to_rgb(bg_color)
        r2, g2, b2 = _hex_to_rgb(primary_color)
        vf_filters.append(
            "geq="
            "lum='255':"
            f"r='({r1})+(({r2}-{r1})*min(1,sqrt((X-W/2)^2+(Y-H/2)^2)/(0.78*H)))':"
            f"g='({g1})+(({g2}-{g1})*min(1,sqrt((X-W/2)^2+(Y-H/2)^2)/(0.78*H)))':"
            f"b='({b1})+(({b2}-{b1})*min(1,sqrt((X-W/2)^2+(Y-H/2)^2)/(0.78*H)))'"
        )

    vf_filters.append(f"ass='{_escape_filter_path(ass_path)}'")

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
    cmd.extend([
        *_render_video_encoder_args(render_device),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_video_path),
    ])

    if job_id:
        _run_cancellable_command(job_id, cmd, f"Creating Karaoke video of {audio_filename}", 30, 95)
    else:
        subprocess.run(cmd, check=True)

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
    reveal_mode: str = Form("block"),
    preview_line_count: int = Form(3),
    pitch: float = Form(1.0),
    volume: float = Form(1.0),
    render_device: str = Form(DEFAULT_RENDER_DEVICE),
    use_preview_audio: bool = Form(False),
):
    rel_audio = audio_filename
    try:
        audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
        project_name = audio_path.parent.name
        rel_audio = _relative_to_output(audio_path)
    except Exception:
        project_name = (Path(audio_filename).parts[0] if "/" in str(audio_filename) else "")

    safe_project = _safe_output_name(project_name, fallback_stem=Path(rel_audio).stem or "project")
    render_token = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    mode_prefix = f"{safe_project}_preview_karaoke_" if use_preview_audio else f"{safe_project}_karaoke_"
    output_filename = f"{mode_prefix}{render_token}.mp4"

    job = _enqueue_job(
        "render",
        f"Render {rel_audio}",
        "render",
        section="render",
        stage="Creating Karaoke Video",
        target_key=f"render:{rel_audio}:{int(use_preview_audio)}",
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
        render_device=render_device,
        use_preview_audio=use_preview_audio,
        output_filename=output_filename,
        render_token=render_token,
        details=(
            f"Render device: {render_device}; Pitch: {pitch}; Volume: {volume}; "
            f"Font: {font_name}; Size: {font_size}; BG: {bg_type}; FX: {transition_style}/{text_effect}; "
            f"Mode: {reveal_mode}; Speed: {fx_speed}; Scope: {fx_scope}; PreviewAudio: {use_preview_audio}; "
            f"Output: {output_filename}"
        ),
    )
    return {"status": "queued", "message": "Queued FFmpeg render job.", "job": job}


@app.get("/api/jobs")
def list_jobs(project_name: str = ""):
    with JOB_LOCK:
        jobs = [_job_view(job) for job in sorted(JOBS.values(), key=lambda item: item["created_at"], reverse=True)]
    if project_name:
        jobs = [job for job in jobs if job.get("project_name") == project_name]
    return {"jobs": jobs}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = _cancel_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"message": f"Job not found: {job_id}"})
    return {"status": "success", "message": f"Cancellation requested for {job['label']}", "job": job}

@app.get("/api/get-fonts")
def get_fonts():
    return {"fonts": _refresh_font_cache()}


@app.get("/api/themes")
def get_themes():
    return {"themes": _load_theme_catalog()}


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
        return JSONResponse(status_code=500, content={"message": f"Write access failure: {exc}"})

@app.get("/api/load-lyrics")
def load_lyrics(filename: str):
    p = _resolve_output_path(filename, require_exists=False)
    return {"content": p.read_text(encoding="utf-8") if p.exists() else ""}

@app.get("/api/list-files")
def list_files(sources_only: bool = False):
    projects = _list_project_manifests()
    if sources_only:
        projects = [item for item in projects if item.get("audio_filename")]
    return {"files": projects}


@app.get("/api/load-project-state")
def load_project_state(audio_filename: str):
    try:
        audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    project_dir = audio_path.parent
    proj_path = project_dir / f"{project_dir.name}.proj.json"
    if not proj_path.exists():
        alt = sorted(project_dir.glob("*.proj.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if alt:
            proj_path = alt[0]
    if not proj_path.exists():
        return {"status": "missing", "project_name": project_dir.name, "state": {}}

    try:
        state = json.loads(proj_path.read_text(encoding="utf-8"))
        return {"status": "success", "project_name": project_dir.name, "state": state}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"message": f"Failed to load project state: {exc}"})


@app.post("/api/save-project-state")
def save_project_state(audio_filename: str = Form(...), state_json: str = Form(...)):
    try:
        audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"message": str(exc)})

    project_dir = audio_path.parent
    proj_path = project_dir / f"{project_dir.name}.proj.json"
    try:
        parsed = json.loads(state_json)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"message": f"Invalid state JSON: {exc}"})

    parsed["project_name"] = project_dir.name
    parsed["audio_filename"] = _relative_to_output(audio_path)
    parsed["saved_at"] = int(time.time())
    proj_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    global_bg = OUTPUT_DIR / "custom_bg.jpg"
    if parsed.get("preview", {}).get("bgType") == "image" and global_bg.exists():
        try:
            shutil.copy2(global_bg, project_dir / "custom_bg.jpg")
        except Exception:
            pass

    return {"status": "success", "message": f"Saved project state to {proj_path.name}.", "project_name": project_dir.name}

@app.post("/api/upload-bg")
def upload_bg(file: UploadFile = File(...), audio_filename: str = Form("")):
    target = OUTPUT_DIR / "custom_bg.jpg"
    if audio_filename:
        try:
            audio_path = _ensure_project_layout_for_audio(_resolve_output_file(audio_filename))
            target = audio_path.parent / "custom_bg.jpg"
        except FileNotFoundError:
            pass
    with target.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "success"}

@app.get("/", response_class=HTMLResponse)
def index_page():
    return (WORKSPACE / "index.html").read_text()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
