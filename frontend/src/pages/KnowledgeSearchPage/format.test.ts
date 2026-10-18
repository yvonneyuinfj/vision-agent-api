import { describe, expect, it } from 'vitest'
import { createElement, Fragment } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { groupHits, highlightMarkdown, highlightSentence, truncateSnippet } from './format'
import type { SearchHit } from '../../api/kb'

const hit = (over: Partial<SearchHit>): SearchHit => ({
  chunk_id: 'c1', content: '前缘裂纹属于裂纹类缺陷，判级标准见手册。', score: 0.9,
  doc_name: '手册A', document_id: 'd1', dataset_id: 'ds1', image_id: null, ...over,
})

describe('groupHits', () => {
  it('按 document 分组、组内最高分在前、组间按 topScore 降序', () => {
    const groups = groupHits([
      hit({ chunk_id: 'a', document_id: 'd1', score: 0.5 }),
      hit({ chunk_id: 'b', document_id: 'd2', score: 0.8 }),
      hit({ chunk_id: 'c', document_id: 'd1', score: 0.9 }),
    ])
    expect(groups.map((g) => g.documentId)).toEqual(['d1', 'd2'])
    expect(groups[0].hits.map((h) => h.chunk_id)).toEqual(['c', 'a'])
    expect(groups[0].topScore).toBe(0.9)
  })

  it('document_id 为 null 时用 doc_name 兜底分组键', () => {
    const groups = groupHits([
      hit({ document_id: null, doc_name: '手册A' }),
      hit({ document_id: null, doc_name: '手册B' }),
    ])
    expect(groups).toHaveLength(2)
    expect(groups[0].docName).toBe('手册A')
  })

  it('单命中也成组', () => {
    expect(groupHits([hit({ document_id: 'd9' })])).toHaveLength(1)
  })
})

describe('highlightSentence', () => {
  it('query 词命中处包 <mark>', () => {
    const html = renderToStaticMarkup(createElement(Fragment, null, highlightSentence('前缘裂纹属于裂纹类缺陷', '前缘裂纹')))
    expect(html).toBe('<mark>前缘裂纹</mark>属于裂纹类缺陷')
  })

  it('空 query / 无匹配返回原文', () => {
    expect(renderToStaticMarkup(createElement(Fragment, null, highlightSentence('原文内容', '  ')))).toBe('原文内容')
    expect(renderToStaticMarkup(createElement(Fragment, null, highlightSentence('原文内容', '不存在')))).toBe('原文内容')
  })
})

describe('highlightMarkdown', () => {
  it('命中 query 词时前后包 <mark>，markdown 语法原样保留', () => {
    expect(highlightMarkdown('前缘裂纹属于**裂纹类**缺陷', '前缘裂纹'))
      .toBe('<mark>前缘裂纹</mark>属于**裂纹类**缺陷')
  })

  it('query 命中在行内 HTML 旁不破坏两侧语法', () => {
    expect(highlightMarkdown('允许σ = 580 MPa<sup>[23-24]</sup>处判定', '580'))
      .toBe('允许σ = <mark>580</mark> MPa<sup>[23-24]</sup>处判定')
  })

  it('空 query / 无匹配返回原文逐字', () => {
    expect(highlightMarkdown('**加粗正文** 与 <sup>[1]</sup>', '')).toBe('**加粗正文** 与 <sup>[1]</sup>')
    expect(highlightMarkdown('**加粗正文**', '不存在')).toBe('**加粗正文**')
  })
})

describe('truncateSnippet', () => {
  it('超长截断加省略号', () => {
    const text = '甲'.repeat(200)
    const out = truncateSnippet(text)
    expect(out.length).toBe(181)
    expect(out.endsWith('…')).toBe(true)
  })
  it('短文本原样返回', () => {
    expect(truncateSnippet('短文本')).toBe('短文本')
  })
})