import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Any, List, Optional, Tuple

SMARTDOC_API_BASE = "https://fit.neu.edu.vn/smartdoc/api/room"
SMARTDOC_IMAGE_BASE = "https://fit.neu.edu.vn/smartdoc/api/images"

# Danh sách 12 chương chuẩn của phòng học DKwsP5 (ThaoP Bai giang Lap trinh Web Python)
DKWSP5_CHAPTER_SKILL_MAPPING: List[Tuple[int, str, Optional[str]]] = [
    (1, "html5-semantic", "cntt1165-chuong-1-2-tong-quan-va-html5"),
    (2, "html5-semantic", None),
    (3, "html5-semantic", "cntt1165-chuong-3-4-css3-flexbox-responsive"),
    (4, "javascript-fundamentals", "cntt1165-chuong-5-javascript-client-side"),
    (5, "python-flask-backend", "cntt1165-chuong-6-dom-events-form-validation"),
    (6, "python-flask-backend", None),
    (7, "python-flask-backend", "cntt1188-chuong-3-4-5-backend-mvc-ecommerce"),
    (8, "python-flask-backend", None),
    (9, "json-fetch-api", "cntt1188-chuong-1-2-client-server-http-api"),
    (10, "python-flask-backend", None),
    (11, "json-fetch-api", None),
    (12, "json-fetch-api", None),
]


def parse_smartdoc_url(url_or_slug: str, default_chap: int = 1) -> Tuple[str, int]:
    """
    Trích xuất room_id (VD: 'DKwsP5') và số chương (chap) từ URL SmartDoc:
    VD: https://fit.neu.edu.vn/smartdoc/room/DKwsP5/?chap=1 -> ('DKwsP5', 1)
    """
    raw = (url_or_slug or "").strip()
    if not raw:
        return "DKwsP5", default_chap

    chap = default_chap
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        if "chap" in qs and qs["chap"]:
            try:
                chap = max(1, int(qs["chap"][0]))
            except ValueError:
                chap = default_chap
        match = re.search(r"/room/([A-Za-z0-9_-]+)", parsed.path)
        room_id = match.group(1) if match else "DKwsP5"
        return room_id, chap

    return re.sub(r"[^A-Za-z0-9_-]", "", raw) or "DKwsP5", chap


def _extract_plain_text(elements: List[Dict[str, Any]]) -> str:
    """Lấy văn bản thuần từ mảng elements của một paragraph."""
    out: List[str] = []
    for el in elements or []:
        tr = el.get("textRun")
        if tr and tr.get("content"):
            out.append(tr["content"])
    return "".join(out).strip()


def _render_paragraph_elements(elements: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """
    Kết xuất cả văn bản định dạng (bold, italic, underline, link) LẪN hình ảnh inlineObjectElement
    từ SmartDoc API (https://fit.neu.edu.vn/smartdoc/api/images/<inlineObjectId>).
    Trả về (markdown_text, list_of_image_html_blocks).
    """
    parts: List[str] = []
    images_html: List[str] = []

    for el in elements or []:
        inline_obj = el.get("inlineObjectElement")
        if inline_obj and inline_obj.get("inlineObjectId"):
            obj_id = str(inline_obj["inlineObjectId"]).strip()
            if obj_id.startswith("http") or obj_id.startswith("data:"):
                img_src = obj_id
            else:
                img_src = f"{SMARTDOC_IMAGE_BASE}/{urllib.parse.quote(obj_id)}"
            rong = inline_obj.get("rong")
            width_style = f'style="width:{int(rong)}%;max-width:100%;height:auto;"' if rong else ""
            images_html.append(
                f'<figure class="smartdoc-figure my-5 text-center">'
                f'<img src="{img_src}" alt="{html.escape(obj_id)}" {width_style} '
                f'class="mx-auto rounded-lg border border-slate-200 shadow-sm max-w-full h-auto" loading="lazy">'
                f'</figure>'
            )

        tr = el.get("textRun")
        if not tr:
            continue
        text = tr.get("content", "")
        if not text:
            continue
        style = tr.get("textStyle") or {}
        link_url = (style.get("link") or {}).get("url")
        stripped = text.strip()
        if not stripped:
            parts.append(text)
            continue

        formatted = stripped
        if style.get("bold") and style.get("italic"):
            formatted = f"***{formatted}***"
        elif style.get("bold"):
            formatted = f"**{formatted}**"
        elif style.get("italic"):
            formatted = f"*{formatted}*"
        if style.get("underline") and not link_url:
            formatted = f"<u>{formatted}</u>"

        if link_url:
            formatted = f"[{formatted}]({link_url})"

        prefix = " " if text[0].isspace() else ""
        suffix = " " if text[-1].isspace() else ""
        parts.append(f"{prefix}{formatted}{suffix}")

    return "".join(parts).strip(), images_html


def _convert_table_to_markdown(table_obj: Dict[str, Any]) -> str:
    rows = table_obj.get("tableRows") or []
    if not rows:
        return ""

    md_rows: List[List[str]] = []
    max_cols = 0
    for r in rows:
        cells = r.get("tableCells") or []
        row_texts: List[str] = []
        for c in cells:
            cell_paras: List[str] = []
            for item in c.get("content") or []:
                if "paragraph" in item:
                    txt, imgs = _render_paragraph_elements(item["paragraph"].get("elements", []))
                    if txt:
                        cell_paras.append(txt)
                    for im in imgs:
                        cell_paras.append(im)
            cell_str = " <br> ".join(cell_paras).replace("|", "\\|").replace("\n", " ").strip()
            row_texts.append(cell_str or " ")
        if row_texts:
            max_cols = max(max_cols, len(row_texts))
            md_rows.append(row_texts)

    if not md_rows or max_cols == 0:
        return ""

    # Nếu bảng chỉ có 1 ô (1x1) thì trong Word/SmartDoc đó thường là khung Code hoặc Khung Ghi chú đóng hộp
    if len(md_rows) == 1 and max_cols == 1:
        box_content = md_rows[0][0].replace(" <br> ", "\n")
        return (
            f'<div class="smartdoc-box my-4 p-4 rounded-lg bg-slate-900 text-slate-100 font-mono text-xs sm:text-sm overflow-x-auto leading-relaxed border border-slate-700 shadow-inner">'
            f'<pre class=" whitespace-pre-wrap m-0">{box_content}</pre>'
            f'</div>'
        )

    lines: List[str] = []
    header = md_rows[0] + [" "] * (max_cols - len(md_rows[0]))
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for r in md_rows[1:]:
        padded = r + [" "] * (max_cols - len(r))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


def _is_special_unnumbered_heading(title_text: str) -> bool:
    return bool(
        re.match(
            r"^\s*(mục lục|danh mục|phần mở đầu|lời nói đầu|lời mở đầu|lời giới thiệu|lời cảm ơn|lời tựa|tài liệu tham khảo|phụ lục)",
            title_text or "",
            re.IGNORECASE,
        )
    )


def extract_smartdoc_steps_and_markdown(
    content_items: List[Dict[str, Any]],
    room_id: str = "DKwsP5",
    chap: int = 1,
    chapter_offset: int = 0,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Chuyển đổi cấu trúc JSON tài liệu SmartDoc (fit.neu.edu.vn) sang HTML/Markdown chuẩn
    Y HỆT bộ dựng của SmartDoc (gồm đánh số tự động 1, 1.1, 1.2, hình ảnh inlineObjectElement,
    bảng biểu, khung code, mục lục TOC, và danh sách steps cho Sidebar bên trái).
    """
    md_blocks: List[str] = []
    steps: List[Dict[str, Any]] = []

    h1_idx = 0
    h2_idx = 0
    h3_idx = 0
    step_counter = 0

    for item in content_items or []:
        if "table" in item:
            tbl_md = _convert_table_to_markdown(item["table"])
            if tbl_md:
                md_blocks.append("\n" + tbl_md + "\n")
            continue

        para = item.get("paragraph")
        if not para:
            continue

        ps = para.get("paragraphStyle") or {}
        p_style = ps.get("namedStyleType") or ""
        class_name = ps.get("className") or ""
        alignment = ps.get("alignment") or ""

        # Bỏ qua ngắt trang (break-after-page chứa chữ 'null')
        if class_name == "break-after-page":
            continue

        text, images_html = _render_paragraph_elements(para.get("elements", []))
        plain_text = _extract_plain_text(para.get("elements", []))

        if not text and not images_html:
            continue

        bullet = para.get("bullet")

        if p_style == "TITLE" or class_name == "Title":
            clean_title = plain_text.strip()
            anchor = f"smartdoc-step-{step_counter}"
            steps.append(
                {
                    "step_index": step_counter,
                    "level": 0,
                    "number": "",
                    "title": clean_title,
                    "anchor": anchor,
                }
            )
            step_counter += 1
            md_blocks.append(
                f'<div id="{anchor}" class="smartdoc-step-marker scroll-mt-24"></div>\n'
                f'<h1 class="text-2xl sm:text-3xl font-extrabold text-[#0061bb] text-center uppercase tracking-wide my-4">{html.escape(clean_title)}</h1>'
            )
        elif p_style == "HEADING_1" or class_name in ("Heading1", "H1"):
            clean_h = re.sub(r"^\d+(\.\d+)*\.?\s*", "", plain_text).strip()
            if _is_special_unnumbered_heading(clean_h):
                num_str = ""
            else:
                h1_idx += 1
                h2_idx = 0
                h3_idx = 0
                base_num = chapter_offset if chapter_offset > 0 else h1_idx
                num_str = f"{base_num}" if h1_idx == 1 and chapter_offset > 0 else f"{h1_idx}"

            anchor = f"smartdoc-step-{step_counter}"
            display_title = f"{num_str}. {clean_h}" if num_str else clean_h
            steps.append(
                {
                    "step_index": step_counter,
                    "level": 1,
                    "number": num_str,
                    "title": clean_h,
                    "anchor": anchor,
                }
            )
            step_counter += 1
            md_blocks.append(
                f'<div id="{anchor}" class="smartdoc-step-marker scroll-mt-24"></div>\n'
                f'<h2 class="text-xl sm:text-2xl font-extrabold text-[#0061bb] border-b-2 border-[#0061bb]/20 pb-2 mt-8 mb-4">{html.escape(display_title)}</h2>'
            )
        elif p_style == "HEADING_2" or class_name == "Heading2":
            clean_h = re.sub(r"^\d+(\.\d+)*\.?\s*", "", plain_text).strip()
            if h1_idx == 0:
                h1_idx = max(1, chapter_offset)
            h2_idx += 1
            h3_idx = 0
            base_num = chapter_offset if chapter_offset > 0 else h1_idx
            num_str = f"{base_num}.{h2_idx}"
            anchor = f"smartdoc-step-{step_counter}"
            steps.append(
                {
                    "step_index": step_counter,
                    "level": 2,
                    "number": num_str,
                    "title": clean_h,
                    "anchor": anchor,
                }
            )
            step_counter += 1
            md_blocks.append(
                f'<div id="{anchor}" class="smartdoc-step-marker scroll-mt-24"></div>\n'
                f'<h3 class="text-lg sm:text-xl font-bold text-slate-900 mt-6 mb-3 flex items-center gap-2">'
                f'<span class="px-2 py-0.5 rounded bg-[#0061bb]/10 text-[#0061bb] text-sm font-extrabold">{num_str}</span>'
                f'<span>{html.escape(clean_h)}</span></h3>'
            )
        elif p_style in ("HEADING_3", "HEADING_4") or class_name in ("Heading3", "Heading4"):
            clean_h = re.sub(r"^\d+(\.\d+)*\.?\s*", "", plain_text).strip()
            if h1_idx == 0:
                h1_idx = max(1, chapter_offset)
            if h2_idx == 0:
                h2_idx = 1
            h3_idx += 1
            base_num = chapter_offset if chapter_offset > 0 else h1_idx
            num_str = f"{base_num}.{h2_idx}.{h3_idx}"
            anchor = f"smartdoc-step-{step_counter}"
            steps.append(
                {
                    "step_index": step_counter,
                    "level": 3,
                    "number": num_str,
                    "title": clean_h,
                    "anchor": anchor,
                }
            )
            step_counter += 1
            md_blocks.append(
                f'<div id="{anchor}" class="smartdoc-step-marker scroll-mt-24"></div>\n'
                f'<h4 class="text-base sm:text-lg font-bold text-slate-800 mt-5 mb-2">'
                f'<span class="text-[#d66f41] font-extrabold mr-1.5">{num_str}</span>{html.escape(clean_h)}</h4>'
            )
        elif class_name in ("TOC1", "TOC2", "TOC3"):
            # Hiển thị dòng Mục lục chuẩn như trang MỤC LỤC của SmartDoc
            indent_cls = "pl-0 font-bold text-slate-900" if class_name == "TOC1" else ("pl-5 text-slate-700" if class_name == "TOC2" else "pl-10 text-slate-600 text-xs")
            parts_tab = [p.strip() for p in plain_text.split("\t") if p.strip()]
            if len(parts_tab) >= 2:
                left_label = " ".join(parts_tab[:-1])
                page_num = parts_tab[-1]
                md_blocks.append(
                    f'<div class="flex items-baseline justify-between py-1 border-b border-dotted border-slate-300 text-sm {indent_cls}">'
                    f'<span>{html.escape(left_label)}</span>'
                    f'<span class="font-mono text-xs text-slate-500 ml-3 shrink-0">Trang {html.escape(page_num)}</span>'
                    f'</div>'
                )
            else:
                md_blocks.append(f'<div class="py-1 text-sm {indent_cls}">{text}</div>')
        elif class_name.lower() in ("code", "console"):
            md_blocks.append(
                f'<pre class="smartdoc-code my-3 p-3.5 rounded-lg bg-slate-900 text-emerald-300 font-mono text-xs sm:text-sm overflow-x-auto border border-slate-700"><code>{html.escape(plain_text)}</code></pre>'
            )
        elif class_name == "Caption" or re.match(r"^\s*(Hình|Bảng)\s+\d+", plain_text):
            md_blocks.append(
                f'<p class="text-center text-xs sm:text-sm italic font-semibold text-slate-600 -mt-2 mb-4">{text}</p>'
            )
        elif bullet:
            nesting = int(bullet.get("nestingLevel") or 0)
            indent = "  " * min(nesting, 4)
            if text:
                md_blocks.append(f"{indent}- {text}")
        else:
            if text:
                if alignment == "center":
                    md_blocks.append(f'<p class="text-center my-2">{text}</p>')
                else:
                    md_blocks.append(text)

        for img_block in images_html:
            md_blocks.append(img_block)

    return "\n\n".join(md_blocks).strip(), steps


def convert_smartdoc_content_to_markdown(
    content_items: List[Dict[str, Any]],
    room_id: str = "DKwsP5",
    chap: int = 1,
) -> str:
    """Chuyển đổi cấu trúc JSON tài liệu SmartDoc (fit.neu.edu.vn) sang Markdown/HTML chuẩn."""
    chapter_offset = max(0, chap - 2) if 3 <= chap <= 9 else 0
    md_body, _ = extract_smartdoc_steps_and_markdown(
        content_items, room_id=room_id, chap=chap, chapter_offset=chapter_offset
    )
    return md_body


def fetch_smartdoc_chapter(url_or_slug: str, chap_override: Optional[int] = None) -> Dict[str, Any]:
    """
    Gọi trực tiếp API của https://fit.neu.edu.vn/smartdoc/api/room/<room_id>?chap=<chap>
    và trả về tiêu đề chương, danh sách toàn bộ các chương, danh sách các mục (steps),
    cùng nội dung Markdown/HTML có đầy đủ hình ảnh gốc.
    """
    room_id, parsed_chap = parse_smartdoc_url(url_or_slug)
    chap = chap_override if chap_override is not None else parsed_chap

    api_url = f"{SMARTDOC_API_BASE}/{room_id}?chap={chap}"
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0 (NEU-WebPath-LMS/1.0)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise RuntimeError(f"Không thể kết nối tới SmartDoc API ({api_url}): {exc}") from exc

    list_chapter: List[str] = payload.get("listChapter") or []
    room_name = payload.get("roomName") or payload.get("name") or f"SmartDoc {room_id}"
    lab_name = payload.get("labName") or payload.get("name") or room_name
    content_items = (payload.get("data") or {}).get("content") or []

    chapter_title = f"Chương {chap}"
    if 1 <= chap <= len(list_chapter):
        chapter_title = list_chapter[chap - 1].strip()

    chapter_offset = max(0, chap - 2) if 3 <= chap <= 9 else 0
    markdown_body, steps = extract_smartdoc_steps_and_markdown(
        content_items, room_id=room_id, chap=chap, chapter_offset=chapter_offset
    )

    return {
        "room_id": room_id,
        "room_name": room_name,
        "lab_name": lab_name,
        "chap": chap,
        "chapter_title": chapter_title,
        "list_chapter": list_chapter,
        "steps": steps,
        "content_markdown": markdown_body,
        "source_url": f"https://fit.neu.edu.vn/smartdoc/room/{room_id}/?chap={chap}",
        "api_url": api_url,
    }


def sync_default_smartdoc_chapters_to_nodes(force_update: bool = True) -> List[str]:
    """
    Đồng bộ TOÀN BỘ 12 chương từ API https://fit.neu.edu.vn/smartdoc/api/room/DKwsP5?chap=1..12
    (gồm đầy đủ hình ảnh inlineObjectElement, bảng biểu, code, mục lục) vào các Node Kỹ năng
    và cập nhật trực tiếp cả các bài giảng gốc để mọi bài giảng trên hệ thống đều giống hệt SmartDoc.
    """
    from app.extensions import db
    from app.models import Skill, Course, Lesson

    synced_titles: List[str] = []

    for chap_no, skill_slug, legacy_lesson_slug in DKWSP5_CHAPTER_SKILL_MAPPING:
        sk = Skill.query.filter_by(slug=skill_slug).first()
        if not sk:
            continue

        l_slug = f"smartdoc-dkwsp5-chap-{chap_no}"
        existing_smartdoc_lesson = Lesson.query.filter_by(slug=l_slug).first()
        if existing_smartdoc_lesson and not force_update:
            continue

        info = fetch_smartdoc_chapter(
            "https://fit.neu.edu.vn/smartdoc/room/DKwsP5/",
            chap_override=chap_no,
        )

        course_slug = f"hoc-lieu-node-{sk.slug}"
        course = Course.query.filter_by(slug=course_slug).first()
        if not course:
            course = Course(
                skill_id=sk.id,
                title=f"Giáo trình SmartDoc FIT-NEU: {sk.name}",
                slug=course_slug,
                description=f"Bài giảng đồng bộ nguyên bản từ https://fit.neu.edu.vn/smartdoc/room/DKwsP5/ cho {sk.name}",
            )
            db.session.add(course)
            db.session.flush()

        l_title = f"[SmartDoc Chương {chap_no}] {info['chapter_title']}"
        if existing_smartdoc_lesson:
            existing_smartdoc_lesson.title = l_title
            existing_smartdoc_lesson.content_markdown = info["content_markdown"]
            existing_smartdoc_lesson.order_index = chap_no
        else:
            db.session.add(
                Lesson(
                    course_id=course.id,
                    title=l_title,
                    slug=l_slug,
                    order_index=chap_no,
                    xp_reward=35,
                    content_markdown=info["content_markdown"],
                )
            )
        synced_titles.append(l_title)

        # Đồng thời cập nhật luôn bài giảng mặc định ở trang Dashboard để bấm vào bất kỳ đâu cũng ra nội dung SmartDoc thật
        if legacy_lesson_slug:
            legacy_lesson = Lesson.query.filter_by(slug=legacy_lesson_slug).first()
            if legacy_lesson:
                legacy_lesson.content_markdown = info["content_markdown"]

    db.session.commit()
    return synced_titles
