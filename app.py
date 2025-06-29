import os
import sys
import logging
import uuid
from datetime import datetime, date, timedelta
from flask import (
    Flask, jsonify, request, render_template, redirect, url_for, flash,
    make_response, has_request_context, g, send_from_directory, session, Response, current_app
)
from flask_cors import CORS
from werkzeug.security import generate_password_hash
from itsdangerous import URLSafeTimedSerializer
from dotenv import load_dotenv
import atexit
from functools import wraps
from mailersend_email import init_email_config
from scheduler_setup import init_scheduler
from models import create_user, get_user_by_email, get_user, get_financial_health, get_budgets, get_bills, get_net_worth, get_emergency_funds, get_learning_progress, get_quiz_results, to_dict_financial_health, to_dict_budget, to_dict_bill, to_dict_net_worth, to_dict_emergency_fund, to_dict_learning_progress, to_dict_quiz_result, initialize_database
from utils import trans_function, is_valid_email, get_mongo_db, close_mongo_db, get_limiter, get_mail, requires_role, check_coin_balance, is_admin, login_manager, flask_session, csrf, babel, compress
from session_utils import create_anonymous_session
from translations import trans, get_translations, get_all_translations, get_module_translations
from flask_login import login_required, current_user
from flask_wtf.csrf import CSRFError
from jinja2.exceptions import TemplateNotFound
import time
from pymongo import MongoClient
import certifi
from pymongo.errors import ConfigurationError

# Load environment variables
load_dotenv()

# Set up logging
root_logger = logging.getLogger('ficore_app')
root_logger.setLevel(logging.INFO)

class SessionFormatter(logging.Formatter):
    def format(self, record):
        record.session_id = getattr(record, 'session_id', 'no-session-id')
        return super().format(record)

class SessionAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs['extra'] = kwargs.get('extra', {})
        session_id = 'no-session-id'
        try:
            if has_request_context():
                session_id = session.get('sid', 'no-session-id')
            else:
                session_id = 'no-request-context'
        except Exception as e:
            session_id = 'session-error'
            kwargs['extra']['session_error'] = str(e)
        kwargs['extra']['session_id'] = session_id
        return msg, kwargs

logger = SessionAdapter(root_logger, {})

# Decorators
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('users_bp.login'))
        if not is_admin():
            flash(trans('general_no_permission', default='You do not have permission to access this page.'), 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def custom_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated or session.get('is_anonymous', False):
            return f(*args, **kwargs)
        return redirect(url_for('users_bp.login', next=request.url))
    return decorated_function

def ensure_session_id(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            if 'sid' not in session:
                if not current_user.is_authenticated:
                    create_anonymous_session()
                else:
                    session['sid'] = session.sid
                    session['is_anonymous'] = False
                    logger.info(f"New session ID generated for authenticated user: {session['sid']}")
        except Exception as e:
            logger.error(f"Session operation failed: {str(e)}")
        return f(*args, **kwargs)
    return decorated_function

def setup_logging(app):
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(SessionFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s [session: %(session_id)s]'))
    root_logger.handlers = []
    root_logger.addHandler(handler)
    
    flask_logger = logging.getLogger('flask')
    werkzeug_logger = logging.getLogger('werkzeug')
    flask_logger.handlers = []
    werkzeug_logger.handlers = []
    flask_logger.addHandler(handler)
    werkzeug_logger.addHandler(handler)
    flask_logger.setLevel(logging.INFO)
    werkzeug_logger.setLevel(logging.INFO)
    
    logger.info("Logging setup complete with StreamHandler for ficore_app, flask, and werkzeug")

def check_mongodb_connection(app):
    with app.app_context():
        try:
            db = get_mongo_db()
            db.command('ping')
            logger.info("MongoDB connection verified with ping")
            return True
        except Exception as e:
            logger.error(f"MongoDB connection failed: {str(e)}")
            return False

def setup_session(app):
    with app.app_context():
        try:
            db = get_mongo_db()
            if not check_mongodb_connection(app):
                logger.error("MongoDB client is not available, falling back to filesystem session")
                app.config['SESSION_TYPE'] = 'filesystem'
                flask_session.init_app(app)
                logger.info("Session configured with filesystem fallback")
                return
            app.config['SESSION_TYPE'] = 'mongodb'
            app.config['SESSION_MONGODB'] = db
            app.config['SESSION_MONGODB_DB'] = 'ficodb'
            app.config['SESSION_MONGODB_COLLECT'] = 'sessions'
            app.config['SESSION_PERMANENT'] = True
            app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
            app.config['SESSION_USE_SIGNER'] = True
            app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
            app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV', 'development') == 'production'
            app.config['SESSION_COOKIE_HTTPONLY'] = True
            app.config['SESSION_COOKIE_NAME'] = 'ficore_session'
            flask_session.init_app(app)
            logger.info(f"Session configured: type={app.config['SESSION_TYPE']}, db={app.config['SESSION_MONGODB_DB']}, collection={app.config['SESSION_MONGODB_COLLECT']}")
        except Exception as e:
            logger.error(f"Failed to configure session with MongoDB: {str(e)}", exc_info=True)
            app.config['SESSION_TYPE'] = 'filesystem'
            flask_session.init_app(app)
            logger.info("Session configured with filesystem fallback due to MongoDB error")

class User:
    def __init__(self, id, email, display_name=None, role='personal'):
        self.id = id
        self.email = email
        self.display_name = display_name or id
        self.role = role

    def get(self, key, default=None):
        with current_app.app_context():
            user = get_mongo_db().users.find_one({'_id': self.id})
            return user.get(key, default) if user else default

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    CORS(app)
    
    # Load configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    if not app.config['SECRET_KEY']:
        logger.error("SECRET_KEY environment variable is not set")
        raise ValueError("SECRET_KEY must be set in environment variables")
    
    app.config['MONGO_URI'] = os.getenv('MONGO_URI')
    if not app.config['MONGO_URI']:
        logger.error("MONGO_URI environment variable is not set")
        raise ValueError("MONGO_URI must be set in environment variables")
    
    # Store MongoDB client in app config
    try:
        with app.app_context():
            client = MongoClient(app.config['MONGO_URI'], serverSelectionTimeoutMS=5000, 
                               tlsCAFile=os.getenv('MONGO_CA_FILE', None))
            client.admin.command('ping')
            app.config['MONGO_CLIENT'] = client
            logger.info("MongoDB client initialized successfully")
    except ConfigurationError as e:
        logger.error(f"Invalid MONGO_URI format: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"MongoDB connection test failed: {str(e)}")
        raise RuntimeError(f"Failed to connect to MongoDB: {str(e)}")
    
    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')
    app.config['SMTP_SERVER'] = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    app.config['SMTP_PORT'] = int(os.environ.get('SMTP_PORT', 587))
    app.config['SMTP_USERNAME'] = os.environ.get('SMTP_USERNAME')
    app.config['SMTP_PASSWORD'] = os.environ.get('SMTP_PASSWORD')
    app.config['BASE_URL'] = os.environ.get('BASE_URL', 'http://localhost:5000')
    
    if not app.config['GOOGLE_CLIENT_ID'] or not app.config['GOOGLE_CLIENT_SECRET']:
        logger.warning("Google OAuth2 credentials not set")
    if not app.config['SMTP_USERNAME'] or not app.config['SMTP_PASSWORD']:
        logger.warning("SMTP credentials not set")
    
    # Initialize extensions
    setup_logging(app)
    compress.init_app(app)
    csrf.init_app(app)
    mail = get_mail(app)
    limiter = get_limiter(app)
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    babel.init_app(app)
    
    # Initialize MongoDB and scheduler within app context
    with app.app_context():
        db = get_mongo_db()
        try:
            scheduler = init_scheduler(app, db)
            app.config['SCHEDULER'] = scheduler
            logger.info("Scheduler initialized successfully")
            def shutdown_scheduler():
                try:
                    if scheduler and scheduler.running:
                        scheduler.shutdown(wait=True)
                        logger.info("Scheduler shutdown successfully")
                except Exception as e:
                    logger.error(f"Error shutting down scheduler: {str(e)}", exc_info=True)
            atexit.register(shutdown_scheduler)
        except Exception as e:
            logger.error(f"Failed to initialize scheduler: {str(e)}", exc_info=True)
    
    setup_session(app)
    
    # Flask-Babel locale selector
    def get_locale():
        return session.get('lang', request.accept_languages.best_match(['en', 'ha'], default='en'))
    babel.locale_selector = get_locale
    
    # Configure Flask-Login
    login_manager.init_app(app)
    login_manager.login_view = 'users_bp.login'
    login_manager.login_message = trans('general_login_required', default='Please log in to access this page.')
    login_manager.login_message_category = 'info'
    
    @login_manager.user_loader
    def load_user(user_id):
        try:
            with app.app_context():
                user = get_user(get_mongo_db(), user_id)
                if user is None:
                    logger.warning(f"No user found for ID: {user_id}")
                else:
                    logger.info(f"User loaded: {user.username if hasattr(user, 'username') else user_id}")
                return user
        except Exception as e:
            logger.error(f"Error loading user {user_id}: {str(e)}", exc_info=True)
            return None
    
    # Initialize database and collections
    with app.app_context():
        try:
            db = get_mongo_db()
            initialize_database(app)
            # Initialize personal finance collections
            personal_finance_collections = [
                'budgets', 'bills', 'emergency_funds', 'financial_health_scores', 
                'net_worth_data', 'quiz_responses', 'learning_materials'
            ]
            for collection_name in personal_finance_collections:
                if collection_name not in db.list_collection_names():
                    db.create_collection(collection_name)
                    logger.info(f"Created personal finance collection: {collection_name}")
            
            # Create indexes
            try:
                db.bills.create_index([('user_id', 1), ('due_date', 1)])
                db.bills.create_index([('session_id', 1), ('due_date', 1)])
                db.bills.create_index([('status', 1)])
                db.budgets.create_index([('user_id', 1), ('created_at', -1)])
                db.budgets.create_index([('session_id', 1), ('created_at', -1)])
                db.emergency_funds.create_index([('user_id', 1), ('created_at', -1)])
                db.emergency_funds.create_index([('session_id', 1), ('created_at', -1)])
                db.financial_health_scores.create_index([('user_id', 1), ('created_at', -1)])
                db.financial_health_scores.create_index([('session_id', 1), ('created_at', -1)])
                db.net_worth_data.create_index([('user_id', 1), ('created_at', -1)])
                db.net_worth_data.create_index([('session_id', 1), ('created_at', -1)])
                db.quiz_responses.create_index([('user_id', 1), ('created_at', -1)])
                db.quiz_responses.create_index([('session_id', 1), ('created_at', -1)])
                db.learning_materials.create_index([('user_id', 1), ('course_id', 1)])
                db.learning_materials.create_index([('session_id', 1), ('course_id', 1)])
                logger.info("Created indexes for personal finance collections")
            except Exception as e:
                logger.warning(f"Some indexes may already exist: {str(e)}")
            
            # Initialize taxation collections
            if 'tax_rates' not in db.list_collection_names():
                db.create_collection('tax_rates')
            if 'payment_locations' not in db.list_collection_names():
                db.create_collection('payment_locations')
            if 'tax_reminders' not in db.list_collection_names():
                db.create_collection('tax_reminders')
            if db.tax_rates.count_documents({}) == 0:
                sample_rates = [
                    {'role': 'personal', 'min_income': 0, 'max_income': 100000, 'rate': 0.1, 'description': '10% tax for income up to 100,000'},
                    {'role': 'trader', 'min_income': 0, 'max_income': 500000, 'rate': 0.15, 'description': '15% tax for turnover up to 500,000'},
                ]
                db.tax_rates.insert_many(sample_rates)
            if db.payment_locations.count_documents({}) == 0:
                sample_locations = [
                    {'name': 'Gombe State IRS Office', 'address': '123 Tax Street, Gombe', 'contact': '+234 123 456 7890', 'coordinates': {'lat': 10.2896, 'lng': 11.1673}},
                ]
                db.payment_locations.insert_many(sample_locations)
            admin_email = os.environ.get('ADMIN_EMAIL', 'ficore@gmail.com')
            admin_password = os.environ.get('ADMIN_PASSWORD', 'Admin123!')
            admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
            admin_user = get_user_by_email(db, admin_email)
            if not admin_user:
                user_data = {
                    'username': admin_username.lower(),
                    'email': admin_email.lower(),
                    'password_hash': generate_password_hash(admin_password),
                    'is_admin': True,
                    'role': 'admin',
                    'created_at': datetime.utcnow(),
                    'lang': 'en',
                    'setup_complete': True,
                    'display_name': admin_username
                }
                create_user(db, user_data)
                logger.info(f"Admin user created with email: {admin_email}")
            else:
                logger.info(f"Admin user already exists with email: {admin_email}")
        except Exception as e:
            logger.error(f"Error creating collections or initializing database: {str(e)}")
            raise
    
    # Register blueprints with correct template folders
    from users.routes import users_bp
    from agents.routes import agents_bp
    from common_features.routes import common_bp
    from common_features.taxation import taxation_bp
    from creditors.routes import creditors_bp
    from dashboard.routes import dashboard_bp
    from debtors.routes import debtors_bp
    from inventory.routes import inventory_bp
    from payments.routes import payments_bp
    from receipts.routes import receipts_bp
    from reports.routes import reports_bp
    from settings.routes import settings_bp
    from personal.bill import bill_bp
    from personal.budget import budget_bp
    from personal.emergency_fund import emergency_fund_bp
    from personal.financial_health import financial_health_bp
    from personal.learning_hub import learning_hub_bp
    from personal.net_worth import net_worth_bp
    from personal.quiz import quiz_bp
    
    app.register_blueprint(users_bp, url_prefix='/users', template_folder='templates/users')
    logger.info("Registered users blueprint with template_folder='templates/users'")
    app.register_blueprint(agents_bp, url_prefix='/agents', template_folder='templates/agents')
    logger.info("Registered agents blueprint with template_folder='templates/agents'")
    app.register_blueprint(common_bp, template_folder='templates/common_features')
    logger.info("Registered common blueprint with template_folder='templates/common_features'")
    app.register_blueprint(taxation_bp, template_folder='templates/common_features')
    logger.info("Registered taxation blueprint with template_folder='templates/common_features'")
    try:
        from coins.routes import coins_bp
        app.register_blueprint(coins_bp, url_prefix='/coins', template_folder='templates/coins')
        logger.info("Registered coins blueprint with template_folder='templates/coins'")
    except Exception as e:
        logger.warning(f"Could not import coins blueprint: {str(e)}")
    app.register_blueprint(creditors_bp, url_prefix='/creditors', template_folder='templates/creditors')
    logger.info("Registered creditors blueprint with template_folder='templates/creditors'")
    app.register_blueprint(dashboard_bp, url_prefix='/dashboard', template_folder='templates/dashboard')
    logger.info("Registered dashboard blueprint with template_folder='templates/dashboard'")
    app.register_blueprint(debtors_bp, url_prefix='/debtors', template_folder='templates/debtors')
    logger.info("Registered debtors blueprint with template_folder='templates/debtors'")
    app.register_blueprint(inventory_bp, url_prefix='/inventory', template_folder='templates/inventory')
    logger.info("Registered inventory blueprint with template_folder='templates/inventory'")
    app.register_blueprint(payments_bp, url_prefix='/payments', template_folder='templates/payments')
    logger.info("Registered payments blueprint with template_folder='templates/payments'")
    app.register_blueprint(receipts_bp, url_prefix='/receipts', template_folder='templates/receipts')
    logger.info("Registered receipts blueprint with template_folder='templates/receipts'")
    app.register_blueprint(reports_bp, url_prefix='/reports', template_folder='templates/reports')
    logger.info("Registered reports blueprint with template_folder='templates/reports'")
    app.register_blueprint(settings_bp, url_prefix='/settings', template_folder='templates/settings')
    logger.info("Registered settings blueprint with template_folder='templates/settings'")
    try:
        from admin.routes import admin_bp
        app.register_blueprint(admin_bp, url_prefix='/admin', template_folder='templates/admin')
        logger.info("Registered admin blueprint with template_folder='templates/admin'")
    except Exception as e:
        logger.warning(f"Could not import admin blueprint: {str(e)}")
    app.register_blueprint(bill_bp, url_prefix='/personal', template_folder='templates/personal')
    app.register_blueprint(budget_bp, url_prefix='/personal', template_folder='templates/personal')
    app.register_blueprint(emergency_fund_bp, url_prefix='/personal', template_folder='templates/personal')
    app.register_blueprint(financial_health_bp, url_prefix='/personal', template_folder='templates/personal')
    app.register_blueprint(learning_hub_bp, url_prefix='/personal', template_folder='templates/personal')
    app.register_blueprint(net_worth_bp, url_prefix='/personal', template_folder='templates/personal')
    app.register_blueprint(quiz_bp, url_prefix='/personal', template_folder='templates/personal')
    logger.info("Registered all personal finance blueprints with template_folder='templates/personal'")
    
    # Jinja2 globals and filters
    app.jinja_env.globals.update(
        FACEBOOK_URL=app.config.get('FACEBOOK_URL', 'https://www.facebook.com'),
        TWITTER_URL=app.config.get('TWITTER_URL', 'https://www.twitter.com'),
        LINKEDIN_URL=app.config.get('LINKEDIN_URL', 'https://www.linkedin.com'),
        FEEDBACK_FORM_URL=app.config.get('FEEDBACK_FORM_URL', '#'),
        WAITLIST_FORM_URL=app.config.get('WAITLIST_FORM_URL', '#'),
        CONSULTANCY_FORM_URL=app.config.get('CONSULTANCY_FORM_URL', '#'),
        trans=trans,
        trans_function=trans_function,
        get_translations=get_translations
    )
    
    @app.template_filter('safe_nav')
    def safe_nav(value):
        try:
            return value
        except Exception as e:
            logger.error(f"Navigation rendering error: {str(e)}", exc_info=True)
            return ''
    
    @app.template_filter('format_number')
    def format_number(value):
        try:
            if isinstance(value, (int, float)):
                return f"{float(value):,.2f}"
            return str(value)
        except (ValueError, TypeError) as e:
            logger.warning(f"Error formatting number {value}: {str(e)}")
            return str(value)
    
    @app.template_filter('format_currency')
    def format_currency(value):
        try:
            value = float(value)
            locale = session.get('lang', 'en')
            symbol = '₦'
            if value.is_integer():
                return f"{symbol}{int(value):,}"
            return f"{symbol}{value:,.2f}"
        except (TypeError, ValueError) as e:
            logger.warning(f"Error formatting currency {value}: {str(e)}")
            return str(value)
    
    @app.template_filter('format_datetime')
    def format_datetime(value):
        try:
            locale = session.get('lang', 'en')
            format_str = '%B %d, %Y, %I:%M %p' if locale == 'en' else '%d %B %Y, %I:%M %p'
            if isinstance(value, datetime):
                return value.strftime(format_str)
            elif isinstance(value, date):
                return value.strftime('%B %d, %Y' if locale == 'en' else '%d %B %Y')
            elif isinstance(value, str):
                parsed = datetime.strptime(value, '%Y-%m-%d')
                return parsed.strftime(format_str)
            return str(value)
        except Exception as e:
            logger.warning(f"Error formatting datetime {value}: {str(e)}")
            return str(value)
    
    @app.template_filter('format_date')
    def format_date(value):
        try:
            locale = session.get('lang', 'en')
            format_str = '%Y-%m-%d' if locale == 'en' else '%d-%m-%Y'
            if isinstance(value, datetime):
                return value.strftime(format_str)
            elif isinstance(value, date):
                return value.strftime(format_str)
            elif isinstance(value, str):
                parsed = datetime.strptime(value, '%Y-%m-%d').date()
                return parsed.strftime(format_str)
            return str(value)
        except Exception as e:
            logger.warning(f"Error formatting date {value}: {str(e)}")
            return str(value)
    
    @app.template_filter('trans')
    def trans_filter(key, **kwargs):
        lang = session.get('lang', 'en')
        translation = trans(key, lang=lang, **kwargs)
        if translation == key:
            logger.warning(f"Missing translation for key='{key}' in lang='{lang}'")
            return key
        return translation
    
    @app.context_processor
    def inject_globals():
        lang = session.get('lang', 'en')
        def context_trans(key, **kwargs):
            used_lang = kwargs.pop('lang', lang)
            return trans(
                key,
                lang=used_lang,
                logger=g.get('logger', logger) if has_request_context() else logger,
                **kwargs
            )
        return {
            'google_client_id': app.config.get('GOOGLE_CLIENT_ID', ''),
            'trans': context_trans,
            'get_translations': get_translations,
            'current_year': datetime.now().year,
            'LINKEDIN_URL': app.config.get('LINKEDIN_URL', '#'),
            'TWITTER_URL': app.config.get('TWITTER_URL', '#'),
            'FACEBOOK_URL': app.config.get('FACEBOOK_URL', '#'),
            'FEEDBACK_FORM_URL': app.config.get('FEEDBACK_FORM_URL', '#'),
            'WAITLIST_FORM_URL': app.config.get('WAITLIST_FORM_URL', '#'),
            'CONSULTANCY_FORM_URL': app.config.get('CONSULTANCY_FORM_URL', '#'),
            'current_lang': lang,
            'current_user': current_user if has_request_context() else None,
            'csrf_token': csrf.generate_csrf if has_request_context() else lambda: '',
            'available_languages': [
                {'code': 'en', 'name': trans('general_english', lang=lang)},
                {'code': 'ha', 'name': trans('general_hausa', lang=lang)}
            ]
        }
    
    # Security headers
    @app.after_request
    def add_security_headers(response):
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://code.jquery.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com;"
        )
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response
    
    # Language switching route
    @app.route('/change-language', methods=['POST'])
    def change_language():
        try:
            data = request.get_json()
            new_lang = data.get('language', 'en')
            
            if new_lang in ['en', 'ha']:
                session['lang'] = new_lang
                with app.app_context():
                    if current_user.is_authenticated:
                        try:
                            get_mongo_db().users.update_one(
                                {'_id': current_user.id}, 
                                {'$set': {'language': new_lang}}
                            )
                        except Exception as e:
                            logger.warning(f"Could not update user language preference: {str(e)}")
                
                logger.info(f"Language changed to {new_lang}", 
                           extra={'session_id': session.get('sid', 'no-session-id')})
                
                return jsonify({
                    'success': True, 
                    'message': trans('general_language_changed', lang=new_lang)
                })
            else:
                return jsonify({
                    'success': False, 
                    'message': trans('general_invalid_language')
                }), 400
                
        except Exception as e:
            logger.error(f"Error changing language: {str(e)}", 
                        extra={'session_id': session.get('sid', 'no-session-id')})
            return jsonify({
                'success': False, 
                'message': trans('general_error')
            }), 500
    
    # Routes
    @app.route('/', methods=['GET', 'HEAD'])
    def index():
        lang = session.get('lang', 'en')
        logger.info(f"Serving index page, authenticated: {current_user.is_authenticated}, user: {current_user.username if current_user.is_authenticated and hasattr(current_user, 'username') else 'None'}")
        if request.method == 'HEAD':
            return '', 200
        if current_user.is_authenticated:
            if current_user.role == 'agent':
                return redirect(url_for('agents_bp.dashboard'))
            elif current_user.role == 'trader':
                return redirect(url_for('dashboard_bp.index'))
            elif current_user.role == 'admin':
                try:
                    return redirect(url_for('admin_bp.dashboard'))
                except:
                    return redirect(url_for('dashboard_bp.index'))
            elif current_user.role == 'personal':
                return redirect(url_for('dashboard_bp.index'))
            else:
                try:
                    return render_template('general/home.html', t=trans, lang=lang)
                except TemplateNotFound as e:
                    logger.error(f"Template not found: {str(e)}", exc_info=True)
                    return render_template('personal/GENERAL/error.html', t=trans, lang=lang, error=str(e)), 404
        try:
            courses = app.config.get('COURSES', [])
            logger.info(f"Retrieved {len(courses)} courses")
            return render_template(
                'personal/GENERAL/index.html',
                t=trans,
                courses=courses,
                lang=lang,
                sample_courses=courses,
                title=trans('general_welcome', lang=lang)
            )
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            flash(trans('general_error', default='Template not found'), 'danger')
            return render_template('personal/GENERAL/error.html', t=trans, lang=lang, error=str(e)), 404
        except Exception as e:
            logger.error(f"Error in index route: {str(e)}", exc_info=True)
            flash(trans('general_error', default='An error occurred'), 'danger')
            try:
                return render_template('errors/500.html', t=trans, lang=lang, error=str(e)), 500
            except TemplateNotFound as e:
                logger.error(f"Template not found: {str(e)}", exc_info=True)
                return render_template('personal/GENERAL/error.html', t=trans, lang=lang, error=str(e)), 500
    
    @app.route('/general_dashboard')
    @ensure_session_id
    def general_dashboard():
        logger.info(f"Redirecting to unified dashboard for {'authenticated' if current_user.is_authenticated else 'anonymous'} user")
        return redirect(url_for('dashboard_bp.index'))
    
    @app.route('/about')
    def about():
        lang = session.get('lang', 'en')
        logger.info("Serving about page")
        try:
            return render_template('general/about.html', 
                                 t=trans, 
                                 lang=lang,
                                 title=trans('general_about', lang=lang))
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 t=trans, 
                                 lang=lang,
                                 error=str(e),
                                 title=trans('general_about', lang=lang)), 404
    
    @app.route('/contact')
    def contact():
        lang = session.get('lang', 'en')
        try:
            return render_template('general/contact.html', 
                                 t=trans, 
                                 lang=lang,
                                 title=trans('general_contact', lang=lang))
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 t=trans, 
                                 lang=lang,
                                 error=str(e),
                                 title=trans('general_contact', lang=lang)), 404
    
    @app.route('/privacy')
    def privacy():
        lang = session.get('lang', 'en')
        try:
            return render_template('general/privacy.html', 
                                 t=trans, 
                                 lang=lang,
                                 title=trans('general_privacy', lang=lang))
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 t=trans, 
                                 lang=lang,
                                 error=str(e),
                                 title=trans('general_privacy', lang=lang)), 404
    
    @app.route('/terms')
    def terms():
        lang = session.get('lang', 'en')
        try:
            return render_template('general/terms.html', 
                                 t=trans, 
                                 lang=lang,
                                 title=trans('general_terms', lang=lang))
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 t=trans, 
                                 lang=lang,
                                 error=str(e),
                                 title=trans('general_terms', lang=lang)), 404
    
    @app.route('/health')
    def health():
        logger.info("Health check")
        status = {"status": "healthy"}
        try:
            with app.app_context():
                get_mongo_db().command('ping')
            return jsonify(status), 200
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}", exc_info=True)
            status["status"] = "unhealthy"
            status["details"] = str(e)
            return jsonify(status), 500
    
    @app.route('/api/translations/<lang>')
    def get_translations_api(lang):
        try:
            if lang not in ['en', 'ha']:
                return jsonify({'error': trans('general_invalid_language')}), 400
            
            all_translations = get_all_translations()
            result = {}
            for module_name, module_translations in all_translations.items():
                if lang in module_translations:
                    result.update(module_translations[lang])
            
            return jsonify({'translations': result})
        except Exception as e:
            logger.error(f"API translations error: {str(e)}", 
                        extra={'session_id': session.get('sid', 'no-session-id')})
            return jsonify({'error': trans('general_error')}), 500
    
    @app.route('/api/translate')
    def api_translate():
        try:
            key = request.args.get('key')
            lang = request.args.get('lang', session.get('lang', 'en'))
            
            if not key:
                return jsonify({'error': trans('general_missing_key')}), 400
            
            translation = trans(key, lang=lang)
            return jsonify({'key': key, 'translation': translation, 'lang': lang})
        except Exception as e:
            logger.error(f"API translate error: {str(e)}", 
                        extra={'session_id': session.get('sid', 'no-session-id')})
            return jsonify({'error': trans('general_error')}), 500
    
    @app.route('/set_language/<lang>')
    def set_language(lang):
        valid_langs = ['en', 'ha']
        new_lang = lang if lang in valid_langs else 'en'
        try:
            session['lang'] = new_lang
            with app.app_context():
                if current_user.is_authenticated:
                    get_mongo_db().users.update_one({'_id': current_user.id}, {'$set': {'language': new_lang}})
            logger.info(f"Language set to {new_lang}")
            flash(trans('general_language_changed', default='Language updated successfully'), 'success')
        except Exception as e:
            logger.error(f"Session operation failed: {str(e)}")
            flash(trans('general_invalid_language', default='Invalid language'), 'danger')
        return redirect(request.referrer or url_for('index'))
    
    @app.route('/acknowledge_consent', methods=['POST'])
    def acknowledge_consent():
        if request.method != 'POST':
            logger.warning(f"Invalid method {request.method} for consent acknowledgement")
            return '', 400
        try:
            session['consent_acknowledged'] = {
                'status': True,
                'timestamp': datetime.utcnow().isoformat(),
                'ip': request.remote_addr,
                'user_agent': request.headers.get('User-Agent')
            }
            logger.info(f"Consent acknowledged for session {session.get('sid', 'no-session-id')} from IP {request.remote_addr}")
        except Exception as e:
            logger.error(f"Session operation failed: {str(e)}")
        response = make_response('', 204)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    
    # Existing accounting API routes
    @app.route('/api/debt-summary')
    @login_required
    def debt_summary():
        try:
            with app.app_context():
                db = get_mongo_db()
                user_id = current_user.id
                creditors_pipeline = [
                    {'$match': {'user_id': user_id, 'type': 'creditor'}},
                    {'$group': {'_id': None, 'total': {'$sum': '$amount_owed'}}}
                ]
                creditors_result = list(db.records.aggregate(creditors_pipeline))
                total_i_owe = creditors_result[0]['total'] if creditors_result else 0
                debtors_pipeline = [
                    {'$match': {'user_id': user_id, 'type': 'debtor'}},
                    {'$group': {'_id': None, 'total': {'$sum': '$amount_owed'}}}
                ]
                debtors_result = list(db.records.aggregate(debtors_pipeline))
                total_i_am_owed = debtors_result[0]['total'] if debtors_result else 0
                return jsonify({
                    'totalIOwe': total_i_owe,
                    'totalIAmOwed': total_i_am_owed
                })
        except Exception as e:
            logger.error(f"Error fetching debt summary: {str(e)}")
            return jsonify({'error': 'Failed to fetch debt summary'}), 500
    
    @app.route('/api/cashflow-summary')
    @login_required
    def cashflow_summary():
        try:
            with app.app_context():
                db = get_mongo_db()
                user_id = current_user.id
                now = datetime.utcnow()
                month_start = datetime(now.year, now.month, 1)
                next_month = month_start.replace(month=month_start.month + 1) if month_start.month < 12 else month_start.replace(year=month_start.year + 1, month=1)
                receipts_pipeline = [
                    {'$match': {'user_id': user_id, 'type': 'receipt', 'created_at': {'$gte': month_start, '$lt': next_month}}},
                    {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
                ]
                receipts_result = list(db.cashflows.aggregate(receipts_pipeline))
                total_receipts = receipts_result[0]['total'] if receipts_result else 0
                payments_pipeline = [
                    {'$match': {'user_id': user_id, 'type': 'payment', 'created_at': {'$gte': month_start, '$lt': next_month}}},
                    {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
                ]
                payments_result = list(db.cashflows.aggregate(payments_pipeline))
                total_payments = payments_result[0]['total'] if payments_result else 0
                net_cashflow = total_receipts - total_payments
                return jsonify({
                    'netCashflow': net_cashflow,
                    'totalReceipts': total_receipts,
                    'totalPayments': total_payments
                })
        except Exception as e:
            logger.error(f"Error fetching cashflow summary: {str(e)}")
            return jsonify({'error': 'Failed to fetch cashflow summary'}), 500
    
    @app.route('/api/inventory-summary')
    @login_required
    def inventory_summary():
        try:
            with app.app_context():
                db = get_mongo_db()
                user_id = current_user.id
                pipeline = [
                    {'$match': {'user_id': user_id}},
                    {'$addFields': {
                        'item_value': {
                            '$multiply': [
                                '$qty',
                                {'$ifNull': ['$buying_price', 0]}
                            ]
                        }
                    }},
                    {'$group': {'_id': None, 'totalValue': {'$sum': '$item_value'}}}
                ]
                result = list(db.inventory.aggregate(pipeline))
                total_value = result[0]['totalValue'] if result else 0
                return jsonify({
                    'totalValue': total_value
                })
        except Exception as e:
            logger.error(f"Error fetching inventory summary: {str(e)}")
            return jsonify({'error': 'Failed to fetch inventory summary'}), 500
    
    @app.route('/api/recent-activity')
    @login_required
    def recent_activity():
        try:
            with app.app_context():
                db = get_mongo_db()
                user_id = current_user.id
                activities = []
                recent_records = list(db.records.find(
                    {'user_id': user_id}
                ).sort('created_at', -1).limit(3))
                for record in recent_records:
                    activity_type = 'debt_added'
                    description = f"Added {record['type']}: {record['name']}"
                    activities.append({
                        'type': activity_type,
                        'description': description,
                        'amount': record['amount_owed'],
                        'timestamp': record['created_at']
                    })
                recent_cashflows = list(db.cashflows.find(
                    {'user_id': user_id}
                ).sort('created_at', -1).limit(3))
                for cashflow in recent_cashflows:
                    activity_type = 'money_in' if cashflow['type'] == 'receipt' else 'money_out'
                    description = f"{'Received' if cashflow['type'] == 'receipt' else 'Paid'} {cashflow['party_name']}"
                    activities.append({
                        'type': activity_type,
                        'description': description,
                        'amount': cashflow['amount'],
                        'timestamp': cashflow['created_at']
                    })
                activities.sort(key=lambda x: x['timestamp'], reverse=True)
                activities = activities[:5]
                for activity in activities:
                    activity['timestamp'] = activity['timestamp'].isoformat()
                return jsonify(activities)
        except Exception as e:
            logger.error(f"Error fetching recent activity: {str(e)}")
            return jsonify({'error': 'Failed to fetch recent activity'}), 500
    
    @app.route('/api/notifications/count')
    @login_required
    def notification_count():
        try:
            with app.app_context():
                db = get_mongo_db()
                user_id = current_user.id
                count = db.reminder_logs.count_documents({
                    'user_id': user_id,
                    'read_status': False
                })
                return jsonify({'count': count})
        except Exception as e:
            logger.error(f"Error fetching notification count: {str(e)}")
            return jsonify({'error': 'Failed to fetch notification count'}), 500
    
    @app.route('/api/notifications')
    @login_required
    def notifications():
        try:
            with app.app_context():
                db = get_mongo_db()
                user_id = current_user.id
                notifications = list(db.reminder_logs.find({
                    'user_id': user_id
                }).sort('sent_at', -1).limit(10))
                notification_ids = [n['notification_id'] for n in notifications if not n.get('read_status', False)]
                if notification_ids:
                    db.reminder_logs.update_many(
                        {'notification_id': {'$in': notification_ids}},
                        {'$set': {'read_status': True}}
                    )
                result = [{
                    'id': str(n['notification_id']),
                    'message': n['message'],
                    'type': n['type'],
                    'timestamp': n['sent_at'].isoformat(),
                    'read': n.get('read_status', False)
                } for n in notifications]
                return jsonify(result)
        except Exception as e:
            logger.error(f"Error fetching notifications: {str(e)}")
            return jsonify({'error': 'Failed to fetch notifications'}), 500
    
    @app.route('/feedback', methods=['GET', 'POST'])
    @ensure_session_id
    def feedback():
        lang = session.get('lang', 'en')
        logger.info("Handling feedback")
        tool_options = [
            ['profile', trans('general_profile', default='Profile')],
            ['coins', trans('coins_dashboard', default='Coins')],
            ['debtors', trans('debtors_dashboard', default='Debtors')],
            ['creditors', trans('creditors_dashboard', default='Creditors')],
            ['receipts', trans('receipts_dashboard', default='Receipts')],
            ['payment', trans('payments_dashboard', default='Payments')],
            ['inventory', trans('inventory_dashboard', default='Inventory')],
            ['report', trans('reports_dashboard', default='Reports')],
            ['financial_health', trans('financial_health_calculator', default='Financial Health')],
            ['budget', trans('budget_budget_planner', default='Budget')],
            ['bill', trans('bill_bill_planner', default='Bill')],
            ['net_worth', trans('net_worth_calculator', default='Net Worth')],
            ['emergency_fund', trans('emergency_fund_calculator', default='Emergency Fund')],
            ['learning', trans('learning_hub_courses', default='Learning')],
            ['quiz', trans('quiz_personality_quiz', default='Quiz')]
        ]
        if request.method == 'POST':
            try:
                from models import create_feedback
                tool_name = request.form.get('tool_name')
                rating = request.form.get('rating')
                comment = request.form.get('comment', '').strip()
                valid_tools = [option[0] for option in tool_options]
                if not tool_name or tool_name not in valid_tools:
                    logger.error(f"Invalid feedback tool: {tool_name}")
                    flash(trans('general_invalid_input', default='Please select a valid tool'), 'danger')
                    return render_template('general/feedback.html', t=trans, lang=lang, tool_options=tool_options)
                if not rating or not rating.isdigit() or int(rating) < 1 or int(rating) > 5:
                    logger.error(f"Invalid rating: {rating}")
                    flash(trans('general_invalid_input', default='Please provide a rating between 1 and 5'), 'danger')
                    return render_template('general/feedback.html', t=trans, lang=lang, tool_options=tool_options)
                with app.app_context():
                    if current_user.is_authenticated:
                        from coins.routes import get_user_query
                        query = get_user_query(str(current_user.id))
                        result = get_mongo_db().users.update_one(query, {'$inc': {'coin_balance': -1}})
                        if result.matched_count == 0:
                            raise ValueError(f"No user found for ID {current_user.id}")
                        get_mongo_db().coin_transactions.insert_one({
                            'user_id': str(current_user.id),
                            'amount': -1,
                            'type': 'spend',
                            'ref': f"FEEDBACK_{datetime.utcnow().isoformat()}",
                            'date': datetime.utcnow()
                        })
                    feedback_entry = {
                        'user_id': current_user.id if current_user.is_authenticated else None,
                        'session_id': session.get('sid', 'no-session-id'),
                        'tool_name': tool_name,
                        'rating': int(rating),
                        'comment': comment or None,
                        'timestamp': datetime.utcnow()
                    }
                    create_feedback(get_mongo_db(), feedback_entry)
                    get_mongo_db().audit_logs.insert_one({
                        'admin_id': 'system',
                        'action': 'submit_feedback',
                        'details': {'user_id': str(current_user.id) if current_user.is_authenticated else None, 'tool_name': tool_name},
                        'timestamp': datetime.utcnow()
                    })
                logger.info(f"Feedback submitted: tool={tool_name}, rating={rating}, session={session.get('sid', 'no-session-id')}")
                flash(trans('general_thank_you', default='Thank you for your feedback!'), 'success')
                return redirect(url_for('index'))
            except ValueError as e:
                logger.error(f"User not found: {str(e)}")
                flash(trans('general_error', default='User not found'), 'danger')
            except Exception as e:
                logger.error(f"Error processing feedback: {str(e)}", exc_info=True)
                flash(trans('general_error', default='Error occurred during feedback submission'), 'danger')
                try:
                    return render_template('general/feedback.html', t=trans, lang=lang, tool_options=tool_options), 500
                except TemplateNotFound as e:
                    logger.error(f"Template not found: {str(e)}", exc_info=True)
                    return render_template('personal/GENERAL/error.html', t=trans, lang=lang, error=str(e)), 500
        logger.info("Rendering feedback index template")
        try:
            return render_template('general/feedback.html', 
                                 t=trans, 
                                 lang=lang, 
                                 tool_options=tool_options,
                                 title=trans('general_feedback', lang=lang))
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 t=trans, 
                                 lang=lang, 
                                 error=str(e),
                                 title=trans('general_feedback', lang=lang)), 404
    
    @app.route('/setup', methods=['GET'])
    @limiter.limit("10 per minute")
    def setup_database_route():
        setup_key = request.args.get('key')
        if setup_key != os.getenv('SETUP_KEY', 'setup-secret'):
            try:
                return render_template('errors/403.html', content=trans('general_access_denied', default='Access denied')), 403
            except TemplateNotFound as e:
                logger.error(f"Template not found: {str(e)}", exc_info=True)
                return render_template('personal/GENERAL/error.html', content=trans('general_access_denied', default='Access denied'), error=str(e)), 403
        try:
            with app.app_context():
                initialize_database(app)
            flash(trans('general_success', default='Database setup successful'), 'success')
            return redirect(url_for('index'))
        except Exception as e:
            flash(trans('general_error', default='An error occurred during database setup'), 'danger')
            try:
                return render_template('errors/500.html', content=trans('general_error', default='Internal server error'), error=str(e)), 500
            except TemplateNotFound as e:
                logger.error(f"Template not found: {str(e)}", exc_info=True)
                return render_template('personal/GENERAL/error.html', content=trans('general_error', default='Internal server error'), error=str(e)), 500
    
    @app.route('/static/<path:filename>')
    def static_files(filename):
        response = send_from_directory('static', filename)
        response.headers['Cache-Control'] = 'public, max-age=31536000'
        return response
    
    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(app.static_folder, 'icons/favicon.ico')
    
    @app.route('/service-worker.js')
    def service_worker():
        return app.send_static_file('js/service-worker.js')
    
    @app.route('/manifest.json')
    def manifest():
        return {
            'name': 'FiCore',
            'short_name': 'FiCore',
            'description': 'Manage your finances with ease',
            'theme_color': '#007bff',
            'background_color': '#ffffff',
            'display': 'standalone',
            'scope': '/',
            'start_url': '/',
            'icons': [
                {'src': '/static/icons/android-chrome-192x192.png', 'sizes': '192x192', 'type': 'image/png'},
                {'src': '/static/icons/android-chrome-512x512.png', 'sizes': '512x512', 'type': 'image/png'}
            ]
        }
    
    @app.route('/robots.txt')
    def robots_txt():
        return Response("User-agent: *\nDisallow: /", mimetype='text/plain')
    
    @app.errorhandler(403)
    def forbidden(e):
        lang = session.get('lang', 'en')
        try:
            return render_template('errors/403.html', 
                                 message=trans('general_access_denied', default='Forbidden'), 
                                 t=trans, 
                                 lang=lang,
                                 title=trans('general_access_denied', lang=lang)), 403
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 message=trans('general_access_denied', default='Forbidden'), 
                                 t=trans, 
                                 lang=lang,
                                 error=str(e),
                                 title=trans('general_access_denied', lang=lang)), 403
    
    @app.errorhandler(404)
    def page_not_found(e):
        lang = session.get('lang', 'en')
        logger.error(f"Error 404: {str(e)}")
        try:
            return render_template('errors/404.html', 
                                 message=trans('general_page_not_found', default='Page not found'), 
                                 t=trans, 
                                 lang=lang,
                                 title=trans('general_page_not_found', lang=lang)), 404
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 message=trans('general_page_not_found', default='Page not found'), 
                                 t=trans, 
                                 lang=lang,
                                 error=str(e),
                                 title=trans('general_page_not_found', lang=lang)), 404
    
    @app.errorhandler(500)
    def internal_server_error(e):
        lang = session.get('lang', 'en')
        logger.error(f"Server error: {str(e)}", exc_info=True)
        try:
            return render_template('errors/500.html', 
                                 message=trans('general_error', default='Internal server error'), 
                                 t=trans, 
                                 lang=lang,
                                 title=trans('general_error', lang=lang)), 500
        except TemplateNotFound as e:
            logger.error(f"Template not found: {str(e)}", exc_info=True)
            return render_template('personal/GENERAL/error.html', 
                                 message=trans('general_error', default='Internal server error'), 
                                 t=trans, 
                                 lang=lang,
                                 error=str(e),
                                 title=trans('general_error', lang=lang)), 500
    
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        lang = session.get('lang', 'en')
        logger.error(f"CSRF error: {str(e)}")
        return jsonify({'error': 'CSRF token invalid'}), 400
    
    @app.before_request
    def before_request():
        if request.path.startswith('/static/') or request.path in [
            '/manifest.json', '/service-worker.js', '/favicon.ico', '/robots.txt'
        ]:
            logger.info(f"Skipping session setup for request: {request.path}")
            return
        logger.info(f"Starting before_request for path: {request.path}")
        try:
            if 'sid' not in session:
                session['sid'] = session.sid
                session['is_anonymous'] = not current_user.is_authenticated
                logger.info(f"Session ID set: {session['sid']}, is_anonymous: {session['is_anonymous']}")
            if 'lang' not in session:
                session['lang'] = request.accept_languages.best_match(['en', 'ha'], 'en')
                logger.info(f"Set default language to {session['lang']}")
            
            g.trans = trans
            g.get_translations = get_translations
            g.logger = logger
            
            if current_user.is_authenticated:
                if 'session_id' not in session:
                    session['session_id'] = str(uuid.uuid4())
                with app.app_context():
                    db = get_mongo_db()
                    user = db.users.find_one({'_id': current_user.id})
                    if user and not user.get('setup_complete', False):
                        allowed_endpoints = [
                            'users_bp.personal_setup_wizard',
                            'users_bp.setup_wizard',
                            'users_bp.agent_setup_wizard',
                            'users_bp.logout',
                            'settings_bp.profile',
                            'coins_bp.purchase',
                            'coins_bp.get_balance',
                            'set_language'
                        ]
                        if request.endpoint not in allowed_endpoints:
                            flash(trans('general_setup_required', default='Please complete your profile setup'), 'warning')
                            if current_user.role == 'agent':
                                return redirect(url_for('users_bp.agent_setup_wizard'))
                            elif current_user.role == 'personal':
                                return redirect(url_for('users_bp.personal_setup_wizard'))
                            return redirect(url_for('users_bp.setup_wizard'))
        except Exception as e:
            logger.error(f"Error in before_request: {str(e)}", exc_info=True)

    @app.teardown_request
    def teardown_request(exception=None):
        if hasattr(g, 'mongo_client'):
            close_mongo_db()

    # Development routes
    if app.debug:
        @app.route('/dev/translations')
        def dev_translations():
            all_translations = get_all_translations()
            try:
                return render_template('dev/translations.html', 
                                     translations=all_translations,
                                     title='Translation Debug')
            except TemplateNotFound as e:
                logger.error(f"Template not found: {str(e)}", exc_info=True)
                return render_template('personal/GENERAL/error.html', 
                                     error=str(e), 
                                     title='Translation Debug'), 404
        
        @app.route('/dev/session')
        def dev_session():
            return jsonify(dict(session))
    
    return app

# Create the app instance
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
