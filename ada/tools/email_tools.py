"""IMAP ile e-posta okuma ve taslak oluşturma.

Güvenlik tercihi: ADA e-posta GÖNDERMEZ, yalnızca Taslaklar klasörüne kaydeder.
Kullanıcı taslağı kendi e-posta istemcisinde kontrol edip gönderir.
"""

from __future__ import annotations

import email
import imaplib
import re
import time
from email.header import decode_header, make_header
from email.message import EmailMessage

from .. import config
from . import ToolError, tool


def _connect() -> imaplib.IMAP4_SSL:
    if not (config.IMAP_USER and config.IMAP_PASSWORD):
        raise ToolError(
            "E-posta yapılandırılmamış. .env dosyasında ADA_IMAP_USER ve ADA_IMAP_PASSWORD "
            "ayarlanmalı (Gmail için Uygulama Şifresi)."
        )
    conn = imaplib.IMAP4_SSL(config.IMAP_HOST)
    conn.login(config.IMAP_USER, config.IMAP_PASSWORD)
    return conn


def _h(value) -> str:
    return str(make_header(decode_header(value or "")))


def _body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace")


@tool(
    "email_list_inbox",
    "Gelen kutusundaki son e-postaları listeler (gönderen, konu, tarih, UID).",
    {
        "limit": {"type": "integer", "description": "Kaç e-posta (varsayılan 20, en fazla 100)"},
        "unread_only": {"type": "boolean"},
        "search": {"type": "string", "description": "İsteğe bağlı: konu veya gönderende aranacak metin"},
    },
)
def email_list_inbox(limit: int = 20, unread_only: bool = False, search: str = "") -> str:
    limit = min(max(limit, 1), 100)
    needle = search.casefold()
    conn = _connect()
    try:
        conn.select("INBOX", readonly=True)
        _, data = conn.uid("search", None, "UNSEEN" if unread_only else "ALL")
        # Aramada Türkçe karakter sorunlarını önlemek için filtreyi başlıklar üzerinde yerelde yaparız
        uids = data[0].split()[-(limit * 10 if needle else limit):]
        rows = []
        for uid in reversed(uids):
            _, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            msg = email.message_from_bytes(msg_data[0][1])
            row = f"UID {uid.decode()} | {_h(msg['Date'])} | {_h(msg['From'])} | {_h(msg['Subject'])}"
            if needle and needle not in row.casefold():
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return "\n".join(rows) or "E-posta bulunamadı."
    finally:
        conn.logout()


@tool(
    "email_read",
    "UID'si verilen e-postanın tam içeriğini okur.",
    {"uid": {"type": "string"}},
    ["uid"],
)
def email_read(uid: str) -> str:
    conn = _connect()
    try:
        conn.select("INBOX", readonly=True)
        _, msg_data = conn.uid("fetch", uid.encode(), "(BODY.PEEK[])")
        if not msg_data or msg_data[0] is None:
            raise ToolError(f"UID bulunamadı: {uid}")
        msg = email.message_from_bytes(msg_data[0][1])
        attachments = [_h(p.get_filename()) for p in msg.walk() if p.get_filename()]
        body = _body(msg)[:30_000]
        return (
            f"Kimden: {_h(msg['From'])}\nKime: {_h(msg['To'])}\nCC: {_h(msg['Cc'])}\n"
            f"Tarih: {_h(msg['Date'])}\nKonu: {_h(msg['Subject'])}\n"
            f"Ekler: {', '.join(attachments) or '-'}\n\n{body}"
        )
    finally:
        conn.logout()


@tool(
    "email_create_draft",
    "E-posta TASLAĞI oluşturur ve kullanıcının Taslaklar klasörüne kaydeder. E-postayı "
    "göndermez; kullanıcı kontrol edip kendisi gönderir.",
    {
        "to": {"type": "string", "description": "Alıcılar, virgülle ayrılmış"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "cc": {"type": "string"},
    },
    ["to", "subject", "body"],
)
def email_create_draft(to: str, subject: str, body: str, cc: str = "") -> str:
    msg = EmailMessage()
    msg["From"] = config.IMAP_USER
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg.set_content(body)
    conn = _connect()
    try:
        status, _ = conn.append(
            config.IMAP_DRAFTS_FOLDER, "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes()
        )
        if status != "OK":
            raise ToolError(f"Taslak kaydedilemedi ({config.IMAP_DRAFTS_FOLDER}).")
    finally:
        conn.logout()
    return f"Taslak '{config.IMAP_DRAFTS_FOLDER}' klasörüne kaydedildi: {subject} -> {to}"
