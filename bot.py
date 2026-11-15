
import os
import re
import json
import time
import asyncio
import subprocess
from pathlib import Path

import requests
import edge_tts

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ==========================================
# MIDNIGHT FILES - CINEMATIC HORROR SETTINGS
# ==========================================

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "output"
MEDIA = OUTPUT / "media"

OUTPUT.mkdir(exist_ok=True)
MEDIA.mkdir(exist_ok=True)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# Deep cinematic male voice
VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")
VOICE_RATE = "-8%"
VOICE_PITCH = "-25Hz"

WIDTH = 1080
HEIGHT = 1920
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
                timeout=(20, 180),
                **kwargs
            )

            if response.status_code in (429, 500, 502, 503, 504):
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

    raise RuntimeError(f"Network request failed: {last_error}")


# ==========================================
# GEMINI - CINEMATIC STORY GENERATION
# ==========================================

def generate_story():

    api_key = os.environ["GEMINI_API_KEY"]

    prompt = """
Create one original cinematic horror short film story.

Return ONLY valid JSON.

STORY REQUIREMENTS:
- English narration.
- 115 to 145 spoken words total.
- Six consecutive scenes.
- Powerful first-sentence hook.
- Gradually increasing suspense.
- Strong unexpected ending.
- Advertiser-friendly horror.
- No gore or graphic violence.

VISUAL REQUIREMENTS:

This must feel like a continuous horror MOVIE,
not six unrelated stock-video clips.

All scenes must belong to the SAME story.

Maintain continuity:
- Same main character.
- Same clothing and appearance.
- Same house or location when appropriate.
- Same time of night.
- Consistent atmosphere and lighting.
- Events must happen in chronological order.

Every scene must visually show what the narration describes.

Use realistic cinematic film shots:
- Establishing shots.
- Close-ups.
- Over-the-shoulder shots.
- Tracking shots.
- POV shots.
- Suspenseful camera angles.

Avoid generic horror footage.

Avoid abstract searches such as:
"horror", "scary", "creepy".

Use specific searchable descriptions.

Return this exact structure:

{
  "title": "Story title",
  "description": "YouTube description",
  "tags": ["horror", "scary story", "shorts"],
  "scenes": [
    {
      "narration": "First part of story.",
      "visual_query": "specific realistic cinematic video search",
      "visual_description": "Detailed description of the exact shot",
      "mood": "dark and suspenseful"
    }
  ]
}

EXACTLY SIX SCENES.

Each narration must continue naturally from the previous scene.
Do not repeat or skip story events.

Keep the JSON compact.
Do not use Markdown.
Do not add explanations.
Ensure the JSON is complete and valid.
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
    )

    print("Using Gemini:", GEMINI_MODEL)

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

    data = response.json()

    candidates = data.get("candidates", [])

    if not candidates:
        raise RuntimeError(
            "Gemini returned no candidates: " + str(data)[:2000]
        )

    candidate = candidates[0]

    if candidate.get("finishReason") == "MAX_TOKENS":
        raise RuntimeError(
            "Gemini reached its output token limit. "
            "The story was incomplete."
        )

    parts = candidate.get("content", {}).get("parts", [])

    raw = "".join(
        part.get("text", "")
        for part in parts
        if part.get("text")
    ).strip()

    if not raw:
        raise RuntimeError("Gemini returned an empty story.")

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        story = json.loads(raw)
    except json.JSONDecodeError as error:
        print("Gemini response:", raw[:3000])
        raise RuntimeError(
            f"Gemini returned invalid JSON: {error}"
        ) from error

    scenes = story.get("scenes", [])

    if len(scenes) != 6:
        raise RuntimeError(
            f"Expected 6 scenes, received {len(scenes)}."
        )

    for scene in scenes:
        scene["narration"] = clean(scene.get("narration"))
        scene["visual_query"] = clean(scene.get("visual_query"))
        scene["visual_description"] = clean(
            scene.get("visual_description")
        )
        scene["mood"] = clean(scene.get("mood"))

        if not scene["narration"] or not scene["visual_query"]:
            raise RuntimeError(
                "A scene is missing narration or visual_query."
            )

    story["title"] = clean(story.get("title")) or "Midnight Files"
    story["description"] = clean(story.get("description"))

    story["tags"] = story.get(
        "tags",
        ["horror", "scary story", "shorts"]
    )

    story["narration"] = " ".join(
        scene["narration"] for scene in scenes
    )

    word_count = len(story["narration"].split())

    if not 100 <= word_count <= 160:
        raise RuntimeError(
            f"Story contains {word_count} words."
        )

    print("Story generated successfully.")
    print("Story title:", story["title"])
    print("Word count:", word_count)

    return story


# ==========================================
# PIXABAY - STORY MATCHED VIDEO FOOTAGE
# ==========================================

def find_video(query):

    api_key = os.environ["PIXABAY_API_KEY"]

    response = request_with_retry(
        "GET",
        "https://pixabay.com/api/videos/",
        params={
            "key": api_key,
            "q": query[:100],
            "per_page": 20,
            "safesearch": "true",
            "order": "popular"
        }
    )

    hits = response.json().get("hits", [])

    if not hits:
        return None

    # Prefer landscape or portrait clips with adequate resolution.
    candidates = []

    query_words = set(
        re.findall(r"[a-z]+", query.lower())
    )

    for hit in hits:
        videos = hit.get("videos", {})
        video = videos.get("medium", {})

        if not video.get("url"):
            continue

        tags = set(
            re.findall(
                r"[a-z]+",
                str(hit.get("tags", "")).lower()
            )
        )

        relevance = len(query_words.intersection(tags))

        width = video.get("width", 0)
        height = video.get("height", 0)

        quality = min(width, height) / 1000

        candidates.append((
            relevance + quality,
            hit,
            video
        ))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    _, hit, video = candidates[0]

    return {
        "url": video["url"],
        "page": hit.get("pageURL", ""),
        "creator": hit.get("user", "Pixabay contributor")
    }


def download_video(url, destination):

    response = request_with_retry(
        "GET",
        url,
        stream=True
    )

    with open(destination, "wb") as file:
        for chunk in response.iter_content(1024 * 1024):
            if chunk:
                file.write(chunk)

    if (
        not destination.exists()
        or destination.stat().st_size < 10000
    ):
        raise RuntimeError("Downloaded video is empty.")

    return destination


def get_scene_video(scene, index):

    query = scene["visual_query"]

    print(f"Searching cinematic scene {index + 1}: {query}")

    result = find_video(query)

    if not result:
        raise RuntimeError(
            f"No matching footage found for scene {index + 1}: "
            f"{query}"
        )

    destination = MEDIA / f"scene_{index + 1}.mp4"

    download_video(result["url"], destination)

    scene["credit"] = (
        f"Scene {index + 1}: {result['creator']} "
        f"- {result['page']}"
    )

    print("Downloaded:", query)

    return destination


# ==========================================
# CINEMATIC DEEP MALE NARRATION
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
                raise RuntimeError("Voice audio is empty.")

            audio_path.write_bytes(audio_data)

            return boundaries

        except Exception as error:
            last_error = error

            if attempt == 4:
                break

            await asyncio.sleep(10 * (attempt + 1))

    raise RuntimeError(
        f"Voice generation failed: {last_error}"
    )


# ==========================================
# SUBTITLES - LOWER SCREEN POSITION
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


def create_subtitles(
    boundaries,
    audio_duration,
    path,
    narration=""
):

    groups = []

    if boundaries:

        for index in range(0, len(boundaries), 4):

            group = boundaries[index:index + 4]

            text = " ".join(
                item["text"] for item in group
            )

            start = group[0]["start"]
            end = min(group[-1]["end"], audio_duration)

            if end <= start:
                end = min(start + 0.25, audio_duration)

            groups.append((start, end, text))

    else:

        print("Using estimated subtitle timings.")

        words = clean(narration).split()

        if not words:
            raise RuntimeError("No narration for subtitles.")

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

            groups.append((
                start,
                end,
                " ".join(chunk)
            ))

            current_time = end

    lines = []

    for index, (start, end, text) in enumerate(groups, start=1):

        lines.extend([
            str(index),
            f"{srt_timestamp(start)} --> {srt_timestamp(end)}",
            text,
            ""
        ])

    path.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


# ==========================================
# CINEMATIC SCENE RENDERING
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

    # Lower subtitles, above the bottom interface area.
    subtitle_filter = (
        f"subtitles='{subtitle_file}':"
        "force_style='"
        "FontName=DejaVu Sans,"
        "FontSize=18,"
        "Bold=1,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H80000000,"
        "BorderStyle=3,"
        "Outline=2,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=190,"
        "MarginL=70,"
        "MarginR=70"
        "'"
    )

    # Portrait crop with subtle cinematic camera movement.
    video_filter = (
        "scale=1188:2112:force_original_aspect_ratio=increase,"
        "crop=1080:1920:"
        "x='(in_w-out_w)/2+12*sin(t*0.35)':"
        "y='(in_h-out_h)/2',"
        "setsar=1,"
        "fps=30,"
        "eq=contrast=1.16:saturation=0.78:brightness=-0.045,"
        "colorbalance=rs=-0.025:gs=-0.01:bs=0.025,"
        "vignette=PI/5,"
        "unsharp=5:5:0.45:5:5:0.0,"
        + subtitle_filter
    )

    run([
        "ffmpeg",
        "-y",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-t", f"{audio_duration:.3f}",
        "-vf", video_filter,
        "-map", "0:v:0",
        "-map", "1:a:0",
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
# BUILD SIX-SCENE HORROR MOVIE
# ==========================================

def build_video(story):

    rendered_scenes = []

    for index, scene in enumerate(story["scenes"]):

        print(f"\nRendering movie scene {index + 1}/6")

        video_path = get_scene_video(scene, index)

        audio_path = MEDIA / f"voice_{index + 1}.mp3"
        subtitle_path = MEDIA / f"captions_{index + 1}.srt"
        rendered_path = MEDIA / f"rendered_{index + 1}.mp4"

        boundaries = asyncio.run(
            generate_voice(
                scene["narration"],
                audio_path
            )
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

    # Smooth video and audio transitions.
    print("\nJoining scenes with cinematic transitions...")

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
            f"acrossfade=d={transition}:c1=tri:c2=tri"
            f"{output_audio}"
        )

        elapsed += (
            duration_of(rendered_scenes[index]) - transition
        )

        current_video = output_video
        current_audio = output_audio

    filter_complex = ";".join(filter_parts)

    final_video = OUTPUT / "midnight_files_short.mp4"

    command.extend([
        "-filter_complex", filter_complex,
        "-map", current_video,
        "-map", current_audio,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "160k",
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

    credits = "\n".join(
        scene.get("credit", "")
        for scene in story["scenes"]
    )

    description = (
        story.get("description", "")
        + "\n\nStock video footage credits:\n"
        + credits
        + "\n\n#Shorts #Horror #ScaryStory"
    )

    tags = story.get(
        "tags",
        ["horror", "scary story", "shorts"]
    )

    if isinstance(tags, str):
        tags = [
            item.strip()
            for item in tags.split(",")
            if item.strip()
        ]

    tags = list(dict.fromkeys(
        tags + ["Shorts", "Horror"]
    ))

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
        "GEMINI_API_KEY",
        "PIXABAY_API_KEY",
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

    print("================================")
    print("MIDNIGHT FILES CINEMATIC ENGINE")
    print("================================")

    print("\nVoice:", VOICE)
    print("Generating horror screenplay...")

    story = generate_story()

    print("\nTITLE:", story["title"])
    print("\nSTORY:", story["narration"])

    print("\nGenerating six cinematic scenes...")

    video = build_video(story)

    print("\nFinal video:", video)
    print("Duration:", duration_of(video), "seconds")

    print("\nUploading to YouTube...")

    upload_to_youtube(video, story)

    print("\nMIDNIGHT FILES COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    main()
