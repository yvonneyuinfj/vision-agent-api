import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import DefectEditPanel from './DefectEditPanel'
import { useDefectEditStore } from '../../store/useDefectEditStore'
import type { ClassDefItem, DefectItem } from '../../api/types'

const CLASS_DEFS: ClassDefItem[] = [
  { class_id: 0, class_name: 'BP', description: '模糊的真缺陷', is_true_defect: true },
  { class_id: 1, class_name: 'SP', description: '线性伪显示', is_true_defect: true },
  { class_id: 2, class_name: 'FI', description: '虚假荧光显示', is_true_defect: false },
]

function d(over: Partial<DefectItem> = {}): DefectItem {
  return {
    defect_id: 'D001',
    defect_type: 'BP',
    defect_name_cn: '模糊的真缺陷',
    confidence: 0.7,
    severity: 'high',
    location: { type: 'bbox', bbox: [10, 10, 90, 90] },
    ...over,
  }
}

const store = () => useDefectEditStore.getState()

describe('DefectEditPanel（缺陷修订面板）', () => {
  beforeEach(() => {
    store().openEditor('T1', 'input-0', [d(), d({ defect_id: 'D002', defect_type: 'SP', severity: 'medium' })])
  })
  afterEach(() => {
    cleanup()
    store().closeEditor()
  })

  it('渲染条目行（A 号）与初始「暂无修订」', () => {
    render(<DefectEditPanel classDefs={CLASS_DEFS} />)
    expect(screen.getByText('A1')).toBeInTheDocument()
    expect(screen.getByText('A2')).toBeInTheDocument()
    expect(screen.getByText('暂无修订')).toBeInTheDocument()
  })

  it('改类：下拉候选来自类别定义表（代码+中文名），写回 draft 并计入 diff', () => {
    render(<DefectEditPanel classDefs={CLASS_DEFS} />)
    const sel = screen.getByLabelText('缺陷类型 A1')
    // 候选含定义表中文名转译
    expect(sel.querySelector('option[value="FI"]')?.textContent).toContain('虚假荧光显示')
    fireEvent.change(sel, { target: { value: 'FI' } })
    expect(store().draft[0].defect_type).toBe('FI')
    expect(store().draft[0].revised).toMatchObject({ defect_type: 'BP' })
    expect(screen.getByText('改类 1')).toBeInTheDocument()
  })

  it('排除：行打排除态、按钮翻转为「恢复」，diff 显示排除计数', () => {
    render(<DefectEditPanel classDefs={CLASS_DEFS} />)
    fireEvent.click(screen.getAllByText('排除')[0])
    expect(store().draft[0].human_rejected).toBe(true)
    expect(screen.getByText('排除 1')).toBeInTheDocument()
    expect(document.querySelector('.dep-row.rejected')).not.toBeNull()
    fireEvent.click(screen.getByText('恢复'))
    expect(store().draft[0].human_rejected).toBe(false)
  })

  it('改严重度：写回并计入 diff', () => {
    render(<DefectEditPanel classDefs={CLASS_DEFS} />)
    fireEvent.change(screen.getByLabelText('严重度 A2'), { target: { value: 'high' } })
    expect(store().draft[1].severity).toBe('high')
    expect(screen.getByText('严重度调整 1')).toBeInTheDocument()
  })

  it('新增漏检框：切换画框工具；落框后出现「人工」条目与未分类拦截提示；人工条目可删除', () => {
    render(<DefectEditPanel classDefs={CLASS_DEFS} />)
    fireEvent.click(screen.getByText('＋ 新增漏检框'))
    expect(store().tool).toBe('add')
    expect(screen.getByText('画框中：图上拖拽…')).toBeInTheDocument()
    // 模拟图上落框（手势层由 DefectEditLayer 承接，这里直调 store 动作）
    act(() => store().addDefect([100, 100, 200, 200]))
    expect(screen.getByText('人工')).toBeInTheDocument()
    expect(screen.getByText(/未选择类型的人工新增条目/)).toBeInTheDocument()
    expect(screen.getByText('新增 1')).toBeInTheDocument()
    // 人工条目有删除按钮，模型条目没有
    const delBtns = screen.getAllByText('删除')
    expect(delBtns.length).toBe(1)
    fireEvent.click(delBtns[0])
    expect(store().draft.length).toBe(2)
  })

  it('人工条目选类型后拦截提示消失', () => {
    render(<DefectEditPanel classDefs={CLASS_DEFS} />)
    act(() => store().addDefect([1, 2, 30, 40]))
    const mid = store().draft[2].defect_id!
    fireEvent.change(screen.getByLabelText('缺陷类型 A3'), { target: { value: 'BP' } })
    expect(store().draft.find((x) => x.defect_id === mid)?.defect_type).toBe('BP')
    expect(screen.queryByText(/未选择类型的人工新增条目/)).toBeNull()
  })

  it('重置修订：draft 回基线、diff 归零', () => {
    render(<DefectEditPanel classDefs={CLASS_DEFS} />)
    fireEvent.click(screen.getAllByText('排除')[0])
    expect(screen.getByText('排除 1')).toBeInTheDocument()
    fireEvent.click(screen.getByText('重置修订'))
    expect(screen.getByText('暂无修订')).toBeInTheDocument()
    expect(store().draft[0].human_rejected).toBeUndefined()
  })
})
