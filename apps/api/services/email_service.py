"""Transactional email via Resend SMTP.

We use Resend's SMTP relay (smtp.resend.com:465) instead of their REST API.
Why: their API endpoint is fronted by Cloudflare, which fingerprints Python's
default TLS handshake as a bot and returns HTTP 403 (error code 1010) even
with a custom User-Agent. SMTP servers don't sit behind Cloudflare, so this
bypass works without any new dependencies (Python's stdlib smtplib).

Auth: username is the literal string "resend", password is the RESEND_API_KEY.

If RESEND_API_KEY env var is not set, calls become no-ops — the rest of the
system still works, only the email send is skipped (and the error captured
in email_send_error).
"""
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Rosetta IMS <onboarding@resend.dev>")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "chris@algogroup.io")
SMTP_HOST = "smtp.resend.com"
# DigitalOcean (and most cloud hosts) block outbound 465/587 by default. Resend also serves
# TLS on 2465 (and STARTTLS on 2587), which clouds leave open — so default to 2465.
SMTP_PORT = int(os.environ.get("SMTP_PORT", "2465"))


def _send(to_addr: str, subject: str, plain_body: str, html_body: str,
          reply_to: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Low-level Resend SMTP send. Returns (success, error_message)."""
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY not set — email skipped"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = to_addr
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=ctx) as server:
            server.login("resend", RESEND_API_KEY)
            server.send_message(msg)
        return True, None
    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTP auth failed (check RESEND_API_KEY): {e}"
    except smtplib.SMTPRecipientsRefused as e:
        return False, f"SMTP recipients refused (verify your sending domain in Resend): {e}"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {type(e).__name__}: {e}"
    except Exception as e:
        return False, f"Email send failed: {type(e).__name__}: {e}"


def send_invite_email(to_email: str, invite_url: str, role_label: str,
                      invited_by: str, expires_days: int = 7) -> tuple[bool, Optional[str]]:
    """Email an invited user their onboarding link. Returns (success, error_message)."""
    subject = "You've been invited to Rosetta IMS"
    plain_body = f"""\
{invited_by} has invited you to Rosetta IMS as a {role_label} user.

Set up your account (username, name, email and password) here — the link
expires in {expires_days} days:

{invite_url}

If you weren't expecting this, you can ignore this email.

— Rosetta IMS
"""
    html_body = f"""\
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #0F172A; line-height: 1.55;">
<p><strong>{invited_by}</strong> has invited you to <strong>Rosetta IMS</strong> as a
<span style="background:#EEF2FF; color:#4338CA; font-weight:600; padding:1px 8px; border-radius:99px;">{role_label}</span> user.</p>
<p>Click below to set up your account — choose your username, name, email and password.
This link expires in {expires_days} days.</p>
<p style="margin: 22px 0;">
  <a href="{invite_url}" style="background:#6366F1; color:white; text-decoration:none; font-weight:600; font-size:14px; padding:11px 22px; border-radius:8px; display:inline-block;">Set up my account</a>
</p>
<p style="font-size:12px; color:#64748B;">Or paste this link into your browser:<br><a href="{invite_url}">{invite_url}</a></p>
<p style="font-size:11px; color:#94A3B8; margin-top:22px;">If you weren't expecting this, you can ignore this email.<br>— Rosetta IMS</p>
</body></html>
"""
    return _send(to_email, subject, plain_body, html_body, reply_to=ADMIN_EMAIL)

# ── NDA boilerplate ───────────────────────────────────────────────────────────
NDA_TEXT_PLAIN = """\
NDA — what the requestor confirmed by clicking "I acknowledge":

1. CONFIDENTIALITY. The Rosetta IMS source code, schema, business logic,
   and any derivative work or output are confidential and proprietary to
   Algo Technologies Pte Ltd and its affiliates. The requestor will not
   share, publish, or disclose them to any third party.

2. SCOPE OF USE. The requestor will use this code solely for work
   explicitly agreed with Algo Technologies Pte Ltd and its affiliates.
   The requestor will not use, reuse, port, or adapt this code for any
   other company, client, project, or personal purpose without prior
   written consent from Algo Technologies Pte Ltd and its affiliates.

3. TERMINATION. Upon termination of the requestor's engagement with
   Algo Technologies Pte Ltd and its affiliates, they will delete all
   local copies of the code and revoke any access tokens granted.

4. BREACH. The requestor understands that any breach of the above may
   give rise to monetary damages, injunctive relief, and other remedies
   available to Algo Technologies Pte Ltd and its affiliates under
   applicable law.
"""


def send_access_request_email(
    full_name: str,
    github_username: str,
    requestor_email: str,
    ims_user_display: str,
    ip_address: Optional[str],
    accepted_at: str,
    terms_version: str,
) -> tuple[bool, Optional[str]]:
    """Send the access-request notification via Resend SMTP.
    Returns (success, error_message)."""
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY not set — email skipped (record stored in DB only)"

    subject = f"Rosetta IMS — Access request from {full_name} (@{github_username})"

    plain_body = f"""\
{full_name} ({requestor_email}) has requested access to the proprietary
Rosetta IMS repository owned by Algo Technologies Pte Ltd and its affiliates.

REQUEST DETAILS
- Full name (typed signature):  {full_name}
- GitHub username:              @{github_username}
- Email:                        {requestor_email}
- IMS user:                     {ims_user_display}
- Requested at (UTC):           {accepted_at}
- IP address:                   {ip_address or 'n/a'}
- Terms version:                {terms_version}

{NDA_TEXT_PLAIN}

TO GRANT ACCESS
1. Go to https://github.com/cswf86/rosetta-ims/settings/access
2. Click "Add people" → paste their GitHub username (@{github_username})
3. Select Role: Read (or as appropriate)
4. GitHub will email an invitation to @{github_username}; they accept from
   their inbox.

Reply-To is set to {requestor_email} — hitting reply on this email goes
directly back to the requestor.

— Rosetta IMS
"""

    html_body = f"""\
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #0F172A; line-height: 1.55;">
<p><strong>{full_name}</strong> (<a href="mailto:{requestor_email}">{requestor_email}</a>) has requested access to the proprietary Rosetta IMS repository owned by Algo Technologies Pte Ltd and its affiliates.</p>

<h3 style="margin-top: 18px; font-size: 13px; color: #64748B; text-transform: uppercase; letter-spacing: 0.06em;">Request details</h3>
<table style="border-collapse: collapse; font-size: 13px;">
  <tr><td style="padding: 3px 12px 3px 0; color: #64748B;">Full name (typed signature):</td><td><strong>{full_name}</strong></td></tr>
  <tr><td style="padding: 3px 12px 3px 0; color: #64748B;">GitHub username:</td><td><code>@{github_username}</code></td></tr>
  <tr><td style="padding: 3px 12px 3px 0; color: #64748B;">Email:</td><td><a href="mailto:{requestor_email}">{requestor_email}</a></td></tr>
  <tr><td style="padding: 3px 12px 3px 0; color: #64748B;">IMS user:</td><td>{ims_user_display}</td></tr>
  <tr><td style="padding: 3px 12px 3px 0; color: #64748B;">Requested at (UTC):</td><td>{accepted_at}</td></tr>
  <tr><td style="padding: 3px 12px 3px 0; color: #64748B;">IP address:</td><td>{ip_address or 'n/a'}</td></tr>
  <tr><td style="padding: 3px 12px 3px 0; color: #64748B;">Terms version:</td><td>{terms_version}</td></tr>
</table>

<h3 style="margin-top: 22px; font-size: 13px; color: #64748B; text-transform: uppercase; letter-spacing: 0.06em;">NDA — what the requestor confirmed</h3>
<div style="background: #F8FAFC; border-left: 3px solid #6366F1; padding: 12px 16px; font-size: 12.5px;">
<p><strong>1. CONFIDENTIALITY.</strong> The Rosetta IMS source code, schema, business logic, and any derivative work or output are confidential and proprietary to Algo Technologies Pte Ltd and its affiliates. The requestor will not share, publish, or disclose them to any third party.</p>
<p><strong>2. SCOPE OF USE.</strong> The requestor will use this code solely for work explicitly agreed with Algo Technologies Pte Ltd and its affiliates. The requestor will not use, reuse, port, or adapt this code for any other company, client, project, or personal purpose without prior written consent from Algo Technologies Pte Ltd and its affiliates.</p>
<p><strong>3. TERMINATION.</strong> Upon termination of the requestor's engagement with Algo Technologies Pte Ltd and its affiliates, they will delete all local copies of the code and revoke any access tokens granted.</p>
<p><strong>4. BREACH.</strong> The requestor understands that any breach of the above may give rise to monetary damages, injunctive relief, and other remedies available to Algo Technologies Pte Ltd and its affiliates under applicable law.</p>
</div>

<h3 style="margin-top: 22px; font-size: 13px; color: #64748B; text-transform: uppercase; letter-spacing: 0.06em;">To grant access</h3>
<ol style="font-size: 13px;">
  <li>Go to <a href="https://github.com/cswf86/rosetta-ims/settings/access">github.com/cswf86/rosetta-ims/settings/access</a></li>
  <li>Click "Add people" → paste their GitHub username (<code>@{github_username}</code>)</li>
  <li>Select Role: <strong>Read</strong> (or as appropriate)</li>
  <li>GitHub will email an invitation to <code>@{github_username}</code>; they accept from their inbox.</li>
</ol>

<p style="font-size: 11px; color: #94A3B8; margin-top: 22px;">
Reply-To is set to <a href="mailto:{requestor_email}">{requestor_email}</a> — hitting reply goes back to the requestor directly.
</p>
<p style="font-size: 11px; color: #94A3B8;">— Rosetta IMS</p>
</body></html>
"""

    # Build the multipart message (plain + HTML).
    msg = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = EMAIL_FROM
    msg["To"]       = ADMIN_EMAIL
    msg["Reply-To"] = requestor_email
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body,  "html",  "utf-8"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=ctx) as server:
            server.login("resend", RESEND_API_KEY)
            server.send_message(msg)
        return True, None
    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTP auth failed (check RESEND_API_KEY): {e}"
    except smtplib.SMTPRecipientsRefused as e:
        return False, f"SMTP recipients refused (likely Resend domain restriction): {e}"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {type(e).__name__}: {e}"
    except Exception as e:
        return False, f"Email send failed: {type(e).__name__}: {e}"
