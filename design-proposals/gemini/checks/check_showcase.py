"""Integration and accessibility checks for the design system showcase, in Arabic and English.

    python check_showcase.py ../showcase.html [--chrome /usr/bin/google-chrome] [--shots DIR]

Needs Python Playwright and a Chrome or Chromium. Prints one line per check and exits non-zero
if any check fails. Contrast is measured on the colours the browser actually renders (WCAG 2.1).

These are automated checks in one browser engine (Chromium). They do not replace testing with
real screen readers, other browsers, touch devices or zoom; RESULTS.md lists what is not covered.
"""
import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PURE_WHITE = (255, 255, 255)
ARABIC = re.compile(r"[؀-ۿ]")
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
TRIGGER = "main section:last-of-type button"


def rgb(css: str) -> tuple[int, int, int]:
    return tuple(int(v) for v in re.findall(r"\d+", css)[:3])


def luminance(c: tuple[int, int, int]) -> float:
    def ch(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def check(self, direction: str, name: str, passed: bool, detail: str = "") -> None:
        self.rows.append((direction, "PASS" if passed else "FAIL", name, detail))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r[1] == "FAIL")


def in_modal(page) -> bool:
    return page.evaluate("document.getElementById('deleteModal').contains(document.activeElement)")


def is_open(page) -> bool:
    return page.get_attribute("#deleteModal", "aria-hidden") == "false"


# Every visible text, placeholder and aria-label, with the language that applies to it.
TEXT_LANGS = """() => {
  const out = [];
  const langOf = el => (el.closest('[lang]') || document.documentElement).getAttribute('lang');
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, el = node.parentElement;
    if (!node.textContent.trim() || el.closest('script,style')) continue;
    out.push([node.textContent.trim(), langOf(el)]);
  }
  for (const el of document.querySelectorAll('[placeholder],[aria-label]')) {
    for (const attr of ['placeholder', 'aria-label']) {
      if (el.getAttribute(attr)) out.push([el.getAttribute(attr), langOf(el)]);
    }
  }
  return out;
}"""


def accessible_headings(page) -> tuple[list[str], list[str]]:
    """Chrome's own accessibility tree (what screen readers get): the roles and the headings
    of every node that is not ignored."""
    cdp = page.context.new_cdp_session(page)
    try:
        nodes = cdp.send("Accessibility.getFullAXTree")["nodes"]
    finally:
        cdp.detach()
    shown = [n for n in nodes if not n.get("ignored")]
    roles = [n.get("role", {}).get("value", "") for n in shown]
    headings = [n.get("name", {}).get("value", "") for n in shown if n.get("role", {}).get("value") == "heading"]
    return roles, headings


def tab_to(page, selector: str, limit: int = 30) -> bool:
    """Press Tab from the top of the page until the element is focused."""
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.locator("body").focus()
    for _ in range(limit):
        page.keyboard.press("Tab")
        if page.evaluate(f"document.activeElement === document.querySelector({selector!r})"):
            return True
    return False


def field_look(page, sel: str) -> str:
    return page.evaluate(
        f"(() => {{ const s = getComputedStyle(document.querySelector({sel!r}));"
        " return [s.borderTopColor, s.boxShadow, s.outlineStyle, s.outlineWidth].join(' ') })()"
    )


def run_direction(page, direction: str, report: Report, shots: Path | None) -> None:
    c = lambda name, ok, detail="": report.check(direction, name, ok, detail)  # noqa: E731
    style = lambda sel, prop, pseudo="null": page.evaluate(  # noqa: E731
        f"getComputedStyle(document.querySelector({sel!r}), {pseudo})[{prop!r}]"
    )
    page.evaluate("window.scrollTo(0, 0)")

    # ---- Direction, language, content and font
    lang = "ar" if direction == "rtl" else "en"
    c("dir and lang", page.evaluate("[document.documentElement.dir, document.documentElement.lang]") == [direction, lang])
    font = page.evaluate("getComputedStyle(document.body).fontFamily")
    c("font family", ("IBM Plex Sans Arabic" if direction == "rtl" else "Space Grotesk") in font, font)
    texts = page.evaluate(TEXT_LANGS)
    wrong = [t for t, l in texts if ARABIC.search(t) and l != "ar"]
    c("no Arabic text is marked as another language", not wrong, "; ".join(wrong[:3]))
    untagged = [t for t, l in texts if LATIN_WORD.search(t) and not ARABIC.search(t) and l != "en"]
    c("English text is marked lang=en", not untagged, "; ".join(untagged[:3]))
    primary = page.evaluate("document.querySelector('.btn-primary').textContent.trim()")
    if direction == "ltr":
        left = [t for t, _ in texts if ARABIC.search(t)]
        c("English page shows English content (no Arabic left)", not left, "; ".join(left[:3]))
    c("component text is in the page language", bool(ARABIC.search(primary)) == (direction == "rtl"), primary)

    # ---- The forward arrow: the path points right, so it must be mirrored in RTL only.
    transform = style(".icon-rtl-flip", "transform")
    c("forward arrow points " + ("left" if direction == "rtl" else "right"),
      (transform != "none") == (direction == "rtl"), transform)

    # ---- Palette and contrast
    bg = rgb(page.evaluate("getComputedStyle(document.body).backgroundColor"))
    for sel in (".card", ".input-field", ".modal-content"):
        colour = rgb(style(sel, "backgroundColor"))
        c(f"{sel} uses the palette white, not #FFFFFF", colour != PURE_WHITE, str(colour))
    field = rgb(style(".input-field", "backgroundColor"))
    for name, ratio, needed in (
        ("main text contrast >= 4.5", contrast(rgb(page.evaluate("getComputedStyle(document.body).color")), bg), 4.5),
        ("secondary text contrast >= 4.5", contrast(rgb(style(".form-label", "color")), bg), 4.5),
        ("placeholder contrast >= 4.5", contrast(rgb(style("#itemName", "color", "'::placeholder'")), field), 4.5),
        ("field border contrast >= 3", contrast(rgb(style("#itemName", "borderTopColor")), bg), 3),
    ):
        c(name, ratio >= needed, f"{ratio:.2f}")

    # ---- Keyboard focus on the page
    reached = []
    page.locator("body").focus()
    for _ in range(14):
        page.keyboard.press("Tab")
        reached.append(page.evaluate(
            "[document.activeElement.matches('[aria-disabled=\"true\"]'),"
            " document.getElementById('deleteModal').contains(document.activeElement)]"
        ))
    c("disabled link is skipped by Tab", not any(d for d, _ in reached))
    c("closed dialog is not reachable", not any(i for _, i in reached))
    c("disabled link ignores the pointer", style("a.btn", "pointerEvents") == "none")
    tab_to(page, ".btn-primary")
    c("keyboard focus shows an outline on page buttons",
      page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid")

    # ---- Keyboard focus on the toolbar's dark background, every toolbar button
    bar = rgb(style(".toolbar", "backgroundColor"))
    for i in range(page.locator(".toolbar button").count()):
        sel = f".toolbar button:nth-of-type({i + 1})"
        box_before = page.locator(sel).bounding_box()
        reached_it = tab_to(page, sel)
        st = page.evaluate(f"(() => {{ const s = getComputedStyle(document.querySelector({sel!r}));"
                           " return [s.outlineStyle, s.outlineWidth, s.outlineColor] })()")
        ratio = contrast(rgb(st[2]), bar)
        c(f"toolbar button {i + 1} is reached by Tab", reached_it)
        c(f"toolbar button {i + 1} focus outline is visible on the dark bar (2px, >= 3:1)",
          st[0] == "solid" and float(st[1].rstrip("px")) >= 2 and ratio >= 3, f"{st[0]} {st[1]} {st[2]}, {ratio:.2f}:1")
        c(f"toolbar button {i + 1} keeps its size and place on focus", page.locator(sel).bounding_box() == box_before)

    # ---- Validation message: nothing on load, shown on a validation error, announced
    c("no error shown on load",
      page.evaluate("document.getElementById('price-error').textContent.trim()") == ""
      and page.get_attribute("#itemPrice", "aria-invalid") != "true")
    c("error region is announced to screen readers (role=alert or aria-live)",
      page.evaluate("(() => { const e = document.getElementById('price-error');"
                    " return e.getAttribute('role') === 'alert' || e.getAttribute('aria-live') === 'assertive' })()"))
    page.fill("#itemPrice", "abc")
    page.keyboard.press("Tab")
    message = page.evaluate("document.getElementById('price-error').textContent.trim()")
    c("an invalid value shows the error",
      bool(message) and page.get_attribute("#itemPrice", "aria-invalid") == "true", message)
    c("the error is linked to its field (aria-describedby)",
      "price-error" in (page.get_attribute("#itemPrice", "aria-describedby") or ""))
    c("the error is in the page language", bool(ARABIC.search(message)) == (direction == "rtl"), message)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    before = field_look(page, "#itemPrice")
    page.focus("#itemPrice")
    c("focus is visible on the invalid field", before != field_look(page, "#itemPrice"), field_look(page, "#itemPrice"))
    page.evaluate("document.activeElement && document.activeElement.blur()")
    before = field_look(page, "#itemName")
    page.focus("#itemName")
    c("focus is visible on a normal field", before != field_look(page, "#itemName"), field_look(page, "#itemName"))
    if shots:
        page.locator("#itemPrice").scroll_into_view_if_needed()
        page.screenshot(path=str(shots / f"error-{direction}.png"))
    page.fill("#itemPrice", "45")
    page.keyboard.press("Tab")
    c("a valid value clears the error",
      page.evaluate("document.getElementById('price-error').textContent.trim()") == ""
      and page.get_attribute("#itemPrice", "aria-invalid") != "true")
    page.fill("#itemPrice", "")

    # ---- Dialog: focus, isolation, scroll lock, and every way of closing it
    trigger = page.locator(TRIGGER)
    for closer in ("Escape", ".modal-close", ".modal-footer .btn-secondary", ".modal-footer .btn-primary"):
        tag = f"[{closer}]"
        page.evaluate("window.scrollTo(0, 0)")
        tab_to(page, TRIGGER)
        page.evaluate("window.scrollBy(0, -120)")  # not at the bottom, so a scroll could show
        y = page.evaluate("window.scrollY")
        layout = page.locator(".content-area").bounding_box()
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        c(f"dialog opens {tag}", is_open(page))
        c(f"focus moves into the dialog {tag}", in_modal(page))
        page.mouse.move(200, 450)
        page.mouse.wheel(0, 900)
        page.mouse.wheel(0, -900)
        page.keyboard.press("PageDown")
        page.keyboard.press("Home")
        page.wait_for_timeout(250)
        c(f"background does not scroll while open {tag}", page.evaluate("window.scrollY") == y,
          f"{y} -> {page.evaluate('window.scrollY')}")
        locked = page.locator(".content-area").bounding_box()
        c(f"locking the scroll does not shift the layout {tag}",
          (locked["x"], locked["width"]) == (layout["x"], layout["width"]),
          f"x {layout['x']}->{locked['x']}, width {layout['width']}->{locked['width']}")
        if closer == "Escape":
            c("page behind is inert", page.evaluate("document.querySelector('.showcase-wrapper').inert") is True)
            roles, headings = accessible_headings(page)
            title = page.evaluate("document.getElementById('modalTitle').textContent.trim()")
            c("screen readers reach only the dialog while it is open (Chrome accessibility tree)",
              "dialog" in roles and headings == [title], f"headings: {headings}")
            c("dialog backdrop covers the sticky toolbar",
              page.evaluate("document.elementFromPoint(window.innerWidth / 2, 20).closest('.modal-overlay') !== null"))
            dir_before = page.evaluate("document.documentElement.dir")
            page.mouse.click(page.viewport_size["width"] - 60 if direction == "ltr" else 60, 28)
            c("toolbar button cannot be used while open", page.evaluate("document.documentElement.dir") == dir_before)
            stays = []
            for key in ["Tab"] * 4 + ["Shift+Tab"] * 4:
                page.keyboard.press(key)
                stays.append(in_modal(page))
            c("Tab and Shift+Tab stay inside the dialog", all(stays), str(stays))
            page.mouse.click(5, page.viewport_size["height"] - 5)
            page.keyboard.press("Tab")
            c("focus stays inside after clicking the backdrop", in_modal(page))
            page.evaluate("document.querySelector('.toolbar button').focus()")
            c("focus cannot be moved to the page by script while open", in_modal(page))
            if shots:
                page.screenshot(path=str(shots / f"dialog-{direction}.png"))
            page.keyboard.press("Escape")
        else:
            page.locator(closer).click()
        page.wait_for_timeout(400)
        c(f"dialog closes {tag}", not is_open(page))
        c(f"scroll position is restored {tag}", page.evaluate("window.scrollY") == y,
          f"{y} -> {page.evaluate('window.scrollY')}")
        c(f"page is interactive and scrollable again {tag}",
          page.evaluate("document.querySelector('.showcase-wrapper').inert") is False
          and page.evaluate("getComputedStyle(document.documentElement).overflowY") != "hidden")
        c(f"focus returns to the opening button {tag}", trigger.evaluate("el => el === document.activeElement"))
    page.mouse.move(200, 450)
    before = page.evaluate("window.scrollY")
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(300)
    c("the page scrolls normally after the dialog closes", page.evaluate("window.scrollY") != before)
    if shots:
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(shots / f"page-{direction}.png"), full_page=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("page")
    parser.add_argument("--chrome", default=None)
    parser.add_argument("--shots", default=None)
    parser.add_argument("--classic-scrollbars", action="store_true",
                        help="scrollbars that take up room, as on Windows (default: overlay, as on phones)")
    args = parser.parse_args()
    url = Path(args.page).resolve().as_uri()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    report = Report()
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=args.chrome, args=["--no-sandbox"],
            ignore_default_args=["--hide-scrollbars"] if args.classic_scrollbars else None,
        )
        page = browser.new_page(viewport={"width": 420, "height": 900})
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
        page.on("requestfailed", lambda r: problems.append(f"failed to load {r.url}: {r.failure}"))
        page.goto(url)
        page.wait_for_load_state("load")
        page.wait_for_timeout(500)
        run_direction(page, "rtl", report, shots)
        page.evaluate("window.scrollTo(0, 0)")
        page.click(".toolbar button")
        page.wait_for_timeout(500)
        run_direction(page, "ltr", report, shots)
        browser.close()
    report.check("both", "no console errors, script errors or failed resource loads", not problems,
                 " | ".join(problems[:5]))
    for direction, status, name, detail in report.rows:
        print(f"{status}  {direction.upper()}  {name}" + (f"  [{detail}]" if detail else ""))
    scrollbars = "classic scrollbars" if args.classic_scrollbars else "overlay scrollbars"
    print(f"\n{len(report.rows) - report.failed} passed, {report.failed} failed ({scrollbars})")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
