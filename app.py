import glob
import json
import os
import re
import subprocess
import sys
import unicodedata
import requests
from bs4 import BeautifulSoup
from flask import Flask, flash, redirect, render_template, request, url_for
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

    return render_template(
        "index.html",
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

    return render_template(
        "player_mobile.html",
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