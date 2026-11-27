"""Package source and bundled sample, excluding local recordings and secrets."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    "package.json", "package-lock.json", "next.config.ts", "tsconfig.json",
    "next-env.d.ts", "README.md", "MODEL-NOTICES.md", "DEPLOY-LIGHTNING.md",
    ".env.hosted.example", ".gitignore",
)
SOURCE_TYPES = {
    "app": {".ts", ".tsx", ".css"},
    "worker": {".py", ".txt", ".json"},
    "scripts": {".py", ".mjs"},
    "tests": {".mjs"},
}


def source_files():
    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            yield path
    for directory in (*SOURCE_TYPES, "public"):
        for path in sorted((ROOT / directory).rglob("*")):
            relative = path.relative_to(ROOT)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            if not path.is_file() or path.is_symlink() or ROOT not in path.resolve().parents:
                continue
            if directory == "public" or path.suffix in SOURCE_TYPES[directory]:
                yield path


def main():
    output = ROOT / "data" / "cutlist-deploy.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".zip.partial")
    count = 0
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for path in source_files():
                archive.write(path, str(Path("CutList") / path.relative_to(ROOT)))
                count += 1
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Created {output} ({count} files, {output.stat().st_size / 1024 ** 2:.1f} MB)")
    print("Source and bundled sample only. Local recordings, models, dependencies and .env.local are excluded.")


if __name__ == "__main__":
    main()
