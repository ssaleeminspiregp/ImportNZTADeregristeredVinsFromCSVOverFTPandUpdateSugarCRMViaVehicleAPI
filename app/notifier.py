import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from app.config import EmailSettings


class EmailNotifier:
    def __init__(self, settings: EmailSettings) -> None:
        self.settings = settings
        from_env = os.getenv("SERVICE_MODE")
        service_name = os.getenv("SERVICE_NAME") or os.getenv(
            "K_SERVICE"
        ) or os.getenv("GCP_SERVICE_NAME")
        self._subject_prefix = ""
        if service_name:
            self._subject_prefix = f"[{service_name}] "

    def send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = ", ".join(self.settings.recipients)
        message["Subject"] = f"{self._subject_prefix}{subject}"
        message.set_content(body)

        logging.debug(
            "EmailNotifier sending message '%s' to %s",
            subject,
            message["To"],
        )
        try:
            with smtplib.SMTP(
                host=self.settings.smtp_host,
                port=self.settings.smtp_port,
                timeout=self.settings.timeout,
            ) as client:
                if self.settings.use_tls:
                    client.starttls()
                if self.settings.smtp_username and self.settings.smtp_password:
                    client.login(
                        self.settings.smtp_username,
                        self.settings.smtp_password,
                    )
                client.send_message(message)
            logging.info(
                "EmailNotifier successfully sent message '%s' to %s",
                subject,
                message["To"],
            )
        except Exception:  # noqa: BLE001
            logging.exception("Failed to send notification email")
            raise


def build_notifier(settings: Optional[EmailSettings]) -> Optional[EmailNotifier]:
    if not settings:
        logging.warning("Email notifications are disabled; SMTP settings not provided")
        return None
    return EmailNotifier(settings)
