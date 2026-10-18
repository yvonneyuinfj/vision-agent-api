import { Modal } from 'antd'
import KnowledgeCard from '../../components/knowledge/KnowledgeCard'
import type { KnowledgeItem } from '../../api/types'

interface Props {
  taskId: string
  citation: KnowledgeItem | null
  onClose: () => void
}

/** 引用原文预览弹窗：markdown 渲染命中片段 + 上下文（复用 KnowledgeCard） */
export default function CitationModal({ taskId, citation, onClose }: Props) {
  return (
    <Modal open={!!citation} onCancel={onClose} footer={null} width={860} title="引用原文预览">
      {citation && <KnowledgeCard taskId={taskId} citation={citation} onClose={onClose} />}
    </Modal>
  )
}