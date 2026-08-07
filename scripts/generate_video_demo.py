#!/usr/bin/env python3
"""
Generate a sub-90-second product demo video for Veritas AI Construction Platform.

Outputs:
  docs/Veritas_AI_Construction_Demo.mp4

Requirements:
  py -m pip install edge-tts playwright flask
  py -m playwright install chromium
  ffmpeg on PATH
  Flask app running on http://localhost:5000
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "video_demo"
FINAL_VIDEO = ROOT / "docs" / "Veritas_AI_Construction_Demo.mp4"
BASE_URL = os.environ.get("VERITAS_DEMO_URL", "http://127.0.0.1:5000")
DASHBOARD_URL = os.environ.get(
    "VERITAS_DEMO_DASHBOARD",
    f"{BASE_URL.rstrip('/')}/dashboard?project=PRJ-2ACDC495",
)
VOICE = "en-GB-SoniaNeural"  # British female, professional
MAX_DURATION = 89.0
SCENE_GAP = 0.35


@dataclass
class Scene:
    name: str
    narration: str
    url: str | None = None
    action: str = "navigate"  # navigate | login | scroll


SCENES: list[Scene] = [
    Scene(
        "login",
        "Welcome to Veritas AI — the intelligent construction management platform "
        "built for modern vocational education.",
        url="/login",
        action="login",
    ),
    Scene(
        "dashboard",
        "Your executive dashboard unifies live project KPIs, BIM visualisation, "
        "and real-time safety alerts in one clear view.",
        url="/dashboard",
    ),
    Scene(
        "design_studio",
        "Design Studio empowers teams to create parametric building models "
        "and export industry-standard IFC files instantly.",
        url="/design-studio",
        action="scroll",
    ),
    Scene(
        "safety",
        "The Safety Monitor delivers live site camera feeds, zone tracking, "
        "and instant alert acknowledgement.",
        url="/safety",
    ),
    Scene(
        "resource_plan",
        "Resource Plan integrates Gantt scheduling, crew allocation, "
        "and critical path analysis for every phase.",
        url="/resource-plan",
        action="scroll",
    ),
    Scene(
        "vr_training",
        "And VR Training prepares students for real construction scenarios — "
        "safely, immersively, and at scale.",
        url="/vr-training",
    ),
]


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def ffprobe_duration(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(result.stdout.strip())


async def _edge_tts_save(text: str, out: Path, retries: int = 4) -> None:
    import edge_tts

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(text, VOICE, rate="-4%")
            await communicate.save(str(out))
            return
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"edge-tts failed after {retries} attempts: {last_err}") from last_err


def _sapi_save(text: str, out: Path) -> None:
    """Fallback: Windows Speech API (offline British voice if installed)."""
    import tempfile

    tmp = out.with_suffix(".tmp.wav")
    ps = rf"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $s.GetInstalledVoices() | ForEach-Object {{ $_.VoiceInfo }}
$preferred = $voices | Where-Object {{ $_.Culture.Name -like 'en-GB*' -and $_.Gender -eq 'Female' }} | Select-Object -First 1
if (-not $preferred) {{
  $preferred = $voices | Where-Object {{ $_.Culture.Name -like 'en-GB*' }} | Select-Object -First 1
}}
if ($preferred) {{ $s.SelectVoice($preferred.Name) }}
$s.Rate = 0
$s.SetOutputToWaveFile('{tmp.as_posix()}')
$s.Speak(@'
{text.replace("'", "''")}
'@)
$s.Dispose()
"""
    run(["powershell", "-NoProfile", "-Command", ps])
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(tmp),
            "-ar",
            "44100",
            "-ac",
            "1",
            str(out.with_suffix(".wav")),
        ]
    )
    tmp.unlink(missing_ok=True)
    # Normalise to mp3 path expected by downstream pipeline
    wav = out.with_suffix(".wav")
    run(["ffmpeg", "-y", "-i", str(wav), str(out)])
    wav.unlink(missing_ok=True)


def _valid_audio(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        return ffprobe_duration(path) > 0.5
    except subprocess.CalledProcessError:
        return False


async def generate_voice_segments() -> list[Path]:
    voice_dir = OUT_DIR / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for i, scene in enumerate(SCENES):
        out = voice_dir / f"{i:02d}_{scene.name}.mp3"
        if _valid_audio(out):
            print(f"  Voice (cached): {scene.name} ({ffprobe_duration(out):.1f}s)")
            paths.append(out)
            continue
        try:
            await _edge_tts_save(scene.narration, out)
        except Exception as exc:
            print(f"  edge-tts unavailable for {scene.name}, using Windows SAPI ({exc})")
            _sapi_save(scene.narration, out)
        paths.append(out)
        print(f"  Voice: {scene.name} ({ffprobe_duration(out):.1f}s)")
    return paths


def build_narration_track(voice_files: list[Path]) -> tuple[Path, list[float]]:
    """Concatenate voice clips with short gaps; return path and per-scene hold times."""
    narration = OUT_DIR / "narration.wav"
    concat_list = OUT_DIR / "narration_concat.txt"
    silence = OUT_DIR / "gap.wav"

    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=44100:cl=mono:d={SCENE_GAP}",
            str(silence),
        ]
    )

    lines: list[str] = []
    hold_times: list[float] = []
    for i, vf in enumerate(voice_files):
        dur = ffprobe_duration(vf)
        hold_times.append(dur + SCENE_GAP)
        lines.append(f"file '{vf.as_posix()}'")
        if i < len(voice_files) - 1:
            lines.append(f"file '{silence.as_posix()}'")

    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-ar",
            "44100",
            "-ac",
            "1",
            str(narration),
        ]
    )
    total = ffprobe_duration(narration)
    print(f"  Narration total: {total:.1f}s")
    if total > MAX_DURATION:
        raise RuntimeError(
            f"Narration is {total:.1f}s — exceeds {MAX_DURATION}s limit. Shorten script."
        )
    return narration, hold_times


def generate_upbeat_music(duration: float) -> Path:
    """Synthesise a light upbeat corporate bed (royalty-free, generated locally)."""
    music = OUT_DIR / "background_music.wav"
    sample_rate = 44100
    n_samples = int(duration * sample_rate)

    # 120 BPM, major-key chord stabs + soft kick pattern
    bpm = 118
    beat_samples = int(sample_rate * 60 / bpm)
    chord_freqs = [
        (261.63, 329.63, 392.00),  # C major
        (293.66, 369.99, 440.00),  # D minor feel
        (329.63, 415.30, 493.88),  # E minor feel
        (349.23, 440.00, 523.25),  # F major
    ]

    samples: list[float] = []
    for i in range(n_samples):
        t = i / sample_rate
        bar = (i // (beat_samples * 4)) % len(chord_freqs)
        beat = (i // beat_samples) % 4
        freqs = chord_freqs[bar]

        tone = 0.0
        for f in freqs:
            tone += math.sin(2 * math.pi * f * t) * 0.045

        # Envelope per bar — brighter on downbeat
        bar_pos = (i % (beat_samples * 4)) / (beat_samples * 4)
        env = max(0.15, 1.0 - bar_pos * 0.85)
        tone *= env

        # Soft kick on beats 0 and 2
        if beat in (0, 2) and (i % beat_samples) < int(sample_rate * 0.04):
            kick_t = (i % beat_samples) / sample_rate
            tone += math.sin(2 * math.pi * 80 * kick_t) * (1.0 - kick_t / 0.04) * 0.12

        # Hi-hat shimmer on off-beats
        if beat in (1, 3) and (i % beat_samples) < int(sample_rate * 0.01):
            tone += (hash(i) % 1000 / 1000 - 0.5) * 0.025

        samples.append(max(-1.0, min(1.0, tone)))

    with wave.open(str(music), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = b"".join(struct.pack("<h", int(s * 32767 * 0.55)) for s in samples)
        wf.writeframes(frames)

    print(f"  Background music: {duration:.1f}s")
    return music


def wait_for_bim_model(page, timeout_ms: int = 300_000) -> str:
    """Block until the dashboard BIM viewer finishes loading and renders."""
    page.wait_for_function(
        """() => {
            const overlay = document.getElementById('bimLoadingOverlay');
            if (overlay && overlay.classList.contains('active')) return false;

            const loadTxt = document.getElementById('bimLoadingText');
            if (loadTxt && loadTxt.offsetParent !== null) return false;

            const tag = document.getElementById('modelPhaseTag');
            const text = ((tag && tag.textContent) || '').trim();
            if (!text) return false;
            if (/loading|preparing|requesting|rendering/i.test(text)) return false;

            return (
                text.includes('BIM Model Loaded') ||
                text.includes('stage IFC') ||
                text.includes('estimated split')
            );
        }""",
        timeout=timeout_ms,
    )
    # Allow Three.js to paint several frames before we capture.
    page.wait_for_timeout(2500)
    tag = page.locator("#modelPhaseTag").inner_text().strip()
    print(f"      BIM ready: {tag}")
    return tag


def prewarm_bim_cache(page) -> None:
    """Log in once before recording so IFC geometry is cached on the server."""
    print("  Pre-warming BIM model cache…")
    page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
    wait_for_bim_model(page)


def trim_clip_tail(src: Path, dest: Path, duration_sec: float) -> None:
    """Keep only the last N seconds of a clip (discards loading / navigation)."""
    total = ffprobe_duration(src)
    start = max(0.0, total - duration_sec)
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(src),
            "-t",
            f"{duration_sec:.3f}",
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0E0E10",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-an",
            str(dest),
        ]
    )


def _scroll_page(page) -> None:
    page.evaluate(
        """() => new Promise(resolve => {
            let y = 0;
            const step = () => {
                y += 120;
                window.scrollTo({ top: y, behavior: 'smooth' });
                if (y < document.body.scrollHeight * 0.45) {
                    setTimeout(step, 350);
                } else resolve();
            };
            step();
        })"""
    )


def record_scene_clip(browser, scene: Scene, hold_sec: float, clips_dir: Path) -> Path:
    """Record one scene; trim away navigation/loading so only the hold remains."""
    from playwright.sync_api import sync_playwright  # noqa: F401 — imported in caller

    raw_dir = clips_dir / "raw" / scene.name
    raw_dir.mkdir(parents=True, exist_ok=True)
    trimmed = clips_dir / f"{scene.name}.mp4"

    ctx = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        record_video_dir=str(raw_dir),
        record_video_size={"width": 1920, "height": 1080},
        color_scheme="dark",
    )
    page = ctx.new_page()
    page.set_default_timeout(120_000)

    if scene.action == "login":
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        page.fill("#email", "instructor@btvi.edu.bs")
        page.fill("#password", "demo2026")
    elif scene.url in ("/dashboard", "/"):
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
        wait_for_bim_model(page)
    else:
        page.goto(f"{BASE_URL}{scene.url}", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        if scene.action == "scroll":
            _scroll_page(page)
            page.wait_for_timeout(800)

    # Only this final hold is kept in the trimmed clip.
    page.wait_for_timeout(int(hold_sec * 1000))

    page.close()
    ctx.close()
    raw_webm = page.video.path()

    trim_clip_tail(Path(raw_webm), trimmed, hold_sec)
    return trimmed


def record_screen(hold_times: list[float]) -> Path:
    from playwright.sync_api import sync_playwright

    clips_dir = OUT_DIR / "clips"
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True)

    print("  Recording browser walkthrough (per-scene clips)…")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-gl=angle",
                "--enable-webgl",
                "--ignore-gpu-blocklist",
            ],
        )

        warm_ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        prewarm_bim_cache(warm_ctx.new_page())
        warm_ctx.close()

        scene_clips: list[Path] = []
        for scene, hold in zip(SCENES, hold_times):
            print(f"    Scene: {scene.name} ({hold:.1f}s)")
            scene_clips.append(record_scene_clip(browser, scene, hold, clips_dir))

        browser.close()

    combined = OUT_DIR / "screen_combined.mp4"
    concat_list = clips_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{c.as_posix()}'" for c in scene_clips) + "\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(combined),
        ]
    )
    return combined


def assemble_final(combined_video: Path, narration: Path, music: Path, duration: float) -> Path:
    """Mix concatenated scene clips + loud narration + quieter upbeat music."""
    mixed_audio = OUT_DIR / "mixed_audio.wav"

    # Narration prominent; music bed at ~22% volume with gentle fade in/out
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(narration),
            "-i",
            str(music),
            "-filter_complex",
            "[0:a]volume=1.0,afade=t=in:st=0:d=0.4,afade=t=out:st="
            f"{max(0, duration - 0.8):.3f}:d=0.8[narr];"
            "[1:a]volume=0.22,afade=t=in:st=0:d=1.0,afade=t=out:st="
            f"{max(0, duration - 1.2):.3f}:d=1.2[mus];"
            "[narr][mus]amix=inputs=2:duration=first:dropout_transition=0[aout]",
            "-map",
            "[aout]",
            "-t",
            f"{duration:.3f}",
            str(mixed_audio),
        ]
    )

    FINAL_VIDEO.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(combined_video),
            "-i",
            str(mixed_audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(FINAL_VIDEO),
        ]
    )
    return FINAL_VIDEO


def verify_health() -> None:
    import urllib.error
    import urllib.request

    for path in ("/api/health", "/login", "/"):
        try:
            with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=8) as resp:
                if resp.status == 200:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise RuntimeError(
                    f"Flask app error at {BASE_URL}{path}: HTTP {exc.code}"
                ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Flask app not reachable at {BASE_URL}. "
                f"Start with: py -c \"import app; app.app.run(port=5001)\"\n  ({exc})"
            ) from exc
    raise RuntimeError(
        f"No Veritas routes found at {BASE_URL}. "
        "Another app may be using the port — try VERITAS_DEMO_URL=http://127.0.0.1:5001"
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Veritas AI — demo video generator")
    print(f"  Output: {FINAL_VIDEO}")

    verify_health()

    print("\n1/4 Generating British voiceover…")
    voice_files = asyncio.run(generate_voice_segments())

    print("\n2/4 Building narration track…")
    narration, hold_times = build_narration_track(voice_files)
    duration = ffprobe_duration(narration)

    print("\n3/4 Recording screen capture…")
    raw_video = record_screen(hold_times)

    print("\n4/4 Generating music and assembling final video…")
    music = generate_upbeat_music(duration + 0.5)
    final = assemble_final(raw_video, narration, music, duration)

    final_dur = ffprobe_duration(final)
    size_mb = final.stat().st_size / (1024 * 1024)
    print(f"\nDone!")
    print(f"  File:   {final}")
    print(f"  Length: {final_dur:.1f}s (limit {MAX_DURATION}s)")
    print(f"  Size:   {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
