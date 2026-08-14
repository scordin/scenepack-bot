# 411 Scenepacks Discord Bot

## Deploy on Railway

1. Create a new GitHub repository and upload these files to its root:
   `bot.py`, `requirements.txt`, `railway.toml`, and `.gitignore`.
2. In Railway, select **New Project** → **Deploy from GitHub Repo**, then select that repository.
3. Open the deployed service → **Variables** and add:
   - `DISCORD_TOKEN` — your Discord bot token
   - `SERPER_API_KEY` — your Serper API key
4. Railway automatically installs dependencies and starts `python bot.py`.
5. Open the **Deploy Logs**. The successful startup message is:
   `Bot is ready: ...`

Never upload `.env` or paste either secret into GitHub.
