import * as React from 'react';
import './theme.css';
import './components.css';
import { Character, type CharacterName } from './Character';
import { Button } from './Button';
import { IconButton } from './IconButton';
import { StatusDot } from './StatusDot';

export interface ChatMessage {
  from: 'bot' | 'me';
  text: string;
}

export interface AssistantProps {
  /** FAB·헤더에 쓰는 캐릭터 */
  character?: CharacterName;
  title?: string;
  subtitle?: string;
  greeting?: string;
  quickReplies?: string[];
  /** 진행 중 작업 상태창 (있으면 캐릭터 위에 다크 pill 노출) */
  status?: { label: string; detail?: string } | null;
  /** 사용자 메시지 전송 시 호출. 반환 문자열이 봇 답변이 됨 */
  onSend?: (text: string) => string | void;
}

/** 하단 플로팅 캐릭터 버튼 + 대화 팝업(채널톡 스타일). 대화형 작업 지시 진입점. */
export function Assistant({
  character = 'boksil',
  title = 'Prism 도우미',
  subtitle = '보통 1분 내 응답',
  greeting = '무엇을 도와드릴까요? 작업을 말로 지시해 보세요.',
  quickReplies = ['신규 이미지 메타 추출', '차단 건만 보기', '품질 리포트'],
  status = null,
  onSend,
}: AssistantProps) {
  const [open, setOpen] = React.useState(false);
  const [msgs, setMsgs] = React.useState<ChatMessage[]>([{ from: 'bot', text: greeting }]);
  const [text, setText] = React.useState('');
  const bodyRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [msgs, open]);

  const send = (t?: string) => {
    const v = (t ?? text).trim();
    if (!v) return;
    setText('');
    setMsgs((m) => [...m, { from: 'me', text: v }]);
    const reply = onSend?.(v) || `알겠어요. “${v}” 작업을 큐에 넣을게요.`;
    window.setTimeout(() => setMsgs((m) => [...m, { from: 'bot', text: reply }]), 400);
  };

  return (
    <>
      {open && (
        <div className="ds-chat" role="dialog" aria-label={title}>
          <div className="ds-chat__head">
            <span className="ds-chat__av">
              <Character name={character} size={30} />
            </span>
            <div>
              <div className="ds-chat__title">{title}</div>
              <div className="ds-chat__sub">
                <StatusDot tone="ok" /> {subtitle}
              </div>
            </div>
            <span style={{ marginLeft: 'auto' }}>
              <IconButton label="닫기" size="sm" onClick={() => setOpen(false)}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </IconButton>
            </span>
          </div>

          <div className="ds-chat__body" ref={bodyRef}>
            {msgs.map((m, i) => (
              <div key={i} className={`ds-chat__msg ds-chat__msg--${m.from}`}>
                {m.text}
              </div>
            ))}
          </div>

          {quickReplies.length > 0 && (
            <div className="ds-chat__quick">
              {quickReplies.map((q) => (
                <Button key={q} variant="pill" onClick={() => send(q)}>
                  {q}
                </Button>
              ))}
            </div>
          )}

          <div className="ds-chat__foot">
            <textarea
              className="ds-chat__input"
              rows={1}
              placeholder="작업을 지시하세요…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <button className="ds-chat__send" aria-label="보내기" disabled={!text.trim()} onClick={() => send()}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M2 8h10M8 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
        </div>
      )}

      <div className="ds-fab-dock">
        {status && !open && (
          <div className="ds-fab-status" onClick={() => setOpen(true)} role="button">
            <span className="ds-fab-status__spin" />
            <div>
              <div className="ds-fab-status__t">{status.label}</div>
              {status.detail && <div className="ds-fab-status__s">{status.detail}</div>}
            </div>
          </div>
        )}
        <button className="ds-fab" aria-label={open ? '도우미 닫기' : '도우미 열기'} onClick={() => setOpen((o) => !o)}>
          <Character name={character} size={88} />
        </button>
      </div>
    </>
  );
}
