from flask import Blueprint, request, session, redirect, url_for, render_template, flash, current_app, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, BooleanField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Optional, Email, ValidationError
from flask_login import current_user
from translations import trans
from mailersend_email import send_email, EMAIL_CONFIG
from datetime import datetime
import uuid
import json
from models import log_tool_usage
from session_utils import create_anonymous_session
from utils import requires_role, is_admin, get_mongo_db

net_worth_bp = Blueprint(
    'net_worth',
    __name__,
    template_folder='templates/NETWORTH',
    url_prefix='/NETWORTH'
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

class NetWorthForm(FlaskForm):
    first_name = StringField(trans('general_first_name', default='First Name'))
    email = StringField(trans('general_email', default='Email'))
    send_email = BooleanField(trans('general_send_email', default='Send Email'))
    cash_savings = FloatField(trans('net_worth_cash_savings', default='Cash Savings'))
    investments = FloatField(trans('net_worth_investments', default='Investments'))
    property = FloatField(trans('net_worth_property', default='Property'))
    loans = FloatField(trans('net_worth_loans', default='Loans'))
    submit = SubmitField(trans('net_worth_submit', default='Submit'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        
        # Set validators
        self.first_name.validators = [DataRequired(message=trans('general_first_name_required', lang=lang))]
        self.email.validators = [Optional(), Email(message=trans('general_email_invalid', lang=lang))]
        self.cash_savings.validators = [
            DataRequired(message=trans('net_worth_cash_savings_required', lang=lang)),
            NumberRange(min=0, max=10000000000, message=trans('net_worth_cash_savings_max', lang=lang))
        ]
        self.investments.validators = [
            DataRequired(message=trans('net_worth_investments_required', lang=lang)),
            NumberRange(min=0, max=10000000000, message=trans('net_worth_investments_max', lang=lang))
        ]
        self.property.validators = [
            DataRequired(message=trans('net_worth_property_required', lang=lang)),
            NumberRange(min=0, max=10000000000, message=trans('net_worth_property_max', lang=lang))
        ]
        self.loans.validators = [
            Optional(),
            NumberRange(min=0, max=10000000000, message=trans('net_worth_loans_max', lang=lang))
        ]

    def validate_cash_savings(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data)
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid cash_savings input: {field.data}", extra={'session_id': session.get('sid', 'unknown')})
                raise ValidationError(trans('net_worth_cash_savings_invalid', lang=session.get('lang', 'en')))

    def validate_investments(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data)
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid investments input: {field.data}", extra={'session_id': session.get('sid', 'unknown')})
                raise ValidationError(trans('net_worth_investments_invalid', lang=session.get('lang', 'en')))

    def validate_property(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data)
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid property input: {field.data}", extra={'session_id': session.get('sid', 'unknown')})
                raise ValidationError(trans('net_worth_property_invalid', lang=session.get('lang', 'en')))

    def validate_loans(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data) if cleaned_data else None
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid loans input: {field.data}", extra={'session_id': session.get('sid', 'unknown')})
                raise ValidationError(trans('net_worth_loans_invalid', lang=session.get('lang', 'en')))

@net_worth_bp.route('/main', methods=['GET', 'POST'])
@custom_login_required
@requires_role(['personal', 'admin'])
def main():
    """Main net worth interface with tabbed layout."""
    if 'sid' not in session:
        create_anonymous_session()
        current_app.logger.debug(f"New anonymous session created with sid: {session['sid']}", extra={'session_id': session['sid']})
    lang = session.get('lang', 'en')
    
    # Initialize form with user data
    form_data = {}
    if current_user.is_authenticated:
        form_data['email'] = current_user.email
        form_data['first_name'] = current_user.username
    
    form = NetWorthForm(data=form_data)
    
    log_tool_usage(
        tool_name='net_worth',
        user_id=current_user.id if current_user.is_authenticated else None,
        session_id=session['sid'],
        action='main_view',
        mongo=get_mongo_db()
    )

    try:
        filter_criteria = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'calculate_net_worth' and form.validate_on_submit():
                log_tool_usage(
                    tool_name='net_worth',
                    user_id=current_user.id if current_user.is_authenticated else None,
                    session_id=session['sid'],
                    action='calculate_net_worth',
                    mongo=get_mongo_db()
                )

                cash_savings = form.cash_savings.data
                investments = form.investments.data
                property = form.property.data
                loans = form.loans.data or 0

                total_assets = cash_savings + investments + property
                total_liabilities = loans
                net_worth = total_assets - total_liabilities

                badges = []
                if net_worth > 0:
                    badges.append('net_worth_badge_wealth_builder')
                if total_liabilities == 0:
                    badges.append('net_worth_badge_debt_free')
                if cash_savings >= total_assets * 0.3:
                    badges.append('net_worth_badge_savings_champion')
                if property >= total_assets * 0.5:
                    badges.append('net_worth_badge_property_mogul')

                net_worth_record = {
                    '_id': str(uuid.uuid4()),
                    'user_id': current_user.id if current_user.is_authenticated else None,
                    'session_id': session['sid'],
                    'first_name': form.first_name.data,
                    'email': form.email.data,
                    'send_email': form.send_email.data,
                    'cash_savings': cash_savings,
                    'investments': investments,
                    'property': property,
                    'loans': loans,
                    'total_assets': total_assets,
                    'total_liabilities': total_liabilities,
                    'net_worth': net_worth,
                    'badges': badges,
                    'created_at': datetime.utcnow()
                }
                
                get_mongo_db().net_worth_data.insert_one(net_worth_record)
                current_app.logger.info(f"Successfully saved record {net_worth_record['_id']} for session {session['sid']}")
                flash(trans("net_worth_success", lang=lang), "success")

                # Send email if requested
                if form.send_email.data and form.email.data:
                    try:
                        config = EMAIL_CONFIG["net_worth"]
                        subject = trans(config["subject_key"], lang=lang)
                        template = config["template"]
                        send_email(
                            app=current_app,
                            logger=current_app.logger,
                            to_email=form.email.data,
                            subject=subject,
                            template_name=template,
                            data={
                                "first_name": net_worth_record['first_name'],
                                "cash_savings": net_worth_record['cash_savings'],
                                "investments": net_worth_record['investments'],
                                "property": net_worth_record['property'],
                                "loans": net_worth_record['loans'],
                                "total_assets": net_worth_record['total_assets'],
                                "total_liabilities": net_worth_record['total_liabilities'],
                                "net_worth": net_worth_record['net_worth'],
                                "badges": net_worth_record['badges'],
                                "created_at": net_worth_record['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
                                "cta_url": url_for('net_worth.main', _external=True),
                                "unsubscribe_url": url_for('net_worth.unsubscribe', email=form.email.data, _external=True)
                            },
                            lang=lang
                        )
                    except Exception as e:
                        current_app.logger.error(f"Failed to send email: {str(e)}")
                        flash(trans("general_email_send_failed", lang=lang), "warning")

        # Get net worth data for display
        user_records = get_mongo_db().net_worth_data.find(filter_criteria).sort('created_at', -1)
        user_data = [(record['_id'], record) for record in user_records]

        # Fallback to email for authenticated users
        if not user_data and current_user.is_authenticated and current_user.email:
            user_records = get_mongo_db().net_worth_data.find({'email': current_user.email}).sort('created_at', -1)
            user_data = [(record['_id'], record) for record in user_records]

        latest_record = user_data[-1][1] if user_data else {}
        records = user_data

        insights = []
        tips = [
            trans("net_worth_tip_track_ajo", lang=lang),
            trans("net_worth_tip_review_property", lang=lang),
            trans("net_worth_tip_pay_loans_early", lang=lang),
            trans("net_worth_tip_diversify_investments", lang=lang)
        ]

        if latest_record:
            if latest_record.get('total_liabilities', 0) > latest_record.get('total_assets', 0) * 0.5:
                insights.append(trans("net_worth_insight_high_loans", lang=lang))
            if latest_record.get('cash_savings', 0) < latest_record.get('total_assets', 0) * 0.1:
                insights.append(trans("net_worth_insight_low_cash", lang=lang))
            if latest_record.get('investments', 0) >= latest_record.get('total_assets', 0) * 0.3:
                insights.append(trans("net_worth_insight_strong_investments", lang=lang))
            if latest_record.get('net_worth', 0) <= 0:
                insights.append(trans("net_worth_insight_negative_net_worth", lang=lang))

        current_app.logger.info(f"Main rendering with {len(records)} records for session {session['sid']}")
        return render_template(
            'NETWORTH/net_worth_main.html',
            form=form,
            records=records,
            latest_record=latest_record,
            insights=insights,
            tips=tips,
            t=trans,
            lang=lang,
            tool_title=trans('net_worth_title', default='Net Worth Calculator', lang=lang)
        )

    except Exception as e:
        current_app.logger.error(f"Error in net_worth.main: {str(e)}", extra={'session_id': session.get('sid', 'unknown')})
        flash(trans("net_worth_dashboard_load_error", lang=lang), "danger")
        return render_template(
            'NETWORTH/net_worth_main.html',
            form=form,
            records=[],
            latest_record={},
            insights=[],
            tips=[
                trans("net_worth_tip_track_ajo", lang=lang),
                trans("net_worth_tip_review_property", lang=lang),
                trans("net_worth_tip_pay_loans_early", lang=lang),
                trans("net_worth_tip_diversify_investments", lang=lang)
            ],
            t=trans,
            lang=lang,
            tool_title=trans('net_worth_title', default='Net Worth Calculator', lang=lang)
        ), 500

@net_worth_bp.route('/unsubscribe/<email>')
@custom_login_required
def unsubscribe(email):
    """Unsubscribe user from net worth emails using MongoDB."""
    if 'sid' not in session:
        create_anonymous_session()
        current_app.logger.debug(f"New anonymous session created with sid: {session['sid']}", extra={'session_id': session['sid']})
    lang = session.get('lang', 'en')
    try:
        log_tool_usage(
            tool_name='net_worth',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='unsubscribe',
            mongo=get_mongo_db()
        )
        result = get_mongo_db().net_worth_data.update_many(
            {'email': email, 'user_id': current_user.id if current_user.is_authenticated else {'$exists': False}},
            {'$set': {'send_email': False}}
        )
        if result.modified_count > 0:
            flash(trans("net_worth_unsubscribed_success", lang=lang), "success")
        else:
            flash(trans("net_worth_unsubscribe_failed", lang=lang), "danger")
            current_app.logger.error(f"Failed to unsubscribe email {email}")
        return redirect(url_for('index'))
    except Exception as e:
        current_app.logger.exception(f"Error in net_worth.unsubscribe: {str(e)}")
        flash(trans("net_worth_unsubscribe_error", lang=lang), "danger")
        return redirect(url_for('index'))