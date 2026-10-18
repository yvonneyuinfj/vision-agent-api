import { describe, it, expect } from 'vitest'
import { pickArtifactImagePair, isImageArtifact } from './artifacts'
import type { ArtifactRef } from '../api/types'

function art(name: string, uri: string, mime: string | null = 'image/jpeg'): ArtifactRef {
  return { name, mime, uri }
}

describe('pickArtifactImagePair', () => {
  it('命名匹配：original_image 与 result_image 成对（真实 FPI 工具产物）', () => {
    const { original, annotated } = pickArtifactImagePair([
      art('artifact_original_image', '/f/original'),
      art('artifact_result_json', '/f/result.json', 'application/json'),
      art('artifact_result_image', '/f/result.jpg'),
      art('artifact_report_pdf', '/f/report.pdf', 'application/pdf'),
    ])
    expect(original?.uri).toBe('/f/original')
    expect(annotated?.uri).toBe('/f/result.jpg')
  })

  it('mock 命名：annotated.png 命中标注图键', () => {
    const { original, annotated } = pickArtifactImagePair([
      art('input', '/f/input.png'),
      art('annotated', '/f/annotated.png'),
    ])
    expect(original?.uri).toBe('/f/input.png')
    expect(annotated?.uri).toBe('/f/annotated.png')
  })

  it('命名不可辨时按出现顺序兜底（非图片产物不参与）', () => {
    const { original, annotated } = pickArtifactImagePair([
      art('report', '/f/r.pdf', 'application/pdf'),
      art('a', '/f/a.png'),
      art('b', '/f/b.png'),
      art('c', '/f/c.png'),
    ])
    expect(original?.uri).toBe('/f/a.png')
    expect(annotated?.uri).toBe('/f/b.png')
  })

  it('单图只作原图，不臆造标注图', () => {
    const { original, annotated } = pickArtifactImagePair([art('original_image', '/f/o.jpg')])
    expect(original?.uri).toBe('/f/o.jpg')
    expect(annotated).toBeNull()
  })

  it('空产物 / 无图片产物：均为 null', () => {
    expect(pickArtifactImagePair(undefined)).toEqual({ original: null, annotated: null })
    expect(pickArtifactImagePair([])).toEqual({ original: null, annotated: null })
    expect(pickArtifactImagePair([art('report', '/f/r.pdf', 'application/pdf')])).toEqual({
      original: null,
      annotated: null,
    })
  })
})

describe('isImageArtifact', () => {
  it('mime 缺失时按 URI 扩展名识别（含大小写与签名 query）', () => {
    expect(isImageArtifact(art('x', '/f/x.PNG', null))).toBe(true)
    expect(isImageArtifact(art('x', '/f/x.jpg?token=a', null))).toBe(true)
    expect(isImageArtifact(art('x', '/f/x.bin', null))).toBe(false)
    expect(isImageArtifact(art('x', '/f/x.png', 'image/png'))).toBe(true)
  })
})
