#!/usr/bin/env python3
"""通过 GitHub 或已有 Codex Unix WebSocket 查询与传递消息；不保存任务状态。"""

import argparse
from contextlib import AbstractContextManager
import json
import os
from pathlib import Path
import socket
import sys
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from participants import (KINDS, TASK_ROUTES, ParticipantError, as_participant,
                          resolve_participant, validate_route)


SKILLS = {
    "decision": "codex-decision-assistant",
    "research": "codex-strategy-research",
    "development": "codex-stage-orchestrator",
}
class CenterError(ParticipantError):
    pass


def read_binding():
    override = os.environ.get("CODEX_AGENT_CENTER_CONFIG", "").strip()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = Path(override) if override else base / "codex-agent-center" / "local.toml"
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CenterError("Agent Center 私有绑定不可读；请在启用阶段填写") from exc


class JsonRpcProxy(AbstractContextManager):
    """连接已有 Unix WebSocket；保留 cli 参数兼容绑定，不启动进程。"""

    def __init__(self, cli, endpoint, timeout=30):
        try:
            import websocket
        except ImportError as exc:
            raise CenterError("Agent Center 本地通道需要 websocket-client；未连接服务") from exc
        self.websocket = websocket
        self.sequence = 0
        self.timeout = timeout
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            stream.settimeout(timeout)
            stream.connect(endpoint)
            # URL 仅生成握手头；已连接的 Unix socket 保持唯一传输，不跟随重定向。
            self.connection = websocket.create_connection(
                "ws://localhost/", socket=stream, suppress_origin=True,
                timeout=timeout, redirect_limit=0,
            )
            if self.connection.getstatus() != 101:
                raise CenterError("Unix WebSocket 未完成协议升级；未发送 RPC，不自动重试")
        except (OSError, websocket.WebSocketException, CenterError) as exc:
            stream.close()
            raise CenterError(f"Unix WebSocket 连接失败：{exc}；未发送 RPC，不自动重试") from exc

    def __enter__(self):
        try:
            self.call("initialize", {
                "clientInfo": {"name": "codex_group_adapter", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.notify("initialized", {})
            return self
        except Exception:
            self.close()
            raise

    def _send(self, message):
        try:
            self.connection.settimeout(self.timeout)
            self.connection.send(json.dumps(message, ensure_ascii=False))
        except (OSError, self.websocket.WebSocketException) as exc:
            raise CenterError(f"{message['method']} 传输异常：{exc}；结果未确认，先回读，禁止盲目重发") from exc

    def notify(self, method, params):
        self._send({"method": method, "params": params})

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        try:
            self._send({"id": request_id, "method": method, "params": params})
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CenterError(f"{method} 响应超时；结果未确认，先回读，禁止盲目重发")
                self.connection.settimeout(remaining)
                opcode, payload = self.connection.recv_data(control_frame=True)
                if opcode in (self.websocket.ABNF.OPCODE_PING, self.websocket.ABNF.OPCODE_PONG):
                    continue
                if opcode == self.websocket.ABNF.OPCODE_CLOSE:
                    raise CenterError(f"{method} 连接结束；结果未确认，先回读，禁止盲目重发")
                response = json.loads(payload)
                if response.get("id") != request_id:
                    if "id" in response and "method" in response:
                        raise CenterError("收到交互请求；请在原客户端处理，不由适配器批准")
                    continue
                if "error" in response:
                    raise CenterError(f"{method} 被服务拒绝：{response['error']}；先回读，不自动重试")
                return response["result"]
        except self.websocket.WebSocketTimeoutException as exc:
            raise CenterError(f"{method} 响应超时；结果未确认，先回读，禁止盲目重发") from exc
        except (OSError, ValueError, KeyError, self.websocket.WebSocketException) as exc:
            raise CenterError(f"{method} 传输或响应异常：{exc}；结果未确认，先回读，禁止盲目重发") from exc

    def close(self):
        self.connection.shutdown()

    def __exit__(self, *exc):
        self.close()


def pages(rpc, method, params):
    """读完服务端给出的全部游标；不把未读取页面当作不存在。"""
    rows = []
    cursor = None
    seen = set()
    while True:
        request = dict(params)
        if cursor is not None:
            request["cursor"] = cursor
        result = rpc.call(method, request)
        rows.extend(result["data"])
        cursor = result["nextCursor"]
        if cursor is None:
            return rows
        if cursor in seen:
            raise CenterError(f"{method} 返回重复游标，历史覆盖未确认")
        seen.add(cursor)


class AgentCenter:
    def __init__(self, rpc, target, group):
        self.rpc, self.target = rpc, target
        self.participant = {**target, "id": target.get("id", group), "role": target.get("role", group)}
        self.group = self.participant["role"]
        try:
            self.thread_id = target["thread_id"]
            for key in ("endpoint", "cwd", "skill_path"):
                if not Path(target[key]).is_absolute():
                    raise CenterError(f"{group} 的 {key} 必须是准确绝对路径")
            if not self.thread_id or not isinstance(self.thread_id, str):
                raise CenterError("缺少准确线程绑定")
        except (KeyError, TypeError) as exc:
            raise CenterError(f"{group} 的线程绑定不完整") from exc

    def _thread(self):
        thread = self.rpc.call("thread/read", {"threadId": self.thread_id, "includeTurns": False})["thread"]
        if thread["id"] != self.thread_id or thread["cwd"] != self.target["cwd"]:
            raise CenterError("线程或工作目录与绑定不符；不猜测替代目标")
        return thread

    def inspect(self):
        threads = pages(self.rpc, "thread/list", {
            "cwd": self.target["cwd"], "limit": 100, "modelProviders": [],
            "sourceKinds": ["cli", "vscode", "exec", "appServer", "subAgent", "subAgentReview",
                            "subAgentCompact", "subAgentThreadSpawn", "subAgentOther", "unknown"],
        })
        turns = pages(self.rpc, "thread/turns/list", {
            "threadId": self.thread_id, "limit": 100, "sortDirection": "asc", "itemsView": "full",
        })
        return {"threads": threads, "turns": turns, "thread": self._thread()}

    def _skill(self):
        result = self.rpc.call("skills/list", {"cwds": [self.target["cwd"]]})
        matches = [
            skill for entry in result["data"] if entry["cwd"] == self.target["cwd"]
            for skill in entry["skills"]
            if skill["name"] == SKILLS[self.group] and skill["path"] == self.target["skill_path"]
            and skill["enabled"]
        ]
        if len(matches) != 1:
            raise CenterError("准确 Skill 路径未被目标发现或不可用；不发送字面名称代替加载")
        return {"type": "skill", "name": matches[0]["name"], "path": matches[0]["path"]}

    def send(self, sender, kind, issuer, issue, message, reply_to=None):
        try:
            sender, issuer = map(as_participant, (sender, issuer))
            validate_route(sender, self.participant, kind, issuer)
        except ParticipantError as exc:
            raise CenterError(str(exc)) from exc
        snapshot = self.inspect()
        thread = snapshot["thread"]
        if thread["status"]["type"] == "notLoaded":
            # 只恢复已绑定线程，不创建、fork 或 flush 新线程。
            self.rpc.call("thread/resume", {"threadId": self.thread_id, "excludeTurns": True})
            snapshot = self.inspect()
            thread = snapshot["thread"]
        state = thread["status"]["type"]
        active = [turn for turn in snapshot["turns"] if turn["status"] == "inProgress"]
        if state not in {"idle", "active"} or thread.get("canAcceptDirectInput") is not True:
            raise CenterError("目标执行状态或接单能力未确认；先回读，不启动任务")
        if (state == "active" and len(active) != 1) or (state == "idle" and active):
            raise CenterError("线程与轮次状态不一致；先回读，不启动第二执行者")
        if kind == "task" and state == "idle":
            # 被打断的轮次可能仍有运行中的命令；不据空闲状态重发作业。
            items = {item["id"]: item for turn in snapshot["turns"] for item in turn["items"]}
            if any(item.get("status") == "inProgress" for item in items.values()):
                raise CenterError("历史执行证据仍有活动作业；向原执行者询问，不重新签发")
            agents = {}
            for item in items.values():
                agents.update({key: value["status"] for key, value in item.get("agentsStates", {}).items()})
                if item["type"] == "subAgentActivity":
                    agents[item["agentThreadId"]] = "completed" if item["kind"] == "completed" else "unknown"
            for writer, status in agents.items():
                if status not in {"pendingInit", "running", "unknown"}:
                    continue
                # agentsStates 是历史快照；对未有终态证据的原执行者回读当前状态。
                current = self.rpc.call("thread/read", {"threadId": writer, "includeTurns": False})["thread"]
                if current["id"] != writer:
                    raise CenterError("原执行者身份未确认；先回读，不重新签发")
                if current["status"]["type"] == "active":
                    raise CenterError("原执行者仍活动；向原线程询问，不启动第二执行者")
                if current["status"]["type"] != "idle":
                    raise CenterError("原执行者当前状态未确认；先回读，不重新签发")
        text = (
            f"来源组：{sender['id']}；消息类型：{kind}；实际签发组：{issuer['id']}；"
            f"目标参与者：{self.participant['id']}；准确 Issue：{issue}\n"
            + (f"原消息：{reply_to}\n" if reply_to else "") +
            "先回读原 Issue 核对授权、签发来源和固定返回关系。本消息不增加范围或预算。\n"
            "恢复或重新签发前核对未提交现场、活动作业、原写入者、遗留队列与剩余预算；"
            "原写入者或作业仍活动时不得启动第二执行者。沿用失败历史及停止条件。\n" + message
        )
        if state == "active":
            result = self.rpc.call("turn/steer", {
                "threadId": self.thread_id, "expectedTurnId": active[0]["id"],
                "input": [{"type": "text", "text": text, "text_elements": []}],
            })
            return {"method": "turn/steer", "result": result}
        skill = self._skill()
        result = self.rpc.call("turn/start", {
            "threadId": self.thread_id, "cwd": self.target["cwd"],
            "input": [{"type": "text", "text": f"${skill['name']}\n{text}", "text_elements": []}, skill],
        })
        return {"method": "turn/start", "result": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "send"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--transport", choices=("github", "agent-center"),
                        help="仅本次调用覆盖目标通道；保留参与者 id 和路线入口，不修改绑定")
    parser.add_argument("--sender")
    parser.add_argument("--kind", choices=sorted(KINDS))
    parser.add_argument("--issuer", help="准确签发参与者；task、result、pr、ack 必填，ack 沿用原消息 issuer")
    parser.add_argument("--issue")
    parser.add_argument("--reply-to", help="原消息评论链接；ack 必填，用于关联准确接收决定")
    parser.add_argument("--since")
    parser.add_argument("--module", action="append", dest="modules")
    parser.add_argument("--message-file", type=Path)
    args = parser.parse_args(argv)
    if args.action == "send" and not all([args.sender, args.kind, args.issue, args.message_file]):
        parser.error("send 需要 sender、kind、issue 和 message-file")
    if args.action == "send" and args.kind in {"task", "result", "pr", "ack"} and not args.issuer:
        parser.error("task、result、pr、ack 需要明确 issuer；ack 沿用原消息 issuer")
    if args.action == "send" and args.kind == "ack" and not args.reply_to:
        parser.error("ack 需要 reply-to 引用原消息")
    try:
        binding = read_binding()
        target = resolve_participant(binding, args.target)
        if args.transport:
            target = {**target, "transport": args.transport}
        def source(selector):
            # 旧配置只要求接收方线程；发送方角色不是额外的线程绑定前提。
            if not binding.get("participants") and selector in SKILLS and selector not in binding.get("targets", {}):
                return as_participant(selector)
            return resolve_participant(binding, selector)
        sender = source(args.sender) if args.action == "send" else target
        issuer = source(args.issuer or args.sender) if args.action == "send" else None
        if args.action == "send":
            validate_route(sender, target, args.kind, issuer)
        if target["transport"] == "github":
            # GitHub 目标无需本机 socket、线程或 Skill，也不创建 RPC 连接。
            from github_transport import GitHubCenter
            center = GitHubCenter(binding, target, sender)
            if args.action == "inspect":
                result = center.inspect(issue=args.issue, since=args.since, modules=args.modules)
            else:
                result = center.send(sender, args.kind, issuer, args.issue,
                                     args.message_file.read_text(), reply_to=args.reply_to)
        else:
            with JsonRpcProxy(binding["agent_center"]["cli"], target["endpoint"]) as rpc:
                center = AgentCenter(rpc, target, target["role"])
                if args.action == "inspect":
                    result = center.inspect()
                else:
                    result = center.send(sender, args.kind, issuer, args.issue,
                                         args.message_file.read_text(), reply_to=args.reply_to)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (RuntimeError, KeyError, OSError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
