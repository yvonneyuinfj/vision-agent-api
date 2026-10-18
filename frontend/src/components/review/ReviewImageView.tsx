/** 复核轻量只读图像区（决策 Q6）：img + SVG 框叠加，三档图层切换
 *  （缺陷框叠加 / 原图 / 算法标注图）。完整 Konva 编辑器只在 Modal 与
 *  复核工作台挂载——聊天流里堆多张复核卡也不吃内存。
 *
 *  数据源（决策 Q7）：
 *  - 原图：task.input_uris（业务 URI 本身即下载端点）；
 *  - 缺陷框：standardized bbox（中断期已落库），SVG viewBox 用图像自然
 *    尺寸 + preserveAspectRatio=xMidYMid meet，与 object-fit:contain 的
 *    letterbox 完全对齐，框位零换算；
 *  - 算法标注图：review.artifacts（中断载荷）→ report.artifacts（终局）兜底，
 *    按当前图配对（annotatedArtifactForImage：fan-out __<stem> 后缀）。
 *    多图任务缺陷框/标注图均按当前显示图过滤（defectsForImage）——
 *    不混叠其他图的框，不张冠李戴别图的模型标注图。
 *
 *  人工修订标记可视化：human_rejected 灰虚线框 + 「已排除」，source=manual
 *  severity 色框 + 「人工」角标。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { EditOutlined } from '@ant-design/icons'
import { annotatedArtifactForImage, defectsForImage } from '../../viewer/taskImages'
import { drawableBBox as drawableBox, SEVERITY_COLORS } from '../../viewer/bbox'
import type { ArtifactRef, DefectItem } from '../../api/types'
import './ReviewImageView.css'

export type RivLayer = 'boxes' | 'original' | 'artifact'

interface Props {
  inputUris: string[]
  defects: DefectItem[]
  /** 复核期 = review.artifacts（中断载荷）；终局后 = report.artifacts */
  artifacts?: ArtifactRef[] | null
  height?: number
  /** 提供时渲染「人工标注」入口按钮（打开结构化编辑器 Modal） */
  onAnnotate?: () => void
}

const REJECTED_COLOR = '#9aa0a6'

export default function ReviewImageView({ inputUris, defects, artifacts, height = 300, onAnnotate }: Props) {
  const uris = inputUris ?? []
  // 多图 bbox 混叠修复：缺陷框按当前图过滤（image_name 溯源），
  // 算法标注图产物按当前图配对（fan-out __<stem> 后缀）——
  // 每张图只显示本图的框与本图的模型标注图，不混叠
  const [active, setActive] = useState(0)
  const activeDefects = useMemo(
    () => defectsForImage(defects, uris[active], uris[0]),
    [defects, uris, active],
  )
  const annotatedArt = useMemo(
    () => annotatedArtifactForImage(artifacts, uris[active]),
    [artifacts, uris, active],
  )
  const artifactEnabled = Boolean(annotatedArt)
  const hasBoxes = useMemo(() => activeDefects.some((d) => drawableBox(d) !== null), [activeDefects])

  const initial: RivLayer = hasBoxes ? 'boxes' : artifactEnabled ? 'artifact' : 'original'
  const [layer, setLayer] = useState<RivLayer>(initial)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const imgRef = useRef<HTMLImageElement>(null)

  // 图层不可用时回退（defects/artifacts 晚到的场景）
  const effLayer: RivLayer =
    layer === 'boxes' && !hasBoxes
      ? artifactEnabled ? 'artifact' : 'original'
      : layer === 'artifact' && !artifactEnabled
        ? 'original'
        : layer
  const src = effLayer === 'artifact' ? annotatedArt?.uri ?? uris[active] : uris[active]

  // 换图/换层重置自然尺寸；缓存图（complete）直接读取
  useEffect(() => {
    setNatural(null)
    const img = imgRef.current
    if (img?.complete && img.naturalWidth > 0) {
      setNatural({ w: img.naturalWidth, h: img.naturalHeight })
    }
  }, [src])

  if (uris.length === 0 && !artifactEnabled) {
    return <div className="riv riv-empty-card" style={{ '--riv-h': `${height}px` } as React.CSSProperties}><span>暂无输入图像</span></div>
  }

  const fontSize = natural ? Math.max(12, Math.round(natural.w / 48)) : 14
  const strokeWidth = natural ? Math.max(2, natural.w / 340) : 2

  return (
    <div className="riv" style={{ '--riv-h': `${height}px` } as React.CSSProperties}>
      <div className="riv-bar">
        <button
          type="button"
          className={`riv-layer${effLayer === 'boxes' ? ' active' : ''}`}
          disabled={!hasBoxes}
          onClick={() => setLayer('boxes')}
        >
          缺陷框叠加
        </button>
        <button
          type="button"
          className={`riv-layer${effLayer === 'original' ? ' active' : ''}`}
          onClick={() => setLayer('original')}
        >
          原图
        </button>
        {artifactEnabled && (
          <button
            type="button"
            className={`riv-layer${effLayer === 'artifact' ? ' active' : ''}`}
            onClick={() => setLayer('artifact')}
          >
            算法标注图
          </button>
        )}
        {onAnnotate && (
          <button type="button" className="riv-annotate" onClick={onAnnotate}>
            <EditOutlined /> 人工标注
          </button>
        )}
      </div>

      <div className="riv-stage">
        {src ? (
          <>
            <img
              ref={imgRef}
              className="riv-img"
              src={src}
              alt={effLayer === 'artifact' ? '算法标注图' : '检测原图'}
              onLoad={(e) => {
                const t = e.currentTarget
                if (t.naturalWidth > 0) setNatural({ w: t.naturalWidth, h: t.naturalHeight })
              }}
            />
            {effLayer === 'boxes' && natural && (
              <svg
                className="riv-svg"
                viewBox={`0 0 ${natural.w} ${natural.h}`}
                preserveAspectRatio="xMidYMid meet"
                data-testid="riv-boxes"
              >
                {activeDefects.map((d, i) => {
                  const bbox = drawableBox(d)
                  if (!bbox) return null
                  const rejected = Boolean(d.human_rejected)
                  const color = rejected
                    ? REJECTED_COLOR
                    : SEVERITY_COLORS[d.severity ?? 'low'] ?? '#6ba3ff'
                  const [x1, y1, x2, y2] = bbox
                  const label = `A${i + 1}${rejected ? ' 已排除' : d.source === 'manual' ? ' 人工' : ''}`
                  return (
                    <g key={d.defect_id ?? `d-${i}`}>
                      <title>
                        {`${d.defect_name_cn || d.defect_type}${d.confidence != null ? ` · 置信 ${d.confidence.toFixed(2)}` : ''}${rejected ? ' · 人工复核排除' : ''}`}
                      </title>
                      <rect
                        x={x1}
                        y={y1}
                        width={Math.max(1, x2 - x1)}
                        height={Math.max(1, y2 - y1)}
                        fill="none"
                        stroke={color}
                        strokeWidth={strokeWidth}
                        strokeDasharray={rejected ? `${fontSize / 2} ${fontSize / 3}` : undefined}
                        opacity={rejected ? 0.8 : 1}
                      />
                      <text
                        x={x1}
                        y={Math.max(fontSize, y1 - fontSize / 3)}
                        fontSize={fontSize}
                        fill={color}
                        stroke="#0b1020"
                        strokeWidth={fontSize / 6}
                        paintOrder="stroke"
                      >
                        {label}
                      </text>
                    </g>
                  )
                })}
              </svg>
            )}
            <span className="riv-badge">
              {effLayer === 'boxes' ? '标注图（缺陷框叠加）' : effLayer === 'artifact' ? '算法标注图（模型渲染）' : '原图'}
            </span>
          </>
        ) : (
          <span className="riv-nosrc">当前图层无图像</span>
        )}
      </div>

      {uris.length > 1 && (
        <div className="riv-strip">
          {uris.map((u, i) => (
            <img
              key={u}
              className={`riv-thumb${i === active ? ' active' : ''}`}
              src={u}
              alt={`输入图 ${i + 1}`}
              onClick={() => setActive(i)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
