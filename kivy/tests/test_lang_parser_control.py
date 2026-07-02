'''
Tests for parsing kv control statements (if/elif/else).

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

    def test_colon_inside_condition_braces(self):
        ctl = parse_rule('''
<W@Widget>:
    if {1: 2}.get(self.x):
        Label:
''').children[0]
        self.assertEqual(ctl.branches[0].cond_src, '{1: 2}.get(self.x)')

    def test_bare_lambda_colon_in_header(self):
        # the block colon is the last depth-0 colon, so a bare lambda's own
        # colon stays inside the header
        ctl = parse_rule('''
<W@Widget>:
    if lambda: 1:
        Label:
''').children[0]
        self.assertEqual(ctl.branches[0].cond_src, 'lambda: 1')

    def test_scoped_id_cannot_be_app(self):
        # unlike a static id, a scoped id rewrites its references, so `app`
        # would silently hijack the app proxy
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        Label:
            id: app
''', 'cannot be "self", "root" or "app"')

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

    def test_non_control_keywords_rejected(self):
        # while/match/case are not kv control statements; they fall through to
        # the ordinary class/property path and are rejected as invalid names.
        # Nothing is reserved: adding such a statement later is additive,
        # exactly like this feature itself.
        for kw in ('while', 'match', 'case'):
            self.assert_parse_error('''
<W@Widget>:
    %s self.x:
        Label:
''' % kw, 'Invalid property name')

    def test_keyword_named_bare_properties_still_parse(self):
        # bare names that are Python keywords elsewhere stay plain kv
        # properties
        rule = parse_rule('''
<W@Widget>:
    match: 1
    case: 2
''')
        self.assertEqual(sorted(rule.properties), ['case', 'match'])

    def test_top_level_control_forbidden(self):
        self.assert_parse_error('''
if True:
    Label:
''', 'not allowed at the top level')

    def test_walrus_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if (y := self.x):
        Label:
''', 'assignment expressions')

    def test_property_inside_if_allowed(self):
        rule = parse_rule('''
<W@Widget>:
    if self.x:
        text: 'hi'
''')
        branch = rule.children[0].branches[0]
        self.assertIn('text', branch.properties)
        self.assertEqual(branch.children, [])

    def test_full_body_inside_if_allowed(self):
        # children + property + canvas + handler all live in one branch
        rule = parse_rule('''
<W@Widget>:
    if self.x:
        my_prop: self.width
        on_touch_down: pass
        canvas:
            Color:
        Label:
    else:
        my_prop: 0
''')
        ctl = rule.children[0]
        on_branch, off_branch = ctl.branches
        self.assertIn('my_prop', on_branch.properties)
        self.assertEqual(len(on_branch.handlers), 1)
        self.assertIsNotNone(on_branch.canvas_root)
        self.assertEqual(len(on_branch.children), 1)
        self.assertIn('my_prop', off_branch.properties)

    def test_id_inside_if_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    if self.x:
        id: nope
        Label:
''', '"id" is not allowed')

    def test_id_inside_if_is_reactive_scoped(self):
        # a widget id inside an if is redirected onto a rule-level scope
        # object so it can mount/unmount reactively; references to it are
        # reachable across the whole rule
        rule = parse_rule('''
<W@Widget>:
    if self.x:
        Label:
            id: nope
    Button:
        disabled: nope is None
''')
        self.assertIsNotNone(rule.id_scope_key)
        self.assertEqual(rule.id_scope_names, ['nope'])
        lbl = rule.children[0].branches[0].children[0]
        self.assertIsNone(lbl.id)               # the static id is cleared
        self.assertEqual(lbl.scope_id, (rule.id_scope_key, 'nope'))
        # the sibling reference was rewritten onto the scope
        btn = rule.children[1]
        self.assertIn(rule.id_scope_key, btn.properties['disabled'].value)

    def test_id_deep_inside_if_is_collected(self):
        rule = parse_rule('''
<W@Widget>:
    if self.x:
        BoxLayout:
            BoxLayout:
                Label:
                    id: deep
''')
        self.assertEqual(rule.id_scope_names, ['deep'])

    def test_id_outside_control_block_still_allowed(self):
        rule = parse_rule('''
<W@Widget>:
    Label:
        id: fine
    if self.x:
        Label:
''')
        self.assertEqual(rule.children[0].id, 'fine')

    def test_handler_inside_if_allowed(self):
        rule = parse_rule('''
<W@Widget>:
    if self.x:
        on_touch_down: pass
        Label:
''')
        branch = rule.children[0].branches[0]
        self.assertEqual(len(branch.handlers), 1)
        self.assertEqual(branch.handlers[0].name, 'on_touch_down')

    def test_canvas_inside_if_allowed(self):
        rule = parse_rule('''
<W@Widget>:
    if self.x:
        canvas:
            Color:
        Label:
''')
        branch = rule.children[0].branches[0]
        self.assertIsNotNone(branch.canvas_root)

    def test_property_inside_canvas_control_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    canvas:
        if self.x:
            foo: 1
            Color:
''', 'only graphics instructions are allowed')

    def test_control_under_canvas_instruction_forbidden(self):
        self.assert_parse_error('''
<W@Widget>:
    canvas:
        Rectangle:
            if self.x:
                Color:
''', 'not allowed under a graphics instruction')

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


if __name__ == '__main__':
    unittest.main()
