
import os
import json
import re
import subprocess
import datetime
import pathlib
import asyncio
import requests

from PIL import Image, ImageDraw, ImageFont
import edge_tts

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


OUT = pathlib.Path("output")
OUT.mkdir(exist_ok=True)

W, H = 1080, 1920
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]


def ask_gemini():

    key = os.environ["GEMINI_API_KEY"]

    prompt = """Write ONE original, suspenseful English horror story
for a YouTube Short.

Return ONLY valid JSON with keys:
title, narration, description, tags.

Narration must be 115-145 words.
Strong hook in first sentence.
Clear ending/twist.
Advertiser-friendly.
No copyrighted characters or copied plots.
Title <= 65 characters.
Description 1-2 sentences.
Tags must be an array of 5-8 short tags.
Avoid generic clickbait."""

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{MODEL}:generateContent?key={key}"
    )

    response = requests.post(
        url,
        json={
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "temperature": 0.95,
                "responseMimeType": "application/json"
            }
        },
        timeout=90
    )

    response.raise_for_status()

    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]

    raw = re.sub(
        r"^```(?:json)?|```$",
        "",
        raw.strip(),
        flags=re.M
    ).strip()

    data = json.loads(raw)
    data["narration"] = data["narration"].strip()

    return data


def font(size, bold=False):

    candidates = [
        "/usr/share/fonts/truetype/dejavu/"
        + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),

        "/usr/share/fonts/truetype/liberation2/"
        + ("LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf")
    ]

    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def make_background(index, caption):

    im = Image.new("RGB", (W, H), (5, 6, 12))
    px = im.load()

    for y in range(H):
        shade = int(7 + 20 * y / H)

        for x in range(W):
            glow = max(
                0,
                1 - (
                    ((x - W * .72) ** 2 +
                     (y - H * .28) ** 2) ** .5
                ) / 650
            )

            px[x, y] = (
                int(shade + 18 * glow),
                int(shade + 2 * glow),
                int(shade + 8 * glow)
            )

    d = ImageDraw.Draw(im, "RGBA")

    d.ellipse(
        (730 + index * 13, 180, 930 + index * 13, 380),
        fill=(180, 185, 195, 28)
    )

    for j in range(7):
        yy = 900 + j * 105

        d.ellipse(
            (-250 + j * 60, yy, 1250 - j * 30, yy + 250),
            fill=(120, 125, 145, 10)
        )

    base = 1190

    d.polygon(
        [
            (0, base + 300),
            (180, base + 70),
            (360, base + 280),
            (520, base + 30),
            (740, base + 310),
            (900, base + 90),
            (1080, base + 270),
            (1080, 1920),
            (0, 1920)
        ],
        fill=(3, 4, 8, 255)
    )

    d.rectangle(
        (380, base - 30, 760, base + 310),
        fill=(7, 7, 12, 255)
    )

    d.polygon(
        [(340, base - 30), (565, base - 210), (800, base - 30)],
        fill=(4, 4, 9, 255)
    )

    d.rectangle(
        (455, base + 45, 535, base + 145),
        fill=(175, 35, 28, 95)
    )

    d.rectangle(
        (630, base + 45, 710, base + 145),
        fill=(175, 35, 28, 75)
    )

    d.rounded_rectangle(
        (70, 1370, 1010, 1745),
        radius=34,
        fill=(0, 0, 0, 185),
        outline=(170, 25, 35, 150),
        width=3
    )

    f = font(48, True)

    words = caption.split()
    lines = []
    line = ""

    for word in words:
        test = (line + " " + word).strip()

        if d.textbbox((0, 0), test, font=f)[2] > 850:
            lines.append(line)
            line = word
        else:
            line = test

    if line:
        lines.append(line)

    lines = lines[:4]

    line_h = 66
    y = 1450 + (230 - len(lines) * line_h) // 2

    for ln in lines:
        box = d.textbbox((0, 0), ln, font=f)

        d.text(
            ((W - (box[2] - box[0])) // 2, y),
            ln,
            font=f,
            fill=(245, 241, 238, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 255)
        )

        y += line_h

    d.text(
        (70, 70),
        "MIDNIGHT FILES  •  HORROR SHORTS",
        font=font(28, True),
        fill=(205, 65, 70, 230)
    )

    return im


def split_captions(text, count=10):

    words = text.split()

    n = max(
        1,
        min(count, (len(words) + 8) // 9)
    )

    size = (len(words) + n - 1) // n

    return [
        " ".join(words[i:i + size])
        for i in range(0, len(words), size)
    ]


async def create_voice(text, path):
    """Create narration with retries for Edge TTS 429 errors."""

    delays = [10, 20, 40, 60, 60]
    last_error = None

    for attempt, delay in enumerate(delays, start=1):
        try:
            communicate = edge_tts.Communicate(
                text,
                "en-US-GuyNeural",
                rate="-5%",
                pitch="-8Hz"
            )

            await communicate.save(str(path))
            print("Voice narration created successfully.")
            return

        except Exception as exc:
            last_error = exc
            status = getattr(exc, "status", None)

            if status != 429:
                raise

            if attempt == len(delays):
                break

            print(
                f"Edge TTS rate limit (429). "
                f"Retrying in {delay} seconds..."
            )

            await asyncio.sleep(delay)

    raise RuntimeError(
        "Edge TTS is still rate-limited after multiple attempts. "
        "Please try again later."
    ) from last_error


def render_video(story, audio_path, out_path):

    caps = split_captions(story["narration"], 11)

    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ],
        text=True
    )

    duration = float(probe.strip())

    scene_dir = OUT / "scenes"
    scene_dir.mkdir(exist_ok=True)

    concat = OUT / "frames.txt"
    each = duration / len(caps)

    with open(concat, "w") as f:

        for i, cap in enumerate(caps):

            p = scene_dir / f"scene_{i:02d}.png"

            make_background(i, cap).save(p)

            f.write(f"file '{p.resolve()}'\n")
            f.write(f"duration {each:.3f}\n")

        last = scene_dir / f"scene_{len(caps)-1:02d}.png"

        f.write(f"file '{last.resolve()}'\n")

    silent = OUT / "silent.mp4"

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat),
            "-vf", "scale=1080:1920,format=yuv420p",
            "-r", "30",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            str(silent)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(silent),
            "-i", str(audio_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(out_path)
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


def youtube_upload(video_path, story):

    print("Starting YouTube OAuth authentication...")

    refresh = os.environ.get(
        "YOUTUBE_REFRESH_TOKEN", ""
    ).strip()

    client_id = os.environ.get(
        "YOUTUBE_CLIENT_ID", ""
    ).strip()

    client_secret = os.environ.get(
        "YOUTUBE_CLIENT_SECRET", ""
    ).strip()

    print("Refresh token present:", bool(refresh))
    print("Refresh token length:", len(refresh))
    print("Client ID present:", bool(client_id))
    print("Client secret present:", bool(client_secret))

    if not all([refresh, client_id, client_secret]):
        raise RuntimeError(
            "Missing YouTube OAuth GitHub Secrets."
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES
    )

    try:

        creds.refresh(Request())

        print(
            "SUCCESS: Google OAuth authentication completed."
        )

    except Exception as e:

        print("OAUTH AUTHENTICATION FAILED.")
        print("Error type:", type(e).__name__)

        if hasattr(e, "args"):
            print("OAuth error:", str(e).split("', {")[0])

        raise

    try:

        yt = build(
            "youtube",
            "v3",
            credentials=creds
        )

        body = {
            "snippet": {
                "title": story["title"][:100],
                "description": (
                    story["description"]
                    + "\n\n#shorts #horror #scarystories"
                ),
                "tags": story.get("tags", []) + [
                    "shorts",
                    "horror",
                    "scarystories"
                ],
                "categoryId": "24",
                "defaultLanguage": "en"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        req = yt.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(
                str(video_path),
                mimetype="video/mp4",
                resumable=True
            )
        )

        response = req.execute()

        print(
            "Published:",
            "https://youtube.com/shorts/" + response["id"]
        )

    except HttpError as e:

        print("YOUTUBE UPLOAD FAILED:", str(e))
        raise


def main():

    print("Generating horror story...")

    story = ask_gemini()

    stamp = datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y%m%d_%H%M%S")

    audio = OUT / f"voice_{stamp}.mp3"
    video = OUT / f"horror_{stamp}.mp4"

    print("Creating narration...")

    asyncio.run(
        create_voice(story["narration"], audio)
    )

    print("Rendering video...")

    render_video(story, audio, video)

    print("Video created:", video)

    print("Uploading to YouTube...")

    youtube_upload(video, story)

    print("ALL TASKS COMPLETED SUCCESSFULLY.")


if __name__ == "__main__":
    main()
