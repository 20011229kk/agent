# QA Agent Suite — Requirements

**状态：** 已定稿（经四轮外部 review 收敛）
**版本：** 0.1.0
**平台事实来源：** `docs/capability-matrix.md`（实测优先于官方文档）

---

## 问题陈述

测试团队的效率瓶颈与质量风险不在缺少 AI 工具，而在流程中的四处断裂：

1. **需求断裂** — 验收标准缺失、非功能需求（性能/兼容/权限/幂等）遗漏，到测试阶段才暴露。
2. **追溯断裂** — 需求、用例、自动化脚本、缺陷各自存放，无人能回答"这条需求测没测到"。
3. **归因断裂** — 用例失败靠人肉翻日志，产品缺陷经常被当成 flaky 重跑掉，形成缺陷逃逸。
4. **门禁断裂** — 准出结论靠口头汇报；为让流水线变绿而 skip 测试、弱化断言、下调阈值无机制拦截。

## 术语

| 术语 | 含义 |
|---|---|
| REQ | 需求基线中一条经确认的验收标准，覆盖率的分母单位 |
| TC | 测试用例（`qa/cases/**`），通过 `covers` 关联 REQ |
| AT | 自动化测试，通过 `@case("TC-x")` 标记关联 TC，**由实际 collect 发现** |
| attempt | 一次 AT 执行记录（原始报告中的 test id + param + 序号） |
| `policy_version` | 门禁执行规则的版本，取自 `target_sha` 上已批准版本 |
| `baseline_version` | 本次需求基线版本，取针对本次变更已确认的版本 |
| 合并门禁 | 受保护分支的 CI required check，本体系唯一门禁 |
| 发布准出 | 消费合并门禁 + 环境验证 + 回归的另一层决策 |

---

## R1 单一入口

**User Story:** 作为测试工程师，我希望只记一个 agent 名字就能开工，不必记住每个子流程叫什么。

- **WHEN** 用户向 `qa-lead` 描述任意测试意图，**THEN** 系统 SHALL 识别所处阶段并委派给对应专职 agent，且在回复中说明委派对象与理由。
- **IF** 用户意图跨越多个阶段，**THEN** 系统 SHALL 先确认本次范围，**SHALL NOT** 在未确认范围时批量执行。
- `qa-lead` SHALL NOT 具备 write 或 shell 工具。
- `qa-lead` 表达"不写"SHALL 通过 `tools` 省略实现，**SHALL NOT** 使用 `permissions` 的 deny 规则（deny 按交集传播至子代理，会禁掉子代理的写）。

## R2 需求可测性左移

**User Story:** 作为测试工程师，我希望需求进来当天就拿到问题清单，而不是等到执行阶段才发现测不了。

- **WHEN** 提供需求文档或需求链接，**THEN** 系统 SHALL 输出歧义项、缺失的验收标准、遗漏的非功能维度（性能/并发/兼容/权限/安全/可观测性）、风险等级与建议测试范围。
- **IF** 需求中某条描述无法推导出可验证的预期结果，**THEN** 系统 SHALL 标记为阻塞项，**SHALL NOT** 自行补全需求内容。
- **WHILE** 存在未关闭的阻塞项，系统 SHALL 在后续阶段的输出中持续列出该项。

## R3 用例设计可覆盖、可验证

**User Story:** 作为测试工程师，我希望用例是方法论驱动的，而不是凭感觉列几条。

- **WHEN** 需求含数值范围、长度限制或枚举约束，**THEN** 系统 SHALL 生成 min-1 / min / max / max+1 及非法值用例。
- **WHEN** 需求涉及状态流转、权限、并发或重试，**THEN** 系统 SHALL 生成状态迁移、越权、幂等与竞态用例。
- 每条用例 SHALL 包含可机器校验的预期结果；**SHALL NOT** 使用"正常显示""功能可用"一类无法验证的描述。
- 用例优先级 SHALL 按风险判定；**SHALL NOT** 采用"每条验收标准至少 1 条 P0"的规则（会使 P0 失去风险区分作用）。
- 每条用例 SHALL 标注 `covers`（关联 REQ）与 `manual`（是否人工执行）。

## R4 全链路追溯

**User Story:** 作为测试负责人，我希望随时查到覆盖缺口，并且这个数字不能被人为做漂亮。

- 覆盖率分母 SHALL 来自独立的需求基线（`qa/baseline/**`），**SHALL NOT** 由追溯矩阵自身决定。
- **WHEN** 用例或自动化脚本产出，**THEN** 系统 SHALL 可由脚本派生出 `REQ ↔ TC ↔ AT ↔ attempt` 映射；`matrix.csv` SHALL 为派生产物且无 agent 写入者。
- 系统 SHALL 分别统计**已设计 / 已自动化 / 已执行 / 已通过**，**SHALL NOT** 合并表述为"已测到"。
- **已自动化** SHALL 来自实际 collect 到的测试标记，**SHALL NOT** 采信清单声明。
- `manual: true` 的 TC **SHALL NOT** 计入可自动化用例分母，但 SHALL 计入必测范围校验。
- **WHILE** 存在未映射的基线条目、错标（引用不存在的 TC ID）或漏标（非 manual 的 TC 无 AT），校验脚本 SHALL 以非零退出码失败并列出缺口。
- **IF** 基线内容哈希变更，**THEN** 关联证据 SHALL 标记为 STALE。
- 手工用例无 AT、通过用例无缺陷，**SHALL NOT** 被判为错误。

## R5 失败必须归因

**User Story:** 作为测试工程师，我不希望失败被囫囵重跑掉，尤其是并发缺陷。

- **WHEN** 测试失败，**THEN** 系统 SHALL 在两条轴上分别归类：根因 `UNKNOWN / ENV / DATA / SCRIPT / PRODUCT / DEPENDENCY`，稳定性 `稳定失败 / 间歇失败 / 未确认`，并附证据路径。
- **IF** 证据不足以判定根因，**THEN** 系统 SHALL 记 `UNKNOWN`，**SHALL NOT** 猜测。
- `FLAKY` **SHALL NOT** 作为根因取值。
- **WHEN** 观察到间歇失败，**THEN** 系统 SHALL 附 attempt 序列作为稳定性证据，**SHALL NOT** 据此排除 `PRODUCT` 根因。

## R6 准出结论基于证据

**User Story:** 作为测试负责人，我需要可审计的判定，而不是一句"测完了没问题"。

- 必测范围 SHALL 来自经确认的执行计划（`qa/plan/**/required-scope.yaml`），**SHALL NOT** 由实际运行结果反向决定。
- **WHEN** 请求门禁评估，**THEN** 系统 SHALL 按此顺序判定：任一阻断项明确失败 → `FAIL`；无明确失败但必需证据不完整 → `INCOMPLETE`；全部必需检查满足 → `PASS`。
- 证据不完整 SHALL 包含：必测项未执行、结果缺失、执行中断、证据版本不匹配、基线 STALE 或未确认、手工必测项缺证据、reviewer 阻断未关闭。
- 缺陷跟踪系统导出清单 SHALL 对每条 `records[]` 要求非空字符串 `id` 与格式正确的
  SHA-256（64 位十六进制，可带 `sha256:` 前缀）；缺失、`null`、空字符串、错误类型、错误
  长度或非十六进制 SHALL 判 `INCOMPLETE`，**SHALL NOT** 因摘要为空而跳过内容核验。
- **IF** 缺陷文件在导出后被改为 `closed: true`，**THEN** 删除或清空清单摘要
  **SHALL NOT** 使 `DEFECT_RECORD_MODIFIED` 消失并得到 PASS；缺摘要本身 SHALL 产生
  `DEFECT_EXPORT_SHA256_INVALID`。
- workflow 变量静态检查 SHALL 按源码位置处理受支持的线性语法：未来赋值
  **SHALL NOT** 提前满足当前引用；`${X:-fallback}` 只保护当前展开，**SHALL NOT**
  使后续裸 `$X` 可见；`${X:=fallback}` 可在**右侧引用检查完成后**使后续引用可见。
  默认值右侧的普通变量 SHALL 独立检查：`${X:-$Y}` 的外层保护 **SHALL NOT** 屏蔽
  `$Y`，`${X:=$X}` 的赋值 **SHALL NOT** 提前满足其右侧 `$X`。
- **IF** 参数展开包含当前解析器不能可靠建模的嵌套 `${...}`，**THEN** 检查器 SHALL
  产生 `ENV_EXPANSION_UNSUPPORTED`，**SHALL NOT** 静默 PASS。
- workflow 变量检查的 PASS SHALL 明确限定为"受支持语法中的线性词法顺序无先读后写"，
  **SHALL NOT** 表述为真实控制流可运行证明；分支支配、循环是否进入、函数调用、
  子 shell/命令替换作用域、`eval/source` 属未建模范围，关键步骤仍需真实 shell 故障路径验证。
- 报告 SHALL 始终列全所有失败项与缺失项，**SHALL NOT** 因汇总状态而隐藏明细。
- **IF** 任一门禁项未达标，**THEN** 系统 SHALL 给出不准出结论，**SHALL NOT** 把局部通过表述为全部通过。
- 运行记录 SHALL 绑定 `candidate_sha`、`target_sha`、`policy_version`、`baseline_version`、`baseline_hash`、`baseline_approval_ref`；配置标识 SHALL 脱敏且 SHALL 保留 `traceId` 等诊断字段。
- `policy_version`（门禁规则、重试策略、`review_mode`）SHALL 取 `target_sha` 上已批准版本；`baseline_version`（需求基线、本次必测范围）SHALL 取针对本次变更已确认的版本。merge-base SHALL 仅用于计算差异范围，**SHALL NOT** 作为规则来源。
- 规则变更 SHALL 在合并进目标分支后对后续候选生效，**SHALL NOT** 对提出该变更的候选自身生效。
- `last_result`（观察）与 `gate_accepted`（判定）SHALL 分开存储。
- **IF** 已确认的阻断产品缺陷在重试后通过，**THEN** 该阻断 **SHALL NOT** 被自动抹掉。
- **IF** 存在未解释的间歇失败，**THEN** 系统 SHALL NOT 仅凭末次通过满足门禁；无预先批准的重试规则时 SHALL 判 `INCOMPLETE`。
- 合并门禁产出 SHALL 命名为 `merge-gate-report`；**SHALL NOT** 表述为发布准出结论。

## R7 反质量门槛下降

**User Story:** 作为测试开发，我要拦住"为了让流水线变绿而改测试"。

- **WHEN** 评估代码变更，**THEN** `guard_scan` SHALL 扫描：显式 skip/xfail、断言删除、阈值数字变化、忽略范围扩大、重试次数或策略变化、测试文件删除。
- 命中项 SHALL 要求说明与替代覆盖证据；**IF** 未关闭，**THEN** SHALL 计入 reviewer 阻断项。
- `guard_scan` 输出 SHALL 标注为"提示，非结论"；**SHALL NOT** 承诺判断断言语义或数据清理有效性。
- 承担评审职责的 agent **SHALL NOT** 具备 write 或 shell 工具。
- 系统文档 SHALL 载明残余风险：溯源分离可防伪造结果，不能防弱化断言。

## R8 最小权限与执行隔离

**User Story:** 作为团队管理者，我要确保这套东西不会顺手改坏别的东西。

- 工具权限 SHALL 由 `tools` 与 `permissions` 配置约束；子进程写入边界 SHALL 由经验证的执行隔离措施保证。
- 系统 **SHALL NOT** 表述为"写入范围由 `permissions` 保证"——已实测确认两条独立的绕过路径（`docs/capability-matrix.md` item 5、item 8）：
  1. shell 子进程写入绕过能力层，包括配置不可更改的 kiro-scope 硬拒绝
  2. **子代理自身的 `permissions.rules`（含 deny 与 ask）在"被委派调用"路径下未生效**，
     在"被直接选中为当前 profile"路径下生效——同一份配置，两种加载路径，两种安全结果。
     该绕过**不涉及 shell**，比第一条更严重：它直接冲击"给子代理配置权限就能限制其
     写入范围"这一假设，而这一假设是四角色委派编排的前提之一。
- **WHERE** `qa-executor` 具备 shell 能力，系统 SHALL 提供经验证的执行环境隔离，并在隔离生效后重跑 item 5 探针。
- 系统 **SHALL NOT** 断言"执行环境隔离对 item 8 无效"——操作系统级隔离约束的是发起
  写入的进程，与该进程内部的应用层权限判定是否正确无关；item 8 暴露的问题是
  `qa-lead` 与被委派的 `qa-executor` 逻辑当前运行在同一宿主进程内，因此进程级隔离
  无法只约束其中一个逻辑角色。**WHERE** 存在委派关系，Task 3 SHALL 先定位实际执行
  `fs_write` 与相关执行服务的进程边界，再设计覆盖该完整执行链路的隔离方案；
  若 `qa-lead`/`qa-executor` 仍共享同一进程，SHALL 拆分为真正独立的执行单元。
- 进程观测结论 SHALL 限定在"观测到什么"，**SHALL NOT** 升级为架构断言
  （2026-09-21 按外部复核收窄，证据：`docs/probe-evidence/task3/task3-findings.md`）：
  委派写入期间，pass 4 的 248 次快照（实测相邻间隔 max 0.105s）**没有记录到** Kiro 树中的
  新建进程；检测下限按每寿命重复 5 次标定，50ms 已证实会漏。
  **IF** 仅有进程创建观测，**THEN** 系统 SHALL NOT 断言"不存在 per-agent 进程边界"、
  "委派链内部拆分不可能"或"顶层会话即独立 OS 进程"——采样器不做 PID 归属，没有新进程
  仍可能由采样前已存在的常驻进程完成写入。
- 写入操作的权威 PID 归属 SHALL 由内核级追踪取得，且 SHALL 覆盖直接调用与委派调用两条
  链路、每条用唯一 canary、记录实际 open/write/rename 事件；单次归属结果
  **SHALL NOT** 被扩大为所有角色的永久架构保证。
- "安全关键角色不经委派调用"这条措施的依据 SHALL 是 item 8 的权限行为观测
  （直接调用 deny 生效 / 委派调用 deny 失效），**SHALL NOT** 挂在进程架构结论上。
- 宿主进程整体沙箱化（macOS）SHALL 记为"未能验证"，**SHALL NOT** 记为"不可行"。
- 执行隔离的可写范围 SHALL 由不可由调用方修改的策略决定；**SHALL NOT** 由调用参数决定。
  策略校验 SHALL 覆盖：仓库外穿越、符号链接组件、仓库根与 rw 之间的挂载重叠、受保护路径
  （含其父目录）、镜像与仓库根的环境变量覆盖。已实测的两条绕过
  （`--rw qa/baseline`、`--rw ../outside`）SHALL 作为永久回归场景保留。
- 隔离执行入口 SHALL 无损传递命令参数：校验器产出的参数 **SHALL NOT** 经由逐行或其他
  会丢失信息的文本协议交回 shell 拼装执行。**IF** 参数内部含换行或为空串，**THEN** 容器内
  收到的 argv SHALL 与调用方 argv 逐项相等。验收 SHALL 包含端到端反例：多行失败命令必须
  以该命令自身的退出码结束（阳性对照为同逻辑单行版本）、空参数必须保留在原位置、
  引号/空格/反斜杠不变。**SHALL NOT** 仅用内存中的参数列表断言代替端到端往返验证。
- 运行时镜像 SHALL 按不可变标识绑定：执行入口 SHALL 在同一次查询中取得镜像 digest 与
  镜像 ID，核对 digest 与策略记录一致后按**镜像 ID** 启动；digest 不符、镜像不存在或策略
  未记录 digest **SHALL** 拒绝执行。**SHALL NOT** 只在验证脚本中核对而普通执行路径仍按
  可变标签启动（同名标签被重建即静默换镜像）。
- 采样归档 SHALL 表述为"保留所有快照时间点的字段裁剪归档"；**SHALL NOT** 声称哈希本身
  证明"未挑选样本"——哈希仅在原件存在时可校验对应关系。**WHERE** 需要长期独立复核，
  原始件 SHALL 保存在版本库之外的受控 artifact 存储，版本库仅记标识与摘要。
- 采样间隔 SHALL 表述为快照开始时刻之差，**SHALL NOT** 表述为原子采集，
  **SHALL NOT** 据此保证捕获所有长于该间隔的进程。
- 隔离验证中"没有发生坏事"型判据 SHALL 附带"坏事确实被尝试过"的证据；网络隔离判据
  SHALL 同时要求结构性证据（仅回环接口）、errno 限定（`ENETUNREACH`/`EHOSTUNREACH`）
  与**同端点**可达性阳性对照；连接被拒绝或超时 SHALL 记为未能验证。
- 系统 SHALL 声明执行隔离是护栏而非安全边界：策略与校验器位于仓库内，对宿主机有写权限的
  角色可以改写它们；强制点 SHALL 仍是受保护分支 + CODEOWNERS + CI 复核。
- CI 从可信执行的原始产物重新生成门禁输入，SHALL 仅作为防止伪造门禁结果进入合并的
  独立防线；**SHALL NOT** 被视为本地执行阶段隔离的替代——CI 复核发生在 agent 执行
  结束之后，无法阻止本地 agent 在执行期间已经改坏文件或读取了不应读取的数据。
- 预期的写路径 SHALL 显式 allow；**IF** 依赖 `ask` 效果，**THEN** 在 headless 或子代理场景将等同于 deny 并导致静默失败。
- `.kiro/agents/**` 与 `.kiro/steering/**` **SHALL NOT** 被视为受保护路径（实测：前者"永久询问"退化为静默允许，后者无任何硬保护）。
- 可信计算基路径的强制点 SHALL 是受保护分支 + CODEOWNERS 审批；哈希校验 SHALL 仅作检测辅助。
- 提交、推送、打 tag、发布、部署、写入缺陷系统或测试平台 SHALL 需用户显式确认。
- 探针判据 SHALL 以外部可复核证据（文件哈希、拒绝原文、独立工具调用记录）为准；
  模型对自身权限、审批状态或因果关系的自述 **SHALL NOT** 被直接采信为结论
  ——历史上已三次出现模型自述与外部事实不一致（工具不存在 vs 被拒绝的混淆、
  item 12 首次重跑的过期状态、item 8 的审批归因错误）。

## R9 效果可度量

**User Story:** 作为测试负责人，我要看到数字，而不是"感觉快了很多"。

- 试点前基线 SHALL 先于试点采集，包含样本需求、人工用例产出耗时、返工量、历史失败的人工归因对照集。
- **IF** 某项基线数据采集不到，**THEN** SHALL 显式标记为缺失，**SHALL NOT** 留空或以零填充。
- **WHEN** 输出效果对比，**THEN** 系统 SHALL 区分有对照与无对照的结论；缺基线数据的指标 SHALL 标记为"不可比"，**SHALL NOT** 宣称改善。
- 模型评测 SHALL 记录模型与配置版本，并分别呈现格式合规证据与输出质量证据；格式检查（EARS 关键词、模糊词黑名单）**SHALL NOT** 被表述为需求完整性或预期正确性的证明。

---

## 反例验收（必过）

这六条是 v1 的硬验收，任一不满足即视为未交付：

| # | 反例 | 必须的行为 |
|---|---|---|
| 1 | 没有真正执行的越权探针（缺工具调用记录或缺拒绝结果） | 不得算通过，记「未能验证」 |
| 2 | 先失败后通过 | 不得自动抹掉已确认阻断 |
| 3 | 手工必测项缺证据 | 不得放行，判 `INCOMPLETE` |
| 4 | 旧 SHA 的评审结论用于新候选版本 | 必须失效 |
| 5 | 候选分支自提 `approved: true` | 不构成评审通过 |
| 6 | `automated_required` 下评审失败后临时切 `manual` | 不得通过 |

## 超出范围（v1 不做）

- 多平台适配与安装分发打包（后置）
- 阈值型门禁规则的具体数值（等试点数据，v1 只发完整性规则）
- 自动提单与测试平台回写（一律草稿 + 显式确认）
- 造新测试框架（只对接现有框架）
