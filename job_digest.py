"""
Daily FRESH Java jobs digest (Pune) for Abhay using a public RSS feed (no Apify/API key).
Strict rules: posted within last 24h only, unverifiable dates dropped, Pune feed,
70%+ resume match, top 10 emailed via Gmail with match %, missing skills and apply link.

Env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, MAIL_TO
"""
import html, json, os, re, smtplib, ssl, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---------------- settings you can change ----------------
TOP_N = 10
MIN_MATCH = 70            # only show jobs at or above this match %
MAX_AGE_HOURS = 24        # strict freshness
YEARS_EXP = 6.5           # Jan 2020 -> now
SEEN_FILE = "seen_jobs.json"
# Public Pune RSS feed; no Apify token or paid scraping actor required.
RSS_FEEDS = ["https://www.onjob.io/job-alerts/jobs-in-pune.xml"]

# skills in your resume (weight = importance)
RESUME_SKILLS = {"java": 3, "spring boot": 3, "spring": 2, "spring mvc": 2, "hibernate": 2,
                 "jpa": 1, "microservices": 3, "rest": 2, "react": 2, "javascript": 1,
                 "mysql": 1, "sql": 1, "websocket": 2, "docker": 1, "git": 1, "junit": 1,
                 "swagger": 1, "postman": 1, "jquery": 1}
# skills JDs ask for that are NOT clearly in your resume (shown as "missing")
GAP_SKILLS = {"kafka": 2, "aws": 2, "kubernetes": 2, "redis": 1, "postgresql": 1,
              "oracle": 1, "angular": 1, "azure": 1, "gcp": 1, "spark": 1, "mongodb": 1,
              "jenkins": 1, "ci/cd": 1, "terraform": 1, "rabbitmq": 1, "elasticsearch": 1}
# ----------------------------------------------------------

# ---------- free public RSS source ----------
def strip_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def fetch_rss_feed(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; JavaJobsDigest/1.0)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()

    root = ET.fromstring(raw)

    def local_name(tag):
        # Handles both plain tags and namespaced tags such as {http://purl.org/dc/elements/1.1/}date.
        return tag.rsplit("}", 1)[-1].split(":")[-1].lower()

    def child_values(node):
        values = {}
        for child in node.iter():
            if child is node:
                continue
            name = local_name(child.tag)
            text_value = " ".join(t.strip() for t in child.itertext() if t.strip())
            if text_value and name not in values:
                values[name] = text_value
            # RSS links may be represented by an href attribute (Atom style).
            if name == "link" and not text_value and child.attrib.get("href"):
                values[name] = child.attrib["href"]
        return values

    items = [node for node in root.iter() if local_name(node.tag) == "item"]
    if not items:
        items = [node for node in root.iter() if local_name(node.tag) == "entry"]

    jobs = []
    date_aliases = ("pubdate", "published", "publishedat", "datepublished", "updated",
                    "date", "created", "createdat", "postingdate", "posted", "timestamp")
    for item in items:
        values = child_values(item)
        title = strip_html(values.get("title", ""))
        link = (values.get("link", "") or values.get("guid", "")).strip()
        description = strip_html(values.get("description", "") or values.get("summary", "")
                                 or values.get("encoded", "") or values.get("content", ""))
        posted = ""
        for field in date_aliases:
            if values.get(field):
                posted = values[field].strip()
                break
        company = strip_html(values.get("author", "") or values.get("creator", "")
                             or values.get("source", ""))

        if not title or not link:
            continue
        jobs.append({
            "source": "OnJob.io RSS", "title": title, "company": company,
            "location": "Pune, Maharashtra, India", "posted": posted, "url": link,
            "exp": "", "salary": "", "text": title + " " + description,
        })

    if items and jobs and not any(job["posted"] for job in jobs):
        print("WARNING: RSS items have no recognized posting-date field.")
        print("Available fields in first item:", sorted(child_values(items[0]).keys()))
    return jobs

def fetch_free_jobs():
    all_jobs = []
    errors = []
    for url in RSS_FEEDS:
        try:
            found = fetch_rss_feed(url)
            print(f"RSS {url}: {len(found)} jobs fetched")
            all_jobs.extend(found)
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            print(f"RSS source failed: {errors[-1]}", file=sys.stderr)
    if not all_jobs and errors:
        raise RuntimeError("All free job feeds failed. " + " | ".join(errors))
    return all_jobs

# ---------- freshness (strict: unknown => None => dropped) ----------
def age_hours(posted):
    p = (posted or "").strip()
    if not p:
        return None
    low = p.lower()
    if re.search(r"just now|few (seconds|minutes)|today|few hours", low):
        return 0.0
    m = re.search(r"(\d+)\+?\s*(minute|min|hour|hr|day|week|month)", low)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return n * {"minute": 1/60, "min": 1/60, "hour": 1, "hr": 1, "day": 24,
                    "week": 168, "month": 720}[u] + (1 if u in ("day", "week", "month") else 0)
    # RSS feeds commonly use RFC 2822 dates (e.g. Tue, 15 Oct 2026 10:00:00 +0530).
    try:
        d = parsedate_to_datetime(p)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d.astimezone(timezone.utc)).total_seconds() / 3600
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        d = datetime.fromisoformat(p.replace("Z", "+00:00").replace("z", "+00:00"))
        if len(p) <= 10:
            return 0.0 if d.date() == datetime.now().date() else 99999
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d.astimezone(timezone.utc)).total_seconds() / 3600
    except ValueError:
        return None


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


def evaluate(j):
    """Returns (match_percent, missing_skills) or None if the job is rejected outright."""
    title = j["title"].lower()
    t = (j["title"] + " " + j["text"]).lower().replace("javascript", "js")
    if not has("java", t):
        return None
    if re.search(r"fresher|intern\b|internship|trainee|junior|\bjr\b|architect|manager|director|principal|head of", title):
        return None
    if "pune" not in j["location"].lower():
        return None
    er = exp_range(j)
    if er and er[0] >= 9:
        return None

    found_have = {k: w for k, w in RESUME_SKILLS.items() if has(k, t)}
    found_gap = {k: w for k, w in GAP_SKILLS.items() if has(k, t)}
    total = sum(found_have.values()) + sum(found_gap.values())
    cov = sum(found_have.values()) / total if total else 0
    if len(found_have) + len(found_gap) < 3:
        cov *= 0.8  # thin description -> less certain

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
    if re.search(r"remote|work from home|wfh", s):
        return "Remote"
    if re.search(r"work from office|wfo|on-?site", s):
        return "WFO"
    return "Not stated"


def key(j):
    return re.sub(r"\W+", "", (j["title"] + j["company"]).lower())


# ---------- main ----------
def card(i, j):
    return (f"<div style='border:1px solid #ccc;border-radius:8px;padding:12px;margin:10px 0'>"
            f"<b>{i}. {html.escape(j['title'])}</b> &mdash; {html.escape(j['company'])}<br>"
            f"<b>{j['match']}% match</b> {'🔥' if j['match'] >= 80 else '🟢'} &middot; "
            f"Posted: {html.escape(j['age_label'])} &middot; {html.escape(j['source'])}<br>"
            f"Location: {html.escape(j['location'])} &middot; Mode: {work_mode(j)} &middot; "
            f"Exp: {html.escape(j['exp'] or 'not stated')}"
            f"{' &middot; ' + html.escape(j['salary']) if j['salary'] else ''}<br>"
            f"Missing skills: {', '.join(j['missing']) or 'none spotted'}<br>"
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

def main():
    try:
        jobs = fetch_free_jobs()
    except Exception as exc:
        message = html.escape(str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        try:
            send_email(
                f"Job Digest FAILED - {date.today():%d %b}",
                "<h3>Java Jobs Digest could not fetch jobs</h3>"
                "<p>The free RSS source failed. This is a source error, not a zero-match result.</p>"
                f"<pre>{message}</pre><p>Check the RSS URL and GitHub Actions logs.</p>")
            print("Sent source-failure email")
        except Exception as mail_exc:
            print(f"Could not send failure email: {mail_exc}", file=sys.stderr)
        sys.exit(1)

    seen = set(json.load(open(SEEN_FILE, encoding="utf-8"))) if os.path.exists(SEEN_FILE) else set()
    unparsed, stale, unique = [], 0, {}
    for j in jobs:
        if not j["title"] or not j["url"]:
            continue
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
    if unparsed:
        print("sample unparseable date strings:", list(set(unparsed))[:5])

    top = sorted(unique.values(), key=lambda x: (x["age_hours"], -x["match"]))[:TOP_N]

    # Publish the same results for index.html (GitHub Pages reads ./jobs.json).
    # Keep jobs.json updated even when there are zero matching jobs.
    website_jobs = []
    for j in top:
        website_jobs.append({
            "title": j["title"],
            "company": j["company"] or "Company not listed",
            "location": j["location"],
            "posted": j["age_label"],
            "age_label": j["age_label"],
            "postedAt": j["posted"],
            "source": j["source"],
            "match": j["match"],
            "missing": j["missing"],
            "skills": [skill for skill in RESUME_SKILLS if has(skill, (j["title"] + " " + j["text"]).lower())],
            "url": j["url"],
            "applyUrl": j["url"],
            "text": j["text"],
            "workMode": work_mode(j),
            "experience": j["exp"] or "Not stated",
            "salary": j["salary"] or "Not stated",
        })
    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump({"updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"), "jobs": website_jobs}, f, ensure_ascii=False, indent=2)
    print(f"Wrote jobs.json with {len(website_jobs)} jobs")

    if top:
        body = (f"<h3>🔥 Top {min(5, len(top))} to apply to first</h3>"
                + "".join(card(i, j) for i, j in enumerate(top[:5], 1)))
        if len(top) > 5:
            body += "<h3>More fresh matches</h3>" + "".join(card(i, j) for i, j in enumerate(top[5:], 6))
    else:
        body = ("<p>No jobs met all rules today (posted in last 24h, Pune feed, 70%+ match, "
                "date verified). Nothing fake added.</p>")
    body += (f"<p style='color:#777;font-size:12px'>Source: public OnJob.io Pune RSS feed. "
             f"Checked {len(jobs)} postings; {stale} older than 24h and "
             f"{len(unparsed)} with unverifiable dates were dropped. "
             "Feed content is not guaranteed to include every job board.</p>")
    send_email(f"Fresh Java Jobs Pune - {len(top)} matches ({date.today():%d %b})", body)
    seen |= {key(j) for j in top}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f)
    print(f"Emailed {len(top)} jobs")

if __name__ == "__main__":
    main()
