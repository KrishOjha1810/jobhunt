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

## Parked , AI mock interview practice mode (later, not now)

Closes the loop: find the job, prepare for that exact job, apply. After JobHunt matches a user to a
role, they can practise for it. Parked as a future feature; no build yet.

1. **AI interviewer by voice.** Asks questions built from the job description and the user's resume,
   with real-interviewer follow-ups ("why that approach?", "how did you do it in your project?").
2. **Open hints during practice.** Prompts like "use STAR" or "mention complexity" that fade out over
   rounds so the user learns to answer unaided.
3. **Feedback after each answer.** What was good, what was missing, a model answer, plus length,
   filler words and pace.
4. **Weak-topic tracking.** Spaced repetition re-asks the topics they fumbled.
5. **Peer mode.** A colleague plays interviewer; the tool gives the candidate feedback afterwards.

**Reuse:** Dictator/voicebridge stacks already give local STT (Parakeet/Whisper), TTS (Kokoro) and a
local LLM (Qwen3 4B via llama.cpp), so the voice loop is mostly reuse.

**Out of scope (deliberately):** no stealth/"undetectable" features, no hiding from screen share, no
in-interview overlays. That is the interview-cheating case and we are not building it. Practice only,
used openly.
