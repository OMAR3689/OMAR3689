import csv
import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

SEARCHES = [
    '"recherche transporteur" Maroc',
    '"prestataire de transport" Maroc',
    '"sous-traitant transport" Maroc',
    '"transport du personnel" Maroc entreprise',
    '"transport de marchandises" "prestataire" Maroc',
    '"besoin de transport" Maroc entreprise',
]

MAX_SENDS = int(os.getenv('MAX_SENDS_PER_RUN', '5'))
MIN_SCORE = int(os.getenv('MIN_SCORE', '80'))
SEND_EMAILS = os.getenv('SEND_EMAILS', 'false').lower() == 'true'


def tavily_search(query):
    r = requests.post(
        'https://api.tavily.com/search',
        json={
            'api_key': os.environ['TAVILY_API_KEY'],
            'query': query,
            'search_depth': 'advanced',
            'max_results': 8,
            'include_answer': False,
        },
        timeout=45,
    )
    r.raise_for_status()
    return r.json().get('results', [])


def openai_score(items):
    prompt = {
        'role': 'system',
        'content': (
            'Tu qualifies des prospects B2B pour TRMB SERVICES (Maroc). '
            'TRMB propose transport du personnel, transport de marchandises et matériels, '
            'logistique de chantier, appui aux chantiers, moyens d’accès/nacelles et moyens techniques. '
            'Ne considère comme lead chaud que les entreprises ayant un signal public récent et concret '
            'd’un besoin de transport/logistique ou de recherche de prestataire. '
            'Retourne JSON uniquement: [{"company":"...","email":"...","score":0,"reason":"...","source":"..."}]. '
            'N’invente jamais une adresse email. Utilise uniquement les emails explicitement publiés dans les résultats.'
        )
    }
    user = {
        'role': 'user',
        'content': json.dumps(items, ensure_ascii=False),
    }
    r = requests.post(
        'https://api.openai.com/v1/chat/completions',
        headers={'Authorization': f"Bearer {os.environ['OPENAI_API_KEY']}", 'Content-Type': 'application/json'},
        json={'model': os.getenv('OPENAI_MODEL', 'gpt-5-mini'), 'temperature': 0.1, 'messages': [prompt, user]},
        timeout=60,
    )
    r.raise_for_status()
    text = r.json()['choices'][0]['message']['content']
    text = re.sub(r'^```json\s*|\s*```$', '', text.strip())
    return json.loads(text)


def load_sent():
    p = Path('prospection/sent_leads.csv')
    if not p.exists():
        return set()
    with p.open(newline='', encoding='utf-8') as f:
        return {row['email'].lower() for row in csv.DictReader(f) if row.get('email')}


def send_email(lead):
    msg = EmailMessage()
    msg['Subject'] = 'TRMB SERVICES – Solution de transport et logistique au Maroc'
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = lead['email']
    msg.set_content(
        f"Bonjour,\n\n"
        f"Nous avons identifié que votre activité peut nécessiter un appui en transport/logistique. "
        f"TRMB SERVICES accompagne les entreprises, industriels et chantiers au Maroc avec des solutions "
        f"de transport du personnel, transport de marchandises et matériels, logistique de chantier et moyens techniques.\n\n"
        f"Nous serions heureux d’échanger avec votre équipe afin de comprendre vos besoins et voir si nous pouvons vous accompagner.\n\n"
        f"Présentation : https://trmb.ma\n\n"
        f"Cordialement,\nTRMB SERVICES\nwww.trmb.ma\n0607 515 430"
    )
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(os.environ['SMTP_HOST'], int(os.environ.get('SMTP_PORT', '465')), context=context) as server:
        server.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
        server.send_message(msg)


def main():
    raw = []
    for q in SEARCHES:
        raw.extend(tavily_search(q))

    unique = {}
    for x in raw:
        key = x.get('url') or x.get('title')
        if key:
            unique[key] = {'title': x.get('title'), 'url': x.get('url'), 'content': x.get('content', '')[:3000]}

    leads = openai_score(list(unique.values()))
    sent = load_sent()
    qualified = [x for x in leads if x.get('email') and int(x.get('score', 0)) >= MIN_SCORE and x['email'].lower() not in sent]
    qualified.sort(key=lambda x: int(x.get('score', 0)), reverse=True)

    Path('prospection').mkdir(exist_ok=True)
    out = Path('prospection/leads_latest.json')
    out.write_text(json.dumps(qualified, ensure_ascii=False, indent=2), encoding='utf-8')

    sent_count = 0
    if SEND_EMAILS:
        for lead in qualified[:MAX_SENDS]:
            send_email(lead)
            with Path('prospection/sent_leads.csv').open('a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['date', 'company', 'email', 'score', 'source'])
                if f.tell() == 0:
                    writer.writeheader()
                writer.writerow({'date': datetime.now(timezone.utc).isoformat(), 'company': lead.get('company',''), 'email': lead['email'], 'score': lead.get('score',0), 'source': lead.get('source','')})
            sent_count += 1

    print(f'Qualified leads: {len(qualified)} | Emails sent: {sent_count}')


if __name__ == '__main__':
    main()
