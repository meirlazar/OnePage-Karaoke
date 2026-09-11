# 🎤 OnePage-Karaoke 
### AI Audio & Video Production Suite 

![Docker](https://img.shields.io/badge/Docker-Supported-blue?logo=docker)
![NVIDIA GPU](https://img.shields.io/badge/GPU-CUDA_11.8-76B900?logo=nvidia)
![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi)


<img width="895" height="698" alt="image" src="https://github.com/user-attachments/assets/569e213c-f779-4cfa-87f6-45714d532f8a" />

#### OnePage Karaoke is the culmination of wanting a self-hosted, simple but highly flexible WebUI-driven karaoke song generator. 
It was born out of my love and passion for singing karaoke songs with my kids but waiting forever for someone to create a karaoke song from a new release drove me crazy.
So I dug around and found all the good karaoke creation tools were either strictly for Windows, or pricey, gimmicky, subscription-based, overly complex, or too automated, too limited, or simply didn't work. 
You can find a messload of karaoke generators on github, every day another popping up. 
These are usually one-commit-wonders that make you wonder how much commitment the developer put into the code. I got tired of building images for hours, watching & praying pip doesn't do what pip does best, give up.

I wanted it to usable even if you don't want to spend a ridiculous amount on a GPU so a decent LLM can accurately get the word-level timing correct. 
I wanted cool effects, duet-capabilities, chorus or no chorus vocals...
Or even better, I wanted absolute flexibility and control over everything, from the website theme to every little detail in the song itself.
I wanted it to look professional, but not have the interface so complex, that only an engineer could understand it.
I hope you enjoy it, you don't even have to buy me a coffee, I just wanted to give something to the world because it's the right thing to do. 

## 🎉 v2.0.0 New Features (from v1.0.0)
### Canvas & Rendering
- 60 FPS Real-Time Preview with requestAnimationFrame rendering loop
- Vocal Waveform Visualization with interactive zoom (↑↓) and pan (←→) controls
- 16 Text Effect Styles (Flat, Shadow, Hard Drop, Glow, Neon, Blur, Rotate-360°, Glimmer, Shake, Flip, etc.)
- 11 Transition Animations (Fade, Pop, Slide, Zoom, Drop, Blur, Rotate-360°, Glimmer, Shake, Flip, Bouncing Ball)
- 3 Reveal Modes (Block, Continuous/Scrolling, Eager)
- Advanced Background Support (Solid color, linear gradient, spiral gradient, custom image)
- Dynamic Resolution Output (240p, 360p, 540p, 720p, 1080p, 1440p with aspect ratio preservation)
- NVENC GPU Hardware Encoding with CPU fallback
- Per-Word Visual Effects when word-scope animations enabled
### Audio Features
- Volume Control (0–2x multiplier) in real-time preview
- Pitch Shifting (0.5–1.5x) in real-time preview
- Playback Speed Control (0.5x, 1.0x, 1.5x)
- Chorus-Aware Stem Separation (auto-detect chorus, render with vocals restored only in chorus sections)
- Or Custom Chorus generation by highlighting words/lines.
- Duet Mode (male, female, both) with complete flexibilty (toggle the gender icon) on color schemes for all 3.
- 3 Render Modes (Preview with vocals, Final instrumental, Chorus-aware instrumental)
### Lyrics & Timing
- Multi-Language Transcription (Auto, English, Russian, Hebrew, Spanish, French, German, Italian, Portuguese, Polish)
- 3 Lyrics Providers (lrclib, Genius, Syncedlyrics)
- Word-Level Timing Correction with 3 AI modes:
- Minor (conservative, max ±2s adjustment), Major (full AI re-alignment), and others...
- Lyric Revision History (step backward/forward through versions)
- Auto Word-Grouping (regroups single-word AI timings into readable phrases: 3–10 words per line)
- Lyric Editor with Live Preview Updates
### Project Management
- Project Snapshots (save/load complete project state with all settings)
- Media Vault (centralized project library with status indicators)
- Project Status Tracking (media only, stems ready, lyrics pending, project saved)
- Quick Load/Delete/Rename Actions for projects
- Auto-Sync Pipeline (automatic stem separation → lyrics fetch → project creation)
- Per-Project Organization (all assets in project-specific directories)
### UI/UX
- Collapsible & Expandable Section/Panels System.
- Multiple Built-in Themes (Catppuccin Mocha, Dracula, Gruvbox Dark, Nord, Rosé Pine, Tokyo Night)
- Theme generator (bash script) - auto generates a random but color-complementary theme and adds it to the themes directory.
- Theme System with Metadata (source credit, license, author info display)
- Compact Control Bar (font selector, size/gap/padding sliders, color pickers, FX controls in header overlay)
- Live Status Badges (running, completed, failed, cancelled with progress bars)
- Debug Toolbar (syntax/runtime error detection, diagnostics, copyable debug reports)
- Panel Dragging (rearrange UI sections with drag-and-drop)
- Responsive Layout (adapts to mobile/tablet screens)
### Hardware & Performance
- Per-Task Device Allocation (choose CUDA/CPU for stem separation, transcription, rendering independently)
- Intelligent GPU Fallback (auto-retry on CPU if CUDA fails)
- Memory-Safe Processing (automatic CUDA cache cleanup after each job)
- Malloc Trimming (return freed heap pages to OS)
- IPC Collect Support (CUDA IPC resource cleanup)
- Job Queue System with cancellation support
### Transcription & AI
- Faster-Whisper Integration with compute type options (int8_float16, float16, float32)
- Whisper Model Selection (small, medium, large-v2, large-v3)
- Multi-Compute Type Fallback (auto-retry with different quantization levels)
- VAD (Voice Activity Detection) filtering
- Word-Timestamp Precision from Faster-Whisper
- Initial Prompt Support (seed transcription with song title/artist for better accuracy)
### File & Asset Management
- Identity-Based Media Naming (FFProbe metadata extraction for artist/title)
- URL Download Engines (yt-dlp or MeTube dual support with smart fallback)
- Media File Validation (supported formats: MP3, WAV, M4A, FLAC, OGG, AAC, WEBM, MP4)
- Automatic Project Layout (moves loose media into organized project folders)
- ASS Subtitle Generation (Advanced Substation Alpha format with full styling)
- Custom Font Serving (fc-cache integration for automatic font discovery)
### Accessibility & Logging
- Detailed Job Diagnostics (error file, line, column, trace information)
- Exception Location Tracking (workspace-relative file paths in error reports)
- Python Syntax Validation (real-time syntax issue detection)
- Runtime Error Tracking (last 12 failed jobs captured for diagnostics)
- Comprehensive Logging (job queue, worker, render, transcription, resource cleanup logs)
### Advanced Features
- Bouncing Ball Animation (animated character hops word-to-word during playback)
- Word-Grouped Lyric Regrouping (configurable ASS_WORD_GROUP_SIZE and max gap)
- Per-Word Karaoke Tags (ASS \k timing codes for smooth color transitions)
- Layer-Based Subtitle Rendering (text, upcoming, effect layers)
- Dynamic Positioning (centered, scrolling, fixed positioning modes)
- Gradient & Spiral Effects (geq FFmpeg filters for animated backgrounds)


## **OnePage-Karaoke** is a high-performance, single-page web application that automates the creation of professional karaoke tracks. Powered by a FastAPI backend, PyTorch AI models (like Faster-Whisper for transcription), and a hardware-accelerated FFmpeg pipeline, this suite downloads, processes, and burns dynamic lyrics into media seamlessly.

## ✨ Core Features

* **All-in-One Interface**: A streamlined HTML5/Canvas frontend (`index.html`) that handles media uploading, lyric syncing, real-time lyric previews, and video rendering all on a single page.
* **Integrated Media Downloader**: Ships with a linked **MeTube** (yt-dlp) container, allowing you to instantly fetch media from the web directly into your workspace.
* **AI Transcription & Processing**: Utilizes `torch` (CUDA) and Whisper models (optimized via `int8` compute types) to process vocals natively on your GPU.
* **Advanced Subtitle Rendering**: Uses a custom-loaded FFmpeg environment equipped with `libass`, `libfreetype`, and `fonts-dejavu` to flawlessly render standard `.ass` karaoke text formats.
* **Custom Font Support**: Easily map your own font directories (e.g., TTF/OTF files) into the container to completely customize the lyric text styles in the final render.

## 🏗 Architecture

* **Frontend**: HTML / CSS / Vanilla JS (HTML5 Canvas for visual lyric previews)
* **Backend API**: Python 3.11 / FastAPI / Uvicorn (`web_server.py`)
* **AI/Compute Engine**: PyTorch 2.1.2+cu118
* **Media Engine**: FFmpeg (with `libsndfile1`, `libswresample-dev`, `libass-dev`)
* **Infrastructure**: Docker Compose (Multi-container architecture)

## ⚙️ Prerequisites

To run this application with full hardware acceleration, your host machine must have:
1. [Docker](https://docs.docker.com/get-docker/) and Docker Compose.
2. [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed to allow GPU passthrough.
3. A CUDA-compatible NVIDIA GPU. (Will fallback to CPU if none is found)

## 🚀 Installation & Setup

1. **Clone the Repository**
   ```bash
   git clone [https://github.com/meirlazar/OnePage-Karaoke.git](https://github.com/meirlazar/OnePage-Karaoke.git)
   cd OnePage-Karaoke
   ```

2. **Configure Font Directories (Optional)**
   By default, the docker-compose.yml mounts a local font directory (/home/meir/extrafonts) to /workspace/fonts.
   Update the volumes path in your docker-compose.yml if your custom fonts are stored elsewhere!

3. **Build and Launch via Docker Compose**
   Since the build compiles C-headers, FFmpeg dependencies, and pulls AI models, the initial build may take some time.
   ```Bash
   docker-compose up -d --build
   ```
   
## 🎯 Usage
Once the containers are up and running, the suite exposes two primary services: 
- OnePage Karaoke UI: http://localhost:8002
This is your main dashboard for creating karaoke videos, tweaking lyrics, and running the AI generation.

- MeTube Downloader: http://localhost:8001
Use this interface to paste YouTube/web URLs. Downloaded media will be sent directly to the shared workspace for the Karaoke UI to process.


## Steps
1. Open a browser to http://localhost:8002
2. Start at the Source and Ingest Window - Select your options and either upload a video/audio file or use the Web fetch to download from the internet (legal downlaods only please).
3. Once uploaded/downloaded, it will automatically split the file into the stems (vocals/no_vocals) and try to grab lyrics from the configured sources.
4. It will also show in the Media Vault Window. Press Load, to have this be your active project. If there are lyrics, they will display in the main Preview Window.
5. You can edit the lyrics in the Edit & Render window and commit them to have them immediately updated in the main preview window.
6. You can change the timing (Enable Timing Mode), Play, change speed & pitch, backgrounds, primary & secondary colors, special effects, spacing, size, resolution, etc.

## Lyrics Corrections 

**Word-Level Timing Mode**
- When a word starts press 'S' and press 'F' when it finishes (useful when there is a delay before the next word starts, otherwise just keep pressing 'S' at the beginning of each word being sung).
- Press the same Timing Mode button to exit that mode.
- Timing is automatically adjusted in real time in the Preview Window and the Lyrics Editor.
- Were you too slow on your timing? No worries, press the 'I'm Slow' button to have all the timings adjusted by -00.00.05 in realtime.
- Were you too fast? No worries, press the 'I'm Fast' button to have all the timings adjusted by +00.00.05 in realtime.
Note: When you enable Timing mode:
  The active word does not progress with the timing already in the lyrics editor. It will wait for you to do the manual timing (S/F). So you can adjust a few words if you choose, disable it again, and it will resume from there.
  
**Auto-Correct Word Timing**
- Alternatively you can have AI try to correct the word-level timing on the song.

**Reverting your lyrics**
- Go forward or backwards through the lyric revisions in case you prefer one over the other.

**Line Breaks**
- In the Lyrics Manager, Press Enter after a word/line to have the preview register it as the start of another line/sentence.

**Save Your Progress**
- When satisifed or just done for now, press Save Project Snapshot and resume later by simply pressing Load in the Media Vault, all settings will be saved.

**Chorus Vocals**
Do you want certain words sung in your karaoke video, and want 100% control of over which ones? You got it!
- In the Lyrics Editor, select the words/lines that you want to hear the vocals in your final karaoke video.
- Then press +Chorus button.
- They will be highlighted/bolded and when you generate a Karaoke + Chorus Video,  you will hear the original singer's vocals on those words (assuming your timing is correct).

- To remove any words/lines, simply select the words/lines, and press -Chorus.
- Those lines will be unbolded again.
  
**Making the Karaoke Videos**
- First Select the Resolution you want (when making the edits, I find it is much more responsive if you select a lower resolutino while you are creating/editing).
- Make sure your lyrics, timings, and Fx, colors, etc are all selected/chosen.
- Press 1,2, or 3 of the Create Karaoke Buttons below the Preview (Normal Karaoke, Karaoke+Chorus, Karaoke+Original Vocals)
- Videos will start being generated or queu up if anopther job is active.
- They will show up in the project folder under the output directory. 

## Directory Structure Mapping
The Docker configuration mounts a local ./workspace folder to persist your files:

/workspace/output/ - Contains the final generated Karaoke videos and background images.

/workspace/fonts/ - Where your custom fonts are read and cached via fc-cache.

/workspace/themes/ - Custom UI themes and CSS extensions.

## 🛠 Advanced Configuration
You can tweak the AI performance by adjusting the environment variables inside docker-compose.yml:
AI_DEVICE=cuda: Forces AI processing onto the GPU.
WHISPER_COMPUTE_TYPE_CUDA=int8: Determines the quantization level for Whisper models. Change to float16 if you have ample VRAM and want maximum accuracy.

## 🤝 Contributing
Contributions, bug reports, and feature requests are always welcome! Check out the issues page to get started.

## 📝 License
This project is open-source and available under the MIT License.
