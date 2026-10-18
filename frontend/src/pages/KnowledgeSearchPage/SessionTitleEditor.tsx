import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

interface SessionTitleEditorProps {
  sessionId?: string
  name?: string
  streaming: boolean
  onRename: (sessionId: string, name: string) => Promise<unknown>
}

function renameErrorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : '会话名称保存失败，请重试'
}

export function SessionTitleEditor({
  sessionId,
  name = '知识库对话',
  streaming,
  onRename,
}: SessionTitleEditorProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const editButtonRef = useRef<HTMLButtonElement>(null)
  const requestVersion = useRef(0)

  useEffect(() => {
    requestVersion.current += 1
    setEditing(false)
    setSaving(false)
    setError(null)
  }, [sessionId])

  useEffect(() => {
    if (!editing) setDraft(name)
  }, [editing, name])

  useEffect(() => {
    if (!editing) return
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [editing])

  const beginEdit = (): void => {
    if (!sessionId || streaming) return
    setDraft(name)
    setError(null)
    setEditing(true)
  }

  const cancelEdit = (): void => {
    if (saving) return
    setDraft(name)
    setError(null)
    setEditing(false)
    requestAnimationFrame(() => editButtonRef.current?.focus())
  }

  const save = async (event?: FormEvent): Promise<void> => {
    event?.preventDefault()
    if (!sessionId || saving) return
    const nextName = draft.trim()
    if (!nextName) {
      setError('请输入会话名称')
      inputRef.current?.focus()
      return
    }
    if (nextName === name) {
      cancelEdit()
      return
    }

    const version = ++requestVersion.current
    setSaving(true)
    setError(null)
    try {
      await onRename(sessionId, nextName)
      if (version !== requestVersion.current) return
      setEditing(false)
      requestAnimationFrame(() => editButtonRef.current?.focus())
    } catch (err) {
      if (version !== requestVersion.current) return
      setError(renameErrorMessage(err))
      inputRef.current?.focus()
    } finally {
      if (version === requestVersion.current) setSaving(false)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.nativeEvent.isComposing) return
    if (event.key === 'Enter') {
      event.preventDefault()
      void save()
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      cancelEdit()
    }
  }

  if (editing) {
    return (
      <form className="chat-session-title chat-session-title--editing" noValidate onSubmit={(event) => void save(event)}>
        <div className="chat-session-title__form-row">
          <label htmlFor="active-session-name">会话名称</label>
          <input
            ref={inputRef}
            id="active-session-name"
            value={draft}
            maxLength={255}
            aria-invalid={Boolean(error)}
            aria-describedby="active-session-name-status"
            disabled={saving}
            onChange={(event) => { setDraft(event.target.value); setError(null) }}
            onKeyDown={onKeyDown}
          />
          <button type="submit" className="chat-session-title__save" disabled={saving} aria-busy={saving}>
            {saving ? '保存中' : '保存'}
          </button>
          <button type="button" className="chat-session-title__cancel" disabled={saving} onClick={cancelEdit}>取消</button>
        </div>
        <span id="active-session-name-status" className={error ? 'is-error' : ''} role={error ? 'alert' : 'status'}>
          {error || 'Enter 保存 · Esc 取消'}
        </span>
      </form>
    )
  }

  const editLabel = sessionId ? `重命名会话“${name}”` : '暂无可重命名的会话'
  return (
    <div className="chat-session-title">
      <div className="chat-session-title__display">
        <strong title={name}>{name}</strong>
        <button
          ref={editButtonRef}
          type="button"
          className="chat-session-title__edit"
          aria-label={editLabel}
          title={streaming ? '回答生成完成后可重命名' : '重命名会话'}
          disabled={!sessionId || streaming}
          onClick={beginEdit}
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z" />
          </svg>
        </button>
      </div>
      <span>{streaming ? '正在生成回答…' : '已保存至 RAGFlow'}</span>
    </div>
  )
}
