from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required

from app.services.ai_helper_service import AIError, explain_error

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")


@ai_bp.route("/")
@login_required
def index():
    return render_template("ai/index.html")


@ai_bp.route("/api/explain", methods=["POST"])
@login_required
def api_explain():
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", ""))[:4000]
    error = str(data.get("error", "")).strip()[:2000]
    try:
        level = int(data.get("level", 1))
    except (TypeError, ValueError):
        level = 1

    if not error:
        return jsonify(ok=False, error="Hãy nhập thông báo lỗi hoặc câu hỏi"), 400

    try:
        answer = explain_error(code, error, level)
    except AIError as e:
        return jsonify(ok=False, error=str(e)), 503
    except Exception:
        current_app.logger.exception("Lỗi khi gọi trợ lý AI")
        return jsonify(ok=False, error="Lỗi máy chủ khi gọi AI"), 500
    return jsonify(ok=True, answer=answer)
