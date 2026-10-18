import { maxMBOf } from './fileValidation'
import type { FileGroup } from './Dropzone'

/** 场景联动输入要求：工作台与新建任务页共用同一份声明，避免两处规则漂移。
 *  输入物料要求与后端 `input_required` 及方案 §输入要求同源。 */
export interface LinkageBlock {
  group: FileGroup
  accept: string
  multiple: boolean
  /** 大 Dropzone 的标题与副说明；小按钮式（红外 CSV）用 btnText */
  label?: string
  sub?: string
  btnText?: string
  miss: string
}

export interface SceneLinkage {
  tag: string
  title: string
  hint: string
  blocks: LinkageBlock[]
}

/** 键为 scene 路由键：目录可长（决策 52），**没有条目＝该场景无配套文件要求**，
 *  而不是"规则缺失"——所以取数用 string 索引、由调用方按 undefined 呈现。 */
export const SCENE_LINKAGE: Record<string, SceneLinkage | undefined> = {
  infrared: {
    tag: '红外联动 · 必传',
    title: '辐射值 CSV',
    hint: `像素级温度矩阵 · ≤ ${maxMBOf('csv')}MB`,
    blocks: [
      {
        group: 'ir',
        accept: '.csv',
        multiple: false,
        btnText: '+ 添加 CSV',
        miss: '红外：缺辐射值 CSV',
      },
    ],
  },
  dr: {
    tag: 'X 射线联动 · 必传',
    title: '参考图 + 标定信息',
    hint: '标定文件由探伤设备导出',
    blocks: [
      {
        group: 'xray-ref',
        accept: '.jpg,.jpeg,.png,.tif,.tiff',
        multiple: true,
        label: '上传参考图',
        sub: `JPG / PNG / TIFF ≤ ${maxMBOf('image')}MB`,
        miss: 'X 射线：缺参考图',
      },
      {
        group: 'xray-calib',
        accept: '.json,.xml',
        multiple: true,
        label: '上传标定信息',
        sub: 'JSON / XML',
        miss: 'X 射线：缺标定 JSON/XML',
      },
    ],
  },
}

/** 当前所选场景缺哪些配套文件（齐备则返回空数组） */
export function missingLinkage(scenes: readonly string[], countOf: (group: FileGroup) => number): string[] {
  const miss: string[] = []
  for (const scene of scenes) {
    const link = SCENE_LINKAGE[scene]
    if (!link) continue
    for (const block of link.blocks) {
      if (countOf(block.group) === 0) miss.push(block.miss)
    }
  }
  return miss
}

export const MAIN_ACCEPT = '.jpg,.jpeg,.png,.tif,.tiff,.mp4,.mov,.avi,.webm'
