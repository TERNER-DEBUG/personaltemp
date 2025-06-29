from flask import Blueprint, request, session, redirect, url_for, render_template, flash, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, SelectField, BooleanField, IntegerField, HiddenField
from wtforms.validators import DataRequired, NumberRange, Email, Optional
from flask_login import current_user
from mailersend_email import send_email, EMAIL_CONFIG
from datetime import datetime, date, timedelta
import uuid
from translations import trans
from pymongo.errors import DuplicateKeyError
from bson import ObjectId
from utils import requires_role, is_admin, get_mongo_db
from models import log_tool_usage
from session_utils import create_anonymous_session

bill_bp = Blueprint(
    'bill',
    __name__,
    template_folder='templates/BILL',
    url_prefix='/BILL'
)

def strip_commas(value):
    """Remove commas from string values for numerical fields."""
    if isinstance(value, str):
        return value.replace(',', '')
    return value

def calculate_next_due_date(due_date, frequency):
    """Calculate the next due date based on frequency."""
    if frequency == 'weekly':
        return due_date + timedelta(days=7)
    elif frequency == 'monthly':
        return due_date + timedelta(days=30)
    elif frequency == 'quarterly':
        return due_date + timedelta(days=90)
    else:
        return due_date

def custom_login_required(f):
    """Custom login decorator that allows both authenticated users and anonymous sessions."""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.is_authenticated or session.get('is_anonymous', False):
            return f(*args, **kwargs)
        return redirect(url_for('users_blueprint.login', next=request.url))
    return decorated_function

class BillForm(FlaskForm):
    first_name = StringField(trans('general_first_name', default='First Name'))
    email = StringField(trans('general_email', default='Email'))
    bill_name = StringField(trans('bill_bill_name', default='Bill Name'))
    amount = FloatField(trans('bill_amount', default='Amount'), filters=[strip_commas])
    due_date = StringField(trans('bill_due_date', default='Due Date (YYYY-MM-DD)'))
    frequency = SelectField(trans('bill_frequency', default='Frequency'), coerce=str)
    category = SelectField(trans('general_category', default='Category'), coerce=str)
    status = SelectField(trans('bill_status', default='Status'), coerce=str)
    send_email = BooleanField(trans('general_send_email', default='Send Email Reminders'))
    reminder_days = IntegerField(trans('bill_reminder_days', default='Reminder Days'), default=7)
    csrf_token = HiddenField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        
        # Set up validators
        self.first_name.validators = [DataRequired(message=trans('general_first_name_required', lang))]
        self.email.validators = [DataRequired(message=trans('general_email_required', lang)), Email()]
        self.bill_name.validators = [DataRequired(message=trans('bill_bill_name_required', lang))]
        self.amount.validators = [DataRequired(message=trans('bill_amount_required', lang)), NumberRange(min=0, max=10000000000)]
        self.due_date.validators = [DataRequired(message=trans('bill_due_date_required', lang))]
        self.frequency.validators = [DataRequired(message=trans('bill_frequency_required', lang))]
        self.category.validators = [DataRequired(message=trans('bill_category_required', lang))]
        self.status.validators = [DataRequired(message=trans('bill_status_required', lang))]
        self.reminder_days.validators = [Optional(), NumberRange(min=1, max=30, message=trans('bill_reminder_days_required', lang))]

        # Set up choices
        self.frequency.choices = [
            ('one-time', trans('bill_frequency_one_time', lang)),
            ('weekly', trans('bill_frequency_weekly', lang)),
            ('monthly', trans('bill_frequency_monthly', lang)),
            ('quarterly', trans('bill_frequency_quarterly', lang))
        ]
        self.category.choices = [
            ('utilities', trans('bill_category_utilities', lang)),
            ('rent', trans('bill_category_rent', lang)),
            ('data_internet', trans('bill_category_data_internet', lang)),
            ('ajo_esusu_adashe', trans('bill_category_ajo_esusu_adashe', lang)),
            ('food', trans('bill_category_food', lang)),
            ('transport', trans('bill_category_transport', lang)),
            ('clothing', trans('bill_category_clothing', lang)),
            ('education', trans('bill_category_education', lang)),
            ('healthcare', trans('bill_category_healthcare', lang)),
            ('entertainment', trans('bill_category_entertainment', lang)),
            ('airtime', trans('bill_category_airtime', lang)),
            ('school_fees', trans('bill_category_school_fees', lang)),
            ('savings_investments', trans('bill_category_savings_investments', lang)),
            ('other', trans('general_other', lang))
        ]
        self.status.choices = [
            ('unpaid', trans('bill_status_unpaid', lang)),
            ('paid', trans('bill_status_paid', lang)),
            ('pending', trans('bill_status_pending', lang)),
            ('overdue', trans('bill_status_overdue', lang))
        ]

        # Set defaults
        self.frequency.default = self.frequency.choices[0][0]
        self.category.default = self.category.choices[0][0]
        self.status.default = self.status.choices[0][0]
        self.process()

@bill_bp.route('/main', methods=['GET', 'POST'])
@custom_login_required
@requires_role(['personal', 'admin'])
def main():
    """Main bill management interface with tabbed layout."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
    lang = session.get('lang', 'en')
    
    # Initialize form with user data
    form_data = {}
    if current_user.is_authenticated:
        form_data['email'] = current_user.email
        form_data['first_name'] = current_user.username
    
    form = BillForm(data=form_data)
    
    log_tool_usage(
        get_mongo_db(),
        tool_name='bill',
        user_id=current_user.id if current_user.is_authenticated else None,
        session_id=session['sid'],
        action='main_view'
    )

    tips = [
        trans('bill_tip_pay_early', lang),
        trans('bill_tip_energy_efficient', lang),
        trans('bill_tip_plan_monthly', lang),
        trans('bill_tip_ajo_reminders', lang),
        trans('bill_tip_data_topup', lang)
    ]

    try:
        filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        bills_collection = get_mongo_db().bills
        
        if request.method == 'POST':
            action = request.form.get('action')
            
            if action == 'add_bill' and form.validate_on_submit():
                log_tool_usage(
                    get_mongo_db(),
                    tool_name='bill',
                    user_id=current_user.id if current_user.is_authenticated else None,
                    session_id=session['sid'],
                    action='add_bill'
                )
                
                try:
                    due_date = datetime.strptime(form.due_date.data, '%Y-%m-%d').date()
                    if due_date < date.today():
                        flash(trans('bill_due_date_future_validation', lang), 'danger')
                        return redirect(url_for('bill.main'))
                except ValueError:
                    flash(trans('bill_due_date_format_invalid', lang), 'danger')
                    return redirect(url_for('bill.main'))

                status = form.status.data
                if status not in ['paid', 'pending'] and due_date < date.today():
                    status = 'overdue'

                bill_data = {
                    '_id': ObjectId(),
                    'user_id': current_user.id if current_user.is_authenticated else None,
                    'session_id': session['sid'],
                    'user_email': form.email.data,
                    'first_name': form.first_name.data,
                    'bill_name': form.bill_name.data,
                    'amount': float(form.amount.data),
                    'due_date': due_date.isoformat(),
                    'frequency': form.frequency.data,
                    'category': form.category.data,
                    'status': status,
                    'send_email': form.send_email.data,
                    'reminder_days': form.reminder_days.data if form.send_email.data else None
                }

                bills_collection.insert_one(bill_data)
                current_app.logger.info(f"Bill added successfully for {form.email.data}: {bill_data['bill_name']}")
                flash(trans('bill_added_success', lang), 'success')

                # Send email if requested
                if form.send_email.data and form.email.data:
                    try:
                        config = EMAIL_CONFIG['bill_reminder']
                        subject = trans(config['subject_key'], lang=lang)
                        template = config['template']
                        send_email(
                            app=current_app,
                            logger=current_app.logger,
                            to_email=form.email.data,
                            subject=subject,
                            template_name=template,
                            data={
                                'first_name': form.first_name.data,
                                'bills': [bill_data],
                                'cta_url': url_for('bill.main', _external=True),
                                'unsubscribe_url': url_for('bill.unsubscribe', email=form.email.data, _external=True)
                            },
                            lang=lang
                        )
                        current_app.logger.info(f"Email sent to {form.email.data}")
                    except Exception as e:
                        current_app.logger.error(f"Failed to send email: {str(e)}")
                        flash(trans('general_email_send_failed', lang), 'warning')

            elif action == 'update_bill':
                bill_id = request.form.get('bill_id')
                bill = bills_collection.find_one({'_id': ObjectId(bill_id), **filter_kwargs})
                if bill:
                    update_data = {
                        'frequency': request.form.get('frequency'),
                        'category': request.form.get('category'),
                        'status': request.form.get('status'),
                        'send_email': 'send_email' in request.form,
                        'reminder_days': int(request.form.get('reminder_days', 0)) if request.form.get('reminder_days') else None
                    }
                    bills_collection.update_one({'_id': ObjectId(bill_id), **filter_kwargs}, {'$set': update_data})
                    flash(trans('bill_updated_success', lang), 'success')
                else:
                    flash(trans('bill_not_found', lang), 'danger')

            elif action == 'delete_bill':
                bill_id = request.form.get('bill_id')
                bills_collection.delete_one({'_id': ObjectId(bill_id), **filter_kwargs})
                flash(trans('bill_deleted_success', lang), 'success')

            elif action == 'toggle_status':
                bill_id = request.form.get('bill_id')
                bill = bills_collection.find_one({'_id': ObjectId(bill_id), **filter_kwargs})
                if bill:
                    new_status = 'paid' if bill['status'] == 'unpaid' else 'unpaid'
                    bills_collection.update_one({'_id': ObjectId(bill_id), **filter_kwargs}, {'$set': {'status': new_status}})
                    
                    # Create recurring bill if marked as paid and not one-time
                    if new_status == 'paid' and bill['frequency'] != 'one-time':
                        try:
                            due_date = datetime.strptime(bill['due_date'], '%Y-%m-%d').date()
                            new_due_date = calculate_next_due_date(due_date, bill['frequency'])
                            new_bill = bill.copy()
                            new_bill['_id'] = ObjectId()
                            new_bill['due_date'] = new_due_date.isoformat()
                            new_bill['status'] = 'unpaid'
                            bills_collection.insert_one(new_bill)
                            flash(trans('bill_new_recurring_bill_success', lang).format(bill_name=bill['bill_name']), 'success')
                        except Exception as e:
                            current_app.logger.error(f"Error creating recurring bill: {str(e)}")
                    
                    flash(trans('bill_status_toggled_success', lang), 'success')

        # Get bills data for display
        bills = bills_collection.find(filter_kwargs)
        bills_data = [(str(bill['_id']), bill) for bill in bills]

        # Calculate statistics
        paid_count = unpaid_count = overdue_count = pending_count = 0
        total_paid = total_unpaid = total_overdue = total_bills = 0.0
        categories = {}
        due_today = due_week = due_month = upcoming_bills = []

        today = date.today()
        for b_id, bill in bills_data:
            try:
                bill_amount = float(bill['amount'])
                total_bills += bill_amount
                cat = bill['category']
                categories[cat] = categories.get(cat, 0) + bill_amount

                if bill['status'] == 'paid':
                    paid_count += 1
                    total_paid += bill_amount
                elif bill['status'] == 'unpaid':
                    unpaid_count += 1
                    total_unpaid += bill_amount
                elif bill['status'] == 'overdue':
                    overdue_count += 1
                    total_overdue += bill_amount
                elif bill['status'] == 'pending':
                    pending_count += 1

                try:
                    bill_due_date = datetime.strptime(bill['due_date'], '%Y-%m-%d').date()
                    if bill_due_date == today:
                        due_today.append((b_id, bill))
                    if today <= bill_due_date <= (today + timedelta(days=7)):
                        due_week.append((b_id, bill))
                    if today <= bill_due_date <= (today + timedelta(days=30)):
                        due_month.append((b_id, bill))
                    if today < bill_due_date:
                        upcoming_bills.append((b_id, bill))
                except ValueError:
                    current_app.logger.warning(f"Invalid due_date for bill {b_id}: {bill.get('due_date')}")
                    continue
            except (ValueError, TypeError):
                current_app.logger.warning(f"Invalid amount for bill {b_id}: {bill.get('amount')}")
                continue

        return render_template(
            'BILL/bill_main.html',
            form=form,
            bills=bills_data,
            paid_count=paid_count,
            unpaid_count=unpaid_count,
            overdue_count=overdue_count,
            pending_count=pending_count,
            total_paid=total_paid,
            total_unpaid=total_unpaid,
            total_overdue=total_overdue,
            total_bills=total_bills,
            categories=categories,
            due_today=due_today,
            due_week=due_week,
            due_month=due_month,
            upcoming_bills=upcoming_bills,
            tips=tips,
            t=trans,
            lang=lang,
            tool_title=trans('bill_title', default='Bill Manager', lang=lang)
        )

    except Exception as e:
        current_app.logger.exception(f"Error in bill.main: {str(e)}")
        flash(trans('bill_dashboard_load_error', lang), 'danger')
        return render_template(
            'BILL/bill_main.html',
            form=form,
            bills=[],
            paid_count=0,
            unpaid_count=0,
            overdue_count=0,
            pending_count=0,
            total_paid=0.0,
            total_unpaid=0.0,
            total_overdue=0.0,
            total_bills=0.0,
            categories={},
            due_today=[],
            due_week=[],
            due_month=[],
            upcoming_bills=[],
            tips=tips,
            t=trans,
            lang=lang,
            tool_title=trans('bill_title', default='Bill Manager', lang=lang)
        )

@bill_bp.route('/unsubscribe/<email>')
def unsubscribe(email):
    """Unsubscribe user from bill email notifications."""
    log_tool_usage(
        get_mongo_db(),
        tool_name='bill',
        user_id=current_user.id if current_user.is_authenticated else None,
        session_id=session.get('sid', str(uuid.uuid4())),
        action='unsubscribe'
    )
    try:
        lang = session.get('lang', 'en')
        bills_collection = get_mongo_db().bills
        bills_collection.update_many(
            {'user_email': email},
            {'$set': {'send_email': False}}
        )
        current_app.logger.info(f"Unsubscribed email: {email}")
        flash(trans('bill_unsubscribe_success', lang), 'success')
    except Exception as e:
        current_app.logger.error(f"Error unsubscribing email {email}: {str(e)}")
        flash(trans('bill_unsubscribe_failed', lang), 'danger')
    return redirect(url_for('index'))
