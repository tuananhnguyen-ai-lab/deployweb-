from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app.extensions import db
from app.models import ChatRoom, User
from app.services import buddy_service as svc

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")


def _msg_dict(m) -> dict:
    return {
        "id": m.id,
        "room_id": m.room_id,
        "user_id": m.user_id,
        "username": m.author.username,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _my_room(room_id: int):
    room = db.session.get(ChatRoom, room_id)
    if room is None or not svc.is_member(current_user.id, room):
        return None
    return room


@chat_bp.route("/")
@login_required
def index():
    uid = current_user.id
    rooms = svc.list_rooms(uid)

    current = None
    room_id = request.args.get("room", type=int)
    if room_id:
        candidate = _my_room(room_id)
        if candidate:
            current = candidate
        else:
            flash("Bạn không có quyền vào phòng này.", "danger")
    if current is None and rooms:
        current = rooms[0][0]

    messages, can_send, other_id, current_title = [], False, None, ""
    if current:
        messages = svc.recent_messages(current.id)
        can_send = svc.can_send(uid, current)
        other_id = svc.other_member_id(current, uid) if current.type == "pair" else None
        current_title = svc.room_title(current, uid)

    partners = (
        User.query.filter(User.id.in_(svc.matched_partner_ids(uid))).order_by(User.username).all()
        if svc.matched_partner_ids(uid) else []
    )
    return render_template(
        "chat/index.html",
        rooms=rooms,
        current=current,
        messages=[_msg_dict(m) for m in messages],
        can_send=can_send,
        other_id=other_id,
        current_title=current_title,
        partners=partners,
    )


@chat_bp.route("/group", methods=["POST"])
@login_required
def create_group():
    name = (request.form.get("name") or "").strip()
    picked = [int(x) for x in request.form.getlist("member_ids") if x.isdigit()]
    allowed = set(svc.matched_partner_ids(current_user.id))
    members = [uid for uid in picked if uid in allowed]

    if not name:
        flash("Hãy đặt tên cho nhóm.", "warning")
    elif not members:
        flash("Chỉ có thể thêm những bạn học bạn đã ghép đôi.", "warning")
    else:
        room = svc.create_group(current_user.id, name, members)
        flash(f"Đã tạo nhóm “{room.name}”.", "success")
        return redirect(url_for("chat.index", room=room.id))
    return redirect(url_for("chat.index"))


@chat_bp.route("/api/rooms/<int:room_id>/messages", methods=["GET"])
@login_required
def api_messages(room_id: int):
    room = _my_room(room_id)
    if room is None:
        return jsonify(ok=False, error="Không có quyền vào phòng này"), 403
    after = request.args.get("after", default=0, type=int)
    msgs = svc.messages_after(room.id, after)
    return jsonify(
        ok=True,
        messages=[_msg_dict(m) for m in msgs],
        can_send=svc.can_send(current_user.id, room),
    )


@chat_bp.route("/api/rooms/<int:room_id>/messages", methods=["POST"])
@login_required
def api_send(room_id: int):
    room = _my_room(room_id)
    if room is None:
        return jsonify(ok=False, error="Không có quyền vào phòng này"), 403
    data = request.get_json(silent=True) or {}
    content = str(data.get("content", "")).strip()
    if not content or len(content) > 500:
        return jsonify(ok=False, error="Tin nhắn phải từ 1 đến 500 ký tự"), 400
    if not svc.can_send(current_user.id, room):
        return jsonify(ok=False, error="Không thể gửi tin nhắn trong phòng này"), 403
    msg = svc.save_message(room, current_user.id, content)
    return jsonify(ok=True, message=_msg_dict(msg))
