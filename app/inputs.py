"""Architecture Markdown and PlantUML parsing."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import string


HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
SECTION = re.compile(r"^([A-L])\.\s+(.+)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
REQUIREMENT_ID = re.compile(r"(?:NFR|ASR|FR)-\d+\b", re.IGNORECASE)
START_UML = re.compile(r"^\s*@startuml(?:\s+(.*?))?\s*$", re.IGNORECASE)
END_UML = re.compile(r"^\s*@enduml\s*$", re.IGNORECASE)


class InputError(ValueError):
    """An input error that can be displayed in the desktop UI."""


@dataclass(frozen=True)
class CodeBlock:
    language: str
    start: int
    end: int


@dataclass(frozen=True)
class Markdown:
    path: Path
    lines: list[str]
    headings: list[tuple[int, str]]
    blocks: list[CodeBlock]

    def location(self, start: int, end: int) -> dict:
        return {"file": self.path.name, "start_line": start + 1, "end_line": end + 1}

    def error(self, message: str, line: int | None = None) -> InputError:
        where = self.path.name if line is None else f"{self.path.name} 第 {line + 1} 行"
        return InputError(f"{where}：{message}")


def _read_markdown(path: str | Path) -> Markdown:
    path = Path(path)
    if path.suffix.lower() != ".md":
        raise InputError(f"{path.name}：请选择 Markdown（.md）文件。")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(f"{path.name}：编码无效，请另存为 UTF-8 后重试。") from None
    except OSError as error:
        raise InputError(f"{path.name}：无法读取文件，请检查文件是否存在及读取权限。") from error
    if not text.strip():
        raise InputError(f"{path.name}：文件为空。")

    lines = text.splitlines(keepends=True)
    headings = []
    blocks = []
    opening = None
    for index, line in enumerate(lines):
        fence = FENCE.match(line)
        if opening is not None:
            start, marker, language = opening
            if fence and fence[1].startswith(marker) and not fence[2].strip():
                blocks.append(CodeBlock(language, start, index))
                opening = None
        elif fence:
            info = fence[2].strip().split()
            opening = (index, fence[1], info[0].lower() if info else "")
        elif heading := HEADING.match(line):
            headings.append((index, heading[1]))

    source = Markdown(path, lines, headings, blocks)
    if opening is not None:
        raise source.error("代码围栏缺少结束标记。", opening[0])
    return source


def _sections(source: Markdown) -> list[dict]:
    sections = []
    for position, (start, heading) in enumerate(source.headings):
        match = SECTION.fullmatch(heading)
        if not match:
            continue
        end = len(source.lines)
        for next_start, next_heading in source.headings[position + 1:]:
            if SECTION.fullmatch(next_heading) or source.lines[next_start].lstrip().startswith("# "):
                end = next_start
                break
        sections.append({
            "id": match[1],
            "title": match[2],
            "text": "".join(source.lines[start:end]),
            **source.location(start, end - 1),
        })
    if [section["id"] for section in sections] != list(string.ascii_uppercase[:12]):
        raise source.error("请选择架构文档，文件应按顺序包含 A–L 章节且每节只出现一次。")
    return sections


def _requirements(source: Markdown, section: dict) -> list[dict]:
    rows = []
    in_table = False
    for index in range(section["start_line"] - 1, section["end_line"]):
        line = source.lines[index].strip()
        if any(block.start <= index <= block.end for block in source.blocks):
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip("|"))]
        if cells[0].casefold() == "requirement id":
            in_table = True
        elif in_table:
            ids = REQUIREMENT_ID.findall(cells[0])
            if not ids:
                continue
            if len(cells) != 6:
                raise source.error("需求追踪表中的这一行应包含 6 列。", index)
            rows.append({
                "ids": [item.upper() for item in ids],
                "summary": cells[1],
                "diagrams": cells[2],
                "components": cells[3],
                "artifacts": cells[4],
                "rationale": cells[5],
                **source.location(index, index),
            })
    if not rows:
        raise source.error("B 节缺少可识别的需求追踪表。", section["start_line"] - 1)
    return rows


def _contracts(source: Markdown, sections: list[dict]) -> list[dict]:
    contracts = {}
    for block in source.blocks:
        text = "".join(source.lines[block.start + 1:block.end])
        if not text.strip():
            continue
        kind = {"proto": "proto", "protobuf": "proto", "sql": "sql"}.get(block.language)
        if block.language in {"yaml", "yml", "json"}:
            if re.search(r'(?im)^\s*"?openapi"?\s*:', text):
                kind = "openapi"
            elif re.search(r'(?im)^\s*"?apiVersion"?\s*:', text):
                kind = "deployment"
        if kind is None:
            continue
        section_id = next(
            (section["id"] for section in sections
             if section["start_line"] <= block.start + 1 <= section["end_line"]),
            None,
        )
        location = {
            "section": section_id,
            **source.location(block.start + 1, block.end - 1),
        }
        key = kind, "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")
        if key in contracts:
            contracts[key]["references"].append(location)
        else:
            contracts[key] = {
                "kind": kind, "language": block.language, "text": text,
                **location, "references": [],
            }
    return list(contracts.values())


def _diagrams(source: Markdown) -> list[dict]:
    diagrams = []
    for block in source.blocks:
        if block.language not in {"plantuml", "puml"}:
            continue
        view = next((title for line, title in reversed(source.headings) if line < block.start), None)
        if view is None:
            raise source.error("PlantUML 围栏上方缺少视图标题。", block.start)
        start = None
        name = None
        found = False
        for index in range(block.start + 1, block.end):
            line = source.lines[index]
            if match := START_UML.fullmatch(line):
                if start is not None:
                    raise source.error("上一条 @startuml 缺少配对的 @enduml。", start)
                start, name = index, match[1]
            elif END_UML.fullmatch(line):
                if start is None:
                    raise source.error("@enduml 缺少配对的 @startuml。", index)
                diagrams.append({
                    "view": view, "name": name,
                    "text": "".join(source.lines[start:index + 1]),
                    **source.location(start, index),
                })
                start, found = None, True
        if start is not None:
            raise source.error("@startuml 缺少配对的 @enduml。", start)
        if not found:
            raise source.error("PlantUML 围栏内缺少 @startuml 和 @enduml。", block.start)
    if not diagrams:
        raise source.error("请选择架构视图，文件内未找到 PlantUML 围栏。")
    return diagrams


def load_architecture_inputs(documentation: str | Path, view: str | Path) -> dict:
    """Return model context with one-based, inclusive source locations."""
    document = _read_markdown(documentation)
    sections = _sections(document)
    requirements = _requirements(document, sections[1])
    contracts = _contracts(document, sections)
    diagrams = _diagrams(_read_markdown(view))
    notes = []
    for index, line in enumerate(document.lines[:sections[0]["start_line"] - 1]):
        declared = re.search(r"\b(\d+)\s+PlantUML\s+diagrams?\b", line, re.IGNORECASE)
        if declared and int(declared[1]) != len(diagrams):
            notes.append({
                "code": "diagram_count_mismatch",
                "message": f"架构文档声明 {declared[1]} 张 PlantUML 图，视图文件实际解析到 {len(diagrams)} 张，已全部保留。",
                "declared_count": int(declared[1]),
                "parsed_count": len(diagrams),
                **document.location(index, index),
            })
    return {
        "sections": sections, "requirements": requirements, "contracts": contracts,
        "diagrams": diagrams, "input_notes": notes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将两份架构 Markdown 转换为 JSON。")
    parser.add_argument("documentation")
    parser.add_argument("view")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.suffix.lower() != ".json":
        parser.exit(1, f"{args.output.name}：输出文件必须使用 .json 扩展名。\n")
    try:
        context = load_architecture_inputs(args.documentation, args.view)
        args.output.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except InputError as error:
        parser.exit(1, f"{error}\n")
    except OSError:
        parser.exit(1, f"{args.output.name}：无法写入 JSON，请检查输出目录及权限。\n")


if __name__ == "__main__":
    main()
