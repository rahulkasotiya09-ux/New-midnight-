
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


# GOOGLE DRIVE FOLDER
DRIVE_FOLDER_ID = os.getenv(
    "DRIVE_FOLDER_ID",
    "13Xn1e6T1B4GVEoQ5Sx-oZUUCIKKZF9Ii"
)

WORKER_URL = os.getenv("MIDNIGHT_WORKER_URL", "").rstrip("/")
SCHEDULER_SECRET = os.getenv("MIDNIGHT_SCHEDULER_SECRET", "")
PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")

HISTORY_FILE = Path("uploaded.json")

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def get_google_config():
    if not WORKER_URL or not SCHEDULER_SECRET:
        raise RuntimeError(
            "Missing MIDNIGHT_WORKER_URL or MIDNIGHT_SCHEDULER_SECRET."
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

    drive = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    return drive, youtube


def load_history():
    if not HISTORY_FILE.exists():
        return {"uploaded_drive_ids": []}

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))

        if (
            isinstance(data, dict)
            and isinstance(data.get("uploaded_drive_ids"), list)
        ):
            return data

    except (json.JSONDecodeError, OSError):
        pass

    return {"uploaded_drive_ids": []}


def save_history(history):
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# FIND THE NEXT UNUPLOADED VIDEO ACROSS ALL DRIVE PAGES
def choose_next_video(drive, uploaded_ids):
    query = (
        f"'{DRIVE_FOLDER_ID}' in parents and trashed = false "
        "and mimeType contains 'video/'"
    )

    page_token = None

    while True:
        result = drive.files().list(
            q=query,
            pageSize=100,
            orderBy="createdTime asc",
            pageToken=page_token,
            fields="nextPageToken,files(id,name,mimeType,size,createdTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        for video in result.get("files", []):
            if video["id"] not in uploaded_ids:
                return video

        page_token = result.get("nextPageToken")

        if not page_token:
            return None


def download_drive_video(drive, file_id, destination):
    request = drive.files().get_media(
        fileId=file_id,
        supportsAllDrives=True,
    )

    with open(destination, "wb") as output:
        downloader = MediaIoBaseDownload(output, request)

        done = False

        while not done:
            _, done = downloader.next_chunk()


# EXTRACT THE ORIGINAL YOUTUBE VIDEO ID FROM THE DRIVE FILENAME
def extract_youtube_id(video_name):
    stem = Path(video_name).stem

    # Example: Original Title [IcesEhvJwSw].mp4
    match = re.search(r"\[([A-Za-z0-9_-]{11})\]", stem)

    if match:
        return match.group(1)

    # Example: IcesEhvJwSw.mp4
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", stem):
        return stem

    return None


# RETRIEVE THE ORIGINAL TITLE FROM YOUTUBE
def get_original_youtube_title(youtube, video_name):
    video_id = extract_youtube_id(video_name)

    if not video_id:
        print("No YouTube video ID found in filename.")
        return None

    print(f"Looking up original YouTube title for: {video_id}")

    try:
        result = youtube.videos().list(
            part="snippet",
            id=video_id,
        ).execute()

        items = result.get("items", [])

        if items:
            original_title = items[0]["snippet"]["title"]

            print(f"Original YouTube title found: {original_title}")

            return original_title

        print("Original video title was not returned by YouTube.")

    except Exception as error:
        print(f"Could not retrieve original title: {error}")

    return None


# FALLBACK TITLE IF ORIGINAL TITLE CANNOT BE RETRIEVED
def fallback_title(video_name):
    title = Path(video_name).stem

    # Never use a raw 11-character video ID as the title.
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", title):
        return "Epic Car Crash Compilation"

    # Remove a trailing [YouTube ID], if present.
    title = re.sub(r"\[[A-Za-z0-9_-]{11}\]", "", title)

    title = re.sub(r"\s+", " ", title).strip(" -_")

    title = title or "Epic Car Crash Compilation"

    return title[:100]


# ORIGINAL TITLE + 10 CAR CRASH HASHTAGS
def generate_metadata(youtube, video_name):
    title = get_original_youtube_title(youtube, video_name)

    if not title:
        title = fallback_title(video_name)
        print(f"Using fallback title: {title}")

    title = title[:100]

    description = (
        "Epic car crashes, crazy collisions and unbelievable "
        "vehicle destruction! 🚗💥\n\n"
        "#CarCrash #CarCrashes #CrashGames #CarCrashSimulator "
        "#EpicCrashes #CrazyCrashes #CarDestruction "
        "#DrivingSimulator #GamingShorts #Shorts"
    )

    tags = [
        "car crash",
        "car crashes",
        "crash games",
        "car crash simulator",
        "epic crashes",
        "crazy crashes",
        "car destruction",
        "driving simulator",
        "gaming shorts",
        "shorts",
    ]

    return {
        "title": title,
        "description": description,
        "tags": tags,
    }


# UPLOAD TO YOUTUBE
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
            print(
                f"YouTube upload progress: {int(status.progress() * 100)}%"
            )

    return response["id"]


def main():
    print("Starting Car Crash Shorts Automation...")
    print(f"Using Google Drive folder: {DRIVE_FOLDER_ID}")

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

        print("Downloading video from Google Drive...")

        download_drive_video(
            drive,
            video["id"],
            local_video,
        )

        metadata = generate_metadata(
            youtube,
            video["name"],
        )

        print(f"Uploading with title: {metadata['title']}")

        youtube_id = upload_to_youtube(
            youtube,
            local_video,
            metadata,
        )

    history["uploaded_drive_ids"].append(video["id"])

    history.setdefault("youtube_ids", []).append(youtube_id)

    save_history(history)

    print("UPLOAD SUCCESSFUL")
    print(f"Original title: {metadata['title']}")
    print(f"Short URL: https://www.youtube.com/shorts/{youtube_id}")
    print(f"Privacy status: {PRIVACY_STATUS}")


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
