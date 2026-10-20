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

# Morocco only: concrete B2B transport/logistics intent.
QUERIES = [
    '"recherche transporteur" Maroc entreprise',
    '"prestataire transport" Maroc entreprise',
    '"besoin transport" Maroc entreprise',
    '"sous-traitant transport" Maroc entreprise',
    '"affrètement" Maroc entreprise transport',
    '"appel d offres" transport logistique Maroc',
    '"demande de devis" transport Maroc entreprise',
    '"transporteur" "Maroc" "besoin" logistique',
]

SIGNALS = {
    "recherche transporteur": 40,
    "prestataire transport": 40,
    "besoin transport": 35,
    "appel d'offres": 30,
    "appel d offres": 30,
    "demande de devis": 35,
    "sous-traitant transport": 35,
    "affrètement": 25,
    "transporteur": 15,
    "logistique": 10,
}

GENERIC_EMAIL_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
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
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {TAVILY_KEY}"}
    payload = {
        "query": query,
        "search_depth": "advanced",
        "max_results": 10,
        "include_answer": False,
    }
    return http_json("https://api.tavily.com/search", payload, headers).get("results", [])


def score(text):
    t = text.lower()
    points = sum(v for k, v in SIGNALS.items() if k in t)
    if any(x in t for x in ["2026", "2027", "urgent", "immediate", "besoin", "demande de devis"]):
        points += 10
    return min(points, 100)


def extract_emails(text):
    found = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    result = []
    for email in found:
        email = email.lower().strip(".,;:()[]<>")
        if email.split("@", 1)[0] in GENERIC_EMAIL_PREFIXES:
            continue
        if email not in result:
            result.append(email)
    return result


def rule_based_qualify(title, content, url):
    text = f"{title} {content}"
    s = score(text)
    emails = extract_emails(text)
    email = emails[0] if emails else ""
    qualified = s >= 50 and bool(email)

    # Personalize from the public evidence instead of inventing a contact/person.
    need = re.sub(r"\s+", " ", content).strip()
    need = need[:500]
    body = (
        "Bonjour,\n\n"
        "Nous avons identifié votre besoin en transport/logistique à partir de votre publication. "
        "TRMB SERVICES peut vous accompagner avec des solutions de transport et de logistique adaptées à vos opérations au Maroc.\n\n"
        f"Besoin identifié : {need}\n\n"
        f"Vous pouvez découvrir TRMB SERVICES ici : {PRESENTATION_URL}\n\n"
        "Si ce besoin est toujours d’actualité, nous pouvons vous transmettre rapidement une proposition adaptée.\n\n"
        "Bien cordialement,\n"
        "TRMB SERVICES\n"
        "https://trmb.ma/"
    )
    return {
        "qualified": qualified,
        "company": title,
        "email": email,
        "contact_name": "",
        "reason": f"Qualification automatique: score={s}; email public={'oui' if email else 'non'}",
        "subject": "Proposition de partenariat transport – TRMB SERVICES",
        "body": body,
    }


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def history_keys(history):
    keys = set(history.keys())
    for value in history.values():
        if isinstance(value, dict) and value.get("email"):
            keys.add(value["email"].lower())
    return keys


def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user).strip()
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
    known = history_keys(history)
    leads = {}

    print(f"Starting Morocco-only discovery with {len(QUERIES)} Tavily queries...")
    for q in QUERIES:
        print(f"Searching: {q}")
        try:
            results = tavily_search(q)
        except RuntimeError as e:
            print(f"⚠️ Tavily query skipped: {e}")
            continue

        for item in results:
            title = item.get("title", "").strip()
            url = item.get("url", "").strip()
            content = item.get("content", "")
            text = f"{title} {content}"
            key = url or title.lower()
            emails = extract_emails(text)
            email_key = emails[0] if emails else ""

            if not key or key in known or (email_key and email_key in known):
                continue

            # Keep only concrete transport/logistics intent and Moroccan evidence.
            lower = text.lower()
            if "maroc" not in lower and ".ma" not in url.lower():
                continue
            s = score(text)
            if s < 30:
                continue

            leads[key] = {
                "company_or_title": title,
                "need": re.sub(r"\s+", " ", content).strip()[:1500],
                "score": s,
                "source_url": url,
                "email_found": email_key,
                "country_hint": "Maroc",
                "found_at": datetime.now(timezone.utc).isoformat(),
            }

    rows = sorted(leads.values(), key=lambda x: x["score"], reverse=True)
    sent = 0

    for lead in rows:
        if sent >= MAX_EMAILS:
            break

        ai = rule_based_qualify(lead["company_or_title"], lead["need"], lead["source_url"])
        lead.update({
            "qualified": bool(ai.get("qualified")),
            "company": ai.get("company", lead["company_or_title"]),
            "email": ai.get("email", ""),
            "reason": ai.get("reason", ""),
        })

        if not lead["qualified"]:
            lead["email_status"] = "not_sent_no_qualified_public_email"
            continue

        email = lead["email"].lower()
        if email in known:
            lead["email_status"] = "not_sent_duplicate"
            continue

        ok, status = send_email(email, ai["subject"], ai["body"])
        lead["email_status"] = status
        if ok:
            history[lead["source_url"] or lead["company_or_title"]] = {
                "email": email,
                "company": lead["company"],
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            known.add(email)
            known.add(lead["source_url"] or lead["company_or_title"])
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
