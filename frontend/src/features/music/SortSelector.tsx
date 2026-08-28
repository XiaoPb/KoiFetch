import { Dropdown } from 'antd';
import { DownOutlined, SortAscendingOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import { useTranslation } from '../../services/i18n';
import type { MusicSort } from './musicStore';

export interface SortSelectorProps {
  sort: MusicSort;
  onChange: (sort: MusicSort) => void;
}

const SORTS: readonly MusicSort[] = ['comprehensive', 'latest', 'hot'];

/** 综合/最新/热度 sort control (P2); hot = bitrate-desc proxy (documented). */
export function SortSelector({ sort, onChange }: SortSelectorProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <Dropdown
      menu={{
        items: SORTS.map((value) => ({
          key: value,
          label: t(`music.sort.${value}` as const),
        })),
        selectedKeys: [sort],
        onClick: ({ key }) => onChange(key as MusicSort),
      }}
      trigger={['click']}
    >
      <Button type="text" icon={<SortAscendingOutlined />} data-testid="sort-selector">
        {t(`music.sort.${sort}` as const)} <DownOutlined />
      </Button>
    </Dropdown>
  );
}
