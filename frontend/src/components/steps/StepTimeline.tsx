import { Steps } from 'antd'
import type { StepTraceItem } from '../../api/types'
import { formatBeijing } from '../../utils/time'

/** 后端节点标识 → 中文展示名 */
const NODE_LABELS: Record<string, string> = {
  intent: '意图识别',
  validate: '输入校验',
  route: '场景路由',
  infer: '模型推理',
  normalize: '结果标准化',
  rag: '知识检索',
  grade: '判级',
  review_gate: '复核门',
  auto_report: '报告生成',
}

/** 任务执行链路时间线（末节点视为进行中） */
export default function StepTimeline({ steps }: { steps: StepTraceItem[] }) {
  if (!steps.length) return null
  return (
    <div className="glass-panel" style={{ padding: '8px 16px', borderRadius: 10 }}>
      <Steps
        size="small"
        direction="horizontal"
        items={steps.map((s, i) => ({
          title: NODE_LABELS[s.node] ?? s.node,
          description: `${formatBeijing(s.ts, 'HH:mm:ss')} ${s.message}`,
          status: i === steps.length - 1 ? 'process' : 'finish',
        }))}
        responsive
      />
    </div>
  )
}
