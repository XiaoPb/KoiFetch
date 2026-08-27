import { Button, Input } from 'antd';
import { ArrowLeftOutlined, SearchOutlined } from '@ant-design/icons';
import { useTranslation } from '../../services/i18n';

export interface MusicSearchBarProps {
  value: string;
  loading: boolean;
  onBack: () => void;
  onChange: (value: string) => void;
  onSearch: () => void;
}

/**
 * Fixed top bar of the music search page (spec §1): back arrow, search input
 * that keeps its text and selects all on focus (so the user can overwrite it
 * directly), and a search button. Enter also triggers the search.
 */
export function MusicSearchBar({ value, loading, onBack, onChange, onSearch }: MusicSearchBarProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="music-search-bar" data-testid="music-search-bar">
      <Button
        type="text"
        icon={<ArrowLeftOutlined />}
        aria-label={t('music.back')}
        onClick={onBack}
        data-testid="music-back"
      />
      <div className="music-search-input-wrap">
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onPressEnter={onSearch}
          onFocus={(event) => event.currentTarget.select()}
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
