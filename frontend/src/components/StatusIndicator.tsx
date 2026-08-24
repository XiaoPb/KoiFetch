import { useEffect, useState } from 'react';
import { healthApi } from '../services/api';
import { useTranslation } from '../services/i18n';
import type { HealthStatus } from '../stores/appStore';

const POLL_INTERVAL_MS = 30_000;

/**
 * Backend status indicator (PRD header: 🟢 在线). Checks /api/health on mount
 * and every 30s; shows online (green) only when the backend reports full
 * readiness (code 0, status "ok"), offline otherwise.
 */
export function StatusIndicator(): JSX.Element {
  const { t } = useTranslation();
  const [status, setStatus] = useState<HealthStatus>('unknown');

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const data = await healthApi.getHealth();
        if (alive) setStatus(data.status === 'ok' ? 'online' : 'offline');
      } catch {
        if (alive) setStatus('offline');
      }
    };
    void check();
    const timer = setInterval(() => void check(), POLL_INTERVAL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const label =
    status === 'online' ? t('header.online') : status === 'offline' ? t('header.offline') : t('header.statusUnknown');

  return (
    <span className={`status-indicator status-${status}`} data-testid="status-indicator">
      <span className="status-dot" aria-hidden="true" />
      {label}
    </span>
  );
}
