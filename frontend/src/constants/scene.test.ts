import { describe, it, expect } from 'vitest'
import { SCENE_MAP, SCENE_OPTIONS } from './scene'

describe('SCENE_MAP', () => {
  it('六个场景编码全部映射且唯一', () => {
    expect(Object.keys(SCENE_MAP)).toHaveLength(6)
    expect(SCENE_MAP.borescope).toBe('孔探')
    expect(SCENE_MAP.infrared).toBe('红外')
    expect(SCENE_MAP.dr).toBe('X射线')
    expect(SCENE_MAP.fpi).toBe('荧光')
    expect(SCENE_MAP.fracture).toBe('断口')
    expect(SCENE_MAP.metallography).toBe('金相')
    expect(SCENE_OPTIONS.map((o) => o.value).sort()).toEqual(Object.keys(SCENE_MAP).sort())
  })
})
