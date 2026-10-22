"""GitHub 持久协作通道；只读事实或发布评论，不启动远端执行者。"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from urllib.parse import urlencode, urlsplit

from participants import configured_participants, validate_route


PROTOCOL = "codex-collaboration/v1"
PREFIX = f"<!-- {PROTOCOL} "
KINDS = {"task", "query", "correction", "result", "pr", "request", "notice", "ack"}


class GitHubError(RuntimeError):
    """在本次私有调用中保留诊断，不写入日志或任务账本。"""

    def __init__(self, message, *, method=None, endpoint=None, returncode=None, stdout=None, stderr=None):
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

        self.method, self.endpoint, self.returncode = method, endpoint, returncode
        self.stdout, self.stderr = decoded(stdout), decoded(stderr)
        details = [message]
        if method is not None:
            details.append(f"方法：{method}；端点：{endpoint}；退出码：{returncode if returncode is not None else '未知'}")
        if self.stderr:
            details.append("原始 stderr：\n" + self.stderr)
        if self.stdout:
            details.append("已取得 stdout：\n" + self.stdout)
        super().__init__("\n".join(details))


def issue_location(url):
    """仅把明确的 GitHub Issue/PR 地址转换为 REST 路径。"""
    parsed = urlsplit(url)
    match = re.fullmatch(r"/([^/]+)/([^/]+)/(issues|pull)/([1-9][0-9]*)/?", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or not match:
        raise GitHubError("需要准确的 GitHub Issue 或 PR URL")
    owner, repo, _, number = match.groups()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise GitHubError("仓库地址不明确")
    return f"{owner}/{repo}", int(number)


def comment_location(url):
    repository, number = issue_location(url)
    match = re.fullmatch(r"issuecomment-([1-9][0-9]*)", urlsplit(url).fragment)
    if not match:
        raise GitHubError("reply_to 必须是原生 Issue 评论链接")
    return repository, number, int(match.group(1))


def parse_message(comment):
    """只识别正文开头的协议标记；正文中的引用不成为另一条消息。"""
    body = comment.get("body") or ""
    if not body.startswith(PREFIX):
        return None
    header, separator, message = body.partition("\n")
    try:
        if not separator or not header.endswith(" -->"):
            raise ValueError("标记缺少结束符或正文")
        metadata = json.loads(header[len(PREFIX):-4])
        if not isinstance(metadata, dict) or metadata.get("kind") not in KINDS:
            raise ValueError("消息类型无效")
        if any(not isinstance(metadata.get(key), str) or not metadata[key].strip()
               for key in ("from", "to", "issuer")):
            raise ValueError("缺少准确参与者")
        if metadata.get("reply_to") is not None:
            comment_location(metadata["reply_to"])
    except (ValueError, TypeError, GitHubError) as exc:
        raise GitHubError("协作标记不完整；须回读原评论，不推断接收或执行状态") from exc
    return {**metadata, "url": comment["html_url"], "author": comment.get("user", {}).get("login"),
            "body": message, "updated_at": comment.get("updated_at")}


def governance_wrapper():
    override = os.environ.get("CODEX_GOVERNANCE_CONFIG", "").strip()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = Path(override) if override else base / "codex-governance" / "local.toml"
    try:
        with path.open("rb") as stream:
            root = Path(tomllib.load(stream)["paths"]["source_root"])
        wrapper = root / "scripts" / "group_github.py"
        if not root.is_absolute() or not wrapper.is_file():
            raise ValueError("缺少治理身份入口")
        return wrapper
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GitHubError("无法从治理私有绑定定位身份入口；不回退当前登录账号") from exc


class GitHubCenter:
    def __init__(self, binding, target, actor):
        self.binding, self.target, self.actor = binding, target, actor
        self.wrapper = governance_wrapper()
        if not target.get("anchor"):
            raise GitHubError("GitHub 接收方缺少准确路线入口 anchor")
        issue_location(target["anchor"])

    def _api(self, endpoint, *, paginated=False, payload=None):
        method = "GET" if payload is None else "POST"
        command = [sys.executable, str(self.wrapper), "--group", self.actor["role"], "gh", "--",
                   "api", endpoint, "--hostname", "github.com", "--method", method,
                   "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
        if paginated:
            command.extend(["--paginate", "--slurp"])
        if payload is not None:
            command.extend(["--input", "-"])
        try:
            result = subprocess.run(command, input=None if payload is None else json.dumps(payload, ensure_ascii=False),
                                    capture_output=True, text=True, timeout=45, check=False)
        except subprocess.TimeoutExpired as exc:
            raise GitHubError(f"GitHub {method} 传输超时，结果未确认；先回读，禁止盲目重发",
                              method=method, endpoint=endpoint, stdout=exc.stdout, stderr=exc.stderr) from exc
        except OSError as exc:
            raise GitHubError(f"GitHub {method} 传输未确认；先回读，禁止盲目重发",
                              method=method, endpoint=endpoint, stderr=str(exc)) from exc
        if result.returncode:
            raise GitHubError(f"GitHub {method} 失败（退出码 {result.returncode}）；核对身份、权限与真实记录，不自动重试",
                              method=method, endpoint=endpoint, returncode=result.returncode,
                              stdout=result.stdout, stderr=result.stderr)
        try:
            data = json.loads(result.stdout)
            if paginated:
                if not isinstance(data, list) or any(not isinstance(page, list) for page in data):
                    raise ValueError("分页结果不完整")
                return [row for page in data for row in page]
            return data
        except (ValueError, TypeError) as exc:
            raise GitHubError(f"GitHub {method} 响应未确认：{exc}；先回读，禁止盲目重发",
                              method=method, endpoint=endpoint, returncode=result.returncode,
                              stdout=result.stdout, stderr=result.stderr) from exc

    def _collection(self, endpoint, **params):
        return self._api(endpoint + "?" + urlencode({"per_page": 100, **params}), paginated=True)

    def inspect(self, issue=None, since=None, modules=None):
        participants = configured_participants(self.binding)
        anchors = list(dict.fromkeys(p["anchor"] for p in participants if p.get("anchor")))
        if self.target["anchor"] not in anchors:
            anchors.append(self.target["anchor"])
        locations = list(dict.fromkeys(issue_location(url) for url in anchors + ([issue] if issue else [])))
        issues, timelines, references = [], {}, []
        repositories = list(dict.fromkeys(repository for repository, _ in locations))
        for repository, number in locations:
            issues.append(self._api(f"repos/{repository}/issues/{number}"))
            # Timeline 没有 since 参数；即使增量发现也完整读取这些准确入口。
            events = self._collection(f"repos/{repository}/issues/{number}/timeline")
            url = f"https://github.com/{repository}/issues/{number}"
            timelines[url] = events
            for event in events:
                linked = event.get("source", {}).get("issue", {}).get("html_url")
                if linked and linked not in references:
                    references.append(linked)
        discovered, comments = {}, {}
        labels = list(dict.fromkeys(modules if modules is not None else self.target.get("modules", [])))
        for repository in repositories:
            params = {"state": "all", "sort": "updated", "direction": "asc"}
            if since:
                params["since"] = since
            rows = {}
            # 多个模块为并集；不把 REST 的多标签交集误当成并集。
            for label in labels or [None]:
                query = {**params, **({"labels": label} if label else {})}
                for row in self._collection(f"repos/{repository}/issues", **query):
                    rows[row["html_url"]] = row
            discovered.update(rows)
            query = {"sort": "updated", "direction": "asc"}
            if since:
                query["since"] = since
            # 仓库评论发现来源任务上的消息，无须为每个来源另建收件箱。
            for row in self._collection(f"repos/{repository}/issues/comments", **query):
                comments[row["html_url"]] = row
        # 准确入口和显式依赖的评论始终完整读取，增量 since 不截断其历史。
        for repository, number in locations:
            for row in self._collection(f"repos/{repository}/issues/{number}/comments"):
                comments[row["html_url"]] = row
        messages, invalid = [], []
        for comment in comments.values():
            try:
                message = parse_message(comment)
                if message:
                    messages.append(message)
            except GitHubError as exc:
                invalid.append({"url": comment["html_url"], "reason": str(exc)})
        incoming = [m for m in messages if m["to"] == self.target["id"]]
        replies = {(m["reply_to"], m["from"], m["to"], m["issuer"])
                   for m in messages if m.get("reply_to")}
        unanswered = [m for m in incoming if m["kind"] in {"request", "task"}
                      and (m["url"], m["to"], m["from"], m["issuer"]) not in replies]
        return {"participant": self.target["id"], "coverage": "incremental" if since else "full",
                "since": since, "issues": issues, "timelines": timelines, "references": references,
                "discovered": list(discovered.values()), "comments": list(comments.values()),
                "messages": messages, "incoming": incoming, "invalid_messages": invalid,
                "requests_without_reply_in_read_evidence": unanswered,
                "execution_state": "unconfirmed"}

    def send(self, sender, kind, issuer, issue, message, reply_to=None):
        validate_route(sender, self.target, kind, issuer)
        if self.actor["id"] != sender["id"]:
            raise GitHubError("平台操作身份须属于准确发送参与者")
        if kind not in KINDS or not message.strip():
            raise GitHubError("需要消息类型和完整交接正文")
        repository, number = issue_location(issue)
        if kind == "ack" and not reply_to:
            raise GitHubError("接收决定须引用原消息 reply_to")
        if reply_to:
            reply_repository, _, comment_id = comment_location(reply_to)
            original = self._api(f"repos/{reply_repository}/issues/comments/{comment_id}")
            if original.get("html_url") != reply_to:
                raise GitHubError("原消息链接与回读结果不符")
            parsed = parse_message(original)
            if parsed and kind in {"ack", "result", "pr"}:
                if parsed["from"] != self.target["id"] or parsed["to"] != sender["id"] or parsed["issuer"] != issuer["id"]:
                    raise GitHubError("回复参与者或实际签发来源与原消息不符")
        metadata = {"kind": kind, "from": sender["id"], "to": self.target["id"],
                    "issuer": issuer["id"], "reply_to": reply_to}
        header = PREFIX + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->"
        body = (header + "\n" + f"来源任务：{issue}\n接收入口：{self.target['anchor']}\n"
                "发布不代表接单或作业已启动；接收方先核对原授权、现场、预算和停止条件。\n\n" + message)
        # 读取精确来源任务与所有评论；未知发布结果恢复后同一正文只返回已有链接。
        self._api(f"repos/{repository}/issues/{number}")
        comments = self._collection(f"repos/{repository}/issues/{number}/comments")
        for comment in comments:
            if comment.get("body") == body:
                return {"publication": "existing", "url": comment["html_url"], "execution_state": "unconfirmed"}
        created = self._api(f"repos/{repository}/issues/{number}/comments", payload={"body": body})
        try:
            comment_location(created["html_url"])
        except (KeyError, TypeError, GitHubError) as exc:
            raise GitHubError("评论发布结果未确认；先回读，禁止盲目重发",
                              method="POST", endpoint=f"repos/{repository}/issues/{number}/comments",
                              returncode=0, stdout=json.dumps(created, ensure_ascii=False)) from exc
        return {"publication": "published", "url": created["html_url"], "execution_state": "unconfirmed"}
