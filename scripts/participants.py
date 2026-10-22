"""解析逻辑参与者；职责、路线身份和传输通道分别保留。"""


ROLES = {"decision", "research", "development"}
TRANSPORTS = {"agent-center", "github"}
TASK_ROUTES = {("decision", "development"), ("decision", "research"), ("research", "development")}
KINDS = {"task", "query", "correction", "result", "pr", "request", "notice", "ack"}


class ParticipantError(RuntimeError):
    pass


def configured_participants(binding):
    """旧 targets 继续作为同名参与者，新 participants 允许同职责多实例。"""
    participants = []
    for identifier, value in binding.get("participants", {}).items():
        participant = {**value, "id": identifier}
        if participant.get("role") not in ROLES:
            raise ParticipantError(f"参与者 {identifier} 缺少有效 role")
        if participant.get("transport") not in TRANSPORTS:
            raise ParticipantError(f"参与者 {identifier} 缺少有效 transport")
        participants.append(participant)
    identifiers = {participant["id"] for participant in participants}
    for role, value in binding.get("targets", {}).items():
        if role not in ROLES:
            raise ParticipantError(f"旧目标 {role} 不是已知职责")
        if role in identifiers:
            raise ParticipantError(f"参与者 {role} 同时出现在 participants 与 targets；请明确绑定来源")
        participants.append({**value, "id": role, "role": role, "transport": "agent-center"})
    return participants


def resolve_participant(binding, selector):
    participants = configured_participants(binding)
    # 角色选择器必须唯一；即使旧目标恰好使用角色名，也不能掩盖其他同类实例。
    if selector in ROLES:
        matches = [participant for participant in participants if participant["role"] == selector]
    else:
        matches = [participant for participant in participants if participant["id"] == selector]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ParticipantError(f"找不到参与者 {selector} 的私有绑定")
    choices = ", ".join(participant["id"] for participant in matches)
    raise ParticipantError(f"参与者 {selector} 存在多个实例，请使用准确 id：{choices}")


def as_participant(value):
    """保留 AgentCenter 旧的角色字符串调用接口。"""
    if isinstance(value, str) and value in ROLES:
        return {"id": value, "role": value}
    if isinstance(value, dict) and value.get("id") and value.get("role") in ROLES:
        return value
    raise ParticipantError("消息来源、目标及签发方须使用准确参与者")


def validate_route(sender, target, kind, issuer):
    sender, target, issuer = map(as_participant, (sender, target, issuer))
    if kind not in KINDS:
        raise ParticipantError(f"未知消息类型：{kind}")
    if kind == "task" and ((sender["role"], target["role"]) not in TASK_ROUTES
                           or issuer["id"] != sender["id"]):
        raise ParticipantError("任务不属于已批准的直接签发路径；跨决策组请求使用 request")
    if kind == "result" and target["id"] != issuer["id"]:
        raise ParticipantError("结果必须回传实际签发组的准确参与者")
    if kind == "pr" and (target["role"] != "development" or sender["id"] != issuer["id"]):
        raise ParticipantError("PR 链接由实际签发组的准确参与者回传开发组")
