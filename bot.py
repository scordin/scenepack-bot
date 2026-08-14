"""
EditPacks Discord search, powered by Serper.

Install:
pip install discord.py requests python-dotenv beautifulsoup4

.env:
DISCORD_TOKEN=...
SERPER_API_KEY=...
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

HOST = "editpacks.org"
MAX_PACKS = 4


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str = ""


def is_editpacks_url(url: str) -> bool:
    parsed = urlparse(url)

    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower().removeprefix("www.") == HOST
        and (
            parsed.path.startswith("/source/")
            or parsed.path.startswith("/i/")
        )
    )


def label(value: str) -> str:
    value = re.sub(
        r"\s*[|–—-]\s*(?:411|Scenepacks?|Editing Clips.*)$",
        "",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"\s*[:|–—-]\s*scenepacks?\s*&\s*audios.*$",
        "",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"\s*[·|-]\s*editpacks.*$",
        "",
        value,
        flags=re.I,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip(" -|–—") or "Scenepack"


def category_from_text(*values: str) -> str | None:
    text = " ".join(values).lower()

    categories = [
        ("streamers & youtubers", "Streamer"),
        ("streamers", "Streamer"),
        ("sports", "Sports"),
        ("music", "Music"),
        ("k-pop", "K-Pop"),
        ("anime", "Anime"),
        ("manga", "Manga"),
        ("movies", "Movie"),
        ("movie", "Movie"),
        ("shows", "Show"),
        ("tv series", "TV Series"),
        ("games", "Game"),
        ("game", "Game"),
        ("animations", "Animation"),
    ]

    for search, result in categories:
        if search in text:
            return result

    return None


def metadata(*values: str) -> str | None:
    text = " ".join(values)

    year = re.search(
        r"\b((?:19|20)\d{2})\b",
        text,
    )

    category = category_from_text(*values)

    if year and category:
        return f"{year.group(1)} · {category}"

    if year:
        return year.group(1)

    return category


def extract_pack_count(*values: str) -> int | None:
    """
    EditPacks title/search pages often contain:
    '8 packs'
    '1 pack'
    '90 items'
    """

    text = " ".join(values)

    matches = re.findall(
        r"\b(\d+)\s+(?:packs?|items?)\b",
        text,
        flags=re.I,
    )

    if not matches:
        return None

    return max(int(x) for x in matches)


def search_serper(query: str) -> list[Result]:
    if not SERPER_API_KEY:
        raise RuntimeError(
            "SERPER_API_KEY is missing from .env"
        )

    response = requests.post(
        SERPER_URL,
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "q": f"{query} EditPacks",
            "num": 20,
        },
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError(
            f"Serper returned HTTP "
            f"{response.status_code}: "
            f"{response.text[:180]}"
        )

    seen = set()
    found = []

    for item in response.json().get("organic", []):
        url = item.get("link", "")

        if (
            is_editpacks_url(url)
            and url not in seen
        ):
            seen.add(url)

            found.append(
                Result(
                    label(
                        item.get("title", "")
                    ),
                    url,
                    item.get("snippet", ""),
                )
            )

    return found


def score(
    result: Result,
    query: str,
) -> int:

    terms = [
        x
        for x in re.findall(
            r"[a-z0-9]+",
            query.lower(),
        )
        if len(x) > 1
    ]

    title = result.title.lower()
    snippet = result.snippet.lower()

    score_value = 0

    for term in terms:
        if term in title:
            score_value += 10

        if term in snippet:
            score_value += 2

    # Prefer actual title/source pages over
    # individual item pages when both appear.
    if "/source/" in result.url:
        score_value += 15

    return score_value


def parse_pack_info(text: str) -> str:
    """
    Pull a few useful details from an EditPacks card
    without making the Discord message too long.
    """

    parts = []

    # Dub / Sub / resolution
    quality_matches = re.findall(
        r"\b(?:Dub|Sub|H\.?26[45]|[248]k|\d{3,4}p)\b",
        text,
        flags=re.I,
    )

    if quality_matches:
        quality = []

        for value in quality_matches:
            if value.lower() not in {
                x.lower() for x in quality
            }:
                quality.append(value)

        parts.append(
            " ".join(quality[:2])
        )

    # Duration
    duration = re.search(
        r"\b\d{1,2}:\d{2}\b",
        text,
    )

    if duration:
        parts.append(
            duration.group(0)
        )

    # File size
    size = re.search(
        r"\b\d+(?:\.\d+)?\s*(?:KB|MB|GB)\b",
        text,
        flags=re.I,
    )

    if size:
        parts.append(
            size.group(0)
        )

    # Downloads
    downloads = re.search(
        r"↓\s*([\d,.]+[kKmM]?)",
        text,
    )

    if downloads:
        parts.append(
            f"{downloads.group(1)} DL"
        )

    # Creator
    creator = re.search(
        r"\bby\s+(.+?)(?:requested by|$)",
        text,
        flags=re.I,
    )

    if creator:
        creator_name = creator.group(1).strip()

        # Keep it short
        if len(creator_name) > 35:
            creator_name = creator_name[:32] + "..."

        parts.append(
            f"by {creator_name}"
        )

    return " · ".join(parts)


def page_packs(url: str) -> list[Result]:
    """
    Read individual EditPacks items from a title page.

    EditPacks title pages contain /i/xxxx item links.
    We only keep cards that are actually Scenepacks,
    not Voice Lines.
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

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    found = []
    seen = set()

    for anchor in soup.select(
        'a[href^="/i/"]'
    ):

        href = anchor.get("href", "")

        if not href:
            continue

        if href.startswith("/"):
            href = f"https://{HOST}{href}"

        href = href.split(
            "#",
            1,
        )[0].rstrip("/")

        if href in seen:
            continue

        # Get the surrounding card text.
        parent = anchor

        for _ in range(5):
            if parent.parent:
                parent = parent.parent

            text = parent.get_text(
                " ",
                strip=True,
            )

            if (
                "Scenepack" in text
                or "Voice Line" in text
                or "Audio" in text
            ):
                break

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

        # Don't include voice lines/audio.
        if "Voice Line" in text:
            continue

        # Only actual scenepacks.
        if "Scenepack" not in text:
            continue

        title = anchor.get_text(
            " ",
            strip=True,
        )

        if not title:
            title = "Scenepack"

        info = parse_pack_info(text)

        if info:
            title = f"{title} — {info}"

        seen.add(href)

        found.append(
            Result(
                title[:180],
                href,
            )
        )

        if len(found) >= MAX_PACKS:
            break

    return found


def poster_url(query: str) -> str | None:
    """
    Use the same Serper key to find a poster/image.
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


def unique(
    results: list[Result],
) -> list[Result]:

    seen = set()
    output = []

    for result in results:
        if result.url not in seen:
            seen.add(result.url)
            output.append(result)

    return output


def make_embed(
    query: str,
    results: list[Result],
) -> discord.Embed:

    ranked = sorted(
        results,
        key=lambda r: score(r, query),
        reverse=True,
    )

    main = ranked[0]

    # If we found an EditPacks title page,
    # grab its individual packs.
    packs = page_packs(main.url)

    # If the search result itself is an individual
    # EditPacks item, keep it as a fallback.
    if not packs:
        packs = [
            r
            for r in ranked
            if r.url != main.url
        ]

        if not packs:
            packs = [main]

    packs = unique(packs)[:MAX_PACKS]

    bullets = [
        f"• [{label(pack.title)}]({pack.url})"
        for pack in packs
    ]

    if not bullets:
        bullets = [
            "• No individual packs were indexed yet — "
            "open the title to browse it."
        ]

    info = metadata(
        main.title,
        main.snippet,
    )

    # Try to get the actual total count from
    # EditPacks instead of saying "0 packs".
    total_count = extract_pack_count(
        main.title,
        main.snippet,
    )

    # If the page itself contains more accurate
    # information, read it.
    try:
        page_response = requests.get(
            main.url,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )

        if page_response.ok:
            page_text = BeautifulSoup(
                page_response.text,
                "html.parser",
            ).get_text(
                " ",
                strip=True,
            )

            page_count = extract_pack_count(
                page_text
            )

            if page_count is not None:
                total_count = page_count

            page_info = metadata(
                page_text
            )

            if page_info:
                info = page_info

    except requests.RequestException:
        pass

    if total_count is None:
        total_count = len(packs)

    embed = discord.Embed(
        title=label(main.title),
        url=main.url,
        description=(
            f"**{total_count} packs found**"
            + (
                f"  •  {info}"
                if info
                else ""
            )
        ),
        colour=discord.Colour.from_rgb(
            114,
            137,
            218,
        ),
    )

    embed.set_author(
        name="EditPacks",
        url="https://editpacks.org/",
    )

    embed.add_field(
        name="Available packs",
        value="\n".join(bullets),
        inline=False,
    )

    embed.add_field(
        name="",
        value=(
            f"[View all packs for this title]"
            f"({main.url})"
        ),
        inline=False,
    )

    image = poster_url(query)

    if image:
        embed.set_thumbnail(
            url=image
        )

    embed.set_footer(
        text=f"Results for {query} · 1 title"
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
        f"✓ Bot is ready: "
        f"{bot.user} "
        f"(ID: {bot.user.id})"
    )

    print(
        "✓ /scenepack is synced and ready to use."
    )


@bot.tree.command(
    name="scenepack",
    description="Find EditPacks for a movie, show, anime, sport, streamer, or music",
)
@discord.app_commands.describe(
    title="Movie, show, anime, sport, streamer, musician, or character"
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
            f"Couldn't find an EditPacks page for "
            f"**{title}**. Try another spelling or "
            f"search directly at https://editpacks.org/",
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
