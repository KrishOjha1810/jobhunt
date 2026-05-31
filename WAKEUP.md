# Morning report , overnight build

All live at https://jobhunt-8i1m.onrender.com, pushed to GitHub, each change tested before deploy
so the live app never broke. Existing dashboard token links still work (no one locked out).

## Shipped tonight (in order)

### Capacity , now safely handles 15-20 friends
- **Fetch-once-match-many:** the job pool is fetched ONCE per run and every user is matched against
  it (was fetched per user). 20 users now cost the same API calls as 1. This was the main blocker.
- **One digest per user per run** instead of one message per job (keeps us under Gmail's ~500/day
  and Telegram/WhatsApp flood limits).
- **DB index** on job_log so dedup lookups stay fast as the log grows.
- **Per-user error isolation:** one bad user/source can't abort the whole run.
- Match cap set to 10 (fits a clean digest).

### Matching quality
- **0-100 fit score with a reason.** Replaced the meaningless raw count. Each match now shows e.g.
  "Strong fit (88/100). Matches 9 of your skills: rust, node, blockchain..." Sorted best-first.
- (Earlier: India city/location matching, fuzzy dedup, seniority deprioritization.)

### Login + Google OAuth
- **Email + password accounts** with sessions (pbkdf2-hashed passwords). Sign up sets a password
  and logs you in; returning users log in at /login.
- **Dashboard is now behind login**, with the existing private token links kept as a fallback so
  nothing breaks.
- **"Sign in with Google"** is built and wired; it stays hidden/inert until you add Google
  credentials (see below). When added, it just works.
- Secure random session key by default; Render auto-generates a persistent one.

### Security (from the tester agent, earlier tonight)
- Fixed dashboard URL/quote XSS, signup keyword XSS, resume filename path-traversal, email/channel
  validation, applied/responded clamping, url-less job dedup.

## Needs YOU (the "intervention required" items, all optional, app works without them)
1. **Email alerts:** add Gmail SMTP creds to Render (10 min). Steps below.
2. **Google sign-in:** create an OAuth app in Google Cloud Console, set the redirect URI to
   `https://jobhunt-8i1m.onrender.com/auth/google/callback`, then add GOOGLE_CLIENT_ID and
   GOOGLE_CLIENT_SECRET in Render > Environment. The Google button then appears automatically.
3. **Rotate the Neon password** (it was pasted in chat earlier).
4. **Confirm SECRET_KEY is set in Render** (the blueprint auto-generates it on sync; if sessions
   log you out after a redeploy, add a fixed SECRET_KEY env var).

### Gmail SMTP (turn on email alerts)
Google account > 2-Step Verification on > https://myaccount.google.com/apppasswords > create one.
In Render > jobhunt > Environment add: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER=your_gmail,
SMTP_PASS=the app password, EMAIL_FROM=your_gmail. Save.

## Deferred (bigger or needs a key) , the next high-value work, all in IDEAS.md
- **Semantic embedding matching** (the real quality leap; needs a free Gemini key, then it ships).
- **LLM resume parsing** (replace the fixed skill vocab; needs the same key).
- **Salary surfacing + filters** (Adzuna/JSearch already return salary, just not displayed yet).
- **Mobile-card dashboard + weekend triage view** (I avoided a big UI rewrite I couldn't visually
  verify overnight; safe to do together when you can eyeball it).
- **Browser-extension one-click apply** (the killer feature per the research).

## Your dashboards (log in, or use the token link)
- Telegram account: https://jobhunt-8i1m.onrender.com/dashboard?token=emlUbLUwXpmQuG1l-lIzag
- Or just go to https://jobhunt-8i1m.onrender.com/login (set a password by signing up again, or
  tell me to set one for your account).

Code: github.com/KrishOjha1810/jobhunt. Strategy in PRODUCT.md, full backlog in IDEAS.md.
