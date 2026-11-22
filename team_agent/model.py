"""Model backends for BBchannel plans. Draft edits are validated locally."""

import json
import os
from urllib import error, request

from .core import PlanError, norm, preview


INSTRUCTIONS = """你是 BBchannel 国服配队助手。把玩家的要求转换成 JSON。
只返回 {"ops":[...]} 或 {"question":"一个需要玩家回答的问题"}。
以下是独立请求的完整响应示例；多个战斗动作可放在同一个 ops 数组里：
{"ops":[{"op":"load","name":"已有预设文件名"}]}
{"ops":[{"op":"new","members":["一号位名称","二号位名称","三号位名称"],"assist":3}]}
{"ops":[{"op":"wave","wave":2,"mode":"replace","skills":[{"who":"3号位","skill":1,"to":"1号位"}]}]}
{"ops":[{"op":"move_skill","skill":"充能","to_wave":3}]}
编辑已有的某一项、固定前排队伍多回合的技能顺序时用 edit_turn。wave、turn、index、at 都从 1 开始，turn 默认 1。
{"ops":[{"op":"edit_turn","wave":2,"turn":1,"edit":"first","who":"二号位光狐","skill":1}]}
edit 可以是 first（本段最前）、last（本段最后）、move（at 指最终项号）、delete、retarget（to 指新目标）。
用 who 和 skill 或玩家明确指定的 index 选择一项。重复出现时程序会追问。
{"ops":[{"op":"edit_turn","wave":2,"turn":1,"edit":"insert","at":2,"steps":[{"who":"二号位光狐","skill":"充能","to":"公主"}]}]}
插入时 at 指插入前的项号，省略表示追加。只插入从者技能。同一条精确编辑可带 load、new 或 use_match。
整面替换与单项编辑须分开。严禁用 wave 重写整面来模拟单项编辑。
按玩家已登记的从者或可借助战找预设时，单独返回 {"ops":[{"op":"recommend"}]}。
临时试配用 {"ops":[{"op":"recommend","owned":[{"name":"公主","count":1},"光狐"],"supports":["光狐"]}]}。
owned 和 supports 可分别省略以沿用已登记名单。只列玩家明确给出的名字和数量。本地程序负责遍历预设。
询问名单用 {"ops":[{"op":"collection"}]}；玩家明确选中上次推荐的第几套时用 {"ops":[{"op":"use_match","index":1}]}。
查找队伍时不要直接加载预设；登记或移除从者请提示使用本地名单指令。
assist 可省略，程序会追问。玩家明确说“三号位光狐一技能给一号位公主”时，
直接按上例输出，who 和 to 使用 3号位、1号位。宝具照旧时省略 np。
玩家只说角色昵称时保留该昵称，由程序解析或追问。无需目标时省略 to。
同名角色的站位不明确时仍然返回带昵称的 ops，让本地程序按实际站位追问。
例如当前队伍有两只光狐，“狐狸给公主充能”的 who 写“光狐”、skill 写“充能”、to 写“公主”。
指明“好友那只”时可把 who 或 to 写为“助战”。不得自行假定助战在三号位。
玩家要把上次指定的技能挪到另一面时只返回 move_skill；程序会保留其目标并移至目标面技能末尾。
不得用两次 wave 重写整面来模拟移动。未指明具体技能时 move_skill 的 skill 写“技能”。
指定宝具时使用 np 数组，例如 ["1号位","2号位"]。
replace 只替换给出的字段，append 表示追加，清空用空数组。不要擅自清空另一项。
已经用过的设置继续沿用。新建队伍依站位给出三位前排从者。先执行技能后按序开宝具。
用户要求的操作无法表示时请说明并追问，不生成近似的动作。
没有模板、关卡和练度依据时，不自行编造打法。不会执行战斗或替玩家保存文件。"""


BIGMODEL_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
BIGMODEL_MODEL = "glm-4.6v"
CREDENTIAL_TARGET = "BBchannel.TeamAgent/BigModel"


def bigmodel_key():
    value = os.environ.get("ZHIPUAI_API_KEY", "").strip()
    if value:
        return value
    try:
        import win32cred
        from pywintypes import error as CredentialError
    except ImportError:
        return ""
    try:
        item = win32cred.CredRead(CREDENTIAL_TARGET, win32cred.CRED_TYPE_GENERIC, 0)
        return item["CredentialBlob"].decode("utf-16le")
    except (CredentialError, UnicodeError):
        return ""


def messages_for(text, session):
    lib = session.library
    names = set()
    needle = norm(text)
    for alias, matches in lib.aliases.items():
        if len(alias) >= 2 and alias in needle:
            names.update(matches)
    if session.cfg:
        names.update(session.cfg.get(f"servant_{i}_name") for i in range(3))
    catalog = {n: lib.data[n]["skill_name"] for n in sorted(names - {None}) if n in lib.data}
    recent = [f"第{wave}面技能列表第{index+1}个" for wave, index in session.recent_skills]
    context = {"已有预设": list(lib.presets()), "上次匹配结果（按编号顺序）": session.finder.names,
               "当前草案": preview(session.cfg), "上次指定的技能": recent,
               "角色及技能": catalog, "当前预设名": session.title}
    messages = [{"role": "system", "content": INSTRUCTIONS}]
    messages.extend({"role": role, "content": value} for role, value in session.history[-6:])
    messages.append({"role": "user", "content": json.dumps(context, ensure_ascii=False) + "\n玩家：" + text})
    return messages


def decode_plan(content):
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[-1][:-3].strip()
    result = json.loads(content)
    if isinstance(result, dict) and result.get("op") in ("load", "new", "wave", "move_skill", "collection", "recommend", "use_match", "edit_turn"):
        return [result]  # Session validates every field, including single-operation responses.
    if isinstance(result, dict) and set(result) == {"ops"} and isinstance(result["ops"], list):
        return result["ops"]
    if isinstance(result, dict) and set(result) == {"question"} and isinstance(result["question"], str):
        return result["question"]
    raise PlanError("模型返回的内容不是配队计划，请重新描述。")



class OllamaPlanner:
    def __init__(self, model):
        self.model = model

    def __call__(self, text, session):
        if not self.model.strip():
            raise PlanError("请填写本机已安装的 Ollama 模型名。")
        payload = {"model": self.model, "messages": messages_for(text, session), "stream": False,
                   "format": "json", "options": {"temperature": 0}}
        req = request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
        try:
            with request.urlopen(req, timeout=90) as response:
                return decode_plan(json.load(response)["message"]["content"])
        except PlanError:
            raise
        except (OSError, KeyError, AttributeError, TypeError, ValueError) as exc:
            raise PlanError("本机 Ollama 未返回可读计划，请检查服务和模型名。") from exc


class BigModelPlanner:
    def __init__(self, api_key, model=BIGMODEL_MODEL):
        self.model = model
        self.api_key = api_key.strip()

    def __call__(self, text, session):
        if not self.api_key:
            raise PlanError("请填写智谱 API Key，或设置 ZHIPUAI_API_KEY。")
        if not self.api_key.isascii() or any(c.isspace() for c in self.api_key):
            raise PlanError("智谱 API Key 格式有误，请检查输入。")
        payload = {"model": self.model, "messages": messages_for(text, session), "stream": False,
                   "thinking": {"type": "disabled"}}
        req = request.Request(BIGMODEL_URL, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"})
        req.add_unredirected_header("Authorization", "Bearer " + self.api_key)
        try:
            with request.urlopen(req, timeout=90) as response:
                return decode_plan(json.load(response)["choices"][0]["message"]["content"])
        except error.HTTPError as exc:
            hints = {401: "请检查 API Key。", 403: "请检查密钥和模型权限。",
                     429: "请检查账户额度、余额或调用频率。"}
            raise PlanError(f"智谱 API 请求失败（HTTP {exc.code}）。" + hints.get(exc.code, "请核对模型与服务状态。")) from None
        except PlanError:
            raise
        except OSError as exc:
            raise PlanError("无法连接智谱 API，请检查网络后重试。") from exc
        except (IndexError, KeyError, AttributeError, TypeError, ValueError) as exc:
            raise PlanError("智谱模型未返回可读的配队计划，请重新描述。") from exc
