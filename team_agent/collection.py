"""Personal servant lists and matching against an existing BBchannel preset."""

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
import json
import re
import sqlite3
from typing import Optional

from .core import PlanError, norm, roster


def quantities(items):
    return "、".join(f"{name}×{count}" for name, count in items.items()) or "无"


@dataclass
class Collection:
    owned: dict
    supports: Optional[list] = None  # None means unrestricted; [] means none available.

    def describe(self):
        support = "不限（匹配时假定可以借到）" if self.supports is None else "、".join(self.supports) or "无"
        return f"已登记从者　{quantities(self.owned)}\n可借助战　{support}"


class CollectionStore:
    def __init__(self, root):
        self.path = root / "team_agent_collection.sqlite3"

    def access(self, action=None, entries=None):
        if action is None and not self.path.exists():
            return Collection({})
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                # Serialize read-modify-write across desktop and CLI sessions.
                if action is not None:
                    db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS collection (id INTEGER PRIMARY KEY, owned TEXT, supports TEXT)")
                row = db.execute("SELECT owned, supports FROM collection WHERE id=1").fetchone()
                profile = Collection(json.loads(row[0]), json.loads(row[1])) if row else Collection({})
                if action is None:
                    return profile
                if action == "register":
                    profile.owned.update(entries)
                elif action == "remove":
                    for name in entries:
                        profile.owned.pop(name, None)
                elif action == "supports":
                    profile.supports = list(entries) if entries is not None else None
                else:
                    raise PlanError("未知的名单修改。")
                db.execute("INSERT OR REPLACE INTO collection VALUES (1, ?, ?)",
                           (json.dumps(profile.owned, ensure_ascii=False), json.dumps(profile.supports, ensure_ascii=False)))
                return profile
        except sqlite3.Error as exc:
            raise PlanError(f"无法访问本地从者名单：{exc}") from exc


def requirements(cfg, profile):
    party = roster(cfg)
    idx = cfg.get("assistIdx")
    if type(idx) is not int or not 0 <= idx < len(party) or not party[idx]:
        raise PlanError("预设未指定有效的助战从者。")
    own = Counter(name for slot, name in enumerate(party) if name and slot != idx)
    missing = own - Counter(profile.owned)
    reasons = ["自有缺 " + quantities(missing)] if missing else []
    unavailable = profile.supports is not None and party[idx] not in profile.supports
    if unavailable:
        reasons.append(f"可借名单缺 {party[idx]}")
    detail = f"借{idx+1}号位 {party[idx]}"
    if cfg.get("assistMode") in ("仅礼装", "从者礼装") and cfg.get("assistEquip"):
        detail += f"（礼装 {cfg['assistEquip']}）"
    if cfg.get("onlyFriendAssist"):
        detail += "，限好友"
    return detail, reasons, sum(missing.values()) + int(unavailable)


NUM = str.maketrans("一二两三四五六七八九", "1223456789")


def parse_member(text):
    text = text.strip()
    match = re.fullmatch(r"([0-9一二两三四五六七八九]+)(?:只|位|个|名)\s*(.+)", text)
    if match:
        return {"name": match[2].strip(), "count": int(match[1].translate(NUM))}
    match = re.fullmatch(r"(.+?)\s*[xX×*]\s*([0-9]+)", text)
    if match:
        return {"name": match[1].strip(), "count": int(match[2])}
    return {"name": text, "count": 1}


def member_list(text, lib):
    items = []
    for piece in re.split(r"[、，,＋+\n]", text):
        piece = piece.strip()
        if not piece:
            raise PlanError("请用顿号分隔从者，例如「登记从者：公主、光狐」。")
        while norm(parse_member(piece)["name"]) not in lib.aliases:
            split = next((i for i in range(len(piece)-1, 0, -1) if piece[i] == "和"
                          and norm(parse_member(piece[:i])["name"]) in lib.aliases), None)
            if split is None:
                break
            items.append(parse_member(piece[:split]))
            piece = piece[split+1:].strip()
        items.append(parse_member(piece))
    return items


def collection_request(text, lib):
    is_question = text.rstrip().endswith(("?", "？"))
    text = text.strip().rstrip("？?。！!").strip()
    if text in ("我的从者", "我的名单", "查看从者名单", "我的可借助战"):
        return {"op": "collection"}
    if text in ("匹配预设", "匹配队伍", "推荐队伍", "按我的从者找队伍", "能用哪套", "我能用哪套"):
        return {"op": "recommend"}
    digit = "[一二两三四五六七八九]"
    pick = re.fullmatch(rf"(?:选用|选择|选|用|加载)(?:推荐)?第?(\d+|{digit}?十{digit}?|{digit})(?:套|个|支)?", text)
    if pick:
        n = pick[1].translate(NUM)
        if "十" in n:
            tens, _, ones = n.partition("十")
            n = str(int(tens or 1) * 10 + int(ones or 0))
        return {"op": "use_match", "index": int(n)}
    if text in ("可借助战不限", "不限制助战", "可借助战：不限"):
        return {"op": "supports", "members": None}
    if text in ("没有可借助战", "可借助战：无"):
        return {"op": "supports", "members": []}
    match = re.fullmatch(r"(登记从者|登记|移除从者|可借助战|我有)[：:\s]*(.+)", text)
    if not match:
        return None
    verb, args = match.groups()
    if verb == "我有":
        query = re.fullmatch(r"(.+?)[，,。；;\s]*(?:我)?(?:能用哪套|能用哪些(?:预设|队伍)?|可以用哪套|有哪些队伍可用|能匹配哪些预设)", args)
        if query:
            parts = re.split(r"[，,。；;]\s*(?:我)?(?:可借助战|只能借到|只能借|可以借到|可以借|能借到|可借)[：:\s]*",
                             query[1], maxsplit=1)
            op = {"op": "recommend", "owned": member_list(parts[0], lib)}
            if len(parts) > 1:
                borrowed = parts[1].strip()
                if borrowed == "不限":
                    op["supports"] = None
                else:
                    op["supports"] = [] if borrowed == "无" else member_list(borrowed, lib)
            return op
        # Leave other questions to the language model. Only a list is a declaration.
        if is_question or re.search(r"[？?，,].*(?:怎么|如何|能|可|哪|吗|谁)|(?:吗|呢)$", args):
            return None
    action = {"可借助战": "supports", "移除从者": "remove"}.get(verb, "register")
    return {"op": action, "members": member_list(args, lib)}


class PresetFinder:
    def __init__(self, library):
        self.lib = library
        self.store = CollectionStore(library.root)
        self.names = []
        self.overrides = {}

    def members(self, raw, resolve):
        if not isinstance(raw, list):
            raise PlanError("从者名单需要按列表提供。")
        counts = Counter()
        for item in raw:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict) or set(item) - {"name", "count"}:
                raise PlanError("从者条目需要名称和持有数量。")
            count = item.get("count", 1)
            if type(count) is not int or count < 1:
                raise PlanError("持有数量需要是正整数。")
            name = resolve(self.lib.resolve, item.get("name"))
            counts[name] += count
        return dict(counts)

    def query_profile(self, overrides):
        saved = self.store.access()
        return Collection(overrides.get("owned", saved.owned), overrides.get("supports", saved.supports))

    def handle(self, op, resolve):
        kind = op["op"]
        if kind == "collection" and set(op) == {"op"}:
            return self.store.access().describe() + "\n可输入「登记从者：公主、光狐」或「可借助战：光狐」。"
        if kind in ("register", "remove", "supports") and set(op) == {"op", "members"}:
            entries = None if kind == "supports" and op["members"] is None else self.members(op["members"], resolve)
            if kind != "supports" and not entries:
                raise PlanError("请至少指定一位从者。")
            profile = self.store.access(kind, entries)
            self.names = []
            return "已保存名单。\n" + profile.describe()
        if kind != "recommend" or set(op) - {"op", "owned", "supports"}:
            raise PlanError("从者查询格式有误。")
        self.names = []
        overrides = {}
        if "owned" in op:
            overrides["owned"] = self.members(op["owned"], resolve)
        if "supports" in op:
            overrides["supports"] = None if op["supports"] is None else list(self.members(op["supports"], resolve))
        profile = self.query_profile(overrides)
        if not profile.owned:
            return "请先登记常用从者，或直接说「我有公主和一只光狐，能用哪套？」。"
        self.lib.current()  # Presets and the installed account page must both use CH.
        matches, near, skipped = [], [], 0
        for name, path in self.lib.presets().items():
            try:
                cfg = self.lib.read_preset(path)
                detail, reasons, deficit = requirements(cfg, profile)
            except (OSError, ValueError):
                skipped += 1
                continue
            if deficit:
                near.append((deficit, name, "，".join(reasons)))
            else:
                matches.append((name, detail))
        self.names = [name for name, _ in matches]
        self.overrides = overrides
        using = "本次试配从者" if overrides else "已登记从者"
        lines = [f"{using}　{quantities(profile.owned)}"]
        if matches:
            lines += [f"{i}. {name}　{detail}" for i, (name, detail) in enumerate(matches, 1)]
            lines.append("回复「用第一套」可载入该预设的原有打法。")
        else:
            lines.append("没有完全匹配的预设。")
            lines.extend(f"{name}　{reason}" for _, name, reason in sorted(near)[:3])
        if profile.supports is None:
            lines.append("可借助战不限，请在游戏内确认好友和礼装。")
        else:
            lines.append("可借助战　" + ("、".join(profile.supports) or "无"))
        if skipped:
            lines.append(f"已跳过 {skipped} 份格式或助战位无效的预设。")
        return "\n".join(lines)

    def select(self, index):
        if type(index) is not int or not 1 <= index <= len(self.names):
            raise PlanError("请先匹配预设，再选择结果列表中的编号。")
        name = self.names[index-1]
        cfg = self.lib.load(name)
        _, reasons, _ = requirements(cfg, self.query_profile(self.overrides))
        if reasons:
            raise PlanError("名单或预设已变化，请重新匹配。" + "，".join(reasons))
        return name, cfg
