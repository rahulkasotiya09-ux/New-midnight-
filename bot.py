
import os
import re
import json
import time
import base64
import asyncio
import shutil
import subprocess
from pathlib import Path

import requests
import edge_tts
from gradio_client import Client, handle_file
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ============================================================
# MIDNIGHT FILES - RESUMABLE WAN VIDEO GENERATOR
# Free-first architecture. No still-image video fallback.
# ============================================================

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "output"
MEDIA = OUTPUT / "media"

CACHE = BASE / "midnight_cache"
IMAGES = CACHE / "images"
VIDEOS = CACHE / "videos"
STORY_FILE = CACHE / "story.json"
UPLOAD_FILE = CACHE / "uploaded.json"

for folder in (OUTPUT, MEDIA, IMAGES, VIDEOS):
    folder.mkdir(parents=True, exist_ok=True)

GEMINI_MODEL = "gemini-3.8-flash"
CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"
WAN_SPACE = "zerogpu-aoti/wan2-2-fp8da-aoti-faster"

# Short clips help conserve free ZeroGPU quota.
WAN_DURATION = 2.0
WAN_STEPS = 4

VOICE = "en-US-ChristopherNeural"
VOICE_RATE = "-8%"
VOICE_PITCH = "-25Hz"

TRANSITION = 0.35


# ============================================================
# GENERAL HELPERS
# ============================================================

def run_command(args):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n" + result.stderr[-4000:]
        )

    return result.stdout.strip()


def get_duration(path):
    value = run_command([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ])
    return float(value)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def request_retry(method, url, **kwargs):
    last_error = None

    for attempt in range(5):
        try:
            response = requests.request(
                method,
                url,
                timeout=(30, 240),
                **kwargs
            )

            if response.status_code == 429:
                body = response.text[:1500]

                if (
                    "generate_content_free_tier_requests" in body
                    or "RequestsPerDay" in body
                ):
                    raise RuntimeError(
                        "GEMINI_DAILY_QUOTA " + body
                    )

                last_error = body

            elif response.status_code in (500, 502, 503, 504):
                last_error = response.text[:1500]

            elif not response.ok:
                raise RuntimeError(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:2000]}"
                )

            else:
                return response

        except requests.RequestException as error:
            last_error = str(error)

        if attempt < 4:
            time.sleep(8 * (attempt + 1))

    raise RuntimeError(f"Request failed: {last_error}")


# ============================================================
# BUILT-IN STORY - WORKS WITHOUT GEMINI
# ============================================================

def fallback_story():

    narration = [
        "At 2:13 a.m., Mara heard three slow knocks coming from inside her bedroom wall. Her apartment was silent, and that wall faced the outside.",

        "She pressed her ear against the cold plaster. A little girl's voice whispered, please don't let him hear you. Mara froze, barely breathing.",

        "A thin crack appeared in the wallpaper, slowly tracing a narrow door. Mara stepped backward, watching the impossible outline grow across the wall.",

        "From behind it came Mara's own voice, calmly asking, why are you standing outside? She had not spoken a word all night.",

        "The handle began turning. Mara ran into the hallway, but every door opened onto that same dark bedroom. Her apartment had become a loop.",

        "Her phone lit up. Her mother texted, don't open the wall. We sealed it after you disappeared. Three knocks sounded again, inside her phone."
    ]

    visuals = [
        "Mara, a 27-year-old woman with shoulder-length dark hair and a grey sweater, sits upright in a dark bedroom staring at a blank wall. A digital clock reads 2:13 AM.",

        "Mara presses her ear against the plaster wall, her frightened eyes widening in cold blue bedroom light.",

        "A crack spreads through the wallpaper, forming the outline of a narrow door. Mara steps backward in fear.",

        "A strange door handle appears in the wall. Mara stares at it while her reflection looks subtly different in a nearby mirror.",

        "Mara runs through a dark apartment hallway and opens a door, only to find the same bedroom again.",

        "Close-up of a phone displaying the mother's warning message. Mara freezes as the bedroom wall looms behind her."
    ]

    motions = [
        "Mara slowly turns toward the wall. Dust trembles with each knock as the camera pushes closer.",

        "Her fingers press against the plaster. Her eyes widen and her breathing becomes shallow.",

        "The crack crawls downward. Mara takes two cautious steps backward as the camera tracks her.",

        "The handle moves slightly. Mara's reflection appears to move a fraction later than she does.",

        "A handheld camera follows Mara running. She pulls open the door and stops in shock.",

        "The phone vibrates in Mara's shaking hand. The camera slowly pulls back as she turns toward the wall."
    ]

    scenes = []

    for i in range(6):
        scenes.append({
            "narration": narration[i],
            "visual_description": visuals[i],
            "motion_description": motions[i],
            "mood": "Psychological horror"
        })

    return {
        "title": "The Room Behind the Wall",
        "description": (
            "A woman hears a voice coming from a room that should not exist. "
            "An original Midnight Files horror story."
        ),
        "tags": ["horror", "scary story", "ghost story", "shorts"],
        "character_description": (
            "Mara, 27, shoulder-length dark brown hair, pale tired face, "
            "faded grey sweater and black trousers. Keep her appearance "
            "identical in every scene."
        ),
        "visual_style": (
            "Photorealistic live-action psychological horror, cinematic "
            "35mm film look, cold blue moonlight, charcoal shadows, "
            "subtle film grain, consistent apartment."
        ),
        "scenes": scenes,
        "narration": " ".join(narration)
    }


# ============================================================
# GEMINI STORY GENERATION
# ============================================================

def generate_story():

    key = os.getenv("GEMINI_API_KEY", "").strip()

    if not key:
        print("Gemini key missing. Using built-in story.")
        return fallback_story()

    prompt = """
Create one original cinematic horror short film.

Return ONLY valid JSON.

Requirements:
- English narration, 115-145 words total.
- Exactly six chronological scenes.
- Strong opening hook.
- Rising suspense and surprising ending.
- Advertiser-friendly. No gore.
- Maintain the same character, face, clothing and location.
- Scenes must follow the narration in chronological order.

Each scene must contain:
narration
visual_description
motion_description
mood

Also include:
title
description
tags
character_description
visual_style

No Markdown.
"""

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    try:
        response = request_retry(
            "POST",
            url,
            params={"key": key},
            json={
                "contents": [
                    {"parts": [{"text": prompt}]}
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.8,
                    "maxOutputTokens": 8192
                }
            }
        )

    except RuntimeError as error:

        if "GEMINI_DAILY_QUOTA" in str(error):
            print("Gemini daily quota reached.")
            print("Using built-in story instead.")
            return fallback_story()

        raise

    data = response.json()
    candidates = data.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            "Gemini returned no candidates: " + str(data)[:1500]
        )

    raw = "".join(
        part.get("text", "")
        for part in candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)

    story = json.loads(raw)

    if len(story.get("scenes", [])) != 6:
        raise RuntimeError(
            "Gemini must return exactly six scenes."
        )

    for scene in story["scenes"]:

        for field in (
            "narration",
            "visual_description",
            "motion_description"
        ):
            scene[field] = clean_text(scene.get(field))

        if not scene["narration"] or not scene["visual_description"]:
            raise RuntimeError("A scene is missing required content.")

    story["title"] = (
        clean_text(story.get("title")) or "Midnight Files"
    )

    story["description"] = clean_text(
        story.get("description")
    )

    story["character_description"] = clean_text(
        story.get("character_description")
    )

    story["visual_style"] = clean_text(
        story.get("visual_style")
    )

    story["tags"] = story.get(
        "tags",
        ["horror", "scary story", "shorts"]
    )

    story["narration"] = " ".join(
        scene["narration"] for scene in story["scenes"]
    )

    if not 100 <= len(story["narration"].split()) <= 160:
        raise RuntimeError(
            "Narration length must be between 100 and 160 words."
        )

    return story


# ============================================================
# CLOUDFLARE FLUX - SCENE STARTING IMAGES
# ============================================================

def generate_image(story, scene, index):

    prompt = f"""
Photorealistic live-action cinematic horror frame.

CHARACTER:
{story.get('character_description', '')}

VISUAL STYLE:
{story.get('visual_style', '')}

SCENE:
{scene['visual_description']}

NARRATION:
{scene['narration']}

Vertical 9:16 composition.
Realistic anatomy.
Coherent environment.
Cinematic low lighting.
No text, captions, logos, watermark, collage or cartoon.
"""

    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{os.environ['CLOUDFLARE_ACCOUNT_ID']}/ai/run/{CF_MODEL}"
    )

    response = request_retry(
        "POST",
        url,
        headers={
            "Authorization": (
                "Bearer " + os.environ["CLOUDFLARE_API_TOKEN"]
            ),
            "Content-Type": "application/json"
        },
        json={
            "prompt": clean_text(prompt)[:2000],
            "steps": 4
        }
    )

    data = response.json()

    if not data.get("success"):
        raise RuntimeError(
            "Cloudflare image generation failed: "
            + str(data)[:1500]
        )

    image_data = data.get("result", {}).get("image")

    if not image_data:
        raise RuntimeError(
            "Cloudflare returned no image data."
        )

    path = MEDIA / f"scene_{index + 1}.png"

    path.write_bytes(
        base64.b64decode(image_data)
    )

    if path.stat().st_size < 10000:
        raise RuntimeError("Generated image is unexpectedly small.")

    return path


# ============================================================
# WAN - REAL AI VIDEO CLIPS
# ============================================================

def generate_wan_clip(client, story, scene, image_path, index):

    prompt = f"""
Create one continuous photorealistic live-action horror movie shot.

CHARACTER:
{story.get('character_description', '')}

VISUAL STYLE:
{story.get('visual_style', '')}

SHOT:
{scene['visual_description']}

ACTION AND CAMERA MOVEMENT:
{scene.get('motion_description', '')}

NARRATIVE:
{scene['narration']}

Animate the actual physical action described.
Use natural body and facial movement.
Include believable environmental movement.
Keep character identity, clothing, location and lighting consistent.
One continuous take. No cuts, no text and no subtitles.
"""

    negative_prompt = (
        "still image, static frame, slideshow, frozen movement, "
        "flicker, jitter, distorted face, deformed body, extra limbs, "
        "changing identity, text, subtitles, watermark, cartoon, illustration"
    )

    try:

        result = client.predict(
            input_image=handle_file(str(image_path)),
            prompt=prompt,
            steps=WAN_STEPS,
            negative_prompt=negative_prompt,
            duration_seconds=WAN_DURATION,
            guidance_scale=1,
            guidance_scale_2=1,
            seed=42 + index,
            randomize_seed=False,
            api_name="/generate_video"
        )

    except Exception as error:

        raise RuntimeError(
            f"WAN_ERROR: {type(error).__name__}: {error}"
        ) from error

    output = result[0] if isinstance(result, tuple) else result

    if isinstance(output, dict):
        output = output.get("path") or output.get("video")

    if not output or not Path(str(output)).exists():
        raise RuntimeError("Wan returned no video file.")

    destination = MEDIA / f"wan_scene_{index + 1}.mp4"

    shutil.copy2(str(output), destination)

    if destination.stat().st_size < 10000:
        raise RuntimeError("Wan video file is unexpectedly small.")

    return destination


def is_zero_gpu_quota_error(error):

    message = str(error).lower()

    if "runs limit" in message:
        return True

    if "subscribe to hugging face pro" in message:
        return True

    if "zerogpu" in message and any(
        word in message
        for word in ("exceeded", "limit", "quota")
    ):
        return True

    if "zero gpu" in message and any(
        word in message
        for word in ("exceeded", "limit", "quota")
    ):
        return True

    return False


# ============================================================
# EDGE TTS - DEEP MALE NARRATION
# ============================================================

async def generate_voice(text, output_path):

    last_error = None

    for attempt in range(5):

        try:

            communicator = edge_tts.Communicate(
                text=text,
                voice=VOICE,
                rate=VOICE_RATE,
                pitch=VOICE_PITCH
            )

            audio_data = bytearray()
            word_boundaries = []

            async for event in communicator.stream():

                if event["type"] == "audio":
                    audio_data.extend(event["data"])

                elif event["type"] == "WordBoundary":

                    word_boundaries.append({
                        "text": event["text"],
                        "start": event["offset"] / 10000000,
                        "end": (
                            event["offset"] + event["duration"]
                        ) / 10000000
                    })

            if len(audio_data) < 1000:
                raise RuntimeError("Voice audio is empty.")

            output_path.write_bytes(audio_data)

            return word_boundaries

        except Exception as error:

            last_error = error

            if attempt < 4:
                await asyncio.sleep(8 * (attempt + 1))

    raise RuntimeError(
        f"Edge TTS failed: {last_error}"
    )


# ============================================================
# SUBTITLES
# ============================================================

def subtitle_timestamp(seconds):

    milliseconds = max(0, int(seconds * 1000))

    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)

    return (
        f"{hours:02}:{minutes:02}:"
        f"{seconds:02},{milliseconds:03}"
    )


def create_subtitles(boundaries, total_duration, output_path, narration):

    groups = []

    if boundaries:

        for start_index in range(0, len(boundaries), 4):

            chunk = boundaries[start_index:start_index + 4]

            start = chunk[0]["start"]
            end = min(chunk[-1]["end"], total_duration)

            if end <= start:
                end = min(start + 0.25, total_duration)

            text = " ".join(
                word["text"] for word in chunk
            )

            groups.append((start, end, text))

    else:

        words = clean_text(narration).split()

        chunks = [
            words[i:i + 4]
            for i in range(0, len(words), 4)
        ]

        weights = [
            sum(len(word) + 1 for word in chunk)
            for chunk in chunks
        ]

        denominator = max(1, sum(weights))
        current = 0.0

        for chunk, weight in zip(chunks, weights):

            end = min(
                total_duration,
                current + total_duration * weight / denominator
            )

            groups.append((
                current,
                end,
                " ".join(chunk)
            ))

            current = end

    lines = []

    for number, (start, end, text) in enumerate(groups, 1):

        lines.extend([
            str(number),
            (
                f"{subtitle_timestamp(start)} --> "
                f"{subtitle_timestamp(end)}"
            ),
            text,
            ""
        ])

    output_path.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# ============================================================
# RENDER EACH WAN CLIP WITH NARRATION
# ============================================================

def render_scene(video, audio, subtitles, output):

    audio_duration = get_duration(audio)
    video_duration = get_duration(video)

    if video_duration <= 0:
        raise RuntimeError("Invalid Wan video duration.")

    # Stretch one real video take to narration length.
    # No looping or repeated still-image fallback.
    speed_factor = audio_duration / video_duration

    subtitle_path = str(
        subtitles.resolve()
    ).replace("\\", "/").replace(":", r"\:")

    subtitle_filter = (
        f"subtitles='{subtitle_path}':"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=18,Bold=1,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H80000000,"
        "BorderStyle=1,Outline=3,Shadow=1,"
        "Alignment=2,MarginV=180,MarginL=70,MarginR=70'"
    )

    video_filter = (
        f"setpts={speed_factor:.6f}*PTS,"
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "setsar=1,fps=30,"
        "eq=contrast=1.16:saturation=0.78:brightness=-0.04,"
        "vignette=PI/5,"
        + subtitle_filter
    )

    run_command([
        "ffmpeg",
        "-y",
        "-i", str(video),
        "-i", str(audio),
        "-vf", video_filter,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-t", f"{audio_duration:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "160k",
        "-ar", "44100",
        "-shortest",
        str(output)
    ])


# ============================================================
# JOIN SIX SCENES
# ============================================================

def join_scenes(rendered_scenes):

    command = ["ffmpeg", "-y"]

    for path in rendered_scenes:
        command.extend(["-i", str(path)])

    filters = []

    current_video = "[0:v]"
    current_audio = "[0:a]"

    elapsed = get_duration(rendered_scenes[0])

    for index in range(1, len(rendered_scenes)):

        next_video = f"[{index}:v]"
        next_audio = f"[{index}:a]"

        output_video = f"[v{index}]"
        output_audio = f"[a{index}]"

        offset = max(0, elapsed - TRANSITION)

        filters.append(
            f"{current_video}{next_video}"
            f"xfade=transition=fade:"
            f"duration={TRANSITION}:"
            f"offset={offset:.3f}"
            f"{output_video}"
        )

        filters.append(
            f"{current_audio}{next_audio}"
            f"acrossfade=d={TRANSITION}:"
            f"c1=tri:c2=tri"
            f"{output_audio}"
        )

        elapsed += (
            get_duration(rendered_scenes[index]) - TRANSITION
        )

        current_video = output_video
        current_audio = output_audio

    final_path = OUTPUT / "midnight_files_short.mp4"

    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", current_video,
        "-map", current_audio,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(final_path)
    ])

    run_command(command)

    return final_path


# ============================================================
# BUILD VIDEO - RESUME FROM CACHE
# ============================================================

def build_video(story):

    print("Connecting to Hugging Face Wan...")

    client = Client(
        WAN_SPACE,
        token=os.environ["HF_TOKEN"],
        verbose=False
    )

    image_paths = []

    # First reuse or create all six starting images.
    for index, scene in enumerate(story["scenes"]):

        cached_image = IMAGES / f"scene_{index + 1}.png"
        local_image = MEDIA / cached_image.name

        if (
            cached_image.exists()
            and cached_image.stat().st_size > 10000
        ):

            shutil.copy2(cached_image, local_image)
            print(f"Reusing cached image {index + 1}/6")

        else:

            print(f"Generating image {index + 1}/6")

            local_image = generate_image(
                story,
                scene,
                index
            )

            shutil.copy2(local_image, cached_image)

            print(f"Saved image cache {index + 1}/6")

        image_paths.append(local_image)

    # Generate missing real Wan videos.
    completed = 0

    for index, scene in enumerate(story["scenes"]):

        cached_video = VIDEOS / f"wan_scene_{index + 1}.mp4"
        local_video = MEDIA / cached_video.name

        if (
            cached_video.exists()
            and cached_video.stat().st_size > 10000
        ):

            shutil.copy2(cached_video, local_video)

            completed += 1

            print(
                f"Reusing genuine Wan video {completed}/6"
            )

            continue

        print(f"Generating genuine Wan video {index + 1}/6")

        try:

            generated = generate_wan_clip(
                client,
                story,
                scene,
                image_paths[index],
                index
            )

        except RuntimeError as error:

            if is_zero_gpu_quota_error(error):

                print("")
                print("HUGGING FACE FREE ZEROGPU QUOTA REACHED")
                print(f"{completed}/6 real video clips saved.")
                print("No photo fallback will be used.")
                print("Saving progress for the next run.")
                print("")

                return None

            raise

        shutil.copy2(generated, cached_video)
        shutil.copy2(cached_video, local_video)

        completed += 1

        print(
            f"Saved genuine Wan video {completed}/6"
        )

    if completed != 6:
        raise RuntimeError(
            f"Only {completed}/6 real Wan clips are ready."
        )

    print("All six genuine Wan videos are ready.")

    # Create narration and subtitles for every scene.
    rendered_scenes = []

    for index, scene in enumerate(story["scenes"]):

        print(f"Creating narration for scene {index + 1}/6")

        video = MEDIA / f"wan_scene_{index + 1}.mp4"
        audio = MEDIA / f"voice_{index + 1}.mp3"
        subtitles = MEDIA / f"captions_{index + 1}.srt"
        rendered = MEDIA / f"rendered_{index + 1}.mp4"

        boundaries = asyncio.run(
            generate_voice(
                scene["narration"],
                audio
            )
        )

        make_duration = get_duration(audio)

        create_subtitles(
            boundaries,
            make_duration,
            subtitles,
            scene["narration"]
        )

        print(f"Rendering scene {index + 1}/6")

        render_scene(
            video,
            audio,
            subtitles,
            rendered
        )

        rendered_scenes.append(rendered)

    print("Joining all six scenes...")

    return join_scenes(rendered_scenes)


# ============================================================
# YOUTUBE UPLOAD
# ============================================================

def upload_to_youtube(video_path, story):

    credentials = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload"
        ]
    )

    credentials.refresh(Request())

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    tags = story.get(
        "tags",
        ["horror", "scary story", "shorts"]
    )

    if isinstance(tags, str):
        tags = [
            tag.strip()
            for tag in tags.split(",")
            if tag.strip()
        ]

    tags = list(dict.fromkeys(
        tags + ["Shorts", "Horror"]
    ))

    body = {
        "snippet": {
            "title": story["title"][:100],
            "description": (
                story.get("description", "")
                + "\n\n#Shorts #Horror #ScaryStory"
            )[:4900],
            "tags": tags[:25],
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
        chunksize=8 * 1024 * 1024,
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
                "YouTube upload:",
                int(status.progress() * 100),
                "%"
            )

    video_id = response["id"]

    # Write the success marker immediately after YouTube confirms upload.
    UPLOAD_FILE.write_text(
        json.dumps({
            "video_id": video_id,
            "title": story["title"],
            "uploaded_at_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )
        }, indent=2),
        encoding="utf-8"
    )

    print("Published successfully:")
    print(f"https://youtube.com/shorts/{video_id}")

    return video_id


# ============================================================
# START A NEW CYCLE AFTER A CONFIRMED UPLOAD
# ============================================================

def clear_previous_cycle():

    print("Previous upload was confirmed.")
    print("Preparing a fresh story cycle.")

    if STORY_FILE.exists():
        STORY_FILE.unlink()

    for folder in (IMAGES, VIDEOS):

        for item in folder.iterdir():

            if item.is_file():
                item.unlink()

    if UPLOAD_FILE.exists():
        UPLOAD_FILE.unlink()


# ============================================================
# MAIN
# ============================================================

def main():

    required = [
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "HF_TOKEN",
        "YOUTUBE_REFRESH_TOKEN",
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET"
    ]

    missing = [
        name for name in required
        if not os.getenv(name)
    ]

    if missing:
        raise RuntimeError(
            "Missing GitHub secrets: " + ", ".join(missing)
        )

    # A marker means the previous upload succeeded.
    # Start a new story on the next scheduled/manual run.
    if UPLOAD_FILE.exists():
        clear_previous_cycle()

    # Reuse the same story after quota pauses.
    if STORY_FILE.exists():

        print("Reusing cached screenplay.")

        story = json.loads(
            STORY_FILE.read_text(encoding="utf-8")
        )

    else:

        print("Creating a new horror story.")

        story = generate_story()

        STORY_FILE.write_text(
            json.dumps(
                story,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print("Saved screenplay cache.")

    print("")
    print("MIDNIGHT FILES:", story["title"])
    print("")

    final_video = build_video(story)

    if final_video is None:

        print("PAUSED: Waiting for free Wan quota reset.")
        print("The workflow should save the scene cache.")
        return

    print("Final video duration:", get_duration(final_video))

    upload_to_youtube(
        final_video,
        story
    )

    print("")
    print("MIDNIGHT FILES FINISHED.")
    print("Upload marker saved for duplicate protection.")


if __name__ == "__main__":
    main()
