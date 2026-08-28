import { useEffect, useState } from 'react';
import { Button, Divider, Typography } from 'antd';
import { DeleteOutlined, HistoryOutlined, FireOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';
import { musicApi } from '../../services/api';
import { HOT_KEYWORDS } from './musicSource';
import { useMusicStore } from './musicStore';

export interface SearchSuggestionsProps {
  visible: boolean;
  onPick: (keyword: string) => void;
}

/**
 * Dropdown under the search bar: 搜索历史 (store, localStorage-backed) +
 * 热门搜索 (fetched from GET /api/music/hot, falling back to the bundled list).
 * Suggestions are v1-local (history + configured hot keywords) — there is no
 * server suggestion API (documented).
 */
export function SearchSuggestions({ visible, onPick }: SearchSuggestionsProps): JSX.Element | null {
  const { t } = useTranslation();
  const [hot, setHot] = useState<string[]>(HOT_KEYWORDS);
  const history = useMusicStore((state) => state.history);
  const clearHistory = useMusicStore((state) => state.clearHistory);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    void musicApi.getHotKeywords().then((data) => {
      if (!cancelled && data.keywords.length > 0) setHot(data.keywords);
    }).catch(() => { /* fallback to the bundled list */ });
    return () => { cancelled = true; };
  }, [visible]);

  if (!visible) return null;

  return (
    <div className="music-suggestions" data-testid="music-suggestions">
      {history.length > 0 && (
        <>
          <div className="music-suggestions-head">
            <Typography.Text type="secondary"><HistoryOutlined /> {t('music.searchHistory')}</Typography.Text>
            <Button type="text" size="small" icon={<DeleteOutlined />} onClick={clearHistory} data-testid="clear-history">
              {t('music.clearHistory')}
            </Button>
          </div>
          <div className="music-suggestions-list">
            {history.map((term) => (
              <Button key={term} type="text" block onClick={() => onPick(term)} data-testid={`history-${term}`}>
                {term}
              </Button>
            ))}
          </div>
          <Divider style={{ margin: '8px 0' }} />
        </>
      )}
      <div className="music-suggestions-head">
        <Typography.Text type="secondary"><FireOutlined /> {t('music.hotSearch')}</Typography.Text>
      </div>
      <div className="music-suggestions-list">
        {hot.map((term) => (
          <Button key={term} type="text" block onClick={() => onPick(term)} data-testid={`hot-${term}`}>
            {term}
          </Button>
        ))}
      </div>
    </div>
  );
}
