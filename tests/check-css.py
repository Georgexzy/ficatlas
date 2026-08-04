#!/usr/bin/env python3
"""
Fail on duplicate CSS rules whose outcome depends on source order.

Two bugs in one session came from this, both invisible in review:

  .library-empty   defined twice with different padding and colour, so which
                   one applied depended on where you were in the file.
  .account-form    defined twice — one block said flex-direction:column, the
                   other flex-wrap:wrap — and in a column container the
                   `flex: 1 1 200px` on its inputs applied its basis to HEIGHT.
                   Every email and password field rendered 200px tall.

Neither is a CSS error. Both are legal, both silently pick a winner, and a
screenshot only shows the symptom. So this checks the property directly: within
one media context, a selector must not be defined twice setting the SAME
property.

Overrides inside a different @media block are legitimate and ignored — that is
how responsive CSS is written. Only same-context collisions count.

    python3 tests/check-css.py
"""
import collections
import pathlib
import re
import sys

CSS = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "app" / "globals.css"
# Remaining known collision. Was 36; a 50-line block had been pasted twice
# byte-for-byte, and the other 15 were earlier rules whose duplicated
# properties were already dead because a later rule won. Never raise this.
BASELINE = 1


def rules(src: str):
    """Yield (media_context, selector, {properties}) for every rule."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    ctx, stack, buf, i = [], [], "", 0
    while i < len(src):
        ch = src[i]
        if ch == "{":
            head, buf = buf.strip(), ""
            if head.startswith("@"):
                ctx.append(head)
                stack.append("at")
                i += 1
                continue
            depth, j = 1, i + 1
            while j < len(src) and depth:
                depth += (src[j] == "{") - (src[j] == "}")
                j += 1
            body = src[i + 1:j - 1]
            props = {m.group(1).strip() for m in re.finditer(r"(?m)^\s*([a-z-]+)\s*:", body)}
            yield " | ".join(ctx), " ".join(head.split()), props
            i = j
            continue
        if ch == "}":
            if stack and stack.pop() == "at" and ctx:
                ctx.pop()
        else:
            buf += ch
        i += 1


def main() -> int:
    groups = collections.defaultdict(list)
    for context, selector, props in rules(CSS.read_text()):
        if not selector or "," in selector:
            continue          # grouped selectors are not a collision on their own
        groups[(context, selector)].append(props)

    clashes = []
    for (context, selector), seen in groups.items():
        if len(seen) < 2:
            continue
        overlap = set.intersection(*seen)
        if overlap:
            clashes.append((selector, context or "(top level)", len(seen), sorted(overlap)))

    for selector, context, n, props in sorted(clashes)[:20]:
        print(f"  {selector:36} x{n}  in {context[:24]:26} {', '.join(props)[:46]}")
    if len(clashes) > 20:
        print(f"  … and {len(clashes) - 20} more")

    print(f"\n  {len(clashes)} selectors defined twice with the same property "
          f"(baseline {BASELINE})")
    if len(clashes) > BASELINE:
        print(f"  FAIL: {len(clashes) - BASELINE} new collision(s) since the baseline.")
        return 1
    if len(clashes) < BASELINE:
        print(f"  {BASELINE - len(clashes)} fixed — lower BASELINE to {len(clashes)} to lock it in.")
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
