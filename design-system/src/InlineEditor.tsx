import * as React from 'react';
import './theme.css';
import './components.css';
import { Button } from './Button';

export interface EditBarProps {
  onSave: () => void;
  onCancel: () => void;
  saveLabel?: string;
  cancelLabel?: string;
  /** 좌측 보조 영역(글자수 등) */
  children?: React.ReactNode;
}

/** 편집 액션 바 · 저장/취소. InlineEditor 내부 또는 단독. */
export function EditBar({ onSave, onCancel, saveLabel = '저장', cancelLabel = '취소', children }: EditBarProps) {
  return (
    <div className="ds-editbar">
      {children}
      <span className="ds-editbar__spacer" />
      <Button variant="ghost" onClick={onCancel}>
        {cancelLabel}
      </Button>
      <Button variant="primary" onClick={onSave}>
        {saveLabel}
      </Button>
    </div>
  );
}

export interface InlineEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** 여러 줄(textarea) */
  multiline?: boolean;
}

/** 인라인 편집 · 클릭하면 입력으로 전환, EditBar 로 저장/취소. */
export function InlineEditor({ value, onChange, placeholder, multiline = false }: InlineEditorProps) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(value);

  React.useEffect(() => {
    if (editing) setDraft(value);
  }, [editing, value]);

  if (!editing) {
    return (
      <div className="ds-inline-edit">
        <div
          className="ds-inline-edit__text"
          role="button"
          tabIndex={0}
          onClick={() => setEditing(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') setEditing(true);
          }}
        >
          {value || <span style={{ color: 'var(--ds-placeholder)' }}>{placeholder ?? '클릭해 편집'}</span>}
        </div>
      </div>
    );
  }

  const commit = () => {
    onChange(draft);
    setEditing(false);
  };

  return (
    <div className="ds-inline-edit">
      {multiline ? (
        <textarea
          className="ds-field"
          rows={3}
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
        />
      ) : (
        <input
          className="ds-field"
          value={draft}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit();
            if (e.key === 'Escape') setEditing(false);
          }}
        />
      )}
      <div style={{ marginTop: 8 }}>
        <EditBar onSave={commit} onCancel={() => setEditing(false)} />
      </div>
    </div>
  );
}
