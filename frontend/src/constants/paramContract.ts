/** 参数契约（决策 3）：`/capabilities` 不返回 input_schema / default_params，
 *  字段表只能由前端自带，故这里显式声明"以工具侧 Schema 为准"。
 *  键名与取值域取自能力注册种子（scripts/sync_capabilities.py 的 default_params）
 *  与 mock 服务校验（mock_service/main.py），非臆造字段。 */
export interface ParamField {
  key: string
  label: string
  type: 'float' | 'int' | 'bool'
  min?: number
  max?: number
  step?: number
  /** 注册中心 default_params 的原值：不修改即不下发，沿用工具侧默认 */
  def: number | boolean
  hint: string
}

export const PARAM_FIELDS: readonly ParamField[] = [
  {
    key: 'confidence_threshold',
    label: '置信度阈值',
    type: 'float',
    min: 0,
    max: 1,
    step: 0.05,
    def: 0.5,
    hint: '低于该值的检出结果被工具侧丢弃 · 0 ~ 1',
  },
  {
    key: 'max_defects',
    label: '单图最大检出数',
    type: 'int',
    min: 1,
    max: 10,
    step: 1,
    def: 1,
    hint: '逐图上限，多图任务按图分别计 · 1 ~ 10',
  },
  {
    key: 'return_annotated',
    label: '返回标注图',
    type: 'bool',
    def: true,
    hint: '关闭后工具不回传画框图，结果页图层对照将无标注底图',
  },
] as const

export type ParamValues = Record<string, number | boolean>

/** 表单初值 = 契约默认值（与注册中心 default_params 同值，不覆盖工具侧行为） */
export function defaultParamValues(): ParamValues {
  return Object.fromEntries(PARAM_FIELDS.map((f) => [f.key, f.def]))
}

/** 只下发被改动过的项：未改的字段不进 parameters，避免覆盖工具侧 default_params */
export function changedParams(values: ParamValues): ParamValues {
  const out: ParamValues = {}
  for (const f of PARAM_FIELDS) {
    if (values[f.key] !== f.def) out[f.key] = values[f.key]
  }
  return out
}

export function validateParams(values: ParamValues): string[] {
  const errs: string[] = []
  for (const f of PARAM_FIELDS) {
    const v = values[f.key]
    if (f.type === 'bool') continue
    if (typeof v !== 'number' || Number.isNaN(v)) {
      errs.push(`${f.label}：请输入数字`)
      continue
    }
    if (f.type === 'int' && !Number.isInteger(v)) {
      errs.push(`${f.label}：须为整数`)
      continue
    }
    if (f.min != null && v < f.min) errs.push(`${f.label}：不得小于 ${f.min}`)
    if (f.max != null && v > f.max) errs.push(`${f.label}：不得大于 ${f.max}`)
  }
  return errs
}
