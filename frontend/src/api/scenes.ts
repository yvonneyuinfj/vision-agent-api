import http, { unwrapResponse } from './http'
import type { PageResult, StandardEnvelope } from './types'
import type { WorkbenchDef } from '../constants/workbench'

/** 后端场景目录（决策 52）：与 app/models/scene.py:SceneSpec 逐字段对齐。
 *  只读——场景新增是后端 `scene_catalog.py` 的代码变更，不是运营可写的配置。 */
export interface SceneSpec {
  scene: string
  label: string
  short: string
  en: string
  params: 'frozen' | 'draft'
  input: string
  auto_recognize: boolean
}

export async function fetchScenes(): Promise<SceneSpec[]> {
  const res = await http.get<StandardEnvelope<PageResult<SceneSpec>>>('/scenes')
  return unwrapResponse(res).items
}

/** 目录 → 工作台定义。字段以后端为准；`params` 出现未知值时按最保守的"契约未冻结"处理。 */
export function scenesToWorkbenches(specs: SceneSpec[]): WorkbenchDef[] {
  return specs.map((s) => ({
    scene: s.scene,
    label: s.label,
    en: s.en,
    params: s.params === 'frozen' ? 'frozen' : 'draft',
    input: s.input,
    autoRecognize: s.auto_recognize,
  }))
}
