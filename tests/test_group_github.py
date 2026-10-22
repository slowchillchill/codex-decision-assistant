import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE = Path(os.environ.get("GOVERNANCE_SOURCE_ROOT", ROOT.parent / "codex-governance"))
spec = importlib.util.spec_from_file_location("group_github", GOVERNANCE / "scripts/group_github.py")
github_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(github_module)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "GH_TOKEN": "inherited-wrong-token", "GITHUB_TOKEN": "another-wrong-token",
                    "GH_CONFIG_DIR": "/wrong/account", "GH_HOST": "wrong.example",
                    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper",
                    "GIT_CONFIG_VALUE_0": "wrong-helper", "FAKE_LOG": str(self.root / "calls")}
        self.env["GIT_ASKPASS"] = "/wrong/askpass"
        self.env["SSH_ASKPASS"] = "/wrong/ssh-askpass"
        for group, (variable, _) in github_module.BINDINGS.items():
            directory = self.root / (group + " account")
            directory.mkdir()
            binding = self.root / (group + ".toml")
            binding.write_text(f'[github.{group}]\nlogin = "{group}-user"\nconfig_dir = {json.dumps(str(directory))}\n')
            self.env[variable] = str(binding)
        gh = self.bin / "gh"
        gh.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
group=Path(os.environ['GH_CONFIG_DIR']).name.split()[0]
record={'tool':'gh','args':sys.argv[1:],'group':group,'host':os.environ['GH_HOST'],
        'tokens_present':any(k in os.environ for k in ['GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN'])}
with open(os.environ['FAKE_LOG'],'a') as f: f.write(json.dumps(record)+'\\n')
if sys.argv[1:3]==['api','user']:
    print(os.environ.get('FAKE_LOGIN',group+'-user'))
else: print(json.dumps(record))
''')
        gh.chmod(0o755)
        git = self.bin / "git"
        git.write_text('''#!/usr/bin/env python3
import json,os,sys
record={'tool':'git','args':sys.argv[1:],'directory':os.environ['GH_CONFIG_DIR'],
        'tokens_present':any(k in os.environ for k in ['GH_TOKEN','GITHUB_TOKEN']),
        'git_config_present':any(k.startswith('GIT_CONFIG_') for k in os.environ),
        'askpass_present':any(k in os.environ for k in ['GIT_ASKPASS','SSH_ASKPASS'])}
with open(os.environ['FAKE_LOG'],'a') as f: f.write(json.dumps(record)+'\\n')
if 'get-url' in sys.argv: print(os.environ.get('FAKE_REMOTE_URL','https://github.com/example/project.git'))
elif '--name-only' in sys.argv: print('http.https://github.com/example/project.git.extraheader')
else: print(json.dumps(record))
''')
        git.chmod(0o755)

    def records(self):
        path = self.root / "calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_gh(self, group, args):
        return github_module.GroupGitHub(group, self.env).gh(args, capture_output=True, text=True)

    def test_each_group_excludes_inherited_tokens_and_uses_own_binding(self):
        for group in github_module.BINDINGS:
            result = self.run_gh(group, ["issue", "view", "7", "--repo", "example/project"])
            data = json.loads(result.stdout)
            self.assertEqual(data["group"], group)
            self.assertFalse(data["tokens_present"])
            self.assertEqual(data["host"], "github.com")
        self.assertEqual(self.env["GH_CONFIG_DIR"], "/wrong/account")
        self.assertEqual(self.env["GH_TOKEN"], "inherited-wrong-token")

    def test_mismatched_login_stops_before_target_command(self):
        self.env["FAKE_LOGIN"] = "other-user"
        with self.assertRaisesRegex(github_module.IdentityError, "核验失败"):
            self.run_gh("development", ["pr", "merge", "9"])
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]["args"][:2], ["api", "user"])

    def test_missing_research_binding_does_not_fall_back_to_controller(self):
        Path(self.env["CODEX_STRATEGY_RESEARCH_CONFIG"]).write_text('[github.controller]\nlogin="old"\nconfig_dir="/old"\n')
        with self.assertRaisesRegex(github_module.IdentityError, "绑定"):
            self.run_gh("research", ["issue", "list"])
        self.assertEqual(self.records(), [])

    def test_development_issues_projects_review_update_and_merge_use_same_identity(self):
        commands = [
            ["issue", "comment", "7", "--body", "验证结果"],
            ["project", "item-edit", "--id", "item", "--field-id", "field"],
            ["pr", "edit", "9", "--title", "实现当前范围"],
            ["api", "repos/example/project/pulls/9/reviews", "--method", "POST", "--input", "-"],
            ["pr", "merge", "9", "--merge", "--match-head-commit", "a" * 40],
        ]
        for command in commands:
            data = json.loads(self.run_gh("development", command).stdout)
            self.assertEqual(data["group"], "development")
            self.assertEqual(data["args"], command)

    def test_only_signing_groups_create_pr_and_preserve_body_file(self):
        body = self.root / "pr body.md"
        body.write_text('Refs #7\n\n实际验证结果。\n字面 $(command) 与 `value`。\n')
        args = ["pr", "create", "--base", "main", "--head", "task", "--body-file", str(body)]
        for signer in ("decision", "research"):
            data = json.loads(self.run_gh(signer, args).stdout)
            self.assertEqual(data["group"], signer)
            self.assertEqual(data["args"], args)
        count = len(self.records())
        with self.assertRaisesRegex(github_module.IdentityError, "签发组"):
            self.run_gh("development", args)
        self.assertEqual(len(self.records()), count)
        self.assertIn("$(command)", body.read_text())

    def test_git_push_clears_token_and_config_injection_and_selects_gh_helper(self):
        identity = github_module.GroupGitHub("development", self.env)
        result = identity.git(self.root, "origin", "push", ["--set-upstream", "refs/heads/task"],
                              capture_output=True, text=True)
        data = json.loads(result.stdout)
        self.assertFalse(data["tokens_present"])
        self.assertFalse(data["git_config_present"])
        self.assertFalse(data["askpass_present"])
        self.assertEqual(data["args"][-4:], ["push", "origin", "--set-upstream", "refs/heads/task"])
        self.assertIn("credential.helper=!gh auth git-credential", data["args"])
        self.assertIn("credential.helper=", data["args"])
        self.assertIn("http.https://github.com/example/project.git.extraheader=", data["args"])
        self.assertEqual(Path(data["directory"]).name, "development account")

    def test_research_cannot_push_business_git(self):
        with self.assertRaisesRegex(github_module.IdentityError, "开发组"):
            github_module.GroupGitHub("research", self.env).git(self.root, "origin", "push", [])
        self.assertEqual(self.records(), [])

    def test_real_git_missing_ref_keeps_exit_code_two_with_options_before_remote(self):
        real_git = shutil.which("git")
        real_run = subprocess.run
        bare = self.root / "empty-remote.git"
        real_run([real_git, "init", "--bare", "-q", str(bare)], check=True)

        def execute(command, **kwargs):
            if command[0] == "git" and "ls-remote" in command:
                # 只替换传输目的地；被测脚本决定选项与远端的相对位置。
                command = command.copy()
                command[0] = real_git
                command[command.index("origin")] = str(bare)
            return real_run(command, **kwargs)

        with patch.object(github_module.subprocess, "run", side_effect=execute):
            result = github_module.GroupGitHub("development", self.env).git(
                self.root, "origin", "ls-remote", ["refs/heads/missing"],
                git_options=["--exit-code", "--refs"], capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_unverifiable_transport_does_not_run_remote_operation(self):
        self.env["FAKE_REMOTE_URL"] = "git@github.com:example/project.git"
        with self.assertRaisesRegex(github_module.IdentityError, "HTTPS"):
            github_module.GroupGitHub("development", self.env).git(self.root, "origin", "push", ["task"])
        self.assertEqual(len(self.records()), 2)
        self.assertTrue(all(record["tool"] == "gh" or "get-url" in record["args"] for record in self.records()))

    def test_configuration_path_handles_whitespace_override_and_xdg(self):
        self.assertEqual(github_module.binding_path("research", {"CODEX_STRATEGY_RESEARCH_CONFIG": "  ",
                         "XDG_CONFIG_HOME": "/test/config"}), Path("/test/config/codex-strategy-research/local.toml"))

    def test_auth_switch_is_not_performed(self):
        with self.assertRaises(github_module.IdentityError):
            self.run_gh("development", ["auth", "switch", "--user", "other"])
        self.assertEqual(self.records(), [])
