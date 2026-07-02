'''
Runtime tests for kv control statements (if/elif/else).

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

    def test_no_kv_post_for_widgets_destroyed_during_apply(self):
        # a widget built by a control statement and torn down again before
        # the apply finishes (here: its own on_parent flips the condition)
        # must never receive on_kv_post
        seen = []
        from kivy.uix.label import Label

        class GhostLabel(Label):
            def on_kv_post(self, base_widget):
                seen.append(self.text)

        root = Builder.load_string('''
BoxLayout:
    show: True
    if self.show:
        GhostLabel:
            text: 'ghost'
            on_parent: root.show = False
''')
        self.assertEqual(texts(root), [])
        self.assertEqual(seen, [])

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


class IfBodyRuntimeTestCase(unittest.TestCase):
    '''An ``if`` block is a full rule body applied to the host widget:
    besides children it may carry properties, canvas and handlers.'''

    def test_conditional_property_over_reactive_base(self):
        # a branch adds an ordinary binding while active; it coexists with
        # the unconditional one (which stays live) and resolves by reactivity
        # order. Leaving the branch does not revert the value: the base
        # re-wins only on its next dependency change (one-way binding).
        root = Builder.load_string('''
BoxLayout:
    cond: False
    base_src: 10
    a: self.base_src * 2
    if self.cond:
        a: 999
''')
        self.assertEqual(root.a, 20)
        root.cond = True
        self.assertEqual(root.a, 999)
        # branch leaves -> no immediate revert, the last value is kept...
        root.cond = False
        self.assertEqual(root.a, 999)
        # ...and the base binding re-wins when its dependency next changes
        root.base_src = 100
        self.assertEqual(root.a, 200)

    def test_branch_only_property_keeps_last_value(self):
        # no unconditional rule for the property -> one-way: leaving the
        # branch does not revert, the latest set value is kept
        from kivy.uix.boxlayout import BoxLayout
        from kivy.properties import NumericProperty, BooleanProperty

        class KeepLast(BoxLayout):
            b = NumericProperty(0)
            cond = BooleanProperty(False)

        Builder.load_string('''
<KeepLast>:
    if self.cond:
        b: 777
''', filename='test_keeplast.kv')
        try:
            w = KeepLast()
            self.assertEqual(w.b, 0)
            w.cond = True
            self.assertEqual(w.b, 777)
            w.cond = False
            self.assertEqual(w.b, 777)
        finally:
            Builder.unload_file('test_keeplast.kv')

    def test_else_branch_property(self):
        root = Builder.load_string('''
BoxLayout:
    cond: False
    label: ''
    if self.cond:
        label: 'on'
    else:
        label: 'off'
''')
        self.assertEqual(root.label, 'off')
        root.cond = True
        self.assertEqual(root.label, 'on')
        root.cond = False
        self.assertEqual(root.label, 'off')

    def test_two_parallel_blocks_resolve_by_reactivity(self):
        # two independent if-blocks driving one property is the power-user
        # parallel-binding case: the most recently applied write wins, and a
        # block leaving does not restore another block's value (one-way).
        root = Builder.load_string('''
BoxLayout:
    a: False
    b: False
    v: 1
    if self.a:
        v: 2
    if self.b:
        v: 3
''')
        self.assertEqual(root.v, 1)
        root.a = True
        self.assertEqual(root.v, 2)
        root.b = True              # most recent write wins
        self.assertEqual(root.v, 3)
        root.b = False             # leaving does not revert to the other
        self.assertEqual(root.v, 3)

    def test_nested_if_property(self):
        # nested branches apply their value on activation; leaving a branch
        # is one-way (no revert), as for any parallel binding
        root = Builder.load_string('''
BoxLayout:
    outer: False
    inner: False
    v: 0
    if self.outer:
        v: 1
        if self.inner:
            v: 2
''')
        self.assertEqual(root.v, 0)
        root.outer = True
        self.assertEqual(root.v, 1)
        root.inner = True
        self.assertEqual(root.v, 2)
        root.inner = False          # nested block torn down, value kept
        self.assertEqual(root.v, 2)
        root.outer = False          # outer block torn down, value kept
        self.assertEqual(root.v, 2)

    def test_property_with_children_in_same_branch(self):
        root = Builder.load_string('''
BoxLayout:
    cond: False
    title: 'none'
    if self.cond:
        title: 'shown'
        Label:
            text: 'child'
''')
        self.assertEqual(root.title, 'none')
        self.assertEqual(texts(root), [])
        root.cond = True
        self.assertEqual(root.title, 'shown')
        self.assertEqual(texts(root), ['child'])
        root.cond = False
        self.assertEqual(root.title, 'shown')   # one-way: value kept
        self.assertEqual(texts(root), [])        # children torn down

    def test_conditional_handler(self):
        root = Builder.load_string('''
BoxLayout:
    cond: False
    fired: 0
    if self.cond:
        on_touch_down: self.fired += 1
''')
        root.dispatch('on_touch_down', None)
        self.assertEqual(root.fired, 0)
        root.cond = True
        root.dispatch('on_touch_down', None)
        self.assertEqual(root.fired, 1)
        root.cond = False
        root.dispatch('on_touch_down', None)
        self.assertEqual(root.fired, 1)

    def test_conditional_canvas_mount_and_binding(self):
        from kivy.graphics import Color, InstructionGroup

        root = Builder.load_string('''
BoxLayout:
    cond: False
    cval: 0.5
    if self.cond:
        canvas:
            Color:
                rgba: 1, 0, 0, self.cval
            Rectangle:
                size: self.size
''')
        groups = [c for c in root.canvas.children
                  if isinstance(c, InstructionGroup)]
        self.assertEqual(groups, [])
        root.cond = True
        groups = [c for c in root.canvas.children
                  if isinstance(c, InstructionGroup)]
        self.assertEqual(len(groups), 1)

        def find_color(group):
            for instr in group.children:
                if isinstance(instr, Color):
                    return instr
            return None
        color = find_color(groups[0])
        self.assertIsNotNone(color)
        root.cval = 0.9
        Builder.sync()              # canvas bindings are delayed
        self.assertAlmostEqual(color.rgba[3], 0.9)
        root.cond = False
        groups = [c for c in root.canvas.children
                  if isinstance(c, InstructionGroup)]
        self.assertEqual(groups, [])

    def test_branch_creates_missing_property(self):
        # a property only ever set inside a branch is created on demand
        root = Builder.load_string('''
BoxLayout:
    cond: True
    if self.cond:
        brand_new: 42
''')
        self.assertEqual(root.brand_new, 42)

    def test_no_handler_leak_on_property_rebuild(self):
        root = Builder.load_string('''
BoxLayout:
    cond: True
    base_src: 1
    a: self.base_src * 2
    if self.cond:
        a: self.base_src + 5
''')
        for _ in range(30):
            root.cond = False
            root.cond = True
        live = len(_handlers.get(root.uid, {}).get('a', ()))
        # base and active branch coexist (parallel bindings) and do not leak
        self.assertEqual(live, 2)
        root.cond = False
        # the branch binding is removed; the base remains
        self.assertEqual(len(_handlers.get(root.uid, {}).get('a', ())), 1)


class ControlIdRuntimeTestCase(unittest.TestCase):
    '''Reactive ids inside control blocks: an id in an ``if`` is reachable
    across the rule and is None while its branch is inactive; an id in a
    ``for`` is iteration-local, reachable only by that iteration's content.'''

    def test_if_id_nullable_reactive_reachable_outside(self):
        from kivy.factory import Factory
        Builder.load_string('''
<IdEditor@BoxLayout>:
    editing: False
    if self.editing:
        TextInput:
            id: field
    Button:
        disabled: field is None
''', filename='ideditor.kv')
        try:
            w = Factory.IdEditor()
            btn = [c for c in w.children
                   if c.__class__.__name__ == 'Button'][0]
            self.assertTrue(btn.disabled)        # inactive branch -> None
            w.editing = True
            self.assertFalse(btn.disabled)       # mounted -> reachable
            w.editing = False
            self.assertTrue(btn.disabled)        # unmounted -> None again
            w.editing = True
            self.assertFalse(btn.disabled)       # and back
        finally:
            Builder.unload_file('ideditor.kv')

    def test_if_id_does_not_leak_into_root_ids(self):
        from kivy.factory import Factory
        Builder.load_string('''
<IdNoLeak@BoxLayout>:
    flag: True
    if self.flag:
        Label:
            id: hidden
''', filename='idnoleak.kv')
        try:
            w = Factory.IdNoLeak()
            # the reactive id must not appear in root.ids (it would blink)
            self.assertNotIn('hidden', w.ids)
        finally:
            Builder.unload_file('idnoleak.kv')

    def test_complementary_chains_can_share_an_id(self):
        # `if cond:` / `if not cond:` may define the same id: the id follows
        # whichever branch is mounted, and a chain tearing down after the
        # other one mounted must not clobber the freshly set id (the reset
        # is compare-and-set: only the widget that owns the value clears it)
        from kivy.factory import Factory
        Builder.load_string('''
<TwoChains@BoxLayout>:
    cond: True
    if self.cond:
        Label:
            id: face
            text: 'yes'
    if not self.cond:
        Label:
            id: face
            text: 'no'
    Button:
        text: face.text if face else '?'
''', filename='twochains.kv')
        try:
            w = Factory.TwoChains()
            btn = [c for c in w.children
                   if c.__class__.__name__ == 'Button'][0]
            self.assertEqual(btn.text, 'yes')
            w.cond = False
            self.assertEqual(btn.text, 'no')
            w.cond = True
            self.assertEqual(btn.text, 'yes')
        finally:
            Builder.unload_file('twochains.kv')

    def test_if_id_widget_is_collected(self):
        import gc
        import weakref
        from kivy.factory import Factory
        Builder.load_string('''
<IdGc@BoxLayout>:
    flag: True
    if self.flag:
        Label:
            id: thing
    Button:
        disabled: thing is None
''', filename='idgc.kv')
        try:
            w = Factory.IdGc()
            w.flag = False
            w.flag = True
            ref = weakref.ref(w)
            del w
            gc.collect()
            self.assertIsNone(ref())
        finally:
            Builder.unload_file('idgc.kv')


class ControlStatementGCTestCase(unittest.TestCase):
    '''A widget that uses control statements must stay garbage-collectable:
    the control nodes hold the children they build (whose ``.parent`` points
    back at the host), so they must live on the host, not in a global
    registry that would pin the whole subtree forever.'''

    def _collected(self, kv):
        import gc
        import weakref
        from kivy.factory import Factory
        Builder.load_string(kv, filename='gc_probe.kv')
        try:
            w = Factory.GcProbe()
            # exercise any reactive content so a branch/iteration is built
            w.flag = not w.flag
            w.flag = not w.flag
            ref = weakref.ref(w)
            del w
            gc.collect()
            # nodes live on the widget (no uid-keyed registries to leak)
            return ref() is None, True
        finally:
            Builder.unload_file('gc_probe.kv')

    def test_if_widget_is_collected(self):
        collected, clean = self._collected('''
<GcProbe@BoxLayout>:
    flag: True
    v: 0
    if self.flag:
        v: 1
        Label:
            text: 'x'
''')
        self.assertTrue(collected)
        self.assertTrue(clean)

    def test_many_rows_collected_after_clear(self):
        import gc
        import weakref
        from kivy.factory import Factory
        from kivy.uix.boxlayout import BoxLayout
        Builder.load_string('''
<GcRow@BoxLayout>:
    label: '?'
    if self.label:
        Label:
            text: root.label
''', filename='gc_rows.kv')
        try:
            container = BoxLayout()
            refs = []
            for i in range(20):
                row = Factory.GcRow()
                row.label = 'row %d' % i
                container.add_widget(row)
                refs.append(weakref.ref(row))
            del row
            container.clear_widgets()
            gc.collect()
            alive = sum(1 for r in refs if r() is not None)
            self.assertEqual(alive, 0)
        finally:
            Builder.unload_file('gc_rows.kv')


def _canvas_types(canvas):
    '''Flatten a canvas (descending into instruction groups) to the list of
    instruction class names, in draw order. ``BindTexture`` is dropped: kv
    auto-adds it alongside textured instructions (Rectangle/Line) and it is
    not part of what the rule declares.'''
    from kivy.graphics import InstructionGroup
    out = []
    for instr in canvas.children:
        if isinstance(instr, InstructionGroup):
            out += _canvas_types(instr)
        elif type(instr).__name__ != 'BindTexture':
            out.append(type(instr).__name__)
    return out


def _canvas_leaves(canvas):
    '''Flatten a canvas (descending into groups) to the list of leaf
    instruction *instances* in draw order, dropping kv's auto-added
    ``BindTexture``. Used to assert instruction identity across reconciles.'''
    from kivy.graphics import InstructionGroup
    out = []
    for instr in canvas.children:
        if isinstance(instr, InstructionGroup):
            out += _canvas_leaves(instr)
        elif type(instr).__name__ != 'BindTexture':
            out.append(instr)
    return out


class CanvasControlRuntimeTestCase(unittest.TestCase):
    '''``if`` and ``for`` work inside a canvas block, managing graphics
    instructions reactively and in document position.'''

    def test_if_in_canvas_switches_instructions(self):
        from kivy.factory import Factory
        Builder.load_string('''
<IfCanvas@Widget>:
    on: False
    canvas:
        Color:
        if self.on:
            Rectangle:
        else:
            Line:
''', filename='if_canvas.kv')
        try:
            w = Factory.IfCanvas()
            self.assertEqual(_canvas_types(w.canvas), ['Color', 'Line'])
            w.on = True
            self.assertEqual(_canvas_types(w.canvas), ['Color', 'Rectangle'])
            w.on = False
            self.assertEqual(_canvas_types(w.canvas), ['Color', 'Line'])
        finally:
            Builder.unload_file('if_canvas.kv')

    def test_instruction_property_binding_in_canvas_control(self):
        from kivy.factory import Factory
        from kivy.graphics import Color, InstructionGroup
        Builder.load_string('''
<BindCanvas@Widget>:
    on: True
    alpha: 0.5
    canvas:
        if self.on:
            Color:
                rgba: 1, 0, 0, self.alpha
''', filename='bind_canvas.kv')
        try:
            w = Factory.BindCanvas()

            def find_color(canvas):
                for instr in canvas.children:
                    if isinstance(instr, InstructionGroup):
                        c = find_color(instr)
                        if c is not None:
                            return c
                    elif isinstance(instr, Color):
                        return instr
                return None
            color = find_color(w.canvas)
            self.assertIsNotNone(color)
            w.alpha = 0.9
            Builder.sync()        # canvas bindings are delayed
            self.assertAlmostEqual(color.rgba[3], 0.9)
        finally:
            Builder.unload_file('bind_canvas.kv')


class ControlRegressionTestCase(unittest.TestCase):
    '''Regression tests pinning behaviors that once broke.'''

    def test_cross_rule_base_coexists_with_branch(self):
        # base in the class rule, conditional override in the subclass rule:
        # they coexist like ordinary stacked-rule bindings -- the branch wins
        # while active, and the base (still live) re-wins on its next change
        from kivy.factory import Factory
        Builder.load_string('''
<CRBase@BoxLayout>:
    src: 1
    foo: self.src * 10
<CRSub@CRBase>:
    cond: False
    if self.cond:
        foo: 999
''', filename='crbase.kv')
        try:
            w = Factory.CRSub()
            self.assertEqual(w.foo, 10)     # class-rule base
            w.cond = True
            self.assertEqual(w.foo, 999)    # subclass-rule branch overrides
            w.cond = False
            # one-way: no immediate revert; the class-rule base stays bound
            self.assertEqual(w.foo, 999)
            w.src = 5
            self.assertEqual(w.foo, 50)     # base re-wins on its next change
        finally:
            Builder.unload_file('crbase.kv')

    def test_ignored_const_base_not_reasserted(self):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.properties import NumericProperty, BooleanProperty

        class IgnConst(BoxLayout):
            foo = NumericProperty(0)
            cond = BooleanProperty(False)

        Builder.load_string('''
<IgnConst>:
    foo: 7
    if self.cond:
        foo: 99
''', filename='ignconst.kv')
        try:
            w = IgnConst(foo=42)            # python value preserved by apply
            self.assertEqual(w.foo, 42)
            w.cond = True
            self.assertEqual(w.foo, 99)
            w.cond = False
            # the suppressed constant 7 must NOT be re-asserted; with no live
            # base the branch's last value is kept (one-way binding)
            self.assertEqual(w.foo, 99)
        finally:
            Builder.unload_file('ignconst.kv')

    def test_reapply_resets_control_nodes(self):
        from kivy.factory import Factory
        Builder.load_string('''
<ReApply@BoxLayout>:
    flag: True
    if self.flag:
        Label:
''', filename='reapply.kv')
        try:
            w = Factory.ReApply()
            n1 = len(w._kv_control_nodes)
            self.assertEqual(n1, 1)
            Builder.unbind_widget(w.uid)
            Builder.apply(w)
            self.assertEqual(len(w._kv_control_nodes), n1)  # not duplicated
        finally:
            Builder.unload_file('reapply.kv')


class CanvasRegressionTestCase(unittest.TestCase):

    def test_clear_inside_canvas_control_does_not_crash(self):
        from kivy.factory import Factory
        Builder.load_string('''
<ClearCanvas@Widget>:
    on: True
    canvas:
        if self.on:
            Color:
            Clear
            Color:
''', filename='clearcanvas.kv')
        try:
            w = Factory.ClearCanvas()    # must not raise
            w.on = False
            w.on = True
        finally:
            Builder.unload_file('clearcanvas.kv')

    def test_branch_canvas_zorder_follows_document_order(self):
        from kivy.factory import Factory
        Builder.load_string('''
<ZOrder@Widget>:
    a: False
    b: False
    if self.a:
        canvas:
            Scale:
    if self.b:
        canvas:
            Rotate:
''', filename='zorder.kv')
        try:
            w = Factory.ZOrder()
            # activate the later block (b) first, then a: a's group must
            # still draw before b's, following document order
            w.b = True
            w.a = True
            self.assertEqual(_canvas_types(w.canvas), ['Scale', 'Rotate'])
        finally:
            Builder.unload_file('zorder.kv')


if __name__ == '__main__':
    unittest.main()
