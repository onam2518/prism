import * as React from 'react';
import './theme.css';
import './components.css';

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: string[];
  invalid?: boolean;
}

/** Prism 드롭다운. 정해진 값(콘텐츠 그룹·모델 등) 입력에 사용. */
export function Select({ label, options, invalid = false, className = '', id, ...props }: SelectProps) {
  const selectId = id || (label ? `ds-${label}` : undefined);
  const cls = ['ds-field', invalid && 'ds-field--invalid', className].filter(Boolean).join(' ');
  return (
    <div>
      {label && (
        <label className="ds-label" htmlFor={selectId}>
          {label}
        </label>
      )}
      <select id={selectId} className={cls} aria-invalid={invalid || undefined} {...props}>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}
