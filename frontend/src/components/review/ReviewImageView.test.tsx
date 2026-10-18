import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import ReviewImageView from './ReviewImageView'
import type { ArtifactRef, DefectItem } from '../../api/types'

function box(x1: number, y1: number, x2: number, y2: number) {
  return { type: 'bbox', bbox: [x1, y1, x2, y2] as [number, number, number, number] }
}

const DEFECTS: DefectItem[] = [
  { defect_id: 'D001', defect_type: 'BP', defect_name_cn: '模糊的真缺陷', confidence: 0.76, severity: 'high', location: box(10, 10, 90, 90) },
  { defect_id: 'D002', defect_type: 'SP', defect_name_cn: '线性伪显示', confidence: 0.55, severity: 'low', location: box(200, 100, 300, 160), human_rejected: true },
  { defect_id: 'M001', defect_type: 'crack', defect_name_cn: '', confidence: null, severity: 'medium', location: box(400, 300, 460, 360), source: 'manual' },
]

const ARTS: ArtifactRef[] = [
  { name: 'artifact_result_image', mime: 'image/jpeg', uri: '/api/v1/tasks/T1/files/result.jpg?kind=artifacts' },
]

/** jsdom 不真正加载图片：手动定义自然尺寸后触发 load（SVG viewBox 依赖它） */
function fireImageLoad(img: HTMLElement, w = 1024, h = 768) {
  Object.defineProperty(img, 'naturalWidth', { value: w, configurable: true })
  Object.defineProperty(img, 'naturalHeight', { value: h, configurable: true })
  fireEvent.load(img)
}

afterEach(cleanup)

describe('ReviewImageView（复核卡内嵌只读图像区）', () => {
  it('默认缺陷框叠加层：原图 + 每条目一框，排除项灰虚线「已排除」、人工项「人工」角标', () => {
    render(<ReviewImageView inputUris={['/api/v1/tasks/T1/files/a.jpg?kind=inputs']} defects={DEFECTS} artifacts={ARTS} />)
    const img = screen.getByAltText('检测原图')
    expect(img).toHaveAttribute('src', '/api/v1/tasks/T1/files/a.jpg?kind=inputs')
    fireImageLoad(img)
    const rects = document.querySelectorAll('.riv-svg rect')
    expect(rects.length).toBe(3)
    expect(screen.getByText(/A2 已排除/)).toBeInTheDocument()
    expect(screen.getByText(/A3 人工/)).toBeInTheDocument()
    expect(screen.getByText('标注图（缺陷框叠加）')).toBeInTheDocument()
  })

  it('图层切换：原图层隐藏框叠加；算法标注图层换 img 源为产物 URI', () => {
    render(<ReviewImageView inputUris={['/inputs/a.jpg']} defects={DEFECTS} artifacts={ARTS} />)
    fireImageLoad(screen.getByAltText('检测原图'))
    fireEvent.click(screen.getByText('原图'))
    expect(document.querySelector('.riv-svg')).toBeNull()
    fireEvent.click(screen.getByText('算法标注图'))
    expect(screen.getByAltText('算法标注图')).toHaveAttribute('src', ARTS[0].uri)
  })

  it('无可绘制框时「缺陷框叠加」禁用，默认落到算法标注图层', () => {
    const noLoc: DefectItem[] = [{ defect_id: 'd', defect_type: 'x', defect_name_cn: '', confidence: null, severity: null, location: null }]
    render(<ReviewImageView inputUris={['/inputs/a.jpg']} defects={noLoc} artifacts={ARTS} />)
    expect(screen.getByText('缺陷框叠加')).toBeDisabled()
    expect(screen.getByAltText('算法标注图')).toBeInTheDocument()
  })

  it('无产物时不渲染「算法标注图」图层按钮', () => {
    render(<ReviewImageView inputUris={['/inputs/a.jpg']} defects={DEFECTS} artifacts={null} />)
    expect(screen.queryByText('算法标注图')).toBeNull()
  })

  it('多图任务渲染缩略条，点击切换主图', () => {
    render(<ReviewImageView inputUris={['/inputs/a.jpg', '/inputs/b.jpg']} defects={[]} artifacts={null} />)
    const thumbs = document.querySelectorAll('.riv-thumb')
    expect(thumbs.length).toBe(2)
    fireEvent.click(thumbs[1])
    expect(screen.getByAltText('检测原图')).toHaveAttribute('src', '/inputs/b.jpg')
  })

  it('提供 onAnnotate 时渲染「人工标注」按钮并回调', () => {
    const onAnnotate = vi.fn()
    render(<ReviewImageView inputUris={['/inputs/a.jpg']} defects={DEFECTS} artifacts={null} onAnnotate={onAnnotate} />)
    fireEvent.click(screen.getByText(/人工标注/))
    expect(onAnnotate).toHaveBeenCalledTimes(1)
  })

  it('无输入图且无产物时呈现空态', () => {
    render(<ReviewImageView inputUris={[]} defects={[]} artifacts={null} />)
    expect(screen.getByText('暂无输入图像')).toBeInTheDocument()
  })
})
