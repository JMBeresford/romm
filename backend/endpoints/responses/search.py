from handler.metadata_handler import MetadataRom
from typing import Optional

class SearchRomSchema(MetadataRom):
    igdb_id: Optional[int]
    moby_id: Optional[int]
