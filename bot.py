
import os
import re
import json
import random
import asyncio
import subprocess
from pathlib import Path

import requests
import edge_tts

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# =====================================
# CONFIGURATION
# =====================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")

MODEL = "gemini-3.8-flash"

VOICE = "en-US-GuyNeural"

WIDTH = 1080
HEIGHT = 1920

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

IMAGE_FILE = OUTPUT_DIR / "background.png"
AUDIO_FILE = OUTPUT_DIR / "voice.mp3"
VIDEO_FILE = OUTPUT_DIR / "midnight_files.mp4"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]


# =====================================
# CHECK SETTINGS
# =====================================

def check_settings():
    required = {
        "GEMINI_API_KEY": GEMINI_API_KEY,
        "YOUTUBE_CLIENT_ID": YOUTUBE_CLIENT_ID,
        "YOUTUBE_CLIENT_SECRET": YOUTUBE_CLIENT_SECRET,
        "YOUTUBE_REFRESH_TOKEN": YOUTUBE_REFRESH_TOKEN,
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        raise RuntimeError(
            "Missing GitHub secrets: " + ", ".join(missing)
        )


# =====================================
# GENERATE HORROR STORY
# =====================================

def ask_gemini():

    print("Generating horror story with Gemini...")

    url = (
        f"https://generativelanguage.googleapis.com/"
        f"v1beta/models/{MODEL}:generateContent"
    )

    prompt = """
Write an original, extremely engaging horror story for a
YouTube Shorts video.

Requirements:
- Around 100 to 130 words.
- Start with a powerful scary hook.
- Psychological horror and supernatural mystery.
- Build suspense throughout.
- Include an unexpected terrifying ending.
- Make it sound natural when spoken aloud.
- Do not include camera directions.
- Do not include sound effect instructions.
- Do not include hashtags.
- Avoid excessive dialogue.

Return exactly this format:

TITLE: A short scary title

STORY:
The complete narration.
"""

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 700
        }
    }

    response = requests.post(
        url,
        params={"key": GEMINI_API_KEY},
        json=payload,
        timeout=90
    )

    if not response.ok:
        raise RuntimeError(
            f"Gemini API error {response.status_code}: "
            f"{response.text[:1500]}"
        )

    data = response.json()

    try:
        result = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            "Gemini returned an unexpected response: "
            + json.dumps(data)[:1500]
        )

    title_match = re.search(
        r"TITLE:\s*(.+)",
        result,
        re.IGNORECASE
    )

    story_match = re.search(
        r"STORY:\s*(.*)",
        result,
        re.IGNORECASE | re.DOTALL
    )

    title = (
        title_match.group(1).strip()
        if title_match else "The Midnight Visitor"
    )

    story = (
        story_match.group(1).strip()
        if story_match else result.strip()
    )

    story = re.sub(r"\*\*", "", story)
    story = re.sub(r"^#+\s*", "", story, flags=re.MULTILINE)

    if len(story.split()) < 30:
        raise RuntimeError("Gemini generated a story that is too short.")

    print("Story generated:", title)

    return title, story


# =====================================
# GENERATE AI VOICE
# =====================================

async def generate_voice(story):

    print("Generating AI narration...")

    communicate = edge_tts.Communicate(
        story,
        VOICE,
        rate="-5%",
        pitch="-5Hz"
    )

    await communicate.save(str(AUDIO_FILE))

    if not AUDIO_FILE.exists():
        raise RuntimeError("Voice generation failed.")

    print("Voice generated successfully.")


# =====================================
# CREATE HORROR BACKGROUND
# =====================================

def get_font(size, bold=False):

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",

        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
    ]

    for path in font_paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):

    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = current + " " + word if current else word

        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def create_background(title):

    print("Creating horror background...")

    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (5, 4, 10)
    )

    pixels = image.load()

    for y in range(HEIGHT):
        for x in range(WIDTH):
            distance = abs(x - WIDTH // 2) / WIDTH
            darkness = int(25 * (1 - distance))

            pixels[x, y] = (
                8 + darkness,
                3 + darkness // 5,
                12 + darkness
            )

    draw = ImageDraw.Draw(image, "RGBA")

    # Dark atmospheric shapes
    for _ in range(45):
        x = random.randint(0, WIDTH)
        y = random.randint(0, HEIGHT)
        radius = random.randint(15, 100)

        draw.ellipse(
            (x-radius, y-radius, x+radius, y+radius),
            fill=(80, 0, 15, 12)
        )

    # Main title
    title_font = get_font(88, bold=True)
    small_font = get_font(38, bold=True)

    lines = wrap_text(
        draw,
        title.upper(),
        title_font,
        900
    )

    y_position = 720

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=title_font
        )

        text_width = bbox[2] - bbox[0]
        x_position = (WIDTH - text_width) // 2

        draw.text(
            (x_position + 4, y_position + 4),
            line,
            font=title_font,
            fill=(0, 0, 0, 255)
        )

        draw.text(
            (x_position, y_position),
            line,
            font=title_font,
            fill=(220, 25, 45, 255)
        )

        y_position += 115

    # Channel branding
    brand = "MIDNIGHT FILES"

    bbox = draw.textbbox(
        (0, 0),
        brand,
        font=small_font
    )

    brand_width = bbox[2] - bbox[0]

    draw.text(
        ((WIDTH - brand_width) // 2, 570),
        brand,
        font=small_font,
        fill=(220, 220, 220, 255)
    )

    draw.line(
        (250, 530, 830, 530),
        fill=(180, 20, 35, 180),
        width=3
    )

    draw.text(
        (WIDTH // 2, 1120),
        "SOME STORIES NEVER END...",
        anchor="mm",
        font=get_font(28),
        fill=(180, 180, 180, 220)
    )

    # Dark vignette
    image = image.filter(
        ImageFilter.GaussianBlur(radius=0.5)
    )

    image.save(IMAGE_FILE)

    print("Background created.")


# =====================================
# CREATE SHORTS VIDEO
# =====================================

def create_video():

    print("Rendering vertical video...")

    command = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-framerate", "30",
        "-i", str(IMAGE_FILE),
        "-i", str(AUDIO_FILE),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        "-vf", "scale=1080:1920",
        str(VIDEO_FILE)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg failed: " + result.stderr[-2000:]
        )

    if not VIDEO_FILE.exists():
        raise RuntimeError("Video file was not created.")

    print("Video rendered successfully.")


# =====================================
# YOUTUBE AUTHENTICATION
# =====================================

def youtube_authentication():

    print("Connecting to YouTube...")

    credentials = Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        scopes=YOUTUBE_SCOPES
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    return youtube


# =====================================
# UPLOAD VIDEO TO YOUTUBE
# =====================================

def upload_video(title, story):

    print("Uploading video to YouTube...")

    youtube = youtube_authentication()

    description = f"""
Welcome to Midnight Files.

A terrifying short horror story for those who love
mystery, supernatural encounters and psychological horror.

Watch until the end.

#shorts #horror #scarystories #horrorstories
#midnightfiles #creepy
"""

    body = {
        "snippet": {
            "title": title[:95] + " #Shorts",
            "description": description,
            "tags": [
                "horror",
                "scary stories",
                "horror shorts",
                "creepy stories",
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
        str(VIDEO_FILE),
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
                "Upload progress: "
                + str(int(status.progress() * 100))
                + "%"
            )

    video_id = response.get("id")

    if not video_id:
        raise RuntimeError(
            "YouTube upload returned no video ID."
        )

    print("UPLOAD SUCCESSFUL!")
    print(
        "Video URL: "
        f"https://www.youtube.com/watch?v={video_id}"
    )


# =====================================
# MAIN BOT
# =====================================

def main():

    print("================================")
    print(" MIDNIGHT FILES HORROR BOT")
    print("================================")

    check_settings()

    title, story = ask_gemini()

    print("\nGenerated story:")
    print(story)

    asyncio.run(generate_voice(story))

    create_background(title)

    create_video()

    upload_video(title, story)

    print("\nBOT COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
