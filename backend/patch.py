"""The apply_patch edit format of OpenAI's Codex CLI (codex-rs/apply-patch), ported for the agent's file tools.

    *** Begin Patch
    *** Update File: src/app.py
    @@ def main():
    -    print("hi")
    +    print("hello")
    *** Add File: notes.txt
    +first line
    *** Delete File: old.txt
    *** End Patch

Lines are located like Codex's seek_sequence: exactly, then ignoring trailing whitespace, then surrounding
whitespace, then typographic punctuation, so small whitespace slips by the model still apply. A patch is checked
in full before any file is written.
"""

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BEGIN, END = "*** Begin Patch", "*** End Patch"
ADD, DELETE, UPDATE, MOVE = "*** Add File: ", "*** Delete File: ", "*** Update File: ", "*** Move to: "
EOF_MARK = "*** End of File"
PUNCTUATION = str.maketrans({
    **dict.fromkeys("‐‑‒–—―−", "-"),
    **dict.fromkeys("‘’‚‛", "'"),
    **dict.fromkeys("“”„‟", '"'),
    **dict.fromkeys("            　", " "),
})


class PatchError(Exception):
    pass


@dataclass
class Chunk:
    context: str | None = None  # the "@@ ..." line: a line just above the change, to find it in the file
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    kept: list[tuple[int, int]] = field(default_factory=list)  # (old, new) indexes of unchanged context lines
    eof: bool = False


@dataclass
class Hunk:
    kind: str  # add | delete | update
    path: str
    contents: str = ""
    move_to: str | None = None
    chunks: list[Chunk] = field(default_factory=list)


def parse(text: str) -> list[Hunk]:
    lines = text.strip().splitlines()
    if lines and lines[0].strip() in ("<<EOF", "<<'EOF'", '<<"EOF"') and lines[-1].strip().endswith("EOF"):
        lines = lines[1:-1]  # the model wrapped it as a heredoc
    if not lines or lines[0].strip() != BEGIN:
        raise PatchError(f"补丁第一行必须是 '{BEGIN}'")
    if lines[-1].strip() != END:
        raise PatchError(f"补丁最后一行必须是 '{END}'")
    hunks: list[Hunk] = []
    i = 1
    while i < len(lines) - 1:
        line = lines[i].strip()
        if line.startswith(ADD):
            hunk = Hunk("add", line[len(ADD):].strip())
            i += 1
            body = []
            while i < len(lines) - 1 and lines[i].startswith("+"):
                body.append(lines[i][1:])
                i += 1
            hunk.contents = "".join(f"{b}\n" for b in body)
            hunks.append(hunk)
        elif line.startswith(DELETE):
            hunks.append(Hunk("delete", line[len(DELETE):].strip()))
            i += 1
        elif line.startswith(UPDATE):
            hunk = Hunk("update", line[len(UPDATE):].strip())
            i += 1
            if i < len(lines) - 1 and lines[i].strip().startswith(MOVE):
                hunk.move_to = lines[i].strip()[len(MOVE):].strip()
                i += 1
            i = _parse_chunks(lines, i, hunk)
            if not hunk.chunks:
                raise PatchError(f"第 {i} 行：更新 {hunk.path} 的部分是空的")
            hunks.append(hunk)
        elif not line:
            i += 1
        else:
            raise PatchError(f"第 {i + 1} 行无法识别：'{lines[i]}'。每个文件以 '{UPDATE}'、'{ADD}' 或 '{DELETE}' 开头")
    if not hunks:
        raise PatchError("补丁里没有任何文件改动")
    return hunks


def _parse_chunks(lines: list[str], i: int, hunk: Hunk) -> int:
    chunk: Chunk | None = None
    while i < len(lines) - 1 and not lines[i].lstrip().startswith((ADD, DELETE, UPDATE)):
        line = lines[i]
        if line.strip() == EOF_MARK:
            if chunk:
                chunk.eof = True
            i += 1
            continue
        if line.startswith("@@"):
            chunk = Chunk(context=line[2:].strip() or None)
            hunk.chunks.append(chunk)
        else:
            if chunk is None:
                chunk = Chunk()  # the first chunk may leave out its "@@" line
                hunk.chunks.append(chunk)
            mark, body = (line[0], line[1:]) if line else (" ", "")
            if mark == "+":
                chunk.new.append(body)
            elif mark == "-":
                chunk.old.append(body)
            elif mark == " ":
                chunk.kept.append((len(chunk.old), len(chunk.new)))
                chunk.old.append(body)
                chunk.new.append(body)
            else:
                raise PatchError(f"第 {i + 1} 行：改动行必须以 ' '、'+' 或 '-' 开头：'{line}'")
        i += 1
    return i


def seek(lines: list[str], pattern: list[str], start: int = 0, eof: bool = False) -> int | None:
    """Index where `pattern` occurs in `lines` from `start`, matching ever more loosely (Codex seek_sequence)."""
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None
    first = max(start, len(lines) - len(pattern)) if eof else start
    for norm in (lambda s: s, str.rstrip, str.strip, lambda s: s.strip().translate(PUNCTUATION)):
        want = [norm(p) for p in pattern]
        for i in range(first, len(lines) - len(pattern) + 1):
            if all(norm(lines[i + k]) == want[k] for k in range(len(pattern))):
                return i
    return None


def nearest(lines: list[str], pattern: list[str]) -> str:
    """The stretch of the file most like `pattern`, with line numbers, to show the model what is really there."""
    if not lines or not pattern:
        return ""
    size = len(pattern)
    target = "\n".join(p.strip() for p in pattern)
    best, at = 0.0, 0
    for i in range(max(1, len(lines) - size + 1)):
        ratio = difflib.SequenceMatcher(None, target, "\n".join(l.strip() for l in lines[i : i + size])).quick_ratio()
        if ratio > best:
            best, at = ratio, i
    if best < 0.5:
        return ""
    return "\n".join(f"{at + k + 1:>5}  {l}" for k, l in enumerate(lines[at : at + size]))


def updated(original: str, chunks: list[Chunk], path: str) -> str:
    lines = original.split("\n")
    trailing = lines and lines[-1] == ""
    if trailing:
        lines.pop()
    replacements = []
    index = 0
    for chunk in chunks:
        if chunk.context:
            found = seek(lines, [chunk.context], index)
            if found is None:
                raise PatchError(f"{path} 里找不到 @@ 定位行：'{chunk.context}'")
            index = found + 1
        if not chunk.old:
            replacements.append((len(lines), 0, chunk.new))
            continue
        old, new = chunk.old, chunk.new
        found = seek(lines, old, index, chunk.eof)
        if found is None and old[-1] == "":
            old, new = old[:-1], new[:-1] if new and new[-1] == "" else new
            found = seek(lines, old, index, chunk.eof)
        if found is None:
            near = nearest(lines, old)
            raise PatchError(f"{path} 里找不到要修改的这几行：\n" + "\n".join(chunk.old)
                             + (f"\n\n文件里最接近的是：\n{near}" if near else "") + "\n\n请先 read_file 确认原文，再按实际内容重写补丁。")
        new = list(new)
        for o, n in chunk.kept:  # context lines keep the file's own text, whitespace included
            if o < len(old) and n < len(new):
                new[n] = lines[found + o]
        replacements.append((found, len(old), new))
        index = found + len(old)
    for start, length, new in sorted(replacements, reverse=True):
        lines[start : start + length] = new
    return "\n".join(lines) + ("\n" if trailing else "")


def apply(text: str, resolve: Callable[[str], Path], write: bool = True) -> list[tuple[str, str]]:
    """Apply a patch (or with write=False only check that it applies); `resolve` maps a patch path to a file and
    refuses paths it must not touch. Returns (action, path) for each change: A added, M modified, D deleted."""
    plan: list[tuple[str, str, Path, str | None]] = []  # action, shown path, file, new contents (None: delete)
    moves: list[Path] = []
    for hunk in parse(text):
        target = resolve(hunk.path)
        if hunk.kind == "add":
            plan.append(("A", hunk.path, target, hunk.contents))
        elif hunk.kind == "delete":
            if not target.is_file():
                raise PatchError(f"要删除的文件不存在：{hunk.path}")
            plan.append(("D", hunk.path, target, None))
        else:
            if not target.is_file():
                raise PatchError(f"要修改的文件不存在：{hunk.path}（新文件请用 {ADD.strip()}）")
            contents = updated(target.read_text(), hunk.chunks, hunk.path)
            if hunk.move_to:
                plan.append(("M", hunk.move_to, resolve(hunk.move_to), contents))
                moves.append(target)
            else:
                plan.append(("M", hunk.path, target, contents))
    if not write:
        return [(action, path) for action, path, _, _ in plan]
    for action, _, target, contents in plan:
        if contents is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)
    for source in moves:
        if source.exists() and all(source != t for _, _, t, _ in plan):
            source.unlink()
    return [(action, path) for action, path, _, _ in plan]
