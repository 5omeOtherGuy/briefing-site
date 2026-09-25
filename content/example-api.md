# Example notes (EXAMPLE)

This file demonstrates the **minimal markdown subset** that `build.py` renders
for `content/<project>.md`: headings, bullet lists, [links](index.html) and
**bold** text. Everything else is shown literally.

## What changed

- The markdown renderer escapes HTML first, so it is safe to paste text.
- Links must use a relative path, a `#fragment`, or an absolute `https`/`mailto`
  address. Anything else renders as plain text.
- This section is optional: without `content/<project>.md` the page simply omits
  the **Details** block.

## Reading order

- **Key numbers** first: the six tiles at the top.
- **Results** next: the same five chart kinds for every project.
- **Decisions needed** last: the recommended option is highlighted.
