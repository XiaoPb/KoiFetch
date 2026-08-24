import { useState } from 'react';
import { App, Button, Card, Form, Input, Typography } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { useNavigate, Link } from 'react-router-dom';
import { useTranslation } from '../services/i18n';
import { useAuthStore } from '../stores/authStore';
import { getErrorMessage } from '../services/apiClient';

interface LoginFormValues {
  username: string;
  password: string;
}

/**
 * Admin login page (PRD §3.4): renders standalone (no app header). Submits to
 * POST /api/auth/login via the auth store and navigates home on success.
 */
export default function LoginPage(): JSX.Element {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const login = useAuthStore((state) => state.login);
  const [submitting, setSubmitting] = useState(false);

  const onFinish = async (values: LoginFormValues) => {
    setSubmitting(true);
    try {
      await login(values.username, values.password);
      void message.success(t('login.success'));
      navigate('/', { replace: true });
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
