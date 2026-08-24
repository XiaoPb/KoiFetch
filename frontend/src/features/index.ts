// Feature modules (parse, download, preview, NAS, auth).
// Task 14 adds the parser workspace feature; Task 15 adds the preview modal
// and the download-center drawer; later tasks add the rest.
export { ParserWorkspace } from './parser/ParserWorkspace';
export { ResultCard, type DownloadOptions } from './parser/ResultCard';
export {
  useParserStore,
  extractUrls,
  selectVisibleResults,
  MAX_PARSE_URLS,
  PARSER_EMPTY_INPUT_MESSAGE,
  PARSER_TOO_MANY_URLS_MESSAGE,
  type ParserStatus,
  type ParserState,
} from './parser/parserStore';
export { usePreviewStore, type PreviewState } from './parser/previewStore';
export { PreviewModal } from './preview/PreviewModal';
export { DownloadCenterDrawer, type DownloadCenterDrawerProps } from './downloads/DownloadCenterDrawer';
export { formatBytes, formatSpeed, splitRemainingTime } from './downloads/format';
