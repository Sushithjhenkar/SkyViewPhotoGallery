import os
import json
import requests
from flask import Flask, redirect, request, render_template, send_from_directory, session, url_for
from google.cloud import storage
import google.generativeai as genai
import pyrebase

app = Flask(__name__)
app.secret_key = "your-secret-key"

os.makedirs('files', exist_ok=True)

bucket_name = 'project1-photo-app'
storage_client = storage.Client()
genai.configure(api_key="API KEY")

firebase_config = {
    "apiKey": "API KEY",
    "authDomain": "DOMAIN URL",
    "databaseURL": "DATABSE URL",
    "projectId": "PROJECT ID",
    "storageBucket": "BUCKET URL",
    "messagingSenderId": "SENDER ID",
    "appId": "APP ID"
}

firebase = pyrebase.initialize_app(firebase_config)
auth = firebase.auth()

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        try:
            user = auth.create_user_with_email_and_password(email, password)
            session['user'] = user['localId']
            return redirect('/')
        except Exception as e:
            return f"Error: {str(e)}" 
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        try:
            user = auth.sign_in_with_email_and_password(email, password)
            session['user'] = user['localId']
            return redirect('/')
        except:
            return "Invalid login credentials"
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return redirect('/login')


def upload_blob(bucket_name, file, destination_blob_name, user_id):
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(f"{user_id}/{destination_blob_name}")
    blob.upload_from_file(file)

def download_blob(bucket_name, source_blob_name, destination_file_name):
    os.makedirs(os.path.dirname(destination_file_name), exist_ok=True)
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(source_blob_name)
    blob.download_to_filename(destination_file_name)

def list_blobs(bucket_name, user_id):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=f"{user_id}/")
    return [blob.name for blob in blobs]

def upload_to_gemini(image_path, mime_type="image/jpeg"):
    print(image_path)
    file = genai.upload_file(image_path, mime_type=mime_type)
    print(f"Uploaded file '{file.display_name}' as: {file.uri}")
    return file

def generate_description(image_file):
    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    }
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config=generation_config,
    )
    chat_session = model.start_chat(
        history=[
            {"role": "user", "parts": ["Generate title and a description for an image\n"]},
            {"role": "model", "parts": ["Please provide me with the image you want a title and description for!"]},
            {"role": "user", "parts": [image_file, "Generate a title and a description."]},
        ]
    )
    response = chat_session.send_message("INSERT_INPUT_HERE")
    try:
        parsed_response = json.loads(response.text)
        title = parsed_response.get("title", "No Title Available")
        description = parsed_response.get("description", "No Description Available")
        return title, description
    except json.JSONDecodeError:
        print("Error decoding JSON response.")
        return "Error generating title", "Error generating description"

@app.route('/')
def index():
    user_id = session.get('user')
    if not user_id:
        return redirect('/login')
    user_folder = os.path.join('files', user_id)
    os.makedirs(user_folder, exist_ok=True)
    blob_names = list_blobs(bucket_name, user_id)
    for blob_name in blob_names:
        local_file_path = os.path.join(user_folder, blob_name.split('/')[-1])
        if not os.path.exists(local_file_path):
            download_blob(bucket_name, blob_name, local_file_path)
    local_files = os.listdir(user_folder)
    for local_file in local_files:
        local_file_path = os.path.join(user_folder, local_file)
        if os.path.isfile(local_file_path):
            if local_file not in [blob.split('/')[-1] for blob in blob_names]:
                os.remove(local_file_path)
    file_list = {}
    for file in blob_names:
        if file.lower().endswith(('.jpg', '.jpeg', '.png')):
            text_filename = os.path.splitext(file)[0] + '.txt'
            description = None
            if os.path.exists(os.path.join(user_folder, text_filename)):
                with open(os.path.join(user_folder, text_filename), 'r') as text_file:
                    description = text_file.read()
            if os.path.exists(os.path.join(user_folder, os.path.basename(file))):
                file_list[os.path.basename(file)] = description 
    return render_template('index.html', files=file_list, user_id=user_id)


@app.route('/upload', methods=['POST'])
def upload():
    if 'user' not in session:
        return redirect('/login')
    user_id = session['user']
    user_folder = os.path.join('files', user_id) 
    os.makedirs(user_folder, exist_ok=True)
    file = request.files['form_file']
    filename = file.filename
    if filename == '':
        return "No file selected", 400
    if not filename.lower().endswith(('.jpg', '.jpeg', '.png')):
        return "Invalid file format. Only .jpg, .jpeg, and .png files are allowed.", 400
    local_image_path = os.path.join(user_folder, filename) 
    file.save(local_image_path) 
    print(f"File saved at: {local_image_path}")
    image_file = upload_to_gemini(local_image_path)
    title, description = generate_description(image_file)
    local_text_path = os.path.join(user_folder, os.path.splitext(filename)[0] + '.txt')
    with open(local_text_path, 'w') as text_file:
        text_file.write(f"{title}\n{description}")
    with open(local_text_path, 'rb') as text_file:
        upload_blob(bucket_name, text_file, os.path.basename(local_text_path), user_id)
    file.seek(0)
    upload_blob(bucket_name, file, os.path.basename(local_image_path), user_id)
    return redirect('/')


@app.route('/files/<user_id>/<filename>')
def files(user_id, filename):
    return send_from_directory(os.path.join('files', user_id), filename)

@app.route('/view/<user_id>/<filename>')
def view_image(user_id, filename):
    text_filename = os.path.splitext(filename)[0] + '.txt'
    title = "No Title Available"
    description = "No Description Available"
    text_file_path = os.path.join('./files', user_id, text_filename)
    if os.path.exists(text_file_path):
        with open(text_file_path, 'r') as text_file:
            content = text_file.read()
            title, description = parse_title_description(content)
    return render_template('view_image.html', filename=filename, title=title, description=description, user_id=user_id)

def parse_title_description(content):
    lines = content.split('\n')
    title = lines[0].strip() if lines else "No Title Available"
    description = '\n'.join(lines[1:]).strip() if len(lines) > 1 else "No Description Available"
    return title, description

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
