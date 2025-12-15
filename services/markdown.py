import re


def _to_md_heading(line: str) -> str:
    title = line.strip("=").strip()
    return f"# {title}"


def simple_report_to_markdown(text: str) -> str:
    """Convert the repo's plain-text reports into readable Markdown.

    This keeps `file.py` unchanged while producing a nicer browser render.
    """
    lines = (text or "").splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith("=====") and line.endswith("====="):
            out.append(_to_md_heading(line))
            continue
        if line.endswith(":") and not line.startswith("Source:"):
            title = line[:-1].strip()
            if title:
                out.append(f"## {title}")
                continue
        out.append(line)

    md = "\n".join(out).strip() + "\n"
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md

