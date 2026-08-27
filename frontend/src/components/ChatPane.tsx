import { useEffect, useRef, type KeyboardEvent } from 'react'
import type { ChatMessage } from '../types'

const SUGGESTIONS = [
  'What tables are on the active source?',
  'How many real non-test customers do we have?',
  'Set product 1 price to 99.99',
]

interface ChatPaneProps {
  messages: ChatMessage[]
  draft: string
  busy: boolean
  sessionId: string | null
  onDraftChange: (value: string) => void
  onSend: (text?: string) => void
  onReset: () => void
}

export function ChatPane({
  messages,
  draft,
  busy,
  sessionId,
  onDraftChange,
  onSend,
  onReset,
}: ChatPaneProps) {
  const endRef = useRef<HTMLDivElement | null>(null)
  const areaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, busy])

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (!busy && draft.trim()) onSend()
    }
  }

  return (
    <section className="panel chat-panel" aria-label="Chat">
      <div className="panel-header">
        <h2>Conversation</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {sessionId ? (
            <span className="meta" title={sessionId}>
              {sessionId.slice(0, 8)}…
            </span>
          ) : (
            <span className="meta">new session</span>
          )}
          <button type="button" className="btn btn-ghost" onClick={onReset}>
            Reset
          </button>
        </div>
      </div>

      <div className="chat-stream">
        {messages.length === 0 ? (
          <div className="empty-chat">
            <h3>Ask, then review.</h3>
            <p>
              Query the active source, or propose a change. Bounded writes can
              apply on their own. Broader ones wait in Preview.
            </p>
            <ul className="prompt-list">
              {SUGGESTIONS.map((prompt) => (
                <li key={prompt}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onSend(prompt)}
                  >
                    {prompt}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          messages.map((message) => (
            <article key={message.id} className={`turn ${message.role}`}>
              <div className="role">
                {message.role === 'user' ? 'You' : 'Sentinel'}
              </div>
              <p className="body">{message.content}</p>
            </article>
          ))
        )}

        {busy ? (
          <div className="thinking" aria-live="polite">
            <span className="dot" />
            Rehearsing against the database…
          </div>
        ) : null}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault()
          onSend()
        }}
      >
        <textarea
          ref={areaRef}
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about the active database, or propose a change…"
          disabled={busy}
          rows={3}
          aria-label="Message"
        />
        <div className="composer-actions">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy || !draft.trim()}
          >
            {busy ? 'Working' : 'Send'}
          </button>
        </div>
      </form>
    </section>
  )
}
