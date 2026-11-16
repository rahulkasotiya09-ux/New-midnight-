
import os
import re
import json
import time
import shutil
import random
import base64
import asyncio
import subprocess
from pathlib import Path

import requests
import edge_tts
from gradio_client import Client, handle_file
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ============================================================
# MIDNIGHT FILES — AUTOMATED HORROR SHORTS
# ============================================================

BASE = Path("midnight_cache")
IMAGE_DIR = BASE / "images"
VIDEO_DIR = BASE / "videos"
MEDIA_DIR = Path("output/media")
OUTPUT_DIR = Path("output")

STORY_FILE = BASE / "story.json"
UPLOADED_FILE = BASE / "uploaded.json"
FINAL_VIDEO = OUTPUT_DIR / "midnight_files.mp4"

SCENE_COUNT = 6
VIDEO_FPS = 16

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
WAN_SPACE = "zerogpu-aoti/wan2-2-fp8da-aoti-faster"

VOICE = "en-US-ChristopherNeural"
VOICE_RATE = "-8%"
VOICE_PITCH = "-25Hz"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MidnightFiles/1.0"})


# ============================================================
# DIRECTORY SETUP
# ============================================================

def setup_directories():
    for folder in [
        BASE,
        IMAGE_DIR,
        VIDEO_DIR,
        MEDIA_DIR,
        OUTPUT_DIR
    ]:
        folder.mkdir(parents=True, exist_ok=True)


def run_command(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(map(str, command))
            + "\n"
            + result.stderr[-5000:]
        )

    return result


def get_duration(path):
    result = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ])

    return float(result.stdout.strip())


# ============================================================
# HTTP RETRIES
# ============================================================

def request_retry(method, url, **kwargs):
    last_error = None

    for attempt in range(5):
        try:
            response = SESSION.request(
                method,
                url,
                timeout=180,
                **kwargs
            )

            if response.status_code == 429:
                body = response.text.lower()

                if (
                    "per day" in body
                    or "daily" in body
                    or "quota" in body
                ):
                    raise RuntimeError("GEMINI_DAILY_QUOTA")

            if response.status_code in [500, 502, 503, 504]:
                last_error = (
                    f"Temporary server error "
                    f"{response.status_code}: "
                    f"{response.text[:1500]}"
                )

                print(last_error)
                time.sleep(min(10 * (attempt + 1), 40))
                continue

            response.raise_for_status()
            return response

        except RuntimeError:
            raise

        except Exception as error:
            last_error = str(error)
            print(f"Request attempt {attempt + 1}: {error}")
            time.sleep(min(8 * (attempt + 1), 30))

    raise RuntimeError(f"Request failed: {last_error}")


# ============================================================
# BUILT-IN STORY
# ============================================================

def fallback_story():
    return {
        "title": "The Room Behind the Wall",
        "description": (
            "A terrifying late-night discovery reveals "
            "a room that should never have existed."
        ),
        "scenes": [
            {
                "narration": (
                    "At three seventeen in the morning, "
                    "someone knocked from inside my bedroom wall."
                ),
                "visual": (
                    "A frightened young man alone in an old "
                    "apartment bedroom at night, staring toward "
                    "a cracked bedroom wall, cinematic "
                    "photorealistic horror film, cold blue lighting"
                ),
                "motion": (
                    "Slow cinematic camera push toward the man. "
                    "He slowly turns his head toward the wall. "
                    "A faint shadow moves behind him."
                )
            },
            {
                "narration": (
                    "I thought it was the neighbours, until "
                    "the knocking came from directly behind my bed."
                ),
                "visual": (
                    "Old apartment bedroom, wooden bed pushed "
                    "against a damaged plaster wall, frightened "
                    "man sitting on bed, realistic cinematic horror"
                ),
                "motion": (
                    "The camera moves slowly from the man's face "
                    "toward the wall. Three small vibrations shake "
                    "a picture frame."
                )
            },
            {
                "narration": (
                    "Then a voice whispered my name, "
                    "from the other side."
                ),
                "visual": (
                    "Close view of a cracked bedroom wall, "
                    "dark narrow crack in plaster, terrified "
                    "man listening, realistic horror cinematography"
                ),
                "motion": (
                    "The crack slowly spreads across the plaster. "
                    "The camera creeps closer as dust falls."
                )
            },
            {
                "narration": (
                    "I pulled the wardrobe away and found "
                    "a small wooden door."
                ),
                "visual": (
                    "A heavy wardrobe moved aside revealing "
                    "a tiny old wooden door hidden inside the "
                    "bedroom wall, dark apartment, cinematic horror"
                ),
                "motion": (
                    "The wardrobe slowly scrapes across the floor. "
                    "The camera reveals the hidden door behind it."
                )
            },
            {
                "narration": (
                    "Behind it was another bedroom, "
                    "and someone was sleeping in my bed."
                ),
                "visual": (
                    "Impossible second bedroom behind a hidden "
                    "door, identical bed, shadowy human figure "
                    "lying beneath a blanket, unsettling "
                    "photorealistic horror film"
                ),
                "motion": (
                    "The camera slowly moves through the doorway. "
                    "The covered figure begins to sit up."
                )
            },
            {
                "narration": (
                    "Then I heard my own voice behind me say, "
                    "you were not supposed to open that door."
                ),
                "visual": (
                    "Terrifying dark bedroom doorway, a pale "
                    "shadowy double of the man standing behind "
                    "him, dramatic low-key lighting, cinematic horror"
                ),
                "motion": (
                    "The camera slowly turns toward the second "
                    "figure. The figure takes one slow step forward "
                    "as the lights flicker."
                )
            }
        ]
    }


# ============================================================
# STORY GENERATION
# ============================================================

def clean_json_text(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text)
    text = re.sub(r"```$", "", text)
    return text.strip()


def validate_story(story):
    if not isinstance(story, dict):
        raise ValueError("Story is not a JSON object")

    scenes = story.get("scenes")

    if not isinstance(scenes, list):
        raise ValueError("Story has no scenes list")

    if len(scenes) != SCENE_COUNT:
        raise ValueError("Story must have exactly six scenes")

    for scene in scenes:
        for key in ["narration", "visual", "motion"]:
            if not scene.get(key):
                raise ValueError(f"Scene is missing {key}")

    return story


def generate_story():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        print("Gemini key missing. Using built-in story.")
        return fallback_story()

    prompt = """
Create an original, extremely engaging horror short film.

Return ONLY valid JSON in this structure:

{
  "title": "short title",
  "description": "short description",
  "scenes": [
    {
      "narration": "voiceover text",
      "visual": "detailed photorealistic image prompt",
      "motion": "detailed realistic video movement prompt"
    }
  ]
}

Rules:
- Exactly six scenes.
- Approximately 100 to 130 narration words total.
- Start with a powerful hook.
- Chronological story with clear continuity.
- Same main character, face, clothing and locations.
- Every scene must logically follow the previous scene.
- Each visual prompt describes one clear cinematic shot.
- Each motion prompt describes physical and camera movement.
- Disturbing, memorable ending.
- Realistic live-action horror film style.
- No cartoons, illustrations or anime.
- No markdown outside the JSON.
"""

    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models/"
        + GEMINI_MODEL
        + ":generateContent"
    )

    try:
        response = request_retry(
            "POST",
            url,
            params={"key": api_key},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.9,
                    "responseMimeType": "application/json"
                }
            }
        )

        data = response.json()

        generated = (
            data["candidates"][0]["content"]["parts"][0]["text"]
        )

        story = json.loads(clean_json_text(generated))
        return validate_story(story)

    except Exception as error:
        print("Gemini unavailable or response invalid.")
        print(f"Reason: {error}")
        print("Using built-in horror story.")
        return fallback_story()


def save_story(story):
    STORY_FILE.write_text(
        json.dumps(story, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def load_story():
    if not STORY_FILE.exists():
        return None

    try:
        story = json.loads(
            STORY_FILE.read_text(encoding="utf-8")
        )
        return validate_story(story)

    except Exception as error:
        print(f"Saved story could not be loaded: {error}")
        return None


# ============================================================
# CLOUDFLARE FLUX IMAGE GENERATION
# ============================================================

def generate_image(prompt, output_path):
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    token = os.getenv("CLOUDFLARE_API_TOKEN")

    if not account_id or not token:
        raise RuntimeError("Cloudflare credentials are missing")

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/"
        "@cf/black-forest-labs/flux-1-schnell"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    full_prompt = (
        prompt
        + ". Photorealistic live-action horror movie scene, "
        "cinematic lighting, realistic human proportions, "
        "detailed environment, dramatic composition, "
        "no text, no watermark."
    )

    response = requests.post(
        url,
        headers=headers,
        json={
            "prompt": full_prompt[:2048],
            "steps": 4
        },
        timeout=180
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Cloudflare image error {response.status_code}: "
            f"{response.text[:3000]}"
        )

    try:
        result = response.json()

        if not result.get("success", True):
            raise RuntimeError(
                f"Cloudflare returned errors: "
                f"{result.get('errors', result)}"
            )

        image_data = result.get("result", {}).get("image")

        if not image_data:
            raise RuntimeError(
                "No image returned: " + str(result)[:2000]
            )

        image_bytes = base64.b64decode(image_data)

        if len(image_bytes) < 1000:
            raise RuntimeError("Generated image is too small")

        output_path.write_bytes(image_bytes)

        print(f"Image successfully saved: {output_path}")

    except Exception as error:
        raise RuntimeError(
            f"Could not process Cloudflare image: {error}"
        )


# ============================================================
# WAN VIDEO GENERATION — AUTHENTICATION FIXED
# ============================================================

def is_zero_gpu_quota_error(error):
    message = str(error).lower()

    indicators = [
        "runs limit",
        "subscribe to hugging face pro",
        "zerogpu",
        "exceeded your",
        "quota"
    ]

    return any(item in message for item in indicators)


def find_video_path(result):
    candidates = []

    def inspect(value):
        if isinstance(value, str):
            if value.lower().endswith(
                (".mp4", ".webm", ".mov")
            ):
                candidates.append(value)

        elif isinstance(value, dict):
            for item in value.values():
                inspect(item)

        elif isinstance(value, (list, tuple)):
            for item in value:
                inspect(item)

        elif hasattr(value, "path"):
            inspect(value.path)

    inspect(result)

    for candidate in candidates:
        path = Path(candidate)

        if path.exists() and path.stat().st_size > 1000:
            return path

    raise RuntimeError(
        "Wan returned no usable video file. "
        f"Result: {str(result)[:1500]}"
    )


def generate_wan_clip(image_path, motion_prompt, output_path):
    token = os.getenv("HF_TOKEN")

    if not token:
        raise RuntimeError("HF_TOKEN is missing")

    print(f"Connecting to Wan for {image_path.name}")

    try:
        # Correct authentication argument: token
        client = Client(
            WAN_SPACE,
            token=token,
            verbose=True
        )

        result = client.predict(
            handle_file(str(image_path)),
            motion_prompt,
            20,
            (
                "blurry, distorted, deformed, low quality, "
                "extra limbs, duplicate people, cartoon, "
                "illustration, text, watermark"
            ),
            3,
            5,
            5,
            random.randint(1, 999999999),
            True,
            api_name="/generate_video"
        )

        source_path = find_video_path(result)

        shutil.copy2(source_path, output_path)

        if (
            not output_path.exists()
            or output_path.stat().st_size < 1000
        ):
            raise RuntimeError("Wan video was not saved correctly")

        print(f"Real Wan video saved: {output_path}")
        return True

    except Exception as error:
        print(f"Wan generation failed: {error}")

        if is_zero_gpu_quota_error(error):
            raise RuntimeError(
                "WAN_ZERO_GPU_QUOTA: " + str(error)
            )

        raise RuntimeError(
            f"WAN_GENERATION_FAILED: {error}"
        )


# ============================================================
# VOICE GENERATION
# ============================================================

async def create_tts(text, output_path):
    communicate = edge_tts.Communicate(
        text=text,
        voice=VOICE,
        rate=VOICE_RATE,
        pitch=VOICE_PITCH
    )

    await communicate.save(str(output_path))


def generate_voice(text, output_path):
    for attempt in range(3):
        try:
            asyncio.run(create_tts(text, output_path))

            if (
                output_path.exists()
                and output_path.stat().st_size > 1000
            ):
                print(f"Voice saved: {output_path}")
                return

            raise RuntimeError("Voice file is empty")

        except Exception as error:
            print(
                f"Voice attempt {attempt + 1} failed: {error}"
            )
            time.sleep(3)

    raise RuntimeError("Could not generate narration")


# ============================================================
# SUBTITLE GENERATION
# ============================================================

def timestamp_ass(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{secs:05.2f}"
    )


def escape_ass(text):
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", " ")
    )


def write_ass_subtitles(narration, duration, output_path):
    words = narration.split()

    if not words:
        raise RuntimeError("Narration has no words")

    groups = []

    for index in range(0, len(words), 4):
        groups.append(words[index:index + 4])

    total_words = len(words)
    current_time = 0.0

    header = """[Script Info]
Title: Midnight Files
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,3,1,2,55,55,190,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    for group in groups:
        count = len(group)
        group_duration = duration * count / total_words

        start = current_time
        end = min(duration, current_time + group_duration)

        text = escape_ass(" ".join(group))

        events.append(
            "Dialogue: 0,"
            + timestamp_ass(start)
            + ","
            + timestamp_ass(end)
            + ",Default,,0,0,0,,"
            + text
        )

        current_time = end

    output_path.write_text(
        header + "\n".join(events),
        encoding="utf-8"
    )


# ============================================================
# RENDER SCENE
# ============================================================

def render_scene(
    index,
    clip_path,
    audio_path,
    subtitle_path,
    output_path
):
    audio_duration = get_duration(audio_path)
    video_duration = get_duration(clip_path)

    if video_duration <= 0 or audio_duration <= 0:
        raise RuntimeError(
            f"Scene {index} has invalid duration"
        )

    speed_factor = video_duration / audio_duration

    subtitle_name = (
        str(subtitle_path.resolve())
        .replace("\\", "/")
        .replace(":", r"\:")
    )

    video_filter = (
        "scale=720:1280:force_original_aspect_ratio=increase,"
        "crop=720:1280,"
        "setsar=1,"
        f"setpts=PTS/{speed_factor},"
        f"subtitles='{subtitle_name}'"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-i", str(clip_path),
        "-i", str(audio_path),
        "-vf", video_filter,
        "-af", "aresample=async=1",
        "-t", str(audio_duration),
        "-r", str(VIDEO_FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        str(output_path)
    ])

    print(f"Rendered scene {index}")


# ============================================================
# JOIN ALL SCENES
# ============================================================

def join_scenes(scene_files):
    concat_file = OUTPUT_DIR / "concat.txt"

    lines = []

    for scene in scene_files:
        escaped = (
            str(scene.resolve())
            .replace("'", "'\\''")
        )

        lines.append(f"file '{escaped}'")

    concat_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    run_command([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "24",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(FINAL_VIDEO)
    ])

    if (
        not FINAL_VIDEO.exists()
        or FINAL_VIDEO.stat().st_size < 10000
    ):
        raise RuntimeError("Final video was not created")

    print(f"Final video ready: {FINAL_VIDEO}")


# ============================================================
# BUILD VIDEO — RESUME SAVED PROGRESS
# ============================================================

def build_video(story):
    scene_files = []

    for index, scene in enumerate(
        story["scenes"],
        start=1
    ):
        image_path = IMAGE_DIR / f"scene_{index}.jpg"
        clip_path = VIDEO_DIR / f"wan_scene_{index}.mp4"

        audio_path = MEDIA_DIR / f"voice_{index}.mp3"
        subtitle_path = MEDIA_DIR / f"scene_{index}.ass"
        rendered_path = MEDIA_DIR / f"rendered_{index}.mp4"

        print(
            f"\n========== SCENE {index}/{SCENE_COUNT} =========="
        )

        # Image generation or reuse.
        if not image_path.exists():
            print(f"Generating image for scene {index}")

            generate_image(
                scene["visual"],
                image_path
            )
        else:
            print(f"Using saved image: {image_path}")

        # Real video generation or reuse.
        if not clip_path.exists():
            print(f"Generating real Wan video for scene {index}")

            try:
                generate_wan_clip(
                    image_path,
                    scene["motion"],
                    clip_path
                )

            except RuntimeError as error:
                if is_zero_gpu_quota_error(error):
                    print("Hugging Face free video quota reached.")
                    print("Progress will be saved for the next run.")
                    return None

                raise

        else:
            print(f"Using saved video: {clip_path}")

        # Voice generation or reuse.
        if not audio_path.exists():
            generate_voice(
                scene["narration"],
                audio_path
            )

        # Subtitle generation or reuse.
        if not subtitle_path.exists():
            duration = get_duration(audio_path)

            write_ass_subtitles(
                scene["narration"],
                duration,
                subtitle_path
            )

        # Scene rendering.
        if not rendered_path.exists():
            render_scene(
                index,
                clip_path,
                audio_path,
                subtitle_path,
                rendered_path
            )

        scene_files.append(rendered_path)

    if len(scene_files) != SCENE_COUNT:
        print("Not all six scenes are ready. Upload cancelled.")
        return None

    join_scenes(scene_files)
    return FINAL_VIDEO


# ============================================================
# YOUTUBE AUTHENTICATION
# ============================================================

def youtube_service():
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")

    if not all([
        refresh_token,
        client_id,
        client_secret
    ]):
        raise RuntimeError("YouTube OAuth secrets are missing")

    credentials = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload"
        ]
    )

    return build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


# ============================================================
# YOUTUBE UPLOAD
# ============================================================

def upload_to_youtube(video_path, story):
    youtube = youtube_service()

    title = story.get(
        "title",
        "Midnight Files"
    )[:95]

    description = (
        story.get(
            "description",
            "A horror story from Midnight Files."
        )
        + "\n\n"
        + "#horror #scarystories #shorts #midnightfiles"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [
                "horror",
                "scary story",
                "horror shorts",
                "midnight files",
                "shorts"
            ],
            "categoryId": "24"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None

    while response is None:
        status, response = request.next_chunk()

        if status:
            print(
                "YouTube upload: "
                f"{int(status.progress() * 100)}%"
            )

    video_id = response["id"]

    # Save marker immediately after confirmed upload.
    UPLOADED_FILE.write_text(
        json.dumps(
            {
                "video_id": video_id,
                "title": title,
                "uploaded_at": time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC",
                    time.gmtime()
                )
            },
            indent=2
        ),
        encoding="utf-8"
    )

    print("YouTube upload successful.")
    print(f"Video ID: {video_id}")
    print(
        f"https://www.youtube.com/shorts/{video_id}"
    )

    return video_id


# ============================================================
# CLEAR PREVIOUS COMPLETED CYCLE
# ============================================================

def clear_previous_cycle():
    print("Previous upload found. Starting a new story cycle.")

    for folder in [
        IMAGE_DIR,
        VIDEO_DIR,
        MEDIA_DIR
    ]:
        if folder.exists():
            shutil.rmtree(folder)

        folder.mkdir(parents=True, exist_ok=True)

    for file_path in [
        STORY_FILE,
        UPLOADED_FILE,
        FINAL_VIDEO
    ]:
        if file_path.exists():
            file_path.unlink()


# ============================================================
# MAIN
# ============================================================

def main():
    setup_directories()

    print("\n========================================")
    print("       MIDNIGHT FILES AUTOMATION")
    print("========================================\n")

    required = [
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "HF_TOKEN",
        "YOUTUBE_REFRESH_TOKEN",
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET"
    ]

    missing = [
        name
        for name in required
        if not os.getenv(name)
    ]

    if missing:
        raise RuntimeError(
            "Missing GitHub Actions secrets: "
            + ", ".join(missing)
        )

    # Start new cycle only after confirmed previous upload.
    if UPLOADED_FILE.exists():
        clear_previous_cycle()

    # Resume existing story after interrupted run.
    story = load_story()

    if story:
        print("Resuming saved story.")
    else:
        print("Creating a new horror story.")
        story = generate_story()
        save_story(story)

    print(f"\nStory: {story['title']}")

    video_path = build_video(story)

    # If Wan quota runs out, do not upload.
    if video_path is None:
        print("Video generation paused.")
        print("No upload attempted.")
        return

    # Verify all six genuine video clips.
    for index in range(1, SCENE_COUNT + 1):
        clip = VIDEO_DIR / f"wan_scene_{index}.mp4"

        if (
            not clip.exists()
            or clip.stat().st_size < 1000
        ):
            raise RuntimeError(
                f"Scene {index} has no valid real video. "
                "Upload cancelled."
            )

    upload_to_youtube(video_path, story)

    print("\nMidnight Files cycle completed successfully.")


if __name__ == "__main__":
    main()
