/** 数值区间钳制：将 value 限制在 [min, max] 内 */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}