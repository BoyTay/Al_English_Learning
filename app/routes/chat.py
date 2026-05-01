from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.llm_service import chat_with_tutor, summarize_chat_session, check_rate_limit
from app.models import ChatMessage, ChatSession

chat_bp = Blueprint('chat', __name__, url_prefix='/chat')


def _get_or_create_active_session(user_id: int, force_new: bool = False) -> ChatSession:
    if force_new:
        session = ChatSession(user_id=user_id)
        db.session.add(session)
        db.session.commit()
        return session
    session = db.session.execute(
        db.select(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.ended_at.is_(None),
        )
        .order_by(ChatSession.started_at.desc())
    ).scalars().first()
    if session:
        return session
    session = ChatSession(user_id=user_id)
    db.session.add(session)
    db.session.commit()
    return session


def _serialize_messages(session_id: int) -> list[dict]:
    rows = db.session.execute(
        db.select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()
    return [
        {
            "role": msg.role,
            "content": msg.content,
            "corrections": msg.corrections or [],
            "error_tags": msg.error_tags or [],
        }
        for msg in rows
    ]


@chat_bp.route('/', methods=['GET'])
@login_required
def index():
    session = _get_or_create_active_session(current_user.id)

    messages = _serialize_messages(session.id)
    return render_template('chat.html', chat_session=session, messages=messages)


@chat_bp.route('/widget/init', methods=['GET'])
@login_required
def widget_init():
    force_new = request.args.get('force_new') == '1'
    session = _get_or_create_active_session(current_user.id, force_new=force_new)
    messages = _serialize_messages(session.id)
    return jsonify({
        "success": True,
        "session_id": session.id,
        "messages": messages[-20:],
    })


@chat_bp.route('/send', methods=['POST'])
@login_required
def send():
    data = request.json or {}
    session_id = data.get('session_id')
    user_message = str(data.get('message') or '').strip()
    if not session_id or not user_message:
        return jsonify({"success": False, "message": "Thiếu dữ liệu chat."}), 400
    allowed, retry_after = check_rate_limit(
        actor_key=f"user:{current_user.id}",
        bucket='tutor-chat-send',
        max_requests=8,
        window_seconds=60,
    )
    if not allowed:
        return jsonify({
            "success": False,
            "message": f"Bạn gửi hơi nhanh. Vui lòng thử lại sau {retry_after} giây.",
            "retry_after": retry_after,
        }), 429

    session = db.session.get(ChatSession, int(session_id))
    if not session or session.user_id != current_user.id:
        return jsonify({"success": False, "message": "Session chat không hợp lệ."}), 404
    if session.ended_at is not None:
        return jsonify({"success": False, "message": "Session đã kết thúc."}), 400

    history = _serialize_messages(session.id)
    tutor_reply = chat_with_tutor(history, user_message)

    user_msg = ChatMessage(
        session_id=session.id,
        user_id=current_user.id,
        role='user',
        content=user_message,
        corrections=[],
        error_tags=[],
    )
    assistant_msg = ChatMessage(
        session_id=session.id,
        user_id=current_user.id,
        role='assistant',
        content=tutor_reply["assistant_reply"],
        corrections=tutor_reply.get("corrections") or [],
        error_tags=tutor_reply.get("error_tags") or [],
    )
    db.session.add(user_msg)
    db.session.add(assistant_msg)
    db.session.commit()

    return jsonify({
        "success": True,
        "assistant_reply": assistant_msg.content,
        "corrections": assistant_msg.corrections or [],
        "error_tags": assistant_msg.error_tags or [],
    })


@chat_bp.route('/end', methods=['POST'])
@login_required
def end_session():
    data = request.json or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({"success": False, "message": "Thiếu session_id."}), 400
    allowed, retry_after = check_rate_limit(
        actor_key=f"user:{current_user.id}",
        bucket='tutor-chat-summary',
        max_requests=4,
        window_seconds=60,
    )
    if not allowed:
        return jsonify({
            "success": False,
            "message": f"Bạn thao tác quá nhanh. Vui lòng thử lại sau {retry_after} giây.",
            "retry_after": retry_after,
        }), 429

    session = db.session.get(ChatSession, int(session_id))
    if not session or session.user_id != current_user.id:
        return jsonify({"success": False, "message": "Session chat không hợp lệ."}), 404

    history = _serialize_messages(session.id)
    summary = summarize_chat_session(history)
    session.summary = summary.get("summary") or ""
    session.summary_error_tags = summary.get("key_errors") or []
    session.ended_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        "success": True,
        "summary": session.summary,
        "key_errors": session.summary_error_tags or [],
    })
