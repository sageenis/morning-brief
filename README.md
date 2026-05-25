# Morning Brief

Sagee's personal daily podcast feed. Auto-generated each morning.

## How it works

1. A scheduled task on my computer gathers data (weather, markets, calendar) each morning and pushes a JSON file to `briefs/YYYY-MM-DD.json` here.
2. A GitHub Action triggers on that push, calls OpenAI TTS to generate an MP3, and updates `feed.xml`.
3. My podcast app fetches `feed.xml` and downloads the new episode.

## Subscribe

Feed URL: `https://sageenis.github.io/morning-brief/feed.xml`
