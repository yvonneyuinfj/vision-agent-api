import { describe, it, expect } from 'vitest'
import { kindOfFile, validateFile, maxMBOf, fileSizeText } from './fileValidation'

function fileOf(name: string, sizeBytes: number): File {
  const f = new File([new Uint8Array(0)], name)
  Object.defineProperty(f, 'size', { value: sizeBytes })
  return f
}

const MB = 1024 * 1024
const GB = 1024 * MB

describe('kindOfFile', () => {
  it('按扩展名识别图像 / 视频 / CSV / 标定', () => {
    expect(kindOfFile('a.jpg')).toBe('image')
    expect(kindOfFile('A.TIF')).toBe('image')
    expect(kindOfFile('a.mp4')).toBe('video')
    expect(kindOfFile('a.mov')).toBe('video')
    expect(kindOfFile('random.csv')).toBe('csv')
    expect(kindOfFile('calib.json')).toBe('calib')
    expect(kindOfFile('calib.xml')).toBe('calib')
  })

  it('不认识的后缀返回 null', () => {
    expect(kindOfFile('a.bmp')).toBeNull()
    expect(kindOfFile('a.exe')).toBeNull()
    expect(kindOfFile('noext')).toBeNull()
  })
})

describe('validateFile', () => {
  it('图像 ≤ 50MB 放行，超限拦截', () => {
    expect(validateFile(fileOf('a.jpg', 10 * MB))).toBeNull()
    expect(validateFile(fileOf('a.jpg', 50 * MB))).toBeNull()
    expect(validateFile(fileOf('a.jpg', 50 * MB + 1))).toMatch(/50MB/)
  })

  it('视频 ≤ 1GB 放行，超限拦截', () => {
    expect(validateFile(fileOf('clip.mp4', 900 * MB))).toBeNull()
    expect(validateFile(fileOf('clip.mp4', 1024 * MB))).toBeNull()
    expect(validateFile(fileOf('clip.mp4', 1024 * MB + 1))).toMatch(/1024MB/)
  })

  it('CSV ≤ 10MB，标定放行', () => {
    expect(validateFile(fileOf('radiance.csv', 10 * MB))).toBeNull()
    expect(validateFile(fileOf('radiance.csv', 10 * MB + 1))).toMatch(/10MB/)
    expect(validateFile(fileOf('calib.json', 1024))).toBeNull()
  })

  it('不成类型后缀直接拦截', () => {
    expect(validateFile(fileOf('a.bmp', 1))).toMatch(/不支持的文件格式/)
  })
})

describe('maxMBOf / fileSizeText', () => {
  it('返回各类型上限', () => {
    expect(maxMBOf('image')).toBe(50)
    expect(maxMBOf('video')).toBe(1024)
    expect(maxMBOf('csv')).toBe(10)
  })

  it('MB 与 GB 两种展示', () => {
    expect(fileSizeText(38.2 * MB)).toBe('38.2 MB')
    expect(fileSizeText(184.6 * MB)).toBe('184.6 MB')
    expect(fileSizeText(2.4 * GB)).toBe('2.40 GB')
  })
})