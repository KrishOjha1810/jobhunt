# Morning checklist (things only you can do)

Everything we discussed is built and deployed live. These four need your accounts/clicks, so I
left them for you. None are urgent; the app works now (Telegram channel + tracker dashboard).

## 1. Turn ON email alerts (10 min) , the easy signup option you wanted
Email signups already work (users register, get a dashboard), but to actually SEND email we need
mail credentials. Easiest is a free Gmail app password:
1. On your Google account: enable 2-Step Verification (if not already).
2. Go to https://myaccount.google.com/apppasswords , create an app password ("JobHunt").
3. In Render > jobhunt service > Environment, add:
   - SMTP_HOST = smtp.gmail.com
   - SMTP_PORT = 587
   - SMTP_USER = your_gmail@gmail.com
   - SMTP_PASS = the 16-char app password
   - EMAIL_FROM = your_gmail@gmail.com
4. Save (Render redeploys). Email alerts now work. Until this is done, email-channel users
   won't receive messages (Telegram users are unaffected).

## 2. Sign yourself up (2 min)
Open https://jobhunt-8i1m.onrender.com , sign up with the Telegram channel and your chat id
1460934377 (Telegram works right now without any extra setup). You'll get a private dashboard
link, bookmark it, that's your tracker.

## 3. Rotate the Neon password (2 min)
It was pasted in chat, so reset it: Neon dashboard > Reset password > copy the new connection
string > update DATABASE_URL in Render > Environment > Save.

## 4. (Optional, later) True one-click Telegram
Right now Telegram needs the chat-id step. A one-tap "Connect Telegram" needs JobHunt to have its
OWN bot (the current bot is shared with your other agent, so they'd conflict on polling). When you
want it: create a new bot via @BotFather, give me the token, and I'll wire one-click connect.

---

## What's live right now
- Public app + signup: https://jobhunt-8i1m.onrender.com
- Per-user tracker dashboard: shown after signup (private token link)
  , job table (company, role, date, apply link, match score), applied + heard-back toggles,
    resume-used dropdown, notes, week/month stats, filters by week/company/role
- Channels: Email (needs step 1), Telegram (works now), WhatsApp (CallMeBot)
- Matcher: twice daily via cron-job.org, up to 20 best-match jobs, deduped
- DB: Neon Postgres | Code: github.com/KrishOjha1810/jobhunt
