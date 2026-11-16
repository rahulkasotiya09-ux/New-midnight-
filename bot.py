
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


# ==========================================
# MIDNIGHT FILES - CINEMATIC AI VIDEO ENGINE
# ==========================================

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "output"
MEDIA = OUTPUT / "media"

OUTPUT.mkdir(exist_ok=True)
MEDIA.mkdir(exist_ok=True)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
CLOUDFLARE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

WAN_SPACE = "zerogpu-aoti/wan2-2-fp8da-aoti-faster"

WAN_STEPS = 4
WAN_DURATION = 2.0

VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
VOICE_RATE = "-8%"
VOICE_PITCH = "-25Hz"

FPS = 30
TRANSITION_DURATION = 0.35


# ==========================================
# GENERAL HELPERS
# ==========================================

def run(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n" + result.stderr[-5000:]
        )

    return result.stdout.strip()


def duration_of(path):
    result = run([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ])
    return float(result)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def request_with_retry(method, url, **kwargs):
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
                error_text = response.text[:2000]

                if (
                    "generate_content_free_tier_requests" in error_text
                    or "RequestsPerDay" in error_text
                    or "per day limit exceeded" in error_text.lower()
                ):
                    raise RuntimeError(
                        "GEMINI_DAILY_QUOTA: " + error_text
                    )

                last_error = f"HTTP 429: {error_text}"

                if attempt == 4:
                    break

                time.sleep(10 * (attempt + 1))
                continue

            if response.status_code in (500, 502, 503, 504):
                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:1500]}"
                )
                time.sleep(10 * (attempt + 1))
                continue

            if not response.ok:
                raise RuntimeError(
                    f"API HTTP {response.status_code}: "
                    f"{response.text[:2000]}"
                )

            return response

        except requests.RequestException as error:
            last_error = str(error)

            if attempt == 4:
                break

            time.sleep(10 * (attempt + 1))

    raise RuntimeError(f"Request failed: {last_error}")


# ==========================================
# BUILT-IN FREE HORROR STORY FALLBACK
# ==========================================

def fallback_story():
    print("\nGemini quota unavailable.")
    print("Switching to built-in original horror story.")

    narrations = [
        (
            "At 2:13 a.m., Mara heard three slow knocks coming "
            "from inside her bedroom wall. Her apartment was silent, "
            "and that wall faced the outside."
        ),
        (
            "She pressed her ear against the cold plaster. "
            "A little girl's voice whispered, 'Please don't let him "
            "hear you.' Mara froze, barely breathing."
        ),
        (
            "A thin crack appeared in the wallpaper, slowly tracing "
            "a narrow door. Mara stepped backward, watching the "
            "impossible outline grow across the wall."
        ),
        (
            "From behind it came Mara's own voice, calmly asking, "
            "'Why are you standing outside?' She had not spoken "
            "a word all night."
        ),
        (
            "The handle began turning. Mara ran into the hallway, "
            "but every door opened onto that same dark bedroom. "
            "Her apartment had become a loop."
        ),
        (
            "Her phone lit up. Her mother texted, 'Don't open the wall. "
            "We sealed it after you disappeared.' Three knocks sounded "
            "again, inside her phone."
        )
    ]

    visuals = [
        (
            "Mara, a 27-year-old woman with shoulder-length dark hair "
            "and a grey sweater, sits upright in a dark bedroom staring "
            "at a blank wall. A digital clock reads 2:13 AM."
        ),
        (
            "Mara presses her ear against the plaster wall, her "
            "frightened eyes widening in the cold blue bedroom light."
        ),
        (
            "A crack spreads through the wallpaper, forming the outline "
            "of a narrow door. Mara steps backward in fear."
        ),
        (
            "A strange door handle appears in the wall. Mara stares "
            "at it while her reflection looks subtly different "
            "in a nearby mirror."
        ),
        (
            "Mara runs through a dark apartment hallway and opens "
            "a door, only to find the same bedroom again."
        ),
        (
            "Close-up of a phone displaying the mother's warning "
            "message. Mara freezes as the bedroom wall looms behind her."
        )
    ]

    motions = [
        (
            "Mara slowly turns toward the wall. Dust trembles with "
            "each knock as the camera pushes closer."
        ),
        (
            "Her fingers press against the plaster. Her eyes widen "
            "and her breathing becomes shallow."
        ),
        (
            "The crack crawls downward. Mara takes two cautious "
            "steps backward as the camera tracks her."
        ),
        (
            "The handle moves slightly. Mara's reflection appears "
            "to move a fraction later than she does."
        ),
        (
            "A handheld camera follows Mara running. She pulls open "
            "the door and stops in shock."
        ),
        (
            "The phone vibrates in Mara's shaking hand. The camera "
            "slowly pulls back as she turns toward the wall."
        )
    ]

    scenes = []

    for i in range(6):
        scenes.append({
            "narration": narrations[i],
            "visual_description": visuals[i],
            "motion_description": motions[i],
            "mood": "Psychological horror and suspense"
        })

    story = {
        "title": "The Room Behind the Wall",
        "description": (
            "A woman hears a voice coming from a room that should "
            "not exist. An original Midnight Files horror story."
        ),
        "tags": [
            "horror",
            "scary story",
            "ghost story",
            "shorts"
        ],
        "character_description": (
            "Mara, 27, shoulder-length dark brown hair, pale tired face, "
            "faded grey sweater and black trousers. Keep her face and "
            "clothing identical in every scene."
        ),
        "visual_style": (
            "Photorealistic live-action psychological horror, cinematic "
            "35mm film look, cold blue moonlight, charcoal shadows, "
            "subtle film grain and consistent apartment interiors."
        ),
        "scenes": scenes
    }

    for scene in scenes:
        scene["narration"] = clean(scene["narration"])
        scene["visual_description"] = clean(
            scene["visual_description"]
        )
        scene["motion_description"] = clean(
            scene["motion_description"]
        )

    story["narration"] = " ".join(narrations)

    word_count = len(story["narration"].split())

    print("Fallback story:", story["title"])
    print("Narration words:", word_count)

    if not 100 <= word_count <= 160:
        raise RuntimeError(
            f"Fallback story has {word_count} words."
        )

    return story


# ==========================================
# GEMINI - HORROR SCREENPLAY
# ==========================================

def generate_story():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        print("Gemini API key missing.")
        return fallback_story()

    prompt = """
Create one original cinematic horror short film.

Return ONLY valid JSON.

STORY:
- English narration.
- 115 to 145 spoken words total.
- Exactly six consecutive scenes.
- Powerful opening hook.
- Gradually increasing suspense.
- Strong unexpected ending.
- Advertiser-friendly.
- No graphic violence or gore.

CONTINUITY:
The six scenes must feel like one continuous movie.

Create one memorable main character.

Maintain the same:
- Face and age.
- Hair and clothing.
- Location.
- Time of night.
- Lighting and color palette.

Every scene must happen in chronological order.

VISUALS:
Describe exact cinematic shots and physical actions.
Use realistic live-action movie photography.
Use close-ups, wide shots, POV and tracking-style compositions.
Show the actual events being narrated.
Avoid generic horror imagery.

Return this structure:

{
  "title": "Story title",
  "description": "YouTube description",
  "tags": ["horror", "scary story", "shorts"],
  "character_description": "Detailed appearance of the main character",
  "visual_style": "Consistent cinematic visual style",
  "scenes": [
    {
      "narration": "Scene narration",
      "visual_description": "Detailed description of the exact image",
      "motion_description": "Specific continuous physical movement and camera movement",
      "mood": "Suspenseful"
    }
  ]
}

Exactly six scenes.
Narration must total 115-145 words.
Keep the JSON complete and valid.
Do not add Markdown or explanations.
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
    )

    print("Generating screenplay with:", GEMINI_MODEL)

    try:
        response = request_with_retry(
            "POST",
            url,
            params={"key": api_key},
            json={
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.8,
                    "maxOutputTokens": 8192
                }
            }
        )

    except RuntimeError as error:
        if "GEMINI_DAILY_QUOTA" in str(error):
            print("Gemini daily quota exhausted.")
            return fallback_story()

        raise

    data = response.json()
    candidates = data.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            "Gemini returned no candidates: " + str(data)[:2000]
        )

    candidate = candidates[0]

    if candidate.get("finishReason") == "MAX_TOKENS":
        raise RuntimeError("Gemini output was cut off.")

    parts = candidate.get("content", {}).get("parts", [])

    raw = "".join(
        part.get("text", "")
        for part in parts
        if part.get("text")
    ).strip()

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        story = json.loads(raw)

    except json.JSONDecodeError as error:
        print(raw[:3000])
        raise RuntimeError(f"Invalid Gemini JSON: {error}")

    scenes = story.get("scenes", [])

    if len(scenes) != 6:
        raise RuntimeError(
            f"Expected six scenes, got {len(scenes)}."
        )

    for scene in scenes:
        scene["narration"] = clean(scene.get("narration"))
        scene["visual_description"] = clean(
            scene.get("visual_description")
        )
        scene["motion_description"] = clean(
            scene.get("motion_description")
        )

        if not scene["narration"] or not scene["visual_description"]:
            raise RuntimeError("A scene is missing required content.")

    story["title"] = clean(story.get("title")) or "Midnight Files"
    story["description"] = clean(story.get("description"))

    story["character_description"] = clean(
        story.get("character_description")
    )

    story["visual_style"] = clean(
        story.get("visual_style")
    )

    story["tags"] = story.get(
        "tags", ["horror", "scary story", "shorts"]
    )

    story["narration"] = " ".join(
        scene["narration"] for scene in scenes
    )

    word_count = len(story["narration"].split())

    if not 100 <= word_count <= 160:
        raise RuntimeError(f"Story has {word_count} words.")

    print("Story:", story["title"])
    print("Narration words:", word_count)

    return story


# ==========================================
# CLOUDFLARE FLUX - STARTING FRAME
# ==========================================

def generate_scene_image(story, scene, index):
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    api_token = os.environ["CLOUDFLARE_API_TOKEN"]

    prompt = f"""
Photorealistic live-action cinematic horror movie frame.

MAIN CHARACTER - KEEP CONSISTENT:
{story.get("character_description", "")}

VISUAL STYLE:
{story.get("visual_style", "")}

EXACT SCENE:
{scene["visual_description"]}

STORY MOMENT:
{scene["narration"]}

Vertical 9:16 composition.
Realistic human face and anatomy.
Detailed coherent environment.
Cinematic low-light photography.
Keep the main subject visible and centered.
No text, captions, logos or watermark.
No collage, split-screen, cartoon or illustration.
No graphic gore.
"""

    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/ai/run/{CLOUDFLARE_MODEL}"
    )

    print(f"\nGenerating starting frame {index + 1}/6...")

    response = request_with_retry(
        "POST",
        url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        },
        json={
            "prompt": clean(prompt)[:2000],
            "steps": 4
        }
    )

    data = response.json()

    if not data.get("success", False):
        raise RuntimeError(
            "Cloudflare image generation failed: "
            + str(data.get("errors", data))[:2000]
        )

    image_base64 = data.get("result", {}).get("image")

    if not image_base64:
        raise RuntimeError("Cloudflare returned no image.")

    image_bytes = base64.b64decode(image_base64)

    image_path = MEDIA / f"scene_{index + 1}.png"
    image_path.write_bytes(image_bytes)

    if image_path.stat().st_size < 10000:
        raise RuntimeError("Generated starting frame is too small.")

    print("Starting frame saved:", image_path.name)

    return image_path


# ==========================================
# WAN 2.2 - REAL AI VIDEO GENERATION
# ==========================================

def create_wan_client():
    token = os.environ.get("HF_TOKEN", "").strip()

    if not token:
        raise RuntimeError("HF_TOKEN secret is missing.")

    print("\nConnecting to Hugging Face Wan 2.2...")

    client = Client(
        WAN_SPACE,
        token=token,
        verbose=False
    )

    print("Wan 2.2 connection successful.")

    return client


def generate_video_clip(client, story, scene, image_path, index):
    character = story.get("character_description", "")
    visual_style = story.get("visual_style", "")

    prompt = f"""
Create a continuous cinematic live-action horror movie shot.

CHARACTER IDENTITY:
{character}

VISUAL STYLE:
{visual_style}

SCENE ACTION:
{scene["visual_description"]}

MOVEMENT:
{scene.get("motion_description", "")}

NARRATIVE CONTEXT:
{scene["narration"]}

Animate the actual action described.
Natural human body movement and realistic facial motion.
Believable environmental movement.
Smooth physical motion, not a slideshow.
Cinematic camera movement.
Maintain the same character appearance and clothing.
Maintain the scene location and lighting.
One continuous shot.
No cuts, no scene changes, no text or subtitles.
No sudden character transformations.
Photorealistic movie footage.
"""

    negative_prompt = (
        "still image, static frame, slideshow, frozen movement, "
        "flickering, jitter, distorted face, deformed body, "
        "extra limbs, extra fingers, changing clothes, "
        "changing identity, text, subtitles, watermark, "
        "cartoon, illustration, low quality, blurry"
    )

    print(
        f"\nGenerating REAL Wan 2.2 video "
        f"{index + 1}/6..."
    )

    print("Uploading image with handle_file...")
    print("Requested duration:", WAN_DURATION, "seconds")
    print("Inference steps:", WAN_STEPS)

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
            f"Wan generation failed on scene {index + 1}: "
            f"{type(error).__name__}: {error}"
        ) from error

    generated_video = (
        result[0] if isinstance(result, tuple) else result
    )

    if isinstance(generated_video, dict):
        generated_video = (
            generated_video.get("path")
            or generated_video.get("video")
        )

    if not generated_video:
        raise RuntimeError("Wan 2.2 returned no video path.")

    source_path = Path(str(generated_video))

    if not source_path.exists():
        raise RuntimeError(
            "Wan video output file does not exist: "
            + str(source_path)
        )

    video_path = MEDIA / f"wan_scene_{index + 1}.mp4"

    shutil.copy2(source_path, video_path)

    if video_path.stat().st_size < 10000:
        raise RuntimeError("Generated video file is too small.")

    print(
        "Actual AI video saved:",
        video_path.name,
        f"({video_path.stat().st_size // 1024} KB)"
    )

    return video_path


# ==========================================
# DEEP MALE VOICE
# ==========================================

async def generate_voice(text, audio_path):
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
            boundaries = []

            async for event in communicator.stream():
                if event["type"] == "audio":
                    audio_data.extend(event["data"])

                elif event["type"] == "WordBoundary":
                    boundaries.append({
                        "text": event["text"],
                        "start": event["offset"] / 10000000,
                        "end": (
                            event["offset"] + event["duration"]
                        ) / 10000000
                    })

            if len(audio_data) < 1000:
                raise RuntimeError("Generated voice audio is empty.")

            audio_path.write_bytes(audio_data)

            return boundaries

        except Exception as error:
            last_error = error

            if attempt == 4:
                break

            await asyncio.sleep(10 * (attempt + 1))

    raise RuntimeError(f"Voice generation failed: {last_error}")


# ==========================================
# SUBTITLES
# ==========================================

def srt_timestamp(seconds):
    milliseconds = max(0, int(seconds * 1000))

    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)

    return (
        f"{hours:02}:{minutes:02}:"
        f"{seconds:02},{milliseconds:03}"
    )


def create_subtitles(boundaries, audio_duration, path, narration=""):
    groups = []

    if boundaries:
        for index in range(0, len(boundaries), 4):
            group = boundaries[index:index + 4]

            text = " ".join(item["text"] for item in group)
            start = group[0]["start"]
            end = min(group[-1]["end"], audio_duration)

            if end <= start:
                end = min(start + 0.25, audio_duration)

            groups.append((start, end, text))

    else:
        words = clean(narration).split()

        chunks = [
            words[i:i + 4]
            for i in range(0, len(words), 4)
        ]

        weights = [
            sum(len(word) + 1 for word in chunk)
            for chunk in chunks
        ]

        total_weight = sum(weights)
        current_time = 0.0

        for chunk, weight in zip(chunks, weights):
            duration = audio_duration * weight / total_weight

            start = current_time
            end = min(current_time + duration, audio_duration)

            groups.append((start, end, " ".join(chunk)))
            current_time = end

    lines = []

    for index, (start, end, text) in enumerate(groups, start=1):
        lines.extend([
            str(index),
            f"{srt_timestamp(start)} --> {srt_timestamp(end)}",
            text,
            ""
        ])

    path.write_text("\n".join(lines), encoding="utf-8")


# ==========================================
# RENDER VIDEO + NARRATION + SUBTITLES
# ==========================================

def render_scene(
    video_path,
    audio_path,
    subtitle_path,
    output_path
):
    audio_duration = duration_of(audio_path)

    subtitle_file = str(subtitle_path.resolve())
    subtitle_file = subtitle_file.replace("\\", "/")
    subtitle_file = subtitle_file.replace(":", r"\:")
    subtitle_file = subtitle_file.replace("'", r"\'")

    subtitle_filter = (
        f"subtitles='{subtitle_file}':"
        "force_style='"
        "FontName=DejaVu Sans,"
        "FontSize=18,"
        "Bold=1,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H80000000,"
        "BorderStyle=1,"
        "Outline=3,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=180,"
        "MarginL=70,"
        "MarginR=70"
        "'"
    )

    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "setsar=1,"
        "fps=30,"
        "eq=contrast=1.16:saturation=0.78:brightness=-0.04,"
        "colorbalance=rs=-0.025:gs=-0.01:bs=0.025,"
        "vignette=PI/5,"
        + subtitle_filter
    )

    run([
        "ffmpeg",
        "-y",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-i", str(audio_path),
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
        str(output_path)
    ])


# ==========================================
# BUILD SIX-SCENE HORROR SHORT
# ==========================================

def build_video(story):
    client = create_wan_client()
    rendered_scenes = []

    for index, scene in enumerate(story["scenes"]):
        print(f"\n========== SCENE {index + 1}/6 ==========")

        image_path = generate_scene_image(
            story, scene, index
        )

        video_path = generate_video_clip(
            client, story, scene, image_path, index
        )

        audio_path = MEDIA / f"voice_{index + 1}.mp3"
        subtitle_path = MEDIA / f"captions_{index + 1}.srt"
        rendered_path = MEDIA / f"rendered_{index + 1}.mp4"

        boundaries = asyncio.run(
            generate_voice(scene["narration"], audio_path)
        )

        audio_duration = duration_of(audio_path)

        create_subtitles(
            boundaries,
            audio_duration,
            subtitle_path,
            scene["narration"]
        )

        render_scene(
            video_path,
            audio_path,
            subtitle_path,
            rendered_path
        )

        rendered_scenes.append(rendered_path)

        print(f"Scene {index + 1} completed.")

    print("\nJoining six moving scenes...")

    command = ["ffmpeg", "-y"]

    for path in rendered_scenes:
        command.extend(["-i", str(path)])

    filter_parts = []

    transition = TRANSITION_DURATION
    current_video = "[0:v]"
    current_audio = "[0:a]"

    elapsed = duration_of(rendered_scenes[0])

    for index in range(1, len(rendered_scenes)):
        next_video = f"[{index}:v]"
        next_audio = f"[{index}:a]"

        output_video = f"[v{index}]"
        output_audio = f"[a{index}]"

        offset = max(0, elapsed - transition)

        filter_parts.append(
            f"{current_video}{next_video}"
            f"xfade=transition=fade:"
            f"duration={transition}:"
            f"offset={offset:.3f}"
            f"{output_video}"
        )

        filter_parts.append(
            f"{current_audio}{next_audio}"
            f"acrossfade=d={transition}:"
            f"c1=tri:c2=tri"
            f"{output_audio}"
        )

        elapsed += (
            duration_of(rendered_scenes[index]) - transition
        )

        current_video = output_video
        current_audio = output_audio

    final_video = OUTPUT / "midnight_files_short.mp4"

    command.extend([
        "-filter_complex",
        ";".join(filter_parts),
        "-map", current_video,
        "-map", current_audio,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(final_video)
    ])

    run(command)

    return final_video


# ==========================================
# YOUTUBE UPLOAD
# ==========================================

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
        credentials=credentials
    )

    description = (
        story.get("description", "")
        + "\n\n#Shorts #Horror #ScaryStory"
    )

    tags = story.get(
        "tags", ["horror", "scary story", "shorts"]
    )

    if isinstance(tags, str):
        tags = [
            item.strip()
            for item in tags.split(",")
            if item.strip()
        ]

    tags = list(dict.fromkeys(tags + ["Shorts", "Horror"]))

    body = {
        "snippet": {
            "title": story["title"][:100],
            "description": description[:4900],
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

    upload_request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None

    while response is None:
        status, response = upload_request.next_chunk()

        if status:
            print(
                "Upload progress:",
                int(status.progress() * 100),
                "%"
            )

    video_id = response["id"]

    print(
        "Published:",
        f"https://youtube.com/shorts/{video_id}"
    )

    return video_id


# ==========================================
# MAIN
# ==========================================

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
            "Missing environment variables: "
            + ", ".join(missing)
        )

    print("===================================")
    print("MIDNIGHT FILES - WAN 2.2 VIDEO ENGINE")
    print("===================================")

    print("Voice:", VOICE)
    print("Image model:", CLOUDFLARE_MODEL)
    print("Video model:", WAN_SPACE)
    print("Wan steps:", WAN_STEPS)
    print("Wan duration:", WAN_DURATION)

    print("\nGenerating horror screenplay...")

    story = generate_story()

    print("\nTITLE:", story["title"])
    print("\nSTORY:", story["narration"])

    print("\nGenerating six actual AI video scenes...")

    video = build_video(story)

    print("\nFinal video:", video)
    print("Duration:", duration_of(video), "seconds")

    print("\nUploading to YouTube...")

    upload_to_youtube(video, story)

    print("\nMIDNIGHT FILES COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    main()
