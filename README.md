# Klaas Vis AI Workforce v0.2

Lokale basisversie van het Klaas Vis AI Command Center met een werkende Mail Intake Agent. Deze versie analyseert handmatig geplakte e-mails en echte Outlook-mails via Microsoft Graph met Ollama en slaat de resultaten op in SQLite.

Deze versie bevat geen Activepieces-koppeling, ANVA-koppeling, DDI-koppeling, automatische e-mailverzending of definitieve acties. Outlook wordt alleen gelezen.

## Installatie

Maak een virtuele omgeving:

```powershell
python -m venv .venv
```

Activeer de virtuele omgeving:

```powershell
.venv\Scripts\activate
```

Installeer dependencies:

```powershell
pip install -r requirements.txt
```

Maak een `.env` aan op basis van `.env.example`:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
DATABASE_PATH=database/klaasvis_ai.db
APP_SECRET_KEY=change-this-secret
MICROSOFT_TENANT_ID=
MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_REDIRECT_URI=http://localhost:5000/auth/microsoft/callback
MICROSOFT_TOKEN_PATH=database/microsoft_token.json
```

## Ollama

Start Ollama lokaal en download het model:

```powershell
ollama pull qwen2.5:7b
```

Controleer dat Ollama bereikbaar is op:

```text
http://localhost:11434
```

## Applicatie starten

Start de Flask-app:

```powershell
python app.py
```

Open daarna:

```text
http://localhost:5000
```

## Testmail analyseren

1. Ga naar `http://localhost:5000/mail-test`.
2. Vul afzender, ontvanger, onderwerp en e-mailtekst in.
3. Geef aan of er bijlagen aanwezig zijn.
4. Klik op `Analyseer e-mail`.

De Mail Intake Agent toont daarna de classificatie, samenvatting, voorgestelde actie en volledige JSON-output. De analyse wordt opgeslagen in SQLite.

## Outlook koppelen

Maak in Microsoft Entra ID een appregistratie voor deze lokale app.

Gebruik deze redirect URI:

```text
http://localhost:5000/auth/microsoft/callback
```

Gebruik alleen delegated permissions:

```text
User.Read
Mail.Read
offline_access
```

Maak een client secret aan en vul de waarde in `.env` in bij:

```env
MICROSOFT_CLIENT_SECRET=...
```

Start daarna de app en open:

```text
http://localhost:5000/mailbox
```

Klik op `Verbind Outlook`, log in via Microsoft en importeer daarna de laatste 10, ongelezen mails of mails met bijlagen.

De applicatie leest mails via Microsoft Graph, analyseert ze lokaal via Ollama en slaat de resultaten lokaal op. Mails worden niet verwijderd, verplaatst of beantwoord.

## Projectstructuur

```text
klaasvis-ai-workforce/
├── app.py
├── requirements.txt
├── .env.example
├── README.md
├── agents/
├── database/
├── services/
├── templates/
├── static/
└── logs/
```

## Logging

De applicatie logt applicatiestart, Ollama-statuschecks, nieuwe analyses, geslaagde analyses, fallback-analyses en databasefouten in de tabel `agent_logs`.

Outlook OAuth, imports, duplicaten en importfouten worden ook gelogd.

Logs zijn zichtbaar via:

```text
http://localhost:5000/logs
```
