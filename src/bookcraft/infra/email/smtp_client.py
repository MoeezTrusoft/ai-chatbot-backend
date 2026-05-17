from __future__ import annotations

import mimetypes
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    path: Path
    filename: str | None = None
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class EmailSendResult:
    success: bool
    provider_message_id: str | None = None
    error_code: str | None = None


@dataclass(slots=True)
class SMTPEmailClient:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    from_name: str = "BookCraft Publishers"
    use_tls: bool = True
    enabled: bool = False

    def send(
        self,
        *,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
        attachments: list[EmailAttachment] | None = None,
    ) -> EmailSendResult:
        if not self.enabled:
            return EmailSendResult(
                success=False,
                error_code="smtp_disabled",
            )

        if not self.host or not self.from_email:
            return EmailSendResult(
                success=False,
                error_code="smtp_not_configured",
            )

        message = EmailMessage()
        message["From"] = f"{self.from_name} <{self.from_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text_body)

        if html_body:
            message.add_alternative(html_body, subtype="html")

        for attachment in attachments or []:
            path = attachment.path
            if not path.exists():
                return EmailSendResult(
                    success=False,
                    error_code="attachment_missing",
                )

            guessed_type = (
                attachment.content_type
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            )
            maintype, subtype = guessed_type.split("/", maxsplit=1)
            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename or path.name,
            )

        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as server:
                if self.use_tls:
                    server.starttls()
                if self.username:
                    server.login(self.username, self.password)
                response = server.send_message(message)
        except Exception as exc:
            return EmailSendResult(
                success=False,
                error_code=f"smtp_{exc.__class__.__name__}",
            )

        return EmailSendResult(
            success=not bool(response),
            provider_message_id=message.get("Message-ID"),
            error_code=None if not response else "smtp_partial_failure",
        )
