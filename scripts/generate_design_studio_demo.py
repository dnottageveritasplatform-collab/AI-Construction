#!/usr/bin/env python3
"""
Generate a sub-90-second Design Studio demo: creating a family home.

Output: docs/Veritas_Design_Studio_Family_Home_Demo.mp4

Requirements:
  py -m pip install edge-tts playwright flask
  py -m playwright install chromium
  ffmpeg on PATH
  Flask app running (default http://127.0.0.1:5001)
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
OUT_DIR = ROOT / "docs" / "video_demo" / "design_studio"
FINAL_VIDEO = ROOT / "docs" / "Veritas_Design_Studio_Family_Home_Demo.mp4"
END_SCREEN_IMAGE = ROOT / "docs" / "assets" / "end_screen.png"
BASE_URL = os.environ.get("VERITAS_DEMO_URL", "http://127.0.0.1:5000")
PROJECT_ID = os.environ.get("VERITAS_DEMO_PROJECT", "PRJ-2ACDC495")
DESIGN_STUDIO_URL = (
    os.environ.get("VERITAS_DESIGN_STUDIO_URL")
    or f"{BASE_URL.rstrip('/')}/design-studio?project={PROJECT_ID}"
)
DASHBOARD_URL = (
    os.environ.get("VERITAS_DEMO_DASHBOARD")
    or f"{BASE_URL.rstrip('/')}/dashboard?project={PROJECT_ID}"
)
VOICE = "en-GB-SoniaNeural"  # British female, professional — no fallback voices
MAX_DURATION = 89.0
SCENE_GAP = 0.35
END_CARD_DURATION = 10.0
MUSIC_VOLUME = 0.345  # 30% base + 15% boost — narration remains at 100%
# Every user-facing module in the Veritas platform (app.py page routes).
PLATFORM_PAGES: list[tuple[str, str, str]] = [
    ("login", f"{BASE_URL.rstrip('/')}/login", "login"),
    ("dashboard", DASHBOARD_URL, "bim"),
    ("design_studio", DESIGN_STUDIO_URL, "design"),
    ("new_project", f"{BASE_URL.rstrip('/')}/new-project", "wizard"),
    ("resourciist", f"{BASE_URL.rstrip('/')}/resourciist", "scroll"),
    ("resource_plan", f"{BASE_URL.rstrip('/')}/resource-plan", "scroll"),
    ("safety", f"{BASE_URL.rstrip('/')}/safety?project_id={PROJECT_ID}", "scroll"),
    ("vr_training", f"{BASE_URL.rstrip('/')}/vr-training?project_id={PROJECT_ID}", "static"),
]
DESIGN_NAME = "Johnson Family Home"


@dataclass
class Scene:
    name: str
    narration: str
    action: str = "intro"
    fixed_hold: float | None = None


SCENES: list[Scene] = [
    Scene(
        "intro",
        "Welcome to Veritas Design Studio — parametric tools for creating "
        "build-ready family homes, entirely in your browser.",
    ),
    Scene(
        "template",
        "Load the Single-Family Residence template — a two-storey footprint, "
        "twelve by ten metres, ready to customise.",
        action="template",
    ),
    Scene(
        "plan_edit",
        "Place doors and windows on walls. Rooms are detected automatically, "
        "with areas calculated as you draw.",
        action="openings",
    ),
    Scene(
        "view_3d",
        "Switch to three-dimensional view to review massing, storeys, "
        "and roof form from any angle.",
        action="view_3d",
    ),
    Scene(
        "building",
        "Configure foundation depth, slab thickness, and a gable roof "
        "for realistic residential construction.",
        action="building",
    ),
    Scene(
        "save",
        "Save your design — it renders in the dashboard BIM viewer "
        "and exports to industry-standard IFC.",
        action="save",
    ),
    Scene(
        "platform",
        "Beyond design, Veritas covers secure login, executive dashboards, project wizards, "
        "asset inventories, Gantt planning, live safety monitoring, and immersive VR training.",
        action="platform_montage",
    ),
    Scene(
        "end_card",
        "",
        action="end_card",
        fixed_hold=END_CARD_DURATION,
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


async def _edge_tts_save(text: str, out: Path, retries: int = 8) -> None:
    import edge_tts

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(text, VOICE, rate="-4%")
            await communicate.save(str(out))
            return
        except Exception as exc:
            last_err = exc
            await asyncio.sleep(2.0 * (attempt + 1))
    raise RuntimeError(
        f"edge-tts failed for British voice ({VOICE}). "
        f"Check network and retry.\n  {last_err}"
    ) from last_err


def _valid_audio(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    try:
        return ffprobe_duration(path) > 0.5
    except subprocess.CalledProcessError:
        return False


async def generate_voice_segments() -> list[Path | None]:
    voice_dir = OUT_DIR / "voice"
    if os.environ.get("REGENERATE_VOICE") == "1":
        shutil.rmtree(voice_dir, ignore_errors=True)
    voice_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path | None] = []
    for i, scene in enumerate(SCENES):
        if scene.action == "end_card" or not scene.narration.strip():
            paths.append(None)
            continue
        out = voice_dir / f"{i:02d}_{scene.name}.mp3"
        if _valid_audio(out):
            print(f"  Voice (cached): {scene.name} ({ffprobe_duration(out):.1f}s)")
        else:
            await _edge_tts_save(scene.narration, out)
            print(f"  Voice: {scene.name} ({ffprobe_duration(out):.1f}s)")
        paths.append(out)
    return paths


def build_narration_track(
    voice_files: list[Path | None],
) -> tuple[Path, list[float], float]:
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
    hold_times: list[float] = []
    lines: list[str] = []
    voiced_indices = [i for i, vf in enumerate(voice_files) if vf is not None]
    for i, (scene, vf) in enumerate(zip(SCENES, voice_files)):
        if scene.fixed_hold is not None:
            hold_times.append(scene.fixed_hold)
            continue
        assert vf is not None
        dur = ffprobe_duration(vf)
        hold_times.append(dur + SCENE_GAP)
        lines.append(f"file '{vf.as_posix()}'")
        if i != voiced_indices[-1]:
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
    narr_dur = ffprobe_duration(narration)
    video_dur = sum(hold_times)
    print(f"  Narration: {narr_dur:.1f}s | Video: {video_dur:.1f}s")
    if video_dur > MAX_DURATION:
        raise RuntimeError(f"Video {video_dur:.1f}s exceeds {MAX_DURATION}s limit.")
    return narration, hold_times, video_dur


def generate_upbeat_music(duration: float) -> Path:
    music = OUT_DIR / "background_music.wav"
    sample_rate = 44100
    n_samples = int(duration * sample_rate)
    bpm = 118
    beat_samples = int(sample_rate * 60 / bpm)
    chord_freqs = [
        (261.63, 329.63, 392.00),
        (293.66, 369.99, 440.00),
        (329.63, 415.30, 493.88),
        (349.23, 440.00, 523.25),
    ]
    samples: list[float] = []
    for i in range(n_samples):
        bar = (i // (beat_samples * 4)) % len(chord_freqs)
        beat = (i // beat_samples) % 4
        freqs = chord_freqs[bar]
        tone = sum(math.sin(2 * math.pi * f * i / sample_rate) * 0.055 for f in freqs)
        bar_pos = (i % (beat_samples * 4)) / (beat_samples * 4)
        tone *= max(0.18, 1.0 - bar_pos * 0.82)
        if beat in (0, 2) and (i % beat_samples) < int(sample_rate * 0.04):
            kick_t = (i % beat_samples) / sample_rate
            tone += math.sin(2 * math.pi * 80 * kick_t) * (1.0 - kick_t / 0.04) * 0.14
        if beat in (1, 3) and (i % beat_samples) < int(sample_rate * 0.01):
            tone += (hash(i) % 1000 / 1000 - 0.5) * 0.03
        samples.append(max(-1.0, min(1.0, tone)))
    with wave.open(str(music), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(
            b"".join(struct.pack("<h", int(s * 32767 * 0.58)) for s in samples)
        )
    return music


def click_plan(page, rx: float, ry: float) -> None:
    box = page.locator("#plan").bounding_box()
    if not box:
        raise RuntimeError("2D plan canvas not visible")
    page.mouse.click(box["x"] + box["width"] * rx, box["y"] + box["height"] * ry)


def orbit_3d(page, steps: int = 10) -> None:
    box = page.locator("#view3d").bounding_box()
    if not box:
        return
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    for i in range(steps):
        page.mouse.move(cx + 35 * (i + 1), cy - 8 * (i + 1))
        page.wait_for_timeout(90)
    page.mouse.up()


def wait_for_bim_model(page, timeout_ms: int = 120_000) -> None:
    page.wait_for_function(
        """() => {
            const overlay = document.getElementById('bimLoadingOverlay');
            if (overlay && overlay.classList.contains('active')) return false;
            const tag = document.getElementById('modelPhaseTag');
            const text = ((tag && tag.textContent) || '').trim();
            if (/loading|preparing|requesting|rendering/i.test(text)) return false;
            return text.includes('BIM Model Loaded') || text.includes('stage IFC')
                || text.includes('estimated split');
        }""",
        timeout=timeout_ms,
    )
    page.wait_for_timeout(1500)


def wait_for_template_loaded(page) -> None:
    page.wait_for_function(
        "() => !!document.querySelector('#presetSel option[value=\"BT-01\"]')",
        timeout=20000,
    )
    page.select_option("#presetSel", "BT-01")
    page.wait_for_function(
        "() => (document.getElementById('status').textContent || '').includes('Template loaded')",
        timeout=20000,
    )
    page.fill("#name", DESIGN_NAME)
    page.wait_for_timeout(800)


def wait_for_3d_ready(page) -> None:
    page.click("#tab3d")
    page.wait_for_function(
        """() => {
            const hud = document.getElementById('hud');
            const t = (hud && hud.textContent) || '';
            return document.getElementById('view3d').style.display !== 'none'
                && t.includes('elements');
        }""",
        timeout=30000,
    )
    page.wait_for_timeout(1200)


def screenshot_to_clip(png: Path, mp4: Path, duration_sec: float) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(png),
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0E0E10",
            "-t",
            f"{duration_sec:.3f}",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(mp4),
        ]
    )


def prepare_platform_page(page, url: str, mode: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if mode == "login":
        page.wait_for_selector("#loginForm", timeout=15000)
        page.fill("#email", "instructor@btvi.edu.bs")
        page.fill("#password", "demo2026")
    elif mode == "bim":
        wait_for_bim_model(page)
    elif mode == "design":
        page.wait_for_selector("#presetSel", timeout=20000)
        page.wait_for_timeout(800)
    elif mode == "wizard":
        page.wait_for_selector("#projectListView, #wizardView", timeout=20000)
        page.wait_for_timeout(800)
    elif mode == "scroll":
        page.wait_for_timeout(700)
        page.evaluate(
            "() => window.scrollTo({ top: Math.min(500, document.body.scrollHeight * 0.3), behavior: 'instant' })"
        )
    else:
        page.wait_for_timeout(900)


def build_platform_montage(browser, hold_sec: float, clips_dir: Path) -> Path:
    """Capture a screenshot of every platform module and stitch into one montage."""
    parts_dir = clips_dir / "platform_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    seg_dur = hold_sec / len(PLATFORM_PAGES)
    segments: list[Path] = []

    print(f"      Capturing {len(PLATFORM_PAGES)} platform modules ({seg_dur:.1f}s each)…")
    for name, url, mode in PLATFORM_PAGES:
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            color_scheme="dark",
        )
        page = ctx.new_page()
        page.set_default_timeout(120_000)
        prepare_platform_page(page, url, mode)
        page.wait_for_timeout(400)
        png = parts_dir / f"{name}.png"
        page.screenshot(path=str(png), full_page=False)
        page.close()
        ctx.close()

        seg = parts_dir / f"{name}.mp4"
        screenshot_to_clip(png, seg, seg_dur)
        segments.append(seg)
        print(f"        done: {name}")

    out = clips_dir / "platform.mp4"
    concat_list = parts_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{s.as_posix()}'" for s in segments) + "\n",
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
            str(out),
        ]
    )
    return out


def load_family_home(page) -> None:
    page.goto(DESIGN_STUDIO_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("#projectSel", timeout=20000)
    page.wait_for_timeout(1000)
    wait_for_template_loaded(page)


def prepare_scene(page, scene: Scene) -> None:
    if scene.action == "intro":
        page.goto(DESIGN_STUDIO_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#projectSel", timeout=20000)
        page.wait_for_timeout(1200)
    elif scene.action == "template":
        load_family_home(page)
    elif scene.action == "openings":
        load_family_home(page)
        page.click('[data-tool="door"]')
        click_plan(page, 0.50, 0.74)
        page.wait_for_timeout(350)
        page.click('[data-tool="window"]')
        click_plan(page, 0.32, 0.26)
        page.wait_for_timeout(300)
        click_plan(page, 0.68, 0.26)
        page.wait_for_timeout(500)
        page.click('[data-tool="select"]')
    elif scene.action == "view_3d":
        load_family_home(page)
        page.click('[data-tool="door"]')
        click_plan(page, 0.50, 0.74)
        wait_for_3d_ready(page)
        orbit_3d(page)
    elif scene.action == "building":
        load_family_home(page)
        page.select_option("#roofType", "gable")
        page.fill("#ridge", "2.5")
        page.fill("#foundDepth", "0.8")
        page.wait_for_timeout(400)
        wait_for_3d_ready(page)
        orbit_3d(page, steps=6)
    elif scene.action == "save":
        load_family_home(page)
        page.click('[data-tool="door"]')
        click_plan(page, 0.50, 0.74)
        page.click("#saveBtn")
        page.wait_for_function(
            "() => document.getElementById('status').classList.contains('ok')",
            timeout=30000,
        )
        page.wait_for_timeout(600)
    elif scene.action == "platform_montage":
        pass  # handled by build_platform_montage()


def trim_clip_tail(src: Path, dest: Path, duration_sec: float) -> None:
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


def make_end_card_clip(dest: Path, duration_sec: float) -> None:
    if not END_SCREEN_IMAGE.exists():
        raise RuntimeError(f"End screen image not found: {END_SCREEN_IMAGE}")
    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(END_SCREEN_IMAGE),
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0E0E10",
            "-t",
            f"{duration_sec:.3f}",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(dest),
        ]
    )


def record_scene_clip(browser, scene: Scene, hold_sec: float, clips_dir: Path) -> Path:
    trimmed = clips_dir / f"{scene.name}.mp4"
    if scene.action == "end_card":
        make_end_card_clip(trimmed, hold_sec)
        return trimmed
    if scene.action == "platform_montage":
        return build_platform_montage(browser, hold_sec, clips_dir)

    raw_dir = clips_dir / "raw" / scene.name
    raw_dir.mkdir(parents=True, exist_ok=True)
    ctx = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        record_video_dir=str(raw_dir),
        record_video_size={"width": 1920, "height": 1080},
        color_scheme="dark",
    )
    page = ctx.new_page()
    page.set_default_timeout(120_000)
    prepare_scene(page, scene)
    page.wait_for_timeout(int(hold_sec * 1000))
    page.close()
    ctx.close()
    trim_clip_tail(Path(page.video.path()), trimmed, hold_sec)
    return trimmed


def record_screen(hold_times: list[float]) -> Path:
    from playwright.sync_api import sync_playwright

    clips_dir = OUT_DIR / "clips"
    if clips_dir.exists():
        shutil.rmtree(clips_dir)
    clips_dir.mkdir(parents=True)

    print("  Recording Design Studio walkthrough…")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--use-gl=angle", "--enable-webgl", "--ignore-gpu-blocklist"],
        )

        warm = browser.new_context(viewport={"width": 1920, "height": 1080})
        load_family_home(warm.new_page())
        warm.close()
        wait_page = browser.new_context(viewport={"width": 1920, "height": 1080}).new_page()
        wait_page.goto(DASHBOARD_URL, wait_until="domcontentloaded")
        wait_for_bim_model(wait_page)
        wait_page.context.close()

        scene_clips = []
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


def assemble_final(
    combined_video: Path,
    narration: Path,
    music: Path,
    video_duration: float,
    narr_duration: float,
) -> Path:
    mixed_audio = OUT_DIR / "mixed_audio.wav"
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(narration),
            "-i",
            str(music),
            "-filter_complex",
            f"[0:a]volume=1.0,afade=t=in:st=0:d=0.4,"
            f"afade=t=out:st={max(0, narr_duration - 0.8):.3f}:d=0.8[narr];"
            f"[1:a]volume={MUSIC_VOLUME},afade=t=in:st=0:d=0.8,"
            f"afade=t=out:st={max(0, video_duration - 1.5):.3f}:d=1.5[mus];"
            "[narr][mus]amix=inputs=2:duration=longest:dropout_transition=0[aout]",
            "-map",
            "[aout]",
            "-t",
            f"{video_duration:.3f}",
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

    for path in ("/api/health", "/design-studio"):
        try:
            with urllib.request.urlopen(f"{BASE_URL.rstrip('/')}{path}", timeout=8) as resp:
                if resp.status == 200:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise RuntimeError(f"HTTP {exc.code} at {path}") from exc
        except Exception as exc:
            raise RuntimeError(
                f"App not reachable at {BASE_URL}. Start Flask first.\n  ({exc})"
            ) from exc
    raise RuntimeError(f"Design Studio not reachable at {BASE_URL}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Veritas Design Studio — family home demo generator")
    print(f"  Output: {FINAL_VIDEO}")

    verify_health()

    print("\n1/4 Generating British voiceover (en-GB-SoniaNeural)…")
    voice_files = asyncio.run(generate_voice_segments())

    print("\n2/4 Building narration track…")
    narration, hold_times, video_duration = build_narration_track(voice_files)
    narr_duration = ffprobe_duration(narration)

    print("\n3/4 Recording screen capture…")
    combined = record_screen(hold_times)

    print("\n4/4 Generating music and assembling final video…")
    music = generate_upbeat_music(video_duration + 0.5)
    final = assemble_final(combined, narration, music, video_duration, narr_duration)

    print(f"\nDone!")
    print(f"  File:   {final}")
    print(f"  Length: {ffprobe_duration(final):.1f}s (limit {MAX_DURATION}s)")
    print(f"  Size:   {final.stat().st_size / (1024 * 1024):.1f} MB")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
