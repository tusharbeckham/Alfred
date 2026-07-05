# Alfred — Email control setup (Gmail: tusharentheoria@gmail.com)

Alfred will READ and DRAFT email freely; **SENDING always needs your explicit approval**.
Your credentials stay LOCAL — never committed (`secrets/` is git-ignored) and never pasted in chat.

## Option A — App Password (recommended, quickest)
1. Turn ON 2-Step Verification: https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
   - App = "Mail", Device = "Other" → name it "Alfred". Copy the 16-character code.
3. Create the file `C:\Alfred\secrets\mail.json` (this folder is git-ignored) containing:

   ```json
   {
     "email": "tusharentheoria@gmail.com",
     "app_password": "the 16-char code (spaces are fine)",
     "imap_host": "imap.gmail.com",
     "smtp_host": "smtp.gmail.com",
     "smtp_port": 587
   }
   ```
4. Tell me it's in place — I'll wire `scripts/alfred-mail.ps1`: read inbox, summarize, draft replies,
   and send **only** with `-Send` + your explicit yes.

## Option B — Gmail API (OAuth) — more setup, more granular + revocable
Prefer scoped OAuth over an app password? Say so and I'll walk you through a Google Cloud OAuth
client + a one-time token, then build against the Gmail REST API instead.

## Safety design (both options)
- Default mode: **read + draft only**. Sending is gated behind an explicit flag AND your confirmation.
- Credentials are read from `secrets/mail.json` (git-ignored); never printed, never committed.
- Revoke anytime: delete the "Alfred" App Password (or the OAuth client) in your Google account.
