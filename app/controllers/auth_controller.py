"""
app.controllers.auth_controller
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

认证与账户管理控制器：
1. 登录/登出与鉴权状态检查
2. 当前用户信息读取
3. 管理员初始化与管理员创建用户
4. 当前用户修改密码
"""

from datetime import datetime, timezone, timedelta
import logging
from flask import Blueprint, request, jsonify, current_app, make_response
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from sqlalchemy.exc import IntegrityError

from app.models.models import db, User

# 配置日志
logger = logging.getLogger(__name__)

# 创建蓝图
auth_bp = Blueprint('auth', __name__)

# 登录接口：校验用户名密码，签发 JWT，并返回用户基础信息
@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录"""
    data = request.get_json()
    
    # 验证必要字段
    if not all(k in data for k in ('username', 'password')):
        return jsonify({'status': 'error', 'message': '用户名和密码都是必填项'}), 400
    
    try:
        # 查找用户
        user = User.query.filter_by(username=data['username']).first()
        
        # 验证用户名和密码
        if not user or not user.check_password(data['password']):
            logger.warning(f"登录失败(用户名或密码错误): {data['username']}")
            return jsonify({'status': 'error', 'message': '用户名或密码错误'}), 401
            
        # 验证账户状态
        if not user.is_active:
            logger.warning(f"登录失败(账户已禁用): {data['username']}")
            return jsonify({'status': 'error', 'message': '账户已被禁用，请联系管理员'}), 403
        
        # 更新最后登录时间
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
        
        logger.info(f"用户登录成功: {data['username']}")
        
        # 生成访问令牌（identity 使用 username，后续接口通过 get_jwt_identity 获取）
        access_token = create_access_token(identity=data['username'])
        
        # 创建响应对象
        response = make_response(jsonify({
            'status': 'success',
            'message': '登录成功',
            'access_token': access_token,
            'user': {
                'user_id': user.user_id,
                'id': user.id,
                'username': user.username,
                'nickname': user.nickname,
                'email': user.email,
                'role': user.role
            }
        }))
        
        # 设置 cookie（当前配置允许前端 JS 读取 token，适用于现有前端实现）
        expires = datetime.now(timezone.utc) + timedelta(days=1)  # 24小时过期
        response.set_cookie(
            'access_token', 
            access_token, 
            expires=expires,
            httponly=False,  # 允许JavaScript访问
            secure=False,    # 开发环境不要求HTTPS
            samesite='Lax'   # 适当的安全级别
        )
        
        return response, 200
        
    except Exception as e:
        logger.error(f"登录过程发生错误: {str(e)}")
        return jsonify({'status': 'error', 'message': '登录过程发生错误'}), 500

# 当前用户信息接口：依赖 JWT，从 identity 反查用户并返回资料
@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def get_current_user():
    """获取当前登录用户信息"""
    username = get_jwt_identity()
    
    try:
        user = User.query.filter_by(username=username).first()
        
        if not user:
            logger.warning(f"获取用户信息失败(用户不存在): {username}")
            return jsonify({'status': 'error', 'message': '用户不存在'}), 404
            
        logger.info(f"获取用户信息成功: {username}")
        
        return jsonify({
            'status': 'success',
            'user': {
                'user_id': user.user_id,
                'id': user.id,
                'username': user.username,
                'nickname': user.nickname,
                'email': user.email,
                'role': user.role,
                'last_login_at': user.last_login_at.isoformat() if user.last_login_at else None
            }
        }), 200
        
    except Exception as e:
        logger.error(f"获取用户信息时发生错误: {str(e)}")
        return jsonify({'status': 'error', 'message': '获取用户信息时发生错误'}), 500

# 认证状态检查接口：用于前端判断当前是否已登录
@auth_bp.route('/check-auth', methods=['GET'])
@jwt_required(optional=True)
def check_auth():
    """检查用户认证状态"""
    current_user = get_jwt_identity()
    
    if current_user:
        return jsonify({
            'status': 'success',
            'authenticated': True,
            'username': current_user
        }), 200
    else:
        return jsonify({
            'status': 'success',
            'authenticated': False
        }), 200

# 退出登录接口：JWT 无状态，服务端仅清理 cookie，真正“退出”由客户端丢弃 token 完成
@auth_bp.route('/logout', methods=['POST'])
@jwt_required(optional=True)
def logout():
    """用户退出登录，清理服务器端会话"""
    # 注意：由于JWT是无状态的，服务器端无法真正"失效"令牌
    # 这里只是返回一个成功响应，客户端负责清理本地存储的令牌
    
    # 记录退出日志
    current_user = get_jwt_identity()
    if current_user:
        logger.info(f"用户退出登录: {current_user}")
    
    # 创建响应对象并清除cookie
    response = make_response(jsonify({
        'status': 'success',
        'message': '退出登录成功'
    }))
    
    # 清除cookie
    response.set_cookie('access_token', '', expires=0)
    
    return response, 200

# 管理员接口 - 创建初始管理员账户
@auth_bp.route('/init-admin', methods=['POST'])
def init_admin():
    """初始化管理员账户（仅在无管理员时可用）"""
    # 检查是否已存在管理员
    admin_exists = User.query.filter_by(role='admin').first()
    if admin_exists:
        return jsonify({'status': 'error', 'message': '管理员账户已存在，无法创建初始管理员'}), 403
    
    data = request.get_json()
    
    # 验证必要字段
    if not all(k in data for k in ('username', 'email', 'password')):
        return jsonify({'status': 'error', 'message': '用户名、邮箱和密码都是必填项'}), 400
    
    try:
        # 创建管理员用户
        admin_user = User(
            username=data['username'],
            email=data['email'],
            role='admin'
        )
        admin_user.set_password(data['password'])
        
        # 保存到数据库
        db.session.add(admin_user)
        db.session.commit()
        
        logger.info(f"管理员账户创建成功: {data['username']}")
        
        return jsonify({
            'status': 'success',
            'message': '管理员账户创建成功'
        }), 201
        
    except IntegrityError:
        db.session.rollback()
        logger.warning(f"管理员账户创建失败(用户名或邮箱已存在): {data['username']}")
        return jsonify({'status': 'error', 'message': '用户名或邮箱已存在'}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(f"管理员账户创建错误: {str(e)}")
        return jsonify({'status': 'error', 'message': f'创建过程发生错误: {str(e)}'}), 500

# 管理员创建用户接口：要求当前登录用户角色为 admin
@auth_bp.route('/create-user', methods=['POST'])
@jwt_required()
def create_user():
    """创建用户账户（仅限管理员）"""
    # 获取当前用户
    username = get_jwt_identity()
    current_user = User.query.filter_by(username=username).first()
    
    # 验证是否管理员
    if not current_user or current_user.role != 'admin':
        return jsonify({'status': 'error', 'message': '只有管理员可以创建用户账户'}), 403
    
    data = request.get_json()
    
    # 验证必要字段
    if not all(k in data for k in ('username', 'email', 'password')):
        return jsonify({'status': 'error', 'message': '用户名、邮箱和密码都是必填项'}), 400
    
    try:
        # 创建新用户
        new_user = User(
            username=data['username'],
            nickname=data.get('nickname'),
            email=data['email'],
            phone=data.get('phone'),
            role=data.get('role', 'user')
        )
        new_user.set_password(data['password'])
        
        # 保存到数据库
        db.session.add(new_user)
        db.session.commit()
        
        logger.info(f"用户账户创建成功: {data['username']} (创建者: {username})")
        
        return jsonify({
            'status': 'success',
            'message': '用户账户创建成功',
            'user': {
                'user_id': new_user.user_id,
                'username': new_user.username,
                'nickname': new_user.nickname,
                'email': new_user.email,
                'phone': new_user.phone,
                'role': new_user.role
            }
        }), 201
        
    except IntegrityError:
        db.session.rollback()
        logger.warning(f"用户账户创建失败(用户名或邮箱已存在): {data['username']}")
        return jsonify({'status': 'error', 'message': '用户名或邮箱已存在'}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(f"用户账户创建错误: {str(e)}")
        return jsonify({'status': 'error', 'message': '创建过程发生错误'}), 500

# 修改密码接口：校验旧密码后写入新密码哈希
@auth_bp.route('/change-password', methods=['POST'])
@jwt_required()
def change_password():
    """修改当前用户密码"""
    data = request.get_json()

    if not all(k in data for k in ('old_password', 'new_password')):
        return jsonify({'status': 'error', 'message': '旧密码和新密码都是必填项'}), 400

    username = get_jwt_identity()
    user = User.query.filter_by(username=username).first()

    if not user or not user.check_password(data['old_password']):
        return jsonify({'status': 'error', 'message': '旧密码不正确'}), 400

    user.set_password(data['new_password'])
    db.session.commit()

    logger.info(f"用户 {username} 修改了密码")

    return jsonify({'status': 'success', 'message': '密码修改成功'})
