import { Typography } from 'antd';
import { useTranslation } from '../services/i18n';
import { ParserWorkspace } from '../features/parser/ParserWorkspace';

/**
 * Home page — the parser workspace (PRD §4.2). A compact gradient hero band
 * (vibrant blue) sits above the workspace and collapses gracefully on mobile.
 */
export default function HomePage(): JSX.Element {
  const { t } = useTranslation();

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
