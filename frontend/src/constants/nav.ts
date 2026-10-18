import type { IconName } from '../components/ui/Icon'
import { WORKBENCHES, type WorkbenchDef } from './workbench'

/* 侧栏唯一数据源（建设方案 v0.5 §3.1）：7 个一级模块 / 36 个导航叶 / 其中 16 个建设中壳页。
   工具注册与工具版本同源同表，已合并为一叶（决策 24）。
   业务类与平台管理只是视觉分组，不构成权限边界（决策 10）。 */

export interface NavLeaf {
  to: string
  label: string
  /** 每个叶必有图标：侧栏收成图标栏时二级项要能凭图形辨认 */
  icon: IconName
  /** 建设中壳页（决策 9）：侧栏标"建"，页面只给方案条款 */
  shell?: boolean
  /** 徽标数据源：review = 待复核任务数（来自 /tasks 窗口，非全量） */
  badge?: 'review'
}

/** 归类标签：只分组、不占路由（决策 22，避免与首个子项看起来重复） */
export interface NavTag {
  label: string
  tag: true
  subs: NavLeaf[]
}

export interface NavSection {
  title?: string
  icon?: IconName
  items: (NavLeaf | NavTag)[]
}

const isTag = (e: NavLeaf | NavTag): e is NavTag => 'tag' in e

/** 检测场景的侧栏图标（图标栏态下每叶都要可凭图形辨认）。
 *  侧栏这组工作台叶取的是内置表（决策 52：`WORKBENCHES` 同时是目录初始值与导航兜底）；
 *  目录里新增的场景不会自动出现在侧栏，须在此补一枚图标——这是有意的：
 *  自动识别还没覆盖它之前，把它做成常驻导航叶会让人以为它已能智能路由。 */
const SCENE_ICON: Record<string, IconName> = {
  fpi: 'droplet',
  borescope: 'scope',
  infrared: 'thermal',
  dr: 'xray',
  fracture: 'crack',
  metallography: 'grain',
}

const workbenchTag = (): NavTag => ({
  label: '专业工作台',
  tag: true,
  subs: WORKBENCHES.map((w: WorkbenchDef) => ({
    to: `/detect/${w.scene}`,
    label: w.label,
    icon: SCENE_ICON[w.scene] ?? 'flask',
  })),
})

export const NAV: NavSection[] = [
  { items: [{ to: '/', label: '首页', icon: 'home' }] },
  {
    title: '智能检测',
    icon: 'sparkle',
    items: [{ to: '/detect/assistant', label: '智能助手', icon: 'chat' }, workbenchTag()],
  },
  {
    title: '任务中心',
    icon: 'tasks',
    items: [
      { to: '/tasks', label: '任务列表', icon: 'list' },
      { to: '/tasks/new', label: '新建任务', icon: 'plus' },
      { to: '/tasks/review', label: '结果显示与复核', icon: 'check', badge: 'review' },
      { to: '/tasks/samples', label: '反馈样本管理', icon: 'flask' },
      { to: '/reports', label: '报告与综合研判', icon: 'doc' },
    ],
  },
  {
    title: '能力中心',
    icon: 'caps',
    items: [
      { to: '/capability/pro', label: '专业能力说明', icon: 'shield' },
      { to: '/capability/general', label: '通用能力说明', icon: 'grid' },
      { to: '/capability/skills', label: '智能体技能清单', icon: 'bolt', shell: true },
      { to: '/capability/templates', label: '模板库', icon: 'template', shell: true },
      { to: '/capability/guide', label: '样例与指南', icon: 'compass' },
    ],
  },
  {
    title: '模型工坊',
    icon: 'model',
    items: [
      { to: '/model/samples', label: '候选样本', icon: 'image', shell: true },
      { to: '/model/annotation', label: '标注', icon: 'pen', shell: true },
      { to: '/model/datasets', label: '数据集', icon: 'database', shell: true },
      { to: '/model/training', label: '训练', icon: 'cpu', shell: true },
      { to: '/model/evaluation', label: '评估', icon: 'chart', shell: true },
      { to: '/model/release', label: '发布与回滚', icon: 'rocket', shell: true },
    ],
  },
  {
    title: '知识中心',
    icon: 'book',
    items: [
      { to: '/knowledge/search', label: '检索与引用', icon: 'search' },
      { to: '/knowledge/ingest', label: '资料入库', icon: 'inbox' },
      { to: '/knowledge/collection', label: '内网采集任务', icon: 'cloud', shell: true },
    ],
  },
  {
    title: '工具服务中心',
    icon: 'plug',
    items: [
      /* 注册与版本同源同表（都是 GET /capabilities），两叶重复，合并为一叶（决策 24） */
      { to: '/tool/registry', label: '工具注册', icon: 'wrench' },
      { to: '/tool/model-versions', label: '模型版本', icon: 'box', shell: true },
      { to: '/tool/skills', label: 'Skill 注册', icon: 'bolt', shell: true },
      { to: '/tool/templates', label: '模板管理', icon: 'template', shell: true },
    ],
  },
  {
    title: '后台管理',
    icon: 'gear',
    items: [
      { to: '/admin/users', label: '用户角色', icon: 'user', shell: true },
      { to: '/admin/orgs', label: '项目组织', icon: 'building', shell: true },
      { to: '/admin/config', label: '系统配置', icon: 'sliders', shell: true },
      { to: '/admin/audit', label: '审计日志', icon: 'history', shell: true },
      { to: '/admin/monitor', label: '运行监控', icon: 'activity' },
    ],
  },
]

export interface FlatLeaf extends NavLeaf {
  /** 一级模块名；无模块（首页）为空串 */
  group: string
}

/** 归一化叶子表：分组标签下的子项带 group，其余同 */
export const LEAVES: FlatLeaf[] = NAV.flatMap((sec) =>
  sec.items.flatMap((e): FlatLeaf[] =>
    isTag(e)
      ? e.subs.map((s) => ({ ...s, group: sec.title ?? '' }))
      : [{ ...e, group: sec.title ?? '' }],
  ),
)

/** 侧栏分组折叠状态存储键；改此键会让用户已存的折叠偏好失效，需一并迁移 */
export const CLOSED_GROUPS_KEY = 'shell.closed-groups'
export const SIDE_RAIL_KEY = 'shell.side-rail'

export const sectionLeaves = (sec: NavSection) =>
  sec.items.flatMap((e) => (isTag(e) ? e.subs : [e]))

const exactLeaf = (pathname: string) => LEAVES.find((l) => l.to === pathname)

/** 最长前缀命中：/tasks/{id} 之类的参数路由归属到最近的静态叶 */
const prefixLeaf = (pathname: string) =>
  [...LEAVES]
    .sort((a, b) => b.to.length - a.to.length)
    .find((l) => l.to !== '/' && pathname.startsWith(`${l.to}/`))

/** 参数路由归属的导航叶：与 PARAM_TRAIL 同源，保证侧栏高亮与面包屑一致 */
const PARAM_LEAF: Array<{ re: RegExp; to: string }> = [
  { re: /^\/tasks\/[^/]+\/report$/, to: '/reports' },
  { re: /^\/tasks\/[^/]+$/, to: '/tasks/review' },
]

/** 侧栏高亮用叶子：精确优先，其次参数路由归属，最后最长前缀 */
export function activeLeaf(pathname: string): FlatLeaf | undefined {
  const hit = exactLeaf(pathname)
  if (hit) return hit
  const param = PARAM_LEAF.find((p) => p.re.test(pathname))
  if (param) return exactLeaf(param.to)
  return prefixLeaf(pathname)
}

/** 当前路径所属的一级模块名（侧栏自动展开用）；未命中导航返回 '' */
export function groupOf(pathname: string): string {
  return activeLeaf(pathname)?.group ?? ''
}

const leafTrail = (leaf: FlatLeaf) =>
  leaf.group === '智能检测' && leaf.to !== '/detect/assistant'
    ? [leaf.group, '专业工作台', leaf.label]
    : leaf.group
      ? [leaf.group, leaf.label]
      : [leaf.label]

/** 参数路由的面包屑（不在导航叶中） */
const PARAM_TRAIL: Array<{ re: RegExp; trail: (m: RegExpMatchArray) => string[] }> = [
  { re: /^\/tasks\/[^/]+\/report$/, trail: () => ['任务中心', '报告与综合研判'] },
  { re: /^\/tasks\/[^/]+$/, trail: () => ['任务中心', '结果显示与复核'] },
  {
    re: /^\/detect\/(fpi|borescope|infrared|dr|fracture|metallography)$/,
    trail: (m) => ['智能检测', '专业工作台', WORKBENCHES.find((w) => w.scene === m[1])?.label ?? m[1]],
  },
]

/** 面包屑：一级模块 /（归类标签）/ 叶名；参数路由走 PARAM_TRAIL */
export function trailOf(pathname: string): string[] {
  const exact = exactLeaf(pathname)
  if (exact) return leafTrail(exact)
  for (const p of PARAM_TRAIL) {
    const m = pathname.match(p.re)
    if (m) return p.trail(m)
  }
  const pre = prefixLeaf(pathname)
  return pre ? leafTrail(pre) : []
}

/** 页面标题（document.title）：导航叶名优先，其次参数路由单独命名，最后取面包屑末级 */
const TITLE_OVERRIDE: Array<{ re: RegExp; title: string }> = [
  { re: /^\/tasks\/[^/]+\/report$/, title: '检测报告' },
  { re: /^\/tasks\/[^/]+$/, title: '结果与复核' },
]

export function titleOf(pathname: string): string {
  if (!exactLeaf(pathname)) {
    const hit = TITLE_OVERRIDE.find((t) => t.re.test(pathname))
    if (hit) return hit.title
  }
  const t = trailOf(pathname)
  return t[t.length - 1] ?? '未找到页面'
}
