"""
Edit Office Discord Bot
-----------------------

Command:
    /scenepack <title>

Install:
    pip install discord.py requests python-dotenv beautifulsoup4

.env:
    DISCORD_TOKEN=your_discord_bot_token
    SERPER_API_KEY=your_serper_api_key
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


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

SERPER_URL = "https://google.serper.dev/search"

HOST = "editpacks.org"

MAX_PACKS = 4

# Your scenepack channel
ALLOWED_CHANNEL_ID = 1537846917130620939


# ============================================================
# RESULT
# ============================================================

@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str = ""


# ============================================================
# HELPERS
# ============================================================

def clean_text(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        value or "",
    ).strip()


def clean_title(value: str) -> str:
    value = clean_text(value)

    value = re.sub(
        r"\s*[|–—-]\s*editpacks.*$",
        "",
        value,
        flags=re.I,
    )

    return value.strip(
        " -|–—"
    ) or "Scenepack"


def is_editpacks_url(url: str) -> bool:
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower().removeprefix("www.")
            == HOST
            and parsed.path.startswith("/source/")
        )

    except Exception:
        return False


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_page(
    url: str,
) -> tuple[BeautifulSoup, str] | None:

    try:
        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/133.0 Safari/537.36"
                )
            },
        )

        response.raise_for_status()

    except requests.RequestException:
        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    return soup, page_text


# ============================================================
# DIRECT EDITPACKS LOOKUP
# ============================================================

def direct_editpacks_page(
    query: str,
) -> list[Result]:

    """
    Try the direct EditPacks source URL first.

    Example:
        /source/you
        /source/dexter
        /source/naruto
    """

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        query.lower(),
    ).strip("-")

    if not slug:
        return []

    url = (
        f"https://{HOST}/source/{slug}"
    )

    page = fetch_page(url)

    if not page:
        return []

    soup, page_text = page

    h1 = soup.find("h1")

    if not h1:
        return []

    title = clean_text(
        h1.get_text(
            " ",
            strip=True,
        )
    )

    if not title:
        return []

    return [
        Result(
            title=title,
            url=url,
            snippet=page_text[:500],
        )
    ]


# ============================================================
# SERPER SEARCH
# ============================================================

def search_serper(
    query: str,
) -> list[Result]:

    if not SERPER_API_KEY:
        raise RuntimeError(
            "SERPER_API_KEY is missing from .env"
        )

    # Try direct page first
    direct = direct_editpacks_page(
        query
    )

    if direct:
        return direct

    # Fall back to Serper
    response = requests.post(
        SERPER_URL,
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "q": f"{query} EditPacks scenepack",
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

    results = []
    seen = set()

    for item in response.json().get(
        "organic",
        [],
    ):

        url = item.get(
            "link",
            "",
        )

        if not is_editpacks_url(url):
            continue

        if url in seen:
            continue

        seen.add(url)

        results.append(
            Result(
                title=clean_title(
                    item.get(
                        "title",
                        "",
                    )
                ),
                url=url,
                snippet=clean_text(
                    item.get(
                        "snippet",
                        "",
                    )
                ),
            )
        )

    return results


# ============================================================
# SCORE RESULTS
# ============================================================

def score_result(
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

    score = 0

    for term in terms:

        if term in title:
            score += 15

        if term in snippet:
            score += 5

    if "/source/" in result.url:
        score += 20

    return score


# ============================================================
# TITLE INFORMATION
# ============================================================

def extract_title_info(
    soup: BeautifulSoup,
) -> tuple[
    str | None,
    str | None,
    str | None,
]:

    title = None
    category = None
    year = None

    h1 = soup.find("h1")

    if h1:
        title = clean_text(
            h1.get_text(
                " ",
                strip=True,
            )
        )

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    # Try to read category/year from EditPacks
    match = re.search(
        r"\b("
        r"Anime|"
        r"Shows|"
        r"Movies|"
        r"Sports|"
        r"Manga|"
        r"Games|"
        r"Streamers(?:\s*&\s*YouTubers)?|"
        r"Music|"
        r"Animations|"
        r"K-Pop|"
        r"Pictures"
        r")"
        r"\s*[·|]\s*"
        r"((?:19|20)\d{2})\b",
        page_text,
        flags=re.I,
    )

    if match:

        category = clean_text(
            match.group(1)
        )

        year = match.group(2)

    return (
        title,
        category,
        year,
    )


# ============================================================
# TITLE IMAGE
# ============================================================

def extract_title_image(
    soup: BeautifulSoup,
) -> str | None:

    # OpenGraph image
    og_image = soup.find(
        "meta",
        attrs={
            "property": "og:image"
        },
    )

    if og_image:

        image = og_image.get(
            "content"
        )

        if image:
            return image

    # Twitter image
    twitter_image = soup.find(
        "meta",
        attrs={
            "name": "twitter:image"
        },
    )

    if twitter_image:

        image = twitter_image.get(
            "content"
        )

        if image:
            return image

    # Fallback to page images
    for image_tag in soup.select(
        "img"
    ):

        src = (
            image_tag.get("src")
            or image_tag.get("data-src")
            or image_tag.get("data-lazy-src")
        )

        if not src:
            continue

        if src.startswith("//"):
            src = "https:" + src

        elif src.startswith("/"):
            src = (
                f"https://{HOST}{src}"
            )

        lower = src.lower()

        if any(
            word in lower
            for word in (
                "logo",
                "favicon",
                "avatar",
            )
        ):
            continue

        return src

    return None


# ============================================================
# PACK INFORMATION
# ============================================================

def extract_pack_info(
    card_text: str,
) -> str:

    text = clean_text(
        card_text
    )

    parts = []

    # Resolution
    resolution = re.search(
        r"\b(?:2160p|1440p|1080p|720p|480p|360p|4k|2k)\b",
        text,
        flags=re.I,
    )

    if resolution:
        parts.append(
            resolution.group(0)
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

    return " · ".join(parts)


# ============================================================
# EXTRACT INDIVIDUAL PACKS
# ============================================================

def page_packs(
    soup: BeautifulSoup,
) -> list[Result]:

    found = []
    seen = set()

    for anchor in soup.select(
        'a[href^="/i/"]'
    ):

        href = anchor.get(
            "href",
            "",
        )

        if not href:
            continue

        if href.startswith("/"):
            href = (
                f"https://{HOST}{href}"
            )

        href = href.split(
            "#",
            1,
        )[0].rstrip("/")

        if href in seen:
            continue

        # Find the containing pack card
        card = None
        current = anchor

        for _ in range(10):

            if not current.parent:
                break

            current = current.parent

            text = clean_text(
                current.get_text(
                    " ",
                    strip=True,
                )
            )

            item_links = current.select(
                'a[href^="/i/"]'
            )

            if (
                "Scenepack" in text
                and len(item_links) == 1
            ):
                card = current
                break

        if card is None:
            continue

        card_text = clean_text(
            card.get_text(
                " ",
                strip=True,
            )
        )

        if "Scenepack" not in card_text:
            continue

        # Ignore voice lines
        if "Voice Line" in card_text:
            continue

        pack_name = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if not pack_name:
            pack_name = "Scenepack"

        info = extract_pack_info(
            card_text
        )

        display_name = pack_name

        if info:
            display_name += (
                f" — {info}"
            )

        seen.add(href)

        found.append(
            Result(
                title=display_name[:180],
                url=href,
            )
        )

    return found


# ============================================================
# UNIQUE
# ============================================================

def unique(
    results: list[Result],
) -> list[Result]:

    seen = set()
    output = []

    for result in results:

        if result.url in seen:
            continue

        seen.add(
            result.url
        )

        output.append(
            result
        )

    return output


# ============================================================
# CREATE EMBED
# ============================================================

def make_embed(
    query: str,
    results: list[Result],
) -> discord.Embed:

    ranked = sorted(
        results,
        key=lambda r: score_result(
            r,
            query,
        ),
        reverse=True,
    )

    main = ranked[0]

    page = fetch_page(
        main.url
    )

    if page:

        soup, page_text = page

        (
            real_title,
            category,
            year,
        ) = extract_title_info(
            soup
        )

        packs = page_packs(
            soup
        )

        image = extract_title_image(
            soup
        )

    else:

        soup = None
        page_text = ""

        real_title = None
        category = None
        year = None

        packs = []
        image = None

    # Title
    title = (
        real_title
        or main.title
        or query
    )

    # Pack count
    total_packs = None

    count_match = re.search(
        r"\b(\d+)\s+items?\b",
        page_text,
        flags=re.I,
    )

    if count_match:

        total_packs = int(
            count_match.group(1)
        )

    if total_packs is None:
        total_packs = len(packs)

    packs = unique(
        packs
    )[:MAX_PACKS]

    # Info
    info_parts = []

    if year:
        info_parts.append(
            year
        )

    if category:
        info_parts.append(
            category
        )

    info = (
        " · ".join(
            info_parts
        )
        if info_parts
        else None
    )

    # Pack bullets
    bullets = [
        f"• [{pack.title}]({pack.url})"
        for pack in packs
    ]

    if not bullets:

        bullets = [
            "• No individual packs were indexed yet — "
            "open the title to browse it."
        ]

    # Embed
    embed = discord.Embed(
        title=title,
        url=main.url,
        description=(
            f"**{total_packs} packs found**"
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
        value="\n".join(
            bullets
        ),
        inline=False,
    )

    embed.add_field(
        name="",
        value=(
            "[View all packs for this title]"
            f"({main.url})"
        ),
        inline=False,
    )

    if image:

        embed.set_thumbnail(
            url=image
        )

    embed.set_footer(
        text=(
            f"Results for {query} · 1 title"
        )
    )

    return embed


# ============================================================
# BOT
# ============================================================

class Bot(
    discord.Client
):

    def __init__(self):

        super().__init__(
            intents=discord.Intents.none()
        )

        self.tree = (
            discord.app_commands.CommandTree(
                self
            )
        )

async def setup_hook(self):
    # Remove old globally registered commands
    self.tree.clear_commands(guild=None)

    # Re-add ONLY the current /scenepack command
    self.tree.add_command(scenepack)

    # Sync the cleaned command list with Discord
    await self.tree.sync()

    print("✓ Old commands cleared.")
    print("✓ Only /scenepack has been synced.")


bot = Bot()


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():

    print(
        f"✓ Bot is ready: "
        f"{bot.user} "
        f"(ID: {bot.user.id})"
    )

    print(
        "✓ /scenepack is synced and ready to use."
    )


# ============================================================
# /SCENEPACK
# ============================================================

@bot.tree.command(
    name="scenepack",
    description=(
        "Find EditPacks for a movie, show, anime, "
        "sport, streamer, game, or music"
    ),
)
@discord.app_commands.describe(
    title=(
        "Movie, show, anime, sport, streamer, "
        "game, or music"
    ),
)
async def scenepack(
    interaction: discord.Interaction,
    title: str,
):

    title = title.strip()

    # Channel restriction
    if (
        interaction.channel_id
        != ALLOWED_CHANNEL_ID
    ):

        return await interaction.response.send_message(
            f"Use this command in "
            f"<#{ALLOWED_CHANNEL_ID}>.",
            ephemeral=True,
        )

    # Empty search
    if not title:

        await interaction.response.send_message(
            "Please enter a title to search for.",
            ephemeral=True,
        )

        return

    await interaction.response.defer(
        thinking=True
    )

    # Search
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

    # No results
    if not results:

        await interaction.followup.send(
            f"Couldn't find an EditPacks page for "
            f"**{title}**.\n"
            f"Try another spelling or search directly "
            f"at https://editpacks.org/",
            ephemeral=True,
        )

        return

    # Create embed
    embed = await asyncio.to_thread(
        make_embed,
        title,
        results,
    )

    await interaction.followup.send(
        embed=embed
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    if not TOKEN:

        raise RuntimeError(
            "DISCORD_TOKEN is missing from .env"
        )

    if not SERPER_API_KEY:

        raise RuntimeError(
            "SERPER_API_KEY is missing from .env"
        )

    bot.run(
        TOKEN
    )
