# Torah Center Browser Copy

This folder is the browser-first hosted copy of the app. The original local Windows app remains untouched in:

- `C:\Users\vctg6\Downloads\parasha-onepager-app`

## What is in this copy

- `cloud_backend/`
  - Flask browser app plus JSON API for login, jobs, uploads, review, editing, history, Hebrew terms library tools, billing, and export
  - SQLite-backed persistence locally, with `DATABASE_URL` support for hosted Postgres
  - OpenAI-backed transcription, review, voice analysis, and article generation
  - translation rules modes: automatic transliteration, manual review, and Hebrew itself
  - server-safe PDF generation without Microsoft Word, including editable header/footer text, PNG download, and custom backgrounds
  - Clerk-ready browser auth with email/password and Google sign-in support when Clerk env vars are configured
  - Stripe-ready one-time unlock and monthly subscription billing when Stripe env vars are configured
  - optional worker mode for hosted deployments
  - DOCX generation using the browser copy's own pure-Python export logic
- `mobile_app/`
  - Expo/React Native client scaffold for a later native-mobile phase

## Quick start

1. Copy `cloud_backend/.env.example` to `cloud_backend/.env`
2. Set your OpenAI API key and admin credentials
3. Install the cloud backend requirements
4. Run:
   - `start_cloud_backend.bat`
5. Open:
   - `http://127.0.0.1:8010`

## Public browser access

To expose the copied app on a temporary public HTTPS URL that works from any browser:

1. Run:
   - `start_public_web_app.bat`
2. Wait a few seconds
3. Open:
   - `public_tunnel.log`
4. Use the `https://...trycloudflare.com` URL shown there

This keeps the OpenAI key on the server side. The browser never receives the key directly.

## Stable hosted deployment

This repo now includes:

- [render.yaml](C:\Users\vctg6\Downloads\parasha-onepager-app-mobile\render.yaml)

Hosted deployment is designed for:

- Render web service
- Render worker service
- Render Postgres
- S3-compatible object storage
- Clerk for auth
- Stripe for billing

To activate the hosted setup, add the required provider env vars in Render:

- `DATABASE_URL`
- `OPENAI_API_KEY`
- `CLERK_PUBLISHABLE_KEY`
- `CLERK_SECRET_KEY`
- `CLERK_FRONTEND_API_URL`
- `CLERK_JWKS_URL`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `S3_BUCKET`
- `S3_REGION`
- `S3_ENDPOINT_URL` if using non-AWS S3
- `MOBILE_APP_SECRET`

Without those provider keys, the local fallback login remains available and billing/auth provider features stay inactive.

Default admin login from the example env:

- email: `admin@torahcenter.app`
- password: set in `cloud_backend/.env`

## Notes

- This new copy keeps ffmpeg/ffprobe server-side for `.mp4` conversion and large-file chunking.
- This copy currently uses OpenAI on the server side for the model steps.
- Completed articles can now be locked behind billing until a one-time unlock or active subscription is present.
- PDF export in this copy no longer depends on Microsoft Word.
- Storage is server-side and shared across devices when this backend is deployed.
- The main usable interface right now is the browser app served by `cloud_backend/`.
- The mobile app scaffold is intentionally deferred while the browser-first hosted version is being stabilized.
- The browser UI is organized into `Article Generation`, `History`, and `Other Tools`.
- The new default product branding is `Torah Center`.
- Do not commit `cloud_backend/.env`; it contains secrets.
