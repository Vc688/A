import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from flask import Flask, jsonify, render_template, request, send_file

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
BACKGROUND_DIR = UPLOAD_DIR / "backgrounds"
BACKGROUND_DIR.mkdir(exist_ok=True)
ENV_PATH = BASE_DIR / ".env"
GLOSSARY_PATH = BASE_DIR / "glossary.json"
MEMORY_PATH = BASE_DIR / "clarification_memory.json"
TEMPLATE_IMAGE_PATH = BASE_DIR / "assets" / "west-deal-template.png"
OPENAI_BASE_URL = "https://api.openai.com/v1"
MAX_AUDIO_BYTES = 25 * 1024 * 1024
TARGET_CHUNK_BYTES = int(MAX_AUDIO_BYTES * 0.88)
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
TRANSCRIBE_CHUNK_SECONDS = int(os.getenv("TRANSCRIBE_CHUNK_SECONDS", "1200"))
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".mp4", ".ogg", ".mpeg"}
CHET_MARKER_LOWER = "ḥ"
CHET_MARKER_UPPER = "Ḥ"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not os.environ.get(key):
            os.environ[key] = value


load_env_file(ENV_PATH)

SHUL_NAME = os.getenv("SHUL_NAME", "West Deal Shul Torah Center")
MAX_OUTPUT_WORDS = int(os.getenv("MAX_OUTPUT_WORDS", "400"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gpt-4o-transcribe")
REVIEW_MODEL = os.getenv("REVIEW_MODEL", "gpt-4.1-mini")
PAMPHLET_MODEL = os.getenv("PAMPHLET_MODEL", "gpt-4.1")
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "1800"))
FFMPEG_EXE = os.getenv(
    "FFMPEG_EXE",
    str(BASE_DIR / "ffmpeg" / "bin" / "ffmpeg.exe"),
)

app = Flask(__name__)
JOBS = {}
JOBS_LOCK = threading.Lock()


def load_glossary(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("entries", [])


GLOSSARY_ENTRIES = load_glossary(GLOSSARY_PATH)


def load_memory(path: Path):
    if not path.exists():
        return {"entries": []}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_memory(path: Path, memory) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(memory, handle, indent=2)


CLARIFICATION_MEMORY = load_memory(MEMORY_PATH)
MEMORY_LOCK = threading.Lock()


def safe_filename(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return clean or "upload.mp3"


def ffmpeg_path() -> str | None:
    configured = Path(FFMPEG_EXE)
    if configured.is_file():
        return str(configured)
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    configured_ffmpeg = Path(FFMPEG_EXE)
    sibling = configured_ffmpeg.with_name("ffprobe.exe")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe")


def convert_media_to_mp3(file_path: Path) -> Path:
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError(
            f"A {file_path.suffix.lower() or 'media'} file was uploaded, but ffmpeg is not installed or configured, so the app cannot convert it to .mp3 first."
        )

    output_path = file_path.with_suffix(".mp3")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(file_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not output_path.exists():
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffmpeg could not convert the {file_path.suffix.lower() or 'media'} file into .mp3: {detail}")
    return output_path


def media_duration_seconds(file_path: Path) -> float:
    ffprobe = ffprobe_path()
    if not ffprobe:
        raise RuntimeError(
            "ffprobe is not installed or configured, so the app cannot measure audio duration for automatic large-file chunking."
        )

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffprobe could not inspect the audio file: {detail}")

    try:
        duration = float((result.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe returned an invalid audio duration.") from exc
    if duration <= 0:
        raise RuntimeError("Audio duration could not be determined for chunking.")
    return duration


def clean_spacing(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def trim_to_words(text: str, max_words: int) -> str:
    words = re.findall(r"\S+", text)
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip() + " ..."


def normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def regex_escape_phrase(text: str) -> str:
    parts = [re.escape(part) for part in text.split()]
    return r"\s+".join(parts)


def replace_phrase(text: str, phrase: str, replacement: str) -> str:
    pattern = regex_escape_phrase(phrase)
    return re.sub(pattern, replacement, text, flags=re.IGNORECASE)


def prefer_chet_marker(text: str) -> str:
    if not text:
        return ""

    def repl(match: re.Match) -> str:
        value = match.group(0)
        if value.isupper():
            return CHET_MARKER_UPPER
        if value[0].isupper():
            return CHET_MARKER_UPPER
        return CHET_MARKER_LOWER

    return re.sub(r"ch(?=[aeiou])", repl, text, flags=re.IGNORECASE)


def chet_spelling_variants(text: str) -> list[str]:
    cleaned = clean_spacing(text)
    if not cleaned:
        return []
    preferred = prefer_chet_marker(cleaned)
    ascii_fallback = preferred.replace(CHET_MARKER_UPPER, "Ch").replace(CHET_MARKER_LOWER, "ch")
    variants = []
    for value in (preferred, ascii_fallback, cleaned):
        if value and value not in variants:
            variants.append(value)
    return variants


def matched_glossary_entries(text: str):
    lowered = text.lower()
    collapsed = normalize_for_match(text)
    matches = []
    seen = set()
    for entry in GLOSSARY_ENTRIES:
        if entry["canonical"] in seen:
            continue
        for variant in entry["variants"]:
            if variant.lower() in lowered or normalize_for_match(variant) in collapsed:
                matches.append(entry)
                seen.add(entry["canonical"])
                break
    return matches


def suggest_glossary_entries(text: str):
    suggestions = [prefer_chet_marker(entry["display"]) for entry in matched_glossary_entries(text)[:4]]
    for memory_entry in memory_lookup_entries():
        raw_text = clean_spacing(memory_entry.get("raw_text", ""))
        replacement = prefer_chet_marker(clean_spacing(memory_entry.get("replacement", "")))
        if raw_text and raw_text.lower() in text.lower() and replacement and replacement not in suggestions:
            suggestions.insert(0, replacement)
    return suggestions[:4]


def glossary_context(entries) -> str:
    if not entries:
        return "No glossary matches were identified for this shiur."
    lines = [f'- {prefer_chet_marker(entry["display"])}' for entry in entries[:18]]
    return "\n".join(lines)


def normalize_confirmed_terms(text: str) -> str:
    normalized = text
    for entry in GLOSSARY_ENTRIES:
        replacement = prefer_chet_marker(entry["display"])
        for variant in chet_spelling_variants(entry.get("canonical", "")) + chet_spelling_variants(entry.get("display", "")) + [
            variant_text
            for variant in entry["variants"]
            for variant_text in chet_spelling_variants(variant)
        ]:
            normalized = replace_phrase(normalized, variant, replacement)
    return clean_spacing(normalized)


def memory_lookup_entries():
    with MEMORY_LOCK:
        return list(CLARIFICATION_MEMORY.get("entries", []))


def remember_clarifications(review_items, clarifications) -> None:
    timestamp = datetime.utcnow().isoformat() + "Z"
    with MEMORY_LOCK:
        entries = CLARIFICATION_MEMORY.setdefault("entries", [])
        by_raw = {entry.get("raw_text", "").lower(): entry for entry in entries}
        for item in review_items:
            raw_text = clean_spacing(item.get("raw_text", ""))
            replacement = prefer_chet_marker(clean_spacing(clarifications.get(item["id"], "")))
            if not raw_text or not replacement:
                continue
            key = raw_text.lower()
            if key in by_raw:
                by_raw[key]["replacement"] = replacement
                by_raw[key]["updated_at"] = timestamp
                by_raw[key]["example_context"] = item.get("context", "")
            else:
                entry = {
                    "raw_text": raw_text,
                    "replacement": replacement,
                    "example_context": item.get("context", ""),
                    "updated_at": timestamp,
                }
                entries.append(entry)
                by_raw[key] = entry
        save_memory(MEMORY_PATH, CLARIFICATION_MEMORY)


def apply_memory_clarifications(text: str) -> str:
    updated = text
    for entry in memory_lookup_entries():
        raw_text = clean_spacing(entry.get("raw_text", ""))
        replacement = prefer_chet_marker(clean_spacing(entry.get("replacement", "")))
        if not raw_text or not replacement:
            continue
        updated = replace_phrase(updated, raw_text, replacement)
    return clean_spacing(updated)


def create_job(rabbi_name: str, topic: str) -> str:
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 5,
            "message": "Upload received.",
            "rabbi_name": rabbi_name,
            "topic": topic,
            "raw_transcript": None,
            "review_items": [],
            "review_status": "not_needed",
            "final_transcript": None,
            "one_pager": None,
            "edited_one_pager": None,
            "pdf_line_spacing": 1.0,
            "pdf_font_size": 0.0,
            "pdf_background_mode": "default",
            "pdf_custom_background": None,
            "error": None,
        }
    return job_id


def update_job(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def get_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def safe_download_component(text: str, fallback: str) -> str:
    cleaned = clean_spacing(text)
    cleaned = re.sub(r'[<>:"/\\|?*]+', "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or fallback


def rabbi_last_name(rabbi_name: str) -> str:
    cleaned = clean_spacing(rabbi_name)
    cleaned = re.sub(r"^\s*rabbi\.?\s+", "", cleaned, flags=re.IGNORECASE)
    parts = [part for part in cleaned.split() if part]
    return parts[-1] if parts else "Rabbi"


def pdf_filename(job) -> str:
    topic = safe_download_component(job.get("topic", ""), "Pamphlet")
    rabbi = safe_download_component(rabbi_last_name(job.get("rabbi_name", "")), "Rabbi")
    return f"R.{rabbi}.{topic}.pdf"


def docx_filename(job) -> str:
    topic = safe_download_component(job.get("topic", ""), "Pamphlet")
    rabbi = safe_download_component(rabbi_last_name(job.get("rabbi_name", "")), "Rabbi")
    return f"R.{rabbi}.{topic}.docx"


def background_filename(job) -> str:
    topic = re.sub(r"[^a-zA-Z0-9]+", "_", job.get("topic", "background")).strip("_")
    return f"{topic or 'background'}_background"


def split_pamphlet_body(text: str, *, topic: str, rabbi_name: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines()]
    filtered = []
    topic_lower = topic.strip().lower()
    rabbi_lower = rabbi_name.strip().lower()
    rabbi_clean_lower = re.sub(r"^\s*rabbi\s+", "", rabbi_lower)
    shul_lower = SHUL_NAME.strip().lower()
    topic_compact = re.sub(r"[^a-z0-9]+", "", topic_lower)
    byline_compact = re.sub(r"[^a-z0-9]+", "", f"by {rabbi_lower}")
    rabbi_compact = re.sub(r"[^a-z0-9]+", "", rabbi_lower)
    clean_byline_compact = re.sub(r"[^a-z0-9]+", "", f"by rabbi {rabbi_clean_lower}")
    clean_rabbi_compact = re.sub(r"[^a-z0-9]+", "", rabbi_clean_lower)

    for line in lines:
        if not line:
            filtered.append("")
            continue

        lowered = line.lower()
        lowered = re.sub(r"\brabbi\s+rabb(i)?\b", "rabbi", lowered)
        lowered = re.sub(r"\brabbi\s+rabbi\b", "rabbi", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        normalized_line = re.sub(r"[^a-z0-9]+", "", lowered)
        if line in {"/", "|", "-", ":", ";"}:
            continue
        if lowered == shul_lower:
            continue
        if lowered == topic_lower:
            continue
        if lowered in {f"by {rabbi_lower}", rabbi_lower, f"by rabbi {rabbi_clean_lower}", rabbi_clean_lower}:
            continue
        if normalized_line == topic_compact:
            continue
        if normalized_line in {byline_compact, rabbi_compact, clean_byline_compact, clean_rabbi_compact}:
            continue
        if clean_rabbi_compact and normalized_line.count(clean_rabbi_compact) >= 2:
            continue
        if any(token and token in normalized_line for token in {byline_compact, clean_byline_compact}):
            continue
        if topic_compact and normalized_line.startswith(topic_compact) and len(normalized_line) <= len(topic_compact) + 4:
            continue
        filtered.append(line)

    body_text = "\n".join(filtered).strip()
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", body_text) if paragraph.strip()]
    if not paragraphs and body_text:
        paragraphs = [body_text]
    return paragraphs


def xml_run(text: str, *, bold: bool = False, italic: bool = False, size_half_points: int = 24) -> str:
    formatting = ['<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>']
    formatting.append(f"<w:sz w:val=\"{size_half_points}\"/>")
    formatting.append(f"<w:szCs w:val=\"{size_half_points}\"/>")
    if bold:
        formatting.append("<w:b/>")
    if italic:
        formatting.append("<w:i/>")
    escaped = escape(text)
    return (
        "<w:r>"
        f"<w:rPr>{''.join(formatting)}</w:rPr>"
        f"<w:t xml:space=\"preserve\">{escaped}</w:t>"
        "</w:r>"
    )


def xml_paragraph(text: str, *, align: str = "both", bold: bool = False, italic: bool = False, size_half_points: int = 24, spacing_after: int = 120) -> str:
    return (
        "<w:p>"
        f"<w:pPr><w:jc w:val=\"{align}\"/><w:spacing w:after=\"{spacing_after}\"/></w:pPr>"
        f"{xml_run(text, bold=bold, italic=italic, size_half_points=size_half_points)}"
        "</w:p>"
    )


def pamphlet_text_for_export(job) -> str:
    return clean_spacing(job.get("edited_one_pager") or job.get("one_pager") or "")


def background_mode_for_export(job) -> str:
    mode = (job.get("pdf_background_mode") or "default").strip().lower()
    return mode if mode in {"default", "blank", "custom"} else "default"


def font_size_override_for_export(job) -> float:
    try:
        value = float(job.get("pdf_font_size") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return value if 8.5 <= value <= 14.0 else 0.0


def build_docx_bytes(job) -> BytesIO:
    topic = clean_spacing(job.get("topic", ""))
    rabbi_name = clean_spacing(job.get("rabbi_name", ""))
    paragraphs = split_pamphlet_body(pamphlet_text_for_export(job), topic=topic, rabbi_name=rabbi_name)
    document_parts = [xml_paragraph(SHUL_NAME, align="center", bold=True, size_half_points=32, spacing_after=80)]
    if topic:
        document_parts.append(xml_paragraph(topic, align="center", italic=True, size_half_points=28, spacing_after=60))
    if rabbi_name:
        document_parts.append(xml_paragraph(f"By {rabbi_name}", align="center", size_half_points=24, spacing_after=140))
    for paragraph_text in paragraphs:
        document_parts.append(xml_paragraph(paragraph_text, align="both", size_half_points=24, spacing_after=140))

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
 xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:w10="urn:schemas-microsoft-com:office:word"
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
 xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
 xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
 xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
 mc:Ignorable="w14 wp14">
  <w:body>
    {''.join(document_parts)}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>"""

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        docx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        docx.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>""",
        )
        docx.writestr("word/document.xml", document_xml)
        docx.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(topic or "Torah Pamphlet")}</dc:title>
  <dc:creator>{escape(rabbi_name or SHUL_NAME)}</dc:creator>
  <cp:lastModifiedBy>{escape(SHUL_NAME)}</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().replace(microsecond=0).isoformat()}Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{datetime.utcnow().replace(microsecond=0).isoformat()}Z</dcterms:modified>
</cp:coreProperties>""",
        )
        docx.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Parasha Pamphlet Pipeline</Application>
</Properties>""",
        )
    buffer.seek(0)
    return buffer


def fit_pdf_layout(paragraphs: list[str], line_spacing: float = 1.0, font_size_override: float = 0.0) -> dict:
    layout_attempts = [
        {"header_top_pct": 0.058, "topic_top_pct": 0.102, "byline_top_pct": 0.132, "body_top_pct": 0.170, "body_bottom_pct": 0.914, "paragraph_gap_px": 12, "body_width_pct": 0.836, "body_font_size": 12.0, "shul_font_size": 15, "topic_font_size": 18, "byline_font_size": 11.5, "footer_font_size": 10.5},
        {"header_top_pct": 0.052, "topic_top_pct": 0.094, "byline_top_pct": 0.123, "body_top_pct": 0.161, "body_bottom_pct": 0.922, "paragraph_gap_px": 10, "body_width_pct": 0.846, "body_font_size": 11.5, "shul_font_size": 14.5, "topic_font_size": 17, "byline_font_size": 11, "footer_font_size": 10.0},
        {"header_top_pct": 0.047, "topic_top_pct": 0.088, "byline_top_pct": 0.116, "body_top_pct": 0.153, "body_bottom_pct": 0.929, "paragraph_gap_px": 8, "body_width_pct": 0.854, "body_font_size": 11.0, "shul_font_size": 14, "topic_font_size": 16, "byline_font_size": 10.5, "footer_font_size": 9.5},
        {"header_top_pct": 0.043, "topic_top_pct": 0.082, "byline_top_pct": 0.109, "body_top_pct": 0.146, "body_bottom_pct": 0.935, "paragraph_gap_px": 8, "body_width_pct": 0.862, "body_font_size": 10.5, "shul_font_size": 13.5, "topic_font_size": 15.5, "byline_font_size": 10.0, "footer_font_size": 9.0},
        {"header_top_pct": 0.039, "topic_top_pct": 0.077, "byline_top_pct": 0.103, "body_top_pct": 0.140, "body_bottom_pct": 0.941, "paragraph_gap_px": 6, "body_width_pct": 0.869, "body_font_size": 10.0, "shul_font_size": 13, "topic_font_size": 15, "byline_font_size": 9.5, "footer_font_size": 9.0},
        {"header_top_pct": 0.036, "topic_top_pct": 0.073, "byline_top_pct": 0.098, "body_top_pct": 0.135, "body_bottom_pct": 0.946, "paragraph_gap_px": 6, "body_width_pct": 0.875, "body_font_size": 9.5, "shul_font_size": 12.5, "topic_font_size": 14, "byline_font_size": 9.0, "footer_font_size": 8.5},
        {"header_top_pct": 0.033, "topic_top_pct": 0.069, "byline_top_pct": 0.094, "body_top_pct": 0.130, "body_bottom_pct": 0.950, "paragraph_gap_px": 4, "body_width_pct": 0.881, "body_font_size": 9.0, "shul_font_size": 12, "topic_font_size": 13.5, "byline_font_size": 8.8, "footer_font_size": 8.5},
    ]

    for attempt in layout_attempts:
        body_font_size = font_size_override or attempt["body_font_size"]
        box_width = 816 * attempt["body_width_pct"]
        chars_per_line = max(55, int(box_width / (body_font_size * 0.56)))
        line_height = body_font_size * max(1.0, line_spacing)
        available_height = (attempt["body_bottom_pct"] - attempt["body_top_pct"]) * 1056
        used_height = 0
        wrapped_paragraphs = []

        for paragraph in paragraphs:
            words = paragraph.split()
            lines = []
            current_line = ""
            for word in words:
                trial = f"{current_line} {word}".strip()
                if len(trial) <= chars_per_line:
                    current_line = trial
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            if not lines:
                lines = [""]
            wrapped_paragraphs.append(lines)
            used_height += (len(lines) * line_height)
            if len(wrapped_paragraphs) > 1:
                used_height += attempt["paragraph_gap_px"]

        if used_height <= available_height:
            attempt = dict(attempt)
            attempt["body_font_size"] = body_font_size
            attempt["wrapped_paragraphs"] = wrapped_paragraphs
            return attempt

    raise RuntimeError(
        "This pamphlet is too long to fit the one-page PDF layout. Shorten the pamphlet and try the export again."
    )


def convert_job_to_pdf_with_word(job) -> BytesIO:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        payload_path = temp_path / "payload.json"
        pdf_path = temp_path / "pamphlet.pdf"
        topic = clean_spacing(job.get("topic", ""))
        rabbi_name = clean_spacing(job.get("rabbi_name", ""))
        line_spacing = float(job.get("pdf_line_spacing") or 1.0)
        font_size_override = font_size_override_for_export(job)
        background_mode = background_mode_for_export(job)
        custom_background = job.get("pdf_custom_background")
        body_paragraphs = split_pamphlet_body(pamphlet_text_for_export(job), topic=topic, rabbi_name=rabbi_name)
        if not body_paragraphs:
            raise RuntimeError("No pamphlet text is available to export.")

        if background_mode == "custom" and not custom_background:
            raise RuntimeError("Choose a custom background file before downloading with the custom background option.")

        layout = fit_pdf_layout(body_paragraphs, line_spacing=line_spacing, font_size_override=font_size_override)
        page_width = 612.0
        page_height = 792.0
        body_width = round(layout["body_width_pct"] * page_width, 2)
        body_left = round((page_width - body_width) / 2, 2)
        border_left = 18.0
        border_top = 18.0
        border_width = 576.0
        border_height = 756.0
        payload = {
            "pdf_path": str(pdf_path),
            "shul_name": SHUL_NAME,
            "header_tagline": "Torah from our Rabbis",
            "background_mode": background_mode,
            "custom_background": custom_background or "",
            "topic": topic,
            "rabbi_name": rabbi_name,
            "body_text": "\r".join(body_paragraphs),
            "header_top": round(layout["header_top_pct"] * page_height, 2),
            "topic_top": round(layout["topic_top_pct"] * 792, 2),
            "byline_top": round(layout["byline_top_pct"] * 792, 2),
            "body_top": round(layout["body_top_pct"] * 792, 2),
            "body_height": round((layout["body_bottom_pct"] - layout["body_top_pct"]) * 792, 2),
            "body_left": body_left,
            "body_width": body_width,
            "paragraph_gap": layout["paragraph_gap_px"],
            "shul_font_size": layout["shul_font_size"],
            "topic_font_size": layout["topic_font_size"],
            "byline_font_size": layout["byline_font_size"],
            "body_font_size": layout["body_font_size"],
            "body_line_spacing": line_spacing,
            "footer_font_size": layout["footer_font_size"],
            "border_left": border_left,
            "border_top": border_top,
            "border_width": border_width,
            "border_height": border_height,
        }
        payload_path.write_text(json.dumps(payload), encoding="utf-8")

        powershell_script = f"""
$ErrorActionPreference = 'Stop'
$payload = Get-Content '{str(payload_path).replace("'", "''")}' -Raw | ConvertFrom-Json
$word = $null
$doc = $null
try {{
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $doc = $word.Documents.Add()
  $doc.PageSetup.TopMargin = 0
  $doc.PageSetup.BottomMargin = 0
  $doc.PageSetup.LeftMargin = 0
  $doc.PageSetup.RightMargin = 0
  $doc.PageSetup.PageWidth = 612
  $doc.PageSetup.PageHeight = 792
  if ($payload.background_mode -eq 'custom') {{
    $background = $doc.Shapes.AddPicture($payload.custom_background, $false, $true, 0, 0, 612, 792)
    $background.WrapFormat.Type = 3
    $background.ZOrder(5) | Out-Null
  }}

  if ($payload.background_mode -eq 'default') {{
    $border = $doc.Shapes.AddShape(1, [single]$payload.border_left, [single]$payload.border_top, [single]$payload.border_width, [single]$payload.border_height)
    $border.Fill.Visible = 0
    $border.Line.ForeColor.RGB = 10202317
    $border.Line.Weight = 1.1

    $topRule = $doc.Shapes.AddLine(46, [single]($payload.header_top + 24), 566, [single]($payload.header_top + 24))
    $topRule.Line.ForeColor.RGB = 10202317
    $topRule.Line.Weight = 1.0

    $bottomRule = $doc.Shapes.AddLine(46, 746, 566, 746)
    $bottomRule.Line.ForeColor.RGB = 10202317
    $bottomRule.Line.Weight = 0.9
  }}

  $shulBox = $doc.Shapes.AddTextbox(1, 64, [single]$payload.header_top, 300, 22)
  $shulBox.TextFrame.TextRange.Text = $payload.shul_name
  $shulBox.Line.Visible = 0
  $shulBox.Fill.Visible = 0
  $shulBox.TextFrame.MarginLeft = 0
  $shulBox.TextFrame.MarginRight = 0
  $shulBox.TextFrame.MarginTop = 0
  $shulBox.TextFrame.MarginBottom = 0
  $shulBox.TextFrame.TextRange.Font.Name = 'Times New Roman'
  $shulBox.TextFrame.TextRange.Font.Size = [single]$payload.shul_font_size
  $shulBox.TextFrame.TextRange.Font.SmallCaps = -1
  $shulBox.TextFrame.TextRange.Font.Color = 10202317
  $shulBox.TextFrame.TextRange.ParagraphFormat.Alignment = 0

  $taglineBox = $doc.Shapes.AddTextbox(1, 372, [single]$payload.header_top, 176, 22)
  $taglineBox.TextFrame.TextRange.Text = $payload.header_tagline
  $taglineBox.Line.Visible = 0
  $taglineBox.Fill.Visible = 0
  $taglineBox.TextFrame.MarginLeft = 0
  $taglineBox.TextFrame.MarginRight = 0
  $taglineBox.TextFrame.MarginTop = 0
  $taglineBox.TextFrame.MarginBottom = 0
  $taglineBox.TextFrame.TextRange.Font.Name = 'Times New Roman'
  $taglineBox.TextFrame.TextRange.Font.Size = [single]($payload.shul_font_size - 0.5)
  $taglineBox.TextFrame.TextRange.Font.Italic = -1
  $taglineBox.TextFrame.TextRange.Font.Color = 10202317
  $taglineBox.TextFrame.TextRange.ParagraphFormat.Alignment = 2

  if ($payload.topic) {{
    $topicBox = $doc.Shapes.AddTextbox(1, 64, [single]$payload.topic_top, 484, 26)
    $topicBox.TextFrame.TextRange.Text = $payload.topic
    $topicBox.Line.Visible = 0
    $topicBox.Fill.Visible = 0
    $topicBox.TextFrame.MarginLeft = 0
    $topicBox.TextFrame.MarginRight = 0
    $topicBox.TextFrame.MarginTop = 0
    $topicBox.TextFrame.MarginBottom = 0
    $topicBox.TextFrame.TextRange.Font.Name = 'Times New Roman'
    $topicBox.TextFrame.TextRange.Font.Size = [single]$payload.topic_font_size
    $topicBox.TextFrame.TextRange.Font.Bold = -1
    $topicBox.TextFrame.TextRange.Font.Color = 3355443
    $topicBox.TextFrame.TextRange.ParagraphFormat.Alignment = 1
  }}

  if ($payload.rabbi_name) {{
    $bylineBox = $doc.Shapes.AddTextbox(1, 104, [single]$payload.byline_top, 404, 16)
    $bylineBox.TextFrame.TextRange.Text = 'By ' + $payload.rabbi_name
    $bylineBox.Line.Visible = 0
    $bylineBox.Fill.Visible = 0
    $bylineBox.TextFrame.MarginLeft = 0
    $bylineBox.TextFrame.MarginRight = 0
    $bylineBox.TextFrame.MarginTop = 0
    $bylineBox.TextFrame.MarginBottom = 0
    $bylineBox.TextFrame.TextRange.Font.Name = 'Times New Roman'
    $bylineBox.TextFrame.TextRange.Font.Size = [single]$payload.byline_font_size
    $bylineBox.TextFrame.TextRange.Font.Italic = -1
    $bylineBox.TextFrame.TextRange.Font.Color = 6118749
    $bylineBox.TextFrame.TextRange.ParagraphFormat.Alignment = 1
  }}

  $bodyBox = $doc.Shapes.AddTextbox(1, [single]$payload.body_left, [single]$payload.body_top, [single]$payload.body_width, [single]$payload.body_height)
  $bodyBox.Line.Visible = 0
  $bodyBox.Fill.Visible = 0
  $bodyBox.TextFrame.MarginLeft = 0
  $bodyBox.TextFrame.MarginRight = 0
  $bodyBox.TextFrame.MarginTop = 0
  $bodyBox.TextFrame.MarginBottom = 0
  $bodyBox.TextFrame.TextRange.Text = $payload.body_text
  $bodyBox.TextFrame.TextRange.Font.Name = 'Times New Roman'
  $bodyBox.TextFrame.TextRange.Font.Size = [single]$payload.body_font_size
  $bodyBox.TextFrame.TextRange.Font.Color = 1973790
  $bodyBox.TextFrame.TextRange.ParagraphFormat.Alignment = 3
  $bodyBox.TextFrame.TextRange.ParagraphFormat.SpaceAfter = [single]$payload.paragraph_gap
  $bodyBox.TextFrame.TextRange.ParagraphFormat.SpaceBefore = 0
  if ([double]$payload.body_line_spacing -le 1.01) {{
    $bodyBox.TextFrame.TextRange.ParagraphFormat.LineSpacingRule = 0
  }} else {{
    $bodyBox.TextFrame.TextRange.ParagraphFormat.LineSpacingRule = 1
    $bodyBox.TextFrame.TextRange.ParagraphFormat.LineSpacing = [single]($payload.body_font_size * [double]$payload.body_line_spacing)
  }}
  $bodyBox.TextFrame.AutoSize = 0
  $bodyBox.TextFrame.WordWrap = -1

  $footerBox = $doc.Shapes.AddTextbox(1, 154, 750, 304, 14)
  $footerBox.TextFrame.TextRange.Text = 'West Deal Shul Torah Center'
  $footerBox.Line.Visible = 0
  $footerBox.Fill.Visible = 0
  $footerBox.TextFrame.MarginLeft = 0
  $footerBox.TextFrame.MarginRight = 0
  $footerBox.TextFrame.MarginTop = 0
  $footerBox.TextFrame.MarginBottom = 0
  $footerBox.TextFrame.TextRange.Font.Name = 'Times New Roman'
  $footerBox.TextFrame.TextRange.Font.Size = [single]$payload.footer_font_size
  $footerBox.TextFrame.TextRange.Font.Color = 6118749
  $footerBox.TextFrame.TextRange.ParagraphFormat.Alignment = 1
  if ($payload.background_mode -eq 'blank') {{
    $footerBox.Visible = 0
  }}

  $doc.ExportAsFixedFormat($payload.pdf_path, 17)
}}
finally {{
  if ($doc -ne $null) {{ $doc.Close([ref] $false) }}
  if ($word -ne $null) {{ $word.Quit() }}
}}
"""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", powershell_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"Word PDF conversion failed. {details}")
        if not pdf_path.exists():
            raise RuntimeError("Word did not produce the PDF file.")

        return BytesIO(pdf_path.read_bytes())


def require_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip() or OPENAI_API_KEY
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add your API key to C:\\Users\\vctg6\\Downloads\\parasha-onepager-app\\.env."
        )
    return api_key


def openai_headers() -> dict:
    return {
        "Authorization": f"Bearer {require_api_key()}",
    }


def openai_json_headers() -> dict:
    return {
        **openai_headers(),
        "Content-Type": "application/json",
    }


def openai_request(method: str, url: str, *, retries: int = 4, retry_label: str = "OpenAI request", **kwargs):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code not in RETRYABLE_STATUS_CODES:
                return response
            last_error = RuntimeError(f"{retry_label} error {response.status_code}: {response.text}")
        except requests.exceptions.RequestException as exc:
            last_error = exc

        if attempt == retries:
            break
        time.sleep(min(2 ** attempt, 8))

    if isinstance(last_error, Exception):
        raise last_error
    raise RuntimeError(f"{retry_label} failed after retries.")


def build_transcription_prompt() -> str:
    important_terms = [entry["display"] for entry in GLOSSARY_ENTRIES[:20]]
    return (
        "This audio is a Torah class from West Deal Shul Torah Center. "
        "Transcribe the class faithfully in English while preserving Torah and Hebrew terms as naturally spoken transliteration. "
        "Do not replace unclear words with placeholders like '(speaking in foreign language)'. "
        "If a Hebrew term is uncertain, transcribe the sounds as best you can rather than hiding it.\n\n"
        "Useful term spellings:\n"
        + "\n".join(f"- {term}" for term in important_terms)
    )


def transcribe_mp3_openai(file_path: Path, job_id: str | None = None) -> str:
    if file_path.stat().st_size > MAX_AUDIO_BYTES:
        if job_id:
            update_job(
                job_id,
                status="running",
                progress=18,
                message="Large audio detected. Splitting it into smaller chunks before transcription.",
            )
        return transcribe_audio_with_chunks(file_path, job_id=job_id, split_reason="size")

    with file_path.open("rb") as audio_file:
        response = openai_request(
            "POST",
            f"{OPENAI_BASE_URL}/audio/transcriptions",
            retry_label="OpenAI transcription",
            headers=openai_headers(),
            data={
                "model": TRANSCRIBE_MODEL,
                "response_format": "json",
                "prompt": build_transcription_prompt(),
            },
            files={"file": (file_path.name, audio_file, "audio/mpeg")},
            timeout=OPENAI_TIMEOUT_SECONDS,
        )

    if response.status_code >= 400:
        if response.status_code == 400 and "longer than 1400 seconds" in response.text.lower():
            if job_id:
                update_job(
                    job_id,
                    status="running",
                    progress=18,
                    message="Long audio detected. Splitting it into smaller chunks before transcription.",
                )
            return transcribe_audio_with_chunks(file_path, job_id=job_id, split_reason="duration")
        raise RuntimeError(f"OpenAI transcription error {response.status_code}: {response.text}")

    body = response.json()
    transcript = clean_spacing(body.get("text", ""))
    if not transcript:
        raise RuntimeError(f"Unexpected transcription response: {body}")
    return transcript


def split_audio_with_ffmpeg(file_path: Path, *, segment_seconds: int | None = None, max_chunk_bytes: int | None = None, split_reason: str = "duration"):
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        if split_reason == "size":
            raise RuntimeError(
                "This audio is larger than OpenAI's single-request upload limit, and ffmpeg is not installed to split it automatically. Install ffmpeg and try again."
            )
        raise RuntimeError(
            "This audio is longer than OpenAI's single-request limit, and ffmpeg is not installed to split it automatically. Install ffmpeg and try again."
        )

    chosen_segment_seconds = segment_seconds
    if max_chunk_bytes:
        duration = media_duration_seconds(file_path)
        estimated_seconds = int(duration * (max_chunk_bytes / max(file_path.stat().st_size, 1)) * 0.92)
        chosen_segment_seconds = max(45, min(TRANSCRIBE_CHUNK_SECONDS, estimated_seconds or TRANSCRIBE_CHUNK_SECONDS))
    if not chosen_segment_seconds:
        chosen_segment_seconds = TRANSCRIBE_CHUNK_SECONDS

    chunk_dir = UPLOAD_DIR / f"{file_path.stem}_chunks_{uuid.uuid4().hex[:8]}"
    last_detail = ""
    for _ in range(6):
        shutil.rmtree(chunk_dir, ignore_errors=True)
        chunk_dir.mkdir(exist_ok=True)
        output_pattern = str(chunk_dir / "chunk_%03d.mp3")
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(file_path),
            "-f",
            "segment",
            "-segment_time",
            str(chosen_segment_seconds),
            "-acodec",
            "libmp3lame",
            output_pattern,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            shutil.rmtree(chunk_dir, ignore_errors=True)
            raise RuntimeError(f"ffmpeg could not split the audio: {detail}")

        chunks = sorted(chunk_dir.glob("chunk_*.mp3"))
        if not chunks:
            shutil.rmtree(chunk_dir, ignore_errors=True)
            raise RuntimeError("ffmpeg did not produce any audio chunks.")

        if not max_chunk_bytes:
            return chunk_dir, chunks

        largest_chunk = max(chunks, key=lambda chunk: chunk.stat().st_size)
        largest_size = largest_chunk.stat().st_size
        if largest_size <= max_chunk_bytes:
            return chunk_dir, chunks

        last_detail = f"Largest chunk was {largest_size} bytes."
        reduced_segment = max(20, int(chosen_segment_seconds * (max_chunk_bytes / largest_size) * 0.9))
        if reduced_segment >= chosen_segment_seconds:
            reduced_segment = max(20, chosen_segment_seconds - 15)
        if reduced_segment == chosen_segment_seconds:
            break
        chosen_segment_seconds = reduced_segment

    shutil.rmtree(chunk_dir, ignore_errors=True)
    raise RuntimeError(f"Could not split the audio into chunks small enough for transcription. {last_detail}")


def transcribe_audio_with_chunks(file_path: Path, job_id: str | None = None, split_reason: str = "duration") -> str:
    max_chunk_bytes = TARGET_CHUNK_BYTES if split_reason == "size" else None
    chunk_dir, chunks = split_audio_with_ffmpeg(file_path, max_chunk_bytes=max_chunk_bytes, split_reason=split_reason)
    transcripts = []
    try:
        total_chunks = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            if job_id:
                progress = min(66, 20 + int((index - 1) / max(total_chunks, 1) * 44))
                update_job(
                    job_id,
                    status="running",
                    progress=progress,
                    message=f"Transcribing audio chunk {index} of {total_chunks}.",
                )

            with chunk.open("rb") as audio_file:
                response = openai_request(
                    "POST",
                    f"{OPENAI_BASE_URL}/audio/transcriptions",
                    retry_label="OpenAI transcription",
                    headers=openai_headers(),
                    data={
                        "model": TRANSCRIBE_MODEL,
                        "response_format": "json",
                        "prompt": build_transcription_prompt(),
                    },
                    files={"file": (chunk.name, audio_file, "audio/mpeg")},
                    timeout=OPENAI_TIMEOUT_SECONDS,
                )
            if response.status_code >= 400:
                raise RuntimeError(f"OpenAI chunk transcription error {response.status_code}: {response.text}")
            body = response.json()
            text = clean_spacing(body.get("text", ""))
            if text:
                transcripts.append(text)
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)

    transcript = clean_spacing("\n".join(transcripts))
    if not transcript:
        raise RuntimeError("Chunked transcription returned empty text.")
    return transcript


def build_review_chunks(transcript: str):
    paragraphs = [clean_spacing(part) for part in re.split(r"\n+", transcript) if clean_spacing(part)]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        if not current:
            current = paragraph
            continue
        if len(current) + 1 + len(paragraph) <= 700:
            current = f"{current}\n{paragraph}"
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    if not chunks and transcript:
        chunks = [transcript]
    return [{"index": idx + 1, "text": chunk} for idx, chunk in enumerate(chunks)]


def build_review_prompt(chunked_transcript: str) -> str:
    return (
        "You are reviewing a Torah class transcript for unclear Hebrew or Torah terms before a final pamphlet is written.\n"
        "Identify only the places where the transcript is likely garbled, uncertain, or awkward enough that a human clarification is needed.\n"
        "Be conservative. If a term is usable as-is, do not flag it.\n"
        "Return JSON with the shape {\"items\": [...] }.\n"
        "Each item must include:\n"
        "- chunk_index: integer\n"
        "- raw_text: a short exact snippet from the transcript chunk\n"
        "- context: a short surrounding excerpt\n"
        "- reason: brief explanation\n"
        "- suggestion: best guess in transliteration + English, or empty string if unsure\n"
        "Use ḥ for the Hebrew letter ח, plain h for ה, and keep כ/ך distinct from ḥ.\n"
        "Limit to at most 8 items.\n\n"
        "Relevant glossary forms:\n"
        f"{glossary_context(GLOSSARY_ENTRIES[:18])}\n\n"
        "Chunked transcript:\n"
        f"{chunked_transcript}"
    )


def detect_review_items(transcript: str):
    chunks = build_review_chunks(transcript)
    chunked_text = "\n\n".join(f"[Chunk {chunk['index']}]\n{chunk['text']}" for chunk in chunks)
    response = openai_request(
        "POST",
        f"{OPENAI_BASE_URL}/chat/completions",
        retry_label="OpenAI review",
        headers=openai_json_headers(),
        json={
            "model": REVIEW_MODEL,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return valid JSON only.",
                },
                {
                    "role": "user",
                    "content": build_review_prompt(chunked_text),
                },
            ],
        },
        timeout=OPENAI_TIMEOUT_SECONDS,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI review error {response.status_code}: {response.text}")

    body = response.json()
    content = body["choices"][0]["message"]["content"]
    payload = json.loads(content)
    items = []
    for idx, item in enumerate(payload.get("items", [])[:8], start=1):
        chunk_index = int(item.get("chunk_index") or 0)
        chunk_text = next((chunk["text"] for chunk in chunks if chunk["index"] == chunk_index), "")
        raw_text = clean_spacing(item.get("raw_text", ""))
        context = clean_spacing(item.get("context", "")) or chunk_text
        if not chunk_index or not chunk_text or not raw_text:
            continue
        items.append(
            {
                "id": f"chunk-{chunk_index}-{idx}",
                "segment_index": chunk_index,
                "timestamp": f"Chunk {chunk_index}",
                "raw_text": raw_text,
                "context": context,
                "reason": clean_spacing(item.get("reason", "")),
                "suggestions": [prefer_chet_marker(clean_spacing(item.get("suggestion", "")))] if clean_spacing(item.get("suggestion", "")) else suggest_glossary_entries(context),
            }
        )
    return items


def apply_clarifications(transcript: str, review_items, clarifications) -> str:
    clarified = transcript
    for item in review_items:
        replacement = prefer_chet_marker(clean_spacing(clarifications.get(item["id"], "")))
        if not replacement:
            continue
        clarified = clarified.replace(item["raw_text"], replacement, 1)
    return clean_spacing(clarified)


def tighten_pamphlet_to_length(pamphlet: str, rabbi_name: str, topic: str) -> str:
    response = openai_request(
        "POST",
        f"{OPENAI_BASE_URL}/chat/completions",
        retry_label="OpenAI pamphlet tightening",
        headers=openai_json_headers(),
        json={
            "model": REVIEW_MODEL,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You revise synagogue pamphlets so they fit the requested length and still end cleanly. "
                        "Do not cut the text off. Preserve the Rabbi's voice and preserve the exact three-line header."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Revise this pamphlet to {MAX_OUTPUT_WORDS} words or fewer while keeping it complete.\n"
                        f"The header must remain exactly:\n{SHUL_NAME}\n{topic}\nBy Rabbi {rabbi_name}\n\n"
                        "Make sure the final paragraph lands with a full concluding thought.\n\n"
                        f"{pamphlet}"
                    ),
                },
            ],
        },
        timeout=OPENAI_TIMEOUT_SECONDS,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI pamphlet tightening error {response.status_code}: {response.text}")

    body = response.json()
    tightened = clean_spacing(body["choices"][0]["message"]["content"])
    if not tightened:
        raise RuntimeError(f"Unexpected pamphlet tightening response: {body}")
    return tightened


def build_pamphlet_prompt(rabbi_name: str, topic: str, transcript: str, glossary_entries) -> str:
    return (
        "You are drafting a finished Torah article for a Shabbat pamphlet.\n"
        f"The article must read as if it was written by Rabbi {rabbi_name}, using the language, cadence, and emphases of his own words.\n"
        "Do not write about the Rabbi in the third person. Do not say 'the Rabbi said', 'the shiur discussed', "
        "'this lecture covered', or anything that sounds like a report.\n"
        "Write in the Rabbi's voice as a synthesis of his own language. Preserve his formulations where they are strong, and smooth only enough for readability.\n"
        f"Target about {MAX_OUTPUT_WORDS} words, with a hard limit of {MAX_OUTPUT_WORDS + 30} words.\n"
        "Required output shape:\n"
        f"1. First line exactly: {SHUL_NAME}\n"
        f"2. Second line exactly: {topic}\n"
        f"3. Third line exactly: By Rabbi {rabbi_name}\n"
        "4. Then an opening paragraph, three or four integrated body paragraphs, and a closing takeaway paragraph.\n"
        "Use transliteration + English for Torah and Hebrew terms. Prefer the glossary spellings below when relevant.\n"
        "Render the Hebrew letter ח as ḥ, plain h for ה, and keep כ/ך distinct from ḥ.\n"
        "Do not invent facts or sources beyond what is grounded in the transcript.\n\n"
        "Relevant glossary forms:\n"
        f"{glossary_context(glossary_entries)}\n\n"
        "Final transcript:\n"
        f"{transcript}"
    )


def generate_pamphlet(rabbi_name: str, topic: str, transcript: str, glossary_entries) -> str:
    response = openai_request(
        "POST",
        f"{OPENAI_BASE_URL}/chat/completions",
        retry_label="OpenAI pamphlet",
        headers=openai_json_headers(),
        json={
            "model": PAMPHLET_MODEL,
            "temperature": 0.7,
            "messages": [
                {
                    "role": "system",
                    "content": "You write polished synagogue pamphlets in an authentic rabbinic voice.",
                },
                {
                    "role": "user",
                    "content": build_pamphlet_prompt(rabbi_name, topic, transcript, glossary_entries),
                },
            ],
        },
        timeout=OPENAI_TIMEOUT_SECONDS,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI pamphlet error {response.status_code}: {response.text}")

    body = response.json()
    pamphlet = clean_spacing(body["choices"][0]["message"]["content"])
    if not pamphlet:
        raise RuntimeError(f"Unexpected pamphlet response: {body}")
    pamphlet = re.sub(r"^\s*\d+\.\s+", "", pamphlet, flags=re.MULTILINE)
    if word_count(pamphlet) > MAX_OUTPUT_WORDS + 30:
        pamphlet = tighten_pamphlet_to_length(pamphlet, rabbi_name, topic)
    if word_count(pamphlet) > MAX_OUTPUT_WORDS + 30:
        pamphlet = tighten_pamphlet_to_length(pamphlet, rabbi_name, topic)
    return pamphlet


def start_generation(job_id: str, transcript: str, rabbi_name: str, topic: str, glossary_entries) -> None:
    try:
        update_job(
            job_id,
            status="running",
            progress=78,
            message="Writing the Rabbi-voice pamphlet with OpenAI.",
            final_transcript=transcript,
            review_status="completed",
        )

        summary_holder = {"value": None, "error": None}

        def run_summary():
            try:
                summary_holder["value"] = generate_pamphlet(rabbi_name, topic, transcript, glossary_entries)
            except Exception as exc:
                summary_holder["error"] = exc

        summary_thread = threading.Thread(target=run_summary, daemon=True)
        summary_thread.start()

        started_at = time.time()
        while summary_thread.is_alive():
            elapsed = time.time() - started_at
            progress = min(95, 78 + int(elapsed / 3))
            update_job(
                job_id,
                status="running",
                progress=progress,
                message="Writing the Rabbi-voice pamphlet with OpenAI.",
            )
            summary_thread.join(timeout=1)

        if summary_holder["error"] is not None:
            raise summary_holder["error"]

        update_job(
            job_id,
            status="completed",
            progress=100,
            message="Pamphlet-ready handout is ready.",
            one_pager=summary_holder["value"],
            edited_one_pager=summary_holder["value"],
        )
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            progress=100,
            message="Processing failed.",
            error=f"Processing failed: {exc}",
        )


def finish_transcript_pipeline(job_id: str, transcript: str) -> None:
    job = get_job(job_id)
    if not job:
        return

    raw_transcript = apply_memory_clarifications(transcript)
    update_job(
        job_id,
        progress=72,
        message="Reviewing the transcript for unclear Hebrew terms.",
        raw_transcript=raw_transcript,
    )

    review_items = detect_review_items(raw_transcript)
    if review_items:
        update_job(
            job_id,
            status="needs_review",
            progress=76,
            message="Please review unclear Hebrew terms before the pamphlet is generated.",
            review_items=review_items,
            review_status="required",
        )
        return

    final_transcript = normalize_confirmed_terms(raw_transcript)
    glossary_entries = matched_glossary_entries(final_transcript)
    start_generation(
        job_id,
        final_transcript,
        job["rabbi_name"],
        job["topic"],
        glossary_entries,
    )


def process_job(job_id: str, target_path: Path) -> None:
    job = get_job(job_id)
    if not job:
        return

    try:
        transcribe_path = target_path
        if target_path.suffix.lower() != ".mp3":
            update_job(
                job_id,
                status="running",
                progress=12,
                message="Converting uploaded media to .mp3 before transcription.",
            )
            transcribe_path = convert_media_to_mp3(target_path)

        update_job(
            job_id,
            status="running",
            progress=20,
            message="Transcribing audio with OpenAI. This can take a while for longer shiurim.",
        )

        transcript_holder = {"value": None, "error": None}

        def run_transcription():
            try:
                transcript_holder["value"] = transcribe_mp3_openai(transcribe_path, job_id=job_id)
            except Exception as exc:
                transcript_holder["error"] = exc

        transcription_thread = threading.Thread(target=run_transcription, daemon=True)
        transcription_thread.start()

        started_at = time.time()
        while transcription_thread.is_alive():
            elapsed = time.time() - started_at
            current_job = get_job(job_id) or {}
            progress = min(68, max(int(current_job.get("progress", 20)), 20 + int(elapsed / 8)))
            update_job(
                job_id,
                status="running",
                progress=progress,
                message=current_job.get("message") or "Transcribing audio with OpenAI. Longer shiurim can take several minutes.",
            )
            transcription_thread.join(timeout=2)

        if transcript_holder["error"] is not None:
            raise transcript_holder["error"]

        finish_transcript_pipeline(job_id, transcript_holder["value"])
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            progress=100,
            message="Processing failed.",
            error=f"Processing failed: {exc}",
        )


def process_pasted_transcript(job_id: str, transcript: str) -> None:
    if not get_job(job_id):
        return

    try:
        update_job(
            job_id,
            status="running",
            progress=30,
            message="Using your pasted transcript and moving straight into review.",
        )
        finish_transcript_pipeline(job_id, transcript)
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            progress=100,
            message="Processing failed.",
            error=f"Processing failed: {exc}",
        )


def process_pasted_pamphlet(job_id: str, pamphlet_text: str) -> None:
    if not get_job(job_id):
        return

    try:
        cleaned = clean_spacing(pamphlet_text)
        update_job(
            job_id,
            status="completed",
            progress=100,
            message="Pamphlet text is ready to edit and download.",
            one_pager=cleaned,
            edited_one_pager=cleaned,
            review_status="completed",
        )
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            progress=100,
            message="Processing failed.",
            error=f"Processing failed: {exc}",
        )


@app.route("/", methods=["GET"])
def home():
    return render_template(
        "index.html",
        transcript=None,
        one_pager=None,
        error=None,
        target_words=MAX_OUTPUT_WORDS,
        shul_name=SHUL_NAME,
    )


@app.route("/process", methods=["POST"])
def process_upload():
    audio = request.files.get("audio")
    transcript_text = clean_spacing(request.form.get("transcript_text", ""))
    pamphlet_text = clean_spacing(request.form.get("pamphlet_text", ""))
    rabbi_name = clean_spacing(request.form.get("rabbi_name", ""))
    topic = clean_spacing(request.form.get("topic", ""))

    if not rabbi_name:
        return jsonify({"error": "Please enter the Rabbi's name."}), 400
    if not topic:
        return jsonify({"error": "Please enter the topic for this pamphlet."}), 400
    if not pamphlet_text and not transcript_text and (not audio or not audio.filename):
        return jsonify({"error": "Please choose an .mp3 file, paste a transcript, or paste a finished pamphlet."}), 400

    job_id = create_job(rabbi_name, topic)
    if pamphlet_text:
        worker = threading.Thread(target=process_pasted_pamphlet, args=(job_id, pamphlet_text), daemon=True)
    elif transcript_text:
        worker = threading.Thread(target=process_pasted_transcript, args=(job_id, transcript_text), daemon=True)
    else:
        suffix = Path(audio.filename).suffix.lower()
        if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
            return jsonify({"error": "Only .mp3, .mp4, .ogg, and .mpeg files are supported right now."}), 400
        target_path = UPLOAD_DIR / safe_filename(audio.filename)
        audio.save(target_path)
        worker = threading.Thread(target=process_job, args=(job_id, target_path), daemon=True)
    worker.start()
    return jsonify({"job_id": job_id})


@app.route("/review/<job_id>", methods=["POST"])
def review(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job["status"] != "needs_review":
        return jsonify({"error": "This job is not waiting for review."}), 400

    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers") or {}
    review_items = job.get("review_items") or []
    missing = [item["id"] for item in review_items if not clean_spacing(answers.get(item["id"], ""))]
    if missing:
        return jsonify({"error": "Please clarify each flagged Hebrew segment before continuing."}), 400

    remember_clarifications(review_items, answers)
    final_transcript = apply_clarifications(job.get("raw_transcript") or "", review_items, answers)
    final_transcript = normalize_confirmed_terms(final_transcript)
    glossary_entries = matched_glossary_entries(final_transcript)

    update_job(
        job_id,
        review_items=[
            {
                **item,
                "clarification": clean_spacing(answers.get(item["id"], "")),
            }
            for item in review_items
        ],
        review_status="completed",
        final_transcript=final_transcript,
        status="running",
        progress=78,
        message="Clarifications saved. Writing the Rabbi-voice pamphlet with OpenAI.",
    )

    worker = threading.Thread(
        target=start_generation,
        args=(job_id, final_transcript, job["rabbi_name"], job["topic"], glossary_entries),
        daemon=True,
    )
    worker.start()
    return jsonify({"ok": True})


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(job)


@app.route("/pamphlet/<job_id>", methods=["POST"])
def update_pamphlet(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job.get("status") != "completed" or not job.get("one_pager"):
        return jsonify({"error": "Pamphlet editing is only available after completion."}), 400

    payload = request.get_json(silent=True) or {}
    edited_text = clean_spacing(payload.get("edited_one_pager", ""))
    edited_topic = clean_spacing(payload.get("topic", job.get("topic", "")))
    if not edited_text:
        return jsonify({"error": "Pamphlet text cannot be empty."}), 400
    if not edited_topic:
        return jsonify({"error": "Pamphlet title cannot be empty."}), 400

    try:
        pdf_line_spacing = float(payload.get("pdf_line_spacing", job.get("pdf_line_spacing", 1.0)))
    except (TypeError, ValueError):
        return jsonify({"error": "Line spacing must be a number."}), 400

    try:
        pdf_font_size = float(payload.get("pdf_font_size", job.get("pdf_font_size", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return jsonify({"error": "Font size must be a number."}), 400

    background_mode = (payload.get("pdf_background_mode", job.get("pdf_background_mode", "default")) or "default").strip().lower()

    if pdf_line_spacing < 1.0 or pdf_line_spacing > 1.5:
        return jsonify({"error": "Line spacing must be between 1.0 and 1.5."}), 400
    if pdf_font_size and (pdf_font_size < 8.5 or pdf_font_size > 14.0):
        return jsonify({"error": "Font size must be between 8.5 and 14.0, or Auto."}), 400
    if background_mode not in {"default", "blank", "custom"}:
        return jsonify({"error": "Background mode must be default, blank, or custom."}), 400

    update_job(
        job_id,
        topic=edited_topic,
        edited_one_pager=edited_text,
        pdf_line_spacing=round(pdf_line_spacing, 2),
        pdf_font_size=round(pdf_font_size, 2),
        pdf_background_mode=background_mode,
    )
    return jsonify({"ok": True})


@app.route("/pamphlet/<job_id>/background", methods=["POST"])
def upload_background(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job.get("status") != "completed" or not job.get("one_pager"):
        return jsonify({"error": "Background uploads are only available after the pamphlet is complete."}), 400

    background = request.files.get("background")
    if not background or not background.filename:
        return jsonify({"error": "Choose a background image to upload."}), 400

    extension = Path(background.filename).suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg"}:
        return jsonify({"error": "Backgrounds must be .png, .jpg, or .jpeg files."}), 400

    target_path = BACKGROUND_DIR / f"{job_id}_{safe_filename(background.filename)}"
    background.save(target_path)
    update_job(job_id, pdf_custom_background=str(target_path))
    return jsonify({"ok": True, "path": str(target_path)})


@app.route("/download/<job_id>.pdf", methods=["GET"])
def download_pdf(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job.get("status") != "completed" or not job.get("one_pager"):
        return jsonify({"error": "PDF is only available after the pamphlet is complete."}), 400

    try:
        pdf_buffer = convert_job_to_pdf_with_word(job)
    except Exception as exc:
        return jsonify({"error": f"PDF export failed: {exc}"}), 400
    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_filename(job),
    )


@app.route("/download/<job_id>.docx", methods=["GET"])
def download_docx(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job.get("status") != "completed" or not job.get("one_pager"):
        return jsonify({"error": "DOCX is only available after the pamphlet is complete."}), 400

    docx_buffer = build_docx_bytes(job)
    return send_file(
        docx_buffer,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=docx_filename(job),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
