import copy
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agent_center", ROOT / "scripts/agent_center.py")
center_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(center_module)
AgentCenter, CenterError = center_module.AgentCenter, center_module.CenterError

TARGET = {"endpoint": "/test/control.sock", "thread_id": "bound-thread", "cwd": "/test/project",
          "skill_path": "/test/skills/codex-stage-orchestrator/SKILL.md"}


class FakeRPC:
    def __init__(self, state="idle"):
        self.calls = []
        self.state = state
        self.accept = True
        self.skill_enabled = True
        self.skill_name = "codex-stage-orchestrator"
        self.skill_path = TARGET["skill_path"]
        self.wrong_cwd = False
        self.turns = [{"id": "old", "status": "failed", "items": [
            {"id": "old-command", "type": "commandExecution", "status": "completed",
             "aggregatedOutput": "原始失败：输入版本不符"}]}]
        if state == "active":
            self.turns.append({"id": "live-turn", "status": "inProgress", "items": []})
        self.fail_send = False
        self.child_states = {}

    def call(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "thread/list":
            if "cursor" not in params:
                return {"data": [{"id": "unrelated"}], "nextCursor": "second-thread-page"}
            return {"data": [{"id": "bound-thread"}], "nextCursor": None}
        if method == "thread/turns/list":
            self.assert_full = params["itemsView"]
            if "cursor" not in params:
                return {"data": self.turns[:1], "nextCursor": "second-turn-page"}
            return {"data": self.turns[1:], "nextCursor": None}
        if method == "thread/read":
            if params["threadId"] != "bound-thread":
                return {"thread": {"id": params["threadId"], "status": {
                    "type": self.child_states.get(params["threadId"], "notLoaded")}}}
            return {"thread": {"id": "bound-thread", "cwd": "/other" if self.wrong_cwd else "/test/project",
                               "status": {"type": self.state}, "canAcceptDirectInput": self.accept}}
        if method == "thread/resume":
            self.state = "idle"
            return {"thread": {"id": "bound-thread"}}
        if method == "skills/list":
            return {"data": [{"cwd": "/test/project", "skills": [{"name": self.skill_name,
                     "path": self.skill_path, "enabled": self.skill_enabled}]}]}
        if method in {"turn/start", "turn/steer"}:
            if self.fail_send:
                raise CenterError("服务端状态变化；拒绝发送")
            return {"turnId": "accepted"}
        raise AssertionError(method)


class RoutingTests(unittest.TestCase):
    def make(self, state="idle"):
        rpc = FakeRPC(state)
        return rpc, AgentCenter(rpc, TARGET, "development")

    def send(self, center, **overrides):
        args = dict(sender="research", kind="task", issuer="research",
                    issue="https://github.com/example/project/issues/7", message="范围与预算引用原 Issue。")
        args.update(overrides)
        return center.send(**args)

    def test_reads_all_pages_and_full_execution_evidence(self):
        rpc, center = self.make("active")
        result = center.inspect()
        self.assertEqual([t["id"] for t in result["threads"]], ["unrelated", "bound-thread"])
        self.assertEqual([t["id"] for t in result["turns"]], ["old", "live-turn"])
        self.assertEqual(result["turns"][0]["items"][0]["aggregatedOutput"], "原始失败：输入版本不符")
        self.assertEqual(rpc.assert_full, "full")

    def test_idle_uses_discovered_structured_skill_without_model_overrides(self):
        rpc, center = self.make()
        result = self.send(center)
        method, request = rpc.calls[-1]
        self.assertEqual(method, "turn/start")
        self.assertEqual(request["threadId"], "bound-thread")
        self.assertEqual(request["input"][1], {"type": "skill", "name": "codex-stage-orchestrator", "path": TARGET["skill_path"]})
        self.assertEqual(set(request), {"threadId", "cwd", "input"})
        self.assertEqual(result["method"], "turn/start")

    def test_active_steers_exact_turn_and_never_starts_another(self):
        rpc, center = self.make("active")
        self.send(center, kind="correction")
        self.assertEqual(rpc.calls[-1][0], "turn/steer")
        self.assertEqual(rpc.calls[-1][1]["expectedTurnId"], "live-turn")
        self.assertNotIn("turn/start", [method for method, _ in rpc.calls])

    def test_interrupted_turn_with_running_job_does_not_reissue(self):
        rpc, center = self.make()
        rpc.turns = [{"id": "stopped-turn", "status": "interrupted", "items": [
            {"id": "job", "type": "commandExecution", "status": "inProgress", "processId": "job-19"}]}]
        with self.assertRaisesRegex(CenterError, "活动作业"):
            self.send(center)
        self.assertNotIn("turn/start", [method for method, _ in rpc.calls])

    def test_running_original_writer_does_not_get_a_second_executor(self):
        rpc, center = self.make()
        rpc.turns[0]["items"].append({"id": "spawn", "type": "collabAgentToolCall", "status": "completed",
                                    "agentsStates": {"original-writer": {"status": "running"}}})
        rpc.child_states["original-writer"] = "active"
        with self.assertRaisesRegex(CenterError, "原执行者仍活动"):
            self.send(center)
        self.assertNotIn("turn/start", [method for method, _ in rpc.calls])
        self.assertIn(("thread/read", {"threadId": "original-writer", "includeTurns": False}), rpc.calls)

    def test_completed_activity_supersedes_old_running_snapshot(self):
        rpc, center = self.make()
        rpc.turns[0]["items"].extend([
            {"id": "spawn", "type": "collabAgentToolCall", "status": "completed",
             "agentsStates": {"original-writer": {"status": "running"}}},
            {"id": "finished", "type": "subAgentActivity", "kind": "completed",
             "agentThreadId": "original-writer"},
        ])
        self.send(center)
        self.assertEqual(rpc.calls[-1][0], "turn/start")

    def test_old_running_snapshot_reads_current_idle_writer_before_accepting(self):
        rpc, center = self.make()
        rpc.turns[0]["items"].append({"id": "spawn", "type": "collabAgentToolCall", "status": "completed",
                                    "agentsStates": {"original-writer": {"status": "running"}}})
        rpc.child_states["original-writer"] = "idle"
        self.send(center)
        self.assertIn(("thread/read", {"threadId": "original-writer", "includeTurns": False}), rpc.calls)
        self.assertEqual(rpc.calls[-1][0], "turn/start")

    def test_unknown_original_writer_state_stops_reissue_after_reading(self):
        rpc, center = self.make()
        rpc.turns[0]["items"].append({"id": "spawn", "type": "collabAgentToolCall", "status": "completed",
                                    "agentsStates": {"original-writer": {"status": "running"}}})
        with self.assertRaisesRegex(CenterError, "当前状态未确认"):
            self.send(center)
        self.assertIn(("thread/read", {"threadId": "original-writer", "includeTurns": False}), rpc.calls)
        self.assertNotIn("turn/start", [method for method, _ in rpc.calls])

    def test_unloaded_target_resumes_only_the_bound_thread(self):
        rpc, center = self.make("notLoaded")
        self.send(center)
        resumes = [params for method, params in rpc.calls if method == "thread/resume"]
        self.assertEqual(resumes, [{"threadId": "bound-thread", "excludeTurns": True}])
        self.assertFalse(any(method in {"thread/start", "thread/fork", "thread/compact/start"} for method, _ in rpc.calls))

    def test_unknown_state_never_sends(self):
        for state in ("systemError", "futureState"):
            rpc, center = self.make(state)
            with self.subTest(state=state), self.assertRaises(CenterError):
                self.send(center)
            self.assertFalse(any(method.startswith("turn/") for method, _ in rpc.calls))

    def test_inconsistent_active_turn_or_direct_input_is_not_guessed(self):
        for change in ("no-turn", "idle-with-turn", "no-direct-input"):
            rpc, center = self.make("active")
            if change == "no-turn":
                rpc.turns = rpc.turns[:1]
            elif change == "idle-with-turn":
                rpc.state = "idle"
            else:
                rpc.accept = False
            with self.subTest(change=change), self.assertRaises(CenterError):
                self.send(center)
            self.assertFalse(any(method.startswith("turn/") for method, _ in rpc.calls))

    def test_mismatched_cwd_or_missing_skill_never_starts(self):
        for change in ("cwd", "skill"):
            rpc, center = self.make()
            rpc.wrong_cwd = change == "cwd"
            rpc.skill_enabled = change != "skill"
            with self.subTest(change=change), self.assertRaises(CenterError):
                self.send(center)
            self.assertNotIn("turn/start", [method for method, _ in rpc.calls])

    def test_rejected_send_is_not_retried_or_changed_to_start(self):
        rpc, center = self.make("active")
        rpc.fail_send = True
        with self.assertRaises(CenterError):
            self.send(center)
        self.assertEqual([method for method, _ in rpc.calls].count("turn/steer"), 1)
        self.assertNotIn("turn/start", [method for method, _ in rpc.calls])

    def test_direct_research_dependency_and_fixed_result_route(self):
        rpc, center = self.make()
        self.send(center)
        self.assertEqual(rpc.calls[-1][0], "turn/start")
        research_target = {**TARGET, "skill_path": "/test/research/SKILL.md"}
        result_center = AgentCenter(rpc, research_target, "research")
        rpc.skill_name, rpc.skill_path = "codex-strategy-research", research_target["skill_path"]
        self.send(result_center, sender="development", kind="result", issuer="research")
        self.assertEqual(rpc.calls[-1][0], "turn/start")
        self.assertIn("实际签发组：research", rpc.calls[-1][1]["input"][0]["text"])
        with self.assertRaisesRegex(CenterError, "实际签发组"):
            self.send(result_center, sender="development", kind="result", issuer="decision")

    def test_invalid_task_path_or_pr_creator_is_rejected_before_rpc(self):
        rpc, center = self.make()
        for args in ({"sender": "development"}, {"issuer": "decision"},
                     {"kind": "pr", "sender": "development"}):
            with self.subTest(args=args), self.assertRaises(CenterError):
                self.send(center, **args)
        self.assertEqual(rpc.calls, [])

    def test_repeated_cursor_reports_incomplete_history(self):
        class Repeated:
            def call(self, *args):
                return {"data": [], "nextCursor": "same"}
        with self.assertRaisesRegex(CenterError, "重复游标"):
            center_module.pages(Repeated(), "thread/list", {})

    def test_same_role_result_returns_only_to_exact_issuer(self):
        rpc = FakeRPC("active")
        target = {**TARGET, "id": "decision:A", "role": "decision"}
        center = AgentCenter(rpc, target, "decision")
        sender = {"id": "decision:B", "role": "decision"}
        with self.assertRaisesRegex(CenterError, "准确参与者"):
            self.send(center, sender=sender, kind="result", issuer=sender)
        self.assertEqual(rpc.calls, [])
        self.send(center, sender=sender, kind="result", issuer=target,
                  reply_to="https://github.com/example/project/issues/7#issuecomment-5")
        self.assertEqual(rpc.calls[-1][0], "turn/steer")
        text = rpc.calls[-1][1]["input"][0]["text"]
        self.assertIn("实际签发组：decision:A", text)
        self.assertIn("目标参与者：decision:A", text)
        self.assertIn("#issuecomment-5", text)

    def test_peer_request_is_not_an_engineering_task(self):
        rpc = FakeRPC("active")
        target = {**TARGET, "id": "decision:B", "role": "decision"}
        center = AgentCenter(rpc, target, "decision")
        source = {"id": "decision:A", "role": "decision"}
        with self.assertRaisesRegex(CenterError, "request"):
            self.send(center, sender=source, issuer=source)
        self.assertEqual(rpc.calls, [])
        self.send(center, sender=source, issuer=source, kind="request")
        self.assertEqual(rpc.calls[-1][0], "turn/steer")

    def test_pr_must_come_from_exact_issuer_not_same_role_peer(self):
        rpc, center = self.make("active")
        with self.assertRaisesRegex(CenterError, "准确参与者"):
            self.send(center, sender={"id": "research:A", "role": "research"}, kind="pr",
                      issuer={"id": "research:B", "role": "research"})
        self.assertEqual(rpc.calls, [])


class ParticipantCLITests(unittest.TestCase):
    def setUp(self):
        self.target = {"id": "decision:B", "role": "decision", "transport": "github",
                       "anchor": "https://github.com/example/project/issues/8"}
        self.source = {"id": "decision:A", "role": "decision", "transport": "github",
                       "anchor": "https://github.com/example/project/issues/7"}
        self.binding = {"participants": {
            item["id"]: {key: value for key, value in item.items() if key != "id"}
            for item in (self.target, self.source)
        }}

    def test_github_inspection_needs_no_socket_cli_or_skill(self):
        backend = types.ModuleType("github_transport")
        backend.GitHubCenter = unittest.mock.Mock()
        backend.GitHubCenter.return_value.inspect.return_value = {"messages": []}
        with patch.object(center_module, "read_binding", return_value=self.binding), \
                patch.object(center_module, "JsonRpcProxy") as proxy, \
                patch.dict(sys.modules, {"github_transport": backend}), \
                patch("sys.stdout", new_callable=io.StringIO):
            code = center_module.main(["inspect", "--target", "decision:B", "--since", "2026-01-01T00:00:00Z",
                                       "--module", "模块:公共链路", "--module", "模块:ledger"])
        self.assertEqual(code, 0)
        proxy.assert_not_called()
        backend.GitHubCenter.assert_called_once_with(self.binding, self.target, self.target)
        backend.GitHubCenter.return_value.inspect.assert_called_once_with(
            issue=None, since="2026-01-01T00:00:00Z", modules=["模块:公共链路", "模块:ledger"])

    def test_github_request_uses_precise_actor_and_default_issuer(self):
        backend = types.ModuleType("github_transport")
        backend.GitHubCenter = unittest.mock.Mock()
        backend.GitHubCenter.return_value.send.return_value = {"publication": "published"}
        with tempfile.TemporaryDirectory() as directory:
            message = Path(directory) / "message.md"
            message.write_text("请核对公共模块。")
            with patch.object(center_module, "read_binding", return_value=self.binding), \
                    patch.object(center_module, "JsonRpcProxy") as proxy, \
                    patch.dict(sys.modules, {"github_transport": backend}), \
                    patch("sys.stdout", new_callable=io.StringIO):
                code = center_module.main(["send", "--target", "decision:B", "--sender", "decision:A",
                                           "--kind", "request", "--issue", self.source["anchor"],
                                           "--message-file", str(message), "--reply-to", "original-message"])
        self.assertEqual(code, 0)
        proxy.assert_not_called()
        backend.GitHubCenter.assert_called_once_with(self.binding, self.target, self.source)
        backend.GitHubCenter.return_value.send.assert_called_once_with(
            self.source, "request", self.source, self.source["anchor"], "请核对公共模块。", reply_to="original-message")

    def test_github_override_reads_local_participant_without_rpc_or_binding_changes(self):
        binding = copy.deepcopy(self.binding)
        binding["participants"]["decision:B"]["transport"] = "agent-center"
        original = copy.deepcopy(binding)
        backend = types.ModuleType("github_transport")
        backend.GitHubCenter = unittest.mock.Mock()
        backend.GitHubCenter.return_value.inspect.return_value = {"messages": []}
        with patch.object(center_module, "read_binding", return_value=binding), \
                patch.object(center_module, "JsonRpcProxy") as proxy, \
                patch.dict(sys.modules, {"github_transport": backend}), \
                patch("sys.stdout", new_callable=io.StringIO):
            code = center_module.main(["inspect", "--target", "decision:B", "--transport", "github"])
        self.assertEqual(code, 0)
        self.assertEqual(binding, original)
        proxy.assert_not_called()
        backend.GitHubCenter.assert_called_once_with(binding, self.target, self.target)

    def test_github_override_publishes_to_local_target_as_same_participant(self):
        binding = copy.deepcopy(self.binding)
        binding["participants"]["decision:B"]["transport"] = "agent-center"
        original = copy.deepcopy(binding)
        backend = types.ModuleType("github_transport")
        backend.GitHubCenter = unittest.mock.Mock()
        backend.GitHubCenter.return_value.send.return_value = {"publication": "published"}
        with tempfile.TemporaryDirectory() as directory:
            message = Path(directory) / "message.md"
            message.write_text("先发布持久请求。")
            with patch.object(center_module, "read_binding", return_value=binding), \
                    patch.object(center_module, "JsonRpcProxy") as proxy, \
                    patch.dict(sys.modules, {"github_transport": backend}), \
                    patch("sys.stdout", new_callable=io.StringIO):
                code = center_module.main(["send", "--target", "decision:B", "--transport", "github",
                                           "--sender", "decision:A", "--kind", "request", "--issue",
                                           self.source["anchor"], "--message-file", str(message)])
        self.assertEqual(code, 0)
        self.assertEqual(binding, original)
        proxy.assert_not_called()
        backend.GitHubCenter.assert_called_once_with(binding, self.target, self.source)
        self.assertEqual(backend.GitHubCenter.return_value.send.call_args.args[0]["id"], "decision:A")

    def test_ack_requires_original_issuer_and_message_link_before_transport(self):
        base = ["send", "--target", "decision:B", "--sender", "decision:A", "--kind", "ack",
                "--issue", self.source["anchor"], "--message-file", "unused.md"]
        for extra, expected in (([], "明确 issuer"), (["--issuer", "decision:B"], "reply-to")):
            with self.subTest(extra=extra), patch.object(center_module, "JsonRpcProxy") as proxy, \
                    patch("sys.stderr", new_callable=io.StringIO) as errors, \
                    self.assertRaises(SystemExit) as error:
                center_module.main(base + extra)
            self.assertEqual(error.exception.code, 2)
            self.assertIn(expected, errors.getvalue())
            proxy.assert_not_called()

    def test_ambiguous_role_does_not_open_any_transport(self):
        with patch.object(center_module, "read_binding", return_value=self.binding), \
                patch.object(center_module, "JsonRpcProxy") as proxy, \
                patch("sys.stderr", new_callable=io.StringIO) as errors:
            code = center_module.main(["inspect", "--target", "decision"])
        self.assertEqual(code, 1)
        self.assertIn("多个实例", errors.getvalue())
        proxy.assert_not_called()

    def test_legacy_cli_does_not_require_sender_thread_binding(self):
        binding = {"agent_center": {"cli": "/test/codex"}, "targets": {"development": TARGET}}
        rpc = FakeRPC()
        with tempfile.TemporaryDirectory() as directory:
            message = Path(directory) / "message.md"
            message.write_text("准确任务合同。")
            with patch.object(center_module, "read_binding", return_value=binding), \
                    patch.object(center_module, "JsonRpcProxy") as proxy, \
                    patch("sys.stdout", new_callable=io.StringIO):
                proxy.return_value.__enter__.return_value = rpc
                code = center_module.main(["send", "--target", "development", "--sender", "decision",
                                           "--kind", "task", "--issuer", "decision", "--issue",
                                           "https://github.com/example/project/issues/7", "--message-file", str(message)])
        self.assertEqual(code, 0)
        self.assertEqual(rpc.calls[-1][0], "turn/start")


class ProxyTests(unittest.TestCase):
    def test_proxy_transport_handles_notifications_unicode_and_handshake(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "codex"
            cli.write_text('''#!/usr/bin/env python3
import json,sys
assert sys.argv[1:]==['app-server','proxy','--sock','/test/socket']
initialized=False
for line in sys.stdin:
    req=json.loads(line)
    if req['method']=='initialized':
        initialized=True
        continue
    if req['method']!='initialize':
        assert initialized
    print(json.dumps({'method':'notice','params':{'text':'中文通知'}}),flush=True)
    print(json.dumps({'id':req['id'],'result':{'echo':req['method']}}),flush=True)
''')
            cli.chmod(0o755)
            with center_module.JsonRpcProxy(str(cli), "/test/socket") as rpc:
                self.assertEqual(rpc.call("thread/read", {"threadId": "test"}), {"echo": "thread/read"})
            self.assertIsNotNone(rpc.process.poll())

    def test_transport_failure_does_not_repeat_a_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "codex"
            log = Path(directory) / "requests"
            cli.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
for line in sys.stdin:
    req=json.loads(line)
    with Path(__file__).with_name('requests').open('a') as f: f.write(req['method']+'\\n')
    if req['method']=='initialized': continue
    if req['method']=='turn/start': sys.exit(0)
    print(json.dumps({'id':req['id'],'result':{}}),flush=True)
''')
            cli.chmod(0o755)
            with center_module.JsonRpcProxy(str(cli), "/test/socket") as rpc:
                with self.assertRaisesRegex(CenterError, "结果未确认"):
                    rpc.call("turn/start", {"threadId": "test", "input": []})
            self.assertEqual(log.read_text().splitlines().count("turn/start"), 1)
