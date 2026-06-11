'''
Tests for parsing kv control statements (if/elif/else, for, slot).

These only cover the parser stage (:class:`kivy.lang.parser.Parser`);
builder/runtime behavior is tested separately.
'''

import unittest

from kivy.lang.parser import Parser, ParserControlRule, ParserException


def parse_rule(kv):
    '''Parse kv content and return the first rule.'''
    return Parser(content=kv).rules[0][1]


class ControlStatementParseTestCase(unittest.TestCase):

    def assert_parse_error(self, kv, message_part):
        with self.assertRaises(ParserException) as cm:
            Parser(content=kv)
        self.assertIn(message_part, str(cm.exception))

    def test_if_chain_merged_into_branches(self):
        rule = parse_rule('''
<W@Widget>:
    if self.expanded:
        Label:
            text: 'open'
    elif self.collapsed:
        Label:
            text: 'mid'
    else:
        Label:
            text: 'closed'
''')
        self.assertTrue(rule.has_controls)
        self.assertEqual(len(rule.children), 1)
        ctl = rule.children[0]
        self.assertIsInstance(ctl, ParserControlRule)
        self.assertEqual(ctl.kind, 'if')
        self.assertEqual(
            [b.cond_src for b in ctl.branches],
            ['self.expanded', 'self.collapsed', None])
        self.assertEqual([len(b.children) for b in ctl.branches], [1, 1, 1])
        self.assertEqual(
            ctl.selector_prop.value,
            '0 if (self.expanded) else 1 if (self.collapsed) else 2')
        self.assertIn(['self', 'expanded'], ctl.selector_prop.watched_keys)
        self.assertIn(['self', 'collapsed'], ctl.selector_prop.watched_keys)

    def test_if_without_else_selects_minus_one(self):
        ctl = parse_rule('''
<W@Widget>:
    if self.x:
        Label:
''').children[0]
        self.assertEqual(ctl.selector_prop.value, '0 if (self.x) else -1')

    def test_two_sibling_if_chains_stay_separate(self):
        rule = parse_rule('''
<W@Widget>:
    if self.a:
        Label:
    if self.b:
        Label:
    else:
        Label:
''')
        self.assertEqual(len(rule.children), 2)
        self.assertEqual(len(rule.children[0].branches), 1)
        self.assertEqual(len(rule.children[1].branches), 2)

    def test_widget_between_if_and_else_is_an_error(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.a:
        Label:
    Label:
    else:
        Label:
''', '"else" must immediately follow')

    def test_for_with_tuple_target_filter_and_key(self):
        ctl = parse_rule('''
<W@Widget>:
    for item, index in enumerate(self.items) if item.visible:
        key: item.uid
        Label:
            text: str(item)
''').children[0]
        self.assertEqual(ctl.kind, 'for')
        self.assertEqual(ctl.target_names, ['item', 'index'])
        self.assertEqual(
            ctl.iterator_prop.value,
            '[(item, index,) for item, index in (enumerate(self.items))'
            ' if (item.visible)]')
        self.assertEqual(ctl.key_prop.value, 'item.uid')
        self.assertEqual(len(ctl.children), 1)

    def test_for_watched_keys_exclude_loop_targets(self):
        ctl = parse_rule('''
<W@Widget>:
    for item in self.items if item.visible and root.active:
        key: item.uid
        Label:
''').children[0]
        self.assertEqual(
            sorted(map(tuple, ctl.iterator_prop.watched_keys)),
            [('root', 'active'), ('self', 'items')])
        self.assertIsNone(ctl.key_prop.watched_keys)

    def test_for_star_target(self):
        ctl = parse_rule('''
<W@Widget>:
    for first, *rest in self.rows:
        Label:
''').children[0]
        self.assertEqual(ctl.target_names, ['first', 'rest'])

    def test_slot_named_and_default(self):
        rule = parse_rule('''
<W@Widget>:
    slot header:
        Label:
    slot:
''')
        named, default = rule.children
        self.assertEqual(named.kind, 'slot')
        self.assertEqual(named.slot_name, 'header')
        self.assertEqual(len(named.children), 1)
        self.assertEqual(default.slot_name, '')
        self.assertEqual(default.children, [])

    def test_nested_controls(self):
        rule = parse_rule('''
<W@Widget>:
    for row in self.rows:
        BoxLayout:
            if row.title:
                Label:
                    text: row.title
''')
        outer = rule.children[0]
        box = outer.children[0]
        self.assertTrue(box.has_controls)
        self.assertEqual(box.children[0].kind, 'if')

    def test_colon_inside_condition_braces(self):
        ctl = parse_rule('''
<W@Widget>:
    if {1: 2}.get(self.x):
        Label:
''').children[0]
        self.assertEqual(ctl.branches[0].cond_src, '{1: 2}.get(self.x)')

    def test_trailing_comment_after_colon(self):
        ctl = parse_rule('''
<W@Widget>:
    if self.x:  # visible?
        Label:
''').children[0]
        self.assertEqual(ctl.branches[0].cond_src, 'self.x')

    def test_keyword_prefixed_names_still_parse_as_rules(self):
        # 'format', 'iffy', 'forward' must not be mistaken for keywords
        rule = parse_rule('''
<W@Widget>:
    format: 'png'
    iffy: 1
    forward: True
''')
        self.assertFalse(rule.has_controls)
        self.assertEqual(
            sorted(rule.properties), ['format', 'forward', 'iffy'])

    def test_orphan_else(self):
        self.assert_parse_error('''
<W@Widget>:
    Label:
    else:
        Label:
''', '"else" must immediately follow')

    def test_orphan_elif(self):
        self.assert_parse_error('''
<W@Widget>:
    elif self.x:
        Label:
''', '"elif" must immediately follow')

    def test_else_after_else(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        Label:
    else:
        Label:
    else:
        Label:
''', '"else" must immediately follow')

    def test_else_after_for(self):
        self.assert_parse_error('''
<W@Widget>:
    for x in self.items:
        Label:
    else:
        Label:
''', '"else" after "for" is not supported')

    def test_reserved_keywords(self):
        for kw in ('while', 'match', 'case'):
            self.assert_parse_error('''
<W@Widget>:
    %s self.x:
        Label:
''' % kw, 'reserved for future kv control statements')

    def test_top_level_control_forbidden(self):
        self.assert_parse_error('''
if True:
    Label:
''', 'not allowed at the top level')

    def test_control_in_canvas_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    canvas:
        if self.x:
            Color:
''', 'not allowed inside canvas')

    def test_control_nested_under_canvas_instruction_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    canvas:
        Color:
            if self.x:
                Rectangle:
''', 'not allowed inside canvas')

    def test_walrus_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if (y := self.x):
        Label:
''', 'assignment expressions')

    def test_loop_target_cannot_shadow_reserved(self):
        for name in ('self', 'root', 'app', 'dp'):
            self.assert_parse_error('''
<W@Widget>:
    for %s in self.items:
        Label:
''' % name, 'cannot shadow the reserved name')

    def test_property_inside_if_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        text: 'hi'
''', 'cannot be made conditional')

    def test_id_inside_if_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        id: nope
        Label:
''', '"id" is not allowed')

    def test_id_on_widget_inside_control_block_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        Label:
            id: nope
''', '"id" is not allowed on widgets inside')
        self.assert_parse_error('''
<W@Widget>:
    for x in self.items:
        Label:
            id: nope
''', '"id" is not allowed on widgets inside')
        self.assert_parse_error('''
<W@Widget>:
    slot header:
        Label:
            id: nope
''', '"id" is not allowed on widgets inside')

    def test_id_deep_inside_control_block_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        BoxLayout:
            BoxLayout:
                Label:
                    id: nope
''', '"id" is not allowed on widgets inside')

    def test_id_outside_control_block_still_allowed(self):
        rule = parse_rule('''
<W@Widget>:
    Label:
        id: fine
    if self.x:
        Label:
''')
        self.assertEqual(rule.children[0].id, 'fine')

    def test_handler_inside_if_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        on_touch_down: pass
        Label:
''', 'event handlers are not allowed')

    def test_canvas_inside_if_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        canvas:
            Color:
''', 'canvas is not allowed')

    def test_key_outside_for_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        key: 1
        Label:
''', '"key:" is only allowed inside a "for" block')

    def test_empty_if_body(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
    Label:
''', 'requires at least one child widget')

    def test_empty_for_body(self):
        self.assert_parse_error('''
<W@Widget>:
    for x in self.items:
    Label:
''', 'requires at least one child widget')

    def test_missing_colon(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x
        Label:
''', "expected ':'")

    def test_inline_body_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x: Label()
''', "unexpected content after ':'")

    def test_if_without_condition(self):
        self.assert_parse_error('''
<W@Widget>:
    if:
        Label:
''', 'requires a condition expression')

    def test_else_with_expression(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        Label:
    else self.y:
        Label:
''', '"else" takes no expression')

    def test_multiple_for_clauses_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    for x in self.a for y in self.b:
        Label:
''', 'single "for ... in ..." clause')

    def test_async_for_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    async for x in self.items:
        Label:
''', '')

    def test_invalid_slot_name(self):
        for name in ('1bad', 'two words', 'if'):
            self.assert_parse_error('''
<W@Widget>:
    slot %s:
        Label:
''' % name, 'invalid slot name')


if __name__ == '__main__':
    unittest.main()
