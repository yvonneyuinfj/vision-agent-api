import { WORKBENCHES, type WorkbenchDef } from '../../constants/workbench'
import './intake.css'
import { PARAM_FIELDS, validateParams, type ParamValues } from '../../constants/paramContract'
import { Notice } from '../common/ModuleShell'

/** 参数分区（决策 3）：所选场景契约全部冻结才给参数表；
 *  未冻结场景只声明契约状态，不编造表单。
 *  场景档位取自目录（决策 52），缺省用内置六条。 */
export function paramTierOf(
  scenes: readonly string[],
  catalog: readonly WorkbenchDef[] = WORKBENCHES,
): 'frozen' | 'draft' | 'mixed' | 'none' {
  if (!scenes.length) return 'none'
  const tiers = new Set(scenes.map((s) => catalog.find((w) => w.scene === s)?.params ?? 'draft'))
  if (tiers.size === 1) return tiers.has('frozen') ? 'frozen' : 'draft'
  return 'mixed'
}

export const paramErrorsOf = (values: ParamValues) => validateParams(values)

export default function ParamIntake({
  scenes,
  values,
  onChange,
  catalog = WORKBENCHES,
}: {
  scenes: readonly string[]
  values: ParamValues
  onChange: (next: ParamValues) => void
  catalog?: readonly WorkbenchDef[]
}) {
  const tier = paramTierOf(scenes, catalog)
  if (tier === 'none') return null

  const labelOf = (s: string) => catalog.find((w) => w.scene === s)?.label ?? s
  const unfrozen = scenes.filter((s) => catalog.find((w) => w.scene === s)?.params !== 'frozen')

  if (tier !== 'frozen') {
    return (
      <Notice tone="gray">
        <span>
          <b>{unfrozen.map(labelOf).join('、')}</b>
          的专业参数契约<b>尚未冻结</b>：工具侧输入参数结构未定稿，本页不提供参数表单，
          提交时沿用注册中心 <code className="mono">default_params</code>。
        </span>
      </Notice>
    )
  }

  const errors = validateParams(values)

  return (
    <div className="param-intake">
      <table className="tbl param-tbl">
        <thead>
          <tr>
            <th>参数</th>
            <th>取值</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          {PARAM_FIELDS.map((f) => {
            const v = values[f.key]
            const bad = errors.some((e) => e.startsWith(f.label))
            return (
              <tr key={f.key}>
                <td>
                  <div className="p-name">{f.label}</div>
                  <div className="p-key mono">{f.key}</div>
                </td>
                <td>
                  {f.type === 'bool' ? (
                    <label className="p-switch">
                      <input
                        type="checkbox"
                        checked={Boolean(v)}
                        onChange={(e) => onChange({ ...values, [f.key]: e.target.checked })}
                        aria-label={f.label}
                      />
                      {v ? '开启' : '关闭'}
                    </label>
                  ) : (
                    <span className="p-num">
                      <input
                        className={`input${bad ? ' bad' : ''}`}
                        type="number"
                        inputMode="decimal"
                        min={f.min}
                        max={f.max}
                        step={f.step}
                        value={Number.isFinite(Number(v)) ? String(v) : ''}
                        onChange={(e) => onChange({ ...values, [f.key]: e.currentTarget.value === '' ? NaN : Number(e.currentTarget.value) })}
                        aria-label={f.label}
                      />
                      {v !== f.def && <span className="p-over">已覆盖默认 {String(f.def)}</span>}
                    </span>
                  )}
                </td>
                <td className="muted">{f.hint}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {errors.length > 0 && (
        <div className="hint err mt-8">{errors.join('；')}</div>
      )}
      <div className="hint mt-8">
        字段表为前端按工具侧 Schema 自带的契约（<code className="mono">/capabilities</code> 不返回
        <code className="mono"> input_schema / default_params</code>）；未改动的项不下发，
        改动的项随 <code className="mono">POST /tasks</code> 的 <code className="mono">parameters</code> 透传到工具。
      </div>
    </div>
  )
}
