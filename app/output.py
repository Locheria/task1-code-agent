"""Output path validation, staging, and publication."""

from ntpath import isreserved
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


def validate_relative_path(path: str) -> None:
    """Validate a project-relative path that can be created on Windows."""
    parts = path.split("/")
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or part != part.strip()
            or "\\" in part
            or isreserved(part)
        ):
            raise ValueError("请使用安全的项目相对路径，以 / 分隔目录。")


def publish_project(directory: str | Path, files: dict[str, str]) -> Path:
    parent = Path(directory).resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("请选择已存在的输出目录。")
    seen = set()
    for path in files:
        validate_relative_path(path)
        normalized = path.casefold()
        if normalized in seen:
            raise ValueError(f"输出路径重复：{path}")
        seen.add(normalized)

    with TemporaryDirectory(dir=parent, prefix=".spacefractions-") as staging:
        root = Path(staging).resolve()
        for path, content in files.items():
            target = root / path
            if not target.resolve().is_relative_to(root):
                raise ValueError(f"输出路径超出暂存目录：{path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)

        stem = datetime.now().strftime("SpaceFractions-%Y%m%d-%H%M%S")
        destination = parent / stem
        while True:
            try:
                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(destination)
                return root.rename(destination)
            except FileExistsError:
                destination = parent / f"{stem}-{uuid4().hex[:8]}"
