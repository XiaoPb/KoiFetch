import { useEffect, useState } from 'react';
import { Alert, App, Button, Card, Form, Input, Typography } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from '../services/i18n';
import { isExpired, useAuthStore } from '../stores/authStore';
import { logoutSession } from '../services/session';
import { getErrorMessage } from '../services/apiClient';

interface LoginFormValues {
  username: string;
  password: string;
}

interface LoginLocationState {
  from?: string;
}

/**
 * Admin login page (PRD §3.4): renders standalone (no app header). Submits to
 * POST /api/auth/login via the auth store and navigates back to the page the
 * user originally requested (or home) on success.
 *
 * Expired-session handling (Task 16): when the page mounts with a rehydrated
 * session whose token already expired (e.g. ProtectedRoute sent an
 * authenticated-but-expired visitor here), it shows a clear "会话已过期" notice
 * and runs the full logout (auth session + session-local download state) so
 * the next login starts clean.
 */
export default function LoginPage(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const login = useAuthStore((state) => state.login);
  const token = useAuthStore((state) => state.token);
  const expiresAt = useAuthStore((state) => state.expiresAt);
  const [submitting, setSubmitting] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);

  useEffect(() => {
    if (token && isExpired(expiresAt)) {
      setSessionExpired(true);
      // Same single logout entry point as the header: also tears down the
      // downloads state so stale items / live sockets never survive a session
      // boundary (e.g. an expired session redirecting here from /nas).
      logoutSession();
    }
  }, [token, expiresAt]);

  const from = (location.state as LoginLocationState | null)?.from ?? '/';

  const onFinish = async (values: LoginFormValues) => {
    setSubmitting(true);
    try {
      await login(values.username, values.password);
      void message.success(t('login.success'));
      navigate(from, { replace: true });
    } catch (error) {
      void message.error(getErrorMessage(error, t('login.failed')));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page" data-testid="login-page">
      <Card className="login-card">
        <Typography.Title level={3} className="login-title">
          🎏 {t('login.title')}
        </Typography.Title>
        {sessionExpired && (
          <Alert
            type="warning"
            showIcon
            message={t('login.expired')}
            style={{ marginBottom: 16 }}
            data-testid="login-expired"
          />
        )}
        <Form<LoginFormValues> layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item
            name="username"
            rules={[{ required: true, message: t('login.username') }]}
          >
            <Input prefix={<UserOutlined />} placeholder={t('login.username')} autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: t('login.password') }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('login.password')}
              autoComplete="current-password"
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            {t('login.submit')}
          </Button>
          <div className="login-back">
            <Link to="/">← {t('home.title')}</Link>
          </div>
        </Form>
      </Card>
    </div>
  );
}
