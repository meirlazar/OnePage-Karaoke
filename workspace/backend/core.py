import concurrent.futures
import ctypes
import gc
import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import cdg_karaoke

# ===============================================================
# SECTION: Application Setup & Configuration
# Purpose: Initialize FastAPI app, set up directories, static file
#          mounting, logging, and global state management
# ===============================================================

app = FastAPI(title="AI Audio Video Production Suite")
# This module lives in <project root>/backend/. WORKSPACE is the project root so
# runtime content dirs (output/fonts/icons) resolve there, while the browser-facing
# assets (index.html, static/, themes/) live under <project root>/frontend/.
_BACKEND_DIR = Path(__file__).resolve().parent
WORKSPACE = Path(
    os.environ.get("WORKSPACE_DIR", _BACKEND_DIR.parent)
).resolve()
FRONTEND_DIR = WORKSPACE / "frontend"
OUTPUT_DIR = WORKSPACE / "output"
FONTS_DIR = WORKSPACE / "fonts"
SERVED_FONTS_DIR = WORKSPACE / ".served-fonts"
THEMES_DIR = FRONTEND_DIR / "themes"
ICONS_DIR = WORKSPACE / "icons"
STATIC_DIR = FRONTEND_DIR / "static"

FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)
SERVED_FONTS_DIR.mkdir(parents=True, exist_ok=True)
THEMES_DIR.mkdir(parents=True, exist_ok=True)
ICONS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------
# Media Vault profiles: projects live under output/<profile>/<project>/.
# A JSON registry tracks known profile names and the active profile so the
# whole app agrees on where new projects are placed and which ones to list.
# ---------------------------------------------------------------
DEFAULT_PROFILE = "Default"
PROFILES_FILE = OUTPUT_DIR / ".profiles.json"
# Top-level names under output/ that are NOT profiles (legacy/system folders).
RESERVED_OUTPUT_NAMES = {"stems"}

app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")
app.mount("/fonts", StaticFiles(directory=str(SERVED_FONTS_DIR)), name="fonts")
app.mount("/themes", StaticFiles(directory=str(THEMES_DIR)), name="themes")
app.mount("/icons", StaticFiles(directory=str(ICONS_DIR)), name="icons")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


logger = logging.getLogger("onepage-karaoke")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

ACTIVE_PROCESSING_CACHE = set()
JOB_LOCK = threading.Lock()
JOB_QUEUE = deque()
JOBS = {}
RUNNING_PROCESSES = {}
RENAME_LOCK = threading.RLock()
PENDING_PROJECT_RENAMES: dict[str, str] = {}
# While the user is hand-timing lyrics, pause automatic ingest/lyrics jobs until this epoch.
# Stored in a mutable holder (not a bare module global) so the value stays shared
# across the split modules that import it via ``from core import *``.
_TIMING_STATE: dict[str, float] = {"until": 0.0}
# Maps a job's ``runner`` name to the callable that executes it. Feature modules
# register their runners here at import time; the worker loop dispatches via this map.
JOB_RUNNERS: dict = {}
SUPPORTED_AUDIO_EXTS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".aac",
    ".webm",
    ".mp4",
}
METUBE_URL = "http://metube:8081"
# Pulling (source ingest + lyrics fetch) is a MANUAL-ONLY action: it must be triggered
# from the UI buttons (Pull / Pull Lyrics / Upload). The background project watcher must
# never auto-enqueue ingest or lyrics jobs on its own. Flip to True to restore the old
# automatic behavior.
AUTO_PULL_ENABLED = False
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
# Optional per-word end tag, e.g. [00:12.34]word<00:12.90>, used for finish-and-hold timing.
_WORD_END_TAG_RE = re.compile(r"<\d{1,3}:\d{1,2}(?:\.\d{1,3})?>")


# ===============================================================
# SECTION: Exception Classes & Job Management
# Purpose: Define custom exceptions, error handling, and job
#          status tracking for async operations
# ===============================================================


class JobCancelledError(RuntimeError):
    pass


# ===============================================================
# SECTION: Memory & Resource Management
# Purpose: Clean up heap memory, garbage collection, and
#          resource allocation for long-running processes
# ===============================================================


def _trim_process_heap() -> None:
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except Exception:
        # Not all libc implementations expose malloc_trim; ignore silently.
        pass


def _release_runtime_resources(reason: str = "") -> None:
    # Full-generation collect so unreachable objects (and their swapped-out pages) are freed immediately.
    gc.collect(2)

    # If CUDA is active, return cached pages to the driver so nvidia-smi reflects the real usage.
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
            if hasattr(torch.cuda, "reset_peak_memory_stats"):
                torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass

    # Hand freed heap pages back to the OS so RSS/swap pressure drops right away.
    _trim_process_heap()
    if reason:
        logger.info("[RESOURCE CLEANUP] %s", reason)


# Endpoints that are polled frequently by the UI; skip the (relatively costly) cleanup
# pass after these so active-job/status polling doesn't thrash the allocator/GC.
_CLEANUP_SKIP_PATH_PREFIXES = ("/files/", "/fonts/", "/themes/")


@app.middleware("http")
async def _cleanup_after_every_request(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if not path.startswith(_CLEANUP_SKIP_PATH_PREFIXES):
        _release_runtime_resources()
    return response


def _job_output_url(job: dict) -> str:
    """Build a /files download URL for a completed job's output, if any."""
    out_name = str(job.get("output_filename", "") or "").strip()
    if not out_name:
        return ""
    audio_rel = str(job.get("audio_filename", "") or "").strip()
    parent = Path(audio_rel).parent.as_posix() if audio_rel else ""
    rel = f"{parent}/{out_name}" if parent and parent not in (".", "") else out_name
    return "/files/" + quote(rel)


def _job_view(job: dict) -> dict:
    queue_position = 0
    if job.get("status") == "queued":
        queued_ids = [
            job_id
            for job_id in JOB_QUEUE
            if JOBS.get(job_id, {}).get("status") == "queued"
        ]
        try:
            queue_position = queued_ids.index(job["id"]) + 1
        except ValueError:
            queue_position = 0
    return {
        "id": job["id"],
        "type": job["type"],
        "section": job.get("section", job["type"]),
        "stage": job.get("stage", ""),
        "label": job["label"],
        "status": job["status"],
        "queue_position": queue_position,
        "progress": job["progress"],
        "message": job["message"],
        "created_at": job["created_at"],
        "audio_filename": job.get("audio_filename", ""),
        "project_name": job.get("project_name", ""),
        "cancel_requested": job.get("cancel_requested", False),
        "device": job.get("device", ""),
        "stem_device": job.get("stem_device", ""),
        "whisper_device": job.get("whisper_device", ""),
        "render_device": job.get("render_device", ""),
        "whisper_model": job.get("whisper_model", ""),
        "timing_mode": job.get("timing_mode", ""),
        "max_offset_seconds": job.get("max_offset_seconds", 0),
        "transcription_language": job.get("transcription_language", ""),
        "pitch": job.get("pitch", 1),
        "volume": job.get("volume", 1),
        "details": job.get("details", ""),
        "output_filename": job.get("output_filename", ""),
        "output_url": _job_output_url(job),
        "render_width": job.get("render_width", 0),
        "render_height": job.get("render_height", 0),
        "render_resolution": job.get("render_resolution", ""),
        "render_source": job.get("render_source", ""),
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
            logger.warning(
                "[CUDA CHECK] CUDA visible but cuDNN runtime libs are missing. Falling back to CPU."
            )
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
        raw = (
            str(os.environ.get("WHISPER_COMPUTE_TYPE_CUDA", "int8_float16"))
            .strip()
            .lower()
            or "int8_float16"
        )
        allowed = {"float16", "int8_float16", "float32"}
        if raw not in allowed:
            logger.warning(
                "[FASTER WHISPER] Unsupported CUDA compute_type '%s', falling back to int8_float16.",
                raw,
            )
            return "int8_float16"
        return raw

    raw = (
        str(os.environ.get("WHISPER_COMPUTE_TYPE_CPU", "int8")).strip().lower()
        or "int8"
    )
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
        for word in getattr(seg, "words", None) or []:
            start = getattr(word, "start", None)
            end = getattr(word, "end", None)
            words.append(
                {
                    "word": str(getattr(word, "word", "") or "").strip(),
                    "start": float(start) if isinstance(start, (int, float)) else None,
                    "end": float(end) if isinstance(end, (int, float)) else None,
                    "probability": float(getattr(word, "probability", 0.0) or 0.0),
                }
            )
        payload_segments.append(
            {
                "id": idx,
                "start": float(getattr(seg, "start", 0.0) or 0.0),
                "end": float(getattr(seg, "end", 0.0) or 0.0),
                "text": str(getattr(seg, "text", "") or "").strip(),
                "words": [item for item in words if item["word"]],
            }
        )
    return {
        "language": str(getattr(info, "language", "") or ""),
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "segments": payload_segments,
    }


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
        res = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if res.returncode != 0:
            return "", ""
        payload = json.loads(res.stdout or "{}")
        tags = payload.get("format", {}).get("tags", {}) or {}
        lowered = {str(key).lower(): str(value).strip() for key, value in tags.items()}
        return lowered.get("artist", ""), lowered.get("title", "")
    except Exception:
        return "", ""


def _probe_media_duration(path: Path) -> float:
    ffprobe_bin = os.environ.get("FFPROBE_BIN", "/usr/bin/ffprobe")
    try:
        res = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return (
            max(0.0, float((res.stdout or "0").strip())) if res.returncode == 0 else 0.0
        )
    except Exception:
        return 0.0


def _derive_media_identity(
    *,
    path: Path | None = None,
    raw_name: str = "",
    artist: str = "",
    title: str = "",
    fallback_stem: str = "track",
) -> dict:
    fallback = (
        _clean_title(raw_name or (path.stem if path else fallback_stem))
        or fallback_stem
    )
    detected_artist = artist.strip()
    detected_title = title.strip()

    if path and (not detected_artist or not detected_title):
        tag_artist, tag_title = _probe_media_tags(path)
        if not detected_artist:
            detected_artist = tag_artist.strip()
        if not detected_title:
            detected_title = tag_title.strip()

    parsed_artist, parsed_title = _split_artist_title(
        _clean_title(fallback) or fallback
    )
    if not detected_artist:
        detected_artist = parsed_artist
    if not detected_title:
        detected_title = parsed_title or fallback

    detected_artist = _clean_title(detected_artist)
    detected_title = _clean_title(detected_title) or fallback_stem
    display = (
        f"{detected_artist} - {detected_title}"
        if detected_artist and detected_title
        else detected_title
    )
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
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and (res.stdout or "").strip():
            meta = json.loads(res.stdout)
            raw_title = str(meta.get("track") or meta.get("title") or fallback)
            artist = str(
                meta.get("artist")
                or meta.get("album_artist")
                or meta.get("creator")
                or meta.get("uploader")
                or ""
            )
            return _derive_media_identity(
                raw_name=raw_title,
                artist=artist,
                title=raw_title,
                fallback_stem=fallback,
            )
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
    identity = _derive_media_identity(
        path=path, raw_name=raw_name or path.stem, artist=artist, title=title
    )
    target = _unique_output_path(
        identity["safe_stem"], path.suffix or ".mp3", current_name=path.name
    )
    if target != path:
        path.rename(target)
        logger.info("[MEDIA RENAME] %s -> %s", path.name, target.name)
        path = target
    return path, identity


def _choose_best_identity(downloaded: Path, expected: dict) -> dict:
    actual = _derive_media_identity(path=downloaded, raw_name=downloaded.stem)
    expected_safe = str(expected.get("safe_stem", "") or "").strip().lower()
    weak_expected = expected_safe.startswith("download_") or expected_safe in {
        "watch",
        "download",
        "video",
    }
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


def _move_audio_into_project(
    audio_path: Path, project_name: str, profile: str = ""
) -> Path:
    safe_project = _safe_project_name(project_name, fallback=audio_path.stem)
    project_dir = _profile_root(profile) / safe_project
    if not project_dir.exists():
        project_dir.mkdir(parents=True, exist_ok=True)
    elif project_dir.is_file():
        raise RuntimeError(f"Cannot create project directory: {project_dir}")

    target_name = f"{safe_project}{audio_path.suffix or '.mp3'}"
    target = project_dir / target_name
    if target.exists() and target.resolve() != audio_path.resolve():
        counter = 2
        while (
            project_dir / f"{safe_project}_{counter}{audio_path.suffix or '.mp3'}"
        ).exists():
            counter += 1
        target = project_dir / f"{safe_project}_{counter}{audio_path.suffix or '.mp3'}"

    if target.resolve() != audio_path.resolve():
        audio_path.rename(target)
    return target


def _wait_for_new_media_file(
    job_id: str, known_files: set[str], timeout_seconds: int = 1800
) -> Path:
    seen_sizes: dict[str, int] = {}
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        _ensure_not_cancelled(job_id)
        for file_path in sorted(
            OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            if not _is_source_media(file_path) or file_path.name in known_files:
                continue
            size = file_path.stat().st_size
            if size > 0 and seen_sizes.get(file_path.name) == size:
                return file_path
            seen_sizes[file_path.name] = size
        _update_job(
            job_id, progress=30, message="Waiting for MeTube download to finish"
        )
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
                    except Exception as exc:
                        logger.warning("Failed to copy font: %s", exc)
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


def _resolve_render_font_family(font_name: str) -> str:
    """Resolve the browser-facing filename stem to the font's embedded family name."""
    requested = str(font_name or "Arial").strip()
    for font_path in SERVED_FONTS_DIR.iterdir():
        if font_path.stem.lower() != requested.lower() or not font_path.is_file():
            continue
        try:
            probe = subprocess.run(
                ["fc-scan", "--format=%{family}", str(font_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            family = (probe.stdout or "").splitlines()[0].strip()
            if family:
                return family
        except (OSError, subprocess.SubprocessError):
            break
    return requested


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
            issues.append(
                {
                    "kind": "syntax",
                    "severity": "error",
                    "message": str(err.msg or err),
                    "file": _workspace_relative_path(path),
                    "line": int(err.lineno or 0),
                    "column": column,
                    "code": line_text,
                    "pointer": (" " * max(0, column - 1) + "^") if column else "",
                }
            )
        except Exception as exc:
            issues.append(
                {
                    "kind": "syntax",
                    "severity": "error",
                    "message": str(exc),
                    "file": _workspace_relative_path(path),
                    "line": 0,
                    "column": 0,
                    "code": "",
                    "pointer": "",
                }
            )
    return issues


def _collect_runtime_issues() -> list[dict]:
    issues = []
    with JOB_LOCK:
        failed_jobs = [job for job in JOBS.values() if job.get("status") == "failed"]
    for job in sorted(
        failed_jobs, key=lambda item: item.get("updated_at", 0), reverse=True
    )[:12]:
        issues.append(
            {
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
            }
        )
    return issues


def _load_theme_catalog() -> list[dict]:
    themes = []
    skipped = 0
    files = sorted(THEMES_DIR.glob("*.json"))
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped += 1
            logger.warning("[THEMES] Skipped unreadable theme %s: %s", path.name, exc)
            continue
        if not isinstance(payload, dict):
            skipped += 1
            logger.warning("[THEMES] Skipped %s: JSON root is not an object.", path.name)
            continue
        theme_id = str(payload.get("id") or path.stem)
        vars_payload = payload.get("vars") or {}
        if not isinstance(vars_payload, dict) or not vars_payload:
            skipped += 1
            logger.warning(
                "[THEMES] Skipped %s: missing/empty 'vars' object (theme files need a "
                "\"vars\": { \"bg-base\": \"#...\", ... } map).",
                path.name,
            )
            continue
        themes.append(
            {
                "id": theme_id,
                "name": str(payload.get("name") or theme_id),
                "description": str(payload.get("description") or ""),
                "source_name": str(payload.get("source_name") or ""),
                "source_url": str(payload.get("source_url") or ""),
                "license": str(payload.get("license") or ""),
                "vars": vars_payload,
            }
        )
    logger.info(
        "[THEMES] %s theme(s) loaded, %s skipped from %s",
        len(themes), skipped, THEMES_DIR,
    )
    return themes


def _cmd_text(cmd: list[str]) -> str:
    return " ".join(str(part) for part in cmd)


def _find_active_job_by_key(target_key: str) -> dict | None:
    for job in JOBS.values():
        if job.get("target_key") == target_key and job["status"] in {
            "queued",
            "running",
        }:
            return job
    return None


def _has_active_job_for_audio(rel_audio: str, project_name: str = "") -> bool:
    # Snapshot under the lock: worker threads mutate JOBS concurrently, so iterating
    # it unlocked risks a "dictionary changed size during iteration" crash.
    with JOB_LOCK:
        jobs_snapshot = list(JOBS.values())
    for job in jobs_snapshot:
        if job.get("status") not in {"queued", "running"}:
            continue
        if rel_audio and job.get("audio_filename") == rel_audio:
            return True
        if project_name and job.get("project_name") == project_name:
            return True
    return False


def _enqueue_job(
    kind: str,
    label: str,
    runner: str,
    *,
    section: str = "general",
    stage: str = "",
    target_key: str = "",
    details: str = "",
    **kwargs,
) -> dict:
    # A pending rename holds this lock through the filesystem move, so jobs
    # either keep using the original directory or start after its final name.
    with RENAME_LOCK:
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
                "timing_mode": kwargs.get("timing_mode", ""),
                "max_offset_seconds": kwargs.get("max_offset_seconds", 0),
                "transcription_language": kwargs.get("transcription_language", ""),
                "pitch": kwargs.get("pitch", 1),
                "volume": kwargs.get("volume", 1),
                "details": details,
                "output_filename": kwargs.get("output_filename", ""),
            }
            JOBS[job_id] = job
            JOB_QUEUE.append(job_id)
            logger.info(
                "[QUEUE] queued job=%s type=%s runner=%s section=%s stage=%s target=%s",
                job_id,
                kind,
                runner,
                section,
                stage,
                target_key or "-",
            )
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
    logger.info(
        "[QUEUE] cancellation requested job=%s label=%s status=%s",
        job_id,
        job.get("label", "-"),
        job.get("status", "-"),
    )
    _terminate_running_process(job_id)
    return _job_view(job)


def _run_cancellable_command(
    job_id: str,
    cmd: list[str],
    message: str,
    start_progress: int,
    end_progress: int,
    total_duration: float = 0.0,
) -> None:
    _ensure_not_cancelled(job_id)
    logger.info("[PROC START] job=%s %s | %s", job_id, message, _cmd_text(cmd))
    _update_job(job_id, status="running", message=message, progress=start_progress)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
    )
    stderr_tail = deque(maxlen=40)
    latest_media_time = [0.0]

    def drain_stderr() -> None:
        if not proc.stderr:
            return
        for line in proc.stderr:
            stripped = line.rstrip()
            if stripped:
                stderr_tail.append(stripped)
            match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
            if match:
                latest_media_time[0] = (
                    float(match.group(1)) * 3600
                    + float(match.group(2)) * 60
                    + float(match.group(3))
                )

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()
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
            if total_duration > 0 and latest_media_time[0] > 0:
                ratio = min(1.0, latest_media_time[0] / total_duration)
                measured_progress = int(
                    start_progress + ((end_progress - start_progress) * ratio)
                )
                _update_job(job_id, progress=min(measured_progress, pulse_cap))
            elif pulse_progress < pulse_cap and (now - last_pulse_at) >= 1.5:
                # Keep UI responsive for commands that do not emit parseable progress.
                pulse_progress += 1
                _update_job(job_id, progress=pulse_progress)
                last_pulse_at = now
            time.sleep(0.5)
        stderr_thread.join(timeout=2)
        stderr_output = "\n".join(stderr_tail)
        if proc.returncode != 0:
            logger.error(
                "[PROC FAIL] job=%s code=%s cmd=%s\n%s",
                job_id,
                proc.returncode,
                _cmd_text(cmd),
                (stderr_output or "")[-2000:],
            )
            raise RuntimeError(
                (stderr_output or f"Command failed: {' '.join(cmd)}").strip()[-2000:]
            )
        logger.info("[PROC OK] job=%s code=0 cmd=%s", job_id, _cmd_text(cmd))
        _update_job(job_id, progress=end_progress)
    finally:
        with JOB_LOCK:
            RUNNING_PROCESSES.pop(job_id, None)


def _sync_project_jobs() -> None:
    # Pulling is manual-only: ingest/lyrics jobs are started exclusively from the UI
    # buttons (Pull / Pull Lyrics / Upload). Never auto-enqueue from the watcher.
    if not AUTO_PULL_ENABLED:
        return
    # Don't auto-enqueue ingest/lyrics jobs while the user is actively hand-timing.
    if time.time() < _TIMING_STATE["until"]:
        return
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
            _start_pipeline_if_idle(
                audio_path,
                project_name,
                display_title=project_name,
                lyrics_query=project_name,
            )
            continue

        # An untimed, nonempty LRC is a valid user-editable lyrics revision (for
        # example after Clear All Timing), not a request to repeatedly auto-fetch.
        if (
            manifest["has_stems"]
            and not manifest["has_timed_lyrics"]
            and not manifest["has_lyrics_content"]
        ):
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
                next_job = JOBS.get(JOB_QUEUE.popleft())
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
            runner_name = job["runner"]
            runner_fn = JOB_RUNNERS.get(runner_name)
            if runner_fn is None:
                raise RuntimeError(f"Unknown job runner: {runner_name}")
            logger.info("[WORKER] start job=%s runner=%s", job["id"], runner_name)
            runner_fn(job["id"], **job["runner_kwargs"])

            current = JOBS.get(job["id"], {})
            if current.get("status") == "running":
                # Only update status to completed; preserve any message set by job runner
                # (e.g., "Lyrics ready via syncedlyrics - syllable-level (487 timestamps)")
                _update_job(job["id"], status="completed", progress=100)
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
            _finalize_pending_project_renames()


def _safe_output_name(name: str, fallback_stem: str = "track") -> str:
    stem = Path(name or fallback_stem).stem.strip() or fallback_stem
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .")
    return cleaned or fallback_stem


def _safe_project_name(name: str, fallback: str = "project") -> str:
    return _safe_output_name(name, fallback_stem=fallback)


def _relative_to_output(path: Path) -> str:
    return path.resolve().relative_to(OUTPUT_DIR.resolve()).as_posix()


# ===============================================================
# SECTION: Media Vault Profiles
# Purpose: Manage per-user profile folders under output/ so each profile
#          only sees its own projects, and songs can be copied/moved between
#          profiles. Layout: output/<profile>/<project>/<files>.
# ===============================================================


def _safe_profile_name(name: str) -> str:
    return _safe_output_name(name, fallback_stem=DEFAULT_PROFILE)


def _load_profiles_registry() -> dict:
    data = {}
    if PROFILES_FILE.exists():
        try:
            data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    raw_profiles = data.get("profiles")
    profiles: list[str] = []
    if isinstance(raw_profiles, list):
        for entry in raw_profiles:
            safe = _safe_profile_name(str(entry))
            if safe and safe not in profiles:
                profiles.append(safe)
    if DEFAULT_PROFILE not in profiles:
        profiles.insert(0, DEFAULT_PROFILE)
    active = _safe_profile_name(str(data.get("active") or ""))
    if active not in profiles:
        active = DEFAULT_PROFILE
    return {"profiles": profiles, "active": active}


def _save_profiles_registry(reg: dict) -> None:
    try:
        PROFILES_FILE.write_text(
            json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        logger.warning("[PROFILES] could not persist registry to %s", PROFILES_FILE)


def _list_profiles() -> list[str]:
    return _load_profiles_registry()["profiles"]


def _active_profile() -> str:
    return _load_profiles_registry()["active"]


def _profile_root(profile: str = "") -> Path:
    safe = _safe_profile_name(profile) if profile else _active_profile()
    root = OUTPUT_DIR / safe
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_profile(profile: str) -> str:
    safe = _safe_profile_name(profile)
    reg = _load_profiles_registry()
    if safe not in reg["profiles"]:
        reg["profiles"].append(safe)
        _save_profiles_registry(reg)
    (OUTPUT_DIR / safe).mkdir(parents=True, exist_ok=True)
    return safe


def _set_active_profile(profile: str) -> str:
    safe = _ensure_profile(profile)
    reg = _load_profiles_registry()
    reg["active"] = safe
    _save_profiles_registry(reg)
    return safe


def _unique_project_dir(profile_root: Path, project_name: str) -> Path:
    candidate = profile_root / project_name
    if not candidate.exists():
        return candidate
    counter = 2
    while (profile_root / f"{project_name}_{counter}").exists():
        counter += 1
    return profile_root / f"{project_name}_{counter}"


def _migrate_legacy_layout() -> None:
    """Move pre-profile projects/media into the Default profile folder.

    Legacy layout stored projects directly under output/<project>/. The new
    layout is output/<profile>/<project>/. On startup we relocate any legacy
    top-level project folders and loose media files into output/Default/ so the
    whole app sees a consistent structure without losing existing work.
    """
    reg = _load_profiles_registry()
    profiles = set(reg["profiles"])
    default_root = OUTPUT_DIR / DEFAULT_PROFILE
    default_root.mkdir(parents=True, exist_ok=True)

    moved_any = False
    for path in list(OUTPUT_DIR.iterdir()):
        name = path.name
        if name.startswith("."):
            continue
        if path.is_dir():
            if name in profiles or name in RESERVED_OUTPUT_NAMES:
                continue
            # Legacy top-level project folder -> relocate under Default.
            dest = _unique_project_dir(default_root, name)
            try:
                shutil.move(str(path), str(dest))
                moved_any = True
                logger.info("[MIGRATE] project %s -> %s", name, _relative_to_output(dest))
            except Exception:
                logger.warning("[MIGRATE] could not move legacy project %s", name)
        elif _is_source_media(path):
            # Legacy loose media at output root -> mint a project under Default.
            try:
                _ensure_project_layout_for_audio(path, profile=DEFAULT_PROFILE)
                moved_any = True
            except Exception:
                logger.warning("[MIGRATE] could not migrate loose media %s", name)

    _save_profiles_registry(reg)
    if moved_any:
        logger.info("[MIGRATE] legacy media relocated into profile '%s'", DEFAULT_PROFILE)


def _resolve_output_path(path_like: str, *, require_exists: bool = True) -> Path:
    rel = Path(str(path_like or "").strip().lstrip("/"))
    candidate = (OUTPUT_DIR / rel).resolve()
    output_root = OUTPUT_DIR.resolve()
    if candidate != output_root and output_root not in candidate.parents:
        raise FileNotFoundError(f"Path not allowed: {path_like}")
    if require_exists and not candidate.exists():
        raise FileNotFoundError(f"Path not found: {path_like}")
    return candidate


def _project_sidecar_paths(project_dir: Path, stem: str) -> list[Path]:
    return [
        project_dir / f"{stem}.lrc",
        project_dir / f"{stem}.ass",
        project_dir / f"{stem}.json",
        project_dir / f"{stem}.srt",
        project_dir / f"{stem}.txt",
        project_dir / f"{stem}_karaoke.mp4",
        project_dir / f"{stem}_minus.mp3",
        project_dir / f"{stem}_vocals.mp3",
        project_dir / f"{stem}_minus_chorus.mp3",
    ]


def _find_project_asset(
    project_dir: Path, extension: str, preferred_stem: str = ""
) -> Path | None:
    suffix = extension.lower()
    candidates = (
        sorted(
            [
                path
                for path in project_dir.iterdir()
                if path.is_file() and path.suffix.lower() == suffix
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if project_dir.exists()
        else []
    )
    if preferred_stem:
        preferred = project_dir / f"{preferred_stem}{suffix}"
        if preferred.exists() and preferred.is_file():
            return preferred
    return candidates[0] if candidates else None


def _find_project_state(project_dir: Path) -> Path | None:
    preferred = project_dir / f"{project_dir.name}.proj.json"
    if preferred.exists() and preferred.is_file():
        return preferred
    project_files = sorted(
        project_dir.glob("*.proj.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if project_files:
        return project_files[0]
    # Only a *.proj.json file counts as saved project state. Never fall back to an
    # arbitrary *.json (e.g. vocals.json / transcript output) or an unsaved project
    # gets treated as saved and save/load/rename can clobber the transcript JSON.
    return None


def _find_project_stems(project_dir: Path, audio_stem: str = "") -> Path | None:
    roots = []
    preferred = project_dir / "stems" / "htdemucs" / audio_stem
    if audio_stem and preferred.is_dir():
        roots.append(preferred)
    stems_root = project_dir / "stems"
    if stems_root.is_dir():
        roots.extend(
            path
            for path in stems_root.rglob("*")
            if path.is_dir() and path not in roots
        )
    for root in roots:
        if any((root / name).is_file() for name in ("vocals.wav", "no_vocals.wav")):
            return root
    return None


def _rename_project_assets(project_dir: Path, project_name: str) -> None:
    for path in sorted(
        project_dir.iterdir() if project_dir.exists() else [],
        key=lambda item: item.name.lower(),
    ):
        if (
            not path.is_file()
            or path.suffix.lower() not in {".lrc", ".ass"}
            and not path.name.lower().endswith(".proj.json")
        ):
            continue
        suffix = (
            ".proj.json"
            if path.name.lower().endswith(".proj.json")
            else path.suffix.lower()
        )
        target = project_dir / f"{project_name}{suffix}"
        if path == target:
            continue
        counter = 2
        while target.exists():
            target = project_dir / f"{project_name}_{counter}{suffix}"
            counter += 1
        path.rename(target)


def _project_has_active_jobs(project_ref: str) -> bool:
    # project_ref may be a bare project name or a profile-qualified path
    # ("profile/project"). Match jobs by audio path prefix or bare project name.
    rel = str(project_ref or "").strip("/")
    bare = Path(rel).name
    return any(
        job.get("status") in {"queued", "running"}
        and (
            job.get("project_name") == bare
            or str(job.get("audio_filename") or "").startswith(f"{rel}/")
        )
        for job in JOBS.values()
    )


def _rewrite_project_references(
    old_project: str, new_project: str, old_rel_audio: str, new_rel_audio: str
) -> None:
    with JOB_LOCK:
        for job in JOBS.values():
            if job.get("audio_filename") == old_rel_audio:
                job["audio_filename"] = new_rel_audio
            if job.get("project_name") == old_project:
                job["project_name"] = new_project
            runner_kwargs = job.get("runner_kwargs")
            if isinstance(runner_kwargs, dict):
                if runner_kwargs.get("audio_filename") == old_rel_audio:
                    runner_kwargs["audio_filename"] = new_rel_audio
                if runner_kwargs.get("project_name") == old_project:
                    runner_kwargs["project_name"] = new_project
            target_key = job.get("target_key")
            if isinstance(target_key, str) and target_key:
                job["target_key"] = target_key.replace(
                    old_rel_audio, new_rel_audio
                ).replace(old_project, new_project)
            for field in ("label", "message", "details"):
                value = job.get(field)
                if isinstance(value, str) and value:
                    job[field] = value.replace(old_project, new_project)


def _finalize_pending_project_renames() -> list[dict]:
    """Move only idle projects, keeping job paths stable until every user is done."""
    completed = []
    with RENAME_LOCK:
        with JOB_LOCK:
            ready = [
                (old_name, new_name)
                for old_name, new_name in PENDING_PROJECT_RENAMES.items()
                if not _project_has_active_jobs(old_name)
            ]
        for old_name, new_name in ready:
            old_project = OUTPUT_DIR / old_name
            target_project = OUTPUT_DIR / new_name
            if not old_project.is_dir() or (
                target_project.exists()
                and target_project.resolve() != old_project.resolve()
            ):
                PENDING_PROJECT_RENAMES.pop(old_name, None)
                continue

            audio_candidates = sorted(
                [path for path in old_project.iterdir() if _is_source_media(path)],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if not audio_candidates:
                PENDING_PROJECT_RENAMES.pop(old_name, None)
                logger.warning(
                    "[RENAME] skipped project=%s because it has no source media",
                    old_name,
                )
                continue

            old_rel_audio = _relative_to_output(audio_candidates[0])
            if target_project.resolve() != old_project.resolve():
                old_project.rename(target_project)
            _rename_project_assets(target_project, target_project.name)

            target_audio = next(
                (path for path in target_project.iterdir() if _is_source_media(path)),
                None,
            )
            if not target_audio:
                PENDING_PROJECT_RENAMES.pop(old_name, None)
                continue
            new_rel_audio = _relative_to_output(target_audio)
            renamed_proj = _find_project_state(target_project)
            if renamed_proj:
                try:
                    payload = json.loads(renamed_proj.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        payload["project_name"] = target_project.name
                        payload["audio_filename"] = new_rel_audio
                        renamed_proj.write_text(
                            json.dumps(payload, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                except Exception:
                    logger.warning(
                        "[RENAME] could not update project state for %s",
                        target_project.name,
                    )

            _rewrite_project_references(
                Path(old_name).name,
                target_project.name,
                old_rel_audio,
                new_rel_audio,
            )
            PENDING_PROJECT_RENAMES.pop(old_name, None)
            completed.append(
                {
                    "old_project": old_name,
                    "project_name": target_project.name,
                    "audio_filename": new_rel_audio,
                }
            )
            logger.info(
                "[RENAME] finalized project=%s -> %s", old_name, target_project.name
            )
    return completed


def _ensure_project_layout_for_audio(audio_path: Path, profile: str = "") -> Path:
    parent = audio_path.parent
    output_root = OUTPUT_DIR.resolve()
    parent_res = parent.resolve()
    profiles = set(_list_profiles())

    if parent_res == output_root:
        # Loose media sitting at the output root -> place under a profile.
        container = _profile_root(profile)
    elif parent.name in profiles and parent.parent.resolve() == output_root:
        # Loose media sitting directly inside a profile folder -> mint a project.
        container = parent
    else:
        # Already nested inside output/<profile>/<project>/ (or a project dir).
        return audio_path

    base_project = _safe_project_name(audio_path.stem, fallback="project")
    project_dir = container / base_project
    if project_dir.exists() and project_dir.is_dir():
        if not (project_dir / audio_path.name).exists():
            counter = 2
            while (container / f"{base_project}_{counter}").exists():
                counter += 1
            project_dir = container / f"{base_project}_{counter}"
    project_dir.mkdir(parents=True, exist_ok=True)

    target_audio = project_dir / audio_path.name
    if target_audio.exists() and target_audio.resolve() != audio_path.resolve():
        counter = 2
        while (
            project_dir / f"{audio_path.stem}_{counter}{audio_path.suffix or '.mp3'}"
        ).exists():
            counter += 1
        target_audio = (
            project_dir / f"{audio_path.stem}_{counter}{audio_path.suffix or '.mp3'}"
        )
    if target_audio != audio_path:
        audio_path.rename(target_audio)
    old_stem = audio_path.stem
    for candidate in _project_sidecar_paths(container, old_stem):
        if candidate.exists() and candidate.is_file():
            candidate.rename(project_dir / candidate.name)

    old_stems = container / "stems" / "htdemucs" / old_stem
    if old_stems.exists() and not (project_dir / "stems").exists():
        (project_dir / "stems").parent.mkdir(parents=True, exist_ok=True)
        old_stems.parent.parent.rename(project_dir / "stems")

    return target_audio


def _project_manifest(project_dir: Path) -> dict | None:
    if not project_dir.exists() or not project_dir.is_dir():
        return None
    audio_files = sorted(
        [path for path in project_dir.iterdir() if _is_source_media(path)],
        key=lambda path: (
            path.suffix.lower()
            not in {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"},
            -path.stat().st_mtime,
        ),
    )
    if not audio_files:
        return None

    audio_path = audio_files[0]
    audio_stem = audio_path.stem
    rel_audio = _relative_to_output(audio_path)
    lrc_path = _find_project_asset(project_dir, ".lrc", audio_stem)
    stems_dir = _find_project_stems(project_dir, audio_stem)
    has_stems = stems_dir is not None
    has_lyrics_content = bool(
        lrc_path and lrc_path.is_file() and lrc_path.stat().st_size > 0
    )
    has_timed_lyrics = _has_timed_lyrics(lrc_path)
    proj_path = _find_project_state(project_dir)
    has_proj = proj_path is not None
    has_chorus_stem = (project_dir / f"{audio_stem}_minus_chorus.mp3").exists()

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
        "profile": project_dir.parent.name,
        "audio_filename": rel_audio,
        "size": f"{audio_path.stat().st_size / (1024*1024):.2f} MB",
        "url": f"/files/{quote(rel_audio, safe='/')}",
        "type": audio_path.suffix.lower(),
        "audio_name": audio_path.name,
        "lrc_filename": _relative_to_output(lrc_path) if lrc_path else "",
        "ass_filename": (
            _relative_to_output(_find_project_asset(project_dir, ".ass", audio_stem))
            if _find_project_asset(project_dir, ".ass", audio_stem)
            else ""
        ),
        "project_state_filename": _relative_to_output(proj_path) if proj_path else "",
        "has_stems": has_stems,
        "has_lyrics_content": has_lyrics_content,
        "has_timed_lyrics": has_timed_lyrics,
        "has_proj": has_proj,
        "has_chorus_stem": has_chorus_stem,
        "state": state,
    }


def _list_project_manifests(profile: str = "") -> list[dict]:
    root = _profile_root(profile)
    manifests = []
    for path in root.iterdir():
        if path.is_dir() and path.name != "stems":
            manifest = _project_manifest(path)
            if manifest:
                manifests.append(manifest)

    # Compatibility migration path: convert loose media directly inside this
    # profile folder into project subfolders.
    for path in sorted(
        root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if not _is_source_media(path):
            continue
        moved = _ensure_project_layout_for_audio(path, profile=root.name)
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


def _resolve_output_file(filename: str) -> Path:
    candidate = _resolve_output_path(filename, require_exists=True)
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"File not found: {filename}")
    return candidate


def _has_timed_lyrics(lrc_path: Path) -> bool:
    if not lrc_path or not lrc_path.exists() or not lrc_path.is_file():
        return False
    try:
        content = lrc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return re.search(r"\[\d{1,2}:\d{1,2}(?:\.\d{1,3})?\]", content) is not None


def directory_watcher_loop() -> None:
    while True:
        try:
            _sync_project_jobs()
        except Exception:
            logger.exception("[WATCHER] _sync_project_jobs failed")
        time.sleep(5)


__all__ = [n for n in dir() if not n.startswith("__")]
