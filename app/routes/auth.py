from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user
from urllib.parse import urlsplit
from app import db
from app.models import User

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
        username = request.form['username']
        password = request.form['password']
        
        if not username or not password:
            flash('Vui lòng nhập đầy đủ thông tin.', 'danger')
            return redirect(url_for('auth.register'))
            
        if db.session.scalar(db.select(User).where(User.username == username)):
            flash('Tên đăng nhập đã tồn tại.', 'danger')
            return redirect(url_for('auth.register'))
            
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Đăng ký thành công! Vui lòng đăng nhập.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/register.html')
