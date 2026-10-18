import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react'
import DefectEditModal from './DefectEditModal'
import { useDefectEditStore } from '../../store/useDefectEditStore'
import type { ArtifactRef, ClassDefItem, DefectItem } from '../../api/types'

const CLASS_DEFS: ClassDefItem[] = [
  { class_id: 0, class_name: 'BP', description: '模糊的真缺陷', is_true_defect: true },
  { class_id: 2, class_name: 'FI', description: '虚假荧光显示', is_true_defect: false },
]

/* Modal 内嵌完整 Viewer（Konva + 标注子系统）：本测试只验证会话接线与
   面板/底图装配，Viewer 内部行为由其自身测试覆盖 → stub */
vi.mock('../../viewer/Viewer', () => ({
  default: (props: { taskId: string; images: Array<{ key: string }>; defects: DefectItem[] }) => (
    <div data-testid="viewer-stub" data-task={props.taskId} data-images={props.images.length}>
      {props.defects.map((d, i) => (
        <span key={d.defect_id ?? i} data-testid="viewer-defect">{d.defect_type}</span>
      ))}
    </div>
  ),
}))

const DEFECTS: DefectItem[] = [
  { defect_id: 'D001', defect_type: 'BP', defect_name_cn: '模糊的真缺陷', confidence: 0.7, severity: 'high', location: { type: 'bbox', bbox: [1, 2, 3, 4] } },
]

const ARTS: ArtifactRef[] = [
  { name: 'artifact_result_image', mime: 'image/jpeg', uri: '/api/v1/tasks/T1/files/r.jpg?kind=artifacts' },
]

const store = () => useDefectEditStore.getState()

afterEach(() => {
  cleanup()
  store().closeEditor()
})

describe('DefectEditModal（人工标注编辑弹窗）', () => {
  it('打开即建立编辑会话：Viewer 以草稿渲染，右栏为修订面板', async () => {
    render(
      <DefectEditModal open taskId="T1" inputUris={['/api/v1/tasks/T1/files/a.jpg?kind=inputs']} defects={DEFECTS} artifacts={ARTS} onClose={vi.fn()} />,
    )
    expect(await screen.findByText('人工标注 · 缺陷修订')).toBeInTheDocument()
    const viewer = screen.getByTestId('viewer-stub')
    expect(viewer).toHaveAttribute('data-task', 'T1')
    expect(viewer).toHaveAttribute('data-images', '1')
    // 面板同会话：A1 行与工具按钮在位
    expect(screen.getByText('A1')).toBeInTheDocument()
    expect(screen.getByText('＋ 新增漏检框')).toBeInTheDocument()
    expect(store().open).toBe(true)
    expect(store().taskId).toBe('T1')
  })

  it('完成：关闭弹窗但保留草稿（随复核 revise 提交）', async () => {
    const onClose = vi.fn()
    render(
      <DefectEditModal open taskId="T1" inputUris={['/a.jpg']} defects={DEFECTS} artifacts={null} onClose={onClose} />,
    )
    await screen.findByText('人工标注 · 缺陷修订')
    act(() => store().toggleReject('D001'))
    fireEvent.click(screen.getByRole('button', { name: /完\s*成/ }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(store().open).toBe(true)
    expect(store().draft[0].human_rejected).toBe(true)
  })

  it('取消：丢弃修订并结束会话', async () => {
    const onClose = vi.fn()
    render(
      <DefectEditModal open taskId="T1" inputUris={['/a.jpg']} defects={DEFECTS} artifacts={null} onClose={onClose} />,
    )
    await screen.findByText('人工标注 · 缺陷修订')
    act(() => store().toggleReject('D001'))
    fireEvent.click(screen.getByRole('button', { name: /取消（丢弃修订）/ }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(store().open).toBe(false)
    expect(store().draft).toEqual([])
  })

  it('open=false 时不建立会话', () => {
    render(<DefectEditModal open={false} taskId="T1" inputUris={['/a.jpg']} defects={DEFECTS} artifacts={null} onClose={vi.fn()} />)
    expect(store().open).toBe(false)
  })

  it('编辑中的草稿实时驱动 Viewer（改类即刷新底图数据）', async () => {
    render(
      <DefectEditModal open taskId="T1" inputUris={['/a.jpg']} defects={DEFECTS} artifacts={null} classDefs={CLASS_DEFS} onClose={vi.fn()} />,
    )
    await screen.findByText('人工标注 · 缺陷修订')
    fireEvent.change(screen.getByLabelText('缺陷类型 A1'), { target: { value: 'FI' } })
    await waitFor(() => {
      expect(screen.getByTestId('viewer-defect')).toHaveTextContent('FI')
    })
  })
})
