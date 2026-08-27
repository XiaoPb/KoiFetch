import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { translate, translations, useTranslation } from './i18n';
import { useAppStore } from '../stores/appStore';
import type { TranslationKey } from './i18n';

function LanguageProbe(): JSX.Element {
  const { t, toggleLanguage } = useTranslation();
  return (
    <div>
      <span>{t('header.online')}</span>
      <button onClick={toggleLanguage}>toggle</button>
    </div>
  );
}

describe('i18n', () => {
  beforeEach(() => {
    useAppStore.setState({ language: 'zh' });
  });

  it('provides an en translation for every zh key', () => {
    const zhKeys = Object.keys(translations.zh);
    // Sanity: the dictionary is not accidentally empty.
    expect(zhKeys.length).toBeGreaterThan(10);
    for (const key of zhKeys) {
      expect(translations.en[key as TranslationKey], `missing en translation for "${key}"`).toBeDefined();
    }
    expect(Object.keys(translations.en)).toHaveLength(zhKeys.length);
  });

  it('translates keys per language', () => {
    expect(translate('zh', 'header.online')).toBe('在线');
    expect(translate('en', 'header.online')).toBe('Online');
    expect(translate('zh', 'header.login')).toBe('登录');
    expect(translate('en', 'header.login')).toBe('Log in');
  });

  it('interpolates params into templates (Task 15 progress strings)', () => {
    expect(translate('zh', 'downloads.remainingMinutes', { value: 2 })).toBe('约 2 分钟');
    expect(translate('en', 'downloads.remainingMinutes', { value: 2 })).toBe('~2 min');
    // Unknown params are left as-is rather than silently dropped.
    expect(translate('en', 'downloads.remainingMinutes', { other: 1 })).toBe('~{value} min');
  });

  it('interpolates music search count keys', () => {
    expect(translate('zh', 'music.count.song', { total: '1,235' })).toBe('约 1,235 首单曲');
    expect(translate('en', 'music.count.artist', { total: '42' })).toBe('About 42 artists');
  });

  it('toggles the active language through the hook', async () => {
    const user = userEvent.setup();
    render(<LanguageProbe />);
    expect(screen.getByText('在线')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'toggle' }));
    expect(screen.getByText('Online')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'toggle' }));
    expect(screen.getByText('在线')).toBeInTheDocument();
  });
});
