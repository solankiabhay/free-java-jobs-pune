"""
Daily FRESH Java jobs digest (Pune) for Abhay using the public OnJob.io JSON feed
(no Apify / API key needed).

Rules: posted within last 24h only (datePosted from feed), unverifiable dates dropped,
Pune only, 70%+ resume match, top 10 emailed via Gmail with match %, missing skills
and apply link. Also writes jobs.json for the GitHub Pages site (index.html).

Env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, MAIL_TO
"""
import html, json, os, re, smtplib, ssl, sys, urllib.request
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---------------- settings you can change ----------------
TOP_N = 10
MIN_MATCH = 70            # only show jobs at or above this match %
MAX_AGE_HOURS = 24        # strict freshness
YEARS_EXP = 6.5           # Jan 2020 -> now
SEEN_FILE = "seen_jobs.json"
JSON_FEED = "https://onjob.io/feeds/jobs.json"   # free-to-integrate; link back to job url
MAX_FEED_PAGES = 10
PUNE_AREAS = ("pune", "pimpri", "chinchwad", "hinjewadi", "hinjawadi", "kharadi", "baner", "wakad")

# skills in your resume (weight = importance)
RESUME_SKILLS = {"java": 3, "spring boot": 3, "spring": 2, "spring mvc": 2, "hibernate": 2,
                 "jpa": 1, "microservices": 3, "rest": 2, "react": 2, "javascript": 1,
                 "mysql": 1, "sql": 1, "websocket": 2, "docker": 1, "git": 1, "junit": 1,
                 "swagger": 1, "postman": 1, "jquery": 1}
# skills JDs ask for that are NOT clearly in your resume (shown as "missing")
GAP_SKILLS = {"kafka": 2, "aws": 2, "kubernetes": 2, "redis": 1, "postgresql": 1,
              "oracle": 1, "angular": 1, "azure": 1, "gcp": 1, "spark": 1, "mongodb": 1,
              "jenkins": 1, "ci/cd": 1, "terraform": 1, "rabbitmq": 1, "elasticsearch": 1}
# titles that are not developer roles even if "java" appears in the JD
BAD_TITLE = (r"fresher|intern\b|internship|trainee|junior|\bjr\b|architect|manager|director|"
             r"principal|head of|\bqa\b|tester|testing|sdet|automation test|devops|sre\b|"
             r"data (scientist|engineer|analyst)|sales|marketing|recruit|support|"
             r"sitecore|salesforce|mendix|firmware|ai engineer|machine learning")
# ----------------------------------------------------------


# ---------- source: OnJob.io JSON feed ----------
def strip_html(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def fetch_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; JavaJobsDigest/1.0)",
                      "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def parse_job(x):
    title = strip_html(x.get("title"))
    url = (x.get("applyUrl") or x.get("url") or "").strip()
    if not title or not url.startswith("http"):
        return None
    desc = strip_html(x.get("descriptionHtml") or (x.get("jsonLd") or {}).get("description"))
    skills = x.get("skills") or []
    if isinstance(skills, str):
        skills = [skills]
    loc = x.get("location") or ""
    city = x.get("city") or ""
    # strip OnJob id suffixes like "Maharashtra-22", "India-101"
    loc_clean = re.sub(r"-\d+\b", "", f"{city}, {loc}" if city and city.lower() not in loc.lower() else loc)
    return {
        "source": "OnJob.io",
        "title": title,
        "company": strip_html(x.get("company")),
        "location": loc_clean,
        "posted": (x.get("datePosted") or "").strip(),
        "url": url,
        "exp": strip_html(x.get("experience")),
        "salary": strip_html(x.get("salary")),
        "remote": bool(x.get("remote")),
        "text": " ".join([title, ", ".join(map(str, skills)), desc]),
    }


def fetch_free_jobs():
    first = fetch_json(JSON_FEED)
    pages = [first]
    seen_urls = {JSON_FEED}
    for p in (first.get("pages") or [])[:MAX_FEED_PAGES]:
        u = p.get("url") if isinstance(p, dict) else None
        if u and u not in seen_urls:
            seen_urls.add(u)
            try:
                pages.append(fetch_json(u))
            except Exception as exc:
                print(f"page failed {u}: {exc}", file=sys.stderr)
    jobs, urls = [], set()
    for data in pages:
        for x in data.get("jobs", []):
            j = parse_job(x)
            if j and j["url"] not in urls:
                urls.add(j["url"])
                jobs.append(j)
    print(f"Feed: {len(jobs)} jobs fetched (feed count: {first.get('totalCount')})")
    if not jobs:
        raise RuntimeError("OnJob feed returned 0 jobs (format may have changed).")
    return jobs


# ---------- freshness (strict: unknown => None => dropped) ----------
def age_hours(posted):
    p = (posted or "").strip()
    if not p:
        return None
    try:
        d = datetime.fromisoformat(p.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d.astimezone(timezone.utc)).total_seconds() / 3600


# ---------- matching ----------
def has(skill, t):
    return re.search(r"(?<![a-z])" + re.escape(skill) + r"(?![a-z])", t) is not None


def exp_range(j):
    s = (j["exp"] + " " + j["text"]).lower()
    m = re.search(r"(\d+)\s*(?:-|to|–)\s*(\d+)\s*\+?\s*(?:years|yrs|yr)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)\s*\+\s*(?:years|yrs|yr)", s)
    if m:
        return int(m.group(1)), int(m.group(1)) + 3
    return None


def is_pune(j):
    loc = j["location"].lower()
    return any(a in loc for a in PUNE_AREAS)


def evaluate(j):
    """Returns (match_percent, missing_skills) or None if rejected outright."""
    title = j["title"].lower()
    t = (j["title"] + " " + j["text"]).lower().replace("javascript", "js")
    if not has("java", t):
        return None
    if re.search(BAD_TITLE, title):
        return None
    if not is_pune(j):
        return None
    er = exp_range(j)
    if er and er[0] >= 9:
        return None

    found_have = {k: w for k, w in RESUME_SKILLS.items() if has(k, t)}
    found_gap = {k: w for k, w in GAP_SKILLS.items() if has(k, t)}
    total = sum(found_have.values()) + sum(found_gap.values())
    cov = sum(found_have.values()) / total if total else 0
    if len(found_have) + len(found_gap) < 3:
        cov *= 0.8

    if er is None:
        expfit = 0.6
    elif er[0] <= YEARS_EXP + 1 and er[1] >= YEARS_EXP - 1.5:
        expfit = 1.0
    elif er[0] > YEARS_EXP + 2:
        expfit = 0.1
    elif er[1] < YEARS_EXP - 3:
        expfit = 0.2
    else:
        expfit = 0.6

    titlefit = 0.5
    if re.search(r"full[- ]?stack|backend|back[- ]end|java developer|software (engineer|developer)|sde[- ]?2|senior", title):
        titlefit = 1.0
    if re.search(r"\blead\b|staff", title):
        titlefit = 0.4

    pct = round(100 * (0.55 * cov + 0.25 * expfit + 0.20 * titlefit))
    return pct, sorted(found_gap)


def work_mode(j):
    s = (j["location"] + " " + j["text"]).lower()
    if "hybrid" in s:
        return "Hybrid"
    if j.get("remote") or re.search(r"remote|work from home|wfh", s):
        return "Remote"
    if re.search(r"work from office|wfo|on-?site", s):
        return "WFO"
    return "Not stated"


def key(j):
    return re.sub(r"\W+", "", (j["title"] + j["company"]).lower())


# ---------- email ----------
def card(i, j):
    return (f"<div style='border:1px solid #ccc;border-radius:8px;padding:12px;margin:10px 0'>"
            f"<b>{i}. {html.escape(j['title'])}</b> &mdash; {html.escape(j['company'])}<br>"
            f"<b>{j['match']}% match</b> {'🔥' if j['match'] >= 80 else '🟢'} &middot; "
            f"Posted: {html.escape(j['age_label'])} &middot; {html.escape(j['source'])}<br>"
            f"Location: {html.escape(j['location'])} &middot; Mode: {work_mode(j)} &middot; "
            f"Exp: {html.escape(j['exp'] or 'not stated')}"
            f"{' &middot; ' + html.escape(j['salary']) if j['salary'] else ''}<br>"
            f"Missing skills: {html.escape(', '.join(j['missing']) or 'none spotted')}<br>"
            f"<a href='{html.escape(j['url'])}'>Apply</a></div>")


def send_email(subject, body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["GMAIL_ADDRESS"]
    msg["To"] = os.environ.get("MAIL_TO") or os.environ["GMAIL_ADDRESS"]
    msg.attach(MIMEText(body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


# ---------- main ----------
def main():
    try:
        jobs = fetch_free_jobs()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        try:
            send_email(f"Job Digest FAILED - {date.today():%d %b}",
                       "<h3>Java Jobs Digest could not fetch jobs</h3>"
                       "<p>The OnJob.io feed failed. This is a source error, not a zero-match result.</p>"
                       f"<pre>{html.escape(str(exc))}</pre>")
            print("Sent source-failure email")
        except Exception as mail_exc:
            print(f"Could not send failure email: {mail_exc}", file=sys.stderr)
        sys.exit(1)

    seen = set(json.load(open(SEEN_FILE, encoding="utf-8"))) if os.path.exists(SEEN_FILE) else set()
    pune_jobs = [j for j in jobs if is_pune(j)]
    print(f"Pune jobs in feed: {len(pune_jobs)}; Pune + 'java' in text: "
          f"{sum(1 for j in pune_jobs if has('java', j['text'].lower()))}")

    unparsed, stale, unique = [], 0, {}
    for j in jobs:
        h = age_hours(j["posted"])
        if h is None:
            unparsed.append(j["posted"])
            continue
        if h < 0 or h > MAX_AGE_HOURS:
            stale += 1
            continue
        res = evaluate(j)
        if not res or res[0] < MIN_MATCH or key(j) in seen:
            continue
        j["match"], j["missing"] = res
        j["age_hours"] = h
        j["age_label"] = "just now" if h < 1 else f"~{round(h)} hours ago"
        k = key(j)
        if k not in unique or j["match"] > unique[k]["match"]:
            unique[k] = j
    print(f"dropped: {stale} older than {MAX_AGE_HOURS}h, {len(unparsed)} with unverifiable date")

    top = sorted(unique.values(), key=lambda x: (x["age_hours"], -x["match"]))[:TOP_N]

    website_jobs = [{
        "title": j["title"], "company": j["company"] or "Company not listed",
        "location": j["location"], "posted": j["age_label"], "age_label": j["age_label"],
        "postedAt": j["posted"], "source": j["source"], "match": j["match"],
        "missing": j["missing"],
        "skills": [s for s in RESUME_SKILLS if has(s, (j["title"] + " " + j["text"]).lower())],
        "url": j["url"], "applyUrl": j["url"], "text": j["text"][:800],
        "workMode": work_mode(j), "experience": j["exp"] or "Not stated",
        "salary": j["salary"] or "Not stated",
    } for j in top]
    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump({"updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "jobs": website_jobs}, f, ensure_ascii=False, indent=2)
    print(f"Wrote jobs.json with {len(website_jobs)} jobs")

    if top:
        body = (f"<h3>🔥 Top {min(5, len(top))} to apply to first</h3>"
                + "".join(card(i, j) for i, j in enumerate(top[:5], 1)))
        if len(top) > 5:
            body += "<h3>More fresh matches</h3>" + "".join(card(i, j) for i, j in enumerate(top[5:], 6))
    else:
        body = ("<p>No jobs met all rules today (posted in last 24h, Pune, 70%+ match, "
                "date verified). Nothing fake added.</p>")
    body += (f"<p style='color:#777;font-size:12px'>Source: OnJob.io public jobs feed. "
             f"Checked {len(jobs)} postings ({len(pune_jobs)} in Pune); {stale} older than 24h and "
             f"{len(unparsed)} with unverifiable dates were dropped. "
             "Feed does not include every job board.</p>")
    send_email(f"Fresh Java Jobs Pune - {len(top)} matches ({date.today():%d %b})", body)
    seen |= {key(j) for j in top}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f)
    print(f"Emailed {len(top)} jobs")


if __name__ == "__main__":
    main()
