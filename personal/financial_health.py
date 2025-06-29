from flask import Blueprint, request, session, redirect, url_for, render_template, flash, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, SelectField, BooleanField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Optional, Email, ValidationError
from flask_login import current_user
from datetime import datetime
import uuid
import json
from mailersend_email import send_email, EMAIL_CONFIG
from translations import trans
from models import log_tool_usage
from session_utils import create_anonymous_session
from utils import requires_role, is_admin, get_mongo_db

financial_health_bp = Blueprint(
    'financial_health',
    __name__,
    template_folder='templates/HEALTHSCORE',
    url_prefix='/HEALTHSCORE'
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

def get_mongo_collection():
    return get_mongo_db()['financial_health_scores']

class FinancialHealthForm(FlaskForm):
    first_name = StringField(trans('general_first_name', default='First Name'))
    email = StringField(trans('general_email', default='Email'))
    user_type = SelectField(trans('financial_health_user_type', default='User Type'))
    send_email = BooleanField(trans('general_send_email', default='Send Email'))
    income = FloatField(trans('financial_health_monthly_income', default='Monthly Income'))
    expenses = FloatField(trans('financial_health_monthly_expenses', default='Monthly Expenses'))
    debt = FloatField(trans('financial_health_total_debt', default='Total Debt'))
    interest_rate = FloatField(trans('financial_health_average_interest_rate', default='Average Interest Rate'))
    submit = SubmitField(trans('financial_health_submit', default='Submit'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        
        # Set validators
        self.first_name.validators = [DataRequired(message=trans('general_first_name_required', lang=lang))]
        self.email.validators = [Optional(), Email(message=trans('general_email_invalid', lang=lang))]
        self.user_type.choices = [
            ('individual', trans('financial_health_user_type_individual', lang=lang)),
            ('business', trans('financial_health_user_type_business', lang=lang))
        ]
        self.income.validators = [
            DataRequired(message=trans('financial_health_income_required', lang=lang)),
            NumberRange(min=0, max=10000000000, message=trans('financial_health_income_max', lang=lang))
        ]
        self.expenses.validators = [
            DataRequired(message=trans('financial_health_expenses_required', lang=lang)),
            NumberRange(min=0, max=10000000000, message=trans('financial_health_expenses_max', lang=lang))
        ]
        self.debt.validators = [
            Optional(),
            NumberRange(min=0, max=10000000000, message=trans('financial_health_debt_max', lang=lang))
        ]
        self.interest_rate.validators = [
            Optional(),
            NumberRange(min=0, message=trans('financial_health_interest_rate_positive', lang=lang))
        ]

    def validate_income(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data)
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid income input: {field.data}")
                raise ValidationError(trans('financial_health_income_invalid', lang=session.get('lang', 'en')))

    def validate_expenses(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data)
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid expenses input: {field.data}")
                raise ValidationError(trans('financial_health_expenses_invalid', lang=session.get('lang', 'en')))

    def validate_debt(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data) if cleaned_data else None
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid debt input: {field.data}")
                raise ValidationError(trans('financial_health_debt_invalid', lang=session.get('lang', 'en')))

    def validate_interest_rate(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data) if cleaned_data else None
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid interest rate input: {field.data}")
                raise ValidationError(trans('financial_health_interest_rate_invalid', lang=session.get('lang', 'en')))

@financial_health_bp.route('/main', methods=['GET', 'POST'])
@custom_login_required
@requires_role(['personal', 'admin'])
def main():
    """Main financial health interface with tabbed layout."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
    lang = session.get('lang', 'en')
    
    # Initialize form with user data
    form_data = {}
    if current_user.is_authenticated:
        form_data['email'] = current_user.email
        form_data['first_name'] = current_user.username
    
    form = FinancialHealthForm(data=form_data)
    
    current_app.logger.info(f"Starting main for session {session['sid']} {'(anonymous)' if session.get('is_anonymous') else ''}")
    log_tool_usage(
        tool_name='financial_health',
        user_id=current_user.id if current_user.is_authenticated else None,
        session_id=session['sid'],
        action='main_view',
        mongo=get_mongo_db()
    )

    try:
        filter_criteria = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'calculate_score' and form.validate_on_submit():
                log_tool_usage(
                    tool_name='financial_health',
                    user_id=current_user.id if current_user.is_authenticated else None,
                    session_id=session['sid'],
                    action='calculate_score',
                    mongo=get_mongo_db()
                )

                debt = float(form.debt.data) if form.debt.data else 0
                interest_rate = float(form.interest_rate.data) if form.interest_rate.data else 0
                income = form.income.data
                expenses = form.expenses.data

                if income <= 0:
                    current_app.logger.error("Income is zero or negative, cannot calculate financial health metrics")
                    flash(trans("financial_health_income_zero_error", lang=lang), "danger")
                    return redirect(url_for('financial_health.main'))

                debt_to_income = (debt / income * 100) if income > 0 else 0
                savings_rate = ((income - expenses) / income * 100) if income > 0 else 0
                interest_burden = ((interest_rate * debt / 100) / 12) / income * 100 if debt > 0 and income > 0 else 0

                score = 100
                if debt_to_income > 0:
                    score -= min(debt_to_income / 50, 50)
                if savings_rate < 0:
                    score -= min(abs(savings_rate), 30)
                elif savings_rate > 0:
                    score += min(savings_rate / 2, 20)
                score -= min(interest_burden, 20)
                score = max(0, min(100, round(score)))

                if score >= 80:
                    status_key = "excellent"
                    status = trans("financial_health_status_excellent", lang=lang)
                elif score >= 60:
                    status_key = "good"
                    status = trans("financial_health_status_good", lang=lang)
                else:
                    status_key = "needs_improvement"
                    status = trans("financial_health_status_needs_improvement", lang=lang)

                badges = []
                if score >= 80:
                    badges.append(trans("financial_health_badge_financial_star", lang=lang))
                if debt_to_income < 20:
                    badges.append(trans("financial_health_badge_debt_manager", lang=lang))
                if savings_rate >= 20:
                    badges.append(trans("financial_health_badge_savings_pro", lang=lang))
                if interest_burden == 0 and debt > 0:
                    badges.append(trans("financial_health_badge_interest_free", lang=lang))

                collection = get_mongo_collection()
                record_data = {
                    '_id': str(uuid.uuid4()),
                    'user_id': current_user.id if current_user.is_authenticated else None,
                    'session_id': session['sid'],
                    'first_name': form.first_name.data,
                    'email': form.email.data,
                    'user_type': form.user_type.data,
                    'income': income,
                    'expenses': expenses,
                    'debt': debt,
                    'interest_rate': interest_rate,
                    'debt_to_income': debt_to_income,
                    'savings_rate': savings_rate,
                    'interest_burden': interest_burden,
                    'score': score,
                    'status': status,
                    'status_key': status_key,
                    'badges': badges,
                    'send_email': form.send_email.data,
                    'created_at': datetime.utcnow()
                }

                collection.insert_one(record_data)
                current_app.logger.info(f"Financial health data saved to MongoDB with ID {record_data['_id']} for session {session['sid']}")
                flash(trans("financial_health_completed_success", lang=lang), "success")

                # Send email if requested
                if form.send_email.data and form.email.data:
                    try:
                        config = EMAIL_CONFIG["financial_health"]
                        subject = trans(config["subject_key"], lang=lang)
                        template = config["template"]
                        send_email(
                            app=current_app,
                            logger=current_app.logger,
                            to_email=form.email.data,
                            subject=subject,
                            template_name=template,
                            data={
                                "first_name": form.first_name.data,
                                "score": score,
                                "status": status,
                                "income": income,
                                "expenses": expenses,
                                "debt": debt,
                                "interest_rate": interest_rate,
                                "debt_to_income": debt_to_income,
                                "savings_rate": savings_rate,
                                "interest_burden": interest_burden,
                                "badges": badges,
                                "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                "cta_url": url_for('financial_health.main', _external=True)
                            },
                            lang=lang
                        )
                    except Exception as e:
                        current_app.logger.error(f"Failed to send email: {str(e)}")
                        flash(trans("general_email_send_failed", lang=lang), "warning")

        # Get financial health data for display
        collection = get_mongo_collection()
        stored_records = list(collection.find(filter_criteria).sort('created_at', -1))
        
        if not stored_records:
            latest_record = {}
            records = []
        else:
            latest_record = stored_records[0]
            records = [(record['_id'], record) for record in stored_records]

        all_records = list(collection.find({}))
        all_scores_for_comparison = [record['score'] for record in all_records if record.get('score') is not None]

        total_users = len(all_scores_for_comparison)
        rank = 0
        average_score = 0
        if all_scores_for_comparison:
            all_scores_for_comparison.sort(reverse=True)
            user_score = latest_record.get("score", 0)
            rank = sum(1 for s in all_scores_for_comparison if s > user_score) + 1
            average_score = sum(all_scores_for_comparison) / total_users

        insights = []
        tips = [
            trans("financial_health_tip_track_expenses", lang=lang),
            trans("financial_health_tip_ajo_savings", lang=lang),
            trans("financial_health_tip_pay_debts", lang=lang),
            trans("financial_health_tip_plan_expenses", lang=lang)
        ]
        
        if latest_record:
            if latest_record.get('debt_to_income', 0) > 40:
                insights.append(trans("financial_health_insight_high_debt", lang=lang))
            if latest_record.get('savings_rate', 0) < 0:
                insights.append(trans("financial_health_insight_negative_savings", lang=lang))
            elif latest_record.get('savings_rate', 0) >= 20:
                insights.append(trans("financial_health_insight_good_savings", lang=lang))
            if latest_record.get('interest_burden', 0) > 10:
                insights.append(trans("financial_health_insight_high_interest", lang=lang))
            if total_users >= 5:
                if rank <= total_users * 0.1:
                    insights.append(trans("financial_health_insight_top_10", lang=lang))
                elif rank <= total_users * 0.3:
                    insights.append(trans("financial_health_insight_top_30", lang=lang))
                else:
                    insights.append(trans("financial_health_insight_below_30", lang=lang))
            else:
                insights.append(trans("financial_health_insight_not_enough_users", lang=lang))
        else:
            insights.append(trans("financial_health_insight_no_data", lang=lang))

        return render_template(
            'HEALTHSCORE/health_score_main.html',
            form=form,
            records=records,
            latest_record=latest_record,
            insights=insights,
            tips=tips,
            rank=rank,
            total_users=total_users,
            average_score=average_score,
            t=trans,
            lang=lang,
            tool_title=trans('financial_health_title', default='Financial Health Score', lang=lang)
        )

    except Exception as e:
        current_app.logger.exception(f"Critical error in main: {str(e)}")
        flash(trans("financial_health_dashboard_load_error", lang=lang), "danger")
        return render_template(
            'HEALTHSCORE/health_score_main.html',
            form=form,
            records=[],
            latest_record={},
            insights=[trans("financial_health_insight_no_data", lang=lang)],
            tips=[
                trans("financial_health_tip_track_expenses", lang=lang),
                trans("financial_health_tip_ajo_savings", lang=lang),
                trans("financial_health_tip_pay_debts", lang=lang),
                trans("financial_health_tip_plan_expenses", lang=lang)
            ],
            rank=0,
            total_users=0,
            average_score=0,
            t=trans,
            lang=lang,
            tool_title=trans('financial_health_title', default='Financial Health Score', lang=lang)
        ), 500