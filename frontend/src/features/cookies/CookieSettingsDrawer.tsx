import { useEffect } from 'react';
import { Alert, App, Button, Drawer, Form, Input, Space, Tag, Typography } from 'antd';
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
 * inputs always start empty (save = overwrite). douyin/tiktok parsing
 * requires a cookie; weibo needs one only for permission-gated posts.
 */
export function CookieSettingsDrawer(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const drawerOpen = useCookieStore((state) => state.drawerOpen);
  const loading = useCookieStore((state) => state.loading);
  const error = useCookieStore((state) => state.error);
  const entries = useCookieStore((state) => state.entries);
  const closeDrawer = useCookieStore((state) => state.closeDrawer);
  const load = useCookieStore((state) => state.load);
  const save = useCookieStore((state) => state.save);
  const remove = useCookieStore((state) => state.remove);
  const [form] = Form.useForm<Record<string, string>>();

  // Fresh form each time the drawer opens — inputs never pre-fill a cookie.
  useEffect(() => {
    if (drawerOpen) form.resetFields();
  }, [drawerOpen, form]);

  const handleSave = async (platform: string, cookie: string) => {
    try {
      await save(platform, cookie);
      form.setFieldValue(platform, '');
      void message.success(t('cookies.saved'));
    } catch {
      void message.error(t('cookies.savedFailed'));
    }
  };

  const handleClear = async (platform: string) => {
    try {
      await remove(platform);
      form.setFieldValue(platform, '');
      void message.success(t('cookies.cleared'));
    } catch {
      void message.error(t('cookies.clearedFailed'));
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

      <Form form={form} layout="vertical" className="cookie-form">
        {COOKIE_PLATFORMS.map(({ platform, labelKey }) => {
          const configured = isConfigured(entries, platform);
          const updatedAt = updatedAtOf(entries, platform);
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
              <Form.Item name={platform}>
                <Input.Password
                  placeholder={t('cookies.placeholder')}
                  autoComplete="off"
                  data-testid={`cookie-input-${platform}`}
                />
              </Form.Item>
              <Space>
                <Button
                  size="small"
                  type="primary"
                  loading={loading}
                  onClick={() => handleSave(platform, form.getFieldValue(platform) ?? '')}
                  data-testid={`cookie-save-${platform}`}
                >
                  {t('cookies.save')}
                </Button>
                <Button
                  size="small"
                  danger
                  disabled={!configured}
                  onClick={() => handleClear(platform)}
                  data-testid={`cookie-clear-${platform}`}
                >
                  {t('cookies.clear')}
                </Button>
              </Space>
              {updatedAt && (
                <Typography.Text type="secondary" className="cookie-updated" data-testid={`cookie-updated-${platform}`}>
                  {updatedAt}
                </Typography.Text>
              )}
            </div>
          );
        })}
      </Form>
    </Drawer>
  );
}
