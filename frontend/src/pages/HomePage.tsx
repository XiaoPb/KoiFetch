import { Alert, Button, Input, Space, Typography, Upload } from 'antd';
import { CloudUploadOutlined, ScanOutlined, UnorderedListOutlined } from '@ant-design/icons';
import { useTranslation } from '../services/i18n';
import { useAppStore } from '../stores/appStore';

/**
 * Parser workspace (PRD §4.2) — Task 13 ships the shell + layout only; the
 * parse feature (URL validation, batch parse, result grid) lands in Task 14.
 * The controls render disabled so the layout matches the PRD without
 * advertising functionality that is not wired yet.
 */
export default function HomePage(): JSX.Element {
  const { t } = useTranslation();
  const mediaMode = useAppStore((state) => state.mediaMode);

  return (
    <div className="home-page" data-testid="home-page">
      <Typography.Title level={3}>{t('home.title')}</Typography.Title>

      <div className="parser-workspace">
        <Input.TextArea
          rows={4}
          placeholder={t('home.placeholder')}
          aria-label={t('home.placeholder')}
          disabled
        />
        <Space wrap>
          <Button type="primary" icon={<ScanOutlined />} disabled>
            {t('home.parse')}
          </Button>
          <Button icon={<UnorderedListOutlined />} disabled>
            {t('home.batchParse')}
          </Button>
          <Upload disabled>
            <Button icon={<CloudUploadOutlined />} disabled>
              {t('home.importTxt')}
            </Button>
          </Upload>
        </Space>
      </div>

      <Alert
        type="info"
        showIcon
        message={`${mediaMode === 'video' ? t('header.modeVideo') : t('header.modeMusic')} · ${t('home.comingSoon')}`}
      />
    </div>
  );
}
