import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SubmitPage from './index'

const { submitTaskMock } = vi.hoisted(() => ({ submitTaskMock: vi.fn() }))

vi.mock('../../api/tasks', () => ({ submitTask: submitTaskMock }))
vi.mock('../../hooks/useCapabilities', () => ({
  useCapabilities: () => ({
    caps: [
      {
        capability_id: 'coating_defect_v1', tool_name: 'coating_defect', tool_version: '1.0.0',
        model_version: 'v1', display_name: '涂层缺陷检出', scene: 'coating',
        capability_type: 'defect_detection', adapter_type: 'http', status: 'enabled', note: '',
      },
    ],
    health: null, loading: false, error: null, refreshing: false, refresh: vi.fn(), reload: vi.fn(),
  }),
}))

/** 目录里长出第七个场景：它还没进自动识别关键词表（决策 52 的边界）。 */
vi.mock('../../api/scenes', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/scenes')>()
  return {
    ...actual,
    fetchScenes: vi.fn(async () => [
      {
        scene: 'coating', label: '涂层检测', short: '涂层', en: 'COATING',
        params: 'draft', input: '仅需检测图像', auto_recognize: false,
      },
      {
        scene: 'fpi', label: '荧光渗透检测', short: '荧光', en: 'FPI',
        params: 'frozen', input: '仅需检测图像 · 暗场', auto_recognize: true,
      },
    ]),
  }
})

function makeFile(name: string): File {
  return new File(['x'], name)
}

describe('提交页 · 场景目录取数（决策 52）', () => {
  it('第七个场景出现在场景网格里，卡片数不再写死六个', async () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/tasks/new']}>
        <SubmitPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(container.querySelectorAll('.scene-card').length).toBe(2))
    expect(container.querySelector('.scene-card[data-code="coating"]')).not.toBeNull()
  })

  it('未进关键词表的场景写明"只能显式选择"，选中后仍按该 scene 提交', async () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/tasks/new']}>
        <SubmitPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(container.querySelectorAll('.scene-card').length).toBe(2))
    const card = container.querySelector<HTMLElement>('.scene-card[data-code="coating"]')!
    expect(within(card).getByText(/自动识别不覆盖此场景/)).not.toBeNull()

    fireEvent.click(card)
    const inputs = container.querySelectorAll<HTMLInputElement>('input[type="file"]')
    fireEvent.change(inputs[0], { target: { files: [makeFile('a.jpg')] } })
    fireEvent.click(screen.getByText('提交检测任务'))
    await waitFor(() => expect(submitTaskMock).toHaveBeenCalledTimes(1))
    expect(submitTaskMock.mock.calls[0][0].scene).toBe('coating')
  })

  it('目录带入的新场景可直接深链锁定：?scene=coating', async () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/tasks/new?scene=coating']}>
        <SubmitPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(container.querySelector('.locked-scene')).not.toBeNull())
    expect(container.querySelector('.locked-scene')?.textContent).toContain('涂层检测')
  })
})
