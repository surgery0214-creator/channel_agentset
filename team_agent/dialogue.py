"""Chinese dialogue and transactional edits shared by the desktop and CLI."""

import copy
import re

from .core import Choose, NUMBERS, PlanError, UnknownReference, editable_wave, export_preset, norm, preview


HELP = """可以直接这样说
• 用公主双光狐队。第二面只让三号位光狐开一技能给公主，然后公主开宝具。
• 第二面追加二号位光狐三技能给公主
• 第三面只改宝具：一号位放宝具
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


def offline_plan(message):
    message = message.strip().rstrip("。！")
    message = re.sub(r"[，,。；;]?(?:其他|其它|其余)(?:照旧|不变|保持不变)$", "", message)
    chunks = re.split(r"(?=第[一二三123]面|(?<!第)[一二三123]面)", message)
    header = chunks.pop(0).strip(" ，,。；;：:")
    ops = []
    if header:
        match = re.fullmatch(r"(?:新建队伍|新建|建队)[：:]?(.+?)(?:[，,]助战(?:在|是)?(?:第)?([123一二三])号?位?)?", header)
        if match:
            members = re.split(r"[、,，+/＋]", match[1])
            if len(members) != 3:
                raise Unparsed("请用顿号分开三个前排名，例如「新建队伍：摩根、光狐、光狐，助战三号位」。")
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


class Session:
    def __init__(self, library, planner=None):
        self.library = library
        self.planner = planner
        self.cfg = None
        self.title = ""
        self.pending = None
        self.previous = None
        self.history = []

    def execute(self, ops, answers=()):
        if not isinstance(ops, list) or not 1 <= len(ops) <= 12:
            raise PlanError("每条消息需要包含 1 至 12 项配队操作。")
        cfg, title = copy.deepcopy(self.cfg), self.title
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
            return resolve(lambda value: self.library.position(cfg, value), ref)

        try:
            for op in ops:
                if not isinstance(op, dict):
                    raise PlanError("无法识别配队操作。")
                kind = op.get("op")
                if kind == "load":
                    check_shape(op, ("op", "name"))
                    title = resolve(self.library.preset_name, op["name"])
                    cfg = self.library.load(title)
                elif kind == "new":
                    check_shape(op, ("op", "members"), ("assist",))
                    if not isinstance(op["members"], list) or len(op["members"]) != 3:
                        raise PlanError("新队需要三位前排。")
                    names = [resolve(self.library.resolve, n) for n in op["members"]]
                    assist = op.get("assist")
                    if assist is None:
                        reply = pick(Choose("哪一位是助战？", ["1号位", "2号位", "3号位"]))
                        assist = int(reply[0])
                    if type(assist) is not int or assist not in (1, 2, 3):
                        raise PlanError("助战位置应是 1、2 或 3。")
                    cfg = self.library.new_team(names, assist)
                    title = "新建队伍"
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
                                action = self.library.skill(cfg, actor, item["skill"], slot(pick(issue)))
                            result.append(action)
                        key = prefix + ("skill" if field == "skills" else "np")
                        if op.get("mode") == "append":
                            result = cfg[key] + result
                        if field == "np" and (len(result) > 3 or len(set(result)) != len(result)):
                            raise PlanError("同一回合最多三个不同站位的宝具。")
                        cfg[key] = result
                else:
                    raise PlanError("不支持该配队操作。")
        except Choose as issue:
            self.pending = (ops, tuple(answers), issue)
            return f"{issue}　" + " / ".join(issue.choices)
        self.pending = None
        self.previous = (self.cfg, self.title)
        self.cfg, self.title = cfg, title
        return f"已更新草案「{title}」。\n" + preview(cfg)

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
            if self.pending or text == "取消":
                self.pending = None
                return "已结束追问，保留原草案。"
            if self.previous is None:
                return "没有可撤销的修改。"
            self.cfg, self.title = self.previous
            self.previous = None
            self.history.clear()
            return "已撤销上一条修改。\n" + preview(self.cfg)
        if text in ("预览", "查看队伍"):
            return preview(self.cfg)
        if self.pending:
            ops, answers, issue = self.pending
            value = norm(text).translate(NUMBERS)
            choices = [c for c in issue.choices if norm(text) == norm(c)
                       or value in (norm(c), norm(c).removesuffix("号位"))]
            if len(choices) != 1:
                return f"{issue}　" + " / ".join(issue.choices) + "。也可以输入「取消」。"
            self.pending = None
            return self.remember(text, self.execute(ops, (*answers, choices[0])))
        match = re.fullmatch(r"(?:导出|另存为)[：:\s]+(.+)", text)
        if match:
            if not self.cfg:
                raise PlanError("请先创建队伍草案。")
            path = export_preset(self.cfg, self.library.root / "settings", match[1])
            return f"已另存预设：{path}"
        try:
            reply = self.execute(offline_plan(text))
        except (Unparsed, UnknownReference):
            if not self.planner:
                raise
            plan = self.planner(text, self)
            reply = plan if isinstance(plan, str) else self.execute(plan)
        return self.remember(text, reply)

    def remember(self, text, reply):
        self.history.extend([("user", text), ("assistant", reply)])
        self.history = self.history[-6:]
        return reply
