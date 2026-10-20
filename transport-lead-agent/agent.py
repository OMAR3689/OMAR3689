import csv
import json
import os
import re
import smtplib
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, unquote, quote_plus
from urllib.request import Request, urlopen

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(OUT_DIR, "contacted.json")
LEADS_JSON = os.path.join(OUT_DIR, "leads.json")
LEADS_CSV = os.path.join(OUT_DIR, "leads.csv")
MAX_EMAILS = int(os.environ.get("MAX_EMAILS_PER_RUN", "5"))
PRESENTATION_URL = os.environ.get("PRESENTATION_URL", "https://trmb.ma/").strip()
SMTP_RETRIES = int(os.environ.get("SMTP_RETRIES", "3"))

QUERIES = [
    '"demande de devis transport" Maroc usine industriel distribution',
    '"besoin de transport" Maroc entreprise marchandises usine',
    '"recherche prestataire" transport Maroc entreprise',
    '"appel d offres" transport Maroc entreprise industriel',
    '"transport quotidien" Maroc usine salariés personnel',
    '"livraison" Maroc entreprise distribution marchandises',
    '"affrètement" Maroc industriel chargeur importateur',
    '"location véhicules avec chauffeur" Maroc entreprise',
]

GENERIC_EMAIL_PREFIXES = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon"}
BAD_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com",
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
}
CONTACT_PATHS = (
    "contact", "contact-us", "nous-contacter", "mentions-legales",
    "a-propos", "about", "contactez-nous",
)

BUYER_SIGNALS = (
    "demande de devis", "demander un devis", "besoin de transport", "besoin transport",
    "recherche prestataire", "recherche un prestataire", "appel d'offre", "appel d offres",
    "sous-traitance", "transport quotidien", "navette", "livraison", "distribution",
    "affrètement", "chargeur", "location de véhicules", "véhicules avec chauffeur",
    "transport des salariés", "transport du personnel", "flux de marchandises",
    "approvisionnement", "expédition", "importation", "exportation",
)

PROVIDER_SIGNALS = (
    "société de transport", "societe de transport", "transporteur routier",
    "transporteur international", "entreprise de transport", "transport & logistique",
    "transport et logistique", "prestataire logistique", "freight forwarder",
    "commissionnaire de transport", "notre flotte", "notre flotte de camions",
    "nos camions", "semi-remorques", "camions 8x4", "transport national et international",
    "solution de transport", "services de transport",
)

DIRECTORY_SIGNALS = (
    "fournisseurs transport maroc", "annuaire", "liste des fournisseurs", "top fournisseurs",
    "comparatif", "magazine", "actualités", "actualites", "procurement.ma/fournisseurs",
    "mordorintelligence", "guide", "fiche fournisseur", "directory", "yellow pages",
)

TARGET_SECTOR_SIGNALS = (
    "usine", "industriel", "industrie", "agroalimentaire", "agricole", "ferme",
    "distribution", "grossiste", "retail", "supermarché", "supermarche", "automobile",
    "pharmaceutique", "pharma", "construction", "chantier", "bâtiment", "batiment",
    "importateur", "exportateur", "fabrication", "production", "entrepôt", "entrepot",
    "zone industrielle", "plateforme", "commerce", "hôtel", "hotel", "tourisme",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def http_get(url, timeout=20):
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TRMB-Services-Lead-Agent/1.0)",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read(500000).decode("utf-8", errors="ignore")


def http_json(url, payload, headers):
    req = Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}")
    except URLError as e:
        raise RuntimeError(f"Network error: {e.reason}")


def tavily_search(q):
    if not TAVILY_KEY:
        raise RuntimeError("TAVILY_API_KEY not configured")
    return http_json(
        "https://api.tavily.com/search",
        {"query": q, "search_depth": "basic", "max_results": 8, "include_answer": False},
        {"Content-Type": "application/json", "Authorization": f"Bearer {TAVILY_KEY}"},
    ).get("results", [])


def bing_rss_search(q):
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(q)
    raw = http_get(url, timeout=20)
    root = ET.fromstring(raw)
    results = []
    for item in root.findall(".//item"):
        title = re.sub(r"\s+", " ", (item.findtext("title") or "")).strip()
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        if link:
            results.append({"title": title, "url": link, "content": desc, "source": "bing-rss"})
    return results[:8]


def ddg_html_search(q):
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(q)
    raw = http_get(url, timeout=20)
    results = []
    pattern = r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
    for m in re.finditer(pattern, raw, flags=re.I | re.S):
        link = unquote(m.group(1))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        results.append({"title": title, "url": link, "content": "", "source": "duckduckgo"})
    return results[:8]


def search_web(q):
    if TAVILY_KEY:
        try:
            results = tavily_search(q)
            return results, "tavily"
        except Exception as e:
            print(f"⚠️ Tavily unavailable ({e}); fallback activated")
    try:
        results = bing_rss_search(q)
        if results:
            return results, "bing-rss"
    except Exception as e:
        print(f"⚠️ Bing fallback unavailable: {e}")
    try:
        results = ddg_html_search(q)
        if results:
            return results, "duckduckgo"
    except Exception as e:
        print(f"⚠️ DuckDuckGo fallback unavailable: {e}")
    return [], "none"


def strip_html(raw):
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_page(url):
    if not url or not url.startswith(("http://", "https://")):
        return "", []
    try:
        raw = http_get(url, timeout=12)
        links = re.findall(r'href=["\']([^"\']+)["\']', raw, flags=re.I)
        return strip_html(raw)[:100000], links
    except Exception:
        return "", []


def extract_emails(text):
    out = []
    for email in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or ""):
        email = email.lower().strip(".,;:()[]<>")
        if email.split("@", 1)[0] not in GENERIC_EMAIL_PREFIXES and email not in out:
            out.append(email)
    return out


def domain(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def usable_email(email, url=""):
    if not email or "@" not in email:
        return False
    local, host = email.rsplit("@", 1)
    if len(local) < 2 or "." not in host:
        return False
    host = host.lower()
    page_domain = domain(url)
    return host not in BAD_DOMAINS and (not page_domain or page_domain == host or host.endswith("." + page_domain) or page_domain.endswith("." + host))


def discover_email(url):
    text, links = fetch_page(url)
    if not text and not links:
        return ""
    candidates = []
    for email in extract_emails(text):
        if usable_email(email, url):
            candidates.append(email)
    for link in links:
        absolute = urljoin(url, link)
        if link.lower().startswith("mailto:"):
            candidates.extend(extract_emails(unquote(link[7:].split("?", 1)[0])))
        path = urlparse(absolute).path.lower().strip("/")
        if path in CONTACT_PATHS or any(path.endswith("/" + x) for x in CONTACT_PATHS):
            page_text, _ = fetch_page(absolute)
            candidates.extend(extract_emails(page_text))
    for email in candidates:
        if usable_email(email, url):
            return email
    return ""


def public_email(title, url):
    host = domain(url)
    q = f'site:{host} contact email Maroc' if host else f'"{title}" Maroc contact email'
    results, _ = search_web(q)
    for item in results:
        candidate_url = item.get("url", "") or url
        for email in extract_emails(f"{item.get('title', '')} {item.get('content', '')}"):
            if usable_email(email, candidate_url):
                return email
    return ""


def clean_company(title):
    t = re.sub(r"\s+", " ", (title or "")).strip()
    t = re.sub(r"\s*[|–—-]\s*(Accueil|Home|Contact|Site officiel|Official).*?$", "", t, flags=re.I)
    t = re.sub(r"^(Accueil|Home|Contact)\s*[-|:]\s*", "", t, flags=re.I)
    return t[:100] or "votre entreprise"


def score(text):
    t = (text or "").lower()
    buyer = sum(15 for x in BUYER_SIGNALS if x in t)
    sector = sum(8 for x in TARGET_SECTOR_SIGNALS if x in t)
    provider = sum(25 for x in PROVIDER_SIGNALS if x in t)
    directory = sum(40 for x in DIRECTORY_SIGNALS if x in t)
    return max(0, min(100, buyer + sector - provider - directory))


def is_bad_prospect(title, content, url):
    t = f"{title} {content} {url}".lower()
    if any(x in t for x in DIRECTORY_SIGNALS):
        return True
    provider_hits = sum(1 for x in PROVIDER_SIGNALS if x in t)
    buyer_hits = sum(1 for x in BUYER_SIGNALS if x in t)
    return provider_hits >= 2 and buyer_hits <= 2


def detect_need(content):
    t = (content or "").lower()
    if any(x in t for x in ("transport du personnel", "transport des salariés", "navette", "transport personnel")):
        return "transport de personnel / navettes"
    if any(x in t for x in ("livraison", "distribution", "approvisionnement")):
        return "livraison / distribution"
    if any(x in t for x in ("affrètement", "chargeur", "flux de marchandises")):
        return "affrètement / flux de marchandises"
    if any(x in t for x in ("importation", "exportation", "expédition")):
        return "transport lié aux flux import/export"
    return "transport et logistique"


def build_message(company, need):
    subject = "Proposition transport & logistique – TRMB SERVICES"
    body = (
        "Bonjour,\n\n"
        "Je me permets de vous contacter au nom de TRMB SERVICES, société spécialisée dans les solutions de transport et de logistique au Maroc.\n\n"
        f"Nous accompagnons les entreprises pour leurs besoins de {need}, avec des solutions adaptées aux trajets, volumes, fréquences et contraintes opérationnelles.\n\n"
        f"Nous avons identifié un besoin potentiel chez {company}. Nous serions ravis d'échanger avec votre équipe afin de comprendre votre besoin actuel ou à venir et de vous proposer une solution adaptée.\n\n"
        f"Présentation : {PRESENTATION_URL}\n\n"
        "Si vous avez un besoin de transport, envoyez-nous simplement le trajet, la fréquence et la nature de la marchandise (ou le nombre de personnes à transporter). Nous vous répondrons rapidement.\n\n"
        "Bien cordialement,\n"
        "TRMB SERVICES\n"
        f"{PRESENTATION_URL}"
    )
    return subject, body


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(data):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def history_keys(data):
    keys = set()
    for key, value in data.items():
        keys.add(str(key).lower())
        if isinstance(value, dict):
            for field in ("email", "domain"):
                if value.get(field):
                    keys.add(str(value[field]).lower())
    return keys


def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user
    if not all([host, user, password, sender]):
        return False, "SMTP credentials not configured"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid()
    msg["Reply-To"] = sender
    msg.set_content(body)

    last_error = "unknown SMTP error"
    for attempt in range(1, SMTP_RETRIES + 1):
        try:
            if port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as smtp:
                    smtp.login(user, password)
                    refused = smtp.send_message(msg, from_addr=sender, to_addrs=[to_addr])
            else:
                with smtplib.SMTP(host, port, timeout=30) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                    smtp.login(user, password)
                    refused = smtp.send_message(msg, from_addr=sender, to_addrs=[to_addr])
            if refused:
                return False, f"SMTP refused recipient: {refused}"
            return True, "EMAIL SENT"
        except Exception as e:
            last_error = str(e)
            if attempt < SMTP_RETRIES:
                time.sleep(2 * attempt)
    return False, last_error


def write_leads(rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(LEADS_JSON, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    fields = ["timestamp", "company", "email", "domain", "need", "score", "source", "url", "qualified", "status", "reason"]
    with open(LEADS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history()
    known = history_keys(history)
    leads = {}
    searched = 0
    sources = {}

    print(f"Starting Morocco-only B2B buyer discovery with {len(QUERIES)} queries...")
    for query in QUERIES:
        searched += 1
        print(f'Searching: "{query}"')
        results, source = search_web(query)
        sources[source] = sources.get(source, 0) + 1
        for item in results:
            title = clean_company(item.get("title", ""))
            url = item.get("url", "")
            if not url or not title:
                continue
            host = domain(url)
            if not host or host in BAD_DOMAINS:
                continue
            # Never qualify a search snippet alone: inspect the actual company page first.
            page_text, _ = fetch_page(url)
            content = page_text or item.get("content", "") or ""
            combined = f"{title} {content} {url}"
            if is_bad_prospect(title, content, url):
                continue
            s = score(combined)
            buyer_hits = sum(1 for x in BUYER_SIGNALS if x in combined.lower())
            sector_hit = any(x in combined.lower() for x in TARGET_SECTOR_SIGNALS)
            if buyer_hits == 0 or not sector_hit or s < 20:
                continue
            email = discover_email(url)
            if not email:
                email = public_email(title, url)
            key = email.lower() if email else host
            if key in known or key in leads:
                continue
            need = detect_need(content)
            leads[key] = {
                "timestamp": now_iso(),
                "company": title,
                "email": email,
                "domain": host,
                "need": need,
                "score": s,
                "source": source,
                "url": url,
                "qualified": False,
                "status": "email_found" if email else "no_email",
                "reason": f"buyer_signals={buyer_hits}; sector={'yes' if sector_hit else 'no'}; score={s}",
            }

    rows = sorted(leads.values(), key=lambda x: x.get("score", 0), reverse=True)
    qualified_count = 0
    sent = 0
    email_failures = 0
    no_email = 0

    for row in rows:
        if row.get("email") and sent < MAX_EMAILS:
            qualified_count += 1
            row["qualified"] = True
            subject, body = build_message(row["company"], row["need"])
            ok, status = send_email(row["email"], subject, body)
            if ok:
                sent += 1
                row["status"] = "sent"
                row["sent_at"] = now_iso()
                history[row["email"].lower()] = {
                    "email": row["email"].lower(),
                    "domain": row["domain"],
                    "company": row["company"],
                    "sent_at": row["sent_at"],
                }
                known.add(row["email"].lower())
                known.add(row["domain"].lower())
                print(f"✅ EMAIL SENT: {row['company']} -> {row['email']}")
            else:
                email_failures += 1
                row["status"] = "smtp_failed"
                row["error"] = status
                print(f"❌ EMAIL FAILED: {row['email']}: {status}")
        elif not row.get("email"):
            no_email += 1
            row["status"] = "no_email"

    save_history(history)
    write_leads(rows)
    source_summary = ", ".join(f"{k}={v}" for k, v in sorted(sources.items())) or "none"
    print(f"Searched {searched} queries ({source_summary}); Found {len(rows)} buyer leads; qualified {qualified_count}; sent {sent} emails; SMTP failures {email_failures}; no email {no_email}")


if __name__ == "__main__":
    main()
