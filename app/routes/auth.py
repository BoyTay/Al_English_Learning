from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, current_user
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from urllib.parse import urlsplit
from app import db, mail
from app.models import User
from flask_mail import Message

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = db.session.scalar(db.select(User).where(User.username == username))
        if user is None or not user.check_password(password):
            flash('Tài khoản hoặc mật khẩu không chính xác.', 'danger')
            return redirect(url_for('auth.login'))
        login_user(user, remember=True)
        user.update_streak()
        db.session.commit()
        next_page = request.args.get('next')
        if not next_page or urlsplit(next_page).netloc != '':
            next_page = url_for('main.dashboard')
        return redirect(next_page)
    return render_template('auth/login.html')

@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('main.index'))

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        
        if not username or not email or not password or not password_confirm:
            flash('Vui lòng nhập đầy đủ thông tin.', 'danger')
            return redirect(url_for('auth.register'))
            
        if len(password) < 8:
            flash('Mật khẩu phải có ít nhất 8 ký tự.', 'danger')
            return redirect(url_for('auth.register'))
            
        if password != password_confirm:
            flash('Mật khẩu xác nhận không khớp.', 'danger')
            return redirect(url_for('auth.register'))
            
        if db.session.scalar(db.select(User).where(User.username == username)):
            flash('Tên đăng nhập đã tồn tại.', 'danger')
            return redirect(url_for('auth.register'))
            
        if db.session.scalar(db.select(User).where(User.email == email)):
            flash('Email này đã được sử dụng.', 'danger')
            return redirect(url_for('auth.register'))
            
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Đăng ký thành công! Vui lòng đăng nhập.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/register.html')

def get_reset_token(user, expires_sec=1800):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps({'user_id': user.id}, salt='password-reset-salt')

def verify_reset_token(token, expires_sec=1800):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        data = s.loads(token, salt='password-reset-salt', max_age=expires_sec)
    except (SignatureExpired, BadSignature):
        return None
    return db.session.get(User, data['user_id'])

@auth_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        user = db.session.scalar(db.select(User).where(User.email == email))
        if user:
            token = get_reset_token(user)
            reset_url = url_for('auth.reset_password', token=token, _external=True)
            
            if current_app.config.get('MAIL_USERNAME'):
                msg = Message('Yêu cầu Khôi phục Mật khẩu', recipients=[user.email])
                msg.body = f'''Xin chào {user.username},

Để đặt lại mật khẩu của bạn, vui lòng click vào đường link sau:
{reset_url}

Nếu bạn không yêu cầu thay đổi mật khẩu, vui lòng bỏ qua email này.

Trân trọng,
English AI Team
'''
                mail.send(msg)
                flash('Một email chứa link khôi phục đã được gửi đến bạn.', 'success')
            else:
                # Fallback for demo when email is not configured
                print(f"PASSWORD RESET LINK: {reset_url}")
                flash(f'LƯU Ý: Do bạn chưa cấu hình thông số Email thật, link khôi phục hiển thị tạm ở đây:', 'info')
                flash(f'Link khôi phục: {reset_url}', 'success')
        else:
            flash('Không tìm thấy tài khoản với email này.', 'danger')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')

@auth_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    user = verify_reset_token(token)
    if not user:
        flash('Link khôi phục không hợp lệ hoặc đã hết hạn.', 'warning')
        return redirect(url_for('auth.forgot_password'))
        
    if request.method == 'POST':
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        
        if not password or not password_confirm:
            flash('Vui lòng nhập đầy đủ.', 'danger')
        elif len(password) < 8:
            flash('Mật khẩu phải có ít nhất 8 ký tự.', 'danger')
        elif password != password_confirm:
            flash('Mật khẩu xác nhận không khớp.', 'danger')
        else:
            user.set_password(password)
            db.session.commit()
            flash('Mật khẩu của bạn đã được cập nhật thành công! Bạn có thể đăng nhập.', 'success')
            return redirect(url_for('auth.login'))
            
    return render_template('auth/reset_password.html', token=token)
