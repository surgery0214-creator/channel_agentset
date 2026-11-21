"""Optional Ollama planner. Only the local validator can modify a draft."""

import json
from urllib import error, request

from .core import PlanError, norm, preview


INSTRUCTIONS = """你是 BBchannel 国服配队助手。把玩家的要求转换成 JSON。
只返回 {"ops":[...]} 或 {"question":"一个需要玩家回答的问题"}。
可用操作如下，按顺序执行：
{"op":"load","name":"已有预设文件名"}
{"op":"new","members":["一号位名称","二号位名称","三号位名称"],"assist":3}
{"op":"wave","wave":2,"mode":"replace","skills":[{"who":"光狐","skill":1,"to":"公主"}],"np":["公主"]}
assist 可省略，程序会追问。who、to、np 用玩家原话里的角色简称或明确站位；
遇到同名角色保留简称以便程序追问，不要自行选位。无需目标时省略 to。
replace 只替换给出的字段，append 表示追加，清空用空数组。不要擅自清空另一项。
已经用过的设置继续沿用。固定三位前排，每面一个回合，先执行技能后按序开宝具。
用户要求的操作无法表示时请说明并追问，不生成近似的动作。
没有模板、关卡和练度依据时，不自行编造打法。不会执行战斗或替玩家保存文件。"""


class OllamaPlanner:
    def __init__(self, model):
        self.model = model

    def __call__(self, text, session):
        lib = session.library
        names = set()
        needle = norm(text)
        for alias, matches in lib.aliases.items():
            if len(alias) >= 2 and alias in needle:
                names.update(matches)
        if session.cfg:
            names.update(session.cfg.get(f"servant_{i}_name") for i in range(3))
        catalog = {n: lib.data[n]["skill_name"] for n in sorted(names - {None}) if n in lib.data}
        context = {"已有预设": list(lib.presets()), "当前草案": preview(session.cfg),
                   "角色及技能": catalog, "当前预设名": session.title}
        messages = [{"role": "system", "content": INSTRUCTIONS}]
        messages.extend({"role": role, "content": value} for role, value in session.history[-6:])
        messages.append({"role": "user", "content": json.dumps(context, ensure_ascii=False) + "\n玩家：" + text})
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "format": "json", "options": {"temperature": 0}}
        req = request.Request("http://127.0.0.1:11434/api/chat",
                              data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
        try:
            with request.urlopen(req, timeout=90) as response:
                result = json.loads(json.load(response)["message"]["content"])
        except (error.URLError, TimeoutError, OSError, KeyError, TypeError, ValueError) as exc:
            raise PlanError(f"本机 Ollama 模型 {self.model} 未返回可读计划（{exc}）。可用「帮助」里的离线句式。") from exc
        if isinstance(result, dict) and set(result) == {"ops"} and isinstance(result["ops"], list):
            return result["ops"]
        if isinstance(result, dict) and set(result) == {"question"} and isinstance(result["question"], str):
            return result["question"]
        raise PlanError("模型返回的内容不是配队计划，请重新描述。")
