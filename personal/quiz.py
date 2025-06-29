from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, BooleanField, SubmitField, RadioField
from wtforms.validators import DataRequired, Email, Optional
from flask_login import current_user
from uuid import uuid4
from datetime import datetime
import json
import logging
from translations import trans
from mailersend_email import send_email, EMAIL_CONFIG
from models import log_tool_usage
from session_utils import create_anonymous_session
from utils import requires_role, is_admin, get_mongo_db

# Configure logging
logger = logging.getLogger('ficore_app')

# Define the quiz blueprint
quiz_bp = Blueprint('quiz', __name__, template_folder='templates/QUIZ', url_prefix='/QUIZ')

def custom_login_required(f):
    """Custom login decorator that allows both authenticated users and anonymous sessions."""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated or session.get('is_anonymous', False):
            return f(*args, **kwargs)
        return redirect(url_for('users_blueprint.login', next=request.url))
    return decorated_function

class QuizForm(FlaskForm):
    first_name = StringField(trans('general_first_name', default='First Name'), validators=[DataRequired()], render_kw={
        'placeholder': trans('general_first_name_placeholder', default='e.g., Muhammad, Bashir, Umar'),
        'title': trans('general_first_name_title', default='Enter your first name to personalize your quiz results')
    })
    email = StringField(trans('general_email', default='Email'), validators=[DataRequired(), Email()], render_kw={
        'placeholder': trans('general_email_placeholder', default='e.g., muhammad@example.com'),
        'title': trans('general_email_title', default='Enter your email to receive quiz results')
    })
    lang = SelectField(trans('general_language', default='Language'), choices=[('en', 'English'), ('ha', 'Hausa')], default='en', validators=[Optional()])
    send_email = BooleanField(trans('general_send_email', default='Send Email'), default=False, validators=[Optional()], render_kw={
        'title': trans('general_send_email_title', default='Check to receive an email with your quiz results')
    })
    
    # Quiz questions
    question_1 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_1')
    question_2 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_2')
    question_3 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_3')
    question_4 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_4')
    question_5 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_5')
    question_6 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_6')
    question_7 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_7')
    question_8 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_8')
    question_9 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_9')
    question_10 = RadioField(validators=[DataRequired()], choices=[('Yes', 'Yes'), ('No', 'No')], id='question_10')
    
    submit = SubmitField(trans('quiz_submit_quiz', default='Submit Quiz'))

    def __init__(self, lang='en', *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Set validation messages
        self.first_name.validators[0].message = trans('general_first_name_required', lang=lang)
        self.email.validators[0].message = trans('general_email_required', lang=lang)
        self.email.validators[1].message = trans('general_email_invalid', lang=lang)

        self.lang.choices = [
            ('en', trans('general_english', default='English', lang=lang)),
            ('ha', trans('general_hausa', default='Hausa', lang=lang))
        ]

        # Set up questions
        questions = [
            {'id': 'question_1', 'text_key': 'quiz_track_expenses_label', 'text': 'Do you track your expenses regularly?', 'tooltip_key': 'quiz_track_expenses_tooltip', 'icon': '💰'},
            {'id': 'question_2', 'text_key': 'quiz_save_regularly_label', 'text': 'Do you save a portion of your income regularly?', 'tooltip_key': 'quiz_save_regularly_tooltip', 'icon': '💰'},
            {'id': 'question_3', 'text_key': 'quiz_budget_monthly_label', 'text': 'Do you set a monthly budget?', 'tooltip_key': 'quiz_budget_monthly_tooltip', 'icon': '📝'},
            {'id': 'question_4', 'text_key': 'quiz_emergency_fund_label', 'text': 'Do you have an emergency fund?', 'tooltip_key': 'quiz_emergency_fund_tooltip', 'icon': '🚨'},
            {'id': 'question_5', 'text_key': 'quiz_invest_regularly_label', 'text': 'Do you invest your money regularly?', 'tooltip_key': 'quiz_invest_regularly_tooltip', 'icon': '📈'},
            {'id': 'question_6', 'text_key': 'quiz_spend_impulse_label', 'text': 'Do you often spend money on impulse?', 'tooltip_key': 'quiz_spend_impulse_tooltip', 'icon': '🛒'},
            {'id': 'question_7', 'text_key': 'quiz_financial_goals_label', 'text': 'Do you set financial goals?', 'tooltip_key': 'quiz_financial_goals_tooltip', 'icon': '🎯'},
            {'id': 'question_8', 'text_key': 'quiz_review_spending_label', 'text': 'Do you review your spending habits regularly?', 'tooltip_key': 'quiz_review_spending_tooltip', 'icon': '🔍'},
            {'id': 'question_9', 'text_key': 'quiz_multiple_income_label', 'text': 'Do you have multiple sources of income?', 'tooltip_key': 'quiz_multiple_income_tooltip', 'icon': '💼'},
            {'id': 'question_10', 'text_key': 'quiz_retirement_plan_label', 'text': 'Do you have a retirement savings plan?', 'tooltip_key': 'quiz_retirement_plan_tooltip', 'icon': '🏖️'},
        ]
        
        for q in questions:
            field = getattr(self, q['id'])
            field.label.text = trans(q['text_key'], default=q['text'], lang=lang)
            field.description = trans(q['tooltip_key'], default='', lang=lang)
            field.choices = [(opt, trans(opt, default=opt, lang=lang)) for opt in ['Yes', 'No']]

# Helper Functions
def calculate_score(answers):
    score = 0
    positive_questions = ['question_1', 'question_2', 'question_3', 'question_4', 'question_5', 'question_7', 'question_8', 'question_9', 'question_10']
    negative_questions = ['question_6']
    for i, answer in enumerate(answers, 1):
        qid = f'question_{i}'
        if qid in positive_questions and answer == 'Yes':
            score += 3
        elif qid in positive_questions and answer == 'No':
            score -= 1
        elif qid in negative_questions and answer == 'No':
            score += 3
        elif qid in negative_questions and answer == 'Yes':
            score -= 1
    return max(0, score)

def assign_personality(score, lang='en'):
    if score >= 21:
        return {
            'name': trans('quiz_planner', default='Planner', lang=lang),
            'description': trans('quiz_planner_description', default='You plan your finances meticulously.', lang=lang),
            'insights': [trans('quiz_insight_planner_1', default='You have a strong grasp of financial planning.', lang=lang)],
            'tips': [trans('quiz_tip_planner_1', default='Continue setting long-term goals.', lang=lang)]
        }
    elif score >= 13:
        return {
            'name': trans('quiz_saver', default='Saver', lang=lang),
            'description': trans('quiz_saver_description', default='You prioritize saving consistently.', lang=lang),
            'insights': [trans('quiz_insight_saver_1', default='You excel at saving regularly.', lang=lang)],
            'tips': [trans('quiz_tip_saver_1', default='Consider investing to grow your savings.', lang=lang)]
        }
    elif score >= 7:
        return {
            'name': trans('quiz_balanced', default='Balanced', lang=lang),
            'description': trans('quiz_balanced_description', default='You maintain a balanced financial approach.', lang=lang),
            'insights': [trans('quiz_insight_balanced_1', default='You balance saving and spending well.', lang=lang)],
            'tips': [trans('quiz_tip_balanced_1', default='Try a budgeting app to optimize habits.', lang=lang)]
        }
    elif score >= 3:
        return {
            'name': trans('quiz_spender', default='Spender', lang=lang),
            'description': trans('quiz_spender_description', default='You enjoy spending freely.', lang=lang),
            'insights': [trans('quiz_insight_spender_1', default='Spending is a strength, but can be controlled.', lang=lang)],
            'tips': [trans('quiz_tip_spender_1', default='Track expenses to avoid overspending.', lang=lang)]
        }
    else:
        return {
            'name': trans('quiz_avoider', default='Avoider', lang=lang),
            'description': trans('quiz_avoider_description', default='You avoid financial planning.', lang=lang),
            'insights': [trans('quiz_insight_avoider_1', default='Planning feels challenging but is learnable.', lang=lang)],
            'tips': [trans('quiz_tip_avoider_1', default='Start with a simple monthly budget.', lang=lang)]
        }

def assign_badges(score, lang='en'):
    badges = []
    if score >= 21:
        badges.append({
            'name': trans('badge_financial_guru', default='Financial Guru', lang=lang),
            'color_class': 'bg-primary',
            'description': trans('badge_financial_guru_desc', default='Awarded for excellent financial planning.', lang=lang)
        })
    elif score >= 13:
        badges.append({
            'name': trans('badge_savings_star', default='Savings Star', lang=lang),
            'color_class': 'bg-success',
            'description': trans('badge_savings_star_desc', default='Recognized for consistent saving habits.', lang=lang)
        })
    badges.append({
        'name': trans('badge_first_quiz', default='First Quiz Completed', lang=lang),
        'color_class': 'bg-info',
        'description': trans('badge_first_quiz_desc', default='Completed your first financial quiz.', lang=lang)
    })
    return badges

@quiz_bp.route('/main', methods=['GET', 'POST'])
@custom_login_required
@requires_role(['personal', 'admin'])
def main():
    """Main quiz interface with tabbed layout."""
    if 'sid' not in session:
        create_anonymous_session()
        logger.debug(f"New anonymous session created with sid: {session['sid']}", extra={'session_id': session['sid']})
    lang = session.get('lang', 'en')
    course_id = request.args.get('course_id', 'financial_quiz')
    
    # Initialize form with user data
    form_data = {}
    if current_user.is_authenticated:
        form_data['email'] = current_user.email
        form_data['first_name'] = current_user.username
    
    form = QuizForm(lang=lang, data=form_data)
    
    log_tool_usage(
        tool_name='quiz',
        user_id=current_user.id if current_user.is_authenticated else None,
        session_id=session['sid'],
        action='main_view',
        mongo=get_mongo_db()
    )

    try:
        filter_criteria = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'submit_quiz' and form.validate_on_submit():
                log_tool_usage(
                    tool_name='quiz',
                    user_id=current_user.id if current_user.is_authenticated else None,
                    session_id=session['sid'],
                    action='submit_quiz',
                    mongo=get_mongo_db()
                )

                # Calculate results
                answers = [getattr(form, f'question_{i}').data for i in range(1, 11)]
                score = calculate_score(answers)
                personality = assign_personality(score, lang)
                badges = assign_badges(score, lang)
                
                # Create and persist quiz result record to MongoDB
                created_at = datetime.utcnow().isoformat()
                quiz_result = {
                    '_id': str(uuid4()),
                    'user_id': current_user.id if current_user.is_authenticated else None,
                    'session_id': session['sid'],
                    'created_at': created_at,
                    'first_name': form.first_name.data,
                    'email': form.email.data,
                    'send_email': form.send_email.data,
                    'personality': personality['name'],
                    'score': score,
                    'badges': badges,
                    'insights': personality['insights'],
                    'tips': personality['tips']
                }
                
                logger.debug(f"Saving quiz result with created_at: {created_at}, type: {type(created_at)}", extra={'session_id': session['sid']})
                get_mongo_db().quiz_responses.insert_one(quiz_result)
                logger.info(f"Successfully saved quiz result {quiz_result['_id']} for session {session['sid']}", extra={'session_id': session['sid']})
                flash(trans('quiz_completed_successfully', default='Quiz completed successfully!'), 'success')
                
                # Send email if user opted in
                if form.send_email.data and form.email.data:
                    try:
                        config = EMAIL_CONFIG["quiz"]
                        subject = trans(config["subject_key"], default='Your Financial Quiz Results', lang=lang)
                        template = config["template"]
                        send_email(
                            app=current_app,
                            logger=current_app.logger,
                            to_email=form.email.data,
                            subject=subject,
                            template_name=template,
                            data={
                                "first_name": quiz_result['first_name'],
                                "score": quiz_result['score'],
                                "max_score": 30,
                                "personality": quiz_result['personality'],
                                "badges": quiz_result['badges'],
                                "insights": quiz_result['insights'],
                                "tips": quiz_result['tips'],
                                "created_at": datetime.fromisoformat(created_at),
                                "cta_url": url_for('quiz.main', course_id=course_id, _external=True)
                            },
                            lang=lang
                        )
                    except Exception as e:
                        logger.error(f"Failed to send quiz results email: {str(e)}", extra={'session_id': session['sid']})
                        flash(trans("general_email_send_failed", default="Failed to send email.", lang=lang), "warning")

        # Get quiz results for display
        quiz_results = list(get_mongo_db().quiz_responses.find(filter_criteria).sort('created_at', -1))
        
        if not quiz_results and current_user.is_authenticated and current_user.email:
            quiz_results = list(get_mongo_db().quiz_responses.find({'email': current_user.email}).sort('created_at', -1))

        latest_record = quiz_results[0] if quiz_results else {}
        
        # Handle created_at conversion for display
        if latest_record and isinstance(latest_record.get('created_at'), str):
            try:
                latest_record['created_at'] = datetime.fromisoformat(latest_record['created_at'])
            except (TypeError, ValueError) as e:
                logger.error(f"Failed to parse created_at '{latest_record['created_at']}' in results: {str(e)}", extra={'session_id': session['sid']})
                latest_record['created_at'] = None

        # Preprocessed questions for template
        questions = [
            {'id': 'question_1', 'text_key': 'quiz_track_expenses_label', 'text': 'Do you track your expenses regularly?', 'tooltip': 'quiz_track_expenses_tooltip', 'icon': '💰'},
            {'id': 'question_2', 'text_key': 'quiz_save_regularly_label', 'text': 'Do you save a portion of your income regularly?', 'tooltip': 'quiz_save_regularly_tooltip', 'icon': '💰'},
            {'id': 'question_3', 'text_key': 'quiz_budget_monthly_label', 'text': 'Do you set a monthly budget?', 'tooltip': 'quiz_budget_monthly_tooltip', 'icon': '📝'},
            {'id': 'question_4', 'text_key': 'quiz_emergency_fund_label', 'text': 'Do you have an emergency fund?', 'tooltip': 'quiz_emergency_fund_tooltip', 'icon': '🚨'},
            {'id': 'question_5', 'text_key': 'quiz_invest_regularly_label', 'text': 'Do you invest your money regularly?', 'tooltip': 'quiz_invest_regularly_tooltip', 'icon': '📈'},
            {'id': 'question_6', 'text_key': 'quiz_spend_impulse_label', 'text': 'Do you often spend money on impulse?', 'tooltip': 'quiz_spend_impulse_tooltip', 'icon': '🛒'},
            {'id': 'question_7', 'text_key': 'quiz_financial_goals_label', 'text': 'Do you set financial goals?', 'tooltip': 'quiz_financial_goals_tooltip', 'icon': '🎯'},
            {'id': 'question_8', 'text_key': 'quiz_review_spending_label', 'text': 'Do you review your spending habits regularly?', 'tooltip': 'quiz_review_spending_tooltip', 'icon': '🔍'},
            {'id': 'question_9', 'text_key': 'quiz_multiple_income_label', 'text': 'Do you have multiple sources of income?', 'tooltip': 'quiz_multiple_income_tooltip', 'icon': '💼'},
            {'id': 'question_10', 'text_key': 'quiz_retirement_plan_label', 'text': 'Do you have a retirement savings plan?', 'tooltip': 'quiz_retirement_plan_tooltip', 'icon': '🏖️'},
        ]

        return render_template(
            'QUIZ/quiz_main.html',
            form=form,
            questions=questions,
            latest_record=latest_record,
            insights=latest_record.get('insights', []),
            tips=latest_record.get('tips', []),
            course_id=course_id,
            lang=lang,
            max_score=30,
            t=trans,
            tool_title=trans('quiz_title', default='Financial Quiz', lang=lang)
        )

    except Exception as e:
        logger.error(f"Error in quiz.main: {str(e)}", extra={'session_id': session['sid']})
        flash(trans('quiz_error_results', default='An error occurred while loading quiz. Please try again.', lang=lang), 'danger')
        return render_template(
            'QUIZ/quiz_main.html',
            form=form,
            questions=[],
            latest_record={},
            insights=[],
            tips=[],
            course_id=course_id,
            lang=lang,
            max_score=30,
            t=trans,
            tool_title=trans('quiz_title', default='Financial Quiz', lang=lang)
        ), 500