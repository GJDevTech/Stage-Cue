from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable

import pymupdf
from PIL import Image


@dataclass(frozen=True)
class ImportedPresentationSlide:
    label: str
    image_data: str
    text: str = ""


class PresentationImportError(RuntimeError):
    pass


def import_presentation(path: str | Path) -> tuple[str, list[ImportedPresentationSlide]]:
    """Convert a .ppt/.pptx/.pdf file to lightweight 16:9 WebP slide images."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise PresentationImportError("The presentation file no longer exists.")
    if source.suffix.lower() not in {".ppt", ".pptx", ".pdf"}:
        raise PresentationImportError("Choose a PowerPoint (.ppt/.pptx) or PDF file.")

    with tempfile.TemporaryDirectory(prefix="stagecue-presentation-") as temp_dir:
        if source.suffix.lower() == ".pdf":
            pdf_path = source
        else:
            pdf_path = _convert_powerpoint_to_pdf(source, Path(temp_dir))
        slides = _render_pdf(pdf_path)

    if not slides:
        raise PresentationImportError("The presentation does not contain any slides.")
    return source.stem, slides


def _convert_powerpoint_to_pdf(source: Path, output_dir: Path) -> Path:
    libreoffice = _find_libreoffice()
    if libreoffice:
        command = [
            libreoffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        candidate = output_dir / f"{source.stem}.pdf"
        if completed is not None and completed.returncode == 0 and candidate.is_file():
            return candidate

    if sys.platform == "win32":
        converted = _convert_with_powerpoint_windows(source, output_dir)
        if converted:
            return converted

    raise PresentationImportError(
        "Stage Cue needs LibreOffice to open PowerPoint files on this computer. "
        "Install LibreOffice, or export the presentation to PDF and add the PDF instead."
    )


def _find_libreoffice() -> str | None:
    for executable in ("libreoffice", "soffice"):
        found = shutil.which(executable)
        if found:
            return found

    candidates: Iterable[Path]
    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
        ]
        candidates = [
            Path(root) / "LibreOffice" / "program" / "soffice.exe"
            for root in roots
            if root
        ]
    elif sys.platform == "darwin":
        candidates = [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")]
    else:
        candidates = []
    return next((str(path) for path in candidates if path.is_file()), None)


def _convert_with_powerpoint_windows(source: Path, output_dir: Path) -> Path | None:
    """Use installed Microsoft PowerPoint as a Windows-only fallback."""

    try:
        import win32com.client  # type: ignore
    except ImportError:
        return None

    output = output_dir / f"{source.stem}.pdf"
    app = None
    presentation = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = app.Presentations.Open(str(source), WithWindow=False)
        # ppSaveAsPDF = 32
        presentation.SaveAs(str(output), 32)
        return output if output.is_file() else None
    except Exception:
        return None
    finally:
        try:
            if presentation is not None:
                presentation.Close()
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass


def _render_pdf(pdf_path: Path) -> list[ImportedPresentationSlide]:
    document = pymupdf.open(str(pdf_path))
    slides: list[ImportedPresentationSlide] = []
    try:
        for index, page in enumerate(document, start=1):
            rect = page.rect
            scale = min(2.0, max(1.0, 1280.0 / max(1.0, rect.width)))
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            mode = "RGB" if pixmap.n < 4 else "RGBA"
            image = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples).convert("RGB")
            canvas = Image.new("RGB", (1280, 720), "black")
            fitted = image.copy()
            fitted.thumbnail((1280, 720), Image.Resampling.LANCZOS)
            x = (1280 - fitted.width) // 2
            y = (720 - fitted.height) // 2
            canvas.paste(fitted, (x, y))
            output = BytesIO()
            canvas.save(output, format="WEBP", quality=88, method=6)
            data_url = "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode("ascii")
            slides.append(ImportedPresentationSlide(label=f"Slide {index}", image_data=data_url))
    finally:
        document.close()
    return slides
