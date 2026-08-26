import { Modal } from 'antd';
import { useTranslation } from '../../services/i18n';

export interface VideoPlayerModalProps {
  /** Download title shown in the modal header. */
  title: string | null;
  /**
   * Tokenized file URL (`/api/download/file/{id}?token=…`) — same-origin, so
   * the native `<video>` plays it directly with no CORS work.
   */
  src: string;
  open: boolean;
  onClose: () => void;
}

/**
 * Plays a completed download's file in a native `<video>` element.
 *
 * The source is the tokenized same-origin URL captured from the WS `complete`
 * event (one-time token, ~5-minute validity). `destroyOnHidden` unmounts the
 * element when the modal closes, which stops the media stream — so the token
 * is never consumed by a backgrounded player.
 */
export function VideoPlayerModal({ title, src, open, onClose }: VideoPlayerModalProps): JSX.Element {
  const { t } = useTranslation();
  return (
    <Modal
      open={open}
      onCancel={onClose}
      title={title ?? t('downloads.unknownTitle')}
      footer={null}
      width={760}
      destroyOnHidden
      data-testid="video-player-modal"
    >
      <video
        controls
        src={src}
        style={{ width: '100%', maxHeight: 440 }}
        data-testid="video-player"
      />
    </Modal>
  );
}
