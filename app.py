import os
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from flask_login import LoginManager, login_required, current_user, logout_user
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash
import pymongo
from bson import ObjectId
import uuid

# Import utilities and models
from utils import get_mongo_db, requires_role, is_admin
from models import User, log_tool_usage
from translations import trans, set_language
from session_utils import create_anonymous_session

# Import blueprints
from users import users_blueprint
from settings import settings_blueprint
from admin import admin_blueprint
from dashboard import dashboard_bp
from common import common_bp
from taxation import taxation_bp

# Import personal finance blueprints
from personal.bill import bill_bp
from personal.budget import budget_bp
from personal.emergency_fund import emergency_fund_bp
from personal.financial_health import financial_health_bp
from personal.learning_hub import learning_hub_bp
from personal.net_worth import net_worth_bp
from personal.quiz import quiz_bp

# Import business blueprints (if they exist)
try:
    from business.creditors import creditors_bp
    from business.debtors import debtors_bp
    from business.inventory import inventory_bp
    BUSINESS_BLUEPRINTS_AVAILABLE = True
except ImportError:
    BUSINESS_BLUEPRINTS_AVAILABLE = False

def create_app():
    app = Flask(__name__)
    
    # Configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['MONGO_URI'] = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/ficore')
    app.config['WTF_CSRF_TIME_LIMIT'] = None
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    
    # Initialize extensions
    csrf = CSRFProtect(app)
    limiter = Limiter(
        app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"]
    )
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Initialize Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'users_blueprint.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    
    @login_manager.user_loader
    def load_user(user_id):
        try:
            db = get_mongo_db()
            user_data = db.users.find_one({'_id': ObjectId(user_id)})
            if user_data:
                return User(user_data)
        except Exception as e:
            app.logger.error(f"Error loading user {user_id}: {str(e)}")
        return None
    
    # Register blueprints
    app.register_blueprint(users_blueprint)
    app.register_blueprint(settings_blueprint)
    app.register_blueprint(admin_blueprint)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(common_bp)
    app.register_blueprint(taxation_bp)
    
    # Register personal finance blueprints
    app.register_blueprint(bill_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(emergency_fund_bp)
    app.register_blueprint(financial_health_bp)
    app.register_blueprint(learning_hub_bp)
    app.register_blueprint(net_worth_bp)
    app.register_blueprint(quiz_bp)
    
    # Register business blueprints if available
    if BUSINESS_BLUEPRINTS_AVAILABLE:
        app.register_blueprint(creditors_bp)
        app.register_blueprint(debtors_bp)
        app.register_blueprint(inventory_bp)
    
    # Global template variables
    @app.context_processor
    def inject_globals():
        return {
            't': trans,
            'current_lang': session.get('lang', 'en'),
            'LINKEDIN_URL': 'https://linkedin.com/company/ficore-africa',
            'TWITTER_URL': 'https://twitter.com/ficore_africa',
            'FACEBOOK_URL': 'https://facebook.com/ficore.africa',
            'FEEDBACK_FORM_URL': 'https://forms.gle/feedback',
            'WAITLIST_FORM_URL': 'https://forms.gle/waitlist',
            'CONSULTANCY_FORM_URL': 'https://forms.gle/consultancy'
        }
    
    # Main routes
    @app.route('/')
    def index():
        """Main landing page with role-based redirection"""
        if current_user.is_authenticated:
            if current_user.role == 'personal':
                return redirect(url_for('dashboard_bp.index'))
            elif current_user.role == 'business':
                return redirect(url_for('dashboard_bp.index'))
            elif current_user.role == 'admin':
                return redirect(url_for('admin_blueprint.dashboard'))
        
        # For anonymous users, show the main landing page
        lang = session.get('lang', 'en')
        
        # Sample courses data for the landing page
        courses = [
            {
                'id': 'budgeting_101',
                'title_key': 'learning_hub_course_budgeting',
                'title_en': 'Budgeting Basics',
                'is_premium': False
            },
            {
                'id': 'financial_quiz',
                'title_key': 'learning_hub_course_financial_quiz',
                'title_en': 'Financial Knowledge Quiz',
                'is_premium': False
            },
            {
                'id': 'savings_basics',
                'title_key': 'learning_hub_course_savings_basics',
                'title_en': 'Savings Fundamentals',
                'is_premium': False
            }
        ]
        
        return render_template('personal/GENERAL/index.html', 
                             courses=courses, 
                             t=trans, 
                             lang=lang)
    
    @app.route('/about')
    def about():
        """About page"""
        lang = session.get('lang', 'en')
        return render_template('personal/GENERAL/about.html', t=trans, lang=lang)
    
    @app.route('/contact')
    def contact():
        """Contact page"""
        lang = session.get('lang', 'en')
        return render_template('personal/GENERAL/contact.html', t=trans, lang=lang)
    
    @app.route('/feedback', methods=['GET', 'POST'])
    def feedback():
        """Feedback page"""
        lang = session.get('lang', 'en')
        
        if request.method == 'POST':
            try:
                # Handle feedback form submission
                tool_name = request.form.get('tool_name')
                rating = request.form.get('rating')
                comment = request.form.get('comment', '')
                
                if tool_name and rating:
                    # Save feedback to database
                    db = get_mongo_db()
                    feedback_data = {
                        'user_id': current_user.id if current_user.is_authenticated else None,
                        'session_id': session.get('sid', str(uuid.uuid4())),
                        'tool_name': tool_name,
                        'rating': int(rating),
                        'comment': comment,
                        'created_at': datetime.utcnow(),
                        'ip_address': request.remote_addr
                    }
                    db.feedback.insert_one(feedback_data)
                    
                    flash(trans('general_feedback_submitted', default='Thank you for your feedback!', lang=lang), 'success')
                    return redirect(url_for('feedback'))
                else:
                    flash(trans('general_feedback_error', default='Please complete all required fields.', lang=lang), 'danger')
            except Exception as e:
                app.logger.error(f"Error submitting feedback: {str(e)}")
                flash(trans('general_feedback_error', default='Error submitting feedback. Please try again.', lang=lang), 'danger')
        
        # Tool options for feedback form
        tool_options = [
            'bill', 'budget', 'emergency_fund', 'financial_health',
            'learning_hub', 'net_worth', 'quiz', 'general_dashboard'
        ]
        
        return render_template('personal/GENERAL/feedback.html', 
                             tool_options=tool_options,
                             t=trans, 
                             lang=lang)
    
    @app.route('/general_dashboard')
    @login_required
    def general_dashboard():
        """General dashboard for all users"""
        lang = session.get('lang', 'en')
        
        try:
            # Get user's financial data from various tools
            db = get_mongo_db()
            filter_criteria = {'user_id': current_user.id}
            
            # Get latest data from each tool
            data = {
                'financial_health': {},
                'budget': {},
                'bills': {},
                'net_worth': {},
                'emergency_fund': {},
                'quiz': {},
                'learning_progress': {}
            }
            
            # Financial Health Score
            health_record = db.financial_health_scores.find_one(filter_criteria, sort=[('created_at', -1)])
            if health_record:
                data['financial_health'] = {
                    'score': health_record.get('score', 0),
                    'status': health_record.get('status', 'Unknown')
                }
            
            # Budget data
            budget_record = db.budgets.find_one(filter_criteria, sort=[('created_at', -1)])
            if budget_record:
                data['budget'] = {
                    'surplus_deficit': budget_record.get('surplus_deficit', 0),
                    'savings_goal': budget_record.get('savings_goal', 0)
                }
            
            # Bills data
            bills = list(db.bills.find(filter_criteria))
            if bills:
                total_amount = sum(bill.get('amount', 0) for bill in bills)
                unpaid_amount = sum(bill.get('amount', 0) for bill in bills if bill.get('status') != 'paid')
                data['bills'] = {
                    'bills': bills,
                    'total_amount': total_amount,
                    'unpaid_amount': unpaid_amount
                }
            
            # Net Worth data
            net_worth_record = db.net_worth_data.find_one(filter_criteria, sort=[('created_at', -1)])
            if net_worth_record:
                data['net_worth'] = {
                    'net_worth': net_worth_record.get('net_worth', 0),
                    'total_assets': net_worth_record.get('total_assets', 0)
                }
            
            # Emergency Fund data
            emergency_fund_record = db.emergency_funds.find_one(filter_criteria, sort=[('created_at', -1)])
            if emergency_fund_record:
                data['emergency_fund'] = {
                    'target_amount': emergency_fund_record.get('target_amount', 0),
                    'savings_gap': emergency_fund_record.get('savings_gap', 0)
                }
            
            # Quiz data
            quiz_record = db.quiz_responses.find_one(filter_criteria, sort=[('created_at', -1)])
            if quiz_record:
                data['quiz'] = {
                    'personality': quiz_record.get('personality', 'Unknown'),
                    'score': quiz_record.get('score', 0)
                }
            
            # Learning progress
            learning_records = list(db.learning_materials.find(filter_criteria))
            learning_progress = {}
            for record in learning_records:
                course_id = record.get('course_id')
                if course_id:
                    learning_progress[course_id] = {
                        'lessons_completed': record.get('lessons_completed', [])
                    }
            data['learning_progress'] = learning_progress
            
            return render_template('personal/GENERAL/general_dashboard.html', 
                                 data=data,
                                 t=trans, 
                                 lang=lang)
        
        except Exception as e:
            app.logger.error(f"Error loading general dashboard: {str(e)}")
            flash(trans('general_dashboard_error', default='Error loading dashboard data.', lang=lang), 'danger')
            return render_template('personal/GENERAL/general_dashboard.html', 
                                 data={},
                                 t=trans, 
                                 lang=lang)
    
    @app.route('/set_language/<lang>')
    def set_language_route(lang):
        """Set user language preference"""
        return set_language(lang)
    
    @app.route('/acknowledge_consent', methods=['POST'])
    def acknowledge_consent():
        """Acknowledge user consent"""
        try:
            session['consent_acknowledged'] = True
            session.permanent = True
            return '', 204
        except Exception as e:
            app.logger.error(f"Error acknowledging consent: {str(e)}")
            return jsonify({'error': 'Invalid request'}), 400
    
    @app.route('/notifications')
    @login_required
    def notifications():
        """Get user notifications"""
        try:
            # This would typically fetch from a notifications collection
            # For now, return empty list
            return jsonify([])
        except Exception as e:
            app.logger.error(f"Error loading notifications: {str(e)}")
            return jsonify({'error': 'Failed to load notifications'}), 500
    
    @app.route('/notification_count')
    @login_required
    def notification_count():
        """Get notification count"""
        try:
            # This would typically count unread notifications
            # For now, return 0
            return jsonify({'count': 0})
        except Exception as e:
            app.logger.error(f"Error loading notification count: {str(e)}")
            return jsonify({'count': 0})
    
    # Error handlers
    @app.errorhandler(404)
    def not_found_error(error):
        lang = session.get('lang', 'en')
        return render_template('personal/GENERAL/404.html', 
                             t=trans, 
                             lang=lang,
                             feedback_form_url='https://forms.gle/feedback',
                             waitlist_form_url='https://forms.gle/waitlist',
                             consultancy_form_url='https://forms.gle/consultancy'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        lang = session.get('lang', 'en')
        return render_template('personal/GENERAL/500.html', 
                             t=trans, 
                             lang=lang), 500
    
    @app.errorhandler(403)
    def forbidden_error(error):
        lang = session.get('lang', 'en')
        flash(trans('general_access_denied', default='Access denied.', lang=lang), 'danger')
        return redirect(url_for('index'))
    
    # Template filters
    @app.template_filter('format_currency')
    def format_currency(value):
        """Format number as currency"""
        try:
            if value is None:
                return '₦0'
            return f"₦{value:,.0f}"
        except (ValueError, TypeError):
            return '₦0'
    
    @app.template_filter('format_number')
    def format_number(value):
        """Format number with commas"""
        try:
            if value is None:
                return '0'
            return f"{value:,.0f}"
        except (ValueError, TypeError):
            return '0'
    
    @app.template_filter('format_datetime')
    def format_datetime(value):
        """Format datetime for display"""
        try:
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return value.strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError, AttributeError):
            return 'N/A'
    
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)