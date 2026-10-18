import { describe, it, expect } from 'vitest'
import { hasDrawableDefectBoxes, resolveAnnotatedLayerSrc } from './annotatedLayer'
import type { DefectItem } from '../api/types'

function defect(loc: DefectItem['location']): DefectItem {
  return {
    defect_id: 'd1',
    defect_type: 'BP',
    defect_name_cn: '模糊的真缺陷',
    confidence: 0.8,
    severity: null,
    location: loc,
    description: '',
  }
}

const ANN = '/api/v1/tasks/t1/files/artifact_result_image?kind=artifacts'

describe('hasDrawableDefectBoxes', () => {
  it('契约形态 {type:bbox, bbox} 可绘制', () => {
    expect(hasDrawableDefectBoxes([defect({ type: 'bbox', bbox: [1, 2, 3, 4] })])).toBe(true)
  })

  it('存量 FPI 数据（仅 bbox_xyxy、无 type）可绘制', () => {
    expect(hasDrawableDefectBoxes([defect({ bbox_xyxy: [1, 2, 3, 4] })])).toBe(true)
  })

  it('无 location / 非 bbox 类型 / 非四元组不可绘制', () => {
    expect(hasDrawableDefectBoxes([defect(null)])).toBe(false)
    expect(hasDrawableDefectBoxes([defect({ type: 'mask' })])).toBe(false)
    expect(hasDrawableDefectBoxes([defect({ type: 'bbox' })])).toBe(false)
    expect(hasDrawableDefectBoxes([defect({ type: 'bbox', bbox: [1, 2] as never })])).toBe(false)
    expect(hasDrawableDefectBoxes([])).toBe(false)
  })
})

describe('resolveAnnotatedLayerSrc', () => {
  const base = { annotatedSrc: ANN, showDefects: true, imageCount: 1 }

  it('条目无 bbox 时用模型标注产物图（agent 轨「标注图==原图」回归）', () => {
    expect(resolveAnnotatedLayerSrc({ ...base, defects: [defect(null)] })).toBe(ANN)
    expect(resolveAnnotatedLayerSrc({ ...base, defects: [] })).toBe(ANN)
  })

  it('条目有可绘制 bbox 时保持原图 + Konva 框（点击联动缺陷卡）', () => {
    expect(
      resolveAnnotatedLayerSrc({ ...base, defects: [defect({ type: 'bbox', bbox: [1, 2, 3, 4] })] }),
    ).toBeNull()
  })

  it('存量 bbox_xyxy 数据同样保持 Konva 框', () => {
    expect(
      resolveAnnotatedLayerSrc({ ...base, defects: [defect({ bbox_xyxy: [1, 2, 3, 4] })] }),
    ).toBeNull()
  })

  it('原图层（showDefects=false）不换图源', () => {
    expect(resolveAnnotatedLayerSrc({ ...base, showDefects: false, defects: [defect(null)] })).toBeNull()
  })

  it('多图任务产物无法对应具体输入图，不猜测', () => {
    expect(resolveAnnotatedLayerSrc({ ...base, imageCount: 2, defects: [defect(null)] })).toBeNull()
  })

  it('无标注产物不换图源', () => {
    expect(
      resolveAnnotatedLayerSrc({ annotatedSrc: null, showDefects: true, imageCount: 1, defects: [defect(null)] }),
    ).toBeNull()
  })
})
