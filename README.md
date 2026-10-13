# ADA

**English** · [Türkçe](README.tr.md)

ADA is an autonomous AI worker that runs on your own computer. Describe a job — a report, an analysis, a presentation, an email, some research, or a chore on your PC — and ADA carries it out from start to finish.

## Download

**[Download ADA for Windows](https://github.com/Guardscloud/ADA/releases/latest/download/ADA-Setup.exe)** · [all releases](https://github.com/Guardscloud/ADA/releases)

- Windows 10 / 11 (64-bit)
- No administrator rights needed; ADA installs for your user only.
- On first launch ADA asks a few short questions (your name, an AI key, how independently it should work, what runs in the background).

> **Windows warning:** ADA is signed with our own certificate, which Windows does not recognise yet, so the first run may show "Windows protected your PC". Choose **More info → Run anyway** to continue.

## What it does

- **Tasks:** works through a job step by step and produces Word, Excel, PDF and presentation files.
- **Routines:** jobs ADA does by itself every day, written as plain Markdown files.
- **Many AI models:** uses local models on your machine (Ollama, LM Studio and others) and any cloud models you add a key for (Gemini, Claude, OpenAI and more), and picks the best fit for each kind of work. If one model hits a limit, the job continues with the next.
- **Watchdog:** keeps an eye on disk, memory, CPU and internet, and tells you when something is wrong.
- **You stay in control:** you choose when ADA must ask before acting, and there is a one-click emergency stop.

ADA runs only on your computer. Your API keys and data stay on your machine; only the content needed for a job is sent to the AI provider you choose.

## Languages

The interface is available in English, Türkçe, Deutsch, Français, Español, Português, Italiano, Русский, 中文 and 日本語. ADA follows your system language and you can change it any time from **Control → language**. ADA replies in whatever language you write to it.

Translations are machine-generated; corrections are welcome.

## Updates

ADA checks for new versions by itself. When one is available it notifies you, and **Control → Update** installs it in one click. Your settings and data are kept.

## Feedback

Found a bug or have an idea?

- In the app: **Control → Feedback and support**
- Or here: [open an issue](https://github.com/Guardscloud/ADA/issues/new/choose)

Every report is read and used to improve ADA.

## Open source

ADA is open source under the [MIT license](LICENSE). You are welcome to read the code, change it and contribute.

To run from source you need [uv](https://docs.astral.sh/uv/) and Node 18+:

```powershell
uv sync
cd frontend; npm install; npm run build; cd ..
uv run pytest -q        # tests
uv run ada-app          # desktop window  (browser only: uv run ada)
```

Architecture, settings and development notes are in [docs/GELISTIRICI.md](docs/GELISTIRICI.md) (Turkish). The code comments and the interface source strings are in Turkish; other languages come from the dictionaries in `frontend/public/assets/i18n`. To contribute, open an issue or send a pull request.

The name "ADA" and the Duck Team duck logo belong to the project owner; the MIT license covers the code, not the name or the logo.
