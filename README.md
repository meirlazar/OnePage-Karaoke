# 🎤 OnePage-Karaoke 
### AI Audio & Video Production Suite (No AI Agent or Subscriptions required)

![Docker](https://img.shields.io/badge/Docker-Supported-blue?logo=docker)
![NVIDIA GPU](https://img.shields.io/badge/GPU-CUDA_11.8-76B900?logo=nvidia)
![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi)

<img width="1076" height="738" alt="image" src="https://github.com/user-attachments/assets/584f52ac-5b62-4d43-9953-fcc6403b4a2e" />

**OnePage-Karaoke** is a high-performance, single-page web application that automates the creation of professional karaoke tracks. Powered by a FastAPI backend, PyTorch AI models (like Faster-Whisper for transcription), and a hardware-accelerated FFmpeg pipeline, this suite downloads, processes, and burns dynamic lyrics into media seamlessly.

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
- Press Push to Editor when done either with all the lyrics or even if you changed the timing on a few words, and it will reflect in the Edit & Render window.
  
**Auto-Correct Word Timing**
- Alternatively you can have AI try to correct the word-level timing on the song.

**Reverting your lyrics**
- Go forward or backwards through the lyric revisions in case you prefer one over the other.

**Save Your Progress**
- When satisifed or just done for now, press Save Project Snapshot and resume later by simply pressing Load in the Media Vault, all settings will be saved.

**Making the Karaoke Videos**
- Press Render Preview to have a 1080p video created (with the vocals and your word-level timed lyrics) in the output directory under the projectname.
- Press Export Final to have a 1080p video created (without the vocals, and with your word-level timed lyrics) in the output directory under the projectname.

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
