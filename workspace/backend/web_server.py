"""OnePage Karaoke suite — server entry point.

The implementation is split across four modules wired together via a
``from X import *`` chain so that every top-level name lives in a single shared
namespace (matching the original single-file behaviour):

    core        -> config, FastAPI app, logging, shared state, job queue,
                   profiles/projects layout, worker loop, directory watcher.
    processing  -> Whisper transcription, Demucs separation, URL ingest,
                   multi-source lyrics fetching and timing correction.
    rendering   -> LRC->ASS subtitles, bouncing ball, FFmpeg burn, CDG+MP3.
    routes      -> all HTTP API endpoints.

Importing ``routes`` cascades the whole chain; we import each module explicitly
so job runners register (in processing/rendering) before the worker thread
starts.
"""
import threading

import uvicorn

from core import (
    app,
    logger,
    _job_worker_loop,
    directory_watcher_loop,
    _migrate_legacy_layout,
)
import processing  # noqa: F401 - registers audio/lyrics job runners
import rendering  # noqa: F401 - registers render/cdg job runners
import routes  # noqa: F401 - attaches all @app endpoints


# Relocate any pre-profile projects/media into the Default profile on startup so
# the whole app agrees on the output/<profile>/<project>/ layout.
try:
    _migrate_legacy_layout()
except Exception as _exc:  # pragma: no cover - defensive startup guard
    logger.warning("[MIGRATE] legacy layout migration skipped: %s", _exc)


# Start the background job worker and directory watcher. Runners are already
# registered via the processing/rendering imports above.
threading.Thread(target=_job_worker_loop, daemon=True).start()
threading.Thread(target=directory_watcher_loop, daemon=True).start()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
