import { Button, Typography } from 'antd';
import { AudioOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useTranslation } from '../services/i18n';
import { ParserWorkspace } from '../features/parser/ParserWorkspace';

/**
 * Home page — the parser workspace (PRD §4.2). A compact gradient hero band
 * (vibrant blue) sits above the workspace and collapses gracefully on mobile.
 * The hero also carries the entry button to the standalone music search page
 * (/music), which is deliberately independent from the video parser.
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
        <div className="home-hero-actions">
          <Link to="/music">
            <Button ghost icon={<AudioOutlined />} data-testid="music-entry">
              {t('music.entry')}
            </Button>
          </Link>
        </div>
      </div>
      <ParserWorkspace />
    </div>
  );
}
