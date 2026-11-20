"""Integration and accessibility checks for the design system showcase, in Arabic and English.

    python check_showcase.py ../showcase.html [--chrome /usr/bin/google-chrome] [--shots DIR]

Needs Python Playwright and a Chrome or Chromium. Prints one line per check and exits non-zero
if any check fails. Contrast is measured on the colours the browser actually renders (WCAG 2.1).
"""
import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PALETTE_WHITE = (245, 245, 245)
PURE_WHITE = (255, 255, 255)


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


def run_direction(page, direction: str, report: Report, shots: Path | None) -> None:
    c = lambda name, ok, detail="": report.check(direction, name, ok, detail)  # noqa: E731
    style = lambda sel, prop, pseudo="null": page.evaluate(  # noqa: E731
        f"getComputedStyle(document.querySelector({sel!r}), {pseudo})[{prop!r}]"
    )

    # Direction, language and font
    c("dir and lang", page.evaluate("[document.documentElement.dir, document.documentElement.lang]")
      == ([direction, "ar"] if direction == "rtl" else [direction, "en"]))
    font = page.evaluate("getComputedStyle(document.body).fontFamily")
    c("font family", ("IBM Plex Sans Arabic" if direction == "rtl" else "Space Grotesk") in font, font)

    # The forward arrow: the path points right, so it must be mirrored in RTL only.
    transform = style(".icon-rtl-flip", "transform")
    c("forward arrow points " + ("left" if direction == "rtl" else "right"),
      (transform != "none") == (direction == "rtl"), transform)

    # Palette and contrast
    bg = rgb(page.evaluate("getComputedStyle(document.body).backgroundColor"))
    for sel in (".card", ".input-field", ".modal-content"):
        colour = rgb(style(sel, "backgroundColor"))
        c(f"{sel} uses the palette white, not #FFFFFF", colour != PURE_WHITE, str(colour))
    field = rgb(style(".input-field", "backgroundColor"))
    text_main = contrast(rgb(page.evaluate("getComputedStyle(document.body).color")), bg)
    text_sec = contrast(rgb(style(".form-label", "color")), bg)
    placeholder = contrast(rgb(style("#itemName", "color", "'::placeholder'")), field)
    border = contrast(rgb(style("#itemName", "borderTopColor")), bg)
    c("main text contrast >= 4.5", text_main >= 4.5, f"{text_main:.2f}")
    c("secondary text contrast >= 4.5", text_sec >= 4.5, f"{text_sec:.2f}")
    c("placeholder contrast >= 4.5", placeholder >= 4.5, f"{placeholder:.2f}")
    c("field border contrast >= 3", border >= 3, f"{border:.2f}")

    # Keyboard
    page.locator("body").focus()
    reached = []
    for _ in range(12):
        page.keyboard.press("Tab")
        reached.append(page.evaluate(
            "[document.activeElement.matches('[aria-disabled=\"true\"]'),"
            " document.getElementById('deleteModal').contains(document.activeElement)]"
        ))
    c("disabled link is skipped by Tab", not any(disabled for disabled, _ in reached))
    c("closed dialog is not reachable", not any(inside for _, inside in reached))
    page.locator("body").focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    c("keyboard focus shows an outline", page.evaluate(
        "getComputedStyle(document.activeElement).outlineStyle") == "solid")
    c("disabled link ignores the pointer", style("a.btn", "pointerEvents") == "none")

    # Focus on fields, valid and invalid
    for sel in ("#itemName", "#itemPrice"):
        look = f"(() => {{ const s = getComputedStyle(document.querySelector({sel!r})); return [s.borderTopColor, s.boxShadow, s.outlineStyle, s.outlineWidth].join(' ') }})()"
        page.evaluate("document.activeElement && document.activeElement.blur()")
        before = page.evaluate(look)
        page.focus(sel)
        after = page.evaluate(look)
        c(f"focus is visible on {sel}" + (" (invalid)" if sel == "#itemPrice" else ""), before != after, after)

    # Dialog
    trigger = page.locator("section button.btn-secondary").last
    trigger.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    c("dialog opens", page.get_attribute("#deleteModal", "aria-hidden") == "false")
    c("focus moves into the dialog", in_modal(page))
    c("page behind is inert (no clicks, no focus, hidden from screen readers)",
      page.evaluate("document.querySelector('.showcase-wrapper').inert") is True)
    top = page.evaluate("document.elementFromPoint(window.innerWidth / 2, 20).closest('.modal-overlay') !== null")
    c("dialog backdrop covers the sticky toolbar", top)
    dir_before = page.evaluate("document.documentElement.dir")
    page.mouse.click(page.viewport_size["width"] - 60 if direction == "ltr" else 60, 28)
    c("toolbar button cannot be used while the dialog is open",
      page.evaluate("document.documentElement.dir") == dir_before)
    stays = []
    for key in ["Tab"] * 4 + ["Shift+Tab"] * 4:
        page.keyboard.press(key)
        stays.append(in_modal(page))
    c("Tab and Shift+Tab stay inside the dialog", all(stays), str(stays))
    page.mouse.click(5, page.viewport_size["height"] - 5)  # the backdrop
    page.keyboard.press("Tab")
    c("focus stays inside after clicking the backdrop", in_modal(page))
    page.evaluate("document.body.focus()")
    page.evaluate("document.querySelector('.toolbar button').focus()")
    c("focus cannot be moved to the page by script while open", in_modal(page))
    if shots:
        page.screenshot(path=str(shots / f"dialog-{direction}.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    c("Escape closes the dialog", page.get_attribute("#deleteModal", "aria-hidden") == "true")
    c("page is interactive again", page.evaluate("document.querySelector('.showcase-wrapper').inert") is False)
    c("focus returns to the opening button", page.evaluate("document.activeElement") is not None
      and trigger.evaluate("el => el === document.activeElement"))
    if shots:
        page.screenshot(path=str(shots / f"page-{direction}.png"), full_page=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("page")
    parser.add_argument("--chrome", default=None)
    parser.add_argument("--shots", default=None)
    args = parser.parse_args()
    url = Path(args.page).resolve().as_uri()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    report = Report()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 420, "height": 900})
        page.goto(url)
        page.wait_for_timeout(300)
        run_direction(page, "rtl", report, shots)
        page.click(".toolbar button")
        page.wait_for_timeout(200)
        run_direction(page, "ltr", report, shots)
        browser.close()
    for direction, status, name, detail in report.rows:
        print(f"{status}  {direction.upper()}  {name}" + (f"  [{detail}]" if detail else ""))
    print(f"\n{len(report.rows) - report.failed} passed, {report.failed} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
