from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from dev_tools.asset_crop_ui import Box, InteractiveCropper  # noqa: E402
from dev_tools.capture_utils import handle_sensitive_info  # noqa: E402
from module.base.utils import load_image, save_image  # noqa: E402
from module.config.server import VALID_LANG  # noqa: E402


ASSET_RESOLUTION = (1280, 720)
ASSET_KINDS = {
    "base": "",
    "button": "BUTTON",
    "area": "AREA",
    "search": "SEARCH",
    "color": "COLOR",
    "grid": "GRID",
}
ASSET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
MODULE_RE = re.compile(r"^[a-z0-9_]+(?:/[a-z0-9_]+)*$")
TEST_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
NAMESPACES = ["share", *VALID_LANG]


class AssetCropError(ValueError):
    pass


@contextmanager
def _json_output_context():
    """Keep stdout machine-readable, including when a native capture DLL logs."""
    try:
        output_fd = sys.stdout.fileno()
        error_fd = sys.stderr.fileno()
    except (AttributeError, OSError):
        result_stream = sys.stdout
        with redirect_stdout(sys.stderr):
            yield result_stream
        return

    result_stream = os.fdopen(
        os.dup(output_fd),
        "w",
        encoding=getattr(sys.stdout, "encoding", None) or "utf-8",
        newline="",
    )
    sys.stdout.flush()
    os.dup2(error_fd, output_fd)
    try:
        with redirect_stdout(sys.stderr):
            yield result_stream
        result_stream.flush()
    finally:
        if sys.platform == "win32":
            import ctypes

            for runtime in ("ucrtbase", "msvcrt"):
                try:
                    ctypes.CDLL(runtime).fflush(None)
                except (OSError, AttributeError):
                    pass
        os.dup2(result_stream.fileno(), output_fd)
        result_stream.close()


@dataclass(frozen=True)
class CropItem:
    name: str
    kind: str
    frame: int
    namespace: str
    module: str
    box: Box

    @property
    def filename(self) -> str:
        return build_asset_filename(self.name, self.kind, self.frame)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "frame": self.frame,
            "namespace": self.namespace,
            "module": self.module,
            "box": list(self.box),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CropItem":
        if not isinstance(data, dict):
            raise AssetCropError(f"Invalid crop item: {data}")
        try:
            box_values = tuple(int(value) for value in data["box"])
            if len(box_values) != 4:
                raise ValueError("box must contain four integers")
            item = cls(
                name=str(data["name"]),
                kind=str(data.get("kind", "base")),
                frame=int(data.get("frame", 1)),
                namespace=str(data["namespace"]),
                module=str(data["module"]),
                box=(box_values[0], box_values[1], box_values[2], box_values[3]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AssetCropError(f"Invalid crop item: {data}") from exc
        validate_crop_item(item)
        return item


@dataclass(frozen=True)
class CropRecipe:
    source: str
    suite: str
    case: str
    items: tuple[CropItem, ...]
    version: int = 1

    def to_dict(self, source: str | None = None) -> dict:
        return {
            "version": self.version,
            "source": source if source is not None else self.source,
            "suite": self.suite,
            "case": self.case,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CropRecipe":
        if not isinstance(data, dict):
            raise AssetCropError(f"Invalid crop recipe: {data}")
        if data.get("version", 1) != 1:
            raise AssetCropError(f"Unsupported recipe version: {data.get('version')}")
        try:
            recipe = cls(
                source=str(data["source"]),
                suite=str(data["suite"]),
                case=str(data["case"]),
                items=tuple(CropItem.from_dict(item) for item in data.get("items", [])),
            )
        except (KeyError, TypeError) as exc:
            raise AssetCropError(f"Invalid crop recipe: {data}") from exc
        validate_recipe(recipe)
        return recipe


@dataclass
class OutputPlan:
    image: np.ndarray
    recipe: CropRecipe
    source_path: Path | None
    test_image: Path
    recipe_path: Path
    assets: list[tuple[CropItem, Path]]
    conflicts: list[Path]
    modules: list[str]
    persist_recipe: bool

    def result(self, dry_run: bool, generated: list[str] | None = None) -> dict:
        return {
            "version": 1,
            "status": "preview" if dry_run else "written",
            "dry_run": dry_run,
            "source": str(self.source_path) if self.source_path else "device",
            "size": list(ASSET_RESOLUTION),
            "test_image": str(self.test_image),
            "recipe": str(self.recipe_path),
            "assets": [
                {**item.to_dict(), "filename": item.filename, "path": str(path)}
                for item, path in self.assets
            ],
            "modules": self.modules,
            "generated": generated or [],
            "conflicts": [str(path) for path in self.conflicts],
        }


def build_asset_filename(name: str, kind: str = "base", frame: int = 1) -> str:
    if not ASSET_NAME_RE.fullmatch(name):
        raise AssetCropError(f"Invalid asset name: {name}")
    if kind not in ASSET_KINDS:
        raise AssetCropError(f"Invalid asset kind: {kind}")
    if frame < 1:
        raise AssetCropError(f"Invalid asset frame: {frame}")
    if kind in ("grid", "search") and frame != 1:
        raise AssetCropError(f"{kind} assets only support frame 1")

    frame_suffix = f".{frame}" if frame > 1 else ""
    attr = ASSET_KINDS[kind]
    attr_suffix = f".{attr}" if attr else ""
    return f"{name}{frame_suffix}{attr_suffix}.png"


def parse_box(value: str) -> Box:
    try:
        values = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("box must contain four integers") from exc
    if len(values) != 4:
        raise argparse.ArgumentTypeError("box must be x1,y1,x2,y2")
    return values[0], values[1], values[2], values[3]


def validate_box(box: Box) -> None:
    if len(box) != 4:
        raise AssetCropError(f"Invalid box: {box}")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= ASSET_RESOLUTION[0]):
        raise AssetCropError(f"Box X is outside 1280px: {box}")
    if not (0 <= y1 < y2 <= ASSET_RESOLUTION[1]):
        raise AssetCropError(f"Box Y is outside 720px: {box}")


def validate_crop_item(item: CropItem) -> None:
    _ = item.filename
    if item.namespace not in NAMESPACES:
        raise AssetCropError(f"Invalid namespace: {item.namespace}")
    if not MODULE_RE.fullmatch(item.module):
        raise AssetCropError(f"Invalid module: {item.module}")
    validate_box(item.box)


def validate_recipe(recipe: CropRecipe) -> None:
    if not TEST_NAME_RE.fullmatch(recipe.suite):
        raise AssetCropError(f"Invalid test suite: {recipe.suite}")
    if not TEST_NAME_RE.fullmatch(recipe.case):
        raise AssetCropError(f"Invalid test case: {recipe.case}")
    seen = set()
    for item in recipe.items:
        validate_crop_item(item)
        key = (item.namespace, item.module, item.filename)
        if key in seen:
            raise AssetCropError(f"Duplicate output in recipe: {item.filename}")
        seen.add(key)


def validate_image(image: np.ndarray) -> None:
    if image.ndim != 3 or image.shape[2] != 3:
        raise AssetCropError(f"Expected an RGB image, got shape={image.shape}")
    height, width = image.shape[:2]
    if (width, height) != ASSET_RESOLUTION:
        raise AssetCropError(f"Expected 1280x720, got {width}x{height}")


def resolve_original_repo(worktree: Path | None = None) -> Path:
    worktree = (worktree or Path(__file__).resolve().parents[1]).resolve()
    try:
        process = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AssetCropError(f"Unable to resolve original repository: {exc}") from exc
    common_dir = Path(process.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (worktree / common_dir).resolve()
    return common_dir.parent


def make_black_asset(image: np.ndarray, box: Box) -> np.ndarray:
    validate_image(image)
    validate_box(box)
    x1, y1, x2, y2 = box
    output = np.zeros_like(image)
    output[y1:y2, x1:x2] = image[y1:y2, x1:x2]
    return output


def _base_filename(name: str, frame: int) -> str:
    return build_asset_filename(name, "base", frame)


def _validate_dependencies(
    items: tuple[CropItem, ...],
    worktree: Path,
) -> None:
    batch = {(item.namespace, item.module, item.filename) for item in items}
    for item in items:
        if item.kind == "grid":
            continue

        required = []
        if item.kind != "base":
            required.append(_base_filename(item.name, item.frame))
        if item.frame > 1:
            required.append(_base_filename(item.name, 1))

        for filename in required:
            key = (item.namespace, item.module, filename)
            path = worktree / "assets" / item.namespace / item.module / filename
            if key not in batch and not path.exists():
                raise AssetCropError(
                    f"{item.filename} requires base asset {path}"
                )


def create_output_plan(
    image: np.ndarray,
    recipe: CropRecipe,
    source_path: Path | None = None,
    worktree: Path | None = None,
    original_repo: Path | None = None,
    persist_recipe: bool = True,
) -> OutputPlan:
    validate_image(image)
    validate_recipe(recipe)
    worktree = (worktree or Path(__file__).resolve().parents[1]).resolve()
    original_repo = (original_repo or resolve_original_repo(worktree)).resolve()
    _validate_dependencies(recipe.items, worktree)

    test_folder = original_repo / "test" / "screenshots" / recipe.suite
    test_image = test_folder / f"{recipe.case}.png"
    recipe_path = test_folder / f"{recipe.case}.assets.json"
    assets = [
        (
            item,
            worktree / "assets" / item.namespace / item.module / item.filename,
        )
        for item in recipe.items
    ]

    source_resolved = source_path.resolve() if source_path is not None else None
    conflicts = []
    if test_image.exists() and source_resolved != test_image.resolve():
        conflicts.append(test_image)
    if persist_recipe and recipe_path.exists():
        conflicts.append(recipe_path)
    conflicts.extend(path for _, path in assets if path.exists())
    modules = sorted({item.module for item in recipe.items})
    return OutputPlan(
        image=handle_sensitive_info(image, copy=True),
        recipe=recipe,
        source_path=source_path,
        test_image=test_image,
        recipe_path=recipe_path,
        assets=assets,
        conflicts=conflicts,
        modules=modules,
        persist_recipe=persist_recipe,
    )


def write_output_plan(
    plan: OutputPlan,
    replace: bool = False,
    extract: bool = False,
) -> dict:
    if plan.conflicts and not replace:
        joined = "\n".join(str(path) for path in plan.conflicts)
        raise AssetCropError(f"Output already exists; use --replace:\n{joined}")

    source_resolved = plan.source_path.resolve() if plan.source_path else None
    plan.test_image.parent.mkdir(parents=True, exist_ok=True)
    if source_resolved != plan.test_image.resolve():
        save_image(plan.image, plan.test_image)

    if plan.persist_recipe:
        plan.recipe_path.write_text(
            json.dumps(
                plan.recipe.to_dict(source=plan.test_image.name),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    for item, path in plan.assets:
        path.parent.mkdir(parents=True, exist_ok=True)
        save_image(make_black_asset(plan.image, item.box), path)

    generated = []
    if extract and plan.modules:
        from dev_tools.button_extract import generate_code

        try:
            generated = generate_code(modules=plan.modules)
        except ValueError as exc:
            raise AssetCropError(str(exc)) from exc
    return plan.result(dry_run=False, generated=generated)


def load_recipe(path: Path) -> tuple[CropRecipe, Path]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetCropError(f"Unable to load recipe {path}: {exc}") from exc
    recipe = CropRecipe.from_dict(data)
    source = Path(recipe.source)
    if not source.is_absolute():
        source = path.parent / source
    return recipe, source.resolve()


def _required(value: str | None, label: str) -> str:
    if value:
        return value
    if not sys.stdin.isatty():
        raise AssetCropError(f"{label} is required")
    value = input(f"{label}: ").strip()
    if not value:
        raise AssetCropError(f"{label} is required")
    return value


def _prompt(default: str, label: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def _confirm(message: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    value = input(f"{message} {suffix}: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


def _print_result(result: dict, json_output: bool, stream=None) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=stream)
        return
    print(f"status: {result['status']}")
    print(f"test image: {result['test_image']}")
    print(f"recipe: {result['recipe']}")
    for item in result["assets"]:
        print(f"asset: {item['path']} area={tuple(item['box'])}")
    if result["conflicts"]:
        print("conflicts:")
        for path in result["conflicts"]:
            print(f"  {path}")


def _print_canceled(args) -> None:
    if args.json_output:
        print(
            json.dumps({"version": 1, "status": "canceled"}, ensure_ascii=False),
            file=args.result_stream,
        )
    else:
        print("Canceled without writing files")


def _interactive_items(args, boxes: list[Box]) -> tuple[CropItem, ...]:
    namespace = args.namespace or "share"
    module = _required(args.module, "Asset module")
    items = []
    for index, box in enumerate(boxes, start=1):
        print(f"Selection {index}: {box}")
        name = _required(None, "Asset name").upper()
        kind = _prompt("base", "Kind (base/button/area/search/color/grid)").lower()
        frame = int(_prompt("1", "Frame"))
        namespace = _prompt(namespace, "Namespace")
        module = _prompt(module, "Module")
        item = CropItem(name, kind, frame, namespace, module, box)
        validate_crop_item(item)
        items.append(item)
    return tuple(items)


def _finish_interactive(args, image: np.ndarray, boxes: list[Box], source: Path | None) -> int:
    suite = _required(args.suite, "Test suite")
    case = _required(args.case, "Test case")
    recipe = CropRecipe(
        source=source.name if source else f"{case}.png",
        suite=suite,
        case=case,
        items=_interactive_items(args, boxes),
    )
    plan = create_output_plan(image, recipe, source_path=source)
    preview = plan.result(dry_run=True)
    if args.dry_run:
        _print_result(preview, json_output=args.json_output, stream=args.result_stream)
        return 0
    _print_result(preview, json_output=False)

    replace = args.replace
    if plan.conflicts and not replace:
        replace = _confirm("Replace the listed files?", default=False)
        if not replace:
            _print_canceled(args)
            return 130
    extract = args.extract
    if extract is None:
        extract = _confirm("Generate asset wrappers after saving?", default=True)
    if not _confirm("Write this batch?", default=False):
        _print_canceled(args)
        return 130

    result = write_output_plan(plan, replace=replace, extract=extract)
    _print_result(result, json_output=args.json_output, stream=args.result_stream)
    return 0


def command_live(args) -> int:
    from dev_tools.screenshot import create_device, take_screenshot

    device = create_device(args.serial or args.config)
    if args.namespace is None:
        import module.config.server as server

        args.namespace = server.lang
    editor = InteractiveCropper(frame_getter=lambda: take_screenshot(device))
    selected = editor.run()
    if selected is None:
        _print_canceled(args)
        return 130
    image, boxes = selected
    return _finish_interactive(args, image, boxes, source=None)


def command_open(args) -> int:
    source = Path(args.image).resolve()
    image = load_image(source)
    validate_image(image)
    editor = InteractiveCropper(image=handle_sensitive_info(image, copy=True))
    selected = editor.run()
    if selected is None:
        _print_canceled(args)
        return 130
    image, boxes = selected
    return _finish_interactive(args, image, boxes, source=source)


def command_capture(args) -> int:
    from dev_tools.screenshot import create_device, take_screenshot

    suite = _required(args.suite, "Test suite")
    case = _required(args.case, "Test case")
    device = create_device(args.serial or args.config)
    image = take_screenshot(device)
    recipe = CropRecipe(source=f"{case}.png", suite=suite, case=case, items=())
    plan = create_output_plan(image, recipe)
    if args.dry_run:
        result = plan.result(dry_run=True)
    else:
        result = write_output_plan(plan, replace=args.replace, extract=False)
    _print_result(result, json_output=args.json_output, stream=args.result_stream)
    return 0


def command_make(args) -> int:
    persist_recipe = True
    if args.recipe:
        recipe_path = Path(args.recipe).resolve()
        recipe, source = load_recipe(recipe_path)
        persist_recipe = False
    elif args.recipe_json:
        try:
            recipe_data = json.loads(args.recipe_json)
        except json.JSONDecodeError as exc:
            raise AssetCropError(f"Invalid recipe JSON: {exc}") from exc
        recipe = CropRecipe.from_dict(recipe_data)
        source = Path(recipe.source).resolve()
    else:
        source = Path(_required(args.image, "Image")).resolve()
        item = CropItem(
            name=_required(args.name, "Asset name").upper(),
            kind=args.kind,
            frame=args.frame,
            namespace=_required(args.namespace, "Namespace"),
            module=_required(args.module, "Asset module"),
            box=args.box,
        )
        recipe = CropRecipe(
            source=str(source),
            suite=_required(args.suite, "Test suite"),
            case=_required(args.case, "Test case"),
            items=(item,),
        )

    image = load_image(source)
    plan = create_output_plan(
        image,
        recipe,
        source_path=source,
        persist_recipe=persist_recipe,
    )
    if args.dry_run:
        result = plan.result(dry_run=True)
    else:
        result = write_output_plan(
            plan,
            replace=args.replace,
            extract=bool(args.extract),
        )
    _print_result(result, json_output=args.json_output, stream=args.result_stream)
    return 0


def _add_output_arguments(parser: argparse.ArgumentParser, include_asset=True) -> None:
    parser.add_argument("--suite", help="test screenshot folder name")
    parser.add_argument("--case", help="readable test screenshot name")
    parser.add_argument("--replace", action="store_true", help="replace existing outputs")
    parser.add_argument("--dry-run", action="store_true", help="validate without writing")
    parser.add_argument("--json", dest="json_output", action="store_true")
    if include_asset:
        parser.add_argument("--namespace", choices=NAMESPACES)
        parser.add_argument("--module", help="asset module path")
        parser.add_argument(
            "--extract",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="run filtered button extraction after saving",
        )


def _add_device_arguments(parser: argparse.ArgumentParser) -> None:
    device = parser.add_mutually_exclusive_group()
    device.add_argument("--config", default="aes", help="AES config name")
    device.add_argument("--serial", help="emulator serial or port")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture AutoEpicSeven screenshots and create black-canvas assets",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    live = commands.add_parser("live", help="live preview, freeze, and crop")
    _add_device_arguments(live)
    _add_output_arguments(live)
    live.set_defaults(handler=command_live)

    open_ = commands.add_parser("open", help="interactively crop an existing image")
    open_.add_argument("--image", required=True)
    _add_output_arguments(open_)
    open_.set_defaults(handler=command_open)

    capture = commands.add_parser("capture", help="capture one test screenshot")
    _add_device_arguments(capture)
    _add_output_arguments(capture, include_asset=False)
    capture.set_defaults(handler=command_capture)

    make = commands.add_parser("make", help="create assets without a GUI")
    recipe_input = make.add_mutually_exclusive_group()
    recipe_input.add_argument("--recipe")
    recipe_input.add_argument("--recipe-json")
    make.add_argument("--image")
    make.add_argument("--name")
    make.add_argument("--kind", choices=ASSET_KINDS, default="base")
    make.add_argument("--frame", type=int, default=1)
    make.add_argument("--box", type=parse_box)
    _add_output_arguments(make)
    make.set_defaults(handler=command_make)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.result_stream = sys.stdout
    try:
        if args.command == "make" and not args.recipe and not args.recipe_json and args.box is None:
            raise AssetCropError("--box is required without --recipe or --recipe-json")
        if args.json_output:
            with _json_output_context() as result_stream:
                args.result_stream = result_stream
                return args.handler(args)
        return args.handler(args)
    except AssetCropError as exc:
        if getattr(args, "json_output", False):
            print(
                json.dumps(
                    {"version": 1, "status": "error", "error": str(exc)},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
