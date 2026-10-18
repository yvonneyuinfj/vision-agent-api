import { describe, expect, it } from 'vitest'
import { hslString, parseHexInput, rgbToHex, rgbString, rgbToHsl } from './annotationColor'

describe('parseHexInput', () => {
  it('支持 6/3 位、可带 #，统一为 #rrggbb 小写', () => {
    expect(parseHexInput('f0756b')).toBe('#f0756b')
    expect(parseHexInput('#F0756B')).toBe('#f0756b')
    expect(parseHexInput('abc')).toBe('#aabbcc')
    expect(parseHexInput(' #C93 ')).toBe('#cc9933')
  })

  it('非法输入返回 null（取色面板回退当前颜色）', () => {
    expect(parseHexInput('')).toBeNull()
    expect(parseHexInput('12345')).toBeNull()
    expect(parseHexInput('gggggg')).toBeNull()
    expect(parseHexInput('#12')).toBeNull()
  })
})

describe('rgbToHex / rgbString', () => {
  it('转 hex 并做越界钳制与四舍五入', () => {
    expect(rgbToHex({ r: 240, g: 117, b: 107 })).toBe('#f0756b')
    expect(rgbToHex({ r: 300, g: -2, b: 12.6 })).toBe('#ff000d')
  })

  it('格式化 rgb 字符串', () => {
    expect(rgbString({ r: 17, g: 34, b: 51 })).toBe('rgb(17, 34, 51)')
    expect(rgbString({ r: 1.6, g: 2.4, b: 3.2 })).toBe('rgb(2, 2, 3)')
  })
})

describe('rgbToHsl / hslString', () => {
  it('灰色：饱和度为 0、亮度为 50% 左右', () => {
    expect(rgbToHsl({ r: 128, g: 128, b: 128 })).toEqual({
      h: 0,
      s: 0,
      l: expect.closeTo(50.196, 2),
    })
  })

  it('纯红：h=0 s=100% l=50%', () => {
    expect(rgbToHsl({ r: 255, g: 0, b: 0 })).toEqual({ h: 0, s: 100, l: 50 })
  })

  it('hslString 四舍五入格式化', () => {
    expect(hslString({ h: 210.4, s: 50.5, l: 13.4 })).toBe('hsl(210, 51%, 13%)')
  })
})