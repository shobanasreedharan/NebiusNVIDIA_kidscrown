# KidsCrown implementation plan and repo status

Last updated: 2026-10-11

## Overview

KidsCrown is a family-focused video policy assistant for YouTube. A parent defines plain-language rules for a child, and the app decides whether a video should be allow, ask parent, or block based on the title/description, transcript evidence, and recent parent overrides.

This repo is no longer a planning-only prototype. It is a live implementation with a FastAPI backend and a React + Vite frontend. Since 2026-10-03 there is also a sideloaded Android prototype (an accessibility service plus an enforcement overlay) that watches the YouTube app, asks the backend for a decision, and covers the screen when a video is blocked or needs a parent.

## Hackathon requirements and submission checklist

Source: the official Devpost rules for the Nebius x NVIDIA Global AI Hackathon (read 2026-10-07). Re-check the rules page before submitting; the sponsor may amend them.

### Key dates

- Submission deadline: **Friday, October 30, 2026, 10:00 am Pacific (noon Central).** Treat the evening of Oct 29 as the real deadline.
- Judging period: Dec 1 to Dec 15, 2026. Winners are announced on or around Jan 11, 2027.
- The project must stay available, free and unrestricted, for testing until the judging period ends (Dec 15).

### Track and prizes

- Track: **Best Apps and Agents** (Nemotron models on Nebius Token Factory).
- A project is eligible for one Overall Award OR one Track Award, plus one Bonus Award.
- Bonus worth targeting: **Best Use of Tavily ($3,000)**, awarded to submissions that make a functional runtime call to the Tavily API. KidsCrown uses Tavily extract as its primary transcript source, so make that prominent in the README and description.
- Most Valuable Feedback (NVIDIA swag pack) goes to submissions that complete the feedback section.

### Judging

Stage one is pass/fail: the project reasonably fits the track and reasonably uses the required APIs. Stage two scores four equally weighted criteria:

1. Technological implementation: how well it is built and how effectively it uses Nebius Token Factory / AI Cloud and NVIDIA Nemotron or other NVIDIA open models.
2. Design: a complete, coherent product experience, not just a technical proof of concept.
3. Potential impact: a credible, specific case for a real problem and audience, shown in the demo.
4. Quality of the idea: a creative, non-obvious use of the models, and understanding of the problem space.

Judges are not required to test the project or watch more than three minutes of video, so the video and the text description may be all they see.

### Submission checklist

- [ ] Working hosted demo URL (HTTPS), with test login credentials in the instructions, kept running through Dec 15.
- [ ] Public GitHub repo with a detectable open-source license file (MIT, Apache 2.0 or MPL 2.0). The repository tree below does not list one; confirm or add it.
- [ ] README with setup and run instructions that says how Nemotron, Token Factory and any other Nebius tools are used, and where Tavily is used.
- [ ] Text description of the features and functionality.
- [ ] Demo video under 3 minutes, public on YouTube, showing the project running on the device it was built for (a phone browser for the web app, and the phone for the Android prototype). No third-party trademarks or copyrighted material without permission.
- [ ] Track identified on the form (Best Apps and Agents).
- [ ] Written feedback on Nebius Token Factory / AI Cloud and the NVIDIA tools and models used.
- [ ] Any third-party API or data used must be used within its terms. Disclose the unofficial `youtube-transcript-api` fallback in the README.
- [ ] No secrets in the repo. If the Android project is committed, remove the demo credentials in `ProbeConfig` first.

### Demo-video caution

Recording third-party videos (for example a MrBeast video) inside the YouTube app shows someone else's content, name and the YouTube logo. Use test videos you made or uploaded yourself on an unlisted test channel, and consider asking support@devpost.com how strictly the trademark rule is read.

## Verified project status

The backend is currently in a good working state. Verified on this machine with:

```powershell
py -3 -m pytest tests -q
py -3 -m pytest evals -q
py -3 -m pytest evals/test_deterministic.py -q
```

Results:

- Backend: **106 passed** with 1 warning (2026-10-11). This includes YouTube timeout handling, Redis breaker behavior, policy/input boundaries, request-size enforcement, and family rate limiting. The warning is a Starlette deprecation notice about `httpx` in the test client.
- Eval suite: 59 passed, 1 skipped, 0 xfailed; deterministic eval module 24 passed (last run 2026-10-02, not re-run since). The one skipped eval is the optional live injection test.

The eval dataset currently has 29 cases: 24 concrete video IDs and 5 `FILL_ME_IN` placeholders. Its buckets are clean, gaming, prank, adversarial, family_rules, policy_swap, no_transcript, and injection; expected statuses include allow, ask_parent, and block.

### Verified end to end against the live YouTube API (2026-10-07)

`POST /identify` was called with the demo account (profile 2):

| Video | Result |
|---|---|
| MrBeast "Eat Everything In A Grocery Store, Win $1,000,000" | identified, `high` match; `block`, decided by a channel rule (confidence 1.0, model not called) |
| Tiko "I LOST FISHY!" | identified, `high` match; `ask_parent` at 0.87 through the full model path (transcript available) |
| Tiko "SECRET FISHY" (from the phone) | identified, `medium` match; `ask_parent` at 0.78 through the model path |

On the phone (vivo V2225, Android 15) the full chain was seen working: screen read, `/identify` call, decision. The enforcement overlay compiles (`gradlew assembleDebug`) but has **not yet been tested on the device**.

## What is implemented

### Backend

The backend under `app/` includes the real application logic:

- auth and JWT validation in `app/auth.py`
- SQLAlchemy models in `app/models.py`
- FastAPI routes in `app/main.py`
- Nebius model client and JSON validation in `app/nebius.py`
- transcript source chaining in `app/transcript.py`
- same-day Redis cache in front of Tavily in `app/redis_cache.py`
- policy hashing and family/child policy combination in `app/policy.py`
- age-band templates in `app/policy_templates.py`
- queue, feedback, and classification orchestration in `app/service.py`
- on-demand title/channel lookup in `app/video_context.py`
- YouTube Data API access, including video identification, in `app/youtube.py`

Key behaviors that are implemented in code:

- parent registration and login with hashed passwords
- one parent per family and family-scoped ownership checks
- 1 to 5 children per family
- age-based policy templates with optional custom overrides
- family + child policy combination with child precedence
- configurable policy limits (2,000 chars each for family/child, 200 for channel notes, 100 blocked words of 50 chars each) and a 4,500-character combined-policy guard
- 64 KiB default request-body cap and a 30-per-hour per-family shared `/classify` + `/identify` rate limit (in-memory per process)
- deterministic channel allow/block rules checked before the model call
- YouTube metadata lookup via the official API
- transcript fetching through Tavily first, then youtube-transcript-api fallback
- a Redis cache in front of Tavily with same-day expiry, so a retry later the same day does not pay Tavily twice for the same video (not yet exercised against a real Redis, because Docker is not installed on the dev machine)
- cache entries for metadata, transcript, and classification results
- Ask Parent queue and parent feedback resolution
- safety net for missing transcripts: allow becomes ask_parent with reduced confidence, block stays block
- **video identification from on-screen text** (`POST /identify`, described below)

#### `POST /identify`

Takes `profile_id`, `title`, `channel` and an optional `duration_seconds` (what the phone read off the screen), resolves the YouTube video ID, then runs the normal classification. Returns `identified`, `video_id`, `match_confidence` (`high` or `medium`) and the usual classification. `identified: false` means the client must treat the video as unknown (ask parent), never as allowed.

Implementation: `YouTubeClient.identify_video` in `app/youtube.py`.

- It searches by title and channel with **no** safeSearch or embeddable filter. The video is already playing, so hiding it from the match would defeat the purpose (`search_videos` keeps its strict filters for the web app's search).
- A result is a candidate only if its title and channel name both equal the inputs after normalization (lowercase, punctuation collapsed, HTML-unescaped).
- A `videos.list` call then reads the candidates' durations. A candidate within 2 seconds of the reported duration gives `high`. A single candidate with no duration match gives `medium`. Several candidates with no duration match is ambiguous and returns no match.
- A wrong or missing duration never rejects a single candidate. The phone's duration reading is unreliable (see the Android section).
- Successful matches are cached in memory for the life of the process; failures are not cached.
- Quota: `search.list` costs 100 units and `videos.list` 1 unit, against a default budget of 10,000 units per day. That allows roughly 100 new identifications a day, shared with the web app's search.

### Frontend

The frontend under `frontend/` is a React + Vite application with the main parent workflow built:

- sign in and register
- family onboarding
- profile management and child setup
- policy and channel rule editing
- video classification result UI
- Ask Parent queue flows
- mobile-first responsive layout

### Android prototype (KidsCrownProbe)

A sideloaded Android proof of concept (decision of 2026-10-03). Project name KidsCrownProbe, package `com.kidscrown.probe`, built in Android Studio and run on a physical vivo V2225 (Android 15). Files:

- `KidsCrownService.kt`: the accessibility service that reads the YouTube app, tracks the current video, calls the backend, and decides which overlay to show
- `BackendClient.kt`: `ProbeConfig` (backend URL, demo login, profile ID, settle time) and `BackendClient` (login, `/identify`, automatic re-login on 401)
- `EnforcementOverlay.kt`: the full-screen cover (Checking, Blocked, Ask a parent) with a "Go back" button
- `MainActivity`: shows the latest snapshot

#### What the accessibility tree exposes

Findings from the on-device screen tests (2026-10-07):

| Screen | Result |
|---|---|
| Normal watch page | Title, channel handle (for example `@MrBeast`), channel display name. Duration only while the player controls are visible. No video ID or URL. |
| Fullscreen | Title not exposed, but detectable (`desc:Exit fullscreen` marker; flickers between states) |
| Miniplayer | Title not exposed, but detectable (`miniplayer_*` markers) |
| Shorts | Detectable through `reel_recycler` / `reel_watch_fragment_root`. Whether a title or channel is exposed is still unknown. (`reel_time_bar` appears on every screen and must not be used.) |
| Home feed, search results | Not yet tested |
| Home button press | Not yet tested (which package the launcher reports is unknown) |

Other findings:

- The duration reading flips between null and the real value, and a wrong small value (`0:15`) was seen, so the service keeps the largest value seen for a video.
- `com.vivo.card` fires window events while YouTube is in use. Treating them as "left YouTube" reset the current video during fullscreen, which would have let a blocked video escape enforcement. It is now ignored.
- The active window can belong to another app (for example a Chrome custom tab), so the service checks that the root window is YouTube's before reading it.

#### Service design (as built)

- One **current video** is kept. It survives fullscreen, the miniplayer, the endscreen and the feed. It resets on a new `watch_list` title, on entering Shorts, or when another app's window takes over.
- Tree walks are throttled to one per second. The backend is called once per video, after the title has been on screen for 2 seconds and the channel name is known. A decision cache (title to status) removes the "Checking" flash for repeat videos.
- Backend failures retry up to 3 times, 10 seconds apart, then **fail closed** to `ask_parent`. A late decision is ignored if the video has changed.
- Overlay mapping: no status yet shows Checking, `block` shows Blocked, `ask_parent` shows Ask a parent, `allow` shows nothing. Overlays are only drawn on the watch, player and miniplayer screens.
- When an overlay appears the video is paused with a media key. It resumes only if the video then turns out to be allowed.
- "Go back" tries to end the video: click the miniplayer close button, otherwise press Back, up to 4 attempts, then Home.
- A watchdog removes a stuck overlay if another app takes focus or the window is unavailable for more than 10 seconds. `OVERLAY_ENABLED` is a kill switch. `adb shell am force-stop com.kidscrown.probe` is the emergency exit.
- The overlay shows only fixed, child-facing text. The backend's explanation (written for the parent) is never shown to the child.

#### Running the probe against a local backend

- The phone reaches the PC's uvicorn over USB with `adb reverse tcp:8000 tcp:8000`, and `ProbeConfig.BACKEND_URL` is `http://127.0.0.1:8000`.
- The manifest needs the `INTERNET` permission and `android:usesCleartextTraffic="true"` (plain HTTP).
- After every reinstall, switch the accessibility service off and on in Settings, because Android can leave it listed as enabled but not bound.

## Current gaps and product status

The codebase is substantial and much of the backend is done, but the project still has real gaps before it is polished for demo / deployment.

Web app and deployment:

1. The combined Main view queue across all children is not yet fully finished as a final polished experience.
2. The demo strip and preloaded example videos need finishing.
3. Deployment documentation and production secrets handling should be tightened before public hosting. A hosted HTTPS backend that stays up through Dec 15 is now a submission requirement, not an extra.
4. Licensing and submission materials (license file, README sections for the rules, feedback text, demo video) are not done.

Android prototype:

5. The overlay has not yet been tested on the device. Pending tests: block, ask and allow videos; fullscreen and miniplayer; Go back; Home press; the watchdog; backend-down fail-closed behavior.
6. Home feed and search-result screens are untested. If they carry the `watch_player` marker, a leftover block status could cover the whole feed after a blocked video is closed. Decide the fix after seeing the log.
7. Shorts are not enforced, and what text Shorts expose is unknown.
8. A video that opens straight in fullscreen (no title ever readable) is not handled. An "unknown video" overlay is left as a TODO until the feed tests are done.
9. Ask a parent is a dead end on the phone. Nothing yet listens for the parent's answer.
10. Whether the media-pause key really stops YouTube playback and audio is not yet confirmed. The fallback is a click on YouTube's own play/pause button or muting the stream.
11. Four small code fixes are queued: set `ok = true` only after the response is parsed; set the overlay's `current` only after the window is added; do not cache the fail-closed status; use dp instead of pixels for overlay padding.
12. Parent approval needs a backend feature: time-limited approvals (Allow once, Deny, and optionally Allow 30 min). The backend currently only records overrides.

Product and messaging:

13. GitHub Pages / browser-extension / full kid-mode work remains out of scope. The Android prototype is in scope as a proof of concept.
14. The product is a decision layer, not a full YouTube interception system. The Android prototype shows enforcement on one device by drawing an overlay: it is sideloaded, watches the YouTube app only, and Google Play restricts accessibility apps. Say this plainly in the README and in any demo messaging, and never claim blanket interception.

## Plan to the deadline (proposed, 2026-10-07)

SmartCart's Amazon deadline (Oct 23) is the first priority and overlaps this work, so KidsCrown realistically gets about 8 to 10 working days.

| Dates | Work |
|---|---|
| Oct 7 to 10 | Test the overlay on the phone; run the untested screens (Home press, Shorts, feed and search taps); apply the four small fixes |
| Oct 11 to 14 | Parent approval: backend time-limited approvals, web Ask Parent queue buttons, phone polls for the answer |
| Oct 15 to 23 | SmartCart first. KidsCrown bug fixes only. |
| Oct 24 to 27 | Demo data and preloaded videos, combined queue polish, hosted HTTPS backend with real secrets, license, README, mobile-viewport check, a 30-minute battery-manager test on the vivo |
| Oct 28 to 29 | Record the demo video (under 3 minutes), write the description and feedback, submit by the evening of Oct 29 |
| Oct 30 | Spare. The form closes at 10:00 am Pacific. |

If time gets tight, cut in this order: Allow 30 min (keep Allow once and Deny); a per-child Shorts setting (treat all Shorts as ask parent); the "unknown video" overlay. The web app carries the Design and Impact criteria, so do not let the Android work use up the days reserved for hosting, README and the video.

Optional upside, only after the checklist is done: use a model tier (Nano for fast calls, Super or Ultra for ambiguous or adversarial cases) to address the adversarial gap and the sampling variance already seen in Nemotron outputs. Deploying the backend on Nebius Serverless is encouraged by the track description but not required.

## Repository structure

```text
app/
  auth.py
  config.py
  db.py
  main.py
  models.py
  nebius.py
  policy.py
  policy_templates.py
  redis_cache.py
  schemas.py
  service.py
  transcript.py
  video_context.py
  youtube.py
frontend/
  src/
  package.json
scripts/
  seed_demo_family.py
  ...
requirements.txt
tests/
  test_identify.py
  ...
README.md
nebius-hackathon-kidscrown-plan.md
```

The Android project (KidsCrownProbe) is a separate Android Studio project. Decide whether it is committed to this repo; if it is, remove the credentials in `ProbeConfig` first. Add a license file at the repo root.

## Setup

### Python

```powershell
cd "c:\Users\ramne\IdeaProjects\kidscrown\kidscrown"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Environment variables

Add a `.env` file at the repo root with the following:

```env
NEBIUS_API_KEY=...
YOUTUBE_API_KEY=...
TAVILY_API_KEY=...
JWT_SECRET=replace_with_a_32_plus_character_secret
CORS_ORIGINS=http://localhost:5173
```

Optional:

```env
JWT_EXPIRE_MINUTES=1440
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/
NEBIUS_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B
REQUEST_BODY_MAX_BYTES=65536
CLASSIFY_IDENTIFY_REQUESTS_PER_FAMILY_PER_HOUR=30
```

### Run the backend

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Run the frontend

```powershell
cd frontend
npm install
npm run dev
```

Open the app at `http://localhost:5173`.

## Demo account

A seeded demo family exists for quick walkthroughs:

- Email: `demo@kidscrown.local`
- Password: `DemoPass123!`

Create or refresh it with:

```powershell
.\.venv\Scripts\python scripts\seed_demo_family.py
```

The demo family's first child is profile 2, which the Android probe uses, and that family has the MrBeast channel on its block list (so that video returns `block` without a model call).

## Implementation notes and design decisions

### Policy layering

The effective model policy is the combination of the family policy and the selected child policy. Child rules take precedence when they conflict, which is encoded in `combine_policies()`.

### Cache integrity

Classification cache keys include profile ID, video ID, the combined effective-policy hash, profile-specific channel-rule state, and a SHA-256 digest of the exact profile-scoped recent feedback context sent to the model. The feedback list retains its newest-ten limit and ordering. The A → B → A policy cycle is guarded so a cache hit returns the row for the matching effective policy.

### Transcript ambiguity

The transcript system is a deliberate engineering tradeoff. Tavily is fast and avoids the worst IP-blocking issues, but a Tavily `None` result is treated as ambiguous rather than a confirmed absence. The fallback provider is only allowed to confirm a genuine transcript gap.

### Safety net

When no transcript is available, the app still classifies using the available metadata. In that situation:

- allow becomes ask_parent with confidence capped at 0.69
- ask_parent stays ask_parent
- block stays block

This ensures missing evidence never makes a clearly disallowed video look safer.

The Android client follows the same principle: an unidentified video is treated as ask_parent, and a backend that cannot be reached after three attempts also fails to ask_parent. Missing evidence never makes a video look safer.

### Eval runner status

The offline eval suite covers channel-rule decisions and profile scope, transcript-provider fallback and failure behavior, model-output validation, and cache behavior for policies, profiles, and feedback. The current eval result is 59 passed, 1 skipped, 0 xfailed; the deterministic module has 24 passing tests.

`evals/run_evals.py --dry-run` reports the workload (14 live-capable rows in the current 29-case dataset) and makes no remote calls. Live evaluation is not implemented: `--live` reports this limitation and exits with status 3. The runner does not write reports; `--compare` currently prints the timestamp from an existing report without computing metric differences. Thresholds are not evaluated during dry-run because no actual case results exist.

### Latency

Measured so far:

- On the phone, a channel-rule block arrived about 3 to 5 seconds after the video appeared (this includes the 2-second settle time and the identification search).
- A cold model path (transcript fetch plus Nemotron) took about 13 seconds.
- A fully cached `/identify` call took about 0.83 seconds when called from PowerShell. The health and profile endpoints take about 0.02 seconds, so the time is spent inside `/identify`. The cause is not yet isolated; timing the identify, classify and metadata steps separately would show whether classification or the metadata lookup is paying a network call on a cache hit.

Design consequence: the overlay appears as soon as a new video is seen and holds the video until the decision arrives, so the child is never watching while the check runs. Preloading and caching the demo videos keeps the cold path off the recorded demo.

## Risks and open questions

- Overlay behavior on the Home feed and search results (see gaps 5 to 7); the media pause key; Shorts text.
- The vivo battery manager may stop the service. Needs a longer test and, if required, an "unrestricted battery" setting.
- Model explanations can be over-sensitive: the first Tiko video returned an explanation citing a self-harm claim that may be a misreading of game-style talk. Review the transcript and the effective policy before the demo, because the explanation text is what a parent sees.
- Nemotron output is not fully deterministic across runs with identical input.
- Hackathon credits ($25 Nebius) and the YouTube and Tavily quotas must last through Dec 15 while judges test. Keep the demo videos cached and consider rate limits on the hosted backend.
- The unofficial transcript fallback is a terms-of-service gray area. Tavily is the primary source; disclose the fallback.
- Demo-video content (third-party trademarks and copyrighted video), as described above.
- Redis caching has not been tried against a real Redis.

## Recommended next move

The web app and the submission package carry most of the judging weight, and the Android prototype should be a short, reliable demo segment on top. Immediate order of work:

1. Test the overlay on the phone (block, ask, allow, feed and search screens, Home, watchdog, fail-closed) and apply the four small fixes.
2. Add time-limited parent approvals (backend, web Ask Parent queue, phone polling).
3. After the SmartCart deadline: seed and document demo data, finish the combined queue and child-main view polish, deploy the app behind HTTPS with real secrets, and validate the end-to-end flow in a browser on a mobile-sized viewport.
4. Add the license, finish the README and feedback text, record the demo video with your own test videos, and submit by the evening of Oct 29.

## Bottom line

This repo is already a viable policy-agent MVP: family auth, child profiles, channel rules, transcript-aware classification, Ask Parent loop, and frontend flows are all present, and the backend can now identify a video from on-screen text and decide it end to end on a real phone. The Android enforcement overlay is built but still needs device testing. The remaining work is mostly overlay testing, parent approval, hardening, hosting, and the submission package (hosted demo, license, README, video, feedback), not foundational feature creation.
