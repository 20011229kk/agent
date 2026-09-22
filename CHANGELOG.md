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

### 外部复核判 Task 3 不具备验收条件，五点全部修复（2026-09-21）

第二轮外部独立复核（读实际仓库 + 真实 Podman 容器反例 + 离线故障注入）判定：
**容器原语与已有测试运行通过，但包装器作为权限边界不通过、进程归属未验证，Task 3 保持阻塞。**
五点全部成立，逐条修复如下。

**P1 包装器自己允许扩大授权范围（真实绕过两条，都写入成功、退出 0、宿主哈希变化）**

| 调用 | 当时后果 |
|---|---|
| `--rw qa/baseline` | 受保护的覆盖率分母被重新挂成可写 |
| `--rw ../outside` | 穿越到仓库外的同级目录写入 |

根因：包装器接受调用方给的任意 `--rw`，只检查目录存在，没有可信策略也没有把路径限制在
仓库内。修复：新增 `isolation/mount-policy.yaml`（`allowed_rw` 白名单 + `protected` 双重
否决 + 镜像 digest + `network.mode`）与 `isolation/mount_policy.py`（可单测的校验器，
覆盖仓库外穿越、符号链接组件、仓库根重叠、rw 间嵌套、受保护路径及其父目录、镜像与仓库根
的环境变量覆盖）。`run-isolated.sh` 自己不再拼任何挂载参数。两条绕过固化为
`verify-isolation.sh` 的 C6/C7 与 `tests/test_mount_policy.py` 的回归场景。

**P1 进程创建观测不能证明写入 PID，也不能证明不存在 per-agent 边界**

采样器自己的注释就写明它只观察进程创建、不做 PID 归属，而结论却被写成了架构断言。
已撤回三条推论："IDE 内不存在 per-agent 进程边界""委派链内部拆分不可能""顶层会话即独立
OS 进程"。另外三处测量问题一并修掉：

- **"0.1s 间隔"是设定值不是实测值。** pass 2 的 148 次 / 22.1 秒反推实际约 150ms。
  采样器原本"干完活再 sleep(interval)"。改为按固定节拍对齐后，第一版又出现连续补采
  （pass 3 实测 p50=0.029s 空转、max=54.3s 长间隙），再改为**跳过错过的节拍**；
  pass 4 实测 min 0.095 / p50 0.100 / **max 0.105s**，与设定值一致。`report` 现在强制
  公布间隔分布——能漏多长的进程由 **max 间隙**决定，不是由设定间隔决定。
- **每种寿命只有一个样本。** 改为每寿命重复 5 次：1s/300ms/150ms/100ms 均 5/5，
  **50ms 为 3/5、20ms 为 1/5**。
- **原始采样未归档**（留在 /tmp 后被清理，结论不可复核）。新增 `reduce` 模式：保留每次
  快照的 t/wall、Kiro 进程子树、整张进程表的 sha256、命令行字典表与完整文件哈希，
  141MB → 2.9MB。只减体积，不减可核对性。

现在的表述是："本轮 248 次快照没有记录到 Kiro 树中的新建进程；写入 PID 未归属，
未证明不存在独立工作进程。" Task 3 的进程侧恢复为**部分完成/待验证**。

**P2 网络检查仍有假通过路径**

旧 C4 捕获所有 `OSError` 就打印 `NETWORK_BLOCKED`，复核的离线故障注入证明
`ConnectionRefusedError` 与 `TimeoutError` 同样命中 PASS——目标服务故障或普通超时会被
当成隔离成功。改为三段判据：结构性证据（容器内只有 `lo` 接口）+ errno 限定
（仅 `ENETUNREACH`/`EHOSTUNREACH`）+ **同端点**可达性阳性对照。新增
`isolation/probes/net_probe.py` 只报事实三态（REACHABLE/UNREACHABLE/INCONCLUSIVE），
判定权交给验证脚本，并用 `tests/test_net_probe.py` 把那次故障注入固化成回归。

顺带实测发现：本机 `1.1.1.1:443` 本就被链路挡住（TimeoutError）。若拿它当阳性对照，
得到的会是"环境本身不通"而非隔离结论。所以端点改为在默认网络下逐个探测后选定。

**P2 默认镜像没接上已构建的执行镜像**

默认曾是 `docker.io/library/python:3.12-slim`，只有显式设 `QA_ISOLATION_IMAGE` 才用
`qa-executor`，结果默认镜像里 `import pytest` 直接 `ModuleNotFoundError`。现在默认取策略
镜像，且默认拒绝覆盖；新增 **C0 镜像身份核对**（本机 digest 必须等于策略记录值，
否则后续结论全部作废）。

可重复性表述按实际锁定程度改写：已锁**基础镜像 manifest digest** + 直接与传递依赖
（pluggy/iniconfig/packaging/pygments）版本 + 构建时 `pip check`；未锁 `--require-hashes`
（只锁版本号不校验包内容）、基础镜像内系统包未单独审计、digest 是 arm64 平台的。
原来的"依赖锁死"是过头表述。

**isolation/ 补上测试覆盖**

原先 `tests/` 里没有任何对 `isolation/` 或采样器的引用，234 这个数字覆盖不到新增路径。
现补 58 项：`tests/test_mount_policy.py` 42 项（含两条绕过回归、穿越回仓库内仍判受保护、
穿越回白名单应放行、符号链接目标在仓库内也拒绝、受保护项写进白名单仍否决、CLI 退出码）
+ `tests/test_net_probe.py` 16 项（verdict 矩阵 + 故障注入）。

**验证**

- `verify-isolation.sh`：**C0–C9 全 PASS**（`isolation-verify-c0-c9.txt`）
- 容器内跑完仓库全量测试 **292 项通过**（`isolation-pytest-in-container.txt`）
- 本机 `make check`：结构校验 PASS + 292 项测试通过

**仍未闭环（明确留着，不当成已解决）**

1. **没有机制强制必须走包装器**；候选方案是收窄 shell allow 清单，但 `bash -c` 与变量
   间接会进入 `ask`，需实测确认子代理/headless 下等同 deny。
2. **策略与校验器本身在仓库里**，对宿主机有写权限的 agent 可以改掉它们。这是护栏不是
   安全边界；强制点仍是受保护分支 + CODEOWNERS + CI 复核（`verify-isolation.sh` 会打印
   策略与脚本的 sha256 manifest 供核对）。
3. **写入的权威 PID 归属需内核级追踪**，且要覆盖直接与委派两条链路、唯一 canary、
   记录实际 open/write/rename 事件；单次归属不得扩大为永久架构保证。
4. **item 8 的工具写入链路需单独闭环**，不能由 shell 包装器代替。

**这一轮的教训（与前几轮同源）：** 自己写的验证脚本连续三次出现"判据比被测对象更弱"
——缺可执行位仍报 PASS、挂载参数被 zsh 吃掉仍能启动容器、捕获所有 OSError 当成隔离生效。
共同点是**否定型判据没有要求"坏事确实被尝试过"的证据**。另外这轮第一次出现
"护栏被当成边界"：包装器能生成正确的挂载，但它同时接受任意越权请求，
"用它执行"与"受它约束"是两件事。

### 第二轮外部复核：修我上一轮引入的假成功缺陷 + 运行时镜像绑定（2026-09-21）

复核核对版本 `fae09c5`，判定"上一轮主要修复有效，但新参数传递实现引入确定的假成功缺陷"。
两点都成立。

**P1 按行传参截断多行命令，失败逻辑未执行却返回成功（我上一轮引入的）**

上一轮我让 `mount_policy.py plan` 逐行输出 podman 参数、`run-isolated.sh` 按行读回并跳过
空行。后果是参数内部的换行被当成分隔符：

| 输入 | 期望 | 实际（修复前） |
|---|---|---|
| `python -c "print('BEGIN'); raise SystemExit(42)"` | 退出 42 | 退出 42 ✓ |
| `python -c $'print(\'BEGIN\')\nraise SystemExit(42)'` | 退出 42 | **只打印 BEGIN，退出 0** |
| argv `['', 'tail']` | `['', 'tail']` | **`['tail']`**（空参数被跳过） |

第二行被拆成 Python 的另一个命令行参数，`raise SystemExit(42)` 根本没执行。这是最坏的一类
假成功：失败逻辑没跑，退出码却是成功。对 agent 来说，多行 `python -c` / `sh -c` 里的断言
或清理步骤会被静默省略。上一轮的 292 项测试覆盖不到它——测的全是 Python 规划器返回的
内存列表，没有一项验证整条 shell 参数传递往返。

修复：`mount_policy.py run` 在 Python 内校验后直接 `os.execvp("podman", argv)`，参数数组
从调用方 argv 一路到 podman argv，中间不经过任何文本协议。`run-isolated.sh` 变成极薄转交层
（`exec python3 mount_policy.py run ... -- "$@"`），不解析也不拼装。`plan` 保留给排查，
默认改为 NUL 分隔并标注不可用于执行；`--sep line` 仅供人读，并有一条测试**断言逐行模式
确实会把一个参数拆成两行**，用来说明它为什么不能当默认值。

回归：C10（多行必须退出 42，且单行阳性对照先过，否则记 VOID）、C11（空参数保留），
加 4 项需要 podman 的端到端单测（多行、空参数、引号/空格/反斜杠）与一组 argv 保真单测。

**P2 C0 核对了镜像，普通执行仍按可变标签启动**

`resolve_image` 返回 `image.reference`，`build_args` 把可变标签交给 podman，策略里的
`image.digest` 从未在普通执行路径上被用到；C0 只在验证脚本里跑。所以禁止
`QA_ISOLATION_IMAGE` 覆盖只限制了**名字**——同名标签被重新构建或移动后，后续执行会静默
用上另一个镜像。

修复：`run` 路径用**一次** `podman image inspect` 同时取回 digest 与 image id，digest 与
策略一致才继续，并按 **image id** 启动，关掉"核对标签后又按标签运行"的竞态。digest 不符、
镜像不存在、策略未记 digest 一律拒绝执行。回归：C12（用 digest 被改坏的临时策略验证普通
执行路径确实拒绝）+ 6 项单测（含"覆盖在 inspect 之前就被拒绝，不去 inspect 不被允许的镜像"）。

**归档措辞收窄（复核第三点，不属缺陷但表述过头）**

- "哈希证明没有挑样本"改为"**在原件仍存在时**可校验对应关系"。原件一旦删除，只剩哈希
  既无法重建完整进程表，也无法复核被裁掉的内容。准确叫法是**保留所有快照时间点的字段
  裁剪归档**。原始件目前只在本地 `/tmp`，属会过期的证据；长期独立复核应放进版本库之外的
  受控 artifact 存储。
- 采样间隔明确为**快照开始时刻之差**，不代表原子采集，也不保证捕获所有长于该间隔的进程。
- `kiro_tree` 只体现"命令行含 kiro 的根及其后代"这条既定规则被忠实应用，与写入 PID 归属无关。

**验证**

- `verify-isolation.sh`：**C0–C12 全 13 项 PASS**（`isolation-verify-c0-c12.txt`）
- 复核给的精确反例在包装器上直接复跑：单行 42、**多行 42**、空参数 `['', 'tail']`
- 宿主 `make check`：180 项结构校验 PASS + **311 项测试通过**
- 容器内：307 passed + **4 skipped**（skip 的是需要 podman 的端到端用例，容器内无 podman，
  按 `skipif` 跳过而不是假装通过；这 4 项在宿主机实际执行）

**仍未闭环（与上一轮相同，未因本轮修复而改变）**

调用方仍可绕开包装器；本地可写的策略与校验器仍可被角色改动。把 shell allow 收窄到包装器
名称至多解决部分入口问题，不保护执行器本体；平台分支保护与 CI 约束的是合并阶段，不能代替
本地执行期的强制边界。item 5 / item 8 的完整链路重跑，要等可信执行器与"策略不可被角色修改"
这两个条件具备之后再做。

**这一轮的教训：** 修一个假成功的过程中引入了另一个假成功，而且是同一类——**判据比被测
对象弱**。上一轮补的 58 项测试全在验证规划器的内存输出，没有一项跨越 shell 边界，所以
"292 项通过"对新缺陷完全没有约束力。**新增的传输层必须有端到端往返测试**，
断言的对象是"最终执行的那个进程收到了什么"，不是"我打算传什么"。

### 交付验收判 INCOMPLETE：补齐四项缺失实现（2026-09-21）

第三方验收核对 `6e49f1c`，结论是"隔离包装器通过，QA Agent 整体 INCOMPLETE"。
五个阻断项里有四项是**缺失的实现**，本轮补齐；余下的强制边界与平台配置仍未闭环。

**新增：正式角色配置 `.kiro/agents/*.json`（四个角色）**

此前只有一次性探针配置，没有正式角色，所以"角色隔离"根本没有验收对象。四份配置的每条写法
都对应一条实测结论：`qa-lead` 用 `tools` 省略表达"不写"且 `rules: []`（不能用 deny——
item 7 实测父代理 deny 按交集传播，会把子代理该有的写权限一起禁掉）；`qa-executor` 写路径
显式 allow（`ask` 在 headless/子代理下等同 deny）、deny 全部受保护路径与破坏性 shell 命令；
所有配置都带 `permissions` 块（缺它且含 CLI-only 字段的配置被 IDE 静默跳过）。

提示词里固化了几条容易被模型自己绕开的约束：**委派不是安全边界**（item 8）、
**FLAKY 不是根因**、不发明阈值、三态门禁、证据强度分级。

**新增：`scripts/validate_agents.py`（47 项测试）**

校验上述写法约束，拒绝码覆盖 `TOOL_NOT_IN_REGISTRY`（`knowledge`）、`TOOL_UNVERIFIED`
（`spec`/`context`，item 12 未能验证匹配结果）、`READONLY_ROLE_USES_DENY`、
`RULE_EFFECT_ASK`、`WRITE_PATHS_NOT_EXPLICIT`、`PROTECTED_PATH_NOT_DENIED`、
`ALLOW_INSIDE_PROTECTED`、`PERMISSIONS_BLOCK_MISSING`、`SUBAGENT_TARGET_MISSING` 等。

写测试时逮到自己一个缺陷：非对象的 rule 项会让后续逻辑抛 `AttributeError`——校验器自己崩掉
比漏报更糟，因为调用方看到的是异常而不是判定。已修。另外首轮跑真实配置直接 PASS，
这是"校验器可能什么都没查"的信号，所以每个拒绝码都补了会触发它的反例。

**新增：`scripts/rebuild_run_json.py`（32 项测试）**

门禁证据链上一直缺的那一环：从框架原生报告（JUnit XML）重建 `run.json`。选 JUnit XML 是为了
不假设被测项目用哪个框架。关键判据：

- 六个版本绑定字段缺任何一个（包括传空字符串）直接拒绝生成，**不填空值**——
  `gate_check` 侧实测过"字段缺失反而跳过核对"这类误放行
- `skipped` 既不记为 failed 也不记为 passed；同时带 failure/skipped 时按更坏的记
- 原始报告解析失败或没有任何 attempt → `incomplete: true`，不得因为"已解析部分都 passed"放行
- message 做敏感信息脱敏（password/token/api_key/连接串/Authorization）并截断

未支持 JSON report / TRX / TAP，遇到应扩展本脚本并补测试，**不要**在 CI 里临时转换——
临时脚本不在受保护路径内，等于把证据来源搬到不受保护的地方。

**新增：`requirements-ci.txt`，并删掉 CI 里的未锁版本 fallback**

原先是 `pip install --require-hashes -r requirements-ci.txt || pip install pyyaml pytest`，
而那个文件不存在，所以每次都走 fallback 拉未锁定版本——判定器的运行环境每次都可能不同。
现在锁版本（含传递依赖），并明确标注**未锁哈希**及补齐方式；CI 里不再有 fallback。

**CI workflow：重建步骤从 `exit 1` 占位变为真实实现**

`gate` job 现在真的调用 `rebuild_run_json.py`，并从**受保护分支**取 `qa/baseline` 计算
`baseline_version`/`baseline_hash`。该步骤现在只会因两种**数据**原因失败：没有原始产物、
或受保护分支上没有需求基线——两者都应导致 INCOMPLETE。`run-tests` 的 collect/执行两步仍是
占位，且是真正的待输入（需要被测项目的框架与报告格式）。

`self-check` 增加角色配置校验；`make check` 现在是"结构校验 + 角色配置校验 + 单测"，
另加 `make validate-agents`、`make isolation-verify`。

**受保护路径清单补齐**：`.kiro/**`（角色配置）、`scripts/validate_agents.py`、
`scripts/rebuild_run_json.py`、`requirements-ci.txt` 入列；`isolation/mount-policy.yaml`
的 `protected` 同步加 `.kiro`。

**文档同步（验收报告附注的漂移项）**：`isolation-verify-c0-c9.txt` → `-c0-c12.txt`，
旧的 292 项数字改为当前 C0–C12 与 307+4skip 的表述。

**验证**：`make check` = 181 项结构校验 PASS + 角色配置校验 PASS + **390 项测试通过**。

**仍未闭环（验收报告的阻断项，本轮没动的部分）**

1. **强制执行边界**：仍无强制入口；策略与校验器在宿主上仍可被改写。这是护栏，不是边界。
2. **item 8 完整链路**：正式角色配置现在存在了，但"委派路径下权限是否生效"需要在 IDE 里
   实测，agent 自己测不了自己的权限层。
3. **真实测试接入**：`qa/baseline`、`qa/plan` 仍无正式数据（需求基线由人维护，不能由 agent
   生成，否则分母失去意义）；collect/执行两步待被测项目输入。
4. **平台门禁**：`main.protected=false`、有效 Rulesets 为空、CODEOWNERS 仍是占位符。
   这三项需要仓库管理员权限，我做不了。
5. **补充证据**：写入 PID 归属需 `sudo` 级内核追踪；原始采样仍只在本地 `/tmp`，
   长期受控 artifact 存储未交付。

### 第三轮复核：新实现的接口不兼容与弱判据，五条全修（2026-09-22）

核对 `593cbf4` 的独立复核判"新实现存在阻断缺陷，暂不能认定门禁证据链已接通"。五条全部成立，
其中两条是我上一轮自己引入的。

**P1 转换结果与下游消费接口不兼容（最严重）**

我写 `rebuild_run_json.py` 时没读消费方的契约：输出 `attempts[].attempt_id` 与嵌套
`version_binding{...}`，而 `trace_matrix.load_runs` 读 `attempts[].node_id`、
`gate_check.check_run_integrity` 读**顶层** `candidate_sha`/`baseline_version`/`baseline_hash`。
后果是所有 attempt 被判 `ATTEMPT_ENTRY_INVALID` 跳过、绑定字段全报 ABSENT——**32 项单测全绿，
整条证据链根本没接通**。复核用真实调用链替换运行记录后直接得到 INCOMPLETE 与六个 finding。

修复：按真实契约输出（顶层绑定字段 + `node_id` + `attempt` + `params` + `interrupted` + `config`）。
并且 `node_id` 不是改字段名就完事——JUnit 的 `classname::name` 不等于框架 collect 的 node_id，
新增 `--node-id-strategy`（`pytest` 把 `tests.test_x` / `test_y[1-2]` 映射为
`tests/test_x.py::test_y` + `params=1-2`；`raw` 原样；`map` 只认显式映射表，命中不到就报
`NODE_ID_UNMAPPED` 而不是猜）。

**新增 20 项集成测试**（`tests/test_integration_junit_to_gate.py`），断言的对象是门禁最终结论：
正例整链 PASS；一份好报告 + 一份损坏报告必须 INCOMPLETE；映射策略错必须不通过；
失败是 FAIL 而 skip 是 INCOMPLETE；运行后改基线必须 `EVIDENCE_STALE`；重试序号递增。

**P1 `incomplete` 标记没有下游消费者**

下游判"证据不完整不得通过"用的是 `interrupted`，我却只写了自造的 `incomplete`。修复：
两个字段同时写，`interrupted` 供既有消费者。集成测试里顺带逮到一个我没想到的缺口：
**一份合法但零 testcase 的报告**（分片丢结果）原先被静默忽略，"好报告 + 空报告"仍判 PASS。
现在按文件记 `EMPTY_REPORT`。

**P1 Authorization 脱敏保留了凭据值**

正则 `authorization:\s*\S+` 只吃掉授权方案 `Bearer`，凭据值原样留下；而我的测试断言的是
"Bearer zzz"整串不出现——Bearer 被删掉就算过。**判据比被测对象弱，这已经是第三次同类**。
修复：整行吃掉 Authorization/cookie 的值，另加裸 `Bearer`/`Basic` 凭据模式；测试改为用合成
canary 断言**凭据值本身**不出现在整份 `run.json` 的任何位置，并补一条专门盯"只删前缀"的回归。

**P1/P2 角色校验器判据过弱 + 畸形输入崩溃**

- `startswith(prefix)` 让 `deny qa/baseline/one-file.txt` 也算"已拒绝该目录"。复核把每个
  受保护目录的 deny 缩成单个文件，校验器零 findings。改为 `covers_prefix()`：只认
  `dir/**`、`dir/*`、`dir` 或上层递归通配，并有 9 项判定表测试（含 `qa/baselines/**` 不得
  误算成覆盖 `qa/baseline/`）。
- `allow: ["**"]` 也零 findings。新增按角色声明的写入范围表 `ROLE_WRITE_SCOPE` +
  `ALLOW_OVERBROAD` / `ALLOW_OUTSIDE_ROLE_SCOPE` / `ROLE_WRITE_SCOPE_UNDECLARED`：
  deny 了受保护路径 ≠ 只写自己该写的，受保护清单之外还有别的角色的产物。
- `tools=[{}]` 抛 `TypeError: unhashable type: dict`。先查元素类型再做集合运算；
  `match` 里的非字符串项同样先过滤。
- 判据加严后**逮到真实配置的实际缺口**：`.kiro/**`（角色配置自身）没被 deny，已补。

**P1 CI 链路的契约与隔离问题**

1. `baseline_version` 原先用 `qa/baseline` 的 git tree SHA、`baseline_hash` 用文件哈希清单的
   哈希，与消费者的 `meta.baseline_version` 和规范化内容 `sha256:...` 是两套契约。改为
   `--baseline-file` 走同一套算法。
2. 导出了 `.gate-policy` 却仍用 `--root .` 跑 trace/gate，读的还是候选 checkout 的
   `qa/baseline`/`qa/plan`——导出了没消费。改为组装 `.gate-root`（受保护侧：脚本 + 规则 +
   基线；候选侧：用例 + collect），判定一律以它为 `--root`。
3. `find ... | head -1` 只取一份 raw 目录，分片一多就静默漏结果。改为把所有分片作为多个
   `--raw` 传入；产物下载到专属 `ci-artifacts/raw`，不再落进 `qa/runs/`（避免与候选自带产物混淆）。
4. 重建失败时后续步骤没有 `always()`，门禁被跳过——job 会红，但**不会产出**所宣称的
   INCOMPLETE 报告。改为 `continue-on-error` + 后续步骤 `if: always()`。
5. `git cat-file -e $TARGET_SHA:qa/baseline` 判目录存在，而只有 `.gitkeep` 的目录同样存在。
   改为查找有效的 `requirements.yaml`。

另外在 workflow 头部写明：**本 workflow 从未在远端实际运行过**（当前提交 Actions runs 为 0），
以上是静态调用链上已修正的问题，不等于端到端跑通。

**验证**：`make check` = 181 项结构校验 PASS + 角色配置校验 PASS + **446 项测试通过**；
workflow YAML 可解析。

**这一轮的教训（与前两轮同源，但换了一层）**：前两轮的教训是"判据比被测对象弱"，这一轮多了
一条——**新写的组件必须按消费方的契约写，并用跨组件的集成测试证明**。我为转换器写了 32 项
单测，它们全部只验证"我打算输出什么"，没有一项验证"下游能不能用"。单测数量在这类缺陷面前
完全没有信息量。

### 第四轮复核：判定树漏输入会让已确认阻断消失，五条全修（2026-09-22）

核对 `ecf1c32` 的复核确认上一轮的字段契约、完整性传递、Bearer 精确反例修复有效，
但 **CI 输入组装仍有阻断风险**。五条全部成立。

**P1 判定树漏掉缺陷记录 → 已确认阻断可以消失（最严重）**

`.gate-root` 只带了脚本、`qa/plan`、`qa/baseline`、`qa/cases`、`qa/trace` 和重建后的
`qa/runs`，**没有 `qa/defects`**。复核的等价反例：同一套基线/规则/用例/collect/运行结果，
带缺陷记录 `BUG-1` 时门禁 `FAIL / BLOCKING_DEFECT_NOT_CLEARED`；按当时的目录清单组装后
变成 **`PASS / findings=[]`**。阻断不是被判掉的，是被"输入没带进来"删掉的。

只把目录加进清单不够——目录为空时同样会静默变成"零阻断"。所以引入**证据来源声明**
`qa/evidence-source.yaml`，gate 新增 `check_evidence_sources`：

| 情况 | 结论 |
|---|---|
| 声明文件缺失 | `EVIDENCE_SOURCE_UNDECLARED` → INCOMPLETE |
| 缺 collect/runs/defects 任一条目 | `EVIDENCE_SOURCE_ENTRY_ABSENT` → INCOMPLETE |
| `kind: candidate_copy` | `EVIDENCE_SOURCE_UNTRUSTED` → INCOMPLETE（候选可任意增删该证据） |
| `kind` 不在可信取值内 | `EVIDENCE_SOURCE_KIND_UNKNOWN` → INCOMPLETE |
| 声明可信但无 `ref` | `EVIDENCE_SOURCE_REF_ABSENT` → INCOMPLETE |

workflow 相应把 `qa/defects` 带进判定树，并**如实**写来源声明：当前 collect 与缺陷记录
确实来自候选 checkout，所以写 `candidate_copy`，结果是这份 workflow 组装出来的证据
**必然判 INCOMPLETE**。这是刻意的：写 `trusted_ci` 就是粉饰。要真正通过，需要执行层随原始
产物上传 collect 清单、缺陷记录改为从跟踪系统导出。

**P1 产物目录未建立可信来源**

"换个目录"不等于"候选之外的干净目录"：候选可以在仓库里预置 `ci-artifacts/raw`，甚至放
符号链接。修复：产物落到 `$RUNNER_TEMP`（checkout 之外）；目录若已存在直接失败拒绝复用；
下载后校验目录内无符号链接；下载失败或目录为空时**不退回候选产物**，直接让门禁 INCOMPLETE；
所有分片作为多个 `--raw` 传入。

**P2 `map` 策略的参数化 node_id 与消费者不匹配**

代码算了 `base, params = split_params(...)` 却返回原 mapping 值，映射到
`...::test_ok[one]` 时 node_id 带着后缀、同时又给 `params=one`，而 collect 按"无后缀
node_id + params"匹配 → 整链 `REQUIRED_CASE_NOT_EXECUTED`。改为返回 `base`，
并补 map 策略参数化的整链用例。

**P2 跨文件重试序号重置**

`seen` 建在每个文件内，同一 node_id 分别出现在 `first.xml`(failed) 与 `second.xml`(passed)
时都是 `attempt=1`，"先失败后通过不得自动抹掉阻断"就失去顺序依据。但**把计数器提到全局
并不等于解决**：重复可能是重试、分片重复或不同环境，JUnit 没有尝试序号。

所以：序号改为跨文件统一分组分配，且默认 `--retry-order none` —— 出现重复即记
`DUPLICATE_ATTEMPTS_WITHOUT_ORDER_EVIDENCE` 并置 `interrupted`，不按文件名排序猜"末次"。
只有调用方显式声明 `--retry-order document-order`（即该适配器保证文档序与 `--raw` 传入
顺序等于执行序）才分配序号，并把这个假设写进 `run.json` 的 `retry_order_assumption`。

**P2 `always()` 不保证缺基线时写出报告**

无有效基线时 `gate_check` CLI 在写 `--out` 之前提前 return：stdout 显示 INCOMPLETE，
但报告文件不存在。CI 的 `if: always()` 只保证步骤被尝试执行，不保证脚本落盘，下游会读到
旧报告或什么都读不到。修复：该分支也生成统一格式报告（`BASELINE_ABSENT` +
verdict=INCOMPLETE + 当前 binding），并补测试断言文件确实存在。

**验证**：`make check` = 183 项结构校验 PASS + 角色配置校验 PASS + **461 项测试通过**；
workflow YAML 可解析。（上一轮汇报写的"181"是当时版本的输出，复核的 183 才是当前值。）

**这一轮的教训**：前几轮是"判据比被测对象弱"和"没按消费方契约写"，这一轮是
**判定输入的完整性本身没有判据**。门禁检查了结果、检查了版本绑定，却没有检查"该带进来的
证据是不是都带进来了、它们从哪来"。缺输入导致的通过比判错更危险，因为它在报告里
表现为 `findings=[]`——干净得看不出问题。

### 第五轮复核：workflow 变量先用后赋值、导出完整性、顺序传播、类型崩溃（2026-09-22）

核对 `88c71ed`。四条全部成立，且全部是本地可修的实现问题，不能归到"只剩外部输入"。

**P1 `RAW_ROOT` 在首次使用后才赋值**

"准备产物目录"步骤在 `set -euo pipefail` 下读 `$RAW_ROOT`，而唯一赋值在后面的"组装判定树"
步骤的 `GITHUB_ENV` 里。复核原样执行该步骤得到 `RAW_ROOT: unbound variable` 退出 1。
后果不是预期的"缺证据 INCOMPLETE"，而是**流程中断**：下载与组装步骤被跳过，
后面的 `if: always()` 也补不出可信脚本、绑定变量或有效报告。

修复：`RAW_ROOT` / `GATE_ROOT` 提到 **job 级 `env:`**，保证所有步骤（含
`download-artifact` 的 `with.path`）都能看到。

并且补了判据：新增 `scripts/check_workflow_env.py`，**按真实步骤顺序**模拟变量可见性
（workflow/job/step env、更早步骤写入 `$GITHUB_ENV`、同步骤内赋值、`for`/`read` 绑定、
runner 内置、`${VAR:-default}` 安全形式、`${{ env.X }}` 未定义会静默变空串），
接进 `make check`（`make validate-workflow`）。26 项测试，含复核那个精确场景。
写检查器时它先逮到我两处：正则把 `read -r d` 的 `d` 当成 `-r` 的参数吃掉（误报），
以及 `run-tests` 占位步骤真的引用了未定义的 `RUN_ID`（真实问题，已在 job env 定义）。
**只验证 YAML 可解析覆盖不到这一类。**

**P1 来源声明证明不了导出集合完整**

上一轮的 `EVIDENCE_SOURCE_*` 只检查标签与 ref 非空，分不清"成功导出零缺陷"与
"导出内容漏带"：保持同一份 `tracker` 声明，仅漏带缺陷文件，门禁仍 `PASS / findings=[]`。
原来的反例没被关闭。

修复：`kind: tracker` 必须附带导出清单 `qa/defects/export-manifest.json`
（`query_ref` / `exported_at` / `complete` / `records[{id, sha256}]`），并逐条核对：

| 情况 | 结论 |
|---|---|
| 清单缺失 | `DEFECT_EXPORT_MANIFEST_ABSENT` |
| `complete != true` | `DEFECT_EXPORT_INCOMPLETE` |
| 清单有、输入没有 | `DEFECT_RECORD_MISSING`（**这就是漏带反例**） |
| 输入有、清单没有 | `DEFECT_RECORD_UNDECLARED` |
| 哈希不符 | `DEFECT_RECORD_MODIFIED` |
| `records: []` + `complete: true` | 有效的显式空集合，放行 |

**P2 顺序未知却仍给出"末次"结果**

默认 `none` 把重复记录都标成 `attempt=1`，下游照旧取列表最后一条：同一组 failed/passed
仅交换输入排列，`last_result` 从 true 变 false、门禁从 INCOMPLETE 变 FAIL。
虽然当时都带 `RUN_INTERRUPTED` 没造成放行，但**判定不能由文件排列决定**。

修复：顺序未知时重建器**不给序号**（`attempt: null` + `order_known: false`）；
`trace_matrix` 识别"序号缺失或重复"为 `ATTEMPT_ORDER_UNKNOWN`（BLOCKING），
该 TC 的 `last_result` 置 `None`，并对已有序号的组按序号排序而不是按出现顺序。
新增排列不变性测试：两种排列的 verdict 与 last_result 必须一致。

**P2 非法 `kind` 类型导致崩溃且不落盘**

`kind: [tracker]` 是合法 YAML，但集合成员检查抛 `TypeError: unhashable type: 'list'`，
CLI 非零退出且 `--out` 文件不存在。修复：先查类型（`EVIDENCE_SOURCE_KIND_INVALID`），
`ref` 同样要求字符串；另加兜底——`evaluate` 的任何未预期异常都写出
`GATE_INTERNAL_ERROR` + INCOMPLETE 报告。崩溃不是判定，但"没有报告"更糟。

**验证**：`make check` = 190 项结构校验 PASS + 角色配置校验 PASS + **workflow 变量检查
PASS** + **510 项测试通过**。

**这一轮的教训**：三轮下来问题在往上游走——先是判据比被测对象弱，然后是没按消费方契约写，
再是判定输入的完整性没有判据，这一轮是**流水线自身的可执行性没有判据**（变量先用后赋值、
崩溃不落盘）。共同点是：我总在验证"业务逻辑对不对"，而没有验证"这套检查在真实执行顺序下
能不能跑起来、跑不起来时还剩什么"。

### 第六轮复核：空摘要绕过内容核验、变量检查错误推广可见性（2026-09-22）

核对 `98bed62`。上一轮四个精确反例已关闭；本轮两条新发现均成立且为本地可修问题。

**P1 缺陷导出条目缺少 SHA-256 时直接跳过内容核验**

上一版只要求 `id`，随后 `if expected_hash:` 才核验内容。结果是把已确认阻断缺陷改为
`closed: true` 后：保留原摘要会得到 `DEFECT_RECORD_MODIFIED / INCOMPLETE`；但只要把
清单摘要删掉、设为 `null` 或空串，三种都变成 **PASS / findings=[]**。摘要本来是完整性
契约，却被实现成了可选优化。

修复：每条 `records[]` 必须有非空字符串 `id` 与格式正确的 SHA-256（64 位十六进制，
可带 `sha256:` 前缀）。缺失、`null`、空串、错误类型、错误长度、非十六进制均产生
`DEFECT_EXPORT_SHA256_INVALID` → INCOMPLETE；重复 ID 产生
`DEFECT_EXPORT_RECORD_DUPLICATE`。即使摘要非法仍登记该 ID，避免同一问题又被误报成
`UNDECLARED`，但绝不接受内容完整性。

回归测试直接覆盖完整攻击路径：原始阻断 → FAIL；篡改 `closed: true` 且保留摘要 →
INCOMPLETE；同一篡改文件删除/null/置空摘要 → 仍 INCOMPLETE 且 findings 非空。

**P2 workflow 检查器没有实现自己声明的步骤内数据流**

旧实现先 `_assigns(script)` 收集整段所有赋值，再一次性检查所有引用；因此未来赋值能
提前满足当前引用：

```bash
printf '%s' "$X"   # 实际 set -u 在这里退出
X=ok
```

同时 `_uses()` 按变量名把整段的安全引用集合减掉，一处 `${X:-fallback}` 会把同名变量
后面所有裸 `$X` 一起洗白；而 `:-` 并不会给 X 赋值。

修复：改为按**字符位置**生成事件流并顺序处理：普通引用、参数展开、普通赋值、`for`/`read`
绑定各有精确位置；未来赋值不提前生效；`${X:-...}`/`${X-...}` 只保护当前展开，
`${X:=...}`/`${X=...}` 在展开后才把变量标为已赋值；普通赋值在简单命令末尾才生效，
所以 `X=$X` 右侧不会被左侧提前洗白。`#`/`%`/替换类参数操作不再误当默认值保护。

测试用真实 `/bin/bash -c 'set -eu'` 同时执行精确反例与阳性对照：未来赋值失败、
默认值后裸引用失败、先赋值后读取成功、同一行前后顺序、`X=$X`、`${X:=...}` 与
`${X-...}` 的差异。

**保证范围收窄（不再说过头）**：检查器只承诺受支持语法中的**线性词法顺序**，不建模
`if/case` 分支支配、循环是否进入、函数调用、子 shell/命令替换作用域、`eval/source`。
PASS 只表示"线性词法上没有先读后写"，不是脚本按所有真实控制流可运行的证明；关键步骤
仍需真实 shell 故障路径执行。

**验证**：定向 `test_gate_check.py` 112 项通过；`test_check_workflow_env.py` 41 项通过。
全量结果见本提交前的 `make check` 输出。

**这一轮的教训**：把完整性字段做成可选，会把"缺证据"变成"跳过检查"；把数据流压成
两个名字集合，会丢掉最重要的信息——**顺序与单次引用的语义**。变量名相同不代表两次引用
等价，字段存在不代表字段可用。

### 第七轮复核：默认值 RHS 变量被外层参数展开整体屏蔽（2026-09-22）

核对 `d38b44e`。上一轮摘要攻击链与两个变量精确反例已关闭；新发现一条 P2 漏报，成立。

**问题：外层 `${...}` 的保护错误地屏蔽了默认值右侧变量**

事件解析器把整个 `${...}` 范围放进 `braced_ranges`，随后跳过该范围内所有 `$VAR`。
这只检查了最外层变量及其操作符，却把右侧表达式当成普通文本：

```bash
printf '%s' "${X:-$Y}"   # X 未定义时必须展开 $Y；Y 未定义应失败
printf '%s' "${X:=$X}"   # RHS 的 $X 必须在 := 给外层 X 赋值之前展开
```

两条都被检查器判零问题，真实 `/bin/bash -c 'set -eu'` 均退出 1。
外层 `:-`/`:=` 保护的是 X，不保护右侧其他变量，也不能回溯保护右侧的自身引用。

**修复**

- `$VAR` 不再因为位于 `${...}` 范围内就整体跳过；只跳过 Actions 的 `${{...}}`。
  因此 `${X:-$Y}` 会生成 Y 的 use 事件。
- `${X:=$X}` 的 RHS X use 事件位于外层 X 的 assign 事件之前；只有 RHS 全部检查完成，
  `:=` 才把 X 标为后续可见。
- 多个 RHS 引用逐一检查；`${X:-$Y}` 不传播 X，`${X:=$Y}` 在 Y 已定义时传播 X。
- 嵌套参数展开（如 `${X:-${Y:-fallback}}`）当前正则解析器不能可靠配对大括号，
  明确产生 `ENV_EXPANSION_UNSUPPORTED`，**不静默 PASS**。这不扩展成完整 Bash 解析器。

**测试**：新增 9 组精确场景并同时运行真实 Bash：两条复核反例、Y 先赋值阳性对照、
多个 RHS 引用、`:=` 在 RHS 后传播、`:-` 不传播、嵌套展开在内层已定义/未定义时均明确报未支持。

**保证范围不变**：PASS 仍只代表受支持语法中的线性词法顺序无先读后写；分支、函数、
子 shell、命令替换、`eval/source` 不在保证范围。遇到已知不能可靠建模的语法要显式
`UNSUPPORTED`，不能因为"不支持"而得到一张干净的 PASS。

### 第八轮复核：普通 `$VAR` 前紧贴字面量时漏报（2026-09-22）

核对 `5130d1e`。上一轮两个 RHS 精确反例及嵌套展开拒绝策略通过；新 P2 漏报成立。

旧正则为 `(?<![\$\w])\$([A-Za-z_][A-Za-z0-9_]*)`，错误要求 `$` 前不能是字母、数字或
下划线。但这些只是普通字面量：`prefix$Y`、`v1$Y`、`_$Y` 仍然展开 Y。复核的
`${X:-prefix$Y}`、`${X:=prefix$X}` 等均被检查器漏掉，真实 Bash `set -u` 失败。

修复：不再靠 `$` 前一字符做负向后顾，改为从 `$` 本身做有限 shell 词法扫描。支持并有
真实 Bash 对照的边界：字母/数字/下划线/分隔符/无前缀；奇数反斜杠转义、偶数反斜杠后
仍展开；`$$Y` 是 PID + 字面 Y；单引号内不展开、双引号内展开；`${{...}}` 属 Actions
表达式；简单注释跳过；`$Ysuffix` 识别为变量 Ysuffix，`${Y}suffix` 才是 Y 后接字面量。

新增 22 组边界测试并实际运行 `/bin/bash -c 'set -eu'`。首次运行先暴露测试自身的错误：
shell 的 `printf "%s"` 被 Python `%` 格式化误当成占位符，目标行为根本没执行；改用字符串
拼接后再跑，73 项定向测试通过。

保证范围没有扩大成完整 shell lexer：命令替换、here-doc、ANSI-C 引号等仍属于明确未建模
范围；这轮只关闭普通命名变量的前置字符边界与已列出的引号/转义对照。

### 第九轮复核：字面量中的 braced 赋值仍会改变检查器状态（2026-09-22）

核对 `17bf55b`。普通变量前缀边界通过；组合使用时存在 P2 假通过，成立。

上一轮只有 `_plain_var_occurrences()` 使用引号、转义与注释词法；
`BRACED_PARAM.finditer(script)` 仍扫描原始全文。因此以下三类非执行文本中的
`${X:=ok}` 会虚假生成 assign 事件，使后续真正的 `$X` 被当成已定义：

```bash
# ${X:=ok}
printf '%s' '${X:=ok}'
printf '%s' "\${X:=ok}"
```

检查器均零问题，真实 Bash `set -u` 都在后续 `$X` 处失败。真正执行
`printf '%s' "${X:=ok}"` 后再读取 X 的阳性对照则应通过并输出 `okok`。

修复：抽取 `_active_dollar_positions()` 作为**唯一词法上下文来源**。普通 `$VAR`、
braced `${...}` 的 use/assign、嵌套展开的 `UNSUPPORTED` 检查全部只消费同一组有效 `$`
位置。该扫描器统一处理注释、单/双引号、反斜杠转义、`$$` 与 Actions `${{...}}`。
注释/单引号/转义中的 braced use 也不再误报，字面量中的嵌套展开也不会虚假报 UNSUPPORTED。

新增 10 项组合测试并用真实 Bash 对照：三条假赋值反例、真实赋值 `okok` 阳性对照、
注释/单引号/转义/双引号中的 braced use、注释中的嵌套展开、active 位置表。

教训：声明支持一组词法边界时，**所有会改变分析状态的事件必须消费同一份词法上下文**。
普通引用正确、braced 赋值绕过词法层，组合后仍然是假通过；分别测试两个 scanner 不等于
测试它们共享同一语义。
