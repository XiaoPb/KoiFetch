import { useCallback } from 'react';
import { useAppStore } from '../stores/appStore';

// Lightweight bilingual i18n (Chinese-primary per product direction).
// A typed dictionary with zh + en for every key, a `translate` helper, and a
// React hook bound to the app language store. No external i18n library.

export type Language = 'zh' | 'en';

export const LANGUAGES: readonly Language[] = ['zh', 'en'];

const zh = {
  'app.name': 'Koi Fetch 锦鲤抓取',

  'header.online': '在线',
  'header.offline': '离线',
  'header.statusUnknown': '…',
  'header.modeVideo': '视频',
  'header.modeMusic': '音乐',
  'header.login': '登录',
  'header.logout': '退出登录',
  'header.language': '切换语言 / Switch language',
  'header.downloadCenter': '下载中心 / Download center',
  'header.adminMenu.nas': 'NAS 管理',
  'downloadCenter.comingSoon': '下载中心将在后续版本开放 / Download center comes in a later version',

  'download.progress': '下载进度 {percent}%',

  'home.title': '解析工作台',

  // Parser workspace (Task 14)
  'parser.placeholder': '粘贴链接，每行一个...',
  'parser.modeHint': '当前模式：{mode}',
  'parser.parse': '解析',
  'parser.importTxt': '导入 TXT',
  'parser.clear': '清空',
  'parser.loading': '解析中...',
  'parser.retry': '重试',
  'parser.noResults': '暂无结果 — 粘贴链接后点击解析',
  'parser.noResultsInMode': '当前模式下没有可显示的结果，试试切换模式',
  'parser.summary': '成功 {ok} 个，失败 {failed} 个',
  'parser.failedTitle': '解析失败',
  'parser.preview': '预览',
  'parser.download': '下载',
  'parser.downloadStarted': '已提交下载任务',
  'parser.quality': '选择清晰度',
  'parser.bitrate': '选择码率',
  'parser.txtRejected': '仅支持 TXT 文本文件',
  'parser.txtReadFailed': 'TXT 文件读取失败',

  'login.title': '管理员登录',
  'login.username': '用户名',
  'login.password': '密码',
  'login.submit': '登录',
  'login.success': '登录成功 / Login successful',
  'login.failed': '登录失败 / Login failed',

  'nas.title': 'NAS 管理',
  'nas.placeholder': 'NAS 文件管理功能将在后续版本开放 / NAS file management comes in a later version',

  'notFound.title': '页面不存在',
  'notFound.backHome': '返回首页',

  'errorBoundary.title': '页面出错了',
  'errorBoundary.reload': '刷新页面',

  'common.networkError': '网络错误 / Network error',
} as const;

export type TranslationKey = keyof typeof zh;

const en: Record<TranslationKey, string> = {
  'app.name': 'Koi Fetch',

  'header.online': 'Online',
  'header.offline': 'Offline',
  'header.statusUnknown': '…',
  'header.modeVideo': 'Video',
  'header.modeMusic': 'Music',
  'header.login': 'Log in',
  'header.logout': 'Log out',
  'header.language': '切换语言 / Switch language',
  'header.downloadCenter': 'Download center',
  'header.adminMenu.nas': 'NAS Admin',
  'downloadCenter.comingSoon': 'Download center comes in a later version',

  'download.progress': 'Download progress {percent}%',

  'home.title': 'Parser Workspace',

  // Parser workspace (Task 14)
  'parser.placeholder': 'Paste links, one per line...',
  'parser.modeHint': 'Current mode: {mode}',
  'parser.parse': 'Parse',
  'parser.importTxt': 'Import TXT',
  'parser.clear': 'Clear',
  'parser.loading': 'Parsing...',
  'parser.retry': 'Retry',
  'parser.noResults': 'No results yet — paste links and click Parse',
  'parser.noResultsInMode': 'No results match the current mode — try switching',
  'parser.summary': '{ok} succeeded, {failed} failed',
  'parser.failedTitle': 'Failed links',
  'parser.preview': 'Preview',
  'parser.download': 'Download',
  'parser.downloadStarted': 'Download submitted',
  'parser.quality': 'Quality',
  'parser.bitrate': 'Bitrate',
  'parser.txtRejected': 'Only TXT text files are supported',
  'parser.txtReadFailed': 'Failed to read the TXT file',

  'login.title': 'Admin Login',
  'login.username': 'Username',
  'login.password': 'Password',
  'login.submit': 'Log in',
  'login.success': 'Login successful',
  'login.failed': 'Login failed',

  'nas.title': 'NAS Admin',
  'nas.placeholder': 'NAS file management comes in a later version',

  'notFound.title': 'Page not found',
  'notFound.backHome': 'Back to home',

  'errorBoundary.title': 'Something went wrong',
  'errorBoundary.reload': 'Reload',

  'common.networkError': 'Network error',
};

export const translations: Record<Language, Record<TranslationKey, string>> = { zh, en };

/**
 * Translate a key for the active language, interpolating `{name}` tokens with
 * `params` (e.g. `translate('zh', 'download.progress', { percent: 45 })` →
 * "下载进度 45%"). Missing params are left as the literal `{name}` token so
 * a missing argument never silently renders a blank.
 */
export function translate(
  language: Language,
  key: TranslationKey,
  params?: Record<string, string | number>,
): string {
  const template = translations[language][key] ?? translations.zh[key] ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}

export function useTranslation(): {
  t: (key: TranslationKey, params?: Record<string, string | number>) => string;
  language: Language;
  setLanguage: (language: Language) => void;
  toggleLanguage: () => void;
} {
  const language = useAppStore((state) => state.language);
  const setLanguage = useAppStore((state) => state.setLanguage);

  const t = useCallback(
    (key: TranslationKey, params?: Record<string, string | number>) => translate(language, key, params),
    [language],
  );
  const toggleLanguage = useCallback(
    () => setLanguage(language === 'zh' ? 'en' : 'zh'),
    [language, setLanguage],
  );

  return { t, language, setLanguage, toggleLanguage };
}
