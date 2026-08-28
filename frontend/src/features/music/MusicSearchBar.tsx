import { Button, Input } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';

export interface MusicSearchBarProps {
  value: string;
  loading: boolean;
  onChange: (value: string) => void;
  onSearch: () => void;
  /** Focus-state reporting so the page can show the suggestions dropdown. */
  onFocusChange?: (focused: boolean) => void;
}

/**
 * Search row at the top of the music content area (spec §1, adapted to the
 * single-main-page layout — no back arrow, the Topbar tab is the navigation):
 * a search input that keeps its text and selects all on focus (so the user
 * can overwrite it directly) and a search button. Enter also triggers search.
 */
export function MusicSearchBar({ value, loading, onChange, onSearch, onFocusChange }: MusicSearchBarProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="music-search-bar" data-testid="music-search-bar">
      <div className="music-search-input-wrap">
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onPressEnter={onSearch}
          onFocus={(event) => {
            event.currentTarget.select();
            onFocusChange?.(true);
          }}
          onBlur={() => onFocusChange?.(false)}
          allowClear
          placeholder={t('music.searchPlaceholder')}
          size="large"
          aria-label={t('music.searchPlaceholder')}
          data-testid="music-search-input"
        />
      </div>
      <Button
        type="primary"
        loading={loading}
        icon={<SearchOutlined />}
        onClick={onSearch}
        data-testid="music-search-submit"
      >
        {t('music.search')}
      </Button>
    </div>
  );
}
