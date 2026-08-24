import { useMemo, useState, type ReactNode } from 'react';
import { App, Button, Drawer, List, Progress, Space, Tabs, Tag, Tooltip, Typography } from 'antd';
import { ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { downloadApi } from '../../services/api';
import { getErrorMessage } from '../../services/apiClient';
import { useDownloadsStore, type DownloadItem } from '../../stores/downloadsStore';
import { formatBytes, formatSpeed, splitRemainingTime } from './format';

export interface DownloadCenterDrawerProps {
  open: boolean;
  onClose: () => void;
}

type TabKey = 'all' | 'active' | 'completed' | 'failed';

const ACTIVE_STATUSES = ['pending', 'downloading'] as const;
const FAILED_STATUSES = ['failed', 'expired'] as const;

/**
 * Download-center Drawer (PRD §3.5, Task 15): the task list from
 * downloadsStore, grouped into tabs (全部/进行中/已完成/已失败), with status
 * rendering, WS/polling progress, and the completed-file handoff.
 *
 * Affordances (documented, only what the backend supports):
 * - completed → [获取文件] opens the tokenized `download_url` (one-time token,
 *   5-minute validity). If the link was missed (polling-only path) or its
 *   token expired, the drawer shows an honest hint + [刷新链接] which briefly
 *   reconnects the socket to capture a fresh `complete` event (the backend
 *   has NO HTTP endpoint that mints a link token).
 * - failed/expired → [重试] re-submits via downloadsStore.retry (the backend
 *   state graph allows failed/expired → pending as a fresh row).
 * - cancel → NOT supported by the v1 backend (no cancel endpoint, no
 *   cancelling transition in the state graph), so active items render a
 *   DISABLED stop control with an explanatory tooltip — never a live one.
 */
export function DownloadCenterDrawer({ open, onClose }: DownloadCenterDrawerProps): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const items = useDownloadsStore((state) => state.items);
  const retry = useDownloadsStore((state) => state.retry);
  const refreshFileLink = useDownloadsStore((state) => state.refreshFileLink);
  const [tab, setTab] = useState<TabKey>('all');
  const [retrying, setRetrying] = useState<string | null>(null);

  const counts = useMemo(
    () => ({
      active: items.filter((item) => (ACTIVE_STATUSES as readonly string[]).includes(item.status)).length,
      completed: items.filter((item) => item.status === 'completed').length,
      failed: items.filter((item) => (FAILED_STATUSES as readonly string[]).includes(item.status)).length,
    }),
    [items],
  );

  const visibleItems = useMemo(() => {
    const filtered = items.filter((item) => {
      switch (tab) {
        case 'active':
          return (ACTIVE_STATUSES as readonly string[]).includes(item.status);
        case 'completed':
          return item.status === 'completed';
        case 'failed':
          return (FAILED_STATUSES as readonly string[]).includes(item.status);
        default:
          return true;
      }
    });
    // Newest first (created_at is ISO-8601, so string compare works).
    return [...filtered].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [items, tab]);

  const isLinkExpired = (item: DownloadItem): boolean => {
    if (!item.token_expire_at) return false;
    return Date.parse(item.token_expire_at) <= Date.now();
  };

  const handleGetFile = (item: DownloadItem) => {
    if (!item.download_url) {
      void message.info(t('downloads.linkMissing'));
      return;
    }
    if (isLinkExpired(item)) {
      void message.warning(t('downloads.linkExpired'));
      return;
    }
    // Direct navigation to the tokenized file URL; the backend serves the
    // bytes with a Content-Disposition attachment header. One-time token:
    // a reused link fails server-side with 5003 — the refresh action covers it.
    window.open(downloadApi.getFileUrl(item.download_url), '_blank', 'noopener');
  };

  const handleRetry = async (item: DownloadItem) => {
    setRetrying(item.download_id);
    try {
      await retry(item.download_id);
      void message.success(t('downloads.retryStarted'));
    } catch (err) {
      void message.error(getErrorMessage(err));
    } finally {
      setRetrying(null);
    }
  };

  const statusMeta = (status: DownloadItem['status']): { label: string; color: string } => {
    switch (status) {
      case 'pending':
        return { label: t('downloads.status.pending'), color: 'default' };
      case 'downloading':
        return { label: t('downloads.status.downloading'), color: 'processing' };
      case 'completed':
        return { label: t('downloads.status.completed'), color: 'success' };
      case 'failed':
        return { label: t('downloads.status.failed'), color: 'error' };
      case 'expired':
        return { label: t('downloads.status.expired'), color: 'warning' };
    }
  };

  const renderRemaining = (seconds: number | null): string => {
    const split = splitRemainingTime(seconds);
    if (!split) return '—';
    return t(split.unitKey, { value: split.value });
  };

  const renderActions = (item: DownloadItem): ReactNode[] => {
    if (item.status === 'completed') {
      const linkReady = item.download_url != null && !isLinkExpired(item);
      if (linkReady) {
        return [
          <Button
            key="file"
            type="primary"
            size="small"
            onClick={() => handleGetFile(item)}
            data-testid={`get-file-${item.download_id}`}
          >
            {t('downloads.getFile')}
          </Button>,
        ];
      }
      return [
        <Button
          key="refresh"
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => refreshFileLink(item.download_id)}
          data-testid={`refresh-link-${item.download_id}`}
        >
          {t('downloads.refreshLink')}
        </Button>,
      ];
    }
    if (item.status === 'failed' || item.status === 'expired') {
      return [
        <Button
          key="retry"
          size="small"
          loading={retrying === item.download_id}
          onClick={() => void handleRetry(item)}
          data-testid={`retry-${item.download_id}`}
        >
          {t('downloads.retry')}
        </Button>,
      ];
    }
    // Active: no cancel in the v1 backend — render a disabled control that
    // explains itself instead of a dead affordance.
    return [
      <Tooltip key="cancel" title={t('downloads.cancelNotSupported')}>
        <Button
          size="small"
          disabled
          icon={<StopOutlined />}
          aria-label={t('downloads.cancelNotSupported')}
          data-testid={`cancel-${item.download_id}`}
        />
      </Tooltip>,
    ];
  };

  const renderDescription = (item: DownloadItem): React.ReactNode => {
    if (item.status === 'completed') {
      if (!item.download_url) return t('downloads.linkMissingHint');
      if (isLinkExpired(item)) return t('downloads.linkExpiredHint');
      return t('downloads.linkValidHint');
    }
    if (item.status === 'failed' || item.status === 'expired') {
      return item.error_message ?? '—';
    }
    const downloaded = formatBytes(item.downloaded_bytes);
    const total = formatBytes(item.total_bytes);
    return (
      <Space size={4} wrap data-testid={`progress-text-${item.download_id}`}>
        <Typography.Text type="secondary">
          {t('downloads.downloadedOf', { downloaded, total })}
        </Typography.Text>
        <Typography.Text type="secondary">· {formatSpeed(item.speed)}</Typography.Text>
        <Typography.Text type="secondary">· {renderRemaining(item.remaining_time)}</Typography.Text>
      </Space>
    );
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t('header.downloadCenter')}
      width={440}
      data-testid="download-center-drawer"
    >
      {items.length === 0 ? (
        <div className="downloads-empty" data-testid="downloads-empty">
          <Typography.Text type="secondary">{t('downloads.empty')}</Typography.Text>
        </div>
      ) : (
        <>
          <Tabs
            activeKey={tab}
            onChange={(key) => setTab(key as TabKey)}
            items={[
              { key: 'all', label: `${t('downloads.tab.all')} (${items.length})` },
              { key: 'active', label: `${t('downloads.tab.active')} (${counts.active})` },
              { key: 'completed', label: `${t('downloads.tab.completed')} (${counts.completed})` },
              { key: 'failed', label: `${t('downloads.tab.failed')} (${counts.failed})` },
            ]}
          />
          <List
            dataSource={visibleItems}
            locale={{ emptyText: null }}
            renderItem={(item) => {
              const meta = statusMeta(item.status);
              const active = item.status === 'pending' || item.status === 'downloading';
              return (
                <List.Item
                  key={item.download_id}
                  actions={renderActions(item)}
                  data-testid={`download-item-${item.download_id}`}
                >
                  <List.Item.Meta
                    title={
                      <Space size={6} wrap>
                        <Typography.Text ellipsis={{ tooltip: item.title ?? undefined }} data-testid={`download-title-${item.download_id}`}>
                          {item.title ?? t('downloads.unknownTitle')}
                        </Typography.Text>
                        <Tag color={meta.color} data-testid={`download-status-${item.download_id}`}>
                          {meta.label}
                        </Tag>
                      </Space>
                    }
                    description={renderDescription(item)}
                  />
                  {active && (
                    <div data-testid={`download-progress-${item.download_id}`}>
                      <Progress
                        percent={Math.round(item.progress * 100)}
                        size="small"
                        status={item.progress >= 1 ? 'success' : 'active'}
                      />
                    </div>
                  )}
                </List.Item>
              );
            }}
          />
        </>
      )}
    </Drawer>
  );
}
