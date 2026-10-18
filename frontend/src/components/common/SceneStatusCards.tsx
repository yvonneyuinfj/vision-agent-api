/* 专业工作台场景状态卡：首页与运行监控共用同一组「场景 × 注册表能力 × 就绪态」卡片。
 * 卡片条数＝调用方传入的 workbenches（页面传 GET /scenes 的目录结果，缺省为内置六条，决策 52）。
 * 三态必须区分，不可塌成一种：caps=undefined 注册表读取中 / 单场景 cap=null 该场景未注册 /
 * status≠enabled 降级——把读取失败说成服务异常是假故障，反之亦然。 */
import { Link } from 'react-router-dom'
import { CAPABILITY_TYPE_TEXT, type CapabilitySummary } from '../../api/capabilities'
import { WORKBENCHES, workbenchPath, type WorkbenchDef } from '../../constants/workbench'
import { Icon } from '../ui/Icon'
/* 场景图标：docs/切图 设计资产（金相无切图，回退符号） */
import capFpi from '../../assets/icons/cap_fpi.png'
import capPore from '../../assets/icons/cap_pore.png'
import capXray from '../../assets/icons/cap_xray.png'
import capThermal from '../../assets/icons/cap_thermal.png'
import capFracture from '../../assets/icons/cap_fracture.png'
import './SceneStatusCards.css'

const SCENE_ICON: Record<string, string> = {
  fpi: capFpi,
  borescope: capPore,
  dr: capXray,
  infrared: capThermal,
  fracture: capFracture,
}
const SCENE_GLYPH: Record<string, string> = { metallography: '🔬' }

/** 能力就绪态：undefined = 注册表未取到（未知），null = 注册表内该场景无工具（未注册）。 */
export function capState(cap: CapabilitySummary | null | undefined): { text: string; cls: string } {
  if (cap === undefined) return { text: '未知', cls: 't-pending' }
  if (cap === null) return { text: '未注册', cls: 't-pending' }
  return cap.status === 'enabled' ? { text: '就绪', cls: 't-success' } : { text: '降级', cls: 't-review' }
}

export interface SceneCard {
  wb: WorkbenchDef
  cap: CapabilitySummary | null | undefined
}

/** caps：null＝注册表尚未取到（含读取失败，卡片显示「未知」而非「未注册」）；
 *  数组＝已取到，此时某场景匹配不到才是真的「未注册」。
 *  workbenches：场景项来源（决策 52 的 `GET /scenes`），缺省用内置六条。 */
export function buildSceneCards(
  caps: CapabilitySummary[] | null,
  workbenches: WorkbenchDef[] = WORKBENCHES,
): SceneCard[] {
  return workbenches.map((wb) => ({
    wb,
    cap: caps === null ? undefined : caps.find((c) => c.scene === wb.scene) ?? null,
  }))
}

export default function SceneStatusCards({
  caps,
  workbenches,
}: {
  caps: CapabilitySummary[] | null
  workbenches?: WorkbenchDef[]
}) {
  return (
    <div className="scene-cards">
      {buildSceneCards(caps, workbenches).map(({ wb, cap }) => {
        const st = capState(cap)
        return (
          <Link className="card hoverable scene-card" to={workbenchPath(wb.scene)} key={wb.scene}>
            <div className="row" style={{ alignItems: 'flex-start' }}>
              <span className="scene-ico">
                {SCENE_ICON[wb.scene] ? (
                  <img src={SCENE_ICON[wb.scene]} alt="" />
                ) : (
                  SCENE_GLYPH[wb.scene] ?? <Icon name="flask" size={17} />
                )}
              </span>
              <div className="grow">
                <div className="dcard-t">{wb.label}</div>
                <div className="dcard-id">
                  {wb.en} · {cap ? cap.capability_id : st.text}
                </div>
              </div>
              <span className={`tag ${st.cls}`}>{st.text}</span>
            </div>
            <div className="dcard-d">
              {cap?.display_name || (cap === undefined ? '能力注册表读取中，稍后自动更新' : wb.input)}
            </div>
            <div className="dcard-f">
              <span>{wb.params === 'frozen' ? '参数表已上线' : '契约未冻结'}</span>
              {cap && <span>{CAPABILITY_TYPE_TEXT[cap.capability_type]}</span>}
            </div>
          </Link>
        )
      })}
    </div>
  )
}
