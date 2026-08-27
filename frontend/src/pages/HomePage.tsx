import { Typography } from 'antd';
import { useTranslation } from '../services/i18n';
import { useAppStore } from '../stores/appStore';
import { ParserWorkspace } from '../features/parser/ParserWorkspace';
import MusicSearchPage from './MusicSearchPage';

/**
 * The single main page (`/`): the Topbar's 视频/音乐 switch selects which
 * content area renders here — video mode shows the parser workspace (hero +
 * workspace), music mode shows the music search experience. Both modes share
 * the same page, the same Topbar and the same URL.
 */
export default function HomePage(): JSX.Element {
  const { t } = useTranslation();
  const mediaMode = useAppStore((state) => state.mediaMode);

  if (mediaMode === 'music') {
    return <MusicSearchPage />;
  }

  return (
    <div className="home-page" data-testid="home-page">
      <div className="home-hero" data-testid="home-hero">
        <Typography.Title level={3} className="home-hero-title">
          {t('home.title')}
        </Typography.Title>
        <Typography.Text className="home-hero-sub" data-testid="home-hero-sub">
          {t('home.subtitle')}
        </Typography.Text>
      </div>
      <ParserWorkspace />
    </div>
  );
}
