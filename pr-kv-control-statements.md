# feat(lang): control statements for the kv language — `if`/`elif`/`else`, `for`, and `slot`

## Summary

This PR adds reactive control statements to the kv language at child-widget
position. Children can now be conditional, repeated over an iterable, or
injected through named insertion points — declaratively, with the same
binding semantics as kv property expressions:

```yaml
<TodoList@BoxLayout>:
    items: []
    filter_done: False

    Label:
        text: 'Todos'
    if not self.items:
        Label:
            text: 'nothing to do'
    for item in self.items if not (self.filter_done and item.done):
        key: item.uid
        TodoRow:
            todo: item
```

When `self.items` or `self.filter_done` changes, the affected children are
destroyed, rebuilt, or moved automatically — positions between static
siblings are preserved, and `key:` gives iterations a stable identity so
unchanged rows keep their widget instances across updates.

Slots make a class rule's tree customizable at the usage site
(Vue/Web-Components model):

```yaml
<Card@BoxLayout>:
    slot header:
        Label:                # fallback when nobody fills the slot
            text: 'untitled'
    slot:                     # default slot: plain children land here

Card:
    slot header:
        Image:
            source: root.logo  # evaluated in the *outer* rule's context
    Label:                     # plain child → routed into the default slot
        text: 'body'
```

## Design decisions

- **Reactivity reuses the existing machinery.** An `if` chain compiles to a
  single selector expression (the active branch index) and a `for` block to
  a single iterator expression (the list of loop-value tuples), both as
  ordinary `ParserRuleProperty` objects. Binding, rebind, and teardown ride
  the standard watched-keys path — no second binding system.
- **Keyed reconciliation for `for`.** A kept key with unchanged loop values
  keeps (and if needed moves) its widgets; anything else is rebuilt. Pure
  appends, removals, and in-place changes don't touch the surviving
  widgets at all; without `key:` the position in the iterable is the key.
  Loop variables are visible in the body's expressions and handlers, and
  bindings on `EventDispatcher` loop items (e.g. `item.title`) react per
  iteration.
- **Synchronous rebuilds.** After `self.expanded = True` the children exist
  immediately, matching kv property semantics. Updates that fire while
  another rule application or rebuild is in flight are deferred and run
  right after it, so rebuilds can never re-enter an in-flight rule
  application or observe a half-reconciled children list.
- **Slot precedence: most derived wins.** The first rule declaring
  `slot name:` defines the insertion point and fallback; later same-name
  blocks are fills — base fallback < subclass fill < instance fill. Fill
  content evaluates in the providing rule's context. Fills can define new
  slots under a fresh name (forwarding through wrapper classes), and slot
  definitions may live inside `if` branches (the insertion point comes and
  goes with the branch, fills are retained). Fallbacks are built lazily at
  the end of the apply chain, so a filled slot never constructs its
  fallback.
- **`id` is forbidden inside control blocks** (and on children routed into
  slots): entries appearing in and vanishing from `root.ids` as branches
  rebuild would silently break any expression bound to them. Parse-time
  error.
- **`while`, `match`, and `case` are reserved** for possible future use and
  rejected with a dedicated message.

## Implementation

- **Parser** (`kivy/lang/parser.py`): `ParserControlRule` /
  `ParserControlBranch`; `elif`/`else` rules are merged into their `if` at
  parse time. Headers are tokenized with Python's tokenizer (colons inside
  strings/dicts/slices/lambdas are handled); `for` headers parse through
  the comprehension grammar, so tuple/star targets and trailing `if`
  filters work. Watched keys rooted at a loop target are dropped — they
  are loop-local, and an unrelated widget id of the same name would
  otherwise be bound by mistake.
- **Builder** (`kivy/lang/builder.py`): `IfNode`/`ForNode`/`SlotNode`
  anchor a contiguous span of the parent's children and rebuild it when
  their expression changes. They activate in a new deferred phase of
  `_apply_rule` (after properties and handlers, so ids and values are
  resolved) and apply subtrees by reusing `_apply_rule` with a seeded ids
  context — canvas, handlers, and nested controls come for free. Teardown
  walks destroyed subtrees through `Builder.unbind_widget` (no handler
  leaks; covered by tests) and scrubs them from pending `on_kv_post`
  lists. Rules without controls take a fast path: the cost for existing
  kv is one boolean test per child.
- Also fixes a pre-existing leak: `_apply_rule` now removes its `rulectx`
  entry when an exception escapes mid-application. A stale entry broke
  `BuilderBase.create_from`'s `assert not builder.rulectx` (used by the
  `kivy_app` test fixture) for every later caller.

## Restrictions (enforced with targeted error messages)

- No control statements at the top level of a kv file or inside `canvas`
  (including nested under graphics instructions).
- No properties, `id`, event handlers, or `canvas` directly on a control
  block — for a conditional *property*, use a conditional expression
  (`text: 'a' if cond else 'b'`).
- Loop targets can't shadow `self`/`root`/`app`/`args` or the metric
  helpers; no walrus in headers; no `async for`; single `for` clause.
- No slot definitions inside `for`; fills must be direct children of the
  instance (put the `if` *inside* the fill); a `slot:` fill block can't be
  mixed with plain children; keys must be hashable and unique.

## Caveat

Children built by control statements are managed: manually removing or
reordering the children of a widget that uses control statements breaks
the position tracking (appending is fine). Documented in the language
guide.

## Testing

- 39 parser tests (`test_lang_parser_control.py`): syntax, compilation
  output, and every error path.
- 38 runtime tests (`test_lang_control_runtime.py`): branch switching,
  keyed diff identity preservation, nesting in both directions, per-item
  reactivity, slot fallback/fill/inheritance/forwarding, handler-leak and
  `rulectx`-leak regressions, re-entrant rebuild convergence.
- Full suite: 1385 passed, 62 skipped, 1 xfailed, 0 errors.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
