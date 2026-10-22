import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = Path(os.environ.get("GOVERNANCE_SOURCE_ROOT", ROOT.parent / "codex-governance"))
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("github_transport", ROOT / "scripts/github_transport.py")
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)
center_spec = importlib.util.spec_from_file_location("github_cli_center", ROOT / "scripts/agent_center.py")
center_module = importlib.util.module_from_spec(center_spec)
center_spec.loader.exec_module(center_module)


SOURCE = "https://github.com/example/project/issues/7"
ANCHOR_A = "https://github.com/example/project/issues/11"
ANCHOR_B = "https://github.com/example/project/issues/12"
ANCHOR_C = "https://github.com/example/project/issues/13"
ORIGINAL = SOURCE + "#issuecomment-101"


def participant(identifier, role="decision", anchor=ANCHOR_B):
    return {"id": identifier, "role": role, "transport": "github", "anchor": anchor}


def binding_for(*participants):
    return {"participants": {p["id"]: {k: v for k, v in p.items() if k != "id"} for p in participants}}


def protocol_comment(identifier, sender, target, kind="request", issuer=None, reply_to=None, body="准确范围见任务合同。"):
    metadata = {"kind": kind, "from": sender, "to": target,
                "issuer": issuer or sender, "reply_to": reply_to}
    return {"id": identifier, "html_url": SOURCE + f"#issuecomment-{identifier}",
            "body": transport.PREFIX + json.dumps(metadata, separators=(",", ":")) + " -->\n" + body,
            "user": {"login": "fixed-decision-account"}, "updated_at": "2026-10-22T00:00:00Z"}


class MemoryGitHub:
    """模拟 REST 边界；保留已生效但响应丢失的评论。"""

    def __init__(self):
        self.calls = []
        self.comments = []
        self.events = {}
        self.issue_rows = {}
        self.fail_post_after_commit = False

    def __call__(self, endpoint, *, paginated=False, payload=None):
        self.calls.append((endpoint, paginated, copy.deepcopy(payload)))
        path, _, query = endpoint.partition("?")
        if payload is not None:
            created = {"html_url": SOURCE + f"#issuecomment-{200 + len(self.comments)}", **payload}
            self.comments.append(created)
            if self.fail_post_after_commit:
                self.fail_post_after_commit = False
                raise transport.GitHubError("GitHub POST 传输未确认；先回读，禁止盲目重发")
            return created
        if "/issues/comments/" in path:
            identifier = int(path.rsplit("/", 1)[1])
            return next(c for c in self.comments if c.get("id") == identifier)
        if path.endswith("/timeline"):
            return self.events.get(path, [])
        if path.endswith("/comments"):
            return copy.deepcopy(self.comments)
        if path.endswith("/issues"):
            label = parse_qs(query).get("labels", [None])[0]
            return copy.deepcopy(self.issue_rows.get(label, []))
        return {"html_url": "https://github.com/" + path.removeprefix("repos/"), "state": "open"}


class GitHubProtocolTests(unittest.TestCase):
    def setUp(self):
        self.a = participant("decision:A", anchor=ANCHOR_A)
        self.b = participant("decision:B")
        self.c = participant("decision:C", anchor=ANCHOR_C)
        self.binding = binding_for(self.a, self.b, self.c)
        self.api = MemoryGitHub()
        self.wrapper_patch = patch.object(transport, "governance_wrapper", return_value=Path("/unused/group_github.py"))
        self.wrapper_patch.start()
        self.addCleanup(self.wrapper_patch.stop)
        self.center = transport.GitHubCenter(self.binding, self.b, self.a)
        self.center._api = self.api

    def test_only_first_line_marker_is_a_message(self):
        original = protocol_comment(101, "decision:A", "decision:B")
        self.assertEqual(transport.parse_message(original)["to"], "decision:B")
        for prefix in ("讨论中的引用：\n", "> ", "\n", "```\n"):
            with self.subTest(prefix=prefix):
                self.assertIsNone(transport.parse_message({**original, "body": prefix + original["body"]}))
        malformed = {**original, "body": transport.PREFIX + '{"kind":"request"} -->\n正文'}
        with self.assertRaises(transport.GitHubError):
            transport.parse_message(malformed)

    def test_inspect_routes_by_participant_id_without_claiming_execution(self):
        self.api.comments = [protocol_comment(101, "decision:A", "decision:B"),
                             protocol_comment(102, "decision:A", "decision:C")]
        result = self.center.inspect()
        self.assertEqual([message["url"] for message in result["incoming"]], [ORIGINAL])
        self.assertEqual(result["requests_without_reply_in_read_evidence"], result["incoming"])
        self.assertEqual(result["execution_state"], "unconfirmed")
        self.assertNotIn("idle", result.values())
        self.api.comments.append(protocol_comment(103, "decision:B", "decision:A", "ack",
                                                  "decision:A", ORIGINAL))
        result = self.center.inspect()
        self.assertEqual(result["requests_without_reply_in_read_evidence"], [])
        self.assertEqual(result["execution_state"], "unconfirmed")

    def test_unrelated_target_or_issuer_reply_does_not_hide_pending_peer_request(self):
        request = protocol_comment(101, "decision:A", "decision:B")
        for recipient, issuer in (("decision:C", "decision:A"), ("decision:A", "decision:C")):
            with self.subTest(recipient=recipient, issuer=issuer):
                self.api.comments = [request, protocol_comment(102, "decision:B", recipient, "notice",
                                                               issuer, ORIGINAL)]
                result = self.center.inspect()
                self.assertEqual([message["url"] for message in result["requests_without_reply_in_read_evidence"]],
                                 [ORIGINAL])
        self.api.comments = [request, protocol_comment(102, "decision:B", "decision:A", "ack",
                                                       "decision:A", ORIGINAL)]
        self.assertEqual(self.center.inspect()["requests_without_reply_in_read_evidence"], [])

    def test_anchors_and_explicit_task_history_are_not_truncated_by_since(self):
        linked = "https://github.com/example/project/issues/31"
        self.api.events["repos/example/project/issues/12/timeline"] = [
            {"event": "cross-referenced", "source": {"issue": {"html_url": linked}}}]
        self.api.comments = [protocol_comment(101, "decision:A", "decision:B")]
        result = self.center.inspect(issue=SOURCE, since="2026-10-21T00:00:00Z")
        self.assertEqual(set(result["timelines"]), {ANCHOR_A, ANCHOR_B, ANCHOR_C, SOURCE})
        self.assertEqual(result["references"], [linked])
        self.assertEqual(len(result["comments"]), 1)
        for endpoint, paginated, payload in self.api.calls:
            path, _, query = endpoint.partition("?")
            params = parse_qs(query)
            if path.endswith("/timeline") or (path.endswith("/comments") and path != "repos/example/project/issues/comments"):
                self.assertNotIn("since", params)
            if path.endswith("/issues") or path.endswith("/issues/comments"):
                self.assertEqual(params["since"], ["2026-10-21T00:00:00Z"])
            if query:
                self.assertTrue(paginated)
                self.assertEqual(params["per_page"], ["100"])
                self.assertIsNone(payload)

    def test_module_discovery_is_union_and_preserves_closed_pr_rows(self):
        pr = {"html_url": "https://github.com/example/project/pull/24", "state": "closed",
              "pull_request": {"url": "https://api.github.com/repos/example/project/pulls/24"}}
        task = {"html_url": "https://github.com/example/project/issues/25", "state": "open"}
        self.api.issue_rows = {"模块:ledger": [pr], "模块:backtest": [task, pr]}
        result = self.center.inspect(modules=["模块:ledger", "模块:backtest", "模块:ledger"])
        self.assertEqual({row["html_url"] for row in result["discovered"]}, {pr["html_url"], task["html_url"]})
        searches = [parse_qs(urlsplit(endpoint).query) for endpoint, _, _ in self.api.calls
                    if endpoint.partition("?")[0] == "repos/example/project/issues"]
        self.assertEqual([query["labels"] for query in searches], [["模块:ledger"], ["模块:backtest"]])
        self.assertTrue(all(query["state"] == ["all"] for query in searches))

    def test_same_exact_message_posts_once_and_does_not_claim_acceptance(self):
        first = self.center.send(self.a, "request", self.a, SOURCE, "请评估公共模块缺口，预算沿用原任务。")
        second = self.center.send(self.a, "request", self.a, SOURCE, "请评估公共模块缺口，预算沿用原任务。")
        self.assertEqual(first["publication"], "published")
        self.assertEqual(second["publication"], "existing")
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(sum(payload is not None for _, _, payload in self.api.calls), 1)
        self.assertEqual(first["execution_state"], "unconfirmed")

    def test_unconfirmed_comment_shape_keeps_received_json_without_retry(self):
        response = {"message": "评论结果尚未提供链接"}
        with patch.object(self.center, "_api", side_effect=[{"state": "open"}, [], response]) as api:
            with self.assertRaises(transport.GitHubError) as captured:
                self.center.send(self.a, "request", self.a, SOURCE, "同一公共请求。")
        error = captured.exception
        self.assertEqual((error.method, error.endpoint, error.returncode),
                         ("POST", "repos/example/project/issues/7/comments", 0))
        self.assertEqual(json.loads(error.stdout), response)
        self.assertEqual(sum(call.kwargs.get("payload") is not None for call in api.call_args_list), 1)

    def test_post_committed_then_disconnected_is_not_retried_and_recovery_reads_existing(self):
        self.api.fail_post_after_commit = True
        with self.assertRaisesRegex(transport.GitHubError, "POST.*未确认"):
            self.center.send(self.a, "request", self.a, SOURCE, "同一项公共任务。")
        self.assertEqual(len(self.api.comments), 1)
        self.assertEqual(sum(payload is not None for _, _, payload in self.api.calls), 1)
        result = self.center.send(self.a, "request", self.a, SOURCE, "同一项公共任务。")
        self.assertEqual(result["publication"], "existing")
        self.assertEqual(sum(payload is not None for _, _, payload in self.api.calls), 1)

    def test_ack_and_result_reply_to_exact_peer_request_and_issuer(self):
        self.api.comments = [protocol_comment(101, self.a["id"], self.b["id"])]
        reply = transport.GitHubCenter(self.binding, self.a, self.b)
        reply._api = self.api
        for kind in ("ack", "result"):
            with self.subTest(kind=kind):
                result = reply.send(self.b, kind, self.a, SOURCE, "回传准确任务事实。", ORIGINAL)
                self.assertEqual(result["publication"], "published")
                parsed = transport.parse_message(self.api.comments[-1])
                self.assertEqual((parsed["from"], parsed["to"], parsed["issuer"], parsed["reply_to"]),
                                 ("decision:B", "decision:A", "decision:A", ORIGINAL))

    def test_reply_participants_and_issuer_cannot_be_substituted(self):
        self.api.comments = [protocol_comment(101, self.a["id"], self.b["id"])]
        reply = transport.GitHubCenter(self.binding, self.a, self.b)
        reply._api = self.api
        for metadata in ({"from": "decision:C"}, {"to": "decision:C"}, {"issuer": "decision:C"}):
            with self.subTest(metadata=metadata):
                original = protocol_comment(101, self.a["id"], self.b["id"])
                parsed = {"kind": "request", "from": self.a["id"], "to": self.b["id"],
                          "issuer": self.a["id"], "reply_to": None, **metadata}
                original["body"] = transport.PREFIX + json.dumps(parsed) + " -->\n原任务。"
                self.api.comments = [original]
                with self.assertRaisesRegex(transport.GitHubError, "参与者.*签发来源"):
                    reply.send(self.b, "ack", self.a, SOURCE, "接收。", ORIGINAL)
        self.assertFalse(any(payload is not None for _, _, payload in self.api.calls))

    def test_pr_is_returned_by_actual_issuer_to_exact_developer(self):
        developer = participant("development:B", "development", ANCHOR_B)
        self.api.comments = [protocol_comment(101, developer["id"], self.b["id"], "result", self.b["id"])]
        center = transport.GitHubCenter(binding_for(self.b, developer), developer, self.b)
        center._api = self.api
        result = center.send(self.b, "pr", self.b, SOURCE, "签发组创建的 PR 链接及准确版本。", ORIGINAL)
        self.assertEqual(result["publication"], "published")
        parsed = transport.parse_message(self.api.comments[-1])
        self.assertEqual((parsed["from"], parsed["to"], parsed["issuer"]),
                         ("decision:B", "development:B", "decision:B"))

    def test_reply_requires_native_comment_and_exact_readback(self):
        for bad_url in (SOURCE, SOURCE + "#discussion_r123", "https://github.com/example/project/pull/7#pullrequestreview-3"):
            with self.subTest(url=bad_url), self.assertRaises(transport.GitHubError):
                self.center.send(self.a, "ack", self.a, SOURCE, "接收。", bad_url)
        self.api.comments = [{**protocol_comment(101, self.b["id"], self.a["id"]),
                              "html_url": SOURCE + "#issuecomment-999"}]
        with self.assertRaisesRegex(transport.GitHubError, "链接.*不符"):
            self.center.send(self.a, "ack", self.a, SOURCE, "接收。", ORIGINAL)

    def test_legacy_native_comment_is_kept_as_evidence_without_invented_routing(self):
        legacy = {"id": 101, "html_url": ORIGINAL, "body": "原用户授权与旧任务交接，按原合同核对。",
                  "user": {"login": "original-user"}}
        self.api.comments = [legacy]
        inspected = self.center.inspect(issue=SOURCE)
        self.assertEqual(inspected["messages"], [])
        self.assertEqual(inspected["comments"], [legacy])
        result = self.center.send(self.a, "ack", self.a, SOURCE, "已回读原评论，具体接收决定见本条正文。", ORIGINAL)
        self.assertEqual(result["publication"], "published")
        self.assertEqual(self.api.comments[0], legacy)

    def test_ack_without_original_comment_is_not_published(self):
        with self.assertRaisesRegex(transport.GitHubError, "reply_to"):
            self.center.send(self.a, "ack", self.a, SOURCE, "接收。")
        self.assertEqual(self.api.calls, [])

    def test_target_anchor_and_actor_are_explicit(self):
        with self.assertRaisesRegex(transport.GitHubError, "anchor"):
            transport.GitHubCenter(self.binding, {"id": "decision:B", "role": "decision"}, self.a)
        with self.assertRaisesRegex(transport.GitHubError, "准确发送参与者"):
            self.center.send(self.c, "notice", self.c, SOURCE, "事实通知。")
        self.assertEqual(self.api.calls, [])

    def test_cli_github_transport_never_opens_rpc(self):
        with tempfile.TemporaryDirectory() as temporary:
            message = Path(temporary) / "message.md"
            message.write_text("请评估当前公共接口，授权与预算见原任务。")
            with patch.object(center_module, "read_binding", return_value=self.binding), \
                    patch.object(center_module, "JsonRpcProxy", side_effect=AssertionError("不能连接本机 RPC")), \
                    patch.dict(sys.modules, {"github_transport": transport}), \
                    patch.object(transport.GitHubCenter, "_api", side_effect=self.api), \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                status = center_module.main(["send", "--target", "decision:B", "--sender", "decision:A",
                                             "--kind", "request", "--issue", SOURCE,
                                             "--message-file", str(message)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["publication"], "published")
        self.assertEqual(transport.parse_message(self.api.comments[-1])["to"], "decision:B")


class GitHubCommandBoundaryTests(unittest.TestCase):
    def make_center(self):
        sender = participant("decision:A", anchor=ANCHOR_A)
        target = participant("decision:B")
        with patch.object(transport, "governance_wrapper", return_value=Path("/test/group_github.py")):
            return transport.GitHubCenter(binding_for(sender, target), target, sender)

    def test_gh_slurp_all_pages_are_flattened_and_complete_flags_pass_through(self):
        center = self.make_center()
        pages = [[{"id": 1}], [{"id": 2}, {"id": 3}]]
        with patch.object(transport.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(pages))) as run:
            rows = center._collection("repos/example/project/issues/7/comments")
        self.assertEqual(rows, [{"id": 1}, {"id": 2}, {"id": 3}])
        command = run.call_args.args[0]
        self.assertEqual(command[:7], [sys.executable, "/test/group_github.py", "--group", "decision", "gh", "--", "api"])
        self.assertIn("--paginate", command)
        self.assertIn("--slurp", command)
        self.assertIn("repos/example/project/issues/7/comments?per_page=100", command)

    def test_bad_pagination_response_does_not_claim_full_read(self):
        center = self.make_center()
        with patch.object(transport.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '[{"id": 1}]')):
            with self.assertRaisesRegex(transport.GitHubError, "响应未确认"):
                center._collection("repos/example/project/issues/7/comments")

    def test_post_timeout_is_unknown_without_automatic_retry(self):
        center = self.make_center()
        with patch.object(transport.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 45)) as run:
            with self.assertRaisesRegex(transport.GitHubError, "POST.*未确认"):
                center._api("repos/example/project/issues/7/comments", payload={"body": "原文。"})
        self.assertEqual(run.call_count, 1)

    def test_same_exit_code_preserves_distinct_original_diagnostics(self):
        center = self.make_center()
        endpoint = "repos/example/project/issues/7/comments?per_page=100"
        failures = [
            ('{"message":"Resource not accessible"}', "gh: Resource not accessible by integration (HTTP 403)\n"),
            ('{"message":"API rate limit exceeded"}', "gh: API rate limit exceeded (HTTP 429)\n"),
            ("", "research 的私有身份绑定缺失或不可读\n"),
        ]
        messages = []
        for stdout, stderr in failures:
            with self.subTest(stderr=stderr), \
                    patch.object(transport.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout, stderr)) as run:
                with self.assertRaises(transport.GitHubError) as captured:
                    center._api(endpoint, paginated=True)
                error = captured.exception
                self.assertEqual((error.method, error.endpoint, error.returncode), ("GET", endpoint, 1))
                self.assertEqual((error.stdout, error.stderr), (stdout, stderr))
                self.assertIn(stderr.strip(), str(error))
                if stdout:
                    self.assertIn(stdout, str(error))
                self.assertEqual(run.call_count, 1)
                messages.append(str(error))
        self.assertEqual(len(set(messages)), 3)

    def test_timeout_keeps_partial_byte_output_as_text_without_retry(self):
        center = self.make_center()
        endpoint = "repos/example/project/issues/7/comments"
        stdout, stderr = '{"partial":"已接收"', "传输中断前的诊断\n"
        for payload, method in ((None, "GET"), ({"body": "原文。"}, "POST")):
            failure = subprocess.TimeoutExpired("fake", 45, output=stdout.encode(), stderr=stderr.encode())
            with self.subTest(method=method), patch.object(transport.subprocess, "run", side_effect=failure) as run:
                with self.assertRaises(transport.GitHubError) as captured:
                    center._api(endpoint, payload=payload)
                error = captured.exception
                self.assertEqual((error.method, error.endpoint, error.returncode), (method, endpoint, None))
                self.assertEqual((error.stdout, error.stderr), (stdout, stderr))
                self.assertIn(stdout, str(error))
                self.assertIn(stderr.strip(), str(error))
                self.assertEqual(run.call_count, 1)

    def test_success_exit_with_invalid_response_preserves_raw_output_and_reason(self):
        center = self.make_center()
        endpoint = "repos/example/project/issues/7/comments"
        cases = [("upstream returned non-JSON content", False, "Expecting value"),
                 ('[{"id":1}]', True, "分页结果不完整")]
        for stdout, paginated, reason in cases:
            stderr = "upstream diagnostic\n"
            with self.subTest(paginated=paginated), \
                    patch.object(transport.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout, stderr)) as run:
                with self.assertRaises(transport.GitHubError) as captured:
                    center._api(endpoint, paginated=paginated)
                error = captured.exception
                self.assertEqual((error.method, error.endpoint, error.returncode), ("GET", endpoint, 0))
                self.assertEqual((error.stdout, error.stderr), (stdout, stderr))
                self.assertIn(stdout, str(error))
                self.assertIn(stderr.strip(), str(error))
                self.assertIn(reason, str(error))
                self.assertEqual(run.call_count, 1)


class RealIdentityBoundaryTests(unittest.TestCase):
    """真实运行身份包装子进程；唯一 gh 位于临时目录，不接触平台。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        binary = self.root / "bin"
        binary.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {"PATH": str(binary) + os.pathsep + os.environ["PATH"],
                    "XDG_CONFIG_HOME": str(self.root / "empty-config"),
                    "GH_CONFIG_DIR": "/inherited/wrong-account", "GH_TOKEN": "wrong-inherited-token",
                    "GITHUB_TOKEN": "another-wrong-token", "GH_HOST": "wrong.example",
                    "FAKE_GH_LOG": str(self.log)}
        for role, variable in (("decision", "CODEX_DECISION_ASSISTANT_CONFIG"),
                               ("research", "CODEX_STRATEGY_RESEARCH_CONFIG"),
                               ("development", "CODEX_GOVERNANCE_CONFIG")):
            directory = self.root / (role + " account")
            directory.mkdir()
            config = self.root / (role + ".toml")
            config.write_text('[paths]\nsource_root = ' + json.dumps(str(GOVERNANCE)) +
                              f'\n[github.{role}]\nlogin = "{role}-fixed"\nconfig_dir = ' + json.dumps(str(directory)) + "\n")
            self.env[variable] = str(config)
        executable = binary / "gh"
        executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
role = Path(os.environ['GH_CONFIG_DIR']).name.split()[0]
record = {'role': role, 'args': args, 'host': os.environ.get('GH_HOST'),
          'tokens': [key for key in ('GH_TOKEN', 'GITHUB_TOKEN') if key in os.environ]}
with open(os.environ['FAKE_GH_LOG'], 'a') as stream:
    stream.write(json.dumps(record) + '\\n')
if args[:2] == ['api', 'user']:
    print(os.environ.get('FAKE_GH_LOGIN', role + '-fixed'))
elif '--input' in args:
    json.load(sys.stdin)
    print(json.dumps({'html_url': 'https://github.com/example/project/issues/7#issuecomment-501'}))
elif '--slurp' in args:
    print('[[], []]')
else:
    print(json.dumps({'html_url': 'https://github.com/example/project/issues/7'}))
''')
        executable.chmod(0o755)

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_actual_wrapper_uses_each_fixed_identity_despite_wrong_inherited_tokens(self):
        for role in ("decision", "research", "development"):
            sender = participant(role + ":A", role, ANCHOR_A)
            target = participant("decision:B")
            with self.subTest(role=role), patch.dict(os.environ, self.env):
                center = transport.GitHubCenter(binding_for(sender, target), target, sender)
                result = center.send(sender, "notice", sender, SOURCE, "真实身份入口的隔离模拟通知。")
            self.assertEqual(result["publication"], "published")
        records = self.records()
        self.assertEqual({record["role"] for record in records}, {"decision", "research", "development"})
        self.assertTrue(all(not record["tokens"] for record in records))
        self.assertTrue(all(record["host"] == "github.com" for record in records))
        for role in ("decision", "research", "development"):
            posts = [record for record in records if record["role"] == role and "POST" in record["args"]]
            self.assertEqual(len(posts), 1)
        self.assertEqual(self.env["GH_TOKEN"], "wrong-inherited-token")

    def test_wrong_actual_login_stops_before_source_read_or_comment_post(self):
        with patch.dict(os.environ, {**self.env, "FAKE_GH_LOGIN": "other-account"}):
            a = participant("decision:A", anchor=ANCHOR_A)
            b = participant("decision:B")
            center = transport.GitHubCenter(binding_for(a, b), b, a)
            with self.assertRaises(transport.GitHubError):
                center.send(a, "request", a, SOURCE, "不应发布。")
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]["args"][:2], ["api", "user"])

    def test_missing_group_binding_does_not_fall_back_to_existing_account(self):
        missing = self.root / "missing-research.toml"
        with patch.dict(os.environ, {**self.env, "CODEX_STRATEGY_RESEARCH_CONFIG": str(missing)}):
            researcher = participant("research:A", "research", ANCHOR_A)
            target = participant("decision:B")
            center = transport.GitHubCenter(binding_for(researcher, target), target, researcher)
            with self.assertRaises(transport.GitHubError):
                center.send(researcher, "notice", researcher, SOURCE, "不应发布。")
        self.assertEqual(self.records(), [])

    def test_missing_governance_root_never_uses_global_login(self):
        config = self.root / "wrong-governance.toml"
        config.write_text('[paths]\nsource_root="/unavailable/governance"\n')
        with patch.dict(os.environ, {**self.env, "CODEX_GOVERNANCE_CONFIG": str(config)}):
            a, b = participant("decision:A", anchor=ANCHOR_A), participant("decision:B")
            with self.assertRaisesRegex(transport.GitHubError, "不回退"):
                transport.GitHubCenter(binding_for(a, b), b, a)
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
