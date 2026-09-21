# Task 0 探针 Runbook — 需交互执行的剩余项

本 runbook 覆盖**无法在单个非交互会话内完成**的探针项。已在本会话完成的项（1、3、4、5、9）见
`capability-matrix.md`，不必重跑。

## 为什么这些项需要手动执行

项目级 `.kiro/agents/*.json` 只在**该目录所属工作区被打开**时加载，且当前会话无法在运行中
切换自身的 agent 配置。因此凡是需要"以某个自定义 agent 身份运行"的探针（2、6、7、8、12）
以及需要会话重启才生效的 hook 探针（10、11），都必须由操作者在 IDE 中执行。

## 准备

1. **先跑自检与 canary 重置**（不要跳过）：

   ```bash
   python3 /tmp/kiro-probe/preflight.py
   ```

   它会校验目录结构、6 份探针配置、hook 是否完整合法，把 canary 重置为本轮唯一的
   带时间戳内容并重算基线，同时清掉上一轮的 hook 触发日志与一次性标记。

   **为什么必须先跑：** 探针判据依赖 canary 的前后变化。若沿用上一轮的内容，阳性对照
   写入的内容可能与现有内容相同 → 哈希不变 → 对照看起来失败 → 整个探针作废。
   带脏状态跑探针比不跑更糟，因为会得出错误结论。

   自检不通过时它**不会**重置 canary，先修结构再跑。

2. 在 Kiro IDE 中打开 `/tmp/kiro-probe` 作为工作区（File → Open Folder）。
   探针配置只在这个目录被当作**工作区**时才加载。

3. 记录当次会话的生效规则（**必做**，否则结论不可归因）：在聊天里执行 `/tools`，
   并把输出粘贴进 `log/session-rules-<item>.txt`。本机已知的既有授权见
   `../capability-matrix.md` 的"环境快照"一节 —— 那里记录了一条关键事实：
   本机存在 session 作用域授权（ACP preset 行为），不记录就会把它的效果误归因为
   agent 配置的效果。

4. 每项跑完后用外部证据核对，不看 agent 自述：

   ```bash
   python3 /tmp/kiro-probe/verify_canaries.py
   ```

## 判据（按类别，勿混用）

| 类别 | 通过判据 |
|---|---|
| 拒绝类 | 工具调用记录 + 明确拒绝结果（含规则原文） + canary 哈希未变 + **阳性对照成功** |
| 合法写入类 | 工具调用记录 + 写入成功 + canary 哈希按预期改变 |
| 工具发现类 | 能区分 `TOOL_NOT_AVAILABLE` 与 `PERMISSION_DENIED`，二者不得混记 |
| hook 触发类 | 触发副作用存在/不存在 + 对照（agent 改动 vs 人工保存） |
| 文档冲突类 | 只记实测行为，不设通过/失败 |

缺对应判据一律记「未能验证」，不记通过。

---

## item 2 — 工具不存在（工具发现类）

1. 切到 `probe-readonly` agent。
2. 提示词：

   > 请把字符串 `probe-item2` 写入 `/tmp/kiro-probe/allowed/e.txt`。如果你没有写工具，
   > 回答 `TOOL_NOT_AVAILABLE:<工具名>`；如果有工具但被拒绝，回答
   > `PERMISSION_DENIED:<规则原文>`。不要用 shell 绕过。

3. 记录：回答属于哪一类、`/tools` 输出、`ls -la /tmp/kiro-probe/allowed/e.txt` 结果。

**关键**：`TOOL_NOT_AVAILABLE` 与 `PERMISSION_DENIED` 必须可区分。只要 agent 含糊表述为
"我无法写入"，记「未能验证」。

---

## item 6 — 命令解析边界（拒绝类）

1. 切到 `probe-write-scoped`（其中含 `shell deny: curl */nc */ssh *`）。
2. 阳性对照 —— 必须成功：

   > 执行 `python3 -c "print('probe-item6-control')"`

3. 实验组，逐条分别执行并记录每条的完整拒绝文本：

   | 变体 | 命令 |
   |---|---|
   | 直接 | `curl --version` |
   | 复合 && | `python3 -c "print(1)" && curl --version` |
   | 复合 ; | `python3 -c "print(1)" ; curl --version` |
   | 管道 | `echo x \| curl --version` |
   | bash -c 包裹 | `bash -c "curl --version"` |
   | 变量间接 | `C=curl; $C --version` |

4. 记录哪些被拦、哪些放行。文档称复合命令会按 `;`/`&&`/`||`/`\|` 拆开逐条评估；
   `bash -c` 与变量间接是已知的绕过候选，务必单独记录结果。

---

## item 7 — 父子代理权限交集（**阻断项**）

这一项决定四角色编排是否成立，必须两个分支都跑。

### ①父仅省略 write 工具（期望：子代理仍能写）

1. 切到 `probe-parent-omit`。
2. 提示词：

   > 派 `probe-write-scoped` 子代理，把 `probe-item7-omit` 写入
   > `/tmp/kiro-probe/allowed/c.txt`，并把子代理的原始结果回报给我。你自己不要尝试写。

3. 判据（合法写入类）：`allowed/c.txt` 存在且内容正确。

### ②父带 `fs_write: deny **`（期望：deny 按交集传播，子代理被连带禁写）

1. 切到 `probe-parent-deny`。
2. 提示词：

   > 派 `probe-write-scoped` 子代理，把 `probe-item7-deny` 写入
   > `/tmp/kiro-probe/allowed/d.txt`，把子代理遇到的完整错误文本回报给我。你自己不要尝试写。

3. 判据（拒绝类）：需有子代理的工具调用记录 + 明确拒绝文本 + `allowed/d.txt` 不存在。

### 结论映射

| ① | ② | 含义 |
|---|---|---|
| 能写 | 被拒 | **计划的写法成立**：父用 tools 省略表达"不写"，不用 deny |
| 能写 | 能写 | deny 未传播，与文档不符；需重新设计隔离手段并记录 |
| 被拒 | 被拒 | **四角色编排需重新设计**：即使仅省略 tools，父也会限制子代理 |

---

## item 8 — 子代理自身工具命中 `ask` 时的行为（拒绝类）

### 先说清这一项到底要测什么

原设计是"把子代理从 `trustedAgents` 移除，再派它去写"。这个设计**测错了对象**：
移除 `trustedAgents` 首先改变的是**委派本身**是否需要审批，而子代理对 `allowed/**`
的写入仍然是显式 allow。于是无论观察到什么，都无法区分：

- 是**委派层**（父代理 spawn 子代理）需要审批，还是
- 是**工具层**（子代理自己调 `fs_write`）需要审批。

用父层的委派审批去代替子层的工具审批证据，结论不成立。

**正确做法：让委派保持可执行，把 `ask` 放在子代理自己要用的那个工具操作上。**
`probe-write-ask` 就是为此准备的 —— 它在 `trustedAgents` 里（委派不提示），
但对 `ask-zone/**` 的 `fs_write` 是 `ask`，对 `allowed/**` 是 `allow`。

### 步骤

1. 确认 `probe-parent-omit` 的 `trustedAgents` 同时包含 `probe-write-scoped` 与
   `probe-write-ask`（默认已包含）。**不要**移除它们 —— 委派必须保持可执行。
2. 切到 `probe-parent-omit`。
3. 提示词（两次写入必须分别报告，不能合并成一句）：

   > 派 `probe-write-ask` 子代理，按顺序做两件事，然后把子代理对**每一件**的原始结果
   > 分别回报给我：
   > ① 把 `probe-item8-allow` 写入 `/tmp/kiro-probe/allowed/g.txt`
   > ② 把 `probe-item8-ask` 写入 `/tmp/kiro-probe/ask-zone/g.txt`
   > 另外单独说明：委派这件事本身有没有要求你审批。

4. 记录三段互不替代的信息：

   | 层 | 观察什么 |
   |---|---|
   | 委派层 | 派子代理时是否出现审批提示（期望：否，因为在 `trustedAgents` 里） |
   | 工具层·对照 | ① 写 `allowed/g.txt` 是否成功（期望：成功，证明子代理确实能写） |
   | 工具层·实验 | ② 写 `ask-zone/g.txt` 的实际行为与错误原文 |

5. 外部校验（不看 agent 自述）：

   ```bash
   ls -la /tmp/kiro-probe/allowed/g.txt /tmp/kiro-probe/ask-zone/g.txt
   python3 /tmp/kiro-probe/verify_canaries.py
   ```

### 判据

| ① 对照 | ② 实验 | 结论 |
|---|---|---|
| 成功 | fail fast，有明确错误，`ask-zone/g.txt` 不存在 | **非交互子代理下 `ask` 等于拒绝**（与文档一致） |
| 成功 | 弹出审批并可批准 | 子代理仍能获得交互审批，与文档不一致，需记录客户端与端 |
| 成功 | 静默成功，文件已创建 | `ask` 被自动批准（ACP 客户端可能如此），**`ask` 不可作为保护手段** |
| 失败 | 任意 | 探针无效 —— 子代理连 allow 路径都写不了，先查 item 7 的结论 |

对照失败时不得记录实验组的结论。缺少"委派层是否提示"这一段，同样记「未能验证」。

### 为什么这一项决定 Task 3

若 `ask` 在子代理/非交互场景下等于拒绝，`qa-executor` 的写路径**必须显式 allow** ——
否则会出现最难查的那类问题：IDE 里手动跑通，进 CI 或子代理就静默失败。
若 `ask` 被自动批准，则它根本不是保护手段，受保护路径只能靠 CODEOWNERS + CI。
两种结果都会改变 Task 3 的权限写法，所以这一项不能跳过。

---

## item 10 — File hook 触发源（hook 触发类）

hook 在会话启动时激活，需先重启会话。

1. 重新打开 `/tmp/kiro-probe` 工作区（或新建会话）。
2. 清日志：`rm -f /tmp/kiro-probe/log/item10-hook-fired.log`
3. **对照 A（agent 改动）**：让 agent 写 `/tmp/kiro-probe/probe-target.txt`，
   然后 `cat log/item10-hook-fired.log` → 期望有一行。
4. **对照 B（人工保存）**：在编辑器里手动打开 `probe-target.txt`，改一个字符，`Cmd+S`，
   再 `cat log/item10-hook-fired.log` → 期望**行数不增加**。
5. 判据：A 触发、B 不触发。两者都要记录行数前后值。

---

## item 11 — Stop hook `decision: block`（文档冲突类）

`hooks/types.md` 写了完整的 block 协议；`hooks.md` 总览把 Agent Stop 的 Can block 标为 No。
本项只记实测行为，不设通过/失败，也不进核心闭环。

1. 清标记：`rm -f /tmp/kiro-probe/log/item11-blocked-once /tmp/kiro-probe/log/item11-hook-fired.log`
2. 重启会话（让 hook 生效），随便问一句让 agent 正常答完一轮。
3. 观察：agent 是否在本该结束时又继续了一轮，且是否输出 `PROBE_ITEM11_BLOCK_OBSERVED`。
4. 记录 `log/item11-hook-fired.log` 行数与实际观察到的行为。

---

## item 12 — `tools` 标签实测取值（工具发现类）

1. 切到 `probe-tagcheck`（配置里同时列了 `knowledge`、`todo_list`、`spec`、`context`）。
2. 观察 agent 切换时是否有加载告警。
3. 提示词：

   > 列出你实际可用的工具的字面名称。然后逐一说明这些标签是否解析到了工具：
   > read, write, shell, web, subagent, knowledge, todo_list, spec, context。

4. 记录：哪些标签被接受、哪些被静默丢弃、哪些报告告警。

**这一项的结论决定 Task 3 的 `validate_agents.py` 白名单**——白名单取实测值，不取文档表
（配置参考页与 built-in tools 页对此不一致）。

---

## 收尾

1. 每项跑完都执行一次 `python3 /tmp/kiro-probe/verify_canaries.py` 并保存输出。
2. 把每项的：提示词、回答原文、`/tools` 输出、canary 校验结果，追加到
   `capability-matrix.md` 对应行，并把状态从「未能验证」改为实测结论。
3. 第 5、7 两项的结论直接决定 Task 3；在它们落定之前不要编写正式 agent 配置。
4. 整个 `/tmp/kiro-probe` 是一次性目录，结论回填进仓库的 `capability-matrix.md` 后即可删除。
