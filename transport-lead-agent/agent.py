import csv, json, os, re, smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.request import Request, urlopen

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
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
    req = Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())

def tavily_search(query):
    if not TAVILY_KEY: raise RuntimeError("TAVILY_API_KEY is required")
    data = http_json("https://api.tavily.com/search", {"api_key": TAVILY_KEY, "query": query, "search_depth": "advanced", "max_results": 10, "include_answer": False}, {"Content-Type": "application/json"})
    return data.get("results", [])

def score(text):
    t = text.lower()
    points = sum(v for k, v in SIGNALS.items() if k in t)
    if any(x in t for x in ["2026", "2027", "urgent", "immediate", "besoin", "demande de devis"]): points += 10
    return min(points, 100)

def openai_qualify(title, content, url):
    if not OPENAI_KEY: return {"qualified": score(title + " " + content) >= 50, "company": title, "email": "", "reason": "Rule-based qualification (OpenAI key not configured)"}
    prompt = f'''Tu es l'agent commercial de TRMB SERVICES, société marocaine de transport/logistique. Analyse ce prospect trouvé en ligne.
Titre: {title}
Contenu: {content[:5000]}
URL: {url}
Retourne UNIQUEMENT JSON avec: qualified (bool), company (string), email (string si clairement public dans le contenu sinon vide), contact_name (string), reason (string), subject (string), body (string).
Ne jamais inventer une adresse email ou un nom. Le message doit être professionnel, court, personnalisé et proposer les services de transport/logistique de TRMB SERVICES avec le site {PRESENTATION_URL}.'''
    data = http_json("https://api.openai.com/v1/responses", {"model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"), "input": prompt}, {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_KEY}"})
    text = data.get("output_text", "")
    try: return json.loads(text)
    except Exception: return {"qualified": False, "company": title, "email": "", "reason": "AI returned invalid JSON"}

def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(history, f, ensure_ascii=False, indent=2)

def send_email(to_addr, subject, body):
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user)
    if not all([host, user, password, sender]): return False, "SMTP credentials not configured"
    msg = EmailMessage(); msg["From"] = sender; msg["To"] = to_addr; msg["Subject"] = subject; msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls(); server.login(user, password); server.send_message(msg)
    return True, "sent"

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    history = load_history(); leads = {}
    for q in QUERIES:
        for item in tavily_search(q):
            title, url, content = item.get("title", "").strip(), item.get("url", "").strip(), item.get("content", "")
            key = url or title.lower()
            if not key or key in history: continue
            text = f"{title} {content}"; s = score(text)
            if s < 30: continue
            leads[key] = {"company_or_title": title, "need": content[:1500].replace("\n", " "), "score": s, "source_url": url, "country_hint": next((c for c in ["Maroc", "France", "Belgique", "Germany", "Allemagne", "Spain", "Italy"] if c.lower() in text.lower()), ""), "found_at": datetime.now(timezone.utc).isoformat()}

    rows = sorted(leads.values(), key=lambda x: x["score"], reverse=True)
    sent = 0
    for lead in rows:
        if sent >= MAX_EMAILS: break
        ai = openai_qualify(lead["company_or_title"], lead["need"], lead["source_url"])
        lead.update({"qualified": bool(ai.get("qualified")), "company": ai.get("company", lead["company_or_title"]), "email": ai.get("email", ""), "reason": ai.get("reason", "")})
        if not lead["qualified"] or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", lead["email"]): continue
        subject = ai.get("subject") or "Proposition de partenariat transport – TRMB SERVICES"
        body = ai.get("body") or f"Bonjour,\n\nNous avons identifié un besoin potentiel de transport/logistique. TRMB SERVICES peut vous accompagner sur vos opérations de transport.\n\nPrésentation : {PRESENTATION_URL}\n\nBien cordialement,\nTRMB SERVICES"
        ok, status = send_email(lead["email"], subject, body)
        lead["email_status"] = status
        if ok:
            history[lead["source_url"] or lead["company_or_title"]] = {"email": lead["email"], "sent_at": datetime.now(timezone.utc).isoformat()}; sent += 1

    save_history(history)
    with open(os.path.join(OUT_DIR, "leads.json"), "w", encoding="utf-8") as f: json.dump(rows, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "leads.csv"), "w", encoding="utf-8", newline="") as f:
        fields = ["company_or_title", "company", "email", "qualified", "score", "email_status", "need", "source_url", "country_hint", "found_at"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print(f"Found {len(rows)} leads; sent {sent} emails")

if __name__ == "__main__": main()
