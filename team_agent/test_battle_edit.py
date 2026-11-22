import copy
import json

from .core import read_json
from .test_agent import AgentFixture


class TurnEditTest(AgentFixture):
    def setUp(self):
        super().setUp()
        catalog = {
            "魔术礼装·迦勒底战斗服": {"SN": 6, "skill_name": ["加攻", "眩晕", "换人"], "skill_type": [0, 0, 2]},
            "决战用迦勒底制服": {"SN": 17, "skill_name": ["加攻", "回血", "换人"], "skill_type": [0, 1, 2]},
            "阿特拉斯院制服": {"SN": 1, "skill_name": ["无敌", "净化", "减冷却"], "skill_type": [1, 1, 3]},
        }
        (self.lib.root / "master_info.json").write_text(json.dumps(catalog), encoding="utf-8")
        self.s = self.session
        self.s.send("加载预设：摩根_光狐_光狐")

    def test_preserve_conditional_turn_and_other_settings(self):
        cfg = self.s.cfg
        cfg["round1_turn1_skill"] = [["Start", 1, [1]], 1, [5, 1], [4, 1], ["End"], 3]
        cfg["round1_turn1_strategy"] = {"cards": [1, 2]}
        cfg["round1_extraSkill"] = [[-2, 2]]
        expected = copy.deepcopy(cfg)
        expected["round1_turn1_skill"][1:4] = [[4, 1], 1, [5, 1]]
        self.assertIn("条件开始", self.s.send("查看第一面第二回合"))
        self.assertIn("已更新", self.s.send("第一面第二回合把二号位光狐一技能移到最前"))
        self.assertEqual(expected, self.s.cfg)
        self.assertEqual([(1, 2)], self.s.changed_turns)
        self.assertEqual(1, self.s.send("预览").count("本次修改"))
        self.assertIn("跨过条件", self.s.send("第一面第二回合把第2项移到第6项"))
        self.s.send("撤销")
        self.assertEqual(cfg, self.s.cfg)

    def test_single_action_insert_delete_and_retarget(self):
        before = copy.deepcopy(self.s.cfg)
        self.assertIn("已更新", self.s.send("第二面在第2项前插入二号位光狐二技能给摩根"))
        self.assertEqual([[4, 1], [5, 1], [7, 1]], self.s.cfg["round2_turn0_skill"])
        self.assertIn("已更新", self.s.send("第二面把第2项改给三号位"))
        self.assertEqual([5, 3], self.s.cfg["round2_turn0_skill"][1])
        self.assertIn("已更新", self.s.send("第二面删除二号位光狐二技能"))
        self.assertEqual(before, self.s.cfg)

    def test_resume_two_questions_cancel_and_one_undo(self):
        self.s.cfg["round2_turn0_skill"] = [[4, 1], [7, 2], [7, 1]]
        before = copy.deepcopy(self.s.cfg)
        msg = "第二面删除光狐一技能"
        self.assertIn("哪一位", self.s.send(msg))
        self.assertIn("第2项", self.s.send("好友那只"))
        self.assertIn("可以输入", self.s.send("不确定"))
        self.assertEqual(before, self.s.cfg)
        self.assertIn("已结束", self.s.send("取消"))
        self.s.send(msg)
        self.s.send("好友那只")
        self.assertIn("已更新", self.s.send("第二次"))
        self.assertEqual([[4, 1], [7, 2]], self.s.cfg["round2_turn0_skill"])
        self.s.send("撤销")
        self.assertEqual(before, self.s.cfg)

    def test_swap_and_targeted_master_round_trip(self):
        self.s.send("新建队伍：公主、光狐、光狐、摩根，助战三号位")
        self.s.send("御主礼装换成决战用迦勒底制服")
        before = copy.deepcopy(self.s.cfg)
        msg = "第二面让三号位光狐先给一号位公主充能，再用换人服把三号位光狐换成四号位摩根，然后摩根开一技能"
        result = self.s.send(msg)
        self.assertIn("换人后前排", result)
        expected = copy.deepcopy(before)
        expected.update(round2_turn0_skill=[[7, 1], [12, 4, 3], 7], usedServant=[0, 1, 3])
        self.assertEqual(expected, self.s.cfg)
        self.assertIn("已更新", self.s.send("第三面追加：御主二技能给摩根"))
        self.assertEqual([[11, 3]], self.s.cfg["round3_turn0_skill"])
        self.assertIn("已更新", self.s.send("第三面摩根放宝具，然后公主放宝具"))
        self.assertEqual([3, 1], self.s.cfg["round3_turn0_np"])
        self.assertIn("3号位测试队长宝具", self.s.send("预览"))
        self.s.send("导出 换人测试")
        self.assertEqual(self.s.cfg, read_json(self.lib.presets()["换人测试"]))
        self.s.send("加载预设：换人测试")
        self.assertIn("3号位测试队长", self.s.send("查看第三面第一回合"))

    def test_failed_swap_rolls_back_outfit_and_steps(self):
        before = copy.deepcopy(self.s.cfg)
        result = self.s.send("第二面让三号位光狐给摩根充能，再用换人服把三号位光狐换成四号位公主")
        self.assertIn("请选择御主礼装", result)
        self.assertEqual(before, self.s.cfg)
        self.assertIn("后排没有", self.s.send("魔术礼装·迦勒底战斗服"))
        self.assertEqual(before, self.s.cfg)
        self.assertIsNone(self.s.edit_run)

    def test_legacy_swap_order_and_resolved_support(self):
        self.s.send("新建队伍：摩根、光狐、公主、光狐，助战四号位")
        self.s.cfg.update(master_equip=6, round1_turn0_skill=[[12, 3, 4]])
        self.assertIn("已更新", self.s.send("第二面追加：好友那只给摩根充能"))
        self.assertEqual([[7, 1]], self.s.cfg["round2_turn0_skill"])
        self.s.cfg["round1_turn0_skill"] = [["Start", 1, [1]], [12, 3, 4], ["End"]]
        self.assertIn("条件换人", self.s.send("第二面追加：好友那只给摩根充能"))

    def test_planner_entry_cannot_guess_duplicate(self):
        self.s.planner = lambda text, session: [{"op":"edit_turn", "wave":2, "turn":1,
                                                  "edit":"delete", "who":"3号位", "skill":1}]
        self.assertIn("哪一位", self.s.send("帮我删掉第二面的狐狸充能"))
        self.assertIn("已更新", self.s.send("自己的那只"))
        self.assertEqual([[7, 1]], self.s.cfg["round2_turn0_skill"])

    def test_fill_empty_bench_and_preserve_native_fields(self):
        before = copy.deepcopy(self.s.cfg)
        self.assertIn("已更新", self.s.send("四号位放公主"))
        expected = dict(before, servant_3_name="测试主攻")
        self.assertEqual(expected, self.s.cfg)
        self.assertIn("已有从者", self.s.send("四号位放摩根"))
        self.s.send("撤销")
        self.assertEqual(before, self.s.cfg)

    def test_incompatible_existing_skill_after_swap_aborts_transaction(self):
        self.s.send("新建队伍：公主、光狐、光狐、摩根，助战三号位")
        self.s.send("御主礼装换成迦勒底战斗服")
        self.s.send("第三面追加：三号位光狐一技能给公主")
        before = copy.deepcopy(self.s.cfg)
        reply = self.s.send("第二面把三号位光狐换成四号位摩根")
        self.assertIn("技能目标不符", reply)
        self.assertEqual(before, self.s.cfg)

    def test_protect_native_replacement_and_special_skill(self):
        self.s.cfg["round1_turn0_replace"] = {"3": 4, "4": 3}
        self.assertIn("退场补位", self.s.send("第一面删除第1项"))
        self.assertIn("退场补位", self.s.send("第二面在第1项前插入摩根三技能"))
        del self.s.cfg["round1_turn0_replace"]
        self.s.cfg["round1_turn0_skill"] = [1, [-2, 2], 3]
        self.assertIn("跨过", self.s.send("第一面把第3项移到第1项"))
        self.assertIn("特殊动作", self.s.send("第一面删除第2项"))

    def test_master_skill_target_prompt_and_equipment_conflict(self):
        self.s.send("御主礼装换成阿特拉斯院制服")
        before = copy.deepcopy(self.s.cfg)
        self.assertIn("给谁", self.s.send("第二面插入御主三技能"))
        self.assertEqual(before, self.s.cfg)
        self.assertIn("已更新", self.s.send("好友那只"))
        self.assertEqual([12, 3], self.s.cfg["round2_turn0_skill"][-1])
        self.assertIn("改变含义", self.s.send("御主礼装换成决战用迦勒底制服"))
        self.assertEqual(1, self.s.cfg["master_equip"])

    def test_validate_swap_after_companion_edits_in_same_transaction(self):
        self.s.send("新建队伍：公主、光狐、光狐、摩根，助战三号位")
        self.s.send("御主礼装换成迦勒底战斗服")
        self.s.send("第三面追加：三号位光狐一技能给公主")
        before = copy.deepcopy(self.s.cfg)
        ops = [{"op": "edit_turn", "wave": 2, "edit": "insert",
                "steps": [{"swap_out": "三号位", "swap_in": "摩根"}]},
               {"op": "edit_turn", "wave": 3, "edit": "delete", "index": 1}]
        self.assertIn("已更新", self.s.execute(ops))
        self.assertEqual([], self.s.cfg["round3_turn0_skill"])
        self.s.send("撤销")
        self.assertEqual(before, self.s.cfg)
