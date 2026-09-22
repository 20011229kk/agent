# QA Agent Suite — Tasks

**本文件是唯一任务状态来源。** 完成、暂缓、阻塞与验证结果只在这里维护，不另建计划文件。

状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 已完成 · `[!]` 阻塞 · `[-]` 暂缓

依赖分两轨：**轨 A** 平台无关，不等探针；**轨 B** 依赖 Task 0 结论。
两轨表示依赖可分离，不要求使用子代理并行。

---

## 轨 B 前置

### [x] Task 0 — Kiro 能力探针（12 项全部完成，1 项新阻断项）

产出：`docs/capability-matrix.md`（平台能力唯一事实来源），原始证据 `docs/probe-evidence/`。

**第一轮（本会话直接执行）**

- [x] item 1 环境快照（IDE 1.1.14 / kiro-cli 2.22.1 / ACP client / 既有权限规则 / session 作用域授权 / python 3.9.6+pytest）
- [x] item 3 工具级拒绝 — **已验证支持**；实测硬拒绝清单 7 条（文档只载 3 条）
- [x] item 4 合法写入 — **已验证支持**
- [x] item 5 shell 子进程越权 — **已验证不支持（阻断项）**：子进程写入完全绕过能力层，含 kiro-scope 硬拒绝
- [x] item 9 配置与 steering 保护边界 — **已验证**：`.kiro/steering/**` 无硬保护；`.kiro/agents/**` 的"永久询问"退化为静默允许

**第二轮（用户在 IDE 交互执行，2026-09-20）**

- [x] item 2 工具不存在 — **已验证**：readonly 生效时 toolCount=6，`allowed/e.txt` 未创建
- [x] item 6 命令解析边界 — **已验证**：直接/`&&`/`;`/管道被拒；`bash -c`/变量间接进入 ask，非静默绕过
- [x] item 7 父子代理权限交集 — **已验证**：父省略 tools→子可写；父显式 deny→子被拒（原设计成立）
- [x] **item 8 子代理自身权限隔离 — 已验证不支持，新阻断项，比 item 5 更严重（不涉及 shell）**：
  同一份 `probe-write-ask` 配置直接调用时拒绝 `protected/**`，被 `probe-parent-omit` 委派调用时
  同一路径写入成功。委派本身绕过了子代理自己的 `permissions.deny`。详见能力矩阵 item 8 一节。
- [x] item 10 File hook 触发源 — **已验证**：agent 改动触发（0→1 行），编辑器保存不触发
- [x] item 11 Stop hook `decision:block` — **已验证**：block 后模型继续输出标记字符串（本 IDE 行为）
- [x] item 12 `tools` 标签实测取值 — **部分已验证**（含一次自我纠正，见下）：`todo_list` 是具体工具 ID；
  `knowledge` 不在标签注册表内（静默无效）；`spec`/`context` 的 `toolCount=1` **不足以证明**
  它们匹配到了 `disclose_context`——该工具是每个配置的必带工具，单独出现不能反推标签匹配成功，
  这两个标签的实际匹配结果**未能验证**，需补探针

**本轮额外发现（不在原 12 项清单内，但改变了正式配置的写法；2026-09-20 复审后部分收窄）**

- [x] **配置加载兼容性（已验证范围收窄）**：含 CLI-only 字段（`allowedTools`/`toolsSettings`）
  且无 `permissions` 块的配置被 IDE **静默跳过**。**不外推**为"所有正式配置都必须带
  `permissions` 块"——已验证的只是这一具体组合，其他配置是否也需要尚未测试。
- [x] **委派会触发一次性审批**（已验证的行为事实）。`trustedAgents`/`availableAgents`
  在本 IDE 到底控制什么**仍未验证清楚**，不能反推它们"控制能不能派、不控制受不受权限
  约束"——这类细分结论超出证据，需补充探针。
- [x] **证据可信度三原则确立**（第三次遇到模型自述与外部事实不一致后固化）：
  探针判据一律以外部可复核证据为准，不采信模型自述的成功/失败/因果归因；
  会话存档的 `status: failed` 字段不可信（已证实与实际落盘结果矛盾）；
  工具层报 `success:true` 时退出码仍可能未采集，不能据此判断真实 exit 0。

执行步骤见 `docs/probe-evidence/RUNBOOK.md`；一次性探针配置在 `docs/probe-evidence/probe-configs/`。
第二轮的完整证据包（`before/`、`configs-tested/`、`sess_*.jsonl`、`runtime-events.log`、
`approval-events.json`、`manifest-sha256.json` 等）已在用户指定路径核对：
`manifest-sha256.json` 与 `final-canary-verification.json` 已读取核验，与 `report.md`
叙述一致；`child-policy-callsite-excerpts.json`、`named-subagent-source.txt` 的压缩代码
摘录已核对，与报告描述的条件表达式吻合（仍是静态摘录，非运行时调试确认）。

---

## 轨 A（平台无关）

### [x] Task 1 — 仓库骨架、spec 三件套、试点前基线采集

- [x] 仓库骨架建于 Kiro 工作区根 `/Users/AI/test-agent`（项目级 `.kiro/` 作用域，git init，branch main，VERSION 0.1.0）
- [x] 探针原始证据收入 `docs/probe-evidence/`（`/tmp` 清空后不失证）
- [x] `.doc/specs/qa-agents/requirements.md` — User Story + EARS 写入 R1–R9 + 六条反例验收
- [x] `.doc/specs/qa-agents/design.md` — Overview / Architecture / 时序图 / 组件数据工作流 / 约束取舍 / 测试策略
- [x] `.doc/specs/qa-agents/tasks.md` — 本文件
- [x] `templates/required-scope.yaml` 格式定义（含 `retry_policy` 三个防抹除字段、`blocking_defect_severities` 留空则 INCOMPLETE、`review_mode`）
- [x] `templates/requirements.yaml` 基线格式定义（meta 六字段含 `baseline_approval_ref` 与 `content_hash`）
- [x] `templates/test-cases.md` 用例格式（frontmatter `covers`/`manual`/`layer`/`priority`/`method`）
- [x] `CODEOWNERS` 与 `docs/protected-paths.md` 受保护路径清单（含 L1–L4 分层防护与"为何不靠权限规则"）
- [x] 试点前基线采集 `qa/metrics/pilot-baseline.yaml` — 全部项目数据**显式标 MISSING 并附 reason**（用户尚未提供 pilot_repo），`meta.status: INCOMPLETE`
- [x] `scripts/validate_spec.py` — 133 项检查：三件套结构、R1–R9 条目、EARS 关键字含 SHALL NOT、design 必备章节与残余风险、tasks 状态标记、基线字段完整性、模板 YAML 可解析、CODEOWNERS 受保护条目
- [x] 测试 `tests/test_validate_spec.py` — 26 项，全部为反例：缺文件、漏需求条目、删 SHALL NOT、删残余风险、无状态标记、非法标记、MISSING 无 reason、**用 0 冒充未采集基线**、MEASURED 无 source、**基线标 COMPLETE 但仍有 MISSING**、模板 YAML 非法、CODEOWNERS 缺受保护条目、单一缺陷不得掩盖其他检查
- [x] `Makefile`（`make check` = validate + test）与 `.gitignore`（派生产物与原始报告不入库）

**Demo 判据（已达成）：** `make check` → 133 项结构校验 PASS + 26 项单测通过，退出码 0。
基线现状可读且缺失项显式标注，不可被误认为已采集。

**校验器自查发现的真实缺陷（已修）：** `meta.status`（COMPLETE/INCOMPLETE）与度量项
`status`（MEASURED/MISSING）取值域冲突，导致汇总状态被误判为度量项。

### [x] Task 2 — 追溯数据模型与 `trace_matrix.py`

- [x] 数据契约 `REQ ← TC ← AT ← attempt`；基线 meta 含 `baseline_version`/`confirmed_by`/`baseline_approval_ref`/`content_hash`
- [x] `scripts/trace_matrix.py`：扫基线 + 用例 frontmatter + **实际 collect 清单** + 缺陷 frontmatter + 必测范围；产出 `matrix.csv`（派生，无写入者）
- [x] 四状态统计；`manual: true` 不进自动化分母但**仍进必测范围校验**
- [x] **证据缺失与"已确认未自动化"分开**：无 collect 清单时 `automated=None` + `AUTOMATION_EVIDENCE_ABSENT`，不报成 0%
- [x] `--fail-on-gap`；`--update-hash`；基线哈希变更传播 `EVIDENCE_STALE`；内容改而哈希未更新报 `BASELINE_HASH_MISMATCH`
- [x] 缺口分级 `BLOCKING / GAP / HINT`；`UNATTRIBUTED_AT` 仅提示不阻断
- [x] 归因取值校验：`FLAKY` 不是合法根因；稳定性轴独立取值
- [x] 测试 `tests/test_trace_matrix.py` — 32 项，含假全覆盖、空基线、基线未确认、证据缺失≠未自动化、manual 三例、错标/漏标/未归属、重复 ID、参数化全通过、多对多全通过、一 AT 覆盖多 TC、重试标记 intermittent、已确认阻断缺陷仍呈现、STALE、哈希不符、必测范围四例、CSV 产出

**Demo 判据（已达成）：** 四状态覆盖表输出正常；基线 10 条用例只覆盖 8 条时精确报出
`REQ-9`/`REQ-10` 未覆盖，`req_total` 仍为 10（分母来自基线，不会算成全覆盖）。

**变异检验（5/5 被捕获）：** 证据缺失当 False、manual 进分母、参数化改 any、
分母改用例数、去掉手工必测证据检查 —— 每一项都让测试失败，文件哈希验证已恢复。
证据：`docs/probe-evidence/mutation-check-task2.txt`

### [x] Task 6a — `gate_check.py` 三态门禁纯函数

- [x] 三态判定顺序（FAIL → INCOMPLETE → PASS，短路）；发现项按 `QUALITY_FAILURE`/`EVIDENCE_MISSING`/`HINT` 分类
- [x] `last_result`（观察）与 `gate_accepted`（判定）分离存储，报告中同时呈现
- [x] 六字段版本绑定；`policy_source=merge_base` 报 `POLICY_FROM_MERGE_BASE`；`policy_from_candidate` 报 `POLICY_FROM_CANDIDATE`（质量失败，防候选自我放行）
- [x] `review_mode` 两模式；`manual` 需平台审批引用，`automated_required` 缺 verdict 一律 INCOMPLETE
- [x] 完整性规则先行；`thresholds.status = NOT_CONFIGURED`，**v1 不填任意数字**
- [x] 产出 `merge-gate-report.json` + 人可读报告；**序列化产物与文本报告均须列全所有失败与缺失项**
- [x] 运行完整性：`RUN_ABSENT`/`RUN_INTERRUPTED`/`RUN_CANDIDATE_MISMATCH`；凭据脱敏检查
- [x] 测试 `tests/test_gate_check.py` — 33 项，六条反例逐条覆盖
- [x] 测试：完整性各触发条件落 `INCOMPLETE`、质量失败落 `FAIL`，且 FAIL 优先时缺失项不被隐藏

**Demo 判据（已达成）：** 同一仓库下，删运行记录 → `INCOMPLETE`（证据不足，质量失败数 0）；
改成用例失败 → `FAIL`（质量失败数 ≥1）；两者理由与措辞不同，报告均列全明细。

**变异检验（10/10 被捕获）：** 抹掉阻断缺陷检查、间歇失败直接算通过、不校验评审 SHA、
不校验 verdict 产出者、缺 verdict 放行、证据缺失判 PASS、FAIL 降级为 INCOMPLETE、
报告截断、脱敏失效、绑定字段不校验。
证据：`docs/probe-evidence/mutation-check-task6a.txt`

**变异检验发现的真实测试盲区（已补）：** 首轮"报告只列第一条发现"变异**未被捕获**——
33 项测试当时全部读 `rep.findings` 属性，无一条检查**序列化产物**，而 CI 写入
`merge-gate-report.json`、人实际读到的正是序列化结果。已补 3 项测试覆盖序列化与
文本渲染边界，复验后 10/10 全部捕获。

### [x] Task 6b — `guard_scan.py` 风险提示器（交付 R7）

- [x] 7 条规则：`SKIP_ADDED`、`ASSERTION_REMOVED`、`THRESHOLD_CHANGED`、`RETRY_POLICY_CHANGED`、`IGNORE_WIDENED`/`IGNORE_FILE_WIDENED`、`TEST_FILE_DELETED`、`PROTECTED_PATH_TOUCHED`
- [x] 输出进 `merge-gate-report` 提示段落（`gate_check --diff-file`）；`DISCLAIMER` 明确写出**不能**判断断言语义与数据清理有效性；`to_gate_findings()` 一律产出 `HINT`；CLI 退出码恒为 0（阻断权不在正则手里）
- [x] 命中项未关闭 → `GUARD_HINTS_UNREVIEWED`（EVIDENCE_MISSING → INCOMPLETE），不会被静默忽略
- [x] 豁免机制 `qa/plan/guard-waivers.yaml`（受保护路径）：**必须同时给出 reason 与 alternative_coverage 才生效**；模板 `templates/guard-waivers.yaml`
- [x] 测试 `tests/test_guard_scan.py` — 65 项，每条规则正反例齐备
- [x] 测试："看似合规但删掉了断言"必须命中；等量替换不得命中
- [x] 测试：正常变更集误报为 0；`Makefile` 的 `gate` 目标串起 trace → guard → gate

**Demo 判据（已达成）：** 对含 7 个文件的真实变更 diff 输出 7 条提示并逐条给出证据；
普通重构 `src/service.py` 静默通过；提示在门禁报告中以 `GUARD_*` HINT 出现并触发
`GUARD_HINTS_UNREVIEWED` → INCOMPLETE。
证据：`docs/probe-evidence/demo-guard-scan-output.txt`、`demo-candidate.diff`

**变异检验（12/12 被捕获）：** 7 条规则逐一失效、阈值方向判反、豁免不要求替代覆盖、
提示器升级为判定器、去掉声明、序列化截断、未评审提示被静默忽略、提示不进报告。
证据：`docs/probe-evidence/mutation-check-task6b.txt`

**端到端 demo 发现的两个真实缺陷（已修 + 补回归测试）：**
1. **多文件 diff 中删除文件路径错归属** —— 删除时 `+++` 是 `/dev/null`，路径须取自同一
   文件段的 `---` 行；原实现用跨文件共享游标回溯，把 `tests/test_login_dup.py` 的删除
   记到了 `tests/test_register.py` 上。单文件 diff 的测试掩盖了它。已重写
   `parse_unified_diff` 并补 2 项回归测试。
2. **同一事实重复报警** —— 文件删除时同时报 `TEST_FILE_DELETED` 与 `ASSERTION_REMOVED`。
   重复报警正是让工具被当噪音关掉的原因，已去重。

**Demo 判据：** 对一次真实变更输出提示清单及其在门禁报告中的位置。

---

## 轨 B（依赖 Task 0）

### [!] Task 3 — 权限规则与执行隔离

**状态（2026-09-21 外部复核后更新）：仍阻塞，但阻塞点变了。**

shell 侧（item 5）的挂载边界已落地并通过 C0–C9 十项验证，包括外部复核实测出的两条绕过
被拒绝，这部分可用。仍阻塞的是**"强制"与"归属"**两件事：没有机制强制必须走包装器；
策略与校验器本身在仓库里可被改写；写入的权威 PID 归属未取得。

进程观测的结论已收窄为"本轮快照没有记录到新建进程"，**不再**声称"IDE 内没有 per-agent
进程边界"，相关架构推论已撤回——采样器不做 PID 归属，没有新进程也可能由常驻进程写入。
item 8（工具级委派写入）仍未闭环，且不能由 shell 包装器代替。

- [ ] 按实测写 `permissions.rules`；`qa-lead` 用 tools 省略而非 deny；配置**始终带
  `permissions` 块**（即便为空规则），否则被 IDE 静默跳过（本轮新发现）
- [ ] `qa-executor` 写路径显式 allow（headless/子代理下 ask 等同 deny）
- [~] **进程边界观测：部分完成（2026-09-21 外部复核后从"已完成"降级）**
  证据与适用范围见 `docs/probe-evidence/task3/task3-findings.md` 及 `task3/results/`：
  - [x] shell 命令的进程归属：`Kiro Helper`(3612) → `Kiro`(3590) 的后代进程，宿主机无隔离
  - [x] 委派写入期间的进程创建观测（pass 4，实测间隔 max 0.105s，248 次快照）：
        **没有记录到** Kiro 树中的新建进程（唯一 `sh` 与探针自身 shell 命令同时刻）
  - [x] 检测下限按每寿命重复 5 次标定：1s/300ms/150ms/100ms 均 5/5，**50ms 3/5、20ms 1/5**
  - [x] 原始采样做**字段裁剪归档**入库（保留所有快照时间点、Kiro 子树、整表 sha256、
        命令行字典表），完整文件哈希记录在 `reduced-manifest.json`
  - [ ] 归档的长期可复核性未解决：**哈希只能在原件存在时校验对应关系**，原件目前只在本地
        `/tmp`，属会过期证据。需要长期独立复核时应把原始压缩件放入 Git 之外的受控
        artifact 存储，Git 只记标识与摘要
  - [x] 措辞收窄：间隔是快照**开始时刻**之间的差值，不代表原子采集，也不保证必然捕获所有
        超过 100ms 的进程；`kiro_tree` 只体现既定筛选规则被忠实应用，与 PID 归属无关
  - [ ] **写入的权威 PID 归属未取得** —— 采样器只观察进程创建，不做归属；没有新进程仍
        可能由采样前已存在的常驻进程完成写入。需内核级追踪（`sudo fs_usage`，覆盖直接与
        委派两条链路、唯一 canary、记录 open/write/rename 事件）
  - [x] 已撤回三条超出证据的推论："不存在 per-agent 进程边界""委派链内部拆分不可能"
        "顶层会话即独立 OS 进程"
  - 附带记录：首版 FIFO+`lsof` 定位 PID 的方法被对照组证伪（阻塞在 `open()` 时无 fd）
- [x] item 5（shell 起源写入）隔离落地并验证 — 2026-09-21，两轮：
  - `isolation/mount-policy.yaml` + `mount_policy.py`：**可写范围由策略决定，不由调用参数
    决定**；`allowed_rw` 白名单 + `protected` 双重否决 + 镜像 digest + `network.mode`
  - `isolation/run-isolated.sh` 自己不拼挂载参数；拒绝 `QA_ISOLATION_REPO`/镜像覆盖
  - `isolation/verify-isolation.sh`：**C0–C12 全 13 项 PASS**，含镜像身份核对与运行时
    digest 绑定、阳性对照、两条挂载绕过回归（C6 `--rw qa/baseline` / C7 `--rw ../outside`）、
    两条参数保真回归（C10 多行命令 / C11 空参数）
  - 容器内跑完仓库全量测试通过（307 passed + 4 skipped，skip 的是需要 podman 的端到端
    用例，在宿主机实际执行）；`isolation/` 自身 74 项单测
  - 证据：`isolation-verify-c0-c12.txt`、`isolation-pytest-in-container.txt`、
    `isolation-image-build.txt`
- [x] 修复外部复核发现的包装器扩权漏洞（P1）：原实现接受任意 `--rw`，实测两条绕过成功
  （重新挂载受保护目录、父目录穿越）。已固化为 C6/C7 + `tests/test_mount_policy.py` 回归
- [x] 修复 C4 网络假通过（P2）：原判据捕获所有 `OSError`，离线故障注入证明
  `ConnectionRefused`/`Timeout` 同样命中 PASS。现为结构性证据 + errno 限定 + 同端点阳性对照
- [x] 修复默认镜像未接上（P2）：默认曾是 `python:3.12-slim`，默认镜像里 `import pytest` 报
  `ModuleNotFoundError`；现默认取策略镜像并默认拒绝覆盖
- [x] **修复按行传参导致的假成功（第二轮复核 P1，我上一轮引入的缺陷）**：
  校验器逐行输出 podman 参数、shell 按行读回，参数内部的换行被当成分隔符 ——
  `python -c $'print("BEGIN")\nraise SystemExit(42)'` 只打印 BEGIN 就退出 **0**，
  失败逻辑根本没执行；空参数也被跳过。改为 `mount_policy.py run` 在 Python 内校验后
  直接 `os.execvp`，argv 全程数组；`plan` 默认 NUL 分隔且标注不可用于执行。
  回归：C10（多行必须退出 42，单行阳性对照先过）、C11（空参数保留）+ 端到端单测
- [x] **修复运行时镜像未按 digest 绑定（第二轮复核 P2）**：C0 只在验证脚本里核对，
  普通执行路径仍按可变标签启动，同名标签重建后会静默换镜像。现在 `run` 路径用一次
  `podman image inspect` 同时取 digest 与 image id，核对后按 **image id** 启动；
  digest 不符 / 镜像不存在 / 策略未记 digest 一律拒绝执行。回归：C12 + 4 项单测
- [ ] **隔离的强制点尚未闭环（两层都没闭）**：
  - 没有机制强制 `qa-executor` 必须走包装器。候选方案是把该角色 shell allow 收窄到只有
    `isolation/run-isolated.sh`，但 `bash -c` 与变量间接会进入 `ask` —— 需实测确认在
    子代理/headless 下确实等同 deny，不得假设
  - 策略与校验器都在仓库里，对宿主机有写权限的 agent 可以改掉它们。这是护栏不是边界；
    强制点仍是受保护分支 + CODEOWNERS + CI 复核（可用 `verify-isolation.sh` 打印的
    sha256 manifest 核对）
- [ ] item 8（工具级委派写入）单独闭环：宿主整体沙箱化在 macOS 上**未能验证**
  （勿记为"不可行"）；**不得由 shell 包装器代替**
- [ ] 隔离方案落地后**重跑 item 5 与 item 8 的完整链路**，验证之前观测到的绕过是否
  真的被堵住；只补设计说明不算通过
- [ ] `qa-lead` 委派 `qa-executor` 的一次性审批如何处理需单独设计——委派会触发审批
  是已验证的行为事实，`trustedAgents` 在本 IDE 到底控制什么仍需补充探针，不能假设
  它能消除审批
- [x] `validate_agents.py` 依**实测白名单**校验 — 2026-09-21 完成，47 项测试：
  `todo_list` 为具体工具、`knowledge` 报 `TOOL_NOT_IN_REGISTRY`、`spec`/`context` 报
  `TOOL_UNVERIFIED`；`allowedTools ⊆ tools`；只读角色不含 write/shell 且**不得用 deny**
  表达（item 7 交集传播）；`permissions` 块必带；`effect: ask` 一律报错（headless/子代理下
  等同 deny）；具备 write 的角色必须显式 allow 写路径且 deny 全部受保护前缀；
  委派目标必须存在。每个拒绝码都有对应反例测试，首轮跑真实配置直接 PASS 时补的
  —— 首次就全过是"校验器可能什么都没查"的信号
- [x] 正式角色配置 `.kiro/agents/{qa-lead,qa-design,qa-executor,qa-reviewer}.json` — 2026-09-21：
  `qa-lead` 用 tools 省略表达不写、`rules: []` 不用 deny；`qa-executor` 写路径显式 allow
  且 deny 受保护路径与破坏性 shell 命令；提示词里写明"委派不是安全边界"（item 8）、
  "FLAKY 不是根因"、"不发明阈值"、三态门禁
- [ ] 实际越权尝试确认被拒（反例 1：缺工具调用记录或拒绝结果不算通过）
- [ ] CI 复核（从可信执行的原始产物重新生成门禁输入）**保留作为独立防线**，但不能
  替代本任务的本地执行隔离——CI 复核发生在执行结束之后，防不住执行期间已经发生的破坏

### [~] Task 4 — `qa-design`

评测基础设施已先行就绪（平台无关，不等探针）；agent 配置本身待 Task 3 解阻。

- [x] `evals/fixtures/` 三份 fixture：`boundary-range`（正例，可自动判定）、
      `unverifiable-criteria`（反例，三条 REQ 全不可验证）、`mixed-partial`（混合例，
      专测"过度阻塞"与"过度补全"两种失败方向）
- [x] `evals/rubrics/qa-design.yaml`：11 条 criteria 逐条标注 `judge: structural|human`；
      漏报与误报**分开记账**并标明代价不同
- [x] `evals/run_evals.py`：**不调用模型**，只做结构检查 + 展开人工清单；
      报告把结构结果与人工结果**分列，无任何合成总分字段**
- [x] 测试 `tests/test_run_evals.py` — 28 项，含 S1–S5 正反例、"结构全过时人工仍 PENDING
      不构成评测通过"、"报告不得出现 score/overall/pass_rate 等合成字段"
- [x] `evals/rubrics/**` 与 `evals/fixtures/**` 纳入 CODEOWNERS 与 `validate_spec` 必查项
- [x] `qa-design` agent 配置（已生成并通过 `validate_agents.py` 静态校验；
      **不等于** IDE 已加载或权限已强制，item 8 完整链路仍待实测）
- [ ] 用真实需求跑首轮评测并记录模型与配置版本

**为什么 rubric 和 fixture 要受保护：** 改 rubric 等于改验收口径；改反例 fixture
可以让"会不会知道自己做不了"这条检查静默失效 —— 后者比前者更隐蔽。

### [ ] Task 5 — `qa-executor`

- [ ] 对接真实测试框架（**阻塞于用户提供 v1 场景仓库**）
- [x] `scripts/rebuild_run_json.py` — 2026-09-22 实现（当前测试数以 `make check` 输出为准）：
      JUnit XML → `run.json`，六字段版本绑定缺一即拒且不接受空串；`baseline_version` /
      `baseline_hash` 由 `--baseline-file` 从受保护版本的 `requirements.yaml` 推出，
      **算法复用 `trace_matrix.compute_baseline_hash`**（两套实现必然漂移，正是"有真实
      基线也对不上"的来源）；`node_id` 按 `--node-id-strategy` 映射（`pytest`/`raw`/`map`），
      参数化后缀拆入 `params`；同一 node_id 出现多次时默认**不猜重试顺序**并置
      `interrupted`，只有适配器显式声明 `document-order` 才分配 `attempt`；
      `skipped` 既不算 failed 也不算 passed；解析失败、零 testcase 的空报告、映射不出
      node_id 三种情况都置 `interrupted=true`（下游消费的是这个字段）
- [x] **集成测试 JUnit → rebuild → trace → gate**（`tests/test_integration_junit_to_gate.py`）
      —— 单测只证明转换器自己的输出约定，证明不了与下游对得上。首版就是这样：输出
      `attempt_id` 与嵌套 `version_binding`，而下游读 `node_id` 与顶层字段，整条链路断开
      而单测全绿。现在断言的对象是门禁最终结论：正例 PASS；一份好报告 + 一份损坏/空报告
      必须 INCOMPLETE；映射策略错必须不通过；失败是 FAIL 而 skip 是 INCOMPLETE；
      运行后改基线必须 EVIDENCE_STALE
- [x] 证据来源可信性判据（2026-09-22，第四轮复核 P1）：`qa/evidence-source.yaml` +
      `gate_check.check_evidence_sources`。声明缺失、条目缺失、`candidate_copy`、
      未知 kind、可信来源无 ref 一律 INCOMPLETE。**"缺输入"不得等同"零阻断"** ——
      实测反例：判定树漏掉 `qa/defects` 时，已确认阻断缺陷从 FAIL 变成 PASS/findings=[]
- [x] 重试顺序不猜（第四轮复核 P2）：JUnit 无尝试序号，重复记录默认记
      `DUPLICATE_ATTEMPTS_WITHOUT_ORDER_EVIDENCE` 并置 `interrupted`；
      只有 `--retry-order document-order` 显式声明适配器保证顺序时才分配序号
- [x] 缺证据分支也落盘报告（第四轮复核 P2）：`always()` 只保证步骤被执行，不保证脚本写文件
- [x] 缺陷导出清单 SHA-256 严格校验（第六轮复核 P1）：每条 `records[]` 的摘要必填，
      必须为字符串且是 64 位 hex（可带 `sha256:`）；缺失/null/空串/错误类型/格式均
      `DEFECT_EXPORT_SHA256_INVALID` → INCOMPLETE。精确回归覆盖：把已确认阻断文件改成
      `closed: true` 后，删除/置 null/置空摘要**不能**从 `DEFECT_RECORD_MODIFIED` 变 PASS
- [x] workflow 变量检查改为按字符位置事件流（第六轮复核 P2）：未来赋值不可提前生效，
      `${X:-fallback}` 只保护当前引用、`${X:=fallback}` 才传播赋值；新增真实
      `/bin/bash -c 'set -eu'` 对照。保证范围明确收窄为**线性词法顺序**，不建模分支、
      函数、子 shell、命令替换、`eval/source`，PASS 不冒充真实控制流可运行证明
- [ ] **CI 证据来源升级为可信（当前必然 INCOMPLETE，需要外部输入）**：
  - [ ] 执行层随原始产物上传 collect 清单（`kind: trusted_ci` + ref）
  - [ ] 缺陷记录改为从跟踪系统导出（`kind: tracker` + 查询/导出 ref）—— 需用户指定系统
  - [ ] 当前 workflow 如实声明 `candidate_copy`，因此组装出来的证据一定判 INCOMPLETE；
        这是刻意的，不要为了"让门禁绿"而改成 `trusted_ci`
- [ ] 对接真实测试框架的 collect/执行两步（**仍阻塞于用户提供 v1 场景仓库**）
      （已纳入 CODEOWNERS —— 改它等于改证据来源）

### [~] Task 7 — 评审链路与三层执行落地

- [x] `.github/workflows/merge-gate.yml` 草稿：三层 job（自检 / 执行 / 判定），
      判定层**从原始产物重新生成**门禁输入，规则取 `target_sha` 上已批准版本，
      `policy_version` 用 `qa/plan` 的树哈希（可复核）
- [x] 草稿里把未落地的部分写成 `exit 1` 占位并注明原因 ——
      一个"能跑过但什么都没验证"的门禁比没有门禁更危险
- [ ] 填入真实框架的 collect 与执行命令（阻塞于场景仓库）
- [ ] `review_mode` 取值（阻塞于"CI 能否跑 agent"）
- [ ] `requirements-ci.txt` 哈希锁定（当前已锁直接/传递依赖版本、已删除未锁 fallback；
      **尚未**补 `--require-hashes`）
- [ ] **平台侧三项设置**（只有用户能做，未完成则受保护路径仍是文档约定）：
      CODEOWNERS 占位符换成真实用户/团队、开启受保护分支并把 `gate` job 设为
      required check、开启"新 commit 使已有审批失效"

### [ ] Task 8 — 编排、端到端贯通、效果度量

细节见 `design.md` 与定稿计划；待 Task 3 解阻后展开。

---

## Task 0 探针的外部验收（2026-09-20）

用户在 IDE 中交互执行了 item 2/6/7/8/10/11/12，方法论优于第一轮：每项区分模型自称
与外部核对，item 8 做了直接调用 vs 委派调用的三组对照，并发现第一轮未测到的一处
关键差异。12 项探针**全部完成**，能力矩阵与本文件已按其结论更新。

### 最重要的发现：item 8，比 item 5 更严重的新阻断项

同一份子代理配置直接调用时其 `permissions.deny` 生效，被父代理**委派**调用时同一条
deny **未生效**（对受保护路径的写入成功），且**不涉及 shell**。item 5 证明"shell 能
绕过能力层"，item 8 证明"委派本身就可能不传递子代理自己的权限策略"——这两条独立，
都必须堵。四角色编排里 `qa-lead → qa-executor` 的委派链路，在此之前不能视为安全边界。

**这不能反推"执行环境隔离对 item 8 无效"。** 操作系统级隔离约束发起写入的进程，
不关心进程内部哪个逻辑角色在调用；真正的问题是 `qa-lead` 与被委派的 `qa-executor`
逻辑目前运行在同一宿主进程内，进程级隔离无法只约束其中一个角色。Task 3 需要先定位
实际执行 `fs_write` 的进程边界，再决定是否要拆分成独立执行单元——这是重新划定隔离
粒度，不是宣布隔离本身失效。CI 从可信执行的原始产物重新生成门禁输入仍作为独立防线
保留，但不能替代本地执行阶段的隔离。

### 其他影响正式配置写法的发现

  - 含 CLI-only 字段（`allowedTools`/`toolsSettings`）且无 `permissions` 块的配置
    被 IDE **静默跳过**——已验证范围仅限该组合，不外推为"所有配置都必须带 `permissions` 块"。
  - 委派会触发一次性审批（已验证行为事实）——`qa-lead` 委派 `qa-executor` 需要为自动化
    场景单独设计处理方式；`trustedAgents`/`availableAgents` 在本 IDE 到底控制什么
    仍未验证清楚，不能假设它们能消除审批或控制委派范围。
  - item 12：`todo_list` 是具体工具 ID 非分类标签；`knowledge` 不在标签注册表内
    （接受字符串但静默不匹配任何工具，已验证）；`spec`/`context` 的 `toolCount=1`
    **不足以证明**匹配到了 `disclose_context`（该工具是必带工具），这两个标签的
    实际匹配结果**未能验证**，需补探针。**配置加载成功不等于标签有效**，
    `validate_agents.py` 的白名单必须区分这两件事。

### 证据可信度三原则（第三次遇到模型自述与事实不一致后固化）

1. 探针判据一律以外部可复核证据（文件哈希、拒绝原文、独立工具调用记录）为准。
2. 会话存档的 `status` 字段不可信——已证实与实际落盘结果矛盾（成功写入被记成 failed）。
3. 工具层报 `success:true` 不等于拿到可信 exit 0——退出码可能根本未被采集。

这条已写入 R8，作为对脚本与探针设计的通用约束，不只是本轮的一次性说明。

### 尚未闭环的部分（2026-09-20 复审后更正）

- [x] item 8 的第二轮证据包已在用户指定路径核对（`manifest-sha256.json`、
      `final-canary-verification.json` 与 `report.md` 叙述一致；已读取
      `child-policy-callsite-excerpts.json`、`named-subagent-source.txt` 的代码摘录）。
      **不再是**"尚未纳入本仓库、无法核验"，此前的表述有误。
- [ ] item 8 的定位线索（`policySession` 未传递给 `invokeSubAgent`）是**压缩代码的
      静态摘录**，不构成完整根因证明，本文件不据此下最终结论，只采信其揭示的行为事实
      （三组对照的写入结果差异）。若需要更强保证，应在受控环境下对该代码路径做运行时验证。
- [x] ~~补 item 8 的阳性拦截对照~~ —— **已存在，不需要再补**。三组对照表的 `protected`
      一列，直接调用行"拒绝、文件不存在"与委派调用行"写入成功"，就是完整的阳性拦截对照。
- [ ] Task 3 需先定位实际执行 `fs_write` 的进程边界（`qa-lead`/`qa-executor` 是否共享
      同一宿主进程），再设计隔离方案；隔离落地后重跑 item 5 与 item 8 的完整链路。
- [ ] item 12 的 `spec`/`context` 标签实际匹配结果未能验证，需补充能排除必带工具干扰
      的探针。
- [ ] `trustedAgents`/`availableAgents` 在本 IDE 的实际作用（是否控制委派范围、是否控制
      权限约束）未能验证，只确认了"委派触发审批"这一行为事实。
- [ ] CLI / IDE / CI 三端能力矩阵需分开维护（当前只做了 IDE）。
- [ ] 证据持久化问题仍未解决：会话存档的成功写入被记成 `status: failed`，需要更可靠的
      实时事件捕获；正式可复跑包应保存每阶段不可覆盖的快照，而非滚动覆盖同一份汇总文件。

## 待用户补充的输入

### 只有用户能做的

- [ ] **v1 真实接口场景仓库**（Task 4/5 需要）。先只读摸清框架与原生报告格式，
      再填 `merge-gate.yml` 的 collect/执行两步与 `rebuild_run_json.py`。
- [ ] **阻断缺陷等级定义位置**。v1 不发明数字；`blocking_defect_severities` 留空时
      门禁判 `INCOMPLETE`。
- [ ] **CI 能否跑 agent**。决定 `review_mode` 取 `automated_required` 还是降级 `manual`。
- [ ] **平台侧三项保护设置**（见 Task 7）。未完成则受保护路径只是文档约定，
      **不得表述为"可信计算基已落地"**。
### 不阻塞的部分

Task 0 全部 12 项探针结论、轨 A 全部脚本、Task 4 的评测基础设施、Task 7 的 workflow
草稿都不依赖上述输入，已完成。Task 3 的隔离方案设计（不含实测重跑）也可以先动手。

---

## 外部验收（2026-09-20）与修复

第一轮交付被外部独立验收**判为部分完成**：现有 163 项单测与六场景 demo 都通过，
但独立构造的 14 个反例中 12 个**错误 PASS**、2 个把证据不足误判为 FAIL。
这些缺陷全在已标完成的 Task 2 / 6a / 6b 里，不依赖轨 B。

根因归为 5 类，均已修复并补回归测试：

| 编号 | 缺陷 | 原表现 | 修复 |
|---|---|---|---|
| F1 | 按 REQ 指定的必测范围不参与结果判定 | `required_requirements` 只校验存在性，结果判定只遍历 `required_cases`；`required_cases: []` 时关联测试失败仍 **PASS** | 追溯层算出唯一有效执行范围 `effective_required_cases = required_cases ∪ expand(REQ)`；门禁只认这一份；必测 REQ 无用例覆盖 → `REQUIRED_REQ_WITHOUT_CASE`；展开差异以 `SCOPE_EXPANDED_FROM_REQ` 可见 |
| F2 | 版本核对只看 binding 字段存在性 | 一律用"字段非空且不相等才报错"，**删掉字段反而跳过核对**；binding 与实际基线不一致也 PASS | 必填字段先查存在性（`RUN_CANDIDATE_SHA_ABSENT`、`RUN_BASELINE_HASH_ABSENT`、`RUN_BASELINE_VERSION_ABSENT`、`REVIEW_VERDICT_UNBOUND`、`REVIEW_CONFIG_VERSION_ABSENT`）；新增交叉核对 `BINDING_BASELINE_{HASH,VERSION}_MISMATCH`、`RUN_{TARGET_SHA,POLICY_VERSION}_MISMATCH`、`CASE_/SCOPE_BASELINE_VERSION_MISMATCH` |
| F3 | 只关联 REQ 的已确认阻断缺陷被丢弃 | `load_defects` 返回的 `by_req` 存入变量后**从未使用**；`blocking_defect_severities` 传入却不参与计算 | REQ 级缺陷传播到覆盖该 REQ 的用例（`defects_via_req` 可查来源）；明确 `closed`/`confirmed_blocking`/`severity` 三字段的阻断契约，严重级真正参与判定；`DEFECT_UNLINKED` 校验关联完整性 |
| F4 | manual 模式消费未验证的 verdict | `check_review` 对 manual 提前返回不校验，`evaluate` 却仍读同一文件交给 `check_guard_hints`；候选自提 `produced_by: candidate` + 旧 SHA 的 JSON 可把 INCOMPLETE 变成 **PASS**；确认只按规则码匹配，一个 `SKIP_ADDED` 可关掉多文件同类提示 | `check_review` 返回 `trusted_verdict`，仅在来源与版本均已验证时非 None；manual 模式不消费本地文件并给出 `REVIEW_VERDICT_IGNORED_IN_MANUAL_MODE`；提示获得稳定 `hint_id`（`code+file+evidence` 摘要），处置必须逐项给出 `hint_id`+`reason`+`alternative_coverage`，否则 `GUARD_ACK_MALFORMED` |
| F5 | 缺失或 skipped 被当成质量失败 | 无 attempt 或非 `passed` 一律折成 False，门禁再解释为"明确失败"→ 应 INCOMPLETE 的判成 **FAIL** | 三态聚合：`failed`/`error` → False；无 attempt 或 `skipped` → None；全 passed → True。逐 AT 原始状态保留在 `at_statuses` |

复验：外部反例脚本 15/15 符合 spec（退出码 0），六场景 demo 结论不变。

对 5 处修复另做 19 条变异检验，把每处修复逐一"退回"缺陷形态，**19/19 全部被测试捕获**。
其中 2 条首轮逃逸，暴露了新的测试盲区并已补齐：

  - 处置记录用 `code` 字段冒充逐项标识 —— 当时的反例用裸字符串，被 `isinstance` 检查挡掉，
    没覆盖"字典里写 code"这条路径。
  - `hint_id` 退化成只用规则码 —— 当时每个用例只有一条提示，测不出"按规则码批量关闭"。
    已补同一规则码 × 两个文件的用例。

证据：`docs/probe-evidence/mutation-check-fixes.txt`、`acceptance-repro-after-fix.txt`

### 四份变异脚本已全部复跑：48/48 捕获

- [x] 模式漂移修正（4 处）：3 条 `PATTERN_NOT_FOUND` + 1 条伪装成 `NOT_CAUGHT` 的漂移
  （原模式重构后匹配到 `reason` 文案行而非门禁条件行，变异无语义效果）。
- [x] **复跑中又抓到一处真实测试盲区：** 模式修正后，"允许重试通过抹掉已确认阻断缺陷"
  仍 `NOT_CAUGHT` —— 禁用 `confirmed_blocking` 路径后 66 项测试全过。原因是当时所有
  `confirmed_blocking: true` 的用例 `severity` 都恰好是 `S1`（在阻断清单内），
  `confirmed_blocking` 与 `severity` 两条阻断路径从未被分别测到。已补两个用例
  （确认阻断 + 严重级不在清单内）并复跑捕获。
- [x] 复跑结果：task2 5/5、task6a 10/10、task6b 12/12、fixes 21/21。
  证据均已更新，并在文件头标明对应代码状态。

**这是第二次遇到同一个模式：多个条件被同一组测试数据同时满足，导致单个条件失效测不出来。**
第一次是"序列化产物没被测到"，这次是"两条阻断路径共用 S1 数据"。写反例时要让每个条件
单独可失效，不能依赖一组"什么都满足"的数据。

### 本轮验收明确**未**签署的结论

  - Task 0 本轮只审读已有证据，未重新执行 Kiro 探针。
  - CODEOWNERS 的 owners 仍是占位符，无 remote / CI / 平台保护设置 —— **真实保护未生效**，
    不得称"可信计算基已落地"。
  - `item3-4-evidence.txt` / `item9-evidence.txt` 是文件状态与哈希，原始工具调用事件与拒绝
    回执未独立归档。因此"所有 shell 子进程完全绕过能力层""永久询问机制失效"应收窄为
    **该版本、该客户端配置、该命令下的观察**，不作普遍结论。执行环境隔离仍是必须项。
  - RUNBOOK item 8 的实验设计需调整：移除父代理 `trustedAgents` 首先改变的是**委派**
    是否需审批，而子代理对 `allowed/**` 仍显式 allow。应保持委派可执行，另给子代理某个
    工具操作显式 `ask`，分别记录在哪一层被拒，不能用父层委派审批代替子层工具审批证据。

## 轨 A 验证结果

```
make check
  validate_spec : 结构检查 PASS
  pytest        : 234 项通过
  MAKE_EXIT     : 0

外部独立反例   : 15/15 符合 spec（退出码 0）
变异检验       : 48/48 被捕获（四份脚本均针对修复后代码复跑）
```

说明：`checks run` 的数值随本文件的 checklist 行数变化（每个任务行 1 项检查 + 72 项固定检查），
**不是固定的覆盖指标**，不要用它的绝对值做趋势判断。真正的保障是 `tests/test_validate_spec.py`
的 26 项反例测试。

变异检验累计 **46 条，全部被测试捕获**：

| 目标 | 条数 | 证据 |
|---|---|---|
| `trace_matrix.py` | 5 | `docs/probe-evidence/mutation-check-task2.txt` |
| `gate_check.py` | 10 | `docs/probe-evidence/mutation-check-task6a.txt` |
| `guard_scan.py` + 门禁接线 | 12 | `docs/probe-evidence/mutation-check-task6b.txt` |
| F1–F5 修复（把修复退回缺陷形态） | 19 | `docs/probe-evidence/mutation-check-fixes.txt` |

端到端 demo 六个场景（`docs/probe-evidence/e2e-demo-output.txt`）：

| 场景 | 结果 |
|---|---|
| 一切齐备 | `PASS`，质量失败 0 / 证据缺失 0 |
| 删掉运行记录 | `INCOMPLETE`，质量失败 **0** / 证据缺失 4 |
| 必测用例失败 | `FAIL`，质量失败 **1** / 证据缺失 0 |
| 先失败后通过 + 已确认阻断缺陷 | `FAIL`，`last_result=True` 而 `gate_accepted=False` |
| 带门槛下降的 diff | `INCOMPLETE`，7 条 `GUARD_*` HINT + `GUARD_HINTS_UNREVIEWED` |
| 基线 10 条覆盖 8 条 | `req_total=10`、`req_designed=8`，精确报出 `REQ-9`/`REQ-10` |

第 2、3 两行是三态可区分性的直接证据：同一套代码下，"没测够"与"测出问题"给出不同结论、
不同理由、不同计数。

## 过程中发现并修正的自身缺陷

记在这里而不是抹掉，因为它们说明了哪些检查真的有用。

| # | 缺陷 | 由什么发现 |
|---|---|---|
| 1 | `validate_spec.py` 把基线 `meta.status`（COMPLETE/INCOMPLETE）误判为度量项（MEASURED/MISSING），取值域冲突 | 校验器首次运行自身 |
| 2 | `gate_check.py` 的 33 项测试全部读内存属性，无一条检查**序列化产物**，"报告只列第一条发现"的变异未被捕获 | 变异检验 |
| 3 | `guard_scan.py` 多文件 diff 中删除文件路径错归属（跨文件共享游标回溯） | 端到端 demo |
| 4 | 文件删除时同时报 `TEST_FILE_DELETED` 与 `ASSERTION_REMOVED`，对同一事实重复报警 | 端到端 demo + 回归测试 |
| 5 | F1–F5 五类误放行与三态误分类（见上节） | **外部独立反例** |
| 6 | 处置记录用 `code` 冒充 `hint_id`；`hint_id` 退化后同类提示被批量关闭 | F1–F5 修复的变异检验 |

四种检查各抓到不同类型的问题，没有一种能替代其他：

| 检查 | 抓到的问题类型 |
|---|---|
| 结构校验 | 定义冲突 |
| 变异检验 | 测试盲区（测了对象但没测产物、样本单一测不出批量效应） |
| 端到端 demo | 单元测试掩盖的集成缺陷 |
| **外部独立反例** | **自己没想到的场景** —— 内部测试全绿时仍有 12 处误放行 |

最后一行是本轮最重要的教训：内部测试、变异检验、端到端 demo 三道检查全绿，
外部独立构造的反例仍发现 12 处错误 PASS。自建的检查只能覆盖自己想到的场景，
对"范围定义的两条路径只实现了一条"这类遗漏无能为力。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-20 | Task 0 完成 5 项（item 5 判定不支持，item 7/12 阻塞轨 B）；轨 A 全部完成（Task 1 / 2 / 6a / 6b）；仓库从 `~/qa-agents` 迁至工作区根 `/Users/AI/test-agent`（逐文件 SHA-256 校验 58/58 一致） |
| 2026-09-20 | 外部独立验收判部分完成（14 反例中 12 误放行）；F1–F5 修复并复验（反例 15/15、变异 48/48） |
| 2026-09-20 | RUNBOOK item 8 实验设计修正（分离委派层与工具层审批）；新增 `probe-write-ask` 探针配置与 `preflight.py` 自检；Task 4 评测基础设施与 Task 7 的 CI workflow 草稿完成 |
