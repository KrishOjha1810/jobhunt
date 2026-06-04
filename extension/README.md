# JobHunt Auto-Apply (Chrome/Edge extension, v2)

A guided **auto-apply flow** in a side panel, not just dumb autofill. On a job application page it:
1. **Detects the ATS** (Greenhouse, Lever, Ashby, Workday).
2. **Autofills your details** (name, email, phone, LinkedIn, GitHub, location) from your JobHunt profile.
3. **Drafts answers to screening questions** ("work authorization", "notice period", "why this company", etc.) with AI, which you review and click into the form.
4. **Generates + pastes a tailored cover note** for the selected job.
5. **Highlights the resume upload box** (you attach the file , browsers block auto-attaching).
6. **Marks the job applied** in your JobHunt tracker in one click.

## What it can't do (browser security)
- It cannot attach your resume file or click the final Submit , both stay manual on purpose, so you never send a half-reviewed application.

## Install (unpacked, ~1 min)
1. Open `chrome://extensions` (or `edge://extensions`).
2. Toggle **Developer mode** on (top-right).
3. **Load unpacked** → select this `extension/` folder.
4. Click the JobHunt icon , the **side panel** opens. Paste your dashboard link or token:
   - Get it from your signup `/dashboard?token=...` link, or open `https://jobhunt-8i1m.onrender.com/me` while logged in and copy `dash_token`.
5. **Save profile.**

## Use it
1. Open a Greenhouse/Lever/Ashby/Workday application page.
2. Open the side panel → **Scan this page**.
3. Pick which of your tracked jobs this is (so AI answers + "Mark applied" target the right row).
4. Work the steps: Autofill → Draft answers → Cover note → attach resume → **Mark as applied**.

## Notes
- AI features (screening answers, cover note) need an LLM key set on the JobHunt server (Groq/Gemini); without it those steps say so and the rest still work.
- Publishing to the Chrome Web Store needs a one-time $5 developer account; until then "Load unpacked" works on your and your friends' machines.
