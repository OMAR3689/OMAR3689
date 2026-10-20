import csv
import json
import os
import re
import smtplib
import ssl
import time
import imaplib
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

# Search buyers/chargers, not transport providers or directories.
QUERIES = [
    '"demande de devis transport" Maroc usine OR industriel OR distribution',
    '"besoin de transport" Maroc entreprise marchandises usine',
    '"recherche prestataire" transport Maroc entreprise',
    '"appel d offres" transport Maroc entreprise industriel',
    '"transport quotidien" Maroc usine OR salariés OR personnel',
    '"livraison" Maroc entreprise distribution marchandises',
    '"affrètement" Maroc industriel chargeur importateur',
    '"location véhicules avec chauffeur" Maroc entreprise',
]

GENERIC_EMAIL_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
}
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
    "fournisseurs transport maroc", "annuaire", "liste des fournisseurs",
    "top fournisseurs", "comparatif", "magazine", "actualités", "actualites",
    "procurement.ma/fournisseurs", "mordorintelligence", "guide", "fiche fournisseur",
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
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TRMB-Services-Lead-Agent/1.0)",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        },
    )
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
    """No-key fallback. Bing RSS is used only when Tavily is unavailable/quota-limited."""
    url = "https://www.bing.com/search?format=rss&q=" + quote_plus(q)
    raw = http_get(url, timeout=20)
    root = ET.fromstring(raw)
    results = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()
        if link:
            results.append({"title": title, "url": link, "content": desc, "source": "bing-rss"})
    return results[:8]


def ddg_html_search(q):
    """Second no-key fallback for runners where Bing RSS is unavailable."""
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(q)
    raw = http_get(url, timeout=20)
    results = []
    for m in re.finditer(
        r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        raw,
        flags=re.I | re.S,
    ):
        link = unquote(m.group(1))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        results.append({"title": title, "url": link, "content": "", "source": "duckduckgo"})
    return results[:8]


def search_web(q):
    """Tavily first; automatic no-key fallbacks prevent HTTP 432 from stopping discovery."""
    if TAVILY_KEY:
        try:
            return tavily_search(q), "tavily"
        except Exception as e:
            print(f"⚠️ Tavily unavailable ({e}); using Bing fallback")
    try:
        results = bing_rss_search(q)
        if results:
            return results, "bing-rss"
    except Exception as e:
        print(f"⚠️ Bing fallback unavailable: {e}")
    try:
        results = ddg_html_search(q)
        return results, "duckduckgo"
    except Exception as e:
        print(f"⚠️ Search fallback unavailable: {e}")
        return [], "none"


def fetch_page(url):
    try:
        raw = http_get(url, timeout=12)
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", " ", raw, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip()
        links = re.findall(r'href=["\']([^"\']+)["\']', raw, flags=re.I)
        return text[:100000], links
    except Exception:
        return "", []


def extract_emails(text):
    out = []
    for e in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or ""):
        e = e.lower().strip(".,;:()[]<>")
        if e.split("@", 1)[0] not in GENERIC_EMAIL_PREFIXES and e not in out:
            out.append(e)
    return out


def domain(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def usable_email(email, url=""):
    if not email or "@" not in email:
        return False
    local, d = email.rsplit("@", 1)
    if len(local) < 2 or "." not in d:
        return False
    d = d.lower()
    return d not in BAD_DOMAINS and (not url or domain(url) not in BAD_DOMAINS)


def discover_email(url):
    if not url:
        return ""
    text, links = fetch_page(url)
    pool = [url]
    for link in links:
        absolute = urljoin(url, link)
        if link.lower().startswith("mailto:"):
            for email in extract_emails(unquote(link[7:].split("?", 1)[0])):
                if usable_email(email, url):
                    return email
        p = urlparse(absolute)
        path = p.path.lower().strip("/")
        if p.scheme in ("http", "https") and (
            path in CONTACT_PATHS or any(path.endswith("/" + x) for x in CONTACT_PATHS)
        ):
            pool.append(absolute)
    for page_url in list(dict.fromkeys(pool))[:8]:
        page_text, _ = fetch_page(page_url)
        for email in extract_emails(page_text):
            if usable_email(email, page_url):
                return email
    for email in extract_emails(text):
        if usable_email(email, url):
            return email
    return ""


def public_email(title, url):
    q = f'site:{domain(url)} email contact' if domain(url) else f'"{title}" Maroc email contact'
    try:
        results, _ = search_web(q)
    except Exception:
        return ""
    for item in results:
        for email in extract_emails(f"{item.get('title', '')} {item.get('content', '')}"):
            if usable_email(email, item.get("url", "") or url):
                return email
    return ""


def score(text):
    t = text.lower()
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
    if any(x in t for x in ("transport du personnel", "transport des salariés", "navette")):
        return "transport de personnel / navettes"
    if any(x in t for x in ("livraison", "distribution", "approvisionnement")):
        return "livraison / distribution"
    if any(x in t for x in ("affrètement", "chargeur", "flux de marchandises")):
        return "affrètement / flux de marchandises"
    if any(x in t for x in ("importation", "exportation", "expédition")):
        return "transport lié aux flux import/export"
    return "transport et logistique"


def clean_company(title):
    t = re.sub(r"\s+", " ", (title or "")).strip()
    t = re.sub(r"\s*[|–—-]\s*(Accueil|Home|Contact|Site officiel|Official).*?$", "", t, flags=re.I)
    return t[:100] or "votre entreprise"


def qualify(title, content, url, email):
    text = f"{title} {content}"
    t = text.lower()
    s = score(text)
    intent_hits = [x for x in BUYER_SIGNALS if x in t]
    sector = any(x in t for x in TARGET_SECTOR_SIGNALS)
    bad = is_bad_prospect(title, content, url)
    email = email or ""
    ok = bool(email and usable_email(email, url) and intent_hits and not bad and (sector or s >= 35))
    need = detect_need(content)
    company = clean_company(title)
    subject = f"Proposition transport & logistique – TRMB SERVICES"
    body = (
        "Bonjour,\n\n"
        "Je me permets de vous contacter au nom de TRMB SERVICES.\n\n"
        f"Nous accompagnons les entreprises au Maroc sur leurs besoins de {need}.\n\n"
        f"En consultant les informations publiques concernant {company}, nous avons identifié un besoin potentiel lié à {need}. "
        "Nous pouvons étudier vos flux, vos fréquences et vos contraintes afin de vous proposer une solution de transport adaptée.\n\n"
        f"Présentation : {PRESENTATION_URL}\n\n"
        "Si vous avez un besoin actuel ou à venir, envoyez-nous simplement votre trajet, la marchandise "
        "ou le nombre de personnes à transporter, et nous vous répondrons rapidement.\n\n"
        "Bien cordialement,\n"
        "TRMB SERVICES\n"
        f"{PRESENTATION_URL}"
    )
    reason = (
        f"intent={'oui' if intent_hits else 'non'}; secteur={'oui' if sector else 'non'}; "
        f"provider={'oui' if bad else 'non'}; score={s}"
    )
    return ok, email, reason, subject, body


def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def history_keys(data):
    keys = set()
    for key, value in data.items():
        keys.add(str(key).lower())
        if isinstance(value, dict):
            if value.get("email"):
                keys.add(value["email"].lower())
            if value.get("domain"):
                keys.add(value["domain"].lower())
    return keys


def save_to_sent(raw):
    """Best effort only. SMTP success must never be turned into a failure because IMAP folder naming differs."""
    host = os.environ.get("IMAP_HOST", os.environ.get("SMTP_HOST", "")).strip()
    port = int(os.environ.get("IMAP_PORT", "993"))
    user = os.environ.get("IMAP_USER", os.environ.get("SMTP_USER", "")).strip()
    password = os.environ.get("IMAP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
    if not all([host, user, password]):
        return False, "IMAP not configured"
    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)
            status, boxes = imap.list()
            names = []
            if status == "OK":
                for row in boxes or []:
                    line = row.decode(errors="ignore")
                    match = re.search(r'\)\s+"[^"]*"\s+(.+)$', line)
                    if match:
                        mailbox = match.group(1).strip().strip('"')
                        names.append(mailbox)
            preferred = []
            configured = os.environ.get("IMAP_SENT_FOLDER", "").strip()
            if configured:
                preferred.append(configured)
            preferred += [n for n in names if any(k in n.lower() for k in ("sent", "envoy"))]
            preferred += ["Sent", "INBOX.Sent", "INBOX/Sent"]
            seen = set()
            for mailbox in preferred:
                if not mailbox or mailbox.lower() in seen:
                    continue
                seen.add(mailbox.lower())
                try:
                    ok, _ = imap.append(mailbox, "\\Seen", imaplib.Time2Internaldate(time.time()), raw)
                    if ok == "OK":
                        return True, "saved to Sent: " + mailbox
                except Exception:
                    continue
            return False, "Sent folder not found/writable (SMTP was still successful)"
    except Exception as e:
        return False, f"IMAP Sent save warning: {e}"


def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user
    if not all([host, user, password, sender]):
        return False, "SMTP credentials not configured", {}
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid()
    msg["Reply-To"] = sender
    msg.set_content(body)
    raw = msg.as_bytes()

    for attempt in range(1, SMTP_RETRIES + 1):
        try:
            refused = {}
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context()) as smtp:
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
                return False, f"SMTP refused recipient: {refused}", {"message_id": msg["Message-ID"], "sender": sender}
            saved, status = save_to_sent(raw)
            return True, "sent_and_saved_to_sent" if saved else "sent_smtp_accepted", {
                "message_id": msg["Message-ID"],
                "sender": sender,
                "sent_folder": status,
                "smtp_accepted": True,
            }
        except (smtplib.SMTPException, OSError) as e:
            if attempt < SMTP_RETRIES:
                time.sleep(2 * attempt)
            else:
                return False, f"SMTP error: {e}", {"sender": sender}
    return False, "SMTP failed", {}


def write_outputs(leads):
    with open(LEADS_JSON, "w", encoding="utf-8") as f:
        json.dump(list(leads.values()), f, ensure_ascii=False, indent=2)
    fields = [
        "company_or_title", "company", "email", "need", "score", "qualified",
        "email_status", "source_url", "domain", "reason", "message_id", "sender", "checked_at",
    ]
    with open(LEADS_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(leads.values())


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history()
    known = history_keys(history)
    leads = {}
    searched = 0
    providers = {}

    print(f"Starting Morocco-only B2B buyer discovery with {len(QUERIES)} targeted queries...")
    for query in QUERIES:
        searched += 1
        print(f"Searching: {query}")
        results, provider = search_web(query)
        providers[provider] = providers.get(provider, 0) + 1
        for item in results:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            content = (item.get("content") or "").strip()
            text = f"{title} {content}"
            key = url or title.lower()
            d = domain(url)
            if not key or key.lower() in known or (d and d in known):
                continue
            if "maroc" not in text.lower() and ".ma" not in url.lower():
                continue
            if is_bad_prospect(title, content, url):
                continue
            if not any(x in text.lower() for x in BUYER_SIGNALS):
                continue
            email = next((e for e in extract_emails(text) if usable_email(e, url)), "")
            if not email:
                email = discover_email(url)
            if not email:
                email = public_email(title, url)
            if email and email.lower() in known:
                continue
            leads[key] = {
                "company_or_title": clean_company(title),
                "company": clean_company(title),
                "need": detect_need(content),
                "score": score(text),
                "source_url": url,
                "domain": d,
                "email_found": email,
                "qualified": False,
                "email": "",
                "email_status": "not_sent",
                "reason": "",
                "message_id": "",
                "sender": "",
                "checked_at": now_iso(),
            }

    sent = 0
    qualified_count = 0
    smtp_failures = 0
    no_email = 0

    ranked = sorted(leads.values(), key=lambda x: x.get("score", 0), reverse=True)
    for lead in ranked:
        if sent >= MAX_EMAILS:
            break
        ok, email, reason, subject, body = qualify(
            lead["company_or_title"], lead["need"], lead["source_url"], lead.get("email_found", "")
        )
        lead.update({"qualified": ok, "company": lead["company_or_title"], "email": email, "reason": reason})
        if not email:
            no_email += 1
        if not ok:
            continue
        qualified_count += 1
        success, status, details = send_email(email, subject, body)
        lead["email_status"] = status
        lead["message_id"] = details.get("message_id", "")
        lead["sender"] = details.get("sender", "")
        if success:
            sent += 1
            history_key = email.lower()
            history[history_key] = {
                "company": lead["company"],
                "email": email,
                "domain": lead.get("domain", ""),
                "source_url": lead.get("source_url", ""),
                "need": lead.get("need", ""),
                "sent_at": now_iso(),
                "status": status,
            }
            known.add(history_key)
            if lead.get("domain"):
                known.add(lead["domain"].lower())
            print(f"✅ EMAIL SENT: {email} | {status}")
        else:
            smtp_failures += 1
            print(f"❌ EMAIL FAILED: {email}: {status}")

    write_outputs(leads)
    save_history(history)
    print(
        f"Searched {searched} queries; Found {len(leads)} buyer leads; "
        f"qualified {qualified_count}; sent {sent} emails; SMTP failures {smtp_failures}; no email {no_email}"
    )
    print("Search providers used: " + ", ".join(f"{k}={v}" for k, v in sorted(providers.items())))


if __name__ == "__main__":
    main()
