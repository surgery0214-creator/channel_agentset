import copy

from .core import PlanError, read_json
from .test_agent import AgentFixture


class TurnEditTest(AgentFixture):
    def setUp(self):
        super().setUp()
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

    def test_planner_entry_cannot_guess_duplicate(self):
        self.s.planner = lambda text, session: [{"op":"edit_turn", "wave":2, "turn":1,
                                                  "edit":"delete", "who":"3号位", "skill":1}]
        self.assertIn("哪一位", self.s.send("帮我删掉第二面的狐狸充能"))
        self.assertIn("已更新", self.s.send("自己的那只"))
        self.assertEqual([[7, 1]], self.s.cfg["round2_turn0_skill"])

    def test_protect_native_replacement_and_special_skill(self):
        self.s.cfg["round1_turn0_replace"] = {"3": 4, "4": 3}
        self.assertIn("退场补位", self.s.send("第一面删除第1项"))
        self.assertIn("退场补位", self.s.send("第二面在第1项前插入摩根三技能"))
        del self.s.cfg["round1_turn0_replace"]
        self.s.cfg["round1_turn0_skill"] = [1, [-2, 2], 3]
        self.assertIn("跨过", self.s.send("第一面把第3项移到第1项"))
        self.assertIn("特殊动作", self.s.send("第一面删除第2项"))

    def test_target_edit_survives_export_and_reload(self):
        self.s.cfg.update(round3_turn0_skill=[7, [-2, 2], [11, 1]], round3_extraSkill=[7, [-2, 2], [11, 1]])
        expected = copy.deepcopy(self.s.cfg)
        expected["round3_turn0_skill"][-1] = [11, 2]
        self.assertIn("已更新", self.s.send("第三面把第3项改给二号位"))
        self.s.send("导出 修改目标")
        self.assertEqual(expected, read_json(self.lib.presets()["修改目标"]))
        self.s.send("加载预设：修改目标")
        self.assertEqual(expected, self.s.cfg)

    def test_fixed_front_and_supported_operations(self):
        before = copy.deepcopy(self.s.cfg)
        for op in ({"op": "equip", "name": "换人服"}, {"op": "bench", "slot": 4, "name": "摩根"},
                   {"op": "new", "members": ["公主", "光狐", "光狐", "摩根"], "assist": 3},
                   {"op": "edit_turn", "wave": 2, "edit": "insert", "steps": [{"master": 1}]},
                   {"op": "edit_turn", "wave": 2, "edit": "insert", "steps": [{"swap_out": "3号位", "swap_in": "4号位"}]}):
            with self.subTest(op=op), self.assertRaises(PlanError):
                self.s.execute([op])
            self.assertEqual(before, self.s.cfg)
