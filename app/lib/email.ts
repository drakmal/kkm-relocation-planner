// Transactional email via Resend. No-op (returns false) when RESEND_API_KEY is
// unset, so the app works locally without email configured.

const RESEND_API_KEY = process.env.RESEND_API_KEY;
const RESEND_FROM = process.env.RESEND_FROM || 'Relocation Planner <onboarding@resend.dev>';
const APP_BASE_URL = (process.env.APP_BASE_URL || '').replace(/\/$/, '');

function escapeHtml(s: string): string {
  return (s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}

/** Confirmation email sent right after a tracking request is submitted,
 *  carrying the reference ID (so the user has it even if they forget). */
export async function sendConfirmationEmail(opts: {
  to: string;
  refId: string;
  officeName: string;
  reportReadyText?: string;
}): Promise<boolean> {
  if (!RESEND_API_KEY) return false;

  const url = APP_BASE_URL ? `${APP_BASE_URL}/report/${opts.refId}` : null;
  const readyLine = opts.reportReadyText
    ? `<p>Your full relocation report will be emailed by <strong>${escapeHtml(opts.reportReadyText)}</strong>.</p>`
    : '';
  const linkLine = url
    ? `<p>Track its status or view the report here:<br><a href="${url}">${url}</a></p>`
    : '';

  const html =
    '<div style="font-family:Segoe UI,Arial,sans-serif;color:#16324f;max-width:600px">' +
    '<h2 style="color:#0f4c92">Tracking request received</h2>' +
    `<p>Target office: <strong>${escapeHtml(opts.officeName)}</strong></p>` +
    '<p>Your reference ID:</p>' +
    `<p style="font-size:20px;font-weight:700;background:#f0f7ff;border:1px solid #d8ebff;border-radius:10px;padding:10px 16px;display:inline-block;letter-spacing:0.5px">${escapeHtml(opts.refId)}</p>` +
    readyLine + linkLine +
    '<p style="color:#60748a;font-size:13px">Please keep this reference ID. Sent by the Relocation Planner.</p>' +
    '</div>';

  const text =
    `Tracking request received.\nTarget office: ${opts.officeName}\nReference ID: ${opts.refId}\n` +
    (opts.reportReadyText ? `Report expected by: ${opts.reportReadyText}\n` : '') +
    (url ? `Link: ${url}\n` : '');

  try {
    const resp = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${RESEND_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: RESEND_FROM,
        to: [opts.to],
        subject: `Your tracking request — Ref ${opts.refId}`,
        html,
        text,
      }),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

/** Magic-link email sent right after submission, before any tracking_requests
 *  row exists — proves the submitter owns the @moh.gov.my inbox. The link
 *  only becomes a real tracking request once clicked (see
 *  app/api/verify/[token]/route.ts). No-op (returns false) when
 *  RESEND_API_KEY is unset, same as sendConfirmationEmail. */
export async function sendVerificationEmail(opts: {
  to: string;
  token: string;
  officeName: string;
  expiresMinutes: number;
}): Promise<boolean> {
  if (!RESEND_API_KEY) return false;

  const url = APP_BASE_URL ? `${APP_BASE_URL}/verify/${opts.token}` : null;
  if (!url) return false;

  const html =
    '<div style="font-family:Segoe UI,Arial,sans-serif;color:#16324f;max-width:600px">' +
    '<h2 style="color:#0f4c92">Confirm your tracking request</h2>' +
    `<p>Target office: <strong>${escapeHtml(opts.officeName)}</strong></p>` +
    '<p>Click below to confirm this is your request and start tracking your commute:</p>' +
    `<p><a href="${url}" style="display:inline-block;background:#0f4c92;color:#fff;text-decoration:none;padding:10px 20px;border-radius:8px;font-weight:600">Confirm and start tracking</a></p>` +
    `<p style="color:#60748a;font-size:13px">This link expires in ${opts.expiresMinutes} minutes. If you didn't request this, you can ignore this email.</p>` +
    '<p style="color:#60748a;font-size:13px">Sent by the Relocation Planner.</p>' +
    '</div>';

  const text =
    `Confirm your tracking request.\nTarget office: ${opts.officeName}\n` +
    `Confirm here (expires in ${opts.expiresMinutes} minutes): ${url}\n` +
    `If you didn't request this, you can ignore this email.\n`;

  try {
    const resp = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${RESEND_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from: RESEND_FROM,
        to: [opts.to],
        subject: 'Confirm your tracking request',
        html,
        text,
      }),
    });
    return resp.ok;
  } catch {
    return false;
  }
}
