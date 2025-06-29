from flask import Blueprint, render_template, session, request, redirect, url_for, flash, current_app, send_from_directory, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, BooleanField, SubmitField, HiddenField, FileField
from wtforms.validators import DataRequired, Email, Optional
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_login import current_user
from datetime import datetime
from mailersend_email import send_email, EMAIL_CONFIG
import uuid
import json
import os
from translations import trans
from werkzeug.utils import secure_filename
from models import log_tool_usage
import pymongo
import logging
from flask import g
from session_utils import create_anonymous_session
from utils import requires_role, is_admin, get_mongo_db

learning_hub_bp = Blueprint(
    'learning_hub',
    __name__,
    template_folder='templates/personal/LEARNINGHUB',
    url_prefix='/LEARNINGHUB'
)

def custom_login_required(f):
    """Custom login decorator that allows both authenticated users and anonymous sessions."""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated or session.get('is_anonymous', False):
            return f(*args, **kwargs)
        return redirect(url_for('users_blueprint.login', next=request.url))
    return decorated_function

# Initialize CSRF protection
csrf = CSRFProtect()

# Define allowed file extensions and upload folder
ALLOWED_EXTENSIONS = {'mp4', 'pdf', 'txt'}
UPLOAD_FOLDER = 'static/uploads'

# Ensure upload folder exists
def init_app(app):
    os.makedirs(app.config.get('UPLOAD_FOLDER', UPLOAD_FOLDER), exist_ok=True)
    init_storage(app)
    # Configure logging
    app.logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    app.logger.addHandler(handler)
    # Ensure MongoDB connections are closed on teardown
    @app.teardown_appcontext
    def close_db(error):
        if hasattr(g, 'db'):
            g.db.client.close()
            current_app.logger.info("MongoDB connection closed", extra={'session_id': session.get('sid', 'no-session-id')})

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Courses data with multimedia support
courses_data = {
    "budgeting_101": {
        "id": "budgeting_101",
        "title_en": "Budgeting 101",
        "title_ha": "Tsarin Kudi 101",
        "description_en": "Learn the basics of budgeting and financial planning to take control of your finances.",
        "description_ha": "Koyon asalin tsarin kudi da shirye-shiryen kudi don sarrafa kudin ku.",
        "title_key": "learning_hub_course_budgeting101_title",
        "desc_key": "learning_hub_course_budgeting101_desc",
        "modules": [
            {
                "id": "module-1",
                "title_key": "learning_hub_module_income_title",
                "title_en": "Understanding Income",
                "lessons": [
                    {
                        "id": "budgeting_101-module-1-lesson-1",
                        "title_key": "learning_hub_lesson_income_sources_title",
                        "title_en": "Income Sources",
                        "content_type": "video",
                        "content_path": "Uploads/budgeting_101_lesson1.mp4",
                        "content_en": "Understanding different sources of income is crucial for effective budgeting. Learn about salary, business income, investments, and passive income streams.",
                        "quiz_id": "quiz-1-1"
                    },
                    {
                        "id": "budgeting_101-module-1-lesson-2",
                        "title_key": "learning_hub_lesson_net_income_title",
                        "title_en": "Calculating Net Income",
                        "content_type": "text",
                        "content_key": "learning_hub_lesson_net_income_content",
                        "content_en": "Learn how to calculate your net income after taxes and deductions. This is the foundation of any successful budget.",
                        "quiz_id": None
                    }
                ]
            },
            {
                "id": "module-2",
                "title_key": "learning_hub_module_expenses_title",
                "title_en": "Managing Expenses",
                "lessons": [
                    {
                        "id": "budgeting_101-module-2-lesson-1",
                        "title_key": "learning_hub_lesson_expense_categories_title",
                        "title_en": "Expense Categories",
                        "content_type": "text",
                        "content_en": "Learn to categorize your expenses into fixed, variable, and discretionary spending to better manage your budget.",
                        "quiz_id": None
                    }
                ]
            }
        ]
    },
    "financial_quiz": {
        "id": "financial_quiz",
        "title_en": "Financial Knowledge Quiz",
        "title_ha": "Jarabawar Ilimin Kudi",
        "description_en": "Test your financial knowledge with our comprehensive quiz and discover areas for improvement.",
        "description_ha": "Gwada ilimin ku na kudi da jarabawa mai cikakke kuma gano wuraren da za ku inganta.",
        "title_key": "learning_hub_course_financial_quiz_title",
        "desc_key": "learning_hub_course_financial_quiz_desc",
        "modules": [
            {
                "id": "module-1",
                "title_key": "learning_hub_module_quiz_title",
                "title_en": "Financial Assessment",
                "lessons": [
                    {
                        "id": "financial_quiz-module-1-lesson-1",
                        "title_key": "learning_hub_lesson_quiz_intro_title",
                        "title_en": "Quiz Introduction",
                        "content_type": "text",
                        "content_key": "learning_hub_lesson_quiz_intro_content",
                        "content_en": "This comprehensive quiz will help assess your current financial knowledge and identify areas where you can improve your financial literacy.",
                        "quiz_id": "quiz-financial-1"
                    }
                ]
            }
        ]
    },
    "savings_basics": {
        "id": "savings_basics",
        "title_en": "Savings Fundamentals",
        "title_ha": "Asalin Tattara Kudi",
        "description_en": "Master the fundamentals of saving money effectively and build a secure financial future.",
        "description_ha": "Koyon asalin tattara kudi yadda ya kamata kuma gina makomar kudi mai tsaro.",
        "title_key": "learning_hub_course_savings_basics_title",
        "desc_key": "learning_hub_course_savings_basics_desc",
        "modules": [
            {
                "id": "module-1",
                "title_key": "learning_hub_module_savings_title",
                "title_en": "Savings Strategies",
                "lessons": [
                    {
                        "id": "savings_basics-module-1-lesson-1",
                        "title_key": "learning_hub_lesson_savings_strategies_title",
                        "title_en": "Effective Savings Strategies",
                        "content_type": "text",
                        "content_key": "learning_hub_lesson_savings_strategies_content",
                        "content_en": "Learn proven strategies for building your savings effectively, including the 50/30/20 rule, automatic savings, and emergency fund planning.",
                        "quiz_id": None
                    },
                    {
                        "id": "savings_basics-module-1-lesson-2",
                        "title_key": "learning_hub_lesson_savings_goals_title",
                        "title_en": "Setting Savings Goals",
                        "content_type": "text",
                        "content_en": "Discover how to set realistic and achievable savings goals that will motivate you to save consistently.",
                        "quiz_id": "quiz-savings-1"
                    }
                ]
            }
        ]
    }
}

quizzes_data = {
    "quiz-1-1": {
        "id": "quiz-1-1",
        "questions": [
            {
                "question_key": "learning_hub_quiz_income_q1",
                "question_en": "What is the most common source of income for most people?",
                "options_keys": [
                    "learning_hub_quiz_income_opt_salary",
                    "learning_hub_quiz_income_opt_business",
                    "learning_hub_quiz_income_opt_investment",
                    "learning_hub_quiz_income_opt_other"
                ],
                "options_en": ["Salary/Wages", "Business Income", "Investment Returns", "Other Sources"],
                "answer_key": "learning_hub_quiz_income_opt_salary",
                "answer_en": "Salary/Wages"
            },
            {
                "question_key": "learning_hub_quiz_income_q2",
                "question_en": "What should you do with your income first?",
                "options_keys": [
                    "learning_hub_quiz_income_opt2_spend",
                    "learning_hub_quiz_income_opt2_save",
                    "learning_hub_quiz_income_opt2_invest",
                    "learning_hub_quiz_income_opt2_budget"
                ],
                "options_en": ["Spend on necessities", "Save everything", "Invest immediately", "Create a budget plan"],
                "answer_key": "learning_hub_quiz_income_opt2_budget",
                "answer_en": "Create a budget plan"
            }
        ]
    },
    "quiz-financial-1": {
        "id": "quiz-financial-1",
        "questions": [
            {
                "question_key": "learning_hub_quiz_financial_q1",
                "question_en": "What percentage of income should you ideally save?",
                "options_keys": [
                    "learning_hub_quiz_financial_opt_a",
                    "learning_hub_quiz_financial_opt_b",
                    "learning_hub_quiz_financial_opt_c",
                    "learning_hub_quiz_financial_opt_d"
                ],
                "options_en": ["20%", "10%", "5%", "30%"],
                "answer_key": "learning_hub_quiz_financial_opt_a",
                "answer_en": "20%"
            },
            {
                "question_key": "learning_hub_quiz_financial_q2",
                "question_en": "What is an emergency fund?",
                "options_keys": [
                    "learning_hub_quiz_financial_opt2_a",
                    "learning_hub_quiz_financial_opt2_b",
                    "learning_hub_quiz_financial_opt2_c",
                    "learning_hub_quiz_financial_opt2_d"
                ],
                "options_en": ["Money for vacations", "Money for unexpected expenses", "Money for investments", "Money for shopping"],
                "answer_key": "learning_hub_quiz_financial_opt2_b",
                "answer_en": "Money for unexpected expenses"
            }
        ]
    },
    "quiz-savings-1": {
        "id": "quiz-savings-1",
        "questions": [
            {
                "question_key": "learning_hub_quiz_savings_q1",
                "question_en": "What is the 50/30/20 rule?",
                "options_keys": [
                    "learning_hub_quiz_savings_opt_a",
                    "learning_hub_quiz_savings_opt_b",
                    "learning_hub_quiz_savings_opt_c",
                    "learning_hub_quiz_savings_opt_d"
                ],
                "options_en": ["50% needs, 30% wants, 20% savings", "50% savings, 30% needs, 20% wants", "50% wants, 30% savings, 20% needs", "50% investments, 30% savings, 20% spending"],
                "answer_key": "learning_hub_quiz_savings_opt_a",
                "answer_en": "50% needs, 30% wants, 20% savings"
            }
        ]
    }
}

class LearningHubProfileForm(FlaskForm):
    first_name = StringField(trans('general_first_name', default='First Name'), validators=[DataRequired()])
    email = StringField(trans('general_email', default='Email'), validators=[Optional(), Email()])
    send_email = BooleanField(trans('general_send_email', default='Send Email'), default=False)
    submit = SubmitField(trans('general_submit', default='Submit'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        self.first_name.validators[0].message = trans('general_first_name_required', lang=lang)
        if self.email.validators:
            self.email.validators[1].message = trans('general_email_invalid', lang=lang)

def get_progress():
    """Retrieve learning progress from MongoDB with caching."""
    try:
        if 'sid' not in session:
            session['sid'] = str(uuid.uuid4())
            session.permanent = True
            session.modified = True
        filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session.get('sid', str(uuid.uuid4()))}
        progress_records = get_mongo_db().learning_materials.find(filter_kwargs)
        progress = {}
        for record in progress_records:
            try:
                course_id = record.get('course_id')
                if not course_id:
                    current_app.logger.warning(f"Invalid progress record, missing course_id: {record}", extra={'session_id': session.get('sid', 'no-session-id')})
                    continue
                progress[course_id] = {
                    'lessons_completed': record.get('lessons_completed', []),
                    'quiz_scores': record.get('quiz_scores', {}),
                    'current_lesson': record.get('current_lesson')
                }
            except Exception as e:
                current_app.logger.error(f"Error parsing progress record for course {record.get('course_id', 'unknown')}: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return progress
    except Exception as e:
        current_app.logger.error(f"Error retrieving progress from MongoDB: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return {}

def save_course_progress(course_id, course_progress):
    """Save course progress to MongoDB with validation."""
    try:
        if 'sid' not in session:
            session['sid'] = str(uuid.uuid4())
            session.permanent = True
            session.modified = True
        if not isinstance(course_id, str) or not isinstance(course_progress, dict):
            current_app.logger.error(f"Invalid course_id or course_progress: course_id={course_id}, course_progress={course_progress}", extra={'session_id': session.get('sid', 'no-session-id')})
            return
        filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        filter_kwargs['course_id'] = course_id
        update_data = {
            '$set': {
                'user_id': current_user.id if current_user.is_authenticated else None,
                'session_id': session['sid'],
                'course_id': course_id,
                'lessons_completed': course_progress.get('lessons_completed', []),
                'quiz_scores': course_progress.get('quiz_scores', {}),
                'current_lesson': course_progress.get('current_lesson')
            }
        }
        get_mongo_db().learning_materials.update_one(filter_kwargs, update_data, upsert=True)
        current_app.logger.info(f"Saved progress for course {course_id}", extra={'session_id': session.get('sid')})
    except Exception as e:
        current_app.logger.error(f"Error saving progress to MongoDB for course {course_id}: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})

def init_storage(app):
    """Initialize storage with app context and logger."""
    with app.app_context():
        current_app.logger.info("Initializing courses storage.", extra={'session_id': 'no-request-context'})
        try:
            existing_courses = get_mongo_db().learning_materials.find({'type': 'course'})
            if not existing_courses.count():
                current_app.logger.info("Courses collection is empty. Initializing with default courses.", extra={'session_id': 'no-request-context'})
                default_courses = [
                    {
                        'type': 'course',
                        'id': course['id'],
                        'title_key': course['title_key'],
                        'title_en': course['title_en'],
                        'title_ha': course['title_ha'],
                        'description_en': course['description_en'],
                        'description_ha': course['description_ha'],
                        'is_premium': False
                    } for course in courses_data.values()
                ]
                if default_courses:
                    get_mongo_db().learning_materials.insert_many(default_courses)
                    current_app.logger.info(f"Initialized courses collection with {len(default_courses)} default courses", extra={'session_id': 'no-request-context'})
        except Exception as e:
            current_app.logger.error(f"Error initializing courses: {str(e)}", extra={'session_id': 'no-request-context'})
            raise

def course_lookup(course_id):
    """Retrieve course by ID with validation."""
    course = courses_data.get(course_id)
    if not course or not isinstance(course, dict) or 'modules' not in course or not isinstance(course['modules'], list):
        current_app.logger.error(f"Invalid course data for course_id {course_id}: {course}", extra={'session_id': session.get('sid', 'no-session-id')})
        return None
    for module in course['modules']:
        if not isinstance(module, dict) or 'lessons' not in module or not isinstance(module['lessons'], list):
            current_app.logger.error(f"Invalid module data in course {course_id}: {module}", extra={'session_id': session.get('sid', 'no-session-id')})
            return None
    return course

def lesson_lookup(course, lesson_id):
    """Retrieve lesson and its module by lesson ID with validation."""
    if not course or not isinstance(course, dict) or 'modules' not in course:
        current_app.logger.error(f"Invalid course data for lesson lookup: {course}", extra={'session_id': session.get('sid', 'no-session-id')})
        return None, None
    for module in course['modules']:
        if not isinstance(module, dict) or 'lessons' not in module:
            current_app.logger.error(f"Invalid module data: {module}", extra={'session_id': session.get('sid', 'no-session-id')})
            continue
        for lesson in module['lessons']:
            if not isinstance(lesson, dict) or 'id' not in lesson:
                current_app.logger.error(f"Invalid lesson data: {lesson}", extra={'session_id': session.get('sid', 'no-session-id')})
                continue
            if lesson['id'] == lesson_id:
                return lesson, module
    current_app.logger.warning(f"Lesson {lesson_id} not found in course", extra={'session_id': session.get('sid', 'no-session-id')})
    return None, None

def calculate_progress_summary():
    """Calculate progress summary for dashboard."""
    progress = get_progress()
    progress_summary = []
    total_completed = 0
    total_quiz_scores = 0
    
    for course_id, course in courses_data.items():
        if not course_lookup(course_id):
            continue
        cp = progress.get(course_id, {'lessons_completed': [], 'current_lesson': None, 'quiz_scores': {}})
        lessons_total = sum(len(m.get('lessons', [])) for m in course.get('modules', []))
        completed = len(cp.get('lessons_completed', []))
        percent = int((completed / lessons_total) * 100) if lessons_total > 0 else 0
        current_lesson_id = cp.get('current_lesson')
        if not current_lesson_id and lessons_total > 0:
            current_lesson_id = course['modules'][0]['lessons'][0]['id'] if course['modules'] and course['modules'][0]['lessons'] else None
        
        progress_summary.append({
            'course': course,
            'completed': completed,
            'total': lessons_total,
            'percent': percent,
            'current_lesson': current_lesson_id
        })
        
        total_completed += completed
        total_quiz_scores += len(cp.get('quiz_scores', {}))
    
    return progress_summary, total_completed, total_quiz_scores

@learning_hub_bp.route('/')
@learning_hub_bp.route('/main')
@custom_login_required
@requires_role(['personal', 'admin'])
def main():
    """Main learning hub interface with all functionality."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
        session.modified = True
    
    lang = session.get('lang', 'en')
    
    try:
        log_tool_usage(
            tool_name='learning_hub',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='main_view',
            mongo=get_mongo_db()
        )
        
        # Get progress and calculate summary
        progress = get_progress()
        progress_summary, total_completed, total_quiz_scores = calculate_progress_summary()
        
        # Get profile data
        profile_data = session.get('learning_hub_profile', {})
        if current_user.is_authenticated:
            profile_data['email'] = profile_data.get('email', current_user.email)
            profile_data['first_name'] = profile_data.get('first_name', current_user.username)
        
        # Create profile form
        profile_form = LearningHubProfileForm(data=profile_data)
        
        current_app.logger.info(f"Rendering main learning hub page", extra={'session_id': session.get('sid', 'no-session-id')})
        
        return render_template(
            'learning_hub_main.html',
            courses=courses_data,
            progress=progress,
            progress_summary=progress_summary,
            total_completed=total_completed,
            total_courses=len(courses_data),
            quiz_scores_count=total_quiz_scores,
            profile_form=profile_form,
            profile_data=profile_data,
            t=trans,
            lang=lang,
            tool_title=trans('learning_hub_title', default='Learning Hub', lang=lang)
        )
        
    except Exception as e:
        current_app.logger.error(f"Error rendering main learning hub page: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        flash(trans("learning_hub_error_loading", default="Error loading learning hub", lang=lang), "danger")
        return render_template(
            'learning_hub_main.html',
            courses={},
            progress={},
            progress_summary=[],
            total_completed=0,
            total_courses=0,
            quiz_scores_count=0,
            profile_form=LearningHubProfileForm(),
            profile_data={},
            t=trans,
            lang=lang,
            tool_title=trans('learning_hub_title', default='Learning Hub', lang=lang)
        ), 500

@learning_hub_bp.route('/api/course/<course_id>')
def get_course_data(course_id):
    """API endpoint to get course data."""
    try:
        course = course_lookup(course_id)
        if not course:
            return jsonify({'success': False, 'message': trans('learning_hub_course_not_found', default='Course not found')})
        
        progress = get_progress()
        course_progress = progress.get(course_id, {'lessons_completed': [], 'quiz_scores': {}, 'current_lesson': None})
        
        return jsonify({
            'success': True,
            'course': course,
            'progress': course_progress
        })
    except Exception as e:
        current_app.logger.error(f"Error getting course data: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return jsonify({'success': False, 'message': trans('learning_hub_course_load_error', default='Error loading course')})

@learning_hub_bp.route('/api/lesson')
def get_lesson_data():
    """API endpoint to get lesson data."""
    try:
        course_id = request.args.get('course_id')
        lesson_id = request.args.get('lesson_id')
        
        course = course_lookup(course_id)
        if not course:
            return jsonify({'success': False, 'message': trans('learning_hub_course_not_found', default='Course not found')})
        
        lesson, module = lesson_lookup(course, lesson_id)
        if not lesson:
            return jsonify({'success': False, 'message': trans('learning_hub_lesson_not_found', default='Lesson not found')})
        
        progress = get_progress()
        course_progress = progress.get(course_id, {'lessons_completed': [], 'quiz_scores': {}, 'current_lesson': None})
        
        # Find next lesson
        next_lesson_id = None
        found = False
        for m in course['modules']:
            for l in m['lessons']:
                if found and l.get('id'):
                    next_lesson_id = l['id']
                    break
                if l['id'] == lesson_id:
                    found = True
            if next_lesson_id:
                break
        
        return jsonify({
            'success': True,
            'course': course,
            'lesson': lesson,
            'progress': course_progress,
            'next_lesson_id': next_lesson_id
        })
    except Exception as e:
        current_app.logger.error(f"Error getting lesson data: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return jsonify({'success': False, 'message': trans('learning_hub_lesson_load_error', default='Error loading lesson')})

@learning_hub_bp.route('/api/quiz')
def get_quiz_data():
    """API endpoint to get quiz data."""
    try:
        course_id = request.args.get('course_id')
        quiz_id = request.args.get('quiz_id')
        
        course = course_lookup(course_id)
        if not course:
            return jsonify({'success': False, 'message': trans('learning_hub_course_not_found', default='Course not found')})
        
        quiz = quizzes_data.get(quiz_id)
        if not quiz:
            return jsonify({'success': False, 'message': trans('learning_hub_quiz_not_found', default='Quiz not found')})
        
        progress = get_progress()
        course_progress = progress.get(course_id, {'lessons_completed': [], 'quiz_scores': {}, 'current_lesson': None})
        
        return jsonify({
            'success': True,
            'course': course,
            'quiz': quiz,
            'progress': course_progress
        })
    except Exception as e:
        current_app.logger.error(f"Error getting quiz data: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return jsonify({'success': False, 'message': trans('learning_hub_quiz_load_error', default='Error loading quiz')})

@learning_hub_bp.route('/api/lesson/action', methods=['POST'])
@custom_login_required
def lesson_action():
    """Handle lesson actions like marking complete."""
    try:
        course_id = request.form.get('course_id')
        lesson_id = request.form.get('lesson_id')
        action = request.form.get('action')
        
        if action == 'mark_complete':
            progress = get_progress()
            if course_id not in progress:
                course_progress = {'lessons_completed': [], 'quiz_scores': {}, 'current_lesson': lesson_id}
                progress[course_id] = course_progress
            else:
                course_progress = progress[course_id]
            
            if lesson_id not in course_progress['lessons_completed']:
                course_progress['lessons_completed'].append(lesson_id)
                course_progress['current_lesson'] = lesson_id
                save_course_progress(course_id, course_progress)
                
                # Send email if user has provided details and opted in
                profile = session.get('learning_hub_profile', {})
                if profile.get('send_email') and profile.get('email'):
                    try:
                        course = course_lookup(course_id)
                        lesson, _ = lesson_lookup(course, lesson_id)
                        
                        config = EMAIL_CONFIG.get("learning_hub_lesson_completed", {})
                        subject = trans(config.get("subject_key", ""), lang=session.get('lang', 'en'))
                        template = config.get("template", "")
                        
                        send_email(
                            app=current_app,
                            logger=current_app.logger,
                            to_email=profile['email'],
                            subject=subject,
                            template_name=template,
                            data={
                                "first_name": profile.get('first_name', ''),
                                "course_title": course.get('title_en', ''),
                                "lesson_title": lesson.get('title_en', ''),
                                "completed_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                "cta_url": url_for('learning_hub.main', _external=True),
                                "unsubscribe_url": url_for('learning_hub.unsubscribe', email=profile['email'], _external=True)
                            },
                            lang=session.get('lang', 'en')
                        )
                    except Exception as e:
                        current_app.logger.error(f"Failed to send email for lesson {lesson_id}: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
                
                return jsonify({'success': True, 'message': trans('learning_hub_lesson_completed', default='Lesson marked as complete')})
            else:
                return jsonify({'success': True, 'message': trans('learning_hub_lesson_already_completed', default='Lesson already completed')})
        
        return jsonify({'success': False, 'message': trans('learning_hub_invalid_action', default='Invalid action')})
    except Exception as e:
        current_app.logger.error(f"Error in lesson action: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return jsonify({'success': False, 'message': trans('learning_hub_action_error', default='Error processing action')})

@learning_hub_bp.route('/api/quiz/action', methods=['POST'])
@custom_login_required
def quiz_action():
    """Handle quiz actions like submitting answers."""
    try:
        course_id = request.form.get('course_id')
        quiz_id = request.form.get('quiz_id')
        action = request.form.get('action')
        
        if action == 'submit_quiz':
            quiz = quizzes_data.get(quiz_id)
            if not quiz:
                return jsonify({'success': False, 'message': trans('learning_hub_quiz_not_found', default='Quiz not found')})
            
            # Calculate score
            score = 0
            for i, question in enumerate(quiz['questions']):
                user_answer = request.form.get(f'q{i}')
                if user_answer and user_answer == question['answer_en']:
                    score += 1
            
            # Save score
            progress = get_progress()
            if course_id not in progress:
                course_progress = {'lessons_completed': [], 'quiz_scores': {}, 'current_lesson': None}
                progress[course_id] = course_progress
            else:
                course_progress = progress[course_id]
            
            course_progress['quiz_scores'][quiz_id] = score
            save_course_progress(course_id, course_progress)
            
            return jsonify({
                'success': True,
                'message': trans('learning_hub_quiz_submitted', default='Quiz submitted successfully'),
                'score': score,
                'total': len(quiz['questions'])
            })
        
        return jsonify({'success': False, 'message': trans('learning_hub_invalid_action', default='Invalid action')})
    except Exception as e:
        current_app.logger.error(f"Error in quiz action: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return jsonify({'success': False, 'message': trans('learning_hub_quiz_error', default='Error processing quiz')})

@learning_hub_bp.route('/profile', methods=['GET', 'POST'])
def profile():
    """Handle user profile form for first name and email."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
        session.modified = True
    
    lang = session.get('lang', 'en')
    
    try:
        log_tool_usage(
            tool_name='learning_hub',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='profile_submit' if request.method == 'POST' else 'profile_view',
            mongo=get_mongo_db()
        )
        
        if request.method == 'POST':
            session['learning_hub_profile'] = {
                'first_name': request.form.get('first_name', ''),
                'email': request.form.get('email', ''),
                'send_email': 'send_email' in request.form
            }
            session.permanent = True
            session.modified = True
            
            current_app.logger.info(f"Profile saved for user {current_user.id if current_user.is_authenticated else 'anonymous'}", extra={'session_id': session.get('sid', 'no-session-id')})
            flash(trans('learning_hub_profile_saved', default='Profile saved successfully', lang=lang), 'success')
            
            return redirect(url_for('learning_hub.main'))
        
        # For GET requests, redirect to main page
        return redirect(url_for('learning_hub.main'))
        
    except Exception as e:
        current_app.logger.error(f"Error in profile page: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        flash(trans("learning_hub_error_loading", default="Error loading profile", lang=lang), "danger")
        return redirect(url_for('learning_hub.main'))

@learning_hub_bp.route('/unsubscribe/<email>')
def unsubscribe(email):
    """Unsubscribe user from learning hub emails."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
        session.modified = True
    
    try:
        log_tool_usage(
            tool_name='learning_hub',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='unsubscribe',
            mongo=get_mongo_db()
        )
        
        lang = session.get('lang', 'en')
        profile = session.get('learning_hub_profile', {})
        
        if profile.get('email') == email and profile.get('send_email', False):
            profile['send_email'] = False
            session['learning_hub_profile'] = profile
            session.permanent = True
            session.modified = True
            current_app.logger.info(f"Unsubscribed email {email}", extra={'session_id': session.get('sid', 'no-session-id')})
            flash(trans("learning_hub_unsubscribed_success", default="Successfully unsubscribed from emails", lang=lang), "success")
        else:
            current_app.logger.error(f"Failed to unsubscribe email {email}: No matching profile or already unsubscribed", extra={'session_id': session.get('sid', 'no-session-id')})
            flash(trans("learning_hub_unsubscribe_failed", default="Failed to unsubscribe. Email not found or already unsubscribed.", lang=lang), "danger")
        
        return redirect(url_for('learning_hub.main'))
        
    except Exception as e:
        current_app.logger.error(f"Error in unsubscribe: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        flash(trans("learning_hub_unsubscribe_error", default="Error processing unsubscribe request", lang=lang), "danger")
        return redirect(url_for('learning_hub.main'))

@learning_hub_bp.route('/static/uploads/<path:filename>')
def serve_uploaded_file(filename):
    """Serve uploaded files securely with caching."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
        session.modified = True
    
    try:
        log_tool_usage(
            tool_name='learning_hub',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='serve_file',
            mongo=get_mongo_db()
        )
        
        response = send_from_directory(current_app.config.get('UPLOAD_FOLDER', UPLOAD_FOLDER), filename)
        response.headers['Cache-Control'] = 'public, max-age=31536000'
        current_app.logger.info(f"Served file: {filename}", extra={'session_id': session.get('sid', 'no-session-id')})
        return response
        
    except Exception as e:
        current_app.logger.error(f"Error serving uploaded file {filename}: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
        return redirect(url_for('learning_hub.main')), 404

@learning_hub_bp.errorhandler(404)
def handle_not_found(e):
    """Handle 404 errors with user-friendly message."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
        session.modified = True
    
    lang = session.get('lang', 'en')
    current_app.logger.error(f"404 error on {request.path}: {str(e)}", extra={'session_id': session.get('sid', 'no-session-id')})
    flash(trans("learning_hub_not_found", default="The requested page was not found. Please check the URL or return to the main page.", lang=lang), "danger")
    return redirect(url_for('learning_hub.main')), 404

@learning_hub_bp.errorhandler(CSRFError)
def handle_csrf_error(e):
    """Handle CSRF errors with user-friendly message."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
        session.modified = True
    
    lang = session.get('lang', 'en')
    current_app.logger.error(f"CSRF error on {request.path}: {e.description}", extra={'session_id': session.get('sid', 'no-session-id')})
    flash(trans("learning_hub_csrf_error", default="Form submission failed due to a missing security token. Please refresh and try again.", lang=lang), "danger")
    return redirect(url_for('learning_hub.main')), 400

# Legacy route redirects for backward compatibility
@learning_hub_bp.route('/courses')
def courses():
    """Redirect to main page with courses tab."""
    return redirect(url_for('learning_hub.main') + '#courses')

@learning_hub_bp.route('/courses/<course_id>')
def course_overview(course_id):
    """Redirect to main page and load course overview."""
    return redirect(url_for('learning_hub.main') + f'#course-{course_id}')

@learning_hub_bp.route('/courses/<course_id>/lesson/<lesson_id>')
def lesson(course_id, lesson_id):
    """Redirect to main page and load lesson."""
    return redirect(url_for('learning_hub.main') + f'#lesson-{course_id}-{lesson_id}')

@learning_hub_bp.route('/courses/<course_id>/quiz/<quiz_id>')
def quiz(course_id, quiz_id):
    """Redirect to main page and load quiz."""
    return redirect(url_for('learning_hub.main') + f'#quiz-{course_id}-{quiz_id}')

@learning_hub_bp.route('/dashboard')
def dashboard():
    """Redirect to main page with dashboard tab."""
    return redirect(url_for('learning_hub.main') + '#dashboard')