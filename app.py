from flask import (
    Flask,
    render_template,
    request,
    abort
)

from src.services.dashboard_service import (
    get_dashboard_summary
)

from src.services.vendor_service import (
    get_vendor_list,
    get_vendor_detail
)


# ==================================================
# FLASK APPLICATION SETUP
# ==================================================

app = Flask(
    __name__,
    template_folder="web/templates",
    static_folder="web/static"
)


# ==================================================
# DASHBOARD
# ==================================================

@app.route("/")
def dashboard():
    """
    Main Vendor Scorecard dashboard.

    Displays high-level scorecard metrics
    generated from the current data source.
    """

    summary = get_dashboard_summary()

    return render_template(
        "dashboard.html",
        summary=summary
    )


# ==================================================
# VENDOR SCORECARD LIST
# ==================================================

@app.route("/vendors")
def vendors():
    """
    Display all vendor/location scorecard rows.

    Supports optional vendor or location search
    using the ?q= query-string parameter.
    """

    search_text = request.args.get(
        "q",
        ""
    )

    vendor_rows = get_vendor_list(
        search_text
    )

    return render_template(
        "vendors.html",
        vendors=vendor_rows,
        search_text=search_text
    )


# ==================================================
# INDIVIDUAL VENDOR DETAIL
# ==================================================

@app.route("/vendors/detail")
def vendor_detail():
    """
    Display the detailed scorecard for one
    vendor/location combination.
    """

    vendor_name = request.args.get(
        "vendor",
        ""
    )

    vendor_city = request.args.get(
        "city",
        ""
    )

    vendor = get_vendor_detail(
        vendor_name,
        vendor_city
    )

    if vendor is None:
        abort(404)

    return render_template(
        "vendor_detail.html",
        vendor=vendor
    )


# ==================================================
# APPLICATION START
# ==================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )