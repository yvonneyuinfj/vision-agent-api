/** 专业工作台定义。一个工作台 = 一个 scene 路由键，同场景可挂多个工具，
 *  故"注册工具"不新增工作台（决策 22）；工具台账见 /tool/registry。
 *  决策 52：这张表是**导航兜底 + 目录初始值**，页面呈现的场景项改由
 *  `GET /scenes` 取数（`hooks/useScenes`）；新增场景是后端 `scene_catalog.py` 的代码变更，
 *  且目录里 `autoRecognize=false` 的场景不会被自动识别命中，只能人工显式选。 */
export interface WorkbenchDef {
  /** scene 路由键：不再限死六键，故为 string（六键的类型安全由 SCENE_MAP 等常量表保持） */
  scene: string
  /** 侧栏与页头用的全称 */
  label: string
  en: string
  /** 参数契约是否冻结：frozen = 本页提供参数表；draft = 只声明契约待冻结（决策 3） */
  params: 'frozen' | 'draft'
  /** 输入物料要求（与 SubmitPage 的校验规则同源） */
  input: string
  /** 是否进了自动识别关键词表；false ⇒ 只能人工显式选场景提交（决策 52） */
  autoRecognize: boolean
}

export const WORKBENCHES: WorkbenchDef[] = [
  { scene: 'fpi', label: '荧光渗透检测', en: 'FPI', params: 'frozen', input: '仅需检测图像 · 暗场', autoRecognize: true },
  { scene: 'borescope', label: '孔探检测', en: 'BORESCOPE', params: 'frozen', input: '图像 / 录像均可', autoRecognize: true },
  { scene: 'infrared', label: '热部图像检测', en: 'INFRARED', params: 'draft', input: '图像 + 辐射值 CSV', autoRecognize: true },
  { scene: 'dr', label: 'X射线检测', en: 'DR', params: 'draft', input: '图像 + 参考图 + 标定 JSON/XML', autoRecognize: true },
  { scene: 'fracture', label: '断口分析', en: 'FRACTURE', params: 'frozen', input: '仅需检测图像', autoRecognize: true },
  { scene: 'metallography', label: '金相分析', en: 'METALLOGRAPHY', params: 'frozen', input: '仅需检测图像 · 附放大倍数', autoRecognize: true },
]

export const WORKBENCH_MAP: Record<string, WorkbenchDef> = Object.fromEntries(
  WORKBENCHES.map((w) => [w.scene, w]),
)

export const workbenchPath = (scene: string) => `/detect/${scene}`

/** 真实执行链（app/agents/graph.py:111-168 已核对）：不是"调工具→出报告"两步 */
export const TASK_FLOW =
  '能力选择 → 输入校验 → 调用工具 → 结果归一 → 依据检索 → 判级 →〔review/fail：人工复核门（暂停等点）｜ pass/defect：指标解释〕→ 生成报告'
