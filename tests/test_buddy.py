import pytest
from config import TestConfig
from app import create_app
from app.extensions import db
from app.seed import seed_initial_data
from app.models import User, Roadmap, ChatRoom, UserBlock
from app.services import buddy_service as svc
from app.services import ai_helper_service as ai
from app.services.buddy_rules import is_compatible


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        seed_initial_data()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _users():
    a = User.query.filter_by(username="minh_anh_k65").one()
    b = User.query.filter_by(username="tuan_kiet_k65").one()
    c = User.query.filter_by(username="lan_huong_k64").one()
    return a, b, c


def _login(client, user_id):
    with client.session_transaction() as s:
        s["_user_id"] = str(user_id)
        s["_fresh"] = True


# ---------- Ghép cặp ----------

def test_quy_tac_ghep_cap():
    me = {"roadmap_id": 1, "level": 2}
    assert is_compatible(me, {"roadmap_id": 1, "level": 3, "available": True})
    assert not is_compatible(me, {"roadmap_id": 1, "level": 4, "available": True})
    assert not is_compatible(me, {"roadmap_id": 2, "level": 2, "available": True})
    assert not is_compatible(me, {"roadmap_id": 1, "level": 2, "available": False})


def test_ung_vien_dung_lo_trinh_va_trinh_do(app):
    r = Roadmap.query.first()
    a, b, c = _users()
    svc.save_profile(a.id, r.id, 1, True)
    svc.save_profile(b.id, r.id, 2, True)
    svc.save_profile(c.id, r.id, 3, True)
    ids = [p.user_id for p in svc.find_candidates(a.id)]
    assert b.id in ids
    assert c.id not in ids


def test_ghep_doi_khi_ca_hai_cung_like(app):
    r = Roadmap.query.first()
    a, b, _ = _users()
    svc.save_profile(a.id, r.id, 1, True)
    svc.save_profile(b.id, r.id, 1, True)

    assert svc.swipe(a.id, b.id, "like")["matched"] is False
    result = svc.swipe(b.id, a.id, "like")
    assert result["matched"] is True and result["room_id"]
    assert b.id in svc.matched_partner_ids(a.id)

    room = db.session.get(ChatRoom, result["room_id"])
    assert svc.can_send(a.id, room)


def test_bi_chan_khong_hien_thi_va_khong_gui_duoc(app):
    r = Roadmap.query.first()
    a, b, _ = _users()
    svc.save_profile(a.id, r.id, 1, True)
    svc.save_profile(b.id, r.id, 1, True)
    db.session.add(UserBlock(blocker_id=a.id, blocked_id=b.id))
    db.session.commit()

    assert b.id not in [p.user_id for p in svc.find_candidates(a.id)]
    with pytest.raises(ValueError):
        svc.swipe(a.id, b.id, "like")


# ---------- Chat: nhóm và API ----------

def _pair_room(a, b, r):
    svc.save_profile(a.id, r.id, 1, True)
    svc.save_profile(b.id, r.id, 1, True)
    svc.swipe(a.id, b.id, "like")
    return db.session.get(ChatRoom, svc.swipe(b.id, a.id, "like")["room_id"])


def test_api_gui_va_lay_tin_nhan(app, client):
    r = Roadmap.query.first()
    a, b, _ = _users()
    room = _pair_room(a, b, r)

    _login(client, a.id)
    res = client.post(f"/chat/api/rooms/{room.id}/messages", json={"content": "Chào bạn"})
    assert res.status_code == 200 and res.get_json()["ok"]

    res = client.get(f"/chat/api/rooms/{room.id}/messages?after=0")
    data = res.get_json()
    assert [m["content"] for m in data["messages"]] == ["Chào bạn"]
    assert data["can_send"] is True


def test_nguoi_ngoai_phong_khong_doc_duoc_tin(app, client):
    r = Roadmap.query.first()
    a, b, c = _users()
    room = _pair_room(a, b, r)
    _login(client, c.id)
    assert client.get(f"/chat/api/rooms/{room.id}/messages").status_code == 403


def test_nhom_chi_gom_ban_da_ghep_doi(app):
    r = Roadmap.query.first()
    a, b, c = _users()
    _pair_room(a, b, r)  # a và b đã ghép, c thì chưa
    group = svc.create_group(a.id, "Nhóm test", [b.id])
    assert {m.user_id for m in group.members} == {a.id, b.id}


def test_api_ai_khong_co_key_tra_loi_ro_rang(app, client, monkeypatch):
    a, _, _ = _users()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ai._cache.clear()
    _login(client, a.id)
    res = client.post("/ai/api/explain", json={"code": "x", "error": "lỗi", "level": 1})
    assert res.status_code == 503
    assert "ANTHROPIC_API_KEY" in res.get_json()["error"]


def test_ai_cache_tra_ve_nhanh_khi_hoi_lai(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json
            return json.dumps({"content": [{"type": "text", "text": "Gợi ý thử"}]}).encode()

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
    ai._cache.clear()

    assert ai.explain_error("print(1)", "SyntaxError", 1) == "Gợi ý thử"
    assert ai.explain_error("print(1)", "SyntaxError", 1) == "Gợi ý thử"
    assert calls["n"] == 1  # lần 2 lấy từ cache, không gọi API
