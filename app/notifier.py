import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

from app.config import EmailSettings


class EmailNotifier:
    def __init__(self, settings: EmailSettings) -> None:
        self.settings = settings
        self.default_recipients = settings.recipients or ["ssaleem@ib4t.co"]
        self.success_recipients = (
            settings.success_recipients or self.default_recipients
        )
        self.failure_recipients = (
            settings.failure_recipients or self.default_recipients
        )
        from_env = os.getenv("SERVICE_MODE")
        service_name = os.getenv("SERVICE_NAME") or os.getenv(
            "K_SERVICE"
        ) or os.getenv("GCP_SERVICE_NAME")
        self._subject_prefix = ""
        if service_name:
            self._subject_prefix = f"[{service_name}] "

    def send(
        self, subject: str, body: str, recipients: Optional[list[str]] = None
    ) -> None:
        targets = recipients or self.default_recipients
        message = EmailMessage()
        message["From"] = self.settings.sender
        message["To"] = ", ".join(targets)
        message["Subject"] = f"{self._subject_prefix}{subject}"
        message.set_content(body)

        logging.debug(
            "EmailNotifier sending message '%s' to %s",
            subject,
            message["To"],
        )
        logging.debug(
            "SMTP connect settings host=%s port=%s tls=%s debug=%s timeout=%s",
            self.settings.smtp_host,
            self.settings.smtp_port,
            self.settings.use_tls,
            self.settings.debug,
            self.settings.timeout,
        )
        try:
            with smtplib.SMTP(
                host=self.settings.smtp_host,
                port=self.settings.smtp_port,
                timeout=self.settings.timeout,
            ) as client:
                if self.settings.debug:
                    client.set_debuglevel(1)

                    def _smtp_debug_output(*args: str) -> None:
                        logging.debug("SMTP raw: %s", "".join(args).strip())

                    client._print_debug = _smtp_debug_output  # type: ignore[attr-defined]

                def _log_response(label: str, response: tuple[int, bytes | str] | None) -> None:
                    if not response:
                        logging.debug("%s: <no response>", label)
                        return
                    code, msg = response
                    if isinstance(msg, bytes):
                        try:
                            msg = msg.decode("utf-8", errors="ignore")
                        except Exception:  # pragma: no cover - defensive
                            msg = str(msg)
                    logging.debug("%s: %s %s", label, code, msg)

                resp = client.ehlo()
                logging.debug("SMTP EHLO raw response: %s", resp)
                _log_response("SMTP EHLO", resp)
                if self.settings.use_tls:
                    logging.debug("SMTP issuing STARTTLS")
                    resp = client.starttls()
                    logging.debug("SMTP STARTTLS raw response: %s", resp)
                    _log_response("SMTP STARTTLS", resp)
                    resp = client.ehlo()
                    logging.debug("SMTP post-STARTTLS EHLO raw response: %s", resp)
                    _log_response("SMTP post-STARTTLS EHLO", resp)
                if self.settings.smtp_username and self.settings.smtp_password:
                    logging.debug(
                        "SMTP logging in as %s", self.settings.smtp_username
                    )
                    resp = client.login(
                        self.settings.smtp_username,
                        self.settings.smtp_password,
                    )
                    logging.debug("SMTP login result: %s", resp)
                refused = client.send_message(message)
                if refused:
                    logging.warning("SMTP refused recipients: %s", refused)
                else:
                    logging.debug("SMTP send_message accepted all recipients")
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
