#!/usr/bin/python
# -*- coding:utf-8 -*-
from flask import render_template, flash, redirect, url_for, request, \
    session, send_file
from flask_login import current_user, login_user, login_required, logout_user
import datetime
import requests
from . import auth
from .. import db
from .forms import LoginForm, RegistForm, AuthEmail, ResetPassword
from ..token import generate_confirmation_token, confirm_token
from ..tasks.celery_tasks import send_email
from ..models import User
from .g_validate import generate_verify_image
import io
from app import redis_store


# 获取验证码
@auth.route('/get_validate/')
def get_validate():
    image, text = generate_verify_image()
    byte_io = io.BytesIO()
    image.save(byte_io, 'PNG')
    byte_io.seek(0)
    session['code_text'] = text
    return send_file(byte_io, mimetype='image/png')


# 用户注册
@auth.route('/register/', methods=['POST', 'GET'])
def register():
    form = RegistForm()
    if form.validate_on_submit():
        user = User(
            email=form.email.data,
            username=form.username.data,
            password=form.password.data,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now(),
            last_login=datetime.datetime.now(),
            confirmed=False
        )
        user.set_password(form.data['password'])
        # if 'code_text' in session and form['validate'].lower() != session['code_text'].lower():
        #     flash(u'验证码错误！')
        #     return redirect(url_for('auth.register'))
        db.session.add(user)
        db.session.commit()
        token = generate_confirmation_token(user.email)
        confirm_url = url_for('auth.confirm_email', token=token, _external=True)
        html = render_template('auth/email_register.html', confirm_url=confirm_url)
        subject = u"[noreply][51datas]账号注册邮件"
        # send_email(user.email, subject, html)
        send_email.delay(user.email, subject, html)  # 异步
        user = db.session.merge(user)
        login_user(user, remember=True)
        flash('注册成功,请登录您的邮箱按照提示激活账户')
        return redirect(url_for('public.index'))
    return render_template('auth/register.html', form=form, title='用户注册')


# 邮件确认
@auth.route('/confirm/<token>/')
@login_required
def confirm_email(token):
    try:
        email = confirm_token(token)
    except:
        flash('确认链接不可用或已过期!', 'danger')
    user = User.query.filter_by(email=email).first_or_404()
    if user.confirmed:
        flash('您的账户已经激活过了!', 'success')
    else:
        user.confirmed = True
        user.confirmed_on = datetime.datetime.now()
        db.session.add(user)
        db.session.commit()
        flash('您的账户已激活,谢谢!', 'success')
    return redirect(url_for('public.index'))


# 发送激活邮件
@auth.route('/active/', methods=['POST', 'GET'])
@login_required
def active():
    user = current_user
    token = generate_confirmation_token(user.email)
    confirm_url = url_for('auth.confirm_email', token=token, _external=True)
    html = render_template('auth/email_active.html', confirm_url=confirm_url)
    subject = u"[noreply][51datas]账号激活邮件"
    # send_email(user.email, subject, html)  # send_email.delay() 异步
    send_email.delay(user.email, subject, html)  # 异步
    flash('激活邮件已发送至您的邮箱!')
    return redirect(url_for('profile.user_index', username=user.username))


# 用户登录
@auth.route('/login/', methods=['POST', 'GET'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        form_data = form.data
        user = form.get_user()
        if user is None:
            flash('用户不存在')
            return redirect(url_for('auth.login'))
        if user.status == 0:
            flash('该账户已经被限制登录')
            return redirect(url_for('auth.login'))
        if not user.check_password(form_data['password']):
            flash('密码错误,请重试!')
            return redirect(url_for('auth.login'))
        if 'code_text' in session and form_data['validate'].lower() != session['code_text'].lower():
            flash(u'验证码错误！')
            return redirect(url_for('auth.login'))
        login_user(user, remember=form.remember_me.data)
        user.last_login = datetime.datetime.now()
        redis_store.delete('validateCount:%d' % user.id)
        try:
            ip_addr = request.headers['X-real-ip']
        except:
            ip_addr = request.remote_addr
        url = requests.get('http://ip.taobao.com/service/getIpInfo.php?ip=%s' % ip_addr)
        data = url.json()
        user.ip_addr = ip_addr
        user.country = data['data']['country']
        user.area = data['data']['area']
        user.region = data['data']['region']
        user.city = data['data']['city']
        user.county = data['data']['county']
        flash('欢迎回来,%s' % user.username)
        next_url = request.args.get('next')
        return redirect(next_url or url_for('public.index'))
    return render_template('auth/login.html', title='用户登录',form=form)


# 用户登出
@auth.route('/logout/')
@login_required
def logout():
    logout_user()
    flash('您已成功登出,期待再次光临.')
    return redirect(url_for('public.index'))


# 重置密码邮箱确认
@auth.route('/reset/confirm_email/', methods=["GET", "POST"])
def reset_confirm_email():
    form = AuthEmail()
    if form.validate_on_submit():
        uemail = form.email.data
        user = User.query.filter_by(email=uemail).first()
        if not user:
            flash('该邮箱还没有注册!')
            return redirect(url_for('auth.reset_confirm_email'))
        else:
            subject = u"[noreply][51datas]重置密码邮件"
            token = generate_confirmation_token(user.email)
            confirm_url = url_for('auth.reset_password', token=token, _external=True)
            html = render_template('auth/email_reset.html', confirm_url=confirm_url)
            # send_email(user.email, subject, html)  # send_email.delay() 异步
            send_email.delay(user.email, subject, html)  # 异步
            flash('验证邮件已发送至您的邮箱!')
            return redirect(url_for('public.index'))
    return render_template('auth/auth_email.html', form=form, title='邮箱验证')


# 重置密码
@auth.route('/reset/reset_password/<token>/', methods=["GET", "POST"])
def reset_password(token):
    try:
        email = confirm_token(token)
    except:
        flash('链接已过期!')
    form = ResetPassword()
    if form.validate_on_submit():
        user = User.query.filter_by(email=email).first()
        user.password = form.password.data
        user.set_password(form.data['password'])
        user.updated_at = datetime.datetime.now()
        db.session.add(user)
        db.session.commit()
        logout_user()
        flash('密码修改成功,请重新登录!')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', form=form, token=token,
                           title='重置密码')
