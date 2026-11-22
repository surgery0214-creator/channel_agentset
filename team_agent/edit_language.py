"""Explicit Chinese commands for the turn editor, also available without a model."""

import re

from .core import NUMBERS


def selector(text):
    match = re.fullmatch(r"第?(\d+)(?:个|项)(?:技能|动作)?", text)
    if match:
        return {"index": int(match[1])}
    match = re.fullmatch(r"(.+?)(?:的|开|使用)?第?([123])技能", text)
    if match:
        who, skill = match[1], int(match[2])
        return {"master": skill} if who == "御主" else {"who": who, "skill": skill}


def step(text):
    text = re.sub(r"^(?:然后|随后|接着|再)?(?:让|由|把)?", "", text)
    match = re.fullmatch(r"(.+?)(?:与|和)(.+?)(?:交换|换人)", text)
    if not match:
        match = re.fullmatch(r"(.+?)(?:换成|换为|换下换上|换上)(.+)", text)
    if match:
        return {"swap_out": match[1], "swap_in": match[2]}
    match = re.fullmatch(r"(.+?)(?:先)?给(.+?)充能", text)
    if match:
        return {"who": match[1], "skill": "充能", "to": match[2]}
    match = re.fullmatch(r"(.+?技能)(?:给(.+))?", text)
    if match:
        selected = selector(match[1])
        if selected and "index" not in selected:
            if match[2]:
                selected["to"] = match[2]
            return selected


def precise_plan(message, *, backline=False):
    text = message.strip().rstrip("。！").replace(" ", "")
    bench = re.fullmatch(r"(?:后排)?([四五六456])号位(?:放入|放|加入|加上|安排)(.+)", text)
    if bench:
        return [{"op": "bench", "slot": int(bench[1].translate(NUMBERS)), "name": bench[2]}]
    equip = re.fullmatch(r"(?:御主礼装|御主服)(?:换成|设为|选用|选择|用|[：:])(.+)", text)
    if equip:
        return [{"op": "equip", "name": equip[1]}]
    # Normalize only ordinal numerals. Characters in servant and outfit names are significant.
    text = re.sub(r"[一二三四五六](?=号位|面|回合|技能|项|个)", lambda m: m[0].translate(NUMBERS), text)
    match = re.fullmatch(r"(?:把|将)?第?([123])面(?:第?(\d+)回合)?[，,：:]?(.*)", text)
    if not match:
        return None
    base = {"op": "edit_turn", "wave": int(match[1]), "turn": int(match[2] or 1)}
    body = re.sub(r"^(?:把|将)", "", match[3])
    if backline or match[2]:
        np_text = re.sub(r"^(?:只改宝具|宝具改为)[：:]?", "", body)
        if np_text in ("清空宝具", "不开宝具", "不用宝具"):
            return [{**base, "edit": "np", "np": []}]
        actors = [re.fullmatch(r"(.+?)(?:放|开|使用)宝具", x) for x in filter(None, re.split("然后|[、，,]", np_text))]
        if actors and all(actors):
            return [{**base, "edit": "np", "np": [a[1] for a in actors]}]
    delete = re.fullmatch(r"(?:删除|删掉|去掉|移除)(.+)", body)
    if delete:
        choice = selector(delete[1])
        return [{**base, "edit": "delete", **choice}] if choice else None
    change = re.fullmatch(r"(.+?)(?:的目标改为|目标改为|改为给|改给)(.+)", body)
    if change:
        choice = selector(change[1])
        return [{**base, "edit": "retarget", **choice, "to": change[2]}] if choice else None
    move = re.fullmatch(r"(.+?)(?:挪到|移到|移至|放到|提前到)(最前面?|开头|最先|末尾|最后面?|第?(\d+)项)", body)
    if move:
        choice = selector(move[1])
        if not choice:
            return None
        if move[3]:
            return [{**base, "edit": "move", **choice, "at": int(move[3])}]
        return [{**base, "edit": "last" if move[2] in ("末尾", "最后", "最后面") else "first", **choice}]
    place = re.match(r"(?:在)?第?(\d+)项(?:动作)?(?:前|之前)(?:插入|添加)[：:]?", body)
    if place:
        base["at"] = int(place[1])
        body = body[place.end():]
    elif not (body.startswith("插入") or "换成" in body or "换人" in body or "御主" in body
              or ((backline or match[2]) and body.startswith(("追加", "补上")))):
        return None
    body = re.sub(r"^(?:追加|补上|插入)[：:]?", "", body)
    steps, outfits = [], []
    for clause in filter(None, re.split(r"然后|随后|接着|[，,；;、。]", body)):
        clause = clause.removeprefix("再")
        outfit = re.match(r"(?:用|使用)(.+?(?:服|制服))把", clause)
        if outfit:
            outfits.append({"op": "equip", "name": outfit[1]})
            clause = clause[outfit.end():]
        item = step(clause)
        if not item:
            return None
        steps.append(item)
    return [*outfits, {**base, "edit": "insert", "steps": steps}] if steps else None
