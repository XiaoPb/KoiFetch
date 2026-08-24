import { useState } from 'react';
import { Button, Card, Image, Select, Space, Tag, Typography } from 'antd';
import { AudioOutlined, DownloadOutlined, EyeOutlined, PictureOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import type { ParseResult } from '../../types/api';

// Tiny inline SVG placeholder shown when a cover fails to load or is absent.
const COVER_FALLBACK =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="170">' +
      '<rect width="100%" height="100%" fill="#f0f0f0"/>' +
      '<text x="50%" y="50%" fill="#bfbfbf" font-size="14" text-anchor="middle" dominant-baseline="middle">Koi Fetch</text>' +
      '</svg>',
  );

/** Small translucent badge on the cover corner identifying the media type. */
const TYPE_BADGE: Record<string, JSX.Element> = {
  video: <VideoCameraOutlined />,
  music: <AudioOutlined />,
  image: <PictureOutlined />,
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
  onPreview: (result: ParseResult) => void;
  onDownload: (result: ParseResult, options: DownloadOptions) => void;
}

/**
 * One result-card grid item (PRD §4.2.2): cover with duration badge, title,
 * platform/format tags, file size, a quality or bitrate picker when the
 * backend offered one, and [预览] / [下载] actions. Single card level only.
 *
 * The quality/bitrate pickers are card-local state; switching the header
 * media mode unmounts non-matching cards, which resets their selection to the
 * first option. Accepted trade-off: the mode filter is presentation-layer
 * only, and a re-parse is not needed to see the card again.
 */
export function ResultCard({ result, downloading, onPreview, onDownload }: ResultCardProps): JSX.Element {
  const { t } = useTranslation();
  const [quality, setQuality] = useState<string | null>(result.available_qualities[0] ?? null);
  const [bitrate, setBitrate] = useState<string | null>(result.available_bitrates[0] ?? null);

  const hasQuality = result.available_qualities.length > 0;
  const hasBitrate = result.available_bitrates.length > 0;
  const sizeText = result.file_size_mb != null ? `${result.file_size_mb} MB` : '—';

  const handleDownload = () => {
    // Music uses the bitrate picker, video the quality picker; both map to the
    // backend's single `quality` slot. The parsed format is passed through.
    const chosen = hasQuality ? quality : hasBitrate ? bitrate : null;
    onDownload(result, { format: result.format ?? null, quality: chosen });
  };

  return (
    <Card
      hoverable
      className="result-card"
      data-testid={`result-card-${result.task_id}`}
      cover={
        <div className="result-card-cover">
          {result.cover ? (
            <Image src={result.cover} alt={result.title} preview={false} fallback={COVER_FALLBACK} />
          ) : (
            <div className="result-card-cover-empty" aria-label={result.title} />
          )}
          <div
            className="result-card-type"
            data-testid={`type-badge-${result.task_id}`}
            aria-label={result.type}
          >
            {TYPE_BADGE[result.type] ?? null}
          </div>
          {result.duration && (
            <Tag className="result-card-duration" data-testid={`duration-${result.task_id}`}>
              {result.duration}
            </Tag>
          )}
        </div>
      }
      actions={[
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
      ]}
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
