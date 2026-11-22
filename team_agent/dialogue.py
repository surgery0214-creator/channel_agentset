"""Chinese dialogue and transactional edits shared by the desktop and CLI."""

import copy
import re

from .collection import PresetFinder, collection_request
from .battle_edit import TurnEditor, actions_for, check_swap_suffix, describe, equip, formation, integer, project
from .edit_language import precise_plan
from .core import Choose, NUMBERS, PlanError, choose_slot, editable_wave, export_preset, norm, preview, roster


HELP = """可以直接这样说
• 我有公主和一只光狐，能用哪套？
• 登记从者：公主、光狐　／　可借助战：光狐
• 匹配预设　／　用第一套
• 用公主双光狐队。第二面只让三号位光狐开一技能给公主，然后公主开宝具。
• 第二面追加二号位光狐三技能给公主
• 把刚才的充能挪到第三面
• 第三面只改宝具：一号位放宝具
• 第二面把二号位光狐一技能移到最前
• 第一面第二回合删除第2项动作
• 御主礼装换成迦勒底战斗服
• 第二面追加：三号位光狐给公主充能，把三号位光狐换成四号位奥伯龙，然后奥伯龙开一技能
• 新建队伍：摩根、光狐、光狐，助战三号位
• 第一面：摩根一技能，二号位光狐一技能给摩根，摩根开宝具
输入「预设」「预览」「撤销」「取消」或「导出 新预设名」。"""


class Unparsed(PlanError):
    """A request outside the offline dialogue grammar."""


def parse_action(text):
    text = re.sub(r"^(?:再让|再由|然后|只让|让|由|再)", "", text).strip()
    if text in ("清空技能", "不用技能", "不开技能"):
        return "skills", None
    if text in ("清空宝具", "不用宝具", "不开宝具", "不放宝具"):
        return "np", None
    match = re.fullmatch(r"(.+?)(?:使用|释放|施放|开启|开|放|打)?宝具", text)
    if match:
        return "np", match[1]
    match = re.fullmatch(r"(.+?)(?:使用|开启|释放|开)?(?:第)?([一二三123])技能(?:给(.+))?", text)
    if match:
        return "skills", {"who": match[1], "skill": int(match[2].translate(NUMBERS)), "to": match[3]}
    match = re.fullmatch(r"(.+?)给(.+?)充能", text)
    if match:
        return "skills", {"who": match[1], "skill": "充能", "to": match[2]}
    raise Unparsed(f"没读懂「{text}」。可写成「三号位光狐一技能给公主」或「公主放宝具」。")


def move_request(message):
    move = re.fullmatch(r"(?:请)?(?:把|将)?(?:刚才|刚刚|上次)(?:的|那个|那次)?([^，,。；;、\n]{1,16}?)(?:挪到|移到|移动到|挪去|移至)(?:第)?([一二三123])面(?:末尾|最后)?", message.strip().rstrip("。！"))
    if move:
        return {"op": "move_skill", "skill": move[1], "to_wave": int(move[2].translate(NUMBERS))}
    return None


def offline_plan(message):
    message = message.strip().rstrip("。！")
    move = move_request(message)
    if move:
        return [move]
    message = re.sub(r"[，,。；;]?(?:其他|其它|其余)(?:照旧|不变|保持不变)$", "", message)
    chunks = re.split(r"(?=第[一二三123]面|(?<!第)[一二三123]面)", message)
    header = chunks.pop(0).strip(" ，,。；;：:")
    ops = []
    if header:
        match = re.fullmatch(r"(?:新建队伍|新建|建队)[：:]?(.+?)(?:[，,]助战(?:在|是)?(?:第)?([1-6一二三四五六])号?位?)?", header)
        if match:
            members = re.split(r"[、,，+/＋]", match[1])
            if not 3 <= len(members) <= 6:
                raise Unparsed("请依站位用顿号分开三至六位从者。")
            ops.append(dict(op="new", members=members, assist=int(match[2].translate(NUMBERS)) if match[2] else None))
        else:
            match = re.fullmatch(r"(?:沿用|加载|选择|使用|用)(?:现有的?)?(?:预设)?[：:]?(.+?)(?:那套|这套)?", header)
            if not match:
                raise Unparsed("请指定队伍或要修改的面。输入「帮助」可查看示例。")
            ops.append(dict(op="load", name=match[1]))
    for chunk in chunks:
        match = re.fullmatch(r"(?:第)?([一二三123])面[，,：:]?(.*)", chunk.strip(" ，,。；;"), re.S)
        if not match:
            raise Unparsed("请按「第二面：技能，宝具」描述动作。")
        body = match[2].strip()
        append = body.startswith(("追加", "补上"))
        body = re.sub(r"^(?:追加|补上|只改宝具|只改技能|改成|改为|只用)[：:]?", "", body)
        step = dict(op="wave", wave=int(match[1].translate(NUMBERS)), mode="append" if append else "replace")
        for clause in re.split(r"(?:然后|随后|接着)|[，,、；;。\n]", body):
            if clause.strip():
                key, value = parse_action(clause.strip())
                if key == "skills" and step.get("np") and value is not None:
                    raise PlanError("同一回合须先开技能、再放宝具。请确认动作顺序。")
                seq = step.setdefault(key, [])
                if value is not None:
                    seq.append(value)
        if "skills" not in step and "np" not in step:
            raise PlanError("请写明这一面要用的技能或宝具。")
        ops.append(step)
    if not ops:
        raise PlanError("请输入配队要求。")
    return ops


def check_shape(obj, required, optional=()):
    if not isinstance(obj, dict) or not set(required) <= obj.keys() or obj.keys() - set(required) - set(optional):
        raise PlanError("动作格式不完整，请重新描述。")


def edited_turns(before, after):
    if not before or not after or roster(before) != roster(after):
        return []
    return [(w, t+1) for w in range(1, 4) for t in range(after.get(f"round{w}_turns", 0))
            if any(before.get(f"round{w}_turn{t}_{f}") != after.get(f"round{w}_turn{t}_{f}") for f in ("skill", "np"))]


def match_answer(issue, text):
    value = norm(text).strip("。！ ").translate(NUMBERS)
    value = norm(issue.aliases.get(value, value))
    return next((c for c in issue.choices if norm(text) == norm(c)
                 or value in (norm(c), norm(c).removesuffix("号位"))), None)


def move_recent_skill(lib, cfg, recent, op, pick):
    check_shape(op, ("op", "to_wave"), ("skill",))
    dest = op["to_wave"]
    dest_key = editable_wave(cfg, dest) + "skill"
    selector = op.get("skill", "技能")
    if not isinstance(selector, str):
        raise PlanError("请用技能名称或序号指出要移动的动作。")
    options = {}
    actors = {}
    for wave, index in recent:
        key = f"round{wave}_turn0_skill"
        action = cfg[key][index]
        code = action[0] if isinstance(action, list) else action
        actor, number = divmod(code - 1, 3)
        skill_name = lib.data[roster(cfg)[actor]]["skill_name"][number]
        if selector not in ("技能", "动作", skill_name) and selector.translate(NUMBERS) != f"{number+1}技能":
            continue
        target = f"给{action[1]}号位" if isinstance(action, list) else ""
        label = f"第{wave}面第{index+1}个：{actor+1}号位{number+1}技能{target}"
        options[label] = wave, index
        actors[label] = actor + 1
    if not options:
        raise PlanError(f"上次技能修改中没有可移动的「{selector}」。请先指定对应的技能。")
    choice = next(iter(options))
    if len(options) > 1:
        issue = Choose("上次涉及多个符合条件的技能。要移动哪一个？", list(options))
        for i, label in enumerate(options, 1):
            issue.aliases[str(i)] = label
            issue.aliases[f"第{i}个"] = label
        slots = sorted(set(actors.values()))
        for alias, slot in choose_slot("", slots, cfg).aliases.items():
            labels = [label for label, actor in actors.items() if f"{actor}号位" == slot]
            if len(labels) == 1:
                issue.aliases[alias] = labels[0]
        choice = pick(issue)
    source, index = options[choice]
    if source == dest:
        raise PlanError(f"这个技能已经在第{dest}面。")
    source_key = editable_wave(cfg, source) + "skill"
    if len(cfg[dest_key]) >= 32:
        raise PlanError("目标面的技能列表已满。")
    cfg[dest_key].append(cfg[source_key].pop(index))
    return [(dest, len(cfg[dest_key]) - 1)]


class Session:
    def __init__(self, library, planner=None):
        self.library = library
        self.finder = PresetFinder(library)
        self.planner = planner
        self.cfg = None
        self.title = ""
        self.pending = None
        self.previous = None
        self.history = []
        self.recent_skills = []
        self.changed_waves = ()
        self.changed_turns = []
        self.edit_run = None

    def execute(self, ops, answers=(), *, player_text=None):
        if not isinstance(ops, list) or not 1 <= len(ops) <= 12:
            raise PlanError("每条消息需要包含 1 至 12 项配队操作。")
        if any(isinstance(op, dict) and op.get("op") in ("edit_turn", "equip", "bench") for op in ops):
            from .edit_graph import EditRun
            if any(not isinstance(op, dict) or op.get("op") not in
                   ("load", "new", "use_match", "edit_turn", "equip", "bench") for op in ops):
                raise PlanError("请将精确编辑和整面替换分开发送。")
            if any(op["op"] in ("load", "use_match", "new") for op in ops[1:]):
                raise PlanError("请将选队操作放在本条编辑指令的开头。")
            run = EditRun(self._apply)
            result = run.start(ops, player_text)
            self.edit_run = run if result is None else None
            return self._finish_run(result)
        return self._apply(ops, answers, player_text=player_text)

    def _apply(self, ops, answers, *, player_text, advanced=False):
        cfg, title = copy.deepcopy(self.cfg), self.title
        baseline = self.cfg
        recent = list(self.recent_skills)
        notes = []
        swap_turns = []
        edited_skills = False
        replies = iter(answers)

        def pick(issue):
            reply = next(replies, None)
            if reply not in issue.choices:
                raise issue
            return reply

        def resolve(function, value):
            if not isinstance(value, str) or not value.strip():
                raise PlanError("请写明角色、站位或预设名称。")
            try:
                return function(value)
            except Choose as issue:
                return function(pick(issue))

        def slot(ref):
            if player_text is not None and isinstance(ref, str):
                try:
                    return self.library.grounded_position(cfg, ref, player_text)
                except Choose as issue:
                    return self.library.position(cfg, pick(issue))
            return resolve(lambda value: self.library.position(cfg, value), ref)

        try:
            for op in ops:
                if not isinstance(op, dict):
                    raise PlanError("无法识别配队操作。")
                kind = op.get("op")
                if kind in ("collection", "register", "remove", "supports", "recommend"):
                    if len(ops) != 1:
                        raise PlanError("请先完成名单或匹配操作，再选择队伍。")
                    if player_text is not None and kind in ("register", "remove", "supports"):
                        raise PlanError("保存名单请使用「登记从者」「移除从者」或「可借助战」指令。")
                    reply = self.finder.handle(op, resolve)
                    self.pending = None
                    return reply
                if kind == "use_match":
                    check_shape(op, ("op", "index"))
                    title, cfg = self.finder.select(op["index"])
                    baseline, recent = copy.deepcopy(cfg), []
                elif kind == "load":
                    check_shape(op, ("op", "name"))
                    selected = resolve(self.library.preset_name, op["name"])
                    if (player_text is not None and len(ops) > 1 and selected == title
                            and not re.search(r"重新加载|重载|重置|从原预设|恢复预设", player_text)):
                        continue
                    title = selected
                    if player_text is not None and selected in self.finder.names:
                        title, cfg = self.finder.select(self.finder.names.index(selected) + 1)
                    else:
                        cfg = self.library.load(title)
                    baseline, recent = copy.deepcopy(cfg), []
                elif kind == "new":
                    check_shape(op, ("op", "members"), ("assist",))
                    if not isinstance(op["members"], list) or not 3 <= len(op["members"]) <= 6:
                        raise PlanError("新队需要三至六位从者。")
                    names = [resolve(self.library.resolve, n) for n in op["members"]]
                    assist = op.get("assist")
                    if assist is None:
                        reply = pick(Choose("哪一位是助战？", [f"{i}号位" for i in range(1, len(names)+1)]))
                        assist = int(reply[0])
                    if type(assist) is not int or not 1 <= assist <= len(names):
                        raise PlanError("助战位置应对应一位已配置的从者。")
                    cfg = self.library.new_team(names, assist)
                    title = "新建队伍"
                    baseline, recent = copy.deepcopy(cfg), []
                elif kind == "bench":
                    if not cfg:
                        raise PlanError("请先加载预设或新建队伍。")
                    check_shape(op, ("op", "slot", "name"))
                    index = integer(op["slot"], 4, 6, "后排站位") - 1
                    name = resolve(self.library.resolve, op["name"])
                    if roster(cfg)[index] not in (None, name):
                        raise PlanError(f"{index+1}号位已有从者。请指定一个空后排，或重新建队。")
                    cfg[f"servant_{index}_name"] = name
                    notes.append(f"{index+1}号位后排为{name}")
                elif kind in ("equip", "edit_turn"):
                    if not cfg:
                        raise PlanError("请先加载预设或新建队伍。")
                    if kind == "equip":
                        check_shape(op, ("op", "name"))
                        notes.append(equip(self.library, cfg, op["name"], pick))
                    else:
                        wave, turn = op.get("wave"), op.get("turn", 1)
                        editor = TurnEditor(self.library, cfg, wave, turn, pick, player_text)
                        notes.append(f"第{wave}面第{turn}回合　" + editor.edit(op))
                        if editor.inserted_swap:
                            swap_turns.append((wave, turn))
                        recent = []
                elif kind == "move_skill":
                    if not cfg:
                        raise PlanError("请先加载队伍并指定要移动的技能。")
                    recent = move_recent_skill(self.library, cfg, recent, op, pick)
                    edited_skills = True
                elif kind == "wave":
                    check_shape(op, ("op", "wave"), ("mode", "skills", "np"))
                    if not cfg:
                        raise PlanError("请先加载预设或新建队伍。")
                    if op.get("mode", "replace") not in ("replace", "append") or not {"skills", "np"} & op.keys():
                        raise PlanError("请指定要替换或追加的动作。")
                    prefix = editable_wave(cfg, op["wave"])
                    for field in ("skills", "np"):
                        if field not in op:
                            continue
                        items = op[field]
                        if not isinstance(items, list) or len(items) > 32:
                            raise PlanError("动作列表格式有误。")
                        result = []
                        for item in items:
                            if field == "np":
                                result.append(slot(item))
                                continue
                            check_shape(item, ("who", "skill"), ("to",))
                            actor = slot(item["who"])
                            target = slot(item["to"]) if item.get("to") is not None else None
                            try:
                                action = self.library.skill(cfg, actor, item["skill"], target)
                            except Choose as issue:
                                target = self.library.position(cfg, pick(issue))
                                action = self.library.skill(cfg, actor, item["skill"], target)
                            result.append(action)
                        key = prefix + ("skill" if field == "skills" else "np")
                        if field == "skills":
                            if not edited_skills:
                                recent = []
                            edited_skills = True
                            start = len(cfg[key]) if op.get("mode") == "append" else 0
                            if op.get("mode") != "append":
                                recent = [r for r in recent if r[0] != op["wave"]]
                            recent.extend((op["wave"], i) for i in range(start, start + len(result)))
                        if op.get("mode") == "append":
                            result = cfg[key] + result
                        if field == "np" and (len(result) > 3 or len(set(result)) != len(result)):
                            raise PlanError("同一回合最多三个不同站位的宝具。")
                        cfg[key] = result
                else:
                    raise PlanError("不支持该配队操作。")
            if swap_turns:
                check_swap_suffix(self.library, cfg, *min(swap_turns))
        except Choose as issue:
            if advanced:
                raise
            self.pending = (ops, tuple(answers), issue, player_text)
            return f"{issue}　" + " / ".join(issue.choices)
        result = dict(cfg=cfg, title=title, recent=recent, turns=edited_turns(baseline, cfg), notes=notes)
        return result if advanced else self._commit(result)

    def _finish_run(self, result):
        if result is None:
            issue = self.edit_run.issue
            return f"{issue}　" + " / ".join(issue.choices)
        self.edit_run = None
        if self.cfg == result["cfg"] and self.title == result["title"]:
            return "当前草案已符合这条指令。" + "。".join(result["notes"])
        return self._commit(result)

    def _commit(self, result):
        self.pending = None
        self.previous = (self.cfg, self.title, self.recent_skills)
        self._mark_turns(result["turns"])
        self.recent_skills = result["recent"]
        self.cfg, self.title = result["cfg"], result["title"]
        self.finder.names = []
        summary = "；".join(result["notes"])
        return f"已更新草案「{self.title}」。{summary}\n" + self.preview()

    def _mark_turns(self, turns):
        self.changed_turns = [tuple(turn) for turn in turns]
        self.changed_waves = tuple(sorted({wave for wave, _ in self.changed_turns}))

    def preview(self):
        return preview(self.cfg, self.changed_waves, self.changed_turns)

    def send(self, message):
        text = message.strip()
        try:
            return self._send(text)
        except (PlanError, OSError, ValueError) as error:
            return f"未完成：{error}"

    def _send(self, text):
        if text in ("帮助", "help", "?"):
            return HELP
        if text in ("预设", "列出预设", "有哪些预设"):
            return "已有预设\n" + "\n".join(self.library.presets())
        if text in ("取消", "撤销"):
            if self.pending or self.edit_run or text == "取消":
                self.pending = None
                self.edit_run = None
                self.finder.names = []
                return "已结束追问，保留原草案。"
            if self.previous is None:
                return "没有可撤销的修改。"
            old = self.cfg
            self.cfg, self.title, self.recent_skills = self.previous
            self._mark_turns(edited_turns(old, self.cfg))
            self.previous = None
            self.finder.names = []
            self.history.clear()
            return "已撤销上一条修改。\n" + self.preview()
        if text in ("预览", "查看队伍"):
            return self.preview()
        detail = re.fullmatch(r"(?:查看|预览)第?([123])面第?(\d+)回合", text.translate(NUMBERS))
        if detail and self.cfg:
            w, t = int(detail[1]), int(detail[2])
            items = actions_for(self.cfg, w, t)
            lines = [f"第{w}面第{t}回合"]
            for i, item in enumerate(items):
                try:
                    party = roster(project(self.cfg, formation(self.cfg, w, t, i)))
                    label = describe(item, party)
                except PlanError:
                    label = f"原动作{item}（站位待核对）"
                lines.append(f"第{i+1}项 {label}")
            return "\n".join(lines)
        if self.edit_run:
            issue = self.edit_run.issue
            choice = match_answer(issue, text)
            if choice is None:
                return f"{issue}　" + " / ".join(issue.choices) + "。也可以输入「取消」。"
            run, self.edit_run = self.edit_run, None
            result = run.resume(choice)
            self.edit_run = run if result is None else None
            return self.remember(text, self._finish_run(result))
        if self.pending:
            ops, answers, issue, player_text = self.pending
            choice = match_answer(issue, text)
            if choice is None:
                return f"{issue}　" + " / ".join(issue.choices) + "。也可以输入「取消」。"
            self.pending = None
            return self.remember(text, self.execute(ops, (*answers, choice), player_text=player_text))
        match = re.fullmatch(r"(?:导出|另存为)[：:\s]+(.+)", text)
        if match:
            if not self.cfg:
                raise PlanError("请先创建队伍草案。")
            path = export_preset(self.cfg, self.library.root / "settings", match[1])
            return f"已另存预设：{path}"
        query = collection_request(text, self.library)
        if query is not None:
            return self.execute([query])
        precise = precise_plan(text, backline=bool(self.cfg and any(roster(self.cfg)[3:])))
        move = move_request(text)
        if precise:
            reply = self.execute(precise)
        elif move:
            # The player identified one previous action. Preserve every unrelated action.
            reply = self.execute([move])
        elif text.startswith("加载预设："):
            reply = self.execute([{"op": "load", "name": text.removeprefix("加载预设：")}])
        elif self.planner:
            plan = self.planner(text, self)
            reply = plan if isinstance(plan, str) else self.execute(plan, player_text=text)
        else:
            reply = self.execute(offline_plan(text))
        return self.remember(text, reply)

    def remember(self, text, reply):
        self.history.extend([("user", text), ("assistant", reply)])
        self.history = self.history[-6:]
        return reply
