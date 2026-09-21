# Task 3 第一步：执行进程边界定位（2026-09-21）

本文件回答 Task 3 的前置问题：**实际执行 agent 文件写入的是哪个进程边界，操作系统级隔离
能挂在哪里。** 结论只依据本文记录的外部可复核证据，不采信任何模型自述。

原始输出归档在 `results/`，探针脚本在本目录，可重跑。

---

## 1. 问题与判据

item 8 的事实是：同一份子代理配置**直接调用**时 `permissions.deny` 生效，被**委派调用**时
同一条 deny 未生效、写入成功。这说明应用层权限判定在委派路径上不可靠，但**不能**由此推出
"执行环境隔离无效"——操作系统隔离约束的是**发起写入的进程**，与该进程内部的权限判定是否
正确无关。

所以隔离方案设计前必须先确定：

- Q1：agent 的 shell 命令跑在哪个进程下（这条已在 item 5 中间接看到，本次补完整进程树）
- Q2：工具级 `fs_write`（不经 shell）是否由一个**per-agent 的独立进程**执行？
- Q3：**委派**给子代理执行写入时，是否创建了新的操作系统进程？

Q2/Q3 决定隔离粒度：若不存在 per-agent 的进程边界，就不存在"只沙箱化 `qa-executor`、
不影响 `qa-lead`"这个选项。

---

## 2. 已确认的事实

### 2.1 shell 命令的进程归属（Q1）

`results/kiro-process-tree.txt`：

- 交互 shell 祖先链 `4041 /bin/zsh → 4037 zsh (kiro-cli-term) → 3612 Kiro Helper → 3590 Kiro`
- 即：agent 执行的 shell 命令是 **Kiro Helper（Electron 工具进程）的后代进程**，
  在宿主机上以用户 `kk` 身份运行，没有任何额外隔离

**设计后果：** shell 起源的写入（item 5 那条路径）发生在**我们能控制的子进程**里，
因此容器/挂载级隔离对这条路径是可用的（podman 只读挂载已在上一轮验证为真实拦截）。

### 2.2 委派写入不创建新进程（Q2/Q3）

方法：在宿主机上按固定间隔快照完整进程表，记录窗口内**新建**的进程（以 `pid + lstart`
配对识别，避免 PID 复用误判），窗口内分别执行 ①主代理直接 `fs_write` ②委派子代理
`fs_write`。采样器自身派生的 `/bin/ps` 子进程被显式排除并计数。

| 轮次 | 采样间隔 | 样本数 | 覆盖 | Kiro 进程树内新建进程 |
|---|---|---|---|---|
| pass 1 | 0.5s | 68（37.5s） | 直接写 + 委派写 | 1 个瞬时子进程 `pid=38202 ppid=3590 comm=(Kiro)`，仅出现在 1 个样本 |
| pass 2 | 0.1s | 148（22.1s） | 委派写全程 | **0 个** |

- pass 1 的 `(Kiro)` 瞬时子进程出现在 11:22:44，距委派开始已 25s，**pass 2 未复现**，
  且两次委派写入都成功；没有证据把它放进写入路径。原始数据：
  `results/delegated-write-pass1-0.5s.txt`、`results/delegated-write-pass2-0.1s.txt`
- 两轮里其余新建进程全部归属 Podman Desktop、CoreSimulator/`launchd_sim` 以及探针自身的
  `sleep`，与 Kiro 无父子关系

### 2.3 测量能力的标定（否定结论的适用范围）

"没看到新进程"只有在知道**多短的进程会被漏掉**时才有意义。

正对照（`results/control-4s-canary.txt`）：0.25s 间隔下，已知 PID 的 4 秒进程
被判定为 `CONTROL_PASS`。

检测下限标定（`results/detection-floor-calibration.txt`，0.1s 间隔）：

| 金丝雀寿命 | 结果 |
|---|---|
| 1000ms | CONTROL_PASS |
| 300ms | CONTROL_PASS |
| 100ms | CONTROL_PASS |
| 20ms | **CONTROL_FAIL（从未出现在任何样本）** |

**因此 2.2 的否定结论应表述为：** 委派写入期间没有出现**存活 ≳100ms** 的新进程；
存活 ≲20ms 的瞬时辅助进程无法用本方法排除。

---

## 3. 一条被证伪的测量方法（保留记录）

初始方案是让 agent 写入一个 FIFO：写入方会阻塞在 `open()` 直到读端出现，期间用
`lsof <fifo>` 读出它的 PID。

该方法**不成立**，由对照组直接判死（`results/fifo-method-invalidated.txt`）：

- 控制写入方 PID 32944 确实在该 FIFO 上阻塞了约 90 秒——证据是读端挂上后
  `content.txt` 收到 `CONTROL-WRITER-PID-32944`
- 同期 **220 次 `lsof` 采样全部为空**

原因：进程阻塞在 `open()` 内部时**尚未建立文件描述符**，而 `lsof` 枚举的是已打开的
描述符。若当初没做对照组，只跑 agent 一侧，"抓不到写入方"会被误读成
"没有进程写入"或"写入走了 temp+rename"——这正是本项目反复出现的那类误判。

脚本保留在 `fifo_writer_probe.py`（含 `control` 模式），作为方法论反面样例，
不作为 Task 3 的证据来源。

---

## 4. 对隔离方案的直接约束

1. **不存在 per-agent 的操作系统进程边界。** 在本 IDE 内，主代理与被委派子代理的工具级
   写入没有各自的进程（≳100ms 精度下），因此**无法**用"只沙箱化 `qa-executor`"的方式
   实现角色间隔离。`design.md` 里"若共享同一进程则拆分为独立执行单元"这条，在 IDE 内
   委派模型下没有可挂载点——拆分必须发生在**进入 IDE 之前**（独立会话/独立运行器），
   而不是在委派链内部。
2. **两条绕过路径要用两种不同手段处理：**
   - item 5（shell 起源写入）：容器/只读挂载可拦截，已验证原语有效，属可落地项
   - item 8（工具级委派写入）：发生在 IDE 宿主进程内，容器化该宿主进程在 macOS 上
     未能验证（`kiro-cli` 是 Mach-O universal 二进制，Linux 容器跑不了；`sandbox-exec`
     已弃用且 `-f` 行为不稳定）。**记"未能验证"，不记"不可行"**
3. **因此 item 8 的可落地缓解是结构性的，不是权限规则性的：** 安全关键的角色分离不能
   依赖"给子代理配 deny"，而要依赖"该角色不通过委派被调用"——直接调用时 deny 已验证生效。
4. **CI 从可信产物重建门禁输入仍是独立防线**，但它在执行之后发生，防不住执行期间的破坏，
   不能替代第 2 条。

---

## 5. 仍未闭环 / 需要输入

- [ ] **写入操作的权威 PID 归属未取得。** 需要内核级追踪，用户侧一条命令即可：
      在 agent 执行一次 `fs_write` 期间运行
      `sudo fs_usage -w -f filesys | grep kiro-proc-canary`
      （`sudo` 在本机需要密码，agent 无法自行执行）。取得后可把第 4.1 条从
      "≳100ms 精度下无 per-agent 进程"升级为"写入由某具体进程执行"。
- [ ] 宿主进程整体沙箱化（macOS）是否可行：未验证，勿当成已排除。
- [ ] 隔离方案落地后重跑 item 5 与 item 8 完整链路——只补设计说明不算通过。

---

## 6. 复现方式

```bash
D=docs/probe-evidence/task3
# 采样器正对照（必须先过，否则否定结论无意义）
zsh $D/run_proc_control.sh /tmp/kiro-proc-control 
# 检测下限标定
zsh $D/run_proc_calibration.sh /tmp/kiro-proc-calib 0.1
# 真实观测：start → (执行 agent 写入) → stop → report
python3 $D/proc_diff_probe.py start --dir /tmp/kiro-proc-x --interval 0.1 --with-args
python3 $D/proc_diff_probe.py mark  --dir /tmp/kiro-proc-x --label t0
#   ... 此处执行 agent 的直接写入 / 委派写入 ...
python3 $D/proc_diff_probe.py stop  --dir /tmp/kiro-proc-x
python3 $D/proc_diff_probe.py report --dir /tmp/kiro-proc-x
```
