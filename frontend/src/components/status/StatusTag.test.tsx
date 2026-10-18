import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import StatusTag from './StatusTag'
import type { TaskStatus } from '../../api/types'

const CASES: Array<[TaskStatus, string]> = [
  ['pending', '排队中'],
  ['running', '推理中'],
  ['awaiting_review', '待复核'],
  ['completed', '已完成'],
  ['failed', '失败'],
  ['canceled', '已取消'],
]

describe('StatusTag', () => {
  it.each(CASES)('渲染 %s 状态标签', (status, label) => {
    render(<StatusTag status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })
})
