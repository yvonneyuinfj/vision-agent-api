/* 颜色格式纯函数：hex 输入解析 + HEX/RGB/HSL 互转（取色器复制面板用），可单测 */

export interface RGB {
  r: number
  g: number
  b: number
}

export interface HSL {
  h: number
  s: number
  l: number
}

const HEX3_RE = /^#?[0-9a-fA-F]{3}$/
const HEX6_RE = /^#?[0-9a-fA-F]{6}$/

/** 解析用户输入的十六进制颜色（可带 #、支持 3/6 位缩写）→ 规范 #rrggbb；非法返回 null */
export function parseHexInput(input: string): string | null {
  const raw = input.trim()
  if (HEX3_RE.test(raw)) {
    const digits = raw.replace('#', '')
    return `#${digits.split('').map((c) => c + c).join('').toLowerCase()}`
  }
  if (HEX6_RE.test(raw)) {
    return `#${raw.replace('#', '').toLowerCase()}`
  }
  return null
}

function clamp255(n: number): number {
  return Math.round(Math.max(0, Math.min(255, n)))
}

export function rgbToHex({ r, g, b }: RGB): string {
  const to2 = (n: number) => clamp255(n).toString(16).padStart(2, '0')
  return `#${to2(r)}${to2(g)}${to2(b)}`
}

export function rgbString({ r, g, b }: RGB): string {
  return `rgb(${clamp255(r)}, ${clamp255(g)}, ${clamp255(b)})`
}

/** RGB → HSL（h: 0-360, s/l: 0-100） */
export function rgbToHsl({ r, g, b }: RGB): HSL {
  const rn = r / 255
  const gn = g / 255
  const bn = b / 255
  const max = Math.max(rn, gn, bn)
  const min = Math.min(rn, gn, bn)
  const l = (max + min) / 2
  if (max === min) return { h: 0, s: 0, l: l * 100 }
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h: number
  if (max === rn) h = (gn - bn) / d + (gn < bn ? 6 : 0)
  else if (max === gn) h = (bn - rn) / d + 2
  else h = (rn - gn) / d + 4
  return { h: h * 60, s: s * 100, l: l * 100 }
}

export function hslString({ h, s, l }: HSL): string {
  return `hsl(${Math.round(h)}, ${Math.round(s)}%, ${Math.round(l)}%)`
}