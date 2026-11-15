# Midnight Files setup quick reference

1. Deploy `cloudflare/worker.js` as a Cloudflare Worker.
2. Bind a Cloudflare KV namespace as `TOKENS`.
3. Add Worker secrets: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SCHEDULER_SECRET`.
4. Add the Worker `/oauth/callback` URL as an authorized redirect URI in Google Cloud.
5. Add the GitHub workflow and set GitHub Actions secrets:
   - `MIDNIGHT_WORKER_URL`
   - `MIDNIGHT_SCHEDULER_SECRET`
   - `GEMINI_API_KEY`
6. Open the Worker URL and connect YouTube.
7. Manually run the GitHub Actions workflow once to test.

See `README.md` for detailed steps and the Google OAuth Testing-mode warning.
