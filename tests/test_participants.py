import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("participants", ROOT / "scripts/participants.py")
participants = importlib.util.module_from_spec(spec)
spec.loader.exec_module(participants)


class ParticipantsTests(unittest.TestCase):
    def test_legacy_targets_keep_single_role_selectors(self):
        binding = {"targets": {"decision": {"thread_id": "legacy"}}}
        result = participants.resolve_participant(binding, "decision")
        self.assertEqual(result, {"id": "decision", "role": "decision", "transport": "agent-center",
                                  "thread_id": "legacy"})

    def test_role_is_a_convenience_only_for_a_unique_instance(self):
        binding = {"participants": {"decision:A": {"role": "decision", "transport": "github"}}}
        self.assertEqual(participants.resolve_participant(binding, "decision")["id"], "decision:A")
        binding["participants"]["decision:B"] = {"role": "decision", "transport": "github"}
        with self.assertRaisesRegex(participants.ParticipantError, "decision:A, decision:B"):
            participants.resolve_participant(binding, "decision")
        self.assertEqual(participants.resolve_participant(binding, "decision:B")["id"], "decision:B")

    def test_legacy_role_name_does_not_hide_new_same_role_instances(self):
        binding = {"targets": {"decision": {}}, "participants": {
            "decision:B": {"role": "decision", "transport": "github"}}}
        with self.assertRaisesRegex(participants.ParticipantError, "多个实例"):
            participants.resolve_participant(binding, "decision")

    def test_roles_are_explicit_not_inferred_from_identifier(self):
        binding = {"participants": {"route-alpha-controller": {"role": "decision", "transport": "github"}}}
        self.assertEqual(participants.resolve_participant(binding, "decision")["id"], "route-alpha-controller")

    def test_each_direct_task_path_keeps_exact_issuer(self):
        for source_role, target_role in participants.TASK_ROUTES:
            source = {"id": source_role + ":A", "role": source_role}
            target = {"id": target_role + ":B", "role": target_role}
            with self.subTest(source=source_role, target=target_role):
                participants.validate_route(source, target, "task", source)
                with self.assertRaises(participants.ParticipantError):
                    participants.validate_route(source, target, "task", {**source, "id": source_role + ":C"})

    def test_same_role_peer_request_is_allowed_but_task_is_rejected(self):
        source = {"id": "decision:A", "role": "decision"}
        target = {"id": "decision:B", "role": "decision"}
        participants.validate_route(source, target, "request", source)
        with self.assertRaisesRegex(participants.ParticipantError, "request"):
            participants.validate_route(source, target, "task", source)

    def test_exact_result_route_and_pr_source(self):
        source = {"id": "decision:A", "role": "decision"}
        peer = {"id": "decision:B", "role": "decision"}
        developer = {"id": "developer:main", "role": "development"}
        participants.validate_route(peer, source, "result", source)
        participants.validate_route(source, developer, "pr", source)
        with self.assertRaises(participants.ParticipantError):
            participants.validate_route(peer, source, "result", peer)
        with self.assertRaises(participants.ParticipantError):
            participants.validate_route(peer, developer, "pr", source)
