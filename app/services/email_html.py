from __future__ import annotations

from email.message import EmailMessage
from html import escape

from ..config import get_settings
from ..models import Lead, Message
from ..outreach import AFFILIATE_STAGES, landing_url, unsubscribe_url

EMAIL_LOGO_ROUTE = "/email-assets/leadflow-logo.png"


def email_logo_url() -> str:
    """One shared, non-unique logo URL; never append lead or recipient tokens."""
    return get_settings().report_base_url + EMAIL_LOGO_ROUTE


def add_branded_html_alternative(email: EmailMessage, html: str) -> None:
    """Add HTML without a related image part, preventing attachment tiles."""
    email.add_alternative(html, subtype="html")


def _blocks(text: str) -> str:
    rendered: list[str] = []
    for block in (text or "").replace("\r\n", "\n").split("\n\n"):
        clean = block.strip()
        if not clean:
            continue
        rendered.append(
            '<p style="margin:0 0 18px;color:#263746;font-size:15px;line-height:1.7;">'
            + escape(clean).replace("\n", "<br>")
            + "</p>"
        )
    return "".join(rendered)


def _cards(items: list[tuple[str, str]]) -> str:
    cells: list[str] = []
    for title, detail in items:
        cells.append(
            '<td width="50%" valign="top" style="width:50%;padding:5px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
            'style="width:100%;background:#f5f8fb;border:1px solid #e3ebf0;border-radius:10px;">'
            '<tr><td style="padding:14px 15px;">'
            f'<p style="margin:0 0 6px;color:#176b8a;font-size:11px;font-weight:700;letter-spacing:.08em;">{escape(title.upper())}</p>'
            f'<p style="margin:0;color:#425867;font-size:13px;line-height:1.5;">{escape(detail)}</p>'
            "</td></tr></table></td>"
        )
    rows: list[str] = []
    for index in range(0, len(cells), 2):
        pair = cells[index:index + 2]
        if len(pair) == 1:
            pair.append('<td width="50%" style="width:50%;padding:5px;"></td>')
        rows.append("<tr>" + "".join(pair) + "</tr>")
    return (
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="width:100%;margin:4px 0 22px;">'
        + "".join(rows)
        + "</table>"
    )


def _shell(*, title: str, body_html: str, footer_html: str, preheader: str, eyebrow: str) -> str:
    safe_title = escape(title)
    safe_preheader = escape(preheader)
    safe_eyebrow = escape(eyebrow.upper())
    safe_logo_url = escape(email_logo_url(), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_title}</title></head>
<body style="margin:0;padding:0;background:#dff2fc;color:#263746;font-family:Arial,'Helvetica Neue',sans-serif;">
<span style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden;">{safe_preheader}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#dff2fc;">
<tr><td align="center" style="padding:28px 12px 34px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:640px;background:#ffffff;border:1px solid #d5e6ef;border-radius:16px;box-shadow:0 12px 34px rgba(20,47,67,.10);">
<tr><td align="center" style="padding:30px 34px 10px;">
<img src="{safe_logo_url}" width="240" alt="LeadFlow" style="display:block;width:240px;max-width:100%;height:auto;border:0;outline:none;text-decoration:none;">
</td></tr>
<tr><td align="center" style="padding:4px 38px 8px;">
<p style="margin:0;color:#176b8a;font-size:11px;font-weight:700;letter-spacing:.14em;">{safe_eyebrow}</p>
</td></tr>
<tr><td style="padding:8px 38px 20px;">
<h1 style="margin:0 0 22px;color:#132b45;font-size:26px;line-height:1.3;font-weight:750;text-align:center;">{safe_title}</h1>
{body_html}
</td></tr>
<tr><td style="padding:22px 38px 30px;border-top:1px solid #e4ebef;background:#f8fafb;border-radius:0 0 16px 16px;">
{footer_html}
</td></tr>
</table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:640px;margin-top:18px;background:#315ff4;border-radius:12px;">
<tr>
<td width="33.33%" align="center" style="padding:17px 8px;color:#ffffff;font-size:11px;line-height:1.45;"><strong style="display:block;font-size:12px;">Evidence first</strong>Public observations only</td>
<td width="33.33%" align="center" style="padding:17px 8px;color:#ffffff;font-size:11px;line-height:1.45;border-left:1px solid rgba(255,255,255,.24);border-right:1px solid rgba(255,255,255,.24);"><strong style="display:block;font-size:12px;">Privacy-minded</strong>No tracking pixel</td>
<td width="33.33%" align="center" style="padding:17px 8px;color:#ffffff;font-size:11px;line-height:1.45;"><strong style="display:block;font-size:12px;">Your choice</strong>Reply or opt out</td>
</tr></table>
<p style="margin:14px 0 0;color:#597384;font-size:10px;line-height:1.5;text-align:center;">LeadFlow · Leads. Automated. Growth.</p>
</td></tr></table>
</body></html>"""


def render_outreach_html(lead: Lead, message: Message) -> str:
    """Render a lightweight, self-contained alternative to the canonical plain text."""
    settings = get_settings()
    report_url = landing_url(lead) if message.stage in AFFILIATE_STAGES else ""
    optout_url = unsubscribe_url(lead)
    display_text = message.body_final or message.body_core
    if report_url:
        display_text = display_text.replace(f": {report_url}", ".").replace(report_url, "")
    display_text = display_text.replace(f"Opt out: {optout_url}", "")

    signature_marker = f"\n\n{settings.sender_name}\n"
    if signature_marker in display_text:
        main_text, footer_tail = display_text.split(signature_marker, 1)
        footer_text = settings.sender_name + "\n" + footer_tail
    else:
        main_text = display_text
        footer_text = (
            f"{settings.sender_name}\n{settings.sender_role}\n{settings.sender_email}\n"
            f"Opt out: {optout_url}"
        )

    feature_html = ""
    cta_html = ""
    if report_url:
        feature_html = _cards([
            ("Performance", "Point-in-time response and asset checks"),
            ("Mobile", "Viewport and responsive-layout signals"),
            ("Search", "Metadata and search-presentation checks"),
            ("Security", "Public HTTPS and TLS observations"),
        ])
        safe_report_url = escape(report_url, quote=True)
        cta_html = f"""
<table role="presentation" align="center" cellspacing="0" cellpadding="0" border="0" style="margin:8px auto 16px;"><tr><td style="border-radius:9px;background:#315ff4;">
<a href="{safe_report_url}" style="display:inline-block;padding:14px 22px;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;line-height:1;">View your private website report</a>
</td></tr></table>
<p style="margin:0 0 20px;color:#6c7e89;font-size:12px;line-height:1.55;text-align:center;">Private signed link · no tracking pixel</p>"""

    footer_html = _blocks(footer_text)
    footer_html += (
        f'<p style="margin:12px 0 0;font-size:12px;line-height:1.6;color:#667985;">'
        f'<a href="{escape(optout_url, quote=True)}" style="color:#176b8a;text-decoration:underline;">Stop future email</a>'
        "</p>"
    )
    return _shell(
        title=f"A website review for {lead.business_name}",
        body_html=_blocks(main_text) + feature_html + cta_html,
        footer_html=footer_html,
        preheader=f"A point-in-time website review for {lead.business_name}",
        eyebrow="Private website review" if report_url else "One-to-one follow-up",
    )


def render_controlled_test_html(*, sender_name: str, reply_to_email: str) -> str:
    body_html = _blocks(
        "This is a one-time controlled LeadFlow delivery test.\n\n"
        "The card, logo, From identity and Reply-To route can be checked safely before any campaign email."
    )
    body_html += _cards([
        ("Authenticated From", "The connected Gmail account"),
        ("Business replies", reply_to_email),
        ("Privacy", "No tracking pixel or remote logo request"),
        ("Queue safety", "No lead, campaign, or follow-up created"),
    ])
    footer_html = _blocks(f"{sender_name}\nControlled inbox test")
    return _shell(
        title="LeadFlow sender test",
        body_html=body_html,
        footer_html=footer_html,
        preheader="One-time controlled Gmail sender test",
        eyebrow="Isolated delivery check",
    )
