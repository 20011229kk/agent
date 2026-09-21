# 证据包说明

每份结果对应的**代码状态**必须写清楚，否则旧结果会被误读成对当前代码的验证。

## Task 0 探针证据

| 文件 | 内容 | 代码状态 |
|---|---|---|
| `log/baseline-hashes.{json,txt}` | canary 基线哈希 | 探针执行时 |
| `log/exitcode-check.txt` | `execute_bash` 外层退出码不可信的验证 | 同上 |
| `log/item3-4-evidence.txt` | item 3/4 的文件状态与哈希 | 同上 |
| `log/item5-result.json` | **item 5 子进程越权的对照与实验结果** | 同上 |
| `log/item9-evidence.txt` | item 9 的写入结果与哈希 | 同上 |
| `probe_item5_subprocess.py` | item 5 探针脚本 | — |
| `verify_canaries.py` | canary 校验器 | — |
| `probe-configs/` | 一次性探针 agent 与 hook 配置 | — |
| `RUNBOOK.md` | 剩余 7 项交互探针的执行步骤 | — |

### 证据强度的诚实边界

`item3-4-evidence.txt` 与 `item9-evidence.txt` 记录的是**文件状态与哈希**；
工具调用的原始事件与拒绝回执**未独立归档**，拒绝文本目前只在
`../capability-matrix.md` 中转述。因此：

- 不得据此签署"所有 shell 子进程完全绕过能力层"或"永久询问机制失效"的**普遍结论**。
- 应表述为**该 IDE 版本、该客户端配置、该命令场景下的观察**。
- 这不影响"执行环境隔离仍是必须项"这一设计决定 —— 该决定成立所需的证据已经足够。

## Task 3 执行进程边界证据（2026-09-21）

结论与完整方法记录在 `task3/task3-findings.md`，原始输出在 `task3/results/`。

| 文件 | 内容 | 代码状态 |
|---|---|---|
| `task3/proc_diff_probe.py` | 进程表差分采样器（`start`/`mark`/`stop`/`report`） | — |
| `task3/run_proc_control.sh` | **正对照**：已知 PID 的 4s 进程必须被判定为 created | — |
| `task3/run_proc_calibration.sh` | 检测下限标定：1000/300/100/20ms 金丝雀 | — |
| `task3/fifo_writer_probe.py` | **被证伪的方法**，仅作方法论反面样例保留 | — |
| `task3/results/control-4s-canary.txt` | 正对照 `CONTROL_PASS` | 探针执行时 |
| `task3/results/detection-floor-calibration.txt` | 100ms 可检出 / 20ms 漏检 | 同上 |
| `task3/results/delegated-write-pass1-0.5s.txt` | 直接写 + 委派写，0.5s 采样 | 同上 |
| `task3/results/delegated-write-pass2-0.1s.txt` | 委派写全程，0.1s 采样，Kiro 树内 0 新进程 | 同上 |
| `task3/results/fifo-method-invalidated.txt` | 对照组判死 FIFO+`lsof` 方法的证据 | 同上 |
| `task3/results/kiro-process-tree.txt` | Kiro 进程树与当前 shell 祖先链 | 同上 |
| `task3/results/delegated-write-pass4-fixed-tick.txt` | **pass 4：修正采样器后的委派写入观测**（实测间隔 max 0.105s） | 采样器修正后 |
| `task3/results/detection-floor-calibration-repeat5.txt` | 检测下限，每寿命重复 5 次（50ms 3/5、20ms 1/5） | 同上 |
| `task3/results/reduced-pass4-fixed-tick/` | pass 4 原始采样的**可复核降采样归档** | 同上 |
| `task3/results/reduced-pass3-flawed-intervals/` | pass 3（修法有缺陷那轮）的归档，作为"必须公布 max 间隙"的实例 | 采样器第一版修法 |
| `task3/results/reduced-calibration-repeat5/` | 标定轮的降采样归档 | 采样器修正后 |
| `task3/results/isolation-image-build.txt` | `qa-executor:0.1.0` 镜像构建记录 | 首版（未锁 digest） |
| `task3/results/isolation-verify-arm64.txt` | 首版隔离验证 5/5（**该版本包装器存在扩权漏洞**，见下） | 修复前 |
| `task3/results/isolation-verify-c0-c9.txt` | **当前版本：C0–C9 全 PASS**，含镜像身份核对与两条绕过回归 | 策略化之后 |
| `task3/results/isolation-pytest-in-container.txt` | 容器内跑完仓库全量测试 292 项通过 | 策略化之后 |

### 归档策略：为什么不是原样入库

一次 866 次快照（带完整命令行）的 `samples.jsonl` 实测 **141MB**，gzip 后仍有 17.8MB。
上一轮的做法是只归档 report 文本、原始数据留在 `/tmp` 后被清理，结果外部复核无法核对
任何一次间隙——**结论变成不可复核**。

现在用 `proc_diff_probe.py reduce`：只减体积，不减可核对性。

| 保留 | 用途 |
|---|---|
| 每次快照的 `t` / `wall` | 逐条复核间隙分布与 max 间隙 |
| Kiro 进程子树（`ppid`/`lstart`/`comm_id`） | 复核"Kiro 树中有没有新建进程"这条结论 |
| 每次快照完整进程表的 `procs_sha256` | 证明降采样没有挑样本 |
| `comm-table.json` | `comm_id` → 完整命令行原文，未截断 |
| `reduced-manifest.json` 里完整文件的 sha256 与字节数 | 需要时与本地留存件比对 |

效果：141MB → 2.9MB（pass 3）、38MB → 0.8MB（pass 4）。

### 外部复核（2026-09-21）判"包装器作为权限边界不通过"，已修

真实容器里实测出两条绕过，当时都**写入成功、退出 0、宿主哈希变化**：

| 调用 | 后果 |
|---|---|
| `--rw qa/baseline` | 受保护的覆盖率分母被重新挂成可写 |
| `--rw ../outside` | 穿越到仓库外的同级目录写入 |

根因是包装器接受调用方给的任意 `--rw`，只检查目录存在。现已改为策略决定可写范围
（`isolation/mount-policy.yaml` + `mount_policy.py`），两条绕过固化为 `verify-isolation.sh`
的 C6/C7 与 `tests/test_mount_policy.py` 的回归场景。同轮还修了 C4 网络假通过
（`ConnectionRefused`/`Timeout` 曾命中 PASS）与默认镜像未接上（默认镜像里无 pytest）。

### 这里有一条值得记住的方法论

第一版方法是"让 agent 写 FIFO，趁写入方阻塞时用 `lsof` 读出它的 PID"。控制写入方
（PID 32944）确实阻塞了约 90 秒，但 **220 次 `lsof` 采样全空**——进程阻塞在 `open()`
内部时还没有文件描述符，`lsof` 原理上看不见。如果没先跑对照组，"抓不到写入方"会被
读成"没有进程写入"。**否定结论必须先证明测量手段能看见阳性样本**，这也是后面要做
100ms/20ms 检测下限标定的原因。

## 变异检验证据

四份脚本均已针对 F1–F5 修复后的代码复跑，**48 条全部被捕获**：

| 结果文件 | 条数 | 对应代码状态 |
|---|---|---|
| `mutation-check-task2.txt` | 5 | F1–F5 修复之后（已复跑） |
| `mutation-check-task6a.txt` | 10 | F1–F5 修复之后（已复跑） |
| `mutation-check-task6b.txt` | 12 | F1–F5 修复之后（已复跑） |
| `mutation-check-fixes.txt` | 21 | F1–F5 修复之后 |

### 复跑中暴露的两类问题

**一、模式漂移（4 处，已修正）。** F1–F5 重构改动了脚本依赖的代码行：

- 3 条变成 `PATTERN_NOT_FOUND` —— 模式过期，容易识别。
- 1 条变成 `NOT_CAUGHT` —— 原模式在重构后匹配到了 `reason` 文案行而非门禁条件行，
  变异因此没有语义效果。**模式漂移伪装成覆盖丢失，比 `PATTERN_NOT_FOUND` 更危险**，
  因为它看起来像真的回归，容易被当成噪音忽略。

**二、一处真实测试盲区（已补）。** 模式修正后，`mutation_check_gate.py` 的
"反例2：允许重试通过抹掉已确认阻断缺陷"仍报 `NOT_CAUGHT`：禁用 `confirmed_blocking`
路径后 66 项测试全过。原因是当时所有 `confirmed_blocking: true` 的用例，`severity`
都恰好是 `S1`（在阻断清单内），两条阻断路径从未被分别测到。补了两个用例
（`confirmed_blocking: true` + `severity` 不在清单内）后捕获。

这是本项目第四次由变异检验发现真实盲区，也是第二次发现"多个条件被同一组数据同时满足，
导致单个条件失效测不出来"这一模式。

### 复跑方式（务必**串行**，这些脚本会就地改写源文件再恢复，并发执行会互相干扰）

```bash
cd /Users/AI/test-agent
python3 docs/probe-evidence/mutation_check.py
python3 docs/probe-evidence/mutation_check_gate.py
python3 docs/probe-evidence/mutation_check_guard.py
python3 docs/probe-evidence/mutation_check_fixes.py
```

每个脚本结束时都会打印"恢复校验"，确认源文件哈希已还原。若中途被杀，
用下面这条确认没有残留：

```bash
grep -n "if False\|for h in \[\]\|for rid in \[\]" scripts/*.py   # 应无输出
```

## 端到端与验收证据

| 文件 | 内容 | 代码状态 |
|---|---|---|
| `e2e_demo.py` / `e2e-demo-output.txt` | 六场景集成 demo | 结论在修复前后一致 |
| `demo-candidate.diff` | 含 7 个文件的门槛下降样例变更 | — |
| `demo-guard-scan-output.txt` | 对该 diff 的提示输出 | 修复前 |
| `acceptance-repro-after-fix.txt` | **外部独立反例 15/15 符合 spec** | F1–F5 修复之后 |

外部反例脚本本身不在本仓库（由验收方维护），复跑方式见
`../../.doc/specs/qa-agents/tasks.md` 的外部验收章节。
