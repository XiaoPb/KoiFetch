import { musicApi } from '../../services/api';
import type { MusicSearchSource } from './musicSource';

/**
 * HTTP-backed `MusicSearchSource` (P0): a thin passthrough over the backend's
 * `/api/music/search` endpoint, whose response is already the frontend wire
 * shape (`MusicSearchResult`) — the backend domain models mirror
 * `frontend/src/types/music.ts` field-for-field, so there is no mapping layer
 * to drift. Play URLs arrive as same-origin proxy paths
 * (`/api/music/{song_id}/stream`) and play directly in the mini player.
 */
export function createHttpMusicSource(): MusicSearchSource {
  return {
    async search(params) {
      return musicApi.search(params);
    },
  };
}

/** The production source — swap target in `musicStore.ts`. */
export const httpMusicSource = createHttpMusicSource();
