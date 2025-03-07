import functools
import json
import os
import re
import sys
import time
from typing import Final, Optional

import pydash
import requests
import xmltodict
from config import IGDB_CLIENT_ID, IGDB_CLIENT_SECRET
from fastapi import HTTPException, status
from handler.redis_handler import cache
from logger.logger import log
from requests.exceptions import HTTPError, Timeout
from tasks.update_mame_xml import update_mame_xml_task
from tasks.update_switch_titledb import update_switch_titledb_task
from typing_extensions import TypedDict
from unidecode import unidecode as uc

MAIN_GAME_CATEGORY: Final = 0
EXPANDED_GAME_CATEGORY: Final = 10
N_SCREENSHOTS: Final = 5
PS2_IGDB_ID: Final = 8
SWITCH_IGDB_ID: Final = 130
ARCADE_IGDB_IDS: Final = [52, 79, 80]

PS2_OPL_REGEX: Final = r"^([A-Z]{4}_\d{3}\.\d{2})\..*$"
PS2_OPL_INDEX_FILE: Final = os.path.join(
    os.path.dirname(__file__), "fixtures", "ps2_opl_index.json"
)

SWITCH_TITLEDB_REGEX: Final = r"(70[0-9]{12})"
SWITCH_TITLEDB_INDEX_FILE: Final = os.path.join(
    os.path.dirname(__file__), "fixtures", "switch_titledb.json"
)

SWITCH_PRODUCT_ID_REGEX: Final = r"(0100[0-9A-F]{12})"
SWITCH_PRODUCT_ID_FILE: Final = os.path.join(
    os.path.dirname(__file__), "fixtures", "switch_product_ids.json"
)

MAME_XML_FILE: Final = os.path.join(os.path.dirname(__file__), "fixtures", "mame.xml")


class IGDBPlatform(TypedDict):
    igdb_id: int
    name: str


class IGDBRelatedGame(TypedDict):
    id: int
    name: str
    slug: str
    type: str
    cover_url: str


class IGDBMetadata(TypedDict):
    total_rating: str
    aggregated_rating: str
    first_release_date: int | None
    genres: list[str]
    franchises: list[str]
    alternative_names: list[str]
    collections: list[str]
    companies: list[str]
    game_modes: list[str]
    platforms: list[IGDBPlatform]
    expansions: list[IGDBRelatedGame]
    dlcs: list[IGDBRelatedGame]
    remasters: list[IGDBRelatedGame]
    remakes: list[IGDBRelatedGame]
    expanded_games: list[IGDBRelatedGame]
    ports: list[IGDBRelatedGame]
    similar_games: list[IGDBRelatedGame]


class IGDBRom(TypedDict):
    igdb_id: Optional[int]
    name: Optional[str]
    slug: Optional[str]
    summary: Optional[str]
    url_cover: str
    url_screenshots: list[str]
    igdb_metadata: Optional[IGDBMetadata]


def extract_metadata_from_igdb_rom(rom: dict) -> IGDBMetadata:
    return IGDBMetadata(
        {
            "total_rating": str(round(rom.get("total_rating", 0.0), 2)),
            "aggregated_rating": str(round(rom.get("aggregated_rating", 0.0), 2)),
            "first_release_date": rom.get("first_release_date", None),
            "genres": pydash.map_(rom.get("genres", []), "name"),
            "franchises": pydash.compact(
                [rom.get("franchise.name", None)]
                + pydash.map_(rom.get("franchises", []), "name")
            ),
            "alternative_names": pydash.map_(rom.get("alternative_names", []), "name"),
            "collections": pydash.map_(rom.get("collections", []), "name"),
            "game_modes": pydash.map_(rom.get("game_modes", []), "name"),
            "companies": pydash.map_(rom.get("involved_companies", []), "company.name"),
            "platforms": [
                {"igdb_id": p.get("id", ""), "name": p.get("name", "")}
                for p in rom.get("platforms", [])
            ],
            "expansions": [
                {"cover_url": pydash.get(e, "cover.url", ""), "type": "expansion", **e}
                for e in rom.get("expansions", [])
            ],
            "dlcs": [
                {"cover_url": pydash.get(d, "cover.url", ""), "type": "dlc", **d}
                for d in rom.get("dlcs", [])
            ],
            "remasters": [
                {"cover_url": pydash.get(r, "cover.url", ""), "type": "remaster", **r}
                for r in rom.get("remasters", [])
            ],
            "remakes": [
                {"cover_url": pydash.get(r, "cover.url", ""), "type": "remake", **r}
                for r in rom.get("remakes", [])
            ],
            "expanded_games": [
                {"cover_url": pydash.get(g, "cover.url", ""), "type": "expanded", **g}
                for g in rom.get("expanded_games", [])
            ],
            "ports": [
                {"cover_url": pydash.get(p, "cover.url", ""), "type": "port", **p}
                for p in rom.get("ports", [])
            ],
            "similar_games": [
                {"cover_url": pydash.get(s, "cover.url", ""), "type": "similar", **s}
                for s in rom.get("similar_games", [])
            ],
        }
    )


class IGDBHandler:
    def __init__(self) -> None:
        self.platform_endpoint = "https://api.igdb.com/v4/platforms"
        self.platform_version_endpoint = "https://api.igdb.com/v4/platform_versions"
        self.platforms_fields = ["id", "name"]
        self.games_endpoint = "https://api.igdb.com/v4/games"
        self.games_fields = GAMES_FIELDS
        self.search_endpoint = "https://api.igdb.com/v4/search"
        self.search_fields = ["game.id", "name"]
        self.pagination_limit = 200
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
            args[0].headers["Authorization"] = (
                f"Bearer {args[0].twitch_auth.get_oauth_token()}"
            )
            return func(*args)

        return wrapper


    def _request(self, url: str, data: str, timeout: int = 120) -> list:
        try:
            res = requests.post(
                url,
                f"{data} limit {self.pagination_limit};",
                headers=self.headers,
                timeout=timeout,
            )

            res.raise_for_status()
            return res.json()
        except requests.exceptions.ConnectionError:
            log.critical("Connection error: can't connect to IGDB", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Can't connect to IGDB, check your internet connection",
            )
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
            res = requests.post(
                url,
                f"{data} limit {self.pagination_limit};",
                headers=self.headers,
                timeout=timeout,
            )
            res.raise_for_status()
        except (HTTPError, Timeout) as err:
            # Log the error and return an empty list if the request fails again
            log.error(err)
            return []

        return res.json()

    @staticmethod
    def _normalize_search_term(search_term: str) -> str:
        return (
            search_term.replace("\u2122", "")  # Remove trademark symbol
            .replace("\u00ae", "")  # Remove registered symbol
            .replace("\u00a9", "")  # Remove copywrite symbol
            .replace("\u2120", "")  # Remove service mark symbol
            .strip()  # Remove leading and trailing spaces
        )

    @staticmethod
    def _normalize_cover_url(url: str) -> str:
        return f"https:{url.replace('https:', '')}" if url != "" else ""

    def _search_rom(
        self, search_term: str, platform_idgb_id: int, category: int = 0
    ) -> dict:
        search_term = uc(search_term)
        category_filter: str = f"& category={category}" if category else ""
        roms = self._request(
            self.games_endpoint,
            data=f'search "{search_term}"; fields {",".join(self.games_fields)}; where platforms=[{platform_idgb_id}] {category_filter};',
        )

        if not roms:
            roms = self._request(
                self.search_endpoint,
                data=f'fields {",".join(self.search_fields)}; where game.platforms=[{platform_idgb_id}] & (name ~ *"{search_term}"* | alternative_name ~ *"{search_term}"*);',
            )
            if roms:
                roms = self._request(
                    self.games_endpoint,
                    f'fields {",".join(self.games_fields)}; where id={roms[0]["game"]["id"]};',
                )

        exact_matches = [
            rom
            for rom in roms
            if rom["name"].lower() == search_term.lower()
            or rom["slug"].lower() == search_term.lower()
        ]

        return pydash.get(exact_matches or roms, "[0]", {})

    async def _ps2_opl_format(self, match: re.Match[str], search_term: str) -> str:
        serial_code = match.group(1)

        with open(PS2_OPL_INDEX_FILE, "r") as index_json:
            opl_index = json.loads(index_json.read())
            index_entry = opl_index.get(serial_code, None)
            if index_entry:
                search_term = index_entry["Name"]  # type: ignore

        return search_term

    async def _switch_titledb_format(
        self, match: re.Match[str], search_term: str
    ) -> str:
        titledb_index = {}
        title_id = match.group(1)

        try:
            with open(SWITCH_TITLEDB_INDEX_FILE, "r") as index_json:
                titledb_index = json.loads(index_json.read())
        except FileNotFoundError:
            log.warning("Fetching the Switch titleDB index file...")
            await update_switch_titledb_task.run(force=True)
            try:
                with open(SWITCH_TITLEDB_INDEX_FILE, "r") as index_json:
                    titledb_index = json.loads(index_json.read())
            except FileNotFoundError:
                log.error("Could not fetch the Switch titleDB index file")
        finally:
            index_entry = titledb_index.get(title_id, None)
            if index_entry:
                search_term = index_entry["name"]  # type: ignore

        return search_term

    async def _switch_productid_format(
        self, match: re.Match[str], search_term: str
    ) -> str:
        product_id_index = {}
        product_id = match.group(1)

        # Game updates have the same product ID as the main application, except with bitmask 0x800 set
        product_id = list(product_id)
        product_id[-3] = "0"
        product_id = "".join(product_id)

        try:
            with open(SWITCH_PRODUCT_ID_FILE, "r") as index_json:
                product_id_index = json.loads(index_json.read())
        except FileNotFoundError:
            log.warning("Fetching the Switch titleDB index file...")
            await update_switch_titledb_task.run(force=True)
            try:
                with open(SWITCH_PRODUCT_ID_FILE, "r") as index_json:
                    product_id_index = json.loads(index_json.read())
            except FileNotFoundError:
                log.error("Could not fetch the Switch titleDB index file")
        finally:
            index_entry = product_id_index.get(product_id, None)
            if index_entry:
                search_term = index_entry["name"]  # type: ignore
        return search_term

    async def _mame_format(self, search_term: str) -> str:
        from handler import fs_rom_handler

        mame_index = {"menu": {"game": []}}

        try:
            with open(MAME_XML_FILE, "r") as index_xml:
                mame_index = xmltodict.parse(index_xml.read())
        except FileNotFoundError:
            log.warning("Fetching the MAME XML file from Github...")
            await update_mame_xml_task.run(force=True)
            try:
                with open(MAME_XML_FILE, "r") as index_xml:
                    mame_index = xmltodict.parse(index_xml.read())
            except FileNotFoundError:
                log.error("Could not fetch the MAME XML file from Github")
        finally:
            index_entry = [
                game
                for game in mame_index["menu"]["game"]
                if game["@name"] == search_term
            ]
            if index_entry:
                search_term = fs_rom_handler.get_file_name_with_no_tags(
                    index_entry[0].get("description", search_term)
                )

        return search_term

    @check_twitch_token
    def get_platform(self, slug: str) -> IGDBPlatform:
        platforms = self._request(
            self.platform_endpoint,
            data=f'fields {",".join(self.platforms_fields)}; where slug="{slug.lower()}";',
        )

        platform = pydash.get(platforms, "[0]", None)

        # Check if platform is a version if not found
        if not platform:
            platform_versions = self._request(
                self.platform_version_endpoint,
                data=f'fields {",".join(self.platforms_fields)}; where slug="{slug.lower()}";',
            )
            version = pydash.get(platform_versions, "[0]", None)
            if not version:
                return IGDBPlatform(igdb_id=None, name=slug.replace("-", " ").title())

            return IGDBPlatform(
                igdb_id=version.get("id", None),
                name=version.get("name", slug),
            )

        return IGDBPlatform(
            igdb_id=platform.get("id", None),
            name=platform.get("name", slug),
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

        search_term = self._normalize_search_term(search_term)

        rom = (
            self._search_rom(search_term, platform_idgb_id, MAIN_GAME_CATEGORY)
            or self._search_rom(search_term, platform_idgb_id, EXPANDED_GAME_CATEGORY)
            or self._search_rom(search_term, platform_idgb_id)
        )

        return IGDBRom(
            igdb_id=rom.get("id", None),
            name=rom.get("name", search_term),
            slug=rom.get("slug", ""),
            summary=rom.get("summary", ""),
            url_cover=self._normalize_cover_url(rom.get("cover", {}).get("url", "")),
            url_screenshots=[
                self._normalize_cover_url(s.get("url", "")).replace(
                    "t_thumb", "t_original"
                )
                for s in rom.get("screenshots", [])
            ],
            igdb_metadata=extract_metadata_from_igdb_rom(rom),
        )

    @check_twitch_token
    def get_rom_by_id(self, igdb_id: int) -> IGDBRom:
        roms = self._request(
            self.games_endpoint,
            f'fields {",".join(self.games_fields)}; where id={igdb_id};',
        )
        rom = pydash.get(roms, "[0]", {})

        return IGDBRom(
            igdb_id=igdb_id,
            name=rom.get("name", ""),
            slug=rom.get("slug", ""),
            summary=rom.get("summary", ""),
            url_cover=self._normalize_cover_url(rom.get("cover", {}).get("url", "")),
            url_screenshots=[
                self._normalize_cover_url(s.get("url", "")).replace(
                    "t_thumb", "t_original"
                )
                for s in rom.get("screenshots", [])
            ],
            igdb_metadata=extract_metadata_from_igdb_rom(rom),
        )

    @check_twitch_token
    def get_matched_roms_by_id(self, igdb_id: int) -> list[IGDBRom]:
        matched_rom = self.get_rom_by_id(igdb_id)
        matched_rom.update(
            url_cover=matched_rom.get("url_cover", "").replace(
                "t_thumb", "t_cover_big"
            ),
        )
        return [matched_rom]

    @check_twitch_token
    def get_matched_roms_by_name(
        self, search_term: str, platform_idgb_id: int, search_extended: bool = False
    ) -> list[IGDBRom]:
        if not platform_idgb_id:
            return []

        search_term = uc(search_term)
        matched_roms = self._request(
            self.games_endpoint,
            data=f'search "{search_term}"; fields {",".join(self.games_fields)}; where platforms=[{platform_idgb_id}];',
        )

        if not matched_roms or search_extended:
            log.info("Extended searching...")
            alternative_matched_roms = self._request(
                self.search_endpoint,
                data=f'fields {",".join(self.search_fields)}; where game.platforms=[{platform_idgb_id}] & (name ~ *"{search_term}"* | alternative_name ~ *"{search_term}"*);',
            )

            if alternative_matched_roms:
                alternative_roms_ids = []
                for rom in alternative_matched_roms:
                    alternative_roms_ids.append(
                        rom.get("game").get("id", "")
                        if "game" in rom.keys()
                        else rom.get("id", "")
                    )
                id_filter = " | ".join(
                    list(
                        map(
                            lambda rom: (
                                f'id={rom.get("game").get("id", "")}'
                                if "game" in rom.keys()
                                else f'id={rom.get("id", "")}'
                            ),
                            alternative_matched_roms,
                        )
                    )
                )
                alternative_matched_roms = self._request(
                    self.games_endpoint,
                    f'fields {",".join(self.games_fields)}; where {id_filter};',
                )
                matched_roms.extend(alternative_matched_roms)

        # Use a dictionary to keep track of unique ids
        unique_ids = {}

        # Use a list comprehension to filter duplicates based on the 'id' key
        matched_roms = [
            unique_ids.setdefault(rom["id"], rom)
            for rom in matched_roms
            if rom["id"] not in unique_ids
        ]

        return [
            IGDBRom(
                igdb_id=rom.get("id"),
                name=rom.get("name", search_term),
                slug=rom.get("slug", ""),
                summary=rom.get("summary", ""),
                url_cover=self._normalize_cover_url(
                    rom.get("cover", {})
                    .get("url", "")
                    .replace("t_thumb", "t_cover_big")
                ),
                url_screenshots=[
                    self._normalize_cover_url(s.get("url", "")).replace(
                        "t_thumb", "t_original"
                    )
                    for s in rom.get("screenshots", [])
                ],
                igdb_metadata=extract_metadata_from_igdb_rom(rom),
            )
            for rom in matched_roms
        ]


class TwitchAuth:
    def _update_twitch_token(self) -> str:
        token = ""
        expires_in = 0
        try:
            res = requests.post(
                url="https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": IGDB_CLIENT_ID,
                    "client_secret": IGDB_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
                timeout=10,
            )

            if res.status_code == 400:
                log.critical("IGDB Error: Invalid IGDB_CLIENT_ID or IGDB_CLIENT_SECRET")
                return token
            else:
                token = res.json().get("access_token", "")
                expires_in = res.json().get("expires_in", 0)
        except requests.exceptions.ConnectionError:
            log.critical("Can't connect to IGDB, check your internet connection.")
            return token

        if not token or expires_in == 0:
            return token

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


GAMES_FIELDS = [
    "id",
    "name",
    "slug",
    "summary",
    "total_rating",
    "aggregated_rating",
    "first_release_date",
    "artworks.url",
    "cover.url",
    "screenshots.url",
    "platforms.id",
    "platforms.name",
    "alternative_names.name",
    "genres.name",
    "franchise.name",
    "franchises.name",
    "collections.name",
    "game_modes.name",
    "involved_companies.company.name",
    "expansions.id",
    "expansions.slug",
    "expansions.name",
    "expansions.cover.url",
    "expanded_games.id",
    "expanded_games.slug",
    "expanded_games.name",
    "expanded_games.cover.url",
    "dlcs.id",
    "dlcs.name",
    "dlcs.slug",
    "dlcs.cover.url",
    "remakes.id",
    "remakes.slug",
    "remakes.name",
    "remakes.cover.url",
    "remasters.id",
    "remasters.slug",
    "remasters.name",
    "remasters.cover.url",
    "ports.id",
    "ports.slug",
    "ports.name",
    "ports.cover.url",
    "similar_games.id",
    "similar_games.slug",
    "similar_games.name",
    "similar_games.cover.url",
]
