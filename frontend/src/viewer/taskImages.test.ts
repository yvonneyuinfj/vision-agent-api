import { describe, it, expect } from 'vitest'
import { annotatedArtifactForImage, buildTaskImages, defectsForImage } from './taskImages'
import type { DefectItem } from '../api/types'

describe('buildTaskImages', () => {
  it('直接使用后端 input_uris 作为原图 src（URI 本身即下载端点）', () => {
    // Arrange
    const uriA = '/api/v1/tasks/_upload/files/class1_1.jpg?kind=inputs'
    const uriB = '/api/v1/tasks/_upload/files/class2_1.jpg?kind=inputs'

    // Act
    const images = buildTaskImages({ input_uris: [uriA, uriB] })

    // Assert
    expect(images).toEqual([
      { key: 'input-0', src: uriA },
      { key: 'input-1', src: uriB },
    ])
  })

  it('detail 缺失或 input_uris 为空时返回空数组，不臆造文件名', () => {
    expect(buildTaskImages(null)).toEqual([])
    expect(buildTaskImages(undefined)).toEqual([])
    expect(buildTaskImages({ input_uris: [] })).toEqual([])
  })
})

// ----------------------------------------------------------------------
// 多图 bbox 混叠回归（2026-09-16 生产事故：每张图叠上全部图的框）
// ----------------------------------------------------------------------
describe('defectsForImage / annotatedArtifactForImage（多图隔离）', () => {
  const defects: DefectItem[] = [
    { defect_id: 'd1', defect_type: 'BP', defect_name_cn: '模糊的真缺陷', confidence: 0.8, severity: 'low',
      location: { type: 'bbox', bbox: [1, 2, 3, 4] }, image_name: 'a.jpg' },
    { defect_id: 'd2', defect_type: 'SP', defect_name_cn: '清晰真缺陷', confidence: 0.7, severity: 'low',
      location: { type: 'bbox', bbox: [5, 6, 7, 8] }, image_name: 'a.jpg' },
    { defect_id: 'd3', defect_type: 'BP', defect_name_cn: '模糊的真缺陷', confidence: 0.5, severity: 'low',
      location: { type: 'bbox', bbox: [9, 10, 11, 12] }, image_name: 'b.jpg' },
  ]
  const URI_A = '/api/v1/tasks/t1/files/a.jpg?kind=inputs'
  const URI_B = '/api/v1/tasks/t1/files/b.jpg?kind=inputs'

  it('按当前图过滤：a 图只见 a 的 2 个框，b 图只见 b 的 1 个框', () => {
    expect(defectsForImage(defects, URI_A).map((d) => d.defect_id)).toEqual(['d1', 'd2'])
    expect(defectsForImage(defects, URI_B).map((d) => d.defect_id)).toEqual(['d3'])
  })

  it('存量数据（无 image_name）不过滤——单图任务兼容', () => {
    const legacy: DefectItem[] = [
      { defect_id: 'd1', defect_type: 'BP', defect_name_cn: '模糊的真缺陷', confidence: 0.8, severity: 'low',
        location: { type: 'bbox', bbox: [1, 2, 3, 4] } },
    ]
    expect(defectsForImage(legacy, URI_A)).toHaveLength(1)
  })

  it('无标记条目只归属第一张图——绝不全图叠加（混叠事故回归）', () => {
    const mixed: DefectItem[] = [
      ...defects,
      { defect_id: 'd4', defect_type: 'crack', defect_name_cn: '裂纹', confidence: null, severity: 'low',
        location: { type: 'bbox', bbox: [9, 9, 10, 10] } }, // 无 image_name
    ]
    // 第一张图：a 的 2 个标记框 + 无标记框（可见）
    expect(defectsForImage(mixed, URI_A, URI_A).map((d) => d.defect_id)).toEqual(['d1', 'd2', 'd4'])
    // 第二张图：只有 b 的 1 个标记框（无标记框不再叠加）
    expect(defectsForImage(mixed, URI_B, URI_A).map((d) => d.defect_id)).toEqual(['d3'])
  })

  it('标注图产物按图配对：__<stem> 后缀，不张冠李戴', () => {
    const artifacts = [
      { name: 'artifact_result_image__a', mime: 'image/jpeg', uri: '/api/a-annot.jpg' },
      { name: 'artifact_result_image__b', mime: 'image/jpeg', uri: '/api/b-annot.jpg' },
    ]
    expect(annotatedArtifactForImage(artifacts, URI_A)?.uri).toBe('/api/a-annot.jpg')
    expect(annotatedArtifactForImage(artifacts, URI_B)?.uri).toBe('/api/b-annot.jpg')
  })

  it('产物无图名标记时回退第一张（单图/存量兼容）', () => {
    const artifacts = [
      { name: 'artifact_result_image', mime: 'image/jpeg', uri: '/api/only.jpg' },
    ]
    expect(annotatedArtifactForImage(artifacts, URI_A)?.uri).toBe('/api/only.jpg')
    expect(annotatedArtifactForImage(artifacts, URI_B)?.uri).toBe('/api/only.jpg')
  })
})
