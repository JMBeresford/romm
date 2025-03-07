/* generated using openapi-typescript-codegen -- do no edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */

import type { IGDBPlatform } from './IGDBPlatform';
import type { IGDBRelatedGame } from './IGDBRelatedGame';

export type IGDBMetadata = {
    total_rating: string;
    aggregated_rating: string;
    first_release_date: (number | null);
    genres: Array<string>;
    franchises: Array<string>;
    alternative_names: Array<string>;
    collections: Array<string>;
    companies: Array<string>;
    game_modes: Array<string>;
    platforms: Array<IGDBPlatform>;
    expansions: Array<IGDBRelatedGame>;
    dlcs: Array<IGDBRelatedGame>;
    remasters: Array<IGDBRelatedGame>;
    remakes: Array<IGDBRelatedGame>;
    expanded_games: Array<IGDBRelatedGame>;
    ports: Array<IGDBRelatedGame>;
    similar_games: Array<IGDBRelatedGame>;
};

