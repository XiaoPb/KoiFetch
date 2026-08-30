import { useMemo } from 'react';
import { Alert, App, Button, Col, Grid, Input, Row, Space, Tag, Typography, Upload } from 'antd';
import { ClearOutlined, FileTextOutlined, ScanOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { useTranslation } from '../../services/i18n';
import { useAppStore } from '../../stores/appStore';
import { useDownloadsStore } from '../../stores/downloadsStore';
import { downloadApi, mediaApi } from '../../services/api';
import { getErrorMessage } from '../../services/apiClient';
import { ResultCard, type DownloadOptions } from './ResultCard';
import { extractUrls, selectVisibleResults, useParserStore } from './parserStore';
import { usePreviewStore } from './previewStore';
import { useCookieStore } from '../cookies/cookieStore';
import { ApiCodes, type ParseResult } from '../../types/api';

/**
 * Read a plain-text file as UTF-8 (TXT batch import). Plain-text only by
 * design — no spreadsheets or archives are accepted, and the file must be
 * UTF-8 encoded (other encodings, e.g. GBK, will not decode correctly).
 */
export function readTxtFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('File read failed'));
    reader.readAsText(file, 'utf-8');
  });
}

/** TXT import size guard — a huge file would freeze the tab on decode. */
export const MAX_TXT_IMPORT_BYTES = 1024 * 1024; // 1 MB

/**
 * Parser workspace (PRD §4.2) — redesigned around a one-line search input.
 *
 * Input UX (product feedback): the batch textarea is gone. A single-line
 * `Input.Search` inside a `Space.Compact` group with a compact TXT button
 * covers 输入 + 搜索 + 加载txt. Any pasted text — Douyin share cards, plain
 * URL lists, TXT batches — is filtered for links client-side (extractUrls),
 * so prose around the URL is ignored. A Tag reports how many links were
 * extracted whenever there is more than one. The header media-mode switch
 * re-keys the result grid so cards replay a staggered fade-up animation.
 * When the backend stamps a failure with code 1006 (cookie missing/expired),
 * a warning alert offers a shortcut to the cookie settings drawer.
 */
export function ParserWorkspace(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const mediaMode = useAppStore((state) => state.mediaMode);

  const input = useParserStore((state) => state.input);
  const status = useParserStore((state) => state.status);
  const error = useParserStore((state) => state.error);
  const results = useParserStore((state) => state.results);
  const failed = useParserStore((state) => state.failed);
  const setInput = useParserStore((state) => state.setInput);
  const parse = useParserStore((state) => state.parse);
  const reset = useParserStore((state) => state.reset);

  const submitting = useDownloadsStore((state) => state.submitting);
  const submitDownload = useDownloadsStore((state) => state.submit);
  const openPreview = usePreviewStore((state) => state.openPreview);

  const visibleResults = useMemo(() => selectVisibleResults(results, mediaMode), [results, mediaMode]);
  const isLoading = status === 'loading';
  const modeName = t(mediaMode === 'video' ? 'header.modeVideo' : 'header.modeMusic');

  // Count the URLs extracted from whatever is in the input (share cards, TXT
  // batches, plain links). Show the batch Tag only when there is more than
  // one link — a single URL needs no hint.
  const extractedCount = useMemo(() => {
    const count = extractUrls(input).length;
    return count > 1 ? count : null;
  }, [input]);

  const handleParse = async () => {
    await parse();
  };

  const handleReset = () => {
    reset();
  };

  const handleTxtImport: UploadProps['beforeUpload'] = (file) => {
    // Plain-text batch import only (security). On the picker (input-change)
    // path rc-upload filters with `!directory || attrAccept(...)`, which
    // passes EVERY file when `directory` is unset — so the isTxt guard below
    // is load-bearing there and rejects non-TXT files here. Only the
    // drag-drop path pre-filters by accept (where this guard is
    // defense-in-depth). The size guard is always load-bearing: accept never
    // limits file size.
    const isTxt = file.name.toLowerCase().endsWith('.txt') || file.type === 'text/plain';
    if (!isTxt) {
      void message.error(t('parser.txtRejected'));
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_TXT_IMPORT_BYTES) {
      void message.error(t('parser.txtTooLarge'));
      return Upload.LIST_IGNORE;
    }
    void readTxtFile(file)
      .then((text) => setInput(text))
      .catch(() => void message.error(t('parser.txtReadFailed')));
    return false; // never upload — the lines land in the search input instead
  };

  const handlePreview = (result: ParseResult) => {
    openPreview(result);
  };

  const handleDownload = async (result: ParseResult, options: DownloadOptions) => {
    // 下载 = 前端下载到本地: if the file is already downloaded server-side
    // with a valid link, open it directly (the browser saves it locally) —
    // an earlier explicit download may make this the instant path.
    const item = useDownloadsStore
      .getState()
      .items.find(
        (i) => i.task_id === result.task_id && i.status === 'completed' && i.download_url != null,
      );
    if (item) {
      const url = item.download_url;
      if (url && item.token_expire_at && Date.parse(item.token_expire_at) > Date.now()) {
        window.open(downloadApi.getFileUrl(url), '_blank', 'noopener');
        return;
      }
      // 5-minute token expired: refresh the link in the background, then let
      // the user click again (or use the drawer's refresh action).
      useDownloadsStore.getState().refreshFileLink(item.download_id);
      void message.info(t('downloads.linkExpired'));
      return;
    }
    try {
      await submitDownload(result.task_id, { ...options, title: result.title });
      void message.success(t('parser.downloadStarted'));
    } catch (err) {
      void message.error(getErrorMessage(err));
    }
  };

  const handleDownloadImage = (result: ParseResult, index: number) => {
    // 下载当前: the backend proxies the image as an attachment — same-origin,
    // no CDN referer issues; the browser saves the file directly.
    window.open(mediaApi.imageUrl(result.task_id, index), '_blank', 'noopener');
  };

  const handleDownloadAlbum = (result: ParseResult) => {
    // 下载全部: the backend bundles the album into a ZIP attachment.
    window.open(mediaApi.albumZipUrl(result.task_id), '_blank', 'noopener');
  };

  const hasOutput = status === 'success';

  return (
    <div className="parser-workspace" data-testid="parser-workspace" data-mode={mediaMode}>
      <div className="parser-input-area">
        <Typography.Text
          key={mediaMode}
          type="secondary"
          className="mode-hint"
          data-testid="mode-hint"
        >
          {t('parser.modeHint', { mode: modeName })}
        </Typography.Text>

        <Space.Compact block size={isMobile ? 'middle' : 'large'} className="parser-search-compact">
          <Input.Search
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onSearch={(_value, _event, info) => {
              // antd fires onSearch with source 'clear' when the allowClear ✖
              // is clicked — that must only clear the field, never parse.
              if (info?.source !== 'clear') {
                handleParse();
              }
            }}
            placeholder={t('parser.placeholder')}
            allowClear
            loading={isLoading}
            enterButton={isMobile ? <ScanOutlined /> : t('parser.parse')}
            aria-label={t('parser.placeholder')}
            data-testid="url-input"
          />
          <Upload
            accept=".txt,text/plain"
            showUploadList={false}
            beforeUpload={handleTxtImport}
            disabled={isLoading}
          >
            <Button
              icon={<FileTextOutlined />}
              disabled={isLoading}
              title={t('parser.txtHint')}
              aria-label={t('parser.importTxt')}
              data-testid="import-txt-button"
            />
          </Upload>
        </Space.Compact>

        <Space className="parser-toolbar" wrap>
          <Button
            size="small"
            icon={<ClearOutlined />}
            onClick={handleReset}
            disabled={!input && !hasOutput}
            data-testid="clear-button"
          >
            {t('parser.clear')}
          </Button>
          {extractedCount != null && (
            <Tag color="blue" icon={<FileTextOutlined />} data-testid="extracted-count">
              {t('parser.extractedCount', { count: extractedCount })}
            </Tag>
          )}
        </Space>
      </div>

      {status === 'error' && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button size="small" onClick={handleParse} data-testid="parse-retry">
              {t('parser.retry')}
            </Button>
          }
          data-testid="parse-error"
        />
      )}

      {hasOutput && (
        <>
          <div className="parser-summary" data-testid="parser-summary">
            <Typography.Text type="secondary">
              {/* Counts the mode-visible cards, matching the grid below. */}
              {t('parser.summary', { ok: visibleResults.length, failed: failed.length })}
            </Typography.Text>
          </div>

          {failed.some((item) => item.code === ApiCodes.COOKIE_ERROR) && (
            <Alert
              type="warning"
              showIcon
              closable
              message={t('parser.cookieAlert')}
              action={
                <Button
                  size="small"
                  onClick={() => useCookieStore.getState().openDrawer()}
                  data-testid="cookie-settings-link"
                >
                  {t('parser.goSettings')}
                </Button>
              }
              data-testid="cookie-alert"
            />
          )}

          {failed.length > 0 && (
            <div className="parser-failed" data-testid="parser-failed">
              <Typography.Text strong>{t('parser.failedTitle')}</Typography.Text>
              <ul className="parser-failed-list">
                {failed.map((item, index) => (
                  // url + index: the same URL can appear more than once in a batch.
                  <li key={`${item.url}-${index}`} className="parser-failed-item">
                    <Typography.Text code>{item.url}</Typography.Text>
                    <Typography.Text type="danger"> — {item.error}</Typography.Text>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {visibleResults.length > 0 ? (
            <Row
              key={mediaMode}
              gutter={[16, 16]}
              className="parser-grid"
              data-testid="parser-grid"
              data-mode={mediaMode}
            >
              {visibleResults.map((result, index) => (
                <Col
                  key={result.task_id}
                  xs={24}
                  sm={12}
                  md={12}
                  lg={8}
                  className="parser-grid-item"
                  style={{ animationDelay: `${Math.min(index, 12) * 45}ms` }}
                >
                  <ResultCard
                    result={result}
                    downloading={Boolean(submitting[result.task_id])}
                    onPreview={handlePreview}
                    onDownload={handleDownload}
                    onDownloadImage={handleDownloadImage}
                    onDownloadAlbum={handleDownloadAlbum}
                  />
                </Col>
              ))}
            </Row>
          ) : (
            <div className="parser-empty" data-testid="parser-empty-mode">
              <Typography.Text type="secondary">
                {results.length > 0 ? t('parser.noResultsInMode') : t('parser.noResults')}
              </Typography.Text>
            </div>
          )}
        </>
      )}

      {status === 'idle' && (
        <div className="parser-empty" data-testid="parser-empty">
          <Typography.Text type="secondary">{t('parser.noResults')}</Typography.Text>
        </div>
      )}
    </div>
  );
}
