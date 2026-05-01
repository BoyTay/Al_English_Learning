from flask import Blueprint, render_template, request, session, flash
from flask_login import login_required, current_user

from app.llm_service import generate_speaking_prompts, check_rate_limit

speaking_bp = Blueprint('speaking', __name__, url_prefix='/speaking')


@speaking_bp.route('/', methods=['GET'])
@login_required
def practice():
    request_new = request.args.get('new') == '1'
    prompts = session.get('speaking_prompts')
    should_generate = request_new or not isinstance(prompts, list) or not prompts
    if should_generate:
        allowed, retry_after = check_rate_limit(
            actor_key=f"user:{current_user.id}",
            bucket='speaking-prompts',
            max_requests=6,
            window_seconds=60,
        )
        if not allowed and isinstance(prompts, list) and prompts:
            flash(f'Bạn tạo đề mới quá nhanh. Vui lòng thử lại sau {retry_after} giây.', 'warning')
            return render_template('speaking.html', prompts=prompts, prompt_count=len(prompts))
        prompts = generate_speaking_prompts(count=6)
        session['speaking_prompts'] = prompts
    return render_template('speaking.html', prompts=prompts, prompt_count=len(prompts))
