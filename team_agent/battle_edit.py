"""Edits to native BBchannel turn lists. All public positions are one-based."""

from .core import Choose, PlanError, roster


def fields(obj, required, optional=()):
    if not isinstance(obj, dict) or not set(required) <= obj.keys() or obj.keys() - set(required) - set(optional):
        raise PlanError("精确编辑指令的字段有误，请说明回合、动作和目标。")


def integer(value, start, end, description):
    if type(value) is not int or not start <= value <= end:
        raise PlanError(f"{description}应在 {start} 至 {end} 之间。")
    return value


def code(action):
    value = action[0] if isinstance(action, list) and action else action
    return value if type(value) is int else 0


def marker(action):
    return action[0] if isinstance(action, list) and action and isinstance(action[0], str) else ""


def barrier(action):
    if not 1 <= code(action) <= 12:
        return True
    return isinstance(action, list) and (len(action) != 2 or type(action[1]) is not int or not 1 <= action[1] <= 3)


def actions_for(cfg, wave, turn):
    integer(wave, 1, 3, "面数")
    integer(turn, 1, cfg.get(f"round{wave}_turns", 0), f"第{wave}面的回合")
    return cfg[f"round{wave}_turn{turn-1}_skill"]


def check_turn(cfg, wave, turn, actions):
    if any(roster(cfg)[3:]):
        raise PlanError("精确编辑需要固定前排。含后排的预设可在原程序中编辑。")
    for w in range(1, wave + 1):
        turns = cfg[f"round{w}_turns"] if w < wave else turn
        if any(cfg.get(f"round{w}_turn{t}_replace") for t in range(turns)):
            raise PlanError("预设有退场补位记录，站位请在原程序中核对。")
    depth = 0
    for action in actions:
        if marker(action) == "Start":
            depth += 1
        elif marker(action) == "End":
            depth -= 1
        if depth < 0:
            break
    if depth:
        raise PlanError("预设里的条件开始和结束标记没有配对。")


def describe(action, party=None):
    def slot(i):
        if party and type(i) is int and 1 <= i <= 6 and party[i-1]:
            return f"{i}号位{party[i-1]}"
        return f"{i}号位"

    tag = marker(action)
    if tag == "Start":
        return f"条件开始 {action[1:]}"
    if tag == "End":
        return "条件结束"
    n = code(action)
    if 1 <= n <= 12:
        if isinstance(action, list) and len(action) != 2:
            return f"原动作{action}"
        who = slot((n-1)//3+1) if n <= 9 else "御主"
        target = f"给{slot(action[1])}" if isinstance(action, list) else ""
        return f"{who}{(n-1)%3+1}技能{target}"
    return f"原动作{action}"


class TurnEditor:
    def __init__(self, lib, cfg, wave, turn, pick, player_text):
        self.lib, self.cfg, self.pick = lib, cfg, pick
        self.actions = actions_for(cfg, wave, turn)
        self.text = player_text
        check_turn(cfg, wave, turn, self.actions)

    def front(self, party, ref):
        if not isinstance(ref, str) or not ref.strip():
            raise PlanError("请写明从者或站位。")
        try:
            if self.text is not None:
                return self.lib.grounded_position(party, ref, self.text)
            return self.lib.position(party, ref)
        except Choose as issue:
            return self.lib.position(party, self.pick(issue))

    def encode(self, item):
        party = self.cfg
        fields(item, ("who", "skill"), ("to",))
        actor = self.front(party, item["who"])
        target = self.front(party, item["to"]) if item.get("to") is not None else None
        try:
            return self.lib.skill(party, actor, item["skill"], target)
        except Choose as issue:
            return self.lib.skill(party, actor, item["skill"], self.lib.position(party, self.pick(issue)))

    def locate(self, op):
        if "index" in op:
            return integer(op["index"], 1, len(self.actions), "动作序号")-1
        actor = self.front(self.cfg, op["who"])
        skills = self.lib.data[roster(self.cfg)[actor-1]]["skill_name"]
        candidates = []
        for i, action in enumerate(self.actions):
            n = code(action)
            if not 1 <= n <= 9 or (n-1)//3+1 != actor:
                continue
            if op["skill"] in ((n-1)%3+1, skills[(n-1)%3]):
                candidates.append(i)
        if not candidates:
            raise PlanError("这一回合没有找到指定的技能。输入「预览」可核对顺序。")
        if len(candidates) == 1:
            return candidates[0]
        labels = [f"第{i+1}项 {describe(self.actions[i], roster(self.cfg))}" for i in candidates]
        issue = Choose("这项技能出现多次，要编辑哪一次？", labels)
        for ordinal, label in enumerate(labels, 1):
            issue.aliases.update({str(ordinal): label, f"第{ordinal}个": label, f"第{ordinal}次": label})
        return candidates[labels.index(self.pick(issue))]

    def edit(self, op):
        kind = op.get("edit")
        common = ("op", "wave", "edit")
        actions = self.actions
        if kind == "insert":
            fields(op, (*common, "steps"), ("turn", "at"))
            steps = op["steps"]
            if not isinstance(steps, list) or not 1 <= len(steps) <= 32 or len(actions)+len(steps) > 32:
                raise PlanError("插入后每回合最多 32 项动作。")
            pos = integer(op.get("at", len(actions)+1), 1, len(actions)+1, "插入位置")-1
            notes = []
            for i, item in enumerate(steps, pos):
                party = roster(self.cfg)
                action = self.encode(item)
                actions.insert(i, action)
                notes.append(describe(action, party))
            return "插入 " + " → ".join(notes)

        if kind not in ("first", "last", "move", "delete", "retarget"):
            raise PlanError("请指定插入、删除、改目标或移动。")
        selector = ("index",) if "index" in op else ("who", "skill")
        extra = ("to",) if kind == "retarget" else ("at",) if kind == "move" else ()
        fields(op, (*common, *selector, *extra), ("turn",))
        index = self.locate(op)
        action = actions[index]
        if barrier(action):
            raise PlanError("该项是条件边界、换人或特殊动作，请保留其位置。")
        party = roster(self.cfg)
        before = describe(action, party)
        if kind == "delete":
            del actions[index]
            return f"删除第{index+1}项 {before}"
        if kind == "retarget":
            n = code(action)
            # The selected action already identifies the actor, including a resolved duplicate.
            if n <= 9:
                target = self.front(self.cfg, op["to"])
                updated = self.lib.skill(self.cfg, (n-1)//3+1, (n-1)%3+1, target)
            else:
                if not isinstance(action, list):
                    raise PlanError("原动作没有队友目标。")
                updated = [n, self.front(self.cfg, op["to"])]
            actions[index] = updated
            return f"第{index+1}项 {before} → {describe(updated, party)}"
        lo = hi = index
        while lo and not barrier(actions[lo-1]):
            lo -= 1
        while hi+1 < len(actions) and not barrier(actions[hi+1]):
            hi += 1
        dest = lo if kind == "first" else hi if kind == "last" else integer(op["at"], 1, len(actions), "移到位置")-1
        if not lo <= dest <= hi:
            raise PlanError("这次移动会跨过条件边界、换人或特殊动作。")
        actions.insert(dest, actions.pop(index))
        return f"{before} 从第{index+1}项移到第{dest+1}项"
