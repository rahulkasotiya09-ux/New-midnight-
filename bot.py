
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
# MIDNIGHT FILES SETTINGS
# ==========================================

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "output"
MEDIA = OUTPUT / "media"

OUTPUT.mkdir(exist_ok=True)
MEDIA.mkdir(exist_ok=True)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

VOICE = "en-US-RogerNeural"
VOICE_RATE = "-10%"
VOICE_PITCH = "-18Hz"

WIDTH = 1080
HEIGHT = 1920
FPS = 30


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
            "Command failed:\n"
            + result.stderr[-4000:]
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


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def request_with_retry(method, url, **kwargs):
    last_error = None

    for attempt in range(5):
        try:
            response = requests.request(
                method,
                url,
                timeout=60,
                **kwargs
            )

            if response.status_code in (429, 500, 502, 503, 504):
                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:2000]}"
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

    raise RuntimeError(
        f"Network request failed: {last_error}"
    )


# ==========================================
# GEMINI STORY GENERATION
# ==========================================

def generate_story():

    api_key = os.environ["GEMINI_API_KEY"]

    prompt = """
Create ONE original, suspenseful, advertiser-friendly
English horror story for a YouTube Short.

The story must contain:
- 115 to 145 spoken words.
- A powerful hook in the first sentence.
- Increasing suspense.
- A surprising ending.
- Natural narration suitable for a scary male voice.

Return ONLY valid JSON in this structure:

{
  "title": "Story title",
  "description": "Short YouTube description",
  "tags": ["horror", "scary story", "shorts"],
  "scenes": [
    {
      "narration": "First consecutive part of the story.",
      "visual_query": "dark staircase",
      "mood": "eerie and suspenseful"
    }
  ]
}

IMPORTANT SCENE RULES:

Create exactly 6 scenes.

Divide the complete story into 6 consecutive narration parts.
Do not skip or repeat any sentence.

Every visual_query must describe what viewers should
actually SEE during that narration.

Examples:
- footsteps upstairs = dark staircase
- someone looking through a window = person at window
- an empty house = empty living room
- a door slowly opening = opening wooden door
- walking through woods = dark forest path
- a phone ringing = mobile phone on table

Use short, searchable stock-video phrases.
Do not use generic queries like horror, scary, creepy.
Do not use copyrighted characters.
Do not include gore or graphic violence.

The six narration parts together must form the entire story.
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent"
    )

    print("Using Gemini model:", GEMINI_MODEL)

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
                "temperature": 0.9
            }
        }
    )

    data = response.json()

    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        story = json.loads(raw)

    except Exception as error:
        raise RuntimeError(
            f"Gemini returned invalid JSON: {data}"
        ) from error

    scenes = story.get("scenes", [])

    if len(scenes) != 6:
        raise RuntimeError(
            "Gemini did not generate exactly six scenes."
        )

    for scene in scenes:
        scene["narration"] = clean(scene.get("narration"))
        scene["visual_query"] = clean(scene.get("visual_query"))

        if not scene["narration"] or not scene["visual_query"]:
            raise RuntimeError(
                "A scene is missing required information."
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
            f"Story length is {word_count} words. "
            "Expected approximately 115–145."
        )

    return story


# ==========================================
# STORY-MATCHED PIXABAY VIDEO SEARCH
# ==========================================

def find_video(query):

    api_key = os.environ["PIXABAY_API_KEY"]

    response = request_with_retry(
        "GET",
        "https://pixabay.com/api/videos/",
        params={
            "key": api_key,
            "q": query[:100],
            "per_page": 10,
            "safesearch": "true",
            "order": "popular"
        }
    )

    hits = response.json().get("hits", [])

    if not hits:
        return None

    for hit in hits:
        videos = hit.get("videos", {})
        video = videos.get("medium", {})

        if video.get("url"):
            return {
                "url": video["url"],
                "page": hit.get("pageURL", ""),
                "creator": hit.get("user", "Pixabay contributor")
            }

    return None


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

    if not destination.exists() or destination.stat().st_size < 10000:
        raise RuntimeError("Downloaded video is empty.")

    return destination


def get_scene_video(scene, index):

    query = scene["visual_query"]

    print(
        f"Searching scene {index + 1}: {query}"
    )

    result = find_video(query)

    if not result:
        raise RuntimeError(
            f"No matching Pixabay video found for: {query}. "
            "Stopping instead of using unrelated footage."
        )

    destination = MEDIA / f"scene_{index + 1}.mp4"

    download_video(
        result["url"],
        destination
    )

    scene["credit"] = (
        f"Scene {index + 1}: {result['creator']} "
        f"- {result['page']}"
    )

    print("Downloaded matching scene:", query)

    return destination


# ==========================================
# DEEP MALE VOICE + WORD TIMING
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

            audio_path.write_bytes(audio_data)

            return word_boundaries

        except Exception as error:

            last_error = error

            if attempt == 4:
                break

            await asyncio.sleep(10 * (attempt + 1))

    raise RuntimeError(
        f"Voice generation failed: {last_error}"
    )


# ==========================================
# SYNCHRONIZED SUBTITLES
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


def create_subtitles(boundaries, audio_duration, path):

    if not boundaries:
        raise RuntimeError(
            "TTS did not provide word timing information; "
            "refusing to create falsely synchronized captions."
        )

    groups = []

    for index in range(0, len(boundaries), 4):

        group = boundaries[index:index + 4]

        text = " ".join(
            item["text"] for item in group
        )

        start = group[0]["start"]
        end = group[-1]["end"]

        end = min(end, audio_duration)

        if end <= start:
            end = min(start + 0.25, audio_duration)

        groups.append((start, end, text))

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
# RENDER EACH STORY SCENE
# ==========================================

def render_scene(video_path, audio_path, subtitle_path, output_path):

    audio_duration = duration_of(audio_path)

    subtitle_file = str(subtitle_path.resolve())
    subtitle_file = subtitle_file.replace("\\", "/")
    subtitle_file = subtitle_file.replace(":", r"\:")
    subtitle_file = subtitle_file.replace("'", r"\'")

    subtitle_filter = (
        f"subtitles='{subtitle_file}':"
        "force_style='FontName=DejaVu Sans,"
        "FontSize=19,"
        "Bold=1,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=3,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=250'"
    )

    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "setsar=1,"
        "fps=30,"
        "eq=contrast=1.08:saturation=0.85:brightness=-0.03,"
        "fade=t=in:st=0:d=0.25,"
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
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-shortest",
        str(output_path)
    ])


# ==========================================
# BUILD COMPLETE SHORT
# ==========================================

def build_video(story):

    rendered_scenes = []

    for index, scene in enumerate(story["scenes"]):

        print(
            f"\nProcessing scene {index + 1}/6"
        )

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
            subtitle_path
        )

        render_scene(
            video_path,
            audio_path,
            subtitle_path,
            rendered_path
        )

        rendered_scenes.append(rendered_path)

    list_path = MEDIA / "scenes.txt"

    with open(list_path, "w", encoding="utf-8") as file:
        for scene_path in rendered_scenes:
            file.write(
                f"file '{scene_path.resolve()}'\n"
            )

    final_video = OUTPUT / "midnight_files_short.mp4"

    run([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(final_video)
    ])

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
                f"Upload progress: "
                f"{int(status.progress() * 100)}%"
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

    print("MIDNIGHT FILES STARTED")

    print("\nGenerating horror story...")
    story = generate_story()

    print("\nTITLE:", story["title"])
    print("\nSTORY:", story["narration"])

    print("\nGenerating six matching video scenes...")
    video = build_video(story)

    print("\nFinal video:", video)
    print("Duration:", duration_of(video), "seconds")

    print("\nUploading to YouTube...")
    upload_to_youtube(video, story)

    print("\nMIDNIGHT FILES COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    main()
