import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Row,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { CloudUploadOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTranslation, type TranslationKey } from '../services/i18n';
import { healthApi, nasApi } from '../services/api';
import { getErrorMessage } from '../services/apiClient';
import { useDownloadsStore, type DownloadItem } from '../stores/downloadsStore';
import type { HealthData } from '../types/api';

/**
 * NAS admin entry screen (PRD §3.4, Task 16) — two panels:
 *
 * 1. **Storage status** — one /api/health fetch on mount renders the service
 *    row (api/storage) and the six Bubble+Pond storage roots with their
 *    ok/error statuses, plus loading / error / retry states.
 * 2. **Save to NAS** — the save action from the PRD §3.4.3 flow: the
 *    download-center's completed items (this session only — the v1 backend
 *    has no download-list endpoint, same documented constraint as the Task 15
 *    drawer) each get an admin-only [存入NAS] button that opens a modal asking
 *    for a target directory (leading "/" optional; see the empty-default note
 *    below) and calls POST /api/nas/save. Success shows "🎉 锦鲤已游入池塘!"
 *    with the returned `nas_path` and REMOVES the item from the store (the
 *    backend moved its bubble file into the pond, so the item's file link and
 *    a re-save would both fail); backend errors (3001/5001/5002/400) surface
 *    their bilingual messages.
 *
 * **Design choice (documented):** the save action lives on the NAS page, not
 * in the download-center Drawer. Rationale: the PRD flow starts from a
 * completed download, but the drawer is the download-management surface and
 * keeping it unchanged avoids touching Task 15's tests; /nas is the admin
 * surface for pond operations and has direct access to the same
 * downloadsStore items. The deferred NAS browser (browse/list/delete/rename)
 * is NOT built — the backend has no such endpoints.
 *
 * **Degraded storage (Task 16 review):** /api/health reports degraded storage
 * as HTTP 200 + code 1 + data.status "degraded"; `healthApi.getHealth`
 * tolerates that envelope (per-request `tolerateErrorEnvelope`), so this page
 * can render exactly which root failed instead of a generic error.
 *
 * The v1 "no download list endpoint" constraint means a page reload loses the
 * in-session items; the page says so honestly via the empty state.
 */

/** Human labels for the six v1 storage roots reported by /api/health. */
const STORAGE_ROOT_LABELS: Record<string, TranslationKey> = {
  video_storage_path: 'nas.storage.root.video_storage_path',
  image_storage_path: 'nas.storage.root.image_storage_path',
  music_storage_path: 'nas.storage.root.music_storage_path',
  temp_video_path: 'nas.storage.root.temp_video_path',
  temp_image_path: 'nas.storage.root.temp_image_path',
  temp_music_path: 'nas.storage.root.temp_music_path',
};

const DRIVE_LETTER = /^[A-Za-z]:/;

/**
 * Client-side target-path validation mirroring the backend's documented rules
 * (backend/app/application/nas_service.py `_parse_target_path`): blank → 400,
 * backslash separator or drive-letter prefix → 400, `.`/`..` segments → 400,
 * and no remaining directory segment after the leading "/" (a bare "/", which
 * the backend also rejects — see its test suite) → 400. The backend remains
 * authoritative (it slugifies every segment); this pre-check catches the
 * obvious classes with inline messages instead of a round-trip 400.
 *
 * @returns an i18n key describing the problem, or null when acceptable.
 */
export function validateNasTargetPath(value: string): TranslationKey | null {
  const text = value.trim();
  if (!text) return 'nas.save.targetEmpty';
  if (text.includes('\\') || DRIVE_LETTER.test(text)) return 'nas.save.targetInvalid';
  const segments = text.split('/').filter(Boolean);
  if (segments.length === 0) return 'nas.save.targetRootOnly';
  if (segments.some((segment) => segment === '.' || segment === '..')) return 'nas.save.targetInvalid';
  if (segments.some((segment) => DRIVE_LETTER.test(segment))) return 'nas.save.targetInvalid';
  return null;
}

/** Map a health status value to its translated label (ok/degraded/raw). */
function serviceStatusLabel(status: string, t: (key: TranslationKey) => string): string {
  if (status === 'ok') return t('nas.storage.ok');
  if (status === 'degraded') return t('nas.storage.degraded');
  return status;
}

/** Map a storage-root status value to its translated label (ok/error/raw). */
function rootStatusLabel(status: string, t: (key: TranslationKey) => string): string {
  if (status === 'ok') return t('nas.storage.ok');
  if (status === 'error') return t('nas.storage.errorLabel');
  return status;
}

export default function NasPage(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const items = useDownloadsStore((state) => state.items);
  const removeItem = useDownloadsStore((state) => state.remove);

  // Only completed downloads are saveable (backend: 5002 otherwise).
  const completedItems = useMemo(
    () => items.filter((item) => item.status === 'completed'),
    [items],
  );

  // --- storage status -------------------------------------------------------
  const [health, setHealth] = useState<HealthData | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthError, setHealthError] = useState(false);

  const loadHealth = useCallback(async () => {
    setHealthLoading(true);
    setHealthError(false);
    try {
      setHealth(await healthApi.getHealth());
    } catch {
      setHealthError(true);
    } finally {
      setHealthLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHealth();
  }, [loadHealth]);

  // --- save-to-NAS ----------------------------------------------------------
  const [saveTarget, setSaveTarget] = useState<DownloadItem | null>(null);
  // PRD §3.4.3 says the modal defaults to "/", but the backend rejects a
  // root-only target (nas_service._parse_target_path requires ≥1 segment) —
  // a genuine PRD-vs-backend conflict. The field therefore opens EMPTY with
  // an example placeholder; typing "/" alone still gets a clear message.
  const [targetPath, setTargetPath] = useState('');
  const [saving, setSaving] = useState(false);

  const openSaveModal = (item: DownloadItem) => {
    setTargetPath('');
    setSaveTarget(item);
  };

  const handleSave = async () => {
    if (!saveTarget || saving) return;
    const invalidKey = validateNasTargetPath(targetPath);
    if (invalidKey) {
      void message.error(t(invalidKey));
      return;
    }
    setSaving(true);
    try {
      const data = await nasApi.save(saveTarget.download_id, targetPath);
      setSaveTarget(null);
      // The backend MOVED the bubble file into the pond: the item is no
      // longer downloadable or re-saveable — remove it so neither the NAS
      // list nor the download-center drawer offers dead actions.
      removeItem(saveTarget.download_id);
      void message.success(t('nas.save.success'));
      void message.success(t('nas.save.successPath', { path: data.nas_path }));
    } catch (error) {
      void message.error(getErrorMessage(error, t('nas.save.failed')));
    } finally {
      setSaving(false);
    }
  };

  const roots = health ? Object.entries(health.storage_roots) : [];

  return (
    <div className="nas-page" data-testid="nas-page">
      <Typography.Title level={3}>{t('nas.title')}</Typography.Title>

      <Card title={t('nas.storage.title')} className="nas-storage-card" data-testid="storage-panel">
        {healthLoading && (
          <div data-testid="storage-loading">
            <Spin size="small" /> <span className="nas-storage-loading-text">{t('nas.storage.loading')}</span>
          </div>
        )}
        {healthError && !healthLoading && (
          <div data-testid="storage-error">
            <Alert
              type="error"
              showIcon
              message={t('nas.storage.error')}
              action={
                <Button
                  size="small"
                  data-testid="storage-retry"
                  icon={<ReloadOutlined />}
                  onClick={() => void loadHealth()}
                >
                  {t('nas.storage.retry')}
                </Button>
              }
            />
          </div>
        )}
        {health && !healthLoading && (
          <div data-testid="storage-content">
            {health.status === 'degraded' && (
              <Alert
                type="warning"
                showIcon
                message={t('nas.storage.degraded')}
                style={{ marginBottom: 12 }}
                data-testid="storage-degraded"
              />
            )}
            <Typography.Paragraph type="secondary">{t('nas.storage.hint')}</Typography.Paragraph>
            <Row gutter={[16, 8]}>
              <Col span={12} data-testid="storage-service-api">
                {t('nas.storage.serviceApi')}:{' '}
                <Tag color={health.services.api === 'ok' ? 'success' : 'warning'}>
                  {serviceStatusLabel(health.services.api ?? '', t)}
                </Tag>
              </Col>
              <Col span={12} data-testid="storage-service-storage">
                {t('nas.storage.serviceStorage')}:{' '}
                <Tag color={health.services.storage === 'ok' ? 'success' : 'warning'}>
                  {serviceStatusLabel(health.services.storage ?? '', t)}
                </Tag>
              </Col>
            </Row>
            <List
              size="small"
              data-testid="storage-roots"
              style={{ marginTop: 8 }}
              dataSource={roots}
              renderItem={([field, status]) => (
                <List.Item key={field} data-testid={`storage-root-${field}`}>
                  <span>{STORAGE_ROOT_LABELS[field] ? t(STORAGE_ROOT_LABELS[field]) : field}</span>
                  <Tag color={status === 'ok' ? 'success' : 'error'}>
                    {rootStatusLabel(status, t)}
                  </Tag>
                </List.Item>
              )}
            />
          </div>
        )}
      </Card>

      <Card title={t('nas.save.title')} className="nas-save-card" data-testid="save-panel" style={{ marginTop: 16 }}>
        <Typography.Paragraph type="secondary">{t('nas.save.hint')}</Typography.Paragraph>
        {completedItems.length === 0 ? (
          <Empty
            data-testid="nas-completed-empty"
            description={<span>{t('nas.save.completedEmpty')}</span>}
          >
            <Typography.Text type="secondary">{t('nas.save.completedEmptyHint')}</Typography.Text>
          </Empty>
        ) : (
          <List
            dataSource={completedItems}
            renderItem={(item) => (
              <List.Item
                key={item.download_id}
                data-testid={`nas-completed-${item.download_id}`}
                actions={[
                  <Button
                    key="save"
                    type="primary"
                    size="small"
                    icon={<CloudUploadOutlined />}
                    data-testid={`nas-save-${item.download_id}`}
                    onClick={() => openSaveModal(item)}
                  >
                    {t('nas.save.toNas')}
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={item.title ?? t('downloads.unknownTitle')}
                  description={item.download_id}
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <Modal
        open={saveTarget !== null}
        title={t('nas.save.modalTitle')}
        onOk={() => void handleSave()}
        onCancel={() => setSaveTarget(null)}
        okText={t('nas.save.confirm')}
        cancelText={t('nas.save.cancel')}
        confirmLoading={saving}
        destroyOnHidden
        okButtonProps={{ 'data-testid': 'nas-save-confirm' }}
      >
        <div data-testid="nas-save-modal">
          <Form layout="vertical">
            <Form.Item label={t('nas.save.targetLabel')}>
              <Input
                data-testid="nas-target-input"
                value={targetPath}
                onChange={(event) => setTargetPath(event.target.value)}
                onPressEnter={() => void handleSave()}
                placeholder={t('nas.save.targetPlaceholder')}
                // Backend cap (NasSaveRequest.target_path max_length=1024).
                maxLength={1024}
              />
              <Typography.Text type="secondary">{t('nas.save.targetHint')}</Typography.Text>
            </Form.Item>
          </Form>
        </div>
      </Modal>
    </div>
  );
}
