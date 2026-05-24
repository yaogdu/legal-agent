# Legal Agent PRD / 法律 Agent 产品需求文档

Version / 版本：v0.2  
Date / 日期：2026-05-22  
Status / 状态：Draft  
Target directory / 目标目录：`/Users/duyaoguang/Documents/agents/legal-agent`

## 1. Background / 背景

The core value of a legal agent is not simple Q&A. It is the ability to turn a legal task into an executable, traceable, reviewable, and recoverable business process.

法律 Agent 的核心价值不是简单问答，而是把法律任务拆成可执行、可追溯、可复核、可恢复的业务流程。

The system must solve four problems:

系统需要同时解决四类问题：

- Legal tasks are complex: user input is often missing facts, evidence, and explicit claims.  
  法律任务复杂：用户输入通常缺事实、缺证据、缺明确诉求。
- Legal knowledge is high-stakes: generated conclusions need support from statutes, cases, templates, or user material.  
  法律知识严肃：生成结论必须有法规、案例、模板或用户材料支撑。
- Agent execution is unstable: multi-step tasks and tool calls need recovery after failure.  
  Agent 执行不稳定：多步任务、工具调用、长任务失败后需要恢复。
- Legal risk is high: the system must not fabricate laws, cases, amounts, or evidence, and must not perform high-risk legal actions automatically.  
  法律风险高：不能编造法条、案例、金额、证据，也不能自动执行高风险法律动作。

Therefore, this system is positioned as a production-style agent runtime for legal business workflows. It supports task understanding, fact completion, legal RAG, tool calls, document generation, review, human approval, audit replay, and quality evaluation.

因此，本系统定位为面向法律业务场景的 Agent 生产系统，支持任务理解、事实补全、法律 RAG、工具调用、文书生成、结果复核、人工审批、审计回放和质量评测。

## 2. Product Positioning / 产品定位

### 2.1 One-Sentence Positioning / 一句话定位

Legal Agent is an intelligent agent system for legal document generation, case analysis, and similar-case retrieval. It uses legal RAG, tool calls, and Agent runtime governance to deliver explainable, reviewable, and recoverable legal task execution.

Legal Agent 是一个面向法律文书生成、案件分析、类案检索的智能体系统，通过法律 RAG、工具调用和 Agent Runtime 治理，实现可解释、可复核、可恢复的法律任务执行。

### 2.2 MVP Scope / 首期 MVP 范围

The MVP does not attempt to cover every legal domain. It focuses first on labor disputes.

MVP 不做全法律领域通用系统，先聚焦劳动争议。

Target capabilities:

目标能力：

- labor arbitration application generation / 劳动仲裁申请书生成；
- labor-dispute case analysis / 劳动争议案情分析；
- similar-case retrieval / 劳动争议类案检索；
- claim amount calculation assistance / 仲裁请求金额辅助计算；
- fact, citation, format, and risk review / 文书事实、引用、格式、风险复核。

Suggested phases:

建议交付阶段：

- Phase 1: close the loop for labor arbitration application generation.  
  Phase 1：先跑通劳动仲裁申请书生成闭环。
- Phase 2: add official legal data sources, Legal RAG, and evidence packs.  
  Phase 2：补齐官方法律数据源、Legal RAG 和 evidence pack。
- Phase 3: integrate AgentLedger tool ledger, checkpoint, approval, and replay.  
  Phase 3：接入 AgentLedger 的 tool ledger、checkpoint、approval、replay。
- Phase 4: add review, evaluation datasets, release gates, and human review console.  
  Phase 4：增加 Review、评测集、上线准入和人工复核后台。

### 2.3 Non-Goals / 非目标

MVP does not include:

MVP 阶段不做：

- automatic arbitration submission / 自动提交仲裁申请；
- replacing lawyers with deterministic legal conclusions / 自动替代律师给出确定性法律结论；
- all legal domains / 覆盖所有法律领域；
- training a foundation model from scratch / 从零训练大模型；
- complex litigation strategy simulation / 复杂庭审策略推演。

The output is positioned as legal document drafts, analysis suggestions, and risk reminders. Formal filing and high-risk advice require human confirmation.

系统输出定位为法律文书草稿、分析建议和风险提示，正式提交和高风险建议需要人工确认。

## 3. Users / 目标用户

### 3.1 Individual Users / C 端普通用户

Needs:

诉求：

- do not know how to write a labor arbitration application / 不知道如何写劳动仲裁申请书；
- do not know what claims can be raised / 不知道自己可以主张哪些请求；
- do not know what evidence to prepare / 不知道需要准备哪些证据。

Concerns:

关注点：

- readability / 结果是否易懂；
- missing information / 信息是否需要补充；
- document completeness / 文书格式是否完整；
- risk reminders / 是否有风险提示。

### 3.2 Legal Professionals / 法律从业者

Needs:

诉求：

- improve first-draft efficiency / 提高文书初稿效率；
- quickly organize facts, claims, and legal basis / 快速整理案情、请求、法律依据；
- quickly retrieve statutes and similar cases / 快速检索相关法条和类案。

Concerns:

关注点：

- citation accuracy / 引用是否准确；
- evidence traceability / 证据是否可追溯；
- professional document structure / 文书结构是否专业；
- human editing and review / 是否可以人工编辑和复核。

### 3.3 Enterprise Operators / 企业内部业务人员

Needs:

诉求：

- process legal consultation or document tasks in batches / 批量处理法律咨询或文书辅助任务；
- retain audit records / 保留审计记录；
- control high-risk outputs / 控制高风险输出。

Concerns:

关注点：

- tenant isolation / 权限隔离；
- task traceability / 任务可追踪；
- quality evaluation / 质量可评测；
- cost control / 成本可控。

## 4. Core Business Flow / 核心业务流程

### 4.1 Labor Arbitration Document Flow / 劳动仲裁申请书生成流程

Example user input:

用户输入示例：

```text
我被公司无故辞退，拖欠 2 个月工资，没有签劳动合同，帮我生成劳动仲裁申请书。
```

System flow:

系统流程：

```text
User input / 用户输入
-> Task understanding / 任务理解
-> Missing fact check / 缺失事实检查
-> Fact follow-up / 事实补全追问
-> Legal RAG retrieval / 法律 RAG 检索
-> Similar-case retrieval / 类案检索
-> Amount calculation / 金额计算
-> Template selection / 文书模板选择
-> Draft generation / 草稿生成
-> Fact verification / 事实校验
-> Citation verification / 引用校验
-> Format verification / 格式校验
-> Risk reminders / 风险提示
-> Human confirmation / 人工确认
-> Draft output / 输出文书草稿
```

### 4.2 Required Follow-Up Facts / 必须追问的信息

The agent must ask for missing information instead of fabricating it.

Agent 不能编造缺失信息，必须追问。

Typical required facts:

典型必需事实：

- applicant name / 申请人姓名；
- respondent company name / 被申请人公司名称；
- employment start date / 入职时间；
- employment end date / 离职时间；
- monthly salary / 月工资金额；
- unpaid salary months / 拖欠工资月份；
- whether a written labor contract was signed / 是否签署劳动合同；
- whether social insurance was paid / 是否缴纳社保；
- termination reason / 解除劳动关系原因；
- available evidence such as salary records, chat logs, attendance, labor contract, offer, badge / 是否有工资流水、聊天记录、考勤记录、劳动合同、offer、工牌等证据；
- expected claims / 期望主张的仲裁请求。

### 4.3 Output Package / 输出内容

The final output is a structured result package, not only a document.

最终输出不只是一篇文书，而是一个结构化结果包：

- labor arbitration application draft / 劳动仲裁申请书草稿；
- claim list / 仲裁请求列表；
- facts and reasons / 事实与理由；
- legal basis and source citations / 法律依据和引用来源；
- evidence list / 证据清单；
- missing information / 待补充信息；
- calculation explanation / 金额计算说明；
- risk reminders / 风险提示；
- human review suggestions / 人工复核建议。

## 5. Architecture / 总体架构

The system is divided into seven layers:

系统分为七层：

```text
Access Layer / 接入层
-> Task Understanding / 任务理解
-> Agent Orchestrator / Agent 调度
-> Legal RAG / 法律 RAG
-> Tool Layer / 工具层
-> Verification / 校验层
-> Runtime Governance / 运行治理
```

### 5.1 Access Layer / 接入层

Responsibilities:

职责：

- authentication / 用户鉴权；
- tenant identification / 租户识别；
- file upload / 文件上传；
- rate limiting / 限流；
- request audit / 请求审计。

Main objects:

主要对象：

- `user_id`
- `tenant_id`
- `request_id`
- `session_id`
- `uploaded_files`

### 5.2 Task Understanding / 任务理解层

Responsibilities:

职责：

- determine task type / 判断任务类型；
- determine legal domain / 判断法律领域；
- extract key facts / 提取关键事实；
- determine risk level / 判断风险等级；
- determine whether follow-up questions are required / 判断是否需要追问。

Supported MVP task types:

MVP 支持任务类型：

- `document_generation`
- `case_analysis`
- `case_search`

Legal domain:

法律领域：

- `labor_dispute`

### 5.3 Agent Orchestrator / Agent 调度层

Responsibilities:

职责：

- define workflow steps / 定义 workflow 步骤；
- schedule activities through Temporal / 通过 Temporal 调度 Activity；
- wait for user facts or approval signals / 等待用户事实补充或审批 signal；
- resume unfinished activities after worker failures / Worker 故障后恢复未完成 Activity。

### 5.4 Legal RAG / 法律 RAG

Responsibilities:

职责：

- retrieve statutes, templates, and cases / 检索法规、模板、案例；
- retrieve uploaded user material / 检索用户上传材料；
- produce evidence packs with source metadata / 生成带来源信息的 evidence pack。

### 5.5 Tool Layer / 工具层

Responsibilities:

职责：

- claim amount calculation / 仲裁请求金额计算；
- citation checks / 引用校验；
- document rendering / 文书渲染；
- format checks / 格式校验。

### 5.6 Verification / 校验层

Responsibilities:

职责：

- check factual consistency / 检查事实一致性；
- check citation existence / 检查引用是否存在；
- check document completeness / 检查文书完整性；
- identify unsupported or high-risk statements / 识别无依据或高风险表述。

### 5.7 Runtime Governance / 运行治理层

Responsibilities:

职责：

- persist run state / 持久化 run 状态；
- record tool ledger / 记录工具账本；
- store artifacts / 保存 artifacts；
- manage approvals / 管理审批；
- support audit replay / 支持审计回放；
- record costs and traces / 记录成本和 trace。

## 6. Runtime Principles / 运行原则

- The agent should ask for missing legal facts instead of inventing them.  
  Agent 必须追问缺失法律事实，不能编造。
- LLM output must be grounded by retrieved evidence or explicit user facts.  
  LLM 输出必须由检索证据或用户明确事实支撑。
- High-risk output requires human approval.  
  高风险输出需要人工审批。
- Each important step should be auditable and replayable.  
  每个关键步骤都应该可审计、可回放。
- Failure recovery is split between Temporal and AgentLedger: Temporal restores orchestration position, AgentLedger restores business context.  
  故障恢复由 Temporal 和 AgentLedger 分工：Temporal 恢复调度位置，AgentLedger 恢复业务上下文。

## 7. Related Documentation / 相关文档

- [README](README.md)
- [Usage / 使用方式](docs/USAGE.md)
- [Architecture / 架构原理](docs/ARCHITECTURE.md)
- [Operations / 运维](docs/OPERATIONS.md)
