'''
Builder
======

Class used for the registering and application of rules for specific widgets.
'''
import sys
from os import environ
from os.path import join
from copy import copy
from types import CodeType

from kivy.factory import Factory
from kivy.lang.parser import (
    Parser,
    ParserException,
    _handlers,
    global_idmap,
    ParserRuleProperty,
    ParserControlRule,
)
from kivy.logger import Logger
from kivy import kivy_data_dir
from kivy.context import register_context
from kivy.resources import resource_find
from kivy._event import Observable, EventDispatcher

__all__ = ('Observable', 'Builder', 'BuilderBase', 'BuilderException')


trace = Logger.trace

# late import
Instruction = None

# delayed calls are canvas expression triggered during an loop. It is one
# directional linked list of args to call call_fn with. Each element is a list
# whose last element points to the next list of args to execute when
# Builder.sync is called.
_delayed_start = None


class BuilderException(ParserException):
    '''Exception raised when the Builder fails to apply a rule on a widget.
    '''
    pass


def custom_callback(__kvlang__, idmap, *largs, **kwargs):
    idmap['args'] = largs
    exec(__kvlang__.co_value, idmap)


def call_fn(args, instance, v):
    element, key, value, rule, idmap = args
    if __debug__:
        trace('Lang: call_fn %s, key=%s, value=%r, %r' % (
            element, key, value, rule.value))
    rule.count += 1
    e_value = eval(value, idmap)
    if __debug__:
        trace('Lang: call_fn => value=%r' % (e_value, ))
    setattr(element, key, e_value)


def delayed_call_fn(args, instance, v):
    # it's already on the list
    if args[-1] is not None:
        return

    global _delayed_start
    if _delayed_start is None:
        _delayed_start = args
        args[-1] = StopIteration
    else:
        args[-1] = _delayed_start
        _delayed_start = args


def update_intermediates(base, keys, bound, s, fn, args, instance, value):
    ''' Function that is called when an intermediate property is updated
    and `rebind` of that property is True. In that case, we unbind
    all bound funcs that were bound to attrs of the old value of the
    property and rebind them to the new value of the property.

    For example, if the rule is `self.a.b.c.d`, then when b is changed, we
    unbind from `b`, `c` and `d`, if they were bound before (they were not
    None and `rebind` of the respective properties was True) and we rebind
    to the new values of the attrs `b`, `c``, `d` that are not None and
    `rebind` is True.

    :Parameters:
        `base`
            A (proxied) ref to the base widget, `self` in the example
            above.
        `keys`
            A list of the name off the attrs of `base` being watched. In
            the example above it'd be `['a', 'b', 'c', 'd']`.
        `bound`
            A list 4-tuples, each tuple being (widget, attr, callback, uid)
            representing callback functions bound to the attributed `attr`
            of `widget`. `uid` is returned by `fbind` when binding.
            The callback may be None, in which case the attr
            was not bound, but is there to be able to walk the attr tree.
            E.g. in the example above, if `b` was not an eventdispatcher,
            `(_b_ref_, `c`, None)` would be added to the list so we can get
            to `c` and `d`, which may be eventdispatchers and their attrs.
        `s`
            The index in `keys` of the of the attr that needs to be
            updated. That is all the keys from `s` and further will be
            rebound, since the `s` key was changed. In bound, the
            corresponding index is `s - 1`. If `s` is None, we start from
            1 (first attr).
        `fn`
            The function to be called args, `args` on bound callback.
    '''
    # first remove all the old bound functions from `s` and down.
    for f, k, fun, uid in bound[s:]:
        if fun is None:
            continue
        try:
            f.unbind_uid(k, uid)
        except ReferenceError:
            pass
    del bound[s:]

    # find the first attr from which we need to start rebinding.
    f = getattr(*bound[-1][:2])
    if f is None:
        fn(args, None, None)
        return
    s += 1
    append = bound.append

    # bind all attrs, except last to update_intermediates
    for val in keys[s:-1]:
        # if we need to dynamically rebind, bindm otherwise just
        # add the attr to the list
        if isinstance(f, (EventDispatcher, Observable)):
            prop = f.property(val, True)
            if prop is not None and getattr(prop, 'rebind', False):
                # fbind should not dispatch, otherwise
                # update_intermediates might be called in the middle
                # here messing things up
                uid = f.fbind(
                    val, update_intermediates, base, keys, bound, s, fn, args)
                append([f.proxy_ref, val, update_intermediates, uid])
            else:
                append([f.proxy_ref, val, None, None])
        else:
            append([getattr(f, 'proxy_ref', f), val, None, None])

        f = getattr(f, val, None)
        if f is None:
            break
        s += 1

    # for the last attr we bind directly to the setting function,
    # because that attr sets the value of the rule.
    if isinstance(f, (EventDispatcher, Observable)):
        uid = f.fbind(keys[-1], fn, args)
        if uid:
            append([f.proxy_ref, keys[-1], fn, uid])
    # when we rebind we have to update the
    # rule with the most recent value, otherwise, the value might be wrong
    # and wouldn't be updated since we might not have tracked it before.
    # This only happens for a callback when rebind was True for the prop.
    fn(args, None, None)


def create_handler(iself, element, key, value, rule, idmap, delayed=False):
    idmap = copy(idmap)
    idmap.update(global_idmap)
    idmap['self'] = iself.proxy_ref
    bound_list = _handlers[iself.uid][key]
    handler_append = bound_list.append

    # we need a hash for when delayed, so we don't execute duplicate canvas
    # callbacks from the same handler during a sync op
    if delayed:
        fn = delayed_call_fn
        args = [element, key, value, rule, idmap, None]  # see _delayed_start
    else:
        fn = call_fn
        args = (element, key, value, rule, idmap)

    # bind every key.value
    if rule.watched_keys is not None:
        for keys in rule.watched_keys:
            base = idmap.get(keys[0])
            if base is None:
                continue
            f = base = getattr(base, 'proxy_ref', base)
            bound = []
            was_bound = False
            append = bound.append

            # bind all attrs, except last to update_intermediates
            k = 1
            for val in keys[1:-1]:
                # if we need to dynamically rebind, bindm otherwise
                # just add the attr to the list
                if isinstance(f, (EventDispatcher, Observable)):
                    prop = f.property(val, True)
                    if prop is not None and getattr(prop, 'rebind', False):
                        # fbind should not dispatch, otherwise
                        # update_intermediates might be called in the middle
                        # here messing things up
                        uid = f.fbind(
                            val, update_intermediates, base, keys, bound, k,
                            fn, args)
                        append([f.proxy_ref, val, update_intermediates, uid])
                        was_bound = True
                    else:
                        append([f.proxy_ref, val, None, None])
                elif not isinstance(f, type):
                    append([getattr(f, 'proxy_ref', f), val, None, None])
                else:
                    append([f, val, None, None])
                f = getattr(f, val, None)
                if f is None:
                    break
                k += 1

            # for the last attr we bind directly to the setting
            # function, because that attr sets the value of the rule.
            if isinstance(f, (EventDispatcher, Observable)):
                uid = f.fbind(keys[-1], fn, args)  # f is not None
                if uid:
                    append([f.proxy_ref, keys[-1], fn, uid])
                    was_bound = True
            if was_bound:
                handler_append(bound)

    try:
        return eval(value, idmap), bound_list
    except Exception as e:
        tb = sys.exc_info()[2]
        raise BuilderException(rule.ctx, rule.line,
                               '{}: {}'.format(e.__class__.__name__, e),
                               cause=tb)


# ---------------------------------------------------------------------------
# Runtime for kv control statements (if / for / slot)
# ---------------------------------------------------------------------------

# root-level control nodes per parent widget uid, in document order
_control_nodes = {}
# slot nodes per widget uid: {slot name: SlotNode}
_slot_nodes = {}

# depth of in-flight rule applications / control node updates. While it is
# non-zero, control nodes whose expression fires are queued instead of
# rebuilt immediately: rebuilding mid-apply could re-enter `_apply_rule`
# for a rule that is currently being applied (``rulectx`` is keyed by
# rule), and rebuilding mid-reconcile would compute positions against a
# half-updated children list.
_update_depth = 0
# nodes waiting for the depth to return to zero, in trigger order
_pending_nodes = []
_flushing = False

# rule_children lists of in-flight applications. When a control node
# destroys widgets, they are scrubbed from these so on_kv_post is not
# dispatched on widgets that no longer exist (e.g. slot fallback content
# replaced by a fill during the same apply).
_sink_stack = []


def _enter_apply(rule_children=None):
    global _update_depth
    _update_depth += 1
    if rule_children is not None:
        _sink_stack.append(rule_children)


def _exit_apply(rule_children=None):
    global _update_depth
    _update_depth -= 1
    if rule_children is not None:
        _sink_stack.pop()
    if _update_depth == 0:
        _flush_pending_nodes()


def _queue_node(node):
    if not node._queued:
        node._queued = True
        _pending_nodes.append(node)


def _flush_pending_nodes():
    global _flushing
    if _flushing:
        return
    _flushing = True
    try:
        while _pending_nodes:
            node = _pending_nodes.pop(0)
            node._queued = False
            node._run_pending()
    finally:
        _flushing = False


def _scrub_sinks(widget):
    for sink in _sink_stack:
        try:
            sink.remove(widget)
        except ValueError:
            pass


def _nodes_length(uid):
    nodes = _control_nodes.get(uid)
    if not nodes:
        return 0
    return sum(node.length() for node in nodes)


def _scan_rule_ids(crule):
    '''Return the first rule of the subtree declaring an id, or None.'''
    if crule.id:
        return crule
    for child in crule.children:
        found = _scan_rule_ids(child)
        if found is not None:
            return found
    return None


def _register_slot_definitions(widget, crules, epoch):
    '''Walk the same-widget child positions of ``crules`` (descending
    through control statement blocks, but not into child widgets, which
    own their slots) and register every slot declared there that isn't
    known yet for ``widget``. A slot block whose name was registered by an
    earlier rule application is left alone: it is a fill, handled at apply
    time. Registration happens before anything is built so that fills can
    target slots whose insertion point lives in a not-yet-built branch.
    '''
    for crule in crules:
        if not isinstance(crule, ParserControlRule):
            continue
        if crule.kind == 'slot':
            registry = _slot_nodes.setdefault(widget.uid, {})
            node = registry.get(crule.slot_name)
            if node is None:
                node = SlotNode(widget, crule.slot_name)
                node.def_epoch = epoch
                registry[crule.slot_name] = node
            if node.def_epoch is epoch:
                # several definitions in one rule are valid when they live
                # in different branches of an if; only one can be active
                node.def_crules.append(crule)
            _register_slot_definitions(widget, crule.children, epoch)
        elif crule.kind == 'if':
            for branch in crule.branches:
                _register_slot_definitions(widget, branch.children, epoch)
        else:  # for; the parser forbids slot definitions inside it
            _register_slot_definitions(widget, crule.children, epoch)


class ControlNode(EventDispatcher):
    '''Runtime anchor for one application of a kv control statement.

    A node owns a contiguous span of its parent widget's children and
    rebuilds that span when its driving state changes. Nodes nested
    inside another control block occupy an item slot in their owner's
    content instead of tracking a static offset of their own.

    .. versionadded:: 3.1.0
    '''

    def __init__(self, parent, crule, ids, **kwargs):
        super(ControlNode, self).__init__(**kwargs)
        self.parent = parent.proxy_ref
        self.crule = crule
        self.ids = ids
        #: owning node when nested inside another control block
        self.owner = None
        #: number of children logically before this node (root nodes only)
        self.static_offset = 0
        #: content items, ('w', widget) or ('n', node)
        self.items = []
        self._queued = False
        self._destroyed = False

    # ------------------------------------------------------------------
    # span geometry
    # ------------------------------------------------------------------

    def _item_lists(self):
        return (self.items, )

    def length(self):
        return sum(
            obj.length() if kind == 'n' else 1
            for items in self._item_lists() for kind, obj in items)

    def start(self):
        '''Logical position of the first child of this node's span within
        the parent widget's children, in document order.
        '''
        if self.owner is not None:
            return self.owner.child_start(self)
        pos = self.static_offset
        for node in _control_nodes.get(self.parent.uid, ()):
            if node is self:
                break
            pos += node.length()
        return pos

    def child_start(self, child):
        pos = self.start()
        for items in self._item_lists():
            for kind, obj in items:
                if kind == 'n':
                    if obj is child:
                        return pos
                    pos += obj.length()
                else:
                    pos += 1
        crule = self.crule or getattr(self, 'active_crule', None)
        raise BuilderException(
            crule.ctx, crule.line,
            'Internal error: lost track of a nested control statement')

    def flat_widgets(self, out):
        for items in self._item_lists():
            for kind, obj in items:
                if kind == 'n':
                    obj.flat_widgets(out)
                else:
                    out.append(obj)

    def _dispatch_kv_post(self, widgets):
        ids = self.ids
        try:
            base = ids.get('root', self.parent) if ids else self.parent
        except ReferenceError:
            return
        for w in widgets:
            w.dispatch('on_kv_post', base)

    def _run_pending(self):
        pass

    # ------------------------------------------------------------------
    # content building / teardown
    # ------------------------------------------------------------------

    def _destroy_items(self, items):
        try:
            parent = self.parent.__self__
        except ReferenceError:
            parent = None
        unbind = Builder.unbind_widget
        for kind, obj in items:
            if kind == 'n':
                obj.destroy()
            elif parent is not None:
                parent.remove_widget(obj)
                for sub in obj.walk(restrict=True):
                    unbind(sub.uid)
                    _scrub_sinks(sub)
        del items[:]

    def _build_content(self, crules, idmap, items, created, pos):
        '''Build the children of a control block at logical position
        ``pos`` of the parent widget, appending entries to ``items``.
        Returns the position following the built content.
        '''
        parent = self.parent.__self__
        root = idmap.get('root', parent.proxy_ref)
        for crule in crules:
            if isinstance(crule, ParserControlRule):
                if crule.kind == 'slot':
                    registry = _slot_nodes.get(parent.uid) or {}
                    node = registry.get(crule.slot_name)
                    if node is None or crule not in node.def_crules:
                        raise BuilderException(
                            crule.ctx, crule.line,
                            'slot %r is already defined for this widget; '
                            'fills must be direct children of the widget, '
                            'and re-exposing a slot through a fill '
                            'requires a fresh name' % crule.slot_name)
                    if node.active:
                        raise BuilderException(
                            crule.ctx, crule.line,
                            'slot %r is already active; a slot can only '
                            'have one live insertion point' %
                            crule.slot_name)
                    node.owner = self
                    items.append(('n', node))
                    node.activate(crule, idmap, created)
                else:
                    node_cls = IfNode if crule.kind == 'if' else ForNode
                    node = node_cls(parent, crule, idmap)
                    node.owner = self
                    items.append(('n', node))
                    node.activate(created)
                pos += node.length()
                continue
            cls = Factory.get(crule.name)
            child = cls(__no_builder=True)
            parent.add_widget(
                child, index=max(0, len(parent.children) - pos))
            child.apply_class_lang_rules(root=root, rule_children=created)
            Builder._apply_rule(
                child, crule, crule, rule_children=created,
                _ids=dict(idmap))
            items.append(('w', child))
            if created is not None:
                created.append(child)
            pos += 1
        return pos

    def destroy(self):
        self._destroyed = True
        self._destroy_items(self.items)


class ExpressionNode(ControlNode):
    '''A control node driven by a compiled kv expression (the selector of
    an ``if`` chain, the iterator of a ``for``), bound through the
    standard handler machinery under a unique per-node key.

    .. versionadded:: 3.1.0
    '''

    _counter = 0

    def __init__(self, parent, crule, ids, **kwargs):
        super(ExpressionNode, self).__init__(parent, crule, ids, **kwargs)
        ExpressionNode._counter += 1
        #: per-node key used for the expression binding in ``_handlers``,
        #: and as the name of the property the expression value is set on
        self.bind_key = 'kv_ctl_%d' % ExpressionNode._counter
        self._pending_created = None
        self._activating = False
        self._updating = False
        self._dirty = False

    def _bind_expression(self, prop, created):
        self.create_property(self.bind_key, None)
        self.fbind(self.bind_key, self._value_changed)
        value, _ = create_handler(
            self.parent, self, self.bind_key, prop.co_value, prop, self.ids)
        self._pending_created = created
        self._activating = True
        try:
            setattr(self, self.bind_key, value)
        finally:
            self._activating = False
            self._pending_created = None

    def _value_changed(self, instance, value):
        if self._updating:
            # the build mutated something the expression depends on;
            # finish the current build, then converge in _do_update
            self._dirty = True
            return
        if _update_depth > 0 and not self._activating:
            # another rule application or node update is in flight;
            # rebuilding now could re-enter _apply_rule for a rule that
            # is currently being applied, or compute positions against a
            # half-reconciled children list. Run once it completes.
            _queue_node(self)
            return
        self._do_update()

    def _run_pending(self):
        if self._destroyed:
            return
        try:
            self.parent.uid
        except ReferenceError:
            return
        self._do_update()

    def _do_update(self):
        global _update_depth
        try:
            self.parent.uid
        except ReferenceError:
            # parent widget is gone; its destructor unbinds us
            return
        self._updating = True
        _update_depth += 1
        try:
            while True:
                self._dirty = False
                created = self._pending_created
                sink = created if created is not None else []
                self.update(getattr(self, self.bind_key), sink)
                if created is None and sink:
                    self._dispatch_kv_post(sink)
                if not self._dirty:
                    return
        finally:
            self._updating = False
            _update_depth -= 1
            if _update_depth == 0:
                _flush_pending_nodes()

    def _unbind_expression(self):
        try:
            uid = self.parent.uid
        except ReferenceError:
            return
        handlers = _handlers.get(uid)
        if not handlers or self.bind_key not in handlers:
            return
        for callbacks in handlers.pop(self.bind_key):
            for f, k, fn, bound_uid in callbacks:
                if fn is None:
                    continue
                try:
                    f.unbind_uid(k, bound_uid)
                except ReferenceError:
                    pass
        if not handlers:
            del _handlers[uid]

    def destroy(self):
        self._unbind_expression()
        super(ExpressionNode, self).destroy()


class IfNode(ExpressionNode):
    '''Runtime for an ``if``/``elif``/``else`` chain.

    .. versionadded:: 3.1.0
    '''

    def __init__(self, parent, crule, ids, **kwargs):
        super(IfNode, self).__init__(parent, crule, ids, **kwargs)
        self.current_index = -1

    def activate(self, created):
        self._bind_expression(self.crule.selector_prop, created)

    def update(self, value, created):
        index = -1 if value is None else int(value)
        if index == self.current_index:
            return
        self._destroy_items(self.items)
        self.current_index = index
        branches = self.crule.branches
        if 0 <= index < len(branches):
            self._build_content(
                branches[index].children, self.ids, self.items, created,
                self.start())

    def destroy(self):
        super(IfNode, self).destroy()
        self.current_index = -1


class ForNode(ExpressionNode):
    '''Runtime for a ``for`` block. Content is reconciled by key: a kept
    key with unchanged loop values keeps (and if needed moves) its
    widgets, anything else is rebuilt. Without a ``key:`` directive the
    position in the iterable is the key.

    .. versionadded:: 3.1.0
    '''

    def __init__(self, parent, crule, ids, **kwargs):
        super(ForNode, self).__init__(parent, crule, ids, **kwargs)
        #: one [key, values, items] entry per iteration
        self.groups = []

    def _item_lists(self):
        return tuple(group[2] for group in self.groups)

    def activate(self, created):
        crule = self.crule
        for name in crule.target_names:
            if name in global_idmap:
                raise BuilderException(
                    crule.ctx, crule.line,
                    'loop target %r collides with a name in the global kv '
                    'context' % name)
        self._bind_expression(crule.iterator_prop, created)

    def _eval_key(self, key_prop, names, values):
        idmap = copy(self.ids)
        idmap.update(global_idmap)
        idmap['self'] = self.parent
        idmap.update(zip(names, values))
        try:
            return eval(key_prop.co_value, idmap)
        except Exception as e:
            tb = sys.exc_info()[2]
            raise BuilderException(
                key_prop.ctx, key_prop.line,
                '{}: {}'.format(e.__class__.__name__, e), cause=tb)

    def update(self, value, created):
        crule = self.crule
        names = crule.target_names
        key_prop = crule.key_prop
        parent = self.parent.__self__
        tuples = [tuple(v) for v in (value or ())]

        new = []
        seen = set()
        for i, values in enumerate(tuples):
            if key_prop is None:
                key = i
            else:
                key = self._eval_key(key_prop, names, values)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                raise BuilderException(
                    key_prop.ctx, key_prop.line,
                    '"key" value %r is not hashable' % (key, ))
            if duplicate:
                raise BuilderException(
                    key_prop.ctx, key_prop.line,
                    'duplicate "key" value %r in "for" block' % (key, ))
            new.append((key, values))

        old = self.groups
        try:
            if [(group[0], group[1]) for group in old] == new:
                return
        except Exception:
            pass

        old_by_key = {group[0]: group for group in old}
        plan = []
        reused = set()
        for key, values in new:
            group = old_by_key.get(key)
            keep = False
            if group is not None and id(group) not in reused:
                try:
                    keep = bool(group[1] == values)
                except Exception:
                    keep = False
            if keep:
                reused.add(id(group))
                plan.append((group, None))
            else:
                plan.append((None, (key, values)))

        # tear down removed or changed iterations
        for group in old:
            if id(group) not in reused:
                self._destroy_items(group[2])

        # when the kept groups appear in the same relative order as
        # before, their widgets are already correctly ordered among
        # themselves: removals shift them automatically and new groups
        # are inserted between them at computed positions, so nothing
        # needs to move. Only a genuine reorder requires detaching.
        kept_new = [group for group, _ in plan if group is not None]
        kept_old = [group for group in old if id(group) in reused]
        order_preserved = len(kept_new) == len(kept_old) and all(
            a is b for a, b in zip(kept_new, kept_old))

        detached = {}
        if not order_preserved:
            for group in kept_new:
                flat = []
                for kind, obj in group[2]:
                    if kind == 'n':
                        obj.flat_widgets(flat)
                    else:
                        flat.append(obj)
                detached[id(group)] = flat
                for w in flat:
                    parent.remove_widget(w)

        self.groups = []
        pos = self.start()
        for group, fresh in plan:
            if group is not None:
                if order_preserved:
                    self.groups.append(group)
                    pos += sum(
                        obj.length() if kind == 'n' else 1
                        for kind, obj in group[2])
                else:
                    for w in detached[id(group)]:
                        parent.add_widget(
                            w, index=max(0, len(parent.children) - pos))
                        pos += 1
                    self.groups.append(group)
            else:
                key, values = fresh
                idmap = dict(self.ids)
                idmap.update(zip(names, values))
                items = []
                self.groups.append([key, values, items])
                pos = self._build_content(
                    crule.children, idmap, items, created, pos)

    def destroy(self):
        self._destroyed = True
        self._unbind_expression()
        for group in self.groups:
            self._destroy_items(group[2])
        del self.groups[:]


class SlotNode(ControlNode):
    '''Runtime for a slot. The node is registered (dormant) as soon as a
    defining rule is scanned, so fills can be stored before the insertion
    point is built; it activates when a defining slot block is reached
    while building, and renders the most recently provided fill, falling
    back to the defining block's children.

    .. versionadded:: 3.1.0
    '''

    def __init__(self, parent, name, **kwargs):
        super(SlotNode, self).__init__(parent, None, None, **kwargs)
        self.name = name
        #: crules that may activate this slot (one per defining branch)
        self.def_crules = []
        #: identity of the rule application that defined the slot
        self.def_epoch = None
        #: most-derived provided content, as (crules, ids), or None
        self.fill = None
        self.active = False
        self.active_crule = None
        self.def_ids = None

    def activate(self, crule, ids, created):
        if self.active:
            raise BuilderException(
                crule.ctx, crule.line,
                'slot %r is already active; a slot can only have one live '
                'insertion point' % self.name)
        self.active = True
        self.active_crule = crule
        self.def_ids = ids
        self.ids = ids
        if self.fill is not None:
            self._build(created)
        elif _update_depth > 0:
            # a fill may still arrive from a later rule of the in-flight
            # application (subclass rule, instance rule); defer building
            # the fallback so it isn't constructed only to be replaced
            _queue_node(self)
        else:
            self._build(created)

    def _run_pending(self):
        if not self.active or self.items or self.fill is not None:
            return
        try:
            self.parent.uid
        except ReferenceError:
            return
        sink = []
        self._build(sink)
        if sink:
            self._dispatch_kv_post(sink)

    def _build(self, created):
        if self.fill is not None:
            crules, idmap = self.fill
        else:
            crules, idmap = self.active_crule.children, self.def_ids
        self._build_content(crules, idmap, self.items, created, self.start())

    def set_fill(self, crules, ids, created):
        self.fill = (crules, ids)
        if self.active:
            self._destroy_items(self.items)
            sink = created if created is not None else []
            self._build(sink)
            if created is None and sink:
                self._dispatch_kv_post(sink)

    def destroy(self):
        # called when a containing branch is destroyed: tear down the
        # live content but keep the registration and the stored fill, so
        # the slot can come back when the branch is rebuilt
        self._destroy_items(self.items)
        self.active = False
        self.active_crule = None
        self.owner = None


class BuilderBase(object):
    '''The Builder is responsible for creating a :class:`Parser` for parsing a
    kv file, merging the results into its internal rules, dynamic classes,
    etc.

    By default, :class:`Builder` is a global Kivy instance used in widgets
    that you can use to load other kv files in addition to the default ones.

    See :mod:`kivy.lang` for details about the rules built here.

    :attributes:
        `files`: list
            Filenames of Kivy-language code that have already been loaded.
            Not necessarily real files on disk; see :meth:`load_string`.
        `dynamic_classes`:  dict
            Classes crated in Kivy-language code.
            They were created with the ``<Class@Superclass>:`` syntax,
            rather than in Python.
        `rules`: list
            Rules loaded from Kivy-language code.
        `rulectx`: dict
            Context used by each rule.
            Mostly, the IDs of widgets.

    .. versionchanged:: 3.0.0
        The deprecated Kivy lang templates feature has been removed.
        ``Builder.template`` and the ``Builder.templates`` dict no longer
        exist; use dynamic classes (``<Name@Base>:``) instead.
    '''

    def __init__(self):
        super(BuilderBase, self).__init__()
        self._match_cache = {}
        self._match_name_cache = {}
        self.files = []
        self.dynamic_classes = {}
        self.rules = []
        self.rulectx = {}

    @classmethod
    def create_from(cls, builder):
        """Creates a instance of the class, and initializes to the state of
        ``builder``.

        :param builder: The builder to initialize from.
        :return: A new instance of this class.
        """
        obj = cls()
        obj._match_cache = copy(builder._match_cache)
        obj._match_name_cache = copy(builder._match_name_cache)
        obj.files = copy(builder.files)
        obj.dynamic_classes = copy(builder.dynamic_classes)
        obj.rules = list(builder.rules)
        assert not builder.rulectx
        obj.rulectx = dict(builder.rulectx)

        return obj

    def load_file(self, filename, encoding='utf8', **kwargs):
        '''Insert a file into the language builder and return the root widget
        (if defined) of the kv file.

        :parameters:
            `rulesonly`: bool, defaults to False
                If True, the Builder will raise an exception if you have a root
                widget inside the definition.

            `encoding`: File character encoding. Defaults to utf-8,
        '''

        filename = resource_find(filename) or filename
        if __debug__:
            trace('Lang: load file %s, using %s encoding', filename, encoding)

        kwargs['filename'] = filename
        with open(filename, 'r', encoding=encoding) as fd:
            data = fd.read()
            return self.load_string(data, **kwargs)

    def unload_file(self, filename):
        '''Unload all rules associated with a previously imported file.

        .. versionadded:: 1.0.8

        .. warning::

            This will not remove rules already applied/used on current
            widgets. It will only effect the next widgets creation.
        '''
        # remove rules associated with this file
        filename = resource_find(filename) or filename
        self.rules = [x for x in self.rules if x[1].ctx.filename != filename]
        self._clear_matchcache()

        if filename in self.files:
            self.files.remove(filename)

        # unregister all the dynamic classes
        Factory.unregister_from_filename(filename)

    def load_string(self, string, **kwargs):
        '''Insert a string into the Language Builder and return the root widget
        (if defined) of the kv string.

        :Parameters:
            `rulesonly`: bool, defaults to False
                If True, the Builder will raise an exception if you have a root
                widget inside the definition.
            `filename`: str, defaults to None
                If specified, the filename used to index the kv rules.

        The filename parameter can be used to unload kv strings in the same way
        as you unload kv files. This can be achieved using pseudo file names
        e.g.::

            Build.load_string("""
                <MyRule>:
                    Label:
                        text="Hello"
            """, filename="myrule.kv")

        can be unloaded via::

            Build.unload_file("myrule.kv")

        '''

        kwargs.setdefault('rulesonly', False)
        self._current_filename = fn = kwargs.get('filename', None)

        # put a warning if a file is loaded multiple times
        if fn in self.files:
            Logger.warning(
                'Lang: The file {} is loaded multiples times, '
                'you might have unwanted behaviors.'.format(fn))

        try:
            # parse the string
            parser = Parser(content=string, filename=fn)

            # merge rules with our rules
            self.rules.extend(parser.rules)
            self._clear_matchcache()

            # register all the dynamic classes
            for name, baseclasses in parser.dynamic_classes.items():
                Factory.register(name, baseclasses=baseclasses, filename=fn,
                                 warn=True)

            # create root object is exist
            if kwargs['rulesonly'] and parser.root:
                filename = kwargs.get('rulesonly', '<string>')
                raise Exception('The file <%s> contain also non-rules '
                                'directives' % filename)

            # save the loaded files only if there is a root without
            # dynamic classes
            if fn and (parser.dynamic_classes or parser.rules):
                self.files.append(fn)

            if parser.root:
                widget = Factory.get(parser.root.name)(__no_builder=True)
                rule_children = []
                # hold the apply depth across the class rules and the
                # root rule, so deferred work (queued node updates, slot
                # fallbacks) runs only after the root rule had its
                # chance to provide slot fills
                _enter_apply(rule_children)
                try:
                    widget.apply_class_lang_rules(
                        root=widget, rule_children=rule_children)
                    self._apply_rule(
                        widget, parser.root, parser.root,
                        rule_children=rule_children)
                finally:
                    _exit_apply(rule_children)

                for child in rule_children:
                    child.dispatch('on_kv_post', widget)
                widget.dispatch('on_kv_post', widget)
                return widget
        finally:
            self._current_filename = None

    def apply_rules(
            self, widget, rule_name, ignored_consts=set(), rule_children=None,
            dispatch_kv_post=False):
        '''Search all the rules that match the name `rule_name`
        and apply them to `widget`.

        .. versionadded:: 1.10.0

        :Parameters:

            `widget`: :class:`~kivy.uix.widget.Widget`
                The widget to whom the matching rules should be applied to.
            `ignored_consts`: set
                A set or list type whose elements are property names for which
                constant KV rules (i.e. those that don't create bindings) of
                that widget will not be applied. This allows e.g. skipping
                constant rules that overwrite a value initialized in python.
            `rule_children`: list
                If not ``None``, it should be a list that will be populated
                with all the widgets created by the kv rules being applied.

                .. versionchanged:: 1.11.0

            `dispatch_kv_post`: bool
                Normally the class `Widget` dispatches the `on_kv_post` event
                to widgets created during kv rule application.
                But if the rules are manually applied by calling :meth:`apply`,
                that may not happen, so if this is `True`, we will dispatch the
                `on_kv_post` event where needed after applying the rules to
                `widget` (we won't dispatch it for `widget` itself).

                Defaults to False.

                .. versionchanged:: 1.11.0
        '''
        rules = self.match_rule_name(rule_name)
        if __debug__:
            trace('Lang: Found %d rules for %s' % (len(rules), rule_name))
        if not rules:
            return

        if dispatch_kv_post:
            rule_children = rule_children if rule_children is not None else []
        # hold the apply depth across all the rules, so deferred work
        # (queued node updates, slot fallbacks) runs only after every
        # rule had its chance to provide slot fills
        _enter_apply()
        try:
            for rule in rules:
                self._apply_rule(
                    widget, rule, rule, ignored_consts=ignored_consts,
                    rule_children=rule_children)
        finally:
            _exit_apply()
        if dispatch_kv_post:
            for w in rule_children:
                w.dispatch('on_kv_post', widget)

    def apply(self, widget, ignored_consts=set(), rule_children=None,
              dispatch_kv_post=False):
        '''Search all the rules that match the widget and apply them.

        :Parameters:

            `widget`: :class:`~kivy.uix.widget.Widget`
                The widget whose class rules should be applied to this widget.
            `ignored_consts`: set
                A set or list type whose elements are property names for which
                constant KV rules (i.e. those that don't create bindings) of
                that widget will not be applied. This allows e.g. skipping
                constant rules that overwrite a value initialized in python.
            `rule_children`: list
                If not ``None``, it should be a list that will be populated
                with all the widgets created by the kv rules being applied.

                .. versionchanged:: 1.11.0

            `dispatch_kv_post`: bool
                Normally the class `Widget` dispatches the `on_kv_post` event
                to widgets created during kv rule application.
                But if the rules are manually applied by calling :meth:`apply`,
                that may not happen, so if this is `True`, we will dispatch the
                `on_kv_post` event where needed after applying the rules to
                `widget` (we won't dispatch it for `widget` itself).

                Defaults to False.

                .. versionchanged:: 1.11.0
        '''
        rules = self.match(widget)
        if __debug__:
            trace('Lang: Found %d rules for %s' % (len(rules), widget))
        if not rules:
            return

        if dispatch_kv_post:
            rule_children = rule_children if rule_children is not None else []
        # hold the apply depth across all the rules, so deferred work
        # (queued node updates, slot fallbacks) runs only after every
        # rule had its chance to provide slot fills
        _enter_apply()
        try:
            for rule in rules:
                self._apply_rule(
                    widget, rule, rule, ignored_consts=ignored_consts,
                    rule_children=rule_children)
        finally:
            _exit_apply()
        if dispatch_kv_post:
            for w in rule_children:
                w.dispatch('on_kv_post', widget)

    def _clear_matchcache(self):
        self._match_cache.clear()
        self._match_name_cache.clear()

    def _apply_rule(self, widget, rule, rootrule,
                    ignored_consts=set(), rule_children=None, _ids=None):
        # widget: the current instantiated widget
        # rule: the current rule
        # rootrule: the current root rule (for children of a rule)
        # _ids: (internal) seeded ids context, used when a control
        # statement node applies a rule subtree with the captured ids of
        # the rule the control block was declared in

        if rule.id and _ids is not None:
            raise BuilderException(
                rule.ctx, rule.line,
                '"id" is not allowed on widgets managed by a control '
                'statement or slot')

        # will collect reference to all the id in children
        assert rule not in self.rulectx
        self.rulectx[rule] = {
            'ids': _ids if _ids is not None else {'root': widget.proxy_ref},
            'set': [], 'hdl': [], 'ctl': []}
        _enter_apply(rule_children)
        try:
            self._apply_rule_inner(
                widget, rule, rootrule, ignored_consts, rule_children)
        except Exception:
            # don't leave a stale context behind when the application
            # fails; the normal path has already removed it
            self.rulectx.pop(rule, None)
            raise
        finally:
            _exit_apply(rule_children)

    def _apply_rule_inner(self, widget, rule, rootrule,
                          ignored_consts, rule_children):
        # extract the context of the rootrule (not rule!)
        assert rootrule in self.rulectx
        rctx = self.rulectx[rootrule]

        # if we got an id, put it in the root rule for a later global usage
        if rule.id:
            # use only the first word as `id` discard the rest.
            rule.id = rule.id.split('#', 1)[0].strip()
            rctx['ids'][rule.id] = widget.proxy_ref
            # set id name as a attribute for root widget so one can in python
            # code simply access root_widget.id_name
            _ids = dict(rctx['ids'])
            _root = _ids.pop('root')
            _new_ids = _root.ids
            for _key, _value in _ids.items():
                if _value == _root:
                    # skip on self
                    continue
                _new_ids[_key] = _value
            _root.ids = _new_ids

        # first, ensure that the widget have all the properties used in
        # the rule if not, they will be created as ObjectProperty.
        rule.create_missing(widget)

        # build the widget canvas
        if rule.canvas_before:
            with widget.canvas.before:
                self._build_canvas(widget.canvas.before, widget,
                                   rule.canvas_before, rootrule)
        if rule.canvas_root:
            with widget.canvas:
                self._build_canvas(widget.canvas, widget,
                                   rule.canvas_root, rootrule)
        if rule.canvas_after:
            with widget.canvas.after:
                self._build_canvas(widget.canvas.after, widget,
                                   rule.canvas_after, rootrule)

        # create children tree
        Factory_get = Factory.get
        uid = widget.uid
        has_controls = rule.has_controls
        epoch = None
        if has_controls:
            # the epoch identifies this rule application, to tell slot
            # definitions of this rule apart from fills targeting slots
            # of earlier rules; None never matches a registered epoch
            epoch = object()
            _register_slot_definitions(widget, rule.children, epoch)
        slots = _slot_nodes.get(uid)
        default_slot = slots.get('') if slots else None
        explicit_default_fill = False
        routed = None
        for crule in rule.children:
            if has_controls and isinstance(crule, ParserControlRule):
                if crule.kind == 'slot':
                    node = slots[crule.slot_name]
                    if crule in node.def_crules:
                        # definition: a root-level insertion point
                        node.static_offset = (
                            len(widget.children) - _nodes_length(uid))
                        _control_nodes.setdefault(uid, []).append(node)
                        rctx['ctl'].append(
                            ('slot', node, crule, rctx['ids']))
                    else:
                        # fill, targeting a slot of an earlier rule
                        if crule.slot_name == '':
                            if routed is not None:
                                raise BuilderException(
                                    crule.ctx, crule.line,
                                    'cannot mix a "slot:" fill block with '
                                    'plain children; put the children '
                                    'inside the block')
                            explicit_default_fill = True
                        rctx['ctl'].append(
                            ('fill', node, crule.children, rctx['ids']))
                else:
                    node_cls = IfNode if crule.kind == 'if' else ForNode
                    node = node_cls(widget, crule, rctx['ids'])
                    node.static_offset = (
                        len(widget.children) - _nodes_length(uid))
                    _control_nodes.setdefault(uid, []).append(node)
                    rctx['ctl'].append(('node', node))
                continue

            cname = crule.name

            if cname in ('canvas', 'canvas.before', 'canvas.after'):
                raise ParserException(
                    crule.ctx, crule.line,
                    'Canvas instructions added in kv must '
                    'be declared before child widgets.')

            if default_slot is not None and \
                    default_slot.def_epoch is not epoch:
                # this widget has a default slot from an earlier rule;
                # plain children are routed into it instead of appended
                if explicit_default_fill:
                    raise BuilderException(
                        crule.ctx, crule.line,
                        'cannot mix a "slot:" fill block with plain '
                        'children; put the children inside the block')
                bad = _scan_rule_ids(crule)
                if bad is not None:
                    raise BuilderException(
                        bad.ctx, bad.line,
                        '"id" is not allowed on children routed into a '
                        'slot, as the widget may not exist when the id '
                        'is accessed')
                if routed is None:
                    routed = []
                    rctx['ctl'].append(
                        ('fill', default_slot, routed, rctx['ids']))
                routed.append(crule)
                continue

            cls = Factory_get(cname)

            # we can't construct it without __no_builder=True, because the
            # previous implementation was doing the add_widget() before
            # apply(), and so, we could use "self.parent".
            child = cls(__no_builder=True)
            widget.add_widget(child)
            child.apply_class_lang_rules(
                root=rctx['ids']['root'], rule_children=rule_children)
            self._apply_rule(
                child, crule, rootrule, rule_children=rule_children)

            if rule_children is not None:
                rule_children.append(child)

        # append the properties and handlers to our final resolution task
        if rule.properties:
            rctx['set'].append((widget.proxy_ref,
                                list(rule.properties.values())))
            for key, crule in rule.properties.items():
                # clear previously applied rules if asked
                if crule.ignore_prev:
                    Builder.unbind_property(widget, key)
        if rule.handlers:
            rctx['hdl'].append((widget.proxy_ref, rule.handlers))

        # if we are applying another rule that the root one, then it's done for
        # us!
        if rootrule is not rule:
            del self.rulectx[rule]
            return

        # normally, we can apply a list of properties with a proper context
        try:
            rule = None
            for widget_set, rules in reversed(rctx['set']):
                for rule in rules:
                    assert isinstance(rule, ParserRuleProperty)
                    key = rule.name
                    value = rule.co_value
                    if type(value) is CodeType:
                        value, bound = create_handler(
                            widget_set, widget_set, key, value, rule,
                            rctx['ids'])
                        # if there's a rule
                        if (widget_set != widget or bound or
                                key not in ignored_consts):
                            setattr(widget_set, key, value)
                    else:
                        if (widget_set != widget or
                                key not in ignored_consts):
                            setattr(widget_set, key, value)

        except Exception as e:
            if rule is not None:
                tb = sys.exc_info()[2]
                raise BuilderException(rule.ctx, rule.line,
                                       '{}: {}'.format(e.__class__.__name__,
                                                       e), cause=tb)
            raise e

        # build handlers
        try:
            crule = None
            for widget_set, rules in rctx['hdl']:
                for crule in rules:
                    assert isinstance(crule, ParserRuleProperty)
                    assert crule.name.startswith('on_')
                    key = crule.name
                    if not widget_set.is_event_type(key):
                        key = key[3:]
                    idmap = copy(global_idmap)
                    idmap.update(rctx['ids'])
                    idmap['self'] = widget_set.proxy_ref
                    if not widget_set.fbind(key, custom_callback, crule,
                                            idmap):
                        raise AttributeError(key)
                    # hack for on_parent
                    if crule.name == 'on_parent':
                        Factory.Widget.parent.dispatch(widget_set.__self__)
        except Exception as e:
            if crule is not None:
                tb = sys.exc_info()[2]
                raise BuilderException(
                    crule.ctx, crule.line,
                    '{}: {}'.format(e.__class__.__name__, e), cause=tb)
            raise e

        # activate control statements and slot fills, now that the ids
        # and properties of the whole rule are resolved
        for entry in rctx['ctl']:
            tag = entry[0]
            if tag == 'node':
                entry[1].activate(rule_children)
            elif tag == 'slot':
                _, node, slot_crule, ids = entry
                node.activate(slot_crule, ids, rule_children)
            else:  # 'fill'
                _, node, crules, ids = entry
                node.set_fill(crules, ids, rule_children)

        # rule finished, forget it
        del self.rulectx[rootrule]

    def match(self, widget):
        '''Return a list of :class:`ParserRule` objects matching the widget.
        '''
        cache = self._match_cache
        k = (widget.__class__, tuple(widget.cls))
        if k in cache:
            return cache[k]
        rules = []
        for selector, rule in self.rules:
            if selector.match(widget):
                if rule.avoid_previous_rules:
                    del rules[:]
                rules.append(rule)
        cache[k] = rules
        return rules

    def match_rule_name(self, rule_name):
        '''Return a list of :class:`ParserRule` objects matching the widget.
        '''
        cache = self._match_name_cache
        rule_name = str(rule_name)
        k = rule_name.lower()
        if k in cache:
            return cache[k]
        rules = []
        for selector, rule in self.rules:
            if selector.match_rule_name(rule_name):
                if rule.avoid_previous_rules:
                    del rules[:]
                rules.append(rule)
        cache[k] = rules
        return rules

    def sync(self):
        '''Execute all the waiting operations, such as the execution of all the
        expressions related to the canvas.

        .. versionadded:: 1.7.0
        '''
        global _delayed_start
        next_args = _delayed_start
        if next_args is None:
            return

        while next_args is not StopIteration:
            # is this try/except still needed? yes, in case widget died in this
            # frame after the call was scheduled
            try:
                call_fn(next_args[:-1], None, None)
            except ReferenceError:
                pass
            args = next_args
            next_args = args[-1]
            args[-1] = None
        _delayed_start = None

    def unbind_widget(self, uid):
        '''Unbind all the handlers created by the KV rules of the
        widget. The :attr:`kivy.uix.widget.Widget.uid` is passed here
        instead of the widget itself, because Builder is using it in the
        widget destructor.

        This effectively clears all the KV rules associated with this widget.
        For example:

        .. code-block:: python-console

            >>> w = Builder.load_string(\'''
            ... Widget:
            ...     height: self.width / 2. if self.disabled else self.width
            ...     x: self.y + 50
            ... \''')
            >>> w.size
            [100, 100]
            >>> w.pos
            [50, 0]
            >>> w.width = 500
            >>> w.size
            [500, 500]
            >>> Builder.unbind_widget(w.uid)
            >>> w.width = 222
            >>> w.y = 500
            >>> w.size
            [222, 500]
            >>> w.pos
            [50, 500]

        .. versionadded:: 1.7.2
        '''
        _control_nodes.pop(uid, None)
        _slot_nodes.pop(uid, None)
        if uid not in _handlers:
            return
        for prop_callbacks in _handlers[uid].values():
            for callbacks in prop_callbacks:
                for f, k, fn, bound_uid in callbacks:
                    if fn is None:  # it's not a kivy prop.
                        continue
                    try:
                        f.unbind_uid(k, bound_uid)
                    except ReferenceError:
                        # proxy widget is already gone, that's cool :)
                        pass
        del _handlers[uid]

    def unbind_property(self, widget, name):
        '''Unbind the handlers created by all the rules of the widget that set
        the name.

        This effectively clears all the rules of widget that take the form::

            name: rule

        For example:

        .. code-block:: python-console

            >>> w = Builder.load_string(\'''
            ... Widget:
            ...     height: self.width / 2. if self.disabled else self.width
            ...     x: self.y + 50
            ... \''')
            >>> w.size
            [100, 100]
            >>> w.pos
            [50, 0]
            >>> w.width = 500
            >>> w.size
            [500, 500]
            >>> Builder.unbind_property(w, 'height')
            >>> w.width = 222
            >>> w.size
            [222, 500]
            >>> w.y = 500
            >>> w.pos
            [550, 500]

        .. versionadded:: 1.9.1
        '''
        uid = widget.uid
        if uid not in _handlers:
            return

        prop_handlers = _handlers[uid]
        if name not in prop_handlers:
            return

        for callbacks in prop_handlers[name]:
            for f, k, fn, bound_uid in callbacks:
                if fn is None:  # it's not a kivy prop.
                    continue
                try:
                    f.unbind_uid(k, bound_uid)
                except ReferenceError:
                    # proxy widget is already gone, that's cool :)
                    pass
        del prop_handlers[name]
        if not prop_handlers:
            del _handlers[uid]

    def _build_canvas(self, canvas, widget, rule, rootrule):
        global Instruction
        if Instruction is None:
            Instruction = Factory.get('Instruction')
        idmap = copy(self.rulectx[rootrule]['ids'])
        for crule in rule.children:
            name = crule.name
            if name == 'Clear':
                canvas.clear()
                continue
            instr = Factory.get(name)()
            if not isinstance(instr, Instruction):
                raise BuilderException(
                    crule.ctx, crule.line,
                    'You can add only graphics Instruction in canvas.')
            try:
                for prule in crule.properties.values():
                    key = prule.name
                    value = prule.co_value
                    if type(value) is CodeType:
                        value, _ = create_handler(
                            widget, instr.proxy_ref,
                            key, value, prule, idmap, True)
                    setattr(instr, key, value)
            except Exception as e:
                tb = sys.exc_info()[2]
                raise BuilderException(
                    prule.ctx, prule.line,
                    '{}: {}'.format(e.__class__.__name__, e), cause=tb)


#: Main instance of a :class:`BuilderBase`.
Builder: BuilderBase = register_context('Builder', BuilderBase)
Builder.load_file(join(kivy_data_dir, 'style.kv'), rulesonly=True)

if 'KIVY_PROFILE_LANG' in environ:
    import atexit
    from html import escape

    def match_rule(fn, index, rule):
        if rule.ctx.filename != fn:
            return
        for prop, prp in rule.properties.items():
            if prp.line != index:
                continue
            yield prp
        if isinstance(rule, ParserControlRule):
            for prp in (rule.selector_prop, rule.iterator_prop,
                        rule.key_prop):
                if prp is not None and prp.line == index:
                    yield prp
            if rule.branches:
                for branch in rule.branches:
                    for child in branch.children:
                        for r in match_rule(fn, index, child):
                            yield r
        for child in rule.children:
            for r in match_rule(fn, index, child):
                yield r
        if rule.canvas_root:
            for r in match_rule(fn, index, rule.canvas_root):
                yield r
        if rule.canvas_before:
            for r in match_rule(fn, index, rule.canvas_before):
                yield r
        if rule.canvas_after:
            for r in match_rule(fn, index, rule.canvas_after):
                yield r

    def dump_builder_stats():
        html = [
            '<!doctype html>'
            '<html><body>',
            '<style type="text/css">\n',
            'pre { margin: 0; }\n',
            '</style>']
        files = {x[1].ctx.filename for x in Builder.rules}
        for fn in files:
            try:
                with open(fn) as f:
                    lines = f.readlines()
            except (IOError, TypeError) as e:
                continue
            html += ['<h2>', fn, '</h2>', '<table>']
            count = 0
            for index, line in enumerate(lines):
                line = line.rstrip()
                line = escape(line)
                matched_prp = []
                for psn, rule in Builder.rules:
                    matched_prp.extend(match_rule(fn, index, rule))

                count = sum({x.count for x in matched_prp})

                color = (255, 155, 155) if count else (255, 255, 255)
                html += ['<tr style="background-color: rgb{}">'.format(color),
                         '<td>', str(index + 1), '</td>',
                         '<td>', str(count), '</td>',
                         '<td><pre>', line, '</pre></td>',
                         '</tr>']
            html += ['</table>']
        html += ['</body></html>']
        with open('builder_stats.html', 'w', encoding='utf-8') as fd:
            fd.write(''.join(html))

        print('Profiling written at builder_stats.html')

    atexit.register(dump_builder_stats)
