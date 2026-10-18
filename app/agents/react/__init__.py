"""ReAct 轨包：真自主智能体（双轨并存的 agent 轨）。

架构定案（18 项决策之 #6/#7/#9）：
- 判断类动作归智能体（选场景、定参数、自救重试、知识检索时机、提交时机）；
- 确定性动作焊在出口：判级按 grade_rules.yaml 终裁、人工复核门运行时强制、
  报告骨架确定性组装；
- 手工 ReAct 循环 StateGraph：agent ⇄ tools，submit_final 之后接出口安检
  （exit_gate → grading →（复核门）→ generate_report），复用现有中断/恢复机器。

图结构：
    START → bootstrap_agent → react_agent ⇄ react_tools
    react_agent（无工具调用）→ nudge → react_agent
    react_tools（已提交）→ exit_gate → grading →[复核门]→ generate_report → END
    任意节点 error → __fail__ → explain_error → END（拒检也走此路径）
"""
