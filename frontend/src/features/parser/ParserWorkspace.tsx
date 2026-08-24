import { useMemo } from 'react';
import { Alert, App, Button, Col, Input, Row, Space, Spin, Typography, Upload } from 'antd';
import { ClearOutlined, CloudUploadOutlined, ScanOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { useTranslation } from '../../services/i18n';
import { useAppStore } from '../../stores/appStore';
import { useDownloadsStore } from '../../stores/downloadsStore';
import { getErrorMessage } from '../../services/apiClient';
import { ResultCard, type DownloadOptions } from './ResultCard';
import { selectVisibleResults, useParserStore } from './parserStore';
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
 * Parser workspace (PRD §4.2): URL batch input (paste or TXT import),
 * media-mode hint, submit/loading/error states, the result-card grid, the
 * failed-URL section, and the preview/download actions.
 *
 * The preview action only records the clicked result in previewStore — the
 * preview Modal itself lands in Task 15. The download action submits through
 * downloadsStore so the Task 15 download-center drawer/badge sees the task.
 */
export function ParserWorkspace(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
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

  const handleParse = () => {
    void parse();
  };

  const handleReset = () => {
    reset();
  };

  const handleTxtImport: UploadProps['beforeUpload'] = (file) => {
    // Plain-text batch import only (security). rc-upload already drops files
    // that do not match the `accept` prop, so the isTxt check below is
    // defense-in-depth; the size guard is the load-bearing one (accept does
    // not limit file size).
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
    return false; // never upload — the lines land in the textarea instead
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
    <div className="parser-workspace" data-testid="parser-workspace">
      <div className="parser-input-area">
        <Typography.Text type="secondary" data-testid="mode-hint">
          {t('parser.modeHint', { mode: modeName })}
        </Typography.Text>
        <Input.TextArea
          rows={5}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={t('parser.placeholder')}
          aria-label={t('parser.placeholder')}
          data-testid="url-input"
        />
        <Space wrap>
          <Button
            type="primary"
            icon={<ScanOutlined />}
            loading={isLoading}
            onClick={handleParse}
            data-testid="parse-button"
          >
            {t('parser.parse')}
          </Button>
          <Upload
            accept=".txt,text/plain"
            showUploadList={false}
            beforeUpload={handleTxtImport}
            disabled={isLoading}
          >
            <Button
              icon={<CloudUploadOutlined />}
              disabled={isLoading}
              title={t('parser.txtHint')}
              data-testid="import-txt-button"
            >
              {t('parser.importTxt')}
            </Button>
          </Upload>
          <Button icon={<ClearOutlined />} onClick={handleReset} disabled={!input && !hasOutput} data-testid="clear-button">
            {t('parser.clear')}
          </Button>
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

      {isLoading && (
        <div className="parser-loading" data-testid="parser-loading">
          <Spin size="large" />
          <Typography.Text type="secondary">{t('parser.loading')}</Typography.Text>
        </div>
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
            <Row gutter={[16, 16]} className="parser-grid" data-testid="parser-grid">
              {visibleResults.map((result) => (
                <Col key={result.task_id} xs={24} sm={12} md={8} lg={6}>
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
