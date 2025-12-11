# Code Review Agent

- [Code Review Agent](#code-review-agent)
  - [Background](#background)
  - [设计实现](#设计实现)
    - [Step 1: Data Ingestion & Knowledge Base Construction](#step-1-data-ingestion--knowledge-base-construction)
      - [多源数据提取 (Extraction)](#多源数据提取-extraction)
      - [混合切片 (Hybrid Chunking)](#混合切片-hybrid-chunking)
      - [向量化存储与更新 (Embedding & Upsert)](#向量化存储与更新-embedding--upsert)
    - [Step 2: RAG Engine / 检索增强生成](#step-2-rag-engine--检索增强生成)
    - [Step 3: Multi-Perspective Verification / 多维校验机制](#step-3-multi-perspective-verification--多维校验机制)
    - [Step 4: Agent Reasoning & Output / 推理与结构化输出](#step-4-agent-reasoning--output--推理与结构化输出)


## Background

1. 痛点

企业代码业务逻辑性强，尤其在多人协作或快速迭代中，容易出现逻辑漏洞、重复代码或安全隐患。传统 Code Review 依赖人工经验，效率低且主观性强。而现有 AI Code Review 工具多聚焦语法细错，缺乏对业务逻辑、团队编码规范的深度理解与适配。


1. 问题

如何设计一个具备上下文理解能力的 AI Code Review 智能体，能结合项目历史、团队规范和业务语义，提供更具洞察力的代码审查建议？

## 设计实现

![picture 2](images/8d4c77a027c8594a6d8220ac05146ebed55fab1db52e88f9744ca96508dad128.png)  

### Step 1: Data Ingestion & Knowledge Base Construction

> 目标：将非结构的代码和文档转化为Agent可理解的语义索引。


#### 多源数据提取 (Extraction)

> TODO:强调一下项目历史、团队规范、业务语义在哪

从repository中提取以下数据：

1. 业务语义层（文档）
   1. 产品需求文档：理解功能背后的业务目的、流程流转及边界条件
   2. 技术/架构设计文档：掌握模块职责划分、数据流向及核心架构约束
   3. 数据库/API 文档：明确数据字段的语义约束及接口调用规范。
2. 项目关键历史记录
   1. 历史Code review记录：提取团队高频关注点（Review Comments）和常见问题
   2. Bug Root Cause 分析：索引历史Bug的成因与修复方案，建立“易错点知识库”
3. 代码
   1. 结构化摘要：**不存储全量代码**（避免噪声、高成本、检索慢），而是通过**静态分析生成**文件级、类级、函数级摘要。
   2. 存储关键代码/热点代码/代表性代码（高频修改、bug多发区）
   3. <span style="color: #1E88E5;">**[NEW] 代码知识图谱 (Code Knowledge Graph)**</span>：
      1. 利用 LSP (Language Server Protocol) 或 AST 提取代码的依赖关系、调用链和继承关系。
      2. 目的：解决向量检索无法理解“引用依赖”的问题，为后续的“影响面分析”提供精确的数据支撑。
      3. <span style="color: #1E88E5;">**[NEW] 仓库地图 (Repository Map)**</span>：生成压缩的项目骨架图（Tree structure），帮助 Agent 理解文件在项目全局中的位置。

#### 混合切片 (Hybrid Chunking)

对所有数据进行embedding，不同类型数据使用不同的分块/切片策略，以保留语义完整性：

* 文档类：按“逻辑段落”或“功能章节”切片，保留业务场景的连贯性。
* 代码结构：按“模块/函数”粒度切片，严禁在函数内部强行截断，确保逻辑闭环。
* 变更记录 (Diff/PR)：按 Diff Hunk（变更块）切片，绑定对应的 Review 评论。
* Bug 知识：采用“问题现象 + Root Cause + 解决方案”的三段式结构化切片。


#### 向量化存储与更新 (Embedding & Upsert)

* 模型选择：使用代码理解能力强的 Embedding 模型（如 OpenAI text-embedding-3-large或CodeBERT）
* 存储引擎：存入Milvus向量数据库。
* 动态增量更新机制：代码Merge后自动触发增量更新，确保Agent的知识“与时俱进”（CI/CD触发机制）


### Step 2: RAG Engine / 检索增强生成

> 目标：精准召回相关上下文，为LLM提供上下文证据，以免幻觉。

每当开发者提交新的PR时，触发Code Review以下工作流：

<span style="color: #1E88E5;">**0. [NEW] 捕获PR/MR核心意图 (Intent Capture)**
   - 核心输入：首先获取 **Pull Request (PR) / Merge Request (MR) 的描述**（Description）和**标题**。
   - 价值：这是本次代码变更的**最高层业务需求（Business Intent）**。LLM将使用此意图作为第一参考，来判断代码是否**有效**地解决了该问题，同时帮助引导后续的文档检索。</span>

1. 基于PR的代码变更内容（diff）生成查询embedding，在此处增加**查询重写与扩充 (Query Expansion/Hyde)** 策略：先让 LLM 根据 <span style="color: #1E88E5;">**PR描述和**</span> Diff 生成一段“假设性的技术摘要”或“假设性功能描述”，再用这段自然语言进行检索，以提高非代码文档（如 PRD）的召回率。
   
   并在向量数据库中检索与该变更最相关的上下文信息，包括：
   1. 相似的历史变更记录（相似 diff / PR）
   2. 与本次修改相关的技术文档段落
   3. 涉及本模块的历史实现或结构化代码摘要
   4. 该模块历史发生过的 Bug 及其修复方案

2. <span style="color: #1E88E5;">**[NEW] 分级检索策略 (Hierarchical Retrieval)**</span>
   1. 第一级：先检索相关的“文件摘要”或“模块说明”。
   2. 第二级：仅当相关度较高时，再展开检索该文件内部的具体代码块或历史 Bug，避免一次性召回大量无关细节污染上下文。

3. 对检索结果执行Reranking（重排序）
   1. 在初步检索得到的候选文档中，使用更细粒度的排序模型（如**cross-encoder**）对候选结果进行重新评分
   2. 让最具语义相关性、最能解释当前变更的文档排在前面；可以极大减少输入给LLM的噪音，剔除“向量接近但语义并不真正相关”的内容

4. 构造LLM的输入结构（Prompt Assembly）
   1. 将以下信息组织成统一的上下文输入：
      <span style="color: #1E88E5;">a. **本次变更的需求说明（MR/PR Description & Title）**：为LLM提供本次审查的业务目标和上下文。</span>
      b. 当前PR的diff（变更内容）
      c. Rerank后的top-k检索结果（文档段落、历史变更、相关代码上下文、bug修复记录等）
      d. 同时在Prompt中明确说明：LLM**必须基于给定证据进行分析**，**禁止无依据猜测，防止产生幻觉**；输出需要聚焦逻辑风险、重复代码、违反规范、安全隐患等 Code Review关切点


### Step 3: Multi-Perspective Verification / 多维校验机制

> 目标：结合规则引擎的“硬约束”与 LLM 的“软推理”。

在此阶段，系统将并行执行两层校验，确保审查既符合行业标准，又符合团队习惯。

1. 硬约束校验：**静态分析** (Static Analysis - The "Hard Truth")
   1. 执行动作：集成工业级工具（Semgrep / CodeQL / SonarQube）对变更代码进行扫描 <span style="color: #1E88E5;">（注：针对 Golang 增加 `golangci-lint` 和 `govulncheck`）</span>。
   2. 作用：可以给出安全缺陷、常见错误、风险路径、空指针/未处理异常、输入未校验等静态分析结果
   3. 策略：将工具输出的JSON报告直接作为“**不可辩驳的事实**”输入给LLM，要求LLM只可解释与补充

2. 软约束对齐：**上下文合规性检查** (Contextual Alignment - The "Soft Truth")
   1. 执行动作：LLM 结合 Step 2 检索到的RAG 上下文，对代码进行语义级比对。
   2. 核心价值：解决传统工具无法理解“业务规则”的痛点。
      1. 架构一致性：检查是否违反了检索到的《技术设计文档》中的分层约束（例如：“检测到在 Controller 中直接编写了复杂的业务过滤逻辑，违背了文档中定义的 Service 职责下沉原则”）。
      2. 业务逻辑闭环：对比《PRD 文档》中的边界条件（例如：“需求文档提到状态流转需校验用户权限，但当前代码 Diff 中未发现鉴权逻辑”）。
      3. 风格一致性：对比检索到的“相似历史代码”，检查命名习惯和异常处理方式是否与团队过往习惯一致。

3. <span style="color: #1E88E5;">**[NEW] 自我反思循环 (Critic Agent / Self-Correction)**</span>
   1. 引入“批评家”角色 (Critic Agent)：在最终输出前，对 Reviewer Agent 生成的建议进行反向校验。
   2. 校验逻辑：检查建议是否引入了新 Bug？是否与静态分析结果冲突？建议是否过于主观？
   3. 目的：大幅降低误报率 (False Positives)，提升开发者信任度。


### Step 4: Agent Reasoning & Output / 推理与结构化输出

> 目标：输出不仅要对，还要有用

LLM 根据上述信息生成最终报告，格式如下：

1. 审查结论 (Verdict)：
   1. BLOCKER：发现安全漏洞或破坏核心业务逻辑（必须修复）。
   2. WARN：代码异味、不符合规范或潜在性能问题（建议修复）。
   3. INFO：代码风格建议或很好的实现（可选）。
   4. <span style="color: #1E88E5;">**EDUCATIONAL**</span>：针对初级工程师的“教学模式”，指出代码虽然没错但写法不地道 (Non-idiomatic Go code) 的写法，并提供最佳实践对比。
2. 深度证据链 (Evidence Chain)：
   1. 引用：指向具体的架构**文档章节**或相似的**历史PR**链接（“参考上次@UserA修复的 Bug #123”）。
   2. 事实：引用**静态分析**的具体报错行。
3. 修复建议 (Actionable Advice)：
   1. 提供具体的Git Patch代码块（可以直接Apply）
   2. 推荐补充的单元测试用例（针对边缘情况）
   3. 影响面分析：修改此函数可能会影响哪些下游模块 <span style="color: #1E88E5;">（注：利用 Step 1 构建的**代码知识图谱**进行精确的依赖反查，而非仅靠 LLM 猜测）</span>。
4. 置信度披露 (Confidence & Uncertainty)：
   1. 告诉开发者哪些地方AI不确定，可能误报，建议人工复核(Human-in-the-loop)

5. <span style="color: #1E88E5;">**[NEW] 反馈闭环 (Feedback Loop)**</span>
   1. 机制：追踪开发者对 Review 建议的操作（接受/拒绝/忽略）。
   2. 学习：将“被拒绝的建议”作为负样本存入向量库，防止 Agent 在未来重复提出类似的无效建议。