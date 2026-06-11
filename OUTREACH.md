# JobHunt outreach drafts

Two ready-to-send drafts. Friend-ask is for THIS WEEK (the real unblock: get 5-10
people using it so the eval harness has real events and you get real feedback).
The Reddit post is drafted but GATED, do not post it until (a) you've earned karma
in the sub and (b) a few friends say it's genuinely useful (see MARKETING.md).

The pitch deliberately does NOT claim "best matching." You can't prove that yet
(the harness has no real labels). "Help me find out if it's any good" is both honest
AND a stronger, more human ask.

---

## 1. Friend-ask (WhatsApp / DM) , send to 10 people this week

Pick people who are actually job-hunting or fresh grads. Personalize the first line.

> Hey [name], built something and I need a small favour from you specifically.
>
> It's a free job-search tool I made. You upload your resume once, and it sends you
> only the jobs that actually fit you (pulled from 150+ company career pages + remote
> boards), to Telegram or email, daily, no spam. It also tracks what you've applied to.
>
> Here's the honest part: I don't yet know if the matching is any good, and the only
> way to find out is to have a few real people use it for a couple of weeks. That's
> where you come in. Takes ~2 min to set up: [LINK]
>
> If it sends you junk, tell me, that feedback is literally the point. If it finds you
> something good, even better. Either way you'd be helping me a lot.

Follow-up after a week (only to people who signed up):
> Hey, did JobHunt send you anything useful, or was it mostly noise? Brutal honesty
> helps me more than politeness. Anything that annoyed you in the signup or the digest?

---

## 2. r/developersIndia build-in-public post , GATED, week 2-3 only

Rules first (from MARKETING.md): earn karma by genuinely answering fresher questions
for 2-3 weeks first; new accounts dropping links get auto-removed. NO link in the
title. Put the link in a comment. Be live in the thread for days. Read the live
sidebar rules before posting.

**Title:**
> I got tired of refreshing 150+ company career pages, so I built a free tool that
> sends me only the jobs matching my resume. Here's how it works.

**Body:**
> Like a lot of you, my off-campus search was just refreshing the same 100+ career
> pages and remote boards, missing postings because I checked on the wrong day, and
> drowning in irrelevant alerts from the big portals. So I built my own thing to fix
> my own problem, and it's now at the point where I want honest feedback.
>
> What it does:
> - Aggregates jobs from free sources (Remotive, RemoteOK, Arbeitnow, Jobicy,
>   Himalayas, Adzuna) plus company ATS boards (Greenhouse / Lever / Ashby), including
>   Indian companies, into one fresh list.
> - Matches them to your resume and ranks by likelihood of actually fitting (skill
>   coverage, seniority fit, location/region, recency), not just keyword overlap. It
>   also learns from what you apply to vs dismiss.
> - Sends a daily digest to Telegram or email. Quiet when there's nothing new (the
>   anti-spam bit matters to me).
> - Tracks your applications (applied / heard back / notes) so the weekend triage is
>   fast.
>
> The matching engine is the part I actually care about and the part I'm least sure
> about, so I built an eval harness to measure it (precision@k, NDCG, per-signal
> ablation). Right now it has too little real data to trust, which is exactly why I'm
> posting: I want real people using it so I can see if the matches are genuinely good.
>
> It's free, I'm a solo dev, there's no spam and no auto-apply (I think spray-and-pray
> is the problem, not the solution). If you try it and it sends you junk, tell me, that
> feedback is the whole point.
>
> Happy to go into the matching logic, the sourcing, or the stack in the comments.
> (Link in a comment below.)

First comment:
> Link: [LINK with ?utm_source=reddit]. Setup is ~2 min (resume + pick Telegram or
> email). Genuinely want the harsh feedback , reply here or DM me.

---

## 3. "Free list" value post , the cold-safe public play (X + Reddit)

Built from the live catalog. This format gives value BEFORE asking, so it survives
Reddit automod and isn't spam. Refresh the list each week (it's literally your product's
output). NOTE: catalog currently skews mid-level / IC, not fresher, so the framing is
"companies hiring in India now," not "freshers."

CAVEAT before posting: check each sub's rules. Many (incl. r/developersIndia) route
hiring content to a weekly/monthly megathread, posting a standalone job list outside it
can get removed. When in doubt, drop the list as a comment in the existing hiring thread
with a soft CTA, that's pure value and never reads as spam.

### X / Twitter thread

Tweet 1 (hook):
> 8 companies hiring software + data roles in India right now, with DIRECT apply links
> (skip LinkedIn/Naukri, apply straight on their career page). 🧵

Tweet 2:
> 1. NielsenIQ , Software Engineer (.NET, Angular, Docker, Azure), Pune
> https://jobs.smartrecruiters.com/NielsenIQ/744000131629509
>
> 2. Cisco , Site Reliability Engineer (4-7 yrs), Bangalore
> https://cisco.wd5.myworkdayjobs.com/en-US/Cisco_Careers/job/Bangalore-India/Site-Reliability-Engineer---4--7-years--Work-Location---Bangalore_2015553

Tweet 3:
> 3. eBay , Traffic Engineer, Bengaluru
> https://ebay.wd5.myworkdayjobs.com/en-US/apply/job/Bengaluru-India/Traffic-Engineer_R0069582
>
> 4. Mastercard , Site Reliability Engineer I, Pune
> https://mastercard.wd1.myworkdayjobs.com/en-US/CorporateCareers/job/Pune-India/Site-Reliability-Engineer-I_R-278230

Tweet 4:
> 5. Micron , Data / Data-Governance Analyst, Hyderabad
> https://micron.wd1.myworkdayjobs.com/en-US/External/job/Hyderabad---Phoenix-Aquila-India/Data-Governance-Analyst_JR92910-1
>
> 6. NVIDIA , Incident Response Engineer (Facility Ops), India (Remote)
> https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/India-Remote/Incident-Response-Engineer-Facility-Operations-Center_JR2019259

Tweet 5:
> 7. NielsenIQ , Software Test Engineer (Java, Selenium), Chennai
> https://jobs.smartrecruiters.com/NielsenIQ/744000131618120
>
> 8. Cisco , Data Engineer, Big Data & Streaming (Spark, Kafka, Flink), Bangalore
> https://cisco.wd5.myworkdayjobs.com/en-US/Cisco_Careers/job/Bangalore-India/Data-Engineer---Big-Data---Streaming-Platforms---Java-Scala-or-Python---Spark--Kafka---Flink--Iceberg---Lakehouse--Trino---4-to-7-Years_2016346

Tweet 6 (soft CTA):
> I pull these from 150+ company career pages daily and match them to your resume, free,
> no spam, delivered to Telegram or email. If that's useful: [LINK in bio]. Reposting
> a fresh list every week.

### Reddit (a value comment in the hiring megathread, or a standalone if the sub allows)

> A few companies hiring software/data roles in India right now, direct apply links so
> you skip the portals:
>
> - NielsenIQ , Software Engineer (.NET/Angular/Azure), Pune: <link>
> - Cisco , Site Reliability Engineer (4-7y), Bangalore: <link>
> - eBay , Traffic Engineer, Bengaluru: <link>
> - Mastercard , SRE I, Pune: <link>
> - Micron , Data Analyst, Hyderabad: <link>
> - NVIDIA , Incident Response Engineer, India (Remote): <link>
> - Cisco , Data Engineer (Spark/Kafka), Bangalore: <link>
>
> (I scrape 150+ career pages daily for my own search; happy to share more for a
> specific stack/city if anyone wants.)

Only add a product link if the sub's rules clearly allow it; otherwise let the value
stand and put the tool in your profile.

---

## After they sign up: feed the harness

Once a handful of people have used it for a week, pull their real interactions into
the eval label set and finally measure matching for real:

    .venv/bin/python -m eval.import_events --days 30 --out eval/labels.json
    # HAND-CORRECT the grades (implicit signals are noisy), then:
    .venv/bin/python -m eval.harness --ablate

That ablation table going from all-zeros to real deltas is the moment you can honestly
say whether your matching beats keyword search, and the moment marketing is unlocked.
