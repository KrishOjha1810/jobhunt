# JobHunt Roadmap

## V3 , Tracker dashboard (next big feature)

Designed for the core user: someone who only gets time on a weekend (Sat/Sun) to apply, so they
can't chase every alert , they need to scan, categorize, and pick the best jobs fast.

Per-user dashboard (private token link, no login, e.g. `/dashboard?token=...`):

**Job log table, one row per job sent**, columns:
- Company name
- Role / category (so jobs are easy to group)
- Date sent
- Direct apply link (so missed-notification jobs are recoverable, apply later)
- Match score (sortable, best-fit first)
- Resume used (dropdown: Backend / Blockchain / FullStack) , so if selected, they know which one landed it
- Applied? (yes/no)
- Heard back? (yes/no)
- Notes (interview dates, contacts)

**Summary stats (top of page):**
- Jobs applied this week / this month
- Responses received (count + rate)

**Filters:**
- By week (this week / this month / all)
- By company
- By role / category

**Principles:** scannable, sortable by best-match, fast to update statuses. Ship this core first,
defer fancier analytics.

## Other future ideas
- LLM resume tailoring per job (V2 is wired, just needs an API key: Groq/Gemini free).
- A real React/Next frontend on Vercel once the dashboard justifies it (API stays on Render).
- WhatsApp at scale via Twilio (CallMeBot is fine for the friends stage).
- Pull the Codex/browser sourcing handoff (LinkedIn/Naukri) into the same pipeline.
