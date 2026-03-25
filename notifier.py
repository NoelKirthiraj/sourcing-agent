"""
Run Summary Notifications — Slack webhook and/or SMTP email.
Configure in .env: NOTIFY_SLACK_WEBHOOK and/or NOTIFY_EMAIL_TO + SMTP_* vars.
"""
import logging
import os
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
import httpx

log = logging.getLogger(__name__)

@dataclass
class RunSummary:
    run_at: str = ""
    total_found: int = 0
    new_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    new_tenders: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    duration_seconds: float = 0.0
    mode: str = "daily"

    def __post_init__(self):
        if not self.run_at:
            self.run_at = datetime.now().strftime("%Y-%m-%d %H:%M %Z")

class Notifier:
    def __init__(self):
        self.slack_webhook = os.getenv("NOTIFY_SLACK_WEBHOOK", "").strip()
        self.email_to = os.getenv("NOTIFY_EMAIL_TO", "").strip()
        self.email_from = os.getenv("NOTIFY_EMAIL_FROM", "").strip()
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "").strip()
        self.smtp_pass = os.getenv("SMTP_PASS", "").strip()

    async def send(self, summary: RunSummary):
        if self.slack_webhook:
            await self._send_slack(summary)
        if self.email_to and self.smtp_user:
            self._send_email(summary)
        if not self.slack_webhook and not self.email_to:
            log.debug("No notification channels configured — skipping")

    async def _send_slack(self, s: RunSummary):
        emoji = "✅" if s.error_count == 0 else "⚠️"
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} CanadaBuys → CFlow | {s.run_at}"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Found:*\n{s.total_found}"},
                {"type": "mrkdwn", "text": f"*New → CFlow:*\n{s.new_count}"},
                {"type": "mrkdwn", "text": f"*Skipped:*\n{s.skipped_count}"},
                {"type": "mrkdwn", "text": f"*Errors:*\n{s.error_count}"},
            ]},
        ]
        if s.new_tenders:
            lines = "\n".join(
                f"• <{t.get('inquiry_link','')}|{t.get('solicitation_title','(no title)')}> — {t.get('solicitation_no','')} — closes {t.get('closing_date','?')}"
                for t in s.new_tenders[:10]
            )
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*New tenders:*\n{lines}"}})
        if s.errors:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Errors:*\n" + "\n".join(f"• {e}" for e in s.errors[:5])}})
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(self.slack_webhook, json={"blocks": blocks})
                if r.status_code == 200:
                    log.info("Slack notification sent")
                else:
                    log.warning("Slack failed: %d %s", r.status_code, r.text)
        except Exception as exc:
            log.warning("Slack error: %s", exc)

    def _send_email(self, s: RunSummary):
        subject = (f"[CanadaBuys Agent] {s.new_count} new tender(s) → CFlow — {s.run_at}"
                   if s.new_count else f"[CanadaBuys Agent] No new tenders — {s.run_at}")
        rows = "".join(
            f"<tr><td><a href='{t.get('inquiry_link','#')}'>{t.get('solicitation_title','')}</a></td>"
            f"<td>{t.get('solicitation_no','')}</td><td>{t.get('client','')}</td><td>{t.get('closing_date','')}</td></tr>"
            for t in s.new_tenders
        )
        html = f"<html><body><h2>CanadaBuys Agent Run — {s.run_at}</h2><p>Found: {s.total_found} | New: {s.new_count} | Skipped: {s.skipped_count} | Errors: {s.error_count}</p>{'<table border=1 cellpadding=6><tr><th>Title</th><th>Sol No</th><th>Client</th><th>Closing</th></tr>' + rows + '</table>' if rows else '<p>No new tenders.</p>'}</body></html>"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.email_from
        msg["To"] = self.email_to
        msg.attach(MIMEText(html, "html"))
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.email_from, [self.email_to], msg.as_string())
            log.info("Email sent to %s", self.email_to)
        except Exception as exc:
            log.warning("Email error: %s", exc)
