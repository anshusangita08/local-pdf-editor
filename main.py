import os

import pymupdf as fitz  # PyMuPDF
import pytesseract
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp")
STATIC_DIR = os.path.join(BASE_DIR, "static")
WORKING_PDF_PATH = os.path.join(TEMP_DIR, "working.pdf")
INDEX_HTML_PATH = os.path.join(BASE_DIR, "index.html")

RENDER_DPI = 300
COORDINATE_SCALE = RENDER_DPI / 72

# Minimum number of non-whitespace characters a page's vector text layer must
# contain before we trust it; anything below this triggers the OCR fallback.
MIN_VECTOR_TEXT_CHARS = 5

# OCR words below this Tesseract confidence score (0-100) are discarded.
MIN_OCR_CONFIDENCE = 40

# Blocks whose top (y0) coordinates -- in original PDF-point space -- fall
# within this many points of each other are grouped into the same visual
# "row" when building the grouped /text/{page_number} response.
ROW_GROUPING_THRESHOLD = 10.0


def _resolve_tesseract_cmd():
    """Auto-detect the Tesseract executable on Windows so users don't have to
    add it to PATH manually."""
    candidates = [
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


_tesseract_cmd = _resolve_tesseract_cmd()
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

app = FastAPI(title="Local PDF Editor")

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class EditedBlock(BaseModel):
    page_number: int
    original_bbox: list[float]
    new_text: str
    font_size: float
    is_ocr: bool = False
    original_font: str = "helv"
    original_color: list[float] = [0.0, 0.0, 0.0]


class ExportRequest(BaseModel):
    custom_filename: str = "edited.pdf"
    edits: list[EditedBlock]

os.makedirs(TEMP_DIR, exist_ok=True)


def _group_blocks_into_rows(blocks: list[dict], threshold: float = ROW_GROUPING_THRESHOLD) -> list[dict]:
    """Sort text blocks top-to-bottom, group blocks whose y0 coordinates fall
    within `threshold` points of each other into the same row, then sort
    each row's blocks left-to-right by x0."""
    sorted_blocks = sorted(blocks, key=lambda b: b["original_bbox"][1])

    rows: list[list[dict]] = []
    current_row_y = None
    for block in sorted_blocks:
        y0 = block["original_bbox"][1]
        if current_row_y is None or abs(y0 - current_row_y) > threshold:
            rows.append([])
            current_row_y = y0
        rows[-1].append(block)

    return [
        {
            "row_index": row_index,
            "blocks": sorted(row_blocks, key=lambda b: b["original_bbox"][0]),
        }
        for row_index, row_blocks in enumerate(rows, start=1)
    ]


def _color_int_to_rgb(color: int) -> list[float]:
    """Convert PyMuPDF's packed integer color (0xRRGGBB) into an (r, g, b)
    tuple normalized to the 0.0-1.0 range expected by PyMuPDF's drawing
    functions."""
    r = (color >> 16) & 255
    g = (color >> 8) & 255
    b = color & 255
    return [r / 255.0, g / 255.0, b / 255.0]


def _extract_ocr_blocks(page, page_number: int):
    """Render the page at RENDER_DPI and run Tesseract OCR to recover text
    blocks for scanned/image-only pages, matching the vector-text JSON shape."""
    matrix = fitz.Matrix(COORDINATE_SCALE, COORDINATE_SCALE)
    pixmap = page.get_pixmap(matrix=matrix)
    image = pixmap.pil_image() if hasattr(pixmap, "pil_image") else None
    if image is None:
        from PIL import Image
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    blocks = []
    word_count = len(data.get("text", []))
    for i in range(word_count):
        text = data["text"][i]
        if not text or not text.strip():
            continue
        try:
            confidence = float(data["conf"][i])
        except (TypeError, ValueError):
            confidence = -1
        if confidence < MIN_OCR_CONFIDENCE:
            continue

        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        scaled_bbox = [x, y, x + w, y + h]
        original_bbox = [value / COORDINATE_SCALE for value in scaled_bbox]
        font_size = h  # already in 300-DPI scaled pixels, matches scaled_bbox units

        blocks.append({
            "id": f"page-{page_number}-ocr-{i}",
            "text": text,
            "font_size": font_size,
            "original_bbox": original_bbox,
            "scaled_bbox": scaled_bbox,
            "is_ocr": True,
            "original_font": "helv",
            "original_color": [0.0, 0.0, 0.0],
        })
    return blocks


@app.get("/")
async def read_index():
    if not os.path.exists(INDEX_HTML_PATH):
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(INDEX_HTML_PATH)


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if file.content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    os.makedirs(TEMP_DIR, exist_ok=True)
    with open(WORKING_PDF_PATH, "wb") as f:
        f.write(contents)

    try:
        doc = fitz.open(WORKING_PDF_PATH)
        page_count = doc.page_count
        doc.close()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {exc}")

    return {"filename": file.filename, "pages": page_count}


@app.get("/text/{page_number}")
async def extract_text(page_number: int):
    if not os.path.exists(WORKING_PDF_PATH):
        raise HTTPException(status_code=404, detail="No working PDF has been uploaded yet.")

    try:
        doc = fitz.open(WORKING_PDF_PATH)
        if page_number < 0 or page_number >= doc.page_count:
            raise HTTPException(status_code=404, detail="Page number out of range.")
        blocks = []
        page = doc.load_page(page_number)
        for block_index, block in enumerate(page.get_text("dict")["blocks"]):
            for line_index, line in enumerate(block.get("lines", [])):
                for span_index, span in enumerate(line.get("spans", [])):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    bbox = span["bbox"]
                    blocks.append({
                        "id": f"page-{page_number}-block-{block_index}-line-{line_index}-span-{span_index}",
                        "text": text,
                        "font_size": span["size"] * COORDINATE_SCALE,
                        "original_bbox": list(bbox),
                        "scaled_bbox": [value * COORDINATE_SCALE for value in bbox],
                        "is_ocr": False,
                        "original_font": span.get("font", "helv"),
                        "original_color": _color_int_to_rgb(span.get("color", 0)),
                    })

        # Scanned/image-only pages have no (or a near-empty) vector text layer;
        # fall back to Tesseract OCR so those pages remain editable.
        vector_char_count = sum(len(b["text"].strip()) for b in blocks)
        if vector_char_count < MIN_VECTOR_TEXT_CHARS:
            blocks = _extract_ocr_blocks(page, page_number)

        doc.close()
        return _group_blocks_into_rows(blocks)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to extract text: {exc}")


@app.post("/export")
async def export_pdf(request: ExportRequest):
    if not os.path.exists(WORKING_PDF_PATH):
        raise HTTPException(status_code=404, detail="No working PDF has been uploaded yet.")

    edits = request.edits
    custom_filename = os.path.basename(request.custom_filename.strip()) or "edited.pdf"
    if not custom_filename.lower().endswith(".pdf"):
        custom_filename += ".pdf"

    try:
        doc = fitz.open(WORKING_PDF_PATH)
        for page_number, page in enumerate(doc):
            page_edits = [edit for edit in edits if edit.page_number == page_number]
            vector_edits = [edit for edit in page_edits if not edit.is_ocr]

            # Vector text: use PyMuPDF's redaction machinery, which cleanly
            # strips the original text object before we draw the replacement.
            for edit in vector_edits:
                page.add_redact_annot(fitz.Rect(edit.original_bbox), fill=(1, 1, 1))
            if vector_edits:
                page.apply_redactions()

            # Belt-and-suspenders erasure: apply_redactions() can fail to
            # fully remove text if the font is embedded strangely, leaving
            # faint or doubled text underneath the replacement. Paint a solid
            # white rectangle over every edited block's bbox regardless of
            # whether it was vector or OCR text, so the export is always
            # clean before we write the new text on top.
            for edit in page_edits:
                page.draw_rect(fitz.Rect(edit.original_bbox), color=(1, 1, 1), fill=(1, 1, 1))

            for edit in page_edits:
                unscaled_font_size = edit.font_size / COORDINATE_SCALE
                rect = fitz.Rect(edit.original_bbox)
                color = tuple(edit.original_color) if edit.original_color else (0, 0, 0)

                # Guardrail: always try to preserve the original font first.
                # Highly custom/subsetted embedded fonts often aren't
                # resolvable by name through insert_textbox, so fall back to
                # a built-in font on failure -- but the color is preserved
                # either way.
                try:
                    rc = page.insert_textbox(
                        rect, edit.new_text,
                        fontname=edit.original_font, fontsize=unscaled_font_size, color=color,
                    )
                except Exception:
                    rc = page.insert_textbox(
                        rect, edit.new_text,
                        fontname="helv", fontsize=unscaled_font_size, color=color,
                    )

                # insert_textbox's internal line-height metrics can slightly exceed a
                # tightly-cropped span bbox; pad the rect a bit so text always fits.
                if rc < 0:
                    padded_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y1 + abs(rc) + 2)
                    try:
                        page.insert_textbox(
                            padded_rect, edit.new_text,
                            fontname=edit.original_font, fontsize=unscaled_font_size, color=color,
                        )
                    except Exception:
                        page.insert_textbox(
                            padded_rect, edit.new_text,
                            fontname="helv", fontsize=unscaled_font_size, color=color,
                        )
        output_path = os.path.join(TEMP_DIR, "output.pdf")
        doc.save(output_path)
        doc.close()
        return FileResponse(
            output_path,
            media_type="application/pdf",
            filename=custom_filename,
            headers={"Content-Disposition": f'attachment; filename="{custom_filename}"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to export PDF: {exc}")


@app.get("/render/{page_number}")
async def render_page(page_number: int):
    if not os.path.exists(WORKING_PDF_PATH):
        raise HTTPException(status_code=404, detail="No working PDF has been uploaded yet.")

    try:
        doc = fitz.open(WORKING_PDF_PATH)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open working PDF: {exc}")

    if page_number < 0 or page_number >= doc.page_count:
        doc.close()
        raise HTTPException(status_code=404, detail="Page number out of range.")

    page = doc.load_page(page_number)
    matrix = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    pixmap = page.get_pixmap(matrix=matrix)
    png_bytes = pixmap.tobytes("png")
    doc.close()

    return Response(content=png_bytes, media_type="image/png")
