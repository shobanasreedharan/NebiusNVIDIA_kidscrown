# KidsCrown frontend

React + Vite web app for KidsCrown. It runs in a browser (phone or computer) and talks to the FastAPI backend. Built mobile-first.

## Run it

You need Node.js 22 or newer, and the backend running (`uvicorn app.main:app --reload`, default `http://localhost:8000`).

```
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The dev server is pinned to port 5173 because that is the backend's default `CORS_ORIGINS`. If the backend runs somewhere else, copy `.env.example` to `.env.local` and set `VITE_API_URL`.

## Test and build

```
npm test          # 50 tests, no backend needed
npm run build     # production build in dist/
```

## What's here

| Path | Purpose |
|---|---|
| `src/api.js` | Backend client: every endpoint, bearer token, plain-language errors |
| `src/auth.jsx` | Sign-in state; verifies a saved token before trusting it |
| `src/family.jsx` | The family's children and which view is selected ("Main" or a child) |
| `src/components/` | Top bar, view switcher, parent menu, route guards, verdict badge |
| `src/pages/` | Sign in, register, onboarding, add a child, home (Main and child views), profile (account, family, children) |
| `src/pages/child/` | One child's screens: Check a video, Ask parent, Rules |
| `src/components/AddChildForm.jsx` | Name, age, and suggested rules; used by onboarding and Add a child |
| `src/queue.js`, `src/rules.js` | Data hooks for a child's Ask parent list and channel rules (shared across tabs) |
| `src/__tests__/` | API client and app-flow tests |

## Notes

- The token is kept in `localStorage` and sent as `Authorization: Bearer`. That needs no CSRF protection, but script running on the page could read it; an httpOnly cookie avoids that at the cost of CORS-with-credentials and CSRF handling. Worth one line in the main README.
- The selected view is remembered per browser and checked against the family's children on load; a child that no longer exists falls back to Main.
- Text marked "Shell build" appears only in development and marks screens still to come.
