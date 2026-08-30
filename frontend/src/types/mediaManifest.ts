/** Public, same-origin media resources returned by the parse/preview APIs. */

export interface PublicVideoManifest {
  kind: 'video';
  videos: Array<{
    url: string;
    format: string;
    quality: string | null;
  }>;
}

export interface PublicImageAlbumManifest {
  kind: 'image_album';
  images: Array<{
    url: string;
    format: string;
  }>;
}

export interface PublicLivePhotoManifest {
  kind: 'live_photo';
  live_photos: Array<{
    image_url: string;
    motion_url: string | null;
  }>;
  warnings: string[];
}

export type PublicMediaManifest =
  | PublicVideoManifest
  | PublicImageAlbumManifest
  | PublicLivePhotoManifest;
