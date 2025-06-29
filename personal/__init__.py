from flask import Blueprint
from flask_login import current_user
from utils import requires_role, is_admin

# Create the personal blueprint
personal_bp = Blueprint('personal', __name__, url_prefix='/personal', template_folder='templates')

def check_personal_access():
    """Check if user has access to personal finance tools"""
    return current_user.is_authenticated and (current_user.role == 'personal' or is_admin())

# Import all personal finance routes
from .bill import bill_bp
from .budget import budget_bp
from .emergency_fund import emergency_fund_bp
from .financial_health import financial_health_bp
from .learning_hub import learning_hub_bp
from .net_worth import net_worth_bp
from .quiz import quiz_bp

# Register all personal finance sub-blueprints
personal_bp.register_blueprint(bill_bp)
personal_bp.register_blueprint(budget_bp)
personal_bp.register_blueprint(emergency_fund_bp)
personal_bp.register_blueprint(financial_health_bp)
personal_bp.register_blueprint(learning_hub_bp)
personal_bp.register_blueprint(net_worth_bp)
personal_bp.register_blueprint(quiz_bp)

@personal_bp.before_request
def check_access():
    """Ensure only personal users and admins can access personal finance tools"""
    if not check_personal_access():
        from flask import redirect, url_for, flash
        from translations import trans
        flash(trans('general_access_denied', default='Access denied. Personal finance tools are only available to personal users.'), 'danger')
        return redirect(url_for('dashboard.index'))

# Export the blueprint
__all__ = ['personal_bp']
