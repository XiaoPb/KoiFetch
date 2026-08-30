import { useMemo, useState } from 'react';
import { Button, Card, Image, Select, Space, Tag, Typography } from 'antd';
import { AudioOutlined, DownloadOutlined, EyeOutlined, PictureOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { downloadApi, mediaApi } from '../../services/api';
import { useDownloadsStore } from '../../stores/downloadsStore';
import type { ParseResult } from '../../types/api';
import { VideoPlayer, type PlayableSource } from './VideoPlayer';
import { ImageCarousel, COVER_FALLBACK } from './ImageCarousel';
import { LivePhotoViewer } from './LivePhotoViewer';

/** Small translucent badge on the cover corner identifying the media type. */
const TYPE_BADGE: Record<string, JSX.Element> = {
  video: <VideoCameraOutlined />,
  music: <AudioOutlined />,
  image: <PictureOutlined />,
  live_photo: <PictureOutlined />,
};

/** Options passed to the download action (format + the single quality slot). */
export interface DownloadOptions {
  format: string | null;
  quality: string | null;
}

export interface ResultCardProps {
  result: ParseResult;
  /** True while this task's download is being submitted (button spinner). */
  downloading: boolean;
  /** Music only: opens the metadata preview modal. */
  onPreview: (result: ParseResult) => void;
  onDownload: (result: ParseResult, options: DownloadOptions) => void;
  /** Image only: download the currently displayed album image. */
  onDownloadImage: (result: ParseResult, index: number) => void;
  /** Image only: download the whole album as a ZIP. */
  onDownloadAlbum: (result: ParseResult) => void;
}

/**
 * One result-card grid item (PRD §4.2.2): media is visible in place — a video
 * plays inline via xgplayer as soon as the engine resolved a playable URL
 * (same-origin proxy → direct CDN → completed download file), and an image
 * album renders as a Swiper carousel with [下载当前]/[下载全部]. Music keeps the
 * cover + [预览]/[下载]. Video cards keep ONLY [下载] (the old [预览] modal is
 * gone for video/image).
 */
export function ResultCard({
  result,
  downloading,
  onPreview,
  onDownload,
  onDownloadImage,
  onDownloadAlbum,
}: ResultCardProps): JSX.Element {
  const { t } = useTranslation();
  const [quality, setQuality] = useState<string | null>(result.available_qualities[0] ?? null);
  const [bitrate, setBitrate] = useState<string | null>(result.available_bitrates[0] ?? null);
  const [activeImage, setActiveImage] = useState(0);

  // A completed download's still-valid file link for this task — the LAST
  // playback fallback for videos (the auto-download keeps making it appear).
  const completedUrl = useDownloadsStore((state) => {
    const item = state.items.find(
      (i) => i.task_id === result.task_id && i.status === 'completed' && i.download_url != null,
    );
    if (!item) return null;
    if (item.token_expire_at && Date.parse(item.token_expire_at) <= Date.now()) return null;
    return item.download_url;
  });

  const hasQuality = result.available_qualities.length > 0;
  const hasBitrate = result.available_bitrates.length > 0;
  const sizeText = result.file_size_mb != null ? `${result.file_size_mb} MB` : '—';

  // Ordered playback candidates: stream proxy first (same-origin, robust),
  // then the engine's direct CDN URL, then the completed local file.
  const playableSources: PlayableSource[] = useMemo(() => {
    if (result.type !== 'video') return [];
    const sources: PlayableSource[] = [];
    if (result.video_url) {
      sources.push({ url: mediaApi.streamUrl(result.task_id), format: result.format });
      sources.push({ url: result.video_url, format: result.format });
    }
    if (completedUrl) {
      sources.push({ url: downloadApi.getFileUrl(completedUrl), format: result.format });
    }
    return sources;
  }, [result, completedUrl]);

  const showPlayer = result.type === 'video' && playableSources.length > 0;

  // Album slide URLs: the engine's list, else the single cover.
  const albumImages: string[] = useMemo(() => {
    if (result.type !== 'image') return [];
    if (result.images && result.images.length > 0) return result.images;
    return result.cover ? [result.cover] : [];
  }, [result]);

  const handleDownload = () => {
    // Music uses the bitrate picker, video the quality picker; both map to the
    // backend's single `quality` slot. The parsed format is passed through.
    const chosen = hasQuality ? quality : hasBitrate ? bitrate : null;
    onDownload(result, { format: result.format ?? null, quality: chosen });
  };

  const livePhotoManifest =
    result.type === 'live_photo' && result.manifest?.kind === 'live_photo' ? result.manifest : null;

  const cover = showPlayer ? (
    <VideoPlayer
      sources={playableSources}
      poster={result.cover}
      testId={`card-player-${result.task_id}`}
    />
  ) : livePhotoManifest ? (
    <LivePhotoViewer
      pairs={livePhotoManifest.live_photos}
      title={result.title}
      testId={`live-photo-${result.task_id}`}
    />
  ) : result.type === 'image' && albumImages.length > 0 ? (
    <ImageCarousel
      images={albumImages}
      title={result.title}
      onIndexChange={setActiveImage}
      testId={`carousel-${result.task_id}`}
    />
  ) : result.cover ? (
    <Image src={result.cover} alt={result.title} preview={false} fallback={COVER_FALLBACK} />
  ) : (
    <div className="result-card-cover-empty" aria-label={result.title} />
  );

  const actions = (
    result.type === 'video'
      ? [
          <Button
            key="download"
            type="text"
            icon={<DownloadOutlined />}
            loading={downloading}
            onClick={handleDownload}
            data-testid={`download-${result.task_id}`}
          >
            {t('parser.download')}
          </Button>,
        ]
      : result.type === 'image'
        ? [
            <Button
              key="download-current"
              type="text"
              icon={<DownloadOutlined />}
              onClick={() => onDownloadImage(result, activeImage)}
              data-testid={`download-current-${result.task_id}`}
            >
              {t('parser.downloadCurrent')}
            </Button>,
            <Button
              key="download-all"
              type="text"
              icon={<DownloadOutlined />}
              onClick={() => onDownloadAlbum(result)}
              data-testid={`download-all-${result.task_id}`}
            >
              {t('parser.downloadAll')}
            </Button>,
          ]
        : [
            <Button
              key="preview"
              type="text"
              icon={<EyeOutlined />}
              onClick={() => onPreview(result)}
              data-testid={`preview-${result.task_id}`}
            >
              {t('parser.preview')}
            </Button>,
            <Button
              key="download"
              type="text"
              icon={<DownloadOutlined />}
              loading={downloading}
              onClick={handleDownload}
              data-testid={`download-${result.task_id}`}
            >
              {t('parser.download')}
            </Button>,
          ]
  );

  return (
    <Card
      hoverable
      className="result-card"
      data-testid={`result-card-${result.task_id}`}
      cover={
        <div className="result-card-cover">
          {cover}
          <div
            className="result-card-type"
            data-testid={`type-badge-${result.task_id}`}
            aria-hidden="true"
          >
            {TYPE_BADGE[result.type] ?? null}
          </div>
          {!showPlayer && result.duration && (
            <Tag className="result-card-duration" data-testid={`duration-${result.task_id}`}>
              {result.duration}
            </Tag>
          )}
        </div>
      }
      actions={actions}
    >
      <Card.Meta
        title={
          <Typography.Text ellipsis={{ tooltip: result.title }} data-testid={`title-${result.task_id}`}>
            {result.title}
          </Typography.Text>
        }
        description={
          <Space size={4} wrap>
            <Tag color="blue" data-testid={`platform-${result.task_id}`}>
              {result.platform}
            </Tag>
            {result.format && <Tag>{result.format}</Tag>}
            <span className="result-card-size">{sizeText}</span>
            {hasQuality && (
              <Select
                size="small"
                value={quality}
                onChange={setQuality}
                options={result.available_qualities.map((q) => ({ value: q, label: q }))}
                aria-label={t('parser.quality')}
                data-testid={`quality-select-${result.task_id}`}
              />
            )}
            {hasBitrate && (
              <Select
                size="small"
                value={bitrate}
                onChange={setBitrate}
                options={result.available_bitrates.map((b) => ({ value: b, label: b }))}
                aria-label={t('parser.bitrate')}
                data-testid={`bitrate-select-${result.task_id}`}
              />
            )}
          </Space>
        }
      />
    </Card>
  );
}
