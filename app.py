# app.py
from flask import Flask, request, render_template, jsonify, send_from_directory
import requests
import base64
import os

app = Flask(
    __name__,
    template_folder='templates',
    static_folder='static'
)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2 MB uploads

# --- GitHub integration ---
def create_github_repo(token, owner, repo_name, private, description):
    url = f"https://api.github.com/orgs/{owner}/repos" if owner else "https://api.github.com/user/repos"
    payload = {"name": repo_name, "private": private, "description": description}
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    resp = requests.post(url, json=payload, headers=headers)
    if resp.status_code != 201:
        raise Exception(f"GitHub repo creation failed: {resp.status_code} {resp.text}")
    return resp.json()

def add_metadata_github(token, owner, repo_name, metadata_path, metadata_content):
    if not owner:
        user_resp = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        )
        user_resp.raise_for_status()
        owner = user_resp.json().get("login")

    url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{metadata_path}"
    encoded = base64.b64encode(metadata_content.encode('utf-8')).decode('utf-8')
    payload = {"message": f"Add {metadata_path}", "content": encoded}
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    resp = requests.put(url, json=payload, headers=headers)
    if resp.status_code not in (200, 201):
        raise Exception(f"GitHub add metadata failed: {resp.status_code} {resp.text}")
    return resp.json()

# --- Azure DevOps integration ---
def create_ado_repo(token, org_url, project, repo_name):
    url = f"{org_url}/{project}/_apis/git/repositories?api-version=6.0"
    payload = {"name": repo_name}
    auth = ('', token)
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, json=payload, auth=auth, headers=headers)
    if resp.status_code == 409:
        raise Exception(f"Repository '{repo_name}' already exists in project '{project}'")
    elif resp.status_code != 201:
        raise Exception(f"ADO repo creation failed: {resp.status_code} {resp.text}")
    return resp.json()

def add_metadata_ado(token, org_url, project, repo_name, metadata_path, metadata_content):
    auth = ('', token)
    repo_resp = requests.get(
        f"{org_url}/{project}/_apis/git/repositories/{repo_name}?api-version=6.0",
        auth=auth
    )
    repo_resp.raise_for_status()
    repo_id = repo_resp.json().get("id")

    url = f"{org_url}/{project}/_apis/git/repositories/{repo_id}/pushes?api-version=6.0"
    change = {
        "refUpdates": [{
            "name": "refs/heads/main",
            "oldObjectId": "0000000000000000000000000000000000000000"
        }],
        "commits": [{
            "comment": "Add metadata",
            "changes": [{
                "changeType": "add",
                "item": {"path": f"/{metadata_path}"},
                "newContent": {"content": metadata_content, "contentType": "rawtext"}
            }]
        }]
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, json=change, auth=auth, headers=headers)
    if resp.status_code not in (200, 201):
        raise Exception(f"ADO add metadata failed: {resp.status_code} {resp.text}")
    return resp.json()

# Serve the SPA shell
@app.route('/', methods=['GET'])
def serve_index():
    return render_template('index.html')

# Catch-all for client-side routing
@app.route('/<path:path>', methods=['GET'])
def serve_spa(path):
    if path.startswith('api/'):
        return jsonify(success=False, message='Not found'), 404
    static_path = os.path.join(app.static_folder, path)
    if os.path.exists(static_path):
        return send_from_directory(app.static_folder, path)
    return render_template('index.html')

# JSON‑only API endpoint
@app.route('/api/create', methods=['POST'])
def api_create():
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()
        platform = data.get('platform')
        if platform not in ['github', 'ado']:
            return jsonify(success=False, message='Invalid platform'), 400

        metadata_path = data.get('metadata_path', 'metadata.yml').strip()
        if request.is_json:
            content = data.get('metadata_content', '')
        else:
            uploaded = request.files.get('metadata_file')
            if uploaded and uploaded.filename:
                content = uploaded.read().decode('utf-8')
            else:
                repo_name = data.get('repo_ado' if platform=='ado' else 'repo_github', '').strip()
                content = f"""
apm_id: YOUR_APM_ID
name: {repo_name}
owner_team: YOUR_TEAM
repo_status: development
version: 0.1.0
language:
framework:
tags: []
"""

        if platform == 'github':
            token       = data.get('token')
            owner       = data.get('org_github')
            repo        = data.get('repo_github')
            description = data.get('description_github')
            private     = data.get('private_github') == 'on'
            if not token or not repo:
                return jsonify(success=False, message='GitHub token and repo name are required'), 400
            create_github_repo(token, owner, repo, private, description)
            add_metadata_github(token, owner, repo, metadata_path, content)
        else:
            token   = data.get('token_ado')
            org_url = data.get('org_url')
            project = data.get('project')
            repo    = data.get('repo_ado')
            if not token or not org_url or not project or not repo:
                return jsonify(success=False, message='ADO token, org URL, project, and repo are required'), 400
            create_ado_repo(token, org_url, project, repo)
            add_metadata_ado(token, org_url, project, repo, metadata_path, content)

        return jsonify(success=True, message='Repository and metadata created!')

    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
