import { App, Badge, Button, Dropdown, Grid, Layout, Segmented, Tooltip } from 'antd';
import { InboxOutlined, LogoutOutlined, UserOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { StatusIndicator } from './StatusIndicator';
import { useTranslation } from '../services/i18n';
import { useAppStore, type MediaMode } from '../stores/appStore';
import { useAuthStore, selectIsAuthenticated } from '../stores/authStore';
import { useDownloadsStore } from '../stores/downloadsStore';

/**
 * PRD header: logo 🎏 Koi Fetch / mode switch / status indicator / login
 * button (or admin dropdown) / download-center icon with badge. The mode
 * switch collapses on mobile viewports; the language toggle is always
 * available.
 */
export function AppHeader(): JSX.Element {
  const { t, language, toggleLanguage } = useTranslation();
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;

  const mediaMode = useAppStore((state) => state.mediaMode);
  const setMediaMode = useAppStore((state) => state.setMediaMode);
  const isAuthenticated = useAuthStore(selectIsAuthenticated);
  const username = useAuthStore((state) => state.username);
  const logout = useAuthStore((state) => state.logout);
  const activeCount = useDownloadsStore((state) => state.activeCount);

  const onDownloadCenterClick = () => {
    void message.info(t('downloadCenter.comingSoon'));
  };

  const onLogout = () => {
    logout();
    void message.info(t('header.logout'));
  };

  return (
    <Layout.Header className="app-header" data-testid="app-header">
      <div className="app-header-inner">
        <Link to="/" className="app-logo" aria-label="Koi Fetch">
          🎏 Koi Fetch
        </Link>

        {!isMobile && (
          <Segmented<MediaMode>
            className="app-mode-switch"
            options={[
              { label: t('header.modeVideo'), value: 'video' },
              { label: t('header.modeMusic'), value: 'music' },
            ]}
            value={mediaMode}
            onChange={setMediaMode}
          />
        )}

        <div className="app-header-actions">
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
    </Layout.Header>
  );
}
