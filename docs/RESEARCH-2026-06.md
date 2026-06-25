# JobHunt sourcing + competition research (June 2026)

Decision-oriented summary of the research that drove this sprint. Full reasoning + citations were in
the working session; this is the actionable distillation.

## Positioning (the wedge to own)
**"Your resume's personal job scout for India."** Free, resume-matched daily shortlist of real Indian
(+ remote) jobs pushed to Telegram/email, with one-tap tracking. Deliberately **no auto-apply**
(employers blacklist spammy auto-appliers; that's the anti-brand). Segment: early-career + non-dev
candidates in tier-1/tier-2 India who live on Telegram, drowning in untargeted Naukri/email alerts and
priced out of $40-50/mo US copilots. No competitor combines India sourcing + resume matching +
tracking + Telegram push.

## Aggregator APIs , ranked next adds
1. **The Muse** , DONE this sprint. Keyless (500 req/hr), ToS-clean, India city-level, strong
   non-dev/early-career. The safe obvious win.
2. **TheirStack** , the only legit **Naukri**-inclusive aggregator (+ LinkedIn/Indeed/Glassdoor),
   real 200 jobs/mo free tier, Bearer auth. Reserve free credits for high-value India queries;
   deep-link, don't re-host. *Next paid-ish add.*
3. **Careerjet or Jooble** , licensed, compliant, broad India volume (dev + non-dev). V2.
- **Avoid for a multi-user tool:** all RapidAPI LinkedIn scrapers, Apify direct actors, Coresignal,
  Mantiks , same ToS posture that got **Proxycurl sued & shut down (July 2025)**. Risk = vendor
  disappearance + account bans.
- **Skip:** Reed/USAJobs/Arbeitnow (wrong geography), Findwork (thin India).

## Internships , DONE this sprint
- **GitHub raw JSON** (zero scraping): SimplifyJobs + vanshb03 `listings.json` on
  raw.githubusercontent.com. Poll, filter `active==true` + India/Remote/sponsorship.
- **Telegram channels** refreshed to verified-active India handles incl. non-dev (`fresherjobsadda`,
  `jobsinternshipswale`).
- **Lever** has a real `commitment=Intern` filter; **Greenhouse** needs title-matching. (Future: an
  India-native intern poller.)
- **Internshala = skip** (no API, anti-scrape). **WhatsApp channels (e.g. UXD Vault) can't be
  ingested** , only Telegram has a public web preview.

## Wellfound , reference, not a source
No usable public API (AngelList API deprecated; site 403s bots). Borrow 3 mechanics: (1) rank
salary/comp-disclosed jobs higher (Wellfound makes no-comp jobs near-invisible); (2) an "actively
hiring / posted N days ago" freshness badge; (3) application-status transparency (viewed/replied/
expired).

## "Time to hear back" per company
**No public dataset/API exists** (Glassdoor API closed; levels.fyi has no interview-timeline data;
Kaggle sets are synthetic or measure time-to-hire). Only real path: **learn it from our own users'**
`applied -> responded` timestamps (an `applications` table + a one-tap Telegram "did you hear back?"
prompt), with Bayesian shrinkage toward industry priors until per-company volume is enough (~20-30
responded apps/company). Compounds into data no competitor can buy. Pairs with the reco-engine events
direction.

## Competition (2026), the gap
Teal/Huntr/Simplify/Jobscan/Careerflow = US-skewed trackers/matchers, weak India. Jobright/LazyApply/
JobCopilot/AIApply = auto-apply copilots, $28-56/mo, poor India. Naukri/Instahyre/Cutshort = India
boards but recruiter-monetized inbound (you wait to be contacted), no outbound resume-matched digest,
no Telegram. Hirect effectively dead. Only Telegram job presence = un-personalized govt-job spam.
**Four columns no one fills together: Telegram push + India sourcing&matching unified + sub-₹200 price
+ non-dev/tier-2 coverage.**

## Next moves (post-sprint backlog)
- TheirStack free tier (Naukri coverage).
- `applications` table + Telegram "did you hear back?" -> first-party response-time data.
- Wellfound mechanics: comp-disclosed ranking + freshness badge + application status.
- India-native Greenhouse/Lever intern poller.
- Careerjet/Jooble for compliant volume.
