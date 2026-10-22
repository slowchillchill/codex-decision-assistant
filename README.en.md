# Codex Decision and Three-Group Collaboration

[简体中文](README.md) | English

`codex-decision-assistant` advances a project from the first principles of its business goal. It starts with the outcome, success criteria, and hard constraints confirmed by the user. It then recovers the route's contribution, stage deliverables, authorization, budget, and bottlenecks, and coordinates research, development, and other route decision groups. The Skill starts only when explicitly invoked. Before issuing tasks, every 900 seconds, and when difficulties or stage results arrive, it checks whether the work can produce verifiable results that support the business goal.

If the goal or a necessary trade-off remains unclear after reading existing records, the decision group must ask the user for clarification. It must bring dependent work and resource use to a safe stopping point. When the goal is clear but the technical method is unknown, it validates the method within existing authorization. A route or task list cannot replace the business goal. Passing tests or finishing an experiment alone does not prove business progress; reliable negative results can inform the direction.

Within its authorization, the decision group can reorder work, pause ineffective repetition, draw conclusions from existing evidence, or arrange tests that distinguish competing explanations. Major changes to goals, acceptance criteria, or approved approaches require immediate user approval. So do budget overruns, the same action exceeding 24 hours, fork/flush, and matters reserved by the original contract. Failure history, budget use, and stop conditions carry across tasks and sessions.

The sole shared definition of the three groups' responsibilities is `governance/group-collaboration.md` in the governance source. All development roles use one development account; research and decision groups each use their own account. Real accounts and directories remain in each group's private bindings. Research can directly issue necessary engineering dependencies within its current authorization, and results return directly to research. The actual issuing group creates the PR. Development maintains it, performs independent review, submits formal approval, completes an ordinary merge, and closes out the work.

Each route has one decision group. Logical participants, roles, and runtime environments are declared separately. Across environments, groups collaborate through comments on the source GitHub task and cross-references from route entry points. Agent Center provides immediate handoffs when the target environment is accessible. Each shared task has one lead route, while each consumer route decides whether to adopt the result. The original requester, shared-record maintainer, and actual engineering issuer retain their own responsibilities. An offline group processes records when it next starts; silence means neither acceptance nor stopped work.

| Path | Purpose |
| --- | --- |
| [Skill](skills/codex-decision-assistant/SKILL.md) | Explicit invocation, rule discovery, and required boundaries |
| [Decision procedure](decision/decision-workflow.md) | Recover goals, supervise, correct course, issue tasks, and check results |
| [Single adapter](scripts/agent_center.py) | Select GitHub or Agent Center by participant and paginate records and execution evidence |
| [Cross-route protocol](docs/cross-route-protocol.md) | Source-task messages, acceptance decisions, results, and recovery across environments |
| [Project reference](docs/projects/btc-updown-marketmaker.md) | Recover A/B/C entry points and shared-record responsibilities when this project is explicitly selected |
| [Configuration templates](templates/) | Placeholder examples for private decision identity, participant, and channel bindings |
| [Installation](docs/installation.md) | Complete sources, identity migration, and enablement steps |
| [Validation](evaluations/validation.md) | Scope and results of local protocol tests and independent behavior evaluations |

After starting a normal session, select this Skill in the client and provide a specific stage request:

```text
$codex-decision-assistant
Advance the approved stage for this route in the current project. First recover the user-confirmed goal, success criteria, hard constraints, exact Issues, designated Project, budget, and bottlenecks.
Ask me to clarify any unclear goal or necessary trade-off. Recover cross-route requests, shared changes, and applicable results from existing GitHub records.
Directly issue research or development tasks within authorization. Supervise and correct course during the current execution turn. Report user-reserved matters immediately.
```

Tasks, progress, and decisions stay in the project's existing Issues, Projects, and decision records. No local task ledger is created. Supervision runs only during the current execution turn and ends with the final reply; no background service is installed. Development handles business source code. Research writes only to its assigned directories outside the business repository.

Local validation uses the Python 3.11+ standard library and simulated CLIs. It does not connect to real accounts or threads:

```bash
python3 -m unittest discover -s tests -v
```

By default, cross-repository identity tests read the identity script from the adjacent `codex-governance` checkout. For other layouts, set the test variable `GOVERNANCE_SOURCE_ROOT`. Real permissions, collaborator invitations, thread bindings, and live integration are completed during enablement. Local simulations do not prove that these conditions are ready.
