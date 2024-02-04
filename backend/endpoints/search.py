import emoji
from decorators.auth import protected_route
from endpoints.responses.search import SearchRomSchema
from fastapi import APIRouter, Request
from handler import db_rom_handler, igdb_handler, moby_handler
from logger.logger import log
from config import DEFAULT_URL_COVER_L

router = APIRouter()


@protected_route(router.get, "/search/roms", ["roms.read"])
async def search_rom(
    request: Request,
    rom_id: str,
    search_term: str = None,
    search_by: str = "name",
) -> list[SearchRomSchema]:
    """Search rom into IGDB database

    Args:
        request (Request): Fastapi Request object
        rom_id (str): Rom internal id
        query (str, optional): Query to search the rom (IGDB name or IGDB id). Defaults to None.
        field (str, optional): field with which to search for the rom (name | id). Defaults to "Name".

    Returns:
        RomSearchResponse: List of objects with all the matched roms
    """

    rom = db_rom_handler.get_roms(rom_id)
    if not rom:
        return []

    search_term = search_term or rom.file_name_no_tags

    log.info(emoji.emojize(":magnifying_glass_tilted_right: IGDB Searching"))
    matched_roms: list = []

    log.info(f"Searching by {search_by.lower()}: {search_term}")
    log.info(emoji.emojize(f":video_game: {rom.platform_slug}: {rom.file_name}"))

    if search_by.lower() == "id":
        igdb_matched_roms = igdb_handler.get_matched_roms_by_id(int(search_term))
        moby_matched_roms = moby_handler.get_matched_roms_by_id(int(search_term))
    elif search_by.lower() == "name":
        igdb_matched_roms = igdb_handler.get_matched_roms_by_name(
            search_term, rom.platform.igdb_id
        )
        moby_matched_roms = moby_handler.get_matched_roms_by_name(
            search_term, rom.platform.moby_id
        )

    merged_dict = {item["name"]: item for item in igdb_matched_roms}
    for item in moby_matched_roms:
        merged_dict[item["name"]] = {**item, **merged_dict.get(item["name"], {})}

    matched_roms = [
        {
            **{
                "slug": "",
                "name": "",
                "summary": "",
                "url_cover": DEFAULT_URL_COVER_L,
                "url_screenshots": [],
            },
            **item,
        }
        for item in list(merged_dict.values())
    ]

    log.info("Results:")
    for m_rom in matched_roms:
        log.info(f"\t - {m_rom['name']}")

    return matched_roms
