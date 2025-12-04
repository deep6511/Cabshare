import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, IntegerField, SubmitField, DateTimeLocalField
from wtforms.validators import DataRequired, EqualTo, InputRequired
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import openrouteservice
from shapely.geometry import Point, LineString
import requests


ORS_API_KEY = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjYxY2UyNTY2ZTIyNDRmNzY5YWQzZDRjZWFkODc0MDFlIiwiaCI6Im11cm11cjY0In0="
ORS_CLIENT = openrouteservice.Client(key=ORS_API_KEY)
app = Flask(__name__)



app.config['SECRET_KEY'] = 'sdf7@#42kj8sd!0932lkajsd'

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'cabshare_v2.db')


db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    phone_number = db.Column(db.String(20), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    college_year = db.Column(db.Integer, nullable=False)
    rides = db.relationship('RideRequest', backref='requester', lazy=True)

# ADD THIS CLASS BACK INTO YOUR FILE
class RideRequestForm(FlaskForm):
    origin = StringField('Origin (e.g., "Chennai")', validators=[DataRequired()])
    destination = StringField('Destination (e.g., "Bangalore")', validators=[DataRequired()])
    travel_datetime = DateTimeLocalField('Travel Date and Time', format='%Y-%m-%dT%H:%M', validators=[InputRequired()])
    preference = SelectField('Preference', choices=[
        ('any', 'Any'),
        ('girls_only', 'Girls Only'),
        ('boys_only', 'Boys Only'),
        ('year_1', '1st Years Only'),
        ('year_2', '2nd Years Only'),
        ('year_3', '3rd Years Only'),
        ('year_4', '4th+ Years Only')
    ], validators=[DataRequired()])
    submit = SubmitField('Find a Ride')

class MatchGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rides = db.relationship('RideRequest', backref='match_group', lazy=True)
    messages = db.relationship('Message', backref='group', lazy=True, order_by='Message.timestamp')



class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_group_id = db.Column(db.Integer, db.ForeignKey('match_group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    text = db.Column(db.String(500), nullable=False)

    user = db.relationship('User')





class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    phone_number = StringField('Phone Number (Not Shared)', validators=[DataRequired()])
    gender = SelectField('Gender', choices=[('male', 'Male'), ('female', 'Female'), ('other', 'Other')],
                         validators=[DataRequired()])
    college_year = IntegerField('College Year (e.g., 1, 2, 3, 4)', validators=[DataRequired()])
    submit = SubmitField('Register')

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


# In app.py - A change to our model
class RideRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # The original text input
    origin_text = db.Column(db.String(100), nullable=False)
    destination_text = db.Column(db.String(100), nullable=False)

    # The NEW geocoded data
    origin_lat = db.Column(db.Float, nullable=False)
    origin_lon = db.Column(db.Float, nullable=False)
    destination_lat = db.Column(db.Float, nullable=False)
    destination_lon = db.Column(db.Float, nullable=False)

    travel_datetime = db.Column(db.DateTime, nullable=False)
    preference = db.Column(db.String(20), default='any')

    is_matched = db.Column(db.Boolean, default=False)
    match_group_id = db.Column(db.Integer, db.ForeignKey('match_group.id'), nullable=True)

    # Helper property to make code cleaner
    @property
    def origin_coords(self):
        return (self.origin_lat, self.origin_lon)

    @property
    def destination_coords(self):
        return (self.destination_lat, self.destination_lon)

class MessageForm(FlaskForm):
    text = StringField('Message', validators=[DataRequired()])
    submit = SubmitField('Send')


def get_coordinates(location_name):
    """
    Converts a text location (e.g., "Chennai") into
    (latitude, longitude) coordinates.
    """
    try:
        # 'boundary.country': 'IND' limits results to India
        geocode_result = ORS_CLIENT.geocode(location_name, boundary_country=['IND'], size=1)

        if not geocode_result or not geocode_result['features']:
            return None  # No result found

        # ORS returns (longitude, latitude)
        coords_lon_lat = geocode_result['features'][0]['geometry']['coordinates']

        # We store as (latitude, longitude)
        # REVERSED!
        return (coords_lon_lat[1], coords_lon_lat[0])

    except Exception as e:
        print(f"Error geocoding {location_name}: {e}")
        return None


def is_route_partial_match(main_ride, check_ride):
    """
    Checks if check_ride's origin AND destination are
    "on the way" of main_ride's route.

    main_ride: The RideRequest object for the longer route (e.g., Chennai-Bangalore)
    check_ride: The RideRequest object for the shorter route (e.g., Chennai-Vellore)
    """
    try:
        # 1. Get coordinates from the ride objects
        #    (We already have them!)
        #    Note: ORS client needs (longitude, latitude)
        main_origin_lonlat = (main_ride.origin_lon, main_ride.origin_lat)
        main_dest_lonlat = (main_ride.destination_lon, main_ride.destination_lat)

        check_origin_lonlat = (check_ride.origin_lon, check_ride.origin_lat)
        check_dest_lonlat = (check_ride.destination_lon, check_ride.destination_lat)

        # 2. Get the full route geometry for the LONGER ride
        main_route_request = {
            'coordinates': [main_origin_lonlat, main_dest_lonlat],
            'format': 'geojson',
            'profile': 'driving-car'
        }
        main_route_geojson = ORS_CLIENT.directions(**main_route_request)

        # 3. Create a 'LineString' (a path) from the route
        route_geometry = main_route_geojson['features'][0]['geometry']['coordinates']
        main_route_line = LineString(route_geometry)

        # 4. Create 'Points' for the shorter ride's start/end
        check_origin_point = Point(check_origin_lonlat)
        check_dest_point = Point(check_dest_lonlat)

        # 5. Check if these points are "near" the main route line
        #    'distance(0.1)' is about 10km. This is the "fudge factor".
        is_origin_near = main_route_line.distance(check_origin_point) < 0.1
        is_dest_near = main_route_line.distance(check_dest_point) < 0.1

        return is_origin_near and is_dest_near

    except Exception as e:
        print(f"Error during ORS check: {e}")
        return False  # Fail safe


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'danger')
    return render_template('login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = generate_password_hash(form.password.data, method='pbkdf2:sha256')
        new_user = User(
            username=form.username.data,
            password_hash=hashed_password,
            phone_number=form.phone_number.data,
            gender=form.gender.data,
            college_year=form.college_year.data
        )
        db.session.add(new_user)
        try:
            db.session.commit()
            flash('Account created! Please login.', 'success')
            return redirect(url_for('login'))
        except:
            db.session.rollback()
            flash('Username already exists.', 'danger')
    return render_template('register.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/')
@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    form = RideRequestForm()
    if request.method == 'POST':  # Change this line
        # Get data directly from request.form (not from WTForms)
        origin_text = request.form.get('origin', '').strip()
        dest_text = request.form.get('destination', '').strip()

        # Get coordinates from the hidden fields
        origin_lat = request.form.get('origin_lat')
        origin_lon = request.form.get('origin_lon')
        dest_lat = request.form.get('destination_lat')
        dest_lon = request.form.get('destination_lon')

        # Debug print
        print(f"📍 Origin: {origin_text} ({origin_lat}, {origin_lon})")
        print(f"🎯 Dest: {dest_text} ({dest_lat}, {dest_lon})")

        # Check if coordinates were provided
        if not origin_lat or not origin_lon or not dest_lat or not dest_lon:
            flash('Please select a location from the dropdown suggestions.', 'danger')
            return redirect(url_for('dashboard'))

        # Convert to float
        try:
            origin_coords = (float(origin_lat), float(origin_lon))
            dest_coords = (float(dest_lat), float(dest_lon))
        except ValueError:
            flash('Invalid coordinates. Please select from dropdown.', 'danger')
            return redirect(url_for('dashboard'))

        # Get the other form fields
        travel_datetime_str = request.form.get('travel_datetime')
        preference = request.form.get('preference')

        # Parse the datetime
        try:
            travel_datetime = datetime.strptime(travel_datetime_str, '%Y-%m-%dT%H:%M')
        except:
            flash('Invalid date/time format.', 'danger')
            return redirect(url_for('dashboard'))

        new_ride = RideRequest(
            user_id=current_user.id,
            origin_text=origin_text,
            destination_text=dest_text,
            origin_lat=origin_coords[0],
            origin_lon=origin_coords[1],
            destination_lat=dest_coords[0],
            destination_lon=dest_coords[1],
            travel_datetime=travel_datetime,
            preference=preference
        )

        match_found = find_and_create_match(new_ride)
        if match_found:
            flash('Match Found! Check the "My Matches" page.', 'success')
        else:
            flash('Ride request submitted. We will notify you when a match is found.', 'info')
        return redirect(url_for('dashboard'))

    pending_rides = RideRequest.query.filter_by(user_id=current_user.id, is_matched=False).all()
    return render_template('dashboard.html', form=form, pending_rides=pending_rides)


@app.route('/matches')
@login_required
def matches():

    my_groups = MatchGroup.query.join(RideRequest).filter(RideRequest.user_id == current_user.id).all()
    return render_template('matches.html', groups=my_groups)


@app.route('/matches/<int:group_id>', methods=['GET', 'POST'])
@login_required
def match_group(group_id):
    group = MatchGroup.query.get_or_404(group_id)


    user_ids_in_group = [ride.user_id for ride in group.rides]
    if current_user.id not in user_ids_in_group:
        flash('You do not have access to this group.', 'danger')
        return redirect(url_for('matches'))

    form = MessageForm()
    if form.validate_on_submit():
        new_message = Message(
            match_group_id=group.id,
            user_id=current_user.id,
            text=form.text.data
        )
        db.session.add(new_message)
        db.session.commit()
        return redirect(url_for('match_group', group_id=group_id))


    members = [ride.requester for ride in group.rides if ride.user_id != current_user.id]

    return render_template('match_group.html', group=group, members=members, form=form)


@app.route("/test_ors")
def test_ors():
    url = "https://api.openrouteservice.org/geocode/autocomplete"
    params = {
        "api_key": ORS_API_KEY,
        "text": "kol"
    }

    response = requests.get(url, params=params)

    print("STATUS:", response.status_code)
    print("RAW RESPONSE:", response.text)

    return response.json()


@app.route('/autocomplete')
def autocomplete():
    query = request.args.get('q')

    # ADD DEBUGGING
    print(f"🔍 Searching for: {query}")

    url = "https://api.openrouteservice.org/geocode/autocomplete"
    params = {
        "api_key": ORS_API_KEY,
        "text": query,
        "boundary.country": "IND"
    }

    # ADD DEBUGGING
    print(f"📡 Request URL: {url}")
    print(f"📋 Params: {params}")

    try:
        response = requests.get(url, params=params)

        # ADD DEBUGGING
        print(f"✅ Status Code: {response.status_code}")
        print(f"📦 Response: {response.text[:200]}")  # First 200 chars

        data = response.json()

        suggestions = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            label = props.get("label")
            coords = f.get("geometry", {}).get("coordinates", [])

            if label and coords:
                suggestions.append({
                    "label": label,
                    "lat": coords[1],
                    "lon": coords[0]
                })

        print(f"🎯 Found {len(suggestions)} suggestions")
        return {"results": suggestions}

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return {"results": [], "error": str(e)}, 500

def find_and_create_match(new_ride):
    """
    Finds a match for the new_ride. If found, creates a
    MatchGroup and links them. If not, just adds the new_ride.
    Returns True if a match was made, False otherwise.
    """

    max_time_diff = timedelta(minutes=90)
    start_window = new_ride.travel_datetime - max_time_diff
    end_window = new_ride.travel_datetime + max_time_diff

    # 1. Get ALL potential matches in the time window
    potential_matches = RideRequest.query.filter(
        RideRequest.is_matched == False,
        RideRequest.user_id != new_ride.user_id, # Not ourself
        RideRequest.travel_datetime.between(start_window, end_window)
    ).all()

    found_match = None
    for existing_ride in potential_matches:
        # 2. Check preferences first (it's fast, no API)
        if not check_mutual_preferences(new_ride, existing_ride):
            continue # Skip, preferences don't match

        # 3. Check for exact text match (fast, no API)
        is_exact_match = (
            new_ride.origin_text.lower() == existing_ride.origin_text.lower() and
            new_ride.destination_text.lower() == existing_ride.destination_text.lower()
        )

        if is_exact_match:
            found_match = existing_ride
            break # Found a perfect match!

        # 4. Check for partial route match (slow, uses API)
        # This checks both ways:
        # A) Is the new ride (Vellore) on the way for the existing ride (Bangalore)?
        # B) Is the existing ride (Vellore) on the way for the new ride (Bangalore)?

        # We assume the ride with the shorter text name is the partial one.
        # This is a simple heuristic.

        # Check A
        if is_route_partial_match(main_ride=existing_ride, check_ride=new_ride):
            found_match = existing_ride
            break

        # Check B
        if is_route_partial_match(main_ride=new_ride, check_ride=existing_ride):
            found_match = existing_ride
            break

    # 5. Act on the result (This logic is the same as V1)
    if found_match:
        # We have a match!
        new_group = MatchGroup()
        db.session.add(new_group)

        # Add both rides to the group
        new_ride.is_matched = True
        new_ride.match_group = new_group

        found_match.is_matched = True
        found_match.match_group = found_match.match_group or new_group

        db.session.add(new_ride)
        db.session.commit()
        return True
    else:
        # No match found. Just add the new ride to the pool.
        db.session.add(new_ride)
        db.session.commit()
        return False

def check_mutual_preferences(ride_a, ride_b):

    return check_one_way_preference(ride_a, ride_b) and check_one_way_preference(ride_b, ride_a)


def check_one_way_preference(ride_a, ride_b):

    pref = ride_a.preference
    user_a = ride_a.requester
    user_b = ride_b.requester

    if pref == 'any':
        return True

    if pref == 'girls_only':

        return user_a.gender == 'female' and user_b.gender == 'female'

    if pref == 'boys_only':
        return user_a.gender == 'male' and user_b.gender == 'male'

    if pref.startswith('year_'):
        year = int(pref.split('_')[-1])
        return user_b.college_year == year

    return False

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)