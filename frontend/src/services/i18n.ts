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
  'parser.noResultsInMode': '当前模式下没有可显示的结果(图片结果在所有模式下展示)',
  'parser.summary': '成功 {ok} 个，失败 {failed} 个',
  'parser.failedTitle': '解析失败',
  'parser.preview': '预览',
  'parser.download': '下载',
  'parser.downloadStarted': '已提交下载任务',
  'parser.quality': '选择清晰度',
  'parser.bitrate': '选择码率',
  'parser.txtRejected': '仅支持 TXT 文本文件',
  'parser.txtTooLarge': 'TXT 文件过大(最大 1 MB)',
  'parser.txtHint': '导入 UTF-8 编码的 TXT 文件(每行一个链接，最大 1 MB)',
  'parser.txtReadFailed': 'TXT 文件读取失败',

  // Preview modal (Task 15) — v1 returns metadata + a streams ladder, not
  // byte streams; the modal renders an honest preview-info panel.
  'preview.title': '预览',
  'preview.metadataOnly': 'v1 预览为元数据信息(不含媒体流)',
  'preview.platform': '平台',
  'preview.url': '链接',
  'preview.duration': '时长',
  'preview.format': '格式',
  'preview.fileSize': '文件大小',
  'preview.quality': '清晰度',
  'preview.bitrate': '码率',
  'preview.streams': '可用流',
  'preview.noStreams': '暂无流信息',

  // Download center (Task 15)
  'downloads.empty': '暂无下载任务',
  'downloads.emptyTab': '该分类下暂无任务',
  'downloads.tab.all': '全部',
  'downloads.tab.active': '进行中',
  'downloads.tab.completed': '已完成',
  'downloads.tab.failed': '已失败',
  'downloads.status.pending': '等待中',
  'downloads.status.downloading': '下载中',
  'downloads.status.completed': '已完成',
  'downloads.status.failed': '失败',
  'downloads.status.expired': '已过期',
  'downloads.unknownTitle': '未命名任务',
  'downloads.getFile': '获取文件',
  'downloads.refreshLink': '刷新链接',
  'downloads.retry': '重试',
  'downloads.retryStarted': '已重新提交下载任务',
  'downloads.linkMissing': '文件链接不可用，请点击刷新链接',
  'downloads.linkMissingHint': '未获取到文件链接(可能错过了推送)，可刷新链接获取',
  'downloads.linkExpired': '文件链接已过期(5 分钟)，请刷新链接',
  'downloads.linkExpiredHint': '链接已过期，刷新后 5 分钟内有效',
  'downloads.linkValidHint': '链接 5 分钟内有效',
  'downloads.cancelNotSupported': '当前版本暂不支持取消下载',
  'downloads.downloadedOf': '已下载 {downloaded} / {total}',
  'downloads.remainingSeconds': '约 {value} 秒',
  'downloads.remainingMinutes': '约 {value} 分钟',
  'downloads.remainingHours': '约 {value} 小时',

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
  'parser.noResultsInMode': 'No results in the current mode (image results show in all modes)',
  'parser.summary': '{ok} succeeded, {failed} failed',
  'parser.failedTitle': 'Failed links',
  'parser.preview': 'Preview',
  'parser.download': 'Download',
  'parser.downloadStarted': 'Download submitted',
  'parser.quality': 'Quality',
  'parser.bitrate': 'Bitrate',
  'parser.txtRejected': 'Only TXT text files are supported',
  'parser.txtTooLarge': 'TXT file too large (max 1 MB)',
  'parser.txtHint': 'Import a UTF-8 TXT file (one link per line, max 1 MB)',
  'parser.txtReadFailed': 'Failed to read the TXT file',

  // Preview modal (Task 15)
  'preview.title': 'Preview',
  'preview.metadataOnly': 'v1 preview shows metadata only (no media stream)',
  'preview.platform': 'Platform',
  'preview.url': 'URL',
  'preview.duration': 'Duration',
  'preview.format': 'Format',
  'preview.fileSize': 'File size',
  'preview.quality': 'Quality',
  'preview.bitrate': 'Bitrate',
  'preview.streams': 'Streams',
  'preview.noStreams': 'No stream info',

  // Download center (Task 15)
  'downloads.empty': 'No download tasks yet',
  'downloads.emptyTab': 'No tasks in this category',
  'downloads.tab.all': 'All',
  'downloads.tab.active': 'In progress',
  'downloads.tab.completed': 'Completed',
  'downloads.tab.failed': 'Failed',
  'downloads.status.pending': 'Pending',
  'downloads.status.downloading': 'Downloading',
  'downloads.status.completed': 'Completed',
  'downloads.status.failed': 'Failed',
  'downloads.status.expired': 'Expired',
  'downloads.unknownTitle': 'Untitled task',
  'downloads.getFile': 'Get file',
  'downloads.refreshLink': 'Refresh link',
  'downloads.retry': 'Retry',
  'downloads.retryStarted': 'Download resubmitted',
  'downloads.linkMissing': 'File link unavailable — refresh to get one',
  'downloads.linkMissingHint': 'The file link was not captured (a push may have been missed) — refresh to get one',
  'downloads.linkExpired': 'File link expired (5 min) — refresh for a new one',
  'downloads.linkExpiredHint': 'Link expired; refreshed links last 5 minutes',
  'downloads.linkValidHint': 'Link valid for 5 minutes',
  'downloads.cancelNotSupported': 'Canceling downloads is not supported in v1',
  'downloads.downloadedOf': 'Downloaded {downloaded} of {total}',
  'downloads.remainingSeconds': '~{value}s',
  'downloads.remainingMinutes': '~{value} min',
  'downloads.remainingHours': '~{value} h',

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
