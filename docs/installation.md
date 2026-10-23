# 本地安装与启用

保留完整的决策源、治理源和研究源。Skill 用相对链接读取所属程序，治理源维护唯一共同规则和 `scripts/group_github.py`，决策源维护单一协作适配器。Python 脚本需要 Python 3.11+，GitHub 通道使用支持 `gh api --paginate --slurp` 的 GitHub CLI；本地即时通道另需 `websocket-client`，当前验证版本为 1.8.0。仅使用 GitHub 通道时不加载该依赖。

## 先完成本地核对

决策源的 `skills/codex-decision-assistant` 可链接到 `~/.agents/skills/codex-decision-assistant`。先核对已有入口和真实来源，只在入口不存在时建立链接；保留整个源目录。`agents/openai.yaml` 必须保持 `allow_implicit_invocation: false`。注册或修改 Skill 不启动监督，也不授权外部操作。

从 `templates/local.example.toml` 准备仓库外决策绑定；优先使用非空 `CODEX_DECISION_ASSISTANT_CONFIG`，否则 `${XDG_CONFIG_HOME:-$HOME/.config}/codex-decision-assistant/local.toml`。填写 `paths.source_root` 与 `github.decision` 的真实登录名和认证目录，不提交填写后的文件。

开发组使用治理绑定中的 `github.development`，研究组使用研究绑定中的 `github.research`。各组登录名和目录由用户固定指定，公开模板只保留占位符；旧控制／实现账号表不回退。共同工程原则仍只从治理绑定的 `paths.source_root` 定位。升级保留既有证据归档、知识库及个性化模型与权限字段，不覆盖认证文件或切换全局活动账号。

每次平台操作使用治理源脚本。下列 `<治理源>` 由私有绑定取得：

```text
python3 <治理源>/scripts/group_github.py --group decision check
python3 <治理源>/scripts/group_github.py --group research gh -- issue view <准确 Issue URL>
python3 <治理源>/scripts/group_github.py --group development gh -- pr view <准确 PR URL>
```

脚本每次排除 `GH_TOKEN`、`GITHUB_TOKEN` 等覆盖，设置绑定目录并核验实际登录名。Git 远端操作由开发组使用同一脚本的 HTTPS 路径，凭据助手仅对当前命令生效。研究与决策不推送业务分支；签发组只创建已交付分支的 PR。具体方法见治理源的共同规则与编排程序。

运行 `python3 -m unittest discover -s tests -v`，核对跨仓库 TOML、Skill 元数据、链接与独立行为结果。已有研究或开发角色副本需按各自安装说明更新指令和注册描述，保留原模型、推理、权限和路径。新会话核对实际加载，磁盘更新不能证明旧会话已生效。

## 启用阶段再填写真实协作

协作邀请、实际项目权限、参与者绑定及真实联调属于启用阶段。先从项目声明恢复各路线入口、职责、公共模块责任和公共记录维护者，再给各参与者明确 id。接收组的仓库规则须接受有效委托；身份核验通过不单独授权接单。

把 `templates/agent-center.example.toml` 复制到仓库外，优先用非空 `CODEX_AGENT_CENTER_CONFIG`，否则 `${XDG_CONFIG_HOME:-$HOME/.config}/codex-agent-center/local.toml`。填写 `paths.source_root` 和 `participants.<id>` 的 `role`、`transport`。GitHub 目标填写准确 Issue URL `anchor`，不需要对方线程或 socket；本地目标另填 `agent_center.cli` 及原有 `endpoint`、`thread_id`、`cwd`、`skill_path`。同一参与者在各环境保持相同 id 和入口。纯 GitHub 配置可省略整个 `agent_center` 段。

本地端点是已有 App Server 的 Unix 控制 socket 的绝对路径；分别确认准确线程、目录及已发现的 Skill 路径，不按标题、序号或最近活动猜测。适配器在该 socket 上完成 HTTP Upgrade，再以 WebSocket 文本帧收发 JSON-RPC，不启动服务或代理进程，不跟随重定向。`agent_center.cli` 保留现有绑定兼容，传输不再调用该路径。握手使用的 `ws://localhost/` 仅生成协议头，传输始终使用已连接的 Unix socket，不建立 TCP 连接。旧 `targets.<group>` 绑定继续可用；同类参与者不唯一时明确 id。绑定不存任务、预算、接收或批准状态。

先只读核对，再按准确 Issue 交接。`inspect` 输出可能含私有路径和执行内容，留在私有会话；公开记录使用脱敏证据。

```text
python3 <决策源>/scripts/agent_center.py inspect --target decision:A --transport github
python3 <决策源>/scripts/agent_center.py send --sender decision:A --target decision:B --kind request --issue <准确IssueURL> --message-file <临时请求正文文件>
python3 <决策源>/scripts/agent_center.py send --sender research:A --target development:A --kind task --issuer research:A --issue <准确IssueURL> --message-file <临时交接正文文件>
python3 <决策源>/scripts/agent_center.py send --sender development:A --target research:A --kind result --issuer research:A --issue <原IssueURL> --message-file <临时结果正文文件>
```

正文包含业务依据、准确合同引用、原授权、范围、验收、预计成本、同一阶段预算归属及剩余额度、失败历史、停止条件和固定返回入口。临时正文只是消息输入，使用后按原产物责任处理，不作为本地合同或任务账本。GitHub 来源评论、接收决定、原生回复链接及读取覆盖见[跨路线协议](cross-route-protocol.md)。原请求与后继工程分别保留实际签发方，公共合并与消费者采用分别判断。

本地参与者也可通过 `--transport github` 先维护准确来源记录，再用可访问的本地通道即时通知；通道覆盖不改变 id。离线组在下次启动读取消息，发布成功不表示接单或停止远端作业。接收方在自己的环境核对现场与预算；不因看不到对方作业而另派执行者。

本地空闲线程由 `turn/start` 携带目标实际发现的结构化 Skill 输入接单；活跃线程通过 `turn/steer` 的 `expectedTurnId` 接收询问、纠偏、PR 链接与结果。分页读取 `thread/list` 和 `thread/turns/list` 的完整条目。服务拒绝、超时或连接中断时不自动重试；先回读真实状态，防止重复操作。适配器的内存快照只供本次路由，不能代替核对外部作业、队列、原写入者及预算。

PR 由实际签发组查询同头对象后创建，创建结果不明时先回读。多行 PR 正文用 `--body-file`，保留真实换行。开发组负责之后的维护、独立准确版本审查、显式 `commit_id` 的正式 Review、实际仓库规则核对、普通合并和原有收尾。若仓库要求其他审批，报告具体缺口，不换身份或管理员绕过。

## 协议依据与限制

本地 CLI `0.163.0` 的 `app-server generate-ts --experimental` 提供了本次采用的字段；只生成临时类型，没有连接真实线程或调用模型。协议语义与[官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)交叉核对：Unix socket 使用 HTTP Upgrade 后的 WebSocket，每个 JSON-RPC 消息占一个文本帧；JSONL 适用于 App Server 的 stdio 传输。`app-server proxy` 转发字节，不能替代 WebSocket 握手；此前向 proxy 写裸 JSONL 的实现会在握手层失败，不能以延长 `initialize` 等待时间处理。`turn/steer` 要求准确活跃轮次，Skill 用结构化输入显式加载。

令牌覆盖的优先级见 [GitHub CLI 环境文档](https://cli.github.com/manual/gh_help_environment)；PR 作者不能自批的限制见 [GitHub Review 文档](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/reviewing-proposed-changes-in-a-pull-request)。这些依据不替代启用时的实际权限、仓库规则及版本兼容核对。

监督由决策组当前执行轮次内的工具循环承担，每 900 秒检查，有界等待不超过 60 秒。最终回复后结束；不安装 cron、系统服务或后台轮询进程。未经用户批准不执行 fork／flush，获批的新上下文仍保留原合同、来源、失败、预算与停止条件。

业务目标及必要取舍回读后仍不清晰时，要求用户补充；依赖答案的任务和资源消费停在安全点。明确选用 btc-updown-marketmaker 时，可使用[该项目的路线入口参考](projects/btc-updown-marketmaker.md)，再回读当前合同；这份参考不自动绑定线程或启动项目。
