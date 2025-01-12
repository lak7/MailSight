# pylint: disable=unused-import, missing-timeout, import-error, no-value-for-parameter

"""
Routes and views for the flask application.

Routes:
    - /index or /
    - /tracklist
    - /track?utm_id=<utm_id>
    - /login
    - /logout
    - /tracking_data/<utm_id>
    - /apphealth
"""

import os
import uuid
from collections import defaultdict
from datetime import datetime as dt
from datetime import timedelta

import pytz
import requests
from firebase_admin import auth, db, exceptions
from dotenv import load_dotenv
from flask import (
    abort,
    flash,
    get_flashed_messages,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

import forms
from app import app

load_dotenv()

TIMEZONE = os.environ["TIMEZONE"]


@app.route("/", methods=["GET", "POST"])
@app.route("/index/", methods=["GET", "POST"])
def index():
    """
    Returns Home page of the site. An authenticated user can generate new tracking links,
    Otherwise, user will be returned to /login page.
    """
    form = forms.GenerateTrackingLink()
    if form.validate_on_submit():
        utm_id = uuid.uuid4()
        generated_on = str(dt.now().astimezone(pytz.timezone(TIMEZONE)))

        # create a new tracking record for user on realtime database
        ref_1 = db.reference(f"/MailTrackData/Users/{session['uid']}")
        ref_1.child(str(utm_id)).set(
            {
                "MailTitle": form.email_title.data,
                "MailAddress": form.email_address.data,
                "GeneratedOn": generated_on,
            }
        )

        # create a new link hits record for user on realtime database
        ref_2 = db.reference("/MailTrackData/LinkHits")
        ref_2.update(
            {
                str(utm_id): "",
            }
        )

        flash("Tracking link successfully generated!")
        return redirect(url_for("tracking_data", utm_id=utm_id), 303)
    return render_template("index.html", form=form)


@app.route("/tracklist")
def tracklist():
    """
    Returns all the tracking links generated by an authenticated user - If there're no
    active records, redirects to /index. Otherwise, user will be returned to /login page.
    """
    # get all tracking records for the user
    ref_1 = db.reference(f"/MailTrackData/Users/{session['uid']}")
    tracking_list = ref_1.get()

    if tracking_list:
        # get all link hits for the user
        ref_2 = db.reference("/MailTrackData/LinkHits/")
        link_hits = ref_2.get()
        for utm_id in tracking_list:
            try:
                tracking_list[utm_id]["Hits"] = len(link_hits[utm_id])
            except KeyError:
                pass

        # Convert string dates to datetime objects
        for item in tracking_list.values():
            item["GeneratedOn"] = dt.strptime(
                item["GeneratedOn"], "%Y-%m-%d %H:%M:%S.%f%z"
            )

        # Create a defaultdict to store items by year and month
        sorted_tracking = defaultdict(lambda: defaultdict(list))

        # Group items by year and month
        for key, value in tracking_list.items():
            year = value["GeneratedOn"].year
            month = value["GeneratedOn"].month
            sorted_tracking[year][month].append((key, value))

        # Sort items within each month by GeneratedOn in descending order
        for year, months in sorted_tracking.items():
            for month, items in months.items():
                sorted_tracking[year][month] = sorted(
                    items, key=lambda x: x[1]["GeneratedOn"], reverse=True
                )

        # Sort months in descending order by their numeric representations
        sorted_tracking = dict(sorted(sorted_tracking.items(), reverse=True))

        month_names = [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]

        sorted_tracking_with_months = {}
        for year, months in sorted_tracking.items():
            sorted_tracking_with_months[year] = {
                month_names[month]: items for month, items in months.items()
            }

        return render_template(
            "track_list.html",
            tracking_list=sorted_tracking_with_months,
            search_mode=False,
        )
    else:
        app.logger.warning("/tracklist - No tracking records found!")
        flash("Sorry, No tracking records found! - Let's generate a one!")
        return redirect(url_for("index"), 303)


@app.route("/track")
def track():
    """
    Serve tracking pixel upon requested UTM ID.
     - Invalid UTM IDs >> Return [400] response
     - Authenticated user >> Return image without DB operations
     - Unauthenticated user >> Return image after DB operations
    """
    try:
        utm_id = request.args["utm_id"]
    except KeyError:
        app.logger.warning("/track - UTM ID argument is missing!")
        abort(400)

    FILENAME = "static/OI-pixel.gif"  # pylint: disable=invalid-name  # noqa

    if utm_id:
        # get all utm ids from realtime database for all users
        ref_1 = db.reference("/MailTrackData/LinkHits")
        all_hits = ref_1.get()

        # check if requested utm id is valid
        try:
            all_hits[utm_id]
        except KeyError:
            app.logger.warning("/track - User provided an invalid UTM ID!")
            abort(400)

        try:
            active_session_id = session["uid"]
            ref_2 = db.reference(f"/MailTrackData/Users/{active_session_id}")
            user_data = ref_2.get()
            user_utm_ids = list(user_data.keys())

            if utm_id in user_utm_ids:
                return send_file(FILENAME, mimetype="image/gif", max_age=0)

            raise KeyError

        except KeyError:
            # not a valid session id, so create a link hit record
            ip_address = request.headers.get("X-Forwarded-For") or request.remote_addr
            header = request.headers["User-Agent"]
            accessed_on = str(dt.now().astimezone(pytz.timezone(TIMEZONE)))

            # save the data to realtime database
            ref_2 = db.reference(f"/MailTrackData/LinkHits/{utm_id}/")
            ref_2.push().set(
                {
                    "IP": ip_address,
                    "UserAgent": header,
                    "AccessedOn": accessed_on,
                }
            )

    else:
        app.logger.warning("/track - User provided an invalid UTM ID!")
        abort(400)

    return send_file(FILENAME, mimetype="image/gif", max_age=0)


@app.route("/tracking-data/<utm_id>")
def tracking_data(utm_id):
    """Returns information from realtime database about a specific UTM ID."""
    # get all tracking records for the user
    ref_1 = db.reference(f"/MailTrackData/Users/{session['uid']}/{utm_id}")
    tracking_list = ref_1.get()

    if tracking_list:
        # get all link hits for the user
        ref_2 = db.reference(f"/MailTrackData/LinkHits/{utm_id}")
        link_hits = ref_2.get()
        return render_template(
            "tracking_data.html", data=tracking_list, link_hits=link_hits, utm_id=utm_id
        )

    app.logger.warning("/tracking-data - User provided an invalid UTM ID!")
    flash("Sorry, Not a valid UTM id!")
    return redirect(url_for("tracklist"), 303)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Handle user login."""
    session_cookie = request.cookies.get("secure-session")
    # check for valid & active session cookie
    if session_cookie:
        try:
            auth.verify_session_cookie(session_cookie, check_revoked=True)
            flash(message="You're already logged in!")
            return redirect(url_for("index"), 303)
        except auth.RevokedSessionCookieError:
            app.logger.warning("/login - Request denied due to revoked session cookie!")
            abort(
                status=401,
                description="Session cookie has been revoked."
                "Try clearing your browser cookies and logging in again.",
            )

    form = forms.LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data

        # Sign in and validate the user.
        try:
            payload = {
                "email": username,
                "password": password,
                "returnSecureToken": True,
            }
            api_key = os.environ["FIREBASE_API_KEY"]
            sign_in_response = requests.post(
                f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}",  # pylint: disable=line-too-long
                data=payload,
            )

            if sign_in_response.status_code == 200:
                expires_in = timedelta(days=14)
                session_cookie = auth.create_session_cookie(
                    sign_in_response.json()["idToken"], expires_in=expires_in
                )
                response = make_response(redirect(url_for("index"), 303))
                response.set_cookie(
                    "secure-session",
                    session_cookie,
                    max_age=expires_in.total_seconds(),
                    httponly=True,
                    secure=True,
                    samesite="Strict",
                )
                app.logger.info("/login - User logged in successfully!")
                return response

            app.logger.warning("/login - User provided invalid credentials!")
            flash("Invalid username or password!")
            return redirect(url_for("login"), 303)

        except (
            ValueError,
            requests.exceptions.RequestException,
            exceptions.FirebaseError,
        ):
            app.logger.error("/login - Unable to sign in user!")
            abort(
                status=503,
                description="Service temporarily unavailable, try again later.",
            )

    return render_template("login.html", form=form)


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """Handle user logout."""
    session_cookie = request.cookies.get("secure-session")
    try:
        decoded_claims = auth.verify_session_cookie(session_cookie)
        auth.revoke_refresh_tokens(decoded_claims["uid"])
        response = make_response(redirect(url_for("login"), 303))
        response.set_cookie("secure-session", "", expires=0)
    except auth.InvalidSessionCookieError:
        app.logger.warning("/logout - Request denied due to invalid session cookie!")
        return redirect(url_for("login"), 303)

    app.logger.info("/logout - User logged out successfully!")
    flash("Successfully Logged Out! - See you soon...")
    return response


@app.route("/apphealth")
def app_health():
    """App health check - Returns a 200 response"""
    app.logger.info("/apphealth - App health check successful!")
    response = make_response("OK", 200)
    return response


@app.errorhandler(404)
def page_not_found(error):  # pylint: disable=unused-argument
    """Handle 404 errors by rendering a custom 404 page."""
    app.logger.warning("/404 - Invalid URL requested by user, redirecting to 404 page")
    return render_template("404.html"), 404