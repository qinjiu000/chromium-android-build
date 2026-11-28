#!/usr/bin/env python3
"""Fail if a real launcher entry shows no window.

Rule (Android launch behaviour, not lint): an activity that the user
actually taps on the home screen must use a visible theme. A theme that
inherits Theme.BrowserUI.NoDisplay (android:windowNoDisplay=true) starts
the activity but never shows a window, so the tap looks dead.

What counts as a launcher entry:
  * an <activity> whose own intent-filter has category LAUNCHER, or
  * an <activity-alias> with a LAUNCHER intent-filter -- the check then
    follows android:targetActivity and checks THAT activity's theme.

Deliberately NOT flagged: forwarding/trampoline activities that correctly
use a NoDisplay theme (ChromeLauncherActivity, DragAndDropLauncherActivity,
IncognitoTabLauncher, ...) -- they have no LAUNCHER filter, so a tap on
the home screen never lands on them directly.

The manifest is a Jinja template, not plain XML, so this scans text with
regexes instead of an XML parser. Theme inheritance is resolved one level
at a time from styles.xml, so a renamed NoDisplay child
(e.g. LauncherTheme, parent Theme.BrowserUI.NoDisplay) is caught too.

Usage:
    python3 check_launcher_theme.py <AndroidManifest.xml>
        [--styles chrome/android/java/res/values]
Exit: 0 PASS, 1 FAIL (a launcher entry uses a NoDisplay theme),
      2 error (missing file -- loud failure, never a silent pass).
"""

import re
import sys
from pathlib import Path

BANNED_ROOTS = {"Theme.BrowserUI.NoDisplay"}

# <activity ...>...</activity>  or self-closing  <activity ... />
# Parsed with a small scanner, not one regex: the manifest is a Jinja
# template, and blocks contain self-closing inner tags (<property ... />)
# that would truncate a naive non-greedy match before the intent-filter.
# <activity-alias ...> is a different element -- the char after "activity"
# must be whitespace or ">", never "-".
ACTIVITY_RE = re.compile(r"<activity(?=[\s>])")
ALIAS_RE = re.compile(r"<activity-alias(?=[\s>])")
APPLICATION_RE = re.compile(r"<application(?=[\s>])")
MANIFEST_RE = re.compile(r"<manifest(?=[\s>])")
STYLE_RE = re.compile(r"<style\b([^>]*)>", re.DOTALL)
ATTR_RE = re.compile(r"""([:\w.-]+)\s*=\s*(["'])(.*?)\2""", re.DOTALL)
CATEGORY_RE = re.compile(r"<category\b([^>]*)>", re.DOTALL)
LAUNCHER = "android.intent.category.LAUNCHER"


def attributes(text):
    """Parse quoted XML attributes independent of quote style and order."""
    return {name: value for name, _, value in ATTR_RE.findall(text)}


def opening_tag_attributes(text, start_re):
    """Return parsed attributes from the first matching opening tag."""
    match = start_re.search(text)
    if match is None:
        return {}
    end = text.find(">", match.start())
    if end == -1:
        return {}
    return attributes(text[match.start():end + 1])


def normalize_theme(name):
    return name.removeprefix("@style/")


def normalize_component(name, package):
    if name.startswith("."):
        return f"{package}{name}" if package else name
    if "." not in name and package:
        return f"{package}.{name}"
    return name


def has_launcher_category(body):
    return any(
        attributes(match.group(1)).get("android:name") == LAUNCHER
        for match in CATEGORY_RE.finditer(body))


def scan_blocks(text, tag, start_re):
    """Yield (opening-tag text, body text) for each <tag>...</tag> block."""
    for m in start_re.finditer(text):
        open_end = text.find(">", m.start())
        if open_end == -1:
            continue
        head = text[m.start():open_end + 1]
        if head.rstrip().endswith("/>"):
            yield head, ""
            continue
        close = text.find(f"</{tag}>", open_end)
        if close == -1:
            continue
        yield head, text[open_end + 1:close]


def banned_themes(styles_dirs):
    """Theme.BrowserUI.NoDisplay plus every style inheriting from it."""
    banned = set(BANNED_ROOTS)
    styles = {}
    for d in styles_dirs:
        for f in Path(d).glob("*.xml"):
            for match in STYLE_RE.finditer(f.read_text(errors="ignore")):
                attrs = attributes(match.group(1))
                name = attrs.get("name")
                if name:
                    styles[name] = normalize_theme(attrs.get("parent", ""))
    changed = True
    while changed:
        changed = False
        for name, parent in styles.items():
            if name not in banned and parent in banned:
                banned.add(name)
                changed = True
    return banned


def activities(manifest_text):
    """name -> (theme, window_no_display, has_launcher_filter)."""
    package = opening_tag_attributes(manifest_text, MANIFEST_RE).get(
        "package", "")
    application_theme = normalize_theme(
        opening_tag_attributes(manifest_text, APPLICATION_RE).get(
            "android:theme", ""))
    out = {}
    for head, body in scan_blocks(manifest_text, "activity", ACTIVITY_RE):
        attrs = attributes(head)
        name = attrs.get("android:name")
        if not name:
            continue
        name = normalize_component(name, package)
        out[name] = (
            normalize_theme(attrs.get("android:theme", application_theme)),
            attrs.get("android:windowNoDisplay", "false") == "true",
            has_launcher_category(body),
        )
    return out


def launcher_targets(manifest_text):
    """Activity names reached by a LAUNCHER activity-alias."""
    package = opening_tag_attributes(manifest_text, MANIFEST_RE).get(
        "package", "")
    targets = set()
    for head, body in scan_blocks(manifest_text, "activity-alias", ALIAS_RE):
        attrs = attributes(head)
        target = attrs.get("android:targetActivity")
        if has_launcher_category(body) and target:
            targets.add(normalize_component(target, package))
    return targets


def main(argv):
    if len(argv) < 2:
        print("usage: check_launcher_theme.py <AndroidManifest.xml> "
              "[--styles <res/values dir> ...]", file=sys.stderr)
        return 2
    manifest = Path(argv[1])
    if not manifest.is_file():
        print(f"ERROR: manifest not found: {manifest}", file=sys.stderr)
        return 2
    styles_dirs = []
    args = argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--styles" and i + 1 < len(args):
            styles_dirs.append(args[i + 1])
            i += 2
        else:
            print(f"ERROR: unknown argument: {args[i]}", file=sys.stderr)
            return 2
    if not styles_dirs:
        # Default layout: <src>/chrome/android/java/AndroidManifest.xml
        # -> <src>/chrome/android/java/res/values
        styles_dirs = [str(manifest.parent / "res" / "values")]
    missing = [d for d in styles_dirs if not Path(d).is_dir()]
    if missing:
        print(f"ERROR: styles dir not found: {missing}", file=sys.stderr)
        return 2

    text = manifest.read_text(errors="ignore")
    banned = banned_themes(styles_dirs)
    acts = activities(text)
    launcher = {n for n, (_, _, has) in acts.items() if has}
    launcher |= launcher_targets(text)

    problems = []
    for name in sorted(launcher):
        theme, no_display, _ = acts.get(name, ("<missing>", False, False))
        if theme == "<missing>":
            problems.append(f"{name}: launcher entry target not declared")
        elif theme in banned:
            problems.append(
                f"{name}: launcher entry uses NoDisplay theme @{theme}")
        elif no_display:
            problems.append(
                f"{name}: launcher entry sets android:windowNoDisplay=true")
    if problems:
        print("LAUNCHER-THEME: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"LAUNCHER-THEME: PASS "
          f"({len(launcher)} launcher entries checked: "
          f"{', '.join(sorted(launcher)) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
