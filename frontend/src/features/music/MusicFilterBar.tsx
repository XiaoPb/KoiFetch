import { Tabs, Typography } from 'antd';
import type { TranslationKey } from '../../services/i18n';
import { useTranslation } from '../../services/i18n';
import type { MusicCategory } from '../../types/music';
import { formatNumber } from './format';

export const MUSIC_CATEGORIES: readonly MusicCategory[] = ['all', 'song', 'artist', 'album', 'playlist'];

export interface MusicFilterBarProps {
  category: MusicCategory;
  /** Total for the ACTIVE category (e.g. "约 1,235 首单曲"). */
  total: number;
  onChange: (category: MusicCategory) => void;
}

/**
 * Stats + filter bar under the top search bar (spec §2): result total on the
 * left, the 综合/单曲/歌手/专辑/歌单 tabs on the right. antd `Tabs` line style
 * draws the underline under the active tab. Clicking a tab re-runs the search
 * for that category (the page wires it to the store's `setCategory`).
 */
export function MusicFilterBar({ category, total, onChange }: MusicFilterBarProps): JSX.Element {
  const { t, language } = useTranslation();
  const items = MUSIC_CATEGORIES.map((value) => ({
    key: value,
    label: t(`music.tab.${value}` as TranslationKey),
  }));

  return (
    <div className="music-filter-bar" data-testid="music-filter-bar">
      <Typography.Text type="secondary" className="music-count" data-testid="music-count">
        {t(`music.count.${category}` as TranslationKey, { total: formatNumber(total, language) })}
      </Typography.Text>
      <Tabs
        size="small"
        className="music-filter-tabs"
        activeKey={category}
        items={items}
        onChange={(key) => onChange(key as MusicCategory)}
        tabBarGutter={4}
      />
    </div>
  );
}
