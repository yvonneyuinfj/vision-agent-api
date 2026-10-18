/* 缺陷修订编辑 Modal（决策 Q5/Q6）：复核卡「人工标注」与复核工作台共用。
   左：完整 Konva Viewer（编辑态渲染 DefectEditLayer，自由笔迹标注层仍可用——
   双模式共存，笔迹不提交）；右：DefectEditPanel 结构化修订列表。
   完成 = 关闭 Modal 保留草稿（提交复核时随 revise 上送）；取消 = 丢弃。 */
import { useEffect } from 'react'
import { Modal } from 'antd'
import Viewer from '../../viewer/Viewer'
import { buildTaskImages } from '../../viewer/taskImages'
import { pickArtifactImagePair } from '../../utils/artifacts'
import DefectEditPanel from './DefectEditPanel'
import { useDefectEditStore } from '../../store/useDefectEditStore'
import type { ArtifactRef, ClassDefItem, DefectItem } from '../../api/types'

interface Props {
  open: boolean
  taskId: string
  inputUris: string[]
  /** 修订基线（detail.standardized）；会话存续期间重开不覆盖草稿 */
  defects: DefectItem[]
  artifacts?: ArtifactRef[] | null
  classDefs?: ClassDefItem[]
  onClose: () => void
}

export default function DefectEditModal({ open, taskId, inputUris, defects, artifacts, classDefs, onClose }: Props) {
  const openEditor = useDefectEditStore((s) => s.openEditor)
  const closeEditor = useDefectEditStore((s) => s.closeEditor)

  // 打开即进入编辑会话（同任务重开保留未提交草稿，openEditor 内部判定）
  useEffect(() => {
    if (open && taskId) openEditor(taskId, inputUris[0] ?? '', defects)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, taskId])

  const images = buildTaskImages({ input_uris: inputUris })
  const annotatedSrc = pickArtifactImagePair(artifacts).annotated?.uri ?? null
  const draft = useDefectEditStore((s) => (s.open && s.taskId === taskId ? s.draft : null))

  return (
    <Modal
      open={open}
      title="人工标注 · 缺陷修订"
      width="min(1240px, 95vw)"
      okText="完成"
      cancelText="取消（丢弃修订）"
      onOk={onClose}
      onCancel={() => {
        closeEditor()
        onClose()
      }}
      styles={{ body: { height: 'min(68vh, 660px)', padding: 0 } }}
      destroyOnHidden
    >
      <div style={{ display: 'flex', height: '100%' }}>
        <div style={{ flex: 1, minWidth: 0, position: 'relative', background: '#0b0e14' }}>
          {images.length > 0 && draft && (
            <Viewer
              taskId={taskId}
              images={images}
              defects={draft}
              annotatedSrc={annotatedSrc}
              artifacts={artifacts ?? null}
              onSelectDefect={() => {}}
              onLayerSwitch={() => {}}
              layerLabel="原图"
              layerIsArtifact={false}
            />
          )}
        </div>
        <div
          style={{
            width: 336,
            flexShrink: 0,
            borderLeft: '1px solid var(--border)',
            overflowY: 'auto',
            background: 'var(--surface)',
          }}
        >
          <DefectEditPanel classDefs={classDefs} />
        </div>
      </div>
    </Modal>
  )
}
