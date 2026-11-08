from collections import defaultdict
from datetime import datetime, timezone
import re
import unicodedata
from flask import Blueprint, render_template, jsonify, request, flash, redirect, url_for
from flask_login import current_user
from app.extensions import db
from app.models import (
    Skill,
    SkillPrerequisite,
    UserSkillProgress,
    Course,
    Lesson,
    UserLessonProgress,
    Exercise,
    Submission,
)
from app.services.dag_service import (
    serialize_dag_for_cytoscape,
    would_create_cycle,
    sync_user_skill_tree,
)
from app.services.smartdoc_service import fetch_smartdoc_chapter
from app.blueprints.auth import role_required

skills_bp = Blueprint("skills", __name__, url_prefix="/skills")


def _slugify_node(text: str, prefix: str = "node") -> str:
    text = text.replace("đ", "d").replace("Đ", "d")
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or f"{prefix}-{int(datetime.now(timezone.utc).timestamp())}"


def _collect_skill_lessons(skill: Skill):
    """Lấy toàn bộ danh sách Bài giảng gắn với Node kỹ năng (qua Exercise của Node hoặc Course riêng của Node)."""
    seen_ids = set()
    lesson_list = []

    # 1. Các bài giảng gắn trực tiếp với bài tập của Node này
    for ex in skill.exercises.order_by(Exercise.id.asc()).all():
        if ex.lesson and ex.lesson.id not in seen_ids:
            seen_ids.add(ex.lesson.id)
            lesson_list.append(ex.lesson)

    # 2. Các bài giảng (như SmartDoc hoặc bài giảng Giảng viên thêm) nằm trong Course riêng của Node
    other_skill_lesson_ids = {
        row[0]
        for row in db.session.query(Exercise.lesson_id)
        .filter(Exercise.skill_id != skill.id, Exercise.lesson_id.isnot(None))
        .all()
    }
    for c in skill.courses.all():
        for l in c.lessons:
            if l.id not in seen_ids and l.id not in other_skill_lesson_ids:
                seen_ids.add(l.id)
                lesson_list.append(l)

    return lesson_list


def get_or_create_node_course(skill: Skill) -> Course:
    """Đảm bảo mỗi Node kỹ năng có một Course đại diện để lưu các bài giảng gắn trực tiếp vào Node."""
    slug = f"hoc-lieu-node-{skill.slug}"
    course = Course.query.filter_by(slug=slug).first() or Course.query.filter_by(skill_id=skill.id).first()
    if not course:
        course = Course(
            skill_id=skill.id,
            title=f"Học liệu Node: {skill.name}",
            slug=slug,
            description=skill.description or f"Bài giảng thuộc node {skill.name}",
        )
        db.session.add(course)
        db.session.flush()
    return course


@skills_bp.route("/")
def tree_view():
    """Hiển thị Cây Lộ Trình theo phong cách Roadmap.sh + Đồ thị DAG tương tác."""
    uid = current_user.id if current_user.is_authenticated else None
    if uid:
        sync_user_skill_tree(uid)

    all_skills = Skill.query.order_by(Skill.tier.asc(), Skill.id.asc()).all()
    edges = SkillPrerequisite.query.all()

    prog_map = {}
    completed_lesson_ids = set()
    ac_exercise_ids = set()
    if uid:
        prog_map = {
            p.skill_id: p
            for p in UserSkillProgress.query.filter_by(user_id=uid).all()
        }
        completed_lesson_ids = {
            row[0]
            for row in db.session.query(UserLessonProgress.lesson_id)
            .filter_by(user_id=uid, is_completed=True)
            .all()
        }
        ac_exercise_ids = {
            row[0]
            for row in db.session.query(Submission.exercise_id)
            .filter_by(user_id=uid, status="AC")
            .distinct()
            .all()
        }

    skill_by_id = {s.id: s for s in all_skills}
    prereq_names_map = defaultdict(list)
    for e in edges:
        if e.prerequisite_id in skill_by_id:
            prereq_names_map[e.skill_id].append(skill_by_id[e.prerequisite_id].name)

    tier_labels = {
        1: "CHẶNG 1 • NỀN TẢNG WEB & CẤU TRÚC HTML5 (FUNDAMENTALS)",
        2: "CHẶNG 2 • GIAO DIỆN CSS3 FLEXBOX & LẬP TRÌNH CLIENT-SIDE",
        3: "CHẶNG 3 • TƯƠNG TÁC DOM, SỰ KIỆN & GIAO THỨC RESTFUL API",
        4: "CHẶNG 4 • BACKEND PYTHON FLASK, CSDL SQL & THƯƠNG MẠI ĐIỆN TỬ",
    }

    tiers_dict = defaultdict(list)
    mastered_count = 0
    unlocked_count = 0

    for s in all_skills:
        prog = prog_map.get(s.id)
        status = prog.status if prog else ("unlocked" if s.tier == 1 else "locked")
        if status == "mastered":
            mastered_count += 1
        elif status in ("unlocked", "in_progress"):
            unlocked_count += 1

        node_lessons = _collect_skill_lessons(s)
        lessons_data = [
            {
                "id": l.id,
                "title": l.title,
                "slug": l.slug,
                "xp_reward": l.xp_reward,
                "is_smartdoc": "fit.neu.edu.vn/smartdoc" in (l.content_markdown or ""),
                "is_completed": l.id in completed_lesson_ids,
            }
            for l in node_lessons
        ]

        node_exercises = s.exercises.order_by(Exercise.id.asc()).all()
        exercises_data = [
            {
                "id": ex.id,
                "title": ex.title,
                "slug": ex.slug,
                "difficulty": ex.difficulty,
                "xp_reward": ex.xp_reward,
                "is_ac": ex.id in ac_exercise_ids,
            }
            for ex in node_exercises
        ]

        tiers_dict[s.tier].append(
            {
                "id": s.id,
                "slug": s.slug,
                "name": s.name,
                "description": s.description,
                "tier": s.tier,
                "status": status,
                "completed_exercises": prog.completed_exercises if prog else 0,
                "required_exercises": s.required_exercises_count,
                "earned_xp": prog.earned_xp if prog else 0,
                "required_xp": s.required_xp,
                "prerequisites": prereq_names_map.get(s.id, []),
                "lessons": lessons_data,
                "exercises": exercises_data,
            }
        )

    roadmap_tiers = [
        {
            "tier": t,
            "label": tier_labels.get(t, f"CHẶNG {t} • CHUYÊN ĐỀ MỞ RỘNG (TIER {t})"),
            "nodes": tiers_dict[t],
        }
        for t in sorted(tiers_dict.keys())
    ]

    total_nodes = len(all_skills)
    progress_pct = round((mastered_count / total_nodes) * 100) if total_nodes else 0

    return render_template(
        "skills/tree.html",
        all_skills=all_skills,
        roadmap_tiers=roadmap_tiers,
        total_nodes=total_nodes,
        mastered_count=mastered_count,
        unlocked_count=unlocked_count,
        progress_pct=progress_pct,
    )


@skills_bp.route("/api/dag-graph")
def api_dag_graph():
    uid = current_user.id if current_user.is_authenticated else None
    data = serialize_dag_for_cytoscape(uid)
    return jsonify(data)


@skills_bp.route("/api/node/<int:skill_id>")
def api_skill_detail(skill_id: int):
    skill = db.session.get(Skill, skill_id)
    if not skill:
        return jsonify({"error": "Not found"}), 404

    prog = None
    if current_user.is_authenticated:
        prog = UserSkillProgress.query.filter_by(
            user_id=current_user.id, skill_id=skill.id
        ).first()

    prereq_edges = SkillPrerequisite.query.filter_by(skill_id=skill.id).all()
    prereq_names = [
        db.session.get(Skill, e.prerequisite_id).name
        for e in prereq_edges
        if db.session.get(Skill, e.prerequisite_id)
    ]

    exercises = [
        {
            "id": ex.id,
            "title": ex.title,
            "slug": ex.slug,
            "difficulty": ex.difficulty,
            "scaffold_level": ex.scaffold_level,
            "xp_reward": ex.xp_reward,
            "lesson_title": ex.lesson.title if ex.lesson else None,
            "lesson_slug": ex.lesson.slug if ex.lesson else None,
        }
        for ex in skill.exercises.all()
    ]

    node_lessons = _collect_skill_lessons(skill)
    courses = [
        {
            "title": f"Bài giảng thuộc Node ({len(node_lessons)} bài)",
            "lessons": [
                {
                    "title": l.title,
                    "slug": l.slug,
                    "is_smartdoc": "fit.neu.edu.vn/smartdoc" in (l.content_markdown or ""),
                }
                for l in node_lessons
            ],
        }
    ] if node_lessons else []

    status = prog.status if prog else ("unlocked" if skill.tier == 1 else "locked")

    return jsonify(
        {
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "tier": skill.tier,
            "status": status,
            "completed_exercises": prog.completed_exercises if prog else 0,
            "required_exercises": skill.required_exercises_count,
            "earned_xp": prog.earned_xp if prog else 0,
            "required_xp": skill.required_xp,
            "prerequisites": prereq_names,
            "exercises": exercises,
            "courses": courses,
        }
    )


@skills_bp.route("/api/smartdoc-preview")
@role_required("admin", "instructor")
def api_smartdoc_preview():
    """Gọi API SmartDoc (fit.neu.edu.vn) để lấy danh sách chương và xem trước nội dung bài giảng."""
    url = (request.args.get("url") or "https://fit.neu.edu.vn/smartdoc/room/DKwsP5/?chap=1").strip()
    chap = request.args.get("chap", type=int)
    try:
        info = fetch_smartdoc_chapter(url, chap_override=chap)
        return jsonify({"ok": True, **info})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@skills_bp.route("/admin/import-smartdoc", methods=["POST"])
@role_required("admin", "instructor")
def import_smartdoc_to_node():
    """
    Gọi API https://fit.neu.edu.vn/smartdoc/api/room/<slug>?chap=<chap>
    lấy nội dung bài giảng và thêm trực tiếp vào một Node có sẵn (hoặc tạo Node mới trên cây).
    """
    smartdoc_url = (
        request.form.get("smartdoc_url")
        or "https://fit.neu.edu.vn/smartdoc/room/DKwsP5/?chap=1"
    ).strip()
    chap_num = request.form.get("chap", type=int)
    target_mode = (request.form.get("target_mode") or "existing").strip()
    skill_id = request.form.get("skill_id", type=int)
    custom_title = (request.form.get("custom_title") or "").strip()

    try:
        doc_data = fetch_smartdoc_chapter(smartdoc_url, chap_override=chap_num)
    except Exception as exc:
        flash(f"Lỗi khi gọi API SmartDoc: {exc}", "danger")
        return redirect(request.referrer or url_for("skills.tree_view"))

    # Nếu giảng viên chọn Tạo Node mới trên Cây Roadmap
    if target_mode == "new_node":
        new_node_name = (request.form.get("new_node_name") or "").strip()
        if not new_node_name:
            new_node_name = f"[SmartDoc] {doc_data['chapter_title']}"
        new_node_tier = request.form.get("new_node_tier", type=int, default=4)
        prereq_id = request.form.get("prerequisite_id", type=int)

        node_slug = _slugify_node(new_node_name, prefix="smartdoc-node")
        if Skill.query.filter_by(slug=node_slug).first():
            node_slug = f"{node_slug}-{int(datetime.now(timezone.utc).timestamp()) % 10000}"

        target_skill = Skill(
            slug=node_slug,
            name=new_node_name,
            description=f"Đồng bộ từ FIT-NEU SmartDoc ({doc_data['room_name']} - Chương {doc_data['chap']}).",
            tier=max(1, min(6, new_node_tier)),
            required_xp=50,
            required_exercises_count=1,
        )
        db.session.add(target_skill)
        db.session.flush()

        if prereq_id and db.session.get(Skill, prereq_id):
            db.session.add(
                SkillPrerequisite(skill_id=target_skill.id, prerequisite_id=prereq_id)
            )
    else:
        target_skill = db.session.get(Skill, skill_id) if skill_id else Skill.query.first()
        if not target_skill:
            flash("Không tìm thấy Node kỹ năng đích.", "danger")
            return redirect(request.referrer or url_for("skills.tree_view"))

    course = get_or_create_node_course(target_skill)
    lesson_title = custom_title or f"[SmartDoc Chương {doc_data['chap']}] {doc_data['chapter_title']}"
    lesson_slug = _slugify_node(
        f"smartdoc-{doc_data['room_id']}-chap-{doc_data['chap']}-{doc_data['chapter_title']}",
        prefix="smartdoc",
    )
    existing_lesson = Lesson.query.filter_by(slug=lesson_slug).first()
    if existing_lesson:
        existing_lesson.course_id = course.id
        existing_lesson.title = lesson_title
        existing_lesson.content_markdown = doc_data["content_markdown"]
        db.session.commit()
        flash(
            f"Đã cập nhật bài giảng '{lesson_title}' từ SmartDoc vào Node '{target_skill.name}'!",
            "success",
        )
    else:
        order_idx = len(course.lessons) + 1
        new_lesson = Lesson(
            course_id=course.id,
            title=lesson_title,
            slug=lesson_slug,
            order_index=order_idx,
            xp_reward=35,
            content_markdown=doc_data["content_markdown"],
        )
        db.session.add(new_lesson)
        db.session.commit()
        flash(
            f"Đã gọi API SmartDoc lấy bài giảng '{lesson_title}' và thêm vào Node '{target_skill.name}' thành công!",
            "success",
        )

    return redirect(request.referrer or url_for("skills.tree_view"))


@skills_bp.route("/admin/add-edge", methods=["POST"])
@role_required("admin", "instructor")
def add_prerequisite_edge():
    """API dành cho Mentor/Admin thêm điều kiện tiên quyết trên DAG (có kiểm tra chống chu trình)."""
    prerequisite_id = int(request.form.get("prerequisite_id", 0))
    skill_id = int(request.form.get("skill_id", 0))

    if would_create_cycle(skill_id=skill_id, prerequisite_id=prerequisite_id):
        flash(
            "Lỗi DAG: Không thể thêm điều kiện tiên quyết này vì sẽ tạo ra chu trình (Cycle)!",
            "danger",
        )
        return redirect(url_for("skills.tree_view"))

    exists = SkillPrerequisite.query.filter_by(
        skill_id=skill_id, prerequisite_id=prerequisite_id
    ).first()
    if exists:
        flash("Điều kiện tiên quyết này đã tồn tại.", "warning")
    else:
        edge = SkillPrerequisite(skill_id=skill_id, prerequisite_id=prerequisite_id)
        db.session.add(edge)
        db.session.commit()
        flash("Đã thêm cạnh điều kiện tiên quyết vào Cây Kỹ Năng (DAG) thành công!", "success")

    return redirect(url_for("skills.tree_view"))
