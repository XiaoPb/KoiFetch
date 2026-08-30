import { Modal } from 'antd';
import ReactPlayer from 'react-player';
import { useTranslation } from '../../services/i18n';

export interface VideoPlayerModalProps {
  /** Download title shown in the modal header. */
  title: string | null;
  /**
   * Tokenized file URL (`/api/download/file/{id}?token=…`) — same-origin, so
   * react-player plays it directly with no CORS work.
   */
  src: string;
  open: boolean;
  onClose: () => void;
}

/**
 * Plays a completed download's file via react-player (native HTML5 media
 * underneath, with a wider container/format fallback surface than a bare
 * `<video>`).
 *
 * The source is the tokenized same-origin URL captured from the WS `complete`
 * event (short-lived reusable token, ~5-minute validity). `destroyOnHidden`
 * unmounts the player when the modal closes, which stops the media stream;
 * repeated GET/Range requests remain valid until the token expires.
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
      <div style={{ aspectRatio: '16 / 9', maxHeight: 440 }} data-testid="video-player">
        <ReactPlayer src={src} controls width="100%" height="100%" />
      </div>
    </Modal>
  );
}
