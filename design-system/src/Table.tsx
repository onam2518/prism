import * as React from 'react';
import './theme.css';
import './components.css';

export interface TableColumn<T> {
  key: keyof T & string;
  header: string;
  /** 셀 커스텀 렌더 */
  render?: (row: T) => React.ReactNode;
}

export interface TableProps<T> extends React.TableHTMLAttributes<HTMLTableElement> {
  columns: TableColumn<T>[];
  data: T[];
  /** 행 key 추출 (기본: index) */
  rowKey?: (row: T, index: number) => string | number;
  /** 행 우측 액션 셀 (편집·삭제 등). 지정 시 액션 컬럼 추가 */
  rowActions?: (row: T, index: number) => React.ReactNode;
  /** 액션 컬럼 헤더 (기본: 빈칸) */
  actionsHeader?: string;
}

/** Prism 테이블. 에디토리얼 · 조용한 헤더 라벨 + 헤어라인 행, hover 시 tint. */
export function Table<T extends Record<string, unknown>>({
  columns,
  data,
  rowKey,
  rowActions,
  actionsHeader = '',
  className = '',
  ...props
}: TableProps<T>) {
  return (
    <table className={`ds-table ${className}`.trim()} {...props}>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key} scope="col">
              {c.header}
            </th>
          ))}
          {rowActions && (
            <th scope="col" style={{ textAlign: 'right' }}>
              {actionsHeader}
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {data.map((row, i) => (
          <tr key={rowKey ? rowKey(row, i) : i}>
            {columns.map((c) => (
              <td key={c.key}>{c.render ? c.render(row) : (row[c.key] as React.ReactNode)}</td>
            ))}
            {rowActions && (
              <td>
                <div className="ds-table__actions">{rowActions(row, i)}</div>
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
