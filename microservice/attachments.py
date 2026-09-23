import os
import io
import time
import shutil
import logging
import tempfile
import requests
import base64
import pdfplumber
from docx import Document
from dotenv import load_dotenv

from retry_utils import read_retry

load_dotenv()

logger = logging.getLogger("inboxpilot.attachments")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# OCR limits: (connect, read) timeout per call, 2 attempts per page, at most
# 10 pages, and no new page is started once 40s have passed for one PDF.
OCR_TIMEOUT = (5, 20)
OCR_ATTEMPTS = 2
MAX_OCR_PAGES = 10
OCR_TIME_BUDGET_S = 40

# extract_attachment_text picks the parser by extension.
SUPPORTED_EXTENSIONS = (".pdf", ".docx")


@read_retry(attempts=OCR_ATTEMPTS)
def _post_ocr_request(headers: dict, payload: dict) -> dict:
    response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=OCR_TIMEOUT)
    response.raise_for_status()
    return response.json()


def ocr_image_bytes(image_bytes: bytes) -> str:
    """
    Returns the text Groq's vision model reads from the image, or "" if the
    response has no text. Raises if the request still fails after retries.
    """
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": "qwen/qwen3.6-27b",
        "temperature": 0,
        "max_tokens": 1500,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe all the text visible in this image exactly as it appears, once only. Do not repeat any part of the text. Do not add commentary, explanation, or reasoning — output only the raw transcribed text."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}
                ]
            }
        ]
    }

    result = _post_ocr_request(headers, payload)

    try:
        content = result["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        logger.warning("OCR response had no text content")
        return ""

    if "</think>" in content:
        content = content.split("</think>", 1)[1]
    return content.strip()


def extract_text_from_pdf(file_path: str) -> str:
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_from_pdf_via_ocr(file_path: str) -> str:
    text_parts = []
    started = time.monotonic()

    with pdfplumber.open(file_path) as pdf:
        total = len(pdf.pages)
        if total > MAX_OCR_PAGES:
            logger.warning(f"PDF has {total} pages; OCR only reads the first {MAX_OCR_PAGES}")

        for number, page in enumerate(pdf.pages[:MAX_OCR_PAGES], start=1):
            if time.monotonic() - started > OCR_TIME_BUDGET_S:
                logger.warning(
                    f"OCR time budget of {OCR_TIME_BUDGET_S}s used up; "
                    f"skipping pages {number}-{min(total, MAX_OCR_PAGES)}"
                )
                break

            image = page.to_image(resolution=150).original

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()

            # If a page still fails after its retry, the rest would most
            # likely fail too, so stop and keep the text read so far.
            try:
                page_text = ocr_image_bytes(image_bytes)
            except Exception as e:
                logger.error(f"OCR failed on page {number}; skipping the remaining pages: {e}")
                break

            if page_text:
                text_parts.append(page_text)

    return "\n".join(text_parts)


def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    text_parts = [para.text for para in doc.paragraphs]
    return "\n".join(text_parts)


def extract_attachment_text(file_path: str) -> tuple[str, bool]:
    """
    Returns a tuple: (extracted_text, attachment_unparsed)
    attachment_unparsed is True if we found the file but got no usable text out of it,
    even after trying OCR as a fallback.
    """
    lower_path = file_path.lower()

    if lower_path.endswith(".pdf"):
        text = extract_text_from_pdf(file_path)
        if not text.strip():
            text = extract_text_from_pdf_via_ocr(file_path)
    elif lower_path.endswith(".docx"):
        text = extract_text_from_docx(file_path)
    else:
        return "", True

    text = text.strip()
    attachment_unparsed = len(text) == 0
    return text, attachment_unparsed


def safe_extension(filename: str) -> str:
    """
    The filename's extension, lowercased, if it's one we can parse; else "".
    Nothing else from the request's filename is used.
    """
    extension = os.path.splitext(filename or "")[1].lower()
    return extension if extension in SUPPORTED_EXTENSIONS else ""


def save_and_extract(filename: str, fileobj) -> tuple[str, bool]:
    """
    Writes an attachment to a new, uniquely named temp file, extracts its
    text, and always deletes the file. Only a safe extension is taken from
    the filename, so two emails with the same attachment name can't collide
    and a crafted name can't write outside the temp folder.
    """
    # mkstemp instead of NamedTemporaryFile: on Windows an open
    # NamedTemporaryFile can't be reopened by pdfplumber.
    fd, temp_path = tempfile.mkstemp(prefix="inboxpilot_", suffix=safe_extension(filename))
    try:
        with os.fdopen(fd, "wb") as temp_file:
            shutil.copyfileobj(fileobj, temp_file)
        return extract_attachment_text(temp_path)
    finally:
        try:
            os.remove(temp_path)
        except OSError as e:
            logger.warning(f"Could not delete temp file {temp_path}: {e}")
