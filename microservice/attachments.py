import os
import io
import requests
import base64
import pdfplumber
from docx import Document
from dotenv import load_dotenv

load_dotenv()


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

def ocr_image_bytes(image_bytes: bytes) -> str:
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

    response = requests.post(GROQ_API_URL, headers=headers, json=payload)

    try:
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        if "</think>" in content:
            content = content.split("</think>", 1)[1]
        return content.strip()
    except Exception:
        return ""


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
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            image = page.to_image(resolution=150).original

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()

            page_text = ocr_image_bytes(image_bytes)
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