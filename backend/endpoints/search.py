import emoji
from decorators.auth import protected_route
from endpoints.responses.search import SearchRomSchema
from fastapi import APIRouter, Request
from handler import db_rom_handler
from handler.scan_handler import SOURCE_TO_HANDLER
from logger.logger import log

router = APIRouter()

@protected_route(router.get, "/search/roms", ["roms.read"])
async def search_rom(
    request: Request,
    rom_id: str,
    source: str,
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
    search_term = search_term or rom.file_name_no_tags

    log.info(emoji.emojize(":magnifying_glass_tilted_right: IGDB Searching"))
    matched_roms: list = []

    log.info(f"Searching by {search_by.lower()}: {search_term}")
    log.info(emoji.emojize(f":video_game: {rom.platform_slug}: {rom.file_name}"))
    if search_by.lower() == "id":
        matched_roms = SOURCE_TO_HANDLER[source].get_matched_roms_by_id(
            int(search_term)
        )
    elif search_by.lower() == "name":
        matched_roms = SOURCE_TO_HANDLER[source].get_matched_roms_by_name(
            search_term, rom.platform.igdb_id
        )

    log.info("Results:")
    results = []
    for m_rom in matched_roms:
        log.info(f"\t - {m_rom['name']}")
        results.append(m_rom)

    return results
