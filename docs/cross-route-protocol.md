# 跨路线协作协议

业务目标、主责、预算和裁决的权威规则由治理源 `governance/group-collaboration.md` 维护。本文件说明单一适配器怎样传递和恢复这些记录。适配器不决定授权、不接管业务源码、不启动远端作业。

## 参与者与通道

私有 Agent Center 绑定使用 `participants.<准确 id>`，例如 `participants."decision:A"`。每项声明 `role` 与默认 `transport`；GitHub 通道还需要准确路线入口 `anchor`。本地通道保留已有线程、端点、目录和 Skill 绑定，也可声明 `anchor`。

`role` 选择该组的固定 GitHub 身份；`id` 表示实际来源和接收者；`anchor` 指向项目已有合同。它们不由账号作者名、机器名或 Issue 排序互相推导。发送与接收环境对同一参与者使用相同 id 和入口，各自保存可用的通道绑定。

旧 `targets.decision|research|development` 继续作为同名参与者。职责选择器仅在该职责有一个实例时可用；多实例必须给准确 id。`--transport github` 可覆盖默认本地通道，读取或发布持久记录；覆盖不修改绑定。旧本地命令不要求为发送方另配接收线程。

## 来源评论与回复

GitHub `send` 在准确来源 Issue 或 PR 的普通评论中发布正文，并引用目标 `anchor`，使路线入口产生原生交叉引用。它不会另建收件箱、更新父合同或自动关闭任务。来源 Issue 的唯一正文维护者按真实结果更新当前进度，其他参与者使用评论。

评论第一行是以下格式；正文中的引用或转发不作为新消息解析：

```text
<!-- codex-collaboration/v1 {"kind":"request","from":"decision:A","to":"decision:B","issuer":"decision:A","reply_to":null} -->
```

| 类型 | 用途 |
| --- | --- |
| `request` | 请求另一决策组处理公共缺口、判断影响或整理公共记录 |
| `notice` | 通知成果、公共变化或已确认缺陷及其适用边界 |
| `ack` | 接收方给出接收、暂缓、拒绝或待澄清的具体决定 |
| `task` | 原三条角色路径中的准确研究／工程签发 |
| `query`、`correction` | 询问事实或在有效授权内纠偏 |
| `result` | 回传结果给准确实际签发参与者 |
| `pr` | 实际签发参与者把已创建的 PR 链接交开发组 |

`issuer` 保留当前请求或具体任务的实际签发来源；跨路线请求与后继工程任务分别保留自己的来源。`task`、`result`、`pr`、`ack` 的 CLI 必须明确 `--issuer`。`ack` 还必须提供 `--reply-to`，沿用原消息的 issuer；其他回复也应给出原评论链接。

回复引用的原评论若有协议标记，适配器核对逆向参与者及 issuer。旧自然语言评论可以引用，原合同仍须人工回读；不要求批量转换旧 Issues。元数据只是路由信息，评论作者、标记可解析或链接匹配均不单独证明授权或接单。

交接正文应能从已有记录恢复业务依据、准确工作、范围与验收、预计成本、同一阶段剩余预算、数据用途、失败历史、停止条件及固定返回入口。结果引用准确版本和证据。接收决定写清是否接收及下一步，不能只回复一个含糊的“收到”。

## 读取、发布与恢复

`inspect` 完整读取配置中的路线入口及显式 `--issue` 的正文、评论和时间线。它分页查询相关仓库的 Issues／PR 和普通评论，以发现准确来源任务上的消息。`--module` 使用现有标签，多次指定取并集；未指定时不按标签缩小仓库发现范围。

`--since` 只用于仓库 Issues 和评论的增量发现。时间线没有该参数；准确入口和显式任务的历史仍完整读取。增量输出明确标为 `coverage=incremental`，不能声称恢复了全部待处理工作。新上下文先完整恢复；同一轮次可在内存保留已读证据和增量时间，最终回复后不保存游标。时间线 `references` 和原正文中的依赖仍须按实际需要定向回读，不能把发现列表当作穷尽的依赖图。

输出保留原生链接、作者、正文和时间。`incoming` 仅按目标 id 定向；`requests_without_reply_in_read_evidence` 只表示已读证据中未找到该接收方的链接回复。它不证明对方空闲，也不证明已有回复接受了工作。格式错误的标记单独列出，不猜其含义。

发布前读取准确来源任务和全部评论。完全相同的消息已存在时返回原链接；否则只发布一次。超时、失败或响应不明不自动重试。恢复时先回读；确认原评论存在后复用链接。GitHub 评论没有原子去重保证，并发发布可能留下重复评论；接收方仍沿原任务和实际现场核实，不因此启动重复工作。

失败异常保留本次方法、端点、退出码及已经取得的 stdout／stderr；超时和格式错误同样保留已有诊断。CLI 将必要诊断输出到当前私有会话，不保存日志文件；公开汇报只引用适用且已脱敏的错误与证据，不能把认证或私有交接内容复制出去。

`publication=published|existing` 仅说明评论事实。`execution_state=unconfirmed` 明确远端执行状态尚未确认。接收方须在自己的环境检查原写入者、活动作业、未提交现场、队列与剩余预算，并在来源任务回传决定。不能从沉默、线程中断或评论发布推断作业已停。

所有 REST 命令都经治理源 `scripts/group_github.py`：按实际发送／读取组清除继承令牌覆盖、选择私有身份目录并核验登录。GitHub 通道不需要对方的 socket、线程或 Skill。可访问的本地 Agent Center 只补充即时交接；相关决定、进度和证据仍归准确 GitHub 来源记录。

## 使用示例与监督

先准备仓库外绑定及临时正文文件，示例中的占位值在启用阶段替换：

```text
python3 <决策源>/scripts/agent_center.py inspect --target decision:A --transport github
python3 <决策源>/scripts/agent_center.py inspect --target decision:A --transport github --since <当前轮次已读时间> --module <现有模块标签>
python3 <决策源>/scripts/agent_center.py send --sender decision:A --target decision:B --kind request --issue <准确来源IssueURL> --message-file <请求正文文件>
python3 <决策源>/scripts/agent_center.py send --sender decision:B --target decision:A --transport github --kind ack --issuer decision:A --issue <原来源IssueURL> --reply-to <原请求评论URL> --message-file <接收决定文件>
python3 <决策源>/scripts/agent_center.py send --sender decision:B --target decision:A --transport github --kind result --issuer decision:A --issue <原来源IssueURL> --reply-to <原请求评论URL> --message-file <结果正文文件>
```

决策组在启动、签发前、当前轮次每 900 秒以及收到困难或结果时检查相关记录。对方未运行时等待其下次启动；不安装后台唤醒或服务。暂停、恢复、重新签发和 fork／flush 沿用共同规则，远端暂停须由实际环境确认。

分页复用 `gh api --paginate --slurp`；接口依据见 [GitHub CLI](https://cli.github.com/manual/gh_api)、[评论 API](https://docs.github.com/en/rest/issues/comments)、[时间线 API](https://docs.github.com/en/rest/issues/timeline)和 [Issues API](https://docs.github.com/en/rest/issues/issues)。API 返回同时包含 Issues 和 PR，保留原对象类型，不把公共合并推断为消费者已采用。
