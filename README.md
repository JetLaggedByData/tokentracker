# TokenTracker

See how much of your daily AI usage you have left — **Claude, Gemini, and ChatGPT** — as a battery icon in your system tray.

TokenTracker is a local-only usage meter: a small Windows tray app plus a
cross-browser extension. The extension reads the usage percentage the AI sites
already show you and sends it — over localhost only — to the tray app, which
displays it as a battery. **No data ever leaves your machine.**

- Battery icon with the live percentage + three colour strides (green / amber / red)
- Works in Chrome, Edge, Brave, and Firefox
- A native desktop dashboard for all three providers at a glance
- ChatGPT usage is an **estimate** (OpenAI publishes no figure) and clearly labelled
- Not affiliated with OpenAI, Anthropic, or Google

## Install

**Desktop app:** download `TokenTracker.exe` from the
[latest release](../../releases) and run it — the battery appears in your tray
and it starts with Windows. The build is unsigned, so Windows may show an
"unknown publisher" warning; each release includes a `.sha256` checksum you can
compare with `Get-FileHash .\TokenTracker.exe -Algorithm SHA256` to verify your
download.

**Browser extension:** install from your browser's store (links on the release
page), or load it manually for testing — see
[the extension README](TokenTracker-extension/README.md).

## Build from source

Desktop app (Windows):
```
cd TokenTracker
uv sync
uv run python build_consumer.py     # -> dist/TokenTracker.exe + .sha256
```

Extension:
```
cd TokenTracker-extension
python build_extension.py           # -> dist/tokentracker-chromium.zip + firefox.zip
```

Run the tests:
```
cd TokenTracker
uv run python -m pytest tests/ -q
```

## How it works

The extension reads each provider's own usage page and POSTs the numbers to a
localhost server (`127.0.0.1:7734`) run by the tray app. Requests are gated by a
shared token and a loopback-only Host check, and the server binds to 127.0.0.1
so nothing on your network can reach it.

## Privacy

No telemetry, no analytics, no remote servers. The only network traffic is to
your own machine. Full details: [PRIVACY.md](PRIVACY.md).

## License

MIT — see [TokenTracker/LICENSE](TokenTracker/LICENSE).
