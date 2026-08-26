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
  'home.subtitle': '视频 · 音乐 · 图片，粘贴链接一键解析下载',

  // Parser workspace (Task 14)
  'parser.placeholder': '粘贴链接或分享文案，回车自动提取',
  'parser.extractedCount': '已提取 {count} 条链接，点击搜索解析',
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
  'preview.videoHint': '下载完成后可在此播放',
  'preview.playing': '已下载文件预览',

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
  'downloads.play': '播放',
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
  'login.expired': '会话已过期，请重新登录',

  // NAS admin (Task 16) — storage status + save-to-NAS entry screen
  'nas.title': 'NAS 管理',
  'nas.storage.title': '存储状态',
  'nas.storage.hint': 'Bubble 与 Pond 存储目录健康状态',
  'nas.storage.loading': '正在检查存储状态...',
  'nas.storage.error': '无法获取存储状态',
  'nas.storage.retry': '重试',
  'nas.storage.serviceApi': 'API 服务',
  'nas.storage.serviceStorage': '存储服务',
  'nas.storage.ok': '正常',
  'nas.storage.errorLabel': '异常',
  'nas.storage.degraded': '降级',
  'nas.storage.root.video_storage_path': '视频池塘',
  'nas.storage.root.image_storage_path': '图片池塘',
  'nas.storage.root.music_storage_path': '音乐池塘',
  'nas.storage.root.temp_video_path': '视频临时区',
  'nas.storage.root.temp_image_path': '图片临时区',
  'nas.storage.root.temp_music_path': '音乐临时区',
  'nas.save.title': '保存到 NAS',
  'nas.save.hint': '将已完成下载的文件移入池塘(仅列出本会话中的已完成下载)',
  'nas.save.toNas': '存入 NAS',
  'nas.save.completedEmpty': '暂无已完成的下载',
  'nas.save.completedEmptyHint': '先在解析工作台完成下载，再回到这里存入 NAS',
  'nas.save.modalTitle': '存入 NAS',
  'nas.save.targetLabel': '目标目录',
  'nas.save.targetPlaceholder': '例如 /视频/抖音',
  'nas.save.targetHint': '相对池塘根目录的逻辑路径，开头 / 可选',
  'nas.save.targetEmpty': '请输入目标目录',
  'nas.save.targetRootOnly': '请输入至少一个目录(例如 /视频/抖音)',
  'nas.save.targetInvalid': '目标路径无效：不能包含 .. 、反斜杠或盘符',
  'nas.save.confirm': '保存',
  'nas.save.cancel': '取消',
  'nas.save.success': '🎉 锦鲤已游入池塘!',
  'nas.save.successPath': '已保存到 {path}',
  'nas.save.failed': '保存失败',

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
  'home.subtitle': 'Video · Music · Images — parse and download in one click',

  // Parser workspace (Task 14)
  'parser.placeholder': 'Paste a link or share text, press Enter',
  'parser.extractedCount': '{count} links found — click search to parse',
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
  'preview.videoHint': 'Play here after the download completes',
  'preview.playing': 'Downloaded file preview',

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
  'downloads.play': 'Play',
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
  'login.expired': 'Session expired, please log in again',

  // NAS admin (Task 16) — storage status + save-to-NAS entry screen
  'nas.title': 'NAS Admin',
  'nas.storage.title': 'Storage status',
  'nas.storage.hint': 'Bubble and Pond storage directory health',
  'nas.storage.loading': 'Checking storage status...',
  'nas.storage.error': 'Failed to load storage status',
  'nas.storage.retry': 'Retry',
  'nas.storage.serviceApi': 'API service',
  'nas.storage.serviceStorage': 'Storage service',
  'nas.storage.ok': 'OK',
  'nas.storage.errorLabel': 'Error',
  'nas.storage.degraded': 'Degraded',
  'nas.storage.root.video_storage_path': 'Video pond',
  'nas.storage.root.image_storage_path': 'Image pond',
  'nas.storage.root.music_storage_path': 'Music pond',
  'nas.storage.root.temp_video_path': 'Video temp',
  'nas.storage.root.temp_image_path': 'Image temp',
  'nas.storage.root.temp_music_path': 'Music temp',
  'nas.save.title': 'Save to NAS',
  'nas.save.hint': 'Move completed download files into the pond (only downloads completed in this session are listed)',
  'nas.save.toNas': 'Save to NAS',
  'nas.save.completedEmpty': 'No completed downloads yet',
  'nas.save.completedEmptyHint': 'Complete a download in the parser workspace first, then save it here',
  'nas.save.modalTitle': 'Save to NAS',
  'nas.save.targetLabel': 'Target directory',
  'nas.save.targetPlaceholder': 'e.g. /Video/Douyin',
  'nas.save.targetHint': 'Logical path relative to the pond root (leading / optional)',
  'nas.save.targetEmpty': 'Enter a target directory',
  'nas.save.targetRootOnly': 'Enter at least one directory (e.g. /Video/Douyin)',
  'nas.save.targetInvalid': 'Invalid target path: no .., backslashes or drive letters',
  'nas.save.confirm': 'Save',
  'nas.save.cancel': 'Cancel',
  'nas.save.success': '🎉 The koi has swum into the pond!',
  'nas.save.successPath': 'Saved to {path}',
  'nas.save.failed': 'Save failed',

  'notFound.title': 'Page not found',
  'notFound.backHome': 'Back to home',

  'errorBoundary.title': 'Something went wrong',
  'errorBoundary.reload': 'Reload',

  'common.networkError': 'Network error',
};

export const translations: Record<Language, Record<TranslationKey, string>> = { zh, en };

/**
 * Translate a key for the active language, interpolating `{name}` tokens with
 * `params` (e.g. `translate('zh', 'downloads.remainingMinutes', { value: 2 })`
 * → "约 2 分钟"). Missing params are left as the literal `{name}` token so
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
