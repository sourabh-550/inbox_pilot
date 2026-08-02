# 📬 InboxPilot

An AI assistant that reads your inbox, figures out what actually matters, and only interrupts you when something genuinely needs your decision.

> 🚧 **Status: In active development.** Core pipeline (email classification, extraction, and confidence scoring) is built and tested. Notion integration, calendar scheduling, conflict detection, and notifications are still in progress. See [Build Status](#build-status) below for details.

## What it does

InboxPilot connects to your Gmail inbox, reads each new email, and uses an AI model to figure out:
- What kind of email is this — a job opportunity, a meeting, a deadline, or just noise?
- What are the key details — who it's from, when it's happening, what it's about?
- How confident is the system in its own reading?

Based on that, it automatically:
- Logs important items into an organized Notion tracker
- Creates Google Calendar events for anything with a clear date — after checking for scheduling conflicts first
- Sends a Telegram notification only when something actually needs a human look (a conflict, or a low-confidence reading)

It also reads PDF and DOCX attachments — so a job description or meeting agenda gets understood too, not just the email body.

## Why

Inboxes fill up with a mix of things that matter and things that don't. Reading and organizing everything by hand is slow and things get missed. InboxPilot automates the reading and sorting, and only asks for your input when it's actually needed.

## Tech stack

| Layer | Tool |
|---|---|
| Workflow orchestration | n8n |
| Classification & extraction | Python + FastAPI |
| LLM | Groq |
| Document parsing | pdfplumber / python-docx |
| Tracker | Notion API |
| Scheduling | Google Calendar API |
| Notifications | Telegram Bot API |
| Dedup state | SQLite |
| Semantic search (stretch) | FAISS + sentence-transformers |

## Build status

- [x] Project scaffold
- [x] Email classification + structured extraction (Groq, tested against multiple real cases)
- [x] Confidence scoring — computed from AI-flagged uncertainty, not self-reported by the model
- [ ] Attachment parsing (PDF/DOCX)
- [ ] Notion integration
- [ ] Calendar integration + conflict detection
- [ ] Telegram notifications
- [ ] n8n workflow wiring
- [ ] Deduplication
- [ ] End-to-end testing on a real inbox
- [ ] Stretch: semantic search, draft-reply suggestions

## Setup

```bash
cd microservice
python -m venv venv
venv\Scripts\activate      # Windows
pip install -r requirements.txt
cp .env.example .env       # then fill in your real API keys
uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/docs` to test the `/process` endpoint directly.

## Roadmap (V2)

A planned second phase extends InboxPilot into an email security and threat-intelligence layer — phishing risk scoring, header authentication checks (SPF/DKIM/DMARC), IOC extraction, and threat intel lookups (VirusTotal, AbuseIPDB, AlienVault OTX). Not started yet — V1 ships first.
