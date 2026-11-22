import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from .core import Library, PlanError, export_preset, preview, read_json, roster
from .dialogue import Session
from .model import BIGMODEL_MODEL, BIGMODEL_URL, BigModelPlanner, OllamaPlanner


class AgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.example = "爱尔奎特_光狐_光狐"
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "settings").mkdir()
        catalog = {
            "测试主攻": {"other_name": ["公主"], "skill_name": ["攻击", "自充", "守护"], "skill_type": [0, 0, 0]},
            "测试辅助": {"other_name": ["光狐"], "skill_name": ["充能", "支援", "增幅"], "skill_type": [1, 1, 1]},
            "测试术士": {"other_name": ["狐狸"], "skill_name": ["攻击", "守护", "增幅"], "skill_type": [0, 0, 0]},
            "测试队长": {"other_name": ["摩根"], "skill_name": ["攻击", "充能", "守护"], "skill_type": [0, 1, 0]},
        }
        (root / "servant_info_CH.json").write_text(json.dumps(catalog), encoding="utf-8")
        template = {"server": "CH", "assistIdx": 2, "usedServant": [0, 1]}
        for i, name in enumerate(["测试主攻", "测试辅助", "测试辅助", None, None, None]):
            template[f"servant_{i}_name"] = name
        for wave, skills in enumerate(([1], [[4, 1], [7, 1]], [2]), 1):
            template.update({f"round{wave}_turns": 1, f"round{wave}_turn0_skill": skills,
                             f"round{wave}_turn0_np": [1]})
        (root / "settings" / (cls.example + ".json")).write_text(json.dumps(template), encoding="utf-8")
        complex_team = dict(template, servant_0_name="测试队长", round1_turns=2,
                            round1_turn1_skill=[], round1_turn1_np=[])
        (root / "settings/摩根_光狐_光狐.json").write_text(json.dumps(complex_team), encoding="utf-8")
        local = dict(template)
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
        self.assertEqual(["测试辅助", "测试辅助", "测试主攻"], roster(cfg)[:3])
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

    def test_presets_load_and_complex_wave_is_protected(self):
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

    def test_friend_followup_moves_only_selected_skill_and_marks_both_waves(self):
        self.session.send("加载预设：" + self.example)
        before = copy.deepcopy(self.session.cfg)
        self.assertIn("哪一位", self.session.send("第二面追加光狐给公主充能"))
        self.assertIn("已更新", self.session.send("好友那只"))
        self.assertEqual(before["round2_turn0_skill"] + [[7, 1]], self.session.cfg["round2_turn0_skill"])
        result = self.session.send("把刚才的充能挪到第三面")
        expected = copy.deepcopy(before)
        expected["round3_turn0_skill"].append([7, 1])
        self.assertEqual(expected, self.session.cfg)
        self.assertEqual((2, 3), self.session.changed_waves)
        self.assertEqual(2, result.count("〔本次修改〕"))
        self.session.send("撤销")
        self.assertEqual(before["round3_turn0_skill"], self.session.cfg["round3_turn0_skill"])
        self.assertIn("已更新", self.session.send("把刚才的充能移到第一面"))

    def test_assist_reference_uses_uncommitted_team_and_actual_slot(self):
        self.assertIn("哪一位", self.session.send("新建队伍：光狐、公主、光狐，助战一号位。第二面光狐给公主充能"))
        self.assertIsNone(self.session.cfg)
        self.session.send("好友那只")
        self.assertEqual([[1, 2]], self.session.cfg["round2_turn0_skill"])
        self.session.send("第一面光狐给公主充能")
        self.session.send("自家那只")
        self.assertEqual([[7, 2]], self.session.cfg["round1_turn0_skill"])

    def test_two_recent_recharges_require_a_choice_and_model_switch_is_safe(self):
        self.session.send("加载预设：" + self.example)
        self.session.send("第二面二号位光狐给公主充能，三号位光狐给公主充能")
        before = copy.deepcopy(self.session.cfg)
        self.session.planner = lambda text, session: [{"op": "move_skill", "skill": "充能", "to_wave": 3}]
        self.assertIn("多个", self.session.send("把上次的充能移走"))
        self.assertEqual(before, self.session.cfg)
        self.session.planner = None
        self.session.send("好友那只")
        self.assertEqual([[4, 1]], self.session.cfg["round2_turn0_skill"])
        self.assertEqual([2, [7, 1]], self.session.cfg["round3_turn0_skill"])

    def test_invalid_move_is_atomic_and_loading_resets_reference(self):
        self.session.send("加载预设：摩根_光狐_光狐")
        self.session.send("第二面三号位光狐给摩根充能")
        before = copy.deepcopy(self.session.cfg)
        self.assertIn("多回合", self.session.send("把刚才的充能移到第一面"))
        self.assertIn("已经在", self.session.send("把刚才的充能移到第二面"))
        self.assertIn("没有可移动", self.session.send("把刚才的增幅移到第三面"))
        self.assertEqual(before, self.session.cfg)
        self.session.send("加载预设：" + self.example)
        self.assertIn("没有可移动", self.session.send("把刚才的充能移到第三面"))

    def test_model_cannot_guess_duplicate_slot_or_reload_pending_edits(self):
        self.session.send("加载预设：" + self.example)
        self.session.send("第一面公主三技能")
        before = copy.deepcopy(self.session.cfg)
        self.session.planner = lambda text, session: [
            {"op": "load", "name": self.example},
            {"op": "wave", "wave": 2, "skills": [{"who": "2号位", "skill": 1, "to": "1号位"}]}]
        self.assertIn("哪一位", self.session.send("双光狐那队，第二面让狐狸给公主充能"))
        self.assertEqual(before, self.session.cfg)
        self.session.send("好友那只")
        self.assertEqual([3], self.session.cfg["round1_turn0_skill"])
        self.assertEqual([[7, 1]], self.session.cfg["round2_turn0_skill"])

    def test_colloquial_fox_is_scoped_to_front_line(self):
        self.assertEqual("测试术士", self.lib.resolve("狐狸"))
        self.session.send("加载预设：" + self.example)
        self.assertIn("哪一位", self.session.send("第二面狐狸给公主充能"))
        self.session.send("好友那只")
        self.assertEqual([[7, 1]], self.session.cfg["round2_turn0_skill"])

    def test_missing_target_reply_is_authoritative_for_model_plan(self):
        self.session.send("加载预设：" + self.example)
        self.session.planner = lambda text, session: [
            {"op": "wave", "wave": 2, "skills": [{"who": "二号位", "skill": 1}]}]
        self.assertIn("给谁", self.session.send("第二面二号位光狐开一技能"))
        self.session.send("好友那只")
        self.assertIsNone(self.session.pending)
        self.assertEqual([[4, 3]], self.session.cfg["round2_turn0_skill"])

    def test_freeform_single_op_model_reply_and_explicit_move_share_semantics(self):
        self.session.send("加载预设：" + self.example)
        self.session.send("第二面三号位光狐一技能给公主")
        self.session.planner = BigModelPlanner("test-only")
        move = {"op": "move_skill", "skill": "充能", "to_wave": 3}
        cloud = {"choices": [{"message": {"content": json.dumps(move)}}]}
        with patch("team_agent.model.request.urlopen", return_value=io.BytesIO(json.dumps(cloud).encode())) as post:
            self.assertIn("已更新", self.session.send("上一步放的那个充能留到最后一波吧"))
            self.assertEqual([2, [7, 1]], self.session.cfg["round3_turn0_skill"])
            self.assertIn("已更新", self.session.send("把刚才的充能挪到第二面"))
            self.assertEqual([[7, 1]], self.session.cfg["round2_turn0_skill"])
            post.assert_called_once()

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

    def test_switch_providers_preserves_draft_and_separates_credentials(self):
        self.session.send("加载预设：" + self.example)
        change = {"ops": [{"op": "wave", "wave": 2, "skills": [{"who": "三号位", "skill": 1, "to": "公主"}]}]}
        cloud = {"choices": [{"message": {"content": json.dumps(change)}}]}
        local = {"message": {"content": json.dumps({"ops": [{"op": "wave", "wave": 3, "np": ["二号位"]}]})}}
        replies = [io.BytesIO(json.dumps(item).encode()) for item in (cloud, local)]
        with patch("team_agent.model.request.urlopen", side_effect=replies) as post:
            self.session.planner = BigModelPlanner("test-only")
            self.assertIn("已更新", self.session.send("第二面三号位光狐一技能给公主"))
            self.session.planner = OllamaPlanner("local-test")
            self.assertIn("已更新", self.session.send("第三面二号位开宝具"))
        first, second = [call.args[0] for call in post.call_args_list]
        self.assertEqual(BIGMODEL_URL, first.full_url)
        self.assertEqual(BIGMODEL_MODEL, json.loads(first.data)["model"])
        self.assertEqual("Bearer test-only", first.get_header("Authorization"))
        self.assertNotIn(b"test-only", first.data)
        self.assertEqual("http://127.0.0.1:11434/api/chat", second.full_url)
        self.assertIsNone(second.get_header("Authorization"))
        self.assertNotIn(b"test-only", second.data)
        self.assertEqual([[7, 1]], self.session.cfg["round2_turn0_skill"])
        self.assertEqual([2], self.session.cfg["round3_turn0_np"])

    def test_cloud_failure_and_missing_key_leave_draft_intact(self):
        self.session.planner = BigModelPlanner("")
        with patch("team_agent.model.request.urlopen") as post:
            self.session.send("加载预设：" + self.example)
            before = copy.deepcopy(self.session.cfg)
            self.assertIn("API Key", self.session.send("第二面公主开宝具"))
            post.assert_not_called()
            self.session.planner = BigModelPlanner("test-only")
            post.side_effect = HTTPError(BIGMODEL_URL, 401, "test-only", {}, None)
            result = self.session.send("第二面公主开宝具")
        self.assertIn("401", result)
        self.assertNotIn("test-only", result)
        self.assertEqual(before, self.session.cfg)
