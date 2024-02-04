import functools
import re
import sys
import time
from typing import Final

import pydash
import requests
from config import DEFAULT_URL_COVER_L, IGDB_CLIENT_ID, IGDB_CLIENT_SECRET
from handler.redis_handler import cache
from logger.logger import log
from requests.exceptions import HTTPError, Timeout
from unidecode import unidecode as uc

from . import (
    MetadataPlatform,
    MetadataRom,
    MetadataHandler,
    PS2_OPL_REGEX,
    SWITCH_TITLEDB_REGEX,
    SWITCH_PRODUCT_ID_REGEX,
)

MAIN_GAME_CATEGORY: Final = 0
EXPANDED_GAME_CATEGORY: Final = 10
N_SCREENSHOTS: Final = 5
PS2_IGDB_ID: Final = 8
SWITCH_IGDB_ID: Final = 130
ARCADE_IGDB_IDS: Final = [52, 79, 80]


class IGDBRom(MetadataRom):
    igdb_id: int


class IGDBPlatform(MetadataPlatform):
    igdb_id: int


class IGDBHandler(MetadataHandler):
    def __init__(self) -> None:
        self.platform_url = "https://api.igdb.com/v4/platforms/"
        self.platform_version_url = "https://api.igdb.com/v4/platform_versions/"
        self.games_url = "https://api.igdb.com/v4/games/"
        self.covers_url = "https://api.igdb.com/v4/covers/"
        self.screenshots_url = "https://api.igdb.com/v4/screenshots/"
        self.twitch_auth = TwitchAuth()
        self.headers = {
            "Client-ID": IGDB_CLIENT_ID,
            "Authorization": f"Bearer {self.twitch_auth.get_oauth_token()}",
            "Accept": "application/json",
        }

    @staticmethod
    def check_twitch_token(func):
        @functools.wraps(func)
        def wrapper(*args):
            args[0].headers[
                "Authorization"
            ] = f"Bearer {args[0].twitch_auth.get_oauth_token()}"
            return func(*args)

        return wrapper

    def _request(self, url: str, data: str, timeout: int = 120) -> list:
        try:
            res = requests.post(url, data, headers=self.headers, timeout=timeout)
            res.raise_for_status()
            return res.json()
        except HTTPError as err:
            # Retry once if the auth token is invalid
            if err.response.status_code != 401:
                log.error(err)
                return []  # All requests to the IGDB API return a list

            # Attempt to force a token refresh if the token is invalid
            log.warning("Twitch token invalid: fetching a new one...")
            token = self.twitch_auth._update_twitch_token()
            self.headers["Authorization"] = f"Bearer {token}"
        except Timeout:
            # Retry once the request if it times out
            pass

        try:
            res = requests.post(url, data, headers=self.headers, timeout=timeout)
            res.raise_for_status()
        except (HTTPError, Timeout) as err:
            # Log the error and return an empty list if the request fails again
            log.error(err)
            return []

        return res.json()

    def _search_rom(
        self, search_term: str, platform_idgb_id: int, category: int = 0
    ) -> dict:
        category_filter: str = f"& category={category}" if category else ""
        roms = self._request(
            self.games_url,
            data=f"""
                search "{search_term}";
                fields id, slug, name, summary, screenshots;
                where platforms=[{platform_idgb_id}] {category_filter};
            """,
        )

        exact_matches = [
            rom
            for rom in roms
            if rom["name"].lower() == search_term.lower()
            or rom["slug"].lower() == search_term.lower()
        ]

        return pydash.get(exact_matches or roms, "[0]", {})

    def _search_cover(self, rom_id: int) -> str:
        covers = self._request(
            self.covers_url,
            data=f"fields url; where game={rom_id};",
        )

        cover = pydash.get(covers, "[0]", None)
        return (
            DEFAULT_URL_COVER_L
            if not cover
            else self._normalize_cover_url(cover["url"])
        )

    def _search_screenshots(self, rom_id: int) -> list:
        screenshots = self._request(
            self.screenshots_url,
            data=f"fields url; where game={rom_id}; limit {N_SCREENSHOTS};",
        )

        return [
            self._normalize_cover_url(r["url"]).replace("t_thumb", "t_original")
            for r in screenshots
            if "url" in r.keys()
        ]

    @check_twitch_token
    def get_platform(self, slug: str) -> IGDBPlatform:
        platforms = self._request(
            self.platform_url,
            data=f'fields id, name; where slug="{slug.lower()}";',
        )

        platform = pydash.get(platforms, "[0]", None)

        if platform:
            return IGDBPlatform(
                igdb_id=platform["id"],
                slug=slug,
                name=platform["name"],
            )

        # Check if platform is a version if not found
        platform_versions = self._request(
            self.platform_version_url,
            data=f'fields id, name; where slug="{slug.lower()}";',
        )
        version = pydash.get(platform_versions, "[0]", None)
        if version:
            return IGDBPlatform(
                igdb_id=version["id"],
                slug=slug,
                name=version["name"],
            )

        return IGDBPlatform(
            igdb_id=None,
            slug=slug,
            name=slug.replace("-", " ").title(),
        )

    @check_twitch_token
    async def get_rom(self, file_name: str, platform_idgb_id: int) -> IGDBRom:
        from handler import fs_rom_handler

        search_term = fs_rom_handler.get_file_name_with_no_tags(file_name)

        # Support for PS2 OPL filename format
        match = re.match(PS2_OPL_REGEX, file_name)
        if platform_idgb_id == PS2_IGDB_ID and match:
            search_term = await self._ps2_opl_format(match, search_term)

        # Support for switch titleID filename format
        match = re.search(SWITCH_TITLEDB_REGEX, file_name)
        if platform_idgb_id == SWITCH_IGDB_ID and match:
            search_term = await self._switch_titledb_format(match, search_term)

        # Support for switch productID filename format
        match = re.search(SWITCH_PRODUCT_ID_REGEX, file_name)
        if platform_idgb_id == SWITCH_IGDB_ID and match:
            search_term = await self._switch_productid_format(match, search_term)

        # Support for MAME arcade filename format
        if platform_idgb_id in ARCADE_IGDB_IDS:
            search_term = await self._mame_format(search_term)

        search_term = self.normalize_search_term(search_term)

        res = (
            self._search_rom(uc(search_term), platform_idgb_id, MAIN_GAME_CATEGORY)
            or self._search_rom(
                uc(search_term), platform_idgb_id, EXPANDED_GAME_CATEGORY
            )
            or self._search_rom(uc(search_term), platform_idgb_id)
        )

        igdb_id = res.get("id", None)
        rom = IGDBRom(
            igdb_id=igdb_id,
            slug=res.get("slug", ""),
            name=res.get("name", search_term),
            summary=res.get("summary", ""),
            url_cover=DEFAULT_URL_COVER_L,
            url_screenshots=[],
        )

        if igdb_id:
            rom["url_cover"] = self._search_cover(igdb_id)
            rom["url_screenshots"] = self._search_screenshots(igdb_id)

        return rom

    @check_twitch_token
    def get_rom_by_id(self, igdb_id: int) -> IGDBRom:
        roms = self._request(
            self.games_url,
            f"fields slug, name, summary; where id={igdb_id};",
        )
        rom = pydash.get(roms, "[0]", {})

        return IGDBRom(
            igdb_id=igdb_id,
            slug=rom.get("slug", ""),
            name=rom.get("name", ""),
            summary=rom.get("summary", ""),
            url_cover=self._search_cover(igdb_id),
            url_screenshots=self._search_screenshots(igdb_id),
        )

    @check_twitch_token
    def get_matched_roms_by_id(self, igdb_id: int) -> list[IGDBRom]:
        matched_rom = self.get_rom_by_id(igdb_id)
        matched_rom.update(
            url_cover=matched_rom["url_cover"].replace("t_thumb", "t_cover_big"),
        )
        return [matched_rom]

    @check_twitch_token
    def get_matched_roms_by_name(
        self, search_term: str, platform_idgb_id: int
    ) -> list[IGDBRom]:
        if not platform_idgb_id:
            return []

        matched_roms = self._request(
            self.games_url,
            data=f"""
                search "{uc(search_term)}";
                fields id, slug, name, summary;
                where platforms=[{platform_idgb_id}];
            """,
        )

        return [
            IGDBRom(
                igdb_id=rom["id"],
                slug=rom["slug"],
                name=rom["name"],
                summary=rom.get("summary", ""),
                url_cover=self._search_cover(rom["id"]).replace(
                    "t_thumb", "t_cover_big"
                ),
                url_screenshots=self._search_screenshots(rom["id"]),
            )
            for rom in matched_roms
        ]


class TwitchAuth:
    def _update_twitch_token(self) -> str:
        res = requests.post(
            url="https://id.twitch.tv/oauth2/token",
            params={
                "client_id": IGDB_CLIENT_ID,
                "client_secret": IGDB_CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
            timeout=30,
        ).json()

        token = res.get("access_token", "")
        expires_in = res.get("expires_in", 0)
        if not token or expires_in == 0:
            log.error(
                "Could not get twitch auth token: check client_id and client_secret"
            )
            sys.exit(2)

        # Set token in redis to expire in <expires_in> seconds
        cache.set("romm:twitch_token", token, ex=expires_in - 10)  # type: ignore[attr-defined]
        cache.set("romm:twitch_token_expires_at", time.time() + expires_in - 10)  # type: ignore[attr-defined]

        log.info("Twitch token fetched!")

        return token

    def get_oauth_token(self) -> str:
        # Use a fake token when running tests
        if "pytest" in sys.modules:
            return "test_token"

        # Fetch the token cache
        token = cache.get("romm:twitch_token")  # type: ignore[attr-defined]
        token_expires_at = cache.get("romm:twitch_token_expires_at")  # type: ignore[attr-defined]

        if not token or time.time() > float(token_expires_at or 0):
            log.warning("Twitch token invalid: fetching a new one...")
            return self._update_twitch_token()

        return token


UNIQUE_TO_IGDB = [
    "psvr2",
    "meta-quest-3",
    "atari2600",
    "mac",
    "switch",
    "evercade",
    "android",
    "playdate",
    "oculus-quest",
    "win",
    "meta-quest-2",
    "ps5",
    "series-x",
    "xboxone",
    "leaptv",
    "oculus-rift",
    "gear-vr",
    "new-nintendo-3ds",
    "psvr",
    "arduboy",
    "3ds",
    "ps4--1",
    "winphone",
    "oculus-go",
    "psvita",
    "wii",
    "ouya",
    "wiiu",
    "ps3",
    "psp",
    "leapster-explorer-slash-leadpad-explorer",
    "nintendo-dsi",
    "nds",
    "xbox360",
    "ps2",
    "zeebo",
    "arcade",
    "windows-mobile",
    "ios",
    "mobile",
    "hyperscan",
    "blu-ray-player",
    "gba",
    "vsmile",
    "ngage",
    "zod",
    "leapster",
    "n64",
    "wonderswan-color",
    "wonderswan",
    "xbox",
    "pokemon-mini",
    "ngc",
    "nuon",
    "ps",
    "visual-memory-unit-slash-visual-memory-system",
    "pocketstation",
    "gbc",
    "nintendo-64dd",
    "dc",
    "neo-geo-pocket-color",
    "dvd-player",
    "blackberry",
    "genesis-slash-megadrive",
    "snes",
    "gb",
    "neo-geo-pocket",
    "game-dot-com",
    "sfam",
    "satellaview",
    "hyper-neo-geo-64",
    "palm-os",
    "saturn",
    "sega-pico",
    "atari-jaguar-cd",
    "casio-loopy",
    "sega32",
    "virtualboy",
    "neo-geo-cd",
    "sms",
    "playdia",
    "3do",
    "pc-fx",
    "amiga-cd32",
    "mega-duck-slash-cougar-boy",
    "nes",
    "segacd",
    "jaguar",
    "famicom",
    "watara-slash-quickshot-supervision",
    "gamegear",
    "philips-cd-i",
    "amiga",
    "neogeoaes",
    "turbografx-16-slash-pc-engine-cd",
    "linux",
    "lynx",
    "neogeomvs",
    "commodore-cdtv",
    "bbcmicro",
    "gamate",
    "turbografx16--1",
    "fm-towns",
    "apple-iigs",
    "pc-9800-series",
    "supergrafx",
    "acorn-archimedes",
    "x1",
    "sharp-x68000",
    "c64",
    "fds",
    "amstrad-pcw",
    "acpc",
    "acorn-electron",
    "dragon-32-slash-64",
    "tatung-einstein",
    "atari-st",
    "epoch-super-cassette-vision",
    "sinclair-ql",
    "c16",
    "atari5200",
    "atari7800",
    "hp3000",
    "thomson-mo5",
    "msx2",
    "msx",
    "sharp-mz-2200",
    "vectrex",
    "c-plus-4",
    "sg1000",
    "nec-pc-6000-series",
    "zxs",
    "intellivision",
    "fm-7",
    "vic-20",
    "colecovision",
    "trs-80",
    "ti-99",
    "sinclair-zx81",
    "pc-8800-series",
    "epoch-cassette-vision",
    "dos",
    "atari8bit",
    "game-and-watch",
    "trs-80-color-computer",
    "microvision--1",
    "odyssey-2-slash-videopac-g7000",
    "1292-advanced-programmable-video-system",
    "exidy-sorcerer",
    "pc-50x-family",
    "appleii",
    "vc-4000",
    "ay-3-8500",
    "cpet",
    "astrocade",
    "ay-3-8760",
    "ay-3-8710",
    "ay-3-8603",
    "fairchild-channel-f",
    "ay-3-8610",
    "ay-3-8607",
    "ay-3-8605",
    "ay-3-8606",
    "sol-20",
    "odyssey--1",
    "plato--1",
    "cdccyber70",
    "pdp10",
    "hp2100",
    "sdssigma7",
    "pdp11",
    "call-a-computer",
    "pdp-8--1",
    "pdp1",
    "nintendo-playstation",
    "donner30",
    "edsac--1",
    "nimrod",
    "intellivision-amico",
    "legacy-computer",
    "handheld-electronic-lcd",
    "swancrystal",
    "browser",
    "ooparts",
    "stadia",
    "plug-and-play",
    "amazon-fire-tv",
    "onlive-game-system",
    "vc",
    "airconsole",
]
