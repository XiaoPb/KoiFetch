import { describe, expect, it } from 'vitest';
import { usePreviewStore } from './previewStore';
import type { ParseResult } from '../../types/api';

// Task 15 seam contract (see previewStore docstring): the parser workspace
// sets `activeTask` on [预览]; Task 15 renders the preview Modal from it.

const task: ParseResult = {
  task_id: 't1',
  url: 'https://example.com/v/a',
  type: 'video',
  platform: 'douyin',
  title: 'Video A',
  cover: null,
  duration: '01:23',
  file_size_mb: 12.5,
  format: 'mp4',
  available_qualities: ['1080p'],
  available_bitrates: [],
};

describe('previewStore', () => {
  it('opens with the clicked parse result', () => {
    usePreviewStore.setState({ activeTask: null });
    usePreviewStore.getState().openPreview(task);
    expect(usePreviewStore.getState().activeTask).toEqual(task);
  });

  it('close clears the active task so the modal unmounts', () => {
    usePreviewStore.setState({ activeTask: task });
    usePreviewStore.getState().closePreview();
    expect(usePreviewStore.getState().activeTask).toBeNull();
  });

  it('starts closed', () => {
    usePreviewStore.setState({ activeTask: null });
    expect(usePreviewStore.getState().activeTask).toBeNull();
  });
});
