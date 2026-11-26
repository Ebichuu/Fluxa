import { AlertTriangle } from 'lucide-react';
import type { ActionImpactPreview } from '../types/api';

interface ConfirmImpactModalProps {
  preview: ActionImpactPreview;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmImpactModal({ preview, busy, onCancel, onConfirm }: ConfirmImpactModalProps) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <AlertTriangle size={20} color={preview.riskLevel === 'high' ? 'var(--err)' : 'var(--warn)'} />
          <h3 style={{ margin: 0, fontSize: 16 }}>{preview.title}</h3>
        </div>
        <p style={{ margin: 0, fontSize: 13.5 }}>{preview.impactSummary}</p>
        <div className="impact-list">
          {preview.affectedItems.map((row, idx) => (
            <div className="impact-row" key={idx}>
              <span className="muted">{row.label}</span>
              <strong style={{ textAlign: 'right', wordBreak: 'break-all' }}>{row.value}</strong>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn" type="button" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            className={`btn ${preview.riskLevel === 'high' ? 'btn-danger' : 'btn-primary'}`}
            type="button"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? '执行中…' : '确认执行此单项操作'}
          </button>
        </div>
      </div>
    </div>
  );
}
