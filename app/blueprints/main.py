from datetime import datetime, timezone
import re
import unicodedata
import markdown
from flask import Blueprint, render_template, redirect, url_for, flash, jsonify, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models import (
    User,
    Course,
    Lesson,
    UserLessonProgress,
    Exercise,
    TestCase,
    Submission,
    Skill,
    UserSkillProgress,
    Roadmap,
    PlacementQuestion,
    PlacementTestResult,
    Badge,
)
from app.services.dag_service import sync_user_skill_tree
from app.services.ai_tutor_service import award_badge_if_not_owned
from app.services.m16_service import classify_score, award_xp
from app.blueprints.auth import (
    role_required,
    learner_required,
    instructor_required,
    get_redirect_target_by_role,
)

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    """Trang chủ: Điều hướng theo vai trò nếu đã đăng nhập, hoặc về trang giới thiệu."""
    if current_user.is_authenticated:
        return redirect(get_redirect_target_by_role(current_user))
    return redirect(url_for("main.landing"))


@main_bp.route("/practice")
@learner_required
def practice():
    """Trang đích của Người học: Phần Ôn tập (/practice) tích hợp từ lms_auth."""
    lessons = Lesson.query.order_by(Lesson.id.desc()).limit(20).all()
    exercises = Exercise.query.order_by(Exercise.id.desc()).limit(20).all()
    return render_template("practice.html", lessons=lessons, exercises=exercises)


@main_bp.route("/instructor/lessons/new", methods=["GET", "POST"])
@instructor_required
def instructor_add_material():
    """Trang đích của Giảng viên: Thêm Bài giảng & Bài tập (/instructor/lessons/new) tích hợp từ lms_auth."""
    courses = Course.query.order_by(Course.id.asc()).all()
    lessons = Lesson.query.order_by(Lesson.id.desc()).all()

    if request.method == "POST":
        form_type = request.form.get("form_type", "lesson")

        if form_type == "lesson":
            course_id = request.form.get("course_id", type=int)
            title = (request.form.get("title") or "").strip()
            content_markdown = (request.form.get("content_markdown") or "").strip()
            video_url = (request.form.get("video_url") or "").strip() or None
            xp_reward = request.form.get("xp_reward", type=int, default=20)

            if not course_id or not title or not content_markdown:
                flash("Vui lòng nhập đầy đủ khóa học, tiêu đề và nội dung bài giảng.", "warning")
            else:
                base_slug = _slugify(title, prefix="lesson")
                slug = f"{base_slug}-{Lesson.query.count() + 1}"
                if Lesson.query.filter_by(slug=slug).first():
                    slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp()) % 10000}"
                order_index = Lesson.query.filter_by(course_id=course_id).count() + 1

                new_lesson = Lesson(
                    course_id=course_id,
                    title=title,
                    slug=slug,
                    order_index=order_index,
                    content_markdown=content_markdown,
                    video_url=video_url,
                    xp_reward=xp_reward,
                )
                db.session.add(new_lesson)
                db.session.commit()
                flash(f"Đã đẩy bài giảng '{title}' lên hệ thống thành công!", "success")
                return redirect(url_for("main.instructor_add_material"))

        elif form_type == "exercise":
            lesson_id = request.form.get("lesson_id", type=int)
            title = (request.form.get("title") or "").strip()
            difficulty = request.form.get("difficulty", "easy")
            problem_statement = (request.form.get("problem_statement") or "").strip()
            sample_input = request.form.get("sample_input") or ""
            expected_output = request.form.get("expected_output") or ""
            xp_reward = request.form.get("xp_reward", type=int, default=30)

            if not title or not problem_statement:
                flash("Vui lòng nhập tiêu đề bài tập và đề bài.", "warning")
            else:
                base_slug = _slugify(title, prefix="exercise")
                slug = f"{base_slug}-{Exercise.query.count() + 1}"
                if Exercise.query.filter_by(slug=slug).first():
                    slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp()) % 10000}"
                lesson_obj = db.session.get(Lesson, lesson_id) if lesson_id else None
                skill_id = lesson_obj.course.skill_id if (lesson_obj and lesson_obj.course) else 1

                new_exercise = Exercise(
                    skill_id=skill_id,
                    lesson_id=lesson_id,
                    title=title,
                    slug=slug,
                    problem_statement=problem_statement,
                    difficulty=difficulty,
                    starter_code={"python": "# Viết code của bạn tại đây\n"},
                    time_limit_ms=1000,
                    memory_limit_kb=65536,
                    xp_reward=xp_reward,
                )
                db.session.add(new_exercise)
                db.session.flush()

                if sample_input or expected_output:
                    tc = TestCase(
                        exercise_id=new_exercise.id,
                        input_data=sample_input,
                        expected_output=expected_output,
                        is_sample=True,
                        score_weight=1,
                        order_index=1,
                    )
                    db.session.add(tc)

                db.session.commit()
                flash(f"Đã đẩy bài tập '{title}' lên hệ thống thành công!", "success")
                return redirect(url_for("main.instructor_add_material"))

    return render_template("instructor_upload.html", courses=courses, lessons=lessons)


@main_bp.route("/welcome")
def landing():
    """Trang giới thiệu dành cho Khách (Guest): Xem cây kỹ năng mẫu, làm thử Test đầu vào."""
    skills = Skill.query.order_by(Skill.tier.asc(), Skill.id.asc()).all()
    roadmaps = Roadmap.query.all()
    sample_exercises = Exercise.query.limit(3).all()
    return render_template(
        "landing.html",
        skills=skills,
        roadmaps=roadmaps,
        sample_exercises=sample_exercises,
    )


@main_bp.route("/slides")
def presentation_slides():
    """Trình chiếu slide thuyết trình dự án bằng Reveal.js."""
    return render_template("slides.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    sync_user_skill_tree(current_user.id)

    skills = Skill.query.order_by(Skill.tier.asc(), Skill.id.asc()).all()
    skill_progress = {
        p.skill_id: p
        for p in UserSkillProgress.query.filter_by(user_id=current_user.id).all()
    }
    exercises = Exercise.query.order_by(Exercise.id.asc()).all()
    ac_exercise_ids = {
        row[0]
        for row in db.session.query(Submission.exercise_id)
        .filter_by(user_id=current_user.id, status="AC")
        .distinct()
        .all()
    }
    courses = Course.query.all()
    completed_lesson_ids = {
        row[0]
        for row in db.session.query(UserLessonProgress.lesson_id)
        .filter_by(user_id=current_user.id, is_completed=True)
        .all()
    }
    roadmaps = Roadmap.query.all()
    recent_submissions = (
        Submission.query.filter_by(user_id=current_user.id)
        .order_by(Submission.created_at.desc())
        .limit(6)
        .all()
    )

    # Tìm bạn học cùng lộ trình (Study Buddies): Các học viên khác đang học cùng hoặc gần tầng kỹ năng
    study_buddies = (
        User.query.filter(User.id != current_user.id, User.role == "learner")
        .order_by(User.total_xp.desc())
        .limit(5)
        .all()
    )

    all_badges = Badge.query.all()
    owned_badge_ids = {ub.badge_id for ub in current_user.badges}

    mastered_skills_count = sum(
        1 for p in skill_progress.values() if p.status == "mastered"
    )

    return render_template(
        "dashboard.html",
        skills=skills,
        skill_progress=skill_progress,
        exercises=exercises,
        ac_exercise_ids=ac_exercise_ids,
        courses=courses,
        completed_lesson_ids=completed_lesson_ids,
        roadmaps=roadmaps,
        recent_submissions=recent_submissions,
        mastered_skills_count=mastered_skills_count,
        study_buddies=study_buddies,
        all_badges=all_badges,
        owned_badge_ids=owned_badge_ids,
    )


@main_bp.route("/lessons/<slug>")
@login_required
def lesson_detail(slug: str):
    lesson = Lesson.query.filter_by(slug=slug).first_or_404()
    html_content = markdown.markdown(
        lesson.content_markdown, extensions=["fenced_code", "tables"]
    )
    progress = UserLessonProgress.query.filter_by(
        user_id=current_user.id, lesson_id=lesson.id
    ).first()
    related_exercises = lesson.exercises.all()
    if not related_exercises and lesson.course and lesson.course.skill_id:
        related_exercises = (
            Exercise.query.filter_by(skill_id=lesson.course.skill_id)
            .order_by(Exercise.id.asc())
            .all()
        )

    # Xác định số chương SmartDoc hiện tại (1..12)
    current_chap = 3
    m_chap = re.search(r"smartdoc-dkwsp5-chap-(\d+)", lesson.slug)
    if m_chap:
        current_chap = int(m_chap.group(1))
    else:
        legacy_chap_map = {
            "cntt1165-chuong-1-2-tong-quan-va-html5": 3,
            "cntt1165-chuong-3-4-css3-flexbox-responsive": 3,
            "cntt1165-chuong-5-javascript-client-side": 4,
            "cntt1165-chuong-6-dom-events-form-validation": 5,
            "cntt1188-chuong-1-2-client-server-http-api": 9,
            "cntt1188-chuong-3-4-5-backend-mvc-ecommerce": 7,
        }
        current_chap = legacy_chap_map.get(lesson.slug, 3)

    if request.args.get("chap", "").isdigit():
        current_chap = max(1, min(12, int(request.args["chap"])))

    # Lấy danh sách toàn bộ các chương SmartDoc trong DB để hiển thị ở Dropdown chuyển chương
    all_sd_lessons = (
        Lesson.query.filter(Lesson.slug.like("smartdoc-dkwsp5-chap-%"))
        .order_by(Lesson.order_index.asc(), Lesson.id.asc())
        .all()
    )
    smartdoc_chapters = []
    for sdl in all_sd_lessons:
        mc = re.search(r"smartdoc-dkwsp5-chap-(\d+)", sdl.slug)
        ch_num = int(mc.group(1)) if mc else sdl.order_index
        clean_name = re.sub(r"^\[SmartDoc Chương \d+\]\s*", "", sdl.title).strip()
        smartdoc_chapters.append(
            {
                "chap": ch_num,
                "title": clean_name,
                "full_title": sdl.title,
                "slug": sdl.slug,
                "is_current": sdl.id == lesson.id or ch_num == current_chap,
            }
        )

    # Trích xuất danh sách Mục lục con (Steps) trong chương cho Sidebar bên trái giống SmartDoc
    chapter_steps = []
    step_pattern = re.compile(
        r'<div id="(smartdoc-step-\d+)"[^>]*></div>\s*<(h[1-4])[^>]*>(.*?)</\2>',
        re.DOTALL,
    )
    for idx, match in enumerate(step_pattern.finditer(html_content)):
        anchor_id = match.group(1)
        tag = match.group(2).lower()
        raw_inner = re.sub(r"<[^>]+>", " ", match.group(3))
        clean_step_title = " ".join(raw_inner.split()).strip()
        lvl = 0 if tag == "h1" else (1 if tag == "h2" else (2 if tag == "h3" else 3))
        chapter_steps.append(
            {
                "step_index": idx,
                "level": lvl,
                "title": clean_step_title,
                "anchor": anchor_id,
            }
        )

    # Nếu bài giảng do giảng viên tự gõ Markdown thông thường (không có smartdoc-step-X), tự tách theo thẻ h1/h2/h3
    if not chapter_steps:
        h_pattern = re.compile(r"<(h[1-3])>(.*?)</\1>", re.DOTALL)
        new_html_parts = []
        last_end = 0
        for idx, match in enumerate(h_pattern.finditer(html_content)):
            anchor_id = f"smartdoc-step-{idx}"
            tag = match.group(1).lower()
            raw_inner = re.sub(r"<[^>]+>", " ", match.group(2))
            clean_step_title = " ".join(raw_inner.split()).strip()
            lvl = 1 if tag == "h1" else (2 if tag == "h2" else 3)
            chapter_steps.append(
                {
                    "step_index": idx,
                    "level": lvl,
                    "title": clean_step_title,
                    "anchor": anchor_id,
                }
            )
            new_html_parts.append(html_content[last_end : match.start()])
            new_html_parts.append(
                f'<div id="{anchor_id}" class="smartdoc-step-marker scroll-mt-24"></div>{match.group(0)}'
            )
            last_end = match.end()
        if new_html_parts:
            new_html_parts.append(html_content[last_end:])
            html_content = "".join(new_html_parts)

    smartdoc_live_url = f"https://fit.neu.edu.vn/smartdoc/room/DKwsP5/?chap={current_chap}"

    return render_template(
        "lessons/detail.html",
        lesson=lesson,
        html_content=html_content,
        progress=progress,
        related_exercises=related_exercises,
        current_chap=current_chap,
        smartdoc_chapters=smartdoc_chapters,
        chapter_steps=chapter_steps,
        smartdoc_live_url=smartdoc_live_url,
    )


@main_bp.route("/lessons/<int:lesson_id>/complete", methods=["POST"])
@login_required
def complete_lesson(lesson_id: int):
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson:
        return jsonify({"error": "Not found"}), 404

    progress = UserLessonProgress.query.filter_by(
        user_id=current_user.id, lesson_id=lesson.id
    ).first()
    if not progress:
        progress = UserLessonProgress(
            user_id=current_user.id,
            lesson_id=lesson.id,
            is_completed=True,
            time_spent_seconds=int(request.form.get("time_spent", 60)),
            completed_at=datetime.now(timezone.utc),
        )
        db.session.add(progress)
        db.session.commit()
        award_xp(
            user_id=current_user.id,
            event_key=f"lesson:{lesson.id}:completed",
            reason=f"Đọc hoàn thành bài giảng: {lesson.title}",
            xp=lesson.xp_reward,
        )
        flash(
            f"Đã hoàn thành bài giảng (+{lesson.xp_reward} XP)! Hãy làm bài tập thực hành bên dưới để củng cố kiến thức.",
            "success",
        )
    else:
        flash("Bạn đã hoàn thành bài giảng này. Hãy tiến hành làm bài thực hành củng cố!", "info")

    # Nếu người học bấm nút "Hoàn thành Bài giảng & Thực hành Ngay", chuyển thẳng tới Phòng Lab của bài giảng đó
    next_ex_slug = request.form.get("next_exercise_slug")
    if next_ex_slug:
        return redirect(url_for("judge.workspace", slug=next_ex_slug))

    return redirect(url_for("main.lesson_detail", slug=lesson.slug))


def _slugify(text: str, prefix: str = "item") -> str:
    """Chuyển chuỗi tiếng Việt có dấu thành slug URL an toàn."""
    text = text.replace("đ", "d").replace("Đ", "d")
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or f"{prefix}-{int(datetime.now(timezone.utc).timestamp())}"


@main_bp.route("/mentor-portal")
@role_required("instructor", "admin")
def mentor_portal():
    """
    Cổng quản lý dành cho Mentor/Giảng viên và Quản trị viên:
    - Bổ sung Bài giảng lý thuyết & Bài tập thực hành mới cho sinh viên.
    - Xem tiến độ và nhận xét bài làm của người học.
    - Quản lý tài khoản và phân quyền (Admin).
    """
    learners = User.query.order_by(User.total_xp.desc()).all()
    submissions = Submission.query.order_by(Submission.created_at.desc()).limit(15).all()
    skills = Skill.query.order_by(Skill.tier.asc(), Skill.id.asc()).all()
    courses = Course.query.order_by(Course.id.asc()).all()
    lessons = Lesson.query.order_by(Lesson.course_id.asc(), Lesson.order_index.asc(), Lesson.id.asc()).all()
    exercises = Exercise.query.order_by(Exercise.id.desc()).all()
    return render_template(
        "mentor_portal.html",
        learners=learners,
        submissions=submissions,
        skills=skills,
        courses=courses,
        lessons=lessons,
        exercises=exercises,
    )


@main_bp.route("/mentor-portal/lessons/create", methods=["POST"])
@role_required("instructor", "admin")
def create_lesson():
    """Giảng viên bổ sung Bài giảng Lý thuyết mới vào Học phần."""
    course_id = request.form.get("course_id", type=int)
    title = (request.form.get("title") or "").strip()
    raw_slug = (request.form.get("slug") or "").strip()
    order_index = request.form.get("order_index", type=int, default=1)
    xp_reward = request.form.get("xp_reward", type=int, default=25)
    content_markdown = (request.form.get("content_markdown") or "").strip()

    if not course_id or not title or not content_markdown:
        flash("Vui lòng nhập đầy đủ Học phần, Tiêu đề và Nội dung bài giảng.", "danger")
        return redirect(url_for("main.mentor_portal"))

    slug = _slugify(raw_slug or title, prefix="bai-giang")
    if Lesson.query.filter_by(slug=slug).first():
        slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp()) % 10000}"

    new_lesson = Lesson(
        course_id=course_id,
        title=title,
        slug=slug,
        order_index=max(1, order_index),
        xp_reward=max(5, xp_reward),
        content_markdown=content_markdown,
    )
    db.session.add(new_lesson)
    db.session.commit()
    flash(f"Đã tạo bài giảng mới '{title}' (+{xp_reward} XP) thành công!", "success")
    return redirect(url_for("main.mentor_portal"))


@main_bp.route("/mentor-portal/exercises/create", methods=["POST"])
@role_required("instructor", "admin")
def create_exercise():
    """Giảng viên bổ sung Bài tập Thực hành (hỗ trợ Python / Flask / SQL / REST API) và Bộ Test Cases."""
    title = (request.form.get("title") or "").strip()
    raw_slug = (request.form.get("slug") or "").strip()
    skill_id = request.form.get("skill_id", type=int)
    lesson_id = request.form.get("lesson_id", type=int) or None
    difficulty = (request.form.get("difficulty") or "easy").strip()
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "easy"
    xp_reward = request.form.get("xp_reward", type=int, default=60)
    scaffold_level = (
        request.form.get("scaffold_level")
        or "Bài tập thực hành do Giảng viên biên soạn (Có sẵn khung code)"
    ).strip()
    problem_statement = (request.form.get("problem_statement") or "").strip()
    starter_python = (request.form.get("starter_code_python") or "").strip()
    hints_raw = (request.form.get("hints_text") or "").strip()

    if not title or not skill_id or not problem_statement or not starter_python:
        flash("Vui lòng điền đầy đủ Tiêu đề, Kỹ năng, Đề bài và Khung code Python mẫu.", "danger")
        return redirect(url_for("main.mentor_portal"))

    slug = _slugify(raw_slug or title, prefix="bai-tap")
    if Exercise.query.filter_by(slug=slug).first():
        slug = f"{slug}-{int(datetime.now(timezone.utc).timestamp()) % 10000}"

    hints_list = [line.strip() for line in hints_raw.splitlines() if line.strip()]
    if not hints_list:
        hints_list = [
            "Đọc kỹ đề bài và hoàn thiện phần đánh dấu # TODO trong khung code.",
            "Kiểm tra định dạng dữ liệu in ra trong hàm print() khớp với Output mẫu.",
        ]

    uses_flask_or_sql = any(kw in starter_python for kw in ("flask", "Flask", "sqlite3"))
    time_limit_ms = 3000 if uses_flask_or_sql else 1500
    memory_limit_kb = 262144 if uses_flask_or_sql else 65536

    new_ex = Exercise(
        skill_id=skill_id,
        lesson_id=lesson_id,
        title=title,
        slug=slug,
        difficulty=difficulty,
        scaffold_level=scaffold_level,
        xp_reward=max(10, xp_reward),
        time_limit_ms=time_limit_ms,
        memory_limit_kb=memory_limit_kb,
        hints=hints_list,
        tags=["giang-vien", difficulty],
        problem_statement=problem_statement,
        starter_code={"python": starter_python},
    )
    db.session.add(new_ex)
    db.session.flush()

    # Thu thập các Test Cases (Sample + Hidden) từ form
    tc_inputs = request.form.getlist("tc_input")
    tc_outputs = request.form.getlist("tc_output")
    tc_samples = set(request.form.getlist("tc_is_sample"))

    added_tc = 0
    for idx, (inp, out) in enumerate(zip(tc_inputs, tc_outputs), start=1):
        out_clean = out.replace("\r\n", "\n").strip()
        inp_clean = inp.replace("\r\n", "\n")
        if not out_clean and not inp_clean.strip():
            continue
        if inp_clean and not inp_clean.endswith("\n"):
            inp_clean += "\n"
        out_clean += "\n"
        is_sample = str(idx) in tc_samples or idx <= 2
        db.session.add(
            TestCase(
                exercise_id=new_ex.id,
                input_data=inp_clean,
                expected_output=out_clean,
                is_sample=is_sample,
                order_index=added_tc + 1,
            )
        )
        added_tc += 1

    if added_tc == 0:
        db.session.add(
            TestCase(
                exercise_id=new_ex.id,
                input_data="1\n",
                expected_output="1\n",
                is_sample=True,
                order_index=1,
            )
        )

    db.session.commit()
    flash(f"Đã tạo bài tập '{title}' kèm {max(1, added_tc)} Test Case thành công! Sinh viên có thể vào luyện tập ngay.", "success")
    return redirect(url_for("main.mentor_portal"))


@main_bp.route("/mentor-portal/lessons/<int:lesson_id>/delete", methods=["POST"])
@role_required("instructor", "admin")
def delete_lesson(lesson_id: int):
    lesson = db.session.get(Lesson, lesson_id)
    if lesson:
        title = lesson.title
        db.session.delete(lesson)
        db.session.commit()
        flash(f"Đã xóa bài giảng '{title}'.", "info")
    return redirect(url_for("main.mentor_portal"))


@main_bp.route("/mentor-portal/exercises/<int:exercise_id>/delete", methods=["POST"])
@role_required("instructor", "admin")
def delete_exercise(exercise_id: int):
    ex = db.session.get(Exercise, exercise_id)
    if ex:
        title = ex.title
        db.session.delete(ex)
        db.session.commit()
        flash(f"Đã xóa bài tập '{title}'.", "info")
    return redirect(url_for("main.mentor_portal"))


@main_bp.route("/mentor-portal/review/<int:submission_id>", methods=["POST"])
@role_required("instructor", "admin")
def review_submission(submission_id: int):
    sub = db.session.get(Submission, submission_id)
    if sub:
        sub.mentor_comment = request.form.get("mentor_comment", "").strip()
        sub.mentor_name = current_user.username
        db.session.commit()
        flash(f"Đã lưu nhận xét của Mentor cho bài nộp #{sub.id} của {sub.user.username}!", "success")
    return redirect(url_for("main.mentor_portal"))


@main_bp.route("/mentor-portal/role/<int:user_id>", methods=["POST"])
@role_required("admin")
def update_user_role(user_id: int):
    user = db.session.get(User, user_id)
    new_role = request.form.get("role")
    if user and new_role in ("learner", "instructor", "admin"):
        user.role = new_role
        db.session.commit()
        flash(f"Đã cập nhật quyền của tài khoản {user.username} thành {new_role.upper()}.", "success")
    return redirect(url_for("main.mentor_portal"))
