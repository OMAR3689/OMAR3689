import csv, json, os, re, smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(OUT_DIR, "contacted.json")
MAX_EMAILS = int(os.environ.get("MAX_EMAILS_PER_RUN", "5"))
PRESENTATION_URL = os.environ.get("PRESENTATION_URL", "https://trmb.ma/")

QUERIES = [
    '"recherche transporteur" OR "prestataire transport" Maroc',
    '"besoin transport" OR "sous-traitant transport" Maroc entreprise',
    '"appel d offres" transporteur logistique Maroc',
    '"recherche transporteur" France logistique',
    '"prestataire transport" Belgique logistique',
    '"carrier needed" OR "transport provider" Germany',
    '"freight tender" OR "transport tender" Europe',
]

SIGNALS = {
    "recherche transporteur": 35, "prestataire transport": 35,
    "appel d'offres": 30, "appel d offres": 30, "freight tender": 30,
    "transport tender": 30, "carrier needed": 35, "transport provider": 30,
    "sous-traitant": 20, "affrètement": 20, "subcontractor": 20,
}


def http_json(url, payload, headers):
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e
    except URLError as e:
        raise RuntimeError(f"Network error calling {url}: {e.reason}") from e


def tavily_search(query):
    if not TAVILY_KEY:
        raise RuntimeError("TAVILY_API_KEY is required")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TAVILY_KEY}",
    }
    payload = {
        "query": query,
        "search_depth": "advanced",
        "max_results": 10,
        "include_answer": False,
    }
    data = http_json("https://api.tavily.com/search", payload, headers)
    return data.get("results", [])


def score(text):
    t = text.lower()
    points = sum(v for k, v in SIGNALS.items() if k in t)
    if any(x in t for x in ["2026", "2027", "urgent", "immediate", "besoin", "demande de devis"]):
        points += 10
    return min(points, 100)


def rule_based_qualify(title, content, url):
    """Qualify a lead without OpenAI API credits.

    The email is accepted only when it is explicitly present in the public
    Tavily result content/title. No address or person is invented.
    """
    text = f"{title} {content}"
    s = score(text)
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    email = emails[0].lower() if emails else ""
    qualified = s >= 50
    return {
        "qualified": qualified,
        "company": title,
        "email": email,
        "contact_name": "",
        "reason": f"Rule-based qualification: score={s}; public email={'found' if email else 'not found'}",
        "subject": "Proposition de partenariat transport – TRMB SERVICES",
        "body": (
            f"Bonjour,\n\n"
            f"Nous avons identifié votre besoin potentiel en transport/logistique et souhaitons vous proposer les services de TRMB SERVICES.\n\n"
            f"Nous pouvons vous accompagner sur vos opérations de transport et de logistique au Maroc et à l'international.\n\n"
            f"Présentation : {PRESENTATION_URL}\n\n"
            f"Bien cordialement,\nTRMB SERVICES"
        ),
    }


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user)
    if not all([host, user, password, sender]):
        return False, "SMTP credentials not configured"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    return True, "sent"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history()
    leads = {}

    print(f"Starting discovery with {len(QUERIES)} Tavily queries...")
    for q in QUERIES:
        print(f"Searching: {q}")
        for item in tavily_search(q):
            title = item.get("title", "").strip()
            url = item.get("url", "").strip()
            content = item.get("content", "")
            key = url or title.lower()
            if not key or key in history:
                continue

            text = f"{title} {content}"
            s = score(text)
            if s < 30:
                continue

            leads[key] = {
                "company_or_title": title,
                "need": content[:1500].replace("\n", " "),
                "score": s,
                "source_url": url,
                "country_hint": next(
                    (c for c in ["Maroc", "France", "Belgique", "Germany", "Allemagne", "Spain", "Italy"] if c.lower() in text.lower()),
                    "",
                ),
                "found_at": datetime.now(timezone.utc).isoformat(),
            }

    rows = sorted(leads.values(), key=lambda x: x["score"], reverse=True)
    sent = 0

    for lead in rows:
        if sent >= MAX_EMAILS:
            break

        # No OpenAI call: qualification and email extraction are local.
        ai = rule_based_qualify(lead["company_or_title"], lead["need"], lead["source_url"])
        lead.update({
            "qualified": bool(ai.get("qualified")),
            "company": ai.get("company", lead["company_or_title"]),
            "email": ai.get("email", ""),
            "reason": ai.get("reason", ""),
        })

        if not lead["qualified"] or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", lead["email"]):
            continue

        subject = ai.get("subject") or "Proposition de partenariat transport – TRMB SERVICES"
        body = ai.get("body") or (
            f"Bonjour,\n\nNous avons identifié un besoin potentiel de transport/logistique. "
            f"TRMB SERVICES peut vous accompagner sur vos opérations de transport.\n\n"
            f"Présentation : {PRESENTATION_URL}\n\nBien cordialement,\nTRMB SERVICES"
        )

        ok, status = send_email(lead["email"], subject, body)
        lead["email_status"] = status
        if ok:
            history[lead["source_url"] or lead["company_or_title"]] = {
                "email": lead["email"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            sent += 1

    save_history(history)
    with open(os.path.join(OUT_DIR, "leads.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT_DIR, "leads.csv"), "w", encoding="utf-8", newline="") as f:
        fields = ["company_or_title", "company", "email", "qualified", "score", "email_status", "need", "source_url", "country_hint", "found_at"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"Found {len(rows)} leads; sent {sent} emails")


if __name__ == "__main__":
    main()
