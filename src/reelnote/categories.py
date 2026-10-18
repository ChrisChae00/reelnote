def parse(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        name = line.strip()
        if name and not name.startswith("#") and name not in out:
            out.append(name)
    return out


def removed(previous: list[str], current: list[str]) -> list[str]:
    cur = set(current)
    return [c for c in previous if c not in cur]


def apply_rename(text: str, old: str, new: str) -> str:
    names = parse(text)
    if old not in names:
        return text
    lines = []
    for line in text.splitlines():
        if line.strip() == old:
            if new in names:
                continue
            line = new
        lines.append(line)
    return "\n".join(lines) + "\n"
