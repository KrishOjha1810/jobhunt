# JobHunt Autofill (Chrome/Edge extension)

Autofills job-application forms (Greenhouse, Lever, Ashby, Workday, and most ATS) with your JobHunt
profile, so you spend the weekend applying, not retyping your name and email 40 times.

## What it does
- Pulls your profile from JobHunt (`/api/profile`): name, email, phone, LinkedIn, GitHub, location,
  years, top skills.
- On any application page, click **Fill this page** and it populates the matching text fields.
- A **Copy a tailored cover note** button puts a short note on your clipboard.

## What it can't do (browser security, not a bug)
- It cannot auto-attach your resume file. Browsers block setting file inputs programmatically, so
  you click "Upload resume" yourself. Everything else is filled.
- Always review before submitting; field detection is heuristic.

## Install (unpacked, ~1 min)
1. Open `chrome://extensions` (or `edge://extensions`).
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked** and select this `extension/` folder.
4. Click the JobHunt icon, paste your dashboard link or token (from your signup confirmation /
   `/dashboard?token=...`), and **Save profile**.
5. Open a job application, click the icon, **Fill this page**.

## Publishing later
To put it on the Chrome Web Store you need a one-time $5 developer account. Until then, "Load
unpacked" works on your own machine and your friends' machines.
