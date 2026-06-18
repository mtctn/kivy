'''
Runtime tests for kv control statements (if/elif/else, for, slot).

These cover the builder stage; pure parsing is tested in
``test_lang_parser_control.py``.
'''

import unittest

from kivy.lang import Builder
from kivy.lang.builder import BuilderException
from kivy.lang.parser import _handlers


def texts(widget):
    '''Texts of the children in document order.'''
    return [c.text for c in reversed(widget.children)]


def by_text(widget):
    return {c.text: c for c in reversed(widget.children)}


class IfRuntimeTestCase(unittest.TestCase):

    def test_initial_branch_and_switching(self):
        root = Builder.load_string('''
BoxLayout:
    expanded: True
    Label:
        text: 'first'
    if self.expanded:
        Label:
            text: 'open'
        Label:
            text: 'open2'
    else:
        Label:
            text: 'closed'
    Label:
        text: 'last'
''')
        self.assertEqual(texts(root), ['first', 'open', 'open2', 'last'])
        root.expanded = False
        self.assertEqual(texts(root), ['first', 'closed', 'last'])
        root.expanded = True
        self.assertEqual(texts(root), ['first', 'open', 'open2', 'last'])

    def test_if_without_else_builds_nothing_when_false(self):
        root = Builder.load_string('''
BoxLayout:
    show: False
    if self.show:
        Label:
            text: 'maybe'
''')
        self.assertEqual(texts(root), [])
        root.show = True
        self.assertEqual(texts(root), ['maybe'])
        root.show = False
        self.assertEqual(texts(root), [])

    def test_elif_chain(self):
        root = Builder.load_string('''
BoxLayout:
    state: 'a'
    if self.state == 'a':
        Label:
            text: 'A'
    elif self.state == 'b':
        Label:
            text: 'B'
    else:
        Label:
            text: 'other'
''')
        self.assertEqual(texts(root), ['A'])
        root.state = 'b'
        self.assertEqual(texts(root), ['B'])
        root.state = 'zzz'
        self.assertEqual(texts(root), ['other'])

    def test_two_sibling_chains_keep_positions(self):
        root = Builder.load_string('''
BoxLayout:
    a: False
    b: True
    if self.a:
        Label:
            text: 'A'
    Label:
        text: 'mid'
    if self.b:
        Label:
            text: 'B'
''')
        self.assertEqual(texts(root), ['mid', 'B'])
        root.a = True
        self.assertEqual(texts(root), ['A', 'mid', 'B'])
        root.b = False
        self.assertEqual(texts(root), ['A', 'mid'])

    def test_nested_if(self):
        root = Builder.load_string('''
BoxLayout:
    outer: True
    inner: False
    if self.outer:
        Label:
            text: 'pre'
        if self.inner:
            Label:
                text: 'deep'
''')
        self.assertEqual(texts(root), ['pre'])
        root.inner = True
        self.assertEqual(texts(root), ['pre', 'deep'])
        root.outer = False
        self.assertEqual(texts(root), [])
        root.outer = True
        self.assertEqual(texts(root), ['pre', 'deep'])

    def test_branch_content_bindings_work_and_die_with_branch(self):
        root = Builder.load_string('''
BoxLayout:
    show: True
    n: 0
    if self.show:
        Label:
            text: str(root.n)
''')
        label = root.children[0]
        root.n = 5
        self.assertEqual(label.text, '5')
        root.show = False
        root.n = 9
        self.assertEqual(label.text, '5')  # unbound on destroy
        root.show = True
        self.assertEqual(root.children[0].text, '9')

    def test_no_handler_leak_on_rebuild(self):
        root = Builder.load_string('''
BoxLayout:
    show: True
    if self.show:
        Label:
            text: 'x'
''')
        baseline = set(_handlers)
        for _ in range(30):
            root.show = False
            root.show = True
        # anything new in the registry must belong to the live subtree
        live = {w.uid for w in root.walk(restrict=True)}
        leaked = set(_handlers) - baseline - live
        self.assertEqual(leaked, set())

    def test_condition_can_use_sibling_ids(self):
        root = Builder.load_string('''
BoxLayout:
    Label:
        id: lbl
        text: 'off'
    if lbl.text == 'on':
        Label:
            text: 'visible'
''')
        self.assertEqual(texts(root), ['off'])
        lbl = root.ids.lbl
        lbl.text = 'on'
        self.assertEqual(texts(root), ['on', 'visible'])

    def test_if_in_class_rule(self):
        Builder.load_string('''
<TogglePanel@BoxLayout>:
    open: True
    if self.open:
        Label:
            text: 'content'
''')
        from kivy.factory import Factory
        panel = Factory.TogglePanel()
        self.assertEqual(texts(panel), ['content'])
        panel.open = False
        self.assertEqual(texts(panel), [])

    def test_on_kv_post_dispatched_for_dynamic_builds(self):
        seen = []
        from kivy.uix.label import Label

        class PostLabel(Label):
            def on_kv_post(self, base_widget):
                seen.append(self.text)

        root = Builder.load_string('''
BoxLayout:
    show: False
    if self.show:
        PostLabel:
            text: 'dyn'
''')
        self.assertEqual(seen, [])
        root.show = True
        self.assertEqual(seen, ['dyn'])


class ForRuntimeTestCase(unittest.TestCase):

    def test_initial_build_in_order(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b', 'c']
    Label:
        text: 'head'
    for item in self.items:
        Label:
            text: item
    Label:
        text: 'tail'
''')
        self.assertEqual(texts(root), ['head', 'a', 'b', 'c', 'tail'])

    def test_positional_keys_reuse_unchanged_prefix(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b']
    for item in self.items:
        Label:
            text: item
''')
        first = by_text(root)
        root.items = ['a', 'b', 'c']
        second = by_text(root)
        self.assertEqual(texts(root), ['a', 'b', 'c'])
        self.assertIs(first['a'], second['a'])
        self.assertIs(first['b'], second['b'])

    def test_keyed_reorder_preserves_identity(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b', 'c']
    for item in self.items:
        key: item
        Label:
            text: item
''')
        before = by_text(root)
        root.items = ['c', 'a', 'b']
        after = by_text(root)
        self.assertEqual(texts(root), ['c', 'a', 'b'])
        for k in 'abc':
            self.assertIs(before[k], after[k])

    def test_keyed_remove_and_add(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b', 'c']
    for item in self.items:
        key: item
        Label:
            text: item
''')
        before = by_text(root)
        root.items = ['c', 'd']
        after = by_text(root)
        self.assertEqual(texts(root), ['c', 'd'])
        self.assertIs(before['c'], after['c'])

    def test_empty_iterable_and_refill(self):
        root = Builder.load_string('''
BoxLayout:
    items: []
    for item in self.items:
        Label:
            text: item
    Label:
        text: 'end'
''')
        self.assertEqual(texts(root), ['end'])
        root.items = ['x']
        self.assertEqual(texts(root), ['x', 'end'])
        root.items = []
        self.assertEqual(texts(root), ['end'])

    def test_tuple_target_and_filter(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b', 'c']
    for item, i in zip(self.items, range(99)) if item != 'b':
        Label:
            text: '%s%d' % (item, i)
''')
        self.assertEqual(texts(root), ['a0', 'c2'])

    def test_loop_variable_in_handler(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b']
    picked: ''
    for item in self.items:
        Button:
            text: item
            on_press: root.picked = item
''')
        button_a = by_text(root)['a']
        button_a.dispatch('on_press', None)
        self.assertEqual(root.picked, 'a')

    def test_loop_target_shadows_metric_helper(self):
        # 'dp' is a metric helper in the global kv context; as a loop
        # target it must resolve to the loop value in child property
        # expressions, not the helper function
        root = Builder.load_string('''
BoxLayout:
    items: ['x', 'y']
    for dp in self.items:
        Label:
            text: dp
''')
        self.assertEqual(texts(root), ['x', 'y'])

    def test_loop_target_shadows_global_in_handler(self):
        # same precedence must hold on the handler eval path
        root = Builder.load_string('''
BoxLayout:
    items: ['x', 'y']
    picked: ''
    for dp in self.items:
        Button:
            text: dp
            on_press: root.picked = dp
''')
        by_text(root)['y'].dispatch('on_press', None)
        self.assertEqual(root.picked, 'y')

    def test_loop_target_self_reshadowed_by_child_widget(self):
        # a loop target named 'self' is shadowed again by each child
        # widget's own 'self', so 'self.x' refers to the widget
        root = Builder.load_string('''
BoxLayout:
    names: ['a', 'b']
    for self in self.names:
        Label:
            text: self.__class__.__name__
''')
        self.assertEqual(texts(root), ['Label', 'Label'])

    def test_nested_for_loop_scopes_compose(self):
        # inner loop sees its own and the enclosing loop's variables
        root = Builder.load_string('''
BoxLayout:
    rows: [['a', 'b'], ['c']]
    for row in self.rows:
        for cell in row:
            Label:
                text: '%s:%s' % (len(row), cell)
''')
        self.assertEqual(texts(root), ['2:a', '2:b', '1:c'])

    def test_multiple_children_per_iteration(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b']
    for item in self.items:
        Label:
            text: item + '1'
        Label:
            text: item + '2'
''')
        self.assertEqual(texts(root), ['a1', 'a2', 'b1', 'b2'])

    def test_for_inside_if(self):
        root = Builder.load_string('''
BoxLayout:
    show: True
    items: ['x', 'y']
    if self.show:
        for item in self.items:
            Label:
                text: item
    Label:
        text: 'end'
''')
        self.assertEqual(texts(root), ['x', 'y', 'end'])
        root.items = ['x', 'y', 'z']
        self.assertEqual(texts(root), ['x', 'y', 'z', 'end'])
        root.show = False
        self.assertEqual(texts(root), ['end'])
        root.show = True
        self.assertEqual(texts(root), ['x', 'y', 'z', 'end'])

    def test_if_inside_for_reacts_per_item(self):
        from kivy.uix.widget import Widget
        from kivy.properties import StringProperty, BooleanProperty

        class FItem(Widget):
            name = StringProperty('')
            special = BooleanProperty(False)

        root = Builder.load_string('''
BoxLayout:
    models: []
    for item in self.models:
        Label:
            text: item.name
        if item.special:
            Label:
                text: item.name + '!'
''')
        a = FItem(name='a')
        b = FItem(name='b', special=True)
        root.models = [a, b]
        self.assertEqual(texts(root), ['a', 'b', 'b!'])
        a.special = True
        self.assertEqual(texts(root), ['a', 'a!', 'b', 'b!'])
        b.special = False
        self.assertEqual(texts(root), ['a', 'a!', 'b'])

    def test_nested_for(self):
        root = Builder.load_string('''
BoxLayout:
    rows: [['a', 'b'], ['c']]
    for row in self.rows:
        for cell in row:
            Label:
                text: cell
''')
        self.assertEqual(texts(root), ['a', 'b', 'c'])
        root.rows = [['x'], ['y', 'z']]
        self.assertEqual(texts(root), ['x', 'y', 'z'])

    def test_duplicate_key_raises(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b']
    for item in self.items:
        key: item
        Label:
            text: item
''')
        with self.assertRaises(BuilderException) as cm:
            root.items = ['a', 'a']
        self.assertIn('duplicate', str(cm.exception))

    def test_unhashable_key_raises(self):
        root = Builder.load_string('''
BoxLayout:
    items: []
    for item in self.items:
        key: item
        Label:
            text: str(item)
''')
        with self.assertRaises(BuilderException) as cm:
            root.items = [['unhashable']]
        self.assertIn('hashable', str(cm.exception))

    def test_cross_widget_reentrant_rebuild(self):
        # a handler firing during one widget's build mutates state that
        # another widget's node (sharing the same class rule) watches;
        # the second rebuild must be deferred, not re-enter _apply_rule
        root = Builder.load_string('''
<ReentryRow@BoxLayout>:
    items: []
    poke: False
    for item in self.items:
        Label:
            text: str(item)
            on_parent:
                (setattr(root.parent.children[0], 'items', ['p'])
                if root.poke and root.parent
                and not root.parent.children[0].items else None)
BoxLayout:
    ReentryRow:
    ReentryRow:
''')
        row1, row2 = root.children[1], root.children[0]
        row1.poke = True
        row1.items = ['a']
        self.assertEqual(texts(row1), ['a'])
        self.assertEqual(texts(row2), ['p'])

    def test_pure_append_does_not_detach_existing(self):
        root = Builder.load_string('''
BoxLayout:
    items: ['a', 'b']
    for item in self.items:
        key: item
        Label:
            text: item
''')
        removals = []
        orig = root.remove_widget

        def tracking_remove(w):
            removals.append(w)
            orig(w)

        root.remove_widget = tracking_remove
        root.items = ['a', 'b', 'c']
        self.assertEqual(removals, [])
        self.assertEqual(texts(root), ['a', 'b', 'c'])

    def test_no_handler_leak_on_updates(self):
        root = Builder.load_string('''
BoxLayout:
    items: []
    for item in self.items:
        Label:
            text: item
''')
        root.items = ['a', 'b', 'c']
        baseline = set(_handlers)
        for i in range(30):
            root.items = ['a', str(i)]
            root.items = ['a', 'b', 'c']
        # anything new in the registry must belong to the live subtree
        live = {w.uid for w in root.walk(restrict=True)}
        leaked = set(_handlers) - baseline - live
        self.assertEqual(leaked, set())


class SlotRuntimeTestCase(unittest.TestCase):

    def setUp(self):
        Builder.load_string('''
<SlotCard@BoxLayout>:
    Label:
        text: 'top'
    slot header:
        Label:
            text: 'fallback-header'
    slot:
    Label:
        text: 'bottom'
''', filename='test_slot_runtime.kv')

    def tearDown(self):
        Builder.unload_file('test_slot_runtime.kv')

    def test_fallback_when_instantiated_from_python(self):
        from kivy.factory import Factory
        card = Factory.SlotCard()
        self.assertEqual(texts(card), ['top', 'fallback-header', 'bottom'])

    def test_fallback_not_built_when_filled(self):
        # the fallback build is deferred until the apply chain ends, so
        # providing a fill means the fallback widgets are never created
        # and never receive on_kv_post
        from kivy.uix.label import Label
        posts = []

        class PostProbe(Label):
            def on_kv_post(self, base_widget):
                posts.append((self.text, self.parent is not None))

        Builder.load_string('''
<ProbeCard@BoxLayout>:
    slot header:
        PostProbe:
            text: 'fallback'
''', filename='test_slot_probe.kv')
        try:
            root = Builder.load_string('''
BoxLayout:
    ProbeCard:
        slot header:
            PostProbe:
                text: 'fill'
''')
            self.assertEqual(texts(root.children[0]), ['fill'])
            self.assertEqual(posts, [('fill', True)])
        finally:
            Builder.unload_file('test_slot_probe.kv')

    def test_named_fill_and_routed_plain_children(self):
        root = Builder.load_string('''
BoxLayout:
    title: 'Title'
    SlotCard:
        slot header:
            Label:
                text: root.title
        Label:
            text: 'body1'
        Label:
            text: 'body2'
''')
        card = root.children[0]
        self.assertEqual(
            texts(card), ['top', 'Title', 'body1', 'body2', 'bottom'])
        root.title = 'New'
        self.assertEqual(
            texts(card), ['top', 'New', 'body1', 'body2', 'bottom'])

    def test_explicit_default_fill_block(self):
        root = Builder.load_string('''
BoxLayout:
    SlotCard:
        slot:
            Label:
                text: 'explicit-body'
''')
        self.assertEqual(
            texts(root.children[0]),
            ['top', 'fallback-header', 'explicit-body', 'bottom'])

    def test_subclass_fill_overrides_base_and_instance_wins(self):
        Builder.load_string('''
<FancySlotCard@SlotCard>:
    slot header:
        Label:
            text: 'fancy-header'
''', filename='test_slot_runtime_sub.kv')
        try:
            from kivy.factory import Factory
            fancy = Factory.FancySlotCard()
            self.assertEqual(
                texts(fancy), ['top', 'fancy-header', 'bottom'])
            root = Builder.load_string('''
BoxLayout:
    FancySlotCard:
        slot header:
            Label:
                text: 'instance-header'
''')
            self.assertEqual(
                texts(root.children[0]),
                ['top', 'instance-header', 'bottom'])
        finally:
            Builder.unload_file('test_slot_runtime_sub.kv')

    def test_mixing_default_fill_block_and_plain_children_raises(self):
        with self.assertRaises(BuilderException) as cm:
            Builder.load_string('''
BoxLayout:
    SlotCard:
        slot:
            Label:
                text: 'in-block'
        Label:
            text: 'plain'
''')
        self.assertIn('cannot mix', str(cm.exception))

    def test_id_on_routed_children_raises(self):
        with self.assertRaises(BuilderException) as cm:
            Builder.load_string('''
BoxLayout:
    SlotCard:
        Label:
            id: nope
            text: 'body'
''')
        self.assertIn('routed into a slot', str(cm.exception))

    def test_slot_inside_if_branch(self):
        Builder.load_string('''
<IfSlotPanel@BoxLayout>:
    show: True
    if self.show:
        Label:
            text: 'pre'
        slot extra:
            Label:
                text: 'extra-fallback'
''', filename='test_slot_runtime_if.kv')
        try:
            root = Builder.load_string('''
BoxLayout:
    IfSlotPanel:
        slot extra:
            Label:
                text: 'injected'
''')
            panel = root.children[0]
            self.assertEqual(texts(panel), ['pre', 'injected'])
            panel.show = False
            self.assertEqual(texts(panel), [])
            panel.show = True
            self.assertEqual(texts(panel), ['pre', 'injected'])
        finally:
            Builder.unload_file('test_slot_runtime_if.kv')

    def test_slot_forwarding_through_fill(self):
        Builder.load_string('''
<WrapCard@SlotCard>:
    slot header:
        Label:
            text: 'wrap-pre'
        slot title_extra:
''', filename='test_slot_runtime_fwd.kv')
        try:
            from kivy.factory import Factory
            wrap = Factory.WrapCard()
            self.assertEqual(texts(wrap), ['top', 'wrap-pre', 'bottom'])
            root = Builder.load_string('''
BoxLayout:
    WrapCard:
        slot title_extra:
            Label:
                text: 'forwarded'
''')
            self.assertEqual(
                texts(root.children[0]),
                ['top', 'wrap-pre', 'forwarded', 'bottom'])
        finally:
            Builder.unload_file('test_slot_runtime_fwd.kv')

    def test_unknown_slot_name_renders_as_definition(self):
        # filling a name the class never defined degrades to declaring a
        # new insertion point; instance children are logically appended
        # after the class rule's children, so it renders at the end
        root = Builder.load_string('''
BoxLayout:
    SlotCard:
        slot typo_name:
            Label:
                text: 'oops'
''')
        card = root.children[0]
        self.assertEqual(
            texts(card), ['top', 'fallback-header', 'bottom', 'oops'])

    def test_failed_apply_leaves_no_stale_rulectx(self):
        # a BuilderException escaping mid-apply must not leave entries in
        # Builder.rulectx; a stale entry breaks BuilderBase.create_from
        # (used by the kivy_app test fixture) for every later caller
        with self.assertRaises(BuilderException):
            Builder.load_string('''
BoxLayout:
    SlotCard:
        Label:
            id: nope
''')
        self.assertEqual(dict(Builder.rulectx), {})

    def test_widgets_without_slots_unaffected(self):
        root = Builder.load_string('''
BoxLayout:
    Label:
        text: 'plain'
    BoxLayout:
        Label:
            text: 'nested'
''')
        self.assertEqual(root.children[1].text, 'plain')
        self.assertEqual(texts(root.children[0]), ['nested'])


if __name__ == '__main__':
    unittest.main()
