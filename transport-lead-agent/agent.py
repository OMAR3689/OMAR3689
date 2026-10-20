import csv, json, os, re, smtplib, ssl, time, imaplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import Request, urlopen

TAVILY_KEY=os.environ.get("TAVILY_API_KEY","").strip()
OUT_DIR=os.path.join(os.path.dirname(__file__),"data")
HISTORY_FILE=os.path.join(OUT_DIR,"contacted.json")
MAX_EMAILS=int(os.environ.get("MAX_EMAILS_PER_RUN","5"))
PRESENTATION_URL=os.environ.get("PRESENTATION_URL","https://trmb.ma/")
SMTP_RETRIES=int(os.environ.get("SMTP_RETRIES","3"))
QUERIES=[
'"demande de devis transport" Maroc entreprise',
'"recherche transporteur" Maroc industriel',
'"besoin transport marchandises" Maroc',
'"appel d offres" transport Maroc entreprise',
'"sous-traitance transport" Maroc entreprise',
'"prestataire transport" Maroc industrie',
'"transport quotidien" Maroc usine',
'"livraison" Maroc entreprise prestataire',
'"affrètement" Maroc chargeur',
'"location véhicules avec chauffeur" Maroc entreprise',
'"besoin logistique" Maroc entreprise',
'"transport marchandises" Maroc distribution'
]
GENERIC_EMAIL_PREFIXES={"noreply","no-reply","donotreply","do-not-reply","mailer-daemon"}
BAD_DOMAINS={"linkedin.com","facebook.com","instagram.com","youtube.com","gmail.com","yahoo.com","outlook.com","hotmail.com"}
CONTACT_PATHS=("contact","contact-us","nous-contacter","mentions-legales","a-propos","about")
POSITIVE=("demande de devis","recherche transporteur","besoin de transport","besoin transport","appel d'offre","appel d offres","sous-traitance","prestataire transport","transport quotidien","affrètement","chargeur","livraison","distribution","industrie","usine","marchandises","flotte","logistique")
COMPETITOR=("société de transport","transporteur routier","transporteur international","freight forwarder","commissionnaire de transport","entreprise de transport")

def now_iso(): return datetime.now(timezone.utc).isoformat()
def http_json(url,payload,headers):
    req=Request(url,data=json.dumps(payload).encode(),headers=headers,method="POST")
    try:
        with urlopen(req,timeout=45) as r:return json.loads(r.read().decode())
    except HTTPError as e:raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except URLError as e:raise RuntimeError(f"Network error: {e.reason}")
def tavily_search(q):
    if not TAVILY_KEY:raise RuntimeError("TAVILY_API_KEY is required")
    return http_json("https://api.tavily.com/search",{"query":q,"search_depth":"advanced","max_results":10,"include_answer":False},{"Content-Type":"application/json","Authorization":f"Bearer {TAVILY_KEY}"}).get("results",[])
def fetch_page(url):
    try:
        with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0 TRMB lead research bot"}),timeout=12) as r:raw=r.read(400000).decode("utf-8",errors="ignore")
        text=re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>"," ",raw,flags=re.I);text=re.sub(r"\s+"," ",text).strip()
        return text[:100000],re.findall(r'href=["\']([^"\']+)["\']',raw,flags=re.I)
    except Exception:return "",[]
def extract_emails(text):
    out=[]
    for e in re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",text or ""):
        e=e.lower().strip(".,;:()[]<>")
        if e.split("@",1)[0] not in GENERIC_EMAIL_PREFIXES and e not in out:out.append(e)
    return out
def domain(url):
    try:return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:return ""
def usable_email(e,url=""):
    if not e or "@" not in e:return False
    d=e.split("@",1)[1].lower()
    return d not in BAD_DOMAINS and (not url or domain(url) not in BAD_DOMAINS)
def discover_email(url):
    if not url:return ""
    text,links=fetch_page(url); pool=[url]
    for link in links:
        absolute=urljoin(url,link)
        if link.lower().startswith("mailto:"):
            for e in extract_emails(unquote(link[7:].split("?",1)[0])):
                if usable_email(e,url):return e
        p=urlparse(absolute);path=p.path.lower().strip("/")
        if p.scheme in ("http","https") and (path in CONTACT_PATHS or any(path.endswith("/"+x) for x in CONTACT_PATHS)):pool.append(absolute)
    for u in list(dict.fromkeys(pool))[:8]:
        text,links=fetch_page(u)
        for e in extract_emails(text):
            if usable_email(e,u):return e
    return ""
def public_email(title,url):
    q=f'site:{domain(url)} email contact' if domain(url) else f'"{title}" Maroc email contact'
    try:r=tavily_search(q)
    except Exception:return ""
    for x in r:
        for e in extract_emails(f"{x.get('title','')} {x.get('content','')}"):
            if usable_email(e,x.get("url","") or url):return e
    return ""
def score(text):
    t=text.lower();p=sum(10 for x in POSITIVE if x in t)-sum(15 for x in COMPETITOR if x in t)
    return max(0,min(100,p))
def qualify(title,content,url,email):
    text=f"{title} {content}";t=text.lower();s=score(text)
    # Require a concrete business/transport signal, but do not impose the old score>=30 gate.
    intent=any(x in t for x in POSITIVE)
    email=email or ""
    ok=bool(email and usable_email(email,url) and intent)
    need=re.sub(r"\s+"," ",content).strip()[:700]
    body=("Bonjour,\n\nTRMB SERVICES accompagne les entreprises au Maroc pour leurs besoins de transport et de logistique.\n\n"
          f"Contexte identifié : {need}\n\nPrésentation : {PRESENTATION_URL}\n\n"
          "Si vous avez un besoin actuel ou à venir, nous pouvons vous proposer rapidement une solution adaptée.\n\n"
          "Bien cordialement,\nTRMB SERVICES\nhttps://trmb.ma/")
    return ok,email,f"intent={'oui' if intent else 'non'}; email public={'oui' if email else 'non'}; score={s}",body
def load_history():
    try:
        with open(HISTORY_FILE,encoding="utf-8") as f:d=json.load(f);return d if isinstance(d,dict) else {}
    except (FileNotFoundError,json.JSONDecodeError):return {}
def save_history(d):
    with open(HISTORY_FILE,"w",encoding="utf-8") as f:json.dump(d,f,ensure_ascii=False,indent=2)
def history_keys(d):
    k=set(d.keys())
    for v in d.values():
        if isinstance(v,dict) and v.get("email"):k.add(v["email"].lower())
    return k
def save_to_sent(raw):
    host=os.environ.get("IMAP_HOST",os.environ.get("SMTP_HOST","")).strip();port=int(os.environ.get("IMAP_PORT","993"));user=os.environ.get("IMAP_USER",os.environ.get("SMTP_USER","")).strip();pw=os.environ.get("IMAP_PASSWORD",os.environ.get("SMTP_PASSWORD",""))
    if not all([host,user,pw]):return False,"IMAP not configured"
    # Avoid LIST wildcard/pattern parsing; try explicit cPanel/common names.
    candidates=[x.strip() for x in os.environ.get("IMAP_SENT_FOLDER","Sent").split(",") if x.strip()]
    candidates += ["Sent","INBOX.Sent","INBOX/Sent","Sent Items","Sent Mail","Sent Messages"]
    seen=set()
    try:
        with imaplib.IMAP4_SSL(host,port) as imap:
            imap.login(user,pw)
            for target in candidates:
                if target.lower() in seen:continue
                seen.add(target.lower())
                try:
                    ok,_=imap.append(target,"\\Seen",imaplib.Time2Internaldate(time.time()),raw)
                    if ok=="OK":return True,"saved to Sent: "+target
                except Exception:continue
            return False,"Sent folder not writable/found"
    except Exception as e:return False,f"IMAP Sent save error: {e}"
def send_email(to_addr,subject,body):
    host=os.environ.get("SMTP_HOST","").strip();port=int(os.environ.get("SMTP_PORT","465"));user=os.environ.get("SMTP_USER","").strip();pw=os.environ.get("SMTP_PASSWORD","");sender=os.environ.get("SMTP_FROM","").strip() or user
    if not all([host,user,pw,sender]):return False,"SMTP credentials not configured",{}
    msg=EmailMessage();msg["From"]=sender;msg["To"]=to_addr;msg["Subject"]=subject;msg["Date"]=formatdate(localtime=False);msg["Message-ID"]=make_msgid();msg["Reply-To"]=sender;msg.set_content(body);raw=msg.as_bytes()
    for attempt in range(1,SMTP_RETRIES+1):
        try:
            refused={}
            if port==465:
                with smtplib.SMTP_SSL(host,port,timeout=30,context=ssl.create_default_context()) as s:s.login(user,pw);refused=s.send_message(msg,from_addr=sender,to_addrs=[to_addr])
            else:
                with smtplib.SMTP(host,port,timeout=30) as s:s.ehlo();s.starttls(context=ssl.create_default_context());s.ehlo();s.login(user,pw);refused=s.send_message(msg,from_addr=sender,to_addrs=[to_addr])
            if refused:return False,f"SMTP refused recipient: {refused}",{"message_id":msg["Message-ID"],"sender":sender}
            saved,status=save_to_sent(raw);return True,"sent_and_saved_to_sent" if saved else "sent_smtp_accepted",{"message_id":msg["Message-ID"],"sender":sender,"sent_folder":status,"smtp_accepted":True}
        except (smtplib.SMTPException,OSError) as e:
            if attempt<SMTP_RETRIES:time.sleep(2*attempt)
            else:return False,f"SMTP error: {e}",{"sender":sender}
    return False,"SMTP failed",{}
def main():
    os.makedirs(OUT_DIR,exist_ok=True);history=load_history();known=history_keys(history);leads={}
    print(f"Starting Morocco-only B2B discovery with {len(QUERIES)} Tavily queries...")
    for q in QUERIES:
        print(f"Searching: {q}")
        try:results=tavily_search(q)
        except Exception as e:print(f"⚠️ query skipped: {e}");continue
        for item in results:
            title=item.get("title","").strip();url=item.get("url","").strip();content=item.get("content","");text=f"{title} {content}";key=url or title.lower()
            if not key or key in known:continue
            if "maroc" not in text.lower() and ".ma" not in url.lower():continue
            email=next((e for e in extract_emails(text) if usable_email(e,url)),"") or discover_email(url) or public_email(title,url)
            if email and email in known:continue
            if not any(x in text.lower() for x in POSITIVE):continue
            leads[key]={"company_or_title":title,"need":re.sub(r"\s+"," ",content).strip()[:1500],"score":score(text),"source_url":url,"email_found":email,"country_hint":"Maroc","found_at":now_iso()}
    rows=sorted(leads.values(),key=lambda x:x["score"],reverse=True);sent=qualified_count=email_failures=no_email=0
    for lead in rows:
        if sent>=MAX_EMAILS:break
        ok,email,reason,body=qualify(lead["company_or_title"],lead["need"],lead["source_url"],lead.get("email_found",""));lead.update({"qualified":ok,"company":lead["company_or_title"],"email":email,"reason":reason})
        if not email:no_email+=1
        if not ok:continue
        qualified_count+=1
        success,status,details=send_email(email,"Proposition de transport & logistique – TRMB SERVICES",body);lead["email_status"]=status;lead["message_id"]=details.get("message_id","");lead["sender"]=details.get("sender","");lead["sent_folder"]=details.get("sent_folder","")
        if success:
            history[lead["source_url"] or lead["company_or_title"]]={"email":email,"company":lead["company"],"message_id":details.get("message_id",""),"sender":details.get("sender",""),"sent_at":now_iso()};known.update({email,lead["source_url"] or lead["company_or_title"]});sent+=1;print(f"✅ EMAIL SENT: {details.get('sender','')} -> {email} | {details.get('message_id','')}")
        else:email_failures+=1;print(f"❌ EMAIL FAILED: {email}: {status}")
    save_history(history)
    with open(os.path.join(OUT_DIR,"leads.json"),"w",encoding="utf-8") as f:json.dump(rows,f,ensure_ascii=False,indent=2)
    print(f"Found {len(rows)} leads; qualified {qualified_count}; sent {sent} emails; SMTP failures {email_failures}; no email {no_email}")
if __name__=="__main__":main()
