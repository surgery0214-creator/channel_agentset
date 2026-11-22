"""Edits to native BBchannel turn lists. All public positions are one-based."""

import re

from .core import Choose, FRIEND_WORDS, NUMBERS, POSITION, PlanError, choose_slot, norm, read_json, roster


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


def swap_slots(action):
    if isinstance(action, list) and len(action) == 3 and 10 <= code(action) <= 12:
        if all(type(i) is int for i in action[1:]):
            front, back = sorted(action[1:])
            if 1 <= front <= 3 < back <= 6:
                return front, back
        raise PlanError("预设的换人动作站位无效。")


def barrier(action):
    if not 1 <= code(action) <= 12:
        return True
    return isinstance(action, list) and (len(action) != 2 or type(action[1]) is not int or not 1 <= action[1] <= 3)


def actions_for(cfg, wave, turn):
    integer(wave, 1, 3, "面数")
    integer(turn, 1, cfg.get(f"round{wave}_turns", 0), f"第{wave}面的回合")
    return cfg[f"round{wave}_turn{turn-1}_skill"]


def branch_depths(actions):
    depth = 0
    depths = [0]
    for action in actions:
        tag = marker(action)
        if tag == "Start":
            depth += 1
        elif tag == "End":
            depth -= 1
        if depth < 0:
            break
        depths.append(depth)
    if depth:
        raise PlanError("预设里的条件开始和结束标记没有配对。")
    return depths


def formation(cfg, wave, turn, stop):
    """Return original roster indices in their positions before a particular action."""
    order = list(range(6))
    for w in range(1, wave + 1):
        for t in range(1, cfg[f"round{w}_turns"] + 1):
            actions = actions_for(cfg, w, t)
            depth = branch_depths(actions)
            last = (w, t) == (wave, turn)
            for index, action in enumerate(actions[:stop] if last else actions):
                slots = swap_slots(action)
                if slots:
                    if depth[index]:
                        raise PlanError("之前有条件换人，当前站位取决于实战分支。")
                    a, b = slots
                    order[a-1], order[b-1] = order[b-1], order[a-1]
            if last:
                return order
            if cfg.get(f"round{w}_turn{t-1}_replace"):
                raise PlanError("前一回合有退场补位记录，后续站位请在原程序中核对。")
        if any(swap_slots(a) for a in cfg.get(f"round{w}_extraSkill", []) or []):
            raise PlanError("前一面的额外回合可能换人，当前站位无法确定。")


def project(cfg, order):
    party = roster(cfg)
    return {**{f"servant_{i}_name": party[j] for i, j in enumerate(order)},
            "assistIdx": order.index(cfg["assistIdx"]) if cfg.get("assistIdx") in order else -1}


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
        swap = swap_slots(action)
        if swap:
            return f"换人（{slot(swap[0])} ↔ {slot(swap[1])}）"
        if isinstance(action, list) and len(action) != 2:
            return f"原动作{action}"
        who = slot((n-1)//3+1) if n <= 9 else "御主"
        target = f"给{slot(action[1])}" if isinstance(action, list) else ""
        return f"{who}{(n-1)%3+1}技能{target}"
    return f"原动作{action}"


def master_catalog(lib):
    path = lib.root / "master_info.json"
    if not path.is_file():
        raise PlanError("安装目录缺少御主礼装资料 master_info.json。")
    return read_json(path)


def equip(lib, cfg, name, pick):
    catalog = master_catalog(lib)
    if not isinstance(name, str) or not name.strip():
        raise PlanError("请指定御主礼装的名称。")
    matches = [n for n, entry in catalog.items() if
               (name == "换人服" and 2 in entry["skill_type"]) or norm(name) in norm(n)]
    if name == "换人服":
        equipped = next((n for n in matches if catalog[n]["SN"] == cfg.get("master_equip")), None)
        if equipped:
            return f"使用{equipped}"
    if name not in catalog:
        if not matches:
            raise PlanError(f"没有找到御主礼装「{name}」。")
        name = matches[0] if len(matches) == 1 else pick(Choose("请选择御主礼装。", matches))
    new = catalog[name]
    old = next((v for v in catalog.values() if v["SN"] == cfg.get("master_equip")), None)
    if old != new:
        for key, actions in cfg.items():
            if not re.fullmatch(r"round\d+_(?:turn\d+_skill|extraSkill)", key):
                continue
            for a in actions or ():
                n = code(a)
                if 10 <= n <= 12 and (not old or any(old[k][n-10] != new[k][n-10] for k in ("skill_type", "skill_name"))):
                    raise PlanError(f"已有御主{n-9}技能会改变含义，请先移除对应动作再更换礼装。")
    cfg["master_equip"] = new["SN"]
    return f"御主礼装为{name}"


def check_inherited_skill(lib, party, action, location):
    n = code(action)
    if not 1 <= n <= 9 or barrier(action):
        return
    slot, skill = divmod(n-1, 3)
    actor = roster(party)[slot]
    if not actor:
        raise PlanError(f"换人后{location}的施法位为空。")
    entry = lib.data[actor]
    target_kinds = {entry["skill_type"][skill], entry.get("skill_type_super", entry["skill_type"])[skill]}
    if target_kinds != {int(isinstance(action, list))}:
        raise PlanError(f"换人后{location}与{actor}的技能目标不符，请先调整原动作。")


def check_swap_suffix(lib, cfg, wave, turn):
    """Check preserved actions against the final draft after all edits have been applied."""
    for w in range(wave, 4):
        turns = cfg[f"round{w}_turns"]
        for t in range(turn if w == wave else 1, turns + 1):
            if cfg.get(f"round{w}_turn{t-1}_replace"):
                raise PlanError("后续有依赖原阵容的退场补位记录，请在原程序中调整换人。")
            for index, action in enumerate(actions_for(cfg, w, t)):
                if 1 <= code(action) <= 9 and not barrier(action):
                    party = project(cfg, formation(cfg, w, t, index))
                    check_inherited_skill(lib, party, action, f"第{w}面第{t}回合第{index+1}项")
        extra = cfg.get(f"round{w}_extraSkill") or []
        if extra:
            if not turns or any(swap_slots(a) for a in extra):
                raise PlanError("额外回合的换人站位无法确定，请在原程序中核对。")
            party = project(cfg, formation(cfg, w, turns, len(actions_for(cfg, w, turns))))
            for index, action in enumerate(extra, 1):
                check_inherited_skill(lib, party, action, f"第{w}面额外回合第{index}项")


class TurnEditor:
    def __init__(self, lib, cfg, wave, turn, pick, player_text):
        self.lib, self.cfg, self.pick = lib, cfg, pick
        self.wave, self.turn = wave, turn
        self.actions = actions_for(cfg, wave, turn)
        self.text = player_text
        self.inserted_swap = False
        branch_depths(self.actions)

    def at(self, index):
        return project(self.cfg, formation(self.cfg, self.wave, self.turn, index))

    def front(self, party, ref):
        if not isinstance(ref, str) or not ref.strip():
            raise PlanError("请写明从者或站位。")
        try:
            if self.text is not None:
                return self.lib.grounded_position(party, ref, self.text)
            return self.lib.position(party, ref)
        except Choose as issue:
            return self.lib.position(party, self.pick(issue))

    def back(self, party, ref):
        members = roster(party)
        raw = norm(ref).translate(NUMBERS)
        match = POSITION.fullmatch(raw)
        slots = [i for i in range(4, 7) if members[i-1]]
        if ref in FRIEND_WORDS:
            slots = [i for i in slots if i == party["assistIdx"]+1]
        elif match:
            slots = [i for i in slots if i == int(match[1]) and
                     (not match[2] or members[i-1] in self.lib.aliases.get(norm(match[2]), set()))]
        else:
            slots = [i for i in slots if members[i-1] in self.lib.aliases.get(raw, set())]
        if not slots:
            raise PlanError(f"后排没有匹配「{ref}」的从者。")
        if len(slots) > 1:
            return int(self.pick(choose_slot("请指定换入的后排。", slots, party))[0])
        return slots[0]

    def target(self, party, ref, prompt):
        if ref is None:
            ref = self.pick(choose_slot(prompt, [i for i,n in enumerate(roster(party)[:3],1) if n], party))
            return self.lib.position(party, ref)
        return self.front(party, ref)

    def encode(self, item, index):
        party = self.at(index)
        if not isinstance(item, dict):
            raise PlanError("请写明要插入的技能。")
        if "master" in item or "swap_out" in item:
            catalog = master_catalog(self.lib)
            entry = next((v for v in catalog.values() if v["SN"] == self.cfg.get("master_equip")), None)
            if not entry:
                raise PlanError("请先选择御主礼装。")
            if "swap_out" in item:
                fields(item, ("swap_out", "swap_in"))
                if 2 not in entry["skill_type"]:
                    raise PlanError("当前御主礼装没有换人技能，请先选择换人服。")
                if branch_depths(self.actions)[index]:
                    raise PlanError("条件块中的换人会使后续站位不确定，请选择条件外的位置。")
                a = self.front(party, item["swap_out"])
                b = self.back(party, item["swap_in"])
                origin = formation(self.cfg, self.wave, self.turn, index)[b-1]
                name = roster(self.cfg)[origin]
                own = next((i for i,n in enumerate(roster(self.cfg)) if n == name and i != self.cfg.get("assistIdx")), None)
                used = self.cfg.setdefault("usedServant", [])
                if origin != self.cfg.get("assistIdx") and own is not None and own not in used:
                    used.append(own)
                return [10 + entry["skill_type"].index(2), b, a]
            fields(item, ("master",), ("to",))
            n = integer(item["master"], 1, 3, "御主技能")
            kind = entry["skill_type"][n-1]
            if kind in (1, 3):
                return [9+n, self.target(party, item.get("to"), "御主技能给谁？")]
            if kind == 2:
                raise PlanError("换人技能需要指定换出的前排和换入的后排。")
            if kind != 0 or item.get("to") is not None:
                raise PlanError("请核对礼装的技能和目标。")
            return 9+n
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
        candidates = []
        actors = {}
        for i, action in enumerate(self.actions):
            n = code(action)
            if "master" in op:
                if n == 9 + integer(op["master"], 1, 3, "御主技能"):
                    candidates.append(i)
            elif 1 <= n <= 9:
                party = self.at(i)
                actor = (n-1)//3+1
                signature = (tuple(roster(party)), party["assistIdx"])
                try:
                    if signature not in actors:
                        actors[signature] = self.front(party, op["who"])
                    if actors[signature] != actor:
                        continue
                except PlanError as error:
                    if isinstance(error, Choose):
                        raise
                    continue
                names = self.lib.data[roster(party)[actor-1]]["skill_name"]
                if op["skill"] in ((n-1)%3+1, names[(n-1)%3]):
                    candidates.append(i)
        if not candidates:
            raise PlanError("这一回合没有找到指定的技能。输入「预览」可核对顺序。")
        if len(candidates) == 1:
            return candidates[0]
        labels = [f"第{i+1}项 {describe(self.actions[i], roster(self.at(i)))}" for i in candidates]
        issue = Choose("这项技能出现多次，要编辑哪一次？", labels)
        for ordinal, label in enumerate(labels, 1):
            issue.aliases.update({str(ordinal): label, f"第{ordinal}个": label, f"第{ordinal}次": label})
        return candidates[labels.index(self.pick(issue))]

    def edit(self, op):
        kind = op.get("edit")
        common = ("op", "wave", "edit")
        actions = self.actions
        if self.cfg.get(f"round{self.wave}_turn{self.turn-1}_replace"):
            raise PlanError("本回合带有按原动作计算的退场补位记录，请在原程序中编辑。")
        if kind == "np":
            fields(op, (*common, "np"), ("turn",))
            refs = op["np"]
            if not isinstance(refs, list) or len(refs) > 3:
                raise PlanError("每回合最多三个不同站位的宝具。")
            party = self.at(len(actions))
            slots = [self.front(party, ref) for ref in refs]
            if len(set(slots)) != len(slots):
                raise PlanError("同一回合不能重复指定宝具站位。")
            key = f"round{self.wave}_turn{self.turn-1}_np"
            old, self.cfg[key] = self.cfg[key], slots
            return f"宝具 {old} → {slots}"
        if kind == "insert":
            fields(op, (*common, "steps"), ("turn", "at"))
            steps = op["steps"]
            if not isinstance(steps, list) or not 1 <= len(steps) <= 32 or len(actions)+len(steps) > 32:
                raise PlanError("插入后每回合最多 32 项动作。")
            pos = integer(op.get("at", len(actions)+1), 1, len(actions)+1, "插入位置")-1
            notes = []
            for i, item in enumerate(steps, pos):
                party = roster(self.at(i))
                action = self.encode(item, i)
                actions.insert(i, action)
                notes.append(describe(action, party))
                if swap_slots(action):
                    self.inserted_swap = True
                    after = roster(self.at(i+1))
                    notes.append("换人后前排 " + "、".join(f"{j}号位{n}" for j,n in enumerate(after[:3],1) if n))
                    outgoing, _ = swap_slots(action)
                    if outgoing in self.cfg[f"round{self.wave}_turn{self.turn-1}_np"]:
                        notes.append(f"本回合原有的{outgoing}号位宝具将由{after[outgoing-1]}施放")
            return "插入 " + " → ".join(notes)

        if kind not in ("first", "last", "move", "delete", "retarget"):
            raise PlanError("请指定插入、删除、改目标或移动。")
        selector = ("index",) if "index" in op else ("master",) if "master" in op else ("who", "skill")
        extra = ("to",) if kind == "retarget" else ("at",) if kind == "move" else ()
        fields(op, (*common, *selector, *extra), ("turn",))
        index = self.locate(op)
        action = actions[index]
        if barrier(action):
            raise PlanError("该项是条件边界、换人或特殊动作，请保留其位置。")
        party = roster(self.at(index))
        before = describe(action, party)
        if kind == "delete":
            del actions[index]
            return f"删除第{index+1}项 {before}"
        if kind == "retarget":
            n = code(action)
            # The selected action already identifies the actor, including a resolved duplicate.
            if n <= 9:
                target = self.front(self.at(index), op["to"])
                updated = self.lib.skill(self.at(index), (n-1)//3+1, (n-1)%3+1, target)
            else:
                updated = self.encode({"master": n-9, "to": op["to"]}, index)
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
