from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_

from app.extensions import db
from app.models import User
from app.services.dag_service import sync_user_skill_tree
from app.services.ai_tutor_service import award_badge_if_not_owned

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# ==============================================================================
# CẤU HÌNH ĐƯỜNG DẪN ĐÍCH THEO PHÂN QUYỀN (TỪ LMS_AUTH)
# ==============================================================================
LEARNER_PRACTICE_URL = "/practice"                      # Trang Ôn tập dành cho Người học
INSTRUCTOR_ADD_LESSON_URL = "/instructor/lessons/new"   # Trang Thêm bài giảng & bài tập cho Giảng viên


def get_redirect_target_by_role(user) -> str:
    """Trả về đường dẫn điều hướng dựa trên vai trò (role) của tài khoản."""
    if user.role in ("instructor", "admin"):
        return INSTRUCTOR_ADD_LESSON_URL
    return LEARNER_PRACTICE_URL


# ==============================================================================
# DECORATOR PHÂN QUYỀN
# ==============================================================================
def role_required(*roles):
    """Decorator kiểm soát phân quyền RBAC (Learner / Instructor / Admin)."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def instructor_required(f):
    """Chỉ cho phép Giảng viên (instructor) hoặc Admin truy cập (VD: trang đẩy bài giảng, bài tập)."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ("instructor", "admin"):
            flash("Bạn cần tài khoản Giảng viên để truy cập trang thêm bài giảng / bài tập.", "danger")
            return redirect(get_redirect_target_by_role(current_user))
        return f(*args, **kwargs)
    return decorated_function


def learner_required(f):
    """Chỉ cho phép Người học (learner) truy cập (hoặc điều hướng giảng viên về trang giảng viên)."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != "learner":
            return redirect(get_redirect_target_by_role(current_user))
        return f(*args, **kwargs)
    return decorated_function


# ==============================================================================
# 1. ĐĂNG NHẬP (/auth/login)
# ==============================================================================
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(get_redirect_target_by_role(current_user))

    if request.method == "POST":
        identifier = (request.form.get("identifier") or request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        selected_role = (request.form.get("role") or "").strip()
        remember = bool(request.form.get("remember"))

        if not identifier or not password:
            flash("Vui lòng nhập đầy đủ tên đăng nhập/email và mật khẩu.", "warning")
            return render_template("auth/login.html", selected_role=selected_role or "learner")

        user = User.query.filter(
            or_(User.username == identifier, User.email == identifier)
        ).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Tên đăng nhập/email hoặc mật khẩu không chính xác.", "danger")
            return render_template("auth/login.html", selected_role=selected_role or "learner")

        # Kiểm tra phân quyền chính khi đăng nhập: Người học vs Giảng viên (nếu có chọn trên form)
        if selected_role == "instructor" and user.role not in ("instructor", "admin"):
            flash("Tài khoản này là Người học, không có quyền đăng nhập vào cổng Giảng viên.", "danger")
            return render_template("auth/login.html", selected_role=selected_role)

        if selected_role == "learner" and user.role == "instructor":
            flash("Tài khoản này là Giảng viên. Vui lòng chọn vai trò 'Giảng viên' để vào trang thêm bài giảng.", "warning")
            return render_template("auth/login.html", selected_role="instructor")

        login_user(user, remember=remember)
        sync_user_skill_tree(user.id)

        if user.role in ("instructor", "admin"):
            flash(f"Chào mừng Giảng viên {user.username}! Đang chuyển đến trang thêm bài giảng & bài tập.", "success")
        else:
            flash(f"Chào mừng {user.username}! Đang chuyển đến phần ôn tập.", "success")

        return redirect(get_redirect_target_by_role(user))

    return render_template("auth/login.html", selected_role="learner")


# ==============================================================================
# 2. ĐĂNG KÝ (/auth/register)
# ==============================================================================
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(get_redirect_target_by_role(current_user))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password")
        if confirm_password is None:
            confirm_password = password
        role = (request.form.get("role") or "learner").strip()

        if role not in ("learner", "instructor"):
            role = "learner"

        if not username or not email or not password:
            flash("Vui lòng điền đầy đủ các thông tin bắt buộc.", "warning")
            return render_template("auth/register.html", selected_role=role)

        if len(password) < 6:
            flash("Mật khẩu phải có ít nhất 6 ký tự.", "warning")
            return render_template("auth/register.html", selected_role=role)

        if password != confirm_password:
            flash("Mật khẩu xác nhận không khớp.", "danger")
            return render_template("auth/register.html", selected_role=role)

        existing_user = User.query.filter(
            or_(User.username == username, User.email == email)
        ).first()
        if existing_user:
            if existing_user.username == username:
                flash("Tên đăng nhập đã tồn tại, vui lòng chọn tên khác.", "danger")
            else:
                flash("Email này đã được sử dụng.", "danger")
            return render_template("auth/register.html", selected_role=role)

        is_instructor = (role == "instructor")
        new_user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            total_xp=0 if is_instructor else 10,
            current_streak=0,
            placement_done=is_instructor,
            placement_level=(
                "Giảng viên / Mentor Khoa CNTT - NEU"
                if is_instructor
                else "Người học mới"
            ),
        )

        db.session.add(new_user)
        db.session.commit()

        sync_user_skill_tree(new_user.id)
        if not is_instructor:
            award_badge_if_not_owned(new_user.id, "welcome_web")

        login_user(new_user)
        if is_instructor:
            flash("Đăng ký tài khoản Giảng viên thành công! Bạn có thể thêm bài giảng và bài tập ngay.", "success")
        else:
            flash("Đăng ký tài khoản Người học thành công! Chào mừng bạn đến phần ôn tập.", "success")

        return redirect(get_redirect_target_by_role(new_user))

    return render_template("auth/register.html", selected_role="learner")


# ==============================================================================
# 3. ĐĂNG NHẬP NHANH DEMO (/auth/quick-login/<role>)
# ==============================================================================
@auth_bp.route("/quick-login/<role>")
def quick_login(role: str):
    role_map = {
        "learner": "hocvien",
        "instructor": "mentor",
        "admin": "admin",
    }
    target_username = role_map.get(role, "hocvien")
    user = User.query.filter_by(username=target_username).first()
    if user:
        login_user(user)
        sync_user_skill_tree(user.id)
        return redirect(get_redirect_target_by_role(user))
    flash("Tài khoản demo chưa được khởi tạo.", "warning")
    return redirect(url_for("auth.login"))


# ==============================================================================
# 4. ĐĂNG XUẤT (/auth/logout)
# ==============================================================================
@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Bạn đã đăng xuất thành công.", "info")
    return redirect(url_for("auth.login"))
