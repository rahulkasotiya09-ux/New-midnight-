# Midnight Files — automated horror Shorts

This starter bot runs in GitHub Actions (so your iPhone can be off). It generates an original English horror micro-story with Gemini, narrates it with Edge TTS, builds a vertical 1080x1920 captioned scene video with FFmpeg/Pillow, then uploads it publicly to your YouTube channel.

## What is automated
- 3 daily runs at 9 AM, 3 PM and 9 PM India time (GitHub scheduled jobs can start late).
- Story, title, description and tags.
- English narration, scene cards/captions, MP4 assembly.
- YouTube upload through the official YouTube Data API.

## Important limits
- This first version uses animated-style procedural horror artwork/scene cards, not expensive cinematic AI video clips.
- Gemini API free tier and Edge TTS availability/rate limits can change. Free usage is not guaranteed or unlimited.
- GitHub Actions is free for public repositories. This repository's source code will be public; NEVER put keys or tokens in code. Add them only as GitHub Actions Secrets.
- Check each upload and channel performance regularly. Repetitive, low-effort or mass-produced content may not qualify for YouTube monetization. Keep stories original and review outputs.
- YouTube API uploads require OAuth authorization. The bot publishes public videos, so test on a channel you control.

## iPhone-only setup

### 1) Create a GitHub repository
1. In Safari, sign in at https://github.com.
2. Create a **public** repository named `midnight-files-bot`.
3. Upload all files from this ZIP, including the hidden `.github/workflows/publish.yml` file. GitHub mobile web may hide dot-folders in some upload flows; if so, create the folders and file manually in GitHub's web editor.
4. Open the repository's **Actions** tab and enable workflows if GitHub asks.

### 2) Get a Gemini API key
1. Open https://aistudio.google.com/app/apikey.
2. Create an API key.
3. Keep it private. Add it to GitHub as a repository secret named `GEMINI_API_KEY`.

### 3) Enable YouTube Data API
1. Open https://console.cloud.google.com/ in Safari and create a Google Cloud project.
2. Enable **YouTube Data API v3**: https://console.cloud.google.com/apis/library/youtube.googleapis.com
3. Configure the OAuth consent screen. Add your Google account as a test user while testing. If the app remains in External / Testing mode, refresh tokens may expire after 7 days; publishing the OAuth app may require Google's verification depending on scopes and account.
4. Create an OAuth Client ID. Choose **Web application** and add this authorized redirect URI: `https://developers.google.com/oauthplayground`
5. Save the Client ID and Client Secret. Add them as GitHub repository secrets named `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.

### 4) Get a YouTube refresh token
1. Open https://developers.google.com/oauthplayground
2. Tap the gear icon; enable **Use your own OAuth credentials** and enter the OAuth Client ID and Client Secret.
3. In Step 1, enter this scope: `https://www.googleapis.com/auth/youtube.upload` and authorize the Google account that owns your channel.
4. In Step 2, exchange the authorization code for tokens. Copy the `refresh_token`.
5. Add it as a GitHub repository secret named `YOUTUBE_REFRESH_TOKEN`. Do not share it with anyone.

### 5) Add GitHub Secrets
Repository → Settings → Secrets and variables → Actions → New repository secret.
Create exactly these four secrets:
- `GEMINI_API_KEY`
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

### 6) Test once
1. Open Actions → **Publish 3 horror Shorts daily** → Run workflow.
2. Check the run log. It should create and upload one public Short.
3. If successful, the schedule runs three times daily. GitHub scheduled jobs can be delayed and may be paused for inactive repositories.

## Safety and security
- Never paste API keys, OAuth client secrets, or refresh tokens into chat, issues, commits, or public files.
- If a token is exposed, revoke it in Google Account security settings and create a new one.
- The workflow uploads publicly. For the first test, change `privacyStatus` in `bot.py` from `public` to `private`, run once, inspect the result, then switch back to `public`.

## Troubleshooting
- `429` from Gemini: wait or reduce runs; free-tier limits apply.
- OAuth `invalid_grant`: refresh token expired/revoked; repeat OAuth Playground flow. Testing-mode external consent apps can issue short-lived refresh tokens.
- YouTube upload error: check that YouTube Data API v3 is enabled, OAuth scope is correct, and the authorized Google account owns the intended channel.
- Missing `ffmpeg`: workflow installs it automatically.
