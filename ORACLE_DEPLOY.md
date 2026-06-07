# Deploying JobHunt on Oracle Cloud Always Free (24GB ARM)

This moves the app off Render's 512MB free tier onto an Oracle **Always Free** ARM VM (up to 4 vCPU +
24GB RAM, always-on, no sleep, free forever). Your database stays on **Neon**, so there is **no data
migration** , the new server just points `DATABASE_URL` at the same Neon URL.

The app runs as Docker containers: the JobHunt app + **Caddy** (automatic free HTTPS). Total hands-on
time ~30-45 min, most of it Oracle's console.

---

## 1. Create the VM (Oracle console)

1. Sign up at https://www.oracle.com/cloud/free/ (needs a card for identity check; Always Free is
   never charged). Pick a home region close to you/India (e.g. Mumbai/Hyderabad) , Always Free ARM
   capacity varies by region/AD, so if creation fails with "out of capacity," try another
   Availability Domain or retry later.
2. **Compute > Instances > Create instance.**
   - Image: **Ubuntu 22.04** (or 24.04).
   - Shape: **Ampere A1 (ARM)**. Set **2 OCPU / 12GB** (plenty) or up to **4 OCPU / 24GB** , all
     within Always Free.
   - Add your SSH public key (or let it generate one and download it).
   - Create. Note the **public IP**.

## 2. Open the network (two layers , both required)

Oracle blocks ports at the cloud level AND inside the OS by default. Do both:

- **Cloud (VCN security list):** Networking > Virtual Cloud Networks > your VCN > the public subnet >
  its Security List > **Add Ingress Rules**: source `0.0.0.0/0`, protocol TCP, destination ports
  **80** and **443**. (Port 22 is already open.)
- **OS firewall (on the VM, after you SSH in):** Oracle's Ubuntu image has iptables rules that drop
  80/443. Allow them:
  ```bash
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save
  ```

## 3. SSH in + install Docker

```bash
ssh -i /path/to/key ubuntu@<PUBLIC_IP>

sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker     # run docker without sudo
```

## 4. Get the code + env

```bash
git clone https://github.com/KrishOjha1810/jobhunt.git
cd jobhunt
cp .env.oracle.example .env
nano .env            # fill it in (see notes below)
```

Filling in `.env`:
- `DATABASE_URL` , copy the exact Neon URL from your Render dashboard (same DB, no migration).
- `SECRET_KEY` , generate one: `openssl rand -hex 32`. Keep it stable (changing it logs everyone out).
- `BASE_URL` , `https://<your hostname>` (see step 5 for the hostname).
- `SITE_ADDRESS` , the same hostname WITHOUT `https://` (Caddy uses it for the cert).
- Copy `GEMINI_API_KEY`, `LLM_API_KEY`, `JSEARCH_RAPIDAPI_KEY`, `ADZUNA_*`, `SMTP_*`,
  `GOOGLE_CLIENT_ID/SECRET`, `TELEGRAM_BOT_TOKEN` from Render.
- The `ATS_FULL_CONTENT=1 / ATS_WORKERS=40 / POOL_CAP=800 / ATS_CAP=500` lines unleash full power now
  that you have 24GB (full Greenhouse JDs inline, more boards at once).
- `ENABLE_SCHEDULER=1` , the VM never sleeps, so the built-in scheduler runs the matcher at
  `RUN_HOURS`. **No external cron needed.**

## 5. Pick a hostname for HTTPS

You need a hostname for the free cert. Two options:
- **Own a domain:** add an **A record** pointing `jobs.yourdomain.com` -> the VM public IP. Use that
  as `BASE_URL`/`SITE_ADDRESS`.
- **No domain (quick):** use **sslip.io** , `SITE_ADDRESS=<IP-with-dashes>.sslip.io`, e.g. if the IP
  is `203.0.113.7` then `203-0-113-7.sslip.io`. It resolves to your IP and Caddy gets a real cert for
  it. `BASE_URL=https://203-0-113-7.sslip.io`.

## 6. Launch

```bash
docker compose up -d --build
docker compose logs -f app        # watch it boot; Ctrl-C to stop watching
```

Visit `https://<your hostname>/healthz` , should return ok. Then the dashboard at `/`.

## 7. Two post-deploy must-dos

1. **Google sign-in:** in Google Cloud Console > Credentials > your OAuth client > **Authorized
   redirect URIs**, add `https://<BASE_URL>/auth/google/callback`. Without this, Google login fails.
2. **Stop Render from also sending** (so users don't get double emails): in the Render dashboard
   either suspend the service, or remove its external cron / set `ENABLE_SCHEDULER` off so only Oracle
   runs the matcher. Keep Render around a day as a fallback if you like, but only ONE host should run
   scheduled sends.

## 8. Verify

```bash
curl https://<host>/diag       # per-user coverage summary
curl https://<host>/status     # last run time
```
Trigger a run manually if you want: `curl "https://<host>/run?token=<RUN_TOKEN>&force=1"` (set
`RUN_TOKEN` in `.env` first), or just wait for the next `RUN_HOURS` slot.

## Updating later

```bash
cd ~/jobhunt && git pull && docker compose up -d --build
```

## Notes / gotchas
- ARM image: the Dockerfile uses `python:3.12-slim`, which is multi-arch , builds fine on ARM.
- Memory: with `ATS_FULL_CONTENT=1` the fetch holds more (full JDs), but 24GB has enormous headroom
  vs Render's 512MB. If you ever want to dial back, lower `ATS_WORKERS` / unset `ATS_FULL_CONTENT`.
- Resumes/docx live on the `jobhunt-data` Docker volume; the real data is on Neon, so the VM is
  disposable , you can rebuild it from this repo + `.env` anytime.
- HTTPS renewals are automatic (Caddy).
