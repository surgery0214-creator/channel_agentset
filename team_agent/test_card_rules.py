import copy

from .core import read_json
from .test_agent import AgentFixture


class CardRulesTest(AgentFixture):
    def setUp(self):
        super().setUp()
        self.s = self.session
        self.s.send("加载预设：摩根_光狐_光狐")
        first = dict(type=1, cards=["1B"], criticalStar=0, more_or_less=True)
        other = dict(type=2, cards=[], criticalStar=0, more_or_less=True)
        row = dict(card1=first, card2=other, card3=other.copy(), breakpoint=[False, False], colorFirst=True)
        strategy = copy.deepcopy(row)
        strategy["card2"] = dict(first, cards=["1B", "1A", "1Q", "2B"])
        self.s.cfg.update(round1_turn1_skill=[1, ["Start", 1, [1]], [2, 1], ["End"], 3],
                          round1_turn1_condition=[row], round1_turn1_strategy=[strategy],
                          round1_turn1_np=[1], round1_extraStrategy=[copy.deepcopy(strategy)])

    def test_named_explanation_uses_current_draft_without_touching_undo(self):
        snapshot = copy.deepcopy(self.s.cfg)
        previous = self.s.previous
        reply = self.s.send("解释摩根队第一面第二回合的条件，告诉我满足和不满足时各会执行哪些动作")
        self.assertIn("条件1　第1张 1号位红卡", reply)
        self.assertIn("满足时　1号位测试队长2技能给1号位测试队长", reply)
        self.assertIn("不满足时　跳过本段技能", reply)
        self.assertEqual(2, reply.count("依序执行"))
        self.assertIn("出卡方案1", reply)
        self.assertEqual(snapshot, self.s.cfg)
        self.assertIs(previous, self.s.previous)

    def test_replace_only_condition_and_export_undo(self):
        original = copy.deepcopy(self.s.cfg)
        expected = copy.deepcopy(original)
        expected["round1_turn1_condition"][0]["card1"]["cards"] = ["1A"]
        response = self.s.send("第一面第二回合条件1的第1张只看摩根的蓝卡")
        self.assertIn("红卡 → 1号位蓝卡", response)
        self.assertIn("不满足时", self.s.preview())
        self.assertEqual([(1, 2)], self.s.changed_turns)
        self.assertEqual(expected, self.s.cfg)
        self.s.send("导出 条件蓝卡")
        self.assertEqual(expected, read_json(self.lib.presets()["条件蓝卡"]))
        self.s.send("撤销")
        self.assertEqual(original, self.s.cfg)

    def test_priority_preserves_candidates_and_other_fields(self):
        expected = copy.deepcopy(self.s.cfg)
        expected["round1_turn1_strategy"][0]["card2"]["cards"] = ["1A", "1B", "1Q", "2B"]
        self.assertIn("已更新", self.s.send("第一面第二回合方案1的第2张优先一号位蓝卡"))
        self.assertEqual(expected, self.s.cfg)
        previous = self.s.previous
        for msg in ("第一面第二回合方案1的第2张优先三号位绿卡", "第一面第二回合条件3的第1张只看一号位蓝卡",
                    "第一面第二回合条件1的第3张只看一号位蓝卡"):
            self.assertIn("未完成", self.s.send(msg))
        self.assertEqual(expected, self.s.cfg)
        self.assertIs(previous, self.s.previous)

    def test_ambiguous_row_interrupt_and_model_edit_rollback(self):
        rows = self.s.cfg["round1_turn1_strategy"]
        rows.append(copy.deepcopy(rows[0]))
        original = copy.deepcopy(self.s.cfg)
        self.assertIn("哪一项", self.s.send("第一面第二回合方案的第2张优先一号位蓝卡"))
        self.s.send("取消")
        self.assertEqual(original, self.s.cfg)
        self.s.send("第一面第二回合方案的第2张优先一号位蓝卡")
        self.assertIn("已更新", self.s.send("第二个"))
        self.assertEqual(rows[0], self.s.cfg["round1_turn1_strategy"][0])
        self.s.send("撤销")
        op = dict(op="edit_card", wave=1, turn=2, section="condition", row=1, card=1, who="摩根", color="A")
        self.s.planner = lambda text, session: [op, dict(op, card=8)]
        self.assertIn("未完成", self.s.send("调整一下我的回合"))
        self.assertEqual(original, self.s.cfg)

    def test_mask_inversion_np_and_unknown_structure(self):
        row = copy.deepcopy(self.s.cfg["round1_turn1_condition"][0])
        self.s.cfg["round1_turn1_condition"].append(row)
        self.s.cfg["round1_turn1_skill"] = [["Start", 0, [0, 1], ["np", [1,"≥",100], [0], [0]]], 1, ["End"]]
        reply = self.s.send("解释第一面第二回合")
        self.assertIn("判断　条件2 匹配 且 1号位 NP ≥ 100%", reply)
        self.assertIn("满足时　跳过本段技能", reply)
        self.assertIn("不满足时　1号位测试队长1技能", reply)
        self.s.cfg["round1_turn1_skill"].pop()
        self.assertIn("缺少条件结束标记", self.s.send("解释第一面第二回合"))

    def test_explain_other_preset_preserves_selection(self):
        cfg, previous = self.s.cfg, self.s.previous
        request = dict(op="explain_turn", name=self.example, wave=2)
        self.s.planner = lambda text, session: [request]
        self.assertIn(self.example, self.s.send("帮我讲讲另一套队伍"))
        self.assertIs(cfg, self.s.cfg)
        self.assertIs(previous, self.s.previous)

    def test_atomic_load_and_edit_displays_both_branches(self):
        self.s.send("导出 测试条件基线")
        self.s.send("加载预设：" + self.example)
        op = dict(op="edit_card", wave=1, turn=2, section="condition", card=1, who="摩根", color="Q")
        reply = self.s.execute([dict(op="load", name="测试条件基线"), op])
        self.assertIn("第1张 1号位绿卡", reply)
        self.assertIn("不满足时　跳过本段技能", self.s.preview())
        self.s.send("撤销")
        self.assertEqual(self.example, self.s.title)

    def test_explanation_is_read_only_even_if_planner_returns_edit(self):
        self.assertIn("不满足时", self.s.send("解释一下第一面第二回合的条件"))
        original = copy.deepcopy(self.s.cfg)
        self.s.planner = lambda text, session: [dict(op="edit_turn", wave=1, turn=2, edit="delete", index=1)]
        self.assertIn("未完成", self.s.send("说明这一波为什么这么安排"))
        self.assertEqual(original, self.s.cfg)
