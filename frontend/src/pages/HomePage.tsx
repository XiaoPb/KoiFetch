import { Typography } from 'antd';
import { useTranslation } from '../services/i18n';
import { ParserWorkspace } from '../features/parser/ParserWorkspace';

/**
 * Home page — the parser workspace (PRD §4.2). The routed page is a thin
 * wrapper around the feature component so routing stays stable while the
 * workspace lives in the parser feature folder.
 */
export default function HomePage(): JSX.Element {
  const { t } = useTranslation();

  return (
    <div className="home-page" data-testid="home-page">
      <Typography.Title level={3}>{t('home.title')}</Typography.Title>
      <ParserWorkspace />
    </div>
  );
}
