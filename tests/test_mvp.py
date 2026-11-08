import pytest
from config import TestConfig
from app import create_app
from app.extensions import db
from app.seed import seed_initial_data
from app.models import User, Skill, Exercise, Submission, UserSkillProgress
from app.services.dag_service import would_create_cycle
from app.workers.sandbox_runner import run_testcase_in_sandbox
from app.workers.judge_worker import evaluate_submission_task


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_initial_data()
        yield app
        db.session.remove()
        db.drop_all()


def test_sandbox_runner_verdicts():
    # 1. Kiểm thử Accepted (AC)
    res_ac = run_testcase_in_sandbox(
        language="python",
        source_code="import sys\nprint(f'<h1>{sys.stdin.read().strip()}</h1>')",
        stdin_data="Xin chao Web\n",
        expected_output="<h1>Xin chao Web</h1>\n",
        time_limit_ms=1000,
    )
    assert res_ac.verdict == "AC"

    # 2. Kiểm thử Wrong Answer (WA)
    res_wa = run_testcase_in_sandbox(
        language="python",
        source_code="print('<h2>Sai the</h2>')",
        stdin_data="Xin chao Web\n",
        expected_output="<h1>Xin chao Web</h1>\n",
        time_limit_ms=1000,
    )
    assert res_wa.verdict == "WA"

    # 3. Kiểm thử Time Limit Exceeded (TLE)
    res_tle = run_testcase_in_sandbox(
        language="python",
        source_code="while True: pass",
        stdin_data="",
        expected_output="",
        time_limit_ms=300,
    )
    assert res_tle.verdict == "TLE"

    # 4. Kiểm thử Runtime Error (RE)
    res_re = run_testcase_in_sandbox(
        language="python",
        source_code="print(1 / 0)",
        stdin_data="",
        expected_output="",
        time_limit_ms=1000,
    )
    assert res_re.verdict == "RE"


def test_web_dag_cycle_and_auto_unlock_with_ai_tutor(app):
    with app.app_context():
        s_html = Skill.query.filter_by(slug="html5-semantic").first()
        s_css = Skill.query.filter_by(slug="css3-flexbox-grid").first()
        s_flask = Skill.query.filter_by(slug="python-flask-backend").first()

        # Kiểm tra chống chu trình trên Cây kỹ năng Web (HTML -> CSS -> DOM -> Flask)
        assert would_create_cycle(skill_id=s_html.id, prerequisite_id=s_flask.id) is True

        learner = User.query.filter_by(username="hocvien").first()
        ex_html = Exercise.query.filter_by(slug="bo-sinh-the-tieu-de-html5").first()

        prog_css_before = UserSkillProgress.query.filter_by(
            user_id=learner.id, skill_id=s_css.id
        ).first()
        assert prog_css_before.status == "locked"

        # 1. Nếu sinh viên nộp nguyên khung code mẫu chưa điền đáp án (# TODO) -> phải báo WA và AI nhắc hoàn thiện TODO
        sub_todo = Submission(
            user_id=learner.id,
            exercise_id=ex_html.id,
            language="python",
            source_code=ex_html.starter_code["python"],
            status="PENDING",
        )
        db.session.add(sub_todo)
        db.session.commit()
        evaluate_submission_task(app, sub_todo.id)
        db.session.refresh(sub_todo)
        assert sub_todo.status == "WA"
        assert "TODO" in sub_todo.ai_explanation

        # 2. Khi sinh viên tự điền đúng đáp án vào khung code -> đạt AC và mở khóa Node Tầng 2
        completed_code = ex_html.starter_code["python"].replace(
            'return ""', 'return f"<h1>{title_text}</h1>"'
        )
        sub = Submission(
            user_id=learner.id,
            exercise_id=ex_html.id,
            language="python",
            source_code=completed_code,
            status="PENDING",
        )
        db.session.add(sub)
        db.session.commit()

        evaluate_submission_task(app, sub.id)

        db.session.refresh(sub)
        assert sub.status == "AC"
        assert sub.passed_test_cases == 3
        assert "Trợ lý AI" in sub.ai_explanation

        # Sau khi giải xong bài tập HTML5 (Tầng 1), Node CSS3 (Tầng 2) tự động mở khóa!
        prog_html_after = UserSkillProgress.query.filter_by(
            user_id=learner.id, skill_id=s_html.id
        ).first()
        prog_css_after = UserSkillProgress.query.filter_by(
            user_id=learner.id, skill_id=s_css.id
        ).first()

        assert prog_html_after.status == "mastered"
        assert prog_css_after.status == "unlocked"


def test_flask_sql_api_exercises_in_sandbox(app):
    """Kiểm thử chạy trực tiếp code Python Flask + SQL (SQLite) + Gọi REST API trong Sandbox."""
    with app.app_context():
        learner = User.query.filter_by(username="hocvien").first()

        # 1. Kiểm thử Bài 6: Python Flask + SQLite INSERT/SELECT + POST /api/orders
        ex6 = Exercise.query.filter_by(slug="tinh-thue-vat-va-chiet-khau-backend").first()
        solved_ex6 = ex6.starter_code["python"].replace(
            "return 0",
            "subtotal = unit_price * quantity\n"
            "    after_discount = (subtotal * 90) // 100 if quantity >= 10 else subtotal\n"
            "    return (after_discount * 110) // 100",
        )
        sub6 = Submission(
            user_id=learner.id,
            exercise_id=ex6.id,
            language="python",
            source_code=solved_ex6,
            status="PENDING",
        )
        db.session.add(sub6)
        db.session.commit()
        evaluate_submission_task(app, sub6.id)
        db.session.refresh(sub6)
        assert sub6.status == "AC"
        assert sub6.passed_test_cases == 3

        # 2. Kiểm thử Bài 7: Python Flask + SQL SELECT/WHERE + GET /api/products
        ex7 = Exercise.query.filter_by(slug="flask-sql-api-tim-kiem-san-pham").first()
        solved_ex7 = ex7.starter_code["python"].replace(
            "return []",
            "rows = conn.execute('SELECT name, price FROM products WHERE price <= ? ORDER BY price ASC', (max_price,)).fetchall()\n"
            "    return [{'name': r[0], 'price': r[1]} for r in rows]",
        )
        sub7 = Submission(
            user_id=learner.id,
            exercise_id=ex7.id,
            language="python",
            source_code=solved_ex7,
            status="PENDING",
        )
        db.session.add(sub7)
        db.session.commit()
        evaluate_submission_task(app, sub7.id)
        db.session.refresh(sub7)
        assert sub7.status == "AC"
        assert sub7.passed_test_cases == 3


def test_instructor_create_lesson_and_exercise(app):
    """Kiểm thử Giảng viên (mentor) đăng nhập và bổ sung thêm Bài giảng + Bài tập mới cho sinh viên."""
    from app.models import Course, Lesson

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": "mentor", "password": "123456"},
        follow_redirects=True,
    )

    with app.app_context():
        course = Course.query.first()
        skill = Skill.query.filter_by(slug="python-flask-backend").first()
        course_id = course.id
        skill_id = skill.id

    # 1. Giảng viên tạo Bài giảng lý thuyết mới
    res_lesson = client.post(
        "/mentor-portal/lessons/create",
        data={
            "course_id": course_id,
            "title": "[CNTT1188] Chuyên đề API Sinh Viên NEU",
            "order_index": 10,
            "xp_reward": 35,
            "content_markdown": "## Nội dung bài giảng mới do Giảng viên biên soạn",
        },
        follow_redirects=True,
    )
    assert res_lesson.status_code == 200
    assert "[CNTT1188] Chuyên đề API Sinh Viên NEU" in res_lesson.get_data(as_text=True)

    with app.app_context():
        created_lesson = Lesson.query.filter_by(title="[CNTT1188] Chuyên đề API Sinh Viên NEU").first()
        assert created_lesson is not None
        lesson_id = created_lesson.id

    # 2. Giảng viên tạo Bài tập thực hành mới kèm 2 Test Cases
    res_ex = client.post(
        "/mentor-portal/exercises/create",
        data={
            "title": "Bài tập Giảng viên: Tính Tổng 2 Số Qua Hàm",
            "skill_id": skill_id,
            "lesson_id": lesson_id,
            "difficulty": "easy",
            "xp_reward": 70,
            "problem_statement": "Tính tổng 2 số nguyên a và b.",
            "starter_code_python": (
                "import sys\n"
                "data = sys.stdin.read().split()\n"
                "# TODO: In ra tổng 2 số\n"
                "print(int(data[0]) + int(data[1]))\n"
            ),
            "hints_text": "Gợi ý 1: Dùng toán tử +",
            "tc_input": ["3 4", "10 20", ""],
            "tc_output": ["7", "30", ""],
            "tc_is_sample": ["1", "2"],
        },
        follow_redirects=True,
    )
    assert res_ex.status_code == 200
    assert "Bài tập Giảng viên: Tính Tổng 2 Số Qua Hàm" in res_ex.get_data(as_text=True)

    with app.app_context():
        created_ex = Exercise.query.filter_by(title="Bài tập Giảng viên: Tính Tổng 2 Số Qua Hàm").first()
        assert created_ex is not None
        assert len(created_ex.test_cases) == 2


