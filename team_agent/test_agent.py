import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from .core import Library, PlanError, ROOT, export_preset, preview, read_json, roster
from .dialogue import Session
from .model import OllamaPlanner


class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.example = "爱尔奎特_光狐_光狐"
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "settings").mkdir()
        examples = [cls.example, "摩根_光狐_光狐", "宇宙凛_Caber_Caber",
                    "泳9_90++爱尔奎特_黑贞_水小黑_妖蛾子", "ex2冠位战2T梅莉_莉莉丝_妖蛾子_妖蛾子"]
        for name in examples:
            filename = name + ".json"
            shutil.copyfile(ROOT / "settings" / filename, root / "settings" / filename)
        shutil.copyfile(ROOT / "servant_info_CH.json", root / "servant_info_CH.json")
        local = read_json(root / "settings" / (cls.example + ".json"))
        local.update(connectMode="测试设备", dropImage=None, allowOtherApple=0)
        (root / "scripts_settings.json").write_text(json.dumps({"page0": local}), encoding="utf-8")
        cls.lib = Library(root)

    def setUp(self):
        self.session = Session(self.lib)

    def test_single_message_edits_only_second_wave(self):
        source = self.lib.load(self.example)
        result = self.session.send("用现有的公主双光狐队。第二面只让三号位光狐开一技能给公主，然后公主开宝具。其他照旧。")
        self.assertIn("已更新", result)
        source["round2_turn0_skill"] = [[7, 1]]
        self.assertEqual(source, self.session.cfg)

    def test_double_clarification_is_atomic(self):
        self.assertIn("哪一位", self.session.send("用公主双光狐队。第二面光狐一技能"))
        self.assertIsNone(self.session.cfg)
        self.assertIn("给谁", self.session.send("二号位"))
        self.assertIsNone(self.session.cfg)
        self.session.send("1")
        self.assertEqual([[4, 1]], self.session.cfg["round2_turn0_skill"])

    def test_new_team_and_support_position(self):
        self.assertIn("助战", self.session.send("新建队伍：光狐、光狐、公主"))
        self.session.send("一号位")
        cfg = self.session.cfg
        self.assertEqual(0, cfg["assistIdx"])
        self.assertEqual([1, 2], cfg["usedServant"])
        self.assertEqual(["光之高扬斯卡娅", "光之高扬斯卡娅", "ArchetypeEarth"], roster(cfg)[:3])
        self.assertIn("已更新", self.session.send("第一面二号位光狐给公主充能，然后公主开宝具。第二面公主二技能"))
        self.assertEqual([[4, 3]], self.session.cfg["round1_turn0_skill"])
        self.assertEqual([3], self.session.cfg["round1_turn0_np"])
        self.assertEqual([8], self.session.cfg["round2_turn0_skill"])
        self.assertEqual([], self.session.cfg["round2_turn0_np"])

    def test_append_clear_np_and_undo(self):
        self.session.send("用公主双光狐队")
        old = self.session.cfg
        self.session.send("第二面追加三号位光狐三技能给公主")
        self.assertEqual(old["round2_turn0_skill"] + [[9, 1]], self.session.cfg["round2_turn0_skill"])
        self.session.send("第二面不开宝具")
        self.assertEqual([], self.session.cfg["round2_turn0_np"])
        self.session.send("撤销")
        self.assertEqual([1], self.session.cfg["round2_turn0_np"])

    def test_bad_last_action_does_not_partially_apply(self):
        self.session.send("用公主双光狐队")
        before = copy.deepcopy(self.session.cfg)
        self.assertIn("未完成", self.session.send("第一面公主放宝具。第二面公主二技能给公主"))
        self.assertEqual(before, self.session.cfg)
        self.assertIn("未完成", self.session.send("第二面公主开宝具，然后公主一技能"))
        self.assertEqual(before, self.session.cfg)

    def test_position_name_must_match(self):
        self.session.send("用公主双光狐队")
        self.assertIn("不符", self.session.send("第二面一号位光狐一技能给公主"))
        self.assertIn("不是可用的前排", self.session.send("第二面四号位开宝具"))

    def test_cancel_and_preserve_selected_preset(self):
        self.session.send("用公主双光狐队")
        before = copy.deepcopy(self.session.cfg)
        self.session.send("第二面光狐给公主充能")
        self.assertIn("2号位 / 3号位", self.session.send("四号位"))
        self.session.send("取消")
        self.assertIsNone(self.session.pending)
        self.assertEqual(before, self.session.cfg)

    def test_supplied_presets_load_and_complex_wave_is_protected(self):
        for name in self.lib.presets():
            with self.subTest(name=name):
                self.assertIn("第1面", preview(self.lib.load(name)))
        self.session.send("加载预设：摩根_光狐_光狐")
        before = copy.deepcopy(self.session.cfg)
        self.assertIn("多回合", self.session.send("第一面摩根开宝具"))
        self.assertEqual(before, self.session.cfg)

    def test_export_and_reopen_through_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings").mkdir()
            for filename in ("servant_info_CH.json", "scripts_settings.json"):
                shutil.copyfile(self.lib.root / filename, root / filename)
            shutil.copyfile(self.lib.presets()[self.example], root / "settings" / (self.example + ".json"))
            session = Session(Library(root))
            session.send("用公主双光狐队。第二面三号位光狐给公主充能")
            self.assertIn("已另存预设", session.send("导出 我的队伍"))
            dest = root / "settings" / "我的队伍.json"
            self.assertEqual(session.cfg, read_json(dest))
            self.assertIn("同名", session.send("导出 我的队伍"))
            self.assertIn("已更新", session.send("加载预设：我的队伍"))
            self.assertEqual([[7,1]], session.cfg["round2_turn0_skill"])
            self.assertEqual((root / "scripts_settings.json").read_bytes(),
                             (self.lib.root / "scripts_settings.json").read_bytes())
            self.assertEqual((root / "settings" / (self.example + ".json")).read_bytes(),
                             self.lib.presets()[self.example].read_bytes())

    def test_export_rejects_invalid_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("..", "../escape", r"..\escape", "c:drive", "CON", "nul.test", "a?", ".hidden", ""):
                with self.subTest(name=name), self.assertRaises(PlanError):
                    export_preset({}, Path(tmp), name)
            self.assertEqual([], list(Path(tmp).iterdir()))

    def test_ollama_uses_same_validator(self):
        plan = {"ops": [{"op": "load", "name": self.example},
                        {"op": "wave", "wave": 2, "skills": [{"who": "光狐", "skill": 1, "to": "公主"}]}]}
        self.session.planner = OllamaPlanner("local-test")
        response = {"message": {"content": json.dumps(plan)}}
        with patch("team_agent.model.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as post:
            self.assertIn("哪一位", self.session.send("帮我调整一下公主组的第二波充能手顺"))
        body = json.loads(post.call_args.args[0].data)
        self.assertEqual("local-test", body["model"])
        self.assertEqual("json", body["format"])
        self.session.send("三号位")
        self.assertEqual([[7,1]], self.session.cfg["round2_turn0_skill"])

    def test_invalid_model_output_preserves_draft(self):
        self.session.send("用公主双光狐队")
        before = copy.deepcopy(self.session.cfg)
        self.session.planner = lambda text, session: [{"op": "wave", "wave": True, "np": ["公主"]}]
        self.assertIn("未完成", self.session.send("帮我重新安排一下"))
        self.assertEqual(before, self.session.cfg)
