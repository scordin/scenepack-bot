"""Veel Scenepacks Discord search, powered by Serper.

Install: pip install discord.py requests python-dotenv beautifulsoup4
.env: DISCORD_TOKEN=... and SERPER_API_KEY=...
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

import discord
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"
SERPER_IMAGES_URL = "https://google.serper.dev/images"
HOST = "veelscp.com"
MAX_PACKS = 4
# Comma-separated Railway variable. The default keeps the bot locked to the
# channel you supplied even before the variable is added in Railway.
DEFAULT_ALLOWED_CHANNEL_IDS = "1537846917130620939"


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str = ""


def allowed_channel_ids() -> set[int]:
    raw = os.getenv("ALLOWED_CHANNEL_IDS", DEFAULT_ALLOWED_CHANNEL_IDS)
    return {int(value.strip()) for value in raw.split(",") if value.strip().isdigit()}


async def require_allowed_channel(interaction: discord.Interaction) -> bool:
    if interaction.channel_id in allowed_channel_ids():
        return True
    channel_list = ", ".join(f"<#{channel_id}>" for channel_id in allowed_channel_ids())
    await interaction.response.send_message(
        f"This bot only works in {channel_list}.", ephemeral=True
    )
    return False


def is_pack_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme in {"http", "https"}
            and parsed.netloc.lower().removeprefix("www.") == HOST
            and (parsed.path.startswith(("/tv/", "/movie/", "/game/", "/other/"))
                 or "scenepack=" in parsed.query))


def label(value: str) -> str:
    value = re.sub(r"\s*[|–—-]\s*(?:411|Scenepacks?|Editing Clips.*)$", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip(" -|–—") or "Scenepack"


def is_format_label(value: str) -> bool:
    return bool(re.fullmatch(r"(?:h\.?26[45]|\d{3,4}p|\d+)", value.strip(), re.I))


def display_title(query: str) -> str:
    """The user's search is usually cleaner than a search-engine page title."""
    return " ".join(word.capitalize() if word.islower() else word for word in query.split())


def metadata(*values: str) -> str | None:
    text = " ".join(values)
    year = re.search(r"\b((?:19|20)\d{2})\b", text)
    lower = text.lower()
    kind = "TV series" if any(x in lower for x in ("tv series", "television series", "season ")) else "Film" if any(x in lower for x in ("film", "movie")) else None
    if year and kind:
        return f"{year.group(1)} · {kind}"
    return year.group(1) if year else kind


def search_serper(query: str) -> list[Result]:
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY is missing from .env")
    response = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        # Serper's free accounts reject advanced patterns such as site: and quotes.
        # Search normally, then enforce the Scenepacks-only rule locally below.
        json={"q": f"{query} Veel Scenepacks", "num": 12}, timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Serper returned HTTP {response.status_code}: {response.text[:180]}")
    seen, found = set(), []
    for item in response.json().get("organic", []):
        url = item.get("link", "")
        if is_pack_url(url) and url not in seen:
            seen.add(url)
            found.append(Result(label(item.get("title", "")), url, item.get("snippet", "")))
    if found:
        return found
    return tmdb_to_veel_page(query)


def tmdb_to_veel_page(query: str) -> list[Result]:
    """Build a VeelsCP title link from the public TMDB title ID."""
    response = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": f"{query} TMDB", "num": 8}, timeout=20,
    )
    if not response.ok:
        return []
    for item in response.json().get("organic", []):
        parsed = urlparse(item.get("link", ""))
        match = re.match(r"^/(tv|movie)/(\d+)", parsed.path)
        if parsed.netloc.lower().endswith("themoviedb.org") and match:
            media_type, media_id = match.groups()
            url = f"https://veelscp.com/{media_type}/{media_id}?search={quote_plus(query)}"
            return [Result(label(item.get("title", query)), url, item.get("snippet", ""))]
    return []


def score(result: Result, query: str) -> int:
    terms = [x for x in re.findall(r"[a-z0-9]+", query.lower()) if len(x) > 1]
    haystack = f"{result.title} {result.snippet}".lower()
    return sum(10 for x in terms if x in result.title.lower()) + sum(2 for x in terms if x in haystack)


def page_packs(url: str) -> list[Result]:
    """Read links exposed on the public title page; harmlessly falls back on error."""
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException:
        return []
    found, seen, labels_seen = [], {url.rstrip("/")}, set()
    for anchor in BeautifulSoup(response.text, "html.parser").select("a[href]"):
        href = anchor["href"]
        if href.startswith("/"):
            href = f"https://{HOST}{href}"
        href = href.split("#", 1)[0].rstrip("/")
        text = label(anchor.get_text(" ", strip=True))
        # Download links are often labelled only "1080p" or "H.264". The nearest
        # preceding heading on the title page normally carries the character/season.
        if is_format_label(text):
            heading = anchor.find_previous(["h6", "h5", "h4", "h3", "h2"])
            if heading:
                candidate = label(heading.get_text(" ", strip=True))
                if not is_format_label(candidate):
                    text = candidate
        label_key = text.casefold()
        if is_pack_url(href) and href not in seen and text != "Scenepack" and label_key not in labels_seen:
            seen.add(href)
            labels_seen.add(label_key)
            found.append(Result(text[:120], href))
    return found


def poster_url(query: str) -> str | None:
    """Use the same Serper key to find a poster; absence of one never fails a search."""
    try:
        response = requests.post(
            SERPER_IMAGES_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": f"{query} official poster", "num": 1}, timeout=15,
        )
        if not response.ok:
            return None
        image = (response.json().get("images") or [{}])[0]
        return image.get("imageUrl") or image.get("thumbnailUrl")
    except (requests.RequestException, ValueError):
        return None


def unique(results: list[Result]) -> list[Result]:
    seen, output = set(), []
    for result in results:
        if result.url not in seen:
            seen.add(result.url)
            output.append(result)
    return output


def make_embed(query: str, results: list[Result]) -> discord.Embed:
    ranked = sorted(results, key=lambda r: score(r, query), reverse=True)
    main = ranked[0]
    packs = unique(page_packs(main.url) + [r for r in ranked if r.url != main.url])[:MAX_PACKS]
    bullets = [f"• [{label(pack.title)}]({pack.url})" for pack in packs]
    if not bullets:
        bullets = ["• No individual links were indexed yet — open the title to browse it."]
    info = metadata(main.title, main.snippet)
    embed = discord.Embed(title=display_title(query), url=main.url,
                          description=f"## Results for `{query}` · {len(packs)} packs across 1 title"
                                      + (f"\n\n*{info}*" if info else ""),
                          colour=discord.Colour.from_rgb(114, 137, 218))
    embed.set_author(name="Veel Scenepacks", url="https://veelscp.com/")
    embed.add_field(name="Available packs", value="\n".join(bullets), inline=False)
    embed.add_field(name="", value=f"[View all packs for this title]({main.url})", inline=False)
    image = poster_url(query)
    if image:
        embed.set_thumbnail(url=image)
    embed.set_footer(text="Page 1 of 1 · Titles 1–1 of 1")
    return embed


class Bot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.none())
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()


bot = Bot()


@bot.event
async def on_ready() -> None:
    print(f"✓ Bot is ready: {bot.user} (ID: {bot.user.id})")
    print("✓ /scenepack is synced and ready to use.")


@bot.tree.command(name="scenepack", description="Find 411 Scenepacks for a movie or show")
@discord.app_commands.describe(title="Movie, show, or character to search for")
async def scenepack(interaction: discord.Interaction, title: str) -> None:
    if not await require_allowed_channel(interaction):
        return
    title = title.strip()
    if not title:
        await interaction.response.send_message("Please enter a title to search for.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        results = await asyncio.to_thread(search_serper, title)
    except Exception as exc:
        await interaction.followup.send(f"Search failed: `{exc}`", ephemeral=True)
        return
    if not results:
        await interaction.followup.send(
            f"Couldn't find a Scenepacks page for **{title}**. Try another spelling or search directly at https://scenepacks.com/",
            ephemeral=True,
        )
        return
    await interaction.followup.send(embed=await asyncio.to_thread(make_embed, title, results))


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing from .env")
    bot.run(TOKEN)
