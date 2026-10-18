import type { Root } from 'mdast'
import { visit } from 'unist-util-visit'

/**
 * 清洗 AI 生成报告里的非法 LaTeX：裸 `#` 是 LaTeX 宏参数记号，
 * 出现在 `$...$` 公式中会被 KaTeX 判为语法错误（katex-error 红字）。
 * 报告正文习惯用 `#` 表示“号/编号”（如 60^{#} → 60 号），
 * 此处统一转义为 `\#`；已是 `\#` 的不重复转义。
 * 必须注册在 remark-math 之后（inlineMath 节点由 remark-math 产出）。
 */
export default function normalizeMath() {
  // 全局正则 + replace（lib 目标低于 es2021，无 replaceAll）：
  // 裸 # 前不是反斜杠才算未转义记号
  const escapeHashes = (value: string) => value.replace(/(?<!\\)#/g, '\\#')

  return (tree: Root) => {
    visit(tree, (node) => {
      if (node.type !== 'inlineMath' && node.type !== 'math') return
      if (typeof node.value === 'string' && node.value.includes('#')) {
        node.value = escapeHashes(node.value)
      }
      // remark-math 同时产出 data.hChildren（<code> 降级内容），
      // remark-rehype 转换时优先取 hChildren，须一并转义
      for (const child of node.data?.hChildren ?? []) {
        if (child.type === 'text' && child.value.includes('#')) {
          child.value = escapeHashes(child.value)
        }
      }
    })
  }
}