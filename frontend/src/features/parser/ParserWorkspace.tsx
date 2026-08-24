import { useMemo } from 'react';
import { Alert, App, Button, Col, Grid, Input, Row, Space, Tag, Typography, Upload } from 'antd';
import { ClearOutlined, FileTextOutlined, ScanOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { useTranslation } from '../../services/i18n';
import { useAppStore } from '../../stores/appStore';
import { useDownloadsStore } from '../../stores/downloadsStore';
import { getErrorMessage } from '../../services/apiClient';
import { ResultCard, type DownloadOptions } from './ResultCard';
import { extractUrls, selectVisibleResults, useParserStore } from './parserStore';
import { usePreviewStore } from './previewStore';
import type { ParseResult } from '../../types/api';

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
 * covers 输入 + 搜索 + 加载txt. Because a single-line input cannot hold line
 * breaks, multi-line batches are TXT-import only: the store still keeps the
 * raw multi-line text (extractUrls splits on line endings) and a Tag reports
 * the imported count. The header media-mode switch re-keys the result grid so
 * cards replay a staggered fade-up animation (see Task 3).
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

  // A single-line input cannot contain line breaks, so any '\n' in the store
  // input means a TXT import happened; report the batch size to the user.
  const importedCount = useMemo(() => {
    if (!input.includes('\n')) return null;
    return extractUrls(input).length;
  }, [input]);

  const handleParse = () => {
    void parse();
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
    try {
      await submitDownload(result.task_id, { ...options, title: result.title });
      void message.success(t('parser.downloadStarted'));
    } catch (err) {
      void message.error(getErrorMessage(err));
    }
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
          {importedCount != null && importedCount > 0 && (
            <Tag color="orange" icon={<FileTextOutlined />} data-testid="imported-count">
              {t('parser.batchImported', { count: importedCount })}
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
                  md={8}
                  lg={6}
                  className="parser-grid-item"
                  style={{ animationDelay: `${Math.min(index, 12) * 45}ms` }}
                >
                  <ResultCard
                    result={result}
                    downloading={Boolean(submitting[result.task_id])}
                    onPreview={handlePreview}
                    onDownload={handleDownload}
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
