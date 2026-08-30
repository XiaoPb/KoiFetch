import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Descriptions, Divider, Image, Modal, Select, Space, Spin, Table, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import ReactPlayer from 'react-player';
import { useTranslation } from '../../services/i18n';
import { downloadApi, previewApi } from '../../services/api';
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
 * - `video` → once the task's download completed (auto-downloaded after parse,
 *   or re-attached via `GET /api/download/by-task` after a page reload), a
 *   real react-player inline player; before that, an honest
 *   "play after download" hint;
 * - `music` → the metadata panel (v1 music URLs are unsupported by the engine).
 *
 * Actions: [下载] downloads to the device — it opens the tokenized file URL
 * directly when a completed download with a valid link exists, otherwise it
 * submits a server-side download first (the same path as the result cards);
 * closing calls `previewStore.closePreview()`.
 */
export function PreviewModal(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const activeTask = usePreviewStore((state) => state.activeTask);
  const closePreview = usePreviewStore((state) => state.closePreview);
  const submitDownload = useDownloadsStore((state) => state.submit);
  const submitting = useDownloadsStore((state) => state.submitting);

  const taskId = activeTask?.task_id ?? null;

  // A completed download's tokenized file URL for this task (null until a
  // download completes AND its 5-minute link is still valid). Session-local:
  // like the download drawer, the store has no server-side list, so after an
  // F5 the link is unknown until a new download completes here.
  const completedUrl = useDownloadsStore((state) => {
    if (!taskId) return null;
    const item = state.items.find(
      (i) => i.task_id === taskId && i.status === 'completed' && i.download_url != null,
    );
    if (!item) return null;
    if (item.token_expire_at && Date.parse(item.token_expire_at) <= Date.now()) return null;
    return item.download_url;
  });

  const [data, setData] = useState<PreviewData | null>(null);
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('loading');
  const [error, setError] = useState('');
  const [quality, setQuality] = useState<string | null>(null);
  const [bitrate, setBitrate] = useState<string | null>(null);

  // Recovery: after a page reload the session-local download list is empty,
  // so a completed download is invisible to the preview. Ask the backend for
  // the task's NEWEST download; if it already completed, re-attach it to the
  // store and refresh its file link (the WS mints a fresh short-lived reusable
  // token → `complete` event → the player appears). In-session flows (auto-download
  // after parse) never need this: the store already has the item.
  useEffect(() => {
    if (!taskId || data?.preview_type !== 'video' || completedUrl) return;
    let cancelled = false;
    void (async () => {
      try {
        const snap = await downloadApi.getLatestByTask(taskId);
        if (cancelled) return;
        if (snap.status === 'completed') {
          useDownloadsStore.getState().upsertSnapshot(snap, {
            taskId: snap.task_id,
            title: data?.title ?? activeTask?.title,
          });
          useDownloadsStore.getState().refreshFileLink(snap.download_id);
        }
      } catch {
        // 3001 (no download for this task yet) or a transient error: the
        // "play after download" hint stays; the auto-download path covers it.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId, data?.preview_type, data?.title, completedUrl, activeTask?.title]);

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
    // 下载 = 前端下载到本地: when the file is already downloaded server-side
    // with a valid link, open it directly (the browser downloads it); the
    // auto-download after parse usually makes this the instant path.
    if (completedUrl) {
      window.open(downloadApi.getFileUrl(completedUrl), '_blank', 'noopener');
      return;
    }
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

          {data.preview_type === 'video' && completedUrl ? (
            <div style={{ marginBottom: 16 }} data-testid="preview-video">
              <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                {t('preview.playing')}
              </Typography.Text>
              <div style={{ aspectRatio: '16 / 9', maxHeight: 420 }} data-testid="preview-video-player">
                <ReactPlayer src={completedUrl ?? undefined} controls width="100%" height="100%" />
              </div>
            </div>
          ) : (data.preview_type === 'video' || data.preview_type === 'music') ? (
            <Alert
              type="info"
              showIcon
              message={data.preview_type === 'video' ? t('preview.videoHint') : t('preview.metadataOnly')}
              style={{ marginBottom: 16 }}
              data-testid="preview-metadata-note"
            />
          ) : null}

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
