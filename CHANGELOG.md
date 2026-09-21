# Changelog

## 0.1.0 — 2026-09-20

轨 A（平台无关部分）完成；轨 B 待 Task 0 剩余探针项解阻。

### Task 0 — Kiro 能力探针（部分完成）

已验证 5 项，未能验证 7 项（需在 IDE 中交互执行，runbook 与一次性探针配置已备）。

- **item 5 判定"已验证不支持"（阻断项）**：shell 子进程的文件写入**完全绕过**
  Kiro 能力层，包括配置不可更改的 kiro-scope 硬拒绝。同一脚本的阳性对照成功，
  实验组写入了 `fs_write` 工具刚被拒绝的同一路径，且该文件无法用 `delete_file` 清理。
  → Task 3 必须补执行环境隔离并重跑此探针。
- item 3 工具级拒绝已验证支持；实测硬拒绝清单为 **7 条**，官方文档只载 3 条。
- item 9：`.kiro/steering/**` **无硬保护**（确认"阈值不放 steering"的设计正确）；
  `.kiro/agents/**` 的"永久询问"在本环境**退化为静默允许**，不得视为受保护路径。
- 环境快照记录了 session 作用域授权（ACP preset 行为），避免把它误归因为 agent 配置效果。

### Task 1 — 骨架、spec 三件套、试点前基线

- spec 三件套：R1–R9 用 User Story + EARS，含六条反例验收。
- 格式定义：需求基线、必测范围、用例、门槛豁免四份模板。
- `CODEOWNERS` + `docs/protected-paths.md`：把可信计算基的强制点说清楚，
  并写明为什么**不能**靠权限规则。
- 试点前基线 `qa/metrics/pilot-baseline.yaml`：全部项目数据**显式标 MISSING 并附原因**，
  `meta.status: INCOMPLETE`（用户尚未提供 pilot_repo）。
- `scripts/validate_spec.py`：133 项结构检查。

### Task 2 — 追溯矩阵

- `scripts/trace_matrix.py`：`REQ ← TC ← AT ← attempt` 四层派生，`matrix.csv` 无写入者。
- 覆盖率分母取自基线，矩阵少收录条目会报缺口而不是算成全覆盖。
- **证据缺失与"已确认未自动化"分开**：无 collect 清单时 `automated=None`。
- `manual: true` 不进自动化分母但仍进必测范围校验。

### Task 6a — 合并门禁

- `scripts/gate_check.py`：三态短路判定；`last_result` 与 `gate_accepted` 分离。
- 六字段版本绑定；规则取 `target_sha`，基线取本次已确认版本；merge-base 仅算差异。
- `review_mode` 两模式；候选自提的评审结论不构成通过；旧 SHA 结论失效。
- 阈值项 `NOT_CONFIGURED` —— v1 不填任意数字。

### Task 6b — 门槛变化提示器

- `scripts/guard_scan.py`：7 条规则；输出一律标注"提示，非结论"。
- 未关闭的提示项计入 reviewer 阻断路径（`GUARD_HINTS_UNREVIEWED` → INCOMPLETE），
  不会被静默忽略；豁免必须同时给出原因与替代覆盖才生效。

### 外部验收与修复（同日）

第一轮交付被外部独立验收判为**部分完成**：内部 163 项单测、27 条变异检验、六场景 demo
全部通过，但独立构造的 14 个反例中 **12 个错误 PASS**、2 个把证据不足误判为 FAIL。
5 类根因已修复（详见 `.doc/specs/qa-agents/tasks.md` 的外部验收章节）：

- **F1** 按 REQ 指定的必测范围不参与结果判定 → 追溯层算出唯一有效执行范围
  `required_cases ∪ expand(REQ)`，门禁只认这一份。
- **F2** 版本核对写成"字段非空且不相等才报错"，**删掉字段反而跳过核对** →
  必填字段先查存在性，并与实际基线交叉核对。
- **F3** 只关联 REQ 的已确认阻断缺陷被丢弃（`by_req` 映射从未使用）→ REQ 级缺陷传播；
  明确 `closed`/`confirmed_blocking`/`severity` 三字段阻断契约，严重级真正参与计算。
- **F4** manual 模式消费未验证的本地 verdict，候选自提 JSON 可关掉门槛提示 →
  `check_review` 返回 `trusted_verdict`；提示获得稳定 `hint_id`，处置须逐项带说明与替代覆盖。
- **F5** 缺失或 skipped 被当成质量失败 → 三态聚合，`failed`/`error` 才是失败。

复验：外部反例 15/15 符合 spec；另做 19 条变异把每处修复退回缺陷形态，19/19 被捕获
（其中 2 条首轮逃逸，暴露并补齐了新的测试盲区）。

**这一轮最重要的教训：** 内部测试、变异检验、端到端 demo 三道检查全绿，外部独立反例
仍发现 12 处误放行。自建检查只能覆盖自己想到的场景。

### 验证

- `make check`：结构校验 PASS + 204 项单测通过。
- 外部独立反例 15/15 符合 spec。
- 变异检验四份脚本全部针对修复后代码复跑：**48/48 被捕获**
  （task2 5、task6a 10、task6b 12、fixes 21）。
- 证据与各份结果对应的代码状态见 `docs/probe-evidence/README.md`。

复跑过程中又发现一处真实测试盲区：`confirmed_blocking` 与 `severity` 两条阻断路径
从未被分别测到（所有相关用例的 severity 都恰好在阻断清单内），已补用例。
这是第二次遇到"多个条件被同一组数据同时满足导致单条件失效测不出来"的模式。

### 过程中发现并修正的自身缺陷

1. `validate_spec.py` 把基线的 `meta.status`（COMPLETE/INCOMPLETE）误判为度量项
   （MEASURED/MISSING），取值域冲突 —— 由校验器自身首次运行时暴露。
2. `gate_check.py` 的测试**首轮变异检验未捕获**"报告只列第一条发现"——33 项测试
   全部读内存属性，无一条检查序列化产物，而 CI 写入 `merge-gate-report.json`、
   人实际读到的正是序列化结果。已补 3 项测试覆盖序列化与渲染边界。

### Task 4/7 基础设施 + RUNBOOK item 8 修正

- `evals/`：三份 fixture（正例/反例/混合例）、rubric（结构 vs 人工判断分列，漏报/误报
  分开记账）、`run_evals.py`（不调用模型，报告无任何合成总分字段）。
- `.github/workflows/merge-gate.yml` 草稿：三层 job（自检/执行/判定），判定层从原始
  产物重新生成门禁输入；未落地部分故意写成 `exit 1` 占位。
- RUNBOOK item 8 实验设计修正：原设计移除父代理 `trustedAgents` 首先改变的是委派
  审批而非工具审批，无法区分两层；改为让委派保持可执行、另建 `probe-write-ask`
  子代理使其自身工具操作命中 `ask`。新增 `preflight.py` 探针自检与 canary 重置。

### Task 0 第二轮探针（用户交互执行）— 新阻断项 item 8

用户在 IDE 中执行了剩余 7 项交互探针（2/6/7/8/10/11/12），方法论优于第一轮：每项区分
模型自称与外部核对，item 8 做了直接调用 vs 委派调用的三组对照。12 项探针全部完成。

**item 8：子代理自身权限在委派路径下未生效，比 item 5 更严重（不涉及 shell）。**
同一份子代理配置，直接调用时 `permissions.deny` 生效、拒绝写入受保护路径；被父代理
**委派**调用时同一条 deny 未生效、写入成功。item 5 证明 shell 能绕过能力层，item 8
证明委派本身就可能不传递子代理自己的权限策略——两条独立，都必须堵。四角色编排里
`qa-lead → qa-executor` 的委派链路，在 Task 3 重新设计隔离范围并验证前不能视为安全
边界。详见 `docs/capability-matrix.md` item 8 一节与 `docs/probe-evidence/task0-round2-report.md`。

其他发现：`allowedTools`/`toolsSettings` 被 IDE 当作 CLI-only 字段，无 `permissions`
块的配置被静默跳过；`trustedAgents` 不消除委派时的一次性审批；item 12 确认
`todo_list` 是具体工具 ID、`knowledge` 不在标签注册表内。

**证据可信度三原则固化：** 探针判据一律以外部可复核证据为准，不采信模型自述；
会话存档的 `status` 字段不可信（已证实与实际落盘结果矛盾）；工具层报
`success:true` 不等于拿到可信 exit 0。已写入 R8 作为通用约束。

原始证据包由独立采集器产出，尚未纳入本仓库，第二轮结论基于报告文本采信，
未逐份核验原始文件。

### Task 0 第二轮报告复审与推理纠正（同日）

用户对本文件此前吸收第二轮探针报告的方式提出五点纠正，全部核实成立：

1. **核心推理错误：**"委派发生在宿主机同一进程内部，所以执行环境隔离对 item 8 无效"
   是错误推理。操作系统级隔离约束的是发起写入的进程，不需要理解进程内部哪个"逻辑角色"
   发起调用；只要真正执行 `fs_write` 的进程被隔离约束，应用内部权限判定坏了不代表能绕过
   操作系统限制。真正的问题是 `qa-lead`、被委派的 `qa-executor` 逻辑当前运行在**同一个**
   宿主进程内，进程级隔离无法只约束其中一个角色——这是需要拆分执行单元的原因，不是
   "隔离本身失效"。已在 `capability-matrix.md`、`design.md`、`requirements.md`、
   `tasks.md` 四处更正表述。
2. **Mach-O 二进制不能证明"CLI 容器化不可行"**，只能证明当前这份 macOS 二进制放不进
   Linux 容器。是否存在适配版本、依赖能否满足、认证能否非交互运行，均改记"未能验证"。
3. **上一轮 sandbox-exec 隔离原语探针存在缺陷**：多次执行顺序与输出异常，仅凭
   "文件未变"宣布拦截成功，缺少同时发生的写入尝试记录与拒绝回执，复刻了此前明确
   批评过的探针缺陷。相关探针记录同步降级为"未能验证"标准。
4. **CI 重建证据不能替代本地执行隔离**：CI 防止的是伪造门禁结果进入合并，不能阻止
   本地 agent 在执行期间已经改坏文件或读取不应读取的数据。item 8 未从 Task 3 移除。
5. **多处结论超出证据范围，已收窄：**`policySession` 漏传维持"静态定位线索"而非
   已确认根因；不再断言 `trustedAgents`/`availableAgents` 在本 IDE"控制能不能派"，
   只保留"委派触发审批"这一验证过的行为事实；"所有配置必须带 `permissions` 块"
   收窄为"含 CLI-only 字段且缺该块的配置会被跳过"；`spec`/`context` 的 `toolCount=1`
   改记"未能验证是否真匹配到 disclose_context"（该工具是必带工具，单独出现不能反推
   标签匹配成功）。

另确认原验收报告末尾"需补 item 8 更极端阳性对照"的建议**不适用**：三组对照表的
`protected` 列已经是完整的直接/委派 deny 对照，不需要再补。

原始证据包位于用户指定路径，已核对 `manifest-sha256.json`（全部证据文件哈希清单）与
`final-canary-verification.json`（canary 前后哈希对照），并读取
`child-policy-callsite-excerpts.json`、`named-subagent-source.txt` 的压缩代码摘录，
与报告叙述一致。这次核对纠正了上一版"证据包不可访问"的错误认定。

**本轮只审阅并修订文本，未复跑任何新增隔离实验。** `make check`：164 项结构校验 PASS
+ 234 项测试通过（未改代码，测试数不变）。

### Task 3 第一步：执行进程边界实测定位（2026-09-21）

上一节承诺的"先定位实际执行 `fs_write` 的进程边界，再设计隔离方案"已完成第一步。
证据与可复跑脚本见 `docs/probe-evidence/task3/`。

**结论：IDE 内不存在 per-agent 的操作系统进程边界。**

- agent 的 shell 命令是 `Kiro Helper`(pid 3612) → `Kiro`(3590) 的后代进程，宿主机上
  以普通用户身份运行、无额外隔离 → shell 起源写入（item 5）在我们能控制的子进程里，
  容器 + 只读挂载对这条路径可用（原语此前已验证为真实拦截）。
- 委派子代理执行 `fs_write` 期间，Kiro 进程树内**未出现存活 ≳100ms 的新进程**
  （0.1s 间隔 148 次采样覆盖整段委派回合）。0.5s 的第一轮曾出现一个仅存在于单个样本的
  `(Kiro)` 瞬时子进程，第二轮未复现，无证据纳入写入路径。
- 因此"若共享同一进程则拆分为独立执行单元"这条，**不能在委派链内部实现**——没有可供
  操作系统隔离挂载的对象。拆分只能发生在进入 IDE 之前：独立顶层会话或 IDE 之外的运行器。
- item 8 的可落地缓解随之变为**结构性**的：需要其写入范围被真正强制的角色，以顶层会话
  直接调用（该路径下 deny 已验证生效），不依赖父代理委派建立安全边界。

**否定结论的适用范围已标定。**"没看到新进程"只有在知道多短的进程会被漏掉时才有意义：
正对照（已知 PID 的 4s 进程）先判 `CONTROL_PASS`；检测下限用已知寿命金丝雀标定为
1000/300/100ms 全部捕获、**20ms 漏检**。所以结论写成"无存活 ≳100ms 的新进程"，
≲20ms 的瞬时辅助进程未被排除。写入操作的权威 PID 归属仍未取得（需 `sudo` 级内核追踪）。

**一条被对照组证伪的测量方法（保留记录）。** 首版方案是让 agent 写 FIFO、趁写入方阻塞时
用 `lsof` 读出 PID。控制写入方（PID 32944）确实在该 FIFO 上阻塞约 90 秒（读端挂上后
收到 `CONTROL-WRITER-PID-32944`），但同期 **220 次 `lsof` 采样全空**——进程阻塞在
`open()` 内部时尚未建立文件描述符，`lsof` 原理上看不见它。若没先跑对照组，"抓不到写入方"
会被误读成"没有进程写入"或"写入走了 temp+rename"。脚本保留为方法论反面样例。

**验证：** `make check` 166 项结构校验 PASS + 234 项测试通过，MAKE_EXIT=0（未改生产代码）。

### Task 3 第二步：shell 执行隔离落地并验证（2026-09-21）

新增 `isolation/`：镜像定义 + 执行包装器 + 边界验证脚本。只针对 item 5（shell 起源写入）——
这条路径的写入发生在我们能控制的子进程里，所以容器边界对它有效。

- `isolation/Containerfile`：`python:3.12-slim` + 锁死 `pytest==8.4.2`、`PyYAML==6.0.2`。
  依赖只在**构建期**装，因为执行期 `--network=none`；这样"临时 pip install 一下"这条既
  扩大攻击面又破坏可复现性的路被关掉。已构建 `localhost/qa-executor:0.1.0`（arm64 原生）。
- `isolation/run-isolated.sh`：整个仓库 `:ro` 挂载，只有 `--rw <相对路径>` 显式列出的目录
  可写，`--network=none`，工作目录 `/work`。
- `isolation/verify-isolation.sh`：5 条检查，每条要求"命令确实执行 + 容器内拒绝原文 +
  宿主机 canary 前后哈希"三段证据。

**结果：5/5 PASS（含阳性对照）。** 只读挂载内写入拿到 `Read-only file system` 且 canary
哈希未变；未挂载的宿主机路径在容器内**不存在**（靠不存在而不是靠权限判定）；执行期
`Network is unreachable`；连仓库根的 `VERSION` 也写不动。**另在容器内跑完仓库全量测试
234 项通过**——隔离没有把活干不了。证据见 `docs/probe-evidence/task3/results/`。

**未闭环，别当成已解决：** 上述只证明"**如果**命令通过包装器执行，边界成立"。目前没有
机制强制 `qa-executor` 必须走包装器，它照样能直接在宿主机跑命令。候选强制路径是把该角色
shell allow 清单收窄到只有 `isolation/run-isolated.sh`（命令层规则生效已由 item 6 验证），
但 item 6 同时发现 `bash -c` 与变量间接会进入 `ask`，需实测确认子代理/headless 下确实等同
deny。实测通过前，隔离只算"可用"，不算"已强制"。

**过程中自己踩的两个坑（都会伪装成"隔离生效"）：**

1. 包装器最初没有可执行位，5 条命令全是 `permission denied`，而 C2 当时只看"canary 哈希
   未变"就报了 PASS。**凡"没发生坏事"型判据，必须附带"坏事确实被尝试过"的证据**，
   否则一律作废。判据已改为强制要求拒绝原文，并在阳性对照失败时把后续全部标 VOID。
2. zsh 把 `"$WORKDIR:ro"` 解析成历史修饰符 `${WORKDIR:r}` 再接字面 `o`，挂载点静默变成
   `/worko`——容器正常启动，但**什么都没挂上**。若镜像里恰好存在 `/work`，写入会落进容器
   临时层随容器消失，结果静默丢失。挂载串里的变量必须加花括号。

这两条与本项目一贯的教训同源：**否定结论必须先证明测量手段能看见阳性样本。**
