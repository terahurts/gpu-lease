"""Check every relative markdown link (file + #anchor) in README and docs/."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [os.path.join(ROOT, "README.md")] + [
    os.path.join(ROOT, "docs", n) for n in sorted(os.listdir(os.path.join(ROOT, "docs")))
    if n.endswith(".md")]

LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def slug(text):
    text = text.strip().lower()
    text = re.sub(r"`", "", text)
    text = re.sub(r"[^\w\s-]", "", text)      # drop punctuation (incl. em dash)
    text = re.sub(r"\s", "-", text)
    return text


def anchors(path):
    out = set()
    in_fence = False
    for line in open(path, encoding="utf-8"):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            out.add(slug(m.group(2)))
    return out


cache = {}
broken = []
for path in FILES:
    rel = os.path.relpath(path, ROOT)
    text = open(path, encoding="utf-8").read()
    # ignore links inside fenced code blocks
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    for target in LINK.findall(body):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, _, anchor = target.partition("#")
        dest = os.path.normpath(os.path.join(os.path.dirname(path), file_part)) \
            if file_part else path
        if not os.path.exists(dest):
            broken.append("%s -> %s (no such file)" % (rel, target))
            continue
        if anchor:
            if dest not in cache:
                cache[dest] = anchors(dest)
            if anchor not in cache[dest]:
                broken.append("%s -> %s (no such heading)" % (rel, target))
print("checked %d files" % len(FILES))
if broken:
    print("\n".join(broken))
    sys.exit(1)
print("all links resolve")
