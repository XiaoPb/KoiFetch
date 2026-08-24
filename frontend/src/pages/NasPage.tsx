import { Alert, Typography } from 'antd';
import { useTranslation } from '../services/i18n';

/**
 * NAS admin page placeholder (PRD §3.4) — admin-only, reachable at /nas.
 * The file browser (browse/preview/download/delete/rename/move) lands in a
 * later task; the route guard + shell wiring ship in Task 13.
 */
export default function NasPage(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div data-testid="nas-page">
      <Typography.Title level={3}>{t('nas.title')}</Typography.Title>
      <Alert type="info" showIcon message={t('nas.placeholder')} />
    </div>
  );
}
