"""
Edit Office Discord Bot
-----------------------

Commands:
    /scenepack <title>
    /resources

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

# Your scenepack/resources channel
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
# GENERAL HELPERS
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
            parsed.scheme in {
                "http",
                "https",
            }
            and parsed.netloc.lower().removeprefix("www.")
            == HOST
            and parsed.path.startswith("/source/")
        )

    except Exception:
        return False


# ============================================================
# EDITPACKS PAGE REQUEST
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
# DIRECT EDITPACKS SEARCH
# ============================================================

def direct_editpacks_page(
    query: str,
) -> list[Result]:

    """
    Try:

        https://editpacks.org/source/<query>

    before using Serper.

    This is important for searches like:

        you
        dexter
        naruto
        bleach
        breaking bad

    where the page exists but Google/Serper might not
    return it.
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

    # Make sure this is actually a source page.
    # EditPacks source pages contain item counts.
    if not re.search(
        r"\b\d+\s+items?\b",
        page_text,
        flags=re.I,
    ):
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

    # --------------------------------------------------------
    # First try the obvious EditPacks URL.
    # --------------------------------------------------------

    direct = direct_editpacks_page(
        query
    )

    if direct:
        return direct

    # --------------------------------------------------------
    # If direct lookup failed, use Serper.
    # --------------------------------------------------------

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
# SEARCH RESULT SCORE
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

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    h1 = soup.find("h1")

    if h1:
        title = clean_text(
            h1.get_text(
                " ",
                strip=True,
            )
        )

    # --------------------------------------------------------
    # CATEGORY + YEAR
    #
    # We DON'T guess these anymore.
    # We read them from EditPacks.
    # --------------------------------------------------------

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )

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

    # --------------------------------------------------------
    # OpenGraph image
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Twitter image
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Fallback to page images
    # --------------------------------------------------------

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

    """
    Extract useful information from a single
    EditPacks pack.

    Example:

        1080p · 3:14 · 139 DL · by tismpill
    """

    text = clean_text(
        card_text
    )

    parts = []

    # --------------------------------------------------------
    # AUDIO
    # --------------------------------------------------------

    audio = re.search(
        r"\b(?:New\s+Dub|Dub|Sub)\b",
        text,
        flags=re.I,
    )

    if audio:
        parts.append(
            audio.group(0)
        )

    # --------------------------------------------------------
    # RESOLUTION
    # --------------------------------------------------------

    resolution = re.search(
        r"\b(?:2160p|1440p|1080p|720p|480p|360p|4k|2k)\b",
        text,
        flags=re.I,
    )

    if resolution:
        parts.append(
            resolution.group(0)
        )

    # --------------------------------------------------------
    # DURATION
    # --------------------------------------------------------

    duration = re.search(
        r"\b\d{1,2}:\d{2}\b",
        text,
    )

    if duration:
        parts.append(
            duration.group(0)
        )

    # --------------------------------------------------------
    # FILE SIZE
    # --------------------------------------------------------

    size = re.search(
        r"\b\d+(?:\.\d+)?\s*(?:KB|MB|GB)\b",
        text,
        flags=re.I,
    )

    if size:
        parts.append(
            size.group(0)
        )

    # --------------------------------------------------------
    # DOWNLOADS
    # --------------------------------------------------------

    downloads = re.search(
        r"↓\s*([\d,.]+[kKmM]?)",
        text,
    )

    if downloads:
        parts.append(
            f"{downloads.group(1)} DL"
        )

    # --------------------------------------------------------
    # CREATOR
    # --------------------------------------------------------

    creator = re.search(
        r"\bby\s+(.+?)(?=$|\s+(?:Preview|Download|S\d))",
        text,
        flags=re.I,
    )

    if creator:

        creator_name = clean_text(
            creator.group(1)
        )

        creator_name = re.sub(
            r"\s+(?:Preview|Download).*$",
            "",
            creator_name,
            flags=re.I,
        ).strip()

        if len(creator_name) > 35:
            creator_name = (
                creator_name[:32]
                + "..."
            )

        parts.append(
            f"by {creator_name}"
        )

    return " · ".join(
        parts
    )


# ============================================================
# PACK EXTRACTION
# ============================================================

def page_packs(
    soup: BeautifulSoup,
) -> list[Result]:

    """
    Extract individual EditPacks /i/ packs.

    The parser deliberately searches for the smallest
    card containing one /i/ link + Scenepack text.
    This prevents duplicate pack names.
    """

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

        # ----------------------------------------------------
        # Find the actual card.
        # ----------------------------------------------------

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
                and "Preview" in text
                and len(item_links) == 1
            ):
                card = current
                break

        if card is None:
            continue

        # ----------------------------------------------------
        # Card text
        # ----------------------------------------------------

        card_text = clean_text(
            card.get_text(
                " ",
                strip=True,
            )
        )

        if "Scenepack" not in card_text:
            continue

        # Ignore voice lines.
        if "Voice Line" in card_text:
            continue

        # ----------------------------------------------------
        # Pack name
        # ----------------------------------------------------

        pack_name = clean_text(
            anchor.get_text(
                " ",
                strip=True,
            )
        )

        if not pack_name:
            pack_name = "Scenepack"

        # ----------------------------------------------------
        # Information
        # ----------------------------------------------------

        info = extract_pack_info(
            card_text
        )

        # ----------------------------------------------------
        # Optional season information
        # ----------------------------------------------------

        season = re.search(
            r"\bS\d+(?:\s*[-–]\s*S\d+)?\b",
            card_text,
            flags=re.I,
        )

        # ----------------------------------------------------
        # Build display
        # ----------------------------------------------------

        display_name = pack_name

        if info:
            display_name += (
                f" — {info}"
            )

        if season:
            season_text = season.group(0)

            if season_text.lower() not in display_name.lower():
                display_name += (
                    f" · {season_text}"
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
# UNIQUE RESULTS
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
# SCENEPACK EMBED
# ============================================================

def make_embed(
    query: str,
    results: list[Result],
) -> discord.Embed:

    # --------------------------------------------------------
    # Pick best result
    # --------------------------------------------------------

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
    # Read actual EditPacks page
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    title = (
        real_title
        or main.title
        or query
    )

    # --------------------------------------------------------
    # Pack count
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Limit displayed packs
    # --------------------------------------------------------

    packs = unique(
        packs
    )[:MAX_PACKS]

    # --------------------------------------------------------
    # Info
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
        " · ".join(
            info_parts
        )
        if info_parts
        else None
    )

    # --------------------------------------------------------
    # Pack bullets
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
        text=(
            f"Results for {query} · 1 title"
        )
    )

    return embed


# ============================================================
# RESOURCES
# ============================================================

RESOURCE_CATEGORIES = {

    "vfx": {
        "name": "🎨 VFX",
        "description": (
            "Visual effects, particles, "
            "smoke, energy, explosions and more."
        ),
        "resources": [
            (
                "ProductionCrate",
                "Free VFX, textures, overlays and visual effects.",
                "https://productioncrate.com/",
            ),
            (
                "FootageCrate",
                "Free VFX and pre-keyed video effects.",
                "https://footagecrate.com/",
            ),
            (
                "FreeVisuals",
                "Free editing assets including VFX and motion graphics.",
                "https://www.freevisuals.net/",
            ),
        ],
    },

    "sfx": {
        "name": "🔊 SFX",
        "description": (
            "Sound effects for edits, anime clips, "
            "movies, gaming and more."
        ),
        "resources": [
            (
                "Pixabay Sound Effects",
                "Large library of free sound effects.",
                "https://pixabay.com/sound-effects/",
            ),
            (
                "Mixkit SFX",
                "Free impacts, whooshes, risers, glitches and more.",
                "https://mixkit.co/free-sound-effects/",
            ),
            (
                "Freesound",
                "Huge community library of sound effects.",
                "https://freesound.org/",
            ),
        ],
    },

    "transitions": {
        "name": "🔄 Transitions",
        "description": (
            "Zooms, glitches, slides, spins, "
            "wipes and other transitions."
        ),
        "resources": [
            (
                "Mixkit — After Effects",
                "Free After Effects transitions and templates.",
                "https://mixkit.co/free-after-effects-templates/transitions/",
            ),
            (
                "Mixkit — Premiere Pro",
                "Free Premiere Pro transition templates.",
                "https://mixkit.co/free-premiere-pro-templates/transitions/",
            ),
            (
                "Mixkit — DaVinci Resolve",
                "Free DaVinci Resolve transition templates.",
                "https://mixkit.co/free-davinci-resolve-templates/transitions/",
            ),
        ],
    },

    "overlays": {
        "name": "🖼️ Overlays",
        "description": (
            "Film burns, VHS, glitches, "
            "particles, light leaks and textures."
        ),
        "resources": [
            (
                "ProductionCrate",
                "VFX, film burns, VHS, textures and overlay effects.",
                "https://footagecrate.productioncrate.com/textures-and-overlay-filters-categories.html",
            ),
            (
                "Mixkit Overlays",
                "Free overlay templates and motion graphics.",
                "https://mixkit.co/free-after-effects-templates/overlay/",
            ),
            (
                "FreeVisuals",
                "Free overlays, motion graphics and editing assets.",
                "https://www.freevisuals.net/",
            ),
        ],
    },

    "music": {
        "name": "🎵 Music",
        "description": (
            "Music and audio resources "
            "for your edits."
        ),
        "resources": [
            (
                "Pixabay Music",
                "Free music and audio for creative projects.",
                "https://pixabay.com/music/",
            ),
            (
                "Mixkit Music",
                "Free music tracks for videos and projects.",
                "https://mixkit.co/free-stock-music/",
            ),
        ],
    },

    "fonts": {
        "name": "🔤 Fonts",
        "description": (
            "Fonts for subtitles, typography, "
            "posters and motion graphics."
        ),
        "resources": [
            (
                "Google Fonts",
                "Large collection of open-source fonts.",
                "https://fonts.google.com/",
            ),
            (
                "DaFont",
                "Huge collection of display and creative fonts.",
                "https://www.dafont.com/",
            ),
            (
                "Font Squirrel",
                "Free fonts with licensing information.",
                "https://www.fontsquirrel.com/",
            ),
        ],
    },

    "presets": {
        "name": "🎛️ Presets & Templates",
        "description": (
            "Editing presets, templates "
            "and ready-made project assets."
        ),
        "resources": [
            (
                "Mixkit",
                "Free templates for After Effects, Premiere Pro and more.",
                "https://mixkit.co/",
            ),
            (
                "FreeVisuals",
                "Free templates, presets, LUTs and editing resources.",
                "https://www.freevisuals.net/",
            ),
            (
                "Motion Array — Free",
                "Free editing templates, presets and assets.",
                "https://motionarray.com/browse/free/",
            ),
        ],
    },

    "luts": {
        "name": "🎨 LUTs / Coloring",
        "description": (
            "LUTs, color grades and resources "
            "for cinematic coloring."
        ),
        "resources": [
            (
                "FreeVisuals",
                "Free LUTs and color grading resources.",
                "https://www.freevisuals.net/",
            ),
            (
                "IWLTBAP",
                "Color grading tools, LUTs and editing resources.",
                "https://luts.iwltbap.com/",
            ),
        ],
    },

    "footage": {
        "name": "🎥 Stock Footage",
        "description": (
            "Background footage, textures, "
            "B-roll and visual assets."
        ),
        "resources": [
            (
                "Pexels",
                "Free stock videos and footage.",
                "https://www.pexels.com/videos/",
            ),
            (
                "Pixabay",
                "Free videos, images and creative assets.",
                "https://pixabay.com/videos/",
            ),
            (
                "Mixkit",
                "Free stock video and creative assets.",
                "https://mixkit.co/free-stock-video/",
            ),
        ],
    },
}


# ============================================================
# RESOURCE DROPDOWN
# ============================================================

class ResourceSelect(
    discord.ui.Select
):

    def __init__(self):

        options = [

            discord.SelectOption(
                label="VFX",
                value="vfx",
                emoji="🎨",
                description="Visual effects and particles",
            ),

            discord.SelectOption(
                label="SFX",
                value="sfx",
                emoji="🔊",
                description="Sound effects",
            ),

            discord.SelectOption(
                label="Transitions",
                value="transitions",
                emoji="🔄",
                description="Transitions and motion effects",
            ),

            discord.SelectOption(
                label="Overlays",
                value="overlays",
                emoji="🖼️",
                description="Overlays, VHS, film burns and textures",
            ),

            discord.SelectOption(
                label="Music",
                value="music",
                emoji="🎵",
                description="Music and audio",
            ),

            discord.SelectOption(
                label="Fonts",
                value="fonts",
                emoji="🔤",
                description="Fonts for editing",
            ),

            discord.SelectOption(
                label="Presets & Templates",
                value="presets",
                emoji="🎛️",
                description="Presets and templates",
            ),

            discord.SelectOption(
                label="LUTs / Coloring",
                value="luts",
                emoji="🎨",
                description="LUTs and color grading",
            ),

            discord.SelectOption(
                label="Stock Footage",
                value="footage",
                emoji="🎥",
                description="Stock footage and B-roll",
            ),
        ]

        super().__init__(
            placeholder=(
                "Choose a resource category..."
            ),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        category = RESOURCE_CATEGORIES[
            self.values[0]
        ]

        embed = discord.Embed(
            title=category["name"],
            description=category["description"],
            colour=discord.Colour.from_rgb(
                114,
                137,
                218,
            ),
        )

        embed.set_author(
            name="Edit Office • Resources"
        )

        for (
            name,
            description,
            url,
        ) in category["resources"]:

            embed.add_field(
                name=f"🔗 [{name}]({url})",
                value=description,
                inline=False,
            )

        embed.set_footer(
            text=(
                "Always check the license before "
                "using an asset commercially."
            )
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self.view,
        )


# ============================================================
# RESOURCE VIEW
# ============================================================

class ResourceView(
    discord.ui.View
):

    def __init__(self):

        super().__init__(
            timeout=300
        )

        self.add_item(
            ResourceSelect()
        )


# ============================================================
# BOT
# ============================================================

class Bot(discord.Client):

    def __init__(self):

        super().__init__(
            intents=discord.Intents.none()
        )

        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self):

        guild = discord.Object(
            id=1520498998757032008
        )

        self.tree.copy_global_to(guild=guild)

        await self.tree.sync(guild=guild)

        print("✓ Slash commands synced to server.")


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
        "✓ /scenepack is synced."
    )

    print(
        "✓ /resources is synced."
    )


# ============================================================
# /SCENEPACK
# ============================================================

@bot.tree.command(
    name="scenepack",
    description=(
        "Find EditPacks for a movie, show, anime, "
        "sport, streamer, game or character"
    ),
)
@discord.app_commands.describe(
    title=(
        "Movie, show, anime, sport, streamer, "
        "game or character"
    ),
)
async def scenepack(
    interaction: discord.Interaction,
    title: str,
):

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
    # Search
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
    # No results
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
# /RESOURCES
# ============================================================

@bot.tree.command(
    name="resources",
    description=(
        "Find VFX, SFX, transitions, overlays, "
        "fonts, presets and other editing resources"
    ),
)
async def resources(
    interaction: discord.Interaction,
):

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
    # Main resources embed
    # --------------------------------------------------------

    embed = discord.Embed(
        title="🎬 Editing Resources",
        description=(
            "A collection of useful resources "
            "for video editors.\n\n"
            "Choose a category below to browse "
            "VFX, SFX, transitions, overlays, "
            "fonts, presets and more."
        ),
        colour=discord.Colour.from_rgb(
            114,
            137,
            218,
        ),
    )

    embed.set_author(
        name="Edit Office • Resources"
    )

    embed.add_field(
        name="🎨 VFX",
        value=(
            "Effects, particles, smoke, "
            "energy & more"
        ),
        inline=True,
    )

    embed.add_field(
        name="🔊 SFX",
        value=(
            "Impacts, whooshes, "
            "risers & sounds"
        ),
        inline=True,
    )

    embed.add_field(
        name="🔄 Transitions",
        value=(
            "Zooms, glitches, "
            "slides & spins"
        ),
        inline=True,
    )

    embed.add_field(
        name="🖼️ Overlays",
        value=(
            "VHS, film burns, "
            "light leaks & textures"
        ),
        inline=True,
    )

    embed.add_field(
        name="🎛️ Presets",
        value=(
            "Presets, templates "
            "& project files"
        ),
        inline=True,
    )

    embed.add_field(
        name="🎨 Coloring",
        value=(
            "LUTs & color grading "
            "resources"
        ),
        inline=True,
    )

    embed.add_field(
        name="🔤 Fonts",
        value=(
            "Fonts for subtitles "
            "& typography"
        ),
        inline=True,
    )

    embed.add_field(
        name="🎵 Music",
        value=(
            "Music and audio "
            "for edits"
        ),
        inline=True,
    )

    embed.add_field(
        name="🎥 Footage",
        value=(
            "Stock footage "
            "& B-roll"
        ),
        inline=True,
    )

    embed.set_footer(
        text="Select a category below"
    )

    await interaction.response.send_message(
        embed=embed,
        view=ResourceView(),
    )


# ============================================================
# START BOT
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
