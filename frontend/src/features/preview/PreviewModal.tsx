import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Descriptions, Divider, Image, Modal, Select, Space, Spin, Table, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { previewApi } from '../../services/api';
import { getErrorMessage } from '../../services/apiClient';
import { useDownloadsStore } from '../../stores/downloadsStore';
import { usePreviewStore } from '../parser/previewStore';
import type { PreviewData } from '../../types/api';

type LoadStatus = 'loading' | 'success' | 'error';

/**
 * v1 single-media preview Modal (PRD §4.4, Task 15).
 *
 * Seam: the parser workspace records the clicked parse result in previewStore
 * (`activeTask`); this modal renders from it. On open it fetches the full
 * metadata via `previewApi.getPreview(task_id)` and renders per `preview_type`:
 * - `image` → the cover image (real media) + metadata;
 * - `video` / `music` → an honest metadata panel: the v1 backend returns
 *   METADATA + a streams ladder only (no byte streams yet), so there is
 *   deliberately NO fake player — just the stream information table.
 *
 * Actions: [下载] submits through downloadsStore (the same path as the result
 * cards, so the download-center badge/drawer see it); closing calls
 * `previewStore.closePreview()`.
 */
export function PreviewModal(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const activeTask = usePreviewStore((state) => state.activeTask);
  const closePreview = usePreviewStore((state) => state.closePreview);
  const submitDownload = useDownloadsStore((state) => state.submit);
  const submitting = useDownloadsStore((state) => state.submitting);

  const taskId = activeTask?.task_id ?? null;

  const [data, setData] = useState<PreviewData | null>(null);
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('loading');
  const [error, setError] = useState('');
  const [quality, setQuality] = useState<string | null>(null);
  const [bitrate, setBitrate] = useState<string | null>(null);

  // Monotonic request token: a slow response for a PREVIOUSLY opened task
  // must never overwrite the modal with stale metadata (open t1 → slow
  // request A; close; open t2 → fast request B; A resolving after B would
  // otherwise render t1's data under t2's title).
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (!taskId) return;
    const seq = ++requestSeq.current;
    setLoadStatus('loading');
    setError('');
    try {
      const preview = await previewApi.getPreview(taskId);
      if (seq !== requestSeq.current) return; // a newer request superseded us
      setData(preview);
      setQuality(preview.available_qualities[0] ?? null);
      setBitrate(preview.available_bitrates[0] ?? null);
      setLoadStatus('success');
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setError(getErrorMessage(err));
      setLoadStatus('error');
    }
  }, [taskId]);

  // Refetch whenever a (possibly different) task is opened. The previous
  // content is cleared so stale data never flashes under the new spinner.
  useEffect(() => {
    if (taskId) {
      // Invalidate any in-flight request for the previous task.
      requestSeq.current += 1;
      setData(null);
      void load();
    }
  }, [taskId, load]);

  const handleDownload = async () => {
    if (!taskId) return;
    const chosen = data
      ? data.available_qualities.length > 0
        ? quality
        : data.available_bitrates.length > 0
          ? bitrate
          : null
      : null;
    try {
      await submitDownload(taskId, {
        format: data?.format ?? activeTask?.format ?? null,
        quality: chosen,
        title: data?.title ?? activeTask?.title,
      });
      void message.success(t('parser.downloadStarted'));
    } catch (err) {
      void message.error(getErrorMessage(err));
    }
  };

  const hasQuality = Boolean(data && data.available_qualities.length > 0);
  const hasBitrate = Boolean(data && data.available_bitrates.length > 0);

  return (
    <Modal
      open={activeTask !== null}
      onCancel={closePreview}
      title={activeTask?.title ?? t('preview.title')}
      width={640}
      footer={null}
      destroyOnHidden
      data-testid="preview-modal"
    >
      {loadStatus === 'loading' && (
        <div className="preview-loading" data-testid="preview-loading">
          <Spin size="large" />
        </div>
      )}

      {loadStatus === 'error' && (
        <Alert
          type="error"
          showIcon
          message={error}
          data-testid="preview-error"
          action={
            <Button size="small" onClick={() => void load()} data-testid="preview-retry">
              {t('parser.retry')}
            </Button>
          }
        />
      )}

      {loadStatus === 'success' && data && (
        <div data-testid="preview-content">
          {data.preview_type === 'image' && data.cover && (
            <div className="preview-cover" data-testid="preview-cover">
              <Image src={data.cover} alt={data.title ?? data.platform} />
            </div>
          )}

          {(data.preview_type === 'video' || data.preview_type === 'music') && (
            <Alert
              type="info"
              showIcon
              message={t('preview.metadataOnly')}
              style={{ marginBottom: 16 }}
              data-testid="preview-metadata-note"
            />
          )}

          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label={t('preview.platform')}>{data.platform}</Descriptions.Item>
            <Descriptions.Item label={t('preview.url')}>
              <Typography.Text copyable ellipsis={{ tooltip: data.url }}>
                {data.url}
              </Typography.Text>
            </Descriptions.Item>
            {data.duration && <Descriptions.Item label={t('preview.duration')}>{data.duration}</Descriptions.Item>}
            {data.format && <Descriptions.Item label={t('preview.format')}>{data.format}</Descriptions.Item>}
            {data.file_size_mb != null && (
              <Descriptions.Item label={t('preview.fileSize')}>{data.file_size_mb} MB</Descriptions.Item>
            )}
          </Descriptions>

          <Divider orientation="left" plain>
            {t('preview.streams')}
          </Divider>
          {data.streams.length > 0 ? (
            <Table
              size="small"
              rowKey="key"
              dataSource={data.streams.map((stream, index) => ({ ...stream, key: String(index) }))}
              pagination={false}
              data-testid="preview-streams"
              columns={
                data.preview_type === 'video'
                  ? [
                      { title: t('preview.quality'), dataIndex: 'quality', render: (value?: string | null) => value ?? '—' },
                      { title: t('preview.format'), dataIndex: 'format', render: (value?: string | null) => value ?? '—' },
                    ]
                  : [
                      { title: t('preview.bitrate'), dataIndex: 'bitrate', render: (value?: string | null) => value ?? '—' },
                      { title: t('preview.format'), dataIndex: 'format', render: (value?: string | null) => value ?? '—' },
                    ]
              }
            />
          ) : (
            <Typography.Text type="secondary" data-testid="preview-no-streams">
              {t('preview.noStreams')}
            </Typography.Text>
          )}

          <Divider />

          <Space wrap>
            {hasQuality && (
              <Select
                size="small"
                value={quality}
                onChange={setQuality}
                options={data.available_qualities.map((q) => ({ value: q, label: q }))}
                aria-label={t('preview.quality')}
                data-testid="preview-quality-select"
              />
            )}
            {hasBitrate && (
              <Select
                size="small"
                value={bitrate}
                onChange={setBitrate}
                options={data.available_bitrates.map((b) => ({ value: b, label: b }))}
                aria-label={t('preview.bitrate')}
                data-testid="preview-bitrate-select"
              />
            )}
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              loading={taskId != null && Boolean(submitting[taskId])}
              onClick={() => void handleDownload()}
              data-testid="preview-download"
            >
              {t('parser.download')}
            </Button>
          </Space>
        </div>
      )}
    </Modal>
  );
}
