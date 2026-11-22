"""Read BBchannel presets, validate a battle plan and export a separate preset."""

import copy
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
NUMBERS = str.maketrans("一二三四五六", "123456")
POSITION = re.compile(r"(?:第)?([1-6])(?:号位|号|位)(.*)")


class PlanError(ValueError):
    pass


class UnknownReference(PlanError):
    """The offline parser could not match the player's wording."""


class Choose(PlanError):
    def __init__(self, question, choices):
        super().__init__(question)
        self.choices = choices
        self.aliases = {}


FRIEND_WORDS = ("好友", "好友那只", "好友那位", "借的", "借的那只", "助战", "助战那只", "助战位")
OWN_WORDS = ("自己的", "自己的那只", "自家的", "自家那只", "我自己的", "我的那只")


def choose_slot(question, slots, cfg):
    choices = [f"{i}号位" for i in slots]
    issue = Choose(question, choices)
    assist = cfg.get("assistIdx")
    if type(assist) is int and 0 <= assist < 6:
        support = f"{assist+1}号位"
        if support in choices:
            issue.aliases.update(dict.fromkeys(FRIEND_WORDS, support))
        own = [c for c in choices if c != support]
        if len(own) == 1:
            issue.aliases.update(dict.fromkeys(OWN_WORDS, own[0]))
    return issue


def read_json(path):
    with path.open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def norm(text):
    return unicodedata.normalize("NFKC", str(text)).strip().casefold()


def roster(config):
    return [config.get(f"servant_{i}_name") for i in range(6)]


# These belong to this player's installation, independently of the chosen team.
LOCAL_FIELDS = {
    "snapshotDevice", "operateDevice", "connectMode", "screenRotation", "dropImage",
    "clearAP", "allowOtherApple", "fullFriendshipStop", "gameCrushRestart",
    "dropStopNum", "randomTouch", "randomTime", "intervalBF", "intervalAF",
    "scrollLimit", "stuckRestart", "checkStuckInterval", "stuck_times",
    "intervalAFswipeAssist", "intervalBFchooseCard", "addFriend", "ignoreFriendship",
}


class Library:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.data = read_json(self.root / "servant_info_CH.json")
        self.aliases = {}
        for name, entry in self.data.items():
            for alias in [name, *entry.get("other_name", [])]:
                if alias:
                    self.aliases.setdefault(norm(alias), set()).add(name)

    def presets(self):
        return {p.stem: p for p in sorted((self.root / "settings").glob("*.json"))}

    def current(self):
        pages = read_json(self.root / "scripts_settings.json")
        page = pages.get("page0") if isinstance(pages, dict) else None
        if not isinstance(page, dict) or page.get("server") != "CH":
            raise PlanError("当前设置的 page0 需要使用国服 CH。")
        return page

    def resolve(self, alias):
        names = self.aliases.get(norm(alias), set())
        if len(names) == 1:
            return next(iter(names))
        if alias in self.data:
            return alias
        if names:
            raise Choose(f"「{alias}」有多个角色。请选择具体名称。", sorted(names))
        raise UnknownReference(f"国服角色库里没有找到「{alias}」，请使用角色正式名或已收录的昵称。")

    def preset_name(self, query):
        files = self.presets()
        if query in files:
            return query
        key = norm(query).removesuffix("这套").removesuffix("那套").removesuffix("队")
        if not key:
            raise PlanError("请输入预设名。")
        matches = []
        for name, path in files.items():
            if key in norm(name):
                matches.append(name)
                continue
            try:
                cfg = read_json(path)
            except (OSError, ValueError):
                continue
            if not isinstance(cfg, dict) or cfg.get("server") != "CH":
                continue
            members = [n for n in roster(cfg) if isinstance(n, str) and n]
            if not members:
                continue
            # Consume the player's shorthand in party order, including 双光狐.
            remaining = key.replace("_", "").replace("＋", "").replace("+", "")
            i = 0
            while remaining and i < len(members):
                count = 2 if remaining.startswith("双") else 1
                if count == 2 and members[i:i+2] != [members[i], members[i]]:
                    break
                part = remaining[1:] if count == 2 else remaining
                entry = self.data.get(members[i], {})
                aliases = sorted({norm(a) for a in [members[i], *entry.get("other_name", [])] if a}, key=len, reverse=True)
                alias = next((a for a in aliases if part.startswith(a)), None)
                if not alias:
                    break
                remaining = part[len(alias):]
                i += count
            if not remaining and i == len(members):
                matches.append(name)
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise Choose("匹配到多个预设，请选一个。", matches)
        raise UnknownReference(f"没有匹配到预设「{query}」。输入「预设」可查看名称。")

    def read_preset(self, path):
        config = read_json(path)
        if not isinstance(config, dict) or config.get("server") != "CH" or not any(roster(config)):
            raise PlanError("仅支持国服的平铺队伍预设。")
        for member in roster(config):
            if member is not None and (not isinstance(member, str) or member not in self.data):
                raise PlanError(f"国服资料无法识别预设中的从者「{member}」。")
        for wave in range(1, 4):
            turns = config.get(f"round{wave}_turns")
            if type(turns) is not int or turns < 0:
                raise PlanError(f"预设中的第{wave}面缺少有效回合数。")
            for turn in range(turns):
                for field in ("skill", "np"):
                    if not isinstance(config.get(f"round{wave}_turn{turn}_{field}"), list):
                        raise PlanError(f"预设中的第{wave}面第{turn+1}回合缺少有效 {field} 列表。")
        return config

    def load(self, name):
        path = self.presets().get(name)
        if path is None:
            raise PlanError("该预设已被移动或改名，请重新选择。")
        config = self.read_preset(path)
        # Use this installation's device, AP and stopping preferences.
        local = self.current()
        for key in LOCAL_FIELDS:
            if key in local:
                config[key] = copy.deepcopy(local[key])
        return config

    def new_team(self, members, assist):
        if len(members) != 3 or not 1 <= assist <= 3:
            raise PlanError("新队需要三位前排，并指定其中一位助战。")
        cfg = self.current()
        for key in list(cfg):
            if key.startswith("round"):
                del cfg[key]
        for i, name in enumerate([*members, None, None, None]):
            cfg[f"servant_{i}_name"] = name
        own = {}
        for i, name in enumerate(members):
            if i != assist - 1:
                own.setdefault(name, i)
        cfg.update(assistIdx=assist - 1, assistMode="仅从者", usedServant=list(own.values()),
                   specialKeys=[], assistTags=[])
        for wave in range(1, 4):
            cfg.update({f"round{wave}_turns": 1, f"round{wave}_extraSkill": [],
                        f"round{wave}_extraStrategy": None})
            for suffix, value in (("skill", []), ("np", []), ("strategy", None), ("condition", None)):
                cfg[f"round{wave}_turn0_{suffix}"] = value
        return cfg

    def position(self, cfg, ref):
        ref = str(ref).strip()
        if ref in FRIEND_WORDS:
            assist = cfg.get("assistIdx")
            if type(assist) is not int or not 0 <= assist < 3:
                raise PlanError("这支队伍的助战不在前排。")
            ref = f"{assist+1}号位"
        match = POSITION.fullmatch(ref.translate(NUMBERS))
        if ref in ("1", "2", "3"):
            match = POSITION.fullmatch(ref + "号位")
        party = roster(cfg)
        if match:
            slot, hint = int(match[1]), match[2]
            if slot > 3 or not party[slot-1]:
                raise PlanError(f"{slot}号位不是可用的前排。")
            if hint and party[slot-1] not in self.aliases.get(norm(hint), set()):
                raise PlanError(f"{slot}号位是{party[slot-1]}，与「{hint}」不符。")
            return slot
        names = self.aliases.get(norm(ref), set())
        if ref == "狐狸":
            # Colloquial in-party reference. Keep the catalog's exact lookup for new teams.
            names = names | self.aliases.get("光狐", set()) | self.aliases.get("暗狐", set())
        matches = [i for i, name in enumerate(party[:3], 1) if name in names]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise choose_slot(f"「{ref}」指哪一位？", matches, cfg)
        raise UnknownReference(f"前排没有「{ref}」，请检查当前队伍。")

    def grounded_position(self, cfg, ref, player_text):
        slot = self.position(cfg, ref)
        name = roster(cfg)[slot - 1]
        slots = [i for i, member in enumerate(roster(cfg)[:3], 1) if member == name]
        if len(slots) == 1:
            return slot
        words = norm(player_text).translate(NUMBERS)
        if re.search(fr"{slot}(?:号位|号|位)", words):
            return slot
        issue = choose_slot(f"「{name}」指哪一位？", slots, cfg)
        for phrase, choice in issue.aliases.items():
            if phrase in words and choice == f"{slot}号位":
                return slot
        raise issue

    def skill(self, cfg, slot, number, target):
        entry = self.data[roster(cfg)[slot-1]]
        if isinstance(number, str) and not number.isdigit():
            hits = [i for i, s in enumerate(entry["skill_name"], 1) if s == number]
            if len(hits) != 1:
                raise PlanError(f"无法确定「{number}」对应哪个技能，请写一、二或三技能。")
            number = hits[0]
        if type(number) not in (int, str) or str(number) not in ("1", "2", "3"):
            raise PlanError("技能序号应当是 1、2 或 3。")
        index = int(number) - 1
        kind = entry["skill_type"][index]
        if entry.get("skill_type_super", entry["skill_type"])[index] != kind:
            raise PlanError("这个技能的强化状态会改变选人方式，请在原程序中配置。")
        if kind == 1 and target is None:
            slots = [i for i, name in enumerate(roster(cfg)[:3], 1) if name]
            raise choose_slot(f"{slot}号位的{number}技能给谁？", slots, cfg)
        if kind != 1 and target is not None:
            raise PlanError(f"{slot}号位的{number}技能在角色表里无需指定队友。")
        code = (slot - 1) * 3 + index + 1
        return [code, target] if target is not None else code


def editable_wave(cfg, wave):
    if type(wave) is not int or wave not in (1, 2, 3):
        raise PlanError("请指定第一、第二或第三面。")
    prefix = f"round{wave}_turn0_"
    if any(roster(cfg)[3:]):
        raise PlanError("该预设含后排，前排可能在战斗中变化。首版仅编辑固定前排队伍。")
    if (cfg.get(f"round{wave}_turns") != 1 or cfg.get(prefix+"condition")
            or cfg.get(prefix+"strategy") or cfg.get(f"round{wave}_extraSkill")
            or cfg.get(f"round{wave}_extraStrategy")):
        raise PlanError(f"第{wave}面含多回合、条件或特殊出卡策略，请在原程序中编辑这一面。")
    return prefix


def preview(cfg, changed_waves=(), changed_turns=None):
    from .battle_edit import describe

    if not cfg:
        return "尚未选择队伍。"
    members = roster(cfg)
    lines = [f"{i}号位  {n}" + ("（助战）" if i-1 == cfg.get("assistIdx") else "")
             for i,n in enumerate(members,1) if n]
    for wave in range(1, 4):
        for turn in range(cfg.get(f"round{wave}_turns", 0)):
            prefix = f"round{wave}_turn{turn}_"
            actions = [describe(action) for action in cfg.get(prefix+"skill", [])]
            actions.extend(f"{n}号位宝具" for n in cfg.get(prefix+"np", []))
            if cfg.get(prefix+"strategy") or cfg.get(prefix+"condition"):
                actions.append("原出卡策略／条件")
            changed = wave in changed_waves if changed_turns is None else (wave, turn+1) in changed_turns
            marker = "〔本次修改〕" if changed else ""
            lines.append(f"第{wave}面 · 回合{turn+1}{marker}　" + (" → ".join(actions) or "常规出卡"))
    return "\n".join(lines)


def export_preset(cfg, directory, name):
    name = name.strip()
    reserved = re.fullmatch(r"(?:con|nul|prn|aux|com[1-9¹²³]|lpt[1-9¹²³]|conin\$|conout\$)",
                            name.split(".", 1)[0].rstrip(), re.IGNORECASE)
    if (not name or len(name) > 80 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.startswith(".") or name.endswith(".") or reserved):
        raise PlanError("请输入有效的预设名（最多 80 字，不含路径符号）。")
    path = directory / (name + ".json")
    fd, filename = tempfile.mkstemp(dir=directory, suffix=".tmp")
    tmp = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cfg, stream, ensure_ascii=False, indent=4, allow_nan=False)
        try:
            if os.name == "nt":
                os.rename(tmp, path)  # Windows refuses to replace an existing destination.
            else:
                os.link(tmp, path)
        except FileExistsError:
            raise PlanError("已有同名预设，请另选一个名字。") from None
    finally:
        tmp.unlink(missing_ok=True)
    return path
