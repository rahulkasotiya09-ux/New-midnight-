import json
import os
import sys
import tempfile
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "1s-AiCiDAd8yjj3OlMKAQO_vQfFfWIcnR")
WORKER_URL = os.getenv("MIDNIGHT_WORKER_URL", "").rstrip("/")
SCHEDULER_SECRET = os.getenv("MIDNIGHT_SCHEDULER_SECRET", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "private")
HISTORY_FILE = Path("uploaded.json")

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]


def get_google_config():
    """Get OAuth credentials from the secured Cloudflare Worker."""
    if not WORKER_URL or not SCHEDULER_SECRET:
        raise RuntimeError(
            "Missing MIDNIGHT_WORKER_URL or MIDNIGHT_SCHEDULER_SECRET GitHub secret."
        )

    response = requests.get(
        f"{WORKER_URL}/internal/config",
        headers={"Authorization": f"Bearer {SCHEDULER_SECRET}"},
        timeout=30,
    )
    response.raise_for_status()
    config = response.json()

    required = [
        "youtube_refresh_token",
        "youtube_client_id",
        "youtube_client_secret",
    ]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(
            "Worker config is missing: " + ", ".join(missing)
            + ". Reconnect Google from the Worker homepage."
        )
    return config


def make_google_services(config):
    credentials = Credentials(
        token=None,
        refresh_token=config["youtube_refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config["youtube_client_id"],
        client_secret=config["youtube_client_secret"],
        scopes=SCOPES,
    )
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    return drive, youtube


def load_history():
    if not HISTORY_FILE.exists():
        return {"uploaded_drive_ids": []}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("uploaded_drive_ids"), list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"uploaded_drive_ids": []}


def save_history(history):
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def choose_next_video(drive, uploaded_ids):
    query = (
        f"'{DRIVE_FOLDER_ID}' in parents and trashed = false "
        "and mimeType contains 'video/'"
    )
    result = drive.files().list(
        q=query,
        pageSize=100,
        orderBy="createdTime asc",
        fields="files(id,name,mimeType,size,createdTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()

    videos = result.get("files", [])
    for video in videos:
        if video["id"] not in uploaded_ids:
            return video

    return None


def download_drive_video(drive, file_id, destination):
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(destination, "wb") as output:
        downloader = __import__(
            "googleapiclient.http", fromlist=["MediaIoBaseDownload"]
        ).MediaIoBaseDownload(output, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def generate_metadata(video_name):
    title = Path(video_name).stem

    title = re.sub(r"\b(?:19|20)\d{6}\b", "", title)
    title = re.sub(r"\[[A-Za-z0-9_-]{11}\]", "", title)
    title = re.sub(r"\bBeamNG\s*\.?\s*drive\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"#shorts", "", title, flags=re.IGNORECASE)

    title = re.sub(r"[_\-]+", " ", title)
    title = re.sub(r"[()\[\]{}]", "", title)
    title = re.sub(r"\s+", " ", title).strip(" -_")

    title = (title or "Funny Gaming Moment")[:95]

    return {
        "title": title,
        "description": (
            "Enjoy this amazing gaming moment!\n\n"
            "#BeamNG #Gaming #Shorts"
        ),
        "tags": ["BeamNG", "BeamNG gameplay", "gaming", "funny moments", "shorts"],
    }

    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is missing; using basic video metadata.")
        return fallback

    prompt = f"""
Create YouTube Shorts metadata for a GTA 5 gameplay clip.
The source filename is: {video_name}
Do not claim events that are not known from the filename.
Return ONLY valid JSON with:
- title: engaging title, maximum 90 characters, no misleading claims
- description: 1-2 short sentences and 3-5 relevant hashtags
- tags: array of 5-10 relevant tags
Do not include markdown fences.
"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent"
    )
    try:
        response = requests.post(
            url,
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(raw)
        title = str(data.get("title", fallback["title"])).strip()[:100]
        description = str(data.get("description", fallback["description"])).strip()
        tags = data.get("tags", fallback["tags"])
        if not isinstance(tags, list):
            tags = fallback["tags"]
        tags = [str(tag).strip().lstrip("#")[:30] for tag in tags if str(tag).strip()]
        return {
            "title": title or fallback["title"],
            "description": description or fallback["description"],
            "tags": tags[:15] or fallback["tags"],
        }
    except Exception as exc:
        print(f"Gemini metadata failed; using fallback. Details: {exc}")
        return fallback


def upload_to_youtube(youtube, video_path, metadata):
    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": "20",
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"YouTube upload progress: {int(status.progress() * 100)}%")

    return response["id"]


def main():
    if not GEMINI_API_KEY:
        print("Warning: GEMINI_API_KEY is not set. Basic titles will be used.")

    config = get_google_config()
    drive, youtube = make_google_services(config)
    history = load_history()
    uploaded_ids = set(history["uploaded_drive_ids"])

    video = choose_next_video(drive, uploaded_ids)
    if not video:
        print("No new video found in the configured Google Drive folder.")
        return

    print(f"Selected Drive video: {video['name']} ({video['id']})")

    suffix = Path(video["name"]).suffix or ".mp4"
    with tempfile.TemporaryDirectory() as temp_dir:
        local_video = str(Path(temp_dir) / f"short{suffix}")
        download_drive_video(drive, video["id"], local_video)

        metadata = generate_metadata(video["name"])
        print(f"Uploading as: {metadata['title']}")
        youtube_id = upload_to_youtube(youtube, local_video, metadata)

    history["uploaded_drive_ids"].append(video["id"])
    history.setdefault("youtube_ids", []).append(youtube_id)
    save_history(history)

    print(f"SUCCESS: https://www.youtube.com/shorts/{youtube_id}")
    print(f"Privacy status: {PRIVACY_STATUS}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
