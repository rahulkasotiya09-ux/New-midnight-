# Midnight Files — website and scheduler package

This package adds a mobile-friendly YouTube connection page, a Cloudflare Worker OAuth backend, and a GitHub Actions schedule to the existing `New-midnight-` repository.

## Files to add to the repository

- `cloudflare/worker.js` — connection website and secure OAuth callback
- `cloudflare/wrangler.toml` — optional Wrangler configuration template
- `.github/workflows/publish-shorts.yml` — 9 AM, 3 PM, 9 PM IST schedule
- `MIDNIGHT_SETUP.md` — setup checklist

The existing `bot.py` and `requirements.txt` are intentionally not replaced.

## Part 1 — Deploy the Worker on Cloudflare

1. Open Cloudflare Dashboard → Workers & Pages → Create → Worker.
2. Name it `midnight-files-connect`.
3. Open the Worker editor and replace its starter code with `cloudflare/worker.js`.
4. Deploy it.
5. Open Worker Settings → Bindings → Add binding → KV Namespace.
6. Create a namespace named `MIDNIGHT_TOKENS`, bind it with variable name exactly `TOKENS`.
7. Open Worker Settings → Variables and Secrets and add these as **secrets**:
   - `GOOGLE_CLIENT_ID` — your Google OAuth Web application client ID
   - `GOOGLE_CLIENT_SECRET` — its client secret
   - `SCHEDULER_SECRET` — a long random secret; create a different one from your Google secret
8. Save and redeploy if Cloudflare asks.

Your Worker address will look like `https://midnight-files-connect.<your-subdomain>.workers.dev`.

## Part 2 — Google OAuth redirect URI

In Google Cloud Console → APIs & Services → Credentials → your Web application OAuth client, add this exact Authorized redirect URI:

`https://YOUR-WORKER-ADDRESS/oauth/callback`

Replace `YOUR-WORKER-ADDRESS` with the real Worker hostname. Do not add a trailing slash.

The OAuth consent screen must include the Google account you use to connect as a test user if the app is still in Testing.

**Important:** Google OAuth apps left in Testing can have refresh tokens expire after 7 days for this YouTube scope. Do not assume scheduled uploads will continue indefinitely until the app's publishing/verification status is resolved in Google Cloud.

## Part 3 — GitHub repository files

Upload these package files into the root of `rahulkasotiya09-ux/New-midnight-` while preserving their folder paths:

- `cloudflare/worker.js`
- `cloudflare/wrangler.toml`
- `.github/workflows/publish-shorts.yml`
- `MIDNIGHT_SETUP.md`

Do not overwrite `bot.py`, `requirements.txt`, or the existing OAuth diagnostic workflow.

## Part 4 — GitHub Actions secrets

In the repository, open Settings → Secrets and variables → Actions → New repository secret. Add:

- `MIDNIGHT_WORKER_URL` — Worker base URL, for example `https://midnight-files-connect.<your-subdomain>.workers.dev`
- `MIDNIGHT_SCHEDULER_SECRET` — exactly the same value as the Cloudflare Worker secret
- `GEMINI_API_KEY` — your Gemini API key

Never put secrets in source files, screenshots, chat messages, or commits.

## Part 5 — Connect YouTube

Open the Worker URL in Safari and tap **Connect YouTube**. Sign in to the Google account that owns your Midnight Files channel and approve the requested YouTube upload permission.

The page will show whether YouTube is connected.

## Part 6 — Verify before enabling unattended uploads

1. In GitHub → Actions, run `Midnight Files Scheduled Shorts` manually once.
2. Confirm the run succeeds and a public Short appears on the correct channel.
3. Only then rely on the cron schedule.

GitHub scheduled workflows can start late. The schedule is 03:30, 09:30, and 15:30 UTC, corresponding to 9 AM, 3 PM, and 9 PM IST.

## Important notes

- The current `bot.py` publishes with `privacyStatus: public`.
- This automation generates one Short per workflow run.
- The package does not itself deploy the Worker or modify your GitHub repository; those steps require your Cloudflare and GitHub account actions.
- GitHub Actions usage limits and Google API quota still apply.
