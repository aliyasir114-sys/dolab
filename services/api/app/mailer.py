"""Outgoing email.

No email provider is wired yet. Messages are kept in memory (tests read them from there) and,
outside production, the reset link is printed to the server log so it can be used locally.
In production nothing is printed, so a real provider must be connected before launch.
"""
import logging
from dataclasses import dataclass, field

from .config import settings

log = logging.getLogger("dolab.mail")


@dataclass
class Message:
    to: str
    subject: str
    body: str


@dataclass
class MemoryMailer:
    sent: list[Message] = field(default_factory=list)

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append(Message(to, subject, body))
        if settings.environment == "production":
            log.error("email provider not configured; message to a user was not delivered")
        elif settings.environment == "development":
            log.warning("DEV MAIL to %s: %s\n%s", to, subject, body)


mailer = MemoryMailer()


def send_password_reset(email: str, token: str, locale: str) -> None:
    if locale == "en":
        subject = "Reset your DOLAB password"
        body = (
            f"Use this code to choose a new password. It works once and expires in "
            f"{settings.reset_minutes} minutes:\n\n{token}\n\n"
            "If you did not ask for this, ignore this message; your password has not changed."
        )
    else:
        subject = "إعادة تعيين كلمة مرور دولاب"
        body = (
            f"استخدم هذا الرمز لاختيار كلمة مرور جديدة. يعمل مرة واحدة وتنتهي صلاحيته بعد "
            f"{settings.reset_minutes} دقيقة:\n\n{token}\n\n"
            "إن لم تطلب ذلك فتجاهل هذه الرسالة؛ كلمة مرورك لم تتغير."
        )
    mailer.send(email, subject, body)
