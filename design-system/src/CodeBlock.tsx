import * as React from 'react';
import './theme.css';
import './components.css';
import { Button } from './Button';

export interface CodeBlockProps {
  /** 문자열 또는 객체(객체면 JSON 포맷) */
  content: string | object;
  /** 우상단 복사 버튼 */
  copyable?: boolean;
  maxHeight?: number;
}

/** 코드/JSON 뷰어. 객체는 자동 포맷, 복사 버튼 옵션. */
export function CodeBlock({ content, copyable = true, maxHeight }: CodeBlockProps) {
  const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      });
    }
  };
  return (
    <pre className="ds-code" style={maxHeight ? { ['--ds-code-max' as string]: `${maxHeight}px` } : undefined}>
      {copyable && (
        <span className="ds-code__copy">
          <Button variant="ghost" onClick={copy}>
            {copied ? '복사됨' : '복사'}
          </Button>
        </span>
      )}
      <code>{text}</code>
    </pre>
  );
}

/** JSON 전용 별칭. */
export function JsonViewer({ data, ...rest }: { data: object } & Omit<CodeBlockProps, 'content'>) {
  return <CodeBlock content={data} {...rest} />;
}
