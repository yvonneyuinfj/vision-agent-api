import type { SceneCode } from '../api/types'

export const SCENE_MAP: Record<SceneCode, string> = {
  borescope: '孔探',
  infrared: '红外',
  dr: 'X射线',
  fpi: '荧光',
  fracture: '断口',
  metallography: '金相',
}

export const SCENE_OPTIONS = (Object.keys(SCENE_MAP) as SceneCode[]).map((value) => ({
  value,
  label: SCENE_MAP[value],
}))

/** 场景视觉指纹（优化轮 D4 场景确认卡片）：一句话帮用户判断图像属于哪个场景。
 *  与 session_routing 提示词中的各类别指纹保持一致。 */
export const SCENE_FINGERPRINT: Record<SceneCode, string> = {
  borescope: '彩色可见光内腔视角，有光照高光/反射，主体为叶片、内壁等部件',
  infrared: '伪彩色温度场（铁红/彩虹/白热），画面平滑渐变，常带温度色标条',
  dr: '灰度"透视"图，可见零件内部轮廓与密度衬度（气孔/夹渣/焊缝）',
  fpi: '黑暗背景下高亮黄绿色荧光迹痕，零件轮廓暗淡，对比强烈',
  fracture: '灰度、强三维浮雕（韧窝/河流花样/辉纹），常带 μm 标尺与放大倍数',
  metallography: '平坦二维截面，晶界网络/相分布/夹杂物，可彩色，常带 μm 标尺',
}
