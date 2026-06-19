# Privacy Policy

_Last updated: June 2026_

**TokenTracker does not collect, store, transmit, or sell any personal data.**

TokenTracker is a local-only tool: a browser extension plus a companion desktop
app that show how much of your daily AI usage remains, as a battery indicator.

## What the extension accesses

- It reads the **usage percentage and reset time** that Claude
  (claude.ai) and Gemini (gemini.google.com) already display to you when you
  are signed in, and it detects ChatGPT's "limit reached" banner and counts the
  messages you send (to estimate ChatGPT usage, since OpenAI publishes no
  official figure).
- It uses local browser **storage** to cache the most recent reading and a
  random pairing token, and **alarms** to refresh that reading periodically.

## What it does NOT do

- It does **not** read, store, or transmit your conversations, prompts,
  messages, account details, passwords, cookies, or browsing history.
- It does **not** send any data to the developer or to any third-party or
  remote server. There is **no telemetry and no analytics**.

## Where data goes

The only network connection the extension makes (other than reading the AI
sites' own pages you are already visiting) is to **`http://127.0.0.1:7734`** —
the TokenTracker desktop app running on **your own computer**. Usage readings
stay entirely on your device. Nothing leaves your machine.

## Data sharing and sale

We do not sell or transfer any user data to third parties. We do not use any
data to determine creditworthiness or for lending. There is no user data to
share, because none is collected.

## Permissions, in plain terms

- **storage** — cache the latest reading and the local pairing token.
- **alarms** — schedule the periodic usage refresh.
- **host access** (claude.ai, chatgpt.com, gemini.google.com) — read the usage
  figure the site already shows you.
- **127.0.0.1** — talk to your own TokenTracker desktop app over localhost.

## Affiliation

TokenTracker is an independent tool and is **not affiliated with, endorsed by,
or sponsored by** OpenAI, Anthropic, or Google. ChatGPT figures are estimates
and are labelled as such.

## Contact

Questions about this policy or the extension? Please open an issue on GitHub:
https://github.com/<your-username>/tokentracker/issues
