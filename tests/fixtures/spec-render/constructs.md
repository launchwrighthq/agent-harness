# Every construct the renderer reads & writes

Date: 2026-01-01
Status: in-progress
Subject: the <renderer> under "test"
Range: abc1234..def5678

The standfirst holds `inline code`, a [link](https://example.com/a?b=1&c=2) and **bold
text** across two lines.

A second standfirst paragraph quotes {{TITLE}} and keeps it as text.

## 1. Scope

A paragraph with *italic*, _underscore italic_, a snake_case_name, ~~struck~~ words and
**bold with *italic* inside**. The ampersand & and the quote ' and " stay escaped.

A code span with a backtick: `` `[text](url)` `` stays code, and ``a`b`` too. An unclosed `
backtick stays text.

![A wide drawing](assets/drawing.png)

![A recording](assets/session.webm)

A sentence with ![an icon](assets/icon.svg) beside it, and a [page](assets/page.html) link.

## 2. Decisions

| ID | Decision | Detail |
|---|---|---|
| D1 | Keep `a|b` as code | **Bold** cell |
| D2 | [A link](https://example.com) | `x` and <tag> |

### 2.1 A numbered subsection

#### 2.1.1. A deeper heading with a dot

### An unnumbered subsection

## 2. Decisions

The second heading with the same text gets its own anchor.

## 3. Lists

- One item
  that wraps onto a second line.
- Two item with a nested list:
  - Nested one
  - Nested two

  A continuation paragraph of item two.
- Three

1. First step.

2. Second step, after a blank line.
   1. A nested step.
3. Third step.

> A quote that runs
> over two lines.
>
> And a second paragraph.

```bash
echo "<code> & stays raw"
```

```
no language
```

## Unnumbered closing section

Ünïcödé text and a CJK word 仕様 stay as they are.
