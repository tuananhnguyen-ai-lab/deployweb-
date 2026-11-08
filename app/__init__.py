from flask import Flask
from config import Config
from app.extensions import db, migrate, login_manager, socketio


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Khởi tạo các Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    socketio.init_app(app)

    # Đăng ký các Modular Blueprints (M10 + M12 + M16 + LMS_AUTH)
    from app.blueprints.auth import auth_bp
    from app.blueprints.main import main_bp, practice, instructor_add_material
    from app.blueprints.judge import judge_bp
    from app.blueprints.skills import skills_bp
    from app.blueprints.buddy import buddy_bp
    from app.blueprints.chat import chat_bp
    from app.blueprints.safety import safety_bp
    from app.blueprints.ai import ai_bp
    from app.blueprints.m16 import m16_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(judge_bp)
    app.register_blueprint(skills_bp)
    app.register_blueprint(buddy_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(safety_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(m16_bp)

    # Alias endpoint cho lms_auth templates (url_for('instructor_add_material') & url_for('practice'))
    app.add_url_rule("/practice", endpoint="practice", view_func=practice)
    app.add_url_rule(
        "/instructor/lessons/new",
        endpoint="instructor_add_material",
        view_func=instructor_add_material,
        methods=["GET", "POST"],
    )

    return app
