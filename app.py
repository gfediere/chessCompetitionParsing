import glob
import json
import os
import re
import subprocess
import sys
import unicodedata
import requests
from bs4 import BeautifulSoup
from flask import Flask, flash, redirect, render_template_string, request, url_for
from dotenv import load_dotenv

# Chargement automatique des variables d'environnement depuis le fichier .env
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

active_bots = {}

# Dossier de stockage fixe sous ./data
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# HELPER DE NETTOYAGE D'ENCODAGE & TEXTE & CALCUL DE POINTS
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Nettoie les espaces insécables, accents et espaces superflus."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).replace("\xa0", " ")
    return " ".join(text.split()).lower()


def calculate_total_points(round_history: list) -> float:
    """Calcule le total cumulé de points à partir de l'historique."""
    total = 0.0
    for item in round_history:
        res = item.get("result", "")
        if res == "1 - 0":
            total += 1.0
        elif res == "0 - 1":
            total += 0.0
        elif res in ["½ - ½", "1/2 - 1/2", "X - X"]:
            total += 0.5
    return total


def format_points(points: float) -> str:
    """Formate le score proprement (ex: 2.5 au lieu de 2.50)."""
    if points.is_integer():
        return f"{int(points)}"
    return f"{points}"


def fetch_live_result(t_id, round_num, player_name):
    """Consulte directement la page de la ronde (Action=0N) comme pour les autres rondes."""
    action_round = f"{round_num:02d}" if round_num < 10 else str(round_num)
    url = f"https://www.echecs.asso.fr/Resultats.aspx?URL=Tournois/Id/{t_id}/{t_id}&Action={action_round}"
    
    clean_player = clean_text(player_name)
    player_tokens = [w for w in clean_player.split() if len(w) > 2]

    try:
        resp = requests.get(url, timeout=5)
        resp.encoding = 'iso-8859-1'
        
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", id="TablePage")
            if table:
                for row in table.find_all("tr")[1:]:
                    cells = [clean_text(el.text) for el in row.find_all("td")]
                    row_str = " ".join(cells)
                    
                    if all(token in row_str for token in player_tokens):
                        white_player = cells[2] if len(cells) > 2 else ""
                        is_white = all(token in white_player for token in player_tokens)

                        if "1 - 0" in row_str or "1-0" in row_str:
                            return "1 - 0" if is_white else "0 - 1"
                        elif "0 - 1" in row_str or "0-1" in row_str:
                            return "0 - 1" if is_white else "1 - 0"
                        elif "1/2" in row_str or "½" in row_str or "x - x" in row_str or "x-x" in row_str:
                            return "½ - ½"
    except Exception:
        pass

    return "En cours"


# ---------------------------------------------------------------------------
# TEMPLATES HTML
# ---------------------------------------------------------------------------

DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FFE Chess Bot Controller</title>
    <style>
        body { font-family: system-ui, sans-serif; background: #f4f6f8; margin: 0; padding: 20px; display: flex; gap: 20px; justify-content: center; flex-wrap: wrap; }
        .card { background: white; padding: 25px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); width: 100%; max-width: 480px; }
        h2 { margin-top: 0; color: #333; }
        .form-group { margin-bottom: 12px; }
        label { display: block; margin-bottom: 4px; font-weight: bold; color: #555; font-size: 14px; }
        input[type="text"], input[type="number"], select { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
        .checkbox-group { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
        button { background: #28a745; color: white; border: none; padding: 10px 15px; border-radius: 4px; cursor: pointer; width: 100%; font-size: 16px; margin-top: 10px; }
        button:hover { background: #218838; }
        .stop-btn { background: #dc3545; margin-top: 0; padding: 6px 12px; font-size: 13px; width: auto; }
        .stop-btn:hover { background: #c82333; }
        .mobile-btn { display: inline-block; background: #17a2b8; color: white; text-decoration: none; padding: 4px 8px; border-radius: 4px; font-size: 12px; margin-top: 6px; }
        .mobile-btn:hover { background: #138496; }
        .flash { background: #d4edda; color: #155724; padding: 10px; border-radius: 4px; margin-bottom: 15px; }
        .bot-item { display: flex; justify-content: space-between; align-items: flex-start; padding: 12px; border: 1px solid #ddd; border-radius: 6px; margin-bottom: 10px; background: #fafafa; }
        .tournament-title { color: #0056b3; font-weight: bold; font-size: 13px; }
        .match-details { margin-top: 6px; padding: 6px 8px; background: #eef4fc; border-left: 3px solid #007bff; border-radius: 4px; font-size: 13px; color: #333; }
        .badge { display: inline-block; padding: 4px 10px; background: #007bff; color: white; border-radius: 12px; font-size: 12px; font-weight: bold; margin-top: 6px; }
        .archive-item { padding: 10px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
    </style>
</head>
<body>
    <div class="card">
        <h2>➕ Lancer un suivi</h2>
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            {% for message in messages %}
              <div class="flash">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <form method="POST" action="/start">
            <div class="form-group">
                <label>ID Tournoi (tournament_id)</label>
                <input type="text" name="tournament_id" value="{{ defaults.tournament_id }}" required placeholder="ex: 63375">
            </div>
            <div class="form-group">
                <label>Nom du Joueur (user)</label>
                <input type="text" name="user" value="{{ defaults.user }}" required placeholder="ex: NOM Prenom">
            </div>
            <div class="form-group">
                <label>Adresse du Serveur Web (pour lien cliquable)</label>
                <input type="text" name="SERVER_URL" value="{{ defaults.SERVER_URL }}" required>
            </div>
            <div class="form-group">
                <label>Nombre de rondes (rounds)</label>
                <input type="number" name="rounds" value="{{ defaults.rounds }}" required>
            </div>
            <div class="form-group">
                <label>Ronde de départ (round_start)</label>
                <input type="number" name="round_start" value="{{ defaults.round_start }}" required>
            </div>
            <div class="form-group">
                <label>Pushover App Token</label>
                <input type="text" name="pushover_app_token" value="{{ defaults.pushover_app_token }}" required>
            </div>
            <div class="form-group">
                <label>Pushover User Key</label>
                <input type="text" name="pushover_user_key" value="{{ defaults.pushover_user_key }}" required>
            </div>
            <div class="form-group">
                <label>Niveau de Log</label>
                <select name="logLevel">
                    <option value="INFO">INFO</option>
                    <option value="DEBUG">DEBUG</option>
                </select>
            </div>
            <div class="checkbox-group">
                <input type="checkbox" id="dry_run" name="dry_run" value="True" {% if defaults.dry_run == 'True' %}checked{% endif %}>
                <label for="dry_run">Mode Simulation (dry-run)</label>
            </div>

            <button type="submit">🚀 Lancer ce suivi</button>
        </form>
    </div>

    <div class="card">
        <h2>📊 Suivis en cours ({{ active_bots|length }})</h2>
        {% if not active_bots %}
            <p style="color: #777;">Aucun suivi actif actuellement.</p>
        {% else %}
            {% for bot_key, process in active_bots.items() %}
                {% set status_info = statuses.get(bot_key, {}) %}
                {% set match = status_info.get('match_info') %}
                {% set t_id = bot_key.split('___')[0] %}
                {% set player_name = bot_key.split('___')[1] %}
                {% set clean_url_name = player_name.replace(' ', '') %}
                
                <div class="bot-item">
                    <div>
                        <strong>👤 {{ player_name }}</strong><br>
                        <span class="tournament-title">🏆 {{ status_info.get('tournament_name', 'Chargement du nom...') }}</span><br>
                        <small style="color: #666;">ID Tournoi: {{ t_id }}</small><br>
                        
                        <span class="badge">
                            Ronde {{ status_info.get('current_round', '?') }} / {{ status_info.get('total_rounds', '?') }}
                        </span>

                        {% if match and match.get('opponent') %}
                            <div class="match-details">
                                <strong>Table {{ match.get('table') }}</strong> ({{ '♔ Blancs' if match.get('color') == 'Blancs' else '♚ Noirs' }})<br>
                                vs 👥 <em>{{ match.get('opponent') }}</em>
                            </div>
                        {% else %}
                            <div style="font-size: 12px; color: #888; margin-top: 6px;">
                                ⏳ {{ status_info.get('status', 'En attente des appariements') }}
                            </div>
                        {% endif %}

                        <a href="/player/{{ t_id }}/{{ clean_url_name }}" target="_blank" class="mobile-btn">📱 Lien Mobile</a>
                    </div>
                    <form method="POST" action="/stop" style="margin: 0;">
                        <input type="hidden" name="bot_key" value="{{ bot_key }}">
                        <button type="submit" class="stop-btn">Arrêter</button>
                    </form>
                </div>
            {% endfor %}
        {% endif %}

        <h3 style="margin-top: 25px; color: #444;">📁 Historiques conservés</h3>
        {% if not statuses %}
            <p style="color: #888; font-size: 13px;">Aucun historique sauvegardé.</p>
        {% else %}
            {% for bot_key, data in statuses.items() %}
                {% if bot_key not in active_bots %}
                    {% set t_id = data.get('tournament_id') %}
                    {% set player_name = data.get('user') %}
                    {% set clean_url_name = player_name.replace(' ', '') %}
                    <div class="archive-item">
                        <div>
                            <strong>{{ player_name }}</strong><br>
                            <small style="color: #666;">{{ data.get('tournament_name') }} (ID: {{ t_id }})</small>
                        </div>
                        <a href="/player/{{ t_id }}/{{ clean_url_name }}" target="_blank" class="mobile-btn">📱 Voir Fiche</a>
                    </div>
                {% endif %}
            {% endfor %}
        {% endif %}
    </div>
</body>
</html>
"""

PLAYER_MOBILE_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="refresh" content="15">
    <title>Suivi - {{ player }}</title>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; background: #eef2f5; margin: 0; padding: 15px; display: flex; justify-content: center; }
        .card { background: white; border-radius: 16px; padding: 20px; width: 100%; max-width: 400px; box-shadow: 0 8px 20px rgba(0,0,0,0.08); }
        .header { text-align: center; border-bottom: 2px solid #f0f0f0; padding-bottom: 15px; margin-bottom: 15px; }
        .player-name { font-size: 22px; font-weight: bold; color: #1a1a1a; margin: 0; }
        .tournament-name { font-size: 14px; color: #0056b3; font-weight: 600; margin-top: 5px; }
        .round-badge { display: inline-block; background: #007bff; color: white; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: bold; margin-top: 10px; }
        .points-badge { display: inline-block; background: #28a745; color: white; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: bold; margin-top: 10px; margin-left: 5px; }
        
        .match-card { background: #f8f9fa; border-radius: 12px; padding: 15px; border: 1px solid #e9ecef; margin-top: 15px; text-align: center; }
        .table-number { font-size: 26px; font-weight: 800; color: #2d3748; margin-bottom: 5px; }
        .piece-color { font-size: 18px; font-weight: 600; padding: 6px 14px; border-radius: 8px; display: inline-block; margin-bottom: 10px; }
        .white-piece { background: #ffffff; color: #1a1a1a; border: 1px solid #ccc; }
        .black-piece { background: #2d3748; color: #ffffff; }
        .vs-label { font-size: 11px; color: #a0aec0; text-transform: uppercase; letter-spacing: 1px; font-weight: bold; }
        .opponent-name { font-size: 18px; font-weight: bold; color: #2b6cb0; margin-top: 4px; }
        
        .history-section { margin-top: 25px; border-top: 2px solid #f0f0f0; padding-top: 15px; }
        .history-title { font-size: 15px; font-weight: bold; color: #4a5568; margin-bottom: 12px; text-align: left; }
        .history-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px; background: #f8f9fa; border-radius: 8px; margin-bottom: 8px; font-size: 13px; border: 1px solid #edf2f7; }
        .history-round { font-weight: bold; color: #007bff; }
        .history-opponent { color: #2d3748; font-weight: 500; }
        .res-badge { padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 12px; }
        .res-win { background: #d4edda; color: #155724; }
        .res-draw { background: #e2e3e5; color: #383d41; }
        .res-loss { background: #f8d7da; color: #721c24; }
        .res-pending { background: #fff3cd; color: #856404; }
        
        .status-waiting { font-size: 15px; color: #718096; padding: 20px 0; text-align: center; }
        .footer { text-align: center; font-size: 11px; color: #a0aec0; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <h1 class="player-name">👤 {{ player }}</h1>
            <div class="tournament-name">🏆 {{ status_info.get('tournament_name', 'Tournoi en cours') }}</div>
            <div>
                <div class="round-badge">
                    {% if status_info.get('status') == 'Terminé' %}
                        Tournoi Terminé
                    {% else %}
                        Ronde {{ status_info.get('current_round', '?') }} / {{ status_info.get('total_rounds', '?') }}
                    {% endif %}
                </div>
                <div class="points-badge">
                    ⭐ {{ points_display }} pt(s)
                </div>
            </div>
        </div>

        {% set match = status_info.get('match_info') %}
        {% if match and match.get('opponent') and status_info.get('status') != 'Terminé' %}
            <div class="match-card">
                <div class="table-number">Echiquier N° {{ match.get('table') }}</div>
                
                {% if match.get('color') == 'Blancs' %}
                    <div class="piece-color white-piece">♔ Pièces Blanches</div>
                {% else %}
                    <div class="piece-color black-piece">♚ Pièces Noires</div>
                {% endif %}

                <div class="vs-label">Adversaire Ronde {{ match.get('round', status_info.get('current_round')) }}</div>
                <div class="opponent-name">{{ match.get('opponent') }}</div>
            </div>
        {% else %}
            <div class="status-waiting">
                {% if status_info.get('status') == 'Terminé' %}
                    🏁 Bilan complet du tournoi ci-dessous
                {% else %}
                    ⏳ {{ status_info.get('status', 'En attente de la publication des appariements...') }}
                {% endif %}
            </div>
        {% endif %}

        {% set history = status_info.get('round_history', []) %}
        {% if history %}
            <div class="history-section">
                <div class="history-title">📜 Historique des parties</div>
                {% for item in history %}
                    {% set res = item.get('result', 'En cours') %}
                    <div class="history-item">
                        <div>
                            <span class="history-round">R. {{ item.get('round') }}</span>
                            <span> (Ech. {{ item.get('table') }})</span><br>
                            <span class="history-opponent">
                                {{ '♔' if item.get('color') == 'Blancs' else '♚' }} vs {{ item.get('opponent') }}
                            </span>
                        </div>
                        <div>
                            {% if res == '1 - 0' %}
                                <span class="res-badge res-win">1 - 0</span>
                            {% elif res == '0 - 1' %}
                                <span class="res-badge res-loss">0 - 1</span>
                            {% elif res == '½ - ½' %}
                                <span class="res-badge res-draw">½ - ½</span>
                            {% else %}
                                <span class="res-badge res-pending">En cours</span>
                            {% endif %}
                        </div>
                    </div>
                {% endfor %}
            </div>
        {% endif %}

        <div class="footer">
            Mis à jour automatiquement toutes les 15s
        </div>
    </div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# LOGIQUE ET ROUTES FLASK
# ---------------------------------------------------------------------------

def get_statuses():
    """Lit tous les fichiers status_*.json dans DATA_DIR."""
    statuses = {}
    pattern = os.path.join(DATA_DIR, "status_*.json")
    for filepath in glob.glob(pattern):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                t_id = str(data.get("tournament_id", "")).strip()
                user_name = str(data.get("user", "")).strip()

                bot_key = f"{t_id}___{user_name}"
                statuses[bot_key] = data
        except Exception:
            continue
    return statuses


def clean_dead_processes():
    to_remove = [k for k, p in active_bots.items() if p.poll() is not None]
    for k in to_remove:
        del active_bots[k]


@app.route("/")
def index():
    clean_dead_processes()
    statuses = get_statuses()
    
    defaults = {
        "tournament_id": os.getenv("tournament_id", ""),
        "user": os.getenv("user", ""),
        "SERVER_URL": os.getenv("SERVER_URL", "https://chess-bot.fedallica.fr"),
        "rounds": os.getenv("rounds", "7"),
        "round_start": os.getenv("round_start", "1"),
        "pushover_app_token": os.getenv("pushover_app_token", ""),
        "pushover_user_key": os.getenv("pushover_user_key", ""),
        "dry_run": os.getenv("dry_run", "False"),
    }

    return render_template_string(
        DASHBOARD_TEMPLATE,
        active_bots=active_bots,
        statuses=statuses,
        defaults=defaults,
    )


@app.route("/player/<tournament_id>/<player_slug>")
def player_view_slug(tournament_id, player_slug):
    statuses = get_statuses()
    status_info = {}
    found_player_name = player_slug
    target_filepath = None

    for bot_key, data in statuses.items():
        t_id = bot_key.split("___")[0]
        p_name = bot_key.split("___")[1]
        
        if t_id == tournament_id and p_name.replace(" ", "") == player_slug:
            status_info = data
            found_player_name = p_name
            clean_player = p_name.replace(" ", "_")
            target_filepath = os.path.join(DATA_DIR, f"status_{t_id}_{clean_player}.json")
            break

    # RAFRAÎCHISSEMENT EN DIRECT : Consulte la page de la ronde si une partie est 'En cours'
    if status_info and target_filepath:
        history = status_info.get("round_history", [])
        updated = False
        for item in history:
            if item.get("result") == "En cours":
                live_res = fetch_live_result(tournament_id, item.get("round"), found_player_name)
                if live_res != "En cours":
                    item["result"] = live_res
                    updated = True

        if updated:
            status_info["current_points"] = calculate_total_points(history)
            try:
                with open(target_filepath, "w", encoding="utf-8") as f:
                    json.dump(status_info, f, ensure_ascii=False)
            except Exception:
                pass

    history = status_info.get("round_history", [])
    total_pts = calculate_total_points(history)
    points_display = format_points(total_pts)

    return render_template_string(
        PLAYER_MOBILE_TEMPLATE,
        player=found_player_name,
        status_info=status_info,
        points_display=points_display,
    )


@app.route("/start", methods=["POST"])
def start():
    clean_dead_processes()

    tournament_id = request.form.get("tournament_id", "").strip()
    user = request.form.get("user", "").strip()
    bot_key = f"{tournament_id}___{user}"

    if bot_key in active_bots:
        flash(f"Un suivi tourne déjà pour {user} sur le tournoi {tournament_id} !")
        return redirect(url_for("index"))

    data = {
        "tournament_id": tournament_id,
        "user": user,
        "SERVER_URL": request.form.get("SERVER_URL", "https://chess-bot.fedallica.fr").strip(),
        "rounds": request.form.get("rounds", "7").strip(),
        "round_start": request.form.get("round_start", "1").strip(),
        "pushover_app_token": request.form.get("pushover_app_token", "").strip(),
        "pushover_user_key": request.form.get("pushover_user_key", "").strip(),
        "logLevel": request.form.get("logLevel", "INFO"),
    }

    if request.form.get("dry_run"):
        data["dry_run"] = "True"
        data["dry-run"] = "True"

    env_child = os.environ.copy()
    env_child.update(data)

    proc = subprocess.Popen([sys.executable, "run.py"], env=env_child)
    active_bots[bot_key] = proc

    flash(f"Suivi démarré pour {user} (Tournoi {tournament_id})")
    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    bot_key = request.form.get("bot_key")
    if bot_key in active_bots:
        proc = active_bots[bot_key]
        if proc.poll() is None:
            proc.terminate()
        del active_bots[bot_key]

        flash("Suivi arrêté. L'historique reste disponible !")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)