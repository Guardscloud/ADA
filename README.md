# ADA

**English** · [Türkçe](README.tr.md)

ADA is an autonomous AI worker that runs on your own computer. Describe a job — a report, an analysis, a presentation, an email, some research, or a chore on your PC — and ADA carries it out from start to finish.

<p align="center"><img src="docs/images/tour.gif" alt="A quick tour of ADA: setup, tasks, control menu, safety mode and languages" width="900"></p>

## Download

**[Download ADA for Windows](https://github.com/Guardscloud/ADA/releases/latest/download/ADA-Setup.exe)** · [all releases](https://github.com/Guardscloud/ADA/releases)

- Windows 10 / 11 (64-bit)
- No administrator rights needed; ADA installs for your user only.
- On first launch ADA asks a few short questions (your name, an AI key, how independently it should work, what runs in the background).


## Getting started

**1. Install.** Download and run `ADA-Setup.exe`. If Windows shows **"Windows protected your PC"**, click **More info**, then **Run anyway**. The warning appears because ADA is signed with our own certificate rather than a paid one that Windows already knows. ADA is open source, so you can read exactly what it does, and each release page lists the file's SHA-256 so you can check your download.

**2. Add an AI key (free).** ADA needs an AI model to work. The quickest free option is Google Gemini:

1. Open [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in with a Google account.
2. Click **Create API key**, then **Copy**.
3. Paste it into the key field on ADA's setup screen and click **Save**. ADA checks the key and lists the available models.

<p align="center"><img src="docs/images/key-step.png" alt="Pasting the AI key during setup" width="520"></p>

No key? ADA also works with local models: install [Ollama](https://ollama.com), pull a model, and ADA finds it by itself. You can add more providers (Claude, OpenAI, Groq and others) later in **Settings → AI**.

**3. Give it a job.** Type what you need in plain language, for example:

- *"Research the electric vehicle charging market and write a Word report with sources."*
- *"Sort the files on my desktop into folders by type."*
- *"Every weekday at 09:00, summarise the news about my industry."*

## What it does

- **Tasks:** works through a job step by step and produces Word, Excel, PDF and presentation files.
- **Routines:** jobs ADA does by itself every day, written as plain Markdown files.
- **Many AI models:** uses local models on your machine (Ollama, LM Studio and others) and any cloud models you add a key for (Gemini, Claude, OpenAI and more), and picks the best fit for each kind of work. If one model hits a limit, the job continues with the next.
- **Watchdog:** keeps an eye on disk, memory, CPU and internet, and tells you when something is wrong.
- **You stay in control:** you choose when ADA must ask before acting, and there is a one-click emergency stop.
- **Safety mode:** commands that could shut down the computer or wipe a disk never run in safe mode; if you switch to *anything with approval*, ADA asks you every single time.

| First-run setup | You decide how independent ADA is |
|---|---|
| <img src="docs/images/setup.png" alt="First-run setup" width="440"> | <img src="docs/images/autonomy.png" alt="Autonomy levels" width="440"> |

| Control menu | Feedback from inside the app |
|---|---|
| <img src="docs/images/control.png" alt="Control menu with emergency stop" width="380"> | <img src="docs/images/feedback.png" alt="Feedback panel" width="440"> |

ADA runs only on your computer. Your API keys and data stay on your machine; only the content needed for a job is sent to the AI provider you choose.

## Languages

The interface is available in English, Türkçe, Deutsch, Français, Español, Português, Italiano, Русский, 中文 and 日本語. ADA follows your system language and you can change it any time from **Control → language**. ADA replies in whatever language you write to it.

You can also write your tasks and routine files in any language: ADA understands the request in the language it is written in and produces its reports and documents in that language.

<p align="center">
  <img src="docs/images/lang-ja.png" alt="ADA in Japanese" width="290">
  <img src="docs/images/lang-de.png" alt="ADA in German" width="290">
  <img src="docs/images/lang-es.png" alt="ADA in Spanish" width="290">
</p>

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
