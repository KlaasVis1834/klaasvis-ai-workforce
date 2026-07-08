# Klaas Vis AI Workforce v0.2

Lokale basisversie van het Klaas Vis AI Command Center met een werkende productiegerichte Mail Intake Agent. Deze versie leest echte Outlook-mails via Microsoft Graph, analyseert ze automatisch met lokale Ollama en slaat de resultaten op in SQLite.

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
MICROSOFT_REDIRECT_URI=http://localhost:5000/outlook/callback
ALLOWED_OUTLOOK_EMAIL=<toegestaan-outlook-account>
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

## Outlook koppelen

Maak in Microsoft Entra ID een appregistratie voor deze lokale app.

Gebruik deze redirect URI:

```text
http://localhost:5000/outlook/callback
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
http://localhost:5000/outlook/accounts
```

Klik op `Verbind Outlook` en log in via Microsoft. Na succesvolle OAuth-login start de Mail Intake Agent automatisch.

Alleen het account uit `ALLOWED_OUTLOOK_EMAIL` mag gekoppeld worden. De applicatie controleert na Microsoft OAuth eerst `/me` via Graph en accepteert alleen een exacte match op `mail` of `userPrincipalName`. Andere accounts worden geweigerd voordat tokens lokaal worden opgeslagen.

De achtergrondservice scant de mailbox elke 15 seconden. Nieuwe Outlook-mails worden automatisch opgehaald, geanalyseerd en opgeslagen. Mails worden niet verwijderd, verplaatst of beantwoord.

Geimporteerde velden:

```text
Subject
Sender
Recipients
Received Date
Body
Attachments metadata
ConversationId
InternetMessageId
```

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

## Realtime dashboard

Het dashboard toont alleen echte Outlook-mails, echte analyses, echte fouten en echte logs. De dashboardpagina ververst automatisch elke 15 seconden.

Status bovenin:

```text
Mail Agent
Outlook
Laatste synchronisatie
Aantal nieuwe mails
Laatste mailbox scan
```

## Logging

De applicatie logt applicatiestart, Ollama-statuschecks, nieuwe analyses, geslaagde analyses, fallback-analyses en databasefouten in de tabel `agent_logs`.

Outlook OAuth, Graph-status, ontvangen mails, Ollama-start, voltooide analyses, database-opslag, dashboard-updates, duplicaten en importfouten worden ook gelogd.

Logs zijn zichtbaar via:

```text
http://localhost:5000/logs
```
