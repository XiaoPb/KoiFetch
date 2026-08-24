import { Button, Result } from 'antd';
import { Link } from 'react-router-dom';
import { useTranslation } from '../services/i18n';

/** Catch-all route: unknown paths fall back here instead of a blank page. */
export default function NotFoundPage(): JSX.Element {
  const { t } = useTranslation();
  return (
    <Result
      status="404"
      title={t('notFound.title')}
      extra={
        <Link to="/">
          <Button type="primary">{t('notFound.backHome')}</Button>
        </Link>
      }
    />
  );
}
