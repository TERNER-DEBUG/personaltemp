from flask import Blueprint, request, session, redirect, url_for, render_template, flash, current_app, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, IntegerField, SelectField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Optional, Email, NumberRange
from flask_login import current_user
from mailersend_email import send_email, EMAIL_CONFIG
from datetime import datetime
import uuid
import json
from translations import trans
from bson import ObjectId
from models import log_tool_usage
import os
from session_utils import create_anonymous_session
from utils import requires_role, is_admin, get_mongo_db

emergency_fund_bp = Blueprint(
    'emergency_fund',
    __name__,
    template_folder='templates/EMERGENCYFUND',
    url_prefix='/EMERGENCYFUND'
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

class CommaSeparatedFloatField(FloatField):
    def process_formdata(self, valuelist):
        if valuelist:
            try:
                self.data = float(valuelist[0].replace(',', ''))
            except ValueError:
                self.data = None
                raise ValueError(self.gettext('Not a valid number'))

class CommaSeparatedIntegerField(IntegerField):
    def process_formdata(self, valuelist):
        if valuelist:
            try:
                self.data = int(valuelist[0].replace(',', ''))
            except ValueError:
                self.data = None
                raise ValueError(self.gettext('Not a number'))

class EmergencyFundForm(FlaskForm):
    first_name = StringField(trans('general_first_name', default='First Name'), validators=[DataRequired()])
    email = StringField(trans('general_email', default='Email'), validators=[Optional(), Email()])
    email_opt_in = BooleanField(trans('general_send_email', default='Send Email'), default=False)
    monthly_expenses = CommaSeparatedFloatField(trans('emergency_fund_monthly_expenses', default='Monthly Expenses'), validators=[DataRequired(), NumberRange(min=0, max=10000000000)])
    monthly_income = CommaSeparatedFloatField(trans('emergency_fund_monthly_income', default='Monthly Income'), validators=[Optional(), NumberRange(min=0, max=10000000000)])
    current_savings = CommaSeparatedFloatField(trans('emergency_fund_current_savings', default='Current Savings'), validators=[Optional(), NumberRange(min=0, max=10000000000)])
    risk_tolerance_level = SelectField(trans('emergency_fund_risk_tolerance_level', default='Risk Tolerance Level'), validators=[DataRequired()], choices=[
        ('low', trans('emergency_fund_risk_tolerance_level_low', default='Low')), 
        ('medium', trans('emergency_fund_risk_tolerance_level_medium', default='Medium')), 
        ('high', trans('emergency_fund_risk_tolerance_level_high', default='High'))
    ])
    dependents = CommaSeparatedIntegerField(trans('emergency_fund_dependents', default='Dependents'), validators=[Optional(), NumberRange(min=0, max=100)])
    timeline = SelectField(trans('emergency_fund_timeline', default='Timeline'), validators=[DataRequired()], choices=[
        ('6', trans('emergency_fund_6_months', default='6 Months')), 
        ('12', trans('emergency_fund_12_months', default='12 Months')), 
        ('18', trans('emergency_fund_18_months', default='18 Months'))
    ])
    submit = SubmitField(trans('emergency_fund_calculate_button', default='Calculate'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        
        # Set validation messages
        self.first_name.validators[0].message = trans('general_first_name_required', lang=lang, default='Please enter your first name.')
        self.email.validators[1].message = trans('general_email_invalid', lang=lang, default='Please enter a valid email address.')
        self.monthly_expenses.validators[0].message = trans('emergency_fund_monthly_expenses_required', lang=lang, default='Please enter your monthly expenses.')
        self.monthly_expenses.validators[1].message = trans('emergency_fund_monthly_exceed', lang=lang, default='Amount exceeds maximum limit.')
        self.monthly_income.validators[1].message = trans('emergency_fund_monthly_exceed', lang=lang, default='Amount exceeds maximum limit.')
        self.current_savings.validators[1].message = trans('emergency_fund_savings_max', lang=lang, default='Amount exceeds maximum limit.')
        self.risk_tolerance_level.validators[0].message = trans('emergency_fund_risk_tolerance_required', lang=lang, default='Please select your risk tolerance.')
        self.dependents.validators[1].message = trans('emergency_fund_dependents_max', lang=lang, default='Number of dependents exceeds maximum.')
        self.timeline.validators[0].message = trans('emergency_fund_timeline_required', lang=lang, default='Please select a timeline.')

@emergency_fund_bp.route('/main', methods=['GET', 'POST'])
@custom_login_required
@requires_role(['personal', 'admin'])
def main():
    """Main emergency fund interface with tabbed layout."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session['permanent'] = True
        session['modified'] = True
    lang = session.get('lang', 'en')
    
    # Initialize form with user data
    form_data = {}
    if current_user.is_authenticated:
        form_data['email'] = current_user.email
        form_data['first_name'] = current_user.username
    
    form = EmergencyFundForm(data=form_data)
    
    log_tool_usage(
        tool_name='emergency_fund',
        user_id=current_user.id if current_user.is_authenticated else None,
        session_id=session['sid'],
        action='main_view',
        mongo=get_mongo_db()
    )

    try:
        filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'create_plan' and form.validate_on_submit():
                log_tool_usage(
                    tool_name='emergency_fund',
                    user_id=current_user.id if current_user.is_authenticated else None,
                    session_id=session['sid'],
                    action='create_plan',
                    mongo=get_mongo_db()
                )

                months = int(form.timeline.data)
                base_target = form.monthly_expenses.data * months
                recommended_months = months
                
                if form.risk_tolerance_level.data == 'high':
                    recommended_months = max(12, months)
                elif form.risk_tolerance_level.data == 'low':
                    recommended_months = min(6, months)
                
                if form.dependents.data and form.dependents.data >= 2:
                    recommended_months += 2
                
                target_amount = form.monthly_expenses.data * recommended_months
                gap = target_amount - (form.current_savings.data or 0)
                monthly_savings = gap / months if gap > 0 else 0
                
                percent_of_income = None
                if form.monthly_income.data and form.monthly_income.data > 0:
                    percent_of_income = (monthly_savings / form.monthly_income.data) * 100

                badges = []
                if form.timeline.data in ['6', '12']:
                    badges.append('Planner')
                if form.dependents.data and form.dependents.data >= 2:
                    badges.append('Protector')
                if gap <= 0:
                    badges.append('Steady Saver')
                if (form.current_savings.data or 0) >= target_amount:
                    badges.append('Fund Master')

                emergency_fund = {
                    '_id': str(uuid.uuid4()),
                    'user_id': current_user.id if current_user.is_authenticated else None,
                    'session_id': session['sid'],
                    'first_name': form.first_name.data,
                    'email': form.email.data,
                    'email_opt_in': form.email_opt_in.data,
                    'lang': lang,
                    'monthly_expenses': form.monthly_expenses.data,
                    'monthly_income': form.monthly_income.data,
                    'current_savings': form.current_savings.data or 0,
                    'risk_tolerance_level': form.risk_tolerance_level.data,
                    'dependents': form.dependents.data or 0,
                    'timeline': months,
                    'recommended_months': recommended_months,
                    'target_amount': target_amount,
                    'savings_gap': gap,
                    'monthly_savings': monthly_savings,
                    'percent_of_income': percent_of_income,
                    'badges': badges,
                    'created_at': datetime.utcnow()
                }
                
                get_mongo_db().emergency_funds.insert_one(emergency_fund)
                current_app.logger.info(f"Emergency fund record saved to MongoDB with ID {emergency_fund['_id']}")
                flash(trans('emergency_fund_completed_successfully', default='Emergency fund calculation completed successfully!'), 'success')

                # Send email if opted in
                if form.email_opt_in.data and form.email.data:
                    try:
                        config = EMAIL_CONFIG["emergency_fund"]
                        subject = trans(config["subject_key"], lang=lang)
                        template = config["template"]
                        send_email(
                            app=current_app,
                            logger=current_app.logger,
                            to_email=form.email.data,
                            subject=subject,
                            template_name=template,
                            data={
                                'first_name': form.first_name.data,
                                'lang': lang,
                                'monthly_expenses': form.monthly_expenses.data,
                                'monthly_income': form.monthly_income.data,
                                'current_savings': form.current_savings.data or 0,
                                'risk_tolerance_level': form.risk_tolerance_level.data,
                                'dependents': form.dependents.data or 0,
                                'timeline': months,
                                'recommended_months': recommended_months,
                                'target_amount': target_amount,
                                'savings_gap': gap,
                                'monthly_savings': monthly_savings,
                                'percent_of_income': percent_of_income,
                                'badges': badges,
                                'created_at': emergency_fund['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
                                'cta_url': url_for('emergency_fund.main', _external=True),
                                'unsubscribe_url': url_for('emergency_fund.unsubscribe', email=form.email.data, _external=True)
                            },
                            lang=lang
                        )
                    except Exception as e:
                        current_app.logger.error(f"Failed to send email: {str(e)}")
                        flash(trans("general_email_send_failed", lang=lang), "danger")

        # Get emergency fund data for display
        user_data = get_mongo_db().emergency_funds.find(filter_kwargs).sort('created_at', -1)
        user_data = list(user_data)
        current_app.logger.info(f"Retrieved {len(user_data)} records from MongoDB for user {current_user.id if current_user.is_authenticated else 'anonymous'}")

        if not user_data and current_user.is_authenticated and current_user.email:
            user_data = get_mongo_db().emergency_funds.find({'email': current_user.email}).sort('created_at', -1)
            user_data = list(user_data)
            current_app.logger.info(f"Retrieved {len(user_data)} records for email {current_user.email}")

        records = [(record['_id'], record) for record in user_data]
        latest_record = records[-1][1] if records else {}

        insights = []
        if latest_record:
            if latest_record.get('savings_gap', 0) <= 0:
                insights.append(trans('emergency_fund_insight_fully_funded', lang=lang))
            else:
                insights.append(trans('emergency_fund_insight_savings_gap', lang=lang,
                                    savings_gap=latest_record.get('savings_gap', 0),
                                    months=latest_record.get('timeline', 0)))
                if latest_record.get('percent_of_income') and latest_record.get('percent_of_income') > 30:
                    insights.append(trans('emergency_fund_insight_high_income_percentage', lang=lang))
                if latest_record.get('dependents', 0) > 2:
                    insights.append(trans('emergency_fund_insight_large_family', lang=lang,
                        recommended_months=latest_record.get('recommended_months', 0)))

        cross_tool_insights = []
        filter_kwargs_budget = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        budget_data = get_mongo_db().budgets.find(filter_kwargs_budget).sort('created_at', -1)
        budget_data = list(budget_data)
        if budget_data and latest_record and latest_record.get('savings_gap', 0) > 0:
            latest_budget = budget_data[0]
            if latest_budget.get('income') and latest_budget.get('fixed_expenses'):
                savings_possible = latest_budget['income'] - latest_budget['fixed_expenses']
                if savings_possible > 0:
                    cross_tool_insights.append(trans('emergency_fund_cross_tool_savings_possible', lang=lang,
                                                   amount=savings_possible))

        current_app.logger.info(f"Rendering main template")
        return render_template(
            'EMERGENCYFUND/emergency_fund_main.html',
            form=form,
            records=records,
            latest_record=latest_record,
            insights=insights,
            cross_tool_insights=cross_tool_insights,
            tips=[
                trans('emergency_fund_tip_automate_savings', lang=lang),
                trans('budget_tip_ajo_savings', lang=lang),
                trans('emergency_fund_tip_track_expenses', lang=lang),
                trans('budget_tip_monthly_savings', lang=lang)
            ],
            t=trans,
            lang=lang,
            tool_title=trans('emergency_fund_title', default='Emergency Fund Calculator', lang=lang)
        )

    except Exception as e:
        current_app.logger.error(f"Error in main: {str(e)}", exc_info=True)
        flash(trans('emergency_fund_load_dashboard_error', lang=lang), 'danger')
        return render_template(
            'EMERGENCYFUND/emergency_fund_main.html',
            form=form,
            records=[],
            latest_record={},
            insights=[],
            cross_tool_insights=[],
            tips=[
                trans('emergency_fund_tip_automate_savings', lang=lang),
                trans('budget_tip_ajo_savings', lang=lang),
                trans('emergency_fund_tip_track_expenses', lang=lang),
                trans('budget_tip_monthly_savings', lang=lang)
            ],
            t=trans,
            lang=lang,
            tool_title=trans('emergency_fund_title', default='Emergency Fund Calculator', lang=lang)
        ), 500

@emergency_fund_bp.route('/unsubscribe/<email>')
def unsubscribe(email):
    """Unsubscribe user from emergency fund emails."""
    try:
        lang = session.get('lang', 'en')
        log_tool_usage(
            tool_name='emergency_fund',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session.get('sid', str(uuid.uuid4())),
            action='unsubscribe',
            mongo=get_mongo_db()
        )
        filter_kwargs = {'email': email}
        if current_user.is_authenticated:
            filter_kwargs['user_id'] = current_user.id
        get_mongo_db().emergency_funds.update_many(
            filter_kwargs,
            {'$set': {'email_opt_in': False}}
        )
        flash(trans("emergency_fund_unsubscribed_success", lang=lang), "success")
    except Exception as e:
        current_app.logger.error(f"Error in emergency_fund.unsubscribe: {str(e)}", exc_info=True)
        flash(trans("emergency_fund_unsubscribe_error", lang=lang), "danger")
    return redirect(url_for('index'))