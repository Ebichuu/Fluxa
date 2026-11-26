import { useState, type FormEvent } from 'react';
import { Tv } from 'lucide-react';
import { api } from '../services/api';

interface LoginPageProps {
  initialized: boolean;
  onSuccess: () => void;
}

export function LoginPage({ initialized, onSuccess }: LoginPageProps) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      if (initialized) {
        await api.login(username, password);
      } else {
        await api.setupAdmin(username, password);
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <form className="auth-card stack" onSubmit={handleSubmit}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Tv size={22} color="var(--accent)" />
          <div>
            <h1 style={{ margin: 0, fontSize: 18 }}>Fluxa v2</h1>
            <div className="muted">{initialized ? '登录你的影音总控入口' : '首次启动：创建管理员账号'}</div>
          </div>
        </div>
        {error && <div className="banner error">{error}</div>}
        <div className="field">
          <label>管理员账号</label>
          <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} required minLength={3} />
        </div>
        <div className="field">
          <label>密码（至少 6 位）</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={6} autoFocus />
        </div>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? '处理中…' : initialized ? '登录' : '创建并进入 Fluxa'}
        </button>
      </form>
    </div>
  );
}
