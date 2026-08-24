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

  'home.title': '解析工作台',
  'home.placeholder': '粘贴链接，每行一个...',
  'home.parse': '解析',
  'home.batchParse': '批量解析',
  'home.importTxt': '导入TXT',
  'home.comingSoon': '解析与下载功能将在后续任务中开放 / Parsing and download features land in a later phase',
  'home.loading': '加载中...',

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

  'home.title': 'Parser Workspace',
  'home.placeholder': 'Paste links, one per line...',
  'home.parse': 'Parse',
  'home.batchParse': 'Batch Parse',
  'home.importTxt': 'Import TXT',
  'home.comingSoon': 'Parsing and download features land in a later phase',
  'home.loading': 'Loading...',

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

export function translate(language: Language, key: TranslationKey): string {
  return translations[language][key] ?? translations.zh[key] ?? key;
}

export function useTranslation(): {
  t: (key: TranslationKey) => string;
  language: Language;
  setLanguage: (language: Language) => void;
  toggleLanguage: () => void;
} {
  const language = useAppStore((state) => state.language);
  const setLanguage = useAppStore((state) => state.setLanguage);

  const t = useCallback((key: TranslationKey) => translate(language, key), [language]);
  const toggleLanguage = useCallback(
    () => setLanguage(language === 'zh' ? 'en' : 'zh'),
    [language, setLanguage],
  );

  return { t, language, setLanguage, toggleLanguage };
}
