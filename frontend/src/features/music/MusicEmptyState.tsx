import { Empty, Space, Tag, Typography } from 'antd';
import { useTranslation } from '../../services/i18n';

export interface MusicEmptyStateProps {
  description: string;
  hotKeywords: string[];
  onSearch: (keyword: string) => void;
}

/**
 * No-results state (spec §3 无结果状态): antd Empty illustration + a hint
 * message + clickable hot-search keywords. Also shown before the first search
 * (idle) so the page invites a query.
 */
export function MusicEmptyState({ description, hotKeywords, onSearch }: MusicEmptyStateProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="music-empty" data-testid="music-empty">
      <Empty description={description} />
      <Typography.Text type="secondary" className="music-hot-title">
        {t('music.hotSearch')}
      </Typography.Text>
      <Space wrap className="music-hot-tags">
        {hotKeywords.map((keyword) => (
          <Tag
            key={keyword}
            color="blue"
            className="music-hot-tag"
            onClick={() => onSearch(keyword)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSearch(keyword);
              }
            }}
            role="button"
            tabIndex={0}
            data-testid="music-hot-tag"
          >
            {keyword}
          </Tag>
        ))}
      </Space>
    </div>
  );
}
