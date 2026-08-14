"""
VeelCP Discord scenepack search, powered by Serper.

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


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str = ""


def is_pack_url(url: str) -> bool:
    parsed = urlparse(url)

    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower().removeprefix("www.") == HOST
        and (
            parsed.path.startswith("/tv/")
            or parsed.path.startswith("/movie/")
            or parsed.path.startswith("/game/")
            or parsed.path.startswith("/other/")
            or "scenepack=" in parsed.query
        )
    )


def label(value: str) -> str:
    value = re.sub(
        r"\s*[|–—-]\s*(?:411|Scenepacks?|Editing Clips.*)$",
        "",
        value,
        flags=re.I,
    )

    return re.sub(r"\s+", " ", value).strip(" -|–—") or "Scenepack"


def metadata(*values: str) -> str | None:
    text = " ".join(values)

    year = re.search(r"\b((?:19|20)\d{2})\b", text)

    lower = text.lower()

    kind = (
        "TV series"
        if any(
            x in lower
            for x in ("tv series", "television series", "season ")
        )
        else "Film"
        if any(x in lower for x in ("film", "movie"))
        else None
    )

    if year and kind:
        return f"{year.group(1)} · {kind}"

    return year.group(1) if year else kind


def search_serper(query: str) -> list[Result]:
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY is missing from .env")

    response = requests.post(
        SERPER_URL,
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "q": f"{query} Veel Scenepacks",
            "num": 12,
        },
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError(
            f"Serper returned HTTP {response.status_code}: "
            f"{response.text[:180]}"
        )

    seen = set()
    found = []

    for item in response.json().get("organic", []):
        url = item.get("link", "")

        if is_pack_url(url) and url not in seen:
            seen.add(url)

            found.append(
                Result(
                    label(item.get("title", "")),
                    url,
                    item.get("snippet", ""),
                )
            )

    # If Serper doesn't directly find VeelCP,
    # use the TMDB ID method from the second code.
    if found:
        return found

    return tmdb_to_veel_page(query)


def tmdb_to_veel_page(query: str) -> list[Result]:
    """
    Find the TMDB title ID and construct the corresponding
    VeelCP page.
    """

    if not SERPER_API_KEY:
        return []

    response = requests.post(
        SERPER_URL,
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "q": f"{query} TMDB",
            "num": 8,
        },
        timeout=20,
    )

    if not response.ok:
        return []

    for item in response.json().get("organic", []):
        link = item.get("link", "")

        parsed = urlparse(link)

        match = re.match(
            r"^/(tv|movie)/(\d+)",
            parsed.path,
        )

        if parsed.netloc.lower().endswith("themoviedb.org") and match:
            media_type, media_id = match.groups()

            url = (
                f"https://veelscp.com/"
                f"{media_type}/{media_id}"
                f"?search={quote_plus(query)}"
            )

            return [
                Result(
                    label(item.get("title", query)),
                    url,
                    item.get("snippet", ""),
                )
            ]

    return []


def score(result: Result, query: str) -> int:
    terms = [
        x
        for x in re.findall(r"[a-z0-9]+", query.lower())
        if len(x) > 1
    ]

    haystack = f"{result.title} {result.snippet}".lower()

    return (
        sum(
            10
            for x in terms
            if x in result.title.lower()
        )
        +
        sum(
            2
            for x in terms
            if x in haystack
        )
    )


def page_packs(url: str) -> list[Result]:
    """
    Read the individual scenepack/download links exposed
    on the public VeelCP title page.
    """

    try:
        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )

        response.raise_for_status()

    except requests.RequestException:
        return []

    found = []
    seen = {url.rstrip("/")}

    for anchor in BeautifulSoup(
        response.text,
        "html.parser",
    ).select("a[href]"):

        href = anchor["href"]

        if href.startswith("/"):
            href = f"https://{HOST}{href}"

        href = href.split("#", 1)[0].rstrip("/")

        text = label(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if (
            is_pack_url(href)
            and href not in seen
            and text != "Scenepack"
        ):
            seen.add(href)

            found.append(
                Result(
                    text[:120],
                    href,
                )
            )

    return found


def poster_url(query: str) -> str | None:
    """
    Use the same Serper key to find a poster.
    """

    try:
        response = requests.post(
            SERPER_IMAGES_URL,
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "q": f"{query} official poster",
                "num": 1,
            },
            timeout=15,
        )

        if not response.ok:
            return None

        image = (
            response.json().get("images")
            or [{}]
        )[0]

        return (
            image.get("imageUrl")
            or image.get("thumbnailUrl")
        )

    except (
        requests.RequestException,
        ValueError,
    ):
        return None


def unique(results: list[Result]) -> list[Result]:
    seen = set()
    output = []

    for result in results:
        if result.url not in seen:
            seen.add(result.url)
            output.append(result)

    return output


def make_embed(query: str, results: list[Result]) -> discord.Embed:
    ranked = sorted(
        results,
        key=lambda r: score(r, query),
        reverse=True,
    )

    main = ranked[0]

    info = metadata(main.title, main.snippet)

    # Determine category
    lower = f"{main.title} {main.snippet}".lower()

    if any(x in lower for x in ("tv series", "television series", "season ")):
        category = "TV"
    elif any(x in lower for x in ("film", "movie")):
        category = "Movie"
    elif "game" in lower:
        category = "Game"
    else:
        category = "Other"

    embed = discord.Embed(
        title="Veel Scenepacks",
        url=main.url,
        colour=discord.Colour.from_rgb(114, 137, 218),
    )

    # Main title
    embed.add_field(
        name="🎬 Title",
        value=f"**{label(main.title)}**",
        inline=False,
    )

    # Category + year
    details = f"**Category:** {category}"

    if info:
        details += f"\n**Release:** {info}"

    embed.add_field(
        name="📁 Information",
        value=details,
        inline=False,
    )

    # Main Veel page
    embed.add_field(
        name="📦 Available Scenepacks",
        value=(
            "Scenepacks are available on the Veel page.\n"
            f"**[Open {label(main.title)} on Veel →]({main.url})**"
        ),
        inline=False,
    )

    # Search-related sections that Veel provides
    embed.add_field(
        name="🎞️ Content",
        value="**Trailer**  •  **Cast**",
        inline=False,
    )


    # Poster
    image = poster_url(query)

    if image:
        embed.set_thumbnail(url=image)

    embed.set_author(
        name="Veel Scenepacks",
        url="https://veelscp.com/",
    )

    embed.set_footer(
        text=f"Veel Scenepacks · Search: {query}"
    )

    return embed


class Bot(discord.Client):

    def __init__(self) -> None:
        super().__init__(
            intents=discord.Intents.none()
        )

        self.tree = discord.app_commands.CommandTree(
            self
        )

    async def setup_hook(self) -> None:
        await self.tree.sync()


bot = Bot()


@bot.event
async def on_ready() -> None:
    print(
        f"✓ Bot is ready: {bot.user} "
        f"(ID: {bot.user.id})"
    )

    print(
        "✓ /scenepack is synced and ready to use."
    )


@bot.tree.command(
    name="scenepack",
    description="Find Veel Scenepacks for a movie or show",
)
@discord.app_commands.describe(
    title="Movie, show, or character to search for"
)
async def scenepack(
    interaction: discord.Interaction,
    title: str,
) -> None:

    title = title.strip()

    if interaction.channel_id != 1537846917130620939:
        return await interaction.response.send_message(
            "Use this command in <#1537846917130620939>.",
            ephemeral=True,
        )

    if not title:
        await interaction.response.send_message(
            "Please enter a title to search for.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(
        thinking=True
    )

    try:
        results = await asyncio.to_thread(
            search_serper,
            title,
        )

    except Exception as exc:
        await interaction.followup.send(
            f"Search failed: `{exc}`",
            ephemeral=True,
        )
        return

    if not results:
        await interaction.followup.send(
            f"Couldn't find a VeelCP page for "
            f"**{title}**. Try another spelling, "
            f"or search directly at "
            f"https://veelscp.com/",
            ephemeral=True,
        )
        return

    embed = await asyncio.to_thread(
        make_embed,
        title,
        results,
    )

    await interaction.followup.send(
        embed=embed
    )


if __name__ == "__main__":

    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing from .env"
        )

    bot.run(TOKEN)
