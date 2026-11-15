
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

    delays = [10, 20, 40, 60, 60]
    last_error = None

    for attempt, delay in enumerate(delays, start=1):

        try:
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

            if response.status_code in [429, 500, 502, 503, 504]:
                response.raise_for_status()

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

            print("Gemini story generated successfully.")

            return data

        except requests.exceptions.HTTPError as exc:

            last_error = exc

            status = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            if status not in [429, 500, 502, 503, 504]:
                raise

        except requests.exceptions.RequestException as exc:

            last_error = exc

        if attempt < len(delays):

            print(
                f"Gemini temporarily unavailable. "
                f"Retry {attempt}/{len(delays)} in {delay} seconds."
            )

            import time
            time.sleep(delay)

    raise RuntimeError(
        "Gemini API failed after multiple retry attempts."
    ) from last_error
