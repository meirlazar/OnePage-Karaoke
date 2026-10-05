      const canvas = document.getElementById("videoCanvas");
      const ctx = canvas.getContext("2d");
      // Text outline thickness in device pixels for the current frame; 0 means
      // the outline stroke is skipped altogether.
      let outlineStrokeWidth = 2;
      const audio = document.getElementById("previewAudio");
      const vocalWaveformCanvas = document.getElementById("vocalWaveform");
      const vocalWaveformRail = document.getElementById("vocalWaveformRail");
      let vocalWaveformPeaks = [];
      let vocalWaveformDuration = 0;
      let vocalWaveformContext = null;
      let vocalWaveformLoadToken = 0;
      let vocalWaveformGain = 1.6;
      let vocalWaveformWindowStart = 0;
      const vocalWaveformWindowSize = 0.4;
      let vocalWaveformDrag = null;

      let lyricLines = [];
      let bgImageObj = null;
      let bgObjectUrl = null;
      let selectedLrcFilename = "";
      let selectedAudioFilename = "";
      let selectedProjectName = "";
      let activeProfile = localStorage.getItem("onepage-active-profile") || "Default";
      let knownProfiles = ["Default"];
      let selectedBgFile = null;
      let lastCommittedLyricsText = "";
      let jobPollTimer = null;
      let lastJobStatusById = new Map();
      let lastGrabbedLyricsProvider = null;
      let isSeekingPreview = false;
      let previewConfigSnapshot = null;
      let previewConfigLocked = false;
      let manualTimingState = { enabled: false, words: [], baselineEntries: [], entries: [], cursor: 0, activeStart: null, previewFromAppliedLyrics: false, revisionIndex: null };
      let manualTimingByAudio = {};
      let manualTimingApplyInFlight = false;
      let lyricsRevisionHistoryByAudio = {};
      let lyricsRevisionCursorByAudio = {};
      let themeCatalog = [];
      let currentThemeId = "";
      // Built-in themes so the picker always works even when the backend is
      // unreachable or the themes/ folder is missing/empty. Keys mirror the
      // :root CSS variables (without the leading --). Backend themes with the
      // same id override these.
      const BUILTIN_THEMES = [
        {
          id: "aurora", name: "Aurora (Default)",
          description: "Built-in default", source_name: "Built-in",
          vars: {
            "bg-base": "#0b0c10", "bg-surface": "#1f2833", "bg-panel": "#161f28",
            "border": "#45f3ff33", "text-main": "#c5c6c7", "text-muted": "#8fa7b2",
            "accent": "#45f3ff", "accent-hover": "#66fcf1", "success": "#7dffb3",
            "warning": "#ffcf66", "danger": "#ff8a80", "label-color": "#66fcf1",
            "control-bg": "#111821", "control-fg": "#ffffff", "control-border": "#45f3ff66",
            "button-bg": "#45f3ff", "button-fg": "#0b0c10", "button-hover-bg": "#66fcf1",
            "button-small-fg": "#c5c6c7", "paper": "#ffffff", "ink": "#111111",
            "canvas-bg": "#000000", "playhead": "#d71920", "shadow": "#000000",
            "overlay-bg": "#0b0c10", "divider": "#ffffff", "active-glow": "#45f3ff", "radius": "6px",
          },
        },
        {
          id: "magenta-night", name: "Magenta Night",
          description: "Built-in", source_name: "Built-in",
          vars: {
            "bg-base": "#0c0910", "bg-surface": "#241a2b", "bg-panel": "#1c1424",
            "border": "#ff5cc833", "text-main": "#e6d3e0", "text-muted": "#b28fa7",
            "accent": "#ff5cc8", "accent-hover": "#ff8ad8", "success": "#7dffb3",
            "warning": "#ffcf66", "danger": "#ff8a80", "label-color": "#ff8ad8",
            "control-bg": "#181019", "control-fg": "#ffffff", "control-border": "#ff5cc866",
            "button-bg": "#ff5cc8", "button-fg": "#170310", "button-hover-bg": "#ff8ad8",
            "button-small-fg": "#e6d3e0", "paper": "#ffffff", "ink": "#111111",
            "canvas-bg": "#000000", "playhead": "#d71920", "shadow": "#000000",
            "overlay-bg": "#0c0910", "divider": "#ffffff", "active-glow": "#ff5cc8", "radius": "6px",
          },
        },
        {
          id: "amber-studio", name: "Amber Studio",
          description: "Built-in", source_name: "Built-in",
          vars: {
            "bg-base": "#100c07", "bg-surface": "#2b241a", "bg-panel": "#241c14",
            "border": "#ffb34733", "text-main": "#e8ddcb", "text-muted": "#b2a08f",
            "accent": "#ffb347", "accent-hover": "#ffc76b", "success": "#7dffb3",
            "warning": "#ffcf66", "danger": "#ff8a80", "label-color": "#ffc76b",
            "control-bg": "#191308", "control-fg": "#ffffff", "control-border": "#ffb34766",
            "button-bg": "#ffb347", "button-fg": "#1a0d00", "button-hover-bg": "#ffc76b",
            "button-small-fg": "#e8ddcb", "paper": "#ffffff", "ink": "#111111",
            "canvas-bg": "#000000", "playhead": "#d71920", "shadow": "#000000",
            "overlay-bg": "#100c07", "divider": "#ffffff", "active-glow": "#ffb347", "radius": "6px",
          },
        },
        {
          id: "emerald", name: "Emerald",
          description: "Built-in", source_name: "Built-in",
          vars: {
            "bg-base": "#080f0b", "bg-surface": "#1a2b22", "bg-panel": "#14241c",
            "border": "#38e08b33", "text-main": "#cbe8d8", "text-muted": "#8fb2a2",
            "accent": "#38e08b", "accent-hover": "#5cf0a6", "success": "#7dffb3",
            "warning": "#ffcf66", "danger": "#ff8a80", "label-color": "#5cf0a6",
            "control-bg": "#0b1912", "control-fg": "#ffffff", "control-border": "#38e08b66",
            "button-bg": "#38e08b", "button-fg": "#052012", "button-hover-bg": "#5cf0a6",
            "button-small-fg": "#cbe8d8", "paper": "#ffffff", "ink": "#111111",
            "canvas-bg": "#000000", "playhead": "#d71920", "shadow": "#000000",
            "overlay-bg": "#080f0b", "divider": "#ffffff", "active-glow": "#38e08b", "radius": "6px",
          },
        },
      ];
      let frontendDebugIssues = [];
      let lastDebugReportText = "";
      let previewWidth = 640;
      let previewHeight = 360;
      let previewShouldStartAtZero = false;
      let karaokePlaceholderImg = null;
      let activeFxScope = "page";
      let activeTransitionStyle = "none";
      let activeFxSpeed = 0.6;
      let bouncingBallCandidate = null;
      let themeAnimationActive = false;

        function getWaveformColors() {
          const background = getThemeColor("--control-bg", "#111821");
          const waveform = getThemeColor("--waveform-accent", getThemeColor("--accent", "#45f3ff"));
          const divider = getThemeColor("--waveform-divider", getThemeColor("--divider", "#333333"));
          const playhead = getThemeColor("--waveform-playhead", getThemeColor("--playhead", "#d71920"));
          return {
            background,
            waveform: _contrastRatio(waveform, background) >= 3 ? waveform : _pickReadableText(background, [waveform, getThemeColor("--accent-hover", "#66fcf1"), "#00e5ff", "#ffcf66", "#ffffff", "#111111"]),
            divider: _contrastRatio(divider, background) >= 2 ? divider : _pickReadableText(background, [divider, getThemeColor("--text-muted", "#8fa7b2"), "#ffffff", "#111111"]),
            playhead: _contrastRatio(playhead, background) >= 3 ? playhead : _pickReadableText(background, [playhead, getThemeColor("--danger", "#ff8a80"), "#ffcf66", "#ffffff", "#111111"]),
          };
        }

        function drawVocalWaveform() {
          if (!vocalWaveformCanvas || !vocalWaveformRail) return;
          const width = Math.max(1, Math.floor(vocalWaveformRail.clientWidth - 16));
          const height = Math.max(1, Math.floor(vocalWaveformRail.clientHeight - 10));
          const dpr = window.devicePixelRatio || 1;
          const pixelWidth = Math.floor(width * dpr);
          const pixelHeight = Math.floor(height * dpr);
          if (vocalWaveformCanvas.width !== pixelWidth || vocalWaveformCanvas.height !== pixelHeight) {
            vocalWaveformCanvas.width = pixelWidth;
            vocalWaveformCanvas.height = pixelHeight;
          }
          const waveformCtx = vocalWaveformCanvas.getContext("2d");
          const colors = getWaveformColors();
          waveformCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
          waveformCtx.clearRect(0, 0, width, height);
          waveformCtx.fillStyle = colors.background;
          waveformCtx.fillRect(0, 0, width, height);
          waveformCtx.strokeStyle = colors.divider;
          waveformCtx.lineWidth = 1;
          waveformCtx.beginPath();
          waveformCtx.moveTo(0, height / 2);
          waveformCtx.lineTo(width, height / 2);
          waveformCtx.stroke();

          if (vocalWaveformPeaks.length) {
            const startIndex = Math.floor(vocalWaveformWindowStart * vocalWaveformPeaks.length);
            const endIndex = Math.max(startIndex + 1, Math.ceil((vocalWaveformWindowStart + vocalWaveformWindowSize) * vocalWaveformPeaks.length));
            const visiblePeaks = vocalWaveformPeaks.slice(startIndex, endIndex);
            waveformCtx.fillStyle = colors.waveform;
            const step = width / visiblePeaks.length;
            visiblePeaks.forEach((peak, index) => {
              const amplitude = Math.max(2, Math.min(height * 0.48, peak * vocalWaveformGain * (height * 0.48)));
              const barWidth = Math.max(1, step * 0.76);
              const x = index * step + ((step - barWidth) / 2);
              waveformCtx.fillRect(x, (height / 2) - amplitude, barWidth, amplitude * 2);
            });
          }

          const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : vocalWaveformDuration;
          if (duration > 0) {
              const relativePosition = ((audio.currentTime / duration) - vocalWaveformWindowStart) / vocalWaveformWindowSize;
              if (relativePosition < 0 || relativePosition > 1) return;
              const x = Math.max(0, Math.min(width, relativePosition * width));
            waveformCtx.strokeStyle = colors.playhead;
            waveformCtx.lineWidth = 2;
            waveformCtx.beginPath();
            waveformCtx.moveTo(x, 0);
            waveformCtx.lineTo(x, height);
            waveformCtx.stroke();
          }
        }

        async function loadVocalWaveform(audioFilename) {
          const token = ++vocalWaveformLoadToken;
          vocalWaveformPeaks = [];
          vocalWaveformDuration = 0;
          vocalWaveformWindowStart = 0;
          drawVocalWaveform();
          if (!audioFilename) return;
          try {
            const { res, data } = await fetchJsonSafe(`/api/vocal-waveform?audio_filename=${encodeURIComponent(audioFilename)}`, {}, "Vocal waveform API");
            if (!res.ok || token !== vocalWaveformLoadToken) return;
            const response = await fetch(data.url);
            if (!response.ok || token !== vocalWaveformLoadToken) return;
            const buffer = await response.arrayBuffer();
            if (!vocalWaveformContext) {
              vocalWaveformContext = new (window.AudioContext || window.webkitAudioContext)();
            }
            const decoded = await vocalWaveformContext.decodeAudioData(buffer);
            if (token !== vocalWaveformLoadToken) return;
            vocalWaveformDuration = decoded.duration;
            const samples = decoded.getChannelData(0);
            const bucketCount = Math.max(240, Math.min(1600, Math.floor(vocalWaveformRail.clientWidth * 2)));
            const bucketSize = Math.max(1, Math.floor(samples.length / bucketCount));
              vocalWaveformPeaks = Array.from({ length: bucketCount }, (_, bucket) => {
              const start = bucket * bucketSize;
              const end = Math.min(samples.length, start + bucketSize);
              let peak = 0;
              for (let index = start; index < end; index += 1) peak = Math.max(peak, Math.abs(samples[index]));
              return peak;
            });
              const peakMax = Math.max(...vocalWaveformPeaks, 0.001);
              vocalWaveformPeaks = vocalWaveformPeaks.map((peak) => peak / peakMax);
            drawVocalWaveform();
          } catch (error) {
            if (token === vocalWaveformLoadToken) console.warn("Vocal waveform unavailable", error);
          }
        }

        function seekFromVocalWaveform(event) {
          if (!vocalWaveformRail) return;
          const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : vocalWaveformDuration;
          if (!duration) return;
          const rect = vocalWaveformRail.getBoundingClientRect();
          const windowPosition = vocalWaveformWindowStart + (((event.clientX - rect.left) / rect.width) * vocalWaveformWindowSize);
          audio.currentTime = Math.max(0, Math.min(duration, windowPosition * duration));
          syncPlaybackUi();
        }

        vocalWaveformRail?.addEventListener("wheel", (event) => {
          event.preventDefault();
          const direction = event.deltaY < 0 ? 1 : -1;
          vocalWaveformGain = Math.max(0.5, Math.min(3.5, vocalWaveformGain + (direction * 0.12)));
          drawVocalWaveform();
        }, { passive: false });
        vocalWaveformRail?.addEventListener("pointerdown", (event) => {
          if (event.button !== 0) return;
          vocalWaveformDrag = { pointerId: event.pointerId, startX: event.clientX, windowStart: vocalWaveformWindowStart, moved: false };
          vocalWaveformRail.setPointerCapture(event.pointerId);
          vocalWaveformRail.style.cursor = "grabbing";
        });
        vocalWaveformRail?.addEventListener("pointermove", (event) => {
          if (!vocalWaveformDrag || event.pointerId !== vocalWaveformDrag.pointerId) return;
          const delta = event.clientX - vocalWaveformDrag.startX;
          vocalWaveformDrag.moved ||= Math.abs(delta) > 3;
          const span = Math.max(1, vocalWaveformRail.clientWidth);
          vocalWaveformWindowStart = Math.max(0, Math.min(1 - vocalWaveformWindowSize, vocalWaveformDrag.windowStart - ((delta / span) * vocalWaveformWindowSize)));
          drawVocalWaveform();
        });
        const endVocalWaveformDrag = (event) => {
          if (!vocalWaveformDrag || event.pointerId !== vocalWaveformDrag.pointerId) return;
          const wasDrag = vocalWaveformDrag.moved;
          vocalWaveformRail.releasePointerCapture?.(event.pointerId);
          vocalWaveformDrag = null;
          vocalWaveformRail.style.cursor = "grab";
          if (!wasDrag) seekFromVocalWaveform(event);
        };
        vocalWaveformRail?.addEventListener("pointerup", endVocalWaveformDrag);
        vocalWaveformRail?.addEventListener("pointercancel", endVocalWaveformDrag);
        window.addEventListener("resize", drawVocalWaveform);
// 1. Variable to keep track of our rendering loop
      let renderLoopId = null;

      // 2. The new 60fps rendering engine
      function startRenderLoop() {
          // Prevent multiple loops from running at the same time
          if (renderLoopId) cancelAnimationFrame(renderLoopId);

          function loop() {
              // This calls your existing canvas drawing function
              // It will now run up to 60 times a second!
              updatePreview();
              drawVocalWaveform();

              // Request the next frame
              renderLoopId = requestAnimationFrame(loop);
          }

          // Kick off the first frame
          loop();
      }

      function stopRenderLoop() {
          if (renderLoopId) {
              cancelAnimationFrame(renderLoopId);
              renderLoopId = null;
          }
      }

      // 3. Automatically start/stop the loop based on audio state
      const previewAudio = document.getElementById('previewAudio');

      if (previewAudio) {
          // When the music plays, start the high-speed loop
          previewAudio.addEventListener('play', startRenderLoop);

          // When the music pauses or ends, stop the loop to save CPU
          previewAudio.addEventListener('pause', stopRenderLoop);
          previewAudio.addEventListener('ended', stopRenderLoop);

          // If the user drags the timeline/seeks while paused, force a single frame update
          previewAudio.addEventListener('seeking', () => {
              if (manualTimingState.enabled) alignManualTimingToCurrentPlayback();
              syncPlaybackUi();
            });
            // While playing, the requestAnimationFrame loop already repaints the
            // canvas and waveform at up to 60fps, so timeupdate only refreshes the
            // lightweight chrome (seek bar, time readout, timing HUD) to avoid
            // repainting the same frame twice.
            previewAudio.addEventListener('timeupdate', () => {
              syncSeekBar();
              displayTimeTracker();
              updateManualTimingHud();
            });
            previewAudio.addEventListener('error', () => {
              const source = previewAudio.currentSrc || previewAudio.src || "(empty source)";
              pushFrontendDebugIssue({
                message: `Preview audio could not be played: ${source}`,
                file: "previewAudio",
                trace: previewAudio.error ? `Media error code ${previewAudio.error.code}` : "",
              });
          });
      }

      // 4. Update the Play/Pause button logic
      function togglePlayback() {
          if (!previewAudio) return;

          if (previewAudio.paused) {
              if (!previewAudio.src) {
                setStatus("Select a media file before pressing Play.", true);
                return;
              }
              previewAudio.play().catch((error) => {
                setStatus("Preview audio could not be played. Check that the source file is available.", true);
                pushFrontendDebugIssue({ message: error.message || "Preview audio playback failed", file: "previewAudio", trace: error.stack || "" });
              });
          } else {
              previewAudio.pause();
          }
      }

      const playButton = document.getElementById('playBtn');
      if (previewAudio) {
          previewAudio.addEventListener('play', () => {
              if (playButton) playButton.textContent = 'Pause';
          });

          previewAudio.addEventListener('pause', () => {
              if (playButton) playButton.textContent = 'Play';
          });
      }

      function _hexToRgbSafe(color) {
        const raw = String(color || "").trim();
        const m = raw.match(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
        if (!m) return null;
        let hex = m[1];
        if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
        return {
          r: parseInt(hex.slice(0, 2), 16),
          g: parseInt(hex.slice(2, 4), 16),
          b: parseInt(hex.slice(4, 6), 16),
        };
      }

      function _rgbToHex(rgb) {
        const ch = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
        return `#${ch(rgb.r)}${ch(rgb.g)}${ch(rgb.b)}`;
      }

      function _mixHex(c1, c2, t = 0.5) {
        const a = _hexToRgbSafe(c1) || { r: 17, g: 24, b: 33 };
        const b = _hexToRgbSafe(c2) || { r: 255, g: 255, b: 255 };
        const k = Math.max(0, Math.min(1, Number(t) || 0));
        return _rgbToHex({
          r: a.r + ((b.r - a.r) * k),
          g: a.g + ((b.g - a.g) * k),
          b: a.b + ((b.b - a.b) * k),
        });
      }

      function _relativeLuminance(hex) {
        const rgb = _hexToRgbSafe(hex) || { r: 0, g: 0, b: 0 };
        const toLinear = (v) => {
          const c = v / 255;
          return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        };
        const r = toLinear(rgb.r);
        const g = toLinear(rgb.g);
        const b = toLinear(rgb.b);
        return (0.2126 * r) + (0.7152 * g) + (0.0722 * b);
      }

      function _contrastRatio(a, b) {
        const l1 = _relativeLuminance(a);
        const l2 = _relativeLuminance(b);
        const hi = Math.max(l1, l2);
        const lo = Math.min(l1, l2);
        return (hi + 0.05) / (lo + 0.05);
      }

      function _pickReadableText(bgHex, candidates) {
        const picks = (candidates || []).filter(Boolean);
        if (picks.length === 0) return "#ffffff";
        picks.sort((x, y) => _contrastRatio(y, bgHex) - _contrastRatio(x, bgHex));
        return picks[0];
      }

      function applyControlContrastVars() {
        const root = document.documentElement;
        const styles = getComputedStyle(root);
        const bgBase = (styles.getPropertyValue("--bg-base") || "#0b0c10").trim();
        const bgSurface = (styles.getPropertyValue("--bg-surface") || "#1f2833").trim();
        const accent = (styles.getPropertyValue("--accent") || "#45f3ff").trim();
        const accentHover = (styles.getPropertyValue("--accent-hover") || accent).trim();
        const textMain = (styles.getPropertyValue("--text-main") || "#c5c6c7").trim();

        const controlBg = _mixHex(bgSurface, bgBase, 0.55);
        const controlFg = _pickReadableText(controlBg, [textMain, "#ffffff", "#f8f8f8", "#111111", "#000000"]);
        const buttonBg = accent;
        const buttonFg = _pickReadableText(buttonBg, ["#111111", "#000000", "#ffffff", textMain]);
        const buttonHoverBg = _contrastRatio(accentHover, buttonFg) >= 4.5 ? accentHover : _mixHex(accentHover, bgSurface, 0.2);
        const labelColor = _contrastRatio(accentHover, bgSurface) >= 3.2 ? accentHover : _pickReadableText(bgSurface, [accent, "#ffffff", textMain]);
        const controlBorder = _mixHex(accent, bgSurface, 0.35);
        const buttonSmallFg = _pickReadableText(bgSurface, [textMain, "#ffffff", accentHover]);

        root.style.setProperty("--label-color", labelColor);
        root.style.setProperty("--control-bg", controlBg);
        root.style.setProperty("--control-fg", controlFg);
        root.style.setProperty("--control-border", controlBorder);
        root.style.setProperty("--button-bg", buttonBg);
        root.style.setProperty("--button-fg", buttonFg);
        root.style.setProperty("--button-hover-bg", buttonHoverBg);
        root.style.setProperty("--button-small-fg", buttonSmallFg);
      }

      function getThemeColor(name, fallback) {
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
      }

      function createDynamicMicrophoneSVG() {
        const placeholderBg = getThemeColor("--bg-base", "#0b0c10");
        const placeholderAccent = getThemeColor("--accent", "#45f3ff");
        const placeholderSuccess = getThemeColor("--success", "#7dffb3");
        const placeholderWarning = getThemeColor("--warning", "#ffcf66");
        const placeholderPaper = getThemeColor("--paper", "#ffffff");
        const timestamp = Date.now();
        const svg = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
  <defs>
    <linearGradient id="bg-${timestamp}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="${placeholderBg}"/>
      <stop offset="35%" stop-color="${placeholderAccent}"/>
      <stop offset="65%" stop-color="${placeholderSuccess}"/>
      <stop offset="100%" stop-color="${placeholderWarning}"/>
    </linearGradient>
    <radialGradient id="halo-${timestamp}" cx="50%" cy="45%" r="55%">
      <stop offset="0%" stop-color="${placeholderPaper}" stop-opacity="0.3"/>
      <stop offset="100%" stop-color="${placeholderPaper}" stop-opacity="0"/>
    </radialGradient>
    <style>
      @keyframes float-note-1 {
        0% { opacity: 1; transform: translate(0, 0) rotate(0deg); }
        100% { opacity: 0; transform: translate(80px, -150px) rotate(25deg); }
      }
      @keyframes float-note-2 {
        0% { opacity: 1; transform: translate(0, 0) rotate(0deg); }
        100% { opacity: 0; transform: translate(-100px, -180px) rotate(-30deg); }
      }
      @keyframes float-note-3 {
        0% { opacity: 1; transform: translate(0, 0) rotate(0deg); }
        100% { opacity: 0; transform: translate(120px, -120px) rotate(35deg); }
      }
      @keyframes float-note-4 {
        0% { opacity: 1; transform: translate(0, 0) rotate(0deg); }
        100% { opacity: 0; transform: translate(-80px, -140px) rotate(-25deg); }
      }
      @keyframes pulse-mic {
        0%, 100% { filter: drop-shadow(0 0 8px ${placeholderAccent}88); }
        50% { filter: drop-shadow(0 0 20px ${placeholderAccent}ff); }
      }
      .music-note { animation-duration: 2.5s; animation-fill-mode: forwards; }
      .mic-body { animation: pulse-mic 1.5s ease-in-out infinite; }
    </style>
  </defs>
  <rect width="1280" height="720" fill="url(#bg-${timestamp})"/>
  <rect width="1280" height="720" fill="url(#halo-${timestamp})"/>
  <circle cx="330" cy="190" r="55" fill="${placeholderPaper}" fill-opacity="0.15"/>
  <circle cx="1030" cy="170" r="34" fill="${placeholderPaper}" fill-opacity="0.13"/>
  <circle cx="940" cy="560" r="62" fill="${placeholderPaper}" fill-opacity="0.12"/>

  <!-- Microphone -->
  <g transform="translate(640, 320)" class="mic-body">
    <!-- Mic head (circular grille) -->
    <circle cx="0" cy="-60" r="45" fill="none" stroke="${placeholderPaper}" stroke-width="3" opacity="0.95"/>
    <circle cx="0" cy="-60" r="40" fill="none" stroke="${placeholderPaper}" stroke-width="2" opacity="0.8"/>
    <circle cx="0" cy="-60" r="35" fill="none" stroke="${placeholderPaper}" stroke-width="1" opacity="0.7"/>

    <!-- Mic neck/stand -->
    <rect x="-8" y="-15" width="16" height="85" rx="8" fill="${placeholderPaper}" opacity="0.9"/>

    <!-- Mic base -->
    <ellipse cx="0" cy="75" rx="55" ry="25" fill="${placeholderPaper}" opacity="0.85"/>
    <ellipse cx="0" cy="75" rx="50" ry="20" fill="${placeholderAccent}" opacity="0.7"/>

    <!-- Music notes floating up -->
    <g transform="translate(-40, -80)" class="music-note" style="animation-name: float-note-1; animation-delay: 0s;">
      <path d="M 0 0 Q 5 -8 8 -15 Q 3 -12 0 -10 Z" fill="${placeholderWarning}" opacity="0.95"/>
      <circle cx="0" cy="8" r="5" fill="${placeholderWarning}" opacity="0.95"/>
    </g>
    <g transform="translate(25, -100)" class="music-note" style="animation-name: float-note-2; animation-delay: 0.3s;">
      <path d="M 0 0 Q 5 -8 8 -15 Q 3 -12 0 -10 Z" fill="${placeholderSuccess}" opacity="0.95"/>
      <circle cx="0" cy="8" r="5" fill="${placeholderSuccess}" opacity="0.95"/>
      <path d="M 8 -3 Q 13 -11 16 -18 Q 11 -15 8 -13 Z" fill="${placeholderSuccess}" opacity="0.95"/>
      <circle cx="8" cy="5" r="5" fill="${placeholderSuccess}" opacity="0.95"/>
    </g>
    <g transform="translate(-15, -120)" class="music-note" style="animation-name: float-note-3; animation-delay: 0.6s;">
      <path d="M 0 0 Q 5 -8 8 -15 Q 3 -12 0 -10 Z" fill="${placeholderAccent}" opacity="0.95"/>
      <circle cx="0" cy="8" r="5" fill="${placeholderAccent}" opacity="0.95"/>
    </g>
    <g transform="translate(45, -90)" class="music-note" style="animation-name: float-note-4; animation-delay: 0.9s;">
      <path d="M 0 0 Q 5 -8 8 -15 Q 3 -12 0 -10 Z" fill="${placeholderWarning}" opacity="0.95"/>
      <circle cx="0" cy="8" r="5" fill="${placeholderWarning}" opacity="0.95"/>
      <path d="M 8 -3 Q 13 -11 16 -18 Q 11 -15 8 -13 Z" fill="${placeholderWarning}" opacity="0.95"/>
      <circle cx="8" cy="5" r="5" fill="${placeholderWarning}" opacity="0.95"/>
    </g>
  </g>

  <text x="640" y="500" text-anchor="middle" font-size="52" font-family="Segoe UI, Arial, sans-serif" font-weight="800" fill="${placeholderPaper}">KARAOKE</text>
  <text x="640" y="555" text-anchor="middle" font-size="24" font-family="Segoe UI, Arial, sans-serif" font-weight="600" fill="${placeholderPaper}" fill-opacity="0.85">Ready to Rock!</text>
</svg>`;
        return svg;
      }

      let _placeholderCacheKey = "";
      function ensureKaraokePlaceholderImage() {
        // Cache the placeholder and only regenerate when the theme colors change.
        // Regenerating every frame used to spawn a new Image whose onload called
        // updatePreview(), which drew another frame and spawned another Image — a
        // self-perpetuating redraw loop that burned CPU while idle.
        const key = [
          getThemeColor("--bg-base", ""),
          getThemeColor("--accent", ""),
          getThemeColor("--success", ""),
          getThemeColor("--warning", ""),
          getThemeColor("--paper", ""),
        ].join("|");
        if (karaokePlaceholderImg && _placeholderCacheKey === key) {
          return karaokePlaceholderImg;
        }
        const svg = createDynamicMicrophoneSVG();
        const img = new Image();
        img.onload = () => updatePreview();
        img.src = `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
        karaokePlaceholderImg = img;
        _placeholderCacheKey = key;
        return karaokePlaceholderImg;
      }

      function animateThemeFadeIn(canvas, ctx, duration) {
        const startTime = performance.now();
        const w = canvas.width;
        const h = canvas.height;
        const originalFrame = ctx.getImageData(0, 0, w, h);

        function frame(currentTime) {
          const progress = Math.min(1, (currentTime - startTime) / duration);

          if (progress < 1) {
            ctx.clearRect(0, 0, w, h);
            ctx.globalAlpha = progress;
            ctx.putImageData(originalFrame, 0, 0);
            ctx.globalAlpha = 1;
            requestAnimationFrame(frame);
          } else {
            ctx.putImageData(originalFrame, 0, 0);
            themeAnimationActive = false;
          }
        }
        requestAnimationFrame(frame);
      }

      function animateThemeSlideIn(canvas, ctx, duration, direction = 'left') {
        const startTime = performance.now();
        const w = canvas.width;
        const h = canvas.height;
        const originalFrame = ctx.getImageData(0, 0, w, h);

        const directions = { left: -1, right: 1, top: -1, bottom: 1 };
        const isHorizontal = direction === 'left' || direction === 'right';
        const dirValue = directions[direction] || -1;

        function frame(currentTime) {
          const progress = Math.min(1, (currentTime - startTime) / duration);
          const offset = isHorizontal ? w * (1 - progress) * dirValue : h * (1 - progress) * dirValue;

          ctx.clearRect(0, 0, w, h);
          ctx.save();

          if (isHorizontal) {
            ctx.translate(offset, 0);
          } else {
            ctx.translate(0, offset);
          }

          ctx.putImageData(originalFrame, 0, 0);
          ctx.restore();

          if (progress < 1) {
            requestAnimationFrame(frame);
          } else {
            ctx.putImageData(originalFrame, 0, 0);
            themeAnimationActive = false;
          }
        }
        requestAnimationFrame(frame);
      }

      function animateThemeShimmer(canvas, ctx, duration) {
        const startTime = performance.now();
        const w = canvas.width;
        const h = canvas.height;
        const originalFrame = ctx.getImageData(0, 0, w, h);

        function frame(currentTime) {
          const progress = Math.min(1, (currentTime - startTime) / duration);
          const shimmerPos = progress * w * 2 - w;

          ctx.putImageData(originalFrame, 0, 0);

          const gradient = ctx.createLinearGradient(shimmerPos, 0, shimmerPos + w * 0.3, 0);
          gradient.addColorStop(0, 'rgba(255, 255, 255, 0)');
          gradient.addColorStop(0.5, `rgba(255, 255, 255, ${0.3 * (1 - progress)})`);
          gradient.addColorStop(1, 'rgba(255, 255, 255, 0)');

          ctx.fillStyle = gradient;
          ctx.fillRect(0, 0, w, h);

          if (progress < 1) {
            requestAnimationFrame(frame);
          } else {
            themeAnimationActive = false;
          }
        }
        requestAnimationFrame(frame);
      }

      function animateThemeSwirl(canvas, ctx, duration) {
        const startTime = performance.now();
        const w = canvas.width;
        const h = canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        const originalFrame = ctx.getImageData(0, 0, w, h);

        function frame(currentTime) {
          const progress = Math.min(1, (currentTime - startTime) / duration);
          const maxRadius = Math.sqrt(cx * cx + cy * cy);
          const radius = maxRadius * progress;

          ctx.clearRect(0, 0, w, h);

          ctx.save();
          ctx.globalCompositeOperation = 'destination-in';
          ctx.beginPath();
          ctx.arc(cx, cy, radius, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();

          ctx.putImageData(originalFrame, 0, 0);

          if (progress < 1) {
            requestAnimationFrame(frame);
          } else {
            ctx.putImageData(originalFrame, 0, 0);
            themeAnimationActive = false;
          }
        }
        requestAnimationFrame(frame);
      }

      function animateThemePopIn(canvas, ctx, duration) {
        const startTime = performance.now();
        const w = canvas.width;
        const h = canvas.height;
        const originalFrame = ctx.getImageData(0, 0, w, h);
        const blockSize = 40;
        const cols = Math.ceil(w / blockSize);
        const rows = Math.ceil(h / blockSize);
        const totalBlocks = cols * rows;

        function frame(currentTime) {
          const progress = Math.min(1, (currentTime - startTime) / duration);
          const blocksToShow = Math.floor(progress * totalBlocks);

          ctx.putImageData(originalFrame, 0, 0);

          ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
          for (let i = blocksToShow; i < totalBlocks; i++) {
            const row = Math.floor(i / cols);
            const col = i % cols;
            ctx.fillRect(col * blockSize, row * blockSize, blockSize, blockSize);
          }

          if (progress < 1) {
            requestAnimationFrame(frame);
          } else {
            ctx.putImageData(originalFrame, 0, 0);
            themeAnimationActive = false;
          }
        }
        requestAnimationFrame(frame);
      }

      function animateThemeWave(canvas, ctx, duration) {
        const startTime = performance.now();
        const w = canvas.width;
        const h = canvas.height;
        const originalFrame = ctx.getImageData(0, 0, w, h);

        function frame(currentTime) {
          const progress = Math.min(1, (currentTime - startTime) / duration);
          const wavePos = progress * (w + h);

          ctx.putImageData(originalFrame, 0, 0);

          const gradient = ctx.createLinearGradient(wavePos - h * 0.5, 0, wavePos + h * 0.5, 0);
          gradient.addColorStop(0, 'rgba(0, 0, 0, 0.8)');
          gradient.addColorStop(0.3, 'rgba(0, 0, 0, 0.4)');
          gradient.addColorStop(0.7, 'rgba(0, 0, 0, 0.4)');
          gradient.addColorStop(1, 'rgba(0, 0, 0, 0.8)');

          ctx.fillStyle = gradient;
          ctx.fillRect(wavePos - h, 0, h * 2, h);

          if (progress < 1) {
            requestAnimationFrame(frame);
          } else {
            ctx.putImageData(originalFrame, 0, 0);
            themeAnimationActive = false;
          }
        }
        requestAnimationFrame(frame);
      }

      function playThemeAnimation() {
        if (themeAnimationActive) return;
        themeAnimationActive = true;

        const canvas = document.getElementById("videoCanvas");
        if (!canvas) return;

        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        const animations = [
          () => animateThemeFadeIn(canvas, ctx, 1500),
          () => animateThemeSlideIn(canvas, ctx, 1500, ['left', 'right', 'top', 'bottom'][Math.floor(Math.random() * 4)]),
          () => animateThemeShimmer(canvas, ctx, 2000),
          () => animateThemeSwirl(canvas, ctx, 1800),
          () => animateThemePopIn(canvas, ctx, 1500),
          () => animateThemeWave(canvas, ctx, 1800)
        ];

        const randomAnimation = animations[Math.floor(Math.random() * animations.length)];
        randomAnimation();
      }

      function setPreviewCanvasSize(width, height) {
        const safeW = Math.max(320, Number(width) || 1280);
        const safeH = Math.max(180, Number(height) || 720);
        if (canvas.width === safeW && canvas.height === safeH) return;
        canvas.width = safeW;
        canvas.height = safeH;
        previewWidth = safeW;
        previewHeight = safeH;
      }

      function onPreviewResolutionChange() {
        const raw = document.getElementById("previewResolution")?.value || "1280x720";
        const [w, h] = raw.split("x").map((v) => Number(v));
        setPreviewCanvasSize(w, h);
        smoothScrollY = 0;
        updatePreview();
      }

      function getLyricsRevisionHistory(audioFilename) {
        if (!audioFilename) return [];
        if (!lyricsRevisionHistoryByAudio[audioFilename]) lyricsRevisionHistoryByAudio[audioFilename] = [];
        return lyricsRevisionHistoryByAudio[audioFilename];
      }

      function addLyricsRevision(audioFilename, content, reason = "commit", metadata = {}) {
        if (!audioFilename) return;
        const text = String(content || "");
        const history = getLyricsRevisionHistory(audioFilename);
        const last = history.length ? history[history.length - 1] : null;
        if (last && last.content === text && !metadata.allowDuplicate) return;

        // Detect timing level from content
        const timingLevel = detectLyricsTimingLevel(content);

        const entry = {
          content: text,
          reason,
          at: Date.now(),
          modifiedAt: metadata.modifiedAt || Date.now(),
          provider: metadata.provider || null,
          isNew: metadata.isNew !== undefined ? metadata.isNew : reason.includes("backend") || reason.includes("grabbed"),
          isCustomized: metadata.isCustomized || false,
          timingLevel: metadata.timingLevel || timingLevel,
          timestampCount: metadata.timestampCount || countTimestamps(content)
        };
        history.push(entry);
        lyricsRevisionCursorByAudio[audioFilename] = Math.max(0, history.length - 1);
        updateLyricsRevisionMeta();
      }

      function beginTimingRevision() {
        if (!selectedAudioFilename) return;
        const content = document.getElementById("lyricsEditor").value || "";
        addLyricsRevision(selectedAudioFilename, content, "timing edit", {
          isNew: false,
          isCustomized: true,
          timingLevel: detectLyricsTimingLevel(content),
          timestampCount: countTimestamps(content),
          allowDuplicate: true,
        });
        manualTimingState.revisionIndex = _getRevisionCursor(selectedAudioFilename);
      }

      function updateTimingRevision(content) {
        const history = getLyricsRevisionHistory(selectedAudioFilename);
        const index = Number(manualTimingState.revisionIndex);
        if (!Number.isInteger(index) || !history[index]) return;
        const entry = history[index];
        entry.content = content;
        entry.modifiedAt = Date.now();
        entry.isCustomized = true;
        entry.timingLevel = detectLyricsTimingLevel(content);
        entry.timestampCount = countTimestamps(content);
        lyricsRevisionCursorByAudio[selectedAudioFilename] = index;
        updateLyricsRevisionMeta();
      }


      async function clearAllLyricsTiming() {
        if (!selectedAudioFilename || !selectedLrcFilename) {
          setStatus("Select source media first.", true);
          return;
        }
        const editor = document.getElementById("lyricsEditor");
        const timedContent = editor.value || "";
        const untimedContent = timedContent
          .replace(/\[(\d+):(\d+)(?:\.(\d{1,3}))?\]/g, "")
          .replace(/<(\d+):(\d+)(?:\.(\d{1,3}))?>/g, "")
          .split("\n")
          .map((line) => line.trim())
          .join("\n");
        if (timedContent === untimedContent) {
          setStatus("This revision has no timing to clear.");
          return;
        }

        if (lyricsEditorSaveTimer) clearTimeout(lyricsEditorSaveTimer);
        const history = getLyricsRevisionHistory(selectedAudioFilename);
        const activeIndex = _getRevisionCursor(selectedAudioFilename);
        if (!history[activeIndex] || history[activeIndex].content !== timedContent) {
          addLyricsRevision(selectedAudioFilename, timedContent, "timing source", {
            isNew: false,
            isCustomized: true,
            timingLevel: detectLyricsTimingLevel(timedContent),
            timestampCount: countTimestamps(timedContent),
          });
        }
        addLyricsRevision(selectedAudioFilename, untimedContent, "timing cleared", {
          isNew: false,
          isCustomized: true,
          timingLevel: 0,
          timestampCount: 0,
        });

        editor.value = untimedContent;
        lastCommittedLyricsText = untimedContent;
        manualTimingState.words = extractWordsFromEditor();
        manualTimingState.baselineEntries = [];
        manualTimingState.entries = [];
        manualTimingState.cursor = 0;
        manualTimingState.activeStart = null;
        manualTimingState.previewFromAppliedLyrics = false;
        manualTimingState.revisionIndex = null;
        parseLrcData(untimedContent);
        persistManualTiming();
        updateManualTimingHud();
        updatePreview();

        try {
          const fd = new FormData();
          fd.append("filename", selectedLrcFilename);
          fd.append("content", untimedContent);
          const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
          const data = await res.json();
          if (!res.ok) throw new Error(data.message || "Lyrics save failed.");
          await autoSaveManualTimingDraft();
          setStatus("Created a new revision with all timing cleared.");
        } catch (err) {
          setStatus(`Failed to clear timing: ${err}`, true);
        }
      }

      function _formatLrcStamp(totalSeconds) {
        // LRC stamps are mm:ss.xx (hundredths). Minutes are not wrapped at 60 so
        // long tracks stay monotonic.
        const safe = Math.max(0, totalSeconds);
        const mins = Math.floor(safe / 60);
        const secs = safe - mins * 60;
        const wholeSecs = Math.floor(secs);
        const hundredths = Math.round((secs - wholeSecs) * 100);
        let m = mins, s = wholeSecs, cs = hundredths;
        if (cs >= 100) { cs -= 100; s += 1; }
        if (s >= 60) { s -= 60; m += 1; }
        return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
      }

      function shiftLyricsTimestamps(content, deltaSeconds) {
        // Shift both line-level [mm:ss.xx] and word-level <mm:ss.xx> stamps.
        // Timestamps are clamped at 0 so a shift can never go negative.
        const shift = (match, mm, ss, frac, open, close) => {
          const fracStr = frac === undefined ? "0" : String(frac);
          // A 3-digit fraction is milliseconds, otherwise hundredths.
          const fracSec = fracStr.length === 3
            ? Number(fracStr) / 1000
            : Number(fracStr.padEnd(2, "0")) / 100;
          const total = Number(mm) * 60 + Number(ss) + fracSec;
          return `${open}${_formatLrcStamp(total + deltaSeconds)}${close}`;
        };
        return content
          .replace(/\[(\d+):(\d+)(?:\.(\d{1,3}))?\]/g, (_m, mm, ss, frac) => shift(_m, mm, ss, frac, "[", "]"))
          .replace(/<(\d+):(\d+)(?:\.(\d{1,3}))?>/g, (_m, mm, ss, frac) => shift(_m, mm, ss, frac, "<", ">"));
      }

      // Shift only the timestamps whose covered text intersects the editor
      // selection [selStart, selEnd). A timestamp "covers" the text that follows
      // it up to the next timestamp, so highlighting any word(s)/line(s) nudges
      // exactly the stamps that govern the highlighted text.
      function shiftLyricsTimestampsInSelection(content, deltaSeconds, selStart, selEnd) {
        const tagRegex = /\[(\d+):(\d+)(?:\.(\d{1,3}))?\]|<(\d+):(\d+)(?:\.(\d{1,3}))?>/g;
        const tags = [];
        let match;
        while ((match = tagRegex.exec(content)) !== null) {
          tags.push({ start: match.index, end: match.index + match[0].length, text: match[0] });
        }
        if (!tags.length) return content;

        let result = "";
        let lastIndex = 0;
        tags.forEach((tag, index) => {
          const coverStart = tag.start;
          const coverEnd = index + 1 < tags.length ? tags[index + 1].start : content.length;
          const intersects = coverStart < selEnd && coverEnd > selStart;
          result += content.slice(lastIndex, tag.start);
          result += intersects ? shiftLyricsTimestamps(tag.text, deltaSeconds) : tag.text;
          lastIndex = tag.end;
        });
        result += content.slice(lastIndex);
        return result;
      }

      async function shiftAllLyricsTiming(deltaSeconds) {
        if (!selectedAudioFilename || !selectedLrcFilename) {
          setStatus("Select source media first.", true);
          return;
        }
        const editor = document.getElementById("lyricsEditor");
        const currentContent = editor.value || "";
        if (!/\[\d+:\d+(?:\.\d{1,3})?\]|<\d+:\d+(?:\.\d{1,3})?>/.test(currentContent)) {
          setStatus("This revision has no timing to shift.", true);
          return;
        }
        // If the user highlighted word(s)/line(s) in the editor, nudge only those
        // timestamps; with no selection, nudge every timestamp (universal shift).
        const selStart = editor.selectionStart;
        const selEnd = editor.selectionEnd;
        const hasSelection =
          Number.isInteger(selStart) && Number.isInteger(selEnd) && selEnd > selStart;
        const shiftedContent = hasSelection
          ? shiftLyricsTimestampsInSelection(currentContent, deltaSeconds, selStart, selEnd)
          : shiftLyricsTimestamps(currentContent, deltaSeconds);
        if (shiftedContent === currentContent) {
          setStatus(
            hasSelection
              ? "No shiftable timestamps in the highlighted selection."
              : "All timestamps are already at 0:00; nothing to shift.",
            true,
          );
          return;
        }

        if (lyricsEditorSaveTimer) clearTimeout(lyricsEditorSaveTimer);
        // Preserve the pre-shift text as its own revision if it isn't recorded yet,
        // so repeated presses remain individually undoable.
        const history = getLyricsRevisionHistory(selectedAudioFilename);
        const activeIndex = _getRevisionCursor(selectedAudioFilename);
        if (!history[activeIndex] || history[activeIndex].content !== currentContent) {
          addLyricsRevision(selectedAudioFilename, currentContent, "timing source", {
            isNew: false,
            isCustomized: true,
            timingLevel: detectLyricsTimingLevel(currentContent),
            timestampCount: countTimestamps(currentContent),
          });
        }

        const sign = deltaSeconds < 0 ? "-" : "+";
        const scope = hasSelection ? "Selection" : "Universal";
        const label = `${scope} ${sign}${_formatLrcStamp(Math.abs(deltaSeconds))} timing`;
        addLyricsRevision(selectedAudioFilename, shiftedContent, label, {
          isNew: false,
          isCustomized: true,
          timingLevel: detectLyricsTimingLevel(shiftedContent),
          timestampCount: countTimestamps(shiftedContent),
          allowDuplicate: true,
        });

        editor.value = shiftedContent;
        // Preserve the highlight so the user can keep pressing to nudge the same
        // word(s)/line(s) without re-selecting each time. Re-focus the editor so
        // the selection stays visibly highlighted (a blurred textarea hides it);
        // it clears naturally once the user selects elsewhere or clicks away.
        if (hasSelection) {
          const clampedEnd = Math.min(selEnd, shiftedContent.length);
          const clampedStart = Math.min(selStart, clampedEnd);
          try {
            editor.focus({ preventScroll: true });
            editor.setSelectionRange(clampedStart, clampedEnd);
          } catch (e) {}
        }
        lastCommittedLyricsText = shiftedContent;
        // The shifted text becomes the new baseline for manual S/F capture.
        manualTimingState.words = extractWordsFromEditor();
        manualTimingState.baselineEntries = [];
        manualTimingState.entries = [];
        manualTimingState.cursor = 0;
        manualTimingState.activeStart = null;
        manualTimingState.previewFromAppliedLyrics = true;
        manualTimingState.revisionIndex = null;
        parseLrcData(shiftedContent);
        persistManualTiming();
        updateManualTimingHud();
        updatePreview();

        try {
          const fd = new FormData();
          fd.append("filename", selectedLrcFilename);
          fd.append("content", shiftedContent);
          const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
          const data = await res.json();
          if (!res.ok) throw new Error(data.message || "Lyrics save failed.");
          await autoSaveManualTimingDraft();
          setStatus(`Created a new revision: ${label}.`);
        } catch (err) {
          setStatus(`Failed to shift timing: ${err}`, true);
        }
      }

      // ================================================================
      // SECTION: Chorus Word Marking
      // Purpose: Let the user select words in the lyrics editor and mark them
      //          as chorus vocals. Marked words are stored in the LRC as
      //          [00:12.34]*word and rendered bold via an overlay, then used to
      //          rebuild the vocalized chorus mp3 at chorus-render time.
      // ================================================================

      const CHORUS_LINE_RE = /^(\s*\[\d+:\d+(?:\.\d{1,3})?\])(\s*\*)?(.*)$/;

      // The editor's value is assigned programmatically from many places
      // (revision switching, project load, auto-correct, transcription...).
      // Patching the property once keeps the bold overlay in sync everywhere
      // instead of requiring a call after every assignment.
      function installChorusOverlayHook() {
        const editor = document.getElementById("lyricsEditor");
        if (!editor || editor._chorusHooked) return;
        const proto = Object.getPrototypeOf(editor);
        const desc = Object.getOwnPropertyDescriptor(proto, "value");
        if (!desc || !desc.set) return;
        Object.defineProperty(editor, "value", {
          get() { return desc.get.call(this); },
          set(v) {
            desc.set.call(this, v);
            try { renderChorusOverlay(); } catch (_) {}
          },
          configurable: true,
        });
        editor._chorusHooked = true;
        renderChorusOverlay();
      }

      function setLineChorusMark(line, marked) {
        const m = line.match(CHORUS_LINE_RE);
        if (!m) return line;              // untimed line: nothing to mark
        if (!m[3].trim()) return line;    // no lyric text on this line
        return marked ? `${m[1]}*${m[3]}` : `${m[1]}${m[3]}`;
      }

      function isLineChorusMarked(line) {
        const m = line.match(CHORUS_LINE_RE);
        return !!(m && m[2]);
      }

      function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, (c) =>
          ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
      }

      function renderChorusOverlay() {
        const editor = document.getElementById("lyricsEditor");
        const overlay = document.getElementById("lyricsChorusOverlay");
        if (!editor || !overlay) return;
        const html = (editor.value || "").split("\n").map((line) => {
          const classes = [];
          if (isLineChorusMarked(line)) classes.push("chorus-on");
          const gender = getLineGender(line);
          if (gender === "m") classes.push("gender-m");
          else if (gender === "f") classes.push("gender-f");
          else if (gender === "b") classes.push("gender-b");
          return classes.length
            ? `<span class="${classes.join(" ")}">${escapeHtml(line)}</span>`
            : escapeHtml(line);
        }).join("\n");
        // Trailing newline needs a filler so the last line stays scrollable.
        overlay.innerHTML = html + "\n";
        syncChorusOverlayScroll();
        updateChorusMarkInfo();
        updateGenderMarkInfo();
      }

      function syncChorusOverlayScroll() {
        const editor = document.getElementById("lyricsEditor");
        const overlay = document.getElementById("lyricsChorusOverlay");
        if (!editor || !overlay) return;
        overlay.scrollTop = editor.scrollTop;
        overlay.scrollLeft = editor.scrollLeft;
      }

      function updateChorusMarkInfo() {
        const info = document.getElementById("chorusMarkInfo");
        if (!info) return;
        const editor = document.getElementById("lyricsEditor");
        const count = (editor?.value || "").split("\n").filter(isLineChorusMarked).length;
        info.textContent = count
          ? `${count} word${count === 1 ? "" : "s"} marked for chorus vocals.`
          : "No words marked for chorus vocals (auto-detect will be used).";
      }

      async function markSelectionChorus(marked) {
        if (!selectedAudioFilename || !selectedLrcFilename) {
          setStatus("Select source media first.", true);
          return;
        }
        const editor = document.getElementById("lyricsEditor");
        const value = editor.value || "";
        let start = editor.selectionStart;
        let end = editor.selectionEnd;
        if (start === end) {
          setStatus("Highlight the words in the editor first.", true);
          return;
        }
        // Expand the selection outward to whole lines: each LRC line carries one
        // word plus its timestamps, so partial selections must still mark the
        // entire word they touch.
        const lineStart = value.lastIndexOf("\n", start - 1) + 1;
        let lineEnd = value.indexOf("\n", end - 1);
        if (lineEnd === -1) lineEnd = value.length;

        const before = value.slice(0, lineStart);
        const target = value.slice(lineStart, lineEnd);
        const after = value.slice(lineEnd);
        const updatedBlock = target.split("\n").map((l) => setLineChorusMark(l, marked)).join("\n");
        if (updatedBlock === target) {
          setStatus(marked ? "Those words are already in the chorus." : "Those words were not in the chorus.");
          return;
        }
        const updated = before + updatedBlock + after;

        editor.value = updated;
        // Keep the user's highlight so they can immediately press the other button.
        editor.setSelectionRange(lineStart, lineStart + updatedBlock.length);
        renderChorusOverlay();
        updateTimingRevision(updated);
        lastCommittedLyricsText = updated;

        const changed = updatedBlock.split("\n").filter((l, i) => l !== target.split("\n")[i]).length;
        try {
          const fd = new FormData();
          fd.append("filename", selectedLrcFilename);
          fd.append("content", updated);
          const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
          const data = await res.json();
          if (!res.ok) throw new Error(data.message || "Lyrics save failed.");
          setStatus(`${marked ? "Added" : "Removed"} ${changed} word${changed === 1 ? "" : "s"} ${marked ? "to" : "from"} the chorus vocals. Render with Chorus to hear it.`);
        } catch (err) {
          setStatus(`Failed to update chorus marks: ${err}`, true);
        }
      }

      // ================================================================
      // SECTION: Duet Voice (Male/Female) Line Marking
      // Purpose: Let the user color whole lyric lines with a Male (blue) or
      //          Female (pink) scheme for duets. Stored inline in the LRC as a
      //          {m}/{f} marker right after the timestamp so it survives saves,
      //          drives the preview canvas, and burns into the ASS render.
      // ================================================================

      // Group1: optional leading whitespace + optional timestamp + optional chorus star.
      // Group2: optional gender token. Group3: the remaining lyric text.
      const GENDER_LINE_RE = /^(\s*(?:\[\d+:\d+(?:\.\d{1,3})?\])?\*?)(\{[mfb]\})?(.*)$/;

      function getLineGender(line) {
        const m = String(line).match(GENDER_LINE_RE);
        return m && m[2] ? m[2][1] : null;
      }

      function setLineGenderMark(line, gender) {
        const m = String(line).match(GENDER_LINE_RE);
        if (!m) return line;
        if (!m[3].trim()) return line; // no lyric text on this line
        return gender ? `${m[1]}{${gender}}${m[3]}` : `${m[1]}${m[3]}`;
      }

      function updateGenderMarkInfo() {
        const info = document.getElementById("genderMarkInfo");
        if (!info) return;
        const editor = document.getElementById("lyricsEditor");
        const lines = (editor?.value || "").split("\n");
        let male = 0, female = 0, both = 0;
        lines.forEach((l) => {
          const g = getLineGender(l);
          if (g === "m") male += 1;
          else if (g === "f") female += 1;
          else if (g === "b") both += 1;
        });
        info.textContent = (male || female || both)
          ? `${male} Male, ${female} Female, ${both} Both.`
          : "No lines assigned a duet voice.";
      }

      // Duet color schemes edited through the single Primary/Secondary/Outline bar.
      // The toggle icon selects which voice the bar currently edits. Male is the
      // default scheme and is what ungendered lyrics render with.
      const genderColorSchemes = {
        m: { p: "#afbcf0", s: "#366fe0", o: "#020512" },
        f: { p: "#fab1db", s: "#960c5b", o: "#1a010f" },
        b: { p: "#8ee084", s: "#17730d", o: "#041c01" },
      };
      let activeColorGender = "m";
      const GENDER_TOGGLE_ICON = { m: "♂", f: "♀", b: "⚥" };
      const GENDER_TOGGLE_TITLE = {
        m: "Editing Male / default colors — click to switch voice",
        f: "Editing Female colors — click to switch voice",
        b: "Editing Both colors — click to switch voice",
      };

      function syncActiveSchemeFromInputs() {
        genderColorSchemes[activeColorGender] = {
          p: document.getElementById("primaryColor").value,
          s: document.getElementById("secondaryColor").value,
          o: document.getElementById("outlineColor").value,
        };
      }

      function loadSchemeIntoInputs(g) {
        const s = genderColorSchemes[g];
        if (!s) return;
        document.getElementById("primaryColor").value = s.p;
        document.getElementById("secondaryColor").value = s.s;
        document.getElementById("outlineColor").value = s.o;
      }

      function updateColorGenderToggle() {
        const btn = document.getElementById("colorGenderToggle");
        if (!btn) return;
        btn.textContent = GENDER_TOGGLE_ICON[activeColorGender];
        btn.title = GENDER_TOGGLE_TITLE[activeColorGender];
        btn.className = `step-btn color-gender-toggle color-gender-${activeColorGender}`;
      }

      function onGenderColorInput() {
        syncActiveSchemeFromInputs();
        updatePreview();
      }

      function cycleColorGender() {
        syncActiveSchemeFromInputs();
        activeColorGender = activeColorGender === "m" ? "f" : activeColorGender === "f" ? "b" : "m";
        loadSchemeIntoInputs(activeColorGender);
        updateColorGenderToggle();
        updatePreview();
      }

      async function markSelectionGender(code) {
        // code: 'm' | 'f' | 'b' | null (None removes any voice assignment).
        if (!selectedAudioFilename || !selectedLrcFilename) {
          setStatus("Select source media first.", true);
          return;
        }
        const editor = document.getElementById("lyricsEditor");
        const value = editor.value || "";
        const start = editor.selectionStart;
        const end = editor.selectionEnd;
        if (start === end) {
          setStatus("Highlight the lines in the editor first.", true);
          return;
        }
        // Expand the selection outward to whole lines so a partial highlight still
        // affects every line it touches.
        const lineStart = value.lastIndexOf("\n", start - 1) + 1;
        let lineEnd = value.indexOf("\n", end - 1);
        if (lineEnd === -1) lineEnd = value.length;

        const before = value.slice(0, lineStart);
        const target = value.slice(lineStart, lineEnd);
        const after = value.slice(lineEnd);
        const updatedBlock = target.split("\n").map((l) => {
          const m = l.match(GENDER_LINE_RE);
          if (!m || !m[3].trim()) return l;
          return setLineGenderMark(l, code);
        }).join("\n");
        if (updatedBlock === target) {
          setStatus("Highlight lines that contain lyric text first.");
          return;
        }
        const updated = before + updatedBlock + after;

        editor.value = updated;
        editor.setSelectionRange(lineStart, lineStart + updatedBlock.length);
        renderChorusOverlay();
        updateTimingRevision(updated);
        lastCommittedLyricsText = updated;
        parseLrcData(updated);
        updatePreview();

        const label = { m: "Male", f: "Female", b: "Both" }[code] || "None";
        try {
          const fd = new FormData();
          fd.append("filename", selectedLrcFilename);
          fd.append("content", updated);
          const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
          const data = await res.json();
          if (!res.ok) throw new Error(data.message || "Lyrics save failed.");
          setStatus(code ? `Assigned those lines to the ${label} voice.` : "Removed the voice assignment from those lines.");
        } catch (err) {
          setStatus(`Failed to update duet voices: ${err}`, true);
        }
      }

      function detectLyricsTimingLevel(content) {
        /*
        Detect timing level of lyrics content:
        - 3: Syllable-level (4+ timestamps per line average)
        - 2: Word-level (2-3 timestamps per line average)
        - 1: Line-level (1 timestamp per line)
        - 0: No timing
        */
        const lines = String(content || "").split("\n");
        if (lines.length === 0) return 0;

        let timestampCounts = [];
        let totalTimestamps = 0;

        for (const line of lines) {
          const lineTimestamps = (line.match(/\[\d+:\d+(?:\.\d+)?\]/g) || []).length;
          if (lineTimestamps > 0) {
            timestampCounts.push(lineTimestamps);
            totalTimestamps += lineTimestamps;
          }
        }

        if (totalTimestamps === 0) return 0; // No timing

        const avgTimestampsPerLine = totalTimestamps / Math.max(1, timestampCounts.length);

        if (avgTimestampsPerLine >= 4 && totalTimestamps >= 20) {
          return 3; // Syllable-level
        } else if (avgTimestampsPerLine >= 1.5 && totalTimestamps >= 10) {
          return 2; // Word-level
        } else if (avgTimestampsPerLine >= 0.8) {
          return 1; // Line-level
        }
        return 0; // No recognizable timing
      }

      function countTimestamps(content) {
        const matches = String(content || "").match(/\[\d+:\d+(?:\.\d+)?\]/g) || [];
        return matches.length;
      }

      function ensureLyricsRevisionSeed(content, reason = "seed") {
        if (!selectedAudioFilename) return;
        const history = getLyricsRevisionHistory(selectedAudioFilename);
        if (history.length === 0) addLyricsRevision(selectedAudioFilename, content, reason);
      }

      function formatRevisionLabel(entry) {
        if (!entry) return "No revision";
        const dt = new Date(entry.at || Date.now());
        const modDt = new Date(entry.modifiedAt || entry.at || Date.now());
        const parts = [];
        parts.push(`${dt.toLocaleString()}`);
        if (entry.reason) parts.push(`(${entry.reason})`);
        const badges = [];
        if (entry.isNew) badges.push("🆕 New");
        if (entry.isCustomized) badges.push("✏️ Customized");
        if (badges.length > 0) parts.push(badges.join(" | "));
        if (entry.provider) parts.push(`Source: ${entry.provider}`);

        // Add timing level info
        if (entry.timingLevel !== undefined) {
          const timingNames = { 3: "🎵 Syllable", 2: "🎶 Word", 1: "📄 Line", 0: "⏳ No timing" };
          const timingDesc = timingNames[entry.timingLevel] || "Unknown";
          const timeStampInfo = entry.timestampCount ? ` (${entry.timestampCount} timestamps)` : "";
          parts.push(`Timing: ${timingDesc}${timeStampInfo}`);
        }

        if (Math.abs((entry.modifiedAt || entry.at) - (entry.at || Date.now())) > 60000) parts.push(`Modified: ${modDt.toLocaleString()}`);
        return parts.join(" | ");
      }

      function updateLyricsRevisionMeta() {
        const meta = document.getElementById("lyricsRevisionMeta");
        if (!meta) return;
        const history = selectedAudioFilename ? getLyricsRevisionHistory(selectedAudioFilename) : [];
        const cursor = selectedAudioFilename ? _getRevisionCursor(selectedAudioFilename) : -1;
        if (cursor < 0 || !history[cursor]) {
          meta.innerHTML = "<span style='color:var(--text-muted)'>Revision -</span>";
          meta.title = "No lyric revisions available";
          return;
        }
        const entry = history[cursor];
        const revisionNum = `<span style='color:var(--accent)'>Rev ${cursor + 1}/${history.length}</span>`;
        const formatLabel = formatRevisionLabel(entry);
        const label = `${revisionNum}<br/>${formatLabel}`;
        meta.innerHTML = label;
        meta.title = formatLabel;
      }

      function _getRevisionCursor(audioFilename) {
        const history = getLyricsRevisionHistory(audioFilename);
        if (history.length === 0) return -1;
        let idx = Number(lyricsRevisionCursorByAudio[audioFilename]);
        if (!Number.isFinite(idx)) idx = history.length - 1;
        idx = Math.max(0, Math.min(history.length - 1, idx));
        lyricsRevisionCursorByAudio[audioFilename] = idx;
        return idx;
      }

      function _loadLyricsRevisionByIndex(audioFilename, idx) {
        const history = getLyricsRevisionHistory(audioFilename);
        if (history.length === 0) return false;
        const clamped = Math.max(0, Math.min(history.length - 1, Number(idx) || 0));
        const target = history[clamped];
        lyricsRevisionCursorByAudio[audioFilename] = clamped;
        document.getElementById("lyricsEditor").value = target.content || "";
        parseLrcData(target.content || "");
        manualTimingState.words = extractWordsFromEditor();
        manualTimingState.baselineEntries = buildTimedWordEntriesFromLyrics();
        manualTimingState.entries = [];
        manualTimingState.cursor = 0;
        manualTimingState.activeStart = null;
        manualTimingState.previewFromAppliedLyrics = manualTimingState.baselineEntries.length > 0;
        manualTimingState.revisionIndex = null;
        persistManualTiming();
        updateManualTimingHud();
        updateLyricsRevisionMeta();
        updatePreview();
        // Auto-save the selected revision as the active lyrics (no manual commit needed).
        if (selectedLrcFilename) {
          const content = target.content || "";
          lastCommittedLyricsText = content;
          const fd = new FormData();
          fd.append("filename", selectedLrcFilename);
          fd.append("content", content);
          fetch("/api/save-lyrics", { method: "POST", body: fd }).catch((err) => console.warn("Auto-save revision failed", err));
        }
        const pos = clamped + 1;
        setStatus(`Revision ${pos}/${history.length} loaded & saved: ${formatRevisionLabel(target)}.`);
        return true;
      }

      function stepLyricsRevision(delta) {
        if (!selectedAudioFilename) { setStatus("Select a project first.", true); return; }
        const history = getLyricsRevisionHistory(selectedAudioFilename);
        if (history.length < 2) {
          setStatus("No additional committed revisions yet.", true);
          return;
        }
        const cursor = _getRevisionCursor(selectedAudioFilename);
        const next = cursor + Number(delta || 0);
        if (next < 0 || next >= history.length) {
          setStatus(next < 0 ? "Already at oldest revision." : "Already at newest revision.");
          return;
        }
        _loadLyricsRevisionByIndex(selectedAudioFilename, next);
      }

      function toggleSection(sectionId, button) {
        const section = document.getElementById(sectionId);
        if (!section) return;
        const collapsed = section.classList.toggle("hidden");
        button.classList.toggle("collapsed", collapsed);
        // Shrink the whole panel to just its header instead of leaving an empty
        // box: the card stops stretching and the docked panel aligns to the top
        // of its grid cell so neighbours can reclaim the freed space.
        const card = button.closest(".card");
        if (card) card.classList.toggle("collapsed", collapsed);
        const panel = button.closest(".dock-panel");
        if (panel) panel.classList.toggle("collapsed", collapsed);
      }

      function onFontSizeChanged() { updatePreview(); }

      // ================================================================
      // SECTION: Dynamic FX Panel Management
      // Purpose: Populate FX customization options based on selected effect
      //          including Scope, Speed, and effect-specific parameters
      // ================================================================

      // FX Scope/Speed live inside the dynamically rebuilt FX panel, so they are
      // absent from the DOM whenever FX is "none". These module-level caches are
      // the source of truth so render payloads never crash on a missing element.
      let fxScopeState = "page";
      let fxSpeedState = "0.6";

      function getFxScope() {
        const el = document.getElementById("fxScope");
        if (el && el.value) fxScopeState = el.value;
        return fxScopeState;
      }

      function getFxSpeed() {
        const el = document.getElementById("fxSpeed");
        if (el && el.value) fxSpeedState = el.value;
        return fxSpeedState;
      }

      // Outline thickness in preview-space pixels. 0 = no outline, -1 = auto
      // (font-relative default). Returned as a number for the render payload.
      function getOutlineWidthSetting() {
        const el = document.getElementById("outlineWidth");
        if (!el || el.value === "" || el.value === null) return -1;
        const n = parseInt(el.value, 10);
        return Number.isFinite(n) ? Math.max(0, Math.min(40, n)) : -1;
      }

      function setFxScope(value) {
        if (!value) return;
        fxScopeState = String(value);
        const el = document.getElementById("fxScope");
        if (el) el.value = fxScopeState;
      }

      function setFxSpeed(value) {
        if (value === undefined || value === null || value === "") return;
        fxSpeedState = String(value);
        const el = document.getElementById("fxSpeed");
        if (el) el.value = fxSpeedState;
      }

      // Build FX Customization Panel based on selected effect
      function updateFxPanel() {
        const fxSelect = document.getElementById("transitionStyle");
        const selectedFx = fxSelect ? fxSelect.value : "none";
        const panelBody = document.getElementById("sectionFxPanelBody");
        const panelContent = document.getElementById("fxPanelContent");
        const panelCard = document.getElementById("fxDockPanel") || document.getElementById("fxPanelCard");
        const subtitle = document.getElementById("fxPanelSubtitle");

        if (!panelBody || !panelContent) return;

        // Hide the whole card when no FX is selected, rather than only the body,
        // so an empty titled panel doesn't occupy sidebar space.
        if (selectedFx === "none") {
          if (panelCard) panelCard.style.display = "none";
          if (subtitle) subtitle.textContent = "Select an FX effect to customize options";
          return;
        }

        if (panelCard) panelCard.style.display = "";

        // Common FX header controls (Scope + Speed apply to all effects)
        const commonControls = `
          <div class="compact-ctrl" style="border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 10px;">
            <span style="color: var(--text-muted)">Scope</span>
            <select id="fxScope" onchange="updatePreview()" style="width: 65px" title="Transition Scope">
              <option value="page">Page</option><option value="line">Line</option><option value="word">Word</option>
            </select>
            <span style="color: var(--text-muted); margin-left: 10px">Speed</span>
            <select id="fxSpeed" onchange="updatePreview()" style="width: 65px" title="Transition Speed">
              <option value="0.2">Fast</option><option value="0.6" selected>Med</option><option value="1.2">Slow</option>
            </select>
          </div>
        `;

        // Build FX-specific controls
        let html = commonControls;
        let fxTitle = "";

        switch(selectedFx) {
          case "bouncing-ball":
            fxTitle = "Bouncing Ball Options";
            html = commonControls + `
              <div class="compact-ctrl" title="Ball Radius">
                <span style="color: var(--text-muted)">Ball Size</span>
                <button class="step-btn" type="button" onpointerdown="startControlStep('ballRadius', 1, 10, 200, 'ballRadiusNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▲</button>
                <button class="step-btn" type="button" onpointerdown="startControlStep('ballRadius', -1, 10, 200, 'ballRadiusNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▼</button>
                <input type="hidden" id="ballRadius" value="26" />
                <input type="number" id="ballRadiusNum" min="10" max="100" value="26" oninput="document.getElementById('ballRadius').value=this.value; updatePreview()" style="width: 45px; padding: 2px; font-size: 11px;" />
              </div>
              <div class="compact-ctrl" title="Arc Height">
                <span style="color: var(--text-muted)">Arc Height</span>
                <button class="step-btn" type="button" onpointerdown="startControlStep('arcHeight', 1, 20, 500, 'arcHeightNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▲</button>
                <button class="step-btn" type="button" onpointerdown="startControlStep('arcHeight', -1, 20, 500, 'arcHeightNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▼</button>
                <input type="hidden" id="arcHeight" value="78" />
                <input type="number" id="arcHeightNum" min="20" max="500" value="78" oninput="document.getElementById('arcHeight').value=this.value; updatePreview()" style="width: 45px; padding: 2px; font-size: 11px;" />
              </div>
              <div class="compact-ctrl" title="Bounces Per Second During Gaps">
                <span style="color: var(--text-muted)">Bounce Freq</span>
                <button class="step-btn" type="button" onpointerdown="startControlStep('bouncePerSec', 0.05, 0.05, 5.0, 'bouncePerSecNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▲</button>
                <button class="step-btn" type="button" onpointerdown="startControlStep('bouncePerSec', -0.05, 0.05, 5.0, 'bouncePerSecNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▼</button>
                <input type="hidden" id="bouncePerSec" value="0.1" />
                <input type="number" id="bouncePerSecNum" min="0.00" max="5.0" step="0.05" value="0.1" oninput="document.getElementById('bouncePerSec').value=this.value; updatePreview()" style="width: 45px; padding: 2px; font-size: 11px;" />
              </div>
              <div class="compact-ctrl">
                <span style="color: var(--text-muted)">Colors</span>
                <label style="font-size: 10px; color: var(--text-muted); margin-left: 5px">Ball:</label>
                <input type="color" id="ballColor" value="#ffffff" title="Ball Color" oninput="updatePreview()" style="width: 30px; height: 24px; cursor: pointer;" />
                <label style="font-size: 10px; color: var(--text-muted); margin-left: 8px">Outline:</label>
                <input type="color" id="ballOutlineColor" value="#000000" title="Ball Outline Color" oninput="updatePreview()" style="width: 30px; height: 24px; cursor: pointer;" />
              </div>
              <div class="compact-ctrl" title="Use any image as the ball — PNG, JPG, GIF, WebP, BMP, ICO or SVG are all supported.">
              <span style="color: var(--text-muted)">Ball Image</span>
              <input type="text" id="ballIcon" onchange="updatePreview()" placeholder="/icons/myimage.png (any image type)" title="Path to any image file (PNG, JPG, GIF, WebP, BMP, ICO, SVG) to use as the ball" style="width: 110px">
              <input type="file" id="ballIconFile" accept="image/png,image/gif,image/webp,image/jpeg,image/bmp,image/x-icon,image/svg+xml,.png,.gif,.webp,.jpg,.jpeg,.bmp,.ico,.svg" style="display:none" onchange="uploadBallIcon(this)">
              <button type="button" class="btn btn-small" title="Browse for any image (PNG, JPG, GIF, WebP, BMP, ICO, SVG) to use as the ball" onclick="document.getElementById('ballIconFile').click()">Browse…</button>
              <button type="button" class="btn btn-small" title="Clear the image and use the vector ball" onclick="clearBallIcon()">✕</button>
              </div>
              <div class="compact-ctrl" title="Ball vertical position: -15 rests the ball on the words, 0 is the default height, +15 floats it high above them. Raise it if the ball sits in the middle of the text at large resolutions.">
                <span style="color: var(--text-muted)">Vert Align</span>
                <button class="step-btn" type="button" onpointerdown="startControlStep('ballAlign', 1, -15, 15, 'ballAlignNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▲</button>
                <button class="step-btn" type="button" onpointerdown="startControlStep('ballAlign', -1, -15, 15, 'ballAlignNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▼</button>
                <input type="hidden" id="ballAlign" value="0" />
                <input type="number" id="ballAlignNum" min="-15" max="15" step="1" value="0" oninput="document.getElementById('ballAlign').value=this.value; updatePreview()" title="Ball vertical position (-15 to +15)" style="width: 45px; padding: 2px; font-size: 11px;" />
              </div>
                <div class="compact-ctrl" title="Spin Rotations per Hop">
                <span style="color: var(--text-muted)">Rotations</span>
                <button class="step-btn" type="button" onpointerdown="startControlStep('ballRotation', 0.05, 0.05, 5, 'ballRotationNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▲</button>
                <button class="step-btn" type="button" onpointerdown="startControlStep('ballRotation', -0.05, 0.05, 5, 'ballRotationNum', event)" onpointerup="stopControlStep(event)" onpointerleave="stopControlStep(event)">▼</button>
                <input type="hidden" id="ballRotation" value="0.1" />
                <input type="number" id="ballRotationNum" min="0.00" max="5.0" step="0.05" value="0.1" oninput="document.getElementById('ballRotation').value=this.value; updatePreview()" style="width: 45px; padding: 2px; font-size: 11px;" />
            </div>
            `;
            break;

          case "fade":
            fxTitle = "Fade Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Fade intensity/opacity">
                <span style="color: var(--text-muted)">Fade Amount</span>
                <select id="fadeIntensity" onchange="updatePreview()" style="width: 70px">
                  <option value="light">Light</option>
                  <option value="medium" selected>Medium</option>
                  <option value="heavy">Heavy</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Fade in/out with adjustable intensity
              </div>
            `;
            break;

          case "zoom":
            fxTitle = "Zoom Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Zoom scale range">
                <span style="color: var(--text-muted)">Zoom Scale</span>
                <select id="zoomScale" onchange="updatePreview()" style="width: 70px">
                  <option value="subtle">Subtle</option>
                  <option value="medium" selected>Medium</option>
                  <option value="dramatic">Dramatic</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Zoom in/out effect at various scales
              </div>
            `;
            break;

          case "blur":
            fxTitle = "Blur Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Blur radius">
                <span style="color: var(--text-muted)">Blur Amount</span>
                <select id="blurAmount" onchange="updatePreview()" style="width: 70px">
                  <option value="5">Subtle</option>
                  <option value="10" selected>Medium</option>
                  <option value="20">Heavy</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Blur in/out transition effect
              </div>
            `;
            break;

          case "rotate-360":
            fxTitle = "360 Rotate Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Rotation direction">
                <span style="color: var(--text-muted)">Direction</span>
                <select id="rotateDirection" onchange="updatePreview()" style="width: 70px">
                  <option value="cw" selected>Clockwise</option>
                  <option value="ccw">Counter-CW</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Full 360° rotation effect
              </div>
            `;
            break;

          case "glimmer":
            fxTitle = "Glimmer Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Glimmer brightness">
                <span style="color: var(--text-muted)">Intensity</span>
                <select id="glimmerIntensity" onchange="updatePreview()" style="width: 70px">
                  <option value="soft">Soft</option>
                  <option value="normal" selected>Normal</option>
                  <option value="bright">Bright</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Sparkle/glimmer shine effect
              </div>
            `;
            break;

          case "shake":
            fxTitle = "Shake Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Shake intensity">
                <span style="color: var(--text-muted)">Intensity</span>
                <select id="shakeIntensity" onchange="updatePreview()" style="width: 70px">
                  <option value="small">Subtle</option>
                  <option value="medium" selected>Medium</option>
                  <option value="strong">Strong</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Vibration/jitter effect
              </div>
            `;
            break;

          case "flip":
            fxTitle = "Flip Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Flip direction">
                <span style="color: var(--text-muted)">Direction</span>
                <select id="flipDirection" onchange="updatePreview()" style="width: 70px">
                  <option value="horizontal" selected>Horizontal</option>
                  <option value="vertical">Vertical</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 3D flip animation
              </div>
            `;
            break;

          case "pulse":
            fxTitle = "Pulse Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Pulse intensity">
                <span style="color: var(--text-muted)">Intensity</span>
                <select id="pulseIntensity" onchange="updatePreview()" style="width: 70px">
                  <option value="subtle">Subtle</option>
                  <option value="normal" selected>Normal</option>
                  <option value="strong">Strong</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Expand/contract heartbeat effect
              </div>
            `;
            break;

          case "sway":
            fxTitle = "Sway Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Sway amount">
                <span style="color: var(--text-muted)">Amount</span>
                <select id="swayAmount" onchange="updatePreview()" style="width: 70px">
                  <option value="slight">Slight</option>
                  <option value="normal" selected>Normal</option>
                  <option value="extreme">Extreme</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Side-to-side swaying motion
              </div>
            `;
            break;

          case "skew":
            fxTitle = "Skew Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Skew direction">
                <span style="color: var(--text-muted)">Direction</span>
                <select id="skewDirection" onchange="updatePreview()" style="width: 70px">
                  <option value="x" selected>Horizontal</option>
                  <option value="y">Vertical</option>
                  <option value="both">Both</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 3D perspective skew effect
              </div>
            `;
            break;

          case "stamp":
            fxTitle = "Stamp Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Stamp style">
                <span style="color: var(--text-muted)">Style</span>
                <select id="stampStyle" onchange="updatePreview()" style="width: 70px">
                  <option value="punch" selected>Punch</option>
                  <option value="fade">Fade</option>
                  <option value="bounce">Bounce</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Sudden appearance with style
              </div>
            `;
            break;

          case "focus":
            fxTitle = "Focus Effect";
            html = commonControls + `
              <div class="compact-ctrl" title="Focus zoom level">
                <span style="color: var(--text-muted)">Zoom Level</span>
                <select id="focusZoom" onchange="updatePreview()" style="width: 70px">
                  <option value="1.2">Subtle</option>
                  <option value="1.5" selected>Normal</option>
                  <option value="2.0">Dramatic</option>
                </select>
              </div>
              <div style="color: var(--text-muted); font-size: 11px; margin-top: 8px; padding: 8px; background: rgba(100,200,255,0.1); border-radius: 4px;">
                💡 Zoom and dim surroundings
              </div>
            `;
            break;

          default:
            // For other effects like "pop", "slide", "drop", "none"
            html = commonControls + `
              <div style="color: var(--text-muted); font-size: 11px; padding: 12px; background: rgba(100,200,255,0.1); border-radius: 4px; text-align: center;">
                ${selectedFx.charAt(0).toUpperCase() + selectedFx.slice(1)} effect selected. Limited customization available.
              </div>
            `;
        }

        subtitle.textContent = fxTitle;

        // Preserve current fxScope/fxSpeed before the DOM nodes are destroyed.
        // These live inside the dynamic panel, so cache them in module state to
        // survive both FX switches and the "none" case where they don't exist.
        fxScopeState = document.getElementById("fxScope")?.value || fxScopeState;
        fxSpeedState = document.getElementById("fxSpeed")?.value || fxSpeedState;

        panelContent.innerHTML = html;

        // Restore fxScope and fxSpeed values after HTML is inserted
        const newFxScope = document.getElementById("fxScope");
        const newFxSpeed = document.getElementById("fxSpeed");
        if (newFxScope) newFxScope.value = fxScopeState;
        if (newFxSpeed) newFxSpeed.value = fxSpeedState;
      }

      // Listen for FX changes and update panel
      document.addEventListener("DOMContentLoaded", () => {
        const fxSelect = document.getElementById("transitionStyle");
        if (fxSelect) {
          fxSelect.addEventListener("change", updateFxPanel);
        }
        installChorusOverlayHook();
        updateColorGenderToggle();
      });

      function getThemeStorageKey() {
        return "onepage-karaoke-theme-id";
      }

      function applyThemeVars(vars) {
        const root = document.documentElement;
        Object.entries(vars || {}).forEach(([key, value]) => {
          root.style.setProperty(`--${key}`, value);
        });
      }

      function updateThemeMeta(theme) {
        const meta = document.getElementById("themeMeta");
        if (!meta) return;
        if (!theme) {
          meta.textContent = "No theme metadata available.";
          return;
        }
        const sourceBits = [theme.source_name, theme.license].filter(Boolean).join(" | ");
        meta.textContent = [theme.description || "", sourceBits, theme.source_url || ""].filter(Boolean).join(" | ");
      }

      function applyThemeById(themeId, persist = true) {
        const theme = themeCatalog.find((item) => item.id === themeId) || themeCatalog[0];
        if (!theme) return;
        currentThemeId = theme.id;
        document.getElementById("themeSelect").value = theme.id;
        applyThemeVars(theme.vars || {});
        applyControlContrastVars();
        karaokePlaceholderImg = null;
        drawVocalWaveform();
        updateThemeMeta(theme);
        if (persist) localStorage.setItem(getThemeStorageKey(), theme.id);

        // Play theme animation when theme is manually selected (persist = true)
        if (persist) {
          setTimeout(() => playThemeAnimation(), 50);
        }
      }

      function applySelectedTheme() {
        const select = document.getElementById("themeSelect");
        if (!select) return;
        applyThemeById(select.value, true);
      }

      async function loadThemes() {
        const select = document.getElementById("themeSelect");
        if (!select) return;
        const meta = document.getElementById("themeMeta");
        // Start from the built-in themes so the picker is never empty, then try
        // to merge in any backend-provided themes. Backend themes with a matching
        // id override the built-in of the same id.
        const byId = new Map(BUILTIN_THEMES.map((t) => [t.id, { ...t }]));
        let backendNote = "";
        try {
          const { res, data } = await fetchJsonSafe("/api/themes", {}, "Themes API");
          if (!res.ok) throw new Error(data.message || `Themes API failed (HTTP ${res.status})`);
          (data.themes || []).forEach((t) => {
            if (t && t.id) byId.set(t.id, t);
          });
        } catch (err) {
          console.warn("Backend themes unavailable, using built-in themes.", err);
          backendNote = "Using built-in themes (backend theme catalog unavailable).";
        }
        themeCatalog = Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name));
        select.innerHTML = "";
        themeCatalog.forEach((theme) => {
          const opt = document.createElement("option");
          opt.value = theme.id;
          opt.textContent = theme.name;
          select.appendChild(opt);
        });
        const preferred = localStorage.getItem(getThemeStorageKey())
          || (themeCatalog[0] && themeCatalog[0].id) || "";
        applyThemeById(preferred, false);
        if (backendNote && meta) meta.textContent = backendNote;
      }

      function formatDebugIssue(issue) {
        const file = issue.file || "(no file)";
        const line = issue.line || 0;
        const column = issue.column || 0;
        const where = line ? `${file}:${line}${column ? `:${column}` : ""}` : file;
        const parts = [`[${(issue.kind || "issue").toUpperCase()}] ${issue.message || "Unknown issue"}`, where];
        if (issue.code) parts.push(issue.code);
        if (issue.pointer) parts.push(issue.pointer);
        if (issue.trace) parts.push(issue.trace);
        return parts.join("\n");
      }

      function pushFrontendDebugIssue(issue) {
        if (!issue) return;
        frontendDebugIssues.unshift({
          kind: "frontend",
          severity: issue.severity || "error",
          message: issue.message || "Unknown frontend issue",
          file: issue.file || (issue.sourceURL || "index.html"),
          line: Number(issue.line || 0),
          column: Number(issue.column || 0),
          code: issue.code || "",
          trace: issue.trace || "",
          at: Date.now(),
        });
        frontendDebugIssues = frontendDebugIssues.slice(0, 20);
      }

      window.addEventListener("error", (event) => {
        pushFrontendDebugIssue({
          message: event.message || "Unhandled frontend error",
          file: event.filename || "index.html",
          line: event.lineno || 0,
          column: event.colno || 0,
          trace: event.error && event.error.stack ? String(event.error.stack) : "",
        });
      });

      window.addEventListener("unhandledrejection", (event) => {
        const reason = event.reason;
        pushFrontendDebugIssue({
          message: reason && reason.message ? reason.message : `Unhandled rejection: ${String(reason)}`,
          file: "browser-promise",
          trace: reason && reason.stack ? String(reason.stack) : "",
        });
      });

      async function copyDebugReport() {
        const output = lastDebugReportText || document.getElementById("debugOutput").textContent || "";
        const summary = document.getElementById("debugSummary").textContent || "";
        const full = `${summary}\n\n${output}`.trim();
        if (!full) {
          setStatus("No debug report to copy.", true);
          return;
        }
        try {
          await navigator.clipboard.writeText(full);
          setStatus("Debug report copied to clipboard.");
        } catch (err) {
          setStatus(`Clipboard copy failed: ${err}`, true);
        }
      }

      async function runDebugger() {
        const summaryEl = document.getElementById("debugSummary");
        const outputEl = document.getElementById("debugOutput");
        summaryEl.textContent = "Capturing backend and frontend diagnostics...";
        outputEl.textContent = "Running debugger...";
        try {
          const { data } = await fetchJsonSafe("/api/debug-report", {}, "Debugger API");
          const syntaxIssues = data.syntax_issues || [];
          const runtimeIssues = data.runtime_issues || [];
          const frontendIssues = frontendDebugIssues.slice();
          summaryEl.textContent = `Syntax issues: ${syntaxIssues.length} | Runtime issues: ${runtimeIssues.length} | Frontend issues: ${frontendIssues.length}`;
          const allIssues = [...frontendIssues, ...runtimeIssues, ...syntaxIssues];
          lastDebugReportText = allIssues.length
            ? allIssues.map(formatDebugIssue).join("\n\n------------------------------\n\n")
            : "No backend syntax or runtime issues detected.";
          outputEl.textContent = lastDebugReportText;
        } catch (err) {
          summaryEl.textContent = "Debugger request failed.";
          lastDebugReportText = String(err);
          outputEl.textContent = lastDebugReportText;
        }
      }

      function appendTranscriptionSettings(fd) {
        fd.append("stem_device", document.getElementById("stemDevice").value);
        fd.append("whisper_device", document.getElementById("whisperDevice").value);
        fd.append("whisper_model", document.getElementById("whisperModel").value);
        fd.append("transcription_language", document.getElementById("transcriptionLanguage").value);
        fd.append("profile", activeProfile);
      }

      // Read whichever per-effect sub-option selects are currently injected in
      // the FX panel. Only present selects are returned, so the backend keeps
      // its defaults for everything else.
      function collectFxOptions() {
        const ids = [
          "fadeIntensity", "zoomScale", "blurAmount", "rotateDirection",
          "glimmerIntensity", "shakeIntensity", "flipDirection", "pulseIntensity",
          "swayAmount", "skewDirection", "stampStyle", "focusZoom",
        ];
        const out = {};
        ids.forEach((id) => {
          const el = document.getElementById(id);
          if (el && el.value) out[id] = el.value;
        });
        return out;
      }

      function capturePreviewConfig() {
        const resolution = document.getElementById("previewResolution")?.value || "1280x720";
        const [renderWidth, renderHeight] = resolution.split("x").map((value) => Number(value));
        syncActiveSchemeFromInputs();
        const gm = genderColorSchemes.m, gf = genderColorSchemes.f, gb = genderColorSchemes.b;
        return {
          audio_filename: selectedAudioFilename,
          render_width: Number.isFinite(renderWidth) ? renderWidth : 1280,
          render_height: Number.isFinite(renderHeight) ? renderHeight : 720,
          font_name: document.getElementById("fontSelect").value || "Arial",
          font_size: document.getElementById("fontSize").value,
          line_spacing: document.getElementById("lineSpacing").value,
          word_padding: document.getElementById("wordPadding").value,
          primary_color: gm.p,
          secondary_color: gm.s,
          outline_color: gm.o,
          male_primary_color: gm.p,
          male_secondary_color: gm.s,
          male_outline_color: gm.o,
          female_primary_color: gf.p,
          female_secondary_color: gf.s,
          female_outline_color: gf.o,
          both_primary_color: gb.p,
          both_secondary_color: gb.s,
          both_outline_color: gb.o,
          outline_width: getOutlineWidthSetting(),
          bg_type: document.getElementById("bgType").value,
          bg_color: document.getElementById("bgColor").value,
          transition_style: document.getElementById("transitionStyle").value,
          fx_scope: getFxScope(),
          fx_speed: getFxSpeed(),
          text_effect: document.getElementById("textEffect").value,
          reveal_mode: document.getElementById("revealMode").value,
          preview_line_count: document.getElementById("previewLineCount").value,
          pitch: document.getElementById("pitch").value,
          volume: document.getElementById("volume").value,
          render_device: document.getElementById("renderDevice").value,
          ball_radius: document.getElementById("ballRadius")?.value || "26",
          arc_height: document.getElementById("arcHeight")?.value || "78",
          bounce_per_sec: document.getElementById("bouncePerSec")?.value || "0.1",
          ball_color: document.getElementById("ballColor")?.value || "#ffffff",
          ball_outline_color: document.getElementById("ballOutlineColor")?.value || "#000000",
          ball_icon: document.getElementById("ballIcon")?.value || "",
          ball_rotation: parseFloat(document.getElementById("ballRotation")?.value || "1.5"),
          ball_align: document.getElementById("ballAlign")?.value || "0",
          fx_options: JSON.stringify(collectFxOptions()),
      };
    }

      let controlStepTimer = null;
      let controlStepState = null;

      function startControlStep(id, direction, min, max, displayId, event) {
        event?.preventDefault();
        stopControlStep();
        const apply = () => {
          const input = document.getElementById(id);
          const display = document.getElementById(displayId);
          if (!input || !display) return;
          const value = Math.max(min, Math.min(max, Number(input.value || 0) + direction));
          input.value = value;
          display.value = value;
          updatePreview();
        };
        apply();
        const startedAt = performance.now();
        controlStepState = { apply, startedAt, direction };
        controlStepTimer = window.setInterval(() => {
          const elapsed = performance.now() - startedAt;
          const repeats = elapsed > 3000 ? 8 : elapsed > 1800 ? 4 : elapsed > 800 ? 2 : 1;
          for (let index = 0; index < repeats; index += 1) apply();
        }, 100);
      }

      function stopControlStep(event) {
        event?.preventDefault();
        if (controlStepTimer) window.clearInterval(controlStepTimer);
        controlStepTimer = null;
        controlStepState = null;
      }

      function formatLrcTime(seconds) {
        const safe = Math.max(0, Number(seconds || 0));
        const mins = Math.floor(safe / 60);
        const secs = safe - mins * 60;
        return `${String(mins).padStart(2, "0")}:${secs.toFixed(2).padStart(5, "0")}`;
      }

      function extractWordsFromEditor() {
        const editorText = document.getElementById("lyricsEditor").value || "";
        const stripped = editorText
          // Drop the manual chorus star ([00:12.34]*word) first so it never
          // survives as a leading '*' on the word in the timing preview.
          .replace(/(\[\d+:\d+(?:\.\d{1,3})?\])\s*\*/g, "$1")
          .replace(/\[(\d+):(\d+)(?:\.(\d{1,3}))?\]/g, " ")
          .replace(/<(\d+):(\d+)(?:\.(\d{1,3}))?>/g, " ")
          .replace(/\{[mfb]\}/g, " ");
        return stripped.match(/\S+/g) || [];
      }

      // Duet voice per word, aligned 1:1 with extractWordsFromEditor(). Each word
      // inherits the gender assigned to the editor line it belongs to.
      function extractWordGendersFromEditor() {
        const editorText = document.getElementById("lyricsEditor").value || "";
        const genders = [];
        editorText.split("\n").forEach((line) => {
          const gender = getLineGender(line);
          const words = line
            .replace(/(\[\d+:\d+(?:\.\d{1,3})?\])\s*\*/g, "$1")
            .replace(/\[(\d+):(\d+)(?:\.(\d{1,3}))?\]/g, " ")
            .replace(/<(\d+):(\d+)(?:\.(\d{1,3}))?>/g, " ")
            .replace(/\{[mfb]\}/g, " ")
            .match(/\S+/g) || [];
          words.forEach(() => genders.push(gender));
        });
        return genders;
      }

      function buildTimedWordEntriesFromLyrics() {
        const entries = [];
        lyricLines.forEach((item, idx) => {
          const nextTime = lyricLines[idx + 1] ? lyricLines[idx + 1].time : item.time + 3.0;
          const words = (item.text || "").split(" ").filter(Boolean);
          if (!words.length) return;
          const perWord = Math.max(0.02, (nextTime - item.time) / words.length);
          words.forEach((word, wordIdx) => {
            const start = item.time + (wordIdx * perWord);
            entries.push({
              word,
              start,
              end: words.length === 1 && Number.isFinite(item.end) ? item.end : start + perWord,
            });
          });
        });
        return entries;
      }

      function findManualCursorIndexAtTime(nowSec) {
        const entries = manualTimingState.entries || [];
        const validEntries = entries.filter((entry) => entry && Number.isFinite(entry.start));
        if (!validEntries.length) return -1;

        for (let i = 0; i < entries.length; i += 1) {
          const entry = entries[i];
          if (!entry || !Number.isFinite(entry.start)) continue;
          const end = Number.isFinite(entry.end) ? entry.end : (entry.start + 0.3);
          if (nowSec >= entry.start && nowSec < end) return i;
          if (nowSec < entry.start) return i;
        }
        const lastValidIndex = entries.reduce((last, entry, index) => Number.isFinite(entry?.start) ? index : last, -1);
        return lastValidIndex === entries.length - 1 ? lastValidIndex : -1;
      }

      function alignManualTimingToCurrentPlayback() {
        const now = Number(audio.currentTime || 0);
        const totalWords = manualTimingState.words.length;
        if (totalWords === 0) {
          manualTimingState.cursor = 0;
          manualTimingState.activeStart = null;
          return;
        }

        let cursor = findManualCursorIndexAtTime(now);
        if (cursor < 0) {
          cursor = getPlaybackWordIndexAtTime(now, true);
        }
        if (cursor < 0) {
          const dur = Number.isFinite(audio.duration) ? audio.duration : 0;
          if (dur > 0) {
            const ratio = Math.max(0, Math.min(1, now / dur));
            cursor = Math.min(totalWords - 1, Math.floor(ratio * totalWords));
          } else {
            cursor = Math.min(totalWords - 1, cursor);
          }
        }
        manualTimingState.cursor = Math.max(0, Math.min(totalWords - 1, cursor));
        manualTimingState.activeStart = null;
      }

      async function autoSaveManualTimingDraft() {
        if (!selectedAudioFilename) return;
        try {
          persistManualTiming();
          const fd = new FormData();
          fd.append("audio_filename", selectedAudioFilename);
          fd.append("state_json", JSON.stringify(collectProjectState()));
          await fetch("/api/save-project-state", { method: "POST", body: fd });
        } catch (err) {
          console.warn("Auto-save manual timing draft failed", err);
        }
      }

      // Tell the backend to pause automatic ingest/lyrics jobs while hand-timing.
      let timingModeHeartbeatTimer = null;
      async function setBackendTimingMode(active) {
        try {
          const fd = new FormData();
          fd.append("active", active ? "1" : "0");
          await fetch("/api/timing-mode", { method: "POST", body: fd });
        } catch (err) { /* best-effort */ }
      }
      function startTimingModeHeartbeat() {
        setBackendTimingMode(true);
        if (timingModeHeartbeatTimer) clearInterval(timingModeHeartbeatTimer);
        timingModeHeartbeatTimer = setInterval(() => setBackendTimingMode(true), 45000);
      }
      function stopTimingModeHeartbeat() {
        if (timingModeHeartbeatTimer) { clearInterval(timingModeHeartbeatTimer); timingModeHeartbeatTimer = null; }
        setBackendTimingMode(false);
      }

      async function toggleManualTiming() {
        if (!selectedAudioFilename) { alert("Select source media first."); return; }
        manualTimingState.enabled = !manualTimingState.enabled;
        const manualBtn = document.getElementById("manualTimingBtn");
        manualBtn.textContent = manualTimingState.enabled ? "Timing Mode is ON" : "Timing Mode is OFF";
        manualBtn.classList.toggle("timing-on", manualTimingState.enabled);
        manualBtn.classList.toggle("timing-off", !manualTimingState.enabled);
        if (manualTimingState.enabled) {
          const existing = manualTimingByAudio[selectedAudioFilename];
          if (existing && Array.isArray(existing.baselineEntries)) {
            manualTimingState.words = existing.words.slice();
            manualTimingState.baselineEntries = existing.baselineEntries.map((item) => ({ ...item }));
            manualTimingState.entries = existing.entries.map((item) => ({ ...item }));
            manualTimingState.cursor = existing.cursor;
            manualTimingState.activeStart = existing.activeStart;
            manualTimingState.previewFromAppliedLyrics = !!existing.previewFromAppliedLyrics;
          } else {
            const seededEntries = buildTimedWordEntriesFromLyrics();
            if (seededEntries.length > 0) {
              manualTimingState.words = seededEntries.map((item) => item.word);
              manualTimingState.baselineEntries = seededEntries.map((item) => ({ ...item }));
              manualTimingState.entries = [];
              manualTimingState.cursor = 0;
              manualTimingState.activeStart = null;
              manualTimingState.previewFromAppliedLyrics = false;
              persistManualTiming();
            } else {
              resetManualTimingSession();
            }
          }
          const forcedPlaybackIndex = getPlaybackWordIndexAtTime(audio.currentTime || 0, true);
          manualTimingState.cursor = Math.max(0, Math.min(
            manualTimingState.words.length - 1,
            forcedPlaybackIndex,
          ));
          manualTimingState.activeStart = null;
          // Timing mode always starts in manual-capture preview: words stay static
          // and wait for the user's S/F instead of auto-advancing with playback.
          manualTimingState.previewFromAppliedLyrics = false;
          beginTimingRevision();
          persistManualTiming();
          startTimingModeHeartbeat();
          setStatus("Timing revision opened. Use 'S' at word start and 'F' at word end; only captured words replace the prior timing.");
        } else {
          persistManualTiming();
          stopTimingModeHeartbeat();
          await autoSaveManualTimingDraft();
          setStatus("Manual timing disabled. Draft timing auto-saved.");
        }
        updateManualTimingHud();
        updatePreview();
      }

      function persistManualTiming() {
        if (!selectedAudioFilename) return;
        manualTimingByAudio[selectedAudioFilename] = {
          words: manualTimingState.words.slice(),
          baselineEntries: manualTimingState.baselineEntries.map((item) => ({ ...item })),
          entries: manualTimingState.entries.map((item) => ({ ...item })),
          cursor: manualTimingState.cursor,
          activeStart: manualTimingState.activeStart,
          previewFromAppliedLyrics: manualTimingState.previewFromAppliedLyrics,
          revisionIndex: manualTimingState.revisionIndex,
        };
      }

      function resetManualTimingSession() {
        manualTimingState.words = extractWordsFromEditor();
        manualTimingState.baselineEntries = buildTimedWordEntriesFromLyrics();
        manualTimingState.entries = [];
        manualTimingState.cursor = 0;
        manualTimingState.activeStart = null;
        manualTimingState.previewFromAppliedLyrics = true;
        persistManualTiming();
        updateManualTimingHud();
        updatePreview();
      }

      // Reset button: always rewind playback to the start of the song. In timing
      // mode it also clears the in-progress timing session so capture starts fresh.
      function resetPreviewAndTiming() {
        if (audio) {
          audio.pause();
          audio.currentTime = 0;
        }
        if (manualTimingState.enabled) {
          resetManualTimingSession();
        }
        syncPlaybackUi();
        setStatus(manualTimingState.enabled ? "Timing session reset. Playback rewound to start." : "Playback reset to start of song.");
      }

      // Auto-write timing into the active timing revision. Untouched words retain
      // their baseline timing; only explicit S/F captures override it.
      let manualTimingAutoSaveTimer = null;
      function autoCommitManualTiming() {
        if (!selectedLrcFilename) return;
        const lrc = buildManualTimingLrc();
        if (!lrc) return;
        document.getElementById("lyricsEditor").value = lrc;
        parseLrcData(lrc);
        updateTimingRevision(lrc);
        lastCommittedLyricsText = lrc;
        updateLyricsReadyIndicator();
        updatePreview();
        if (manualTimingAutoSaveTimer) clearTimeout(manualTimingAutoSaveTimer);
        manualTimingAutoSaveTimer = setTimeout(async () => {
          try {
            const fd = new FormData();
            fd.append("filename", selectedLrcFilename);
            fd.append("content", lrc);
            const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
            if (res.ok) {
              setStatus("Timing updated & saved.");
              await autoSaveManualTimingDraft();
            }
          } catch (err) { console.warn("Auto-save timing failed", err); }
        }, 500);
      }

      // Debounced auto-save for direct edits in the lyrics editor.
      let lyricsEditorSaveTimer = null;
      function onLyricsEditorInput() {
        const text = document.getElementById("lyricsEditor").value || "";
        parseLrcData(text);
        renderChorusOverlay();
        updateLyricsReadyIndicator();
        updatePreview();
        if (!selectedLrcFilename) return;
        if (lyricsEditorSaveTimer) clearTimeout(lyricsEditorSaveTimer);
        lyricsEditorSaveTimer = setTimeout(async () => {
          try {
            const fd = new FormData();
            fd.append("filename", selectedLrcFilename);
            fd.append("content", text);
            const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
            if (res.ok) {
              lastCommittedLyricsText = text;
              const meta = {
                provider: "manual-edit",
                isNew: false,
                isCustomized: true,
                modifiedAt: Date.now(),
                timingLevel: detectLyricsTimingLevel(text),
                timestampCount: countTimestamps(text)
              };
              addLyricsRevision(selectedAudioFilename, text, "edit", meta);
              setStatus("Lyrics auto-saved.");
            }
          } catch (err) { console.warn("Auto-save lyrics failed", err); }
        }, 800);
      }

      function updateManualTimingHud() {
        const total = manualTimingState.words.length;
        const currentWord = getPlaybackWordAtTime(audio.currentTime || 0) || manualTimingState.words[manualTimingState.cursor] || "-";
        const currentIndex = getPlaybackWordIndexAtTime(audio.currentTime || 0);
        const displayTotal = total || lyricLines.reduce((sum, line) => sum + (line.text || "").split(/\s+/).filter(Boolean).length, 0);
        document.getElementById("manualCurrentWord").textContent = currentWord;
        document.getElementById("manualProgressText").textContent = displayTotal ? `${Math.min(currentIndex + 1, displayTotal)} / ${displayTotal}` : "0 / 0";
      }

      function getPlaybackWordIndexAtTime(time, ignoreManual = false) {
        if (!ignoreManual && manualTimingState.enabled && manualTimingState.words.length) {
          return Math.max(0, Math.min(manualTimingState.words.length - 1, manualTimingState.cursor));
        }
        // Before the first timed line, playback is at the very start -> first word.
        if (lyricLines.length && time < lyricLines[0].time) return 0;
        let wordOffset = 0;
        for (let lineIndex = 0; lineIndex < lyricLines.length; lineIndex += 1) {
          const line = lyricLines[lineIndex];
          const words = (line.text || "").split(/\s+/).filter(Boolean);
          const nextTime = lyricLines[lineIndex + 1]?.time || (line.time + 3);
          if (time >= line.time && time < nextTime) {
            const progress = Math.max(0, Math.min(0.999, (time - line.time) / Math.max(0.05, nextTime - line.time)));
            return wordOffset + Math.min(words.length - 1, Math.floor(progress * words.length));
          }
          wordOffset += words.length;
        }
        return Math.max(0, wordOffset - 1);
      }

      function getPlaybackWordAtTime(time) {
        if (manualTimingState.enabled && manualTimingState.words.length) {
          return manualTimingState.words[manualTimingState.cursor] || "";
        }
        if (!lyricLines.length) return "";
        let activeLine = lyricLines[0];
        for (let index = 0; index < lyricLines.length; index += 1) {
          if (time >= lyricLines[index].time) activeLine = lyricLines[index];
          else break;
        }
        const nextTime = lyricLines[lyricLines.indexOf(activeLine) + 1]?.time || (activeLine.time + 3);
        const words = (activeLine.text || "").split(/\s+/).filter(Boolean);
        if (!words.length) return "";
        const progress = Math.max(0, Math.min(0.999, (time - activeLine.time) / Math.max(0.05, nextTime - activeLine.time)));
        return words[Math.min(words.length - 1, Math.floor(progress * words.length))] || "";
      }

      function estimateActiveWordDuration() {
        const completedDurations = manualTimingState.entries
          .map((entry, idx) => ({ entry, idx }))
          .filter(({ entry, idx }) => idx < manualTimingState.cursor && entry && Number.isFinite(entry.start) && Number.isFinite(entry.end) && entry.end > entry.start)
          .map(({ entry }) => entry.end - entry.start)
          .slice(-4);

        if (completedDurations.length > 0) {
          const avg = completedDurations.reduce((sum, d) => sum + d, 0) / completedDurations.length;
          return Math.max(0.12, Math.min(2.5, avg));
        }
        return 0.45;
      }

      function manualTimingStepBackward() {
        if (manualTimingState.words.length === 0) return;
        manualTimingState.activeStart = null;
        const newCursor = Math.max(0, manualTimingState.cursor - 1);
        manualTimingState.cursor = newCursor;
        manualTimingState.entries = manualTimingState.entries.slice(0, newCursor);
        manualTimingState.previewFromAppliedLyrics = false;
        persistManualTiming();
        updateManualTimingHud();
        updatePreview();
      }

      function manualTimingStepForward() {
        if (manualTimingState.words.length === 0) return;
        manualTimingState.activeStart = null;
        manualTimingState.cursor = Math.min(manualTimingState.words.length - 1, manualTimingState.cursor + 1);
        manualTimingState.previewFromAppliedLyrics = false;
        persistManualTiming();
        updateManualTimingHud();
        updatePreview();
      }

      function captureManualWordStart() {
        if (!manualTimingState.enabled || manualTimingState.words.length === 0 || manualTimingState.cursor >= manualTimingState.words.length) return;
        const now = Number(audio.currentTime || 0);
        manualTimingState.previewFromAppliedLyrics = false;
        if (manualTimingState.activeStart !== null) {
          const word = manualTimingState.words[manualTimingState.cursor];
          manualTimingState.entries[manualTimingState.cursor] = { word, start: manualTimingState.activeStart, end: Math.max(now, manualTimingState.activeStart + 0.02) };
          manualTimingState.cursor += 1;
          if (manualTimingState.cursor < manualTimingState.words.length) {
            const nextWord = manualTimingState.words[manualTimingState.cursor];
            manualTimingState.activeStart = now;
            manualTimingState.entries[manualTimingState.cursor] = { word: nextWord, start: now, end: null };
          } else {
            manualTimingState.activeStart = null;
          }
        } else {
          const word = manualTimingState.words[manualTimingState.cursor];
          manualTimingState.activeStart = now;
          manualTimingState.entries[manualTimingState.cursor] = { word, start: now, end: null };
        }
        persistManualTiming();
        updateManualTimingHud();
        updatePreview();
        autoCommitManualTiming();
      }

      function captureManualWordEnd() {
        if (!manualTimingState.enabled || manualTimingState.words.length === 0 || manualTimingState.cursor >= manualTimingState.words.length) return;
        const now = Number(audio.currentTime || 0);
        const existing = manualTimingState.entries[manualTimingState.cursor] || {};
        let start = manualTimingState.activeStart;

        // If no explicit start was captured with S, allow F to close the selected
        // word using an existing/inferred start so key presses still register.
        if (!Number.isFinite(start)) {
          if (Number.isFinite(existing.start)) {
            start = Number(existing.start);
          } else {
            const inferred = now - estimateActiveWordDuration();
            start = Math.max(0, inferred);
          }
        }

        manualTimingState.previewFromAppliedLyrics = false;
        const word = manualTimingState.words[manualTimingState.cursor];
        manualTimingState.entries[manualTimingState.cursor] = { word, start, end: Math.max(now, start + 0.02) };
        manualTimingState.cursor += 1;

        // F only closes the current word and creates a pause. The next word
        // does not start until S is pressed.
        manualTimingState.activeStart = null;

        persistManualTiming();
        updateManualTimingHud();
        updatePreview();
        autoCommitManualTiming();
      }

      function isTypingTarget(target) {
        if (!target) return false;
        const tag = (target.tagName || "").toLowerCase();
        if (tag === "textarea" || target.isContentEditable) return true;
        if (tag !== "input") return false;
        const inputType = String(target.type || "").toLowerCase();
        return ["text", "search", "email", "url", "tel", "password", "number"].includes(inputType);
      }

      document.addEventListener("keydown", (event) => {
        if (!manualTimingState.enabled) return;
        if (event.repeat) return;
        const key = (event.key || "").toLowerCase();
        // In timing mode, capture S/F even if editor focus is in an input/textarea.
        if (isTypingTarget(event.target) && key !== "s" && key !== "f") return;
        if (key === "s") {
          event.preventDefault();
          captureManualWordStart();
        } else if (key === "f") {
          event.preventDefault();
          captureManualWordEnd();
        }
      });

      function buildManualTimingLrc() {
        if (!manualTimingState.entries.length) return "";

        const allWords = extractWordsFromEditor();
        if (!allWords.length) return "";

        const hasOverrides = manualTimingState.entries.some((entry) => entry && Number.isFinite(entry.start) && entry.word);
        if (!hasOverrides) return "";

        // Preserve the full lyric body: use the baseline timing unless this exact
        // word has an explicit S/F capture in the current timing revision.
        const fallback = manualTimingState.baselineEntries.length
          ? manualTimingState.baselineEntries
          : buildTimedWordEntriesFromLyrics();
        const resolved = [];
        let lastTime = 0;
        for (let i = 0; i < allWords.length; i += 1) {
          let start = null, end = null;
          const override = manualTimingState.entries[i];
          if (override && Number.isFinite(override.start) && String(override.word || "").trim()) {
            start = Number(override.start);
            end = Number.isFinite(override.end) ? Number(override.end) : null;
          } else if (fallback[i] && Number.isFinite(fallback[i].start)) {
            start = Number(fallback[i].start);
            end = Number.isFinite(fallback[i].end) ? Number(fallback[i].end) : null;
          } else {
            start = lastTime + 0.35;
          }
          const safeStart = Math.max(lastTime, Number(start) || 0);
          resolved.push({ word: allWords[i], start: safeStart, end });
          lastTime = safeStart;
        }
        // Emit a <end> hold marker only when a word finishes before the next word starts
        // (a real pause the user created with F); contiguous words stay start-only.
        const out = [];
        for (let i = 0; i < resolved.length; i += 1) {
          const cur = resolved[i];
          const nextStart = i + 1 < resolved.length ? resolved[i + 1].start : null;
          let tag = `[${formatLrcTime(cur.start)}]${cur.word}`;
          if (Number.isFinite(cur.end) && cur.end > cur.start && (nextStart === null || cur.end < nextStart - 0.03)) {
            tag += `<${formatLrcTime(cur.end)}>`;
          }
          out.push(tag);
        }
        return out.join("\n");
      }

      async function applyManualTimingToLyrics() {
        const lrc = buildManualTimingLrc();
        if (!lrc) { setStatus("No manual timing entries yet.", true); return; }
        if (!selectedLrcFilename) { setStatus("Select source media first.", true); return; }
        const previous = lastCommittedLyricsText || document.getElementById("lyricsEditor").value || "";
        if (lrc === previous) { setStatus("No timing changes to push."); return; }
        manualTimingApplyInFlight = true;
        document.getElementById("lyricsEditor").value = lrc;
        parseLrcData(lrc);
        manualTimingState.previewFromAppliedLyrics = true;
        persistManualTiming();
        updatePreview();
        try {
          const fd = new FormData();
          fd.append("filename", selectedLrcFilename);
          fd.append("content", lrc);
          const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
          const data = await res.json();
          if (!res.ok) throw new Error(data.message);
          lastCommittedLyricsText = lrc;
          setStatus(data.message || "Manual timing applied and saved.");
        } catch (err) { setStatus(`Failed to save: ${err}`, true); }
        finally { manualTimingApplyInFlight = false; }
      }

      function updateTuneValueLabels() {
        const vol = Number(document.getElementById("volume")?.value || 1);
        const pitch = Number(document.getElementById("pitch")?.value || 1);
        const volEl = document.getElementById("volumeValue");
        const pitchEl = document.getElementById("pitchValue");
        if (volEl) volEl.textContent = vol.toFixed(2);
        if (pitchEl) pitchEl.textContent = pitch.toFixed(2);
      }

      function updateLyricsReadyIndicator() {
        const badge = document.getElementById("lyricsReadyBadge");
        const ready = lyricLines.length > 0;
        badge.className = `state-badge ${ready ? "state-ready" : "state-waiting"}`;
        badge.textContent = ready ? "Ready" : "Pending";
        document.getElementById("activeLyricsLabel").style.color = ready ? "var(--success)" : "var(--text-muted)";
      }

      function renderJobs(jobs) {
        const container = document.getElementById("job-list");
        if (!jobs || jobs.length === 0) {
          container.textContent = selectedProjectName ? `Nothing running for ${selectedProjectName}.` : "No jobs running.";
          return;
        }
        container.innerHTML = "";
        jobs.forEach((job) => {
          const item = document.createElement("div");
          item.className = `job-item`;
          const canCancel = job.status === "queued" || job.status === "running";
          const statClass = `state-${job.status || "waiting"}`;
          const stageLabels = {
            ingest: "Preparing media",
            download: "Downloading media",
            "stem separation": "Separating vocals",
            "package accompaniment": "Preparing accompaniment",
            "lyrics fetch": "Finding lyrics",
            transcription: "Transcribing vocals",
            "word timing": "Aligning word timing",
            render: "Rendering video",
            "Creating Karaoke Video": "Rendering video",
            "render fallback": "Switching to CPU fallback",
          };
          const statusLabels = {
            queued: "Waiting",
            running: "In progress",
            completed: "Complete",
            failed: "Needs attention",
            cancelled: "Cancelled",
          };
          const liveStage = stageLabels[job.stage] || job.stage || job.label || "Working";
          const statusLabel = statusLabels[job.status] || job.status || "Waiting";
          const queueLabel = job.status === "queued" && job.queue_position
            ? `Queue position ${job.queue_position}`
            : "";
          const metaBits = [];
          if (job.project_name) metaBits.push(job.project_name);
          if (job.timing_mode) {
            const timingLabel = job.timing_mode === "minor"
              ? "Minor"
              : job.timing_mode === "custom"
                ? `Custom (range < ${job.max_offset_seconds}s)`
                : "Major";
            metaBits.push(`Timing: ${timingLabel}`);
          }
          if (job.render_source) metaBits.push(`Audio: ${job.render_source.charAt(0).toUpperCase()}${job.render_source.slice(1)}`);
          if (job.render_resolution) metaBits.push(`Resolution: ${job.render_resolution}`);
          if (job.audio_filename) metaBits.push(`Source: ${job.audio_filename}`);
          if (job.output_filename) metaBits.push(`Output: ${job.output_filename}`);
          if (job.stem_device || job.whisper_device || job.render_device) {
            const devices = [job.stem_device, job.whisper_device, job.render_device]
              .filter(Boolean)
              .map((device) => device.toLowerCase() === "cuda" ? "GPU" : device.toUpperCase())
              .filter((device, index, list) => list.indexOf(device) === index);
            if (devices.length) metaBits.push(`Hardware: ${devices.join(" + ")}`);
          }
          const metaText = metaBits.join(" | ");
          const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
          const downloadLink = (job.status === "completed" && job.output_url)
            ? `<a class="btn btn-small" href="${job.output_url}" download title="Download ${job.output_filename || "output"}">Download</a>`
            : (job.output_filename ? `<span style="font-size:11px;">${job.output_filename}</span>` : "");
          item.innerHTML = `
            <div class="job-top"><div><div class="job-label">${liveStage}</div><div class="job-sub" title="${job.label || ""}">${job.label || ""}</div></div><span class="state-badge ${statClass}">${statusLabel}</span></div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">${queueLabel || job.message || "Working..."}</div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:4px; word-break:break-word;">${metaText}</div>
            <div class="job-bar"><div class="job-bar-fill" style="width:${progress}%;"></div></div>
            <div style="display:flex; justify-content:space-between; margin-top:6px; align-items:center;">
                <span style="font-size:11px;">${progress}%</span>
                ${canCancel ? `<button class="btn btn-small" data-job-id="${job.id}">Stop</button>` : downloadLink}
            </div>`;
          if (canCancel) item.querySelector("button").addEventListener("click", () => cancelJob(job.id));
          container.appendChild(item);
        });
      }

      async function loadJobs() {
        try {
          const query = selectedProjectName ? `?project_name=${encodeURIComponent(selectedProjectName)}` : "";
          const { data } = await fetchJsonSafe(`/api/jobs${query}`, {}, "Jobs API");
          const jobs = data.jobs || [];
          let shouldRefreshVault = false;
          let completedLyricsJob = false;
          const nextStatuses = new Map();
          jobs.forEach((job) => {
            nextStatuses.set(job.id, job.status);
            const prevStatus = lastJobStatusById.get(job.id);
            if (prevStatus && prevStatus !== job.status && ["completed", "failed", "cancelled"].includes(job.status)) {
              shouldRefreshVault = true;
              if (job.status === "completed" && ["lyrics", "word_timing"].includes((job.type || "").toLowerCase())) {
                completedLyricsJob = true;
              }
            }
          });
          for (const [jobId, prevStatus] of lastJobStatusById.entries()) {
            if (!nextStatuses.has(jobId) && ["queued", "running"].includes(prevStatus)) {
              shouldRefreshVault = true;
            }
          }
          lastJobStatusById = nextStatuses;

          renderJobs(jobs);
          if (shouldRefreshVault) await loadFileManifest();
          if (selectedAudioFilename) {
            const hasActiveIngestJob = jobs.some((job) => {
              if (!job || !job.status) return false;
              const active = job.status === "queued" || job.status === "running";
              const ingestLike = ["url", "pipeline", "lyrics", "word_timing"].includes((job.type || "").toLowerCase());
              return active && ingestLike;
            });
            await refreshSelectedLyricsIfReady(completedLyricsJob || hasActiveIngestJob || shouldRefreshVault, completedLyricsJob);
          }
        } catch (err) { setStatus(`Status error: ${err}`, true); }
      }

      async function cancelJob(jobId) {
        const { res, data } = await fetchJsonSafe(
          `/api/jobs/${encodeURIComponent(jobId)}/cancel`,
          { method: "POST" },
          "Cancel job API",
        );
        setStatus(data.message || (res.ok ? "Cancellation requested." : "Cancel failed."), !res.ok);
        await loadJobs();
      }

      async function refreshSelectedLyricsIfReady(force = false, lyricsJobCompleted = false) {
        if (!force) return;
        if (!selectedLrcFilename || manualTimingApplyInFlight) return;
        // Never meld the editor back while the user is actively hand-timing.
        if (manualTimingState.enabled) return;
        // A just-completed explicit lyrics pull always wins, even over in-progress timing preview.
        if (!lyricsJobCompleted && manualTimingState.previewFromAppliedLyrics) return;
        const res = await fetch(`/api/load-lyrics?filename=${encodeURIComponent(selectedLrcFilename)}`);
        const data = await res.json();
        const existing = document.getElementById("lyricsEditor").value || "";
        if ((data.content || "") && (data.content !== existing || lyricsJobCompleted)) {
          ensureLyricsRevisionSeed(existing, "loaded");
          document.getElementById("lyricsEditor").value = data.content;
          parseLrcData(data.content);
          const timingLevel = detectLyricsTimingLevel(data.content);
          const timestampCount = countTimestamps(data.content);
          const metadata = {
            provider: lastGrabbedLyricsProvider || data.provider || "auto-grab",
            isNew: !existing || existing.length < 20,
            isCustomized: data.isCustomized || false,
            modifiedAt: data.modifiedAt || Date.now(),
            timingLevel: timingLevel,
            timestampCount: timestampCount
          };
          addLyricsRevision(selectedAudioFilename, data.content, "grabbed", metadata);
          manualTimingState.previewFromAppliedLyrics = true;
          persistManualTiming();
          updatePreview();
          const timingNames = { 3: "syllable-level", 2: "word-level", 1: "line-level", 0: "no-timing" };
          const timingDesc = timingNames[timingLevel] || "unknown";
          setStatus(`Lyrics loaded from ${metadata.provider} (${timingDesc}, ${timestampCount} timestamps).`);
          lastGrabbedLyricsProvider = null;
        }
      }

      async function uploadSourceFile() {
        const input = document.getElementById("sourceUpload");
        const file = input.files[0];
        if (!file) { alert("Choose a source file."); return; }
        const fd = new FormData();
        fd.append("file", file);
        appendTranscriptionSettings(fd);
        setStatus(`Uploading ${file.name}...`);
        const res = await fetch("/api/upload-file", { method: "POST", body: fd });
        const data = await res.json();
        setStatus(data.message || (res.ok ? "Upload started." : "Upload failed."), !res.ok);
        if (res.ok) { input.value = ""; await loadFileManifest(); await loadJobs(); }
      }

      async function clearInactiveJobs() {
        try {
            const response = await fetch('/api/jobs/clear', { method: 'POST' });
            if (!response.ok) throw new Error("Failed to clear jobs");

            // If you have a function that manually fetches and renders jobs (e.g., fetchJobs()), call it here.
            // Otherwise, the UI will update automatically on the next UI polling tick.
        } catch (error) {
            console.error("[Job Cleanup] Error clearing jobs:", error);
        }
      }

      async function submitSourceUrl() {
        const url = document.getElementById("sourceUrl").value.trim();
        if (!url) { alert("Paste a URL."); return; }
        const fd = new FormData();
        fd.append("url", url);
        fd.append("engine", document.getElementById("downloadEngine").value);
        appendTranscriptionSettings(fd);
        setStatus("Fetching URL...");
        const res = await fetch("/api/process-url", { method: "POST", body: fd });
        const data = await res.json();
        setStatus(data.message || (res.ok ? "URL accepted." : "URL failed."), !res.ok);
        if (res.ok) await loadJobs();
      }

      function setStatus(message, isError = false) {
        const el = document.getElementById("actionStatus");
        el.textContent = message;
        el.style.color = isError ? "var(--danger)" : "var(--text-muted)";
      }

      async function fetchJsonSafe(url, options = {}, label = "Request") {
        const res = await fetch(url, options);
        const ctype = (res.headers.get("content-type") || "").toLowerCase();
        if (ctype.includes("application/json")) {
          const data = await res.json();
          return { res, data };
        }
        const raw = await res.text();
        const brief = String(raw || "").replace(/\s+/g, " ").slice(0, 140);
        throw new Error(`${label} returned non-JSON (HTTP ${res.status}). ${brief || "No response body."}`);
      }

      async function loadFonts() {
        const select = document.getElementById("fontSelect");
        if (!select) return;
        try {
          const res = await fetch("/api/get-fonts");
          if (!res.ok) throw new Error("Backend font API failed");
          const data = await res.json();
          select.innerHTML = '<option value="Arial">Arial</option>';

          if (data.fonts && data.fonts.length > 0) {
            data.fonts.forEach((f) => {
              const opt = document.createElement("option");
              opt.value = f.name; opt.textContent = f.name;
              select.appendChild(opt);
              new FontFace(f.name, `url(/fonts/${f.filename})`)
                .load().then((face) => document.fonts.add(face))
                .catch((e) => console.warn(`Could not load font preview for ${f.name}`));
            });
          }
        } catch (e) {
          console.error("Font load error:", e);
          if (select.options.length === 0) select.innerHTML = '<option value="Arial">Arial</option>';
        }
      }

      // Derive the project folder name from a profile-qualified path
      // ("profile/project/file" -> "project"). Falls back gracefully.
      function projectNameFromPath(pathLike) {
        const parts = String(pathLike || "").split("/").filter(Boolean);
        if (parts.length >= 2) return parts[parts.length - 2];
        return (parts[0] || "").replace(/\.[^/.]+$/, "");
      }

      // Directory portion of a profile-qualified audio path ("profile/project").
      function projectDirFromPath(pathLike) {
        const parts = String(pathLike || "").split("/").filter(Boolean);
        if (parts.length >= 2) return parts.slice(0, -1).join("/");
        return "";
      }

      function renderProfileOptions() {
        const select = document.getElementById("profileSelect");
        if (!select) return;
        select.innerHTML = "";
        knownProfiles.forEach((name) => {
          const opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          if (name === activeProfile) opt.selected = true;
          select.appendChild(opt);
        });
      }

      async function loadProfiles() {
        try {
          const { res, data } = await fetchJsonSafe("/api/profiles", {}, "Profiles API");
          if (!res.ok) throw new Error(data.message || "Profiles API failed");
          knownProfiles = Array.isArray(data.profiles) && data.profiles.length ? data.profiles : ["Default"];
          if (!knownProfiles.includes(activeProfile)) {
            activeProfile = data.active || knownProfiles[0] || "Default";
            localStorage.setItem("onepage-active-profile", activeProfile);
          }
          renderProfileOptions();
        } catch (err) {
          knownProfiles = ["Default"];
          activeProfile = "Default";
          renderProfileOptions();
        }
      }

      async function onProfileSelectChange() {
        const select = document.getElementById("profileSelect");
        const chosen = select ? select.value : activeProfile;
        if (!chosen || chosen === activeProfile) return;
        activeProfile = chosen;
        localStorage.setItem("onepage-active-profile", activeProfile);
        try {
          const fd = new FormData();
          fd.append("name", activeProfile);
          await fetch("/api/profiles/active", { method: "POST", body: fd });
        } catch (err) { /* non-fatal */ }
        setStatus(`Switched to profile: ${activeProfile}`);
        await loadFileManifest();
      }

      async function createProfilePrompt() {
        const name = prompt("New profile name:", "");
        if (name === null) return;
        const trimmed = name.trim();
        if (!trimmed) { setStatus("Profile creation cancelled: empty name.", true); return; }
        const fd = new FormData();
        fd.append("name", trimmed);
        const res = await fetch("/api/profiles", { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) { setStatus(data.message || "Could not create profile.", true); return; }
        knownProfiles = data.profiles || knownProfiles;
        activeProfile = data.created || trimmed;
        localStorage.setItem("onepage-active-profile", activeProfile);
        try {
          const af = new FormData(); af.append("name", activeProfile);
          await fetch("/api/profiles/active", { method: "POST", body: af });
        } catch (err) { /* non-fatal */ }
        renderProfileOptions();
        setStatus(data.message || `Profile '${activeProfile}' created.`);
        await loadFileManifest();
      }

      async function deleteActiveProfile() {
        if (activeProfile === "Default") { setStatus("The Default profile cannot be deleted.", true); return; }
        if (!confirm(`Delete profile "${activeProfile}"? Its projects will be moved to Default.`)) return;
        const fd = new FormData();
        fd.append("name", activeProfile);
        const res = await fetch("/api/profiles/delete", { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) { setStatus(data.message || "Could not delete profile.", true); return; }
        knownProfiles = data.profiles || ["Default"];
        activeProfile = data.active || "Default";
        localStorage.setItem("onepage-active-profile", activeProfile);
        renderProfileOptions();
        setStatus(data.message || "Profile deleted.");
        await loadFileManifest();
      }

      async function transferProject(audioFilename, mode, targetProfile) {
        if (!audioFilename || !targetProfile) return;
        const verb = mode === "move" ? "Moving" : "Copying";
        setStatus(`${verb} project to ${targetProfile}...`);
        const fd = new FormData();
        fd.append("audio_filename", audioFilename);
        fd.append("target_profile", targetProfile);
        fd.append("mode", mode);
        const res = await fetch("/api/transfer-project", { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) { setStatus(data.message || "Transfer failed.", true); await loadFileManifest(); return; }
        if (mode === "move" && selectedAudioFilename === audioFilename) {
          selectedAudioFilename = selectedLrcFilename = selectedProjectName = "";
        }
        setStatus(data.message || "Transfer complete.");
        await loadFileManifest();
        await loadJobs();
      }

      async function loadFileManifest(retryCount = 0) {
        const container = document.getElementById("file-vault");
        try {
          const { res, data } = await fetchJsonSafe(`/api/list-files?sources_only=true&profile=${encodeURIComponent(activeProfile)}`, {}, "Media vault API");
          if (!res.ok) throw new Error(data.message || `Media vault API failed (HTTP ${res.status})`);
          container.innerHTML = "";
          if (!data.files || data.files.length === 0) { container.textContent = "No media found."; return; }
          data.files.forEach((f) => {
            const row = document.createElement("div"); row.className = "file-row";
            const projectLabel = f.project_name || f.name;
            const lrcStateClass = f.has_timed_lyrics ? "state-ready" : "state-waiting";
            const lrcStateText = f.has_timed_lyrics ? "LRC Ready" : "LRC Missing";
            const saveStateClass = f.has_proj ? "state-completed" : "state-waiting";
            const saveStateText = f.has_proj ? "Saved" : "Unsaved";
            row.innerHTML = `
            <div class="file-row-main">
              <span class="file-name" title="${projectLabel}">${projectLabel}</span>
            </div>
            <div class="file-row-actions">
              <button class="btn btn-small vault-btn state-badge ${lrcStateClass}" data-action="lrc-state" title="Lyric timing status for this project" disabled>${lrcStateText}</button>
              <button class="btn btn-small vault-btn state-badge ${saveStateClass}" data-action="save-state" title="Saved project snapshot status" disabled>${saveStateText}</button>
              <button class="btn btn-small vault-btn" data-action="use" title="Load this project into editor and preview">Load</button>
              <button class="btn btn-small vault-btn" data-action="export" title="Download this project and all of its files">Export</button>
              <button class="btn btn-small vault-btn" data-action="rename" title="Rename this project folder">Rename</button>
              <button class="btn btn-small vault-btn" style="border-color:var(--danger); color:var(--danger);" data-action="remove" title="Delete this project and all files">Remove</button>
            </div>`;
            if (selectedAudioFilename === f.audio_filename) row.classList.add("file-row-active");
            const otherProfiles = knownProfiles.filter((p) => p !== activeProfile);
            if (otherProfiles.length) {
              const actions = row.querySelector(".file-row-actions");
              const transfer = document.createElement("select");
              transfer.className = "btn btn-small vault-btn";
              transfer.title = "Copy or move this project to another profile";
              transfer.style.maxWidth = "120px";
              const head = document.createElement("option");
              head.value = ""; head.textContent = "Send to…"; head.selected = true;
              transfer.appendChild(head);
              otherProfiles.forEach((p) => {
                const c = document.createElement("option");
                c.value = `copy:${p}`; c.textContent = `Copy → ${p}`;
                transfer.appendChild(c);
                const m = document.createElement("option");
                m.value = `move:${p}`; m.textContent = `Move → ${p}`;
                transfer.appendChild(m);
              });
              transfer.addEventListener("change", () => {
                const val = transfer.value;
                transfer.value = "";
                if (!val) return;
                const [mode, target] = val.split(":");
                transferProject(f.audio_filename, mode, target);
              });
              actions.appendChild(transfer);
            }
            row.querySelector('[data-action="use"]').addEventListener("click", () => stageTrackTarget(f.url, f.audio_filename, f.project_name, f.lrc_filename || ""));
            row.querySelector('[data-action="export"]').addEventListener("click", () => {
              setStatus(`Exporting ${projectLabel}...`);
              window.location.assign(`/api/export-project?audio_filename=${encodeURIComponent(f.audio_filename)}`);
            });
            row.querySelector('[data-action="rename"]').addEventListener("click", () => beginRenameMedia(f.audio_filename, projectLabel));
            row.querySelector('[data-action="remove"]').addEventListener("click", () => deleteMediaAsset(f.audio_filename, projectLabel));
            container.appendChild(row);
          });
          return;
        } catch (err) {
          if (retryCount < 3) {
            window.setTimeout(() => loadFileManifest(retryCount + 1), 1000);
            return;
          }
          container.textContent = "Media vault unavailable. Retrying...";
          setStatus(`Media vault load failed: ${err}`, true);
          return;
        }
      }

      async function beginRenameMedia(filename, currentProjectName = "") {
        const current = currentProjectName || projectNameFromPath(filename) || filename.replace(/\.[^/.]+$/, "");
        const proposed = prompt("Rename project:", current);
        if (proposed === null) return;
        const trimmed = proposed.trim();
        if (!trimmed) { setStatus("Project rename cancelled: empty name.", true); return; }
        await renameMediaAsset(filename, trimmed);
      }

      async function renameMediaAsset(oldFilename, newProjectName) {
        const fd = new FormData();
        fd.append("audio_filename", oldFilename);
        fd.append("new_name", newProjectName);
        const res = await fetch("/api/rename-media", { method: "POST", body: fd });
        const data = await res.json();

        if (!res.ok) {
          setStatus(data.message || "Rename failed.", true);
          return;
        }

        if (data.rename_pending) {
          setStatus(data.message || "Project rename queued.");
          await loadFileManifest();
          await loadJobs();
          return;
        }

        const renamedAudio = data.audio_filename || oldFilename;
        const renamedProject = data.project_name || newProjectName;
        if (selectedAudioFilename === oldFilename) {
          if (manualTimingByAudio[oldFilename]) {
            manualTimingByAudio[renamedAudio] = manualTimingByAudio[oldFilename];
            delete manualTimingByAudio[oldFilename];
          }
          if (lyricsRevisionHistoryByAudio[oldFilename]) {
            lyricsRevisionHistoryByAudio[renamedAudio] = lyricsRevisionHistoryByAudio[oldFilename];
            delete lyricsRevisionHistoryByAudio[oldFilename];
          }
          if (Object.prototype.hasOwnProperty.call(lyricsRevisionCursorByAudio, oldFilename)) {
            lyricsRevisionCursorByAudio[renamedAudio] = lyricsRevisionCursorByAudio[oldFilename];
            delete lyricsRevisionCursorByAudio[oldFilename];
          }
          await stageTrackTarget(
            data.audio_url || `/files/${encodeURIComponent(renamedAudio).replace(/%2F/g, "/")}`,
            renamedAudio,
            renamedProject,
            data.lrc_filename || selectedLrcFilename,
          );
          if (data.lrc_filename) {
            selectedLrcFilename = data.lrc_filename;
            document.getElementById("activeLyricsLabel").textContent = `Editing: ${selectedLrcFilename}`;
          }
        }

        setStatus(data.message || "Project renamed.");
        await loadFileManifest();
        await loadJobs();
      }

      async function deleteMediaAsset(filename, projectName = "") {
        const label = projectName || projectNameFromPath(filename) || filename;
        if (!confirm(`Are you sure you want to remove this project: ${label}?`)) return;
        const fd = new FormData(); fd.append("audio_filename", filename);
        const res = await fetch("/api/delete-media", { method: "POST", body: fd });
        const data = await res.json();
        if (selectedAudioFilename === filename) {
          selectedAudioFilename = selectedLrcFilename = selectedProjectName = "";
          delete manualTimingByAudio[filename];
          delete lyricsRevisionHistoryByAudio[filename];
          document.getElementById("lyricsEditor").value = "";
          lyricLines = []; audio.pause(); audio.removeAttribute("src"); audio.load();
          loadVocalWaveform("");
          document.getElementById("activeLyricsLabel").textContent = "No media mounted";
          updateLyricsReadyIndicator(); updatePreview();
        }
        setStatus(data.message || (res.ok ? "Deleted." : "Delete failed."), !res.ok);
        await loadFileManifest(); await loadJobs();
      }

      async function stageTrackTarget(audioUrl, filename, projectName = "", lrcFilename = "") {
        if (manualTimingState.enabled) persistManualTiming();
        selectedAudioFilename = filename;
        selectedProjectName = projectName || projectNameFromPath(filename) || "";
        loadVocalWaveform(filename);
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
        audio.src = new URL(audioUrl, window.location.href).href;
        audio.load();
        previewShouldStartAtZero = true;
        applyPreviewAudioTuning();
        selectedLrcFilename = lrcFilename || `${filename.slice(0, filename.lastIndexOf(".")) || filename}.lrc`;
        document.getElementById("activeLyricsLabel").textContent = `Editing: ${selectedLrcFilename}`;
        const res = await fetch(`/api/load-lyrics?filename=${encodeURIComponent(selectedLrcFilename)}`);
        const data = await res.json();
        document.getElementById("lyricsEditor").value = data.content || "";
        lastCommittedLyricsText = data.content || "";
        parseLrcData(data.content || "");
        lyricsRevisionHistoryByAudio[selectedAudioFilename] = [];
        lyricsRevisionCursorByAudio[selectedAudioFilename] = 0;
        const seedMetadata = {
          provider: data.provider || "loaded",
          isNew: false,
          isCustomized: false,
          modifiedAt: data.modifiedAt || Date.now(),
          timingLevel: detectLyricsTimingLevel(data.content || ""),
          timestampCount: countTimestamps(data.content || "")
        };
        addLyricsRevision(selectedAudioFilename, data.content || "", "loaded", seedMetadata);

        const existing = manualTimingByAudio[selectedAudioFilename];
        if (existing && Array.isArray(existing.baselineEntries)) {
          manualTimingState.words = existing.words.slice();
          manualTimingState.baselineEntries = existing.baselineEntries.map((item) => ({ ...item }));
          manualTimingState.entries = existing.entries.map((item) => ({ ...item }));
          manualTimingState.cursor = existing.cursor;
          manualTimingState.activeStart = existing.activeStart;
          manualTimingState.previewFromAppliedLyrics = !!existing.previewFromAppliedLyrics;
          manualTimingState.revisionIndex = existing.revisionIndex ?? null;
        } else {
          manualTimingState.words = extractWordsFromEditor();
          manualTimingState.baselineEntries = buildTimedWordEntriesFromLyrics();
          manualTimingState.entries = []; manualTimingState.cursor = 0; manualTimingState.activeStart = null; manualTimingState.previewFromAppliedLyrics = false; manualTimingState.revisionIndex = null;
        }
        await loadProjectSnapshot();
        updateManualTimingHud(); updateLyricsReadyIndicator(); updatePreview();
        await loadJobs();
        await loadFileManifest();
      }

      function collectProjectState() {
        return {
          preview: {
            fontSelect: document.getElementById("fontSelect").value,
            fontSize: document.getElementById("fontSize").value,
            lineSpacing: document.getElementById("lineSpacing").value,
            wordPadding: document.getElementById("wordPadding").value,
            primaryColor: genderColorSchemes.m.p,
            secondaryColor: genderColorSchemes.m.s,
            outlineColor: genderColorSchemes.m.o,
            malePrimaryColor: genderColorSchemes.m.p,
            maleSecondaryColor: genderColorSchemes.m.s,
            maleOutlineColor: genderColorSchemes.m.o,
            femalePrimaryColor: genderColorSchemes.f.p,
            femaleSecondaryColor: genderColorSchemes.f.s,
            femaleOutlineColor: genderColorSchemes.f.o,
            bothPrimaryColor: genderColorSchemes.b.p,
            bothSecondaryColor: genderColorSchemes.b.s,
            bothOutlineColor: genderColorSchemes.b.o,
            outlineWidth: getOutlineWidthSetting(),
            bgColor: document.getElementById("bgColor").value,
            bgType: document.getElementById("bgType").value,
            transitionStyle: document.getElementById("transitionStyle").value,
            textEffect: document.getElementById("textEffect").value,
            fxScope: getFxScope(),
            fxSpeed: getFxSpeed(),
            revealMode: document.getElementById("revealMode").value,
            previewLineCount: document.getElementById("previewLineCount").value,
            playbackRate: document.getElementById("playbackRate").value,
            volume: document.getElementById("volume").value,
            pitch: document.getElementById("pitch").value,
            // Bouncing-ball settings. These inputs only exist while the ball FX
            // panel is mounted, so read defensively and restore the same way.
            ballRadius: document.getElementById("ballRadius")?.value,
            arcHeight: document.getElementById("arcHeight")?.value,
            bouncePerSec: document.getElementById("bouncePerSec")?.value,
            ballRotation: document.getElementById("ballRotation")?.value,
            ballColor: document.getElementById("ballColor")?.value,
            ballOutlineColor: document.getElementById("ballOutlineColor")?.value,
            ballIcon: document.getElementById("ballIcon")?.value,
            ballAlign: document.getElementById("ballAlign")?.value,
          },
          lyricsText: document.getElementById("lyricsEditor").value || "",
          manualTiming: manualTimingByAudio[selectedAudioFilename] || {
            words: manualTimingState.words,
            baselineEntries: manualTimingState.baselineEntries,
            entries: manualTimingState.entries,
            cursor: manualTimingState.cursor,
            activeStart: manualTimingState.activeStart,
            previewFromAppliedLyrics: manualTimingState.previewFromAppliedLyrics,
            revisionIndex: manualTimingState.revisionIndex,
          },
          lyricRevisions: lyricsRevisionHistoryByAudio[selectedAudioFilename] || [],
          uiThemeId: currentThemeId,
        };
      }

      // ================================================================
      // SECTION: Project State Management
      // Purpose: Load/save project state including lyrics, timing, theme,
      //          effects, and preview settings to persistent storage
      // ================================================================

      function applyProjectState(state) {
        if (!state) return;
        const preview = state.preview || {};
        const setIf = (id, value) => {
          if (value === undefined || value === null) return;
          const el = document.getElementById(id);
          if (el) el.value = value;
        };
        setIf("fontSelect", preview.fontSelect);
        setIf("fontSize", preview.fontSize);
        setIf("fontSizeNum", preview.fontSize || preview.fontSizeNum);
        setIf("lineSpacing", preview.lineSpacing);
        setIf("lineGapNum", preview.lineSpacing || preview.lineGapNum);
        setIf("wordPadding", preview.wordPadding);
        setIf("wordPadNum", preview.wordPadding || preview.wordPadNum);
        // Duet color schemes: male falls back to the legacy primary/secondary/outline
        // fields so older projects still load, then mirror the active (male) scheme
        // into the shared color bar.
        genderColorSchemes.m = {
          p: preview.malePrimaryColor || preview.primaryColor || genderColorSchemes.m.p,
          s: preview.maleSecondaryColor || preview.secondaryColor || genderColorSchemes.m.s,
          o: preview.maleOutlineColor || preview.outlineColor || genderColorSchemes.m.o,
        };
        genderColorSchemes.f = {
          p: preview.femalePrimaryColor || genderColorSchemes.f.p,
          s: preview.femaleSecondaryColor || genderColorSchemes.f.s,
          o: preview.femaleOutlineColor || genderColorSchemes.f.o,
        };
        genderColorSchemes.b = {
          p: preview.bothPrimaryColor || genderColorSchemes.b.p,
          s: preview.bothSecondaryColor || genderColorSchemes.b.s,
          o: preview.bothOutlineColor || genderColorSchemes.b.o,
        };
        activeColorGender = "m";
        loadSchemeIntoInputs("m");
        updateColorGenderToggle();
        setIf("outlineWidth", preview.outlineWidth);
        setIf("outlineWidthNum", preview.outlineWidth);
        setIf("bgColor", preview.bgColor);
        setIf("bgType", preview.bgType);
        setIf("transitionStyle", preview.transitionStyle);
        setIf("textEffect", preview.textEffect);
        // FX panel is rebuilt from transitionStyle, so seed the cached scope/speed
        // first, then rebuild the panel so the restored values are applied.
        setFxScope(preview.fxScope);
        setFxSpeed(preview.fxSpeed);
        if (typeof updateFxPanel === "function") updateFxPanel();
        // Honor the project's saved reveal mode; "continuous" is the markup default
        // for new projects, but an explicit saved choice must not be discarded.
        setIf("revealMode", preview.revealMode);
        setIf("previewLineCount", preview.previewLineCount);
        setIf("playbackRate", preview.playbackRate);
        setIf("volume", preview.volume);
        setIf("pitch", preview.pitch);

        if (state.lyricsText) {
          document.getElementById("lyricsEditor").value = state.lyricsText;
          parseLrcData(state.lyricsText);
        }

        if (state.manualTiming && selectedAudioFilename) {
          manualTimingByAudio[selectedAudioFilename] = state.manualTiming;
          if (manualTimingState.enabled) {
            manualTimingState.words = (state.manualTiming.words || []).slice();
            manualTimingState.baselineEntries = (state.manualTiming.baselineEntries || buildTimedWordEntriesFromLyrics()).map((item) => ({ ...item }));
            manualTimingState.entries = (state.manualTiming.entries || []).map((item) => ({ ...item }));
            manualTimingState.cursor = Number(state.manualTiming.cursor || 0);
            manualTimingState.activeStart = state.manualTiming.activeStart ?? null;
            manualTimingState.previewFromAppliedLyrics = !!state.manualTiming.previewFromAppliedLyrics;
            manualTimingState.revisionIndex = state.manualTiming.revisionIndex ?? null;
          }
        }

        if (selectedAudioFilename && Array.isArray(state.lyricRevisions)) {
          lyricsRevisionHistoryByAudio[selectedAudioFilename] = state.lyricRevisions
            .filter((item) => item && typeof item.content === "string")
            .map((item) => ({
              content: item.content,
              reason: item.reason || "commit",
              at: Number(item.at || Date.now()),
            }));
          lyricsRevisionCursorByAudio[selectedAudioFilename] = Math.max(0, lyricsRevisionHistoryByAudio[selectedAudioFilename].length - 1);
          ensureLyricsRevisionSeed(document.getElementById("lyricsEditor").value || "", "loaded");
        }

        // Preserve the user's currently selected global theme when loading media.
        // Only restore snapshot theme if no theme has been chosen in this session yet.
        if (state.uiThemeId && !currentThemeId) {
          applyThemeById(state.uiThemeId, false);
        }

        if ((preview.bgType || "") === "image" && selectedAudioFilename) {
          const dir = projectDirFromPath(selectedAudioFilename);
          const cached = new Image();
          cached.onload = () => { bgImageObj = cached; updatePreview(); };
          cached.src = `/files/${encodeURIComponent(dir).replace(/%2F/g, "/")}/custom_bg.png`;
        }

        applyPreviewAudioTuning();
        updateManualTimingHud();
        updateLyricsRevisionMeta();
        updateFxPanel(); // Refresh FX customization panel with loaded settings
        // Restore bouncing-ball settings AFTER the FX panel is (re)mounted, since
        // updateFxPanel rebuilds the panel's innerHTML back to default values. The
        // paired hidden+number controls must both be set so the display and the
        // preview stay in sync; without this a saved custom ball icon (and the
        // other ball tuning) reverts to the default vector ball on load.
        const setBallPair = (hiddenId, numId, value) => {
          if (value === undefined || value === null || value === "") return;
          const hidden = document.getElementById(hiddenId);
          const num = document.getElementById(numId);
          if (hidden) hidden.value = value;
          if (num) num.value = value;
        };
        setBallPair("ballRadius", "ballRadiusNum", preview.ballRadius);
        setBallPair("arcHeight", "arcHeightNum", preview.arcHeight);
        setBallPair("bouncePerSec", "bouncePerSecNum", preview.bouncePerSec);
        setBallPair("ballRotation", "ballRotationNum", preview.ballRotation);
        setBallPair("ballAlign", "ballAlignNum", preview.ballAlign);
        setIf("ballColor", preview.ballColor);
        setIf("ballOutlineColor", preview.ballOutlineColor);
        setIf("ballIcon", preview.ballIcon);
        updatePreview();
      }

      async function loadProjectSnapshot() {
        if (!selectedAudioFilename) return;
        try {
          const res = await fetch(`/api/load-project-state?audio_filename=${encodeURIComponent(selectedAudioFilename)}`);
          const data = await res.json();
          if (res.ok && data.state) {
            applyProjectState(data.state);
            if (data.status === "success") setStatus(`Loaded saved project state for ${data.project_name || selectedProjectName}.`);
          }
        } catch (err) {
          setStatus(`Project state load failed: ${err}`, true);
        }
      }

      async function saveProjectSnapshot() {
        if (!selectedAudioFilename) { setStatus("Select a project first.", true); return; }
        try {
          await saveLyricsData();
          const fd = new FormData();
          fd.append("audio_filename", selectedAudioFilename);
          fd.append("state_json", JSON.stringify(collectProjectState()));
          const res = await fetch("/api/save-project-state", { method: "POST", body: fd });
          const data = await res.json();
          setStatus(data.message || (res.ok ? "Project snapshot saved." : "Project snapshot failed."), !res.ok);
          await loadFileManifest();
        } catch (err) {
          setStatus(`Snapshot save failed: ${err}`, true);
        }
      }

      async function autoCorrectWordTiming(mode = "major") {
        if (!selectedAudioFilename) { setStatus("Select a project first.", true); return; }
        const allowedModes = ["minor", "custom", "safe-word", "major"];
        const selectedMode = allowedModes.includes(mode) ? mode : "major";
        const fd = new FormData();
        fd.append("audio_filename", selectedAudioFilename);
        fd.append("whisper_model", document.getElementById("whisperModel").value || "medium");
        fd.append("whisper_device", document.getElementById("whisperDevice").value || "auto");
        fd.append("timing_mode", selectedMode);

        let queueLabel = selectedMode;
        if (selectedMode === "custom") {
          const maxOffset = document.getElementById("customTimingMaxOffset").value || "5";
          fd.append("max_offset_seconds", maxOffset);
          queueLabel = `custom (range < ${maxOffset}s)`;
        }

        setStatus(`Queuing ${queueLabel} AI timing correction...`);
        const res = await fetch("/api/auto-correct-word-timing", { method: "POST", body: fd });
        const data = await res.json();
        setStatus(data.message || (res.ok ? "AI word timing job queued." : "AI word timing failed to queue."), !res.ok);
        if (res.ok) {
          await loadJobs();
        }
      }

      async function saveLyricsData() {
        if (!selectedLrcFilename) return;
        const text = document.getElementById("lyricsEditor").value;
        const keepTime = Number(audio.currentTime || 0);
        const wasPaused = audio.paused;
        const history = getLyricsRevisionHistory(selectedAudioFilename);
        const lastEntry = history.length ? history[history.length - 1] : null;
        const isCustomized = lastEntry && lastEntry.content !== text;

        parseLrcData(text);
        updateLyricsReadyIndicator();
        updatePreview();

        ensureLyricsRevisionSeed(text, "loaded");
        const fd = new FormData(); fd.append("filename", selectedLrcFilename); fd.append("content", text);
        const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
        const data = await res.json();
        if (res.ok) lastCommittedLyricsText = text;

        if (Number.isFinite(keepTime)) {
          const dur = Number.isFinite(audio.duration) ? Number(audio.duration) : 0;
          const capped = dur > 0 ? Math.min(Math.max(0, keepTime), Math.max(0, dur - 0.001)) : Math.max(0, keepTime);
          audio.currentTime = capped;
        }
        if (!wasPaused) {
          audio.play().catch(() => {});
        }
        syncSeekBar();
        displayTimeTracker();

        manualTimingState.previewFromAppliedLyrics = true;
        const saveMetadata = {
          provider: lastEntry?.provider || null,
          isNew: false,
          isCustomized: isCustomized,
          modifiedAt: Date.now(),
          timingLevel: detectLyricsTimingLevel(text),
          timestampCount: countTimestamps(text)
        };
        addLyricsRevision(selectedAudioFilename, text, "commit", saveMetadata);
        persistManualTiming();
        updateManualTimingHud();
        updatePreview();
        setStatus(data.message || (res.ok ? "Saved." : "Save failed."), !res.ok);
        await loadFileManifest();
      }

      // Show the Suno song-link field only when the Suno provider is selected.
      function onLyricsProviderChange() {
        const provider = document.getElementById("lyricsProvider")?.value || "auto";
        const field = document.getElementById("sunoUrl");
        if (field) field.style.display = provider === "suno" ? "block" : "none";
      }

      async function autoGrabLyrics() {
        if (!selectedAudioFilename) { alert("Select media first."); return; }
        const provider = (document.getElementById("lyricsProvider")?.value || "auto").trim();
        const fd = new FormData();
        fd.append("audio_filename", selectedAudioFilename);
        fd.append("provider", provider);
        if (provider === "suno") {
          const sunoUrl = (document.getElementById("sunoUrl")?.value || "").trim();
          if (!sunoUrl) { setStatus("Paste a Suno song link or ID first.", true); return; }
          fd.append("suno_url", sunoUrl);
        }
        setStatus(`Queuing lyrics Pull via ${provider}...`);
        const res = await fetch("/api/auto-grab-lyrics", { method: "POST", body: fd });
        const data = await res.json();
        if (res.ok) {
          selectedLrcFilename = data.filename || selectedLrcFilename;
          // Store the requested provider; actual provider will come from metadata file when job completes
          lastGrabbedLyricsProvider = provider;
          await loadJobs();
          await loadFileManifest();
        }
        setStatus(data.message || (res.ok ? "Lyrics job queued." : "Fetch failed."), !res.ok);
      }

      async function uploadLrcFile(input) {
        const file = input?.files?.[0];
        if (!file) return;
        if (!selectedAudioFilename || !selectedLrcFilename) {
          setStatus("Mount media first, then upload an LRC.", true);
          input.value = "";
          return;
        }
        try {
          const text = await file.text();
          document.getElementById("lyricsEditor").value = text;
          lastCommittedLyricsText = text;
          parseLrcData(text);
          renderChorusOverlay();
          updateLyricsReadyIndicator();
          const fd = new FormData();
          fd.append("filename", selectedLrcFilename);
          fd.append("content", text);
          const res = await fetch("/api/save-lyrics", { method: "POST", body: fd });
          if (res.ok) {
            const meta = {
              provider: "uploaded",
              isNew: false,
              isCustomized: true,
              modifiedAt: Date.now(),
              timingLevel: detectLyricsTimingLevel(text),
              timestampCount: countTimestamps(text)
            };
            addLyricsRevision(selectedAudioFilename, text, "upload", meta);
            updatePreview();
            setStatus(`Loaded lyrics from ${file.name}.`);
          } else {
            setStatus("Failed to save uploaded LRC.", true);
          }
        } catch (err) {
          setStatus(`LRC upload failed: ${err}`, true);
        } finally {
          input.value = "";
        }
      }

      async function uploadBallIcon(input) {
        const fileEl = input || document.getElementById("ballIconFile");
        const file = fileEl?.files?.[0];
        if (!file) return;
        const fd = new FormData();
        fd.append("file", file);
        setStatus(`Uploading icon ${file.name}...`);
        try {
          const res = await fetch("/api/upload-icon", { method: "POST", body: fd });
          const data = await res.json();
          if (res.ok && data.path) {
            const iconField = document.getElementById("ballIcon");
            if (iconField) iconField.value = data.path;
            updatePreview();
            setStatus(`Icon ready: ${data.filename}. It replaces the ball in the preview.`);
          } else {
            setStatus(data.message || "Icon upload failed.", true);
          }
        } catch (err) {
          setStatus(`Icon upload failed: ${err}`, true);
        } finally {
          fileEl.value = "";
        }
      }

      function clearBallIcon() {
        const iconField = document.getElementById("ballIcon");
        if (iconField) iconField.value = "";
        const fileEl = document.getElementById("ballIconFile");
        if (fileEl) fileEl.value = "";
        updatePreview();
      }

      async function autoTranscribe() {
        if (!selectedAudioFilename) { alert("Select media first."); return; }
        const fd = new FormData(); fd.append("audio_filename", selectedAudioFilename);
        appendTranscriptionSettings(fd); setStatus(`Queuing transcription...`);
        const res = await fetch("/api/auto-transcribe", { method: "POST", body: fd });
        const data = await res.json();
        setStatus(data.message || (res.ok ? "Started." : "Failed."), !res.ok);
        if (res.ok) await loadJobs();
      }

      async function startRender(renderSource = "final") {
        if (!selectedAudioFilename) return;
        const usePreview = renderSource === "preview";
        const cfg = (usePreview || previewConfigLocked) && previewConfigSnapshot ? previewConfigSnapshot : capturePreviewConfig();
        const fd = new FormData();
        Object.keys(cfg).forEach((k) => fd.append(k, cfg[k]));
        fd.append("use_preview_audio", usePreview ? "1" : "0");
        fd.append("render_source", renderSource);
        setStatus(`Queuing ${renderSource} render...`);
        const res = await fetch("/api/burn-video", { method: "POST", body: fd });
        const data = await res.json();
        setStatus(data.message || (res.ok ? "Render Queued" : "Render failed"), !res.ok);
        if (res.ok) await loadJobs();
      }

      async function startCdgExport(renderSource = "final") {
        if (!selectedAudioFilename) { alert("Select media first."); return; }
        const cfg = previewConfigLocked && previewConfigSnapshot ? previewConfigSnapshot : capturePreviewConfig();
        const fd = new FormData();
        fd.append("audio_filename", selectedAudioFilename);
        fd.append("font_name", cfg.font_name || "Arial");
        fd.append("primary_color", cfg.primary_color || "#ffe14d");
        fd.append("secondary_color", cfg.secondary_color || "#9fb4ff");
        fd.append("bg_color", cfg.bg_color || "#000820");
        fd.append("render_source", renderSource);
        fd.append("lines_per_page", cfg.preview_line_count || "4");
        setStatus("Queuing CDG+MP3 export...");
        const res = await fetch("/api/burn-cdg", { method: "POST", body: fd });
        const data = await res.json();
        setStatus(data.message || (res.ok ? "CDG+MP3 Queued" : "CDG export failed"), !res.ok);
        if (res.ok) await loadJobs();
      }

      function applyPreviewAudioTuning() {
        const speed = Number(document.getElementById("playbackRate").value || 1);
        const vol = Number(document.getElementById("volume").value || 1);
        const pitch = Number(document.getElementById("pitch").value || 1);
        audio.volume = Math.max(0, Math.min(1, vol));
        // Browser preview approximation: pitch scales playbackRate with speed.
        // Final render still uses FFmpeg pitch filter independently.
        audio.playbackRate = Math.max(0.25, Math.min(4.0, speed * pitch));
        if ("preservesPitch" in audio) audio.preservesPitch = false;
        if ("mozPreservesPitch" in audio) audio.mozPreservesPitch = false;
        if ("webkitPreservesPitch" in audio) audio.webkitPreservesPitch = false;
        updateTuneValueLabels();
      }

      function onPreviewTuneChanged() {
        if (previewConfigLocked) previewConfigSnapshot = capturePreviewConfig();
        applyPreviewAudioTuning(); updatePreview();
      }

      function parseLrcData(lrcText) {
        lyricLines = [];
        // Strip manual chorus markers ([00:12.34]*word) so they never render as
        // lyric text; they only select which words the chorus stem vocalizes.
        lrcText = String(lrcText || "").replace(/(\[\d+:\d+(?:\.\d{1,3})?\])\s*\*/g, "$1");
        const tagRegex = /\[(\d+):(\d+)(?:\.(\d{1,3}))?\]/g;
        const endTagRegex = /<(\d+):(\d+)(?:\.(\d{1,3}))?>/g;
        const toSeconds = (m) => parseInt(m[1], 10) * 60 + parseInt(m[2], 10) + parseInt((m[3] || "0").padEnd(3, "0").slice(0, 3), 10) / 1000;
        const plain = [];
        let breakBefore = false;
        lrcText.split("\n").forEach((line) => {
          let raw = (line || "").trim();
          if (!raw) { breakBefore = lyricLines.length > 0 || plain.length > 0; return; }
          // Capture the duet voice for this line, then strip the {m}/{f} marker so
          // it never appears as lyric text.
          const lineGender = getLineGender(raw);
          raw = raw.replace(/\{[mfb]\}/g, "");
          const tags = [...raw.matchAll(tagRegex)];
          if (tags.length === 0) {
            plain.push({ text: raw.replace(endTagRegex, "").trim() || raw, breakBefore });
            breakBefore = false;
            return;
          }
          // Split into [tag -> following text] pieces so inline word-level timestamps
          // ([t1]word1 [t2]word2) label their own words instead of duplicating the whole line.
          const splitRe = /\[(\d+):(\d+)(?:\.(\d{1,3}))?\]/g;
          const pieces = [];
          let m, prevTime = null, prevIndex = 0;
          while ((m = splitRe.exec(raw)) !== null) {
            if (prevTime !== null) pieces.push({ time: prevTime, raw: raw.slice(prevIndex, m.index) });
            prevTime = toSeconds(m);
            prevIndex = splitRe.lastIndex;
          }
          if (prevTime !== null) pieces.push({ time: prevTime, raw: raw.slice(prevIndex) });
          const parsed = pieces.map((p) => {
            const ends = [...p.raw.matchAll(endTagRegex)];
            return { time: p.time, text: p.raw.replace(endTagRegex, "").trim(), end: ends.length ? toSeconds(ends[ends.length - 1]) : null };
          });
          const nonEmpty = parsed.filter((p) => p.text);
          if (nonEmpty.length <= 1) {
            // A single line, or a repeated line with leading stacked timestamps.
            const fullEnds = [...raw.matchAll(endTagRegex)];
            const fullText = raw.replace(tagRegex, "").replace(endTagRegex, "").trim();
            if (!fullText) return;
            const endT = fullEnds.length ? toSeconds(fullEnds[fullEnds.length - 1]) : null;
            parsed.forEach((p) => {
              lyricLines.push({ time: p.time, end: endT, text: fullText, breakBefore, gender: lineGender });
              breakBefore = false;
            });
          } else {
            // Inline word-level timing: each timestamp labels the word(s) after it.
            nonEmpty.forEach((p, i) => {
              lyricLines.push({ time: p.time, end: p.end, text: p.text, breakBefore: breakBefore && i === 0, gender: lineGender });
            });
            breakBefore = false;
          }
        });
        lyricLines.sort((a, b) => a.time - b.time);
        updateLyricsReadyIndicator();
      }

      // CLAMPED MATH: Prevents negative values from causing the whole screen to disappear
      // FIXED MATH: Scales perfectly 0 to 1 based on the Speed Dropdown
      function applyTransition(p, style) {
        let alpha = 1, scale = 1, yOff = 0, blur = 0, rotation = 0, skewX = 0;
        let pClamped = Math.max(0, Math.min(1, p));

        if (style === "fade") alpha = pClamped;
        else if (style === "pop") scale = pClamped < 0.5 ? 1.0 + (0.1 * (1 - (pClamped/0.5))) : 1;
        else if (style === "slide") yOff = (1 - pClamped) * 40;
        else if (style === "zoom") { scale = 0.8 + (pClamped * 0.2); alpha = pClamped; }
        else if (style === "drop") { yOff = (1 - pClamped) * -40; alpha = pClamped; }
        else if (style === "blur") { blur = (1 - pClamped) * 10; alpha = pClamped; }
        else if (style === "bouncing-ball") { yOff = Math.sin(pClamped * Math.PI) * -24; scale = 1 + (Math.sin(pClamped * Math.PI) * 0.08); }
        else if (style === "rotate-360") { rotation = (1 - pClamped) * Math.PI * 2; alpha = Math.min(1, pClamped * 1.2); }
        else if (style === "glimmer") { alpha = 0.72 + (Math.sin(pClamped * Math.PI * 3) * 0.28); scale = 1 + (Math.sin(pClamped * Math.PI) * 0.03); }
        else if (style === "shake") { yOff = Math.sin(pClamped * Math.PI * 8) * 8; }
        else if (style === "flip") { scale = Math.max(0.05, Math.abs(Math.cos(pClamped * Math.PI))); }
        else if (style === "pulse") { scale = 0.82 + (0.28 * Math.sin(pClamped * Math.PI)); alpha = 0.65 + (0.35 * pClamped); }
        else if (style === "sway") { rotation = Math.sin(pClamped * Math.PI * 1.5) * 0.18; }
        else if (style === "skew") { skewX = Math.sin(pClamped * Math.PI * 1.5) * 0.35; }
        else if (style === "stamp") { scale = 1.38 - (0.38 * pClamped); alpha = pClamped; }
        else if (style === "focus") { blur = (1 - pClamped) * 9; alpha = pClamped; }

        return { alpha, scale, yOff, blur, rotation, skewX };
      }
      function applyTextEffect(effect, pColor, sColor) {
        ctx.shadowBlur = 0; ctx.shadowColor = "transparent"; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0;
        if (effect === "shadow") { ctx.shadowBlur = 15; ctx.shadowColor = getThemeColor("--shadow", "#000000"); }
        else if (effect === "hard-shadow") { ctx.shadowOffsetX = 8; ctx.shadowOffsetY = 8; ctx.shadowColor = getThemeColor("--shadow", "#000000"); }
        else if (effect === "glow") { ctx.shadowBlur = 25; ctx.shadowColor = getThemeColor("--paper", "#ffffff"); }
        else if (effect === "neon") { ctx.shadowBlur = 35; ctx.shadowColor = sColor; }
      }

      function handleBgUpload(event) {
        const file = event.target.files[0];
        if (file) {
          if (bgObjectUrl) { URL.revokeObjectURL(bgObjectUrl); bgObjectUrl = null; }
          bgImageObj = new Image();
          bgImageObj.onload = () => { updatePreview(); };
          bgObjectUrl = URL.createObjectURL(file);
          bgImageObj.src = bgObjectUrl;
          document.getElementById("bgType").value = "image";
          if (selectedAudioFilename) {
            const fd = new FormData();
            fd.append("file", file);
            fd.append("audio_filename", selectedAudioFilename);
            fetch("/api/upload-bg", { method: "POST", body: fd }).catch(() => {});
          }
        }
      }

      // ================================================================
      // SECTION: Canvas Text Layout Engine
      // Purpose: Build stable text layout (lines/words) from LRC lyrics,
      //          calculate word positions and timing for rendering
      // ================================================================

      // NEW STATIC LAYOUT ENGINE: Wraps all lyrics once to perfectly prevent shifting
      function buildStableLines(currentTime, maxW, wordPad = 0) {
        let lines = [];
        const spaceW = ctx.measureText(" ").width + wordPad;
        const MAX_WORDS_PER_LINE = 8;
        const PAUSE_THRESHOLD_SEC = 0.8;

        let wordTokens = [];

        if (manualTimingState.enabled) {
            // Timing mode ignores any existing timing: show every word statically
            // and only fill words the user has captured with S/F this session.
            // Auto timing (with editor precedence) resumes once timing mode is off.
            const words = manualTimingState.words.length ? manualTimingState.words : extractWordsFromEditor();
            const genders = extractWordGendersFromEditor();
            words.forEach((w, idx) => {
                const entry = manualTimingState.entries[idx];
                const startT = (entry && Number.isFinite(entry.start)) ? entry.start : null;
                // Keep end null when the word has no explicit F yet so it is treated as "open".
                const endT = (entry && Number.isFinite(entry.end)) ? entry.end : null;
                wordTokens.push({ text: w, start: startT, end: endT, isManual: true, idx: idx, gender: genders[idx] || null });
            });
            if (wordTokens.length === 0) return [];
        } else if (lyricLines.length === 0) {
            // No timing is present (e.g. right after "Clear All Timing"). Show the
            // lyric words statically so they never disappear; they simply stay
            // unfilled until timing is captured or generated again.
            const editorText = document.getElementById("lyricsEditor")?.value || "";
            let sawContent = false;
            editorText.split("\n").forEach((rawLine) => {
                const gender = getLineGender(rawLine);
                const line = rawLine
                    .replace(/\[(\d+):(\d+)(?:\.(\d{1,3}))?\]/g, "")
                    .replace(/<(\d+):(\d+)(?:\.(\d{1,3}))?>/g, "")
                    .replace(/\{[mfb]\}/g, "")
                    .trim();
                if (!line) return;
                const words = line.split(/\s+/).filter(Boolean);
                words.forEach((w, wIdx) => {
                    wordTokens.push({ text: w, start: null, end: null, isStatic: true, breakBefore: sawContent && wIdx === 0, gender });
                });
                if (words.length) sawContent = true;
            });
            if (wordTokens.length === 0) return [];
        } else {
            lyricLines.forEach((item, idx) => {
                const nextTime = lyricLines[idx + 1] ? lyricLines[idx + 1].time : item.time + 3.0;
                let words = (item.text || "").split(" ").filter(Boolean);
                if (!words.length) return;
                if (words.length === 1 && Number.isFinite(item.end) && item.end > item.time) {
                    // Word-level line with an explicit end (F): fill start->end, then hold until next line.
                    wordTokens.push({ text: words[0], start: item.time, end: item.end, isLrcStart: true, breakBefore: !!item.breakBefore, gender: item.gender || null });
                    return;
                }
                const durPerWord = (nextTime - item.time) / words.length;
                words.forEach((w, wIdx) => {
                    wordTokens.push({ text: w, start: item.time + (wIdx * durPerWord), end: item.time + ((wIdx + 1) * durPerWord), isLrcStart: wIdx === 0, breakBefore: !!item.breakBefore && wIdx === 0, gender: item.gender || null });
                });
            });
        }

        let currLine = []; let currW = 0;

        wordTokens.forEach((token, i) => {
            const tw = ctx.measureText(token.text).width;
            const prevToken = i > 0 ? wordTokens[i - 1] : null;
            let forceBreak = false;

            if (currLine.length > 0 && (currW + spaceW + tw) > maxW) forceBreak = true;
            if (currLine.length >= MAX_WORDS_PER_LINE) forceBreak = true;
            if (prevToken && prevToken.end !== null && token.start !== null && (token.start - prevToken.end) > PAUSE_THRESHOLD_SEC) forceBreak = true;
            if (token.breakBefore) forceBreak = true;
            if (token.isLrcStart && currLine.length > 0) {
                const prevIsLrcStart = prevToken ? prevToken.isLrcStart : true;
                const nextIsLrcStart = (i + 1 < wordTokens.length) ? wordTokens[i+1].isLrcStart : true;
                if (!(prevIsLrcStart && nextIsLrcStart)) forceBreak = true;
            }

            if (forceBreak && currLine.length > 0) { lines.push(currLine); currLine = []; currW = 0; }

            let prog = 0, isActive = false, isPast = false;
            if (token.isStatic) {
                // Untimed word: render as-is with no fill or highlight.
                isActive = false; isPast = false; prog = 0;
            } else if (token.isManual) {
                const s = token.start;
                const e = token.end;
                if (s === null) {
                    // Word not timed yet: unfilled and inactive.
                    isActive = false; isPast = false; prog = 0;
                } else if (e === null) {
                    // Open word being captured (S pressed, awaiting F or next S).
                    isActive = true;
                    const dur = estimateActiveWordDuration();
                    prog = Math.max(0, Math.min(1, (currentTime - s) / dur));
                } else if (currentTime >= e) {
                    // F reached: word is finished and holds fully filled through any pause.
                    isPast = true; prog = 1;
                } else if (currentTime >= s) {
                    // Filling from S to F.
                    isActive = true;
                    const dur = Math.max(0.02, e - s);
                    prog = Math.max(0, Math.min(1, (currentTime - s) / dur));
                }
            } else {
                isActive = (currentTime >= token.start && currentTime < token.end);
                isPast = (currentTime >= token.end);
                if (isActive) {
                    const dur = Math.max(0.02, token.end - token.start);
                    prog = Math.max(0, Math.min(1, (currentTime - token.start) / dur));
                }
            }

            // FIXED: Added `start` to the payload so animations can calculate absolute entrance times
            currLine.push({
              text: token.text,
              active: isActive,
              progress: isActive ? prog : (isPast ? 1 : 0),
              start: token.start,
              end: token.end,
              isManual: !!token.isManual,
              idx: token.idx,
              gender: token.gender || null,
            });
            currW += (currLine.length === 1 ? tw : spaceW + tw);
        });

        if (currLine.length > 0) lines.push(currLine);
        return lines;
      }



      function onAudioLoaded() {
        if (previewShouldStartAtZero) {
          audio.currentTime = 0;
          previewShouldStartAtZero = false;
        }
        applyPreviewAudioTuning();
        syncSeekBar();
        displayTimeTracker();
        updatePreview();
      }





      function onSeekBarInput() { isSeekingPreview = true; seekPreviewToSlider(); }
      function onSeekBarCommit() { seekPreviewToSlider(); isSeekingPreview = false; }
      function seekPreviewToSlider() {
        const dur = Number.isFinite(audio.duration) ? audio.duration : 0;
        if (dur > 0) audio.currentTime = dur * (Number(document.getElementById("seekBar").value || 0) / 1000);
        syncPlaybackUi();
      }
      function syncSeekBar() {
        if (isSeekingPreview) return;
        const dur = Number.isFinite(audio.duration) ? audio.duration : 0;
        document.getElementById("seekBar").value = dur > 0 ? Math.round((audio.currentTime / dur) * 1000) : 0;
      }
      function onPlaybackRateChange() { applyPreviewAudioTuning(); }
      function displayTimeTracker() {
        const fm = (s) => `${Math.floor(s / 60).toString().padStart(2, "0")}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
        document.getElementById("timeDisplay").textContent = `${fm(audio.currentTime || 0)} / ${fm(Number.isFinite(audio.duration) ? audio.duration : 0)}`;
      }

      function syncPlaybackUi() {
        syncSeekBar();
        displayTimeTracker();
        updateManualTimingHud();
        updatePreview();
        drawVocalWaveform();
      }

      function skipPreview(seconds) {
        if (!audio.src || !Number.isFinite(audio.duration)) return;
        audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + seconds));
        syncPlaybackUi();
      }

      function updatePreview() { drawFrame(audio.currentTime || 0); }

      let smoothScrollY = 0; // State variable for Continuous Scroll Mode

     function drawTokenLine(tokens, y, pColor, sColor, oColor, wordPad, centerX, frameTime, schemes) {
      const spaceW = ctx.measureText(" ").width + wordPad;
      const cx = Number.isFinite(centerX) ? centerX : (canvas.width / 2);

      // Use the frame time being rendered so the preview stays consistent with
      // drawFrame(); fall back to the live element only if it wasn't provided.
      const currentTime = Number.isFinite(frameTime)
        ? frameTime
        : (audio ? audio.currentTime : 0);

      let x = cx - tokens.reduce((s, t, i) => s + ctx.measureText(t.text).width + (i > 0 ? spaceW : 0), 0) / 2;
      const positions = [];

      tokens.forEach((t, i) => {
          if (i > 0) x += spaceW;
          const tw = ctx.measureText(t.text).width;
          positions.push({ x: x + (tw / 2), width: tw });
          // Per-word duet voice colors: fall back to the default scheme when the
          // word has no gender assigned.
          const scheme = (schemes && t.gender && schemes[t.gender]) ? schemes[t.gender] : null;
          const wp = scheme ? scheme.p : pColor;
          const ws = scheme ? scheme.s : sColor;
          const wo = scheme ? scheme.o : oColor;
          // Word-scope FX is a one-shot entrance animation that runs as the word
          // is reached. It must only apply while the word is animating in, and it
          // must never leave a word invisible once its animation window passed.
          const wordFx = activeFxScope === "word"
            && activeTransitionStyle !== "none"
            && Number.isFinite(t.start)
            && currentTime >= t.start
            && currentTime < t.start + Math.max(0.05, activeFxSpeed);
          if (wordFx) {
            const wordProgress = Math.max(0, Math.min(1, (currentTime - t.start) / Math.max(0.05, activeFxSpeed)));
            const wordTransition = applyTransition(wordProgress, activeTransitionStyle);
            ctx.save();
            ctx.globalAlpha = wordTransition.alpha;
            ctx.translate(x + (tw / 2), y + wordTransition.yOff);
            ctx.scale(wordTransition.scale, wordTransition.scale);
            if (wordTransition.skewX) ctx.transform(1, 0, wordTransition.skewX, 1, 0, 0);
            ctx.rotate(wordTransition.rotation || 0);
            ctx.translate(-(x + (tw / 2)), -y);
            if (wordTransition.blur > 0) ctx.filter = `blur(${wordTransition.blur}px)`;
          }
          ctx.strokeStyle = wo;
          if (outlineStrokeWidth > 0) ctx.strokeText(t.text, x, y);

          // Once 'F' was pressed (finite end) and playback is past it, hold the word fully filled.
          let currentProgress = t.progress || 0;
          if (Number.isFinite(t.end) && currentTime >= t.end) {
              currentProgress = 1.0;
          }

          // NEW: Use our currentProgress override instead of just checking t.active
          if (t.active || (currentProgress > 0 && currentProgress < 1)) {
              // NEW: Use currentProgress for the math instead of t.progress
              const split = Math.max(0, Math.min(t.text.length, Math.floor(t.text.length * currentProgress)));
              const left = t.text.slice(0, split);
              const right = t.text.slice(split);
              const lw = ctx.measureText(left).width;

              ctx.fillStyle = wp;
              ctx.fillText(left, x, y);

              ctx.fillStyle = ws;
              ctx.fillText(right, x + lw, y);

          } else if (currentProgress >= 1) { // NEW: Use currentProgress
              ctx.fillStyle = wp;
              ctx.fillText(t.text, x, y);
          } else {
              ctx.fillStyle = ws;
              ctx.fillText(t.text, x, y);
          }
          x += tw;
            if (wordFx) ctx.restore();
      });
          if (activeTransitionStyle === "bouncing-ball" && activeFxScope !== "word" && positions.length) {
          const activeIndex = tokens.findIndex((token) => token.isManual
            ? token.idx === manualTimingState.cursor
            : token.active);
          const lastFinished = tokens.reduce((last, token, index) => token.progress >= 1 ? index : last, -1);
          const targetIndex = activeIndex >= 0 ? activeIndex : lastFinished;
          if (targetIndex >= 0) {
            const target = positions[targetIndex].x;
              const previous = positions[lastFinished >= 0 ? lastFinished : targetIndex].x;
            const targetToken = tokens[targetIndex];
            const targetStart = targetToken.isManual
              ? (manualTimingState.activeStart ?? currentTime)
              : (Number.isFinite(targetToken.start) ? targetToken.start : currentTime);

            // Get bouncing ball settings from UI
            const arcHeight = parseFloat(document.getElementById("arcHeight")?.value || "78");
            const bouncePerSec = parseFloat(document.getElementById("bouncePerSec")?.value || "0.1");

            // Match the burned video's bounce magnitude: the render uses
            // arc_height * layout_scale pixels, so scale the preview the same way
            // (canvas is drawn at full res from the 1920x1080 baseline). Previously
            // this divided arc_height by 10 with no scaling, so changing the Arc
            // Height barely moved the preview ball.
            const ballLayoutScale = Math.min(canvas.width / 1920, canvas.height / 1080) || 1;
            const bounceMag = Math.max(2, arcHeight * ballLayoutScale);

            const flightProgress = activeIndex >= 0
              ? Math.max(0, Math.min(1, (currentTime - targetStart) / 0.32))
              : 1;
            const ballX = previous + ((target - previous) * flightProgress);
            const arc = Math.sin(flightProgress * Math.PI) * bounceMag;
            const idleBounce = activeIndex < 0 ? Math.abs(Math.sin(currentTime * Math.PI * bouncePerSec * 2)) * bounceMag * 0.67 : 0;
            // Vertical alignment: lift the ball above the words. Mirrors the burn's
            // font-size multiplier so tall fonts keep the ball clear of the glyphs.
            // ballAlign is a signed integer in [-15, +15]: 0 keeps the historical
            // default height, positive raises the ball, negative lowers it.
            const alignVal = parseFloat(document.getElementById("ballAlign")?.value || "0") || 0;
            const alignMult = 0.85 + alignVal * 0.12;
            const fontMatch = /(\d+(?:\.\d+)?)px/.exec(ctx.font);
            const fontPx = fontMatch ? parseFloat(fontMatch[1]) : 60;
            // Keep the value-0 look unchanged (~30px), then add proportional lift
            // for higher settings so the preview tracks the rendered video.
            const alignOffset = 30 + fontPx * (alignMult - 0.85);
            const priority = activeIndex >= 0 ? 2 : 1;
            if (!bouncingBallCandidate || priority >= bouncingBallCandidate.priority) {
              const selectedIcon = document.getElementById("ballIcon")?.value || "";
              const ballRotation = parseFloat(document.getElementById("ballRotation")?.value || "1.5");
              bouncingBallCandidate = {
                x: ballX,
                y: y - alignOffset - arc - idleBounce,
                color: pColor,
                priority,
                icon: selectedIcon,
                rotation: currentTime * Math.PI * 2 * ballRotation
              };
            }
          }
        }
  }

    // ================================================================
    // SECTION: Canvas Drawing Engine (Preview Rendering)
    // Purpose: Render lyrics, text effects, and animations to canvas
    //          in real-time as audio plays (50fps preview loop)
    // ================================================================

    function drawFrame(time) {
      const w = Math.max(320, Number(previewWidth) || 1280);
      const h = Math.max(180, Number(previewHeight) || 720);

      // Scale all lyric measurements from the 1920x1080 design baseline.
      const layoutScale = Math.min(w / 1920, h / 1080);

        const cx = Math.round(w / 2);
        const cy = Math.round(h / 2);

        ctx.clearRect(0, 0, w, h);
        const bgType = document.getElementById("bgType").value;
        const bgCol = document.getElementById("bgColor").value;
        const primCol = genderColorSchemes.m.p;

        const hasPreviewMedia = !!(typeof selectedAudioFilename !== 'undefined' && selectedAudioFilename && audio && audio.src);

        if (!hasPreviewMedia) {
            // Note: ensureKaraokePlaceholderImage() must exist elsewhere in your code
            const placeholder = typeof ensureKaraokePlaceholderImage === 'function' ? ensureKaraokePlaceholderImage() : null;

            if (placeholder && placeholder.complete) {
                ctx.drawImage(placeholder, 0, 0, w, h);
            } else {
                let fallback = ctx.createLinearGradient(0, 0, w, h);
                fallback.addColorStop(0, getThemeColor("--bg-base", "#0b0c10"));
                fallback.addColorStop(0.55, getThemeColor("--accent", "#45f3ff"));
                fallback.addColorStop(1, getThemeColor("--warning", "#ffcf66"));
                ctx.fillStyle = fallback;
                ctx.fillRect(0, 0, w, h);
            }
        } else if (bgType === "image" && typeof bgImageObj !== 'undefined' && bgImageObj) {
            ctx.drawImage(bgImageObj, 0, 0, w, h);
        } else if (bgType === "gradient") {
            let grd = ctx.createLinearGradient(0, 0, w, h);
            grd.addColorStop(0, bgCol);
            grd.addColorStop(1, genderColorSchemes.m.o);
            ctx.fillStyle = grd;
            ctx.fillRect(0, 0, w, h);
        } else if (bgType === "spiral") {
            let grd = ctx.createRadialGradient(cx, cy, Math.max(30, h * 0.05), cx, cy, Math.max(w, h) * 0.75);
            grd.addColorStop(0, bgCol);
            grd.addColorStop(0.5, primCol);
            grd.addColorStop(1, bgCol);
            ctx.fillStyle = grd;
            ctx.fillRect(0, 0, w, h);
        } else {
            ctx.fillStyle = bgCol;
            ctx.fillRect(0, 0, w, h);
        }

        const fName = document.getElementById("fontSelect").value || "Arial";
        const fSize = parseInt(document.getElementById("fontSize").value, 10) || 130;
        // Keep the active scheme's live edits in sync, then draw from the scheme
        // state. Male is the base/default used for ungendered lyrics.
        syncActiveSchemeFromInputs();
        const primary = genderColorSchemes.m.p;
        const secondary = genderColorSchemes.m.s;
        const outline = genderColorSchemes.m.o;
        const genderSchemes = {
          m: { p: genderColorSchemes.m.p, s: genderColorSchemes.m.s, o: genderColorSchemes.m.o },
          f: { p: genderColorSchemes.f.p, s: genderColorSchemes.f.s, o: genderColorSchemes.f.o },
          b: { p: genderColorSchemes.b.p, s: genderColorSchemes.b.s, o: genderColorSchemes.b.o },
        };
        const tStyle = document.getElementById("transitionStyle").value;
        const effect = document.getElementById("textEffect").value;
        const baseLineGap = parseInt(document.getElementById("lineSpacing").value, 10) || 30;
        const baseWordPad = parseInt(document.getElementById("wordPadding").value, 10) || 0;
        const scaledFontSize = Math.max(26, Math.round(fSize * layoutScale));
        const lineGap = Math.max(8, Math.round(baseLineGap * layoutScale));
        const wordPad = Math.max(0, Math.round(baseWordPad * layoutScale));

        // New FX modifiers (read through cached accessors so a hidden/absent
        // FX panel never resets scope/speed back to defaults mid-playback)
        const fxScope = getFxScope();
        const fxSpeed = parseFloat(getFxSpeed());
        activeFxScope = fxScope;
        activeTransitionStyle = tStyle;
        activeFxSpeed = Math.max(0.05, fxSpeed || 0.6);
        bouncingBallCandidate = null;

        ctx.font = `bold ${scaledFontSize}px ${fName}`;
        const horizontalPadding = Math.max(90, Math.round(w * 0.08));
        const maxW = Math.max(280, w - horizontalPadding * 2);
        const lHeight = Math.round(scaledFontSize * 1.1);

        const allLines = buildStableLines(time, maxW, wordPad);
        const reqVisible = Math.max(1, Math.min(4, parseInt(document.getElementById("previewLineCount").value, 10) || 3));
        const revealMode = document.getElementById("revealMode").value || "continuous";

        let activeIdx = -1;
        for (let i = 0; i < allLines.length; i++) { if (allLines[i].some((t) => t.active)) { activeIdx = i; break; } }
        if (activeIdx < 0) {
          // No word is actively filling (e.g. the pause after pressing F). Stay on the
          // most recently started word instead of snapping back to the first page.
          let bestStart = -Infinity;
          for (let i = 0; i < allLines.length; i++) {
            allLines[i].forEach((t) => {
              if (Number.isFinite(t.start) && t.start <= time && t.start >= bestStart) { bestStart = t.start; activeIdx = i; }
            });
          }
          if (activeIdx < 0) activeIdx = 0;
        }

        // Timing mode: keep the view parked on the word the user is about to time
        // (the cursor), so playback never scrolls the karaoke ahead of them. The
        // page waits here until they press S/F to advance the cursor.
        if (manualTimingState.enabled) {
          const cur = manualTimingState.cursor;
          let cursorLine = -1;
          for (let i = 0; i < allLines.length; i++) {
            if (allLines[i].some((t) => t.isManual && t.idx === cur)) { cursorLine = i; break; }
          }
          activeIdx = cursorLine >= 0 ? cursorLine : Math.max(0, allLines.length - 1);
        }

        if (allLines.length === 0) {
          ctx.save();
          ctx.textAlign = "center";
          ctx.font = `700 ${Math.max(24, Math.round(scaledFontSize * 0.45))}px ${fName}`;
          ctx.fillStyle = getThemeColor("--paper", "#ffffff");
          ctx.strokeStyle = getThemeColor("--shadow", "#000000");
          ctx.lineWidth = Math.max(2, Math.round(scaledFontSize * 0.03));
          const msg = hasPreviewMedia ? "Lyrics will appear here" : "Load media to start karaoke";
          ctx.strokeText(msg, cx, Math.round(h * 0.82));
          ctx.fillText(msg, cx, Math.round(h * 0.82));
          ctx.restore();
          return;
        }

        let linesToDraw = [];
        let bTop = cy;
        let startIdx = 0;

        // Display Mode Math
        if (revealMode === "continuous") {
          let targetScrollY = activeIdx * (lHeight + lineGap);
          smoothScrollY += (targetScrollY - smoothScrollY) * 0.08; // Interpolated Smooth Lerp

          const visibleBefore = Math.floor((reqVisible - 1) / 2);
          startIdx = Math.max(0, activeIdx - visibleBefore);
          const endIdx = Math.min(allLines.length, startIdx + reqVisible);
          linesToDraw = allLines.slice(startIdx, endIdx);

          bTop = cy - smoothScrollY + (startIdx * (lHeight + lineGap)) - Math.round(lHeight/2);
        } else if (revealMode === "block") {
          const pageIdx = Math.floor(activeIdx / reqVisible) * reqVisible;
          linesToDraw = allLines.slice(pageIdx, pageIdx + reqVisible);
          const bH = linesToDraw.length ? linesToDraw.length * lHeight + (linesToDraw.length - 1) * lineGap : lHeight;
          bTop = cy - Math.round(bH / 2);
        } else if (revealMode === "eager") {
          linesToDraw = allLines.slice(activeIdx, activeIdx + reqVisible);
          const bH = linesToDraw.length ? linesToDraw.length * lHeight + (linesToDraw.length - 1) * lineGap : lHeight;
          bTop = cy - Math.round(bH / 2);
        }

        ctx.textAlign = "left"; ctx.textBaseline = "middle";
        // Outline thickness: -1 (auto) keeps the classic font-relative stroke;
        // an explicit value (0 = none) is scaled like the font so the preview
        // matches the burned-in ASS \bord width.
        const outlineWidthSetting = getOutlineWidthSetting();
        ctx.lineJoin = "round";
        // Canvas2D silently ignores lineWidth <= 0 (it keeps the previous
        // value), so a zero-thickness outline has to be handled by skipping the
        // stroke entirely rather than by setting the width to 0.
        outlineStrokeWidth = outlineWidthSetting < 0
          ? Math.max(2, Math.round(scaledFontSize * 0.16))
          : Math.max(0, Math.round(outlineWidthSetting * layoutScale));
        if (outlineStrokeWidth > 0) ctx.lineWidth = outlineStrokeWidth;

        if (linesToDraw.length) {
          applyTextEffect(effect, primary, secondary);

          // FX SCOPE: Page Entrance Animation
          if (fxScope === "page" && revealMode !== "continuous") {
            let pageStart = time;
            if (linesToDraw[0] && linesToDraw[0][0] && linesToDraw[0][0].start !== undefined) pageStart = linesToDraw[0][0].start;

            // One-shot page entrance: animate only while the page is entering so
            // the page never stays stuck at alpha 0 / mid-transform.
            const pageAnimating = Number.isFinite(pageStart)
              && time >= pageStart
              && time < pageStart + fxSpeed;
            let prog = pageAnimating
              ? Math.max(0, Math.min(1, (time - pageStart) / fxSpeed))
              : 1;
            const tr = applyTransition(prog, tStyle);

            ctx.save();
            ctx.globalAlpha = tr.alpha;
            if (tr.blur > 0) ctx.filter = `blur(${tr.blur}px)`;
            ctx.translate(cx, cy + tr.yOff); ctx.scale(tr.scale, tr.scale); if (tr.skewX) ctx.transform(1, 0, tr.skewX, 1, 0, 0); ctx.rotate(tr.rotation || 0); ctx.translate(-cx, -cy);

            linesToDraw.forEach((t, i) => drawTokenLine(t, bTop + Math.round(lHeight / 2) + i * (lHeight + lineGap), primary, secondary, outline, wordPad, cx, time, genderSchemes));
            ctx.restore();

          } else if (fxScope === "line") {
            // FX SCOPE: Line Entrance Animation (or fallback for continuous)
            linesToDraw.forEach((t, i) => {
              let lineStart = time;
              if (t[0] && t[0].start !== undefined) lineStart = t[0].start;

              // One-shot entrance: only animate while the line is entering, and
              // never leave an already-entered (or upcoming) line invisible.
              const animating = Number.isFinite(lineStart)
                && time >= lineStart
                && time < lineStart + fxSpeed;
              let prog = animating
                ? Math.max(0, Math.min(1, (time - lineStart) / fxSpeed))
                : 1;
              const tr = applyTransition(prog, tStyle);

              ctx.save();
              ctx.globalAlpha = tr.alpha;
              if (tr.blur > 0) ctx.filter = `blur(${tr.blur}px)`;

              let lineY = bTop + Math.round(lHeight / 2) + i * (lHeight + lineGap);

              ctx.translate(cx, lineY + tr.yOff); ctx.scale(tr.scale, tr.scale); if (tr.skewX) ctx.transform(1, 0, tr.skewX, 1, 0, 0); ctx.rotate(tr.rotation || 0); ctx.translate(-cx, -lineY);
              drawTokenLine(t, lineY, primary, secondary, outline, wordPad, cx, time, genderSchemes);
              ctx.restore();
            });
          } else {
            linesToDraw.forEach((line, i) => {
              const lineY = bTop + Math.round(lHeight / 2) + i * (lHeight + lineGap);
              drawTokenLine(line, lineY, primary, secondary, outline, wordPad, cx, time, genderSchemes);
            });
          }
        } else {
          ctx.shadowBlur = 0; ctx.fillStyle = secondary; ctx.font = `bold ${Math.max(18, Math.round(scaledFontSize * 0.5))}px ${fName}`;
          ctx.fillText("No timed lyrics loaded yet", cx - ctx.measureText("No timed lyrics loaded yet").width / 2, cy);
        }
        if (bouncingBallCandidate) {
          const size = parseInt(document.getElementById("ballRadius")?.value || "26") * 2.5;

          if (bouncingBallCandidate.icon) {
            // Cache decoded icon images by src so we don't allocate a new Image()
            // (and re-trigger onload -> updatePreview loops) on every 50fps frame.
            const cache = (window.__ballIconImgCache ||= {});
            let img = cache[bouncingBallCandidate.icon];
            if (!img) {
              img = new Image();
              img.onload = () => updatePreview();
              img.src = bouncingBallCandidate.icon;
              cache[bouncingBallCandidate.icon] = img;
            }
            if (img.complete && img.naturalWidth > 0) {
              // Preserve aspect ratio inside the size x size box (fit-inside),
              // centred on the ball position — matches the FFmpeg overlay which
              // scales with force_original_aspect_ratio=decrease then pads to square.
              const nw = img.naturalWidth, nh = img.naturalHeight;
              const fit = Math.min(size / nw, size / nh);
              const dw = nw * fit, dh = nh * fit;
              const ang = bouncingBallCandidate.rotation || 0;
              ctx.save();
              ctx.translate(bouncingBallCandidate.x, bouncingBallCandidate.y);
              ctx.rotate(ang);
              ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
              ctx.restore();
            }
          } else {
            // ... Keep your existing vector drawing logic here for the standard ball
            ctx.beginPath();
            ctx.arc(bouncingBallCandidate.x, bouncingBallCandidate.y, size/2.5, 0, Math.PI * 2);
            ctx.fillStyle = bouncingBallCandidate.color;
            ctx.fill();
            ctx.stroke();
          }
        }
      }

      // ---- Preview control overlay auto-fade ----
      let previewControlsFadeTimer = null;
      function showPreviewControls() {
        const overlay = document.querySelector(".preview-header-overlay");
        if (!overlay) return;
        overlay.classList.add("controls-visible");
        if (previewControlsFadeTimer) clearTimeout(previewControlsFadeTimer);
        previewControlsFadeTimer = window.setTimeout(() => {
          overlay.classList.remove("controls-visible");
        }, 3500);
      }
      function initPreviewControlsAutoFade() {
        const pane = document.getElementById("previewPane");
        const overlay = document.querySelector(".preview-header-overlay");
        if (!pane || !overlay) return;
        ["pointermove", "pointerdown", "touchstart", "wheel"].forEach((evt) =>
          pane.addEventListener(evt, showPreviewControls, { passive: true })
        );
        // Keep controls fully visible while the user is actually adjusting an option.
        overlay.addEventListener("focusin", showPreviewControls);
        overlay.addEventListener("input", showPreviewControls);
        overlay.addEventListener("pointerenter", showPreviewControls);
        showPreviewControls();
      }

      // ---- Touch-friendly timing capture (mobile/tablet) ----
      function onTouchTimingPress(event, kind) {
        if (event) event.preventDefault();
        if (!manualTimingState.enabled) {
          toggleManualTiming();
          if (kind === "start") captureManualWordStart();
          return;
        }
        if (kind === "start") captureManualWordStart();
        else captureManualWordEnd();
      }

      // ================= DOCKED PANEL SYSTEM =================
      // Panels live in named CSS grid areas. Users can drag a panel by its
      // header onto another panel to SWAP the two areas, and drag the edge
      // grips to resize the shared grid tracks (so neighbours reflow instead
      // of overlapping). Assignments + track sizes persist in localStorage.
      const DOCK_DEFAULT = { jobs:"jobs", settings:"settings", lyrics:"lyrics", preview:"preview", vault:"vault", fx:"fx" };
      const DOCK_STORE = "karaokeDockLayout";
      let dockAssign = { ...DOCK_DEFAULT };

      function dockPanels() {
        return Array.from(document.querySelectorAll(".dock-panel"));
      }
      function applyDockAssignments() {
        dockPanels().forEach((p) => {
          const key = p.getAttribute("data-dock");
          const area = dockAssign[key] || key;
          p.style.gridArea = area;
          // Mirrored onto an attribute so CSS (e.g. preview takeover) can target
          // whichever panel currently occupies an area, even after a swap.
          p.setAttribute("data-dock-area", area);
        });
      }
      function saveDockLayout() {
        try {
          const layout = document.getElementById("appLayout");
          const cs = layout ? layout.style : null;
          localStorage.setItem(DOCK_STORE, JSON.stringify({
            assign: dockAssign,
            cols: cs ? [cs.getPropertyValue("--dock-col-left"), cs.getPropertyValue("--dock-col-right")] : null,
            rowMain: cs ? cs.getPropertyValue("--dock-row-main") : null,
            maximized: !!(layout && layout.classList.contains("preview-maximized")),
          }));
        } catch (e) {}
      }
      function loadDockLayout() {
        try {
          const raw = localStorage.getItem(DOCK_STORE);
          if (!raw) return;
          const d = JSON.parse(raw);
          if (d && d.assign) {
            // Only accept a complete, non-duplicated mapping.
            const vals = Object.values(d.assign);
            const keys = Object.keys(DOCK_DEFAULT);
            if (keys.every((k) => d.assign[k]) && new Set(vals).size === vals.length) {
              dockAssign = { ...d.assign };
            }
          }
          const layout = document.getElementById("appLayout");
          if (layout && d) {
            if (d.cols && d.cols[0]) { layout.style.setProperty("--dock-col-left", d.cols[0]); dockManualCols = true; }
            if (d.cols && d.cols[1]) { layout.style.setProperty("--dock-col-right", d.cols[1]); dockManualCols = true; }
            if (d.rowMain) { layout.style.setProperty("--dock-row-main", d.rowMain); dockManualRow = true; }
            if (d.maximized) layout.classList.add("preview-maximized");
          }
        } catch (e) {}
      }

      // ---- Drag a panel header onto another panel to swap places ----
      function initDockDragSwap() {
        let dragKey = null;
        let dropTarget = null;

        const clearTarget = () => {
          if (dropTarget) dropTarget.classList.remove("dock-drop-target");
          dropTarget = null;
        };

        dockPanels().forEach((panel) => {
          const handle = panel.querySelector(".section-toggle");
          if (!handle) return;
          handle.addEventListener("pointerdown", (ev) => {
            // Ignore clicks on real controls inside the header.
            if (ev.target.closest("button.btn, select, input, .dock-resizer")) return;
            if (ev.button !== 0) return;
            const startX = ev.clientX, startY = ev.clientY;
            let active = false;

            const move = (mv) => {
              if (!active) {
                if (Math.hypot(mv.clientX - startX, mv.clientY - startY) < 8) return;
                active = true;
                dragKey = panel.getAttribute("data-dock");
                panel.classList.add("dock-dragging");
                document.body.classList.add("dock-resizing");
              }
              const el = document.elementFromPoint(mv.clientX, mv.clientY);
              const over = el && el.closest ? el.closest(".dock-panel") : null;
              if (over !== dropTarget) {
                clearTarget();
                if (over && over !== panel) {
                  dropTarget = over;
                  dropTarget.classList.add("dock-drop-target");
                }
              }
            };
            const up = () => {
              document.removeEventListener("pointermove", move);
              document.removeEventListener("pointerup", up);
              document.body.classList.remove("dock-resizing");
              panel.classList.remove("dock-dragging");
              if (active && dropTarget && dragKey) {
                const otherKey = dropTarget.getAttribute("data-dock");
                const a = dockAssign[dragKey], b = dockAssign[otherKey];
                dockAssign[dragKey] = b;
                dockAssign[otherKey] = a;
                applyDockAssignments();
                saveDockLayout();
                if (typeof updatePreview === "function") updatePreview();
                window.dispatchEvent(new Event("resize"));
                if (typeof setStatus === "function") setStatus("Panels swapped.");
              }
              clearTarget();
              dragKey = null;
            };
            document.addEventListener("pointermove", move);
            document.addEventListener("pointerup", up);
          });
        });
      }

      // ---- Edge grips resize the shared grid tracks ----
      // ---- Preview takeover ----
      // When the preview is dragged taller than the page can accommodate, the
      // other panels would have to clip. Instead they slide up and out and the
      // preview claims the full width and height of the layout.
      const PREVIEW_TAKEOVER_SLACK = 24;

      function isPreviewPanel(panel) {
        if (!panel) return false;
        return (dockAssign[panel.getAttribute("data-dock")] || "") === "preview";
      }

      function setPreviewMaximized(on) {
        const layout = document.getElementById("appLayout");
        if (!layout || layout.classList.contains("preview-maximized") === !!on) return;
        layout.classList.toggle("preview-maximized", !!on);
        const btn = document.getElementById("previewMaxBtn");
        if (btn) {
          btn.textContent = on ? "🗗" : "⛶";
          btn.title = on ? "Restore panel layout" : "Expand preview to fill the page";
        }
        // The canvas is sized from its container, so re-measure after reflow.
        window.requestAnimationFrame(() => {
          window.dispatchEvent(new Event("resize"));
          if (typeof updatePreview === "function") updatePreview();
        });
      }

      function togglePreviewMaximized() {
        const layout = document.getElementById("appLayout");
        if (!layout) return;
        const on = !layout.classList.contains("preview-maximized");
        setPreviewMaximized(on);
        if (!on) {
          // Leaving takeover: drop the oversized row so panels get their space back.
          dockManualRow = false;
          layout.style.removeProperty("--dock-row-main");
          layout.style.removeProperty("grid-template-rows");
          try { fitUiToScreen(); } catch (e) {}
        }
        saveDockLayout();
      }

      function initDockResizers() {
        const layout = document.getElementById("appLayout");
        if (!layout) return;
        layout.addEventListener("pointerdown", (ev) => {
          const grip = ev.target.closest(".dock-resizer");
          if (!grip) return;
          ev.preventDefault();
          const panel = grip.closest(".dock-panel");
          const dir = grip.getAttribute("data-dir");
          const area = panel ? (dockAssign[panel.getAttribute("data-dock")] || "") : "";
          const startX = ev.clientX, startY = ev.clientY + window.scrollY;
          const cs = getComputedStyle(layout);
          const startLeft = parseFloat(cs.getPropertyValue("--dock-col-left")) || 340;
          const startRight = parseFloat(cs.getPropertyValue("--dock-col-right")) || 340;
          // Map the drag 1:1 onto the panel's own height. The preview spans two
          // grid rows, and the trailing auto row silently absorbs the first
          // ~160px of growth, which makes the grip feel dead; basing the new
          // track on the panel height instead keeps the edge under the cursor.
          const panelH = panel ? panel.getBoundingClientRect().height : 480;
          const startMain = panelH;
          const extraRows = 0;
          document.body.classList.add("dock-resizing");

          const move = (mv) => {
            const dx = mv.clientX - startX;
            // Track page-space Y so dragging past the viewport edge (which
            // scrolls the page) keeps increasing the height instead of stalling.
            const dy = (mv.clientY + window.scrollY) - startY;
            // Auto-scroll while the pointer sits against the bottom/top edge so
            // the grip can be dragged well beyond one screen height.
            if (dir === "s" || dir === "se") {
              const edge = 40;
              if (mv.clientY > window.innerHeight - edge) window.scrollBy(0, 24);
              else if (mv.clientY < edge) window.scrollBy(0, -24);
            }
            if (dir === "e" || dir === "se") {
              dockManualCols = true;
              // Which column does this panel occupy?
              if (area === "lyrics" || area === "fx") {
                layout.style.setProperty("--dock-col-left", `${Math.max(220, startLeft + dx)}px`);
              } else if (area === "vault") {
                // Right column grows when dragged left.
                layout.style.setProperty("--dock-col-right", `${Math.max(220, startRight - dx)}px`);
              } else {
                // Centre panels (jobs/settings/preview): their east edge is the
                // right column's boundary, so dragging right shrinks the vault.
                layout.style.setProperty("--dock-col-right", `${Math.max(220, startRight - dx)}px`);
              }
            }
            if (dir === "s" || dir === "se") {
              dockManualRow = true;
              const nextMain = Math.max(280, startMain + dy);
              layout.style.setProperty("--dock-row-main", `minmax(280px, ${nextMain}px)`);

              // FIX: Change '0px' to '1fr' or 'auto'.
              // '1fr' allows the panel to fill the remaining space in the container,
              // whereas '0px' was strictly capping it.
              if (isPreviewPanel(panel)) {
                layout.style.gridTemplateRows = `var(--dock-row-top) minmax(280px, ${nextMain}px) 1fr`;
              }

              if (isPreviewPanel(panel)) {
                const avail = window.innerHeight - PREVIEW_TAKEOVER_SLACK;
                setPreviewMaximized(nextMain >= avail);
              }
            }
          };
          const up = () => {
            document.removeEventListener("pointermove", move);
            document.removeEventListener("pointerup", up);
            document.body.classList.remove("dock-resizing");
            saveDockLayout();
            if (typeof updatePreview === "function") updatePreview();
            window.dispatchEvent(new Event("resize"));
          };
          document.addEventListener("pointermove", move);
          document.addEventListener("pointerup", up);
        });
      }

      function initDockSystem() {
        loadDockLayout();
        applyDockAssignments();
        initDockDragSwap();
        initDockResizers();
        // Sync the header button with a restored takeover state.
        const layout = document.getElementById("appLayout");
        const btn = document.getElementById("previewMaxBtn");
        if (layout && btn && layout.classList.contains("preview-maximized")) {
          btn.textContent = "🗗";
          btn.title = "Restore panel layout";
        }
      }

      // ---- Reset UI layout ----
      // Two stages:
      //   1. Strip every inline style written by the CSS `resize` handles and
      //      the panel-drag code, so panels fall back to the stylesheet.
      //   2. Re-fit the layout to the *current* screen (see fitUiToScreen), so
      //      "reset" means "ideal for this display", not a fixed default size.
      // Set once the user clicks Reset UI: keeps the fit up to date as the
      // window is resized or the page is zoomed, so the px values never
      // go stale against the new viewport.
      let uiFitActive = false;
      // Once the user drags a resize grip, their explicit track sizes win over
      // the automatic screen fit, so a window/zoom resize no longer clobbers them.
      let dockManualCols = false;
      let dockManualRow = false;
      function fitUiToScreen() {
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const layout = document.getElementById("appLayout");
        if (!layout) return;

        // Side columns take a proportional slice, clamped to a usable band,
        // leaving the centre (preview) column as the dominant area.
        const side = Math.round(Math.max(260, Math.min(400, vw * 0.19)));
        if (!dockManualCols) {
          layout.style.setProperty("--dock-col-left", `${side}px`);
          layout.style.setProperty("--dock-col-right", `${side}px`);
        }
        layout.style.maxWidth = `${Math.round(Math.min(vw - 24, 2400))}px`;

        // Give the preview row most of the viewport height. The pane itself
        // never gets a hard max-height: it must always show every bar and
        // button, so its content governs the minimum.
        const rowH = Math.round(Math.max(360, vh * 0.66));
        if (!dockManualRow) {
          layout.style.setProperty("--dock-row-main", `minmax(360px, ${rowH}px)`);
        }

        // The preview pane is grid-sized now; clear any legacy inline caps
        // that would otherwise clip its lower controls.
        const pane = document.getElementById("previewPane");
        if (pane) {
          ["max-height", "max-width", "width", "height"].forEach((prop) =>
            pane.style.removeProperty(prop)
          );
        }
      }

      function resetUiLayout() {
        uiFitActive = true;
        dockManualCols = false;
        dockManualRow = false;
        const layoutReset = document.getElementById("appLayout");
        if (layoutReset) layoutReset.classList.remove("preview-maximized");
        const maxBtn = document.getElementById("previewMaxBtn");
        if (maxBtn) { maxBtn.textContent = "⛶"; maxBtn.title = "Expand preview to fill the page"; }
        const selectors = [".card", ".preview-pane", "textarea", ".panel", ".section-body"];
        const seen = new Set();
        selectors.forEach((sel) => {
          document.querySelectorAll(sel).forEach((el) => {
            if (seen.has(el)) return;
            seen.add(el);
            // Only clear layout-related properties; leave unrelated inline
            // styling (colors, cursors, etc.) authored in the markup intact.
            ["width", "height", "min-width", "min-height", "max-width", "max-height",
             "top", "left", "right", "bottom", "position", "z-index", "transform",
             "flex", "flex-basis", "grid-area", "margin"].forEach((p) => {
              el.style.removeProperty(p);
            });
            el.classList.remove("panel-dragging");
          });
        });
        document.body.classList.remove("panel-drag-active");

        // The layout container may carry inline styles from a previous fit or
        // a manual resize; clear them so the stylesheet defaults apply again.
        const layoutEl = document.getElementById("appLayout");
        if (layoutEl) {
          ["width", "max-width", "height", "grid-template-columns", "grid-template-rows",
           "--dock-col-left", "--dock-col-right", "--dock-row-main", "--dock-row-top"].forEach((p) =>
            layoutEl.style.removeProperty(p)
          );
        }

        // Restore the default docking arrangement (undo any panel swaps).
        try {
          dockAssign = { ...DOCK_DEFAULT };
          applyDockAssignments();
        } catch (e) {}

        // Drop any persisted layout state.
        try {
          Object.keys(localStorage).forEach((k) => {
            if (/panel|layout|pane|size|pos/i.test(k)) localStorage.removeItem(k);
          });
        } catch (e) {}

        // Re-evaluate mobile/desktop, then size everything to this screen.
        try { if (typeof initMobileMode === "function") initMobileMode(); } catch (e) {}
        // Panels had their inline grid-area stripped above; put the docking
        // assignments back before measuring.
        try { applyDockAssignments(); } catch (e) {}
        try { fitUiToScreen(); } catch (e) {}
        try { saveDockLayout(); } catch (e) {}
        try { if (typeof updatePreview === "function") updatePreview(); } catch (e) {}
        try { if (typeof drawVocalWaveform === "function") drawVocalWaveform(); } catch (e) {}
        window.dispatchEvent(new Event("resize"));
        if (typeof setStatus === "function") {
          setStatus(`UI reset and fitted to this screen (${window.innerWidth}\u00d7${window.innerHeight}).`);
        }
      }

      // Keep a Reset-UI fit in sync with zoom / window-resize changes.
      let uiFitTimer = null;
      window.addEventListener("resize", () => {
        if (!uiFitActive) return;
        if (uiFitTimer) clearTimeout(uiFitTimer);
        uiFitTimer = window.setTimeout(() => {
          try { fitUiToScreen(); } catch (e) {}
        }, 120);
      });

      // ---- Mobile mode ----
      function applyMobileMode(on) {
        document.body.classList.toggle("mobile-mode", !!on);
        const btn = document.getElementById("mobileModeToggle");
        if (btn) btn.textContent = on ? "🖥 Desktop" : "📱 Mobile";
        try { localStorage.setItem("karaokeMobileMode", on ? "1" : "0"); } catch (e) {}
        if (typeof updatePreview === "function") updatePreview();
      }
      function toggleMobileMode() {
        applyMobileMode(!document.body.classList.contains("mobile-mode"));
      }
      function isLikelyMobileDevice() {
        const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
        const touch = "ontouchstart" in window || (navigator.maxTouchPoints || 0) > 0;
        const narrow = window.innerWidth <= 820;
        return (coarse && touch) || narrow;
      }
      function initMobileMode() {
        let saved = null;
        try { saved = localStorage.getItem("karaokeMobileMode"); } catch (e) {}
        if (saved === "1") applyMobileMode(true);
        else if (saved === "0") applyMobileMode(false);
        else applyMobileMode(isLikelyMobileDevice());
      }

      onPreviewResolutionChange();
      applyControlContrastVars();
      onFontSizeChanged(); updateManualTimingHud();
      initPreviewControlsAutoFade();
      initMobileMode();
      initDockSystem();   // docked grid: restore assignments, enable swap + resize
      fitUiToScreen();    // size the docked grid to this screen on first paint
      uiFitActive = true; // keep it fitted as the window is resized / zoomed
      updateFxPanel(); // Initialize FX panel

      // Defer heavy operations - UI is responsive first
      setTimeout(async () => {
        updatePreview(); // Heavy canvas drawing
      }, 50);

      setTimeout(async () => {
        await loadJobs();
        jobPollTimer = window.setInterval(loadJobs, 2000);
      }, 100);

      // Load supplementary data in background
      setTimeout(() => { loadFonts(); }, 500);
      setTimeout(() => { loadThemes(); }, 600);
      setTimeout(async () => { await loadProfiles(); await loadFileManifest(); }, 700);
