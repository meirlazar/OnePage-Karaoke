# OnePage Karaoke: AI Audio Video Production Suite

Welcome to the OnePage Karaoke Production Suite! This application is a comprehensive web-based tool for creating professional karaoke videos. It automatically separates audio stems, fetches or transcribes timed lyrics, allows for deep visual customization, and renders high-quality videos right from your browser.

---

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Media Ingestion (The Vault)](#2-media-ingestion-the-vault)
3. [Lyrics & Timing Engine](#3-lyrics--timing-engine)
4. [Visuals & FX Customization](#4-visuals--fx-customization)
5. [Export & Rendering](#5-export--rendering)
6. [Dynamic Theme Generator](#6-dynamic-theme-generator)

---

## 1. System Overview

OnePage Karaoke integrates multiple AI and media processing tools into a single, dockable user interface. 
* **Backend**: Powered by FastAPI, utilizing FFmpeg for media manipulation, Faster-Whisper for AI transcription, and Demucs for audio stem separation (vocal/instrumental splitting).
* **Frontend**: A dynamic, grid-based layout allowing you to dock and arrange panels (Active Jobs, Settings, Manage Lyrics, Preview, Media Vault, and FX Customization).
* **Hardware Acceleration**: Supports GPU/CUDA acceleration for stem separation, transcription, and NVENC video rendering.

---

## 2. Media Ingestion (The Vault)

The **Media Vault** is where your projects live. You can add media in two ways:
* **Local Upload**: Push audio or video files directly from your machine (supports `.mp3`, `.wav`, `.m4a`, `.flac`, `.mp4`, etc.).
* **Web Fetch**: Pull media via a URL using the integrated `yt-dlp` engine or a `MeTube` instance.

Once media is ingested, the system automatically starts a pipeline to download, separate the audio into stems, and attempt to fetch synced lyrics. Projects can be renamed, exported as `.zip` archives, or completely removed from the Vault.

---

## 3. Lyrics & Timing Engine

The **Manage Lyrics** panel provides powerful tools for sourcing and syncing text.

### Fetching Lyrics
* The system searches for the best available lyrics using LRCLib, SyncedLyrics, and Genius APIs.
* You can manually force a lyric pull by selecting a specific provider and clicking **Pull Lyrics**.
* If no lyrics are found, you can force the AI to transcribe the vocals using the Faster-Whisper model.

### Editing & Duet Voices
* **Text Editing**: Edit lyrics directly in the text area. The system auto-saves your changes and tracks a revision history.
* **Chorus Marking**: Highlight words and click **Add Chorus {*}** to mark them. These words appear bold in the editor and are used to selectively leave the original vocals in the "Chorus" audio stem render.
* **Duet Voice Marking**: Highlight lines and assign them to Male `{m}`, Female `{f}`, or Both `{b}` voices. This changes their colors in the video render to indicate who should sing. 

### Timing Correction
* **Manual Timing (S/F)**: Toggle "Timing Mode" on to hand-sync lyrics to the audio. Press **S** to start a word's highlight and **F** to end the word.
* **AI Auto-Correct**: Use "Minor", "Major", or "Custom" Auto-Correct to let the AI align text to the vocal waveform automatically.
* **Nudging**: Use the **I'm fast (+0.05s)** or **I'm slow (-0.05s)** buttons to globally shift the lyrics timestamps.

---

## 4. Visuals & FX Customization

The **Preview & Adjustments** pane offers a real-time HTML canvas preview of how your video will look.

### Layout & Text Options
* **Font & Size**: Select from available server fonts and dynamically scale the text.
* **Reveal Modes**: Choose how text appears on screen: **Scroll** (continuous continuous feeding), **Block** (page-by-page), or **Eager**
* **Text Effects**: Apply visual styles like Flat, Shadow, Hard Drop, Glow, or Neon.
* **Colors**: Define distinct Primary, Secondary, and Outline colors for the default voice, as well as separate palettes for Male, Female, and Both duet parts.

### FX & Transitions
* Apply entry animations like Fade, Zoom, Blur, Pop, Slide, Drop, Flip, Shake, Pulse, Sway, Skew, Stamp, Focus, and Rotate-360.
* **Bouncing Ball**: Add a classic bouncing ball over the lyrics. You can customize the ball's radius, arc height, bounce frequency, and colors.
* **Scope & Speed**: Apply transitions at the Page, Line, or Word level, and tweak the speed (Fast, Med, Slow).

---

## 5. Export & Rendering

Once you are satisfied with the preview, scroll to the bottom of the Preview pane to render the final video using FFmpeg. 

You have three render options:
1. **Create Karaoke + Vocals (Preview)**: Renders the video using the original full audio track so singers can follow along with the original artist.
2. **Create Karaoke + Chorus**: Uses the separated instrumental track but fades the original vocals back in specifically for lines/words you marked as Chorus.
3. **Create Std. Karaoke (Final)**: Renders the video with the pure instrumental track (no vocals).

You can track rendering progress in the **Active Jobs** panel. 

---

## 6. Dynamic Theme Generator

The app includes a bash script named `theme_generator.sh` for procedurally generating UI color schemes. 

### Usage
* Run the script in your terminal to generate as many JSON theme templates as yuo want.
* It combines random nouns and verbs (e.g., "Forest Ignite", "Nebula Pulse") to create unique 2-part theme names with unique color schemes.
* It uses an AWK-based engine to generate cohesive HSL palettes based on color theory schemes like Analogous, Complementary, Triadic, and Tetradic.
* Generated `.json` files are saved to the `./themes` directory by default, which the FastAPI server mounts and serves to the frontend UI.
