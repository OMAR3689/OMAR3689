import csv, json, os, re, smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(OUT_DIR, "contacted.json")
MAX_EMAILS = int(os.environ.get("MAX_EMAILS_PER_RUN", "5"))
PRESENTATION_URL = os.environ.get("PRESENTATION_URL", "https://trmb.ma/")

QUERIES = [
    '"recherche transporteur" Maroc entreprise -transporteur -logistique',
    '"besoin de transport" Maroc entreprise marchandises -transporteur',
    '"demande de devis" "transport" Maroc industriel',
    '"appel d offres" "transport" Maroc marchandises entreprise',
    '"sous-traitance" "transport" Maroc marchandises',
    '"location de véhicules avec chauffeur" Maroc entreprise',
    '"transport de marchandises" "besoin" Maroc entreprise',
    '"prestataire logistique" Maroc "demande" entreprise',
    '"transport quotidien" Maroc usine entreprise',
    '"livraison" Maroc entreprise "prestataire transport"',
    '"flotte" Maroc entreprise "transport" besoin',
    '"affrètement" Maroc "chargeur" entreprise',
]

POSITIVE_SIGNALS = {
    "recherche transporteur": 40, "besoin de transport": 40,
    "demande de devis": 35, "appel d'offres": 35, "appel d offres": 35,
    "sous-traitance": 30, "location de véhicules avec chauffeur": 30,
    "transport de marchandises": 20, "transport quotidien": 25,
    "besoin": 10, "marchandises": 10, "chargeur": 15, "logistique": 8,
}

COMPETITOR_SIGNALS = {
    "commissionnaire de transport": 45, "société de transport": 35,
    "transporteur": 25, "freight forwarder": 30, "freight forwarding": 30,
    "prestataire transport": 25, "solution de transport": 15,
    "flotte de camions": 25, "camions disponibles": 25, "bourse de fret": 30,
}

GENERIC_EMAIL_PREFIXES = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon"}
GENERIC_PROVIDER_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com",
    "procurement.ma", "datao.ma", "kerix-export.net", "kerix.net",
    "google.com", "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
}
CONTACT_PATHS = ("contact", "contact-us", "nous-contacter", "a-propos", "about", "mentions-legales")


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
    payload = {"query": query, "search_depth": "advanced", "max_results": 10, "include_answer": False}
    return http_json("https://api.tavily.com/search", payload, headers).get("results", [])


def fetch_page(url):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 TRMB lead research bot"})
        with urlopen(req, timeout=12) as r:
            raw = r.read(400000).decode("utf-8", errors="ignore")
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", raw, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        links = re.findall(r'href=["\']([^"\']+)["\']', raw, flags=re.I)
        return text[:100000], links
    except Exception:
        return "", []


def discover_contact_email(url):
    if not url:
        return ""
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
    candidates = [url]
    _, links = fetch_page(url)
    for link in links:
        absolute = urljoin(url, link)
        path = urlparse(absolute).path.lower().strip("/")
        if path in CONTACT_PATHS or any(path.endswith("/" + p) for p in CONTACT_PATHS):
            candidates.append(absolute)
    for candidate in candidates[:5]:
        text, _ = fetch_page(candidate)
        for email in extract_emails(text):
            if usable_email(email, candidate):
                return email
    return ""


def score(text):
    t = text.lower()
    points = sum(v for k, v in POSITIVE_SIGNALS.items() if k in t)
    points -= sum(v for k, v in COMPETITOR_SIGNALS.items() if k in t)
    if any(x in t for x in ["2026", "2027", "urgent", "immédiat", "immediate", "en cours"]):
        points += 10
    return max(0, min(points, 100))


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


def source_domain(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def usable_email(email, url=""):
    if not email or "@" not in email:
        return False
    domain = email.split("@", 1)[1].lower()
    if domain in GENERIC_PROVIDER_DOMAINS:
        return False
    src = source_domain(url)
    if src in GENERIC_PROVIDER_DOMAINS:
        return False
    return True


def looks_stale(text):
    years = [int(y) for y in re.findall(r"\b20\d{2}\b", text.lower())]
    return bool(years and max(years) < datetime.now(timezone.utc).year - 1)


def rule_based_qualify(title, content, url, email=""):
    text = f"{title} {content}"
    s = score(text)
    email = email or (extract_emails(text)[0] if extract_emails(text) else "")
    stale = looks_stale(text)
    qualified = s >= 30 and bool(email) and usable_email(email, url) and not stale
    need = re.sub(r"\s+", " ", content).strip()[:500]
    body = (
        "Bonjour,\n\n"
        "Nous avons identifié un besoin potentiel en transport/logistique lié à votre activité. "
        "TRMB SERVICES peut vous accompagner avec des solutions de transport et de logistique adaptées à vos opérations au Maroc.\n\n"
        f"Besoin identifié : {need}\n\n"
        f"Présentation TRMB SERVICES : {PRESENTATION_URL}\n\n"
        "Si ce besoin est toujours d’actualité, nous pouvons vous transmettre rapidement une proposition adaptée.\n\n"
        "Bien cordialement,\nTRMB SERVICES\nhttps://trmb.ma/"
    )
    reason = f"score={s}; email public={'oui' if email else 'non'}; prospect acheteur={'oui' if s >= 30 else 'non'}; stale={'oui' if stale else 'non'}"
    return {"qualified": qualified, "company": title, "email": email, "reason": reason,
            "subject": "Proposition de transport & logistique – TRMB SERVICES", "body": body}


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
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user).strip()
    if not all([host, user, password, sender]):
        return False, "SMTP credentials not configured"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to_addr, subject
    msg.set_content(body)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as server:
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                server.ehlo(); server.starttls(); server.ehlo()
                server.login(user, password); server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        return False, f"SMTP error: {e}"
    return True, "sent"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history(); known = history_keys(history); leads = {}
    print(f"Starting Morocco-only B2B discovery with {len(QUERIES)} Tavily queries...")
    for q in QUERIES:
        print(f"Searching: {q}")
        try: results = tavily_search(q)
        except RuntimeError as e:
            print(f"⚠️ Tavily query skipped: {e}"); continue
        for item in results:
            title = item.get("title", "").strip(); url = item.get("url", "").strip(); content = item.get("content", "")
            text = f"{title} {content}"; key = url or title.lower()
            emails = [e for e in extract_emails(text) if usable_email(e, url)]
            email_key = emails[0] if emails else ""
            if not email_key and url:
                email_key = discover_contact_email(url)
            if not key or key in known or (email_key and email_key in known): continue
            lower = text.lower()
            if "maroc" not in lower and ".ma" not in url.lower(): continue
            s = score(text)
            if s < 30: continue
            leads[key] = {"company_or_title": title, "need": re.sub(r"\s+", " ", content).strip()[:1500],
                          "score": s, "source_url": url, "email_found": email_key,
                          "country_hint": "Maroc", "found_at": datetime.now(timezone.utc).isoformat()}

    rows = sorted(leads.values(), key=lambda x: x["score"], reverse=True); sent = 0
    for lead in rows:
        if sent >= MAX_EMAILS: break
        ai = rule_based_qualify(lead["company_or_title"], lead["need"], lead["source_url"], lead.get("email_found", ""))
        lead.update({"qualified": bool(ai["qualified"]), "company": ai["company"], "email": ai["email"], "reason": ai["reason"]})
        if not lead["qualified"]:
            lead["email_status"] = "not_sent_not_qualified"; continue
        email = lead["email"].lower()
        if email in known:
            lead["email_status"] = "not_sent_duplicate"; continue
        ok, status = send_email(email, ai["subject"], ai["body"]); lead["email_status"] = status
        if ok:
            history[lead["source_url"] or lead["company_or_title"]] = {"email": email, "company": lead["company"], "sent_at": datetime.now(timezone.utc).isoformat()}
            known.update({email, lead["source_url"] or lead["company_or_title"]}); sent += 1
        else:
            print(f"⚠️ Email failed for {email}: {status}")

    save_history(history)
    with open(os.path.join(OUT_DIR, "leads.json"), "w", encoding="utf-8") as f: json.dump(rows, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "leads.csv"), "w", encoding="utf-8", newline="") as f:
        fields = ["company_or_title", "company", "email", "qualified", "score", "email_status", "reason", "need", "source_url", "country_hint", "found_at"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print(f"Found {len(rows)} leads; sent {sent} emails")


if __name__ == "__main__": main()
