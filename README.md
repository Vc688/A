# Parasha Pamphlet Pipeline (OpenAI API)

This app creates Shabbat pamphlets from recorded Rabbi classes using the OpenAI API.

## What it does
- Receives `.mp3` and `.mp4` uploads.
- Collects Rabbi name and topic before processing begins.
- Transcribes audio with OpenAI.
- Can skip audio entirely by pasting a transcript or a finished pamphlet into the home page.
- Pauses for Hebrew clarification review when the transcript contains unclear Hebrew terms.
- Produces a Rabbi-voice pamphlet around 400 words with OpenAI.
- Uses a local glossary to stabilize recurring Torah terms in transliteration + English.
- Remembers clarified Hebrew terms so they can be reused automatically in future pamphlets.
- Offers a downloadable one-page PDF version with a clean handout layout in Times New Roman.
- Offers a downloadable DOCX version that stays simple and editable.
- Lets you edit the final pamphlet, PDF line spacing, PDF font size, and PDF background choice before download.

## Setup
1. Install Python and app dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Open [C:\Users\vctg6\Downloads\parasha-onepager-app\.env.example](C:\Users\vctg6\Downloads\parasha-onepager-app\.env.example) and mirror it into [C:\Users\vctg6\Downloads\parasha-onepager-app\.env](C:\Users\vctg6\Downloads\parasha-onepager-app\.env).
3. Add your OpenAI API key and keep the default model values unless you want to tune cost/quality.

## `.env` settings
```env
SHUL_NAME=West Deal Shul Torah Center
OPENAI_API_KEY=sk-your-api-key-here
MAX_OUTPUT_WORDS=400
OPENAI_TIMEOUT_SECONDS=1800
TRANSCRIBE_MODEL=gpt-4o-transcribe
TRANSCRIBE_CHUNK_SECONDS=1200
REVIEW_MODEL=gpt-4.1-mini
PAMPHLET_MODEL=gpt-4.1
FFMPEG_EXE=C:\Users\vctg6\Downloads\parasha-onepager-app\ffmpeg\bin\ffmpeg.exe
```

## Run
- Double-click `start_app.bat`
- Or run manually:
  ```bash
  python app.py
  ```

## Notes
- Upload support currently accepts `.mp3` and `.mp4`.
- `.mp4` uploads are converted into `.mp3` locally with `FFMPEG_EXE` before transcription begins.
- OpenAI's transcription endpoint has a 25 MB per-request upload limit, but the app now automatically splits oversized audio into smaller chunks before transcription when `FFMPEG_EXE` is configured.
- Longer shiurim are automatically split into chunks before transcription, using `FFMPEG_EXE` and `ffprobe` if they are available.
- The app automatically retries temporary OpenAI/API gateway errors such as `502 Bad Gateway`.
- Uploaded files are stored in `uploads/`.
- The glossary lives in [C:\Users\vctg6\Downloads\parasha-onepager-app\glossary.json](C:\Users\vctg6\Downloads\parasha-onepager-app\glossary.json).
- Saved clarification memory lives in [C:\Users\vctg6\Downloads\parasha-onepager-app\clarification_memory.json](C:\Users\vctg6\Downloads\parasha-onepager-app\clarification_memory.json).
- Pamphlets are branded by default for West Deal Shul Torah Center.
- The PDF export always stays on one page and uses Microsoft Word on this Windows machine to convert the formatted handout into PDF.
- The PDF layout is intentionally minimal so it preserves more space for the actual pamphlet text. If the content runs long, the export tightens spacing and can shrink text to keep the full article visible on one page.
- On the completed pamphlet screen, you can choose a `default`, `blank`, or `custom upload` PDF background. Custom backgrounds are uploaded from the app and reused for that pamphlet.
- The DOCX export is generated without extra compiled Word dependencies, which keeps it friendlier to this Windows/Python setup.
