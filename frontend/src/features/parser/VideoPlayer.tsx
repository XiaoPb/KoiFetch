import { useEffect, useRef, useState } from 'react';
import { Typography } from 'antd';
import Player from 'xgplayer';
// xgplayer v3 plugin classes (verified in Task 4 Step 3): they must be listed
// in the config `plugins` array — importing them does NOT self-register.
import FlvPlugin from 'xgplayer-flv';
import HlsPlugin from 'xgplayer-hls';
import { useTranslation } from '../../services/i18n';

export interface PlayableSource {
  /** Playable URL: same-origin proxy, direct CDN, or tokenized file. */
  url: string;
  /** Media format ("mp4" | "flv" | "m3u8" | null) — selects the plugin. */
  format: string | null;
}

export interface VideoPlayerProps {
  /** Ordered playback candidates; the player advances on `error`. */
  sources: PlayableSource[];
  poster?: string | null;
  testId?: string;
}

/**
 * xgplayer config per source format: native <video> / flv plugin / hls plugin.
 * v3 shape (verified): plugins registered via `plugins`, cross-origin fetches
 * via `fetchOptions: { mode: 'cors' }`.
 */
function playerConfigFor(
  el: HTMLElement,
  source: PlayableSource,
  poster?: string | null,
): Record<string, unknown> {
  const format = (source.format ?? '').toLowerCase();
  const plugins =
    format === 'flv' ? [FlvPlugin] : format === 'm3u8' || format === 'hls' ? [HlsPlugin] : [];
  const base: Record<string, unknown> = {
    el,
    url: source.url,
    poster: poster ?? undefined,
    fluid: true,
    width: '100%',
    height: '100%',
    playsinline: true,
    autoplay: false,
    controls: true,
    plugins,
  };
  if (format === 'flv') return { ...base, flv: { fetchOptions: { mode: 'cors' } } };
  if (format === 'm3u8' || format === 'hls') {
    return { ...base, hls: { fetchOptions: { mode: 'cors' } } };
  }
  return base;
}

/**
 * Inline xgplayer wrapper for the result grid (PRD §4.2.2 upgrade).
 *
 * Imperative lifecycle: one Player per mounted source; destroyed on unmount
 * and on source advance (a fresh Player per source avoids plugin-state
 * carry-over). The caller orders `sources` from most to least desirable —
 * e.g. [stream proxy, direct CDN URL, completed download file] — and the
 * component advances on every `error` event, ending in a visible failure hint.
 */
export function VideoPlayer({ sources, poster, testId }: VideoPlayerProps): JSX.Element {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [sourceIndex, setSourceIndex] = useState(0);
  const [failed, setFailed] = useState(false);

  const source = sources[sourceIndex] ?? null;

  // A new source list restarts the ladder from the top.
  useEffect(() => {
    setFailed(false);
    setSourceIndex(0);
  }, [sources]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !source) return;
    const player = new Player(playerConfigFor(el, source, poster));
    const onError = () => {
      if (sourceIndex + 1 < sources.length) {
        setSourceIndex(sourceIndex + 1);
      } else {
        setFailed(true);
      }
    };
    player.on('error', onError);
    return () => {
      player.off('error', onError);
      player.destroy();
    };
    // Keyed on primitives so an identical source never recreates the player.
  }, [source?.url, source?.format, poster, sourceIndex, sources.length]);

  return (
    <div
      ref={containerRef}
      className="result-card-player"
      data-testid={testId}
      style={{ aspectRatio: '16 / 9', width: '100%', background: '#000' }}
    >
      {failed && (
        <div className="result-card-player-failed" data-testid={`${testId}-failed`}>
          <Typography.Text type="secondary">{t('parser.playbackFailed')}</Typography.Text>
        </div>
      )}
    </div>
  );
}
