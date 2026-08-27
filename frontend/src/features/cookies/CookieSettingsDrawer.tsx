import { useEffect, useState } from 'react';
import { Alert, App, Button, Drawer, Input, Space, Tag, Typography } from 'antd';
import { useTranslation } from '../../services/i18n';
import {
  COOKIE_PLATFORMS,
  isConfigured,
  updatedAtOf,
  useCookieStore,
} from './cookieStore';

/**
 * Admin settings drawer for the f2 parser's per-platform cookies.
 *
 * The backend persists each cookie and never echoes it back, so the drawer
 * only shows a configured/unconfigured Tag + the last-updated time, and the
 * inputs always start empty (save = overwrite; the blank guard disables Save
 * until something is typed). douyin/tiktok parsing requires a cookie; weibo
 * needs one only for permission-gated posts. Per-platform `pending` drives
 * each row's Save/Clear spinner (the store's `loading` covers the initial
 * load only — binding buttons to it would spin all rows during the wrong
 * phase).
 */
export function CookieSettingsDrawer(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const drawerOpen = useCookieStore((state) => state.drawerOpen);
  const error = useCookieStore((state) => state.error);
  const entries = useCookieStore((state) => state.entries);
  const closeDrawer = useCookieStore((state) => state.closeDrawer);
  const load = useCookieStore((state) => state.load);
  const save = useCookieStore((state) => state.save);
  const remove = useCookieStore((state) => state.remove);
  // Local input values (never pre-filled from the server) + the platform
  // whose save/clear is currently in flight.
  const [values, setValues] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<string | null>(null);

  // Fresh inputs each time the drawer opens — cookies are never echoed back.
  useEffect(() => {
    if (drawerOpen) setValues({});
  }, [drawerOpen]);

  const setValue = (platform: string, value: string) => {
    setValues((prev) => ({ ...prev, [platform]: value }));
  };

  const handleSave = async (platform: string, cookie: string) => {
    if (!cookie.trim()) {
      void message.error(t('cookies.savedFailed'));
      return;
    }
    setPending(platform);
    try {
      await save(platform, cookie);
      setValue(platform, '');
      void message.success(t('cookies.saved'));
    } catch {
      void message.error(t('cookies.savedFailed'));
    } finally {
      setPending(null);
    }
  };

  const handleClear = async (platform: string) => {
    setPending(platform);
    try {
      await remove(platform);
      setValue(platform, '');
      void message.success(t('cookies.cleared'));
    } catch {
      void message.error(t('cookies.clearedFailed'));
    } finally {
      setPending(null);
    }
  };

  return (
    <Drawer
      title={t('cookies.title')}
      open={drawerOpen}
      onClose={closeDrawer}
      width={420}
      data-testid="cookie-settings-drawer"
    >
      <Typography.Paragraph type="secondary" data-testid="cookie-hint">
        {t('cookies.hint')}
      </Typography.Paragraph>

      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button size="small" onClick={() => void load()} data-testid="cookie-retry">
              {t('parser.retry')}
            </Button>
          }
          data-testid="cookie-load-error"
        />
      )}

      {COOKIE_PLATFORMS.map(({ platform, labelKey }) => {
        const configured = isConfigured(entries, platform);
        const updatedAt = updatedAtOf(entries, platform);
        const cookie = values[platform] ?? '';
        return (
          <div key={platform} className="cookie-row" data-testid={`cookie-row-${platform}`}>
            <div className="cookie-row-head">
              <Typography.Text strong>{t(labelKey)}</Typography.Text>
              {configured ? (
                <Tag color="green" data-testid={`cookie-tag-${platform}`}>
                  {t('cookies.configured')}
                </Tag>
              ) : (
                <Tag data-testid={`cookie-tag-${platform}`}>{t('cookies.notConfigured')}</Tag>
              )}
            </div>
            <Input.Password
              placeholder={t('cookies.placeholder')}
              autoComplete="new-password"
              value={cookie}
              onChange={(e) => setValue(platform, e.target.value)}
              aria-label={t(labelKey)}
              data-testid={`cookie-input-${platform}`}
            />
            <Space>
              <Button
                size="small"
                type="primary"
                loading={pending === platform}
                disabled={!cookie.trim()}
                onClick={() => void handleSave(platform, cookie)}
                data-testid={`cookie-save-${platform}`}
              >
                {t('cookies.save')}
              </Button>
              <Button
                size="small"
                danger
                disabled={!configured}
                onClick={() => void handleClear(platform)}
                data-testid={`cookie-clear-${platform}`}
              >
                {t('cookies.clear')}
              </Button>
            </Space>
            {updatedAt && (
              <Typography.Text
                type="secondary"
                className="cookie-updated"
                data-testid={`cookie-updated-${platform}`}
              >
                {updatedAt}
              </Typography.Text>
            )}
          </div>
        );
      })}
    </Drawer>
  );
}
