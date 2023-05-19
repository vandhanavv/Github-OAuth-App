from flask import Flask, redirect, request, url_for, Response
import requests
import psycopg2
import os
import dotenv
import csv
import logging

#Configure log file
logging.basicConfig(filename='github_api_data.log', level=logging.DEBUG, format='%(asctime)s - %(message)s')

#Creating a new Flask Instance
app = Flask(__name__)

#Redirecting user to Github OAuth authorization endpoint
@app.route('/')
def index():
    oauth_endpoint = 'https://github.com/login/oauth/authorize'
    #Get client ID and client secret by registering your Github OAuth app
    client_id = os.environ.get('CLIENT_ID')
    #Define your callback URL 
    redirect_uri = os.environ.get('REDIRECT_URI')

    return redirect(f'{oauth_endpoint}?client_id={client_id}&redirect_uri={redirect_uri}')

#Endpoint after Github OAuth authorization to retrieve access token and fetch owner and repo data
@app.route('/github/callback')
def callback():
    #Get the authorization code from the URL query parameters
    code = request.args.get('code')

    try:
    #Define the GitHub OAuth access token endpoint
        token_endpoint = 'https://github.com/login/oauth/access_token'
        client_id = os.environ.get('CLIENT_ID')
        client_secret = os.environ.get('CLIENT_SECRET')
        redirect_uri = os.environ.get('REDIRECT_URI')

        payload = {
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'redirect_uri': redirect_uri
        }
        headers = {
            'Accept': 'application/json'
        }
        response = requests.post(token_endpoint, data=payload, headers=headers)
        #Retrieve the access token from the JSON response
        access_token = response.json()['access_token']
    except Exception as e:
        logging.error(f'Error in retrieving access token: {e}')


    try:
        owner_url = 'https://api.github.com/user'
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        owner_response = requests.get(owner_url, headers=headers)
        #Fetch the owner(user) data from the JSON response using the access token
        owner_data = owner_response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f'Error on request for fetching owner(user) data: {e}')


    try:
        # Fetch the repository data from the JSON response using the access token
        repo_url = 'https://api.github.com/user/repos'
        repo_response = requests.get(repo_url, headers=headers)
        repo_data = repo_response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f'Error on request for fetching repository data: {e}')   

    #Calling the database_load function and storing the data in variable 'load'
    load = database_load(owner_data, repo_data) 
    #POST request made to endpoint for downloading the CSV file and 'load' passed as parameter
    request.post('/github/download', json={'rows':load})

#Function for database connection and insertion of json data into normalized tables 
def database_load(owner_data, repo_data):
    #Database connection parameters
    database_username = os.environ.get('DATABASE_USERNAME')
    database_password = os.environ.get('DATABASE_PASSWORD')
    database_name = os.environ.get('DATABASE_NAME')
    database_port = os.environ.get('DATABASE_PORT')

    #Database connection and exception handling for connection errors
    try:
        postgres = psycopg2.connect(
        host=database_port,
        database=database_name,
        user=database_username,
        password=database_password
        )
    except (psycopg2.OperationalError, psycopg2.DatabaseError) as e:
        logging.error(f"Error connecting to database: {e}")
        raise

    cursor = postgres.cursor()

    #Create Normalized tables owner and repository
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS owner (
    owner_id INT PRIMARY KEY NOT NULL,
    owner_name TEXT NOT NULL,
    owner_email TEXT 
    );
    ''')

    #Note: owner_id is a foreign key in repository table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS repository (
    repo_id INT PRIMARY KEY NOT NULL,
    fk_owner_id INTEGER REFERENCES owner(owner_id),
    repo_name TEXT NOT NULL,
    repo_status TEXT NOT NULL,
    repo_stars INTEGER NOT NULL
    );
    ''')

    #Insert data into normazlized tables and update when duplicate is encountered
    owner_query = "INSERT INTO owner (owner_id, owner_name, owner_email) VALUES (%s, %s, %s) ON CONFLICT (owner_id) DO UPDATE SET owner_name = EXCLUDED.owner_name, owner_email = EXCLUDED.owner_email RETURNING owner_id"
    cursor.execute(owner_query, (owner_data["id"], owner_data["name"], owner_data["email"] if owner_data["email"] else ''))

    for repo in repo_data:
        repo_query = "INSERT INTO repository (repo_id, repo_name, repo_status, repo_stars) VALUES (%s, %s, %s, %s) ON CONFLICT (repo_id) DO UPDATE SET status = EXCLUDED.status, stars = EXCLUDED.stars"
        cursor.execute(repo_query, (repo_data["id"], repo_data["name"], "public" if not repo_data["private"] else "private", repo_data["stargazers_count"]))

    #Join the tables owner and repository using the owner id
    cursor.execute('''
    SELECT o.id, o.name, o.email, r.id, r.name, r.status, r.stars 
    FROM owner o 
    JOIN repository r 
    ON o.owner_id = r.owner_id
    ''')
    rows = cursor.fetchall()

    postgres.commit()
    postgres.close()

    return rows

#Writing data into CSV file and downloading the same after hitting the below endpoint
@app.route('/github/download', methods=['POST'])
def github_csv():
    #Extract data using the GET request from the parameters passed in the POST request for hitting the '/github/download' endpoint
    rows = request.json.get('rows')
    #Writing into CSV file
    with open('data.csv', mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Owner ID", "Owner Name", "Owner Email", "Repo ID", "Repo Name", "Status", "Stars Count"])

    for row in rows:
        writer.writerow(row)
    
    #Download the CSV file
    with open('data.csv', 'r') as f:
        csv_data = f.read()
    return Response(csv_data, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=data.csv"})
    
if __name__ == "__main__":
    app.run(debug=True)
