import { useState } from 'react';
import { App, Badge, Button, Dropdown, Grid, Layout, Segmented, Tooltip } from 'antd';
import {
  InboxOutlined,
  LogoutOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Link, useNavigate } from 'react-router-dom';
import { StatusIndicator } from './StatusIndicator';
import { DownloadCenterDrawer } from '../features/downloads/DownloadCenterDrawer';
import { CookieSettingsDrawer } from '../features/cookies/CookieSettingsDrawer';
import { useCookieStore } from '../features/cookies/cookieStore';
import { useTranslation } from '../services/i18n';
import { useAppStore, type MediaMode } from '../stores/appStore';
import { selectIsAuthenticated, useAuthStore } from '../stores/authStore';
import { selectActiveCount, useDownloadsStore } from '../stores/downloadsStore';
import { logoutSession } from '../services/session';

/**
 * PRD header: logo 🎏 Koi Fetch / video-music mode switch / status indicator /
 * login button (or admin dropdown + settings gear) / download-center icon with
 * badge that opens the Task 15 download-center Drawer. The gear opens the
 * admin-only platform-cookie settings drawer (Task 15). The 视频/音乐 mode
 * switch shows BOTH tabs directly on every viewport (a compact Segmented
 * shrinks on mobile) — no dropdown; the language toggle is always visible.
 */
export function AppHeader(): JSX.Element {
  const { t, language, toggleLanguage } = useTranslation();
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [drawerOpen, setDrawerOpen] = useState(false);

  const mediaMode = useAppStore((state) => state.mediaMode);
  const setMediaMode = useAppStore((state) => state.setMediaMode);
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const username = useAuthStore((state) => state.username);
  const activeCount = useDownloadsStore(selectActiveCount);
  const navigate = useNavigate();

  const modeOptions: { label: string; value: MediaMode }[] = [
    { label: t('header.modeVideo'), value: 'video' },
    { label: t('header.modeMusic'), value: 'music' },
  ];

  /**
   * The video/music switch selects the CONTENT AREA of the single main page
   * (one page, one Topbar): switching mode from anywhere returns to `/`.
   */
  const onModeChange = (mode: MediaMode) => {
    setMediaMode(mode);
    navigate('/');
  };

  const onDownloadCenterClick = () => {
    setDrawerOpen(true);
  };

  const onLogout = () => {
    // One logout entry point: clears the auth session AND tears down the
    // session-local download state (live sockets, poll timer, task list).
    logoutSession();
    void message.info(t('header.logout'));
  };

  return (
    <Layout.Header className="app-header" data-testid="app-header">
      <div className="app-header-inner">
        <Link to="/" className="app-logo" aria-label="Koi Fetch">
          🎏 Koi Fetch
        </Link>

        <Segmented<MediaMode>
          className="app-mode-switch"
          size={isMobile ? 'small' : 'middle'}
          data-testid="mode-switch"
          options={modeOptions}
          value={mediaMode}
          onChange={onModeChange}
        />

        <div className="app-header-actions">
          {isAuthenticated && (
            <Tooltip title={t('header.settings')}>
              <Button
                size="small"
                icon={<SettingOutlined />}
                aria-label={t('header.settings')}
                data-testid="settings-button"
                onClick={() => useCookieStore.getState().openDrawer()}
              />
            </Tooltip>
          )}

          <StatusIndicator />

          <Tooltip title={t('header.language')}>
            <Button size="small" onClick={toggleLanguage} data-testid="language-toggle">
              {language === 'zh' ? 'EN' : '中文'}
            </Button>
          </Tooltip>

          {isAuthenticated ? (
            <Dropdown
              menu={{
                items: [
                  { key: 'username', label: username ?? 'admin', icon: <UserOutlined />, disabled: true },
                  { type: 'divider' },
                  { key: 'nas', label: <Link to="/nas">{t('header.adminMenu.nas')}</Link> },
                  { key: 'logout', label: t('header.logout'), icon: <LogoutOutlined />, onClick: onLogout },
                ],
              }}
            >
              <Button size="small" type="primary" ghost>
                <UserOutlined /> {username ?? 'admin'}
              </Button>
            </Dropdown>
          ) : (
            <Link to="/login">
              <Button type="primary" size="small">
                {t('header.login')}
              </Button>
            </Link>
          )}

          <Badge count={activeCount} size="small">
            <Button
              type="text"
              aria-label={t('header.downloadCenter')}
              data-testid="download-center"
              onClick={onDownloadCenterClick}
            >
              <InboxOutlined />
            </Button>
          </Badge>
        </div>
      </div>

      <DownloadCenterDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
      <CookieSettingsDrawer />
    </Layout.Header>
  );
}
