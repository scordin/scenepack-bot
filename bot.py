"""
EditPacks Discord scenepack search, powered by Serper.

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


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

SERPER_URL = "https://google.serper.dev/search"

HOST = "editpacks.org"

MAX_PACKS = 4

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
# EDITPACKS URL CHECK
# ============================================================

def is_editpacks_url(url: str) -> bool:
    parsed = urlparse(url)

    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.lower().removeprefix("www.") == HOST
        and parsed.path.startswith("/source/")
    )


# ============================================================
# TEXT CLEANING
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

    value = re.sub(
        r"\s*:\s*scenepacks?\s*&\s*audios?.*$",
        "",
        value,
        flags=re.I,
    )

    return value.strip(" -|–—") or "Scenepack"


# ============================================================
# SEARCH
# ============================================================

def direct_editpacks_page(query: str) -> list[Result]:
    """
    Try the obvious EditPacks /source/slug URL first.

    This fixes searches like:
        you
        dexter
        naruto
        bleach
        death note

    where the EditPacks page exists but Serper doesn't
    return it reliably.
    """

    # Turn the search into an EditPacks-style slug.
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        query.lower(),
    ).strip("-")

    if not slug:
        return []

    url = f"https://{HOST}/source/{slug}"

    try:
        response = requests.get(
            url,
            timeout=12,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/133.0 Safari/537.36"
                )
            },
            allow_redirects=True,
        )

        if response.status_code != 200:
            return []

        # Make sure this is actually an EditPacks title page
        # and not just some generic 200 response.
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

        # A real source page contains "items" and
        # EditPacks title information.
        if not re.search(
            r"\b\d+\s+items?\b",
            page_text,
            flags=re.I,
        ):
            return []

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
                url=response.url,
                snippet=page_text[:500],
            )
        ]

    except requests.RequestException:
        return []


def search_serper(query: str) -> list[Result]:
    """
    Search EditPacks.

    Order:
        1. Direct /source/slug lookup
        2. Serper fallback
    """

    if not SERPER_API_KEY:
        raise RuntimeError(
            "SERPER_API_KEY is missing from .env"
        )

    # ========================================================
    # 1. DIRECT EDITPACKS LOOKUP
    # ========================================================

    direct = direct_editpacks_page(query)

    if direct:
        return direct

    # ========================================================
    # 2. SERPER FALLBACK
    # ========================================================

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
# SEARCH RESULT RANKING
# ============================================================

def score_result(
    result: Result,
    query: str,
) -> int:

    query_terms = [
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

    for term in query_terms:

        # Exact title matches are useful for normal
        # searches like "dexter".
        if term in title:
            score += 15

        # Search snippets can contain character names.
        if term in snippet:
            score += 10

    # Prefer EditPacks source/title pages.
    if "/source/" in result.url:
        score += 20

    return score


# ============================================================
# FETCH EDITPACKS PAGE
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
# TITLE INFORMATION
# ============================================================

def extract_title_info(
    soup: BeautifulSoup,
) -> tuple[str | None, str | None, str | None]:

    title = None
    category = None
    year = None

    # EditPacks has the actual page title as an H1.
    h1 = soup.find("h1")

    if h1:
        title = clean_text(
            h1.get_text(
                " ",
                strip=True,
            )
        )

    # The page has information like:
    #
    # Shows · 2006
    # Anime · 2002
    # Movies · 2018
    #
    # Find the text directly around the H1.
    if h1:
        previous_text = []

        for element in h1.find_all_previous(
            limit=8
        ):
            text = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if text:
                previous_text.append(text)

        for text in previous_text:

            match = re.search(
                r"\b(Anime|Shows|Movies|Sports|Manga|Games|Streamers(?:\s*&\s*YouTubers)?|Music|Animations|K-Pop|Pictures)\s*[·|]\s*((?:19|20)\d{2})\b",
                text,
                flags=re.I,
            )

            if match:
                category = match.group(1)
                year = match.group(2)
                break

    # Fallback: search the entire page for the same pattern.
    if not category or not year:

        page_text = clean_text(
            soup.get_text(
                " ",
                strip=True,
            )
        )

        match = re.search(
            r"\b(Anime|Shows|Movies|Sports|Manga|Games|Streamers(?:\s*&\s*YouTubers)?|Music|Animations|K-Pop|Pictures)\s*[·|]\s*((?:19|20)\d{2})\b",
            page_text,
            flags=re.I,
        )

        if match:
            category = match.group(1)
            year = match.group(2)

    if category:
        category = clean_text(category)

    return title, category, year


# ============================================================
# TITLE IMAGE
# ============================================================

def extract_title_image(
    soup: BeautifulSoup,
) -> str | None:

    # Best option: Open Graph image.
    og_image = soup.find(
        "meta",
        attrs={
            "property": "og:image"
        },
    )

    if og_image:
        image = og_image.get("content")

        if image:
            return image

    # Twitter image fallback.
    twitter_image = soup.find(
        "meta",
        attrs={
            "name": "twitter:image"
        },
    )

    if twitter_image:
        image = twitter_image.get("content")

        if image:
            return image

    # Final fallback: find the first sensible image.
    for image_tag in soup.select("img"):

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
            src = f"https://{HOST}{src}"

        lower = src.lower()

        if any(
            x in lower
            for x in (
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

def extract_pack_info(card_text: str) -> str:
    """
    Extract a small amount of useful information from
    one EditPacks pack card.

    Example:
    Scenepack 1080p 3:14↓ 139 Dexter by tismpill

    -> 1080p · 3:14 · 139 DL · by tismpill
    """

    text = clean_text(card_text)
    parts = []

    # Resolution
    resolution = re.search(
        r"\b(?:2160p|1440p|1080p|720p|480p|360p|4k|2k)\b",
        text,
        flags=re.I,
    )

    if resolution:
        parts.append(resolution.group(0))

    # Dub / Sub
    audio = re.search(
        r"\b(?:New\s+Dub|Dub|Sub)\b",
        text,
        flags=re.I,
    )

    if audio:
        parts.insert(0, audio.group(0))

    # Duration
    duration = re.search(
        r"\b\d{1,2}:\d{2}\b",
        text,
    )

    if duration:
        parts.append(duration.group(0))

    # File size
    size = re.search(
        r"\b\d+(?:\.\d+)?\s*(?:KB|MB|GB)\b",
        text,
        flags=re.I,
    )

    if size:
        parts.append(size.group(0))

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
        r"\bby\s+(.+?)(?=$|\s+(?:S\d|Preview|Download))",
        text,
        flags=re.I,
    )

    if creator:
        creator_name = clean_text(
            creator.group(1)
        )

        # Remove trailing junk if present
        creator_name = re.sub(
            r"\s+(?:Preview|Download).*$",
            "",
            creator_name,
            flags=re.I,
        ).strip()

        if len(creator_name) > 35:
            creator_name = (
                creator_name[:32] + "..."
            )

        parts.append(
            f"by {creator_name}"
        )

    return " · ".join(parts)


# ============================================================
# FIND CHARACTER / PACK NAME
# ============================================================

def find_nearest_heading(
    anchor,
) -> str | None:

    # EditPacks organizes packs under H2 headings:
    #
    # Dexter Morgan
    # 5
    # Image
    # Dexter Morgan Scenepack
    #
    # Find the closest heading above the card.
    heading = anchor.find_previous(
        ["h2", "h3", "h4"]
    )

    if not heading:
        return None

    value = clean_text(
        heading.get_text(
            " ",
            strip=True,
        )
    )

    if not value:
        return None

    # Ignore the main page title.
    if value.lower() in {
        "all characters",
        "all titles",
        "editpacks",
    }:
        return None

    return value


# ============================================================
# PACK EXTRACTION
# ============================================================

def page_packs(
    soup: BeautifulSoup,
) -> list[Result]:
    """
    Extract individual scenepacks from an EditPacks
    source page.

    Each pack gets:
    - actual pack name
    - character/section when available
    - resolution
    - duration
    - file size
    - downloads
    - creator
    - optional short note

    This also prevents identical-looking pack names.
    """

    found = []
    seen = set()

    # Every individual pack on EditPacks uses /i/
    # links.
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
            href = f"https://{HOST}{href}"

        href = href.split(
            "#",
            1,
        )[0].rstrip("/")

        if href in seen:
            continue

        # ====================================================
        # FIND THE INDIVIDUAL CARD
        # ====================================================

        card = None
        current = anchor

        # Walk upward until we find the smallest element
        # containing:
        #   - Scenepack
        #   - Preview
        #   - exactly one /i/ link
        #
        # This prevents us from accidentally grabbing the
        # entire Dexter page.
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
                and "Preview" in text
                and len(item_links) == 1
            ):
                card = current
                break

        if card is None:
            continue

        # ====================================================
        # CARD TEXT
        # ====================================================

        card_text = clean_text(
            card.get_text(
                " ",
                strip=True,
            )
        )

        # Only actual scenepacks.
        if "Scenepack" not in card_text:
            continue

        # Ignore voice lines.
        if "Voice Line" in card_text:
            continue

        # ====================================================
        # PACK NAME
        # ====================================================

        pack_name = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if not pack_name:
            pack_name = "Scenepack"

        # ====================================================
        # CHARACTER / SECTION
        # ====================================================

        character = None

        # Find the nearest H2/H3 above this pack.
        heading = anchor.find_previous(
            ["h2", "h3", "h4"]
        )

        if heading:
            heading_text = clean_text(
                heading.get_text(
                    " ",
                    strip=True,
                )
            )

            # Don't use the main title as the character.
            if (
                heading_text
                and heading_text.lower()
                not in {
                    "all characters",
                    "all titles",
                    "editpacks",
                }
            ):
                character = heading_text

        # ====================================================
        # INFORMATION
        # ====================================================

        info = extract_pack_info(
            card_text
        )

        # ====================================================
        # OPTIONAL NOTE
        # ====================================================

        note = ""

        # EditPacks sometimes has notes such as:
        # "mix of seasons:"
        # "S1 scp"
        # "S1 ep1, intro pool death scene"
        #
        # Extract only short useful notes.
        #
        # We remove the normal metadata first.
        note_text = re.sub(
            r"Scenepack",
            "",
            card_text,
            flags=re.I,
        )

        note_text = re.sub(
            r"\b(?:2160p|1440p|1080p|720p|480p|360p|4k|2k)\b",
            "",
            note_text,
            flags=re.I,
        )

        note_text = re.sub(
            r"\b(?:New\s+Dub|Dub|Sub)\b",
            "",
            note_text,
            flags=re.I,
        )

        note_text = re.sub(
            r"\b\d{1,2}:\d{2}\b",
            "",
            note_text,
        )

        note_text = re.sub(
            r"\b\d+(?:\.\d+)?\s*(?:KB|MB|GB)\b",
            "",
            note_text,
            flags=re.I,
        )

        note_text = re.sub(
            r"↓\s*[\d,.]+[kKmM]?",
            "",
            note_text,
        )

        note_text = re.sub(
            r"\bby\s+.+?(?=$|\s+(?:Preview|Download))",
            "",
            note_text,
            flags=re.I,
        )

        note_text = clean_text(
            note_text
        )

        # Remove common UI text.
        note_text = re.sub(
            r"\b(?:Preview|Download)\b",
            "",
            note_text,
            flags=re.I,
        )

        note_text = clean_text(
            note_text
        )

        # Only use notes that look meaningful.
        if (
            note_text
            and len(note_text) <= 60
            and note_text.lower()
            not in {
                pack_name.lower(),
                character.lower()
                if character
                else "",
            }
        ):
            # Don't accidentally use the title
            # or character name as a note.
            if (
                "Scenepack"
                not in note_text
            ):
                note = note_text

        # ====================================================
        # BUILD DISPLAY NAME
        # ====================================================

        display_name = pack_name

        # If there is useful metadata, add it.
        if info:
            display_name += (
                f" — {info}"
            )

        # Add short note only when useful.
        if note:
            display_name += (
                f" · {note}"
            )

        # ====================================================
        # SAVE
        # ====================================================

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

        seen.add(result.url)
        output.append(result)

    return output


# ============================================================
# EMBED
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

    # --------------------------------------------------------
    # Load the ACTUAL EditPacks page.
    # --------------------------------------------------------

    page = fetch_page(
        main.url
    )

    if page:
        soup, page_text = page

        real_title, category, year = (
            extract_title_info(soup)
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

    # --------------------------------------------------------
    # Use EditPacks' own title.
    # --------------------------------------------------------

    title = (
        real_title
        or main.title
        or query
    )

    # --------------------------------------------------------
    # If the search was for a character, the search
    # result may point to a title page. That's okay,
    # but make sure we actually found matching packs.
    # --------------------------------------------------------

    if packs:

        query_terms = [
            x
            for x in re.findall(
                r"[a-z0-9]+",
                query.lower(),
            )
            if len(x) > 1
        ]

        matching_packs = []

        for pack in packs:

            pack_text = (
                f"{pack.title}"
            ).lower()

            if any(
                term in pack_text
                for term in query_terms
            ):
                matching_packs.append(
                    pack
                )

        # If we have matching character packs,
        # prefer those.
        if matching_packs:
            packs = matching_packs

    packs = unique(
        packs
    )[:MAX_PACKS]

    # --------------------------------------------------------
    # Count actual EditPacks items.
    # --------------------------------------------------------

    total_packs = None

    if page_text:

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

    # --------------------------------------------------------
    # Information line.
    #
    # IMPORTANT:
    # category/year comes ONLY from EditPacks.
    # No guessing.
    # --------------------------------------------------------

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
        " · ".join(info_parts)
        if info_parts
        else None
    )

    # --------------------------------------------------------
    # Pack bullets.
    # --------------------------------------------------------

    bullets = [
        f"• [{pack.title}]({pack.url})"
        for pack in packs
    ]

    if not bullets:

        bullets = [
            "• No individual packs were indexed yet — "
            "open the title to browse it."
        ]

    # --------------------------------------------------------
    # EMBED
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # ACTUAL EDITPACKS IMAGE
    # --------------------------------------------------------

    if image:
        embed.set_thumbnail(
            url=image
        )

    embed.set_footer(
        text=f"Results for {query} · 1 title"
    )

    return embed


# ============================================================
# BOT
# ============================================================

class Bot(discord.Client):

    def __init__(self) -> None:

        super().__init__(
            intents=discord.Intents.none()
        )

        self.tree = (
            discord.app_commands.CommandTree(
                self
            )
        )

    async def setup_hook(
        self,
    ) -> None:

        await self.tree.sync()


bot = Bot()


# ============================================================
# READY
# ============================================================

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


# ============================================================
# COMMAND
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
        "game, music, or character"
    ),
)
async def scenepack(
    interaction: discord.Interaction,
    title: str,
) -> None:

    title = title.strip()

    # --------------------------------------------------------
    # Channel restriction
    # --------------------------------------------------------

    if (
        interaction.channel_id
        != ALLOWED_CHANNEL_ID
    ):

        return await interaction.response.send_message(
            f"Use this command in "
            f"<#{ALLOWED_CHANNEL_ID}>.",
            ephemeral=True,
        )

    # --------------------------------------------------------
    # Empty search
    # --------------------------------------------------------

    if not title:

        await interaction.response.send_message(
            "Please enter a title to search for.",
            ephemeral=True,
        )

        return

    # --------------------------------------------------------
    # Searching
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # No result
    # --------------------------------------------------------

    if not results:

        await interaction.followup.send(
            f"Couldn't find an EditPacks page for "
            f"**{title}**.\n"
            f"Try another spelling or search directly "
            f"at https://editpacks.org/",
            ephemeral=True,
        )

        return

    # --------------------------------------------------------
    # Build embed
    # --------------------------------------------------------

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

    bot.run(TOKEN)
