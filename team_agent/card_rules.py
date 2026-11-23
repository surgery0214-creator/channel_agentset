"""Read native card rules and edit a single ordinary-card candidate list."""

import re

from .battle_edit import actions_for, describe, fields, formation, integer, marker, project
from .core import Choose, NUMBERS, PlanError, roster


COLORS = {"B": "红", "A": "蓝", "Q": "绿"}
COLOR_CODES = {name: code for code, name in COLORS.items()}
WHEN = r"第?([1-3一二三])面(?:第?([0-9一二三四五六]+)回合)?"
LABEL = {"condition": "条件", "strategy": "出卡方案"}


def number(text):
    return int(text.translate(NUMBERS))


def card_request(message):
    text = message.strip().rstrip("。？！?").replace(" ", "")
    match = re.fullmatch(r"(?:解释|说明)(?:一下)?(?:预设)?(.*?)" + WHEN +
                         r"(?:的)?(?:技能条件|出卡策略|出卡顺序|条件和出卡|条件)?"
                         r"(?:[，,](?:告诉我)?满足和不满足时各会执行哪些动作)?", text)
    if match:
        name, wave, turn = match.groups()
        op = dict(op="explain_turn", wave=number(wave), turn=number(turn or "1"))
        if name and name not in ("当前", "这队", "当前队伍"):
            op["name"] = name
        return op
    match = re.fullmatch(WHEN + r"[，,：:]?(?:把|将)?(条件|出卡方案|方案)([0-9一二三四五六]*)的?"
                         r"第?([1-3一二三])张(?:卡)?(只看|改为|改成|优先(?:改为)?)"
                         r"(.+?)的?([红蓝绿])卡", text)
    if match:
        wave, turn, label, row, card, verb, who, color = match.groups()
        section = "condition" if label == "条件" else "strategy"
        if (section == "condition" and verb.startswith("优先")) or (section == "strategy" and verb == "只看"):
            raise PlanError("条件用「只看」指定候选牌，出卡方案用「优先」调整已有候选的顺序。")
        op = dict(op="edit_card", wave=number(wave), turn=number(turn or "1"), section=section,
                  card=number(card), who=who, color=COLOR_CODES[color])
        if row:
            op["row"] = number(row)
        return op


def token_text(token):
    if isinstance(token, str) and re.fullmatch(r"[1-3][BAQ]", token):
        return f"{token[0]}号位{COLORS[token[1]]}卡"
    return f"原选项 {token!r}"


def card_text(spec):
    if not isinstance(spec, dict):
        return f"原设置 {spec!r}"
    kind, cards = spec.get("type"), spec.get("cards")
    if kind == 0 and isinstance(cards, list) and cards:
        return f"{cards[0]}号位宝具"
    if kind == 2:
        return "任意普通指令卡"
    if kind != 1 or not isinstance(cards, list):
        return f"原设置 {spec!r}"
    text = "、".join(map(token_text, cards)) or "候选列表为空"
    if spec.get("criticalStar") or spec.get("more_or_less") is False:
        sign = "≥" if spec.get("more_or_less", True) else "≤"
        text += f"（暴击阈值 {sign} {spec.get('criticalStar', 0)}）"
    return text


def row_text(row):
    if not isinstance(row, dict):
        return f"原方案 {row!r}"
    parts = [f"第{i}张 {card_text(row.get(f'card{i}'))}" for i in range(1, 4)]
    if row.get("breakpoint") not in (None, [], [False, False]):
        parts.append(f"原断点设置 {row['breakpoint']}")
    if row.get("colorFirst") is False:
        parts.append("原设置 colorFirst=False")
    return "；".join(parts)


def bit(value):
    return type(value) in (int, bool) and value in (0, 1)


def guard_text(start, conditions):
    if len(start) not in (3, 4) or not bit(start[1]):
        raise PlanError("未知条件块格式")
    mask = start[2]
    if not isinstance(mask, list) or not all(bit(x) for x in mask):
        raise PlanError("未知条件选择格式")
    parts = []
    if any(mask):
        if conditions is None:
            parts.append("从上一次条件记录中匹配启用的方案")
        elif not isinstance(conditions, list) or len(mask) < len(conditions):
            raise PlanError("条件选择与方案数量不符")
        else:
            enabled = [f"条件{i+1}" for i in range(len(conditions)) if mask[i]]
            if not enabled:
                raise PlanError("条件块未引用有效方案")
            parts.append(" 或 ".join(enabled) + " 匹配")
    if len(start) == 4:
        np = start[3]
        if not isinstance(np, list) or len(np) != 4 or np[0] != "np":
            raise PlanError("未知宝具量表条件")
        for slot, spec in enumerate(np[1:], 1):
            if not isinstance(spec, list) or not spec or not bit(spec[0]):
                raise PlanError("未知宝具量表条件")
            if spec[0]:
                if len(spec) != 3 or spec[1] not in ("≥", "≤") or type(spec[2]) is not int:
                    raise PlanError("未知宝具量表阈值")
                parts.append(f"{slot}号位 NP {spec[1]} {spec[2]}%")
    return " 且 ".join(parts) or "无附加条件（始终满足）"


def skill_lines(cfg, wave, turn, actions, conditions):
    lines, block, labels = [], None, []

    def flush():
        sequence = " → ".join(labels)
        if block is None:
            if sequence:
                lines.append("依序执行　" + sequence)
        else:
            test = guard_text(block, conditions)
            run = sequence or "本段没有技能"
            skip = "跳过本段技能"
            yes, no = (run, skip) if block[1] else (skip, run)
            lines.extend([f"判断　{test}", f"满足时　{yes}", f"不满足时　{no}"])
        labels.clear()

    for index, action in enumerate(actions):
        tag = marker(action)
        if tag == "Start":
            if block is not None:
                raise PlanError("嵌套条件块")
            flush()
            block = action
        elif tag == "End":
            if block is None or action != ["End"]:
                raise PlanError("条件结束标记未配对")
            flush()
            block = None
        else:
            try:
                party = roster(project(cfg, formation(cfg, wave, turn, index)))
            except PlanError:
                party = None
            labels.append(describe(action, party))
    if block is not None:
        raise PlanError("缺少条件结束标记")
    flush()
    return lines or ["本回合没有预设技能"]


def explain_turn(cfg, wave, turn):
    actions = actions_for(cfg, wave, turn)
    prefix = f"round{wave}_turn{turn-1}_"
    conditions = cfg.get(prefix + "condition")
    lines = [f"第{wave}面第{turn}回合"]
    if isinstance(conditions, list):
        lines.extend(f"条件{i}　{row_text(row)}" for i, row in enumerate(conditions, 1))
    try:
        lines.extend(skill_lines(cfg, wave, turn, actions, conditions))
    except PlanError as exc:
        lines.append(f"技能分支需在原程序核对（{exc}）　{actions!r}")
    np = cfg.get(prefix + "np")
    if np:
        lines.append("预设宝具顺序　" + " → ".join(f"{i}号位" for i in np))
    strategies = cfg.get(prefix + "strategy")
    if isinstance(strategies, list) and strategies:
        lines.append("技能结束后，按方案顺序匹配出卡")
        lines.extend(f"出卡方案{i}　{row_text(row)}" for i, row in enumerate(strategies, 1))
    elif strategies:
        lines.append(f"原出卡设置　{strategies!r}")
    else:
        lines.append("使用默认出卡")
    return "\n".join(lines)


def edit_card(lib, cfg, op, pick, player_text):
    fields(op, ("op", "wave", "section", "card", "who", "color"), ("turn", "row"))
    wave, turn = op["wave"], op.get("turn", 1)
    actions = actions_for(cfg, wave, turn)
    section, color, ref = op["section"], op["color"], op["who"]
    if section not in ("condition", "strategy") or color not in ("B", "A", "Q") or not isinstance(ref, str):
        raise PlanError("请指定条件或出卡方案、从者和红蓝绿卡色。")
    key = f"round{wave}_turn{turn-1}_{section}"
    rows = cfg.get(key)
    if not isinstance(rows, list) or not rows:
        raise PlanError(f"本回合没有可修改的{LABEL[section]}。")
    row = op.get("row")
    if row is None and len(rows) > 1:
        options = [f"{LABEL[section]}{i}" for i in range(1, len(rows)+1)]
        issue = Choose("要修改哪一项？", options)
        for i, label in enumerate(options, 1):
            issue.aliases[str(i)] = label
            issue.aliases[f"第{i}个"] = label
        row = options.index(pick(issue))+1
    row = integer(1 if row is None else row, 1, len(rows), "方案编号")
    card = integer(op["card"], 1, 3, "第几张牌")
    rule = rows[row-1]
    spec = rule.get(f"card{card}") if isinstance(rule, dict) else None
    if not isinstance(spec, dict) or type(spec.get("type")) is not int or spec["type"] != 1:
        raise PlanError("请选择带普通指令卡候选列表的牌位。")
    cards = spec.get("cards")
    if not isinstance(cards, list) or any(not isinstance(c, str) for c in cards):
        raise PlanError("原候选列表格式无法识别。")

    pos = re.fullmatch(r"([1-3])号位", ref.translate(NUMBERS))
    if pos and player_text is not None and pos[0] not in player_text.translate(NUMBERS):
        raise PlanError("请写明确切站位，或使用从者名。")
    if pos:
        slot = int(pos[1])
        if not any(roster(cfg)[3:]) and not roster(cfg)[slot-1]:
            raise PlanError(f"{slot}号位没有从者。")
    else:
        # A condition row can be evaluated at multiple points within a turn.
        points = [len(actions)] if section == "strategy" else [
            i for i, action in enumerate(actions) if marker(action) == "Start" and len(action) >= 3
            and isinstance(action[2], list) and len(action[2]) >= row and action[2][row-1]]
        if not points:
            raise PlanError("本回合没有技能块引用这项条件，请用明确站位编辑。")
        orders = {tuple(formation(cfg, wave, turn, i)) for i in points}
        slots = set()
        for order in sorted(orders):
            party = project(cfg, order)
            try:
                slot = lib.grounded_position(party, ref, player_text) if player_text else lib.position(party, ref)
            except Choose as issue:
                slot = lib.position(party, pick(issue))
            slots.add(slot)
        if len(slots) != 1:
            raise PlanError("这项条件在不同阵容下引用，请用明确站位编辑。")
        slot = slots.pop()
    token = f"{slot}{color}"
    if section == "strategy" and token not in cards:
        raise PlanError("该牌不在原方案的候选列表中，请选择已有候选来调整优先级。")
    updated = [token] if section == "condition" else [token, *(c for c in cards if c != token)]
    before = card_text(spec)
    new_spec = dict(spec, cards=updated)
    cfg[key] = [*rows[:row-1], {**rule, f"card{card}": new_spec}, *rows[row:]]
    return f"第{wave}面第{turn}回合 {LABEL[section]}{row} 第{card}张　{before} → {card_text(new_spec)}"
