"""411-style Discord scenepack search, powered by Serper.

Install: pip install discord.py requests python-dotenv beautifulsoup4
.env: DISCORD_TOKEN=... and SERPER_API_KEY=...
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import discord
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"
SERPER_IMAGES_URL = "https://google.serper.dev/images"
HOST = "scenepacks.com"
MAX_PACKS = 4


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class Resource:
    name: str
    url: str
    app: str
    note: str


# Curated official pages. These are links to licensed-resource libraries, not
# reuploads of copyrighted clips or paid packs.
RESOURCE_LIBRARY: dict[str, list[Resource]] = {
    "presets": [
        Resource("Mixkit After Effects templates", "https://mixkit.co/free-after-effects-templates/", "After Effects", "Free templates, intros, titles, and transitions"),
        Resource("Mixkit Premiere Pro templates", "https://mixkit.co/free-premiere-pro-templates/", "Premiere Pro", "Free motion templates and titles"),
        Resource("DaVinci Resolve templates", "https://mixkit.co/free-davinci-resolve-templates/", "DaVinci Resolve", "Free editable video templates"),
    ],
    "transitions": [
        Resource("After Effects transitions", "https://mixkit.co/free-after-effects-templates/transitions/", "After Effects", "Free transition templates"),
        Resource("Premiere Pro transitions", "https://mixkit.co/free-premiere-pro-templates/transitions/", "Premiere Pro", "Free transition templates"),
        Resource("DaVinci Resolve templates", "https://mixkit.co/free-davinci-resolve-templates/", "DaVinci Resolve", "Browse editable transition projects"),
    ],
    "fonts": [
        Resource("Google Fonts", "https://fonts.google.com/", "All apps", "Open-source fonts for titles and captions"),
        Resource("Font Squirrel", "https://www.fontsquirrel.com/", "All apps", "Commercial-use font finder; check each font's licence"),
        Resource("Adobe Fonts", "https://fonts.adobe.com/", "Adobe apps", "Included with eligible Adobe subscriptions"),
    ],
    "sfx": [
        Resource("Mixkit sound effects", "https://mixkit.co/free-sound-effects/", "All apps", "Free whooshes, impacts, glitches, and ambience"),
        Resource("Freesound", "https://freesound.org/", "All apps", "Community sounds; check the individual licence"),
        Resource("YouTube Audio Library", "https://www.youtube.com/audiolibrary", "All apps", "Sound effects and music for video projects"),
    ],
    "music": [
        Resource("Pixabay Music", "https://pixabay.com/music/", "All apps", "Royalty-free music; review the Pixabay licence"),
        Resource("YouTube Audio Library", "https://www.youtube.com/audiolibrary", "All apps", "Music and sound effects with usage details"),
        Resource("Mixkit Music", "https://mixkit.co/free-stock-music/", "All apps", "Free music tracks for video projects"),
    ],
    "footage": [
        Resource("Pexels Videos", "https://www.pexels.com/videos/", "All apps", "Free stock video footage"),
        Resource("Pixabay Videos", "https://pixabay.com/videos/", "All apps", "Free stock clips and backgrounds"),
        Resource("Mixkit Stock Video", "https://mixkit.co/free-stock-video/", "All apps", "Free stock video clips"),
    ],
}


def is_pack_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme in {"http", "https"}
            and parsed.netloc.lower().removeprefix("www.") == HOST
            and parsed.path.startswith("/scps/"))


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
        json={"q": f"{query} Scenepacks", "num": 12}, timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Serper returned HTTP {response.status_code}: {response.text[:180]}")
    seen, found = set(), []
    for item in response.json().get("organic", []):
        url = item.get("link", "")
        if is_pack_url(url) and url not in seen:
            seen.add(url)
            found.append(Result(label(item.get("title", "")), url, item.get("snippet", "")))
    return found


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
    embed.set_author(name="411 Scenepacks")
    embed.add_field(name="Available packs", value="\n".join(bullets), inline=False)
    embed.add_field(name="", value=f"[View all packs for this title]({main.url})", inline=False)
    image = poster_url(query)
    if image:
        embed.set_thumbnail(url=image)
    embed.set_footer(text="Page 1 of 1 · Titles 1–1 of 1")
    return embed


def make_resources_embed(kind: str, app: str | None) -> discord.Embed:
    resources = RESOURCE_LIBRARY[kind]
    if app and app != "all":
        resources = [item for item in resources if item.app.casefold() == app.casefold() or item.app == "All apps"]
    title = f"{kind.title()} resources"
    embed = discord.Embed(
        title=title,
        description="Free, legitimate resources for your edits.",
        colour=discord.Colour.from_rgb(87, 242, 135),
    )
    for item in resources:
        embed.add_field(name=item.name, value=f"[{item.app}]({item.url})\n{item.note}", inline=False)
    if not resources:
        embed.description = "No exact match for that app yet. Try `app: all`."
    embed.set_footer(text="Always check the source licence before publishing an edit.")
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


@bot.tree.command(name="resources", description="Find free editing resources")
@discord.app_commands.describe(kind="What you need", app="Editing app (optional)")
@discord.app_commands.choices(
    kind=[
        discord.app_commands.Choice(name="Presets & templates", value="presets"),
        discord.app_commands.Choice(name="Transitions", value="transitions"),
        discord.app_commands.Choice(name="Fonts", value="fonts"),
        discord.app_commands.Choice(name="Sound effects", value="sfx"),
        discord.app_commands.Choice(name="Music", value="music"),
        discord.app_commands.Choice(name="Stock footage", value="footage"),
    ],
    app=[
        discord.app_commands.Choice(name="All apps", value="all"),
        discord.app_commands.Choice(name="After Effects", value="After Effects"),
        discord.app_commands.Choice(name="Premiere Pro", value="Premiere Pro"),
        discord.app_commands.Choice(name="DaVinci Resolve", value="DaVinci Resolve"),
    ],
)
async def resources(interaction: discord.Interaction, kind: str, app: str | None = "all") -> None:
    await interaction.response.send_message(embed=make_resources_embed(kind, app))


@bot.tree.command(name="scenepack", description="Find 411 Scenepacks for a movie or show")
@discord.app_commands.describe(title="Movie, show, or character to search for")
async def scenepack(interaction: discord.Interaction, title: str) -> None:
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
